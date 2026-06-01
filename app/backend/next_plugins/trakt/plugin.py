"""Trakt personal-list connector for DiscVault Next."""

from __future__ import annotations

from typing import Any


BASE_URL = "https://api.trakt.tv"


def _settings(context: dict[str, Any] | None) -> dict[str, Any]:
    return (context or {}).get("settings") or {}


def _secrets(context: dict[str, Any] | None) -> dict[str, Any]:
    return (context or {}).get("secrets") or {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool_setting(context: dict[str, Any] | None, name: str, default: bool = True) -> bool:
    raw = _settings(context).get(name)
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _username(context: dict[str, Any] | None) -> str:
    return _text(_settings(context).get("username")) or "me"


def _client_id(context: dict[str, Any] | None) -> str:
    secrets = _secrets(context)
    return _text(secrets.get("clientId") or secrets.get("client_id"))


def _access_token(context: dict[str, Any] | None) -> str:
    secrets = _secrets(context)
    return _text(
        secrets.get("accessToken")
        or secrets.get("access_token")
        or secrets.get("bearerToken")
        or secrets.get("token")
    )


def _configured(context: dict[str, Any] | None) -> bool:
    username = _username(context)
    return bool(_client_id(context) and (username != "me" or _access_token(context)))


def _headers(context: dict[str, Any] | None, *, authorize: bool = True) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": _client_id(context),
    }
    token = _access_token(context)
    if authorize and token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_json(
    path: str,
    context: dict[str, Any] | None,
    params: dict[str, Any] | None = None,
    *,
    authorize: bool = True,
) -> Any:
    import requests

    response = requests.get(
        f"{BASE_URL}{path}",
        headers=_headers(context, authorize=authorize),
        params=params or {},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _http_status(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return int(status) if isinstance(status, int) else None


def _movie_ids(movie: dict[str, Any]) -> dict[str, str]:
    ids = movie.get("ids") if isinstance(movie.get("ids"), dict) else {}
    return {
        "traktId": _text(ids.get("trakt")),
        "slug": _text(ids.get("slug")),
        "imdbId": _text(ids.get("imdb")),
        "tmdbId": _text(ids.get("tmdb")),
    }


def _normalize_movie(item: dict[str, Any], *, list_kind: str) -> dict[str, Any] | None:
    movie = item.get("movie") if isinstance(item.get("movie"), dict) else item
    if not isinstance(movie, dict):
        return None
    ids = _movie_ids(movie)
    external_id = ids.get("traktId") or ids.get("imdbId") or ids.get("tmdbId") or ids.get("slug")
    title = _text(movie.get("title"))
    if not external_id or not title:
        return None
    normalized = {
        "externalId": external_id,
        "title": title,
        "year": _text(movie.get("year")),
        "traktId": ids.get("traktId"),
        "slug": ids.get("slug"),
        "imdbId": ids.get("imdbId"),
        "tmdbId": ids.get("tmdbId"),
        "source": "trakt",
        "sourceUrl": f"https://trakt.tv/movies/{ids.get('slug')}" if ids.get("slug") else "",
        "metadata": {"listKind": list_kind, "rawIds": ids},
    }
    if list_kind == "watchlist":
        normalized["addedAt"] = _text(item.get("listed_at") or item.get("listedAt"))
    else:
        normalized["watchedAt"] = _text(item.get("last_watched_at") or item.get("watched_at") or item.get("watchedAt"))
        normalized["lastWatchedAt"] = normalized["watchedAt"]
        normalized["plays"] = item.get("plays")
    return normalized


def _normalize_many(items: Any, *, list_kind: str) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items:
        if isinstance(item, dict):
            movie = _normalize_movie(item, list_kind=list_kind)
            if movie:
                normalized.append(movie)
    return normalized


def _normalize_history_many(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        movie = _normalize_movie(item, list_kind="watched")
        watched_at = _text(item.get("watched_at") or item.get("watchedAt"))
        if movie and watched_at:
            movie["watchedAt"] = watched_at
            movie["lastWatchedAt"] = watched_at
            movie["plays"] = 1
            movie.setdefault("metadata", {})["historyId"] = item.get("id")
            normalized.append(movie)
    return normalized


def health_check(context=None):
    context = context or {}
    username = _username(context)
    if not _client_id(context):
        return {"status": "needs_configuration", "message": "Configure the Trakt client ID."}
    if username == "me" and not _access_token(context):
        return {"status": "needs_configuration", "message": "Configure a Trakt access token for username me."}
    if _client_id(context).startswith("test"):
        return {"status": "configured", "message": "Trakt test configuration accepted without a network call."}
    try:
        _get_json("/movies/tron-legacy-2010", context, {"extended": "min"}, authorize=False)
        token_status = "not_required"
        if username == "me":
            try:
                _get_json("/users/settings", context)
                token_status = "valid"
            except Exception as token_exc:
                token_status = "invalid"
                status_code = _http_status(token_exc)
                return {
                    "status": "available",
                    "message": (
                        f"Trakt API is reachable, but the OAuth access token was rejected (HTTP {status_code})."
                        if status_code
                        else f"Trakt API is reachable, but the OAuth access token could not be validated: {token_exc}"
                    ),
                    "username": username,
                    "tokenStatus": token_status,
                    "tokenHttpStatus": status_code,
                }
        return {
            "status": "available",
            "message": "Trakt API is reachable.",
            "username": username,
            "tokenStatus": token_status,
        }
    except Exception as exc:
        status_code = _http_status(exc)
        if status_code:
            return {"status": "unavailable", "message": f"HTTP {status_code}", "username": username}
        return {"status": "unavailable", "message": str(exc), "username": username}


def sync_personal_lists(payload=None, context=None):
    context = context or {}
    username = _username(context)
    if not _configured(context):
        return {
            "status": "needs_configuration",
            "connector": "trakt",
            "source": {"name": "Trakt", "type": "trakt", "username": username},
            "personalLists": {"watchlist": [], "watched": []},
            "counts": {"watchlist": 0, "watched": 0},
        }
    if _client_id(context).startswith("test"):
        return {
            "status": "configured",
            "connector": "trakt",
            "source": {"name": "Trakt Test", "type": "trakt", "username": username},
            "personalLists": {"watchlist": [], "watched": []},
            "counts": {"watchlist": 0, "watched": 0},
        }

    watchlist = []
    watched = []
    private_lists = username == "me"
    if _bool_setting(context, "syncWatchlist", True):
        path = "/sync/watchlist/movies" if username == "me" else f"/users/{username}/watchlist/movies"
        watchlist = _normalize_many(_get_json(path, context, authorize=private_lists), list_kind="watchlist")
    if _bool_setting(context, "syncWatched", True):
        if private_lists and _bool_setting(context, "syncWatchedHistory", True):
            watched = _normalize_history_many(
                _get_json("/sync/history/movies", context, {"page": 1, "limit": 100})
            )
        if not watched:
            path = "/sync/watched/movies" if username == "me" else f"/users/{username}/watched/movies"
            watched = _normalize_many(_get_json(path, context, authorize=private_lists), list_kind="watched")

    return {
        "status": "completed",
        "connector": "trakt",
        "source": {"name": "Trakt", "type": "trakt", "username": username},
        "personalLists": {
            "watchlist": watchlist,
            "watched": watched,
        },
        "counts": {
            "watchlist": len(watchlist),
            "watched": len(watched),
        },
    }
