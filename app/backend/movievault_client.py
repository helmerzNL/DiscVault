import hashlib
import hmac
import json
import os
import base64
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

try:
    from .config import (
        DB_PATH,
        JWT_SECRET,
        MOVIEVAULT_API_KEY,
        MOVIEVAULT_API_TOKEN,
        MOVIEVAULT_DISCVAULT_HANDSHAKE_SECRET,
        MOVIEVAULT_ENABLED_DEFAULT,
        MOVIEVAULT_INGEST_URL,
        RP_NAME,
    )
    from .logging_utils import add_log
except ImportError:  # pragma: no cover - supports running app.py directly
    from config import (
        DB_PATH,
        JWT_SECRET,
        MOVIEVAULT_API_KEY,
        MOVIEVAULT_API_TOKEN,
        MOVIEVAULT_DISCVAULT_HANDSHAKE_SECRET,
        MOVIEVAULT_ENABLED_DEFAULT,
        MOVIEVAULT_INGEST_URL,
        RP_NAME,
    )
    from logging_utils import add_log


REQUESTED_SCOPES = ("search:read", "contributions:write", "contributions:read")
HANDSHAKE_PATH = "/api/v1/internal/discvault/handshake"
BOOTSTRAP_PATH = "/api/v1/internal/discvault/bootstrap"
INSTANCE_ID_KEY = "movievault_instance_id"
INSTANCE_NAME_KEY = "movievault_instance_name"
API_TOKEN_KEY = "movievault_api_token"
API_TOKEN_ENC_KEY = "movievault_api_token_enc"
TOKEN_PREFIX_KEY = "movievault_token_prefix"
TOKEN_SCOPES_KEY = "movievault_token_scopes"
LAST_HANDSHAKE_AT_KEY = "movievault_last_handshake_at"
LINK_STATUS_KEY = "movievault_link_status"
AUTH_METHOD_KEY = "movievault_auth_method"
INSTANCE_PRIVATE_KEY_KEY = "movievault_instance_private_key"
INSTANCE_PRIVATE_KEY_ENC_KEY = "movievault_instance_private_key_enc"
INSTANCE_PUBLIC_KEY_KEY = "movievault_instance_public_key"
INSTANCE_PUBLIC_KEY_ID_KEY = "movievault_instance_public_key_id"
SOFTWARE_VERSION = os.environ.get("BUILD_VERSION", "dev")


class MovieVaultHandshakeError(RuntimeError):
    pass


class MovieVaultInstanceRevoked(MovieVaultHandshakeError):
    pass


def _setting_value(key: str, default: str = "") -> str:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row and row[0] is not None:
            return str(row[0])
    except Exception:
        pass
    return default


def _set_setting(key: str, value: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()


def _delete_setting(key: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM settings WHERE key=?", (key,))
        conn.commit()


def _bool_setting(key: str, default: bool) -> bool:
    value = _setting_value(key, "true" if default else "false")
    return str(value).strip().lower() == "true"


def is_movievault_integration_enabled() -> bool:
    return _bool_setting("movievault_enabled", MOVIEVAULT_ENABLED_DEFAULT)


def _log(level: str, message: str, detail: str = ""):
    try:
        add_log("movievault", message, _sanitize_log_detail(detail), level)
    except Exception:
        pass


def _url_to_path(match) -> str:
    return _path_only(match.group(0))


def _path_only(value: str) -> str:
    try:
        parsed = urlparse(str(value or ""))
        return parsed.path or "/"
    except Exception:
        return "/api"


def _sanitize_log_detail(detail: str) -> str:
    detail = str(detail or "")
    if not detail:
        return ""
    detail = re.sub(r"https?://[^\s;,\)\]\"']+", _url_to_path, detail)
    for token in (
        _stored_movievault_api_token(),
        os.environ.get("MOVIEVAULT_API_TOKEN", ""),
        os.environ.get("MOVIEVAULT_API_KEY", ""),
    ):
        token = str(token or "").strip()
        if token:
            detail = detail.replace(token, "[redacted-token]")
    return detail


def _delete_token_settings():
    for key in (API_TOKEN_KEY, API_TOKEN_ENC_KEY, TOKEN_PREFIX_KEY, TOKEN_SCOPES_KEY, LAST_HANDSHAKE_AT_KEY):
        _delete_setting(key)


def disconnect_movievault_connection(reason: str = "disabled"):
    _set_setting(LINK_STATUS_KEY, "disabled")
    _delete_token_settings()
    instance_id = _setting_value(INSTANCE_ID_KEY, "").strip() or "-"
    _log("info", "MovieVault connection disabled", f"instanceId={instance_id}; reason={reason}")


def enable_movievault_connection():
    if _setting_value(LINK_STATUS_KEY, "") == "disabled":
        _set_setting(LINK_STATUS_KEY, "unlinked")
        instance_id = _setting_value(INSTANCE_ID_KEY, "").strip() or "-"
        _log("info", "MovieVault connection enabled", f"instanceId={instance_id}")


def _ingest_url() -> str:
    value = _setting_value("movievault_ingest_url", str(MOVIEVAULT_INGEST_URL).strip())
    return value.strip().rstrip("/")


def _handshake_secret() -> str:
    return str(MOVIEVAULT_DISCVAULT_HANDSHAKE_SECRET).strip()


def _xor_crypt(data: bytes) -> bytes:
    stream = bytearray()
    counter = 0
    seed = JWT_SECRET.encode("utf-8")
    while len(stream) < len(data):
        stream.extend(hashlib.sha256(seed + counter.to_bytes(4, "big")).digest())
        counter += 1
    return bytes(value ^ stream[index] for index, value in enumerate(data))


def _encrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        from cryptography.fernet import Fernet

        key_bytes = hashlib.sha256(JWT_SECRET.encode()).digest()
        fernet_key = base64.urlsafe_b64encode(key_bytes)
        return "fernet:" + Fernet(fernet_key).encrypt(value.encode("utf-8")).decode("ascii")
    except Exception:
        encrypted = _xor_crypt(value.encode("utf-8"))
        return "xor-v1:" + base64.urlsafe_b64encode(encrypted).decode("ascii").rstrip("=")


def _decrypt_secret(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        if value.startswith("fernet:"):
            from cryptography.fernet import Fernet

            key_bytes = hashlib.sha256(JWT_SECRET.encode()).digest()
            fernet_key = base64.urlsafe_b64encode(key_bytes)
            return Fernet(fernet_key).decrypt(value[len("fernet:"):].encode("ascii")).decode("utf-8")
        if value.startswith("xor-v1:"):
            encoded = value[len("xor-v1:"):]
            padding = "=" * (-len(encoded) % 4)
            encrypted = base64.urlsafe_b64decode((encoded + padding).encode("ascii"))
            return _xor_crypt(encrypted).decode("utf-8")
    except Exception:
        return ""
    return value


def _stored_movievault_api_token() -> str:
    if not is_movievault_integration_enabled():
        return ""
    if _setting_value(LINK_STATUS_KEY, "") == "revoked":
        return ""
    encrypted = _setting_value(API_TOKEN_ENC_KEY, "").strip()
    if encrypted:
        decrypted = _decrypt_secret(encrypted)
        if decrypted:
            return decrypted
    stored = _setting_value(API_TOKEN_KEY, "").strip() or _setting_value("movievault_api_key", "").strip()
    if stored:
        return stored
    env_token = os.environ.get("MOVIEVAULT_API_TOKEN", os.environ.get("MOVIEVAULT_API_KEY", "")).strip()
    if env_token:
        return env_token
    return str(MOVIEVAULT_API_TOKEN).strip() or str(MOVIEVAULT_API_KEY).strip()


def get_movievault_api_token() -> str:
    return token_provider().get_token()


def mask_token(token: str | None) -> str:
    token = str(token or "").strip()
    if not token:
        return "-"
    prefix = _setting_value(TOKEN_PREFIX_KEY, "").strip()
    if prefix and token.startswith(prefix):
        return f"{prefix}..."
    return "configured"


def get_or_create_instance_id() -> str:
    existing = _setting_value(INSTANCE_ID_KEY, "").strip()
    if existing:
        return existing
    instance_id = f"dv_{secrets.token_urlsafe(24)}"
    _set_setting(INSTANCE_ID_KEY, instance_id)
    return instance_id


def get_instance_name() -> str:
    existing = _setting_value(INSTANCE_NAME_KEY, "").strip()
    if existing:
        return existing
    name = (str(RP_NAME).strip() or "DiscVault")[:120]
    _set_setting(INSTANCE_NAME_KEY, name)
    return name


def _b64url(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def get_or_create_instance_key_pair() -> tuple[str, str, str]:
    private_key_pem = _decrypt_secret(_setting_value(INSTANCE_PRIVATE_KEY_ENC_KEY, "").strip())
    if not private_key_pem:
        private_key_pem = _setting_value(INSTANCE_PRIVATE_KEY_KEY, "").strip()
    public_key = _setting_value(INSTANCE_PUBLIC_KEY_KEY, "").strip()
    public_key_id = _setting_value(INSTANCE_PUBLIC_KEY_ID_KEY, "").strip()
    if private_key_pem and public_key and public_key_id:
        if not _setting_value(INSTANCE_PRIVATE_KEY_ENC_KEY, "").strip():
            _set_setting(INSTANCE_PRIVATE_KEY_ENC_KEY, _encrypt_secret(private_key_pem))
            _delete_setting(INSTANCE_PRIVATE_KEY_KEY)
        return private_key_pem, public_key, public_key_id

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

    _set_setting(INSTANCE_PRIVATE_KEY_ENC_KEY, _encrypt_secret(private_key_pem))
    _delete_setting(INSTANCE_PRIVATE_KEY_KEY)
    _set_setting(INSTANCE_PUBLIC_KEY_KEY, public_key)
    _set_setting(INSTANCE_PUBLIC_KEY_ID_KEY, public_key_id)
    return private_key_pem, public_key, public_key_id


def _raw_json_body(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_handshake_request() -> tuple[str, dict, str]:
    secret = _handshake_secret()
    if not secret:
        raise MovieVaultHandshakeError("MovieVault handshake secret is not configured")
    payload = {
        "instanceId": get_or_create_instance_id(),
        "instanceName": get_instance_name(),
        "requestedScopes": list(REQUESTED_SCOPES),
    }
    raw_body = _raw_json_body(payload)
    timestamp = _timestamp()
    nonce = secrets.token_urlsafe(32)
    signature_input = f"{timestamp}.{nonce}.{raw_body}".encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), signature_input, hashlib.sha256).hexdigest()
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-DiscVault-Timestamp": timestamp,
        "X-DiscVault-Nonce": nonce,
        "X-DiscVault-Signature": f"sha256={signature}",
    }
    return raw_body, headers, nonce


def _response_error_code(response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict):
            return str(data.get("error") or data.get("code") or "")
    except Exception:
        pass
    return ""


def _store_token_response(data: dict, http_status: int) -> dict:
    client = data.get("client") if isinstance(data, dict) else None
    instance = data.get("instance") if isinstance(data, dict) else None
    if not isinstance(client, dict) or not client.get("apiToken"):
        _set_setting(LINK_STATUS_KEY, "error")
        raise MovieVaultHandshakeError("MovieVault response did not include an API token")

    api_token = str(client["apiToken"]).strip()
    token_prefix = str(client.get("tokenPrefix") or api_token[:12]).strip()
    scopes = [str(scope) for scope in (client.get("scopes") or [])]
    last_handshake_at = str((instance or {}).get("lastHandshakeAt") or _timestamp())
    rotated = bool((instance or {}).get("rotated"))

    _set_setting(API_TOKEN_KEY, api_token)
    _set_setting(API_TOKEN_ENC_KEY, _encrypt_secret(api_token))
    _delete_setting(API_TOKEN_KEY)
    _set_setting(TOKEN_PREFIX_KEY, token_prefix)
    _set_setting(TOKEN_SCOPES_KEY, json.dumps(scopes, separators=(",", ":")))
    _set_setting(LAST_HANDSHAKE_AT_KEY, last_handshake_at)
    _set_setting(LINK_STATUS_KEY, "active")
    _log(
        "info",
        "MovieVault token provisioned",
        (
            f"authMethod={token_provider().name}; instanceId={get_or_create_instance_id()}; "
            f"instanceName={get_instance_name()}; tokenPrefix={token_prefix}; "
            f"requestedScopes={','.join(REQUESTED_SCOPES)}; rotated={str(rotated).lower()}; "
            f"httpStatus={http_status}"
        ),
    )
    return data


def _mark_revoked():
    _set_setting(LINK_STATUS_KEY, "revoked")
    _delete_token_settings()
    _log(
        "warn",
        "MovieVault connection revoked",
        f"authMethod={token_provider().name}; instanceId={get_or_create_instance_id()}; error=instance_revoked",
    )


class MovieVaultTokenProvider:
    name = "base"

    def configured(self) -> bool:
        raise NotImplementedError

    def get_token(self) -> str:
        return _stored_movievault_api_token()

    def ensure_token(self) -> str:
        token = self.get_token()
        if token:
            return token
        if self.configured():
            self.refresh_token()
            return self.get_token()
        return ""

    def refresh_token(self) -> dict:
        raise NotImplementedError

    def status(self) -> dict:
        return {}


class HmacHandshakeTokenProvider(MovieVaultTokenProvider):
    name = "hmac_handshake"

    def configured(self) -> bool:
        return bool(_handshake_secret() and _ingest_url())

    def refresh_token(self) -> dict:
        base = _ingest_url()
        if not base:
            raise MovieVaultHandshakeError("MovieVault ingest URL is not configured")
        if not _handshake_secret():
            raise MovieVaultHandshakeError("MovieVault HMAC handshake secret is not configured")

        raw_body, headers, _nonce = build_handshake_request()
        url = f"{base}{HANDSHAKE_PATH}"
        try:
            response = requests.post(url, data=raw_body.encode("utf-8"), headers=headers, timeout=8)
        except Exception as ex:
            _log("warn", "MovieVault HMAC handshake failed", f"path={HANDSHAKE_PATH}; error={ex}")
            raise MovieVaultHandshakeError(str(ex)) from ex

        if response.status_code == 403 and _response_error_code(response) == "instance_revoked":
            _mark_revoked()
            raise MovieVaultInstanceRevoked("MovieVault instance is revoked")

        if response.status_code >= 400:
            code = _response_error_code(response) or f"http_{response.status_code}"
            _set_setting(LINK_STATUS_KEY, "error")
            _log(
                "warn",
                "MovieVault HMAC handshake rejected",
                (
                    f"instanceId={get_or_create_instance_id()}; instanceName={get_instance_name()}; "
                    f"requestedScopes={','.join(REQUESTED_SCOPES)}; httpStatus={response.status_code}; error={code}"
                ),
            )
            raise MovieVaultHandshakeError(f"MovieVault handshake failed: {code}")

        return _store_token_response(response.json(), response.status_code)

    def status(self) -> dict:
        return {
            "movievault_auth_method": self.name,
            "movievault_handshake_secret_set": bool(_handshake_secret()),
            "movievault_handshake_ready": self.configured(),
        }


class BootstrapSignedTokenProvider(MovieVaultTokenProvider):
    name = "bootstrap_signed"

    def configured(self) -> bool:
        return bool(_ingest_url())

    def bootstrap_payload(self) -> dict:
        _private_key_pem, public_key, _public_key_id = get_or_create_instance_key_pair()
        return {
            "instanceId": get_or_create_instance_id(),
            "instanceName": get_instance_name(),
            "publicKey": public_key,
            "software": {
                "name": "DiscVault",
                "version": SOFTWARE_VERSION,
            },
            "requestedScopes": list(REQUESTED_SCOPES),
        }

    def refresh_token(self) -> dict:
        if _setting_value(INSTANCE_PUBLIC_KEY_ID_KEY, "").strip():
            return self.recover_token()
        return self.bootstrap()

    def bootstrap(self) -> dict:
        base = _ingest_url()
        if not base:
            raise MovieVaultHandshakeError("MovieVault ingest URL is not configured")
        payload = self.bootstrap_payload()
        raw_body = _raw_json_body(payload)
        url = f"{base}{BOOTSTRAP_PATH}"
        try:
            response = requests.post(
                url,
                data=raw_body.encode("utf-8"),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=8,
            )
        except Exception as ex:
            _log("warn", "MovieVault bootstrap failed", f"path={BOOTSTRAP_PATH}; error={ex}")
            raise MovieVaultHandshakeError(str(ex)) from ex

        code = _response_error_code(response)
        if response.status_code == 403 and code == "instance_revoked":
            _mark_revoked()
            raise MovieVaultInstanceRevoked("MovieVault instance is revoked")
        if response.status_code == 409 and code == "instance_already_registered":
            return self.recover_token()
        if response.status_code >= 400:
            _set_setting(LINK_STATUS_KEY, "error")
            _log(
                "warn",
                "MovieVault bootstrap rejected",
                (
                    f"instanceId={get_or_create_instance_id()}; instanceName={get_instance_name()}; "
                    f"requestedScopes={','.join(REQUESTED_SCOPES)}; httpStatus={response.status_code}; "
                    f"error={code or f'http_{response.status_code}'}"
                ),
            )
            raise MovieVaultHandshakeError(f"MovieVault bootstrap failed: {code or response.status_code}")

        data = response.json()
        key_id = str(((data.get("instance") or {}) if isinstance(data, dict) else {}).get("keyId") or "").strip()
        if key_id:
            _set_setting(INSTANCE_PUBLIC_KEY_ID_KEY, key_id)
        return _store_token_response(data, response.status_code)

    def signed_recovery_request(self) -> tuple[str, dict, str]:
        private_key_pem, _public_key, public_key_id = get_or_create_instance_key_pair()
        payload = {
            "instanceId": get_or_create_instance_id(),
            "instanceName": get_instance_name(),
            "requestedScopes": list(REQUESTED_SCOPES),
        }
        raw_body = _raw_json_body(payload)
        timestamp = _timestamp()
        nonce = secrets.token_urlsafe(32)
        signature_input = f"{timestamp}.{nonce}.{raw_body}".encode("utf-8")

        from cryptography.hazmat.primitives import serialization

        private_key = serialization.load_pem_private_key(private_key_pem.encode("ascii"), password=None)
        signature = _b64url(private_key.sign(signature_input))
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-DiscVault-Timestamp": timestamp,
            "X-DiscVault-Nonce": nonce,
            "X-DiscVault-Key-Id": public_key_id,
            "X-DiscVault-Signature": f"key-v1={signature}",
        }
        return raw_body, headers, nonce

    def recover_token(self) -> dict:
        base = _ingest_url()
        if not base:
            raise MovieVaultHandshakeError("MovieVault ingest URL is not configured")
        raw_body, headers, _nonce = self.signed_recovery_request()
        url = f"{base}{HANDSHAKE_PATH}"
        try:
            response = requests.post(url, data=raw_body.encode("utf-8"), headers=headers, timeout=8)
        except Exception as ex:
            _log("warn", "MovieVault signed recovery failed", f"path={HANDSHAKE_PATH}; error={ex}")
            raise MovieVaultHandshakeError(str(ex)) from ex

        code = _response_error_code(response)
        if response.status_code == 403 and code == "instance_revoked":
            _mark_revoked()
            raise MovieVaultInstanceRevoked("MovieVault instance is revoked")
        if response.status_code >= 400:
            _set_setting(LINK_STATUS_KEY, "error")
            _log(
                "warn",
                "MovieVault signed recovery rejected",
                (
                    f"instanceId={get_or_create_instance_id()}; instanceName={get_instance_name()}; "
                    f"requestedScopes={','.join(REQUESTED_SCOPES)}; httpStatus={response.status_code}; "
                    f"error={code or f'http_{response.status_code}'}"
                ),
            )
            raise MovieVaultHandshakeError(f"MovieVault signed recovery failed: {code or response.status_code}")
        return _store_token_response(response.json(), response.status_code)

    def status(self) -> dict:
        return {
            "movievault_auth_method": self.name,
            "movievault_handshake_secret_set": False,
            "movievault_handshake_ready": self.configured(),
            "movievault_instance_key_set": bool(
                _setting_value(INSTANCE_PRIVATE_KEY_ENC_KEY, "")
                or _setting_value(INSTANCE_PRIVATE_KEY_KEY, "")
            ),
        }


def token_provider() -> MovieVaultTokenProvider:
    configured_method = _setting_value(AUTH_METHOD_KEY, "").strip().lower()
    env_method = os.environ.get("MOVIEVAULT_AUTH_METHOD", "").strip().lower()
    method = configured_method or env_method
    if method in {"hmac", "hmac_handshake", "legacy_hmac"}:
        return HmacHandshakeTokenProvider()
    return BootstrapSignedTokenProvider()


def perform_handshake() -> dict:
    if not is_movievault_integration_enabled():
        raise MovieVaultHandshakeError("MovieVault integration is disabled")
    return token_provider().refresh_token()


def _auth_token_for_request() -> str:
    if not is_movievault_integration_enabled():
        return ""
    return token_provider().ensure_token()


def movievault_request(
    method: str,
    url: str,
    *,
    include_auth: bool = True,
    retry_on_unauthorized: bool = True,
    **kwargs,
):
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault("Accept", "application/json")
    if include_auth:
        token = _auth_token_for_request()
        if token:
            headers["Authorization"] = f"Bearer {token}"
            if method.upper() in {"POST", "PUT", "PATCH"}:
                headers.setdefault("Content-Type", "application/json")
    response = requests.request(method.upper(), url, headers=headers, **kwargs)
    if (
        include_auth
        and retry_on_unauthorized
        and response.status_code == 401
        and is_movievault_integration_enabled()
        and token_provider().configured()
    ):
        _log(
            "warn",
            "MovieVault token recovery started",
            f"instanceId={get_or_create_instance_id()}; httpStatus=401; path={_path_only(url)}",
        )
        token_provider().refresh_token()
        headers["Authorization"] = f"Bearer {get_movievault_api_token()}"
        response = requests.request(method.upper(), url, headers=headers, **kwargs)
    return response


def movievault_status() -> dict:
    scopes_raw = _setting_value(TOKEN_SCOPES_KEY, "[]")
    try:
        scopes = json.loads(scopes_raw)
    except Exception:
        scopes = []
    if not isinstance(scopes, list):
        scopes = []
    token = get_movievault_api_token()
    return {
        "movievault_auth_method": token_provider().name,
        "movievault_enabled": is_movievault_integration_enabled(),
        "movievault_instance_id": _setting_value(INSTANCE_ID_KEY, ""),
        "movievault_instance_name": _setting_value(INSTANCE_NAME_KEY, ""),
        "movievault_instance_key_id": _setting_value(INSTANCE_PUBLIC_KEY_ID_KEY, ""),
        "movievault_link_status": _setting_value(LINK_STATUS_KEY, "unlinked"),
        "movievault_last_handshake_at": _setting_value(LAST_HANDSHAKE_AT_KEY, ""),
        "movievault_token_prefix": _setting_value(TOKEN_PREFIX_KEY, ""),
        "movievault_token_scopes": scopes,
        "movievault_token_set": bool(token),
        "movievault_key_set": bool(token),
        "movievault_token_masked": mask_token(token) if token else "",
        **token_provider().status(),
    }
