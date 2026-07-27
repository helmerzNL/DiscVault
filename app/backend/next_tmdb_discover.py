"""TMDb Discover helpers built on top of the existing TMDb plugin context."""

from __future__ import annotations

from datetime import date
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
DEFAULT_MODE = "popular"
# Mirrors the four category pills on iOS/Android (Popular, Now playing, Upcoming, Top rated)
# plus the pre-existing "trending" mode used by the API.
SUPPORTED_MODES = ("popular", "now_playing", "upcoming", "top_rated", "trending")
DISCOVER_PILL_MODES = ("popular", "now_playing", "upcoming", "top_rated")
_MODE_ALIASES = {
    "nowplaying": "now_playing",
    "in_theatres": "now_playing",
    "theatres": "now_playing",
    "theaters": "now_playing",
    "cinema": "now_playing",
    "toprated": "top_rated",
    "top": "top_rated",
    "soon": "upcoming",
}


def normalize_discover_mode(value: Any) -> str:
    """Accept camelCase/kebab-case/snake_case mode names from any client."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        return DEFAULT_MODE
    resolved = _MODE_ALIASES.get(text, text)
    return resolved if resolved in SUPPORTED_MODES else DEFAULT_MODE


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


def _discover_request_spec(*, media_type: str, mode: str, language: str, page: int) -> tuple[str, dict[str, Any]]:
    """Map a Discover pill to the TMDb endpoint iOS/Android use for the same category."""
    base: dict[str, Any] = {"page": page, "language": language}
    if mode == "trending":
        return f"/trending/{media_type}/week", base
    if mode == "now_playing":
        # TV has no "now playing"; "on the air" is the equivalent currently-running list.
        return ("/tv/on_the_air" if media_type == "tv" else "/movie/now_playing"), base
    if mode == "upcoming":
        if media_type == "tv":
            # TMDb offers no /tv/upcoming, so approximate it with future first air dates.
            return "/discover/tv", {
                **base,
                "sort_by": "first_air_date.asc",
                "include_adult": "false",
                "first_air_date.gte": date.today().isoformat(),
            }
        return "/movie/upcoming", base
    if mode == "top_rated":
        return f"/{media_type}/top_rated", base
    return f"/discover/{media_type}", {
        **base,
        "sort_by": "popularity.desc",
        "include_adult": "false",
        "include_video": "false",
    }


def discover_feed(context: dict[str, Any], *, media_type: str, mode: str, page: int) -> dict[str, Any]:
    clean_media_type = str(media_type or "movie").strip().lower()
    if clean_media_type not in SUPPORTED_MEDIA_TYPES:
        raise NextApiError("Unsupported discover media type", 400)
    clean_mode = normalize_discover_mode(mode)
    page_number = max(min(int(page or 1), DISCOVER_PAGE_MAX), 1)
    path, params = _discover_request_spec(
        media_type=clean_media_type,
        mode=clean_mode,
        language=normalize_locale((context.get("settings") or {}).get("language")),
        page=page_number,
    )
    payload = tmdb_plugin._request(context, path, **params)  # noqa: SLF001 - reusing shared plugin transport
    normalized: list[dict[str, Any]] = []
    for row in payload.get("results") or []:
        item = _normalize_discover_item(row or {}, clean_media_type)
        if item:
            normalized.append(item)
    total_pages = int(payload.get("total_pages") or 1)
    bounded_total_pages = max(min(total_pages, DISCOVER_PAGE_MAX), 1)
    return {
        "items": normalized,
        "mode": clean_mode,
        "page": page_number,
        "totalPages": bounded_total_pages,
        "hasMore": page_number < bounded_total_pages,
    }


def _credit_person(
    *,
    tmdb_id: Any,
    name: Any,
    role: Any = "",
    job: Any = "",
) -> dict[str, str] | None:
    clean_name = str(name or "").strip()
    if not clean_name:
        return None
    clean_id = str(tmdb_id or "").strip()
    clean_role = str(role or "").strip()
    clean_job = str(job or "").strip()
    return {
        "tmdbId": clean_id,
        "name": clean_name,
        "role": clean_role,
        "job": clean_job,
    }


def _director_and_cast(data: dict[str, Any], media_type: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    credits = data.get("credits") or {}
    crew = credits.get("crew") or []
    cast = credits.get("cast") or []
    directors: list[dict[str, str]] = []
    if media_type == "tv":
        for row in data.get("created_by") or []:
            person = _credit_person(
                tmdb_id=row.get("id"),
                name=row.get("name"),
                job="Creator",
            )
            if person:
                directors.append(person)
    else:
        for row in crew:
            if str(row.get("job") or "").strip().lower() != "director":
                continue
            person = _credit_person(
                tmdb_id=row.get("id"),
                name=row.get("name"),
                job=row.get("job"),
            )
            if person:
                directors.append(person)
    actors: list[dict[str, str]] = []
    for row in cast[:8]:
        person = _credit_person(
            tmdb_id=row.get("id"),
            name=row.get("name"),
            role=row.get("character"),
            job=row.get("known_for_department"),
        )
        if person:
            actors.append(person)
    return directors, actors


def _runtime_minutes(data: dict[str, Any], media_type: str) -> int | None:
    if media_type == "movie":
        value = int(data.get("runtime") or 0)
        return value if value > 0 else None
    for entry in data.get("episode_run_time") or []:
        value = int(entry or 0)
        if value > 0:
            return value
    return None


def _content_ratings(data: dict[str, Any], media_type: str) -> dict[str, str]:
    ratings: dict[str, str] = {}
    if media_type == "movie":
        entries = ((data.get("release_dates") or {}).get("results") or [])
        for entry in entries:
            country = str(entry.get("iso_3166_1") or "").strip().upper()
            if not country:
                continue
            release_rows = entry.get("release_dates") or []
            certification = ""
            for release in release_rows:
                text = str(release.get("certification") or "").strip()
                if text:
                    certification = text
                    if int(release.get("type") or 0) == 3:
                        break
            if certification:
                ratings[country] = certification
        return ratings
    entries = ((data.get("content_ratings") or {}).get("results") or [])
    for entry in entries:
        country = str(entry.get("iso_3166_1") or "").strip().upper()
        rating = str(entry.get("rating") or "").strip()
        if country and rating:
            ratings[country] = rating
    return ratings


def _locale_region(context: dict[str, Any]) -> str:
    locale = normalize_locale((context.get("settings") or {}).get("language"))
    if "-" in locale:
        region = locale.split("-", 1)[1].strip().upper()
        if len(region) == 2:
            return region
    return "US"


def _is_released(release_date: str) -> bool:
    clean_release_date = str(release_date or "").strip()
    if len(clean_release_date) < 10:
        return False
    try:
        return date.fromisoformat(clean_release_date[:10]) <= date.today()
    except ValueError:
        return False


def _streaming_providers(context: dict[str, Any], tmdb_id: str, *, release_date: str) -> tuple[str, list[dict[str, str]]]:
    if not _is_released(release_date):
        return "", []
    try:
        payload = tmdb_plugin._request(  # noqa: SLF001 - reusing shared plugin transport
            context,
            f"/movie/{tmdb_id}/watch/providers",
        )
    except NextApiError:
        return "", []
    results = payload.get("results") if isinstance(payload.get("results"), dict) else {}
    preferred_region = _locale_region(context)
    region_payload = results.get(preferred_region)
    region = preferred_region
    if not isinstance(region_payload, dict):
        region = "US" if isinstance(results.get("US"), dict) else ""
        region_payload = results.get(region) if region else None
    if not isinstance(region_payload, dict):
        for key, candidate in results.items():
            if isinstance(candidate, dict):
                region = str(key or "").strip().upper()
                region_payload = candidate
                break
    if not isinstance(region_payload, dict):
        return "", []
    rows = region_payload.get("flatrate") or []
    providers: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        provider_id = str(row.get("provider_id") or "").strip()
        name = str(row.get("provider_name") or "").strip()
        if not provider_id or not name or provider_id in seen:
            continue
        seen.add(provider_id)
        providers.append(
            {
                "providerId": provider_id,
                "name": name,
                "logoUrl": tmdb_plugin._image(row.get("logo_path")),
            }
        )
    return region, providers


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
        append_to_response="credits,images,translations,release_dates,content_ratings",
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
        append_to_response="credits,images,translations,release_dates,content_ratings",
        include_image_language="null,en",
        language=normalize_locale((context.get("settings") or {}).get("language")),
    )
    title = data.get("title") or data.get("name") or data.get("original_title") or data.get("original_name") or ""
    release_date = data.get("release_date") or data.get("first_air_date") or ""
    director_people, cast_people = _director_and_cast(data, clean_media_type)
    overview = _overview_with_fallback(context, data, clean_media_type, item_id)
    runtime_minutes = _runtime_minutes(data, clean_media_type)
    content_ratings = _content_ratings(data, clean_media_type)
    providers_region = ""
    streaming_providers: list[dict[str, str]] = []
    if clean_media_type == "movie":
        providers_region, streaming_providers = _streaming_providers(context, item_id, release_date=release_date)
    return {
        "tmdbId": item_id,
        "mediaType": clean_media_type,
        "title": title,
        "year": str(release_date)[:4] if release_date else "",
        "releaseDate": release_date,
        "runtimeMinutes": runtime_minutes,
        "director": ", ".join([item["name"] for item in director_people]),
        "actors": ", ".join([item["name"] for item in cast_people]),
        "directorPeople": director_people,
        "castPeople": cast_people,
        "contentRatings": content_ratings,
        "providersRegion": providers_region,
        "streamingProviders": streaming_providers,
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
