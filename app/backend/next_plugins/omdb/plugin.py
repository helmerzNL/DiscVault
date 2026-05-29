import re
from datetime import datetime

import requests


def _settings(context):
    return (context or {}).get("settings") or {}


def _secrets(context):
    return (context or {}).get("secrets") or {}


def _api_key(context):
    return str(_secrets(context).get("apiKey") or _secrets(context).get("api_key") or "").strip()


def _release_date(value):
    text = str(value or "").strip()
    if not text or text == "N/A":
        return ""
    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def _clean(value):
    text = str(value or "").strip()
    return "" if text in {"N/A", "None", "null"} else text


def _runtime(value):
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else None


def _request(context, **params):
    api_key = _api_key(context)
    if not api_key:
        return {"Response": "False", "Error": "OMDb API key is not configured"}
    response = requests.get(
        "https://www.omdbapi.com/",
        params={**params, "apikey": api_key},
        timeout=8,
    )
    response.raise_for_status()
    return response.json()


def _normalize_movie(data):
    if data.get("Response") != "True":
        return {"status": "miss", "provider": "omdb", "error": data.get("Error") or "No result"}
    rated = _clean(data.get("Rated"))
    return {
        "status": "hit",
        "provider": "omdb",
        "sourceLabel": "OMDb",
        "movie": {
            "title": _clean(data.get("Title")),
            "year": _clean(data.get("Year"))[:4],
            "releaseDate": _release_date(data.get("Released")),
            "runtimeMinutes": _runtime(data.get("Runtime")),
            "overview": _clean(data.get("Plot")),
            "rating": _clean(data.get("imdbRating")),
            "director": _clean(data.get("Director")),
            "actor": _clean(data.get("Actors")),
            "genre": _clean(data.get("Genre")),
            "country": _clean(data.get("Country")),
            "language": _clean(data.get("Language")),
            "posterUrl": _clean(data.get("Poster")),
            "audienceRating": rated if rated not in {"Not Rated"} else "",
        },
        "technicalSpecs": {
            "contentRatings": {"US": rated} if rated and rated != "Not Rated" else {},
        },
        "identifiers": {
            "imdbId": _clean(data.get("imdbID")),
        },
        "imdbId": _clean(data.get("imdbID")),
    }


def health_check(context=None):
    if not _api_key(context or {}):
        return {"status": "needs_configuration", "message": "Configure an OMDb API key."}
    data = _request(context or {}, t="Inception", type="movie")
    return {
        "status": "available" if data.get("Response") == "True" else "unavailable",
        "message": data.get("Error") or "OMDb reachable.",
    }


def search_title(payload, context=None):
    title = str((payload or {}).get("title") or "").strip()
    year = str((payload or {}).get("year") or "").strip()
    if not title:
        return {"status": "skipped", "provider": "omdb", "items": []}
    data = _request(context or {}, s=title, y=year, type="movie")
    items = []
    for item in data.get("Search") or []:
        items.append(
            {
                "provider": "omdb",
                "providerLabel": "OMDb",
                "id": item.get("imdbID") or "",
                "imdbId": item.get("imdbID") or "",
                "title": item.get("Title") or "",
                "year": str(item.get("Year") or "")[:4],
                "posterUrl": _clean(item.get("Poster")),
            }
        )
    return {"status": "hit" if items else "miss", "provider": "omdb", "items": items[:8]}


def lookup_external_id(payload, context=None):
    imdb_id = str((payload or {}).get("imdbId") or (payload or {}).get("imdb_id") or "").strip()
    if not imdb_id:
        return {"status": "skipped", "provider": "omdb"}
    return _normalize_movie(_request(context or {}, i=imdb_id, type="movie", plot="full"))


def movie_details(payload, context=None):
    imdb_id = str((payload or {}).get("imdbId") or (payload or {}).get("imdb_id") or "").strip()
    title = str((payload or {}).get("title") or "").strip()
    year = str((payload or {}).get("year") or "").strip()
    if imdb_id:
        return lookup_external_id(payload, context)
    if not title:
        return {"status": "skipped", "provider": "omdb"}
    return _normalize_movie(_request(context or {}, t=title, y=year, type="movie", plot="full"))
