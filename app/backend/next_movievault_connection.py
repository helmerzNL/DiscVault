"""Zero-config MovieVault connection management for DiscVault Next."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib import error as url_error
from urllib import request as url_request

try:
    from psycopg.types.json import Jsonb
except ModuleNotFoundError:  # pragma: no cover - allows import-only tests without psycopg
    class Jsonb:  # type: ignore[no-redef]
        def __init__(self, value: Any) -> None:
            self.value = value

try:
    from .versioning import backend_version, software_identity
    from .next_runtime_secrets import key_encryption_secret
except ImportError:  # pragma: no cover - supports running modules directly
    from versioning import backend_version, software_identity
    from next_runtime_secrets import key_encryption_secret


MOVIEVAULT_PLUGIN_ID = "movievault"
MOVIEVAULT_NEXT_PLUGIN_ID = "movievault_26"
MOVIEVAULT_PLUGIN_IDS = frozenset({MOVIEVAULT_PLUGIN_ID, MOVIEVAULT_NEXT_PLUGIN_ID})
REQUESTED_SCOPES = ("search:read", "contributions:write", "contributions:read")
BOOTSTRAP_PATH = "/api/v1/internal/discvault/bootstrap"
HANDSHAKE_PATH = "/api/v1/internal/discvault/handshake"
DEFAULT_MOVIEVAULT_URL = "https://movies.vaultstack.eu"
DEFAULT_SEARCH_URL = DEFAULT_MOVIEVAULT_URL
DEFAULT_INGEST_URL = DEFAULT_MOVIEVAULT_URL

# --- MovieVault /api/v3 signed metadata API ---
# The v3 surface mandates per-request Ed25519 signing on every endpoint and a
# renamed/generalized bootstrap. It reuses the same instance keypair as the
# legacy v1 signer but registers it under a v3 ``iosk_`` key id and carries its
# own bearer token / instance id (a browser-style ``web_<uuid>``).
V3_BOOTSTRAP_PATH = "/api/v3/clients/bootstrap"
HEALTH_PATH = "/api/v1/health"
V3_REQUESTED_SCOPES = ("metadata:read",)
V3_API_TOKEN_KEY = "movievault_v3_api_token"
V3_KEY_ID_KEY = "movievault_v3_key_id"
V3_INSTANCE_ID_KEY = "movievault_v3_instance_id"
V3_TOKEN_PREFIX_KEY = "movievault_v3_token_prefix"
V3_SCOPES_KEY = "movievault_v3_scopes"
V3_LAST_BOOTSTRAP_AT_KEY = "movievault_v3_last_bootstrap_at"

# Marker prefix for Fernet-encrypted secrets stored at rest. Values without this
# prefix are treated as legacy plaintext and transparently migrated on next read.
SECRET_ENC_PREFIX = "enc.v1:"

INSTANCE_ID_KEY = "movievault_instance_id"
INSTANCE_NAME_KEY = "movievault_instance_name"
INSTANCE_PRIVATE_KEY_KEY = "movievault_instance_private_key"
INSTANCE_PUBLIC_KEY_KEY = "movievault_instance_public_key"
INSTANCE_PUBLIC_KEY_ID_KEY = "movievault_instance_public_key_id"
TOKEN_PREFIX_KEY = "movievault_token_prefix"
TOKEN_SCOPES_KEY = "movievault_token_scopes"
LAST_BOOTSTRAP_AT_KEY = "movievault_last_bootstrap_at"
LAST_HANDSHAKE_AT_KEY = "movievault_last_handshake_at"
LINK_STATUS_KEY = "movievault_link_status"
AUTH_METHOD_KEY = "movievault_auth_method"
CONTRIBUTION_ENABLED_KEY = "movievault_contribution_enabled"
CONTRIBUTION_URL_KEY = "movievault_contribution_url"
SHARING_MODE_KEY = "movievault_sharing_mode"


def is_movievault_plugin(plugin_id: str | None) -> bool:
    return str(plugin_id or "") in MOVIEVAULT_PLUGIN_IDS


def _plugin_token_secret_key(plugin_id: str | None = None) -> str:
    plugin = str(plugin_id or MOVIEVAULT_PLUGIN_ID)
    if plugin not in MOVIEVAULT_PLUGIN_IDS:
        plugin = MOVIEVAULT_PLUGIN_ID
    return f"plugin_secret:{plugin}:token"


class MovieVaultConnectionError(RuntimeError):
    """Raised when DiscVault cannot connect to MovieVault."""


class MovieVaultInstanceRevoked(MovieVaultConnectionError):
    """Raised when MovieVault has revoked this DiscVault instance."""


class MovieVaultClientVersionUnsupported(MovieVaultConnectionError):
    """Raised when MovieVault rejects this DiscVault build as too old (HTTP 426)."""

    def __init__(self, message: str, *, min_version: str = "", detected_version: str = "") -> None:
        super().__init__(message)
        self.min_version = min_version
        self.detected_version = detected_version


def _table_exists(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL AS exists", (table_name,))
        row = cur.fetchone()
    return bool(row and row["exists"])


def _setting_value(conn, key: str, default: Any = None, *, include_secret: bool = False) -> Any:
    if not _table_exists(conn, "app_settings"):
        return default
    sql = "SELECT value FROM app_settings WHERE key=%s"
    params: tuple[Any, ...] = (key,)
    if not include_secret:
        sql += " AND is_secret=false"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return row["value"] if row else default


def _set_setting(conn, key: str, value: Any, *, is_secret: bool = False, actor_id: Any = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app_settings (key, value, is_secret, updated_at, updated_by)
            VALUES (%s, %s, %s, now(), %s)
            ON CONFLICT (key) DO UPDATE SET
                value=EXCLUDED.value,
                is_secret=EXCLUDED.is_secret,
                updated_at=now(),
                updated_by=EXCLUDED.updated_by
            """,
            (key, Jsonb(value), is_secret, actor_id),
        )


def _delete_setting(conn, key: str) -> None:
    if not _table_exists(conn, "app_settings"):
        return
    with conn.cursor() as cur:
        cur.execute("DELETE FROM app_settings WHERE key=%s", (key,))


def _text(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def _bool_setting(conn, key: str, default: bool) -> bool:
    value = _setting_value(conn, key, default)
    if isinstance(value, bool):
        return value
    return _text(value, "true" if default else "false").lower() in {"1", "true", "yes", "on"}


def _plugin_config(conn, plugin_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    if not _table_exists(conn, "plugin_settings"):
        return {}, {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT settings, secrets_ref FROM plugin_settings WHERE plugin_id=%s",
            (plugin_id if is_movievault_plugin(plugin_id) else MOVIEVAULT_PLUGIN_ID,),
        )
        row = cur.fetchone()
    if not row:
        return {}, {}
    return dict(row.get("settings") or {}), dict(row.get("secrets_ref") or {})


def _search_url(conn) -> str:
    value = (
        _setting_value(conn, "movievault_search_url", "")
        or os.environ.get("MOVIEVAULT_SEARCH_URL")
        or os.environ.get("MOVIEVAULT_BASE_URL")
        or DEFAULT_SEARCH_URL
    )
    return _text(value, DEFAULT_SEARCH_URL).rstrip("/")


def _ingest_url(conn) -> str:
    value = (
        _setting_value(conn, "movievault_ingest_url", "")
        or os.environ.get("MOVIEVAULT_INGEST_URL")
        or DEFAULT_INGEST_URL
    )
    return _text(value, DEFAULT_INGEST_URL).rstrip("/")


def _contribution_url(conn) -> str:
    value = (
        _setting_value(conn, CONTRIBUTION_URL_KEY, "")
        or os.environ.get("MOVIEVAULT_CONTRIBUTION_URL")
        or os.environ.get("MOVIEVAULT_INGEST_URL")
        or DEFAULT_MOVIEVAULT_URL
    )
    return _text(value, DEFAULT_MOVIEVAULT_URL).rstrip("/")


def _handshake_secret(conn) -> str:
    return _text(
        os.environ.get("MOVIEVAULT_DISCVAULT_HANDSHAKE_SECRET")
        or _setting_value(conn, "movievault_discvault_handshake_secret", "", include_secret=True)
    )


def movievault_enabled(conn) -> bool:
    return _bool_setting(conn, "movievault_enabled", True)


def _token_from_ref(conn, key: str) -> str:
    if not key:
        return ""
    return _text(_setting_value(conn, key, "", include_secret=True))


def stored_movievault_token(conn, plugin_id: str | None = None) -> str:
    _settings, secrets_ref = _plugin_config(conn, plugin_id)
    token_ref = secrets_ref.get("token") or {}
    token_key = token_ref.get("key") if isinstance(token_ref, dict) else token_ref
    token = _token_from_ref(conn, _text(token_key))
    if token:
        return token
    for candidate_plugin_id in (plugin_id, MOVIEVAULT_PLUGIN_ID, MOVIEVAULT_NEXT_PLUGIN_ID):
        token = _text(_setting_value(conn, _plugin_token_secret_key(candidate_plugin_id), "", include_secret=True))
        if token:
            return token
    return _text(
        os.environ.get("MOVIEVAULT_API_TOKEN")
        or os.environ.get("MOVIEVAULT_API_KEY")
        or _setting_value(conn, "movievault_api_token", "", include_secret=True)
        or _setting_value(conn, "movievault_api_key", "", include_secret=True)
    )


def _store_plugin_token(conn, token: str, *, plugin_id: str | None = None, actor_id: Any = None) -> None:
    plugin = plugin_id if is_movievault_plugin(plugin_id) else MOVIEVAULT_PLUGIN_ID
    token_key = _plugin_token_secret_key(plugin)
    settings, secrets_ref = _plugin_config(conn, plugin)
    secrets_ref["token"] = {"key": token_key, "configured": True}
    _set_setting(conn, token_key, token, is_secret=True, actor_id=actor_id)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO plugin_settings (plugin_id, settings, secrets_ref, updated_at, updated_by)
            VALUES (%s, %s, %s, now(), %s)
            ON CONFLICT (plugin_id) DO UPDATE SET
                settings=EXCLUDED.settings,
                secrets_ref=EXCLUDED.secrets_ref,
                updated_at=now(),
                updated_by=EXCLUDED.updated_by
            """,
            (plugin, Jsonb(settings), Jsonb(secrets_ref), actor_id),
        )
        if _table_exists(conn, "metadata_plugin_settings"):
            cur.execute(
                """
                INSERT INTO metadata_plugin_settings (plugin_id, settings, secrets_ref, updated_at, updated_by)
                VALUES (%s, %s, %s, now(), %s)
                ON CONFLICT (plugin_id) DO UPDATE SET
                    settings=EXCLUDED.settings,
                    secrets_ref=EXCLUDED.secrets_ref,
                    updated_at=now(),
                    updated_by=EXCLUDED.updated_by
                """,
                (plugin, Jsonb(settings), Jsonb(secrets_ref), actor_id),
            )


def _delete_token(conn) -> None:
    for key in (
        _plugin_token_secret_key(MOVIEVAULT_PLUGIN_ID),
        _plugin_token_secret_key(MOVIEVAULT_NEXT_PLUGIN_ID),
        "movievault_api_token",
        "movievault_api_key",
    ):
        _delete_setting(conn, key)
    if not _table_exists(conn, "plugin_settings"):
        return
    for plugin in MOVIEVAULT_PLUGIN_IDS:
        _settings, secrets_ref = _plugin_config(conn, plugin)
        if "token" not in secrets_ref:
            continue
        secrets_ref.pop("token", None)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE plugin_settings
                SET secrets_ref=%s, updated_at=now()
                WHERE plugin_id=%s
                """,
                (Jsonb(secrets_ref), plugin),
            )
            if _table_exists(conn, "metadata_plugin_settings"):
                cur.execute(
                    """
                    UPDATE metadata_plugin_settings
                    SET secrets_ref=%s, updated_at=now()
                    WHERE plugin_id=%s
                    """,
                    (Jsonb(secrets_ref), plugin),
                )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _key_encryption_key() -> bytes:
    """Derive a stable Fernet key for encrypting instance secrets at rest.

    A dedicated key-encryption secret takes precedence over the required app JWT
    secret. No database-derived fallback is permitted because a database dump must
    not contain enough information to recover encrypted integration credentials.
    """
    configured = key_encryption_secret()
    return base64.urlsafe_b64encode(hashlib.sha256(configured.encode("utf-8")).digest())


def _encrypt_secret_value(plaintext: str) -> str:
    """Fernet-encrypt a secret for storage. Returns plaintext unchanged if empty."""
    text = plaintext or ""
    if not text:
        return text
    try:
        from cryptography.fernet import Fernet
    except Exception:  # pragma: no cover - cryptography is a hard dependency in prod
        return text
    token = Fernet(_key_encryption_key()).encrypt(text.encode("utf-8"))
    return SECRET_ENC_PREFIX + token.decode("ascii")


def _decrypt_secret_value(stored: Any) -> str:
    """Decrypt a value written by :func:`_encrypt_secret_value`.

    Legacy plaintext values (no marker prefix) are returned as-is so existing
    installs keep working; callers re-encrypt them on next write.
    """
    text = _text(stored)
    if not text.startswith(SECRET_ENC_PREFIX):
        return text
    try:
        from cryptography.fernet import Fernet

        token = text[len(SECRET_ENC_PREFIX):].encode("ascii")
        return Fernet(_key_encryption_key()).decrypt(token).decode("utf-8")
    except Exception as exc:  # pragma: no cover - defensive
        raise MovieVaultConnectionError(
            "Unable to decrypt the stored MovieVault signing key; verify the "
            "DISCVAULT key-encryption configuration or reset the connection."
        ) from exc


def _is_encrypted_secret(stored: Any) -> bool:
    return _text(stored).startswith(SECRET_ENC_PREFIX)


def _raw_json_body(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _software_version() -> str:
    return backend_version()


def _instance_name(conn) -> str:
    existing = _text(_setting_value(conn, INSTANCE_NAME_KEY, ""))
    if existing:
        return existing
    name = _text(os.environ.get("RP_NAME") or os.environ.get("DISCVAULT_INSTANCE_NAME") or "DiscVault")[:120]
    _set_setting(conn, INSTANCE_NAME_KEY, name, is_secret=False)
    return name


def _instance_id(conn) -> str:
    existing = _text(_setting_value(conn, INSTANCE_ID_KEY, ""))
    if existing:
        return existing
    instance_id = f"dv_{secrets.token_urlsafe(24)}"
    _set_setting(conn, INSTANCE_ID_KEY, instance_id, is_secret=False)
    return instance_id


def _instance_key_pair(conn) -> tuple[str, str, str]:
    stored_private = _text(_setting_value(conn, INSTANCE_PRIVATE_KEY_KEY, "", include_secret=True))
    private_key_pem = _decrypt_secret_value(stored_private) if stored_private else ""
    public_key = _text(_setting_value(conn, INSTANCE_PUBLIC_KEY_KEY, ""))
    public_key_id = _text(_setting_value(conn, INSTANCE_PUBLIC_KEY_ID_KEY, ""))
    if private_key_pem and public_key and public_key_id:
        if stored_private and not _is_encrypted_secret(stored_private):
            # One-time migration of a legacy plaintext key to encrypted-at-rest.
            _set_setting(conn, INSTANCE_PRIVATE_KEY_KEY, _encrypt_secret_value(private_key_pem), is_secret=True)
        return private_key_pem, public_key, public_key_id
    if (public_key or public_key_id) and not private_key_pem:
        raise MovieVaultConnectionError("MovieVault instance key is missing; reset the local MovieVault connection.")

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.generate()
    public_key_obj = private_key.public_key()
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_raw = public_key_obj.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    public_key = _b64url(public_raw)
    public_key_id = f"dvpk_{_b64url(hashlib.sha256(public_key.encode('ascii')).digest()[:16])}"
    _set_setting(conn, INSTANCE_PRIVATE_KEY_KEY, _encrypt_secret_value(private_key_pem), is_secret=True)
    _set_setting(conn, INSTANCE_PUBLIC_KEY_KEY, public_key, is_secret=False)
    _set_setting(conn, INSTANCE_PUBLIC_KEY_ID_KEY, public_key_id, is_secret=False)
    return private_key_pem, public_key, public_key_id


def _sign_request_body(conn, raw_body: Any) -> dict[str, str]:
    """Sign the exact contribution body bytes with the bootstrap-registered Ed25519 key.

    Returns the signing headers ({keyId, timestamp, nonce, signature}) or {} when no
    instance key is registered yet. The signed input is
    ``timestamp.encode() + b"." + nonce.encode() + b"." + raw_body`` so MovieVault can
    verify against the identical bytes DiscVault transmits. Reuses the same private key
    and key_id registered at bootstrap (the recovery-handshake signer key).
    """
    private_key_pem = _decrypt_secret_value(_setting_value(conn, INSTANCE_PRIVATE_KEY_KEY, "", include_secret=True))
    public_key_id = _text(_setting_value(conn, INSTANCE_PUBLIC_KEY_ID_KEY, ""))
    if not private_key_pem or not public_key_id:
        return {}
    body_bytes = raw_body.encode("utf-8") if isinstance(raw_body, str) else bytes(raw_body or b"")
    timestamp = _timestamp()
    nonce = secrets.token_urlsafe(32)
    signature_input = f"{timestamp}.{nonce}.".encode("utf-8") + body_bytes
    from cryptography.hazmat.primitives import serialization

    private_key = serialization.load_pem_private_key(private_key_pem.encode("ascii"), password=None)
    signature = _b64url(private_key.sign(signature_input))
    return {
        "keyId": public_key_id,
        "timestamp": timestamp,
        "nonce": nonce,
        "signature": f"key-v1={signature}",
    }


def _connection_payload(conn) -> dict[str, Any]:
    return {
        "instanceId": _instance_id(conn),
        "instanceName": _instance_name(conn),
        "instanceVersion": _software_version(),
        "software": software_identity(),
        "requestedScopes": list(REQUESTED_SCOPES),
    }


class _HttpJsonResponse:
    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, Any]:
        if not self._body:
            return {}
        parsed = json.loads(self._body.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}


def _post_json(url: str, raw_body: str, headers: dict[str, str]) -> _HttpJsonResponse:
    body = raw_body.encode("utf-8")
    request = url_request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with url_request.urlopen(request, timeout=10) as response:
            return _HttpJsonResponse(int(response.status), response.read())
    except url_error.HTTPError as exc:
        return _HttpJsonResponse(int(exc.code), exc.read())
    except (OSError, TimeoutError, url_error.URLError) as exc:
        raise MovieVaultConnectionError(str(exc)) from exc


def _response_error(response: _HttpJsonResponse) -> tuple[str, str]:
    try:
        data = response.json()
    except Exception:
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    error = data.get("error")
    if isinstance(error, dict):
        return _text(error.get("code")), _text(error.get("message"))
    return _text(data.get("code") or data.get("error")), _text(data.get("message"))


def _response_error_code(response: _HttpJsonResponse) -> str:
    code, _message = _response_error(response)
    return code


def _client_version_error_fields(response: _HttpJsonResponse) -> tuple[str, str]:
    try:
        data = response.json()
    except Exception:
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    error = data.get("error") if isinstance(data.get("error"), dict) else data
    min_version = _text(error.get("minVersion") or error.get("min_version") or data.get("minVersion"))
    detected = _text(error.get("detectedVersion") or error.get("detected_version") or data.get("detectedVersion"))
    return min_version, detected


def _is_client_version_unsupported(response: _HttpJsonResponse) -> bool:
    if int(getattr(response, "status_code", 0) or 0) == 426:
        return True
    return _response_error_code(response) == "client_version_unsupported"


def _raise_client_version_unsupported(conn, response: _HttpJsonResponse) -> None:
    min_version, detected = _client_version_error_fields(response)
    if min_version:
        message = (
            f"This MovieVault server requires DiscVault version {min_version} or newer. "
            "Please update DiscVault to keep syncing."
        )
    else:
        message = (
            "This MovieVault server requires a newer DiscVault version. "
            "Please update DiscVault to keep syncing."
        )
    if detected:
        message += f" (current version: {detected})"
    _set_setting(conn, LINK_STATUS_KEY, "error")
    raise MovieVaultClientVersionUnsupported(message, min_version=min_version, detected_version=detected)


def _plugin_connection_action(plugin_id: str | None, phase: str, response: _HttpJsonResponse) -> str:
    plugin = plugin_id if is_movievault_plugin(plugin_id) else MOVIEVAULT_PLUGIN_ID
    try:
        try:
            from .next_plugin_runtime import run_plugin_entrypoint
        except ImportError:  # pragma: no cover - supports running modules directly
            from next_plugin_runtime import run_plugin_entrypoint

        execution = run_plugin_entrypoint(
            plugin,
            "connection_recovery_action",
            {
                "phase": phase,
                "statusCode": response.status_code,
                "response": response.json(),
            },
            {},
        )
        result = execution.get("result") if isinstance(execution, dict) and execution.get("status") == "ok" else {}
        if isinstance(result, dict):
            return _text(result.get("action"))
        return _text(result)
    except Exception:
        return ""


def _plugin_connection_request(
    plugin_id: str | None,
    phase: str,
    body: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], dict[str, str]]:
    default_path = BOOTSTRAP_PATH if phase == "bootstrap" else HANDSHAKE_PATH
    default_headers = {"Accept": "application/json", "Content-Type": "application/json"}
    plugin = plugin_id if is_movievault_plugin(plugin_id) else MOVIEVAULT_PLUGIN_ID
    try:
        try:
            from .next_plugin_runtime import run_plugin_entrypoint
        except ImportError:  # pragma: no cover - supports running modules directly
            from next_plugin_runtime import run_plugin_entrypoint

        execution = run_plugin_entrypoint(
            plugin,
            "connection_request",
            {"phase": phase, "body": body, **(extra or {})},
            {},
        )
        result = execution.get("result") if isinstance(execution, dict) else {}
        if execution.get("status") != "ok" or not isinstance(result, dict) or result.get("status") != "ok":
            return default_path, body, default_headers
        request_body = result.get("body") if isinstance(result.get("body"), dict) else body
        request_headers = result.get("headers") if isinstance(result.get("headers"), dict) else default_headers
        return _text(result.get("path") or default_path), request_body, {str(k): str(v) for k, v in request_headers.items()}
    except Exception:
        return default_path, body, default_headers


def _store_token_response(
    conn,
    data: dict[str, Any],
    *,
    plugin_id: str | None = None,
    actor_id: Any = None,
    source: str = "handshake",
) -> dict[str, Any]:
    client = data.get("client") if isinstance(data, dict) else None
    if not isinstance(client, dict) and isinstance(data, dict) and data.get("apiToken"):
        client = data
    instance = data.get("instance") if isinstance(data, dict) else None
    if not isinstance(instance, dict):
        instance = data if isinstance(data, dict) else None
    if not isinstance(client, dict) or not client.get("apiToken"):
        _set_setting(conn, LINK_STATUS_KEY, "error")
        raise MovieVaultConnectionError("MovieVault response did not include an API token")

    token = _text(client.get("apiToken"))
    token_prefix = _text(client.get("tokenPrefix") or token[:12])
    scopes = [str(scope) for scope in (client.get("scopes") or [])]
    timestamp = _timestamp()
    last_handshake_at = _text((instance or {}).get("lastHandshakeAt") or timestamp)
    key_id = _text((instance or {}).get("keyId") or client.get("keyId"))
    if key_id:
        _set_setting(conn, INSTANCE_PUBLIC_KEY_ID_KEY, key_id)
    _store_plugin_token(conn, token, plugin_id=plugin_id, actor_id=actor_id)
    _set_setting(conn, TOKEN_PREFIX_KEY, token_prefix)
    _set_setting(conn, TOKEN_SCOPES_KEY, scopes)
    if source == "bootstrap":
        _set_setting(conn, LAST_BOOTSTRAP_AT_KEY, timestamp)
    else:
        _set_setting(conn, LAST_HANDSHAKE_AT_KEY, last_handshake_at)
    _set_setting(conn, LINK_STATUS_KEY, "active")
    return data


def _mark_revoked(conn) -> None:
    _set_setting(conn, LINK_STATUS_KEY, "revoked")
    _delete_token(conn)


def _bootstrap(
    conn,
    *,
    plugin_id: str | None = None,
    actor_id: Any = None,
    allow_recovery_fallback: bool = True,
) -> dict[str, Any]:
    _private_key, public_key, _public_key_id = _instance_key_pair(conn)
    path, request_body, headers = _plugin_connection_request(
        plugin_id,
        "bootstrap",
        _connection_payload(conn),
        {"publicKey": public_key},
    )
    try:
        response = _post_json(
            f"{_ingest_url(conn)}{path}",
            _raw_json_body(request_body),
            headers,
        )
    except MovieVaultConnectionError as exc:
        _set_setting(conn, LINK_STATUS_KEY, "error")
        raise
    code = _response_error_code(response)
    action = _plugin_connection_action(plugin_id, "bootstrap", response)
    if response.status_code == 403 and code == "instance_revoked":
        _mark_revoked(conn)
        raise MovieVaultInstanceRevoked("MovieVault instance is revoked")
    if _is_client_version_unsupported(response):
        _raise_client_version_unsupported(conn, response)
    if allow_recovery_fallback and (
        (response.status_code == 409 and code == "instance_already_registered")
        or action == "recover"
    ):
        return _recover(conn, plugin_id=plugin_id, actor_id=actor_id, allow_bootstrap_fallback=False)
    if response.status_code >= 400:
        _set_setting(conn, LINK_STATUS_KEY, "error")
        raise MovieVaultConnectionError(f"MovieVault bootstrap failed: {code or response.status_code}")
    return _store_token_response(conn, response.json(), plugin_id=plugin_id, actor_id=actor_id, source="bootstrap")


def _recover(
    conn,
    *,
    plugin_id: str | None = None,
    actor_id: Any = None,
    allow_bootstrap_fallback: bool = True,
) -> dict[str, Any]:
    from cryptography.hazmat.primitives import serialization

    private_key_pem, _public_key, public_key_id = _instance_key_pair(conn)
    payload = _connection_payload(conn)
    raw_body = _raw_json_body(payload)
    timestamp = _timestamp()
    nonce = secrets.token_urlsafe(32)
    signature_input = f"{timestamp}.{nonce}.{raw_body}".encode("utf-8")
    private_key = serialization.load_pem_private_key(private_key_pem.encode("ascii"), password=None)
    signature = _b64url(private_key.sign(signature_input))
    path, request_body, headers = _plugin_connection_request(
        plugin_id,
        "recovery",
        payload,
        {
            "timestamp": timestamp,
            "nonce": nonce,
            "publicKeyId": public_key_id,
            "signature": f"key-v1={signature}",
        },
    )
    try:
        response = _post_json(
            f"{_ingest_url(conn)}{path}",
            _raw_json_body(request_body),
            headers,
        )
    except MovieVaultConnectionError as exc:
        _set_setting(conn, LINK_STATUS_KEY, "error")
        raise
    code = _response_error_code(response)
    action = _plugin_connection_action(plugin_id, "recovery", response)
    if response.status_code == 403 and code == "instance_revoked":
        _mark_revoked(conn)
        raise MovieVaultInstanceRevoked("MovieVault instance is revoked")
    if _is_client_version_unsupported(response):
        _raise_client_version_unsupported(conn, response)
    if allow_bootstrap_fallback and action == "bootstrap":
        _delete_token(conn)
        _set_setting(conn, LINK_STATUS_KEY, "connecting")
        return _bootstrap(conn, plugin_id=plugin_id, actor_id=actor_id, allow_recovery_fallback=False)
    if response.status_code >= 400:
        _set_setting(conn, LINK_STATUS_KEY, "error")
        raise MovieVaultConnectionError(f"MovieVault signed recovery failed: {code or response.status_code}")
    return _store_token_response(conn, response.json(), plugin_id=plugin_id, actor_id=actor_id, source="handshake")


def _hmac_handshake(conn, *, plugin_id: str | None = None, actor_id: Any = None) -> dict[str, Any]:
    secret = _handshake_secret(conn)
    if not secret:
        raise MovieVaultConnectionError("MovieVault HMAC handshake secret is not configured")
    payload = _connection_payload(conn)
    raw_body = _raw_json_body(payload)
    timestamp = _timestamp()
    nonce = secrets.token_urlsafe(32)
    signature_input = f"{timestamp}.{nonce}.{raw_body}".encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), signature_input, hashlib.sha256).hexdigest()
    try:
        response = _post_json(
            f"{_ingest_url(conn)}{HANDSHAKE_PATH}",
            raw_body,
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-DiscVault-Timestamp": timestamp,
                "X-DiscVault-Nonce": nonce,
                "X-DiscVault-Signature": f"sha256={signature}",
            },
        )
    except MovieVaultConnectionError as exc:
        _set_setting(conn, LINK_STATUS_KEY, "error")
        raise
    code = _response_error_code(response)
    if response.status_code == 403 and code == "instance_revoked":
        _mark_revoked(conn)
        raise MovieVaultInstanceRevoked("MovieVault instance is revoked")
    if _is_client_version_unsupported(response):
        _raise_client_version_unsupported(conn, response)
    if response.status_code >= 400:
        _set_setting(conn, LINK_STATUS_KEY, "error")
        raise MovieVaultConnectionError(f"MovieVault HMAC handshake failed: {code or response.status_code}")
    return _store_token_response(conn, response.json(), plugin_id=plugin_id, actor_id=actor_id, source="handshake")


def reset_movievault_connection(conn) -> None:
    for key in (
        INSTANCE_ID_KEY,
        INSTANCE_NAME_KEY,
        INSTANCE_PRIVATE_KEY_KEY,
        INSTANCE_PUBLIC_KEY_KEY,
        INSTANCE_PUBLIC_KEY_ID_KEY,
        TOKEN_PREFIX_KEY,
        TOKEN_SCOPES_KEY,
        LAST_BOOTSTRAP_AT_KEY,
        LAST_HANDSHAKE_AT_KEY,
        LINK_STATUS_KEY,
        AUTH_METHOD_KEY,
        V3_API_TOKEN_KEY,
        V3_KEY_ID_KEY,
        V3_INSTANCE_ID_KEY,
        V3_TOKEN_PREFIX_KEY,
        V3_SCOPES_KEY,
        V3_LAST_BOOTSTRAP_AT_KEY,
    ):
        _delete_setting(conn, key)
    _delete_token(conn)


def refresh_movievault_connection(
    conn,
    *,
    plugin_id: str | None = None,
    actor_id: Any = None,
    reset: bool = False,
) -> dict[str, Any]:
    if not movievault_enabled(conn):
        raise MovieVaultConnectionError("MovieVault integration is disabled")
    if reset:
        reset_movievault_connection(conn)
    _set_setting(conn, LINK_STATUS_KEY, "connecting")
    if _handshake_secret(conn):
        _set_setting(conn, AUTH_METHOD_KEY, "hmac_handshake")
        return _hmac_handshake(conn, plugin_id=plugin_id, actor_id=actor_id)
    _set_setting(conn, AUTH_METHOD_KEY, "bootstrap_signed")
    if _text(_setting_value(conn, INSTANCE_PUBLIC_KEY_ID_KEY, "")):
        return _recover(conn, plugin_id=plugin_id, actor_id=actor_id)
    return _bootstrap(conn, plugin_id=plugin_id, actor_id=actor_id)


def ensure_movievault_token(conn, *, plugin_id: str | None = None, actor_id: Any = None) -> str:
    if not movievault_enabled(conn) or _text(_setting_value(conn, LINK_STATUS_KEY, "")) == "revoked":
        return ""
    token = stored_movievault_token(conn, plugin_id)
    if token:
        return token
    refresh_movievault_connection(conn, plugin_id=plugin_id, actor_id=actor_id)
    return stored_movievault_token(conn, plugin_id)


def _scopes(conn) -> list[str]:
    raw = _setting_value(conn, TOKEN_SCOPES_KEY, [])
    if isinstance(raw, list):
        return [str(item) for item in raw]
    try:
        parsed = json.loads(str(raw or "[]"))
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    except Exception:
        return []


def movievault_connection_status(conn, plugin_id: str | None = None) -> dict[str, Any]:
    token = stored_movievault_token(conn, plugin_id)
    stored_private_key = _setting_value(conn, INSTANCE_PRIVATE_KEY_KEY, "", include_secret=True)
    private_key_set = bool(stored_private_key)
    private_key_usable = False
    if private_key_set:
        try:
            private_key_usable = bool(_decrypt_secret_value(stored_private_key))
        except MovieVaultConnectionError:
            pass
    public_key_id = _text(_setting_value(conn, INSTANCE_PUBLIC_KEY_ID_KEY, ""))
    public_key = _text(_setting_value(conn, INSTANCE_PUBLIC_KEY_KEY, ""))
    sharing_mode = _text(_setting_value(conn, SHARING_MODE_KEY, "") or os.environ.get("MOVIEVAULT_SHARING_MODE") or "opt_in")
    link_status = _text(_setting_value(conn, LINK_STATUS_KEY, "unlinked"), "unlinked")
    # When MovieVault is active (enabled, linked and authenticated) contributing is
    # allowed by default. An owner can still explicitly opt out via the stored setting.
    movievault_active = movievault_enabled(conn) and link_status == "active" and bool(token)
    return {
        "authMethod": _text(_setting_value(conn, AUTH_METHOD_KEY, "")) or ("hmac_handshake" if _handshake_secret(conn) else "bootstrap_signed"),
        "contributionEnabled": _bool_setting(conn, CONTRIBUTION_ENABLED_KEY, movievault_active),
        "contributionUrl": _contribution_url(conn),
        "enabled": movievault_enabled(conn),
        "ingestUrl": _ingest_url(conn),
        "instanceId": _text(_setting_value(conn, INSTANCE_ID_KEY, "")),
        "instanceName": _text(_setting_value(conn, INSTANCE_NAME_KEY, "")),
        "keyId": public_key_id,
        "lastBootstrapAt": _text(_setting_value(conn, LAST_BOOTSTRAP_AT_KEY, "")),
        "lastHandshakeAt": _text(_setting_value(conn, LAST_HANDSHAKE_AT_KEY, "")),
        "linkStatus": link_status,
        "privateKeySet": private_key_set,
        "requiresReset": bool((stored_private_key or public_key or public_key_id) and not private_key_usable),
        "scopes": _scopes(conn),
        "searchUrl": _search_url(conn),
        "sharingMode": sharing_mode,
        "sourceVersion": _software_version(),
        "tokenPrefix": _text(_setting_value(conn, TOKEN_PREFIX_KEY, "")),
        "tokenSet": bool(token),
    }


def movievault_plugin_context(
    conn,
    plugin_id: str | None,
    context: dict[str, Any],
    *,
    ensure_token: bool = False,
    actor_id: Any = None,
) -> dict[str, Any]:
    plugin = str(plugin_id or "")
    if not is_movievault_plugin(plugin):
        return context
    safe_context = dict(context or {})
    settings = dict(safe_context.get("settings") or {})
    secrets_payload = dict(safe_context.get("secrets") or {})
    status = movievault_connection_status(conn, plugin)
    token = stored_movievault_token(conn, plugin)
    connection_error = ""
    if ensure_token:
        try:
            token = ensure_movievault_token(conn, plugin_id=plugin, actor_id=actor_id)
        except MovieVaultConnectionError as exc:
            connection_error = str(exc)
        status = movievault_connection_status(conn, plugin)
    if token:
        secrets_payload["token"] = token

    def recover_token_once() -> str:
        refresh_movievault_connection(conn, plugin_id=plugin, actor_id=actor_id)
        return stored_movievault_token(conn, plugin)

    def mark_revoked() -> None:
        _mark_revoked(conn)

    def sign_request(raw_body: Any) -> dict[str, str]:
        return _sign_request_body(conn, raw_body)

    def signed_request_v3_json(method: str, path: str, body_obj: Any = None) -> dict[str, Any]:
        response = signed_request_v3(conn, method, path, body_obj=body_obj, actor_id=actor_id)
        return {"status": response.status_code, "data": response.json()}

    safe_context.update(
        {
            "settings": settings,
            "secrets": secrets_payload,
            "secretNames": sorted({*(safe_context.get("secretNames") or []), *(["token"] if token else [])}),
            "secretsConfigured": bool(token) or bool(safe_context.get("secretsConfigured")),
            "movievault": {
                **{key: value for key, value in status.items() if key not in {"ingestUrl"}},
                **({"error": connection_error} if connection_error else {}),
            },
            "movievaultMarkRevoked": mark_revoked,
            "movievaultRecoverToken": recover_token_once,
            "movievaultSignRequest": sign_request,
            "movievaultSignedRequestV3": signed_request_v3_json,
        }
    )
    return safe_context


# ---------------------------------------------------------------------------
# MovieVault /api/v3 signed client
# ---------------------------------------------------------------------------


def _v3_base_url(conn) -> str:
    """Base host for the signed /api/v3 metadata API (reuses the search host)."""
    return _search_url(conn)


def _v3_instance_id(conn) -> str:
    existing = _text(_setting_value(conn, V3_INSTANCE_ID_KEY, ""))
    if existing.startswith("web_"):
        return existing
    instance_id = "web_" + str(uuid.uuid4())
    _set_setting(conn, V3_INSTANCE_ID_KEY, instance_id, is_secret=False)
    return instance_id


def _v3_key_id(conn) -> str:
    return _text(_setting_value(conn, V3_KEY_ID_KEY, ""))


def _v3_token(conn) -> str:
    return _decrypt_secret_value(_setting_value(conn, V3_API_TOKEN_KEY, "", include_secret=True))


def _http_request(method: str, url: str, headers: dict[str, str], data: bytes | None = None) -> _HttpJsonResponse:
    request = url_request.Request(url, data=data, headers=headers, method=str(method).upper())
    try:
        with url_request.urlopen(request, timeout=10) as response:
            return _HttpJsonResponse(int(response.status), response.read())
    except url_error.HTTPError as exc:
        return _HttpJsonResponse(int(exc.code), exc.read())
    except (OSError, TimeoutError, url_error.URLError) as exc:
        raise MovieVaultConnectionError(str(exc)) from exc


_V3_LINKED_INSTANCE_RE = re.compile(r"web_[0-9a-fA-F][0-9a-fA-F-]{6,}")


def _linked_instance_from_message(message: str) -> str:
    """Extract the instanceId MovieVault reports a signing key is already linked to.

    MovieVault's bootstrap rejection reads: "This signing key is already linked to
    web_<uuid>; reset that link or reuse its instanceId". We adopt that instanceId so
    a drifted local instanceId self-heals instead of failing bootstrap forever.
    """
    match = _V3_LINKED_INSTANCE_RE.search(message or "")
    return match.group(0) if match else ""


def bootstrap_v3(conn, *, actor_id: Any = None, _allow_relink_retry: bool = True) -> dict[str, Any]:
    """Register this instance with MovieVault's generalized ``/api/v3/clients/bootstrap``.

    Bootstrap is unsigned. Reuses the shared Ed25519 keypair, always adopts the
    ``keyId`` returned by the server, and persists the v3 bearer token / key id /
    instance id (all reused across re-bootstraps).
    """
    _private_key, public_key, _legacy_key_id = _instance_key_pair(conn)
    instance_id = _v3_instance_id(conn)
    body = {
        "instanceId": instance_id,
        "publicKey": public_key,
        "platform": "web",
        "keyAlgorithm": "ed25519",
        "appName": "DiscVault",
        "appVersion": _software_version(),
        "app": {"name": "DiscVault", "platform": "web"},
    }
    response = _http_request(
        "POST",
        f"{_v3_base_url(conn)}{V3_BOOTSTRAP_PATH}",
        {"Accept": "application/json", "Content-Type": "application/json"},
        json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )
    code, message = _response_error(response)
    if response.status_code == 403 and code in {"instance_blocked", "instance_revoked"}:
        _mark_revoked(conn)
        raise MovieVaultInstanceRevoked("MovieVault instance is blocked")
    if _is_client_version_unsupported(response):
        _raise_client_version_unsupported(conn, response)
    if response.status_code == 400 and code == "validation_error" and _allow_relink_retry:
        linked_instance = _linked_instance_from_message(message)
        if linked_instance and linked_instance != instance_id:
            # The signing key is already linked to a different instanceId on
            # MovieVault (our local instanceId drifted from the registered one).
            # Adopt the linked id and retry bootstrap once so the transport
            # self-heals instead of failing on every token refresh.
            _set_setting(conn, V3_INSTANCE_ID_KEY, linked_instance, is_secret=False)
            return bootstrap_v3(conn, actor_id=actor_id, _allow_relink_retry=False)
    if response.status_code >= 400:
        _set_setting(conn, LINK_STATUS_KEY, "error")
        raise MovieVaultConnectionError(f"MovieVault v3 bootstrap failed: {code or response.status_code}")
    data = response.json()
    token = _text(data.get("apiToken"))
    key_id = _text(data.get("keyId"))
    if not token or not key_id:
        _set_setting(conn, LINK_STATUS_KEY, "error")
        raise MovieVaultConnectionError("MovieVault v3 bootstrap response did not include apiToken/keyId")
    resolved_instance = _text(data.get("instanceId")) or instance_id
    scopes = [str(scope) for scope in (data.get("scopes") or [])]
    _set_setting(conn, V3_API_TOKEN_KEY, _encrypt_secret_value(token), is_secret=True, actor_id=actor_id)
    _set_setting(conn, V3_KEY_ID_KEY, key_id, is_secret=False)
    _set_setting(conn, V3_INSTANCE_ID_KEY, resolved_instance, is_secret=False)
    _set_setting(conn, V3_TOKEN_PREFIX_KEY, _text(data.get("tokenPrefix") or token[:12]), is_secret=False)
    _set_setting(conn, V3_SCOPES_KEY, scopes)
    _set_setting(conn, V3_LAST_BOOTSTRAP_AT_KEY, _timestamp())
    _set_setting(conn, INSTANCE_PUBLIC_KEY_ID_KEY, key_id, is_secret=False)
    _set_setting(conn, LINK_STATUS_KEY, "active")
    return {"apiToken": token, "keyId": key_id, "instanceId": resolved_instance, "scopes": scopes}


def _v3_signature_headers(conn, body_bytes: bytes) -> dict[str, str]:
    """Build the four ``X-MV-*`` signature headers for a v3 request.

    Signs ``utf8(timestamp) + '.' + utf8(nonce) + '.' + body_bytes`` with the
    instance Ed25519 key, matching the exact bytes transmitted (empty for GET).
    """
    private_key_pem = _decrypt_secret_value(_setting_value(conn, INSTANCE_PRIVATE_KEY_KEY, "", include_secret=True))
    key_id = _v3_key_id(conn)
    if not private_key_pem or not key_id:
        return {}
    timestamp = _timestamp()
    nonce = str(uuid.uuid4())
    message = f"{timestamp}.{nonce}.".encode("utf-8") + (body_bytes or b"")
    from cryptography.hazmat.primitives import serialization

    private_key = serialization.load_pem_private_key(private_key_pem.encode("ascii"), password=None)
    signature = _b64url(private_key.sign(message))
    return {
        "X-MV-Key-Id": key_id,
        "X-MV-Timestamp": timestamp,
        "X-MV-Nonce": nonce,
        "X-MV-Signature": f"key-v1={signature}",
    }


# Error codes that indicate the bearer token / key link is stale and a single
# automatic re-bootstrap + retry is warranted.
_V3_REBOOTSTRAP_CODES = frozenset(
    {"", "unauthorized", "token_expired", "token_revoked", "signature_required", "signature_invalid", "key_not_found"}
)


def signed_request_v3(
    conn,
    method: str,
    path: str,
    *,
    body_obj: Any = None,
    actor_id: Any = None,
    _allow_rebootstrap: bool = True,
) -> _HttpJsonResponse:
    """Perform a signed MovieVault ``/api/v3`` request.

    Every call carries ``Authorization: Bearer`` plus the four ``X-MV-*`` headers.
    The JSON body is serialized exactly once and both signed and transmitted, so
    the server verifies the identical bytes. A single automatic re-bootstrap +
    retry is attempted on a stale-auth 401 (guarded against loops); a 403
    ``instance_blocked`` is never retried.
    """
    if not movievault_enabled(conn):
        raise MovieVaultConnectionError("MovieVault integration is disabled")
    if _text(_setting_value(conn, LINK_STATUS_KEY, "")) == "revoked":
        raise MovieVaultInstanceRevoked("MovieVault instance is blocked")

    if not _v3_token(conn) or not _v3_key_id(conn):
        bootstrap_v3(conn, actor_id=actor_id)

    body_bytes = (
        b""
        if body_obj is None
        else json.dumps(body_obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    headers = {"Accept": "application/json", "Authorization": f"Bearer {_v3_token(conn)}"}
    if body_obj is not None:
        headers["Content-Type"] = "application/json"
    headers.update(_v3_signature_headers(conn, body_bytes))

    response = _http_request(
        method,
        f"{_v3_base_url(conn)}{path}",
        headers,
        body_bytes if body_obj is not None else None,
    )
    code = _response_error_code(response)
    if response.status_code == 401 and _allow_rebootstrap and code in _V3_REBOOTSTRAP_CODES:
        bootstrap_v3(conn, actor_id=actor_id)
        return signed_request_v3(
            conn,
            method,
            path,
            body_obj=body_obj,
            actor_id=actor_id,
            _allow_rebootstrap=False,
        )
    if response.status_code == 403 and code in {"instance_blocked", "instance_revoked"}:
        _mark_revoked(conn)
        raise MovieVaultInstanceRevoked("MovieVault instance is blocked")
    if _is_client_version_unsupported(response):
        _raise_client_version_unsupported(conn, response)
    return response


def movievault_v3_health(conn) -> bool:
    """Unauthenticated connectivity probe against ``GET /api/v1/health``."""
    try:
        response = _http_request("GET", f"{_v3_base_url(conn)}{HEALTH_PATH}", {"Accept": "application/json"})
    except MovieVaultConnectionError:
        return False
    return 200 <= response.status_code < 300
