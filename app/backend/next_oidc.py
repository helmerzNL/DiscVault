"""Optional OpenID Connect login for the PostgreSQL-backed Next runtime."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

import requests
from authlib.integrations.requests_client import OAuth2Session
from authlib.jose import JsonWebToken
from authlib.oidc.core import CodeIDToken
from flask import Flask, redirect, request

try:  # pragma: no cover - exercised indirectly by both runtime layouts
    from .next_audit import audit_event
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_audit import audit_event


OIDC_ISSUER_ENV = "DISCVAULT_OIDC_ISSUER"
OIDC_CLIENT_ID_ENV = "DISCVAULT_OIDC_CLIENT_ID"
OIDC_CLIENT_SECRET_ENV = "DISCVAULT_OIDC_CLIENT_SECRET"
OIDC_PROVIDER_NAME_ENV = "DISCVAULT_OIDC_PROVIDER_NAME"
OIDC_TRANSACTION_TTL_SECONDS = 5 * 60
OIDC_FLOW_COOKIE_PREFIX = "dv_oidc_flow_"
OIDC_FLOW_COOKIE_PATH = "/api/next/auth/oidc/callback"
OIDC_HTTP_TIMEOUT = (3.05, 10)
OIDC_DISCOVERY_CACHE_SECONDS = 5 * 60
OIDC_ALLOWED_ID_TOKEN_ALGORITHMS = (
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
    "ES512",
)
OIDC_USERNAME_SANITIZER = re.compile(r"[^A-Za-z0-9._@+-]+")


class OidcConfigurationError(RuntimeError):
    """Raised when OIDC environment variables cannot form a safe configuration."""


class OidcFlowError(RuntimeError):
    """A callback-safe OIDC failure with a non-sensitive public code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class OidcConfig:
    issuer: str
    client_id: str
    client_secret: str
    provider_name: str


_discovery_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_discovery_cache_lock = threading.Lock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_local_hostname(hostname: str) -> bool:
    lowered = hostname.lower().rstrip(".")
    if lowered == "localhost" or lowered.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return False


def _validate_https_url(value: Any, label: str) -> str:
    text = str(value or "").strip().rstrip("/")
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as exc:
        raise OidcConfigurationError(f"{label} is not a valid URL") from exc
    hostname = str(parsed.hostname or "").lower()
    if (
        not hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (parsed.scheme != "https" and not (parsed.scheme == "http" and _is_local_hostname(hostname)))
    ):
        raise OidcConfigurationError(f"{label} must use HTTPS (HTTP is allowed only for localhost)")
    default_port = 443 if parsed.scheme == "https" else 80
    authority = hostname if port in {None, default_port} else f"{hostname}:{port}"
    if ":" in hostname and not hostname.startswith("["):
        authority = f"[{hostname}]" if port in {None, default_port} else f"[{hostname}]:{port}"
    return urlunsplit((parsed.scheme, authority, parsed.path.rstrip("/"), "", ""))


def oidc_config_from_env(environ: Mapping[str, str] | None = None) -> OidcConfig | None:
    values = environ if environ is not None else os.environ
    issuer = str(values.get(OIDC_ISSUER_ENV) or "").strip()
    client_id = str(values.get(OIDC_CLIENT_ID_ENV) or "").strip()
    client_secret = str(values.get(OIDC_CLIENT_SECRET_ENV) or "").strip()
    configured = (bool(issuer), bool(client_id), bool(client_secret))
    if any(configured) and not all(configured):
        raise OidcConfigurationError(
            f"{OIDC_ISSUER_ENV}, {OIDC_CLIENT_ID_ENV}, and {OIDC_CLIENT_SECRET_ENV} must be set together"
        )
    if not any(configured):
        return None
    normalized_issuer = _validate_https_url(issuer, OIDC_ISSUER_ENV)
    provider_name = str(values.get(OIDC_PROVIDER_NAME_ENV) or "").strip() or "OIDC"
    if len(provider_name) > 120:
        raise OidcConfigurationError(f"{OIDC_PROVIDER_NAME_ENV} must be 120 characters or fewer")
    return OidcConfig(
        issuer=normalized_issuer,
        client_id=client_id,
        client_secret=client_secret,
        provider_name=provider_name,
    )


def validate_oidc_environment() -> None:
    oidc_config_from_env()


def oidc_auth_status(table_exists: bool) -> dict[str, Any]:
    config = oidc_config_from_env()
    configured = config is not None
    return {
        "oidc_available": bool(configured and table_exists),
        "oidc_provider_name": config.provider_name if config else None,
    }


def oidc_state_hash(state: Any) -> str:
    return hashlib.sha256(str(state or "").encode("utf-8")).hexdigest()


def oidc_pkce_s256_challenge(verifier: Any) -> str:
    import base64

    digest = hashlib.sha256(str(verifier or "").encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def safe_oidc_return_path(value: Any, default: str = "/") -> str:
    candidate = str(value or "").strip()
    if (
        not candidate.startswith("/")
        or candidate.startswith("//")
        or "\\" in candidate
        or any(ord(char) < 32 or ord(char) == 127 for char in candidate)
    ):
        return default
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc or parsed.path.startswith("//"):
        return default
    if parsed.path in {"/api/next/auth/oidc/start", "/api/next/auth/oidc/callback"}:
        return default
    return urlunsplit(("", "", parsed.path or "/", parsed.query, parsed.fragment))


def oidc_feedback_path(
    return_path: str,
    *,
    error: str | None = None,
    linked: bool = False,
) -> str:
    parsed = urlsplit(safe_oidc_return_path(return_path))
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in {"oidc_error", "oidc_linked"}
    ]
    if error:
        query.append(("oidc_error", error))
    elif linked:
        query.append(("oidc_linked", "1"))
    return urlunsplit(("", "", parsed.path, urlencode(query), parsed.fragment))


def clear_oidc_discovery_cache() -> None:
    with _discovery_cache_lock:
        _discovery_cache.clear()


def _openid_configuration_url(issuer: str) -> str:
    return f"{issuer.rstrip('/')}/.well-known/openid-configuration"


def fetch_oidc_document(url: str) -> dict[str, Any]:
    try:
        result = requests.get(
            url,
            headers={"Accept": "application/json"},
            timeout=OIDC_HTTP_TIMEOUT,
        )
        result.raise_for_status()
        payload = result.json()
    except (requests.RequestException, ValueError) as exc:
        raise OidcFlowError("provider_unavailable") from exc
    if not isinstance(payload, dict):
        raise OidcFlowError("provider_invalid")
    return payload


def oidc_discovery(config: OidcConfig, *, force: bool = False) -> dict[str, Any]:
    now = time.monotonic()
    with _discovery_cache_lock:
        cached = _discovery_cache.get(config.issuer)
        if cached and not force and cached[0] > now:
            return dict(cached[1])
    payload = fetch_oidc_document(_openid_configuration_url(config.issuer))
    if str(payload.get("issuer") or "").rstrip("/") != config.issuer:
        raise OidcFlowError("provider_invalid")
    try:
        normalized = {
            **payload,
            "authorization_endpoint": _validate_https_url(
                payload.get("authorization_endpoint"), "authorization_endpoint"
            ),
            "token_endpoint": _validate_https_url(payload.get("token_endpoint"), "token_endpoint"),
            "jwks_uri": _validate_https_url(payload.get("jwks_uri"), "jwks_uri"),
        }
    except OidcConfigurationError as exc:
        raise OidcFlowError("provider_invalid") from exc
    with _discovery_cache_lock:
        _discovery_cache[config.issuer] = (
            now + OIDC_DISCOVERY_CACHE_SECONDS,
            normalized,
        )
    return dict(normalized)


def _token_auth_method(discovery: Mapping[str, Any]) -> str:
    supported = discovery.get("token_endpoint_auth_methods_supported")
    if not isinstance(supported, list):
        return "client_secret_basic"
    for method in ("client_secret_basic", "client_secret_post"):
        if method in supported:
            return method
    raise OidcFlowError("provider_invalid")


def exchange_oidc_code(
    config: OidcConfig,
    discovery: Mapping[str, Any],
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> dict[str, Any]:
    client = OAuth2Session(
        config.client_id,
        config.client_secret,
        redirect_uri=redirect_uri,
        scope="openid profile email",
        code_challenge_method="S256",
        token_endpoint_auth_method=_token_auth_method(discovery),
    )
    try:
        token = client.fetch_token(
            str(discovery["token_endpoint"]),
            code=code,
            code_verifier=code_verifier,
            timeout=OIDC_HTTP_TIMEOUT,
        )
    except Exception as exc:
        raise OidcFlowError("token_exchange_failed") from exc
    if not isinstance(token, dict) or not token.get("id_token"):
        raise OidcFlowError("id_token_missing")
    return token


def validate_oidc_id_token(
    config: OidcConfig,
    discovery: Mapping[str, Any],
    token: Mapping[str, Any],
    *,
    nonce_hash: str,
    code: str,
) -> dict[str, Any]:
    jwks = fetch_oidc_document(str(discovery["jwks_uri"]))
    try:
        claims = JsonWebToken(OIDC_ALLOWED_ID_TOKEN_ALGORITHMS).decode(
            str(token["id_token"]),
            jwks,
            claims_cls=CodeIDToken,
            claims_options={
                "iss": {"essential": True, "value": config.issuer},
                "sub": {"essential": True},
                "aud": {"essential": True, "value": config.client_id},
                "exp": {"essential": True},
                "iat": {"essential": True},
                "nonce": {"essential": True},
            },
            claims_params={
                "client_id": config.client_id,
                "code": code,
                "access_token": token.get("access_token"),
            },
        )
        claims.validate(leeway=60)
    except Exception as exc:
        raise OidcFlowError("id_token_invalid") from exc
    subject = str(claims.get("sub") or "").strip()
    presented_nonce = str(claims.get("nonce") or "")
    if (
        not subject
        or len(subject) > 255
        or not presented_nonce
        or not secrets.compare_digest(
            oidc_state_hash(presented_nonce),
            str(nonce_hash or ""),
        )
    ):
        raise OidcFlowError("id_token_invalid")
    return dict(claims)


def oidc_identity_payloads(conn, user_id: UUID | str, table_exists: Callable[[Any, str], bool]) -> list[dict[str, Any]]:
    if not table_exists(conn, "oidc_identities"):
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, issuer, subject, provider_name, email, preferred_username,
                   display_name, created_at, updated_at, last_login_at
            FROM oidc_identities
            WHERE user_id=%s
            ORDER BY created_at, id
            """,
            (user_id,),
        )
        rows = cur.fetchall()
    config = oidc_config_from_env()
    return [
        {
            "id": row["id"],
            "providerName": row.get("provider_name") or "OIDC",
            "provider_name": row.get("provider_name") or "OIDC",
            "issuer": row["issuer"],
            "subject": row["subject"],
            "email": row.get("email"),
            "preferredUsername": row.get("preferred_username"),
            "preferred_username": row.get("preferred_username"),
            "displayName": row.get("display_name"),
            "display_name": row.get("display_name"),
            "createdAt": row.get("created_at"),
            "created_at": row.get("created_at"),
            "updatedAt": row.get("updated_at"),
            "updated_at": row.get("updated_at"),
            "lastLoginAt": row.get("last_login_at"),
            "last_login_at": row.get("last_login_at"),
            "usable": bool(config and row["issuer"] == config.issuer),
        }
        for row in rows
    ]


def oidc_identity_count(conn, user_id: UUID | str | None = None) -> int:
    with conn.cursor() as cur:
        if user_id is None:
            cur.execute("SELECT COUNT(*) AS count FROM oidc_identities")
        else:
            cur.execute(
                "SELECT COUNT(*) AS count FROM oidc_identities WHERE user_id=%s",
                (user_id,),
            )
        row = cur.fetchone()
    return int((row or {}).get("count") or 0)


def _preferred_username(claims: Mapping[str, Any]) -> str:
    candidates = (
        claims.get("preferred_username"),
        str(claims.get("email") or "").split("@", 1)[0],
        claims.get("name"),
        f"oidc-{hashlib.sha256(str(claims['sub']).encode('utf-8')).hexdigest()[:12]}",
    )
    for candidate in candidates:
        value = OIDC_USERNAME_SANITIZER.sub("-", str(candidate or "").strip()).strip(".-")
        if value:
            return value[:80]
    return f"oidc-{secrets.token_hex(6)}"


def _insert_oidc_user(
    conn,
    claims: Mapping[str, Any],
    *,
    normalize_username: Callable[[Any], str],
) -> dict[str, Any]:
    base = normalize_username(_preferred_username(claims))
    display_name = str(claims.get("name") or claims.get("preferred_username") or base).strip()[:160] or base
    first_name = str(claims.get("given_name") or "").strip()[:160] or None
    last_name = str(claims.get("family_name") or "").strip()[:160] or None
    for attempt in range(100):
        suffix = "" if attempt == 0 else f"-{attempt + 1}"
        username = f"{base[:80 - len(suffix)]}{suffix}"
        user_id = uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (
                    id, username, display_name, first_name, last_name,
                    status, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, 'active', now(), now())
                ON CONFLICT (username) DO NOTHING
                RETURNING id, username, display_name, first_name, last_name, status
                """,
                (user_id, username, display_name, first_name, last_name),
            )
            row = cur.fetchone()
        if row:
            return row
    raise OidcFlowError("username_unavailable")


def _upsert_oidc_identity(
    conn,
    *,
    config: OidcConfig,
    user_id: UUID | str,
    claims: Mapping[str, Any],
    login: bool,
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO oidc_identities (
                user_id, issuer, subject, provider_name, email,
                preferred_username, display_name, created_at, updated_at, last_login_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now(), CASE WHEN %s THEN now() END)
            ON CONFLICT (issuer, subject) DO UPDATE SET
                provider_name=EXCLUDED.provider_name,
                email=EXCLUDED.email,
                preferred_username=EXCLUDED.preferred_username,
                display_name=EXCLUDED.display_name,
                updated_at=now(),
                last_login_at=CASE
                    WHEN %s THEN now()
                    ELSE oidc_identities.last_login_at
                END
            WHERE oidc_identities.user_id=EXCLUDED.user_id
            RETURNING id, user_id, issuer, subject
            """,
            (
                user_id,
                config.issuer,
                str(claims["sub"]),
                config.provider_name,
                str(claims.get("email") or "").strip()[:320] or None,
                str(claims.get("preferred_username") or "").strip()[:320] or None,
                str(claims.get("name") or "").strip()[:320] or None,
                login,
                login,
            ),
        )
        row = cur.fetchone()
    if not row:
        raise OidcFlowError("identity_already_linked")
    return row


def _callback_url(configured_origin: str) -> str:
    origin = _validate_https_url(configured_origin, "OIDC callback origin")
    return f"{origin}/api/next/auth/oidc/callback"


def _transaction_row(conn, state: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM oidc_auth_transactions WHERE expires_at < now()")
        cur.execute(
            """
            UPDATE oidc_auth_transactions
            SET used_at=now()
            WHERE state_hash=%s
              AND used_at IS NULL
              AND expires_at > now()
            RETURNING id, nonce_hash, browser_binding_hash, code_verifier, mode,
                      initiating_user_id, return_path, redirect_uri, expires_at
            """,
            (oidc_state_hash(state),),
        )
        return cur.fetchone()


def _safe_subject_digest(issuer: str, subject: str) -> str:
    return hashlib.sha256(f"{issuer}\0{subject}".encode("utf-8")).hexdigest()[:16]


def _flow_cookie_name(state: str) -> str:
    return f"{OIDC_FLOW_COOKIE_PREFIX}{oidc_state_hash(state)[:24]}"


def _clear_flow_cookie(result, state: str, *, secure: bool):
    if state:
        result.delete_cookie(
            _flow_cookie_name(state),
            path=OIDC_FLOW_COOKIE_PATH,
            secure=secure,
            httponly=True,
            samesite="Lax",
        )
    return result


def register_oidc_routes(
    app: Flask,
    *,
    connect: Callable[[], Any],
    table_exists: Callable[[Any, str], bool],
    current_session_user: Callable[[Any], dict[str, Any] | None],
    create_session_token: Callable[[str, str], str],
    session_redirect: Callable[[str, str], Any],
    registration_enabled: Callable[[Any], bool],
    default_registration_role: Callable[[Any], str],
    assign_role: Callable[[Any, UUID | str, str], None],
    primary_role: Callable[[Any, UUID | str], str | None],
    set_auth_enabled: Callable[[Any], None],
    normalize_username: Callable[[Any], str],
    callback_origin: Callable[[], str],
) -> None:
    def require_tables(conn) -> None:
        if not table_exists(conn, "oidc_identities") or not table_exists(conn, "oidc_auth_transactions"):
            raise OidcFlowError("oidc_not_ready")

    def record_failure(conn, code: str, transaction: Mapping[str, Any] | None = None) -> None:
        audit_event(
            conn,
            event_type="auth.oidc_failed",
            category="security",
            target_type="oidc_provider",
            summary="OIDC authentication failed",
            metadata={
                "provider": (oidc_config_from_env().provider_name if oidc_config_from_env() else None),
                "code": code,
                "mode": (transaction or {}).get("mode"),
            },
        )

    @app.get("/api/next/auth/oidc/start")
    def oidc_start():
        return_path = safe_oidc_return_path(
            request.args.get("returnPath") or request.args.get("return_path"),
            "/",
        )
        mode = str(request.args.get("mode") or "login").strip().lower()
        if mode not in {"login", "link"}:
            return redirect(oidc_feedback_path(return_path, error="invalid_request"), code=302)
        try:
            config = oidc_config_from_env()
            if not config:
                raise OidcFlowError("oidc_disabled")
            callback_url = _callback_url(callback_origin())
            discovery = oidc_discovery(config)
            state = secrets.token_urlsafe(32)
            nonce = secrets.token_urlsafe(32)
            code_verifier = secrets.token_urlsafe(64)
            browser_binding = secrets.token_urlsafe(32)
            with connect() as conn:
                require_tables(conn)
                actor = current_session_user(conn)
                if mode == "link" and not actor:
                    raise OidcFlowError("login_required")
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM oidc_auth_transactions WHERE expires_at < now()")
                        cur.execute(
                            """
                            INSERT INTO oidc_auth_transactions (
                                state_hash, nonce_hash, browser_binding_hash,
                                code_verifier, mode, initiating_user_id,
                                return_path, redirect_uri, expires_at
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                oidc_state_hash(state),
                                oidc_state_hash(nonce),
                                oidc_state_hash(browser_binding),
                                code_verifier,
                                mode,
                                actor["id"] if actor else None,
                                return_path,
                                callback_url,
                                _utcnow() + timedelta(seconds=OIDC_TRANSACTION_TTL_SECONDS),
                            ),
                        )
                    audit_event(
                        conn,
                        event_type="auth.oidc_started",
                        category="security",
                        actor={
                            "id": actor.get("id"),
                            "username": actor.get("username"),
                            "role": primary_role(conn, actor["id"]),
                        }
                        if actor
                        else None,
                        target_type="oidc_provider",
                        summary=f"Started OIDC {mode}",
                        metadata={"provider": config.provider_name, "mode": mode},
                    )
            client = OAuth2Session(
                config.client_id,
                config.client_secret,
                redirect_uri=callback_url,
                scope="openid profile email",
                code_challenge_method="S256",
            )
            authorization_url, _ = client.create_authorization_url(
                str(discovery["authorization_endpoint"]),
                state=state,
                nonce=nonce,
                code_verifier=code_verifier,
            )
            result = redirect(authorization_url, code=302)
            result.set_cookie(
                _flow_cookie_name(state),
                browser_binding,
                max_age=OIDC_TRANSACTION_TTL_SECONDS,
                secure=urlsplit(callback_url).scheme == "https",
                httponly=True,
                samesite="Lax",
                path=OIDC_FLOW_COOKIE_PATH,
            )
            return result
        except OidcFlowError as exc:
            return redirect(oidc_feedback_path(return_path, error=exc.code), code=302)
        except OidcConfigurationError:
            return redirect(
                oidc_feedback_path(return_path, error="oidc_misconfigured"),
                code=302,
            )

    @app.get("/api/next/auth/oidc/callback")
    def oidc_callback():
        state = str(request.args.get("state") or "").strip()
        transaction: dict[str, Any] | None = None
        return_path = "/"
        cookie_secure = request.is_secure
        try:
            if not state:
                raise OidcFlowError("invalid_state")
            config = oidc_config_from_env()
            if not config:
                raise OidcFlowError("oidc_disabled")
            with connect() as conn:
                require_tables(conn)
                with conn.transaction():
                    transaction = _transaction_row(conn, state)
                if not transaction:
                    raise OidcFlowError("invalid_state")
                return_path = safe_oidc_return_path(transaction.get("return_path"), "/")
                cookie_secure = (
                    urlsplit(str(transaction.get("redirect_uri") or "")).scheme
                    == "https"
                )
                browser_binding = str(
                    request.cookies.get(_flow_cookie_name(state)) or ""
                )
                if (
                    not browser_binding
                    or not secrets.compare_digest(
                        oidc_state_hash(browser_binding),
                        str(transaction.get("browser_binding_hash") or ""),
                    )
                ):
                    raise OidcFlowError("invalid_state")
                if request.args.get("error"):
                    raise OidcFlowError("provider_denied")
                code = str(request.args.get("code") or "").strip()
                if not code:
                    raise OidcFlowError("authorization_code_missing")
                discovery = oidc_discovery(config)
                token = exchange_oidc_code(
                    config,
                    discovery,
                    code=code,
                    code_verifier=str(transaction["code_verifier"]),
                    redirect_uri=str(transaction["redirect_uri"]),
                )
                claims = validate_oidc_id_token(
                    config,
                    discovery,
                    token,
                    nonce_hash=str(transaction["nonce_hash"]),
                    code=code,
                )
                subject = str(claims["sub"])
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT pg_advisory_xact_lock("
                            "hashtext('discvault-legacy-bootstrap'))"
                        )
                        cur.execute(
                            """
                            SELECT oi.id, oi.user_id, u.username, u.display_name, u.status
                            FROM oidc_identities oi
                            JOIN users u ON u.id=oi.user_id
                            WHERE oi.issuer=%s AND oi.subject=%s
                            FOR UPDATE
                            """,
                            (config.issuer, subject),
                        )
                        identity = cur.fetchone()
                    mode = str(transaction["mode"])
                    actor = current_session_user(conn)
                    created_user = False
                    if mode == "link":
                        initiating_user_id = transaction.get("initiating_user_id")
                        if not actor or str(actor["id"]) != str(initiating_user_id):
                            raise OidcFlowError("login_required")
                        if identity and str(identity["user_id"]) != str(actor["id"]):
                            raise OidcFlowError("identity_already_linked")
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                SELECT id FROM oidc_identities
                                WHERE user_id=%s AND issuer=%s AND subject<>%s
                                """,
                                (actor["id"], config.issuer, subject),
                            )
                            if cur.fetchone():
                                raise OidcFlowError("issuer_already_linked")
                        user = actor
                    elif identity:
                        user = identity
                    else:
                        with conn.cursor() as cur:
                            cur.execute("SELECT COUNT(*) AS count FROM users")
                            user_count = int((cur.fetchone() or {}).get("count") or 0)
                        if user_count > 0 and not registration_enabled(conn):
                            raise OidcFlowError("registration_disabled")
                        user = _insert_oidc_user(
                            conn,
                            claims,
                            normalize_username=normalize_username,
                        )
                        assign_role(
                            conn,
                            user["id"],
                            "owner" if user_count == 0 else default_registration_role(conn),
                        )
                        created_user = True
                    if user.get("status") != "active":
                        raise OidcFlowError("user_disabled")
                    identity_row = _upsert_oidc_identity(
                        conn,
                        config=config,
                        user_id=user["id"],
                        claims=claims,
                        login=mode == "login",
                    )
                    if created_user:
                        set_auth_enabled(conn)
                    audit_event(
                        conn,
                        event_type="auth.oidc_linked" if mode == "link" else "auth.oidc_login",
                        category="security",
                        actor={
                            "id": user["id"],
                            "username": user["username"],
                            "role": primary_role(conn, user["id"]),
                        },
                        target_type="oidc_identity",
                        target_id=identity_row["id"],
                        summary=(
                            f"Linked {config.provider_name} identity"
                            if mode == "link"
                            else f"{user['username']} logged in with {config.provider_name}"
                        ),
                        metadata={
                            "provider": config.provider_name,
                            "subjectDigest": _safe_subject_digest(config.issuer, subject),
                            "createdUser": created_user,
                        },
                    )
                if str(transaction["mode"]) == "link":
                    return _clear_flow_cookie(
                        redirect(
                            oidc_feedback_path(return_path, linked=True),
                            code=302,
                        ),
                        state,
                        secure=cookie_secure,
                    )
                token_value = create_session_token(str(user["id"]), str(user["username"]))
                return _clear_flow_cookie(
                    session_redirect(return_path, token_value),
                    state,
                    secure=cookie_secure,
                )
        except OidcFlowError as exc:
            try:
                with connect() as conn:
                    with conn.transaction():
                        record_failure(conn, exc.code, transaction)
            except Exception:
                app.logger.warning("Unable to persist OIDC failure audit event", exc_info=True)
            return _clear_flow_cookie(
                redirect(
                    oidc_feedback_path(return_path, error=exc.code),
                    code=302,
                ),
                state,
                secure=cookie_secure,
            )
        except OidcConfigurationError:
            return _clear_flow_cookie(
                redirect(
                    oidc_feedback_path(
                        return_path,
                        error="oidc_misconfigured",
                    ),
                    code=302,
                ),
                state,
                secure=cookie_secure,
            )
