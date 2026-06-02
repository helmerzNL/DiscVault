"""Zero-config MovieVault connection management for DiscVault Next."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
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
except ImportError:  # pragma: no cover - supports running modules directly
    from versioning import backend_version, software_identity


MOVIEVAULT_PLUGIN_ID = "movievault"
MOVIEVAULT_NEXT_PLUGIN_ID = "movievault_26"
MOVIEVAULT_PLUGIN_IDS = frozenset({MOVIEVAULT_PLUGIN_ID, MOVIEVAULT_NEXT_PLUGIN_ID})
REQUESTED_SCOPES = ("search:read", "contributions:write", "contributions:read")
BOOTSTRAP_PATH = "/api/v1/internal/discvault/bootstrap"
HANDSHAKE_PATH = "/api/v1/internal/discvault/handshake"
DEFAULT_MOVIEVAULT_URL = "https://movies.vaultstack.eu"
DEFAULT_SEARCH_URL = DEFAULT_MOVIEVAULT_URL
DEFAULT_INGEST_URL = DEFAULT_MOVIEVAULT_URL

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
    private_key_pem = _text(_setting_value(conn, INSTANCE_PRIVATE_KEY_KEY, "", include_secret=True))
    public_key = _text(_setting_value(conn, INSTANCE_PUBLIC_KEY_KEY, ""))
    public_key_id = _text(_setting_value(conn, INSTANCE_PUBLIC_KEY_ID_KEY, ""))
    if private_key_pem and public_key and public_key_id:
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
    _set_setting(conn, INSTANCE_PRIVATE_KEY_KEY, private_key_pem, is_secret=True)
    _set_setting(conn, INSTANCE_PUBLIC_KEY_KEY, public_key, is_secret=False)
    _set_setting(conn, INSTANCE_PUBLIC_KEY_ID_KEY, public_key_id, is_secret=False)
    return private_key_pem, public_key, public_key_id


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


def _plugin_connection_action(plugin_id: str | None, phase: str, response: _HttpJsonResponse) -> str:
    plugin = plugin_id if is_movievault_plugin(plugin_id) else MOVIEVAULT_PLUGIN_ID
    try:
        try:
            from .next_plugin_runtime import discovered_plugin, load_runtime_module
        except ImportError:  # pragma: no cover - supports running modules directly
            from next_plugin_runtime import discovered_plugin, load_runtime_module

        discovery, _snapshot = discovered_plugin(plugin)
        if not discovery:
            return ""
        module = load_runtime_module(discovery)
        handler = getattr(module, "connection_recovery_action", None) if module else None
        if not callable(handler):
            return ""
        result = handler(
            {
                "phase": phase,
                "statusCode": response.status_code,
                "response": response.json(),
            },
            {},
        )
        if isinstance(result, dict):
            return _text(result.get("action"))
        return _text(result)
    except Exception:
        return ""


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
    payload = {**_connection_payload(conn), "publicKey": public_key}
    try:
        response = _post_json(
            f"{_ingest_url(conn)}{BOOTSTRAP_PATH}",
            _raw_json_body(payload),
            {"Accept": "application/json", "Content-Type": "application/json"},
        )
    except MovieVaultConnectionError as exc:
        _set_setting(conn, LINK_STATUS_KEY, "error")
        raise
    code = _response_error_code(response)
    action = _plugin_connection_action(plugin_id, "bootstrap", response)
    if response.status_code == 403 and code == "instance_revoked":
        _mark_revoked(conn)
        raise MovieVaultInstanceRevoked("MovieVault instance is revoked")
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
    try:
        response = _post_json(
            f"{_ingest_url(conn)}{HANDSHAKE_PATH}",
            raw_body,
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-DiscVault-Timestamp": timestamp,
                "X-DiscVault-Nonce": nonce,
                "X-DiscVault-Key-Id": public_key_id,
                "X-DiscVault-Signature": f"key-v1={signature}",
            },
        )
    except MovieVaultConnectionError as exc:
        _set_setting(conn, LINK_STATUS_KEY, "error")
        raise
    code = _response_error_code(response)
    action = _plugin_connection_action(plugin_id, "recovery", response)
    if response.status_code == 403 and code == "instance_revoked":
        _mark_revoked(conn)
        raise MovieVaultInstanceRevoked("MovieVault instance is revoked")
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
    private_key_set = bool(_setting_value(conn, INSTANCE_PRIVATE_KEY_KEY, "", include_secret=True))
    public_key_id = _text(_setting_value(conn, INSTANCE_PUBLIC_KEY_ID_KEY, ""))
    public_key = _text(_setting_value(conn, INSTANCE_PUBLIC_KEY_KEY, ""))
    sharing_mode = _text(_setting_value(conn, SHARING_MODE_KEY, "") or os.environ.get("MOVIEVAULT_SHARING_MODE") or "opt_in")
    return {
        "authMethod": _text(_setting_value(conn, AUTH_METHOD_KEY, "")) or ("hmac_handshake" if _handshake_secret(conn) else "bootstrap_signed"),
        "contributionEnabled": _bool_setting(conn, CONTRIBUTION_ENABLED_KEY, False),
        "contributionUrl": _contribution_url(conn),
        "enabled": movievault_enabled(conn),
        "ingestUrl": _ingest_url(conn),
        "instanceId": _text(_setting_value(conn, INSTANCE_ID_KEY, "")),
        "instanceName": _text(_setting_value(conn, INSTANCE_NAME_KEY, "")),
        "keyId": public_key_id,
        "lastBootstrapAt": _text(_setting_value(conn, LAST_BOOTSTRAP_AT_KEY, "")),
        "lastHandshakeAt": _text(_setting_value(conn, LAST_HANDSHAKE_AT_KEY, "")),
        "linkStatus": _text(_setting_value(conn, LINK_STATUS_KEY, "unlinked"), "unlinked"),
        "privateKeySet": private_key_set,
        "requiresReset": bool((public_key or public_key_id) and not private_key_set),
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
        }
    )
    return safe_context
