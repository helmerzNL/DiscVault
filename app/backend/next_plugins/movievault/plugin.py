import os
from urllib.parse import quote

import requests


def _settings(context):
    return (context or {}).get("settings") or {}


def _secrets(context):
    return (context or {}).get("secrets") or {}


def _base_url(context):
    return str(
        _settings(context).get("baseUrl")
        or os.environ.get("MOVIEVAULT_SEARCH_URL")
        or os.environ.get("MOVIEVAULT_BASE_URL")
        or "https://search.discvault.eu"
    ).strip().rstrip("/")


def _token(context):
    return str(_secrets(context).get("token") or _secrets(context).get("apiToken") or "").strip()


def _headers(context):
    headers = {"Accept": "application/json"}
    token = _token(context)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get(context, path, **params):
    response = requests.get(f"{_base_url(context)}{path}", params=params, headers=_headers(context), timeout=10)
    response.raise_for_status()
    return response.json()


def _items(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("items", "results", "movies", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return [payload]


def _first(payload):
    items = _items(payload)
    return items[0] if items and isinstance(items[0], dict) else {}


def _text(value):
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


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


def _normalize_result(payload, *, source_ref=""):
    item = _first(payload)
    if not item:
        return {"status": "miss", "provider": "movievault"}
    movie = _movie_payload(item)
    if not movie.get("title"):
        return {"status": "miss", "provider": "movievault"}
    return {
        "status": "hit",
        "provider": "movievault",
        "sourceLabel": "MovieVault",
        "sourceRef": source_ref or _text(item.get("id") or item.get("movieVaultId") or item.get("movievault_id")),
        "movie": movie,
        "technicalSpecs": _technical_payload(item),
        "tmdbId": _text(item.get("tmdbId") or item.get("tmdb_id")),
        "imdbId": _text(item.get("imdbId") or item.get("imdb_id")),
        "boxSetProposal": item.get("boxSetProposal") or item.get("box_set_proposal") or item.get("box_set"),
    }


def health_check(context=None):
    if not _token(context or {}):
        return {"status": "needs_configuration", "message": "Configure a MovieVault token."}
    try:
        response = requests.get(f"{_base_url(context or {})}/api/v1/health", headers=_headers(context or {}), timeout=8)
        return {"status": "available" if response.status_code < 500 else "unavailable", "message": f"HTTP {response.status_code}"}
    except Exception as exc:
        return {"status": "unavailable", "message": str(exc)}


def search_barcode(payload, context=None):
    barcode = str((payload or {}).get("barcode") or "").strip()
    if not barcode:
        return {"status": "skipped", "provider": "movievault"}
    return _normalize_result(_get(context or {}, f"/api/v1/barcodes/{quote(barcode)}"), source_ref=f"barcode:{barcode}")


def search_title(payload, context=None):
    title = str((payload or {}).get("title") or "").strip()
    year = str((payload or {}).get("year") or "").strip()
    if not title:
        return {"status": "skipped", "provider": "movievault", "items": []}
    data = _get(context or {}, "/api/v1/movies", q=title, year=year)
    items = []
    for item in _items(data)[:8]:
        movie = _movie_payload(item)
        if movie.get("title"):
            items.append(
                {
                    "provider": "movievault",
                    "providerLabel": "MovieVault",
                    "id": _text(item.get("id") or item.get("movieVaultId") or item.get("movievault_id")),
                    "title": movie.get("title"),
                    "year": movie.get("year"),
                    "posterUrl": movie.get("posterUrl"),
                    "movie": movie,
                }
            )
    return {"status": "hit" if items else "miss", "provider": "movievault", "items": items}


def movie_details(payload, context=None):
    barcode = str((payload or {}).get("barcode") or "").strip()
    title = str((payload or {}).get("title") or "").strip()
    year = str((payload or {}).get("year") or "").strip()
    if barcode:
        result = search_barcode(payload, context)
        if result.get("status") == "hit":
            return result
    if not title:
        return {"status": "skipped", "provider": "movievault"}
    return _normalize_result(_get(context or {}, "/api/v1/movies", q=title, year=year), source_ref=f"title:{title}")


def box_set_candidates(payload, context=None):
    title = str((payload or {}).get("title") or "").strip()
    year = str((payload or {}).get("year") or "").strip()
    barcode = str((payload or {}).get("barcode") or "").strip()
    data = _get(context or {}, "/api/v1/box-sets", q=title, year=year, barcode=barcode)
    return {"status": "hit", "provider": "movievault", "boxSetProposal": _first(data)}


def receive_metadata(payload, context=None):
    return {
        "status": "not_implemented",
        "message": "MovieVault receiver contribution execution is handled by the dedicated contribution flow.",
    }
