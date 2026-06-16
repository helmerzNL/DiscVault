"""Passkey authentication endpoints for the PostgreSQL-backed Next runtime."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import struct
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlencode
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
from flask import Flask, jsonify, make_response, redirect, request
from psycopg.types.json import Jsonb


ConnectFactory = Callable[[], Any]
ResponseFactory = Callable[[dict[str, Any], int], Any]
TableExists = Callable[[Any, str], bool]

SESSION_COOKIE_NAME = "dv_next_session"
SESSION_COOKIE_MAX_AGE_SECONDS = 24 * 60 * 60
API_TOKEN_PREFIX = "dvapi_"
RBAC_MODE_SETTING = "rbac_mode"
RBAC_MODES = {"basic", "advanced"}
RBAC_BASIC_ROLE_KEYS = ("admin", "media_editor", "media_fan", "media_viewer", "member_groups")
RBAC_PROTECTED_ROLE_KEYS = ("owner", "admin", "media_editor", "media_fan", "media_viewer", "member_groups")
ROLE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
RECOVERY_CODE_GROUPS = 4
RECOVERY_CODE_GROUP_LENGTH = 4
MOBILE_AUTH_FLOW_TTL_SECONDS = 5 * 60
MOBILE_AUTH_CODE_TTL_SECONDS = 60
MOBILE_AUTH_ALLOWED_CALLBACK_SCHEMES = {"discvault"}
IOS_WEBCREDENTIAL_BUNDLE_ID = "HelmerNL.DiscVault"
MOBILE_AUTH_TOKEN_PERMISSIONS = (
    "api.read",
    "metadata.search",
    "collection.add",
    "collection.add_own",
    "collection.import",
    "collection.edit_all",
    "watchlist.manage",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    value += "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(value.encode("ascii"))


def _make_challenge() -> bytes:
    return secrets.token_bytes(32)


def next_normalize_recovery_code(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def next_recovery_code_hash(value: Any) -> str:
    normalized = next_normalize_recovery_code(value)
    return hashlib.sha256(f"disc-vault-next-recovery:{_jwt_secret()}:{normalized}".encode("utf-8")).hexdigest()


def next_generate_recovery_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(RECOVERY_CODE_GROUPS * RECOVERY_CODE_GROUP_LENGTH))
    return "-".join(
        raw[index : index + RECOVERY_CODE_GROUP_LENGTH]
        for index in range(0, len(raw), RECOVERY_CODE_GROUP_LENGTH)
    )


def next_generate_recovery_codes(count: int = 8) -> list[str]:
    codes: list[str] = []
    while len(codes) < count:
        code = next_generate_recovery_code()
        if code not in codes:
            codes.append(code)
    return codes


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


def _ios_webcredential_app_ids() -> list[str]:
    configured = os.environ.get("DISCVAULT_IOS_WEBCREDENTIAL_APPS", "")
    values = [item.strip() for item in configured.split(",") if item.strip()]
    if values:
        return values
    team_id = os.environ.get("APPLE_TEAM_ID", "").strip()
    if not team_id:
        return []
    return [f"{team_id}.{IOS_WEBCREDENTIAL_BUNDLE_ID}"]


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


def next_create_api_token_value() -> str:
    return f"{API_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


def next_api_token_hash(value: Any) -> str:
    token = str(value or "").strip()
    return hashlib.sha256(f"disc-vault-next-api-token:{_jwt_secret()}:{token}".encode("utf-8")).hexdigest()


def next_mobile_auth_code_hash(value: Any) -> str:
    code = str(value or "").strip()
    return hashlib.sha256(f"disc-vault-next-mobile-code:{_jwt_secret()}:{code}".encode("utf-8")).hexdigest()


def next_pkce_s256_challenge(code_verifier: Any) -> str:
    verifier = str(code_verifier or "").strip()
    return _b64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())


def _bearer_token() -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return ""


def _bearer_api_token() -> str:
    token = _bearer_token()
    return token if token.startswith(API_TOKEN_PREFIX) else ""


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
        api_token = _bearer_api_token()
        if api_token:
            return next_auth_current_api_token_user(conn, api_token)
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


def _auth_table_exists(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS table_name", (f"public.{table_name}",))
        row = cur.fetchone()
    return bool(row and row["table_name"])


def next_auth_current_api_token_user(conn, token: str) -> dict[str, Any] | None:
    if not _auth_table_exists(conn, "api_access_tokens") or not _auth_table_exists(conn, "users"):
        return None
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
                t.id AS api_token_id,
                t.name AS api_token_name,
                t.scopes AS api_token_scopes,
                t.permission_keys AS api_token_permission_keys
            FROM api_access_tokens t
            JOIN users u ON u.id = t.user_id
            WHERE t.token_hash=%s
              AND t.revoked_at IS NULL
              AND (t.expires_at IS NULL OR t.expires_at > now())
              AND u.status='active'
            """,
            (next_api_token_hash(token),),
        )
        row = cur.fetchone()
        if not row:
            return None
        cur.execute("UPDATE api_access_tokens SET last_used_at=now() WHERE id=%s", (row["api_token_id"],))
    row["apiToken"] = {
        "id": row.pop("api_token_id"),
        "name": row.pop("api_token_name"),
        "scopes": row.pop("api_token_scopes") or [],
        "permissionKeys": row.pop("api_token_permission_keys") or [],
    }
    return row


def _parse_uuid(value: Any) -> UUID | None:
    if value in (None, ""):
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


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

    def mobile_allowed_callback_schemes() -> set[str]:
        configured = os.environ.get("DISCVAULT_MOBILE_CALLBACK_SCHEMES", "")
        values = {item.strip().lower() for item in configured.split(",") if item.strip()}
        return values or MOBILE_AUTH_ALLOWED_CALLBACK_SCHEMES

    def mobile_cleanup(conn) -> None:
        if not table_exists(conn, "mobile_auth_codes") or not table_exists(conn, "mobile_auth_flows"):
            return
        with conn.cursor() as cur:
            cur.execute("DELETE FROM mobile_auth_codes WHERE expires_at < now()")
            cur.execute("DELETE FROM mobile_auth_flows WHERE expires_at < now()")

    def validate_mobile_flow_start_params() -> dict[str, str]:
        host = (request.headers.get("X-Forwarded-Host") or request.host or "").split(":", 1)[0].lower()
        if not _request_is_secure() and host not in {"localhost", "127.0.0.1", "::1"}:
            raise next_api_error("Mobile auth requires HTTPS", 400)
        callback_scheme = str(request.args.get("callback_scheme") or "").strip().lower()
        if callback_scheme not in mobile_allowed_callback_schemes():
            raise next_api_error("Callback scheme is not allowed", 400)
        code_challenge = str(request.args.get("code_challenge") or "").strip()
        code_challenge_method = str(request.args.get("code_challenge_method") or "").strip() or "S256"
        if code_challenge_method != "S256":
            raise next_api_error("Only PKCE S256 is supported", 400)
        if not code_challenge:
            raise next_api_error("code_challenge is required", 400)
        if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", code_challenge):
            raise next_api_error("code_challenge is not a valid PKCE challenge", 400)
        state = str(request.args.get("state") or "").strip()
        if len(state) > 512:
            raise next_api_error("state is too long", 400)
        return {
            "callback_scheme": callback_scheme,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
        }

    def mobile_callback_url(callback_scheme: str, code: str, state: str | None) -> str:
        params = {"code": code}
        if state:
            params["state"] = state
        return f"{callback_scheme}://auth-callback?{urlencode(params)}"

    def create_mobile_auth_code(
        conn,
        *,
        mobile_flow_id: UUID,
        user_id: UUID | str,
    ) -> dict[str, Any]:
        if not table_exists(conn, "mobile_auth_flows") or not table_exists(conn, "mobile_auth_codes"):
            raise next_api_error("Mobile auth tables are not available", 503)
        mobile_cleanup(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, callback_scheme, state, code_challenge, code_challenge_method, expires_at, used_at
                FROM mobile_auth_flows
                WHERE id=%s
                FOR UPDATE
                """,
                (mobile_flow_id,),
            )
            flow = cur.fetchone()
            if not flow or flow.get("used_at") is not None or flow["expires_at"] <= _utcnow():
                raise next_api_error("Mobile auth flow is expired or already used", 400)
            if flow["code_challenge_method"] != "S256":
                raise next_api_error("Mobile auth flow uses an unsupported PKCE method", 400)
            code = secrets.token_urlsafe(32)
            cur.execute(
                """
                INSERT INTO mobile_auth_codes (
                    code_hash,
                    user_id,
                    mobile_flow_id,
                    code_challenge,
                    state,
                    expires_at
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    next_mobile_auth_code_hash(code),
                    user_id,
                    flow["id"],
                    flow["code_challenge"],
                    flow.get("state"),
                    _utcnow() + timedelta(seconds=MOBILE_AUTH_CODE_TTL_SECONDS),
                ),
            )
            cur.execute("UPDATE mobile_auth_flows SET used_at=now() WHERE id=%s", (flow["id"],))
        return {
            "code": code,
            "callbackUrl": mobile_callback_url(flow["callback_scheme"], code, flow.get("state")),
            "state": flow.get("state"),
        }

    def issue_mobile_api_token(conn, *, user_id: UUID | str, username: str) -> dict[str, Any]:
        if not table_exists(conn, "api_access_tokens"):
            raise next_api_error("API token table is not available", 503)
        known_permissions = permission_keys_catalog(conn)
        role = primary_role(conn, user_id)
        user_permission_keys = set(user_permissions(conn, user_id))
        if role in {"owner", "admin"}:
            permission_keys = [key for key in MOBILE_AUTH_TOKEN_PERMISSIONS if key in known_permissions]
        else:
            permission_keys = [
                key
                for key in MOBILE_AUTH_TOKEN_PERMISSIONS
                if key in known_permissions and key in user_permission_keys
            ]
        if "api.read" not in permission_keys and "api.read" in known_permissions:
            permission_keys.insert(0, "api.read")
        scopes = sorted({key.split(".", 1)[0] for key in permission_keys})
        token_value = next_create_api_token_value()
        token_name = "DiscVault iOS"
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO api_access_tokens (
                    user_id,
                    name,
                    token_hash,
                    scopes,
                    permission_keys,
                    created_by,
                    expires_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, NULL)
                RETURNING id, name, scopes, permission_keys, created_at, last_used_at, expires_at, revoked_at
                """,
                (
                    user_id,
                    token_name,
                    next_api_token_hash(token_value),
                    Jsonb(scopes),
                    Jsonb(permission_keys),
                    user_id,
                ),
            )
            token_row = cur.fetchone()
        return {"token": token_value, "tokenRow": token_row, "permissionKeys": permission_keys, "scopes": scopes}

    def native_login_response(
        conn,
        *,
        user_id: UUID | str,
        username: str,
        display_name: str | None,
    ) -> dict[str, Any]:
        token_payload = issue_mobile_api_token(conn, user_id=user_id, username=username)
        role = primary_role(conn, user_id)
        effective_permission_keys = sorted(user_permissions(conn, user_id))
        token = token_payload["token"]
        user_payload = {
            "id": str(user_id),
            "username": username,
            "display_name": display_name,
            "displayName": display_name,
            "role": role,
            "avatar_url": None,
        }
        api_token_payload = {
            "id": str(token_payload["tokenRow"]["id"]),
            "name": token_payload["tokenRow"]["name"],
            "scopes": token_payload["tokenRow"]["scopes"] or [],
            "permission_keys": token_payload["tokenRow"]["permission_keys"] or [],
            "permissionKeys": token_payload["tokenRow"]["permission_keys"] or [],
            "created_at": token_payload["tokenRow"]["created_at"].isoformat()
            if token_payload["tokenRow"].get("created_at")
            else None,
            "expires_at": token_payload["tokenRow"]["expires_at"].isoformat()
            if token_payload["tokenRow"].get("expires_at")
            else None,
            "revoked_at": None,
        }
        return {
            "status": "ok",
            "token": token,
            "access_token": token,
            "refresh_token": None,
            "session": {
                "access_token": token,
                "refresh_token": None,
            },
            "username": username,
            "role": role,
            "tokenPermissionKeys": token_payload["permissionKeys"],
            "effectivePermissionKeys": effective_permission_keys,
            "api_token": api_token_payload,
            "apiToken": api_token_payload,
            "user": user_payload,
            "currentUser": {
                "id": str(user_id),
                "username": username,
                "displayName": display_name,
                "role": role,
            },
            "profile": {
                "id": str(user_id),
                "username": username,
                "display_name": display_name,
                "role": role,
            },
        }

    def permission_keys_catalog(conn) -> set[str]:
        if not table_exists(conn, "permissions"):
            return set(MOBILE_AUTH_TOKEN_PERMISSIONS)
        with conn.cursor() as cur:
            cur.execute("SELECT key FROM permissions")
            return {row["key"] for row in cur.fetchall()}

    def audit_event(
        conn,
        *,
        event_type: str,
        category: str = "security",
        actor: dict[str, Any] | None = None,
        target_type: str | None = None,
        target_id: Any = None,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not table_exists(conn, "audit_events"):
            return
        actor = actor or {}
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_events (
                    event_type,
                    category,
                    actor_user_id,
                    actor_username,
                    actor_role,
                    target_type,
                    target_id,
                    summary,
                    metadata,
                    request_ip,
                    user_agent
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    event_type,
                    category,
                    actor.get("id"),
                    actor.get("username"),
                    actor.get("role"),
                    target_type,
                    str(target_id) if target_id is not None else None,
                    summary,
                    Jsonb(_json_ready(metadata or {})),
                    request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip(),
                    request.headers.get("User-Agent"),
                ),
            )

    def configured_auth_enabled(conn) -> bool:
        return bool(setting_value(conn, "auth_enabled", False))

    def auth_ready(conn) -> bool:
        return count_table(conn, "users") > 0 and count_table(conn, "passkey_credentials") > 0

    def auth_enabled(conn) -> bool:
        return configured_auth_enabled(conn) and auth_ready(conn)

    def registration_enabled(conn) -> bool:
        return bool(setting_value(conn, "registration_enabled", True))

    def rbac_mode(conn) -> str:
        mode = str(setting_value(conn, RBAC_MODE_SETTING, "basic") or "basic").strip().lower()
        return mode if mode in RBAC_MODES else "basic"

    def set_rbac_mode(conn, mode: str) -> None:
        normalized = str(mode or "").strip().lower()
        if normalized not in RBAC_MODES:
            raise next_api_error("RBAC mode must be basic or advanced", 400)
        set_setting(conn, RBAC_MODE_SETTING, normalized)

    def feature_enabled(conn, feature_key: str, default: bool = True) -> bool:
        if not table_exists(conn, "feature_entitlements"):
            return default
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT enabled
                FROM feature_entitlements
                WHERE feature_key=%s
                  AND (expires_at IS NULL OR expires_at > now())
                """,
                (feature_key,),
            )
            row = cur.fetchone()
        return bool(row["enabled"]) if row else default

    def normalize_role_key(value: Any) -> str:
        role_key = str(value or "").strip().lower().replace("-", "_")
        if not ROLE_KEY_PATTERN.match(role_key):
            raise next_api_error(
                "Role key must start with a letter and contain only lowercase letters, numbers and underscores",
                400,
            )
        return role_key

    def basic_visible_role_keys() -> set[str]:
        return {"owner", *RBAC_BASIC_ROLE_KEYS}

    def basic_assignable_role_keys() -> set[str]:
        return set(RBAC_BASIC_ROLE_KEYS)

    def default_registration_role(conn) -> str:
        if role_exists(conn, "media_viewer"):
            return "media_viewer"
        return "member"

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

    def permission_catalog(conn) -> list[dict[str, Any]]:
        if not table_exists(conn, "permissions"):
            return []
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT key, domain, description
                FROM permissions
                ORDER BY domain, key
                """
            )
            return cur.fetchall()

    def permission_keys(conn) -> set[str]:
        return {row["key"] for row in permission_catalog(conn)}

    def managed_roles(conn, *, include_all: bool = False) -> list[dict[str, Any]]:
        if not table_exists(conn, "roles"):
            return []
        mode = rbac_mode(conn)
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
        roles = []
        for role in by_id.values():
            key = role["key"]
            is_basic_visible = key in basic_visible_role_keys()
            role["basicRole"] = is_basic_visible
            role["custom"] = not bool(role["system"])
            role["protected"] = key in RBAC_PROTECTED_ROLE_KEYS or key == "owner"
            role["assignable"] = (mode == "advanced" and key != "owner") or (
                mode == "basic" and key in basic_assignable_role_keys()
            )
            if include_all or is_basic_visible or role["assignable"]:
                roles.append(role)
        return roles

    def role_by_identifier(conn, identifier: str) -> dict[str, Any] | None:
        value = str(identifier or "").strip()
        role_uuid = _parse_uuid(value)
        if role_uuid:
            query = "r.id=%s"
            params: tuple[Any, ...] = (role_uuid,)
        else:
            query = "r.key=%s"
            params = (value.lower(),)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT r.id, r.key, r.name, r.description, r.system, rp.permission_key
                FROM roles r
                LEFT JOIN role_permissions rp ON rp.role_id = r.id
                WHERE {query}
                ORDER BY rp.permission_key
                """,
                params,
            )
            rows = cur.fetchall()
        if not rows:
            return None
        row = rows[0]
        permissions = [item["permission_key"] for item in rows if item.get("permission_key")]
        key = row["key"]
        return {
            "id": row["id"],
            "key": key,
            "name": row["name"],
            "description": row["description"],
            "system": row["system"],
            "permissions": permissions,
            "basicRole": key in basic_visible_role_keys(),
            "custom": not bool(row["system"]),
            "protected": key in RBAC_PROTECTED_ROLE_KEYS or key == "owner",
            "assignable": (rbac_mode(conn) == "advanced" and key != "owner")
            or (rbac_mode(conn) == "basic" and key in basic_assignable_role_keys()),
        }

    def validate_permission_selection(conn, values: Any) -> list[str]:
        if not isinstance(values, list):
            raise next_api_error("permissions must be an array", 400)
        known = permission_keys(conn)
        selected: list[str] = []
        for raw in values:
            key = str(raw or "").strip()
            if key not in known:
                raise next_api_error(f"Unknown permission: {key}", 400)
            if key not in selected:
                selected.append(key)
        return selected

    def custom_role_mutations_allowed(conn) -> None:
        if rbac_mode(conn) != "advanced":
            raise next_api_error("Switch to Advanced RBAC mode before managing custom roles", 400)
        if not feature_enabled(conn, "rbac.custom_roles", True):
            raise next_api_error("Custom roles are not enabled by the current license", 403)

    def role_assignable(conn, role_key: str, actor_role: str | None) -> bool:
        if role_key == "owner":
            return False
        if rbac_mode(conn) == "basic":
            return role_key in RBAC_BASIC_ROLE_KEYS
        return True

    def normalize_role_key_list(values: Any) -> list[str]:
        if not isinstance(values, list):
            raise next_api_error("roles must be an array", 400)
        role_keys: list[str] = []
        for value in values:
            role_key = normalize_role_key(value)
            if role_key not in role_keys:
                role_keys.append(role_key)
        if not role_keys:
            raise next_api_error("At least one role is required", 400)
        return role_keys

    def set_user_global_roles(
        conn,
        *,
        actor: dict[str, Any],
        user_id: UUID,
        role_keys: list[str],
    ) -> dict[str, Any]:
        target = user_admin_row(conn, user_id)
        if not target:
            raise next_api_error("User not found", 404)
        actor_role = actor.get("role")
        target_role = target.get("role")

        if "owner" in role_keys or target_role == "owner":
            raise next_api_error("Owner role changes must use the ownership transfer flow", 400)

        for role_key in role_keys:
            if not role_exists(conn, role_key):
                raise next_api_error(f"Unknown role: {role_key}", 400)
            if not role_assignable(conn, role_key, actor_role):
                raise next_api_error(f"Role is not assignable in the current RBAC mode: {role_key}", 400)

        if str(actor.get("id")) == str(user_id) and not {"owner", "admin"}.intersection(role_keys):
            raise next_api_error("You cannot remove your own admin access", 400)
        if "owner" in role_keys and actor_role != "owner":
            raise next_api_error("Only owners can assign the owner role", 403)
        if target_role == "owner" and actor_role != "owner":
            raise next_api_error("Only owners can modify owner accounts", 403)
        if target_role == "owner" and "owner" not in role_keys and active_owner_count(conn) <= 1:
            raise next_api_error("The last active owner cannot be demoted", 400)

        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM user_roles WHERE user_id=%s AND scope_type='global' AND scope_id=''",
                    (user_id,),
                )
            for role_key in role_keys:
                assign_role(conn, user_id, role_key)
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET updated_at=now() WHERE id=%s", (user_id,))
        return user_admin_row(conn, user_id)

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
            "rbac_mode": rbac_mode(conn),
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

    @route("/.well-known/apple-app-site-association", methods=["GET"])
    def apple_app_site_association():
        payload = {"webcredentials": {"apps": _ios_webcredential_app_ids()}}
        result = make_response(json.dumps(payload))
        result.headers["Content-Type"] = "application/json"
        return result

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
                    "rbac_mode": rbac_mode(conn),
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
                    role_key = "owner" if not has_users or not has_credentials else default_registration_role(conn)
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
                audit_event(
                    conn,
                    event_type="auth.passkey_registered",
                    category="security",
                    actor={
                        "id": caller.get("id") if caller else user_id,
                        "username": caller.get("username") if caller else username,
                        "role": primary_role(conn, caller["id"]) if caller else primary_role(conn, user_id),
                    },
                    target_type="user",
                    target_id=user_id,
                    summary=f"Registered passkey for {username}",
                    metadata={
                        "username": username,
                        "createdUser": not bool(existing_user),
                        "credentialName": credential_name,
                        "inviteUsed": bool(invite_code),
                    },
                )

        token = _create_token(str(user_id), username)
        return cookie_response({"status": "ok", "token": token, "username": username}, token)

    @route("/api/next/auth/mobile/start", "/api/auth/mobile/start", methods=["GET"])
    def mobile_auth_start():
        params = validate_mobile_flow_start_params()
        with connect() as conn:
            if not table_exists(conn, "mobile_auth_flows"):
                raise next_api_error("Mobile auth tables are not available", 503)
            with conn.transaction():
                mobile_cleanup(conn)
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO mobile_auth_flows (
                            callback_scheme,
                            state,
                            code_challenge,
                            code_challenge_method,
                            expires_at
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            params["callback_scheme"],
                            params["state"],
                            params["code_challenge"],
                            params["code_challenge_method"],
                            _utcnow() + timedelta(seconds=MOBILE_AUTH_FLOW_TTL_SECONDS),
                        ),
                    )
                    flow_id = cur.fetchone()["id"]
                    flow_id_str = str(flow_id)
                audit_event(
                    conn,
                    event_type="auth.mobile_flow_started",
                    category="security",
                    summary="Started mobile passkey login flow",
                    metadata={
                        "mobileFlowId": flow_id_str,
                        "callbackScheme": params["callback_scheme"],
                        "codeChallengeMethod": params["code_challenge_method"],
                    },
                )
        target = f"/?{urlencode({'mobile_flow': flow_id_str})}"
        return redirect(target, code=302)

    @route(
        "/api/next/auth/passkeys/login/options",
        "/api/next/auth/passkey/login/options",
        "/api/next/auth/login/options",
        "/api/auth/login/options",
        methods=["POST"],
    )
    def login_options():
        body = request.get_json(silent=True) or {}
        client_kind = str(body.get("client_kind") or body.get("clientKind") or "").strip().lower()
        username_raw = body.get("username")
        username = str(username_raw or "").strip()
        if username:
            try:
                username = _normalize_username(username)
            except ValueError as exc:
                raise next_api_error(str(exc), 400) from exc
        with connect() as conn:
            if not table_exists(conn, "passkey_credentials"):
                raise next_api_error("Auth tables are not available", 503)
            with conn.cursor() as cur:
                if username:
                    cur.execute(
                        """
                        SELECT c.id
                        FROM passkey_credentials c
                        JOIN users u ON u.id = c.user_id
                        WHERE u.username=%s
                        ORDER BY c.created_at
                        """,
                        (username,),
                    )
                    credentials = cur.fetchall()
                elif client_kind == "ios":
                    credentials = []
                else:
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
        return response(
            {
                "status": "ok",
                "options": options,
                "publicKey": options,
                "public_key": {
                    "challenge": options["challenge"],
                    "rp_id": options["rpId"],
                    "allow_credentials": [
                        {"id": item["id"], "type": item["type"]}
                        for item in options["allowCredentials"]
                    ],
                },
            }
        )

    @route(
        "/api/next/auth/passkeys/login/verify",
        "/api/next/auth/passkey/login/verify",
        "/api/next/auth/login/verify",
        "/api/auth/login/verify",
        methods=["POST"],
    )
    def login_verify():
        body = request.get_json(silent=True) or {}
        credential = body.get("credential") or {}
        client_kind = str(body.get("client_kind") or body.get("clientKind") or "").strip().lower()
        mobile_flow_raw = body.get("mobile_flow") or body.get("mobileFlow")
        mobile_flow = _parse_uuid(mobile_flow_raw)
        if mobile_flow_raw and not mobile_flow:
            raise next_api_error("mobile_flow is invalid", 400)
        credential_id = str(credential.get("id") or credential.get("rawId") or "")
        if not credential_id:
            raise next_api_error("Credential id is required", 400)

        with connect() as conn:
            challenge = pop_challenge(conn, "login")
            if not challenge:
                raise next_api_error("No pending challenge", 400)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.*, u.username, u.display_name, u.status AS user_status
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
                audit_event(
                    conn,
                    event_type="auth.login",
                    category="security",
                    actor={
                        "id": stored["user_id"],
                        "username": stored["username"],
                        "role": primary_role(conn, stored["user_id"]),
                    },
                    target_type="user",
                    target_id=stored["user_id"],
                    summary=f"{stored['username']} logged in with a passkey",
                    metadata={"credentialId": credential_id, "mobileFlow": bool(mobile_flow)},
                )
                if mobile_flow:
                    mobile_code = create_mobile_auth_code(
                        conn,
                        mobile_flow_id=mobile_flow,
                        user_id=stored["user_id"],
                    )
                    audit_event(
                        conn,
                        event_type="auth.mobile_code_issued",
                        category="security",
                        actor={
                            "id": stored["user_id"],
                            "username": stored["username"],
                            "role": primary_role(conn, stored["user_id"]),
                        },
                        target_type="user",
                        target_id=stored["user_id"],
                        summary=f"Issued mobile one-time code for {stored['username']}",
                        metadata={"mobileFlowId": str(mobile_flow)},
                    )
                    return response(
                        {
                            "status": "ok",
                            "callback_url": mobile_code["callbackUrl"],
                            "callbackUrl": mobile_code["callbackUrl"],
                            "state": mobile_code.get("state"),
                        }
                    )
                if client_kind == "ios":
                    payload = native_login_response(
                        conn,
                        user_id=stored["user_id"],
                        username=stored["username"],
                        display_name=stored.get("display_name"),
                    )
                    audit_event(
                        conn,
                        event_type="auth.native_token_issued",
                        category="security",
                        actor={
                            "id": stored["user_id"],
                            "username": stored["username"],
                            "role": primary_role(conn, stored["user_id"]),
                        },
                        target_type="api_access_token",
                        target_id=payload["api_token"]["id"],
                        summary=f"Issued native iOS token for {stored['username']}",
                        metadata={
                            "clientKind": "ios",
                            "apiTokenId": payload["api_token"]["id"],
                            "permissionKeys": payload["api_token"]["permission_keys"],
                            "effectivePermissionKeys": payload["effectivePermissionKeys"],
                        },
                    )
                    return response(payload)

        token = _create_token(str(stored["user_id"]), stored["username"])
        return cookie_response({"status": "ok", "token": token, "username": stored["username"]}, token)

    @route("/api/next/auth/mobile/exchange", "/api/auth/mobile/exchange", methods=["POST"])
    def mobile_auth_exchange():
        body = request.get_json(silent=True) or {}
        code = str(body.get("code") or "").strip()
        code_verifier = str(body.get("code_verifier") or body.get("codeVerifier") or "").strip()
        if not code:
            raise next_api_error("code is required", 400)
        if not code_verifier:
            raise next_api_error("code_verifier is required", 400)
        try:
            expected_challenge = next_pkce_s256_challenge(code_verifier)
        except UnicodeEncodeError as exc:
            raise next_api_error("code_verifier is not valid ASCII", 400) from exc

        with connect() as conn:
            if not table_exists(conn, "mobile_auth_codes"):
                raise next_api_error("Mobile auth tables are not available", 503)
            with conn.transaction():
                mobile_cleanup(conn)
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT mac.*, u.username, u.status AS user_status
                        FROM mobile_auth_codes mac
                        JOIN users u ON u.id = mac.user_id
                        WHERE mac.code_hash=%s
                        FOR UPDATE
                        """,
                        (next_mobile_auth_code_hash(code),),
                    )
                    row = cur.fetchone()
                    if not row or row.get("used_at") is not None or row["expires_at"] <= _utcnow():
                        raise next_api_error("Mobile auth code is expired or already used", 400)
                    if row["user_status"] != "active":
                        raise next_api_error("User is disabled", 403)
                    if not secrets.compare_digest(str(row["code_challenge"]), expected_challenge):
                        raise next_api_error("PKCE verification failed", 400)
                    cur.execute("UPDATE mobile_auth_codes SET used_at=now() WHERE id=%s", (row["id"],))
                    token_payload = issue_mobile_api_token(conn, user_id=row["user_id"], username=row["username"])
                    role = primary_role(conn, row["user_id"])
                    effective_permission_keys = sorted(user_permissions(conn, row["user_id"]))
                audit_event(
                    conn,
                    event_type="auth.mobile_token_exchanged",
                    category="security",
                    actor={
                        "id": row["user_id"],
                        "username": row["username"],
                        "role": role,
                    },
                    target_type="api_access_token",
                    target_id=token_payload["tokenRow"]["id"],
                    summary=f"Exchanged mobile auth code for {row['username']}",
                    metadata={
                        "mobileFlowId": str(row["mobile_flow_id"]),
                        "apiTokenId": str(token_payload["tokenRow"]["id"]),
                        "permissionKeys": token_payload["permissionKeys"],
                        "tokenPermissionKeys": token_payload["permissionKeys"],
                        "effectivePermissionKeys": effective_permission_keys,
                        "scopes": token_payload["scopes"],
                    },
                )
        return response(
            {
                "status": "ok",
                "token": token_payload["token"],
                "username": row["username"],
                "role": role,
                "tokenPermissionKeys": token_payload["permissionKeys"],
                "effectivePermissionKeys": effective_permission_keys,
                "apiToken": {
                    "id": str(token_payload["tokenRow"]["id"]),
                    "name": token_payload["tokenRow"]["name"],
                    "scopes": token_payload["tokenRow"]["scopes"] or [],
                    "permissionKeys": token_payload["tokenRow"]["permission_keys"] or [],
                    "createdAt": token_payload["tokenRow"]["created_at"].isoformat()
                    if token_payload["tokenRow"].get("created_at")
                    else None,
                    "expiresAt": token_payload["tokenRow"]["expires_at"].isoformat()
                    if token_payload["tokenRow"].get("expires_at")
                    else None,
                    "revokedAt": None,
                },
            }
        )

    @route("/api/next/auth/recovery", "/api/auth/recovery", methods=["POST"])
    def recovery_login():
        body = request.get_json(silent=True) or {}
        try:
            username = _normalize_username(body.get("username") or "")
        except ValueError as exc:
            raise next_api_error(str(exc), 400) from exc
        recovery_code = next_normalize_recovery_code(body.get("recovery_code") or body.get("recoveryCode"))
        if not username or not recovery_code:
            raise next_api_error("Username and recovery code are required", 400)
        code_hash = next_recovery_code_hash(recovery_code)

        with connect() as conn:
            if not table_exists(conn, "users") or not table_exists(conn, "recovery_codes"):
                raise next_api_error("Recovery is not available yet", 503)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT rc.id AS recovery_code_id, u.id AS user_id, u.username, u.status AS user_status
                        FROM recovery_codes rc
                        JOIN users u ON u.id = rc.user_id
                        WHERE u.username=%s
                          AND rc.code_hash=%s
                          AND rc.used_at IS NULL
                          AND (rc.expires_at IS NULL OR rc.expires_at > now())
                        """,
                        (username, code_hash),
                    )
                    row = cur.fetchone()
                    if not row:
                        raise next_api_error("Invalid username or recovery code", 401)
                    if row["user_status"] != "active":
                        raise next_api_error("User is disabled", 403)
                    cur.execute(
                        """
                        UPDATE recovery_codes
                        SET used_at=now()
                        WHERE id=%s
                        """,
                        (row["recovery_code_id"],),
                    )
                    cur.execute(
                        """
                        SELECT COUNT(*) AS count
                        FROM recovery_codes
                        WHERE user_id=%s
                          AND used_at IS NULL
                          AND (expires_at IS NULL OR expires_at > now())
                        """,
                        (row["user_id"],),
                    )
                    remaining = int(cur.fetchone()["count"])
                audit_event(
                    conn,
                    event_type="auth.recovery_login",
                    category="security",
                    actor={
                        "id": row["user_id"],
                        "username": row["username"],
                        "role": primary_role(conn, row["user_id"]),
                    },
                    target_type="user",
                    target_id=row["user_id"],
                    summary=f"{row['username']} logged in with a recovery code",
                    metadata={"remainingRecoveryCodes": remaining},
                )

        token = _create_token(str(row["user_id"]), row["username"])
        return cookie_response(
            {
                "status": "ok",
                "token": token,
                "username": row["username"],
                "recovery": True,
                "remainingRecoveryCodes": remaining,
            },
            token,
        )

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
                audit_event(
                    conn,
                    event_type="auth.owner_transferred",
                    category="security",
                    actor=owner,
                    target_type="user",
                    target_id=target_user_id,
                    summary=f"Ownership transferred to {target['username']}",
                    metadata={
                        "previousOwnerId": str(owner["id"]),
                        "newOwnerId": str(target_user_id),
                        "newOwnerUsername": target["username"],
                    },
                )
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

    @route("/api/next/auth/rbac", "/api/auth/rbac", methods=["GET"])
    def rbac_status():
        with connect() as conn:
            admin = require_admin(conn)
            mode = rbac_mode(conn)
            return response(
                {
                    "status": "ok",
                    "mode": mode,
                    "basicRoleKeys": list(RBAC_BASIC_ROLE_KEYS),
                    "advancedEnabled": feature_enabled(conn, "rbac.advanced_mode", True),
                    "customRolesEnabled": feature_enabled(conn, "rbac.custom_roles", True),
                    "canSwitchMode": admin.get("role") == "owner",
                    "permissions": permission_catalog(conn),
                    "roles": managed_roles(conn, include_all=True),
                    "assignableRoles": [
                        role for role in managed_roles(conn) if role["assignable"]
                    ],
                }
            )

    @route("/api/next/auth/rbac", "/api/auth/rbac", methods=["PATCH"])
    def update_rbac_status():
        body = request.get_json(silent=True) or {}
        mode = str(body.get("mode") or "").strip().lower()
        with connect() as conn:
            owner = require_owner(conn)
            if mode == "advanced" and not feature_enabled(conn, "rbac.advanced_mode", True):
                raise next_api_error("Advanced RBAC mode is not enabled by the current license", 403)
            with conn.transaction():
                set_rbac_mode(conn, mode)
                audit_event(
                    conn,
                    event_type="rbac.mode_changed",
                    category="security",
                    actor=owner,
                    target_type="rbac",
                    target_id="mode",
                    summary=f"RBAC mode changed to {mode}",
                    metadata={"mode": mode},
                )
            return response(
                {
                    "status": "ok",
                    "mode": rbac_mode(conn),
                    "permissions": permission_catalog(conn),
                    "roles": managed_roles(conn, include_all=True),
                    "assignableRoles": [
                        role for role in managed_roles(conn) if role["assignable"]
                    ],
                }
            )

    @route("/api/next/auth/roles", "/api/auth/roles", methods=["GET"])
    def roles():
        with connect() as conn:
            require_admin(conn)
            include_all = str(request.args.get("all") or "").strip().lower() in {"1", "true", "yes"}
            return response(
                {
                    "status": "ok",
                    "mode": rbac_mode(conn),
                    "roles": managed_roles(conn, include_all=include_all),
                    "permissions": permission_catalog(conn),
                }
            )

    @route("/api/next/auth/roles", "/api/auth/roles", methods=["POST"])
    def create_role():
        body = request.get_json(silent=True) or {}
        if not body.get("key"):
            raise next_api_error("key is required", 400)
        role_key = normalize_role_key(body.get("key"))
        name = str(body.get("name") or "").strip()
        description = str(body.get("description") or "").strip()
        if not name:
            raise next_api_error("name is required", 400)

        with connect() as conn:
            owner = require_owner(conn)
            custom_role_mutations_allowed(conn)
            if role_exists(conn, role_key):
                raise next_api_error("Role key already exists", 409)
            permissions = validate_permission_selection(conn, body.get("permissions") or [])
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO roles (key, name, description, system, created_by, created_at, updated_at)
                        VALUES (%s, %s, %s, false, %s, now(), now())
                        RETURNING id
                        """,
                        (role_key, name, description or None, owner.get("id")),
                    )
                    role_id = cur.fetchone()["id"]
                    for permission_key in permissions:
                        cur.execute(
                            """
                            INSERT INTO role_permissions (role_id, permission_key)
                            VALUES (%s, %s)
                            ON CONFLICT DO NOTHING
                            """,
                            (role_id, permission_key),
                        )
                audit_event(
                    conn,
                    event_type="rbac.role_created",
                    category="security",
                    actor=owner,
                    target_type="role",
                    target_id=role_id,
                    summary=f"Created role {role_key}",
                    metadata={"key": role_key, "name": name, "permissions": permissions},
                )
            return response({"status": "ok", "role": role_by_identifier(conn, role_key)}, 201)

    @route("/api/next/auth/roles/<role_id>", "/api/auth/roles/<role_id>", methods=["PATCH"])
    def update_role(role_id: str):
        body = request.get_json(silent=True) or {}
        with connect() as conn:
            owner = require_owner(conn)
            custom_role_mutations_allowed(conn)
            role = role_by_identifier(conn, role_id)
            if not role:
                raise next_api_error("Role not found", 404)
            if role["system"]:
                raise next_api_error("System roles cannot be edited through the custom role API", 400)
            assignments: list[str] = []
            params: list[Any] = []
            if "name" in body:
                name = str(body.get("name") or "").strip()
                if not name:
                    raise next_api_error("name cannot be empty", 400)
                assignments.append("name=%s")
                params.append(name)
            if "description" in body:
                assignments.append("description=%s")
                params.append(str(body.get("description") or "").strip() or None)
            permissions = None
            if "permissions" in body:
                permissions = validate_permission_selection(conn, body.get("permissions"))
            if not assignments and permissions is None:
                raise next_api_error("Supply name, description and/or permissions", 400)

            with conn.transaction():
                with conn.cursor() as cur:
                    if assignments:
                        cur.execute(
                            f"UPDATE roles SET {', '.join(assignments)}, updated_at=now() WHERE id=%s",
                            (*params, role["id"]),
                        )
                    if permissions is not None:
                        cur.execute("DELETE FROM role_permissions WHERE role_id=%s", (role["id"],))
                        for permission_key in permissions:
                            cur.execute(
                                """
                                INSERT INTO role_permissions (role_id, permission_key)
                                VALUES (%s, %s)
                                ON CONFLICT DO NOTHING
                                """,
                                (role["id"], permission_key),
                            )
                audit_event(
                    conn,
                    event_type="rbac.role_updated",
                    category="security",
                    actor=owner,
                    target_type="role",
                    target_id=role["id"],
                    summary=f"Updated role {role['key']}",
                    metadata={
                        "key": role["key"],
                        "fields": sorted([item.split("=")[0] for item in assignments]),
                        "permissionsChanged": permissions is not None,
                        "permissions": permissions,
                    },
                )
            return response({"status": "ok", "role": role_by_identifier(conn, str(role["id"]))})

    @route("/api/next/auth/roles/<role_id>", "/api/auth/roles/<role_id>", methods=["DELETE"])
    def delete_role(role_id: str):
        with connect() as conn:
            owner = require_owner(conn)
            custom_role_mutations_allowed(conn)
            role = role_by_identifier(conn, role_id)
            if not role:
                raise next_api_error("Role not found", 404)
            if role["system"]:
                raise next_api_error("System roles cannot be deleted", 400)
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS count FROM user_roles WHERE role_id=%s", (role["id"],))
                assigned_count = int(cur.fetchone()["count"])
            if assigned_count:
                raise next_api_error("Role is assigned to users and cannot be deleted", 409)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM roles WHERE id=%s", (role["id"],))
                audit_event(
                    conn,
                    event_type="rbac.role_deleted",
                    category="security",
                    actor=owner,
                    target_type="role",
                    target_id=role["id"],
                    summary=f"Deleted role {role['key']}",
                    metadata={"key": role["key"], "name": role["name"]},
                )
            return response({"status": "deleted"})

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
                audit_event(
                    conn,
                    event_type="user.updated",
                    category="security",
                    actor=admin,
                    target_type="user",
                    target_id=user_uuid,
                    summary=f"Updated user {target['username']}",
                    metadata={"fields": sorted([item.split("=")[0] for item in assignments])},
                )
            updated = user_admin_row(conn, user_uuid)
        return response({"status": "ok", "user": updated})

    @route("/api/next/auth/users/<user_id>/role", "/api/auth/users/<user_id>/role", methods=["PUT", "PATCH"])
    def update_user_role(user_id: str):
        user_uuid = _parse_uuid(user_id)
        if not user_uuid:
            raise next_api_error("Invalid user id", 400)
        body = request.get_json(silent=True) or {}
        raw_role_key = body.get("role") or body.get("role_key")
        if not raw_role_key:
            raise next_api_error("role is required", 400)
        role_key = normalize_role_key(raw_role_key)

        with connect() as conn:
            admin = require_admin(conn)
            updated = set_user_global_roles(conn, actor=admin, user_id=user_uuid, role_keys=[role_key])
            audit_event(
                conn,
                event_type="user.roles_updated",
                category="security",
                actor=admin,
                target_type="user",
                target_id=user_uuid,
                summary=f"Updated roles for {updated['username']}",
                metadata={"roles": [role_key]},
            )
        return response({"status": "ok", "user": updated})

    @route("/api/next/auth/users/<user_id>/roles", "/api/auth/users/<user_id>/roles", methods=["PUT", "PATCH"])
    def update_user_roles(user_id: str):
        user_uuid = _parse_uuid(user_id)
        if not user_uuid:
            raise next_api_error("Invalid user id", 400)
        body = request.get_json(silent=True) or {}
        role_keys = normalize_role_key_list(body.get("roles"))

        with connect() as conn:
            admin = require_admin(conn)
            updated = set_user_global_roles(conn, actor=admin, user_id=user_uuid, role_keys=role_keys)
            audit_event(
                conn,
                event_type="user.roles_updated",
                category="security",
                actor=admin,
                target_type="user",
                target_id=user_uuid,
                summary=f"Updated roles for {updated['username']}",
                metadata={"roles": role_keys},
            )
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
                audit_event(
                    conn,
                    event_type="user.deleted",
                    category="security",
                    actor=admin,
                    target_type="user",
                    target_id=user_uuid,
                    summary=f"Deleted user {target['username']}",
                    metadata={"username": target["username"], "role": target_role},
                )
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
                    cur.execute(
                        "SELECT COUNT(*) AS count FROM passkey_credentials WHERE user_id=%s",
                        (credential_row["user_id"],),
                    )
                    target_remaining = int(cur.fetchone()["count"])
                    if target_remaining <= 1:
                        raise next_api_error("You cannot delete the last passkey for a user", 400)
                    cur.execute("DELETE FROM passkey_credentials WHERE id=%s", (credential_id,))
                    cur.execute("SELECT COUNT(*) AS count FROM passkey_credentials")
                    remaining = int(cur.fetchone()["count"])
                audit_event(
                    conn,
                    event_type="auth.passkey_deleted",
                    category="security",
                    actor={"id": user.get("id") if user else None, "username": user.get("username") if user else "system", "role": role},
                    target_type="passkey",
                    target_id=credential_id,
                    summary="Deleted a passkey",
                    metadata={"credentialUserId": str(credential_row["user_id"]), "remainingCredentials": remaining},
                )
                if remaining == 0:
                    set_setting(conn, "auth_enabled", False)
        return response({"status": "deleted", "remaining": remaining})

    @route("/api/next/auth/toggle", "/api/auth/toggle", methods=["POST"])
    def toggle_auth():
        body = request.get_json(silent=True) or {}
        enabled = bool(body.get("enabled", False))
        with connect() as conn:
            admin = require_admin(conn)
            if enabled and count_table(conn, "passkey_credentials") == 0:
                raise next_api_error("Register a passkey before enabling authentication", 400)
            with conn.transaction():
                set_setting(conn, "auth_enabled", enabled)
                audit_event(
                    conn,
                    event_type="auth.toggled",
                    category="security",
                    actor=admin,
                    target_type="auth",
                    target_id="auth_enabled",
                    summary=f"Authentication {'enabled' if enabled else 'disabled'}",
                    metadata={"enabled": enabled},
                )
        return response({"status": "ok", "auth_enabled": enabled})

    @route("/api/next/auth/registration", "/api/auth/registration", methods=["POST"])
    def toggle_registration():
        body = request.get_json(silent=True) or {}
        enabled = bool(body.get("enabled", False))
        with connect() as conn:
            admin = require_admin(conn)
            with conn.transaction():
                set_setting(conn, "registration_enabled", enabled)
                audit_event(
                    conn,
                    event_type="auth.registration_mode_changed",
                    category="security",
                    actor=admin,
                    target_type="auth",
                    target_id="registration_enabled",
                    summary="Registration mode changed",
                    metadata={"publicRegistration": enabled},
                )
            payload = auth_status_payload(conn)
        return response({"status": "ok", **payload})

    @route("/api/next/auth/owner/settings", "/api/auth/owner/settings", methods=["GET", "POST"])
    def owner_settings():
        with connect() as conn:
            owner = require_owner(conn)
            if request.method == "POST":
                body = request.get_json(silent=True) or {}
                if "movievault_contribution_enabled" in body:
                    enabled = bool(body.get("movievault_contribution_enabled"))
                    with conn.transaction():
                        set_setting(conn, "movievault_contribution_enabled", enabled)
                        audit_event(
                            conn,
                            event_type="metadata.receiver_setting_changed",
                            category="plugins",
                            actor=owner,
                            target_type="setting",
                            target_id="movievault_contribution_enabled",
                            summary="MovieVault receiver setting changed",
                            metadata={"enabled": enabled},
                        )
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
                audit_event(
                    conn,
                    event_type="invite.created",
                    category="security",
                    actor=admin,
                    target_type="invite",
                    target_id=invite_id,
                    summary=f"Created invite for {username}",
                    metadata={"username": username, "expiresAt": expires_at.isoformat()},
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
            admin = require_admin(conn)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM invite_codes WHERE id=%s AND used_at IS NULL",
                        (invite_uuid,),
                    )
                audit_event(
                    conn,
                    event_type="invite.deleted",
                    category="security",
                    actor=admin,
                    target_type="invite",
                    target_id=invite_uuid,
                    summary="Deleted invite",
                    metadata={},
                )
        return response({"status": "deleted"})
