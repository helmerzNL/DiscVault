"""Passkey authentication endpoints for the PostgreSQL-backed Next runtime."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import struct
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit
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
from flask import Flask, g as flask_g, jsonify, make_response, redirect, request
from psycopg import Error as PsycopgError
from psycopg.types.json import Jsonb


SHIPPED_RP_IDS = frozenset({"localhost", "discvault.example.com"})
SHIPPED_RP_ORIGINS = frozenset(
    {"http://localhost:6080", "https://discvault.example.com"}
)
LOCAL_IPV4_NETWORKS = tuple(
    ipaddress.ip_network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
LEGACY_BOOTSTRAP_PENDING_STATUS = "pending_legacy_bootstrap"
ADMIN_DEDUP_EXECUTE_ENV = "DISCVAULT_ADMIN_DEDUP_EXECUTE_ENABLED"
ADMIN_DEDUP_EXECUTE_DISABLED_CODE = "admin_dedup_execute_disabled"

try:
    from .admin_dedup_reports import (
        CanonicalReportError,
        apply_winner_selections as _dedup_apply_winner_selections,
        collection_fingerprint as _dedup_collection_fingerprint,
        consume_report as _dedup_consume_report,
        create_report as _dedup_create_canonical_report,
        load_report as _dedup_load_canonical_report,
    )
    from .next_legacy_auth import (
        FLOW_TTL_SECONDS,
        LEGACY_AUTH_SETTING,
        PASSWORD_HASH_VERSION,
        RECOVERY_ACK_FLOW_TTL_SECONDS,
        PasswordPolicyError,
        attempt_is_throttled,
        constant_time_text_equal,
        decrypt_totp_secret,
        encrypt_totp_secret,
        flow_token_hash,
        failed_attempt_state,
        generate_flow_token,
        generate_totp_secret,
        hash_password,
        hash_recovery_code,
        identity_digest,
        ip_digest,
        ip_only_attempt_is_throttled,
        legacy_auth_env_enabled,
        safe_audit_metadata,
        secret_digest,
        totp_qr_data_uri,
        totp_uri,
        validate_password,
        verify_password,
        verify_recovery_code,
        verify_totp,
    )
    from .next_audit import request_ip_details, request_is_behind_trusted_proxy, trusted_client_ip
    from .next_common import table_exists as _shared_table_exists
    from .next_movievault_v2_contributions import (
        registration_state as movievault_v2_contribution_state,
        reset_registration as reset_movievault_v2_contribution_registration,
    )
    from .next_runtime_secrets import jwt_secret
    from .scripts.sync_dedup_merge import (
        build_report as _dedup_build_report,
        execute_merge as _dedup_execute_merge,
        lock_collection_snapshot as _dedup_lock_collection_snapshot,
    )
except ImportError:  # pragma: no cover - direct module execution compatibility
    from admin_dedup_reports import (
        CanonicalReportError,
        apply_winner_selections as _dedup_apply_winner_selections,
        collection_fingerprint as _dedup_collection_fingerprint,
        consume_report as _dedup_consume_report,
        create_report as _dedup_create_canonical_report,
        load_report as _dedup_load_canonical_report,
    )
    from next_legacy_auth import (
        FLOW_TTL_SECONDS,
        LEGACY_AUTH_SETTING,
        PASSWORD_HASH_VERSION,
        RECOVERY_ACK_FLOW_TTL_SECONDS,
        PasswordPolicyError,
        attempt_is_throttled,
        constant_time_text_equal,
        decrypt_totp_secret,
        encrypt_totp_secret,
        flow_token_hash,
        failed_attempt_state,
        generate_flow_token,
        generate_totp_secret,
        hash_password,
        hash_recovery_code,
        identity_digest,
        ip_digest,
        ip_only_attempt_is_throttled,
        legacy_auth_env_enabled,
        safe_audit_metadata,
        secret_digest,
        totp_qr_data_uri,
        totp_uri,
        validate_password,
        verify_password,
        verify_recovery_code,
        verify_totp,
    )
    from next_audit import request_ip_details, request_is_behind_trusted_proxy, trusted_client_ip
    from next_common import table_exists as _shared_table_exists
    from next_movievault_v2_contributions import (
        registration_state as movievault_v2_contribution_state,
        reset_registration as reset_movievault_v2_contribution_registration,
    )
    from next_runtime_secrets import jwt_secret
    from scripts.sync_dedup_merge import (
        build_report as _dedup_build_report,
        execute_merge as _dedup_execute_merge,
        lock_collection_snapshot as _dedup_lock_collection_snapshot,
    )


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
# What a review login's token may do.
#
# The review flow assigns exactly one role -- `media_viewer` (see review_login),
# whose grants are collection.view, containers.view, groups.view,
# watchlist.manage and lending.request. That role exists, in its own migration's
# words, so an account "can view group media and use personal watchlist or
# borrowing features"; the token was missing both halves of that second clause,
# so a reviewer could browse and nothing else. Neither key grants anything the
# assigned role does not already carry.
#
# The metadata.* keys below are not in `media_viewer`. They are left in place
# because removing them would narrow the token today, and this tuple only ever
# widens; once a token's keys must also be held by the role, they stop having
# any effect on their own.
REVIEW_LOGIN_TOKEN_PERMISSIONS = (
    "api.read",
    "collection.view",
    "containers.view",
    "groups.view",
    "lending.request",
    "metadata.search",
    "metadata.refresh_one",
    "metadata.refresh_bulk",
    "watchlist.manage",
)
# What a native client's token may do.
#
# The guiding rule is that this tuple must cover what the server itself tells
# its clients to call -- no more. `mobile_endpoint_contract_payload` is that
# instruction, and three of its groups had no key here at all: locations and
# their QR codes (containers.view), the two metadata-refresh routes and the job
# list (metadata.refresh_one), and every loan-request route (lending.request).
#
# collection.view is not in the advertised contract; it is here because its
# absence was incoherent. The tuple already grants collection.add and
# collection.edit_all, so it described a client permitted to change a movie but
# not to read one. That went unnoticed while a token's keys were merely added to
# the role's; it stops being harmless the moment they have to agree.
MOBILE_AUTH_TOKEN_PERMISSIONS = (
    "api.read",
    "metadata.search",
    "metadata.refresh_one",
    "collection.view",
    "collection.add",
    "collection.add_own",
    "collection.import",
    "collection.edit_all",
    "containers.view",
    "lending.request",
    "watchlist.manage",
)

# What a throttled request is told, per throttle scope.
#
# Each entry repeats verbatim what its endpoint already answers when the
# credential is simply wrong -- same message, same status. A distinct "you are
# rate limited" reply would confirm to an attacker that they found a real
# account and are being counted, and would tell a legitimate user nothing they
# can act on. The legacy password flow has answered this way since it was
# written; these follow it.
SCOPED_THROTTLE_RESPONSES: dict[str, tuple[str, int]] = {
    "recovery": ("Invalid username or recovery code", 401),
    "invite": ("Invalid or expired invite code", 403),
    "review": ("Invalid credentials", 401),
    "mobile-exchange": ("Mobile auth code is expired or already used", 400),
    "passkey-verify": ("Unknown credential", 400),
}

# Native clients that may mint their own API token by logging in. The mobile
# PKCE exchange and the deep-link scheme are shared between platforms, so the
# client has to say which one it is: without this the Android app's tokens end
# up under the iOS label, indistinguishable from real iOS ones.
NATIVE_CLIENT_KINDS = ("ios", "android")
NATIVE_CLIENT_TOKEN_NAMES = {
    "ios": "DiscVault iOS",
    "android": "DiscVault Android",
}
# The mobile PKCE exchange carries no client kind today, and the shipped apps
# predate the field. Treating an absent kind as iOS keeps their tokens named the
# way they already are instead of relabelling every existing connection.
NATIVE_CLIENT_DEFAULT_KIND = "ios"
# Matches the API-token name limit enforced in next_profile.create_next_profile_api_token.
NATIVE_DEVICE_FIELD_MAX_LENGTH = 120


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


def next_normalize_client_kind(value: Any) -> str:
    """Return a known native client kind, or ``""`` for anything else."""

    kind = str(value or "").strip().lower()
    return kind if kind in NATIVE_CLIENT_KINDS else ""


def next_native_client_kind_or_default(value: Any) -> str:
    return next_normalize_client_kind(value) or NATIVE_CLIENT_DEFAULT_KIND


def next_native_token_name(client_kind: str) -> str:
    return NATIVE_CLIENT_TOKEN_NAMES[next_native_client_kind_or_default(client_kind)]


def next_normalize_device_field(value: Any) -> str | None:
    """Trim a client-supplied device string, or ``None`` when it carries nothing."""

    text = str(value or "").strip()
    if not text:
        return None
    return text[:NATIVE_DEVICE_FIELD_MAX_LENGTH]


def next_native_device_context(body: Any, *, user_agent: Any = None) -> dict[str, Any]:
    """Read the device identity a native client sends when it pairs.

    ``deviceId`` is the stable per-install identifier that lets a re-login rotate
    the existing token instead of adding a row. ``deviceLabel`` is the name the
    user gave the device and ``deviceModel`` the hardware it runs on — both are
    display-only, and the app is responsible for turning a raw model identifier
    (``iPhone16,2``) into something readable, so a new device never needs a
    server release to show up properly.
    """

    values = body if isinstance(body, dict) else {}
    return {
        "clientKind": next_normalize_client_kind(
            values.get("client_kind") or values.get("clientKind")
        ),
        "deviceId": next_normalize_device_field(
            values.get("device_id") or values.get("deviceId")
        ),
        "deviceLabel": next_normalize_device_field(
            values.get("device_label") or values.get("deviceLabel")
        ),
        "deviceModel": next_normalize_device_field(
            values.get("device_model") or values.get("deviceModel")
        ),
        "userAgent": next_normalize_device_field(user_agent),
    }


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


def next_replace_recovery_codes(cur, user_id: UUID | str, codes: list[str]) -> None:
    cur.execute(
        """
        UPDATE recovery_codes
        SET used_at=COALESCE(used_at, now())
        WHERE user_id=%s
          AND used_at IS NULL
        """,
        (user_id,),
    )
    for index, code in enumerate(codes, start=1):
        cur.execute(
            """
            INSERT INTO recovery_codes (
                user_id, code_hash, legacy_code_hash, label, created_at
            )
            VALUES (%s, %s, %s, %s, now())
            """,
            (
                user_id,
                next_recovery_code_hash(code),
                hash_recovery_code(code),
                f"Recovery code {index}",
            ),
        )


def next_consume_recovery_code(cur, user_id: UUID | str, code: Any) -> bool:
    normalized = next_normalize_recovery_code(code)
    if not normalized:
        return False
    deterministic_hash = next_recovery_code_hash(normalized)
    cur.execute(
        """
        SELECT id, code_hash, legacy_code_hash
        FROM recovery_codes
        WHERE user_id=%s
          AND used_at IS NULL
          AND (expires_at IS NULL OR expires_at > now())
        FOR UPDATE
        """,
        (user_id,),
    )
    matched_id = None
    for row in cur.fetchall():
        deterministic_match = secrets.compare_digest(
            str(row.get("code_hash") or ""),
            deterministic_hash,
        )
        legacy_hash = row.get("legacy_code_hash")
        if deterministic_match or (
            legacy_hash and verify_recovery_code(legacy_hash, normalized)
        ):
            matched_id = row["id"]
            break
    if not matched_id:
        return False
    cur.execute(
        """
        UPDATE recovery_codes
        SET used_at=now()
        WHERE id=%s AND used_at IS NULL
        """,
        (matched_id,),
    )
    return cur.rowcount == 1


def _jwt_secret() -> str:
    return jwt_secret()


def _rp_id() -> str:
    configured = os.environ.get("RP_ID", "").strip()
    if configured:
        return configured
    return request.host.split(":", 1)[0] if request else "localhost"


def _configured_rp_origins() -> list[str]:
    configured = os.environ.get("RP_ORIGINS") or os.environ.get("RP_ORIGIN") or ""
    return [item.strip().rstrip("/") for item in configured.split(",") if item.strip()]


def _normalized_origin(value: Any) -> str | None:
    text = str(value or "").strip().rstrip("/")
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        return None
    hostname = str(parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        return None
    if parsed.scheme.lower() != "https" and hostname != "localhost":
        return None
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    port_suffix = f":{port}" if port and not default_port else ""
    return f"{parsed.scheme.lower()}://{hostname}{port_suffix}"


def _passkey_configuration_valid() -> bool:
    rp_id = os.environ.get("RP_ID", "").strip().lower()
    if (
        not rp_id
        or rp_id in SHIPPED_RP_IDS
        or len(rp_id) > 253
        or "." not in rp_id
    ):
        return False
    labels = rp_id.split(".")
    valid_rp_id = all(
        0 < len(label) <= 63
        and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
        for label in labels
    ) and not all(label.isdigit() for label in labels)
    if not valid_rp_id:
        return False
    origins = _configured_rp_origins()
    if not origins:
        return False
    for origin in origins:
        normalized = _normalized_origin(origin)
        if not normalized or normalized in SHIPPED_RP_ORIGINS:
            return False
        origin_host = str(urlsplit(normalized).hostname or "").lower()
        if origin_host != rp_id and not origin_host.endswith(f".{rp_id}"):
            return False
    return True


def _request_hostname() -> str:
    if not request:
        return ""
    host = str(request.host or "").strip()
    try:
        return str(urlsplit(f"//{host}").hostname or "").lower()
    except ValueError:
        return ""


def _local_ip_address(value: Any) -> bool:
    try:
        address = ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return False
    if address.is_loopback:
        return True
    return isinstance(address, ipaddress.IPv4Address) and any(
        address in network for network in LOCAL_IPV4_NETWORKS
    )


def _request_host_is_local_ip() -> bool:
    if not request or not _local_ip_address(_request_hostname()):
        return False
    if not _local_ip_address(request.remote_addr):
        return False
    forwarded_for = str(request.headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
    return not forwarded_for or _local_ip_address(forwarded_for)


def _passkey_access_valid() -> bool:
    if not _passkey_configuration_valid():
        return False
    current_origin = _normalized_origin(_request_origin())
    configured_origins = {
        normalized
        for origin in _configured_rp_origins()
        if (normalized := _normalized_origin(origin))
    }
    return bool(current_origin and current_origin in configured_origins)


def _legacy_bootstrap_mfa_optional() -> bool:
    return (
        legacy_auth_env_enabled()
        and not _passkey_configuration_valid()
        and _request_host_is_local_ip()
    )


def _rp_name() -> str:
    return os.environ.get("RP_NAME", "DiscVault").strip() or "DiscVault"


def _request_origin() -> str:
    origin = (request.headers.get("Origin") or "").strip().rstrip("/")
    if origin:
        return origin
    scheme = request.headers.get("X-Forwarded-Proto") or request.scheme or "http"
    host = request.headers.get("X-Forwarded-Host") or request.host
    return f"{scheme}://{host}".rstrip("/")


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def admin_dedup_execute_enabled() -> bool:
    return _env_flag(ADMIN_DEDUP_EXECUTE_ENV, default=False)


def _register_admin_dedup_routes(
    *,
    route: Callable[..., Any],
    connect: ConnectFactory,
    response: ResponseFactory,
    next_api_error: type[Exception],
    require_admin: Callable[[Any], dict[str, Any]],
    require_authenticated_admin: Callable[[Any], dict[str, Any]],
    require_passkey_access: Callable[[], None],
    verify_step_up_assertion: Callable[..., dict[str, Any]],
    audit_event: Callable[..., None],
    make_challenge: Callable[[], bytes],
    store_challenge: Callable[[Any, str, bytes], None],
    b64url_encode: Callable[[bytes], str],
    rp_id: Callable[[], str],
) -> None:
    def canonical_error(error: CanonicalReportError):
        raise next_api_error(
            str(error),
            error.status_code,
            code=error.code,
        ) from error

    def request_body(*, allowed_keys: set[str]) -> dict[str, Any]:
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise next_api_error(
                "Admin dedup request body must be an object",
                400,
                code="admin_dedup_request_invalid",
            )
        if "report" in body:
            raise next_api_error(
                "Browser-supplied dedup reports are not accepted",
                400,
                code="admin_dedup_legacy_report_rejected",
            )
        if set(body) - allowed_keys:
            raise next_api_error(
                "Admin dedup request contains unsupported fields",
                400,
                code="admin_dedup_request_invalid",
            )
        return body

    @route("/api/next/admin/dedup/report", methods=["GET"])
    def admin_dedup_report():
        with connect() as conn:
            actor = require_admin(conn)
            with conn.transaction():
                report = _dedup_build_report(conn)
                canonical = _dedup_create_canonical_report(
                    conn,
                    report,
                    created_by=actor.get("id"),
                )
        return response(
            {
                "status": "ok",
                "reportId": canonical["id"],
                "reportHash": canonical["reportHash"],
                "expiresAt": canonical["expiresAt"],
                "report": canonical["report"],
                "executeEnabled": admin_dedup_execute_enabled(),
            }
        )

    @route("/api/next/admin/dedup/options", methods=["POST"])
    def admin_dedup_options():
        require_passkey_access()
        body = request_body(allowed_keys={"reportId", "winnerSelections"})
        report_id = body.get("reportId")
        if not report_id:
            raise next_api_error(
                "reportId is required",
                400,
                code="admin_dedup_report_id_malformed",
            )
        try:
            with connect() as conn:
                actor = require_authenticated_admin(conn)
                with conn.transaction():
                    source = _dedup_load_canonical_report(
                        conn,
                        report_id,
                        for_update=True,
                    )
                    selected_report = _dedup_apply_winner_selections(
                        source["report"],
                        body.get("winnerSelections"),
                    )
                    canonical = _dedup_create_canonical_report(
                        conn,
                        selected_report,
                        created_by=actor["id"],
                        source_report_id=source["id"],
                        collection_hash=source["collectionHash"],
                    )
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT id
                            FROM passkey_credentials
                            WHERE user_id=%s
                            ORDER BY created_at
                            """,
                            (actor["id"],),
                        )
                        credentials = cur.fetchall()
                    if not credentials:
                        raise next_api_error(
                            "No passkeys registered for your account",
                            400,
                        )
                    challenge = make_challenge()
                    challenge_key = f"admin_dedup:{actor['id']}:{canonical['id']}"
                    store_challenge(conn, challenge_key, challenge)
        except CanonicalReportError as error:
            canonical_error(error)
        options = {
            "challenge": b64url_encode(challenge),
            "timeout": 60000,
            "rpId": rp_id(),
            "allowCredentials": [
                {"type": "public-key", "id": row["id"]} for row in credentials
            ],
            "userVerification": "preferred",
        }
        return response(
            {
                "status": "ok",
                "reportId": canonical["id"],
                "reportHash": canonical["reportHash"],
                "expiresAt": canonical["expiresAt"],
                "options": options,
            }
        )

    @route("/api/next/admin/dedup/execute", methods=["POST"])
    def admin_dedup_execute():
        if not admin_dedup_execute_enabled():
            raise next_api_error(
                "Admin dedup execution is disabled",
                403,
                code=ADMIN_DEDUP_EXECUTE_DISABLED_CODE,
            )
        require_passkey_access()
        body = request_body(allowed_keys={"reportId", "credential"})
        report_id = body.get("reportId")
        if not report_id:
            raise next_api_error(
                "reportId is required",
                400,
                code="admin_dedup_report_id_malformed",
            )
        credential = body.get("credential") or {}
        if not credential:
            raise next_api_error("credential is required", 400)
        try:
            with connect() as conn:
                actor = require_authenticated_admin(conn)
                with conn.transaction():
                    _dedup_lock_collection_snapshot(conn)
                    canonical = _dedup_load_canonical_report(
                        conn,
                        report_id,
                        for_update=True,
                    )
                    current_report = _dedup_build_report(conn)
                    if (
                        _dedup_collection_fingerprint(current_report)
                        != canonical["collectionHash"]
                    ):
                        raise CanonicalReportError(
                            "The collection changed after this dedup report was generated",
                            code="admin_dedup_report_stale",
                            status_code=409,
                        )
                    challenge_key = (
                        f"admin_dedup:{actor['id']}:{canonical['id']}"
                    )
                    stored = verify_step_up_assertion(
                        conn,
                        challenge_key=challenge_key,
                        expected_user_id=actor["id"],
                        credential=credential,
                    )
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE passkey_credentials
                            SET sign_count=%s, last_used_at=now()
                            WHERE id=%s
                            """,
                            (stored["new_sign_count"], stored["id"]),
                        )
                    tombstoned = _dedup_execute_merge(
                        conn,
                        canonical["report"],
                        commit=False,
                    )
                    consumed_at = _dedup_consume_report(
                        conn,
                        canonical["id"],
                        consumed_by=actor["id"],
                    )
                    audit_event(
                        conn,
                        event_type="admin.dedup_executed",
                        category="operations",
                        actor=actor,
                        target_type="movies",
                        target_id=None,
                        summary=(
                            f"Dedup merge executed: {tombstoned} movie(s) "
                            "tombstoned"
                        ),
                        metadata={
                            "canonicalReportId": canonical["id"],
                            "canonicalReportHash": canonical["reportHash"],
                            "tombstoned": tombstoned,
                            "mergeGroupCount": len(
                                canonical["report"].get("groups", [])
                            ),
                        },
                    )
        except CanonicalReportError as error:
            canonical_error(error)
        return response(
            {
                "status": "ok",
                "reportId": canonical["id"],
                "reportHash": canonical["reportHash"],
                "consumedAt": consumed_at,
                "tombstoned": tombstoned,
            }
        )


def _parse_review_login_expires_at(raw: Any) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    normalized = value
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    expires_at = datetime.fromisoformat(normalized)
    if expires_at.tzinfo is None:
        return expires_at.replace(tzinfo=timezone.utc)
    return expires_at.astimezone(timezone.utc)


def _review_login_expires_at() -> datetime | None:
    return _parse_review_login_expires_at(os.environ.get("REVIEW_LOGIN_EXPIRES_AT"))


def _rp_origins() -> list[str]:
    origins = _configured_rp_origins()
    if origins:
        return [_normalized_origin(origin) or origin for origin in origins]
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
    if not table_exists(conn, "users"):
        return False
    credential_checks: list[str] = []
    if table_exists(conn, "passkey_credentials"):
        credential_checks.append(
            "EXISTS (SELECT 1 FROM passkey_credentials p WHERE p.user_id=u.id)"
        )
    if table_exists(conn, "legacy_password_credentials"):
        credential_checks.append(
            "EXISTS (SELECT 1 FROM legacy_password_credentials l WHERE l.user_id=u.id)"
        )
    if not credential_checks:
        return False
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT EXISTS (
                SELECT 1
                FROM users u
                WHERE u.status='active'
                  AND ({" OR ".join(credential_checks)})
            ) AS ready
            """
        )
        row = cur.fetchone()
    return bool(row and row["ready"])


_AUTH_ENABLED_ATTR = "_dv_auth_effective_enabled"


def forget_auth_enabled() -> None:
    """Drop the per-request answer to "is authentication on".

    Called wherever ``auth_enabled`` is written, so a request that switches
    authentication on or off does not go on reading its own stale answer.
    """

    try:
        if hasattr(flask_g, _AUTH_ENABLED_ATTR):
            delattr(flask_g, _AUTH_ENABLED_ATTR)
    except RuntimeError:  # pragma: no cover - no application context
        pass


def next_auth_effective_enabled(conn, table_exists: TableExists) -> bool:
    """Is authentication both switched on and usable? Answered once per request.

    Two queries -- the setting, then an EXISTS over ``users`` joined against the
    credential tables -- and a warm ``/api/next/app/snapshot`` ran both of them
    three times: once in the ``before_request`` gate and twice more inside the
    handler, on a second connection, for an answer that had not moved.

    Held in ``flask.g`` rather than on the connection because the point is to
    span the two connections a single request opens. Anything that writes the
    setting calls ``forget_auth_enabled``.
    """

    try:
        remembered = getattr(flask_g, _AUTH_ENABLED_ATTR, None)
    except RuntimeError:  # pragma: no cover - no application context (worker, tests)
        remembered = None
    if remembered is not None:
        return bool(remembered)

    enabled = next_auth_configured_enabled(conn, table_exists) and next_auth_ready(
        conn, table_exists
    )
    try:
        setattr(flask_g, _AUTH_ENABLED_ATTR, enabled)
    except RuntimeError:  # pragma: no cover - no application context
        pass
    return enabled


_REQUEST_ACTOR_ATTR = "_dv_request_actor"


def _remembered_request_actor() -> dict[str, Any] | None:
    try:
        return getattr(flask_g, _REQUEST_ACTOR_ATTR, None)
    except RuntimeError:  # pragma: no cover - no application context (worker, tests)
        return None


def _remember_request_actor(actor: dict[str, Any]) -> None:
    try:
        setattr(flask_g, _REQUEST_ACTOR_ATTR, actor)
    except RuntimeError:  # pragma: no cover - no application context (worker, tests)
        pass


def forget_request_actor() -> None:
    """Drop the memo so the next call resolves again.

    For a request that changes who the caller is midway -- signing in, signing
    out, revoking the very token it arrived on. Nothing else should need it.
    """

    try:
        if hasattr(flask_g, _REQUEST_ACTOR_ATTR):
            delattr(flask_g, _REQUEST_ACTOR_ATTR)
    except RuntimeError:  # pragma: no cover - no application context
        pass


def next_auth_current_user(conn) -> dict[str, Any] | None:
    """Who is making this request -- resolved once, then reused.

    Every protected request authenticates twice. ``require_next_auth`` runs as a
    ``before_request`` hook on its own connection and resolves the caller to
    decide whether to answer 401 at all; the handler then opens its own
    connection and resolves the same caller again from the same unchanged
    header. The credentials cannot differ between the two -- a request's headers
    and cookies are fixed for its lifetime -- so the second resolution can only
    ever reach the same answer.

    On a token it was not merely a repeated read: ``next_auth_current_api_token_user``
    stamps ``last_used_at`` and the observed address, so a plain GET wrote to
    ``api_access_tokens`` twice. It now writes once.

    Only a successful resolution is remembered. A failure costs one query at
    most, and caching "nobody" is the direction that could wrongly outlive a
    sign-in happening inside the same request.

    The memo is handed out as a copy because callers add to what they get --
    ``require_admin`` writes ``role``, the profile routes write ``permissions``
    -- and those are per-caller conclusions, not part of the identity.
    """

    remembered = _remembered_request_actor()
    if remembered is not None:
        return dict(remembered)

    payload = _current_user_payload()
    user_id = payload.get("sub") if payload else None
    if not user_id:
        api_token = _bearer_api_token()
        if api_token:
            actor = next_auth_current_api_token_user(conn, api_token)
            if actor is not None:
                _remember_request_actor(actor)
                return dict(actor)
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
        actor = cur.fetchone()
    if actor is None:
        return None
    _remember_request_actor(actor)
    return dict(actor)


def _auth_table_exists(conn, table_name: str) -> bool:
    """The same question as ``next_common.table_exists``, so ask it there.

    This module carried its own copy, which meant token authentication paid two
    ``to_regclass`` round trips that the rest of the request had already paid and
    memoised. Delegating shares the connection's memo instead of running beside
    it.
    """

    return _shared_table_exists(conn, table_name)


def _observed_request_ip() -> dict[str, str]:
    """The public address this request came from, as the server saw it.

    Returns empty strings outside a request context, and whenever every
    candidate is private — a LAN address says nothing useful about *where* a
    connection came from, and self-hosted instances are reached over one all
    the time.
    """

    try:
        details = request_ip_details()
    except RuntimeError:  # pragma: no cover - no request context (workers, tests)
        return {"ip": "", "source": ""}
    return {"ip": details.get("ip") or "", "source": details.get("source") or ""}


def _peer_ip_address() -> str:
    """The address a throttle may attribute this request to.

    A direct connection answers with the peer. Behind a proxy the operator has
    configured as trusted, it answers with the client address recovered from the
    forwarded chain -- so each real client gets its own bucket instead of the
    whole instance sharing the proxy's.

    What it never does is believe a forwarding header from an unvouched hop.
    Counting failures against a value the caller sets for itself would hand the
    caller a reset button. A private address is kept rather than discarded: on a
    self-hosted instance the LAN address is frequently the only one there is,
    and collapsing every LAN client into one empty bucket is worse than useless.
    """

    try:
        return trusted_client_ip()
    except RuntimeError:  # pragma: no cover - no request context (workers, tests)
        return ""


def _throttle_address_is_per_client() -> bool:
    """Whether the throttle address identifies one client rather than a crowd.

    True for a direct connection, and for a trusted-proxy topology where the
    real client address was recovered. False for the unconfigured proxy case,
    where every caller shares the proxy's address and a tight ceiling would lock
    everyone out at once.
    """

    try:
        if request_is_behind_trusted_proxy():
            return True
        peer = str(request.remote_addr or "").strip()
    except RuntimeError:  # pragma: no cover - no request context
        return False
    if not peer:
        return False
    # No trusted proxy is configured. The address is per-client only if nothing
    # is forwarding on the caller's behalf; a forwarding header arriving from an
    # unvouched hop is exactly the case we must not tighten for.
    return not any(
        request.headers.get(header)
        for header in ("X-Forwarded-For", "X-Original-Forwarded-For", "Forwarded")
    )


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
                t.client_kind AS api_token_client_kind,
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
        # Record where the token was used from, so the connections list can
        # answer "is this one mine?". Always the address the server observed —
        # a client-supplied one would be a claim rather than evidence.
        observed = _observed_request_ip()
        stored_keys = [str(item) for item in (row["api_token_permission_keys"] or [])]
        reconciled = _reconciled_native_token_permissions(
            row.pop("api_token_client_kind"), stored_keys
        )
        cur.execute(
            """
            UPDATE api_access_tokens
            SET last_used_at=now(),
                last_seen_ip=COALESCE(NULLIF(%s, ''), last_seen_ip),
                last_seen_ip_source=COALESCE(NULLIF(%s, ''), last_seen_ip_source),
                permission_keys=COALESCE(%s::jsonb, permission_keys)
            WHERE id=%s
            """,
            (
                observed.get("ip"),
                observed.get("source"),
                json.dumps(reconciled) if reconciled is not None else None,
                row["api_token_id"],
            ),
        )
        if reconciled is not None:
            row["api_token_permission_keys"] = reconciled
    row["apiToken"] = {
        "id": row.pop("api_token_id"),
        "name": row.pop("api_token_name"),
        "scopes": row.pop("api_token_scopes") or [],
        "permissionKeys": row.pop("api_token_permission_keys") or [],
    }
    return row


def _reconciled_native_token_permissions(
    client_kind: Any, stored_keys: list[str]
) -> list[str] | None:
    """Bring a native client's stored scope up to what login would issue today.

    A native client has no refresh endpoint (see issue_mobile_api_token): the
    tuple it was given is fixed until the user signs in again, and it only signs
    in again once its token stops working. So widening the tuple leaves every
    already-installed app on the old, narrower one -- and once a token's keys
    have to agree with the role, "narrower" means routes that worked yesterday
    answer 403 until someone re-authenticates. Reconciling on use removes that
    window without asking anyone to log in again.

    Only native rows are touched. A user-created token is a scope its owner
    chose, and rewriting that would be the opposite of taking it seriously; a
    native token is not a chosen scope but "this app, on this device", which the
    server already re-decides at every login. This applies the same decision
    without waiting for one.

    This cannot escalate: the role is required independently, so a key added
    here that the holder's role lacks stays inert. Returns None when there is
    nothing to change, which is the case from the second request onwards.
    """
    if str(client_kind or "") not in NATIVE_CLIENT_KINDS:
        return None
    if not stored_keys:
        # An empty list is grandfathered as unscoped rather than reconciled --
        # rewriting it would narrow a token that currently has full role
        # authority, which is a revocation, not a repair.
        return None
    missing = [key for key in MOBILE_AUTH_TOKEN_PERMISSIONS if key not in stored_keys]
    if not missing:
        return None
    return stored_keys + missing


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
        if key == "auth_enabled":
            # The one setting a request caches for itself. Invalidated here
            # rather than at each of the four call sites, so a new writer cannot
            # forget: enabling authentication during first-owner bootstrap,
            # disabling it when the last passkey is deleted, and the toggle
            # route all go through this function.
            forget_auth_enabled()

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

    def issue_mobile_api_token(
        conn,
        *,
        user_id: UUID | str,
        username: str,
        device: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not table_exists(conn, "api_access_tokens"):
            raise next_api_error("API token table is not available", 503)
        device = device or {}
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
        client_kind = next_native_client_kind_or_default(device.get("clientKind"))
        token_name = next_native_token_name(client_kind)
        device_id = device.get("deviceId")
        # A native client has no refresh endpoint, so it logs in again whenever
        # its token stops working. When the client identifies its install, the
        # secret is rotated on the row that device already owns instead of a new
        # row being added — that is what made the API & MCP page grow an entry
        # per login. Without a device id there is nothing to recognise the
        # client by, so the insert stands and the profile payload collapses the
        # rows for display: deduping on the name alone would treat an iPhone and
        # an iPad as one device and log one of them out.
        #
        # The upsert is one statement on purpose. Two logins racing on a device's
        # first token would both miss a separate SELECT and then collide on
        # idx_api_access_tokens_user_device_active.
        conflict_clause = (
            """
            ON CONFLICT (user_id, device_id) WHERE device_id IS NOT NULL AND revoked_at IS NULL
            DO UPDATE SET
                token_hash=EXCLUDED.token_hash,
                name=EXCLUDED.name,
                scopes=EXCLUDED.scopes,
                permission_keys=EXCLUDED.permission_keys,
                client_kind=EXCLUDED.client_kind,
                device_label=COALESCE(EXCLUDED.device_label, api_access_tokens.device_label),
                device_model=COALESCE(EXCLUDED.device_model, api_access_tokens.device_model),
                user_agent=COALESCE(EXCLUDED.user_agent, api_access_tokens.user_agent),
                expires_at=NULL,
                last_used_at=now()
            """
            if device_id
            else ""
        )
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO api_access_tokens (
                    user_id,
                    name,
                    token_hash,
                    scopes,
                    permission_keys,
                    created_by,
                    expires_at,
                    client_kind,
                    device_id,
                    device_label,
                    device_model,
                    user_agent
                )
                VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s)
                {conflict_clause}
                RETURNING id, name, scopes, permission_keys, created_at, last_used_at, expires_at, revoked_at
                """,
                (
                    user_id,
                    token_name,
                    next_api_token_hash(token_value),
                    Jsonb(scopes),
                    Jsonb(permission_keys),
                    user_id,
                    client_kind,
                    device_id,
                    device.get("deviceLabel"),
                    device.get("deviceModel"),
                    device.get("userAgent"),
                ),
            )
            token_row = cur.fetchone()
        return {"token": token_value, "tokenRow": token_row, "permissionKeys": permission_keys, "scopes": scopes}

    def issue_review_api_token(
        conn,
        *,
        user_id: UUID | str,
        username: str,
        expires_at: datetime | None,
        device: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not table_exists(conn, "api_access_tokens"):
            raise next_api_error("API token table is not available", 503)
        device = device or {}
        known_permissions = permission_keys_catalog(conn)
        permission_keys = [
            key for key in REVIEW_LOGIN_TOKEN_PERMISSIONS if key in known_permissions
        ]
        scopes = sorted({key.split(".", 1)[0] for key in permission_keys})
        token_value = next_create_api_token_value()
        token_name = "App Store Review"
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
                    expires_at,
                    client_kind,
                    device_id,
                    device_label,
                    device_model,
                    user_agent
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, name, scopes, permission_keys, created_at, last_used_at, expires_at, revoked_at
                """,
                (
                    user_id,
                    token_name,
                    next_api_token_hash(token_value),
                    Jsonb(scopes),
                    Jsonb(permission_keys),
                    user_id,
                    expires_at,
                    next_native_client_kind_or_default(device.get("clientKind")),
                    device.get("deviceId"),
                    device.get("deviceLabel"),
                    device.get("deviceModel"),
                    device.get("userAgent"),
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
        device: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token_payload = issue_mobile_api_token(
            conn, user_id=user_id, username=username, device=device
        )
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
                    # The address the server resolved, not the one the caller
                    # claimed. This writer kept its own inline copy of the old
                    # "first X-Forwarded-For hop" rule and so was missed when
                    # forwarded headers stopped being believed on their own --
                    # which left the auth events, the ones that most need a
                    # truthful address, choosable by the caller.
                    trusted_client_ip(),
                    request.headers.get("User-Agent"),
                ),
            )

    def configured_auth_enabled(conn) -> bool:
        return bool(setting_value(conn, "auth_enabled", False))

    def auth_ready(conn) -> bool:
        return next_auth_ready(conn, table_exists)

    def auth_enabled(conn) -> bool:
        """The same question the ``before_request`` gate already answered.

        Delegating means the handler reads the gate's answer instead of running
        both queries again on its own connection.
        """

        return next_auth_effective_enabled(conn, table_exists)

    def require_passkey_access() -> None:
        if not _passkey_access_valid():
            raise next_api_error(
                "Passkeys require a valid RP_ID and HTTPS RP_ORIGIN, accessed through the configured origin",
                403,
            )

    def legacy_tables_available(conn) -> bool:
        return all(
            table_exists(conn, table_name)
            for table_name in (
                "legacy_password_credentials",
                "legacy_totp_credentials",
                "legacy_mfa_recovery_codes",
                "legacy_auth_flows",
                "legacy_auth_attempts",
            )
        )

    def legacy_db_enabled(conn) -> bool:
        return bool(setting_value(conn, LEGACY_AUTH_SETTING, False))

    def legacy_effective_enabled(conn) -> bool:
        return legacy_auth_env_enabled() and legacy_tables_available(conn) and legacy_db_enabled(conn)

    def require_legacy_enabled(conn) -> None:
        if not legacy_auth_env_enabled():
            raise next_api_error("Password login is unavailable", 404)
        if not legacy_tables_available(conn):
            raise next_api_error("Legacy authentication tables are not available", 503)
        if not legacy_db_enabled(conn):
            raise next_api_error("Password login is unavailable", 404)

    def passkey_registration_allowed(conn, user_id: UUID | str) -> bool:
        if not table_exists(conn, "legacy_password_credentials"):
            return True
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT passkey_registration_allowed
                FROM legacy_password_credentials
                WHERE user_id=%s
                """,
                (user_id,),
            )
            row = cur.fetchone()
        return bool(row["passkey_registration_allowed"]) if row else True

    def request_ip_address() -> str:
        return (
            request.headers.get("X-Forwarded-For", request.remote_addr or "")
            .split(",", 1)[0]
            .strip()
        )

    def issue_legacy_flow(
        conn,
        flow_type: str,
        *,
        user_id: UUID | str | None = None,
        payload: dict[str, Any] | None = None,
        seconds: int = FLOW_TTL_SECONDS,
    ) -> str:
        token = generate_flow_token()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM legacy_auth_flows WHERE expires_at < now() OR used_at IS NOT NULL")
            cur.execute(
                """
                INSERT INTO legacy_auth_flows (token_hash, flow_type, user_id, payload, expires_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    flow_token_hash(token),
                    flow_type,
                    user_id,
                    Jsonb(payload or {}),
                    _utcnow() + timedelta(seconds=seconds),
                ),
            )
        return token

    def get_legacy_flow(
        conn,
        token: Any,
        *,
        flow_types: set[str],
        consume: bool = False,
    ) -> dict[str, Any]:
        token_value = str(token or "").strip()
        if not token_value:
            raise next_api_error("Authentication flow is expired or already used", 400)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, flow_type, user_id, payload, expires_at
                FROM legacy_auth_flows
                WHERE token_hash=%s AND used_at IS NULL AND expires_at > now()
                FOR UPDATE
                """,
                (flow_token_hash(token_value),),
            )
            flow = cur.fetchone()
            if not flow or flow["flow_type"] not in flow_types:
                raise next_api_error("Authentication flow is expired or already used", 400)
            if consume:
                cur.execute(
                    "UPDATE legacy_auth_flows SET used_at=now() WHERE id=%s AND used_at IS NULL",
                    (flow["id"],),
                )
                if cur.rowcount != 1:
                    raise next_api_error("Authentication flow is expired or already used", 400)
        flow["payload"] = flow.get("payload") or {}
        return flow

    def legacy_client_context(body: dict[str, Any]) -> dict[str, Any]:
        mobile_flow_raw = body.get("mobile_flow") or body.get("mobileFlow")
        mobile_flow = _parse_uuid(mobile_flow_raw)
        if mobile_flow_raw and not mobile_flow:
            raise next_api_error("mobile_flow is invalid", 400)
        device = next_native_device_context(body, user_agent=request.headers.get("User-Agent"))
        return {
            "mobileFlow": str(mobile_flow) if mobile_flow else None,
            "clientKind": device["clientKind"],
            "device": device,
        }

    def legacy_complete_login(
        conn,
        *,
        user: dict[str, Any],
        context: dict[str, Any],
        method: str,
    ):
        mobile_flow = _parse_uuid(context.get("mobileFlow"))
        actor = {
            "id": user["id"],
            "username": user["username"],
            "role": primary_role(conn, user["id"]),
        }
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE legacy_password_credentials
                SET last_login_at=now(), failed_attempt_count=0, first_failed_at=NULL,
                    locked_until=NULL, updated_at=now()
                WHERE user_id=%s
                """,
                (user["id"],),
            )
        audit_event(
            conn,
            event_type="auth.legacy_login",
            category="security",
            actor=actor,
            target_type="user",
            target_id=user["id"],
            summary=f"{user['username']} completed password login",
            metadata=safe_audit_metadata(
                {"method": method, "mobileFlow": bool(mobile_flow), "clientKind": context.get("clientKind")}
            ),
        )
        if mobile_flow:
            mobile_code = create_mobile_auth_code(
                conn,
                mobile_flow_id=mobile_flow,
                user_id=user["id"],
            )
            return response(
                {
                    "status": "ok",
                    "stage": "complete",
                    "callback_url": mobile_code["callbackUrl"],
                    "callbackUrl": mobile_code["callbackUrl"],
                    "state": mobile_code.get("state"),
                }
            )
        if context.get("clientKind") in NATIVE_CLIENT_KINDS:
            return response(
                native_login_response(
                    conn,
                    user_id=user["id"],
                    username=user["username"],
                    display_name=user.get("display_name"),
                    device=context.get("device"),
                )
            )
        token = _create_token(str(user["id"]), user["username"])
        return cookie_response(
            {
                "status": "ok",
                "stage": "complete",
                "token": token,
                "username": user["username"],
            },
            token,
        )

    def issue_mfa_enrollment(conn, user: dict[str, Any], context: dict[str, Any]):
        secret_value = generate_totp_secret()
        nonce, ciphertext = encrypt_totp_secret(secret_value)
        flow_token = issue_legacy_flow(
            conn,
            "mfa_enrollment",
            user_id=user["id"],
            payload={
                **context,
                "nonce": _b64url_encode(nonce),
                "encryptedSecret": _b64url_encode(ciphertext),
            },
        )
        return response(
            {
                "status": "ok",
                "stage": "mfa_enrollment",
                "flow_token": flow_token,
                "manual_key": secret_value,
                "otpauth_uri": totp_uri(secret_value, user["username"]),
                "qr_data_uri": totp_qr_data_uri(totp_uri(secret_value, user["username"])),
            }
        )

    def resume_legacy_bootstrap(
        conn,
        *,
        credential: dict[str, Any],
        context: dict[str, Any],
    ):
        """Hand an abandoned first-owner bootstrap back its recovery-code step.

        The account exists with a working password but never reached
        ``status='active'`` because the recovery codes were never acknowledged.
        Callers must have verified the password first, so resuming here reveals
        nothing that completing the login would not.

        Only the hashes of the original recovery codes are stored, so a fresh
        set is issued. That is also the correct outcome: codes the owner never
        acknowledged must not stay valid.
        """

        user_id = credential["id"]
        mfa_required = bool(credential.get("mfa_required"))
        recovery_codes = next_generate_recovery_codes()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE legacy_password_credentials
                SET failed_attempt_count=0, first_failed_at=NULL, locked_until=NULL,
                    updated_at=now()
                WHERE user_id=%s
                """,
                (user_id,),
            )
            next_replace_recovery_codes(cur, user_id, recovery_codes)
        ack_token = issue_legacy_flow(
            conn,
            "bootstrap_recovery_ack",
            user_id=user_id,
            payload={**context, "mfaRequired": mfa_required},
            seconds=RECOVERY_ACK_FLOW_TTL_SECONDS,
        )
        audit_event(
            conn,
            event_type="auth.legacy_bootstrap_resumed",
            category="security",
            actor={
                "id": user_id,
                "username": credential["username"],
                "role": primary_role(conn, user_id),
            },
            target_type="user",
            target_id=user_id,
            summary=f"Resumed password onboarding for {credential['username']}",
            metadata={"mfaRequired": mfa_required, "recoveryCodesReissued": True},
        )
        return response(
            {
                "status": "ok",
                "stage": "recovery_codes",
                "flow_token": ack_token,
                "recovery_codes": recovery_codes,
            }
        )

    def legacy_next_stage(
        conn,
        *,
        user: dict[str, Any],
        credential: dict[str, Any],
        context: dict[str, Any],
    ):
        if credential.get("must_change_password"):
            token = issue_legacy_flow(
                conn,
                "password_change",
                user_id=user["id"],
                payload=context,
            )
            return response({"status": "ok", "stage": "password_change", "flow_token": token})
        if credential.get("mfa_required"):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT confirmed_at
                    FROM legacy_totp_credentials
                    WHERE user_id=%s
                    """,
                    (user["id"],),
                )
                totp_row = cur.fetchone()
            if not totp_row or not totp_row.get("confirmed_at"):
                return issue_mfa_enrollment(conn, user, context)
            token = issue_legacy_flow(
                conn,
                "mfa_challenge",
                user_id=user["id"],
                payload=context,
            )
            return response({"status": "ok", "stage": "mfa_challenge", "flow_token": token})
        return legacy_complete_login(conn, user=user, context=context, method="password")

    def legacy_user_row(conn, user_id: UUID | str) -> dict[str, Any]:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, username, display_name, status
                FROM users
                WHERE id=%s
                """,
                (user_id,),
            )
            user = cur.fetchone()
        if not user or user.get("status") != "active":
            raise next_api_error("Invalid username or password", 401)
        return user

    def recent_legacy_attempts(conn, username: str) -> tuple[int, int, str, str]:
        identity_hash = identity_digest(username)
        ip_hash = ip_digest(request_ip_address())
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE identity_digest=%s AND NOT succeeded)::int AS identity_failures,
                    COUNT(*) FILTER (WHERE ip_digest=%s AND NOT succeeded)::int AS ip_failures
                FROM legacy_auth_attempts
                WHERE occurred_at >= now() - interval '15 minutes'
                """,
                (identity_hash, ip_hash),
            )
            row = cur.fetchone() or {}
        return (
            int(row.get("identity_failures") or 0),
            int(row.get("ip_failures") or 0),
            identity_hash,
            ip_hash,
        )

    def record_legacy_attempt(conn, identity_hash: str, ip_hash: str, *, succeeded: bool) -> None:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO legacy_auth_attempts (identity_digest, ip_digest, succeeded)
                VALUES (%s, %s, %s)
                """,
                (identity_hash, ip_hash, succeeded),
            )
            cur.execute(
                """
                DELETE FROM legacy_auth_attempts
                WHERE occurred_at < now() - interval '24 hours'
                """
            )

    # --- Throttling for the endpoints outside the legacy password flow --------
    #
    # These reuse the legacy_auth_attempts table rather than adding one. The
    # table has no column saying which endpoint an attempt belongs to, and it
    # does not need one: the digests are namespaced, so a failed recovery
    # attempt and a failed invite redemption for the same username land in
    # different buckets and cannot exhaust each other's budget. Nothing here
    # touches the buckets the legacy password flow already uses.

    def scoped_attempt_digests(scope: str, identity: Any = None) -> tuple[str, str]:
        """Return (identity_digest, ip_digest) for a throttle scope.

        When ``identity`` is None the endpoint has no name to key on -- the
        caller proves possession of a credential and nothing else -- so both
        dimensions collapse onto the peer address and the caller is expected to
        use the IP-only threshold.
        """

        ip_hash = secret_digest(f"{scope}-ip", _peer_ip_address())
        if identity is None:
            return ip_hash, ip_hash
        normalized = str(identity or "").strip().casefold()
        return secret_digest(f"{scope}-identity", normalized), ip_hash

    def scoped_attempt_failures(conn, identity_hash: str, ip_hash: str) -> tuple[int, int]:
        if not table_exists(conn, "legacy_auth_attempts"):
            return (0, 0)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE identity_digest=%s AND NOT succeeded)::int AS identity_failures,
                    COUNT(*) FILTER (WHERE ip_digest=%s AND NOT succeeded)::int AS ip_failures
                FROM legacy_auth_attempts
                WHERE occurred_at >= now() - interval '15 minutes'
                """,
                (identity_hash, ip_hash),
            )
            row = cur.fetchone() or {}
        return (
            int(row.get("identity_failures") or 0),
            int(row.get("ip_failures") or 0),
        )

    def record_scoped_attempt(identity_hash: str, ip_hash: str, *, succeeded: bool) -> None:
        """Record an attempt on a connection of its own.

        Every endpoint below rejects from inside ``with conn.transaction():``,
        where raising rolls the transaction back -- and a rejection that erases
        its own evidence is a throttle that never counts past one. A separate
        short-lived connection commits independently of whatever the caller's
        transaction decides to do.

        Failures here are swallowed: a throttle that cannot write must not be
        able to reject a legitimate login.
        """

        try:
            with connect() as attempt_conn:
                if not table_exists(attempt_conn, "legacy_auth_attempts"):
                    return
                record_legacy_attempt(attempt_conn, identity_hash, ip_hash, succeeded=succeeded)
                attempt_conn.commit()
        except Exception:  # pragma: no cover - defensive
            app.logger.exception("Failed to record authentication attempt")

    def enforce_scoped_throttle(
        conn,
        scope: str,
        identity: Any = None,
        *,
        ip_only: bool = False,
    ) -> tuple[str, str]:
        """Reject when this scope is over budget, else return its digests.

        The rejection is deliberately the same generic 401 the endpoint gives
        for a wrong credential: a distinct "you are rate limited" answer tells an
        attacker their probing is working and tells a legitimate user nothing
        they can act on.
        """

        identity_hash, ip_hash = scoped_attempt_digests(scope, identity)
        identity_failures, ip_failures = scoped_attempt_failures(conn, identity_hash, ip_hash)
        throttled = (
            ip_only_attempt_is_throttled(
                ip_failures,
                address_is_per_client=_throttle_address_is_per_client(),
            )
            if ip_only
            else attempt_is_throttled(identity_failures, ip_failures)
        )
        if throttled:
            record_scoped_attempt(identity_hash, ip_hash, succeeded=False)
            message, status = SCOPED_THROTTLE_RESPONSES[scope]
            raise next_api_error(message, status)
        return identity_hash, ip_hash

    def reconcile_review_legacy_user(conn) -> None:
        if not (
            legacy_auth_env_enabled()
            and _env_flag("REVIEW_LOGIN_ENABLED", default=False)
            and legacy_tables_available(conn)
        ):
            return
        configured_username = str(os.environ.get("REVIEW_LOGIN_USERNAME") or "").strip()
        configured_password = str(os.environ.get("REVIEW_LOGIN_PASSWORD") or "")
        if not configured_username or not configured_password or not role_exists(conn, "media_viewer"):
            return
        username = _normalize_username(
            os.environ.get("REVIEW_LOGIN_USER") or configured_username
        )
        expires_at = _review_login_expires_at()
        changed = False
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        u.id, u.username, u.status,
                        c.password_hash, c.mfa_required, c.passkey_registration_allowed,
                        c.credential_expires_at
                    FROM users u
                    LEFT JOIN legacy_password_credentials c ON c.user_id=u.id
                    WHERE lower(u.username)=lower(%s)
                    FOR UPDATE OF u
                    """,
                    (username,),
                )
                reviewer = cur.fetchone()
                if not reviewer:
                    reviewer_id = uuid4()
                    cur.execute(
                        """
                        INSERT INTO users (id, username, display_name, status, created_at, updated_at)
                        VALUES (%s, %s, 'App Store Reviewer', 'active', now(), now())
                        """,
                        (reviewer_id, username),
                    )
                    reviewer = {"id": reviewer_id, "username": username, "password_hash": None}
                    changed = True
                elif reviewer.get("status") != "active":
                    cur.execute(
                        "UPDATE users SET status='active', updated_at=now() WHERE id=%s",
                        (reviewer["id"],),
                    )
                    changed = True
                password_matches = verify_password(
                    reviewer.get("password_hash"), configured_password
                ).valid
                cur.execute(
                    """
                    INSERT INTO legacy_password_credentials (
                        user_id, password_hash, hash_version, must_change_password,
                        mfa_required, passkey_registration_allowed, credential_expires_at
                    )
                    VALUES (%s, %s, %s, false, false, true, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        password_hash=CASE
                            WHEN %s THEN legacy_password_credentials.password_hash
                            ELSE EXCLUDED.password_hash
                        END,
                        hash_version=EXCLUDED.hash_version,
                        must_change_password=false,
                        mfa_required=false,
                        passkey_registration_allowed=true,
                        credential_expires_at=EXCLUDED.credential_expires_at,
                        failed_attempt_count=0,
                        first_failed_at=NULL,
                        locked_until=NULL,
                        updated_at=now()
                    """,
                    (
                        reviewer["id"],
                        reviewer.get("password_hash")
                        if password_matches
                        else hash_password(configured_password),
                        PASSWORD_HASH_VERSION,
                        expires_at,
                        password_matches,
                    ),
                )
                cur.execute("DELETE FROM legacy_mfa_recovery_codes WHERE user_id=%s", (reviewer["id"],))
                cur.execute("DELETE FROM legacy_totp_credentials WHERE user_id=%s", (reviewer["id"],))
                cur.execute(
                    """
                    DELETE FROM user_roles
                    WHERE user_id=%s AND scope_type='global' AND scope_id=''
                      AND role_id NOT IN (SELECT id FROM roles WHERE key='media_viewer')
                    """,
                    (reviewer["id"],),
                )
            assign_role(conn, reviewer["id"], "media_viewer")
            if changed or not password_matches:
                audit_event(
                    conn,
                    event_type="auth.review_user_migrated",
                    actor={"id": reviewer["id"], "username": username, "role": "media_viewer"},
                    target_type="user",
                    target_id=reviewer["id"],
                    summary="Reconciled App Store Review as a password user",
                    metadata={
                        "mfaRequired": False,
                        "passkeyRegistrationAllowed": True,
                        "expiresAt": expires_at.isoformat() if expires_at else None,
                    },
                )

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
        """The same resolution as the module-level function, so share its memo.

        This closure was a duplicate of ``next_auth_current_user``. Keeping the
        copy would mean the handler resolved the caller again even though the
        ``before_request`` gate had already done it a moment earlier.
        """

        return next_auth_current_user(conn)

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

    def active_owner_passkey_count(conn) -> int:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(DISTINCT u.id) AS count
                FROM users u
                JOIN user_roles ur ON ur.user_id = u.id
                JOIN roles r ON r.id = ur.role_id
                JOIN passkey_credentials c ON c.user_id = u.id
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
                    MAX(c.last_used_at) AS last_credential_used_at,
                    COUNT(DISTINCT lc.user_id)::int AS legacy_credential_count,
                    COALESCE(bool_or(lc.must_change_password), false) AS legacy_must_change_password,
                    COALESCE(bool_or(lc.mfa_required), false) AS legacy_mfa_required,
                    COALESCE(bool_or(lc.passkey_registration_allowed), true) AS passkey_registration_allowed,
                    EXISTS (
                        SELECT 1 FROM legacy_totp_credentials lt
                        WHERE lt.user_id=u.id AND lt.confirmed_at IS NOT NULL
                    ) AS legacy_mfa_enrolled
                FROM users u
                LEFT JOIN passkey_credentials c ON c.user_id = u.id
                LEFT JOIN legacy_password_credentials lc ON lc.user_id = u.id
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

    def deny_api_token_actor(conn, actor: dict[str, Any]) -> None:
        """Refuse an actor that arrived on a bearer token, whatever its scope.

        The gates below ask "is this an owner or an admin" and stop. That is the
        right question for a session and the wrong one for a token: a token
        inherits its holder's role, so these routes -- roles, users, invites,
        switching authentication off, RBAC mode, ownership transfer, legacy
        credentials -- stood open to any token belonging to an admin, however
        narrowly it was scoped. Making the permission gates an intersection does
        not reach them, because they consult no permission at all.

        Nothing legitimate is lost: no route in the advertised native contract
        needs an admin role, and administrative permissions are not grantable to
        a token in the first place.
        """
        if not isinstance(actor.get("apiToken"), dict):
            return
        token = actor.get("apiToken") or {}
        message = "API tokens cannot be used for administrative actions"
        try:
            audit_event(
                conn,
                event_type="security.permission_denied",
                category="security",
                actor=actor,
                target_type="permission",
                target_id="administrative:session",
                summary=message,
                metadata={
                    "reason": "api_token_barred_from_role_only_gate",
                    "authMethod": "api_token",
                    "userRole": actor.get("role"),
                    "tokenPermissions": sorted(
                        str(item) for item in (token.get("permissionKeys") or [])
                    ),
                    "apiToken": {
                        "id": str(token.get("id")) if token.get("id") is not None else None,
                        "name": token.get("name"),
                        "scopes": token.get("scopes") or [],
                    },
                },
            )
            # Authorisation runs before the route mutates anything, so this
            # commits the denial on its own -- the raise below would otherwise
            # roll it back and leave the refusal unrecorded.
            conn.commit()
        except Exception:
            app.logger.exception("Failed to persist the token permission-denied audit event")
        raise next_api_error(message, 403)

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
        deny_api_token_actor(conn, user)
        return user

    def require_authenticated_admin(conn) -> dict[str, Any]:
        user = require_admin(conn)
        if user.get("id") is None:
            raise next_api_error("An authenticated Owner or Admin is required", 401)
        return user

    def require_owner(conn) -> dict[str, Any]:
        user = current_user(conn)
        if not user:
            raise next_api_error("Unauthorized", 401)
        role = primary_role(conn, user["id"])
        if role != "owner":
            raise next_api_error("Owner access required", 403)
        user["role"] = role
        deny_api_token_actor(conn, user)
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

    def login_challenge_key(challenge: bytes) -> str:
        """One row per login ceremony, addressed by the challenge itself.

        The challenge already makes the round trip inside clientDataJSON, so
        keying on it needs nothing new from any client -- which matters,
        because the iOS and Android apps live in other repositories and
        cannot be changed in step with the server.
        """

        return f"login:{_b64url_encode(challenge)}"

    def validate_invite(conn, username: str, invite_code: str) -> dict[str, Any] | None:
        if not invite_code:
            return None
        # Throttled here rather than at the four call sites: every path that
        # redeems an invite goes through this function, and a guard that has to
        # be remembered in four places is a guard that will be forgotten in one.
        identity_hash, ip_hash = enforce_scoped_throttle(conn, "invite", username)
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
            invite = cur.fetchone()
        record_scoped_attempt(identity_hash, ip_hash, succeeded=bool(invite))
        return invite

    def auth_status_payload(conn) -> dict[str, Any]:
        user_count = count_table(conn, "users")
        credential_count = count_table(conn, "passkey_credentials")
        legacy_credential_count = count_table(conn, "legacy_password_credentials")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS count FROM users WHERE status<>%s",
                (LEGACY_BOOTSTRAP_PENDING_STATUS,),
            )
            blocking_user_count = int((cur.fetchone() or {}).get("count") or 0)
        is_configured_auth_enabled = configured_auth_enabled(conn)
        is_auth_ready = auth_ready(conn)
        user = current_user(conn)
        role = primary_role(conn, user["id"]) if user else None
        review_expires_at: datetime | None = None
        legacy_available = legacy_auth_env_enabled() and legacy_tables_available(conn)
        effective_legacy = legacy_effective_enabled(conn)
        review_login_available = effective_legacy
        passkey_configuration_valid = _passkey_configuration_valid()
        passkey_access_valid = _passkey_access_valid()
        legacy_bootstrap_available = bool(
            legacy_available
            and not is_auth_ready
            and blocking_user_count == 0
            and credential_count == 0
        )
        try:
            review_expires_at = _review_login_expires_at()
        except ValueError:
            review_expires_at = None
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
            "legacy_credential_count": legacy_credential_count,
            "legacy_auth_available": legacy_available,
            "legacy_auth_enabled": effective_legacy,
            "legacy_auth_database_enabled": legacy_db_enabled(conn)
            if legacy_tables_available(conn)
            else False,
            "legacy_bootstrap_available": legacy_bootstrap_available,
            "legacy_bootstrap_mfa_optional": bool(
                legacy_bootstrap_available and _legacy_bootstrap_mfa_optional()
            ),
            "rp_id": _rp_id(),
            "rp_name": _rp_name(),
            "rp_origins": _rp_origins(),
            "passkey_configuration_valid": passkey_configuration_valid,
            "passkey_access_valid": passkey_access_valid,
            "request_host_is_local_ip": _request_host_is_local_ip(),
            "authenticated": bool(user),
            "user_id": user["id"] if user else None,
            "username": user["username"] if user else None,
            "display_name": user.get("display_name") if user else None,
            "role": role,
            "rbac_mode": rbac_mode(conn),
            "review_login_available": review_login_available,
            "review_login_expires_at": review_expires_at.isoformat() if review_expires_at else None,
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
            reconcile_review_legacy_user(conn)
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
        require_passkey_access()
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
            has_credentials = (
                count_table(conn, "passkey_credentials")
                + count_table(conn, "legacy_password_credentials")
            ) > 0
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS count FROM users WHERE status=%s",
                    (LEGACY_BOOTSTRAP_PENDING_STATUS,),
                )
                pending_bootstrap = int((cur.fetchone() or {}).get("count") or 0)
            if pending_bootstrap:
                raise next_api_error(
                    "Finish or restart the pending password onboarding before adding a passkey",
                    409,
                )
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
            if user and not passkey_registration_allowed(conn, user_id):
                raise next_api_error("Passkey registration is disabled for this user", 403)
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
        require_passkey_access()
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
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pg_advisory_xact_lock(hashtext('discvault-legacy-bootstrap'))"
                    )
                has_users = count_table(conn, "users") > 0
                has_credentials = (
                    count_table(conn, "passkey_credentials")
                    + count_table(conn, "legacy_password_credentials")
                ) > 0
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) AS count FROM users WHERE status=%s",
                        (LEGACY_BOOTSTRAP_PENDING_STATUS,),
                    )
                    pending_bootstrap = int((cur.fetchone() or {}).get("count") or 0)
                if pending_bootstrap:
                    raise next_api_error(
                        "Finish or restart the pending password onboarding before adding a passkey",
                        409,
                    )
                caller = current_user(conn)
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM users WHERE id=%s", (user_id,))
                    existing_user = cur.fetchone()
                if existing_user and not passkey_registration_allowed(conn, user_id):
                    raise next_api_error("Passkey registration is disabled for this user", 403)
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
        callback_url: str | None = None
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
                existing_user = current_user(conn)
                if existing_user:
                    mobile_code = create_mobile_auth_code(
                        conn,
                        mobile_flow_id=flow_id,
                        user_id=existing_user["id"],
                    )
                    callback_url = mobile_code["callbackUrl"]
                    audit_event(
                        conn,
                        event_type="auth.mobile_flow_completed",
                        category="security",
                        actor={
                            "id": existing_user["id"],
                            "username": existing_user["username"],
                            "role": primary_role(conn, existing_user["id"]),
                        },
                        target_type="user",
                        target_id=existing_user["id"],
                        summary=f"Completed mobile auth flow using active session for {existing_user['username']}",
                        metadata={"mobileFlowId": flow_id_str, "sessionShortCircuit": True},
                    )
        if callback_url:
            return redirect(callback_url, code=302)
        target = f"/?{urlencode({'mobile_flow': flow_id_str})}"
        return redirect(target, code=302)

    def require_first_owner_bootstrap_available(
        conn,
        *,
        cleanup_pending: bool = False,
    ) -> None:
        if cleanup_pending:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM users WHERE status=%s",
                    (LEGACY_BOOTSTRAP_PENDING_STATUS,),
                )
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS count FROM users")
            blocking_users = int((cur.fetchone() or {}).get("count") or 0)
        if blocking_users or count_table(conn, "passkey_credentials") or auth_ready(conn):
            raise next_api_error("First-owner password onboarding is no longer available", 409)

    def create_legacy_bootstrap_owner(
        conn,
        *,
        payload: dict[str, Any],
        mfa_required: bool,
        accepted_step: int | None = None,
    ) -> dict[str, Any]:
        user_id = uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (id, username, display_name, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, now(), now())
                """,
                (
                    user_id,
                    payload["username"],
                    payload["displayName"],
                    LEGACY_BOOTSTRAP_PENDING_STATUS,
                ),
            )
            cur.execute(
                """
                INSERT INTO legacy_password_credentials (
                    user_id, password_hash, hash_version, must_change_password,
                    mfa_required, passkey_registration_allowed
                )
                VALUES (%s, %s, %s, false, %s, true)
                """,
                (
                    user_id,
                    payload["passwordHash"],
                    PASSWORD_HASH_VERSION,
                    mfa_required,
                ),
            )
            if mfa_required:
                cur.execute(
                    """
                    INSERT INTO legacy_totp_credentials (
                        user_id, encrypted_secret, nonce, confirmed_at, last_accepted_step
                    )
                    VALUES (%s, %s, %s, NULL, %s)
                    """,
                    (
                        user_id,
                        _b64url_decode(payload["encryptedSecret"]),
                        _b64url_decode(payload["nonce"]),
                        accepted_step,
                    ),
                )
        assign_role(conn, user_id, "owner")
        recovery_codes = next_generate_recovery_codes()
        with conn.cursor() as cur:
            next_replace_recovery_codes(cur, user_id, recovery_codes)
        set_setting(conn, LEGACY_AUTH_SETTING, True)
        set_setting(conn, "auth_enabled", True)
        ack_token = issue_legacy_flow(
            conn,
            "bootstrap_recovery_ack",
            user_id=user_id,
            payload={"mfaRequired": mfa_required},
            seconds=RECOVERY_ACK_FLOW_TTL_SECONDS,
        )
        return {
            "status": "ok",
            "stage": "recovery_codes",
            "recovery_codes": recovery_codes,
            "flow_token": ack_token,
        }

    @route(
        "/api/next/auth/legacy/bootstrap/start",
        "/api/auth/legacy/bootstrap/start",
        methods=["POST"],
    )
    def legacy_bootstrap_start():
        if not legacy_auth_env_enabled():
            raise next_api_error("Password onboarding is unavailable", 404)
        body = request.get_json(silent=True) or {}
        mfa_optional = _legacy_bootstrap_mfa_optional()
        mfa_required = body.get("mfa_enabled") is True if mfa_optional else True
        try:
            username = _normalize_username(body.get("username"))
            password = validate_password(body.get("password"), username)
        except (ValueError, PasswordPolicyError) as exc:
            raise next_api_error(str(exc), 400) from exc
        display_name = str(body.get("display_name") or username).strip() or username
        with connect() as conn:
            if not legacy_tables_available(conn):
                raise next_api_error("Legacy authentication tables are not available", 503)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_xact_lock(hashtext('discvault-legacy-bootstrap'))")
                require_first_owner_bootstrap_available(conn, cleanup_pending=True)
                payload = {
                    "username": username,
                    "displayName": display_name,
                    "passwordHash": hash_password(password),
                }
                if not mfa_required:
                    return response(
                        create_legacy_bootstrap_owner(
                            conn,
                            payload=payload,
                            mfa_required=False,
                        )
                    )
                secret_value = generate_totp_secret()
                nonce, ciphertext = encrypt_totp_secret(secret_value)
                payload.update(
                    {
                        "nonce": _b64url_encode(nonce),
                        "encryptedSecret": _b64url_encode(ciphertext),
                    }
                )
                token = issue_legacy_flow(conn, "bootstrap_totp", payload=payload)
        return response(
            {
                "status": "ok",
                "stage": "bootstrap_totp",
                "flow_token": token,
                "manual_key": secret_value,
                "otpauth_uri": totp_uri(secret_value, username),
                "qr_data_uri": totp_qr_data_uri(totp_uri(secret_value, username)),
            }
        )

    @route(
        "/api/next/auth/legacy/bootstrap/verify",
        "/api/auth/legacy/bootstrap/verify",
        methods=["POST"],
    )
    def legacy_bootstrap_verify():
        if not legacy_auth_env_enabled():
            raise next_api_error("Password onboarding is unavailable", 404)
        body = request.get_json(silent=True) or {}
        with connect() as conn:
            if not legacy_tables_available(conn):
                raise next_api_error("Legacy authentication tables are not available", 503)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_xact_lock(hashtext('discvault-legacy-bootstrap'))")
                flow = get_legacy_flow(
                    conn,
                    body.get("flow_token"),
                    flow_types={"bootstrap_totp"},
                    consume=True,
                )
                require_first_owner_bootstrap_available(conn)
                payload = flow["payload"]
                secret_value = decrypt_totp_secret(
                    _b64url_decode(payload["nonce"]),
                    _b64url_decode(payload["encryptedSecret"]),
                )
                accepted_step = verify_totp(secret_value, body.get("code"))
                if accepted_step is None:
                    record_legacy_attempt(
                        conn,
                        identity_digest(payload["username"]),
                        ip_digest(request_ip_address()),
                        succeeded=False,
                    )
                    return response(
                        {"status": "error", "error": "Invalid authentication code"},
                        400,
                    )
                result = create_legacy_bootstrap_owner(
                    conn,
                    payload=payload,
                    mfa_required=True,
                    accepted_step=accepted_step,
                )
        return response(result)

    @route(
        "/api/next/auth/legacy/recovery-codes/ack",
        "/api/auth/legacy/recovery-codes/ack",
        methods=["POST"],
    )
    def legacy_recovery_codes_ack():
        body = request.get_json(silent=True) or {}
        if body.get("acknowledged") is not True:
            raise next_api_error("Recovery-code acknowledgement is required", 400)
        with connect() as conn:
            require_legacy_enabled(conn)
            with conn.transaction():
                flow = get_legacy_flow(
                    conn,
                    body.get("flow_token"),
                    flow_types={"bootstrap_recovery_ack", "mfa_recovery_ack"},
                    consume=True,
                )
                if flow["flow_type"] == "bootstrap_recovery_ack":
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE users
                            SET status='active', updated_at=now()
                            WHERE id=%s AND status=%s
                            RETURNING id, username, display_name, status
                            """,
                            (flow["user_id"], LEGACY_BOOTSTRAP_PENDING_STATUS),
                        )
                        user = cur.fetchone()
                    if not user:
                        raise next_api_error(
                            "Password onboarding is expired or already completed",
                            400,
                        )
                else:
                    user = legacy_user_row(conn, flow["user_id"])
                mfa_required = flow["payload"].get("mfaRequired", True) is True
                if mfa_required:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE legacy_totp_credentials
                            SET confirmed_at=now(), updated_at=now()
                            WHERE user_id=%s
                            """,
                            (user["id"],),
                        )
                        if flow["flow_type"] == "mfa_recovery_ack":
                            cur.execute(
                                """
                                UPDATE legacy_password_credentials
                                SET mfa_required=true, updated_at=now()
                                WHERE user_id=%s
                                """,
                                (user["id"],),
                            )
                if flow["flow_type"] == "bootstrap_recovery_ack":
                    audit_event(
                        conn,
                        event_type="auth.legacy_owner_bootstrapped",
                        actor={
                            "id": user["id"],
                            "username": user["username"],
                            "role": "owner",
                        },
                        target_type="user",
                        target_id=user["id"],
                        summary=(
                            "Created the first Owner with password and TOTP"
                            if mfa_required
                            else "Created the first Owner with password on a local address"
                        ),
                        metadata={
                            "mfaRequired": mfa_required,
                            "passkeyRegistrationAllowed": True,
                            "localPasswordFallback": not mfa_required,
                            "recoveryCodesAcknowledged": True,
                        },
                    )
                if flow["payload"].get("profileEnrollment") is True:
                    audit_event(
                        conn,
                        event_type="auth.legacy_mfa_enabled",
                        category="security",
                        actor={
                            "id": user["id"],
                            "username": user["username"],
                            "role": primary_role(conn, user["id"]),
                        },
                        target_type="user",
                        target_id=user["id"],
                        summary=f"Enabled two-factor authentication for {user['username']}",
                        metadata={"recoveryCodesAcknowledged": True},
                    )
                    return response({"status": "ok", "stage": "complete"})
                return legacy_complete_login(
                    conn,
                    user=user,
                    context=flow["payload"],
                    method="totp_enrollment" if mfa_required else "password_bootstrap",
                )

    @route("/api/next/auth/legacy/login", "/api/auth/legacy/login", methods=["POST"])
    def legacy_login():
        body = request.get_json(silent=True) or {}
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        if not username or not password:
            raise next_api_error("Invalid username or password", 401)
        context = legacy_client_context(body)
        database_username = username
        configured_review_username = str(
            os.environ.get("REVIEW_LOGIN_USERNAME") or ""
        ).strip()
        if (
            _env_flag("REVIEW_LOGIN_ENABLED", default=False)
            and configured_review_username
            and constant_time_text_equal(username, configured_review_username)
        ):
            database_username = str(
                os.environ.get("REVIEW_LOGIN_USER") or configured_review_username
            ).strip()
        with connect() as conn:
            require_legacy_enabled(conn)
            with nullcontext():
                identity_failures, ip_failures, identity_hash, ip_hash = recent_legacy_attempts(conn, username)
                if attempt_is_throttled(identity_failures, ip_failures):
                    record_legacy_attempt(conn, identity_hash, ip_hash, succeeded=False)
                    conn.commit()
                    raise next_api_error("Invalid username or password", 401)
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT
                            u.id, u.username, u.display_name, u.status,
                            c.password_hash, c.hash_version, c.must_change_password,
                            c.mfa_required, c.passkey_registration_allowed,
                            c.failed_attempt_count, c.first_failed_at, c.locked_until,
                            c.credential_expires_at
                        FROM users u
                        JOIN legacy_password_credentials c ON c.user_id=u.id
                        WHERE lower(u.username)=lower(%s)
                        FOR UPDATE
                        """,
                        (database_username,),
                    )
                    credential = cur.fetchone()
                if not credential:
                    # Argon2 work keeps the unknown-user path close to a known-user failure.
                    verify_password(hash_password("unknown-user-padding-value"), password)
                    record_legacy_attempt(conn, identity_hash, ip_hash, succeeded=False)
                    conn.commit()
                    raise next_api_error("Invalid username or password", 401)
                now = _utcnow()
                expired = bool(
                    credential.get("credential_expires_at")
                    and credential["credential_expires_at"] <= now
                )
                locked = bool(credential.get("locked_until") and credential["locked_until"] > now)
                verification = verify_password(credential["password_hash"], password)
                credentials_ok = verification.valid and not expired and not locked
                if (
                    credentials_ok
                    and credential.get("status") == LEGACY_BOOTSTRAP_PENDING_STATUS
                ):
                    # Onboarding created this account but never acknowledged the
                    # recovery codes, so it is stuck outside 'active'. The password
                    # already verified above, so resume the flow instead of
                    # reporting a valid credential as invalid.
                    record_legacy_attempt(conn, identity_hash, ip_hash, succeeded=True)
                    return resume_legacy_bootstrap(
                        conn,
                        credential=credential,
                        context=context,
                    )
                if (
                    credential.get("status") != "active"
                    or expired
                    or locked
                    or not verification.valid
                ):
                    failed_count, first_failed_at, new_locked_until = failed_attempt_state(
                        int(credential.get("failed_attempt_count") or 0),
                        credential.get("first_failed_at"),
                        now,
                    )
                    locked_until = new_locked_until or credential.get("locked_until")
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE legacy_password_credentials
                            SET failed_attempt_count=%s, first_failed_at=%s,
                                locked_until=%s, updated_at=now()
                            WHERE user_id=%s
                            """,
                            (failed_count, first_failed_at, locked_until, credential["id"]),
                        )
                    record_legacy_attempt(conn, identity_hash, ip_hash, succeeded=False)
                    conn.commit()
                    raise next_api_error("Invalid username or password", 401)
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE legacy_password_credentials
                        SET failed_attempt_count=0, first_failed_at=NULL, locked_until=NULL,
                            password_hash=CASE WHEN %s THEN %s ELSE password_hash END,
                            hash_version=%s, updated_at=now()
                        WHERE user_id=%s
                        """,
                        (
                            verification.needs_rehash,
                            hash_password(password) if verification.needs_rehash else credential["password_hash"],
                            PASSWORD_HASH_VERSION,
                            credential["id"],
                        ),
                    )
                record_legacy_attempt(conn, identity_hash, ip_hash, succeeded=True)
                user = {
                    "id": credential["id"],
                    "username": credential["username"],
                    "display_name": credential.get("display_name"),
                    "status": credential["status"],
                }
                return legacy_next_stage(
                    conn,
                    user=user,
                    credential=credential,
                    context=context,
                )

    @route(
        "/api/next/auth/legacy/password/change",
        "/api/auth/legacy/password/change",
        methods=["POST"],
    )
    def legacy_password_change():
        body = request.get_json(silent=True) or {}
        with connect() as conn:
            require_legacy_enabled(conn)
            with conn.transaction():
                flow_token = body.get("flow_token")
                if flow_token:
                    flow = get_legacy_flow(
                        conn,
                        flow_token,
                        flow_types={"password_change"},
                        consume=True,
                    )
                    user = legacy_user_row(conn, flow["user_id"])
                    context = flow["payload"]
                else:
                    user = current_user(conn)
                    if not user:
                        raise next_api_error("Unauthorized", 401)
                    context = {}
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT *
                        FROM legacy_password_credentials
                        WHERE user_id=%s
                        FOR UPDATE
                        """,
                        (user["id"],),
                    )
                    credential = cur.fetchone()
                if not credential:
                    raise next_api_error("Password credential not found", 404)
                if not flow_token and not verify_password(
                    credential["password_hash"], body.get("current_password")
                ).valid:
                    raise next_api_error("Current password is invalid", 401)
                try:
                    new_password = validate_password(body.get("new_password"), user["username"])
                except PasswordPolicyError as exc:
                    raise next_api_error(str(exc), 400) from exc
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE legacy_password_credentials
                        SET password_hash=%s, hash_version=%s, must_change_password=false,
                            password_changed_at=now(), failed_attempt_count=0,
                            first_failed_at=NULL, locked_until=NULL, updated_at=now()
                        WHERE user_id=%s
                        """,
                        (hash_password(new_password), PASSWORD_HASH_VERSION, user["id"]),
                    )
                audit_event(
                    conn,
                    event_type="auth.legacy_password_changed",
                    actor={
                        "id": user["id"],
                        "username": user["username"],
                        "role": primary_role(conn, user["id"]),
                    },
                    target_type="user",
                    target_id=user["id"],
                    summary=f"Changed password for {user['username']}",
                    metadata={"forcedChange": bool(flow_token)},
                )
                credential["must_change_password"] = False
                if flow_token:
                    return legacy_next_stage(
                        conn,
                        user=user,
                        credential=credential,
                        context=context,
                    )
        return response({"status": "ok", "stage": "complete"})

    @route("/api/next/auth/legacy/me", "/api/auth/legacy/me", methods=["GET"])
    def legacy_me():
        with connect() as conn:
            user = current_user(conn)
            if not user:
                raise next_api_error("Unauthorized", 401)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        c.must_change_password, c.mfa_required,
                        c.passkey_registration_allowed, c.password_changed_at,
                        c.last_login_at,
                        EXISTS (
                            SELECT 1 FROM legacy_totp_credentials t
                            WHERE t.user_id=c.user_id AND t.confirmed_at IS NOT NULL
                        ) AS mfa_enrolled,
                        (
                            SELECT COUNT(*) FROM recovery_codes r
                            WHERE r.user_id=c.user_id
                              AND r.used_at IS NULL
                              AND (r.expires_at IS NULL OR r.expires_at > now())
                        )::int AS active_recovery_codes
                    FROM legacy_password_credentials c
                    WHERE c.user_id=%s
                    """,
                    (user["id"],),
                )
                credential = cur.fetchone()
        return response(
            {
                "status": "ok",
                "has_credential": bool(credential),
                "credential": credential,
            }
        )

    @route(
        "/api/next/auth/legacy/mfa/enroll/start",
        "/api/auth/legacy/mfa/enroll/start",
        methods=["POST"],
    )
    def legacy_mfa_enroll_start():
        body = request.get_json(silent=True) or {}
        with connect() as conn:
            require_legacy_enabled(conn)
            with nullcontext():
                if not _current_user_payload():
                    raise next_api_error("Unauthorized", 401)
                user = current_user(conn)
                if not user:
                    raise next_api_error("Unauthorized", 401)
                identity_failures, ip_failures, identity_hash, ip_hash = recent_legacy_attempts(
                    conn, user["username"]
                )
                if attempt_is_throttled(identity_failures, ip_failures):
                    record_legacy_attempt(conn, identity_hash, ip_hash, succeeded=False)
                    conn.commit()
                    raise next_api_error("Current password is invalid", 401)
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT
                            c.password_hash, c.must_change_password, c.mfa_required,
                            c.failed_attempt_count, c.first_failed_at,
                            c.locked_until, c.credential_expires_at,
                            EXISTS (
                                SELECT 1 FROM legacy_totp_credentials t
                                WHERE t.user_id=c.user_id
                                  AND t.confirmed_at IS NOT NULL
                            ) AS mfa_enrolled
                        FROM legacy_password_credentials c
                        WHERE c.user_id=%s
                        FOR UPDATE
                        """,
                        (user["id"],),
                    )
                    credential = cur.fetchone()
                if not credential:
                    raise next_api_error("Password credential not found", 404)
                if credential.get("mfa_required") and credential.get("mfa_enrolled"):
                    raise next_api_error("Two-factor authentication is already enabled", 409)
                if credential.get("must_change_password"):
                    raise next_api_error(
                        "Change your password before enabling two-factor authentication",
                        409,
                    )
                now = _utcnow()
                credential_unavailable = (
                    credential.get("locked_until")
                    and credential["locked_until"] > now
                ) or (
                    credential.get("credential_expires_at")
                    and credential["credential_expires_at"] <= now
                )
                password_verification = verify_password(
                    credential["password_hash"], body.get("current_password")
                )
                if credential_unavailable or not password_verification.valid:
                    failed_count, first_failed_at, new_locked_until = failed_attempt_state(
                        int(credential.get("failed_attempt_count") or 0),
                        credential.get("first_failed_at"),
                        now,
                    )
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE legacy_password_credentials
                            SET failed_attempt_count=%s, first_failed_at=%s,
                                locked_until=%s, updated_at=now()
                            WHERE user_id=%s
                            """,
                            (
                                failed_count,
                                first_failed_at,
                                new_locked_until or credential.get("locked_until"),
                                user["id"],
                            ),
                        )
                    record_legacy_attempt(conn, identity_hash, ip_hash, succeeded=False)
                    conn.commit()
                    raise next_api_error("Current password is invalid", 401)
                record_legacy_attempt(conn, identity_hash, ip_hash, succeeded=True)
                return issue_mfa_enrollment(conn, user, {"profileEnrollment": True})

    @route("/api/next/auth/legacy/mfa/setup", "/api/auth/legacy/mfa/setup", methods=["POST"])
    def legacy_mfa_setup():
        body = request.get_json(silent=True) or {}
        with connect() as conn:
            require_legacy_enabled(conn)
            flow = get_legacy_flow(
                conn,
                body.get("flow_token"),
                flow_types={"mfa_enrollment"},
            )
            user = legacy_user_row(conn, flow["user_id"])
            secret_value = decrypt_totp_secret(
                _b64url_decode(flow["payload"]["nonce"]),
                _b64url_decode(flow["payload"]["encryptedSecret"]),
            )
        return response(
            {
                "status": "ok",
                "stage": "mfa_enrollment",
                "manual_key": secret_value,
                "otpauth_uri": totp_uri(secret_value, user["username"]),
                "qr_data_uri": totp_qr_data_uri(totp_uri(secret_value, user["username"])),
            }
        )

    @route(
        "/api/next/auth/legacy/mfa/setup/verify",
        "/api/auth/legacy/mfa/setup/verify",
        methods=["POST"],
    )
    def legacy_mfa_setup_verify():
        body = request.get_json(silent=True) or {}
        with connect() as conn:
            require_legacy_enabled(conn)
            with conn.transaction():
                flow = get_legacy_flow(
                    conn,
                    body.get("flow_token"),
                    flow_types={"mfa_enrollment"},
                    consume=True,
                )
                user = legacy_user_row(conn, flow["user_id"])
                nonce = _b64url_decode(flow["payload"]["nonce"])
                ciphertext = _b64url_decode(flow["payload"]["encryptedSecret"])
                secret_value = decrypt_totp_secret(nonce, ciphertext)
                accepted_step = verify_totp(secret_value, body.get("code"))
                if accepted_step is None:
                    record_legacy_attempt(
                        conn,
                        identity_digest(user["username"]),
                        ip_digest(request_ip_address()),
                        succeeded=False,
                    )
                    return response(
                        {"status": "error", "error": "Invalid authentication code"},
                        400,
                    )
                recovery_codes = next_generate_recovery_codes()
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO legacy_totp_credentials (
                            user_id, encrypted_secret, nonce, confirmed_at, last_accepted_step
                        )
                        VALUES (%s, %s, %s, NULL, %s)
                        ON CONFLICT (user_id) DO UPDATE SET
                            encrypted_secret=EXCLUDED.encrypted_secret,
                            nonce=EXCLUDED.nonce,
                            confirmed_at=NULL,
                            last_accepted_step=EXCLUDED.last_accepted_step,
                            updated_at=now()
                        """,
                        (user["id"], ciphertext, nonce, accepted_step),
                    )
                    next_replace_recovery_codes(cur, user["id"], recovery_codes)
                ack_token = issue_legacy_flow(
                    conn,
                    "mfa_recovery_ack",
                    user_id=user["id"],
                    payload={
                        "mobileFlow": flow["payload"].get("mobileFlow"),
                        "clientKind": flow["payload"].get("clientKind"),
                        # Carried across the recovery acknowledgement so the token
                        # minted at the end of a multi-step login still knows which
                        # device it belongs to.
                        "device": flow["payload"].get("device"),
                        "profileEnrollment": flow["payload"].get("profileEnrollment") is True,
                    },
                    seconds=RECOVERY_ACK_FLOW_TTL_SECONDS,
                )
                audit_event(
                    conn,
                    event_type="auth.legacy_mfa_enrolled",
                    actor={
                        "id": user["id"],
                        "username": user["username"],
                        "role": primary_role(conn, user["id"]),
                    },
                    target_type="user",
                    target_id=user["id"],
                    summary=f"Enrolled TOTP for {user['username']}",
                    metadata={"recoveryCodeCount": len(recovery_codes)},
                )
        return response(
            {
                "status": "ok",
                "stage": "recovery_codes",
                "recovery_codes": recovery_codes,
                "flow_token": ack_token,
            }
        )

    @route("/api/next/auth/legacy/mfa/verify", "/api/auth/legacy/mfa/verify", methods=["POST"])
    def legacy_mfa_verify():
        body = request.get_json(silent=True) or {}
        with connect() as conn:
            require_legacy_enabled(conn)
            with conn.transaction():
                flow = get_legacy_flow(
                    conn,
                    body.get("flow_token"),
                    flow_types={"mfa_challenge"},
                    consume=True,
                )
                user = legacy_user_row(conn, flow["user_id"])
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT encrypted_secret, nonce, last_accepted_step
                        FROM legacy_totp_credentials
                        WHERE user_id=%s AND confirmed_at IS NOT NULL
                        FOR UPDATE
                        """,
                        (user["id"],),
                    )
                    totp_row = cur.fetchone()
                if not totp_row:
                    return response(
                        {"status": "error", "error": "Invalid authentication code"},
                        400,
                    )
                accepted_step = verify_totp(
                    decrypt_totp_secret(totp_row["nonce"], totp_row["encrypted_secret"]),
                    body.get("code"),
                    last_accepted_step=totp_row.get("last_accepted_step"),
                )
                if accepted_step is None:
                    record_legacy_attempt(
                        conn,
                        identity_digest(user["username"]),
                        ip_digest(request_ip_address()),
                        succeeded=False,
                    )
                    return response(
                        {"status": "error", "error": "Invalid authentication code"},
                        401,
                    )
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE legacy_totp_credentials
                        SET last_accepted_step=%s, updated_at=now()
                        WHERE user_id=%s
                        """,
                        (accepted_step, user["id"]),
                    )
                return legacy_complete_login(
                    conn,
                    user=user,
                    context=flow["payload"],
                    method="totp",
                )

    @route("/api/next/auth/legacy/mfa/recovery", "/api/auth/legacy/mfa/recovery", methods=["POST"])
    def legacy_mfa_recovery():
        body = request.get_json(silent=True) or {}
        with connect() as conn:
            require_legacy_enabled(conn)
            with conn.transaction():
                flow = get_legacy_flow(
                    conn,
                    body.get("flow_token"),
                    flow_types={"mfa_challenge"},
                    consume=True,
                )
                user = legacy_user_row(conn, flow["user_id"])
                with conn.cursor() as cur:
                    matched = next_consume_recovery_code(
                        cur,
                        user["id"],
                        body.get("recovery_code"),
                    )
                if not matched:
                    record_legacy_attempt(
                        conn,
                        identity_digest(user["username"]),
                        ip_digest(request_ip_address()),
                        succeeded=False,
                    )
                    return response(
                        {"status": "error", "error": "Invalid authentication code"},
                        401,
                    )
                return legacy_complete_login(
                    conn,
                    user=user,
                    context=flow["payload"],
                    method="recovery_code",
                )

    @route("/api/next/auth/review/login", "/api/auth/review/login", methods=["POST"])
    def review_login():
        if legacy_auth_env_enabled():
            return legacy_login()
        if not _env_flag("REVIEW_LOGIN_ENABLED", default=False):
            raise next_api_error("Review login is disabled", 404)

        configured_username = str(os.environ.get("REVIEW_LOGIN_USERNAME") or "").strip()
        configured_password = str(os.environ.get("REVIEW_LOGIN_PASSWORD") or "").strip()
        reviewer_username_raw = str(
            os.environ.get("REVIEW_LOGIN_USER") or configured_username or "appstore-reviewer"
        ).strip()
        if not configured_username or not configured_password:
            raise next_api_error("Review login is not configured", 503)

        try:
            configured_username = _normalize_username(configured_username)
            reviewer_username = _normalize_username(reviewer_username_raw)
        except ValueError as exc:
            raise next_api_error(str(exc), 400) from exc

        expires_at = _review_login_expires_at()
        if expires_at and _utcnow() >= expires_at:
            raise next_api_error("Review login is expired", 403)

        body = request.get_json(silent=True) or {}
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        if not username or not password:
            raise next_api_error("username and password are required", 400)
        # Only this branch needs its own throttle. When LEGACY_AUTH_ENABLED is
        # set, the route returned through legacy_login() above and was counted
        # there; this is the fallback that compares against environment values
        # and never opens a connection of its own, so it opens one here.
        with connect() as throttle_conn:
            identity_hash, ip_hash = enforce_scoped_throttle(
                throttle_conn, "review", configured_username
            )
        if not (
            constant_time_text_equal(username, configured_username)
            and constant_time_text_equal(password, configured_password)
        ):
            record_scoped_attempt(identity_hash, ip_hash, succeeded=False)
            raise next_api_error("Invalid credentials", 401)
        record_scoped_attempt(identity_hash, ip_hash, succeeded=True)

        mobile_flow_raw = body.get("mobile_flow") or body.get("mobileFlow")
        mobile_flow = _parse_uuid(mobile_flow_raw)
        if mobile_flow_raw and not mobile_flow:
            raise next_api_error("mobile_flow is invalid", 400)

        review_callback_url: str | None = None
        with connect() as conn:
            if not table_exists(conn, "users"):
                raise next_api_error("Auth tables are not available", 503)
            if not role_exists(conn, "media_viewer"):
                raise next_api_error("Review login requires the media_viewer role", 503)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, username, display_name, status
                        FROM users
                        WHERE username=%s
                        FOR UPDATE
                        """,
                        (reviewer_username,),
                    )
                    reviewer = cur.fetchone()
                    if not reviewer:
                        reviewer_id = uuid4()
                        cur.execute(
                            """
                            INSERT INTO users (id, username, display_name, status, created_at, updated_at)
                            VALUES (%s, %s, %s, 'active', now(), now())
                            RETURNING id, username, display_name, status
                            """,
                            (reviewer_id, reviewer_username, "App Store Reviewer"),
                        )
                        reviewer = cur.fetchone()
                    elif reviewer.get("status") != "active":
                        cur.execute(
                            """
                            UPDATE users
                            SET status='active', updated_at=now()
                            WHERE id=%s
                            RETURNING id, username, display_name, status
                            """,
                            (reviewer["id"],),
                        )
                        reviewer = cur.fetchone()
                    else:
                        cur.execute("UPDATE users SET updated_at=now() WHERE id=%s", (reviewer["id"],))
                    if table_exists(conn, "user_roles"):
                        cur.execute(
                            """
                            DELETE FROM user_roles
                            WHERE user_id=%s
                              AND scope_type='global'
                              AND scope_id=''
                            """,
                            (reviewer["id"],),
                        )
                assign_role(conn, reviewer["id"], "media_viewer")
                token_payload = issue_review_api_token(
                    conn,
                    user_id=reviewer["id"],
                    username=reviewer["username"],
                    expires_at=expires_at,
                    device=next_native_device_context(
                        request.get_json(silent=True) or {},
                        user_agent=request.headers.get("User-Agent"),
                    ),
                )
                reviewer_role = primary_role(conn, reviewer["id"])
                effective_permission_keys = sorted(user_permissions(conn, reviewer["id"]))
                audit_event(
                    conn,
                    event_type="auth.review_login",
                    category="security",
                    actor={
                        "id": reviewer["id"],
                        "username": reviewer["username"],
                        "role": reviewer_role,
                    },
                    target_type="api_access_token",
                    target_id=token_payload["tokenRow"]["id"],
                    summary=f"Issued review login token for {reviewer['username']}",
                    metadata={
                        "reviewLogin": True,
                        "tokenPermissionKeys": token_payload["permissionKeys"],
                        "effectivePermissionKeys": effective_permission_keys,
                        "expiresAt": expires_at.isoformat() if expires_at else None,
                    },
                )
                if mobile_flow:
                    mobile_code = create_mobile_auth_code(
                        conn,
                        mobile_flow_id=mobile_flow,
                        user_id=reviewer["id"],
                    )
                    review_callback_url = mobile_code["callbackUrl"]
                    audit_event(
                        conn,
                        event_type="auth.mobile_code_issued",
                        category="security",
                        actor={
                            "id": reviewer["id"],
                            "username": reviewer["username"],
                            "role": reviewer_role,
                        },
                        target_type="user",
                        target_id=reviewer["id"],
                        summary=f"Issued mobile one-time code for {reviewer['username']} via review login",
                        metadata={"mobileFlowId": str(mobile_flow), "reviewLogin": True},
                    )

        if review_callback_url:
            return response(
                {
                    "status": "ok",
                    "callback_url": review_callback_url,
                    "callbackUrl": review_callback_url,
                    "token": token_payload["token"],
                    "username": reviewer["username"],
                    "role": reviewer_role,
                }
            )

        return response(
            {
                "status": "ok",
                "token": token_payload["token"],
                "username": reviewer["username"],
                "role": reviewer_role,
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
                "review": {
                    "expiresAt": expires_at.isoformat() if expires_at else None,
                },
            }
        )

    @route(
        "/api/next/auth/passkeys/login/options",
        "/api/next/auth/passkey/login/options",
        "/api/next/auth/login/options",
        "/api/auth/login/options",
        methods=["POST"],
    )
    def login_options():
        require_passkey_access()
        body = request.get_json(silent=True) or {}
        client_kind = next_normalize_client_kind(
            body.get("client_kind") or body.get("clientKind")
        )
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
                elif client_kind in NATIVE_CLIENT_KINDS:
                    credentials = []
                else:
                    cur.execute("SELECT id FROM passkey_credentials ORDER BY created_at")
                    credentials = cur.fetchall()
            challenge = _make_challenge()
            with conn.transaction():
                # Keyed by the challenge itself rather than a fixed "login"
                # slot. One slot meant one pending login for the whole
                # instance: two people signing in at the same moment
                # overwrote each other, and the first to finish failed on a
                # challenge that no longer existed. On a passkey-only
                # instance that is the entire front door.
                store_challenge(conn, login_challenge_key(challenge), challenge)

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
        require_passkey_access()
        body = request.get_json(silent=True) or {}
        credential = body.get("credential") or {}
        device = next_native_device_context(body, user_agent=request.headers.get("User-Agent"))
        client_kind = device["clientKind"]
        mobile_flow_raw = body.get("mobile_flow") or body.get("mobileFlow")
        mobile_flow = _parse_uuid(mobile_flow_raw)
        if mobile_flow_raw and not mobile_flow:
            raise next_api_error("mobile_flow is invalid", 400)
        credential_id = str(credential.get("id") or credential.get("rawId") or "")
        if not credential_id:
            raise next_api_error("Credential id is required", 400)

        with connect() as conn:
            # A passkey assertion names a credential, not a user, so this scope
            # counts on the peer address alone under the IP-only ceiling. The
            # point is not to make guessing a signature harder -- that is already
            # infeasible -- but to bound how cheaply this route can be driven,
            # since each call consumes a stored challenge row.
            identity_hash, ip_hash = enforce_scoped_throttle(
                conn, "passkey-verify", ip_only=True
            )
            # The challenge is read out of clientDataJSON before anything is
            # trusted, purely to address the row it belongs to. Nothing is
            # believed on its say-so: a value we never issued finds no row, and
            # a value we did issue is consumed here so it cannot be replayed.
            try:
                presented_challenge = _b64url_decode(
                    str(
                        (json.loads(_b64url_decode(credential["response"]["clientDataJSON"])) or {})
                        .get("challenge")
                        or ""
                    )
                )
            except Exception:
                presented_challenge = b""
            # Decoded to bytes and re-encoded through the same helper the store
            # side uses, so padding differences between clients cannot turn a
            # valid challenge into a missing row.
            challenge = (
                pop_challenge(conn, login_challenge_key(presented_challenge))
                if presented_challenge
                else None
            )
            if not challenge:
                record_scoped_attempt(identity_hash, ip_hash, succeeded=False)
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
                record_scoped_attempt(identity_hash, ip_hash, succeeded=False)
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
                record_scoped_attempt(identity_hash, ip_hash, succeeded=False)
                raise next_api_error(f"Verification failed: {exc}", 400) from exc

            # The signature counter was written on every login and compared on
            # none, so the one thing it exists to reveal -- the same credential
            # in two places -- went unnoticed.
            #
            # Only meaningful when both sides count. Passkeys synced through
            # iCloud Keychain or a password manager report 0 forever by design,
            # and treating a permanent 0 as a regression would reject every
            # login from the authenticators most people actually use.
            stored_sign_count = int(stored.get("sign_count") or 0)
            if new_sign_count and stored_sign_count and new_sign_count <= stored_sign_count:
                audit_event(
                    conn,
                    event_type="auth.passkey_sign_count_regressed",
                    category="security",
                    actor={
                        "id": stored["user_id"],
                        "username": stored["username"],
                        "role": None,
                    },
                    target_type="passkey_credential",
                    target_id=str(credential_id),
                    summary="Passkey signature counter did not advance",
                    metadata={
                        "storedSignCount": stored_sign_count,
                        "presentedSignCount": int(new_sign_count),
                    },
                )
                conn.commit()
                record_scoped_attempt(identity_hash, ip_hash, succeeded=False)
                raise next_api_error("Verification failed: signature counter did not advance", 400)

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
                if client_kind in NATIVE_CLIENT_KINDS:
                    payload = native_login_response(
                        conn,
                        user_id=stored["user_id"],
                        username=stored["username"],
                        display_name=stored.get("display_name"),
                        device=device,
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
                        summary=f"Issued {next_native_token_name(client_kind)} token for {stored['username']}",
                        metadata={
                            "clientKind": client_kind,
                            "deviceLabel": device.get("deviceLabel"),
                            "deviceModel": device.get("deviceModel"),
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
        # The deep-link scheme is shared between platforms and this route never
        # sees the login itself, so the device identity has to come from the
        # exchange body — otherwise every native client lands under one label.
        device = next_native_device_context(body, user_agent=request.headers.get("User-Agent"))
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
            # No username is known until the code resolves, so this scope counts
            # on the peer address alone and uses the wider IP-only ceiling.
            identity_hash, ip_hash = enforce_scoped_throttle(
                conn, "mobile-exchange", ip_only=True
            )
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
                        record_scoped_attempt(identity_hash, ip_hash, succeeded=False)
                        raise next_api_error("Mobile auth code is expired or already used", 400)
                    if row["user_status"] != "active":
                        raise next_api_error("User is disabled", 403)
                    if not secrets.compare_digest(str(row["code_challenge"]), expected_challenge):
                        record_scoped_attempt(identity_hash, ip_hash, succeeded=False)
                        raise next_api_error("PKCE verification failed", 400)
                    cur.execute("UPDATE mobile_auth_codes SET used_at=now() WHERE id=%s", (row["id"],))
                    token_payload = issue_mobile_api_token(
                        conn,
                        user_id=row["user_id"],
                        username=row["username"],
                        device=device,
                    )
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
                        "clientKind": next_native_client_kind_or_default(device.get("clientKind")),
                        "deviceLabel": device.get("deviceLabel"),
                        "deviceModel": device.get("deviceModel"),
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
        mobile_flow_raw = body.get("mobile_flow") or body.get("mobileFlow")
        mobile_flow = _parse_uuid(mobile_flow_raw)
        if mobile_flow_raw and not mobile_flow:
            raise next_api_error("mobile_flow is invalid", 400)
        with connect() as conn:
            if not table_exists(conn, "users") or not table_exists(conn, "recovery_codes"):
                raise next_api_error("Recovery is not available yet", 503)
            identity_hash, ip_hash = enforce_scoped_throttle(conn, "recovery", username)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT u.id AS user_id, u.username, u.status AS user_status
                        FROM users u
                        WHERE u.username=%s
                        """,
                        (username,),
                    )
                    row = cur.fetchone()
                    if not row:
                        record_scoped_attempt(identity_hash, ip_hash, succeeded=False)
                        raise next_api_error("Invalid username or recovery code", 401)
                    # The status check deliberately sits *after* the code is
                    # consumed. Ahead of it, "User is disabled" answered anyone
                    # who guessed a username with a wrong code, which is a
                    # membership oracle: 403 meant the account exists, 401 meant
                    # it does not. Behind it, the caller has already proven they
                    # hold a valid recovery code for that account, so telling
                    # them why they still cannot get in reveals nothing they did
                    # not already know.
                    if not next_consume_recovery_code(cur, row["user_id"], recovery_code):
                        record_scoped_attempt(identity_hash, ip_hash, succeeded=False)
                        raise next_api_error("Invalid username or recovery code", 401)
                    if row["user_status"] != "active":
                        raise next_api_error("User is disabled", 403)
                    record_scoped_attempt(identity_hash, ip_hash, succeeded=True)
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
                    metadata={"remainingRecoveryCodes": remaining, "mobileFlow": bool(mobile_flow)},
                )
                if mobile_flow:
                    mobile_code = create_mobile_auth_code(
                        conn,
                        mobile_flow_id=mobile_flow,
                        user_id=row["user_id"],
                    )
                    audit_event(
                        conn,
                        event_type="auth.mobile_code_issued",
                        category="security",
                        actor={
                            "id": row["user_id"],
                            "username": row["username"],
                            "role": primary_role(conn, row["user_id"]),
                        },
                        target_type="user",
                        target_id=row["user_id"],
                        summary=f"Issued mobile one-time code for {row['username']} via recovery code",
                        metadata={"mobileFlowId": str(mobile_flow), "recovery": True},
                    )
                    return response(
                        {
                            "status": "ok",
                            "recovery": True,
                            "remainingRecoveryCodes": remaining,
                            "callback_url": mobile_code["callbackUrl"],
                            "callbackUrl": mobile_code["callbackUrl"],
                            "state": mobile_code.get("state"),
                        }
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
        require_passkey_access()
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
        require_passkey_access()
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

    @route(
        "/api/next/auth/legacy/settings",
        "/api/auth/legacy/settings",
        methods=["GET", "PATCH"],
    )
    def legacy_settings():
        with connect() as conn:
            admin = require_authenticated_admin(conn)
            if request.method == "PATCH":
                if not legacy_auth_env_enabled():
                    raise next_api_error("Password login is unavailable", 404)
                body = request.get_json(silent=True) or {}
                enabled = bool(body.get("enabled"))
                if enabled:
                    raise next_api_error("Use fresh passkey confirmation to enable password login", 400)
                if active_owner_passkey_count(conn) == 0:
                    raise next_api_error(
                        "An active Owner must have a passkey before password login can be disabled",
                        400,
                    )
                with conn.transaction():
                    set_setting(conn, LEGACY_AUTH_SETTING, False)
                    audit_event(
                        conn,
                        event_type="auth.legacy_disabled",
                        actor=admin,
                        target_type="setting",
                        target_id=LEGACY_AUTH_SETTING,
                        summary="Disabled password login",
                        metadata={"enabled": False},
                    )
            payload = {
                "available": legacy_auth_env_enabled() and legacy_tables_available(conn),
                "database_enabled": legacy_db_enabled(conn)
                if legacy_tables_available(conn)
                else False,
                "effective_enabled": legacy_effective_enabled(conn),
            }
        return response({"status": "ok", "legacy_auth": payload})

    @route(
        "/api/next/auth/legacy/settings/enable/options",
        "/api/auth/legacy/settings/enable/options",
        methods=["POST"],
    )
    def legacy_settings_enable_options():
        require_passkey_access()
        with connect() as conn:
            if not legacy_auth_env_enabled():
                raise next_api_error("Password login is unavailable", 404)
            admin = require_authenticated_admin(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM passkey_credentials WHERE user_id=%s ORDER BY created_at",
                    (admin["id"],),
                )
                credentials = cur.fetchall()
            if not credentials:
                raise next_api_error("A passkey is required to enable password login", 400)
            challenge = _make_challenge()
            with conn.transaction():
                store_challenge(conn, f"legacy_enable:{admin['id']}", challenge)
        options = {
            "challenge": _b64url_encode(challenge),
            "timeout": 60000,
            "rpId": _rp_id(),
            "allowCredentials": [
                {"type": "public-key", "id": row["id"]} for row in credentials
            ],
            "userVerification": "required",
        }
        return response({"status": "ok", "options": options})

    @route(
        "/api/next/auth/legacy/settings/enable/verify",
        "/api/auth/legacy/settings/enable/verify",
        methods=["POST"],
    )
    def legacy_settings_enable_verify():
        require_passkey_access()
        if not legacy_auth_env_enabled():
            raise next_api_error("Password login is unavailable", 404)
        body = request.get_json(silent=True) or {}
        with connect() as conn:
            admin = require_authenticated_admin(conn)
            with conn.transaction():
                stored = verify_step_up_assertion(
                    conn,
                    challenge_key=f"legacy_enable:{admin['id']}",
                    expected_user_id=admin["id"],
                    credential=body.get("credential") or {},
                )
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE passkey_credentials
                        SET sign_count=%s, last_used_at=now()
                        WHERE id=%s
                        """,
                        (stored["new_sign_count"], stored["id"]),
                    )
                set_setting(conn, LEGACY_AUTH_SETTING, True)
                audit_event(
                    conn,
                    event_type="auth.legacy_enabled",
                    actor=admin,
                    target_type="setting",
                    target_id=LEGACY_AUTH_SETTING,
                    summary="Enabled password login after passkey confirmation",
                    metadata={"enabled": True, "stepUp": "passkey"},
                )
        return response({"status": "ok", "legacy_auth_enabled": True})

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
                        MAX(c.last_used_at) AS last_credential_used_at,
                        COUNT(DISTINCT lc.user_id)::int AS legacy_credential_count,
                        COALESCE(bool_or(lc.must_change_password), false) AS legacy_must_change_password,
                        COALESCE(bool_or(lc.mfa_required), false) AS legacy_mfa_required,
                        COALESCE(bool_or(lc.passkey_registration_allowed), true) AS passkey_registration_allowed,
                        EXISTS (
                            SELECT 1 FROM legacy_totp_credentials lt
                            WHERE lt.user_id=u.id AND lt.confirmed_at IS NOT NULL
                        ) AS legacy_mfa_enrolled
                    FROM users u
                    LEFT JOIN passkey_credentials c ON c.user_id = u.id
                    LEFT JOIN legacy_password_credentials lc ON lc.user_id = u.id
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

    @route("/api/next/auth/users", "/api/auth/users", methods=["POST"])
    def create_legacy_user():
        body = request.get_json(silent=True) or {}
        try:
            username = _normalize_username(body.get("username"))
            password = validate_password(
                body.get("temporary_password") or body.get("password"),
                username,
            )
        except (ValueError, PasswordPolicyError) as exc:
            raise next_api_error(str(exc), 400) from exc
        display_name = str(body.get("display_name") or username).strip() or username
        raw_roles = body.get("roles")
        role_keys = (
            normalize_role_key_list(raw_roles)
            if raw_roles is not None
            else [normalize_role_key(body.get("role") or "media_viewer")]
        )
        mfa_required = bool(body.get("mfa_required", True))
        passkey_allowed = bool(body.get("passkey_registration_allowed", True))
        with connect() as conn:
            if not legacy_auth_env_enabled():
                raise next_api_error("Password credentials are unavailable", 404)
            if not legacy_tables_available(conn):
                raise next_api_error("Legacy authentication tables are not available", 503)
            admin = require_authenticated_admin(conn)
            if "owner" in role_keys:
                raise next_api_error("Owner accounts must use the ownership transfer flow", 400)
            for role_key in role_keys:
                if not role_exists(conn, role_key) or not role_assignable(
                    conn, role_key, admin.get("role")
                ):
                    raise next_api_error(f"Role is not assignable: {role_key}", 400)
            user_id = uuid4()
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM users WHERE lower(username)=lower(%s)", (username,))
                    if cur.fetchone():
                        raise next_api_error("User already exists", 409)
                    cur.execute(
                        """
                        INSERT INTO users (id, username, display_name, status, created_at, updated_at)
                        VALUES (%s, %s, %s, 'active', now(), now())
                        """,
                        (user_id, username, display_name),
                    )
                    cur.execute(
                        """
                        INSERT INTO legacy_password_credentials (
                            user_id, password_hash, hash_version, must_change_password,
                            mfa_required, passkey_registration_allowed, created_by
                        )
                        VALUES (%s, %s, %s, true, %s, %s, %s)
                        """,
                        (
                            user_id,
                            hash_password(password),
                            PASSWORD_HASH_VERSION,
                            mfa_required,
                            passkey_allowed,
                            admin.get("id"),
                        ),
                    )
                for role_key in role_keys:
                    assign_role(conn, user_id, role_key)
                audit_event(
                    conn,
                    event_type="auth.legacy_user_created",
                    actor=admin,
                    target_type="user",
                    target_id=user_id,
                    summary=f"Created password user {username}",
                    metadata={
                        "roles": role_keys,
                        "mfaRequired": mfa_required,
                        "passkeyRegistrationAllowed": passkey_allowed,
                        "temporaryPassword": True,
                    },
                )
            created = user_admin_row(conn, user_id)
        return response({"status": "ok", "user": created}, 201)

    @route("/api/next/auth/users/<user_id>", "/api/auth/users/<user_id>", methods=["PATCH"])
    def update_user(user_id: str):
        user_uuid = _parse_uuid(user_id)
        if not user_uuid:
            raise next_api_error("Invalid user id", 400)
        body = request.get_json(silent=True) or {}
        status = str(body.get("status") or "").strip().lower()
        display_name = body.get("display_name")

        with connect() as conn:
            admin = require_authenticated_admin(conn)
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
            admin = require_authenticated_admin(conn)
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

    @route(
        "/api/next/auth/users/<user_id>/legacy-credential",
        "/api/auth/users/<user_id>/legacy-credential",
        methods=["GET", "PUT", "PATCH", "DELETE"],
    )
    def manage_legacy_credential(user_id: str):
        user_uuid = _parse_uuid(user_id)
        if not user_uuid:
            raise next_api_error("Invalid user id", 400)
        body = request.get_json(silent=True) or {}
        with connect() as conn:
            if not legacy_auth_env_enabled():
                raise next_api_error("Password credentials are unavailable", 404)
            if not legacy_tables_available(conn):
                raise next_api_error("Legacy authentication tables are not available", 503)
            admin = require_authenticated_admin(conn)
            target = user_admin_row(conn, user_uuid)
            if not target:
                raise next_api_error("User not found", 404)
            if target.get("role") == "owner" and admin.get("role") != "owner":
                raise next_api_error("Only owners can modify owner credentials", 403)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT user_id, must_change_password, mfa_required,
                           passkey_registration_allowed, credential_expires_at,
                           password_changed_at, last_login_at, locked_until
                    FROM legacy_password_credentials
                    WHERE user_id=%s
                    """,
                    (user_uuid,),
                )
                credential = cur.fetchone()
            if request.method == "GET":
                return response({"status": "ok", "credential": credential})
            if request.method == "DELETE":
                if not credential:
                    raise next_api_error("Password credential not found", 404)
                if str(admin.get("id")) == str(user_uuid):
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT COUNT(*) AS count FROM passkey_credentials WHERE user_id=%s",
                            (user_uuid,),
                        )
                        passkey_count = int(cur.fetchone()["count"])
                    if passkey_count == 0:
                        raise next_api_error(
                            "Add a passkey before removing your own password credential",
                            400,
                        )
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM legacy_auth_flows WHERE user_id=%s", (user_uuid,))
                        cur.execute("DELETE FROM legacy_mfa_recovery_codes WHERE user_id=%s", (user_uuid,))
                        cur.execute("DELETE FROM legacy_totp_credentials WHERE user_id=%s", (user_uuid,))
                        cur.execute("DELETE FROM legacy_password_credentials WHERE user_id=%s", (user_uuid,))
                    audit_event(
                        conn,
                        event_type="auth.legacy_credential_removed",
                        actor=admin,
                        target_type="user",
                        target_id=user_uuid,
                        summary=f"Removed password credential for {target['username']}",
                        metadata={},
                    )
                return response({"status": "deleted"})

            mfa_required = bool(
                body.get(
                    "mfa_required",
                    credential.get("mfa_required") if credential else True,
                )
            )
            passkey_allowed = bool(
                body.get(
                    "passkey_registration_allowed",
                    credential.get("passkey_registration_allowed") if credential else True,
                )
            )
            if request.method == "PUT":
                try:
                    password = validate_password(
                        body.get("temporary_password") or body.get("password"),
                        target["username"],
                    )
                except PasswordPolicyError as exc:
                    raise next_api_error(str(exc), 400) from exc
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO legacy_password_credentials (
                                user_id, password_hash, hash_version, must_change_password,
                                mfa_required, passkey_registration_allowed, created_by,
                                password_changed_at, updated_at
                            )
                            VALUES (%s, %s, %s, true, %s, %s, %s, now(), now())
                            ON CONFLICT (user_id) DO UPDATE SET
                                password_hash=EXCLUDED.password_hash,
                                hash_version=EXCLUDED.hash_version,
                                must_change_password=true,
                                mfa_required=EXCLUDED.mfa_required,
                                passkey_registration_allowed=EXCLUDED.passkey_registration_allowed,
                                failed_attempt_count=0,
                                first_failed_at=NULL,
                                locked_until=NULL,
                                credential_expires_at=NULL,
                                password_changed_at=now(),
                                updated_at=now()
                            """,
                            (
                                user_uuid,
                                hash_password(password),
                                PASSWORD_HASH_VERSION,
                                mfa_required,
                                passkey_allowed,
                                admin.get("id"),
                            ),
                        )
                        cur.execute("DELETE FROM legacy_auth_flows WHERE user_id=%s", (user_uuid,))
                        if not mfa_required:
                            cur.execute("DELETE FROM legacy_mfa_recovery_codes WHERE user_id=%s", (user_uuid,))
                            cur.execute("DELETE FROM legacy_totp_credentials WHERE user_id=%s", (user_uuid,))
                    audit_event(
                        conn,
                        event_type="auth.legacy_credential_reset",
                        actor=admin,
                        target_type="user",
                        target_id=user_uuid,
                        summary=f"Set a temporary password for {target['username']}",
                        metadata={
                            "mfaRequired": mfa_required,
                            "passkeyRegistrationAllowed": passkey_allowed,
                        },
                    )
            else:
                if not credential:
                    raise next_api_error("Password credential not found", 404)
                previous_mfa = bool(credential.get("mfa_required"))
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE legacy_password_credentials
                            SET mfa_required=%s, passkey_registration_allowed=%s, updated_at=now()
                            WHERE user_id=%s
                            """,
                            (mfa_required, passkey_allowed, user_uuid),
                        )
                        if previous_mfa != mfa_required:
                            cur.execute("DELETE FROM legacy_auth_flows WHERE user_id=%s", (user_uuid,))
                            cur.execute("DELETE FROM legacy_mfa_recovery_codes WHERE user_id=%s", (user_uuid,))
                            cur.execute("DELETE FROM legacy_totp_credentials WHERE user_id=%s", (user_uuid,))
                    audit_event(
                        conn,
                        event_type="auth.legacy_policy_changed",
                        actor=admin,
                        target_type="user",
                        target_id=user_uuid,
                        summary=f"Changed password policy for {target['username']}",
                        metadata={
                            "mfaRequired": mfa_required,
                            "passkeyRegistrationAllowed": passkey_allowed,
                        },
                    )
            updated = user_admin_row(conn, user_uuid)
        return response({"status": "ok", "user": updated})

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
                # The v2 gate is a separate setting from the v1 receiver above:
                # they send different things to different systems, and an owner
                # who allowed one has not thereby allowed the other.
                if "movievault_v2_contribution_enabled" in body:
                    v2_enabled = bool(body.get("movievault_v2_contribution_enabled"))
                    with conn.transaction():
                        set_setting(conn, "movievault_v2_contribution_enabled", v2_enabled)
                        audit_event(
                            conn,
                            event_type="movievault.release_contribution_setting_changed",
                            category="plugins",
                            actor=owner,
                            target_type="setting",
                            target_id="movievault_v2_contribution_enabled",
                            summary="MovieVault release contribution setting changed",
                            metadata={"enabled": v2_enabled},
                        )
                if body.get("movievault_v2_contribution_reset"):
                    # Registration cannot be recovered - MovieVault attaches no
                    # account to a pseudonymous instance - so resetting means
                    # the next send registers a brand new identity.
                    with conn.transaction():
                        reset_movievault_v2_contribution_registration(conn)
                        audit_event(
                            conn,
                            event_type="movievault.release_contribution_identity_reset",
                            category="plugins",
                            actor=owner,
                            target_type="setting",
                            target_id="movievault_v2_contribution_instance_id",
                            summary="MovieVault contribution identity reset",
                            metadata={},
                        )
            settings = {
                "movievault_contribution_enabled": bool(
                    setting_value(conn, "movievault_contribution_enabled", False)
                ),
                "movievault_v2_contribution_enabled": bool(
                    setting_value(conn, "movievault_v2_contribution_enabled", False)
                ),
                "movievault_v2_contribution": movievault_v2_contribution_state(conn),
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

    _register_admin_dedup_routes(
        route=route,
        connect=connect,
        response=response,
        next_api_error=next_api_error,
        require_admin=require_admin,
        require_authenticated_admin=require_authenticated_admin,
        require_passkey_access=require_passkey_access,
        verify_step_up_assertion=verify_step_up_assertion,
        audit_event=audit_event,
        make_challenge=_make_challenge,
        store_challenge=store_challenge,
        b64url_encode=_b64url_encode,
        rp_id=_rp_id,
    )

    if legacy_auth_env_enabled() and _env_flag("REVIEW_LOGIN_ENABLED", default=False):
        try:
            with connect() as startup_conn:
                reconcile_review_legacy_user(startup_conn)
        except (PsycopgError, RuntimeError, ValueError) as exc:
            app.logger.warning("Legacy review-user reconciliation deferred: %s", exc)
