import hashlib
import json
import os
import re
import time
from urllib.parse import quote

import requests


PROVIDER_ID = "movievault_26"
PROVIDER_LABEL = "MovieVault 26"
DEFAULT_MOVIEVAULT_URL = "https://movies.vaultstack.eu"
BOOTSTRAP_PATH = "/api/v1/internal/discvault/bootstrap"
HANDSHAKE_PATH = "/api/v1/internal/discvault/handshake"
REQUESTED_SCOPES = ("search:read", "contributions:write", "contributions:read")
PUBLIC_BARCODE_LENGTHS = {8, 12, 13, 14}
FORBIDDEN_CONTRIBUTION_KEYS = {
    "apiToken",
    "authorization",
    "inviteCode",
    "jwtSecret",
    "libraryDetails",
    "localPath",
    "mediaGroup",
    "mediaGroups",
    "owner_id",
    "passkeys",
    "personalRating",
    "privateNotes",
    "providerTokens",
    "purchaseDate",
    "purchasePrice",
    "roles",
    "sessions",
    "shelf",
    "userIds",
    "usernames",
    "watchHistory",
    "watchlist",
}
_TEMPLATE_CACHE = {}

CLIENT_VERSION_UNSUPPORTED_CODE = "client_version_unsupported"

RATE_LIMIT_MAX_RETRIES = 1
RATE_LIMIT_BACKOFF_CAP = 5.0
RATE_LIMIT_DEFAULT_DELAY = 1.0


def _coerce_positive_float(value, default):
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


# Per-request HTTP timeout. Kept well under the frontend's 30s lookup budget so a
# slow or unreachable MovieVault server cannot make a search appear to hang.
REQUEST_TIMEOUT_SECONDS = _coerce_positive_float(os.environ.get("DISCVAULT_MOVIEVAULT_TIMEOUT"), 8.0)

# A single lookup runs several MovieVault entrypoints (title search, movie
# details, box-set candidates), each issuing its own request. If the server is
# unreachable, every request would otherwise pay the full timeout and the total
# easily exceeds the frontend's 30s budget. Once a request proves the server
# unreachable (or throttled to exhaustion) we set this flag on the shared lookup
# context so the remaining entrypoints skip their calls instead of stacking
# timeouts. The context is rebuilt per lookup, so the flag resets each search.
_UNREACHABLE_CONTEXT_KEY = "_movievaultUnreachable"


class MovieVaultUnavailable(RuntimeError):
    """Raised to skip a MovieVault call after the server proved unreachable in this lookup."""


def _movievault_network_error_types():
    exceptions = getattr(requests, "exceptions", None)
    resolved = []
    for name in ("ConnectTimeout", "ReadTimeout", "Timeout", "ConnectionError"):
        exc = getattr(exceptions, name, None) if exceptions is not None else None
        if isinstance(exc, type) and issubclass(exc, BaseException):
            resolved.append(exc)
    return tuple(resolved)


def _movievault_unreachable(context):
    return bool(isinstance(context, dict) and context.get(_UNREACHABLE_CONTEXT_KEY))


def _mark_movievault_unreachable(context):
    if isinstance(context, dict):
        context[_UNREACHABLE_CONTEXT_KEY] = True


class MovieVaultClientVersionUnsupported(RuntimeError):
    """Raised when MovieVault rejects DiscVault with HTTP 426 / client_version_unsupported."""

    def __init__(self, message, *, min_version="", detected_version=""):
        super().__init__(message)
        self.min_version = min_version
        self.detected_version = detected_version


class MovieVaultRateLimited(RuntimeError):
    """Raised when MovieVault throttles DiscVault with HTTP 429 / Too Many Requests."""

    def __init__(self, message, *, retry_after=0.0, url=""):
        super().__init__(message)
        self.retry_after = retry_after
        self.url = url


def _settings(context):
    return (context or {}).get("settings") or {}


def _secrets(context):
    return (context or {}).get("secrets") or {}


def _base_url(context):
    movievault = (context or {}).get("movievault") or {}
    return str(
        movievault.get("searchUrl")
        or os.environ.get("MOVIEVAULT_SEARCH_URL")
        or os.environ.get("MOVIEVAULT_BASE_URL")
        or DEFAULT_MOVIEVAULT_URL
    ).strip().rstrip("/")


def _contribution_url(context):
    movievault = (context or {}).get("movievault") or {}
    return str(
        movievault.get("contributionUrl")
        or os.environ.get("MOVIEVAULT_CONTRIBUTION_URL")
        or os.environ.get("MOVIEVAULT_INGEST_URL")
        or DEFAULT_MOVIEVAULT_URL
    ).strip().rstrip("/")


def _token(context):
    return str(_secrets(context).get("token") or _secrets(context).get("apiToken") or "").strip()


def _headers(context):
    headers = {"Accept": "application/json"}
    token = _token(context)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _status_code(response):
    try:
        return int(getattr(response, "status_code", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _retry_after_seconds(response, *, default=0.0):
    headers = getattr(response, "headers", None) or {}
    try:
        raw = headers.get("Retry-After")
    except AttributeError:
        raw = None
    if raw is None:
        return default
    try:
        return max(0.0, float(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def _json(response):
    try:
        payload = response.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _response_error(payload):
    if not isinstance(payload, dict):
        return "", ""
    error = payload.get("error")
    if isinstance(error, dict):
        return _text(error.get("code")), _text(error.get("message"))
    return _text(payload.get("code") or payload.get("error")), _text(payload.get("message"))


def _error_object(payload):
    if not isinstance(payload, dict):
        return {}
    error = payload.get("error")
    return error if isinstance(error, dict) else payload


def _version_fields(payload):
    error = _error_object(payload)
    min_version = _text(error.get("minVersion") or error.get("min_version") or payload.get("minVersion"))
    detected = _text(error.get("detectedVersion") or error.get("detected_version") or payload.get("detectedVersion"))
    return min_version, detected


def _is_client_version_unsupported(status_code, payload):
    if int(status_code or 0) == 426:
        return True
    code, _message = _response_error(payload)
    return code == CLIENT_VERSION_UNSUPPORTED_CODE


def _client_version_unsupported_message(payload):
    min_version, detected = _version_fields(payload)
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
    return message


def _body_client_version(body):
    if not isinstance(body, dict):
        return ""
    software = body.get("software") if isinstance(body.get("software"), dict) else {}
    for value in (
        body.get("clientVersion"),
        software.get("version"),
        software.get("backendVersion"),
        body.get("sourceVersion"),
        body.get("instanceVersion"),
    ):
        text = _text(value)
        if text:
            return text
    return ""


def _ensure_client_version(body, context):
    if not isinstance(body, dict):
        return body
    if _text(body.get("clientVersion")):
        return body
    version = _body_client_version(body) or _source_version(context)
    if version:
        body["clientVersion"] = version
    return body


def _error_code(response):
    payload = _json(response)
    code, _message = _response_error(payload)
    return code


def connection_recovery_action(payload, context=None):
    payload = payload or {}
    phase = _text(payload.get("phase")).lower()
    status_code = int(payload.get("statusCode") or payload.get("status_code") or 0)
    response = payload.get("response") or {}
    code, message = _response_error(response)
    lowered = message.lower()
    if _is_client_version_unsupported(status_code, response):
        return {
            "action": "",
            "reason": CLIENT_VERSION_UNSUPPORTED_CODE,
            "terminal": True,
            "message": _client_version_unsupported_message(response),
        }
    if status_code == 400 and phase == "recovery" and code == "validation_error" and "bootstrap is required" in lowered:
        return {"action": "bootstrap", "reason": "server_requires_bootstrap"}
    if status_code == 400 and phase == "bootstrap" and code == "validation_error":
        if "already linked" in lowered or "use signed recovery" in lowered:
            return {"action": "recover", "reason": "server_requires_signed_recovery"}
    return {"action": ""}


def connection_request(payload, context=None):
    payload = payload if isinstance(payload, dict) else {}
    phase = _text(payload.get("phase")).lower()
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if phase == "bootstrap":
        public_key = _text(payload.get("publicKey") or payload.get("public_key"))
        request_body = dict(body)
        if public_key:
            request_body["publicKey"] = public_key
        _ensure_client_version(request_body, context)
        return {
            "status": "ok",
            "provider": PROVIDER_ID,
            "phase": "bootstrap",
            "method": "POST",
            "path": BOOTSTRAP_PATH,
            "headers": headers,
            "body": request_body,
            "auth": "public_key_bootstrap",
            "requestedScopes": list(REQUESTED_SCOPES),
        }
    if phase in {"recovery", "handshake"}:
        timestamp = _text(payload.get("timestamp"))
        nonce = _text(payload.get("nonce"))
        key_id = _text(payload.get("publicKeyId") or payload.get("public_key_id") or payload.get("keyId"))
        signature = _text(payload.get("signature"))
        if timestamp:
            headers["X-DiscVault-Timestamp"] = timestamp
        if nonce:
            headers["X-DiscVault-Nonce"] = nonce
        if key_id:
            headers["X-DiscVault-Key-Id"] = key_id
        if signature:
            headers["X-DiscVault-Signature"] = signature if signature.startswith("key-v1=") else f"key-v1={signature}"
        recovery_body = _ensure_client_version(dict(body), context)
        return {
            "status": "ok",
            "provider": PROVIDER_ID,
            "phase": "recovery",
            "method": "POST",
            "path": HANDSHAKE_PATH,
            "headers": headers,
            "body": recovery_body,
            "auth": "signed_recovery",
            "requestedScopes": list(REQUESTED_SCOPES),
        }
    return {"status": "skipped", "provider": PROVIDER_ID, "reason": "unknown_connection_phase"}


def _canonical_contribution_body(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _contribution_signature_headers(signer, raw_body):
    if not callable(signer):
        return {}
    try:
        result = signer(raw_body) or {}
    except Exception:
        return {}
    if not isinstance(result, dict):
        return {}
    key_id = _text(result.get("keyId") or result.get("key_id"))
    timestamp = _text(result.get("timestamp"))
    nonce = _text(result.get("nonce"))
    signature = _text(result.get("signature"))
    if not (key_id and timestamp and nonce and signature):
        return {}
    return {
        "X-DiscVault-Key-Id": key_id,
        "X-DiscVault-Timestamp": timestamp,
        "X-DiscVault-Nonce": nonce,
        "X-DiscVault-Signature": signature if signature.startswith("key-v1=") else f"key-v1={signature}",
    }


def _request(method, url, *, context=None, params=None, json_payload=None, retry_recovery=True, allow_validation_error=False, sign_body=False, retry_429=RATE_LIMIT_MAX_RETRIES):
    if _movievault_unreachable(context):
        raise MovieVaultUnavailable(
            f"{PROVIDER_LABEL} was unreachable earlier in this lookup; skipping further requests."
        )
    headers = _headers(context)
    data = None
    if json_payload is not None:
        headers["Content-Type"] = "application/json"
        if sign_body:
            signer = (context or {}).get("movievaultSignRequest")
            raw_body = _canonical_contribution_body(json_payload)
            signature_headers = _contribution_signature_headers(signer, raw_body)
            if signature_headers:
                data = raw_body.encode("utf-8")
                headers.update(signature_headers)
    request_func = getattr(requests, "request", None)
    try:
        if callable(request_func):
            if data is not None:
                response = request_func(method, url, params=params, data=data, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
            else:
                response = request_func(method, url, params=params, json=json_payload, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        elif method.upper() == "GET":
            response = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        elif data is not None:
            response = requests.post(url, data=data, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        else:
            response = requests.post(url, json=json_payload, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    except _movievault_network_error_types():
        _mark_movievault_unreachable(context)
        raise

    status_code = _status_code(response)
    if status_code == 401 and retry_recovery:
        recover = (context or {}).get("movievaultRecoverToken")
        if callable(recover):
            token = _text(recover())
            if token:
                (context or {}).setdefault("secrets", {})["token"] = token
                return _request(
                    method,
                    url,
                    context=context,
                    params=params,
                    json_payload=json_payload,
                    retry_recovery=False,
                    allow_validation_error=allow_validation_error,
                    sign_body=sign_body,
                    retry_429=retry_429,
                )
    if status_code == 429:
        retry_after = _retry_after_seconds(response, default=RATE_LIMIT_DEFAULT_DELAY)
        if retry_429 > 0:
            delay = retry_after if retry_after > 0 else RATE_LIMIT_DEFAULT_DELAY
            time.sleep(min(delay, RATE_LIMIT_BACKOFF_CAP))
            return _request(
                method,
                url,
                context=context,
                params=params,
                json_payload=json_payload,
                retry_recovery=retry_recovery,
                allow_validation_error=allow_validation_error,
                sign_body=sign_body,
                retry_429=retry_429 - 1,
            )
        _mark_movievault_unreachable(context)
        raise MovieVaultRateLimited(
            f"{PROVIDER_LABEL} is rate limiting requests (HTTP 429). Please retry later.",
            retry_after=retry_after,
            url=url,
        )
    if status_code == 403 and _error_code(response) == "instance_revoked":
        mark_revoked = (context or {}).get("movievaultMarkRevoked")
        if callable(mark_revoked):
            mark_revoked()
    if _is_client_version_unsupported(status_code, _json(response)):
        payload = _json(response)
        min_version, detected = _version_fields(payload)
        raise MovieVaultClientVersionUnsupported(
            _client_version_unsupported_message(payload),
            min_version=min_version,
            detected_version=detected,
        )
    if status_code == 404 or (allow_validation_error and status_code == 400 and _error_code(response) == "validation_error"):
        return response
    response.raise_for_status()
    return response


def _get(context, path, **params):
    clean_params = {key: value for key, value in params.items() if value not in (None, "")}
    return _json(_request("GET", f"{_base_url(context)}{path}", context=context, params=clean_params))


def _post_contribution(context, envelope):
    return _json(
        _request(
            "POST",
            f"{_contribution_url(context)}/api/v1/contributions",
            context=context,
            json_payload=envelope,
            allow_validation_error=True,
            sign_body=True,
        )
    )


def _items(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("items", "results", "matches", "candidates", "boxSets", "box_sets", "sets", "containers", "movies", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    for key in ("data", "result", "item", "candidate", "movie", "release", "boxSet", "box_set", "boxSetCandidate", "box_set_candidate", "details"):
        value = payload.get(key)
        if isinstance(value, dict):
            nested = _items(value)
            if nested:
                return nested
    return [payload]


def _first(payload):
    items = _items(payload)
    return items[0] if items and isinstance(items[0], dict) else {}


def _text(value, default=""):
    if value is None:
        value = default
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _first_value(item, *keys):
    if not isinstance(item, dict):
        return ""
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    return ""


def _parse_year(value):
    text = _text(value)
    return text[:4] if len(text) >= 4 and text[:4].isdigit() else ""


def _image_url(value):
    text = _text(value)
    return text if text.startswith(("http://", "https://")) else ""


def _resolve_asset_url(value, context=None):
    """Return an absolute image URL.

    MovieVault serves box-set artwork as root-relative asset paths
    (e.g. ``/api/v1/assets/box_sets/.../poster/...``) so the URL stays portable
    across hosts. Resolve those against the configured MovieVault base URL
    instead of dropping them like :func:`_image_url` does for non-absolute
    values; otherwise a box-set loses its own cover and falls back to a member
    poster.
    """
    text = _text(value)
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        return text
    if text.startswith("/"):
        base = _base_url(context)
        if base:
            return f"{base}{text}"
    return ""


def _is_public_barcode(value):
    text = _text(value)
    return text.isdigit() and len(text) in PUBLIC_BARCODE_LENGTHS


def _movie_payload(item):
    if not isinstance(item, dict):
        return {}
    return {
        "title": _text(item.get("title") or item.get("name")),
        "originalTitle": _text(item.get("originalTitle") or item.get("original_title")),
        "year": _text(item.get("year") or item.get("releaseYear") or item.get("release_year"))[:4],
        "releaseDate": _text(item.get("releaseDate") or item.get("release_date")),
        "overview": _text(item.get("overview") or item.get("plot") or item.get("description")),
        "runtimeMinutes": item.get("runtime") or item.get("runtimeMinutes"),
        "genre": _text(item.get("genre") or item.get("genres")),
        "director": _text(item.get("director") or item.get("directors")),
        "actor": _text(item.get("actor") or item.get("actors") or item.get("cast")),
        "producer": _text(item.get("producer") or item.get("producers")),
        "studios": _text(item.get("studios") or item.get("studio")),
        "format": _text(item.get("format") or item.get("mediaType") or item.get("media_type")),
        "edition": _text(item.get("edition")),
        "country": _text(item.get("country")),
        "language": _text(item.get("language")),
        "rating": _text(item.get("rating") or item.get("imdbRating")),
        "posterUrl": _text(item.get("posterUrl") or item.get("poster_url") or item.get("poster")),
        "backdropUrl": _text(item.get("backdropUrl") or item.get("backdrop_url") or item.get("backdrop")),
        "backdropUrls": item.get("backdropUrls") or item.get("backdrop_urls") or [],
        "trailerUrl": _text(item.get("trailerUrl") or item.get("trailer_url")),
        "videos": item.get("videos") or [],
        "audienceRating": _text(item.get("audienceRating") or item.get("audience_rating")),
    }


_BOX_SET_DIRECT_KEYS = (
    "boxSetProposal",
    "box_set_proposal",
    "boxSet",
    "box_set",
    "boxSetCandidate",
    "box_set_candidate",
)

_BOX_SET_PRIMARY_MEMBER_KEYS = (
    "members",
    "memberMovies",
    "member_movies",
    "memberReleases",
    "member_releases",
    "includedTitles",
    "included_titles",
    "boxSetMovies",
    "box_set_movies",
    "boxSetMembers",
    "box_set_members",
    "bundleMembers",
    "bundle_members",
    "moviesInSet",
    "movies_in_set",
)

_BOX_SET_GENERIC_MEMBER_KEYS = (
    "movies",
    "items",
    "titles",
    "children",
    "parts",
    "discs",
    "discItems",
    "disc_items",
    "contents",
)

# Packaging / format / region noise stripped from a member title before computing
# its dedup identity key. A single film listed under several physical editions
# (e.g. "Fight Club", "Fight Club Blu-ray (Sweden)", "Fight Club 4K UHD") must
# collapse to one member. Mirrors next_metadata._SCANNED_TITLE_NOISE_RE; replicated
# locally because plugins are self-contained and cannot import next_metadata.
_MEMBER_TITLE_NOISE_RE = re.compile(
    r"\b("
    r"4k|uhd|ultra\s*hd|hd|hdr|dolby\s*vision|dv|"
    r"blu[\s-]*ray|bluray|bd|dvd|"
    r"remaster(?:ed)?|steelbook|limited|limit[eé]e?|edition|[eé]dition|"
    r"collector'?s?|special|deluxe|anniversary|"
    r"region\s*[abc012]?|import|disc\s*\d*"
    r")\b",
    re.IGNORECASE,
)


def _has_primary_member_list(item):
    """True when *item* carries an explicit box-set member list.

    Primary keys (members, boxSetMovies, moviesInSet, …) only appear on a payload
    a provider has already classified as a box-set. Generic content arrays (movies,
    items, releases, discs) are NOT a box-set signal — a plain movie legitimately
    carries those — so they are deliberately excluded here.
    """
    if not isinstance(item, dict):
        return False
    return any(isinstance(item.get(key), list) and item.get(key) for key in _BOX_SET_PRIMARY_MEMBER_KEYS)


def _box_set_payload_marker(item):
    if not isinstance(item, dict):
        return False
    if any(isinstance(item.get(key), dict) for key in _BOX_SET_DIRECT_KEYS):
        return True
    if item.get("isBoxSet") is True or item.get("is_box_set") is True:
        return True
    if item.get("detectedWithoutMembers") is True or item.get("detected_without_members") is True:
        return True
    if item.get("memberCount") is not None or item.get("member_count") is not None:
        return True
    if item.get("memberConfidence") or item.get("member_confidence") or item.get("memberSource") or item.get("member_source"):
        return True
    if _first_value(item, "boxSetTitle", "box_set_title", "collectionTitle", "collection_title"):
        return True
    type_text = _text(
        _first_value(
            item,
            "entityType",
            "entity_type",
            "containerType",
            "container_type",
            "entityKind",
            "entity_kind",
            "releaseType",
            "release_type",
            "category",
            "kind",
            "type",
        )
    ).casefold()
    type_key = type_text.replace("-", "_").replace(" ", "_")
    return type_key in {"box_set", "boxset"} or "box_set" in type_key or "boxset" in type_key


def _explicit_box_set_marker(item):
    """Deliberate box-set signals only.

    Unlike :func:`_box_set_payload_marker`, this excludes weak hints (memberCount,
    memberConfidence/source, boxSetTitle/collectionTitle) that a regular single-movie
    payload can legitimately carry. A genuine box-set must declare itself through a
    nested box-set object, an ``isBoxSet`` flag, ``detectedWithoutMembers`` or an
    explicit box-set type/category.
    """
    if not isinstance(item, dict):
        return False
    if any(isinstance(item.get(key), dict) for key in _BOX_SET_DIRECT_KEYS):
        return True
    if item.get("isBoxSet") is True or item.get("is_box_set") is True:
        return True
    if item.get("detectedWithoutMembers") is True or item.get("detected_without_members") is True:
        return True
    type_text = _text(
        _first_value(
            item,
            "entityType",
            "entity_type",
            "containerType",
            "container_type",
            "entityKind",
            "entity_kind",
            "releaseType",
            "release_type",
            "category",
            "kind",
            "type",
        )
    ).casefold().replace("-", "_").replace(" ", "_")
    return "box_set" in type_text or "boxset" in type_text


def _member_list(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in _BOX_SET_DIRECT_KEYS:
        found = _member_list(payload.get(key))
        if found:
            return found
    for key in _BOX_SET_PRIMARY_MEMBER_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            found = _member_list(value)
            if found:
                return found
            nested_items = _items(value)
            if nested_items and nested_items != [value]:
                return nested_items
    has_parent_title = bool(_first_value(payload, "title", "name", "boxSetTitle", "box_set_title"))
    if has_parent_title:
        for key in _BOX_SET_GENERIC_MEMBER_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                found = _member_list(value)
                if found:
                    return found
                nested_items = _items(value)
                if nested_items and nested_items != [value]:
                    return nested_items
    if _box_set_payload_marker(payload):
        for key in _BOX_SET_GENERIC_MEMBER_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                found = _member_list(value)
                if found:
                    return found
                nested_items = _items(value)
                if nested_items and nested_items != [value]:
                    return nested_items
    for key in (
        "proposal",
        "candidate",
        "container",
        "bundle",
        "set",
        "release",
        "data",
        "result",
        "item",
    ):
        nested = payload.get(key)
        found = _member_list(nested)
        if found:
            return found
    return []


def _box_set_signal(item):
    if not isinstance(item, dict):
        return False
    return _box_set_payload_marker(item)


def _box_set_numeric_id(item):
    if not isinstance(item, dict):
        return ""
    value = _first_value(item, "id", "boxSetId", "box_set_id")
    text = _text(value)
    return text if text.isdigit() else ""


def _box_set_entity(payload):
    if not isinstance(payload, dict):
        return {}
    wrapper_keys = (
        "data",
        "result",
        "item",
        "candidate",
        "container",
        "bundle",
        "set",
        "release",
    )
    for key in _BOX_SET_DIRECT_KEYS:
        value = payload.get(key)
        if isinstance(value, dict):
            return _box_set_entity(value) or value
    if _box_set_signal(payload):
        return payload
    has_parent_title = bool(_first_value(payload, "title", "name", "boxSetTitle", "box_set_title"))
    if has_parent_title and any(isinstance(payload.get(key), list) for key in _BOX_SET_PRIMARY_MEMBER_KEYS):
        return payload
    if has_parent_title and any(isinstance(payload.get(key), list) for key in _BOX_SET_GENERIC_MEMBER_KEYS):
        return payload
    for key in wrapper_keys:
        value = payload.get(key)
        if isinstance(value, dict):
            found = _box_set_entity(value)
            if found:
                return found
    return {}


def _with_box_set_detail(context, item):
    if not isinstance(item, dict) or not _box_set_signal(item):
        return item
    if len(_member_list(item)) >= 2:
        return item
    box_set_id = _box_set_numeric_id(item)
    if not box_set_id:
        return item
    merged = dict(item)
    try:
        detail = _get(context or {}, f"/api/v1/box-sets/{quote(box_set_id)}")
        if isinstance(detail, dict):
            merged = {**merged, **detail}
    except Exception as exc:
        merged["memberLookupError"] = str(exc)
    if len(_member_list(merged)) >= 2:
        return merged
    try:
        member_response = _get(context or {}, f"/api/v1/box-sets/{quote(box_set_id)}/members")
        members = _items(member_response)
        if members:
            merged["members"] = members
    except Exception as exc:
        merged["memberLookupError"] = str(exc)
    return merged


def _member_source(item):
    if not isinstance(item, dict):
        return {}
    for key in ("movie", "release", "metadata", "details"):
        value = item.get(key)
        if isinstance(value, dict):
            return {**value, **{k: v for k, v in item.items() if k not in {key}}}
    return item


def _normalize_member(item, index):
    if isinstance(item, str):
        title = _text(item)
        return {"title": title, "sort_order": index, "sortOrder": index} if title else {}
    source = _member_source(item)
    title = _text(_first_value(source, "title", "name", "originalTitle", "original_title"))
    if not title:
        return {}
    movie = _movie_payload(source)
    year = _parse_year(_first_value(source, "year", "releaseYear", "release_year", "releaseDate", "release_date") or movie.get("year"))
    sort_order = _text(_first_value(source, "sortOrder", "sort_order", "discNumber", "disc_number")) or index
    disc_number = _text(_first_value(source, "discNumber", "disc_number", "disc", "diskNumber", "disk_number"))
    poster = _image_url(_first_value(source, "posterUrl", "poster_url", "poster", "coverUrl", "cover_url", "image"))
    backdrop = _image_url(_first_value(source, "backdropUrl", "backdrop_url", "backdrop"))
    member = {
        "title": title,
        "originalTitle": _text(_first_value(source, "originalTitle", "original_title") or movie.get("originalTitle")),
        "original_title": _text(_first_value(source, "original_title", "originalTitle") or movie.get("originalTitle")),
        "year": year,
        "releaseDate": _text(_first_value(source, "releaseDate", "release_date") or movie.get("releaseDate")),
        "release_date": _text(_first_value(source, "release_date", "releaseDate") or movie.get("releaseDate")),
        "tmdbId": _text(_first_value(source, "tmdbId", "tmdb_id")),
        "tmdb_id": _text(_first_value(source, "tmdb_id", "tmdbId")),
        "imdbId": _text(_first_value(source, "imdbId", "imdb_id")),
        "imdb_id": _text(_first_value(source, "imdb_id", "imdbId")),
        "overview": _text(_first_value(source, "overview", "plot", "description") or movie.get("overview")),
        "plot": _text(_first_value(source, "plot", "overview", "description") or movie.get("overview")),
        "runtime": _first_value(source, "runtime", "runtimeMinutes", "runtime_minutes") or movie.get("runtimeMinutes"),
        "format": _text(_first_value(source, "format", "mediaType", "media_type") or movie.get("format")),
        "genre": _text(_first_value(source, "genre", "genres") or movie.get("genre")),
        "director": _text(_first_value(source, "director", "directors") or movie.get("director")),
        "actor": _text(_first_value(source, "actor", "actors", "cast") or movie.get("actor")),
        "poster": poster,
        "posterUrl": poster,
        "poster_url": poster,
        "backdrop": backdrop,
        "backdropUrl": backdrop,
        "backdrop_url": backdrop,
        "backdropUrls": source.get("backdropUrls") or source.get("backdrop_urls") or movie.get("backdropUrls") or [],
        "backdrop_urls": source.get("backdrop_urls") or source.get("backdropUrls") or movie.get("backdropUrls") or [],
        "sortOrder": sort_order,
        "sort_order": sort_order,
        "discNumber": disc_number,
        "disc_number": disc_number,
        "source": _text(source.get("source") or "MovieVault 26"),
        "sourceRef": _text(_first_value(source, "sourceRef", "source_ref", "id", "movieVaultId", "movievault_id")),
    }
    return {key: value for key, value in member.items() if value not in (None, "", [], {})}


def _member_needs_identification(member):
    return not (member.get("tmdbId") or member.get("tmdb_id") or member.get("imdbId") or member.get("imdb_id"))


def _member_identity_keys(member):
    keys = set()
    if not isinstance(member, dict):
        return keys
    tmdb_id = _text(member.get("tmdbId") or member.get("tmdb_id")).casefold()
    imdb_id = _text(member.get("imdbId") or member.get("imdb_id")).casefold()
    if tmdb_id:
        keys.add(("tmdb", tmdb_id))
    if imdb_id:
        keys.add(("imdb", imdb_id))
    title = _text(member.get("title") or member.get("name") or member.get("originalTitle") or member.get("original_title"))
    normalized_title = re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()
    # Collapse physical-edition variants of the same film onto one identity key so
    # e.g. "Fight Club" and "Fight Club Blu-ray (Sweden)" do not survive as two
    # distinct members of a (genuine) box-set.
    denoised_title = _MEMBER_TITLE_NOISE_RE.sub(" ", title.casefold())
    denoised_title = re.sub(r"\(.*?\)", " ", denoised_title)
    denoised_title = re.sub(r"[^a-z0-9]+", " ", denoised_title).strip()
    year = _parse_year(member.get("year") or member.get("releaseYear") or member.get("release_year"))
    identity_title = denoised_title or normalized_title
    if identity_title and year:
        keys.add(("title_year", f"{identity_title}:{year}"))
    elif identity_title:
        keys.add(("title", identity_title))
    return keys


def _public_barcode(value):
    text = _text(value)
    return text if _is_public_barcode(text) else ""


def _format_key(value):
    text = _text(value).casefold().replace("-", " ").replace("_", " ").replace("/", " ")
    text = " ".join(text.split())
    if not text:
        return ""
    if "ultra hd" in text or "uhd" in text or "4k" in text:
        return "ultra_hd_blu_ray"
    if "blu ray" in text or "bluray" in text or text == "bd":
        return "blu_ray"
    if text in {"dvd", "dvd video"}:
        return "dvd"
    if "hd dvd" in text or "hddvd" in text:
        return "hd_dvd"
    if "laserdisc" in text or "laser disc" in text:
        return "laserdisc"
    if "svcd" in text or "vcd" in text:
        return "vcd_svcd"
    return text


def _selected_format(context=None, item=None):
    context = context or {}
    item = item or {}
    return _text(
        _first_value(
            {**item, **context},
            "selectedFormat",
            "selected_format",
            "format",
            "mediaType",
            "media_type",
            "editionFormat",
            "edition_format",
        )
    )


def _compatible_format(candidate, expected):
    candidate_key = _format_key(candidate)
    expected_key = _format_key(expected)
    return not candidate_key or not expected_key or candidate_key == expected_key


def _movievault_context(context):
    return (context or {}).get("movievault") or {}


def _movievault_enabled(context):
    value = _movievault_context(context).get("enabled", True)
    if isinstance(value, bool):
        return value
    return _text(value, "true").lower() in {"1", "true", "yes", "on"}


def _contribution_enabled(context):
    value = _movievault_context(context).get("contributionEnabled")
    if value is None:
        value = _settings(context).get("contributionEnabled") or os.environ.get("MOVIEVAULT_CONTRIBUTION_ENABLED")
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _sharing_mode(context):
    return _text(
        _movievault_context(context).get("sharingMode")
        or _settings(context).get("sharingMode")
        or os.environ.get("MOVIEVAULT_SHARING_MODE")
        or "opt_in"
    )


def _source_version(context):
    return _text(_movievault_context(context).get("sourceVersion") or _settings(context).get("sourceVersion") or "")


def _merge_member_enrichment(member, enrichment, expected_format=""):
    proposal = enrichment.get("proposal") if isinstance(enrichment, dict) else {}
    proposal = proposal if isinstance(proposal, dict) else {}
    movie_updates = proposal.get("movieUpdates") or {}
    metadata_updates = proposal.get("metadataUpdates") or {}
    media_updates = proposal.get("mediaUpdates") or {}
    identifiers = proposal.get("identifiers") or {}
    enriched = dict(member)

    mappings = {
        "title": ("title",),
        "original_title": ("original_title", "originalTitle"),
        "originalTitle": ("original_title", "originalTitle"),
        "year": ("year",),
        "release_date": ("release_date", "releaseDate"),
        "releaseDate": ("release_date", "releaseDate"),
        "overview": ("overview", "plot"),
        "plot": ("overview", "plot"),
        "runtime": ("runtime_minutes", "runtimeMinutes", "runtime"),
        "format": ("format",),
        "genre": ("genre",),
        "director": ("director",),
        "actor": ("actor",),
    }
    for target, keys in mappings.items():
        if enriched.get(target):
            continue
        for key in keys:
            value = movie_updates.get(key) or metadata_updates.get(key)
            if target == "format" and expected_format and not _compatible_format(value, expected_format):
                continue
            if value not in (None, "", [], {}):
                enriched[target] = value
                break
    if expected_format and not enriched.get("format"):
        enriched["format"] = expected_format

    if identifiers.get("tmdb") and not (enriched.get("tmdbId") or enriched.get("tmdb_id")):
        enriched["tmdbId"] = str(identifiers["tmdb"])
        enriched["tmdb_id"] = str(identifiers["tmdb"])
    if identifiers.get("imdb") and not (enriched.get("imdbId") or enriched.get("imdb_id")):
        enriched["imdbId"] = str(identifiers["imdb"])
        enriched["imdb_id"] = str(identifiers["imdb"])

    poster = metadata_updates.get("poster_url") or (media_updates.get("poster") or {}).get("sourceUrl")
    if poster and not (enriched.get("poster") or enriched.get("posterUrl") or enriched.get("poster_url")):
        enriched["poster"] = poster
        enriched["posterUrl"] = poster
        enriched["poster_url"] = poster
    backdrop = metadata_updates.get("backdrop_url") or (media_updates.get("backdrop") or {}).get("sourceUrl")
    if backdrop and not (enriched.get("backdrop") or enriched.get("backdropUrl") or enriched.get("backdrop_url")):
        enriched["backdrop"] = backdrop
        enriched["backdropUrl"] = backdrop
        enriched["backdrop_url"] = backdrop

    sources = []
    for item in enrichment.get("sourceSummary") or []:
        if item.get("state") in {"applied", "hit"}:
            sources.append(item.get("pluginId"))
    if sources:
        enriched["identifiedBy"] = sources
        enriched["memberConfidence"] = "identified_by_metadata_plugins"
    return {key: value for key, value in enriched.items() if value not in (None, "", [], {})}


def _identify_member_with_other_plugins(member, context):
    lookup = (context or {}).get("metadataLookup")
    if not callable(lookup):
        return member, None
    query = {
        "title": member.get("title") or member.get("originalTitle") or member.get("original_title") or "",
        "year": member.get("year") or "",
        "tmdbId": member.get("tmdbId") or member.get("tmdb_id") or "",
        "imdbId": member.get("imdbId") or member.get("imdb_id") or "",
        "format": member.get("format") or (context or {}).get("format") or "",
    }
    if not query["title"] and not query["tmdbId"] and not query["imdbId"]:
        return member, None
    try:
        enrichment = lookup(query, excludeProviders=["movievault", "movievault_26"])
    except Exception as exc:  # Fallback discovery should never make MovieVault 26 unusable.
        return {**member, "identificationWarning": str(exc)}, None
    expected_format = member.get("format") or (context or {}).get("selectedFormat") or (context or {}).get("format") or ""
    return _merge_member_enrichment(member, enrichment or {}, expected_format), enrichment


def _box_set_evidence(proposal, context=None):
    context = context or {}
    members = _member_list(proposal)
    source_ref = _text(context.get("sourceRef") or proposal.get("sourceRef") or proposal.get("movievault_id") or proposal.get("movieVaultId") or proposal.get("id"))
    input_barcode = _text(context.get("barcode"))
    proposal_barcode = _text(_first_value(proposal, "barcode", "ean", "upc"))
    barcode_match = bool(input_barcode and proposal_barcode and input_barcode == proposal_barcode)
    if not barcode_match and source_ref.startswith("barcode:"):
        barcode_match = bool(proposal_barcode and source_ref.split(":", 1)[1] == proposal_barcode)
    members_are_explicit = bool(members)
    return {
        "barcodeMatch": barcode_match,
        "entityType": "box_set",
        "memberSource": _text(proposal.get("memberSource") or proposal.get("member_source")) or "MovieVault 26",
        "memberConfidence": "identified" if members and all(not _member_needs_identification(m) for m in members) else "needs_member_confirmation" if members else "needs_member_confirmation",
        "memberCount": len(members),
        "membersAreExplicit": members_are_explicit,
        "detectedWithoutMembers": not members,
        "format": _text(proposal.get("format")),
        "sourceRef": source_ref,
    }


def _normalize_box_set_proposal(payload, context=None, *, require_explicit=True):
    item = _box_set_entity(payload) if isinstance(payload, dict) else {}
    if not item:
        item = _first(payload)
    if not item:
        return {}
    raw_members = _member_list(item)
    nested = (
        item.get("boxSetProposal")
        or item.get("box_set_proposal")
        or item.get("boxSet")
        or item.get("box_set")
        or item.get("boxSetCandidate")
        or item.get("box_set_candidate")
        or item.get("container")
        or item.get("bundle")
        or item.get("set")
        or item.get("candidate")
    )
    if isinstance(nested, dict) and (not raw_members or not _first_value(item, "title", "name", "boxSetTitle", "box_set_title")):
        nested_members = _member_list(nested)
        if nested_members or _box_set_signal(nested):
            item = nested
            raw_members = nested_members
    if not raw_members and not _box_set_signal(item):
        return {}

    title = _text(_first_value(item, "title", "name", "boxSetTitle", "box_set_title"))
    selected_format = _selected_format(context, item)
    members = []
    lookup_summaries = []
    seen = set()
    for index, raw_member in enumerate(raw_members[:50], start=1):
        member = _normalize_member(raw_member, index)
        if not member:
            continue
        if selected_format and not member.get("format"):
            member["format"] = selected_format
        if selected_format and member.get("format") and not _compatible_format(member.get("format"), selected_format):
            member["format"] = selected_format
        if _member_needs_identification(member):
            member, enrichment = _identify_member_with_other_plugins(member, {**(context or {}), "selectedFormat": selected_format})
            if isinstance(enrichment, dict):
                lookup_summaries.append(
                    {
                        "member": member.get("title"),
                        "sourceOrder": enrichment.get("sourceOrder") or [],
                        "proposalStats": enrichment.get("proposalStats") or {},
                    }
                )
        keys = _member_identity_keys(member)
        if keys and seen.intersection(keys):
            continue
        if keys:
            seen.update(keys)
        members.append(member)

    # A box-set proposal is only ever emitted on an explicit provider signal: a
    # deliberate box-set marker (nested boxSet object, entityType/type=box_set,
    # isBoxSet, detectedWithoutMembers) OR a primary member list (members,
    # boxSetMovies, moviesInSet, …). A movie's own generic content arrays
    # (releases/discs/items) must NEVER be inferred as a box-set. The authoritative
    # /api/v1/box-sets endpoint passes require_explicit=False, since its items are
    # box-sets by definition even when the inline payload lacks a marker.
    has_explicit_signal = _explicit_box_set_marker(item) or _has_primary_member_list(item)
    if require_explicit and not has_explicit_signal:
        return {}
    if len(members) < 2 and not has_explicit_signal:
        return {}

    if not title and members:
        title = _text(item.get("boxSetTitle") or item.get("collectionTitle") or item.get("name"))
    if not title:
        return {}

    box_set_poster = _resolve_asset_url(_first_value(item, "posterUrl", "poster_url", "poster", "image"), context)
    box_set_backdrop = _resolve_asset_url(_first_value(item, "backdropUrl", "backdrop_url", "backdrop"), context)
    proposal = {
        "title": title,
        "name": title,
        "source": "MovieVault 26",
        "provider": "movievault_26",
        "movievault_id": _text(_first_value(item, "movieVaultId", "movievaultId", "movievault_id", "id")),
        "barcode": _text(_first_value(item, "barcode", "ean", "upc")),
        "year": _parse_year(_first_value(item, "year", "releaseYear", "release_year")),
        "year_range": _text(_first_value(item, "yearRange", "year_range")),
        "format": selected_format or _text(_first_value(item, "format", "mediaType", "media_type")),
        "poster": box_set_poster,
        "poster_url": box_set_poster,
        "posterUrl": box_set_poster,
        "backdrop": box_set_backdrop,
        "backdrop_url": box_set_backdrop,
        "backdrop_urls": item.get("backdrop_urls") or item.get("backdropUrls") or [],
        "movies": members,
        "members": members,
        "member_count": len(members),
        "memberCount": len(members),
        "member_source": "MovieVault 26",
        "memberSource": "MovieVault 26",
        "member_confidence": "identified" if members and all(not _member_needs_identification(m) for m in members) else "needs_member_confirmation",
        "memberConfidence": "identified" if members and all(not _member_needs_identification(m) for m in members) else "needs_member_confirmation",
        "metadata_plugin_fallbacks": lookup_summaries,
    }
    if not members:
        proposal["detectedWithoutMembers"] = True
        proposal["detected_without_members"] = True
        proposal["member_confidence"] = "needs_member_confirmation"
        proposal["memberConfidence"] = "needs_member_confirmation"
    if lookup_summaries:
        proposal["member_source"] = "MovieVault 26 + metadata plugins"
        proposal["memberSource"] = "MovieVault 26 + metadata plugins"
    proposal["boxSetEvidence"] = _box_set_evidence(proposal, context)
    proposal["box_set_evidence"] = proposal["boxSetEvidence"]
    proposal["membersAreExplicit"] = proposal["boxSetEvidence"]["membersAreExplicit"]
    proposal["members_are_explicit"] = proposal["boxSetEvidence"]["membersAreExplicit"]
    return {key: value for key, value in proposal.items() if value not in (None, "", [], {})}


def _container_type(payload):
    raw = _text(
        _first_value(
            payload if isinstance(payload, dict) else {},
            "containerType",
            "container_type",
            "entityType",
            "entity_type",
            "type",
        )
    ).casefold()
    normalized = raw.replace("-", "_").replace(" ", "_")
    if normalized in {"boxset", "box_set"} or "box_set" in normalized or "boxset" in normalized:
        return "box_set"
    if normalized == "vault" or "vault" in normalized:
        return "vault"
    if normalized == "collection" or "collection" in normalized:
        return "collection"
    return normalized or "container"


def _container_members(payload, context=None):
    members = []
    seen = set()
    selected_format = _selected_format(context or {}, payload if isinstance(payload, dict) else {})
    for index, raw_member in enumerate(_member_list(payload)[:100], start=1):
        member = _normalize_member(raw_member, index)
        if not member:
            continue
        if selected_format and not member.get("format"):
            member["format"] = selected_format
        keys = _member_identity_keys(member)
        if keys and seen.intersection(keys):
            continue
        if keys:
            seen.update(keys)
        members.append(member)
    return members


def prepare_barcode_update(payload, context=None):
    payload = payload if isinstance(payload, dict) else {}
    barcode = _public_barcode(payload.get("barcode") or payload.get("newBarcode") or payload.get("new_barcode"))
    if not barcode:
        return {"status": "skipped", "provider": PROVIDER_ID, "reason": "not_public_barcode"}
    entity_type = _text(payload.get("entityType") or payload.get("entity_type") or "release")
    identity = _text(payload.get("identity") or payload.get("id") or payload.get("movievaultId") or payload.get("movievault_id") or barcode)
    reference = _public_reference(_source_reference(payload) or payload)
    update_payload = {"barcode": barcode}
    return {
        "status": "ok",
        "provider": PROVIDER_ID,
        "operation": "barcode_update",
        "entityType": entity_type,
        "identity": identity,
        "sourceReference": reference,
        "payload": update_payload,
        "contribution": {
            "entityType": entity_type,
            "identity": identity,
            "sourceReference": reference,
            "payload": update_payload,
            "metadata": {"changedFields": ["barcode"], "sourceProviders": ["discvault"]},
        },
    }


def prepare_container_update(payload, context=None):
    payload = payload if isinstance(payload, dict) else {}
    container = payload.get("container") if isinstance(payload.get("container"), dict) else payload
    container_type = _container_type(container)
    title = _text(_first_value(container, "title", "name", "boxSetTitle", "box_set_title"))
    barcode = _public_barcode(_first_value(container, "barcode", "ean", "upc"))
    members = _container_members(container, context)
    update_payload = {
        "title": title,
        "barcode": barcode,
        "format": _text(_first_value(container, "format", "mediaType", "media_type")),
        "posterUrl": _image_url(_first_value(container, "posterUrl", "poster_url", "poster", "image")),
        "backdropUrl": _image_url(_first_value(container, "backdropUrl", "backdrop_url", "backdrop")),
    }
    if members:
        update_payload["members"] = members
        update_payload["boxSetMovies"] = members
        update_payload["memberCount"] = len(members)
    update_payload = _safe_contribution_value(update_payload)
    update_payload = {key: value for key, value in update_payload.items() if value not in (None, "", [], {})}
    identity = _text(
        payload.get("identity")
        or _first_value(container, "movieVaultId", "movievault_id", "id", "publicId", "public_id")
        or barcode
        or title
    )
    reference = _public_reference(_source_reference(payload) or container)
    return {
        "status": "ok" if update_payload else "skipped",
        "provider": PROVIDER_ID,
        "operation": "container_update",
        "entityType": container_type,
        "identity": identity,
        "sourceReference": reference,
        "payload": update_payload,
        "memberIntelligence": {
            "memberCount": len(members),
            "membersIdentified": sum(1 for member in members if not _member_needs_identification(member)),
            "membersNeedingConfirmation": sum(1 for member in members if _member_needs_identification(member)),
            "memberSource": PROVIDER_LABEL,
        },
        "contribution": {
            "entityType": "box_set" if container_type == "box_set" else "release",
            "identity": identity,
            "sourceReference": reference,
            "payload": update_payload,
            "metadata": {
                "changedFields": sorted(update_payload.keys()),
                "sourceProviders": ["discvault"],
            },
        },
    }


def member_intelligence(payload, context=None):
    payload = payload if isinstance(payload, dict) else {}
    proposal = _normalize_box_set_proposal(payload, context or {}) or payload
    members = _container_members(proposal, context)
    return {
        "status": "ok",
        "provider": PROVIDER_ID,
        "memberCount": len(members),
        "members": members,
        "membersIdentified": sum(1 for member in members if not _member_needs_identification(member)),
        "membersNeedingConfirmation": sum(1 for member in members if _member_needs_identification(member)),
        "memberConfidence": "identified" if members and all(not _member_needs_identification(member) for member in members) else "needs_member_confirmation",
        "memberSource": PROVIDER_LABEL,
    }


def _technical_payload(item):
    if not isinstance(item, dict):
        return {}
    return {
        "format": _text(item.get("format") or item.get("mediaType") or item.get("media_type")),
        "hdr": _text(item.get("hdr") or item.get("hdrFormat") or item.get("hdr_format")),
        "packaging": _text(item.get("packaging")),
        "screenRatios": _text(item.get("screenRatios") or item.get("screen_ratios")),
        "audioTracks": item.get("audioTracks") or item.get("audio_tracks") or [],
        "subtitles": item.get("subtitles") or [],
        "regions": item.get("regions") or [],
        "contentRatings": item.get("contentRatings") or item.get("content_ratings") or {},
    }


def _candidate_payload(item, movie, *, source_ref=""):
    if not isinstance(item, dict):
        item = {}
    movie = movie if isinstance(movie, dict) else {}
    title = _text(movie.get("title") or item.get("title") or item.get("name"))
    if not title:
        return {}
    poster = _image_url(
        _first_value(item, "posterUrl", "poster_url", "poster", "coverUrl", "cover_url", "image")
        or movie.get("posterUrl")
    )
    backdrop = _image_url(
        _first_value(item, "backdropUrl", "backdrop_url", "backdrop")
        or movie.get("backdropUrl")
    )
    candidate = {
        "provider": PROVIDER_ID,
        "providerId": PROVIDER_ID,
        "providerLabel": PROVIDER_LABEL,
        "source": PROVIDER_LABEL,
        "sourceRef": source_ref or _text(_first_value(item, "id", "movieVaultId", "movievaultId", "movievault_id")),
        "id": _text(_first_value(item, "id", "movieVaultId", "movievaultId", "movievault_id")),
        "movieVaultId": _text(_first_value(item, "movieVaultId", "movievaultId", "movievault_id", "id")),
        "title": title,
        "originalTitle": _text(movie.get("originalTitle") or item.get("originalTitle") or item.get("original_title")),
        "year": _text(movie.get("year") or item.get("year") or item.get("releaseYear") or item.get("release_year"))[:4],
        "format": _text(movie.get("format") or item.get("format") or item.get("mediaType") or item.get("media_type")),
        "barcode": _text(_first_value(item, "barcode", "ean", "upc")),
        "posterUrl": poster,
        "poster_url": poster,
        "backdropUrl": backdrop,
        "backdrop_url": backdrop,
        "tmdbId": _text(_first_value(item, "tmdbId", "tmdb_id")),
        "imdbId": _text(_first_value(item, "imdbId", "imdb_id")),
        "movie": movie,
    }
    return {key: value for key, value in candidate.items() if value not in (None, "", [], {})}


def _normalization_sources(payload):
    sources = []

    def add_source(value):
        if isinstance(value, dict):
            sources.append(value)

    add_source(payload)
    if isinstance(payload, dict):
        for key in (
            "data",
            "result",
            "item",
            "movie",
            "details",
            "release",
            "boxSetProposal",
            "box_set_proposal",
            "boxSet",
            "box_set",
            "boxSetCandidate",
            "box_set_candidate",
            "container",
            "bundle",
            "set",
        ):
            add_source(payload.get(key))
        if _box_set_entity(payload):
            unique = []
            seen = set()
            for item in sources:
                marker = id(item)
                if marker in seen:
                    continue
                seen.add(marker)
                unique.append(item)
            return unique
    for item in _items(payload):
        if isinstance(item, dict):
            sources.append(item)
    unique = []
    seen = set()
    for item in sources:
        marker = id(item)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(item)
    return unique


def _candidate_key(candidate):
    return "::".join(
        [
            _text(candidate.get("id") or candidate.get("movieVaultId") or candidate.get("sourceRef")).casefold(),
            _text(candidate.get("title")).casefold(),
            _text(candidate.get("year")),
            _text(candidate.get("barcode")),
        ]
    )


def _box_set_proposal_key(proposal):
    members = _member_list(proposal)
    return "::".join(
        [
            _text(proposal.get("movievault_id") or proposal.get("movieVaultId") or proposal.get("id") or proposal.get("sourceRef")).casefold(),
            _text(proposal.get("title") or proposal.get("name")).casefold(),
            _text(proposal.get("barcode")),
            str(len(members)),
        ]
    )


def _ingest_localizations(item):
    if not isinstance(item, dict):
        return []
    raw = (
        item.get("localizations")
        or item.get("localisations")
        or item.get("translations")
    )
    if isinstance(raw, dict):
        expanded = []
        for lang_key, value in raw.items():
            if isinstance(value, dict):
                entry = dict(value)
                entry.setdefault("lang", lang_key)
                expanded.append(entry)
        raw = expanded
    if not isinstance(raw, list):
        return []
    rows = []
    seen = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        lang = _localized_language_code(
            entry.get("lang")
            or entry.get("language")
            or entry.get("locale")
            or entry.get("iso_639_1")
        )
        if not lang or lang in seen:
            continue
        if not (lang.isalpha() and len(lang) in (2, 3)):
            continue
        title = _text(entry.get("title") or entry.get("name"))
        original_title = _text(entry.get("originalTitle") or entry.get("original_title"))
        overview = _text(entry.get("overview") or entry.get("plot") or entry.get("description"))
        edition = _text(entry.get("edition"))
        if not (title or original_title or overview or edition):
            continue
        seen.add(lang)
        row = {"lang": lang, "source": "movievault_26"}
        if title:
            row["title"] = title
        if original_title:
            row["originalTitle"] = original_title
        if overview:
            row["overview"] = overview
        if edition:
            row["edition"] = edition
        rows.append(row)
    return rows


def _normalize_result(payload, *, source_ref=""):
    sources = _normalization_sources(payload)
    if not sources:
        return {"status": "miss", "provider": "movievault_26"}
    candidates = []
    seen_candidates = set()
    proposals = []
    seen_proposals = set()
    first_movie = {}
    first_item = {}

    for item in sources:
        direct_box_set = (
            item.get("boxSetProposal")
            or item.get("box_set_proposal")
            or item.get("box_set")
            or item.get("boxSet")
            or item.get("boxSetCandidate")
            or item.get("box_set_candidate")
            or item.get("container")
        )
        box_set_proposal = (
            _normalize_box_set_proposal(item, {"sourceRef": source_ref, "barcode": source_ref.split(":", 1)[1] if source_ref.startswith("barcode:") else ""})
            or _normalize_box_set_proposal(direct_box_set, {"sourceRef": source_ref, "barcode": source_ref.split(":", 1)[1] if source_ref.startswith("barcode:") else ""})
            or (direct_box_set if isinstance(direct_box_set, dict) else {})
        )
        if isinstance(box_set_proposal, dict) and box_set_proposal:
            key = _box_set_proposal_key(box_set_proposal)
            if key not in seen_proposals:
                seen_proposals.add(key)
                proposals.append(box_set_proposal)

        if _box_set_signal(item):
            continue
        movie = _movie_payload(item)
        if not movie.get("title"):
            continue
        candidate = _candidate_payload(item, movie, source_ref=source_ref)
        if not candidate:
            continue
        key = _candidate_key(candidate)
        if key in seen_candidates:
            continue
        seen_candidates.add(key)
        candidates.append(candidate)
        if not first_movie:
            first_movie = movie
            first_item = item

    if not candidates and not proposals:
        return {"status": "miss", "provider": "movievault_26"}

    source_item = first_item or (sources[0] if sources else {})
    localizations = []
    for candidate_source in ([source_item] + list(sources)):
        localizations = _ingest_localizations(candidate_source)
        if localizations:
            break
    result = {
        "status": "hit",
        "provider": "movievault_26",
        "sourceLabel": "MovieVault 26",
        "sourceRef": source_ref or _text(source_item.get("id") or source_item.get("movieVaultId") or source_item.get("movievault_id")),
        "movie": first_movie,
        "technicalSpecs": _technical_payload(source_item),
        "tmdbId": _text(source_item.get("tmdbId") or source_item.get("tmdb_id")),
        "imdbId": _text(source_item.get("imdbId") or source_item.get("imdb_id")),
        "items": candidates,
        "candidates": candidates,
    }
    if localizations:
        result["localizations"] = localizations
    if proposals:
        result["boxSetProposal"] = proposals[0]
        result["boxSetProposals"] = proposals
    return result


def health_check(context=None):
    context = context or {}
    connection = context.get("movievault") or {}
    try:
        response = requests.get(f"{_base_url(context)}/api/v1/health", headers={"Accept": "application/json"}, timeout=8)
        status = "available" if response.status_code < 500 else "unavailable"
        if connection.get("error"):
            status = "connection_error"
        elif connection.get("requiresReset"):
            status = "reset_required"
        elif not connection.get("tokenSet") and connection.get("enabled", True):
            status = "needs_connection"
        elif connection.get("linkStatus") == "revoked":
            status = "revoked"
        elif connection.get("linkStatus") == "disabled":
            status = "disabled"
        return {
            "status": status,
            "message": f"HTTP {response.status_code}",
            "connection": {
                "authMethod": connection.get("authMethod"),
                "instanceId": connection.get("instanceId"),
                "instanceName": connection.get("instanceName"),
                "keyId": connection.get("keyId"),
                "lastBootstrapAt": connection.get("lastBootstrapAt"),
                "lastHandshakeAt": connection.get("lastHandshakeAt"),
                "linkStatus": connection.get("linkStatus"),
                "requiresReset": bool(connection.get("requiresReset")),
                "scopes": connection.get("scopes") or [],
                "sharingMode": connection.get("sharingMode"),
                "tokenPrefix": connection.get("tokenPrefix"),
                "tokenSet": bool(connection.get("tokenSet")),
            },
        }
    except Exception as exc:
        return {"status": "unavailable", "message": str(exc)}


def _matched_type(data):
    """Return MovieVault's barcode match discriminator (lowercased).

    The contract response is ``{"type": "release"|"box_set"|"movie", "lookup":
    {"matchedType": ...}, ...}``. Older / mocked shapes omit it, in which case
    this returns ``""`` and the caller falls back to generic normalization.
    """
    if not isinstance(data, dict):
        return ""
    lookup = data.get("lookup")
    if isinstance(lookup, dict):
        matched = _text(lookup.get("matchedType") or lookup.get("matched_type"))
        if matched:
            return matched.casefold()
    return _text(data.get("type")).casefold()


def _scanned_release_from_movie(movie, barcode):
    """Find the release in ``movie.releases`` matching the scanned barcode."""
    if not isinstance(movie, dict):
        return {}
    wanted = re.sub(r"\D", "", _text(barcode))
    releases = movie.get("releases")
    candidates = [r for r in releases if isinstance(r, dict)] if isinstance(releases, list) else []
    single = movie.get("release")
    if isinstance(single, dict):
        candidates.insert(0, single)
    if wanted:
        for release in candidates:
            for key in ("barcode", "ean", "upc", "normalizedBarcode", "normalized_barcode"):
                if re.sub(r"\D", "", _text(release.get(key))) == wanted:
                    return release
    return candidates[0] if candidates else {}


def _release_candidate_payload(movie, release, barcode):
    """Build a single movie-shaped payload for the scanned disc.

    A barcode identifies exactly one physical release. MovieVault copies the
    *first* release's spec (often a different edition such as 4K UHD) onto the
    movie level, so the candidate must take its format/edition/country/region from
    the actually-scanned ``release`` object, while keeping the movie's canonical
    title/overview/year. The ``releases``/``release`` arrays are dropped so they
    are never re-read as box-set members or as extra candidates.
    """
    movie = movie if isinstance(movie, dict) else {}
    release = release if isinstance(release, dict) else {}
    base = dict(movie) if _text(movie.get("title")) else {}
    if not base and release:
        base = {key: value for key, value in release.items() if key not in ("releases", "release")}
    base.pop("releases", None)
    base.pop("release", None)
    if release:
        for key in (
            "format",
            "edition",
            "country",
            "language",
            "regions",
            "hdr",
            "audioTracks",
            "subtitles",
            "technicalSpecs",
            "distributor",
            "barcode",
        ):
            value = release.get(key)
            if value not in (None, "", [], {}):
                base[key] = value
        release_title = _text(release.get("title"))
        if release_title:
            base.setdefault("releaseTitle", release_title)
            base.setdefault("release_title", release_title)
    if not _text(base.get("barcode")):
        base["barcode"] = barcode
    return base


def search_barcode(payload, context=None):
    barcode = str((payload or {}).get("barcode") or "").strip()
    if not _movievault_enabled(context):
        return {"status": "skipped", "provider": PROVIDER_ID, "reason": "disabled"}
    if not _is_public_barcode(barcode):
        return {"status": "skipped", "provider": PROVIDER_ID, "reason": "not_public_barcode"}
    data = _get(context or {}, f"/api/v1/barcodes/{quote(barcode)}")
    # A barcode that resolves to a specific release must surface exactly one
    # candidate — the scanned disc — never a synthesized set and never the
    # movie-level (first-release) format.
    if _matched_type(data) == "release" and isinstance(data, dict):
        release = data.get("release") if isinstance(data.get("release"), dict) else {}
        movie = data.get("movie") if isinstance(data.get("movie"), dict) else {}
        if not release:
            release = _scanned_release_from_movie(movie, barcode)
        merged = _release_candidate_payload(movie, release, barcode)
        if _text(merged.get("title")):
            return _normalize_result(merged, source_ref=f"barcode:{barcode}")
    box_set_entity = _box_set_entity(data)
    if box_set_entity and len(_member_list(box_set_entity)) < 2:
        detailed = _with_box_set_detail(context or {}, box_set_entity)
        if isinstance(data, dict) and data.get("data") is box_set_entity:
            data = {**data, "data": detailed}
        else:
            data = detailed
    elif _box_set_signal(data) and len(_member_list(data)) < 2:
        data = _with_box_set_detail(context or {}, data)
    return _normalize_result(data, source_ref=f"barcode:{barcode}")


def search_title(payload, context=None):
    title = str((payload or {}).get("title") or "").strip()
    year = str((payload or {}).get("year") or "").strip()
    if not _movievault_enabled(context):
        return {"status": "skipped", "provider": PROVIDER_ID, "items": [], "reason": "disabled"}
    if not title:
        return {"status": "skipped", "provider": PROVIDER_ID, "items": []}
    data = _get(context or {}, "/api/v1/movies", q=title, year=year)
    items = []
    for item in _items(data)[:8]:
        movie = _movie_payload(item)
        if movie.get("title"):
            items.append(
                {
                    "provider": "movievault_26",
                    "providerLabel": "MovieVault 26",
                    "id": _text(item.get("id") or item.get("movieVaultId") or item.get("movievault_id")),
                    "title": movie.get("title"),
                    "year": movie.get("year"),
                    "posterUrl": movie.get("posterUrl"),
                    "movie": movie,
                }
            )
    return {"status": "hit" if items else "miss", "provider": "movievault_26", "items": items}


def movie_details(payload, context=None):
    barcode = str((payload or {}).get("barcode") or "").strip()
    title = str((payload or {}).get("title") or "").strip()
    year = str((payload or {}).get("year") or "").strip()
    if _is_public_barcode(barcode):
        result = search_barcode(payload, context)
        if result.get("status") == "hit":
            return result
    if not title:
        return {"status": "skipped", "provider": PROVIDER_ID}
    return _normalize_result(_get(context or {}, "/api/v1/movies", q=title, year=year), source_ref=f"title:{title}")


def box_set_candidates(payload, context=None):
    payload = payload or {}
    title = str((payload or {}).get("title") or "").strip()
    year = str((payload or {}).get("year") or "").strip()
    barcode = str((payload or {}).get("barcode") or "").strip()
    if not _movievault_enabled(context):
        return {"status": "skipped", "provider": PROVIDER_ID, "boxSetProposal": {}, "reason": "disabled"}
    proposal_context = {
        **(context or {}),
        "format": payload.get("format") or payload.get("mediaType") or payload.get("media_type") or "",
        "barcode": barcode if _is_public_barcode(barcode) else "",
    }
    data = _get(context or {}, "/api/v1/box-sets", q=title, year=year, barcode=barcode if _is_public_barcode(barcode) else "")
    sources = [item for item in _items(data) if isinstance(item, dict)]
    if isinstance(data, dict):
        sources.insert(0, data)
    proposals = []
    seen = set()
    for source in sources:
        candidate = _with_box_set_detail(context or {}, source)
        proposal = _normalize_box_set_proposal(candidate, {**proposal_context, "sourceRef": _text(_first_value(candidate, "id", "movieVaultId", "movievaultId", "movievault_id"))}, require_explicit=False)
        if not proposal:
            continue
        key = _box_set_proposal_key(proposal)
        if key in seen:
            continue
        seen.add(key)
        proposals.append(proposal)
    addable = [proposal for proposal in proposals if len(proposal.get("movies") or proposal.get("members") or []) >= 2]
    if not addable and not proposals:
        return {"status": "miss", "provider": "movievault_26", "boxSetProposal": {}}
    selected = addable[0] if addable else proposals[0]
    return {
        "status": "hit",
        "provider": "movievault_26",
        "sourceLabel": "MovieVault 26",
        "sourceRef": selected.get("movievault_id") or selected.get("barcode") or title,
        "boxSetProposal": selected,
        "boxSetProposals": proposals,
    }


def _person_results(payload):
    """Normalize the people read-API response into a list of person objects.

    ``/api/v1/people`` returns ``{"results": [...]}`` while ``/api/v1/people/{id}``
    returns a single object (or a ``{"error": ...}`` envelope on 404).
    """
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        results = payload.get("results")
        if isinstance(results, list):
            return [item for item in results if isinstance(item, dict)]
        if payload.get("error"):
            return []
        if payload.get("id") or payload.get("movieVaultId") or payload.get("movievault_id") or payload.get("name"):
            return [payload]
    return []


def _person_localizations(item):
    """Lift ``biography_<iso639-1>`` keys into localization rows."""
    rows = []
    seen = set()
    for key, value in (item or {}).items():
        if not isinstance(key, str):
            continue
        lowered = key.lower()
        if not lowered.startswith("biography_"):
            continue
        lang = key[len("biography_"):].strip()
        biography = _text(value)
        if not lang or not biography:
            continue
        norm = lang.lower()
        if norm in seen:
            continue
        seen.add(norm)
        rows.append({"lang": lang, "biography": biography, "source": PROVIDER_ID})
    return rows


def _person_profiles(item):
    """Public https-only, de-duplicated profile image URLs (cap 12)."""
    urls = []
    seen = set()
    for value in (item.get("profiles") or []):
        text = _text(value)
        if not text.lower().startswith("https://"):
            continue
        if text in seen:
            continue
        seen.add(text)
        urls.append(text)
    return urls[:12]


def _person_award_year(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = _text(value)
    return int(text) if text.isdigit() else None


def _person_award_tmdb_id(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = _text(value)
    return int(text) if text.isdigit() else None


def _person_awards(item):
    """Normalize MovieVault person awards to the shared award schema."""
    awards = []
    for entry in (item.get("awards") or []):
        if not isinstance(entry, dict):
            continue
        award = _text(entry.get("award"))
        award_qid = _text(entry.get("awardWikidataId") or entry.get("award_wikidata_id"))
        if not award and not award_qid:
            continue
        result = _text(entry.get("result")).lower()
        awards.append(
            {
                "award": award or award_qid,
                "awardWikidataId": award_qid,
                "category": _text(entry.get("category")),
                "year": _person_award_year(entry.get("year")),
                "work": _text(entry.get("work")),
                "workWikidataId": _text(entry.get("workWikidataId") or entry.get("work_wikidata_id")),
                "workTmdbId": _person_award_tmdb_id(entry.get("workTmdbId") or entry.get("work_tmdb_id")),
                "result": "won" if result == "won" else "nominated",
                "source": _text(entry.get("source")) or PROVIDER_ID,
                "sourceRef": _text(entry.get("sourceRef") or entry.get("source_ref")),
            }
        )
    return awards


def _person_payload(item, *, language="", source_ref=""):
    name = _text(item.get("name"))
    if not name:
        return {"status": "miss", "provider": PROVIDER_ID, "reason": "not_found"}
    localizations = _person_localizations(item)
    configured = str(language or "").strip().lower()
    bios_by_lang = {row["lang"].lower(): row["biography"] for row in localizations}
    biography = _text(item.get("biography"))
    preferred = bios_by_lang.get(configured) or next(
        (bio for lang_key, bio in bios_by_lang.items() if lang_key.split("-")[0] == configured.split("-")[0]),
        "",
    )
    if preferred:
        biography = preferred
    elif not biography and localizations:
        biography = localizations[0]["biography"]
    profile_url = _image_url(item.get("profileUrl") or item.get("profile_url"))
    profiles = _person_profiles(item)
    if profile_url and profile_url.lower().startswith("https://") and profile_url not in profiles:
        profiles.insert(0, profile_url)
    aliases = [_text(alias) for alias in (item.get("alsoKnownAs") or item.get("also_known_as") or []) if _text(alias)]
    movievault_id = _text(item.get("id") or item.get("movieVaultId") or item.get("movievault_id"))
    return {
        "status": "hit",
        "provider": PROVIDER_ID,
        "sourceLabel": PROVIDER_LABEL,
        "sourceRef": source_ref or (f"movievault:person:{movievault_id}" if movievault_id else ""),
        "movieVaultId": movievault_id,
        "tmdbId": _text(item.get("tmdbId") or item.get("tmdb_id")),
        "imdbId": _text(item.get("imdbId") or item.get("imdb_id")),
        "name": name,
        "biography": biography,
        "birthday": _text(item.get("birthday") or item.get("birthDate") or item.get("birth_date")),
        "deathday": _text(item.get("deathday") or item.get("deathDate") or item.get("death_date")),
        "placeOfBirth": _text(item.get("placeOfBirth") or item.get("place_of_birth")),
        "knownFor": _text(item.get("knownFor") or item.get("known_for")),
        "alsoKnownAs": aliases,
        "profileUrl": profile_url,
        "profiles": profiles,
        "awards": _person_awards(item),
        "localizations": localizations,
        "language": _text(language),
    }


def person_details(payload, context=None):
    payload = payload or {}
    if not _movievault_enabled(context):
        return {"status": "skipped", "provider": PROVIDER_ID, "reason": "disabled"}
    tmdb_id = _text(payload.get("tmdbId") or payload.get("tmdb_id"))
    imdb_id = _text(payload.get("imdbId") or payload.get("imdb_id"))
    movievault_id = _text(
        payload.get("movieVaultId")
        or payload.get("movievaultId")
        or payload.get("movievault_id")
        or payload.get("personId")
        or payload.get("id")
    )
    name = _text(payload.get("name") or payload.get("q"))
    language = _text(_settings(context).get("language")) or "en-US"
    item = {}
    source_ref = ""
    if movievault_id.startswith("mv_person_"):
        data = _get(context or {}, f"/api/v1/people/{quote(movievault_id)}")
        results = _person_results(data)
        if results:
            item = results[0]
            source_ref = f"movievault:person:{movievault_id}"
    if not item and (tmdb_id or imdb_id or name):
        data = _get(
            context or {},
            "/api/v1/people",
            tmdbId=tmdb_id,
            imdbId=imdb_id,
            q="" if (tmdb_id or imdb_id) else name,
        )
        results = _person_results(data)
        if results:
            item = results[0]
            if tmdb_id:
                source_ref = f"movievault:person:tmdb:{tmdb_id}"
            elif imdb_id:
                source_ref = f"movievault:person:imdb:{imdb_id}"
    if not item:
        return {"status": "miss", "provider": PROVIDER_ID, "reason": "not_found"}
    return _person_payload(item, language=language, source_ref=source_ref)


def _safe_contribution_value(value):
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            key_text = _text(key)
            if not key_text:
                continue
            if key_text in FORBIDDEN_CONTRIBUTION_KEYS or key_text.lower() in {item.lower() for item in FORBIDDEN_CONTRIBUTION_KEYS}:
                continue
            safe = _safe_contribution_value(item)
            if safe not in (None, "", [], {}):
                clean[key_text] = safe
        return clean
    if isinstance(value, list):
        clean_items = [_safe_contribution_value(item) for item in value]
        return [item for item in clean_items if item not in (None, "", [], {})]
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("/api/next/assets/", "/assets/", "file:", "plugin_secret:")):
            return ""
        return text
    return value


def _template_cache_key(context):
    return f"{_contribution_url(context)}/api/v1/contribution-template"


def _contribution_template(context, *, force_refresh=False):
    key = _template_cache_key(context)
    now = time.time()
    cached = _TEMPLATE_CACHE.get(key)
    if not force_refresh and cached and now - cached.get("fetchedAt", 0) < 86400:
        return cached.get("template") or {}
    template = _json(_request("GET", key, context=context))
    _TEMPLATE_CACHE[key] = {"fetchedAt": now, "template": template}
    return template


def _cached_min_client_version(context):
    cached = _TEMPLATE_CACHE.get(_template_cache_key(context))
    template = (cached or {}).get("template") if isinstance(cached, dict) else {}
    return _text((template or {}).get("minClientVersion"))


def _allowed_fields(template, entity_type):
    if not isinstance(template, dict):
        return set()
    candidates = (
        template.get("allowedFields"),
        template.get("fields"),
        (template.get("entities") or {}).get(entity_type) if isinstance(template.get("entities"), dict) else None,
        (template.get("entityTypes") or {}).get(entity_type) if isinstance(template.get("entityTypes"), dict) else None,
    )
    for candidate in candidates:
        if isinstance(candidate, list):
            return {_text(item) for item in candidate if _text(item)}
        if isinstance(candidate, dict):
            nested = candidate.get("allowedFields") or candidate.get("fields")
            if isinstance(nested, list):
                return {_text(item) for item in nested if _text(item)}
            return {_text(key) for key in candidate.keys() if _text(key)}
    return set()


def _with_box_set_member_aliases(entity_type, payload, allowed):
    if entity_type != "box_set" or not isinstance(payload, dict):
        return payload
    members = None
    for key in ("members", "movies", "boxSetMovies", "box_set_movies"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            members = value
            break
    if not members:
        return payload
    expanded = dict(payload)
    for key in ("members", "movies", "boxSetMovies", "box_set_movies"):
        if not allowed or key in allowed:
            expanded[key] = members
    count = len(members)
    for key in ("memberCount", "member_count"):
        if not allowed or key in allowed:
            expanded[key] = count
    return expanded


def _metadata_context(payload):
    metadata = payload.get("metadata") or payload.get("meta") or {}
    return metadata if isinstance(metadata, dict) else {}


def _title_hint_from_metadata(payload):
    metadata = _metadata_context(payload)
    hints = []
    direct_title = _text(
        metadata.get("tmdbTitle")
        or metadata.get("tmdb_title")
        or payload.get("tmdbTitle")
        or payload.get("tmdb_title")
    )
    direct_original = _text(
        metadata.get("tmdbOriginalTitle")
        or metadata.get("tmdb_original_title")
        or payload.get("tmdbOriginalTitle")
        or payload.get("tmdb_original_title")
    )
    if direct_title or direct_original:
        hints.append(
            {
                "pluginId": "tmdb",
                "sourceLabel": "TMDb",
                "title": direct_title,
                "originalTitle": direct_original,
            }
        )

    raw_hints = (
        metadata.get("providerTitleHints")
        or metadata.get("provider_title_hints")
        or payload.get("providerTitleHints")
        or payload.get("provider_title_hints")
        or []
    )
    if isinstance(raw_hints, dict):
        raw_hints = [
            {"pluginId": provider, **hint} if isinstance(hint, dict) else {"pluginId": provider, "title": hint}
            for provider, hint in raw_hints.items()
        ]
    if isinstance(raw_hints, list):
        for item in raw_hints:
            if not isinstance(item, dict):
                continue
            plugin_id = _text(item.get("pluginId") or item.get("providerId") or item.get("provider"))
            title = _text(item.get("title") or item.get("providerTitle") or item.get("sourceTitle"))
            original_title = _text(item.get("originalTitle") or item.get("original_title"))
            if plugin_id or title or original_title:
                hints.append(
                    {
                        "pluginId": plugin_id,
                        "sourceLabel": _text(item.get("sourceLabel") or item.get("providerLabel") or plugin_id),
                        "title": title,
                        "originalTitle": original_title,
                    }
                )

    for hint in hints:
        if _text(hint.get("pluginId")).lower() == "tmdb":
            return {
                "pluginId": "tmdb",
                "sourceLabel": _text(hint.get("sourceLabel")) or "TMDb",
                "title": _text(hint.get("title")),
                "originalTitle": _text(hint.get("originalTitle")),
            }
    return {}


def _add_if_allowed(target, allowed, key, value):
    safe = _safe_contribution_value(value)
    if safe in (None, "", [], {}):
        return
    if allowed and key not in allowed:
        return
    target.setdefault(key, safe)


def _with_provider_title_hints(entity_type, safe_payload, payload, allowed):
    if entity_type not in {"movie", "release", "box_set"}:
        return safe_payload
    hint = _title_hint_from_metadata(payload)
    title = _text(hint.get("title"))
    original_title = _text(hint.get("originalTitle"))
    if not title and not original_title:
        return safe_payload

    enriched = dict(safe_payload)
    for key in (
        "tmdbTitle",
        "tmdb_title",
        "tmdbMovieTitle",
        "tmdb_movie_title",
        "sourceTitle",
        "source_title",
        "providerTitle",
        "provider_title",
        "metadataProviderTitle",
        "metadata_provider_title",
        "movieTitleFromTmdb",
        "movie_title_from_tmdb",
    ):
        _add_if_allowed(enriched, allowed, key, title)
    for key in (
        "tmdbOriginalTitle",
        "tmdb_original_title",
        "tmdbMovieOriginalTitle",
        "tmdb_movie_original_title",
        "sourceOriginalTitle",
        "source_original_title",
        "providerOriginalTitle",
        "provider_original_title",
    ):
        _add_if_allowed(enriched, allowed, key, original_title)

    structured_hint = {
        "provider": "tmdb",
        "pluginId": "tmdb",
        "sourceLabel": hint.get("sourceLabel") or "TMDb",
    }
    if title:
        structured_hint["title"] = title
    if original_title:
        structured_hint["originalTitle"] = original_title
    for key in ("providerTitleHints", "provider_title_hints", "sourceTitleHints", "source_title_hints"):
        _add_if_allowed(enriched, allowed, key, [structured_hint])
    provider_titles = {"tmdb": {}}
    if title:
        provider_titles["tmdb"]["title"] = title
    if original_title:
        provider_titles["tmdb"]["originalTitle"] = original_title
    for key in ("providerTitles", "provider_titles", "sourceTitles", "source_titles"):
        _add_if_allowed(enriched, allowed, key, provider_titles)
    return enriched


def _payload_fingerprint(payload):
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _source_reference(payload):
    reference = payload.get("sourceReference") or payload.get("source_reference") or {}
    return reference if isinstance(reference, dict) else {}


def _public_reference(reference):
    if not isinstance(reference, dict):
        return {}
    allowed = {
        "type",
        "key",
        "publicId",
        "public_id",
        "barcode",
        "containerType",
        "container_type",
        "remoteRef",
        "remote_ref",
        "movievaultId",
        "movievault_id",
        "tmdbId",
        "tmdb_id",
        "imdbId",
        "imdb_id",
    }
    clean = {}
    for key, value in reference.items():
        key_text = _text(key)
        if key_text in allowed and value not in (None, "", [], {}):
            clean[key_text] = _safe_contribution_value(value)
    remote_ref = _text(clean.get("remoteRef") or clean.get("remote_ref"))
    if remote_ref and not clean.get("movievaultId"):
        clean["movievaultId"] = remote_ref
    return clean


def _connection_details(context):
    connection = _movievault_context(context)
    details = {
        "name": PROVIDER_LABEL,
        "baseUrl": _base_url(context),
        "contributionUrl": _contribution_url(context),
        "authMethod": connection.get("authMethod"),
        "instanceId": connection.get("instanceId"),
        "instanceName": connection.get("instanceName"),
        "keyId": connection.get("keyId"),
        "linkStatus": connection.get("linkStatus"),
        "sharingMode": _sharing_mode(context),
        "tokenPrefix": connection.get("tokenPrefix"),
        "tokenSet": bool(connection.get("tokenSet") or _token(context)),
        "clientVersion": _source_version(context) or None,
        "minClientVersion": _cached_min_client_version(context) or None,
    }
    return details


def _payload_identity(payload, contribution_payload):
    reference = _source_reference(payload)
    identity = _text(payload.get("identity") or payload.get("id") or payload.get("sourceRef"))
    if identity:
        return identity
    for key in ("barcode", "key", "publicId", "public_id", "remoteRef", "remote_ref", "movievaultId", "movievault_id"):
        value = _text(reference.get(key))
        if value:
            return value
    if contribution_payload:
        return _payload_fingerprint(contribution_payload)[:16]
    return ""


def describe_payload(payload, context=None):
    context = context or {}
    payload = payload if isinstance(payload, dict) else {}
    entity_type, contribution_payload = _contribution_payload(payload, {})
    metadata = _metadata_context(payload)
    fields = sorted(contribution_payload.keys())
    reference = _public_reference(_source_reference(payload))
    warnings = []
    if not contribution_payload:
        warnings.append("empty_or_disallowed_payload")
    if not reference:
        warnings.append("missing_source_reference")
    if not _movievault_enabled(context):
        warnings.append("movievault_disabled")
    if not _contribution_enabled(context):
        warnings.append("contribution_disabled")
    if not _token(context) and not _movievault_context(context).get("tokenSet"):
        warnings.append("missing_connection_token")
    changed_fields = metadata.get("changedFields") or metadata.get("changed_fields") or fields
    source_providers = metadata.get("sourceProviders") or metadata.get("source_providers") or []
    identity = _payload_identity(payload, contribution_payload)
    return {
        "status": "ok",
        "provider": PROVIDER_ID,
        "providerLabel": PROVIDER_LABEL,
        "entityType": entity_type,
        "identity": identity,
        "fieldCount": len(fields),
        "fields": fields,
        "changedFields": changed_fields if isinstance(changed_fields, list) else fields,
        "sourceProviders": source_providers if isinstance(source_providers, list) else [],
        "sourceReference": reference,
        "destination": _connection_details(context),
        "summary": f"{PROVIDER_LABEL} contribution prepared for {entity_type} with {len(fields)} field(s).",
        "warnings": warnings,
    }


def activity_summary(payload, context=None):
    payload = payload if isinstance(payload, dict) else {}
    contribution = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
    execution = payload.get("execution") if isinstance(payload.get("execution"), dict) else {}
    entity_type, contribution_payload = _contribution_payload(contribution, {})
    response_payload = execution.get("response") if isinstance(execution.get("response"), dict) else {}
    remote_id = _text(
        response_payload.get("id")
        or response_payload.get("contributionId")
        or response_payload.get("contribution_id")
        or response_payload.get("operationId")
        or response_payload.get("operation_id")
    )
    state = _text(execution.get("status") or payload.get("status") or "unknown")
    reason = _text(execution.get("reason") or payload.get("reason"))
    fields = sorted(contribution_payload.keys())
    result = {
        "status": "ok",
        "provider": PROVIDER_ID,
        "providerLabel": PROVIDER_LABEL,
        "state": state,
        "entityType": entity_type,
        "identity": _payload_identity(contribution, contribution_payload),
        "fieldCount": len(fields),
        "fields": fields,
        "idempotencyPrefix": execution.get("idempotencyPrefix"),
        "templateVersion": execution.get("templateVersion"),
        "remoteId": remote_id,
        "summary": f"{PROVIDER_LABEL} contribution {state} for {entity_type}.",
    }
    if reason:
        result["reason"] = reason
    return result


_DEFAULT_LOCALIZED_FIELDS = ("title", "originalTitle", "overview", "edition", "description", "biography")
_DEFAULT_LOCALIZED_FIELD_PATTERN = "<field>_<iso-639-1-language>"


def _localized_language_code(value):
    code = _text(value).strip().lower()
    if not code:
        return ""
    for sep in ("-", "_"):
        if sep in code:
            code = code.split(sep, 1)[0]
    return code.strip()


def _iter_template_field_defs(template, entity_type):
    candidates = []
    fields = template.get("fields")
    if isinstance(fields, dict):
        candidates.append(fields)
    for container_key in ("templates", "entities", "entityTypes"):
        container = template.get(container_key)
        if isinstance(container, dict):
            entity_def = container.get(entity_type)
            if isinstance(entity_def, dict) and isinstance(entity_def.get("fields"), dict):
                candidates.append(entity_def["fields"])
    for mapping in candidates:
        for name, spec in mapping.items():
            yield _text(name), spec


def _localized_field_settings(template, entity_type):
    pattern = _DEFAULT_LOCALIZED_FIELD_PATTERN
    fields = set()
    if isinstance(template, dict):
        pattern = _text(template.get("localizedFieldPattern")) or pattern
        explicit = template.get("localizedFields")
        if isinstance(explicit, list):
            fields = {_text(item) for item in explicit if _text(item)}
        if not fields:
            for name, spec in _iter_template_field_defs(template, entity_type):
                if name and isinstance(spec, dict) and spec.get("localized"):
                    fields.add(name)
    if not fields:
        fields = set(_DEFAULT_LOCALIZED_FIELDS)
    return pattern, fields


def _format_localized_key(pattern, base, lang):
    key = pattern or _DEFAULT_LOCALIZED_FIELD_PATTERN
    replacements = (
        ("<field>", base),
        ("<iso-639-1-language>", lang),
        ("<iso-639-1>", lang),
        ("<language>", lang),
        ("<lang>", lang),
    )
    for token, value in replacements:
        key = key.replace(token, value)
    return _text(key)


def _expand_localized_fields(entity_type, safe_payload, localizations, allowed, template):
    if not isinstance(safe_payload, dict):
        return safe_payload
    if entity_type not in {"movie", "release", "box_set", "person"}:
        return safe_payload
    if not isinstance(localizations, list) or not localizations:
        return safe_payload
    pattern, localized_fields = _localized_field_settings(template, entity_type)
    enriched = dict(safe_payload)
    for entry in localizations:
        if not isinstance(entry, dict):
            continue
        lang = _localized_language_code(
            entry.get("lang") or entry.get("language") or entry.get("locale")
        )
        if not lang:
            continue
        for base in localized_fields:
            if allowed and base not in allowed:
                continue
            value = _safe_contribution_value(entry.get(base))
            if value in (None, "", [], {}):
                continue
            key = _format_localized_key(pattern, base, lang)
            if not key or key in enriched:
                continue
            enriched[key] = value
    return enriched


def _with_release_title_mapping(entity_type, safe_payload):
    """Map DiscVault's clean/raw titles onto the MovieVault release contract.

    The MovieVault *release* entity carries the PHYSICAL/packaging title in
    ``title`` while the canonical film title travels as ``movieTitle`` /
    ``tmdbTitle``. DiscVault keeps the clean title in ``title`` and the raw
    scanned title in ``release_title``, so swap them for release contributions."""
    if entity_type != "release" or not isinstance(safe_payload, dict):
        return safe_payload
    enriched = dict(safe_payload)
    clean_title = _text(enriched.get("title"))
    raw_release_title = enriched.pop("release_title", None)
    raw_release_title_camel = enriched.pop("releaseTitle", None)
    scanned_title = _text(raw_release_title or raw_release_title_camel)
    physical_title = scanned_title or clean_title
    if physical_title:
        enriched["title"] = physical_title
    if clean_title:
        enriched.setdefault("movieTitle", clean_title)
        enriched.setdefault("tmdbTitle", clean_title)
    return enriched


def _contribution_payload(payload, template):
    entity_type = _text(payload.get("entityType") or payload.get("entity_type") or "movie")
    if entity_type not in {"movie", "release", "box_set", "person"}:
        return entity_type, {}
    raw_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    if not raw_payload:
        raw_payload = {key: value for key, value in payload.items() if key not in {"entityType", "entity_type", "sourceReference", "source_reference", "force"}}
    safe_payload = _safe_contribution_value(raw_payload)
    allowed = _allowed_fields(template, entity_type)
    safe_payload = _with_box_set_member_aliases(entity_type, safe_payload, allowed)
    safe_payload = _with_release_title_mapping(entity_type, safe_payload)
    localizations = safe_payload.pop("localizations", None) if isinstance(safe_payload, dict) else None
    if allowed:
        safe_payload = {key: value for key, value in safe_payload.items() if key in allowed}
    safe_payload = _expand_localized_fields(entity_type, safe_payload, localizations, allowed, template)
    safe_payload = _with_provider_title_hints(entity_type, safe_payload, payload, allowed)
    return entity_type, safe_payload


def _contribution_field_diagnostics(payload, template, contribution_payload):
    entity_type = _text(payload.get("entityType") or payload.get("entity_type") or "movie")
    raw_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    if not raw_payload:
        raw_payload = {key: value for key, value in payload.items() if key not in {"entityType", "entity_type", "sourceReference", "source_reference", "force"}}
    safe_incoming = _safe_contribution_value(raw_payload)
    incoming_keys = {
        _text(key)
        for key, value in (safe_incoming.items() if isinstance(safe_incoming, dict) else [])
        if _text(key) and _text(key) != "localizations" and value not in (None, "", [], {})
    }
    accepted_keys = {_text(key) for key in (contribution_payload or {}).keys() if _text(key)}
    allowed = _allowed_fields(template, entity_type)
    dropped = []
    for key in sorted(incoming_keys - accepted_keys):
        reason = "not_in_template" if allowed else "excluded"
        dropped.append({"field": key, "reason": reason})
    return sorted(accepted_keys), dropped


def _validation_error(response_payload):
    return _text(response_payload.get("code") or response_payload.get("error")) == "validation_error"


def receive_metadata(payload, context=None):
    context = context or {}
    payload = payload if isinstance(payload, dict) else {}
    if not _movievault_enabled(context):
        return {"status": "skipped", "provider": PROVIDER_ID, "reason": "disabled"}
    if not _contribution_enabled(context):
        return {"status": "skipped", "provider": PROVIDER_ID, "reason": "contribution_disabled"}
    sharing_mode = _sharing_mode(context)
    if sharing_mode == "disabled":
        return {"status": "skipped", "provider": PROVIDER_ID, "reason": "sharing_disabled"}
    if not _token(context):
        return {"status": "skipped", "provider": PROVIDER_ID, "reason": "missing_token"}

    template = _contribution_template(context)
    entity_type, contribution_payload = _contribution_payload(payload, template)
    if not contribution_payload:
        return {"status": "skipped", "provider": PROVIDER_ID, "reason": "empty_or_disallowed_payload"}

    fingerprint = _payload_fingerprint(contribution_payload)
    identity = _text(payload.get("identity") or payload.get("id") or payload.get("sourceRef") or fingerprint[:16])
    template_version = _text(template.get("version") or template.get("templateVersion") or "unversioned")
    envelope = {
        "idempotencyKey": _text(payload.get("idempotencyKey"))
        or f"{entity_type}:{identity}:{template_version}:{fingerprint}",
        "sourceClient": "discvault",
        "sourceVersion": _source_version(context),
        "sharingMode": sharing_mode,
        "entityType": entity_type,
        "sourceReference": _source_reference(payload),
        "payload": contribution_payload,
    }
    try:
        response_payload = _post_contribution(context, envelope)
        if _validation_error(response_payload):
            template = _contribution_template(context, force_refresh=True)
            entity_type, contribution_payload = _contribution_payload(payload, template)
            if not contribution_payload:
                return {"status": "skipped", "provider": PROVIDER_ID, "reason": "empty_or_disallowed_payload"}
            envelope["payload"] = contribution_payload
            response_payload = _post_contribution(context, envelope)
    except MovieVaultRateLimited as exc:
        return {
            "status": "skipped",
            "provider": PROVIDER_ID,
            "entityType": entity_type,
            "reason": "rate_limited",
            "retryAfter": exc.retry_after,
        }
    accepted_fields, dropped_fields = _contribution_field_diagnostics(payload, template, contribution_payload)
    return {
        "status": "submitted",
        "provider": PROVIDER_ID,
        "entityType": entity_type,
        "idempotencyPrefix": envelope["idempotencyKey"][:24],
        "templateVersion": template_version,
        "acceptedFields": accepted_fields,
        "droppedFields": dropped_fields,
        "response": response_payload,
    }
