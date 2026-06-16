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


def _error_code(response):
    payload = _json(response)
    code, _message = _response_error(payload)
    return code


def connection_recovery_action(payload, context=None):
    payload = payload or {}
    phase = _text(payload.get("phase")).lower()
    status_code = int(payload.get("statusCode") or payload.get("status_code") or 0)
    code, message = _response_error(payload.get("response") or {})
    lowered = message.lower()
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
        return {
            "status": "ok",
            "provider": PROVIDER_ID,
            "phase": "recovery",
            "method": "POST",
            "path": HANDSHAKE_PATH,
            "headers": headers,
            "body": dict(body),
            "auth": "signed_recovery",
            "requestedScopes": list(REQUESTED_SCOPES),
        }
    return {"status": "skipped", "provider": PROVIDER_ID, "reason": "unknown_connection_phase"}


def _request(method, url, *, context=None, params=None, json_payload=None, retry_recovery=True, allow_validation_error=False):
    headers = _headers(context)
    if json_payload is not None:
        headers["Content-Type"] = "application/json"
    request_func = getattr(requests, "request", None)
    if callable(request_func):
        response = request_func(method, url, params=params, json=json_payload, headers=headers, timeout=10)
    elif method.upper() == "GET":
        response = requests.get(url, params=params, headers=headers, timeout=10)
    else:
        response = requests.post(url, json=json_payload, headers=headers, timeout=10)

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
                )
    if status_code == 403 and _error_code(response) == "instance_revoked":
        mark_revoked = (context or {}).get("movievaultMarkRevoked")
        if callable(mark_revoked):
            mark_revoked()
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
    "releases",
)


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
    year = _parse_year(member.get("year") or member.get("releaseYear") or member.get("release_year"))
    if normalized_title and year:
        keys.add(("title_year", f"{normalized_title}:{year}"))
    elif normalized_title:
        keys.add(("title", normalized_title))
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


def _normalize_box_set_proposal(payload, context=None):
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

    if not title and members:
        title = _text(item.get("boxSetTitle") or item.get("collectionTitle") or item.get("name"))
    if not title:
        return {}

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
        "poster": _image_url(_first_value(item, "posterUrl", "poster_url", "poster", "image")),
        "poster_url": _image_url(_first_value(item, "posterUrl", "poster_url", "poster", "image")),
        "backdrop": _image_url(_first_value(item, "backdropUrl", "backdrop_url", "backdrop")),
        "backdrop_url": _image_url(_first_value(item, "backdropUrl", "backdrop_url", "backdrop")),
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


def search_barcode(payload, context=None):
    barcode = str((payload or {}).get("barcode") or "").strip()
    if not _movievault_enabled(context):
        return {"status": "skipped", "provider": PROVIDER_ID, "reason": "disabled"}
    if not _is_public_barcode(barcode):
        return {"status": "skipped", "provider": PROVIDER_ID, "reason": "not_public_barcode"}
    data = _get(context or {}, f"/api/v1/barcodes/{quote(barcode)}")
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
        proposal = _normalize_box_set_proposal(candidate, {**proposal_context, "sourceRef": _text(_first_value(candidate, "id", "movieVaultId", "movievaultId", "movievault_id"))})
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
    return {
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
    }


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
    if allowed:
        safe_payload = {key: value for key, value in safe_payload.items() if key in allowed}
    safe_payload = _with_provider_title_hints(entity_type, safe_payload, payload, allowed)
    return entity_type, safe_payload


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
    response_payload = _post_contribution(context, envelope)
    if _validation_error(response_payload):
        template = _contribution_template(context, force_refresh=True)
        entity_type, contribution_payload = _contribution_payload(payload, template)
        if not contribution_payload:
            return {"status": "skipped", "provider": PROVIDER_ID, "reason": "empty_or_disallowed_payload"}
        envelope["payload"] = contribution_payload
        response_payload = _post_contribution(context, envelope)
    return {
        "status": "submitted",
        "provider": PROVIDER_ID,
        "entityType": entity_type,
        "idempotencyPrefix": envelope["idempotencyKey"][:24],
        "templateVersion": template_version,
        "response": response_payload,
    }
