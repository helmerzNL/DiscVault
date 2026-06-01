import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests


def _settings(context):
    return (context or {}).get("settings") or {}


def _secrets(context):
    return (context or {}).get("secrets") or {}


def _base_url(context):
    return str(_settings(context).get("baseUrl") or "").strip().rstrip("/")


def _token(context):
    return str(_secrets(context).get("token") or "").strip()


def _library_ids(context):
    value = _settings(context).get("libraryIds") or []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _configured(context):
    return bool(_base_url(context) and _token(context))


def _request_xml(url, token, timeout=12, **params):
    response = requests.get(url, params={"X-Plex-Token": token, **params}, timeout=timeout)
    response.raise_for_status()
    return ET.fromstring(response.text)


def _ids_from_video(video):
    tmdb_id = ""
    imdb_id = ""
    guid = video.get("guid", "")
    if "themoviedb" in guid or "tmdb" in guid:
        match = re.search(r"(?:themoviedb|tmdb)[:/]+(\d+)", guid)
        if match:
            tmdb_id = match.group(1)
    elif "imdb" in guid:
        match = re.search(r"tt\d+", guid)
        if match:
            imdb_id = match.group(0)
    for child in video.findall("Guid"):
        child_id = child.get("id", "")
        if child_id.startswith("tmdb://"):
            tmdb_id = child_id[7:]
        elif child_id.startswith("imdb://"):
            imdb_id = child_id[7:]
    return tmdb_id, imdb_id


def _server_info(context):
    base_url = _base_url(context)
    token = _token(context)
    if not _configured(context):
        return {"machineIdentifier": "", "friendlyName": "Plex"}
    if base_url.endswith(".example"):
        return {"machineIdentifier": "example-plex", "friendlyName": "Plex Example"}
    try:
        root = _request_xml(f"{base_url}/identity", token, timeout=6)
        return {
            "machineIdentifier": root.get("machineIdentifier", ""),
            "friendlyName": root.get("friendlyName", "Plex"),
        }
    except Exception:
        return {"machineIdentifier": "", "friendlyName": "Plex"}


def _movie_sections(context):
    base_url = _base_url(context)
    token = _token(context)
    configured_ids = set(_library_ids(context))
    root = _request_xml(f"{base_url}/library/sections", token, timeout=12)
    sections = [
        {
            "key": item.get("key", ""),
            "title": item.get("title", ""),
            "type": item.get("type", ""),
        }
        for item in root.findall(".//Directory")
        if item.get("type") == "movie"
    ]
    if configured_ids:
        sections = [section for section in sections if section["key"] in configured_ids]
    return sections


def _items_for_section(context, section):
    base_url = _base_url(context)
    token = _token(context)
    root = _request_xml(
        f"{base_url}/library/sections/{section['key']}/all",
        token,
        timeout=30,
        type="1",
        includeGuids="1",
    )
    machine_id = _server_info(context).get("machineIdentifier", "")
    items = []
    for video in root.findall(".//Video"):
        external_id = video.get("ratingKey", "")
        tmdb_id, imdb_id = _ids_from_video(video)
        items.append(
            {
                "externalId": external_id,
                "title": video.get("title", ""),
                "year": str(video.get("year", "") or ""),
                "tmdbId": tmdb_id,
                "imdbId": imdb_id,
                "mediaType": "movie",
                "playbackUrl": f"plex://server/{machine_id}/details?key=/library/metadata/{external_id}" if machine_id and external_id else "",
                "libraryId": section["key"],
                "libraryTitle": section["title"],
                "metadata": {
                    "guid": video.get("guid", ""),
                    "ratingKey": external_id,
                    "thumb": video.get("thumb", ""),
                    "duration": video.get("duration", ""),
                },
            }
        )
    return items


def _plex_datetime(value):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.fromtimestamp(int(text), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return text


def _watched_history_items(context):
    base_url = _base_url(context)
    token = _token(context)
    root = _request_xml(
        f"{base_url}/status/sessions/history/all",
        token,
        timeout=30,
        type="1",
        includeGuids="1",
    )
    machine_id = _server_info(context).get("machineIdentifier", "")
    items = []
    for video in root.findall(".//Video"):
        viewed_at = _plex_datetime(video.get("viewedAt") or video.get("lastViewedAt"))
        if not viewed_at:
            continue
        external_id = video.get("ratingKey", "") or video.get("key", "").split("/")[-1]
        tmdb_id, imdb_id = _ids_from_video(video)
        items.append(
            {
                "externalId": external_id,
                "title": video.get("title", ""),
                "year": str(video.get("year", "") or ""),
                "tmdbId": tmdb_id,
                "imdbId": imdb_id,
                "watchedAt": viewed_at,
                "lastWatchedAt": viewed_at,
                "plays": 1,
                "source": "plex",
                "sourceUrl": f"plex://server/{machine_id}/details?key=/library/metadata/{external_id}" if machine_id and external_id else "",
                "metadata": {
                    "guid": video.get("guid", ""),
                    "ratingKey": external_id,
                    "historyKey": video.get("historyKey", ""),
                    "viewedAt": video.get("viewedAt", ""),
                },
            }
        )
    return items


def health_check(context=None):
    context = context or {}
    if not _configured(context):
        return {"status": "needs_configuration", "message": "Configure Server URL and Plex token."}
    base_url = _base_url(context)
    if base_url.endswith(".example"):
        return {"status": "configured", "message": "Plex example configuration accepted without a network call."}
    try:
        root = _request_xml(f"{base_url}/", _token(context), timeout=8)
        return {
            "status": "available",
            "message": "Plex server reachable.",
            "machineIdentifier": root.get("machineIdentifier", ""),
            "friendlyName": root.get("friendlyName", "Plex"),
        }
    except requests.HTTPError as exc:
        return {"status": "unavailable", "message": f"HTTP {exc.response.status_code}"}
    except Exception as exc:
        return {"status": "unavailable", "message": str(exc)}


def discover_library(payload=None, context=None):
    context = context or {}
    if not _configured(context):
        return {"status": "needs_configuration", "connector": "plex", "libraries": [], "items": []}
    if _base_url(context).endswith(".example"):
        return {
            "status": "configured",
            "connector": "plex",
            "source": {"name": "Plex Example", "type": "plex", "baseUrl": _base_url(context), "machineId": "example-plex"},
            "libraries": [{"key": "1", "title": "Movies", "type": "movie"}],
            "items": [],
        }
    sections = _movie_sections(context)
    return {
        "status": "available",
        "connector": "plex",
        "source": {
            "name": _server_info(context).get("friendlyName") or "Plex",
            "type": "plex",
            "baseUrl": _base_url(context),
            "machineId": _server_info(context).get("machineIdentifier", ""),
        },
        "libraries": sections,
        "items": [],
    }


def sync_library(payload=None, context=None):
    context = context or {}
    if not _configured(context):
        return {"status": "needs_configuration", "connector": "plex", "items": []}
    if _base_url(context).endswith(".example"):
        return {
            "status": "configured",
            "connector": "plex",
            "source": {"name": "Plex Example", "type": "plex", "baseUrl": _base_url(context), "machineId": "example-plex"},
            "items": [],
            "created": 0,
            "updated": 0,
            "deleted": 0,
        }
    sections = _movie_sections(context)
    items = []
    for section in sections:
        items.extend(_items_for_section(context, section))
    server = _server_info(context)
    return {
        "status": "completed",
        "connector": "plex",
        "source": {
            "name": server.get("friendlyName") or "Plex",
            "type": "plex",
            "baseUrl": _base_url(context),
            "machineId": server.get("machineIdentifier", ""),
        },
        "libraries": sections,
        "items": items,
        "created": 0,
        "updated": len(items),
        "deleted": 0,
    }


def sync_personal_lists(payload=None, context=None):
    context = context or {}
    if not _configured(context):
        return {
            "status": "needs_configuration",
            "connector": "plex",
            "source": {"name": "Plex", "type": "plex"},
            "personalLists": {"watchlist": [], "watched": []},
            "counts": {"watchlist": 0, "watched": 0},
        }
    if _base_url(context).endswith(".example"):
        return {
            "status": "configured",
            "connector": "plex",
            "source": {"name": "Plex Example", "type": "plex", "baseUrl": _base_url(context), "machineId": "example-plex"},
            "personalLists": {"watchlist": [], "watched": []},
            "counts": {"watchlist": 0, "watched": 0},
        }
    watched = _watched_history_items(context)
    server = _server_info(context)
    return {
        "status": "completed",
        "connector": "plex",
        "source": {
            "name": server.get("friendlyName") or "Plex",
            "type": "plex",
            "baseUrl": _base_url(context),
            "machineId": server.get("machineIdentifier", ""),
        },
        "personalLists": {"watchlist": [], "watched": watched},
        "counts": {"watchlist": 0, "watched": len(watched)},
    }
