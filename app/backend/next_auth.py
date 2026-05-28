"""Passkey authentication endpoints for the PostgreSQL-backed Next runtime."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import struct
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import UUID, uuid4

import cbor2
import jwt
from cryptography.hazmat.primitives.asymmetric.ec import (
    ECDSA,
    SECP256R1,
    EllipticCurvePublicKey,
    EllipticCurvePublicNumbers,
)
from cryptography.hazmat.primitives.hashes import SHA256
from flask import Flask, jsonify, make_response, request
from psycopg.types.json import Jsonb


ConnectFactory = Callable[[], Any]
ResponseFactory = Callable[[dict[str, Any], int], Any]
TableExists = Callable[[Any, str], bool]

SESSION_COOKIE_NAME = "dv_next_session"
SESSION_COOKIE_MAX_AGE_SECONDS = 24 * 60 * 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    value += "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(value.encode("ascii"))


def _make_challenge() -> bytes:
    return secrets.token_bytes(32)


def _jwt_secret() -> str:
    configured = (
        os.environ.get("DISCVAULT_NEXT_JWT_SECRET")
        or os.environ.get("JWT_SECRET")
        or ""
    ).strip()
    if configured:
        return configured
    # Stable fallback for development stacks. Production should set JWT_SECRET.
    return hashlib.sha256(os.environ.get("DATABASE_URL", "discvault-next").encode("utf-8")).hexdigest()


def _rp_id() -> str:
    configured = os.environ.get("RP_ID", "").strip()
    if configured:
        return configured
    return request.host.split(":", 1)[0] if request else "localhost"


def _rp_name() -> str:
    return os.environ.get("RP_NAME", "DiscVault").strip() or "DiscVault"


def _request_origin() -> str:
    origin = (request.headers.get("Origin") or "").strip().rstrip("/")
    if origin:
        return origin
    scheme = request.headers.get("X-Forwarded-Proto") or request.scheme or "http"
    host = request.headers.get("X-Forwarded-Host") or request.host
    return f"{scheme}://{host}".rstrip("/")


def _rp_origins() -> list[str]:
    configured = os.environ.get("RP_ORIGINS") or os.environ.get("RP_ORIGIN") or ""
    origins = [item.strip().rstrip("/") for item in configured.split(",") if item.strip()]
    if origins:
        return origins
    return [_request_origin()]


def _create_token(user_id: str, username: str) -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "usr": username,
            "iat": _utcnow(),
            "exp": _utcnow() + timedelta(hours=24),
        },
        _jwt_secret(),
        algorithm="HS256",
    )


def _verify_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    except Exception:
        return None


def _bearer_token() -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return ""


def _session_cookie_token() -> str:
    return str(request.cookies.get(SESSION_COOKIE_NAME) or "").strip()


def _request_is_secure() -> bool:
    forwarded_proto = (request.headers.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip().lower()
    if forwarded_proto:
        return forwarded_proto == "https"
    if request.is_secure:
        return True
    return _request_origin().startswith("https://")


def _current_user_payload() -> dict[str, Any] | None:
    for token in (_bearer_token(), _session_cookie_token()):
        if not token:
            continue
        payload = _verify_token(token)
        if payload:
            return payload
    return None


def next_auth_setting_value(
    conn,
    table_exists: TableExists,
    key: str,
    default: Any = None,
) -> Any:
    if not table_exists(conn, "app_settings"):
        return default
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM app_settings WHERE key=%s", (key,))
        row = cur.fetchone()
    return row["value"] if row else default


def next_auth_count_table(conn, table_exists: TableExists, table_name: str) -> int:
    if not table_exists(conn, table_name):
        return 0
    with conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) AS count FROM "{table_name}"')
        row = cur.fetchone()
    return int(row["count"] if row else 0)


def next_auth_configured_enabled(conn, table_exists: TableExists) -> bool:
    return bool(next_auth_setting_value(conn, table_exists, "auth_enabled", False))


def next_auth_ready(conn, table_exists: TableExists) -> bool:
    return (
        next_auth_count_table(conn, table_exists, "users") > 0
        and next_auth_count_table(conn, table_exists, "passkey_credentials") > 0
    )


def next_auth_effective_enabled(conn, table_exists: TableExists) -> bool:
    return next_auth_configured_enabled(conn, table_exists) and next_auth_ready(conn, table_exists)


def next_auth_current_user(conn) -> dict[str, Any] | None:
    payload = _current_user_payload()
    user_id = payload.get("sub") if payload else None
    if not user_id:
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, username, display_name, first_name, last_name, status, created_at, updated_at
            FROM users
            WHERE id=%s AND status='active'
            """,
            (user_id,),
        )
        return cur.fetchone()


def _parse_uuid(value: Any) -> UUID | None:
    if value in (None, ""):
        return None
    return UUID(str(value))


def _hash_invite_code(code: str) -> str:
    normalized = code.replace("-", "").strip().upper()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalize_username(value: Any) -> str:
    username = str(value or "").strip()
    if not username:
        raise ValueError("Username is required")
    if len(username) > 80:
        raise ValueError("Username is too long")
    return username


def _parse_cose_key(cose_map: dict[Any, Any]) -> EllipticCurvePublicKey:
    x = cose_map[-2]
    y = cose_map[-3]
    numbers = EllipticCurvePublicNumbers(
        x=int.from_bytes(x, "big"),
        y=int.from_bytes(y, "big"),
        curve=SECP256R1(),
    )
    return numbers.public_key()


def _parse_attestation_object(att_obj_b64: str) -> tuple[bytes, bytes, bytes, int]:
    raw = _b64url_decode(att_obj_b64)
    attestation = cbor2.loads(raw)
    auth_data = attestation["authData"]
    sign_count = struct.unpack(">I", auth_data[33:37])[0]
    credential_id_len = struct.unpack(">H", auth_data[53:55])[0]
    credential_id = auth_data[55 : 55 + credential_id_len]
    cose_key_bytes = auth_data[55 + credential_id_len :]
    cbor2.loads(cose_key_bytes)
    return credential_id, cose_key_bytes, auth_data, sign_count


def _parse_auth_data(auth_data: bytes) -> tuple[bytes, int, int]:
    rp_id_hash = auth_data[:32]
    flags = auth_data[32]
    sign_count = struct.unpack(">I", auth_data[33:37])[0]
    return rp_id_hash, flags, sign_count


def _verify_signature(public_key_bytes: bytes, auth_data: bytes, client_data_hash: bytes, signature: bytes) -> None:
    cose_key = cbor2.loads(public_key_bytes)
    public_key = _parse_cose_key(cose_key)
    public_key.verify(signature, auth_data + client_data_hash, ECDSA(SHA256()))


def register_next_auth_routes(
    app: Flask,
    *,
    connect: ConnectFactory,
    table_exists: TableExists,
    response: ResponseFactory,
    next_api_error: type[Exception],
) -> None:
    def cookie_response(payload: dict[str, Any], token: str | None, status: int = 200):
        result = make_response(response(payload, status))
        if token:
            result.set_cookie(
                SESSION_COOKIE_NAME,
                token,
                max_age=SESSION_COOKIE_MAX_AGE_SECONDS,
                httponly=True,
                secure=_request_is_secure(),
                samesite="Lax",
                path="/",
            )
        return result

    def clear_cookie_response(payload: dict[str, Any], status: int = 200):
        result = make_response(response(payload, status))
        result.delete_cookie(
            SESSION_COOKIE_NAME,
            path="/",
            secure=_request_is_secure(),
            samesite="Lax",
        )
        return result

    def setting_value(conn, key: str, default: Any = None) -> Any:
        if not table_exists(conn, "app_settings"):
            return default
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM app_settings WHERE key=%s", (key,))
            row = cur.fetchone()
        return row["value"] if row else default

    def set_setting(conn, key: str, value: Any, *, is_secret: bool = False) -> None:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app_settings (key, value, is_secret, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (key) DO UPDATE SET
                    value=EXCLUDED.value,
                    is_secret=EXCLUDED.is_secret,
                    updated_at=now()
                """,
                (key, Jsonb(value), is_secret),
            )

    def configured_auth_enabled(conn) -> bool:
        return bool(setting_value(conn, "auth_enabled", False))

    def auth_ready(conn) -> bool:
        return count_table(conn, "users") > 0 and count_table(conn, "passkey_credentials") > 0

    def auth_enabled(conn) -> bool:
        return configured_auth_enabled(conn) and auth_ready(conn)

    def registration_enabled(conn) -> bool:
        return bool(setting_value(conn, "registration_enabled", True))

    def count_table(conn, table_name: str) -> int:
        if not table_exists(conn, table_name):
            return 0
        with conn.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) AS count FROM "{table_name}"')
            row = cur.fetchone()
        return int(row["count"] if row else 0)

    def current_user(conn) -> dict[str, Any] | None:
        payload = _current_user_payload()
        user_id = payload.get("sub") if payload else None
        if not user_id:
            return None
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, username, display_name, first_name, last_name, status, created_at, updated_at
                FROM users
                WHERE id=%s AND status='active'
                """,
                (user_id,),
            )
            return cur.fetchone()

    def user_roles(conn, user_id: UUID | str) -> list[dict[str, Any]]:
        if not table_exists(conn, "user_roles") or not table_exists(conn, "roles"):
            return []
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, r.key, r.name, r.system, ur.scope_type, ur.scope_id, ur.assigned_at
                FROM user_roles ur
                JOIN roles r ON r.id = ur.role_id
                WHERE ur.user_id=%s
                ORDER BY r.system DESC, r.key
                """,
                (user_id,),
            )
            return cur.fetchall()

    def user_permissions(conn, user_id: UUID | str) -> list[str]:
        if not table_exists(conn, "role_permissions") or not table_exists(conn, "user_roles"):
            return []
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT rp.permission_key
                FROM user_roles ur
                JOIN role_permissions rp ON rp.role_id = ur.role_id
                WHERE ur.user_id=%s
                ORDER BY rp.permission_key
                """,
                (user_id,),
            )
            return [row["permission_key"] for row in cur.fetchall()]

    def primary_role(conn, user_id: UUID | str) -> str | None:
        roles = user_roles(conn, user_id)
        for preferred in ("owner", "admin"):
            if any(role["key"] == preferred for role in roles):
                return preferred
        return roles[0]["key"] if roles else None

    def role_exists(conn, role_key: str) -> bool:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM roles WHERE key=%s", (role_key,))
            return cur.fetchone() is not None

    def active_owner_count(conn) -> int:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(DISTINCT u.id) AS count
                FROM users u
                JOIN user_roles ur ON ur.user_id = u.id
                JOIN roles r ON r.id = ur.role_id
                WHERE u.status = 'active'
                  AND r.key = 'owner'
                """
            )
            row = cur.fetchone()
        return int(row["count"] if row else 0)

    def user_admin_row(conn, user_id: UUID | str) -> dict[str, Any] | None:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.display_name,
                    u.first_name,
                    u.last_name,
                    u.status,
                    u.created_at,
                    u.updated_at,
                    COUNT(c.id)::int AS credential_count,
                    MAX(c.last_used_at) AS last_credential_used_at
                FROM users u
                LEFT JOIN passkey_credentials c ON c.user_id = u.id
                WHERE u.id=%s
                GROUP BY u.id
                """,
                (user_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        row["roles"] = user_roles(conn, row["id"])
        row["role"] = primary_role(conn, row["id"])
        row["permissions"] = user_permissions(conn, row["id"])
        return row

    def managed_roles(conn) -> list[dict[str, Any]]:
        if not table_exists(conn, "roles"):
            return []
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, r.key, r.name, r.description, r.system, rp.permission_key
                FROM roles r
                LEFT JOIN role_permissions rp ON rp.role_id = r.id
                ORDER BY r.system DESC, r.name, rp.permission_key
                """
            )
            rows = cur.fetchall()
        by_id: dict[Any, dict[str, Any]] = {}
        for row in rows:
            role_id = row["id"]
            role = by_id.setdefault(
                role_id,
                {
                    "id": role_id,
                    "key": row["key"],
                    "name": row["name"],
                    "description": row["description"],
                    "system": row["system"],
                    "permissions": [],
                },
            )
            if row.get("permission_key"):
                role["permissions"].append(row["permission_key"])
        return list(by_id.values())

    def require_admin(conn) -> dict[str, Any]:
        if not auth_enabled(conn):
            return {"id": None, "username": "system", "role": "owner"}
        user = current_user(conn)
        if not user:
            raise next_api_error("Unauthorized", 401)
        role = primary_role(conn, user["id"])
        if role not in {"owner", "admin"}:
            raise next_api_error("Admin access required", 403)
        user["role"] = role
        return user

    def require_owner(conn) -> dict[str, Any]:
        user = current_user(conn)
        if not user:
            raise next_api_error("Unauthorized", 401)
        role = primary_role(conn, user["id"])
        if role != "owner":
            raise next_api_error("Owner access required", 403)
        user["role"] = role
        return user

    def ownership_transfer_target(conn, owner: dict[str, Any], target_user_id: UUID) -> dict[str, Any]:
        if str(owner["id"]) == str(target_user_id):
            raise next_api_error("Ownership can only be transferred to another user", 400)
        target = user_admin_row(conn, target_user_id)
        if not target:
            raise next_api_error("Target user not found", 404)
        if target.get("status") != "active":
            raise next_api_error("Ownership can only be transferred to an active user", 400)
        if target.get("role") not in {"admin", "owner"}:
            raise next_api_error("Target user must have an admin-like role before ownership transfer", 400)
        return target

    def verify_step_up_assertion(
        conn,
        *,
        challenge_key: str,
        expected_user_id: UUID | str,
        credential: dict[str, Any],
    ) -> dict[str, Any]:
        credential_id = str(credential.get("id") or "")
        if not credential_id:
            raise next_api_error("Credential id is required", 400)
        challenge = pop_challenge(conn, challenge_key)
        if not challenge:
            raise next_api_error("No pending ownership transfer challenge", 400)

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.*, u.username, u.status AS user_status
                FROM passkey_credentials c
                JOIN users u ON u.id = c.user_id
                WHERE c.id=%s
                """,
                (credential_id,),
            )
            stored = cur.fetchone()
        if not stored:
            raise next_api_error("Unknown credential", 400)
        if stored["user_status"] != "active":
            raise next_api_error("User is disabled", 403)
        if str(stored["user_id"]) != str(expected_user_id):
            raise next_api_error("Ownership transfer must be approved with the current owner's passkey", 403)

        try:
            client_data_raw = _b64url_decode(credential["response"]["clientDataJSON"])
            client_data = json.loads(client_data_raw)
            if client_data.get("type") != "webauthn.get":
                raise ValueError("Wrong type in clientDataJSON")
            if _b64url_decode(client_data["challenge"]) != challenge:
                raise ValueError("Challenge mismatch")
            incoming_origin = str(client_data.get("origin") or "").rstrip("/")
            if incoming_origin not in _rp_origins():
                raise ValueError(f"Origin not allowed: {incoming_origin}")

            auth_data = _b64url_decode(credential["response"]["authenticatorData"])
            signature = _b64url_decode(credential["response"]["signature"])
            client_data_hash = hashlib.sha256(client_data_raw).digest()
            expected_rp_hash = hashlib.sha256(_rp_id().encode("utf-8")).digest()
            if auth_data[:32] != expected_rp_hash:
                raise ValueError("RP ID hash mismatch")
            _verify_signature(bytes(stored["public_key"]), auth_data, client_data_hash, signature)
            _, _, new_sign_count = _parse_auth_data(auth_data)
        except Exception as exc:
            raise next_api_error(f"Verification failed: {exc}", 400) from exc

        stored["new_sign_count"] = new_sign_count
        return stored

    def can_register_for_existing_user(conn, caller: dict[str, Any] | None, target_user_id: UUID) -> bool:
        if not caller:
            return False
        if str(caller["id"]) == str(target_user_id):
            return True
        return primary_role(conn, caller["id"]) in {"owner", "admin"}

    def assign_role(conn, user_id: UUID | str, role_key: str) -> None:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_roles (user_id, role_id, scope_type, scope_id)
                SELECT %s, roles.id, 'global', ''
                FROM roles
                WHERE roles.key=%s
                ON CONFLICT DO NOTHING
                """,
                (user_id, role_key),
            )

    def store_challenge(conn, key: str, challenge: bytes) -> None:
        expires_at = _utcnow() + timedelta(minutes=5)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM auth_challenges WHERE expires_at < now()")
            cur.execute(
                """
                INSERT INTO auth_challenges (key, challenge, created_at, expires_at)
                VALUES (%s, %s, now(), %s)
                ON CONFLICT (key) DO UPDATE SET
                    challenge=EXCLUDED.challenge,
                    created_at=now(),
                    expires_at=EXCLUDED.expires_at
                """,
                (key, challenge, expires_at),
            )

    def pop_challenge(conn, key: str) -> bytes | None:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM auth_challenges
                WHERE key=%s AND expires_at > now()
                RETURNING challenge
                """,
                (key,),
            )
            row = cur.fetchone()
        return bytes(row["challenge"]) if row else None

    def validate_invite(conn, username: str, invite_code: str) -> dict[str, Any] | None:
        if not invite_code:
            return None
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, username
                FROM invite_codes
                WHERE code_hash=%s
                  AND username=%s
                  AND used_at IS NULL
                  AND expires_at > now()
                """,
                (_hash_invite_code(invite_code), username),
            )
            return cur.fetchone()

    def auth_status_payload(conn) -> dict[str, Any]:
        user_count = count_table(conn, "users")
        credential_count = count_table(conn, "passkey_credentials")
        is_configured_auth_enabled = configured_auth_enabled(conn)
        is_auth_ready = user_count > 0 and credential_count > 0
        user = current_user(conn)
        role = primary_role(conn, user["id"]) if user else None
        return {
            "auth_enabled": is_configured_auth_enabled and is_auth_ready,
            "configured_auth_enabled": is_configured_auth_enabled,
            "auth_ready": is_auth_ready,
            "setup_required": not is_auth_ready,
            "registration_enabled": registration_enabled(conn) or not is_auth_ready,
            "has_users": user_count > 0,
            "has_credentials": credential_count > 0,
            "user_count": user_count,
            "credential_count": credential_count,
            "rp_id": _rp_id(),
            "rp_name": _rp_name(),
            "rp_origins": _rp_origins(),
            "authenticated": bool(user),
            "user_id": user["id"] if user else None,
            "username": user["username"] if user else None,
            "display_name": user.get("display_name") if user else None,
            "role": role,
        }

    def route(*rules: str, methods: list[str] | None = None):
        def decorator(func):
            endpoint_base = f"next_auth_{func.__name__}"
            for index, rule in enumerate(rules):
                app.add_url_rule(rule, f"{endpoint_base}_{index}", func, methods=methods)
            return func

        return decorator

    @route("/.well-known/webauthn", methods=["GET"])
    def webauthn_well_known():
        return jsonify({"origins": _rp_origins()})

    @route("/api/next/auth/status", "/api/auth/status", methods=["GET"])
    def auth_status():
        with connect() as conn:
            return response({"status": "ok", **auth_status_payload(conn)})

    @route("/api/next/auth/me", "/api/auth/me", methods=["GET"])
    def auth_me():
        with connect() as conn:
            user = current_user(conn)
            if not user:
                return response({"authenticated": False, "auth_enabled": auth_enabled(conn)})
            role = primary_role(conn, user["id"])
            roles = user_roles(conn, user["id"])
            permissions = user_permissions(conn, user["id"])
            return response(
                {
                    "authenticated": True,
                    "id": user["id"],
                    "username": user["username"],
                    "display_name": user.get("display_name"),
                    "first_name": user.get("first_name"),
                    "last_name": user.get("last_name"),
                    "role": role,
                    "roles": roles,
                    "permissions": permissions,
                }
            )

    @route("/api/next/auth/register/options", "/api/auth/register/options", methods=["POST"])
    def register_options():
        body = request.get_json(silent=True) or {}
        try:
            username = _normalize_username(body.get("username") or "admin")
        except ValueError as exc:
            raise next_api_error(str(exc), 400) from exc
        display_name = str(body.get("display_name") or username).strip() or username
        invite_code = str(body.get("invite_code") or "").strip()

        with connect() as conn:
            if not table_exists(conn, "users") or not table_exists(conn, "passkey_credentials"):
                raise next_api_error("Auth tables are not available", 503)
            has_users = count_table(conn, "users") > 0
            has_credentials = count_table(conn, "passkey_credentials") > 0
            caller = current_user(conn)
            if has_users and has_credentials and not caller and not registration_enabled(conn):
                invite = validate_invite(conn, username, invite_code)
                if not invite:
                    raise next_api_error("Invalid or expired invite code", 403)

            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE username=%s", (username,))
                user = cur.fetchone()
                user_id = user["id"] if user else uuid4()
                cur.execute(
                    """
                    SELECT id FROM passkey_credentials
                    WHERE user_id=%s
                    ORDER BY created_at
                    """,
                    (user_id,),
                )
                existing_credentials = cur.fetchall()
            if (
                has_users
                and has_credentials
                and user
                and not can_register_for_existing_user(conn, caller, user_id)
            ):
                invite = validate_invite(conn, username, invite_code)
                if not invite:
                    raise next_api_error("Login or a valid invite code is required to add a passkey", 403)
            challenge = _make_challenge()
            with conn.transaction():
                store_challenge(conn, f"register:{user_id}", challenge)

        options = {
            "rp": {"name": _rp_name(), "id": _rp_id()},
            "user": {
                "id": _b64url_encode(str(user_id).encode("utf-8")),
                "name": username,
                "displayName": display_name,
            },
            "challenge": _b64url_encode(challenge),
            "pubKeyCredParams": [{"type": "public-key", "alg": -7}],
            "timeout": 60000,
            "authenticatorSelection": {
                "residentKey": "preferred",
                "userVerification": "preferred",
            },
            "excludeCredentials": [
                {"type": "public-key", "id": row["id"]} for row in existing_credentials
            ],
            "attestation": "none",
        }
        return response({"status": "ok", "user_id": user_id, "username": username, "options": options})

    @route("/api/next/auth/register/verify", "/api/auth/register/verify", methods=["POST"])
    def register_verify():
        body = request.get_json(silent=True) or {}
        user_id = _parse_uuid(body.get("user_id"))
        if not user_id:
            raise next_api_error("user_id is required", 400)
        try:
            username = _normalize_username(body.get("username") or "admin")
        except ValueError as exc:
            raise next_api_error(str(exc), 400) from exc
        display_name = str(body.get("display_name") or username).strip() or username
        credential_name = str(body.get("credential_name") or "Passkey").strip() or "Passkey"
        credential = body.get("credential") or {}
        invite_code = str(body.get("invite_code") or "").strip()

        with connect() as conn:
            challenge = pop_challenge(conn, f"register:{user_id}")
            if not challenge:
                raise next_api_error("No pending challenge", 400)

            try:
                client_data_raw = _b64url_decode(credential["response"]["clientDataJSON"])
                client_data = json.loads(client_data_raw)
                if client_data.get("type") != "webauthn.create":
                    raise ValueError("Wrong type in clientDataJSON")
                if _b64url_decode(client_data["challenge"]) != challenge:
                    raise ValueError("Challenge mismatch")
                incoming_origin = str(client_data.get("origin") or "").rstrip("/")
                if incoming_origin not in _rp_origins():
                    raise ValueError(f"Origin not allowed: {incoming_origin}")

                credential_id, cose_key_bytes, auth_data, sign_count = _parse_attestation_object(
                    credential["response"]["attestationObject"]
                )
                expected_rp_hash = hashlib.sha256(_rp_id().encode("utf-8")).digest()
                if auth_data[:32] != expected_rp_hash:
                    raise ValueError("RP ID hash mismatch")
                credential_id_b64 = _b64url_encode(credential_id)
            except Exception as exc:
                raise next_api_error(f"Verification failed: {exc}", 400) from exc

            with conn.transaction():
                has_users = count_table(conn, "users") > 0
                has_credentials = count_table(conn, "passkey_credentials") > 0
                caller = current_user(conn)
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM users WHERE id=%s", (user_id,))
                    existing_user = cur.fetchone()
                if (
                    existing_user
                    and has_users
                    and has_credentials
                    and not can_register_for_existing_user(conn, caller, user_id)
                ):
                    invite = validate_invite(conn, username, invite_code)
                    if not invite:
                        raise next_api_error("Login or a valid invite code is required to add a passkey", 403)
                if (
                    has_users
                    and has_credentials
                    and not existing_user
                    and not caller
                    and not registration_enabled(conn)
                ):
                    invite = validate_invite(conn, username, invite_code)
                    if not invite:
                        raise next_api_error("Invalid or expired invite code", 403)

                if not existing_user:
                    role_key = "owner" if not has_users or not has_credentials else "member"
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO users (id, username, display_name, status, created_at, updated_at)
                            VALUES (%s, %s, %s, 'active', now(), now())
                            """,
                            (user_id, username, display_name),
                        )
                    assign_role(conn, user_id, role_key)
                    if invite_code:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE invite_codes
                                SET used_at=now(), used_by=%s
                                WHERE code_hash=%s AND username=%s AND used_at IS NULL
                                """,
                                (user_id, _hash_invite_code(invite_code), username),
                            )
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO passkey_credentials (
                            id,
                            user_id,
                            public_key,
                            sign_count,
                            credential_name,
                            transports,
                            created_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, now())
                        ON CONFLICT (id) DO UPDATE SET
                            user_id=EXCLUDED.user_id,
                            public_key=EXCLUDED.public_key,
                            sign_count=EXCLUDED.sign_count,
                            credential_name=EXCLUDED.credential_name
                        """,
                        (
                            credential_id_b64,
                            user_id,
                            cose_key_bytes,
                            sign_count,
                            credential_name,
                            Jsonb(credential.get("response", {}).get("transports") or []),
                        ),
                    )
                    if invite_code:
                        cur.execute(
                            """
                            UPDATE invite_codes
                            SET used_at=COALESCE(used_at, now()), used_by=COALESCE(used_by, %s)
                            WHERE code_hash=%s AND username=%s
                            """,
                            (user_id, _hash_invite_code(invite_code), username),
                        )
                set_setting(conn, "auth_enabled", True)

        token = _create_token(str(user_id), username)
        return cookie_response({"status": "ok", "token": token, "username": username}, token)

    @route("/api/next/auth/login/options", "/api/auth/login/options", methods=["POST"])
    def login_options():
        with connect() as conn:
            if not table_exists(conn, "passkey_credentials"):
                raise next_api_error("Auth tables are not available", 503)
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM passkey_credentials ORDER BY created_at")
                credentials = cur.fetchall()
            challenge = _make_challenge()
            with conn.transaction():
                store_challenge(conn, "login", challenge)

        options = {
            "challenge": _b64url_encode(challenge),
            "timeout": 60000,
            "rpId": _rp_id(),
            "allowCredentials": [
                {"type": "public-key", "id": row["id"]} for row in credentials
            ],
            "userVerification": "preferred",
        }
        return response({"status": "ok", "options": options})

    @route("/api/next/auth/login/verify", "/api/auth/login/verify", methods=["POST"])
    def login_verify():
        body = request.get_json(silent=True) or {}
        credential = body.get("credential") or {}
        credential_id = str(credential.get("id") or "")
        if not credential_id:
            raise next_api_error("Credential id is required", 400)

        with connect() as conn:
            challenge = pop_challenge(conn, "login")
            if not challenge:
                raise next_api_error("No pending challenge", 400)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.*, u.username, u.status AS user_status
                    FROM passkey_credentials c
                    JOIN users u ON u.id = c.user_id
                    WHERE c.id=%s
                    """,
                    (credential_id,),
                )
                stored = cur.fetchone()
            if not stored:
                raise next_api_error("Unknown credential", 400)
            if stored["user_status"] != "active":
                raise next_api_error("User is disabled", 403)

            try:
                client_data_raw = _b64url_decode(credential["response"]["clientDataJSON"])
                client_data = json.loads(client_data_raw)
                if client_data.get("type") != "webauthn.get":
                    raise ValueError("Wrong type in clientDataJSON")
                if _b64url_decode(client_data["challenge"]) != challenge:
                    raise ValueError("Challenge mismatch")
                incoming_origin = str(client_data.get("origin") or "").rstrip("/")
                if incoming_origin not in _rp_origins():
                    raise ValueError(f"Origin not allowed: {incoming_origin}")

                auth_data = _b64url_decode(credential["response"]["authenticatorData"])
                signature = _b64url_decode(credential["response"]["signature"])
                client_data_hash = hashlib.sha256(client_data_raw).digest()
                expected_rp_hash = hashlib.sha256(_rp_id().encode("utf-8")).digest()
                if auth_data[:32] != expected_rp_hash:
                    raise ValueError("RP ID hash mismatch")
                _verify_signature(bytes(stored["public_key"]), auth_data, client_data_hash, signature)
                _, _, new_sign_count = _parse_auth_data(auth_data)
            except Exception as exc:
                raise next_api_error(f"Verification failed: {exc}", 400) from exc

            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE passkey_credentials
                        SET sign_count=%s, last_used_at=now()
                        WHERE id=%s
                        """,
                        (new_sign_count, credential_id),
                    )

        token = _create_token(str(stored["user_id"]), stored["username"])
        return cookie_response({"status": "ok", "token": token, "username": stored["username"]}, token)

    @route("/api/next/auth/logout", "/api/auth/logout", methods=["POST"])
    def auth_logout():
        return clear_cookie_response({"status": "ok", "authenticated": False})

    @route("/api/next/auth/owner/transfer/options", "/api/auth/owner/transfer/options", methods=["POST"])
    def ownership_transfer_options():
        body = request.get_json(silent=True) or {}
        target_user_id = _parse_uuid(body.get("target_user_id") or body.get("targetUserId"))
        if not target_user_id:
            raise next_api_error("target_user_id is required", 400)

        with connect() as conn:
            owner = require_owner(conn)
            target = ownership_transfer_target(conn, owner, target_user_id)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id
                    FROM passkey_credentials
                    WHERE user_id=%s
                    ORDER BY created_at
                    """,
                    (owner["id"],),
                )
                credentials = cur.fetchall()
            if not credentials:
                raise next_api_error("Current owner has no passkeys to approve the transfer", 400)
            challenge = _make_challenge()
            challenge_key = f"owner_transfer:{owner['id']}:{target_user_id}"
            with conn.transaction():
                store_challenge(conn, challenge_key, challenge)

        options = {
            "challenge": _b64url_encode(challenge),
            "timeout": 60000,
            "rpId": _rp_id(),
            "allowCredentials": [
                {"type": "public-key", "id": row["id"]} for row in credentials
            ],
            "userVerification": "preferred",
        }
        return response(
            {
                "status": "ok",
                "target_user": {
                    "id": target["id"],
                    "username": target["username"],
                    "display_name": target.get("display_name"),
                    "role": target.get("role"),
                },
                "options": options,
            }
        )

    @route("/api/next/auth/owner/transfer/verify", "/api/auth/owner/transfer/verify", methods=["POST"])
    def ownership_transfer_verify():
        body = request.get_json(silent=True) or {}
        target_user_id = _parse_uuid(body.get("target_user_id") or body.get("targetUserId"))
        if not target_user_id:
            raise next_api_error("target_user_id is required", 400)
        credential = body.get("credential") or {}

        with connect() as conn:
            owner = require_owner(conn)
            target = ownership_transfer_target(conn, owner, target_user_id)
            challenge_key = f"owner_transfer:{owner['id']}:{target_user_id}"
            stored = verify_step_up_assertion(
                conn,
                challenge_key=challenge_key,
                expected_user_id=owner["id"],
                credential=credential,
            )
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE passkey_credentials
                        SET sign_count=%s, last_used_at=now()
                        WHERE id=%s
                        """,
                        (stored["new_sign_count"], stored["id"]),
                    )
                    cur.execute(
                        """
                        DELETE FROM user_roles
                        WHERE user_id=%s
                          AND role_id IN (SELECT id FROM roles WHERE key='owner')
                          AND scope_type='global'
                          AND scope_id=''
                        """,
                        (owner["id"],),
                    )
                    cur.execute("UPDATE users SET updated_at=now() WHERE id IN (%s, %s)", (owner["id"], target_user_id))
                assign_role(conn, target_user_id, "owner")
                assign_role(conn, owner["id"], "admin")
            updated_owner = user_admin_row(conn, owner["id"])
            updated_target = user_admin_row(conn, target_user_id)
            payload = auth_status_payload(conn)
        return response(
            {
                "status": "ok",
                "message": "Ownership transferred",
                "previous_owner": updated_owner,
                "new_owner": updated_target,
                "auth": payload,
            }
        )

    @route("/api/next/auth/roles", "/api/auth/roles", methods=["GET"])
    def roles():
        with connect() as conn:
            require_admin(conn)
            return response({"status": "ok", "roles": managed_roles(conn)})

    @route("/api/next/auth/users", "/api/auth/users", methods=["GET"])
    def users():
        with connect() as conn:
            require_admin(conn)
            if not table_exists(conn, "users"):
                return response({"status": "ok", "users": []})
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        u.id,
                        u.username,
                        u.display_name,
                        u.first_name,
                        u.last_name,
                        u.status,
                        u.created_at,
                        u.updated_at,
                        COUNT(c.id)::int AS credential_count,
                        MAX(c.last_used_at) AS last_credential_used_at
                    FROM users u
                    LEFT JOIN passkey_credentials c ON c.user_id = u.id
                    GROUP BY u.id
                    ORDER BY lower(u.username)
                    """
                )
                rows = cur.fetchall()
            for row in rows:
                row["roles"] = user_roles(conn, row["id"])
                row["role"] = primary_role(conn, row["id"])
                row["permissions"] = user_permissions(conn, row["id"])
            return response({"status": "ok", "users": rows, "roles": managed_roles(conn)})

    @route("/api/next/auth/users/<user_id>", "/api/auth/users/<user_id>", methods=["PATCH"])
    def update_user(user_id: str):
        user_uuid = _parse_uuid(user_id)
        if not user_uuid:
            raise next_api_error("Invalid user id", 400)
        body = request.get_json(silent=True) or {}
        status = str(body.get("status") or "").strip().lower()
        display_name = body.get("display_name")

        with connect() as conn:
            admin = require_admin(conn)
            target = user_admin_row(conn, user_uuid)
            if not target:
                raise next_api_error("User not found", 404)
            target_role = target.get("role")
            if target_role == "owner" and admin.get("role") != "owner":
                raise next_api_error("Only owners can modify owner accounts", 403)
            if str(admin.get("id")) == str(user_uuid) and status and status != "active":
                raise next_api_error("You cannot disable your own account", 400)
            if target_role == "owner" and status and status != "active" and active_owner_count(conn) <= 1:
                raise next_api_error("The last active owner cannot be disabled", 400)

            assignments: list[str] = []
            params: list[Any] = []
            if status:
                if status not in {"active", "disabled"}:
                    raise next_api_error("Status must be active or disabled", 400)
                assignments.append("status=%s")
                params.append(status)
            if display_name is not None:
                assignments.append("display_name=%s")
                params.append(str(display_name or "").strip() or target["username"])
            if not assignments:
                raise next_api_error("No user fields supplied", 400)

            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE users SET {', '.join(assignments)}, updated_at=now() WHERE id=%s",
                        (*params, user_uuid),
                    )
            updated = user_admin_row(conn, user_uuid)
        return response({"status": "ok", "user": updated})

    @route("/api/next/auth/users/<user_id>/role", "/api/auth/users/<user_id>/role", methods=["PUT", "PATCH"])
    def update_user_role(user_id: str):
        user_uuid = _parse_uuid(user_id)
        if not user_uuid:
            raise next_api_error("Invalid user id", 400)
        body = request.get_json(silent=True) or {}
        role_key = str(body.get("role") or body.get("role_key") or "").strip().lower()
        if not role_key:
            raise next_api_error("role is required", 400)

        with connect() as conn:
            admin = require_admin(conn)
            if not role_exists(conn, role_key):
                raise next_api_error("Unknown role", 400)
            target = user_admin_row(conn, user_uuid)
            if not target:
                raise next_api_error("User not found", 404)
            target_role = target.get("role")
            if str(admin.get("id")) == str(user_uuid) and role_key not in {"owner", "admin"}:
                raise next_api_error("You cannot remove your own admin access", 400)
            if role_key == "owner" and admin.get("role") != "owner":
                raise next_api_error("Only owners can assign the owner role", 403)
            if target_role == "owner" and admin.get("role") != "owner":
                raise next_api_error("Only owners can modify owner accounts", 403)
            if target_role == "owner" and role_key != "owner" and active_owner_count(conn) <= 1:
                raise next_api_error("The last active owner cannot be demoted", 400)

            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM user_roles WHERE user_id=%s AND scope_type='global' AND scope_id=''",
                        (user_uuid,),
                    )
                assign_role(conn, user_uuid, role_key)
                with conn.cursor() as cur:
                    cur.execute("UPDATE users SET updated_at=now() WHERE id=%s", (user_uuid,))
            updated = user_admin_row(conn, user_uuid)
        return response({"status": "ok", "user": updated})

    @route("/api/next/auth/users/<user_id>", "/api/auth/users/<user_id>", methods=["DELETE"])
    def delete_user(user_id: str):
        user_uuid = _parse_uuid(user_id)
        if not user_uuid:
            raise next_api_error("Invalid user id", 400)
        with connect() as conn:
            admin = require_admin(conn)
            target = user_admin_row(conn, user_uuid)
            if not target:
                raise next_api_error("User not found", 404)
            target_role = target.get("role")
            if str(admin.get("id")) == str(user_uuid):
                raise next_api_error("You cannot delete your own account", 400)
            if target_role == "owner" and admin.get("role") != "owner":
                raise next_api_error("Only owners can delete owner accounts", 403)
            if target_role == "owner" and active_owner_count(conn) <= 1:
                raise next_api_error("The last active owner cannot be deleted", 400)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM users WHERE id=%s", (user_uuid,))
        return response({"status": "deleted"})

    @route("/api/next/auth/credentials", "/api/auth/credentials", methods=["GET"])
    def credentials():
        with connect() as conn:
            user = current_user(conn)
            params: list[Any] = []
            where = ""
            if auth_enabled(conn):
                if not user:
                    raise next_api_error("Unauthorized", 401)
                if primary_role(conn, user["id"]) not in {"owner", "admin"}:
                    where = "WHERE c.user_id=%s"
                    params.append(user["id"])
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT c.id, c.credential_name, c.created_at, c.last_used_at,
                           c.sign_count, u.id AS user_id, u.username
                    FROM passkey_credentials c
                    JOIN users u ON u.id = c.user_id
                    {where}
                    ORDER BY c.created_at DESC
                    """,
                    params,
                )
                rows = cur.fetchall()
        return response({"status": "ok", "credentials": rows})

    @route("/api/next/auth/credentials/<credential_id>", "/api/auth/credentials/<credential_id>", methods=["DELETE"])
    def delete_credential(credential_id: str):
        with connect() as conn:
            user = current_user(conn)
            if auth_enabled(conn) and not user:
                raise next_api_error("Unauthorized", 401)
            role = primary_role(conn, user["id"]) if user else "owner"
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT user_id FROM passkey_credentials WHERE id=%s", (credential_id,))
                    credential_row = cur.fetchone()
                    if not credential_row:
                        raise next_api_error("Credential not found", 404)
                    if role not in {"owner", "admin"} and credential_row["user_id"] != user["id"]:
                        raise next_api_error("Not authorized", 403)
                    cur.execute("DELETE FROM passkey_credentials WHERE id=%s", (credential_id,))
                    cur.execute("SELECT COUNT(*) AS count FROM passkey_credentials")
                    remaining = int(cur.fetchone()["count"])
                if remaining == 0:
                    set_setting(conn, "auth_enabled", False)
        return response({"status": "deleted", "remaining": remaining})

    @route("/api/next/auth/toggle", "/api/auth/toggle", methods=["POST"])
    def toggle_auth():
        body = request.get_json(silent=True) or {}
        enabled = bool(body.get("enabled", False))
        with connect() as conn:
            require_admin(conn)
            if enabled and count_table(conn, "passkey_credentials") == 0:
                raise next_api_error("Register a passkey before enabling authentication", 400)
            with conn.transaction():
                set_setting(conn, "auth_enabled", enabled)
        return response({"status": "ok", "auth_enabled": enabled})

    @route("/api/next/auth/registration", "/api/auth/registration", methods=["POST"])
    def toggle_registration():
        body = request.get_json(silent=True) or {}
        enabled = bool(body.get("enabled", False))
        with connect() as conn:
            require_admin(conn)
            with conn.transaction():
                set_setting(conn, "registration_enabled", enabled)
            payload = auth_status_payload(conn)
        return response({"status": "ok", **payload})

    @route("/api/next/auth/owner/settings", "/api/auth/owner/settings", methods=["GET", "POST"])
    def owner_settings():
        with connect() as conn:
            require_owner(conn)
            if request.method == "POST":
                body = request.get_json(silent=True) or {}
                if "movievault_contribution_enabled" in body:
                    enabled = bool(body.get("movievault_contribution_enabled"))
                    with conn.transaction():
                        set_setting(conn, "movievault_contribution_enabled", enabled)
            settings = {
                "movievault_contribution_enabled": bool(
                    setting_value(conn, "movievault_contribution_enabled", False)
                )
            }
        return response({"status": "ok", "settings": settings})

    @route("/api/next/auth/invite", "/api/auth/invite", methods=["POST"])
    def create_invite():
        body = request.get_json(silent=True) or {}
        username = _normalize_username(body.get("username"))
        with connect() as conn:
            admin = require_admin(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE username=%s", (username,))
                if cur.fetchone():
                    raise next_api_error("User already exists", 409)
            alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
            raw_code = "".join(secrets.choice(alphabet) for _ in range(12))
            code_display = f"{raw_code[:4]}-{raw_code[4:8]}-{raw_code[8:12]}"
            expires_at = _utcnow() + timedelta(hours=48)
            invite_id = uuid4()
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO invite_codes (
                            id, code_hash, username, created_by, created_at, expires_at
                        )
                        VALUES (%s, %s, %s, %s, now(), %s)
                        """,
                        (
                            invite_id,
                            hashlib.sha256(raw_code.encode("utf-8")).hexdigest(),
                            username,
                            admin.get("id"),
                            expires_at,
                        ),
                    )
        return response(
            {
                "status": "ok",
                "id": invite_id,
                "code": code_display,
                "username": username,
                "expires_at": expires_at,
            },
            201,
        )

    @route("/api/next/auth/invite", "/api/auth/invite", methods=["GET"])
    def list_invites():
        with connect() as conn:
            require_admin(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, username, created_at, expires_at, used_at, used_by
                    FROM invite_codes
                    ORDER BY created_at DESC
                    """
                )
                rows = cur.fetchall()
        return response({"status": "ok", "invites": rows})

    @route("/api/next/auth/invite/<invite_id>", "/api/auth/invite/<invite_id>", methods=["DELETE"])
    def delete_invite(invite_id: str):
        invite_uuid = _parse_uuid(invite_id)
        if not invite_uuid:
            raise next_api_error("Invalid invite id", 400)
        with connect() as conn:
            require_admin(conn)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM invite_codes WHERE id=%s AND used_at IS NULL",
                        (invite_uuid,),
                    )
        return response({"status": "deleted"})
