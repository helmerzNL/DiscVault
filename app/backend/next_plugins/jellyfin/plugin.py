import requests


def _settings(context):
    return (context or {}).get("settings") or {}


def _secrets(context):
    return (context or {}).get("secrets") or {}


def _base_url(context):
    return str(_settings(context).get("baseUrl") or "").strip().rstrip("/")


def _token(context):
    return str(_secrets(context).get("token") or "").strip()


def _configured(context):
    return bool(_base_url(context) and _token(context))


def health_check(context=None):
    context = context or {}
    if not _base_url(context):
        return {"status": "needs_configuration", "message": "Configure Server URL."}
    if _base_url(context).endswith(".example"):
        return {"status": "configured", "message": "Jellyfin example configuration accepted without a network call."}
    try:
        response = requests.get(f"{_base_url(context)}/System/Info/Public", timeout=8)
        response.raise_for_status()
        info = response.json()
        return {
            "status": "available",
            "message": f"Jellyfin {info.get('Version', 'server reachable')}",
            "serverName": info.get("ServerName", "Jellyfin"),
            "version": info.get("Version", ""),
        }
    except requests.HTTPError as exc:
        return {"status": "unavailable", "message": f"HTTP {exc.response.status_code}"}
    except Exception as exc:
        return {"status": "unavailable", "message": str(exc)}


def discover_library(payload=None, context=None):
    context = context or {}
    if not _base_url(context):
        return {"status": "needs_configuration", "connector": "jellyfin", "libraries": [], "items": []}
    if _base_url(context).endswith(".example"):
        return {
            "status": "configured",
            "connector": "jellyfin",
            "source": {"name": "Jellyfin Example", "type": "jellyfin", "baseUrl": _base_url(context), "machineId": ""},
            "libraries": [{"key": "movies", "title": "Movies", "type": "movie"}],
            "items": [],
        }
    health = health_check(context)
    return {
        "status": health.get("status", "unknown"),
        "connector": "jellyfin",
        "source": {
            "name": health.get("serverName") or "Jellyfin",
            "type": "jellyfin",
            "baseUrl": _base_url(context),
            "machineId": "",
        },
        "libraries": [{"key": "movies", "title": "Movies", "type": "movie"}],
        "items": [],
    }


def sync_library(payload=None, context=None):
    context = context or {}
    if not _configured(context):
        return {"status": "needs_configuration", "connector": "jellyfin", "items": []}
    if _base_url(context).endswith(".example"):
        return {
            "status": "configured",
            "connector": "jellyfin",
            "source": {"name": "Jellyfin Example", "type": "jellyfin", "baseUrl": _base_url(context), "machineId": ""},
            "items": [],
            "created": 0,
            "updated": 0,
            "deleted": 0,
        }

    response = requests.get(
        f"{_base_url(context)}/Items",
        params={
            "IncludeItemTypes": "Movie",
            "Recursive": "true",
            "Fields": "ProviderIds",
            "Limit": str((payload or {}).get("limit") or 5000),
            "api_key": _token(context),
        },
        timeout=30,
    )
    response.raise_for_status()
    items = []
    for item in response.json().get("Items", []):
        provider_ids = item.get("ProviderIds") or {}
        external_id = str(item.get("Id", ""))
        items.append(
            {
                "externalId": external_id,
                "title": item.get("Name", ""),
                "year": str(item.get("ProductionYear", "") or ""),
                "tmdbId": str(provider_ids.get("Tmdb") or provider_ids.get("tmdb") or ""),
                "imdbId": str(provider_ids.get("Imdb") or provider_ids.get("imdb") or ""),
                "mediaType": "movie",
                "playbackUrl": f"{_base_url(context)}/web/index.html#!/details?id={external_id}" if external_id else "",
                "metadata": {"providerIds": provider_ids},
            }
        )
    return {
        "status": "completed",
        "connector": "jellyfin",
        "source": {"name": "Jellyfin", "type": "jellyfin", "baseUrl": _base_url(context), "machineId": ""},
        "libraries": [{"key": "movies", "title": "Movies", "type": "movie"}],
        "items": items,
        "created": 0,
        "updated": len(items),
        "deleted": 0,
    }
