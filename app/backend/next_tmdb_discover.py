"""TMDb Discover helpers built on top of the existing TMDb plugin context."""

from __future__ import annotations

from typing import Any

try:  # pragma: no cover - exercised indirectly in both package layouts
    from .next_common import NextApiError
    from .next_plugin_runtime import run_plugin_health
    from .next_plugins.tmdb import plugin as tmdb_plugin
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_common import NextApiError
    from next_plugin_runtime import run_plugin_health
    from next_plugins.tmdb import plugin as tmdb_plugin


TMDB_PLUGIN_ID = "tmdb"
DEFAULT_LOCALE = "en-US"
DISCOVER_PAGE_MAX = 1000
SUPPORTED_MEDIA_TYPES = {"movie", "tv"}
SUPPORTED_MODES = {"popular", "trending"}


def _next_app():
    try:  # pragma: no cover - runtime layout dependent
        from . import next_app
    except ImportError:  # pragma: no cover - supports gunicorn next_app:app
        import next_app
    return next_app


def normalize_locale(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return DEFAULT_LOCALE
    compact = text.replace("_", "-")
    if "-" not in compact:
        return compact.lower()
    language, _, region = compact.partition("-")
    language = language.lower()
    region = region.upper()
    if not language:
        return DEFAULT_LOCALE
    return f"{language}-{region}" if region else language


def _fetch_tmdb_plugin(conn) -> dict[str, Any] | None:
    _app = _next_app()
    if not _app.table_exists(conn, "plugins"):
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id,
                name,
                version,
                enabled,
                installed,
                categories,
                capabilities,
                manifest,
                settings_schema
            FROM plugins
            WHERE id=%s
            LIMIT 1
            """,
            (TMDB_PLUGIN_ID,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def tmdb_discover_context(
    conn,
    *,
    actor: dict[str, Any] | None,
    locale: str | None = None,
) -> dict[str, Any]:
    _app = _next_app()
    plugin = _fetch_tmdb_plugin(conn)
    if not plugin or not plugin.get("installed") or not plugin.get("enabled"):
        return {
            "configured": False,
            "reason": "plugin_unavailable",
            "message": "TMDb plugin is not enabled.",
        }
    config = _app.plugin_config_from_db(conn, TMDB_PLUGIN_ID)
    context = _app.plugin_execution_context(conn, plugin, config, actor)
    language = normalize_locale(locale)
    settings = context.get("settings") if isinstance(context.get("settings"), dict) else {}
    context["settings"] = {**settings, "language": language}
    health = run_plugin_health(TMDB_PLUGIN_ID, context)
    if str(health.get("status") or "").strip() == "needs_configuration":
        return {
            "configured": False,
            "reason": "missing_api_key",
            "message": "TMDb API key is missing.",
            "context": context,
        }
    return {"configured": True, "context": context}


def _normalize_discover_item(item: dict[str, Any], media_type: str) -> dict[str, Any] | None:
    tmdb_id = item.get("id")
    if not tmdb_id:
        return None
    title = item.get("title") or item.get("name") or item.get("original_title") or item.get("original_name") or ""
    if not title:
        return None
    release_date = item.get("release_date") or item.get("first_air_date") or ""
    return {
        "id": str(tmdb_id),
        "tmdbId": str(tmdb_id),
        "mediaType": media_type,
        "title": title,
        "year": str(release_date)[:4] if release_date else "",
        "releaseDate": release_date,
        "overview": item.get("overview") or "",
        "posterUrl": tmdb_plugin._image(item.get("poster_path")),
        "backdropUrl": tmdb_plugin._image(item.get("backdrop_path")),
        "voteAverage": item.get("vote_average"),
    }


def discover_feed(context: dict[str, Any], *, media_type: str, mode: str, page: int) -> dict[str, Any]:
    clean_media_type = str(media_type or "movie").strip().lower()
    if clean_media_type not in SUPPORTED_MEDIA_TYPES:
        raise NextApiError("Unsupported discover media type", 400)
    clean_mode = str(mode or "popular").strip().lower()
    if clean_mode not in SUPPORTED_MODES:
        raise NextApiError("Unsupported discover mode", 400)
    page_number = max(min(int(page or 1), DISCOVER_PAGE_MAX), 1)
    if clean_mode == "trending":
        payload = tmdb_plugin._request(  # noqa: SLF001 - reusing shared plugin transport
            context,
            f"/trending/{clean_media_type}/week",
            page=page_number,
            language=normalize_locale((context.get("settings") or {}).get("language")),
        )
    else:
        payload = tmdb_plugin._request(  # noqa: SLF001 - reusing shared plugin transport
            context,
            f"/discover/{clean_media_type}",
            page=page_number,
            sort_by="popularity.desc",
            include_adult="false",
            include_video="false",
            language=normalize_locale((context.get("settings") or {}).get("language")),
        )
    normalized: list[dict[str, Any]] = []
    for row in payload.get("results") or []:
        item = _normalize_discover_item(row or {}, clean_media_type)
        if item:
            normalized.append(item)
    total_pages = int(payload.get("total_pages") or 1)
    return {
        "items": normalized,
        "page": page_number,
        "totalPages": max(total_pages, 1),
        "hasMore": page_number < total_pages,
    }


def _director_and_cast(data: dict[str, Any], media_type: str) -> tuple[str, str]:
    credits = data.get("credits") or {}
    crew = credits.get("crew") or []
    cast = credits.get("cast") or []
    if media_type == "tv":
        directors = [str(row.get("name") or "").strip() for row in (data.get("created_by") or []) if str(row.get("name") or "").strip()]
    else:
        directors = [str(row.get("name") or "").strip() for row in crew if str(row.get("job") or "") == "Director" and str(row.get("name") or "").strip()]
    actors = [str(row.get("name") or "").strip() for row in cast[:8] if str(row.get("name") or "").strip()]
    return ", ".join(directors), ", ".join(actors)


def _overview_with_fallback(context: dict[str, Any], data: dict[str, Any], media_type: str, tmdb_id: str) -> str:
    text = str(data.get("overview") or "").strip()
    if text:
        return text
    current_language = normalize_locale((context.get("settings") or {}).get("language"))
    if current_language.lower().startswith("en"):
        return ""
    english_context = {
        **context,
        "settings": {**(context.get("settings") or {}), "language": "en-US"},
    }
    english_data = tmdb_plugin._request(  # noqa: SLF001 - reusing shared plugin transport
        english_context,
        f"/{media_type}/{tmdb_id}",
        append_to_response="credits,images,translations",
        include_image_language="null,en",
        language="en-US",
    )
    return str(english_data.get("overview") or "").strip()


def discover_detail(context: dict[str, Any], *, media_type: str, tmdb_id: str) -> dict[str, Any]:
    clean_media_type = str(media_type or "movie").strip().lower()
    if clean_media_type not in SUPPORTED_MEDIA_TYPES:
        raise NextApiError("Unsupported discover media type", 400)
    item_id = str(tmdb_id or "").strip()
    if not item_id.isdigit():
        raise NextApiError("Invalid TMDb id", 400)
    data = tmdb_plugin._request(  # noqa: SLF001 - reusing shared plugin transport
        context,
        f"/{clean_media_type}/{item_id}",
        append_to_response="credits,images,translations",
        include_image_language="null,en",
        language=normalize_locale((context.get("settings") or {}).get("language")),
    )
    title = data.get("title") or data.get("name") or data.get("original_title") or data.get("original_name") or ""
    release_date = data.get("release_date") or data.get("first_air_date") or ""
    director, actors = _director_and_cast(data, clean_media_type)
    overview = _overview_with_fallback(context, data, clean_media_type, item_id)
    return {
        "tmdbId": item_id,
        "mediaType": clean_media_type,
        "title": title,
        "year": str(release_date)[:4] if release_date else "",
        "releaseDate": release_date,
        "director": director,
        "actors": actors,
        "overview": overview,
        "posterUrl": tmdb_plugin._image(data.get("poster_path")),
        "backdropUrl": tmdb_plugin._image(data.get("backdrop_path")),
        "budget": data.get("budget") if clean_media_type == "movie" else None,
        "revenue": data.get("revenue") if clean_media_type == "movie" else None,
        "awardNominations": None,
        "showtimes": {
            "supported": False,
            "reason": "tmdb_no_local_showtimes",
        },
    }
