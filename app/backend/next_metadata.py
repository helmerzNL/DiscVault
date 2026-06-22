"""Metadata source execution and merge policy for DiscVault Next."""

from __future__ import annotations

import json
import hashlib
import os
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

try:
    from psycopg.types.json import Jsonb
except ModuleNotFoundError:  # pragma: no cover - allows policy tests without psycopg
    class Jsonb:  # type: ignore[no-redef]
        def __init__(self, value: Any) -> None:
            self.value = value

try:
    from .next_import import clean_text
    from .next_movievault_connection import MOVIEVAULT_PLUGIN_ID
    from .next_movievault_connection import MOVIEVAULT_PLUGIN_IDS
    from .next_movievault_connection import is_movievault_plugin
    from .next_movievault_connection import movievault_plugin_context
    from .next_plugin_runtime import run_plugin_entrypoint
    from .next_plugin_runtime import sync_plugin_registry
except ImportError:  # pragma: no cover - supports direct module execution
    from next_import import clean_text
    from next_movievault_connection import MOVIEVAULT_PLUGIN_ID
    from next_movievault_connection import MOVIEVAULT_PLUGIN_IDS
    from next_movievault_connection import is_movievault_plugin
    from next_movievault_connection import movievault_plugin_context
    from next_plugin_runtime import run_plugin_entrypoint
    from next_plugin_runtime import sync_plugin_registry


METADATA_REFRESH_JOB_TYPE = "metadata.refresh_movie"

METADATA_MAIN_FIELDS = {
    "title",
    "sort_title",
    "original_title",
    "release_title",
    "year",
    "release_date",
    "format",
    "edition",
    "edition_type",
    "country",
    "language",
    "runtime_minutes",
    "overview",
    "rating",
}

METADATA_LOCAL_ONLY_FIELDS = {
    "barcode",
    "public_id",
    "owner_id",
    "purchase_date",
    "purchase_price",
    "location",
    "notes",
}

METADATA_MANUAL_PROTECTED_FIELDS = {
    "title",
    "sort_title",
    "original_title",
    "overview",
    "poster_url",
    "backdrop_url",
    "backdrop_urls",
    "trailer_url",
    "videos",
}

METADATA_DISPLAY_TITLE_FIELDS = {
    "title",
    "sort_title",
    "original_title",
}

METADATA_TECHNICAL_FIELDS = {
    "hdr",
    "packaging",
    "screen_ratios",
    "audio_tracks",
    "subtitles",
    "regions",
    "content_ratings",
}

MOVIE_METADATA_LOCKS_KEY = "field_locks"

MOVIE_LOCKABLE_FIELDS = {
    "title",
    "sort_title",
    "original_title",
    "year",
    "barcode",
    "release_date",
    "format",
    "edition",
    "country",
    "language",
    "location",
    "overview",
    "notes",
    "runtime_minutes",
    "director",
    "genre",
    "studios",
    "distributor",
    "hdr",
    "packaging",
    "screen_ratios",
    "audio_tracks",
    "subtitles",
    "content_ratings",
}


def normalize_movie_field_locks(value: Any) -> list[str]:
    """Return a sorted, de-duplicated list of recognised lockable field names."""
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        return []
    result: set[str] = set()
    for item in items:
        name = str(item or "").strip()
        if name in MOVIE_LOCKABLE_FIELDS:
            result.add(name)
    return sorted(result)


def movie_locked_fields(metadata: Any) -> set[str]:
    """Read the set of locked field names stored on a movie's metadata."""
    if not isinstance(metadata, dict):
        return set()
    raw = metadata.get(MOVIE_METADATA_LOCKS_KEY)
    if raw is None:
        raw = metadata.get("fieldLocks")
    return set(normalize_movie_field_locks(raw))


# Maps a canonical lockable field name to the receiver-payload keys that carry
# its value, so locked fields can be stripped before pushing to receivers.
MOVIE_LOCK_RECEIVER_KEYS: dict[str, tuple[str, ...]] = {
    "title": ("title",),
    "sort_title": ("sortTitle",),
    "original_title": ("originalTitle",),
    "year": ("year",),
    "barcode": ("barcode",),
    "release_date": ("releaseDate",),
    "format": ("format",),
    "edition": ("edition", "editionType"),
    "country": ("country",),
    "language": ("language",),
    "location": ("location",),
    "overview": ("overview",),
    "notes": ("notes",),
    "runtime_minutes": ("runtimeMinutes",),
    "director": ("director",),
    "genre": ("genre",),
    "studios": ("studios", "studio"),
    "distributor": ("distributor",),
    "hdr": ("hdr",),
    "packaging": ("packaging",),
    "screen_ratios": ("screenRatios", "screen_ratios"),
    "audio_tracks": ("audioTracks", "audio_tracks"),
    "subtitles": ("subtitles",),
    "content_ratings": ("contentRatings", "content_ratings"),
}


def locked_receiver_payload_keys(metadata: Any) -> set[str]:
    """Return the set of receiver-payload keys to drop for a movie's locked fields."""
    drop: set[str] = set()
    for field in movie_locked_fields(metadata):
        drop.update(MOVIE_LOCK_RECEIVER_KEYS.get(field, ()))
    return drop


METADATA_LIST_FIELDS = {
    "audio_tracks",
    "subtitles",
    "regions",
    "backdrop_urls",
    "videos",
}

METADATA_RELEASE_FIELDS = {
    "format",
    "edition",
    "edition_type",
    "country",
    "language",
    *METADATA_TECHNICAL_FIELDS,
}

METADATA_IDENTIFIER_TYPES = {
    "tmdb_id": ("tmdb", "movie_id"),
    "tmdbId": ("tmdb", "movie_id"),
    "imdb_id": ("imdb", "movie_id"),
    "imdbId": ("imdb", "movie_id"),
}

METADATA_MEDIA_FIELDS = {
    "poster_url": "poster",
    "backdrop_url": "backdrop",
}

METADATA_NAMESPACE = uuid.UUID("7c76309b-063d-4c63-b925-2f49fdad332c")

MOVIE_FIELD_ALIASES = {
    "sortTitle": "sort_title",
    "originalTitle": "original_title",
    "releaseTitle": "release_title",
    "release_title": "release_title",
    "releaseDate": "release_date",
    "runtime": "runtime_minutes",
    "runtimeMinutes": "runtime_minutes",
    "audienceRating": "audience_rating",
    "poster": "poster_url",
    "posterUrl": "poster_url",
    "poster_url": "poster_url",
    "backdrop": "backdrop_url",
    "backdropUrl": "backdrop_url",
    "backdrop_url": "backdrop_url",
    "backdrops": "backdrop_urls",
    "backdropUrls": "backdrop_urls",
    "backdrop_urls": "backdrop_urls",
    "trailerUrl": "trailer_url",
    "trailer_url": "trailer_url",
    "contentRatings": "content_ratings",
    "content_ratings": "content_ratings",
    "audioTracks": "audio_tracks",
    "audio_tracks": "audio_tracks",
    "screenRatios": "screen_ratios",
    "screen_ratios": "screen_ratios",
}


def table_exists(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS table_name", (f"public.{table_name}",))
        row = cur.fetchone()
    return bool(row and row.get("table_name"))


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [json_ready(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def bool_value(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def value_present(value: Any) -> bool:
    return value not in (None, "", [], {})


def normalize_media_format(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if re.search(r"4k|uhd|ultra\s*hd", text):
        return "4K UHD"
    if re.search(r"blu[- ]?ray", text):
        return "Blu-ray"
    if re.search(r"\bdvd\b", text):
        return "DVD"
    return ""


def detect_format_from_text(value: Any) -> str:
    return normalize_media_format(value)


def external_metadata_barcode(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    upper = text.upper()
    if upper.startswith("IMPORT-") or "-BOX-" in upper or upper.startswith("LEGACY-"):
        return ""
    return text if re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z ._-]{3,64}", text) else ""


def plugin_config_payload(settings: Any, secrets_ref: Any) -> dict[str, Any]:
    safe_settings = settings if isinstance(settings, dict) else {}
    refs = secrets_ref if isinstance(secrets_ref, dict) else {}
    safe_refs: dict[str, dict[str, Any]] = {}
    for name, ref in refs.items():
        key = ref.get("key") if isinstance(ref, dict) else ref
        item: dict[str, Any] = {"configured": True}
        if key:
            item["key"] = str(key)
        safe_refs[str(name)] = item
    return {
        "settings": safe_settings,
        "settingsConfigured": bool(safe_settings),
        "secretNames": sorted(safe_refs),
        "secretsConfigured": bool(safe_refs),
        "secretsRef": safe_refs,
    }


def plugin_config_from_db(conn, plugin_id: str) -> dict[str, Any]:
    if not table_exists(conn, "plugin_settings"):
        return plugin_config_payload({}, {})
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT settings, secrets_ref
            FROM plugin_settings
            WHERE plugin_id=%s
            """,
            (plugin_id,),
        )
        row = cur.fetchone()
    return plugin_config_payload(
        row.get("settings") if row else {},
        row.get("secrets_ref") if row else {},
    )


def plugin_secret_values(conn, config: dict[str, Any]) -> dict[str, Any]:
    refs = config.get("secretsRef") or {}
    keys = [
        str(ref.get("key"))
        for ref in refs.values()
        if isinstance(ref, dict) and ref.get("key")
    ]
    if not keys or not table_exists(conn, "app_settings"):
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT key, value
            FROM app_settings
            WHERE key = ANY(%s) AND is_secret = true
            """,
            (keys,),
        )
        rows = cur.fetchall()
    by_key = {row["key"]: row["value"] for row in rows}
    values = {}
    for name, ref in refs.items():
        key = ref.get("key") if isinstance(ref, dict) else None
        if key in by_key:
            values[str(name)] = by_key[key]
    return values


def metadata_app_settings(conn) -> dict[str, Any]:
    if not table_exists(conn, "app_settings"):
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT key, value
            FROM app_settings
            WHERE is_secret = false
              AND key IN ('metadata_preferred_provider_overwrite')
            """
        )
        return {row["key"]: row["value"] for row in cur.fetchall()}


def preferred_provider_overwrite(conn) -> bool:
    value = metadata_app_settings(conn).get("metadata_preferred_provider_overwrite")
    return bool_value(value, default=False)


def sync_metadata_registry(conn) -> None:
    sync_plugin_registry(conn, table_exists, Jsonb)


def metadata_source_plugins(conn) -> list[dict[str, Any]]:
    if not table_exists(conn, "plugins"):
        return []
    sync_metadata_registry(conn)
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
                order_index,
                manifest,
                settings_schema,
                premium_feature_key,
                source_path,
                runtime_module,
                updated_at
            FROM plugins
            WHERE installed = true
              AND enabled = true
              AND categories ? 'metadata_source'
            ORDER BY order_index, lower(name)
            """
        )
        rows = cur.fetchall()
    return [dict(row) for row in rows]


def plugin_categories(plugin: dict[str, Any]) -> set[str]:
    manifest = plugin.get("manifest") if isinstance(plugin.get("manifest"), dict) else {}
    values = plugin.get("categories") or manifest.get("categories") or []
    return {str(item) for item in values if str(item)}


def plugin_capabilities(plugin: dict[str, Any]) -> set[str]:
    manifest = plugin.get("manifest") if isinstance(plugin.get("manifest"), dict) else {}
    values = plugin.get("capabilities") or manifest.get("capabilities") or []
    return {str(item) for item in values if str(item)}


def plugin_is_bootstrap_metadata_source(plugin: dict[str, Any]) -> bool:
    manifest = plugin.get("manifest") if isinstance(plugin.get("manifest"), dict) else {}
    bootstrap = manifest.get("bootstrap") if isinstance(manifest.get("bootstrap"), dict) else {}
    return (
        "metadata_bootstrap" in plugin_categories(plugin)
        or "bootstrap_lookup" in plugin_capabilities(plugin)
        or bool(bootstrap.get("metadataSource"))
    )


def metadata_bootstrap_lookup_allowed(query: dict[str, Any]) -> bool:
    # Bootstrap metadata sources are barcode hint providers, not deep metadata
    # enrichers. They may run whenever DiscVault has a public barcode, but the
    # normal source stack still decides which fields are accepted.
    return bool(query.get("externalBarcode"))


def metadata_source_plugin_allowed(plugin: dict[str, Any], query: dict[str, Any]) -> bool:
    if not plugin_is_bootstrap_metadata_source(plugin):
        return True
    return metadata_bootstrap_lookup_allowed(query)


def metadata_receiver_plugins(conn) -> list[dict[str, Any]]:
    if not table_exists(conn, "plugins"):
        return []
    sync_metadata_registry(conn)
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
                order_index,
                manifest,
                settings_schema,
                premium_feature_key,
                source_path,
                runtime_module,
                updated_at
            FROM plugins
            WHERE installed = true
              AND enabled = true
              AND categories ? 'metadata_receiver'
            ORDER BY order_index, lower(name)
            """
        )
        rows = cur.fetchall()
    return [dict(row) for row in rows]


def plugin_execution_context(
    conn,
    plugin: dict[str, Any],
    config: dict[str, Any],
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = plugin.get("manifest") or {}
    context = {
        "pluginId": plugin.get("id"),
        "pluginName": plugin.get("name"),
        "enabled": bool(plugin.get("enabled")),
        "categories": plugin.get("categories") or manifest.get("categories") or [],
        "capabilities": plugin.get("capabilities") or manifest.get("capabilities") or [],
        "settings": config.get("settings") or {},
        "secrets": plugin_secret_values(conn, config),
        "settingsConfigured": bool(config.get("settingsConfigured")),
        "secretNames": config.get("secretNames") or [],
        "secretsConfigured": bool(config.get("secretsConfigured")),
        "actor": {
            "id": str(actor.get("id")) if actor and actor.get("id") else None,
            "username": actor.get("username") if actor else None,
            "role": actor.get("role") if actor else None,
        },
    }
    return movievault_plugin_context(
        conn,
        str(plugin.get("id") or ""),
        context,
        ensure_token=is_movievault_plugin(str(plugin.get("id") or "")),
        actor_id=actor.get("id") if actor else None,
    )


def plugin_requires_config(plugin: dict[str, Any], config: dict[str, Any], entrypoint: str) -> bool:
    if entrypoint == "health_check":
        return False
    if is_movievault_plugin(str(plugin.get("id") or "")):
        return False
    manifest = plugin.get("manifest") or {}
    return bool(plugin.get("requiresSecrets") or manifest.get("requiresSecrets")) and not bool(config.get("secretsConfigured"))


def movie_identifiers(conn, movie_id: UUID) -> dict[str, str]:
    if not table_exists(conn, "movie_identifiers"):
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT provider_id, identifier_type, identifier
            FROM movie_identifiers
            WHERE movie_id=%s
            """,
            (movie_id,),
        )
        rows = cur.fetchall()
    values = {}
    for row in rows:
        provider = str(row["provider_id"])
        identifier_type = str(row["identifier_type"])
        if provider == "tmdb" and identifier_type == "movie_id":
            values["tmdb_id"] = row["identifier"]
            values["tmdbId"] = row["identifier"]
        elif provider == "imdb" and identifier_type == "movie_id":
            values["imdb_id"] = row["identifier"]
            values["imdbId"] = row["identifier"]
    return values


def movie_container_context(conn, movie_id: UUID) -> list[dict[str, Any]]:
    if not table_exists(conn, "container_movies") or not table_exists(conn, "containers"):
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                c.id,
                c.container_type,
                c.title,
                c.barcode,
                c.metadata,
                cm.sort_order
            FROM container_movies cm
            JOIN containers c ON c.id = cm.container_id
            WHERE cm.movie_id=%s
            ORDER BY c.container_type, cm.sort_order, lower(c.title)
            """,
            (movie_id,),
        )
        return [dict(row) for row in cur.fetchall()]


def movie_technical_specs(conn, movie_id: UUID) -> dict[str, Any]:
    if not table_exists(conn, "movie_technical_specs"):
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT hdr, packaging, screen_ratios, audio_tracks, subtitles, regions, content_ratings
            FROM movie_technical_specs
            WHERE movie_id=%s
            """,
            (movie_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else {}


def movie_contribution_credits(conn, movie_id: UUID, *, limit: int = 80) -> list[dict[str, Any]]:
    if not table_exists(conn, "movie_credits") or not table_exists(conn, "people"):
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT mc.credit_type, mc.character, mc.job, mc.sort_order, p.name
            FROM movie_credits mc
            JOIN people p ON p.id = mc.person_id
            WHERE mc.movie_id=%s
            ORDER BY mc.sort_order, p.name
            LIMIT %s
            """,
            (movie_id, limit),
        )
        return [dict(row) for row in cur.fetchall()]


def movie_lookup_context(conn, movie_id: UUID) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id,
                public_id,
                barcode,
                title,
                sort_title,
                original_title,
                year,
                release_date,
                format,
                edition,
                edition_type,
                country,
                language,
                runtime_minutes,
                overview,
                rating,
                metadata
            FROM movies
            WHERE id=%s
            """,
            (movie_id,),
        )
        movie = cur.fetchone()
    if not movie:
        raise RuntimeError("Movie not found")
    metadata = movie.get("metadata") if isinstance(movie.get("metadata"), dict) else {}
    identifiers = movie_identifiers(conn, movie_id)
    technical = movie_technical_specs(conn, movie_id)
    containers = movie_container_context(conn, movie_id)
    box_sets = [item for item in containers if item.get("container_type") == "box_set"]
    query = {
        "movieId": str(movie_id),
        "title": movie.get("original_title") or movie.get("title") or "",
        "fallbackTitle": movie.get("title") or "",
        "year": movie.get("year") or "",
        "barcode": movie.get("barcode") or "",
        "externalBarcode": external_metadata_barcode(movie.get("barcode")),
        "format": movie.get("format") or "",
        "normalizedFormat": normalize_media_format(movie.get("format")),
        "tmdbId": identifiers.get("tmdbId") or metadata.get("tmdb_id") or metadata.get("tmdbId") or "",
        "imdbId": identifiers.get("imdbId") or metadata.get("imdb_id") or metadata.get("imdbId") or "",
        "memberOfBoxSet": bool(box_sets),
        "parentBoxSets": [
            {
                "id": str(item.get("id")),
                "title": item.get("title"),
                "barcode": item.get("barcode"),
                "sortOrder": item.get("sort_order"),
                "metadata": item.get("metadata") or {},
            }
            for item in box_sets
        ],
        "containers": containers,
    }
    return {
        "movie": dict(movie),
        "metadata": metadata,
        "identifiers": identifiers,
        "technicalSpecs": technical,
        "query": query,
    }


def query_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    title = clean_text(payload.get("title") or payload.get("originalTitle") or payload.get("original_title"))
    fallback = clean_text(payload.get("fallbackTitle") or payload.get("fallback_title"))
    barcode = clean_text(payload.get("barcode"))
    media_format = clean_text(payload.get("format") or payload.get("mediaFormat") or payload.get("media_format"))
    return {
        "title": title,
        "fallbackTitle": fallback,
        "year": clean_text(payload.get("year")),
        "barcode": barcode,
        "externalBarcode": external_metadata_barcode(barcode),
        "format": media_format,
        "normalizedFormat": normalize_media_format(media_format),
        "tmdbId": clean_text(payload.get("tmdbId") or payload.get("tmdb_id")),
        "imdbId": clean_text(payload.get("imdbId") or payload.get("imdb_id")),
        "memberOfBoxSet": bool(payload.get("memberOfBoxSet") or payload.get("member_of_box_set")),
        "detectBoxSets": bool(payload.get("detectBoxSets") or payload.get("detect_box_sets") or payload.get("importBoxSets") or payload.get("import_box_sets")),
        "previewMode": bool(payload.get("previewMode") or payload.get("preview_mode")),
        "parentBoxSets": payload.get("parentBoxSets") or payload.get("parent_box_sets") or [],
    }


def plugin_execution_plan(plugin: dict[str, Any], query: dict[str, Any]) -> list[dict[str, Any]]:
    capabilities = set(plugin.get("capabilities") or (plugin.get("manifest") or {}).get("capabilities") or [])
    plan: list[dict[str, Any]] = []
    external_barcode = clean_text(query.get("externalBarcode"))
    title = clean_text(query.get("title"))
    fallback = clean_text(query.get("fallbackTitle"))
    tmdb_id = clean_text(query.get("tmdbId"))
    imdb_id = clean_text(query.get("imdbId"))

    def add(entrypoint: str, payload: dict[str, Any]) -> None:
        if entrypoint in capabilities and not any(item["entrypoint"] == entrypoint for item in plan):
            plan.append({"entrypoint": entrypoint, "payload": payload})

    base_payload = dict(query)
    if query.get("previewMode"):
        if external_barcode:
            add("search_barcode", {**base_payload, "barcode": external_barcode})
            if query.get("detectBoxSets"):
                add("box_set_candidates", base_payload)
            return plan
        if title:
            if "search_title" in capabilities:
                add("search_title", base_payload)
            add("movie_details", base_payload)
            if query.get("detectBoxSets"):
                add("box_set_candidates", base_payload)
            return plan
    if external_barcode:
        add("search_barcode", {**base_payload, "barcode": external_barcode})
    if tmdb_id or imdb_id:
        add("lookup_external_id", base_payload)
    has_external_lookup = bool(tmdb_id or imdb_id) and "lookup_external_id" in capabilities
    if "technical_specs" in capabilities and (title or external_barcode):
        add("technical_specs", base_payload)
    elif (title or external_barcode) and not has_external_lookup:
        add("movie_details", base_payload)
    if title and "movie_details" not in capabilities:
        add("search_title", base_payload)
    if (query.get("memberOfBoxSet") or query.get("detectBoxSets")) and (title or fallback or external_barcode):
        add("box_set_candidates", base_payload)
    return plan


def normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        normalized_items = []
        for item in value:
            normalized = normalize_value(item)
            if value_present(normalized):
                normalized_items.append(normalized)
        return normalized_items
    if isinstance(value, dict):
        normalized_items = {}
        for key, item in value.items():
            normalized = normalize_value(item)
            if value_present(normalized):
                normalized_items[str(key)] = normalized
        return normalized_items
    return value


def split_outside_parentheses(text: str, separators: set[str]) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        if char in separators and depth == 0:
            value = "".join(current).strip()
            if value:
                parts.append(value)
            current = []
            continue
        current.append(char)
    value = "".join(current).strip()
    if value:
        parts.append(value)
    return parts


def normalize_list_field(field: str, value: Any) -> list[Any]:
    normalized = normalize_value(value)
    raw_items = normalized if isinstance(normalized, list) else [normalized]
    items: list[Any] = []
    separators = {";", "|", "\n", "\r"}
    if field in {"subtitles", "regions", "backdrop_urls"}:
        separators.add(",")

    for item in raw_items:
        if not value_present(item):
            continue
        if isinstance(item, str):
            text = re.sub(r"\s+", " ", item).strip()
            split_items = split_outside_parentheses(text, separators)
            if field == "audio_tracks":
                language_prefix = r"(?:English|French|Spanish|German|Dutch|Italian|Japanese|Portuguese|Cantonese|Mandarin|Korean|Danish|Finnish|Norwegian|Swedish|Polish|Czech|Hungarian|Russian|Thai|Turkish)(?:\s*\([^)]*\))?"
                comma_split_items: list[str] = []
                for split_item in split_items:
                    comma_parts = split_outside_parentheses(split_item, {","})
                    if len(comma_parts) > 1 and all(re.match(rf"^{language_prefix}(?:\s*:|\s+|$)", part) for part in comma_parts[1:]):
                        comma_split_items.extend(comma_parts)
                    else:
                        comma_split_items.append(split_item)
                split_items = comma_split_items
            if len(split_items) == 1 and field == "audio_tracks":
                # Blu-ray pages sometimes concatenate tracks as
                # "English: Atmos French: DD 5.1". Split only before a new
                # language label so codec commas inside parentheses survive.
                split_items = [
                    part.strip()
                    for part in re.split(
                        rf",?\s+(?={language_prefix}\s*:)",
                        text,
                    )
                    if part.strip()
                ]
            items.extend(split_items)
        elif isinstance(item, list):
            items.extend(normalize_list_field(field, item))
        else:
            items.append(item)

    deduped: list[Any] = []
    seen: set[str] = set()
    for item in items:
        key = json.dumps(json_ready(item), sort_keys=True) if not isinstance(item, str) else item.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def normalize_field_value(field: str, value: Any) -> Any:
    if field in METADATA_LIST_FIELDS:
        return normalize_list_field(field, value)
    return normalize_value(value)


def normalize_box_set_evidence(
    value: Any,
    *,
    proposal: dict[str, Any] | None = None,
    plugin_id: str = "",
    source_ref: str = "",
) -> dict[str, Any]:
    evidence_source = value if isinstance(value, dict) else {}
    proposal = proposal if isinstance(proposal, dict) else {}
    members = []
    for key in ("members", "movies", "boxSetMovies", "box_set_movies", "items", "releases"):
        raw_members = proposal.get(key)
        if isinstance(raw_members, list):
            members = [item for item in raw_members if isinstance(item, dict) and clean_text(item.get("title") or item.get("name"))]
            if members:
                break

    def first_text(*values: Any) -> str:
        for item in values:
            text = clean_text(item)
            if text:
                return text
        return ""

    def first_bool(*values: Any, default: bool = False) -> bool:
        for item in values:
            if isinstance(item, bool):
                return item
            text = clean_text(item)
            if text:
                lowered = text.casefold()
                if lowered in {"1", "true", "yes", "y", "on"}:
                    return True
                if lowered in {"0", "false", "no", "n", "off"}:
                    return False
        return default

    member_count = evidence_source.get("memberCount", evidence_source.get("member_count"))
    try:
        member_count = int(member_count)
    except (TypeError, ValueError):
        member_count = len(members)

    member_confidence = first_text(
        evidence_source.get("memberConfidence"),
        evidence_source.get("member_confidence"),
        proposal.get("memberConfidence"),
        proposal.get("member_confidence"),
    )
    detected_without_members = first_bool(
        evidence_source.get("detectedWithoutMembers"),
        evidence_source.get("detected_without_members"),
        proposal.get("detectedWithoutMembers"),
        proposal.get("detected_without_members"),
    )
    members_are_explicit = first_bool(
        evidence_source.get("membersAreExplicit"),
        evidence_source.get("members_are_explicit"),
        proposal.get("membersAreExplicit"),
        proposal.get("members_are_explicit"),
        default=bool(members and member_confidence.casefold() not in {"candidate", "fallback", "metadata_candidates"}),
    )
    if detected_without_members or member_confidence.casefold() == "candidate":
        members_are_explicit = False

    normalized = {
        "barcodeMatch": first_bool(evidence_source.get("barcodeMatch"), evidence_source.get("barcode_match"), proposal.get("barcodeMatch"), proposal.get("barcode_match")),
        "entityType": first_text(evidence_source.get("entityType"), evidence_source.get("entity_type"), proposal.get("entityType"), proposal.get("entity_type"), "box_set"),
        "memberSource": first_text(evidence_source.get("memberSource"), evidence_source.get("member_source"), proposal.get("memberSource"), proposal.get("member_source"), proposal.get("source"), plugin_id),
        "memberConfidence": member_confidence or ("identified" if members_are_explicit and members else "candidate" if members else "needs_member_confirmation"),
        "memberCount": max(member_count, 0),
        "membersAreExplicit": bool(members_are_explicit),
        "detectedWithoutMembers": bool(detected_without_members),
        "format": first_text(evidence_source.get("format"), proposal.get("format")),
        "sourceRef": first_text(evidence_source.get("sourceRef"), evidence_source.get("source_ref"), proposal.get("sourceRef"), proposal.get("source_ref"), proposal.get("detailUrl"), proposal.get("detail_url"), source_ref),
    }
    return {key: item for key, item in normalized.items() if value_present(item)}


def normalize_box_set_proposal_contract(proposal: Any, *, plugin_id: str, source_ref: str) -> dict[str, Any]:
    if not isinstance(proposal, dict):
        return {}
    normalized = normalize_value(proposal)
    if not isinstance(normalized, dict):
        return {}
    evidence = normalize_box_set_evidence(
        normalized.get("boxSetEvidence") or normalized.get("box_set_evidence"),
        proposal=normalized,
        plugin_id=plugin_id,
        source_ref=source_ref,
    )
    if evidence:
        normalized["boxSetEvidence"] = evidence
        normalized["box_set_evidence"] = evidence
        normalized.setdefault("entityType", evidence.get("entityType"))
        normalized.setdefault("memberSource", evidence.get("memberSource"))
        normalized.setdefault("member_source", evidence.get("memberSource"))
        normalized.setdefault("memberConfidence", evidence.get("memberConfidence"))
        normalized.setdefault("member_confidence", evidence.get("memberConfidence"))
        normalized.setdefault("memberCount", evidence.get("memberCount"))
        normalized.setdefault("member_count", evidence.get("memberCount"))
        normalized.setdefault("membersAreExplicit", evidence.get("membersAreExplicit"))
        normalized.setdefault("members_are_explicit", evidence.get("membersAreExplicit"))
        normalized.setdefault("detectedWithoutMembers", evidence.get("detectedWithoutMembers"))
        normalized.setdefault("detected_without_members", evidence.get("detectedWithoutMembers"))
    return {key: item for key, item in normalized.items() if value_present(item)}


def image_url_options(*values: Any) -> list[str]:
    options: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                add(item)
            return
        if isinstance(value, tuple):
            for item in value:
                add(item)
            return
        if isinstance(value, dict):
            for key in ("url", "sourceUrl", "source_url", "posterUrl", "poster_url", "backdropUrl", "backdrop_url"):
                if value.get(key):
                    add(value.get(key))
            return
        text = clean_text(value) or ""
        if not text.startswith(("http://", "https://")):
            return
        key = text.casefold()
        if key in seen:
            return
        seen.add(key)
        options.append(text)

    for value in values:
        add(value)
    return options


def metadata_field_name(name: str) -> str:
    return MOVIE_FIELD_ALIASES.get(name, name)


def parse_runtime_minutes(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return value
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def normalize_date_value(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    return None


def metadata_stable_uuid(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(METADATA_NAMESPACE, f"{kind}:{key}")


def parse_sort_order(value: Any, fallback: int) -> int:
    if isinstance(value, int):
        return value
    text = clean_text(value)
    if not text:
        return fallback
    try:
        return int(text)
    except ValueError:
        return fallback


def normalize_credit_role(value: Any, *, default: str = "") -> str:
    role = (clean_text(value) or default or "").casefold()
    if role in {"cast", "actor", "acting", "performer"}:
        return "actor"
    if role in {"crew", "director", "writer", "producer", "production"}:
        return "crew"
    return clean_text(value) or default or "credit"


def normalize_credit_entries(
    value: Any,
    *,
    plugin_id: str,
    source_label: str,
    source_ref: str,
    default_role: str = "",
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    def add_from_item(item: Any, *, role_hint: str, fallback_sort_order: int) -> None:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            return
        name = clean_text(
            item.get("name")
            or item.get("personName")
            or item.get("person_name")
            or item.get("displayName")
            or item.get("display_name")
        )
        if not name:
            return
        role = normalize_credit_role(
            item.get("role") or item.get("creditType") or item.get("credit_type") or role_hint,
            default=role_hint or default_role,
        )
        character = clean_text(item.get("character") or item.get("as"))
        job = clean_text(item.get("job") or item.get("department"))
        if role == "crew" and not job and role_hint not in {"actor", "cast"}:
            job = clean_text(item.get("role"))
        identifiers = item.get("identifiers") if isinstance(item.get("identifiers"), dict) else {}
        tmdb_id = clean_text(
            item.get("tmdbId")
            or item.get("tmdb_id")
            or item.get("tmdb")
            or identifiers.get("tmdb")
            or identifiers.get("tmdbId")
        )
        sort_order = parse_sort_order(
            item.get("sortOrder") or item.get("sort_order") or item.get("order"),
            fallback_sort_order,
        )
        identity = tmdb_id or name.casefold()
        key = (identity, role, character or "", job or "", source_label.casefold())
        if key in seen:
            return
        seen.add(key)
        entry = {
            "role": role,
            "name": name,
            "character": character,
            "job": job,
            "tmdbId": tmdb_id,
            "sortOrder": sort_order,
            "sourceProvider": plugin_id,
            "sourceLabel": source_label,
            "sourceRef": source_ref,
        }
        for image_key in ("profileUrl", "profile_url", "photoUrl", "photo_url", "profilePath", "profile_path", "photoFile", "photo_file"):
            if value_present(item.get(image_key)):
                entry[image_key] = item.get(image_key)
        entries.append(entry)

    def add(value_to_add: Any, *, role_hint: str = "") -> None:
        if isinstance(value_to_add, dict):
            if any(key in value_to_add for key in ("cast", "actors")):
                add(value_to_add.get("cast") or value_to_add.get("actors"), role_hint="actor")
            if "crew" in value_to_add:
                add(value_to_add.get("crew"), role_hint="crew")
            if any(key in value_to_add for key in ("credits", "people", "moviePeople", "movie_people")):
                for key in ("credits", "people", "moviePeople", "movie_people"):
                    if key in value_to_add:
                        add(value_to_add.get(key), role_hint=role_hint)
            if "name" in value_to_add or "personName" in value_to_add or "person_name" in value_to_add:
                add_from_item(value_to_add, role_hint=role_hint or default_role, fallback_sort_order=len(entries))
            return
        if isinstance(value_to_add, (list, tuple)):
            for item in value_to_add:
                add_from_item(item, role_hint=role_hint or default_role, fallback_sort_order=len(entries))
            return

    add(value, role_hint=default_role)
    return entries[:120]


def plugin_credit_updates(
    result: dict[str, Any],
    movie_source: dict[str, Any],
    *,
    plugin_id: str,
    source_label: str,
    source_ref: str,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for source in (movie_source, result):
        if not isinstance(source, dict):
            continue
        for key in ("credits", "people", "moviePeople", "movie_people"):
            if key in source:
                merged.extend(
                    normalize_credit_entries(
                        source.get(key),
                        plugin_id=plugin_id,
                        source_label=source_label,
                        source_ref=source_ref,
                    )
                )
        if "cast" in source or "actors" in source:
            merged.extend(
                normalize_credit_entries(
                    source.get("cast") or source.get("actors"),
                    plugin_id=plugin_id,
                    source_label=source_label,
                    source_ref=source_ref,
                    default_role="actor",
                )
            )
        if "crew" in source:
            merged.extend(
                normalize_credit_entries(
                    source.get("crew"),
                    plugin_id=plugin_id,
                    source_label=source_label,
                    source_ref=source_ref,
                    default_role="crew",
                )
            )
    deduped: list[dict[str, Any]] = []
    for item in merged:
        identity = clean_text(item.get("tmdbId")) or (clean_text(item.get("name")) or "").casefold()
        key = (
            identity,
            clean_text(item.get("role")) or "",
            clean_text(item.get("character")) or "",
            clean_text(item.get("job")) or "",
        )
        if not identity or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:120]


def normalize_localization_entries(
    value: Any,
    *,
    plugin_id: str,
    source_label: str,
    source_ref: str,
) -> list[dict[str, Any]]:
    raw_items: list[Any] = []
    if isinstance(value, dict):
        if isinstance(value.get("localizations"), list):
            raw_items.extend(value.get("localizations") or [])
        if isinstance(value.get("translations"), list):
            raw_items.extend(value.get("translations") or [])
    elif isinstance(value, (list, tuple)):
        raw_items.extend(value)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        payload = item.get("data") if isinstance(item.get("data"), dict) else item
        lang = clean_text(
            item.get("lang")
            or item.get("language")
            or item.get("locale")
            or item.get("iso_639_1")
        )
        country = clean_text(item.get("country") or item.get("iso_3166_1"))
        if lang and country and "-" not in lang:
            lang = f"{lang.lower()}-{country.upper()}"
        title = clean_text(payload.get("title") or payload.get("name"))
        overview = clean_text(payload.get("overview") or payload.get("plot") or payload.get("description"))
        if not lang or not (title or overview):
            continue
        key = lang.lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "lang": lang,
                "title": title,
                "overview": overview,
                "pluginId": plugin_id,
                "sourceLabel": source_label,
                "sourceRef": source_ref,
            }
        )
    return rows[:80]


# Keywords that mark a bracketed/parenthetical group (or trailing segment) as
# packaging/format/edition/region noise rather than part of the film title.
_SCANNED_TITLE_NOISE_RE = re.compile(
    r'blu[- ]?ray|ultra\s*hd|\buhd\b|\b4k\b|\bdvd\b|\b3d\b|\bvhs\b|\bhddvd\b|hd[- ]?dvd'
    r'|steel\s*book|steelbook|limited\s+edition|collector|special\s+edition'
    r'|digibook|mediabook|slipcover|slipcase|box\s*set|boxset|gift\s*set'
    r'|\bimport\b|region[\s-]*(?:free|locked|[abc]|[0-9])'
    r'|\bpal\b|\bntsc\b|remaster|anniversary\s+edition|uncut|extended\s+edition'
    r'|\bocard\b|o[- ]card|amaray|digipack|digipak',
    re.I,
)


def _clean_scanned_title(raw_title: str) -> str:
    """Return the bare film title from a UPC/EAN packaging title.

    Strips bracket/parenthetical groups and trailing segments that contain
    format/edition/region noise (e.g. "(4K Ultra HD + Blu-ray)", "[UK Import]",
    "- Steelbook"). Keeps a subtitle after a colon. Returns the original title
    when nothing recognisable remains."""
    title = (raw_title or "").strip()
    if not title:
        return ""

    def _strip_groups(text: str, open_ch: str, close_ch: str) -> str:
        pattern = re.compile(re.escape(open_ch) + r'[^' + re.escape(open_ch + close_ch) + r']*' + re.escape(close_ch))
        prev = None
        while prev != text:
            prev = text
            text = pattern.sub(
                lambda m: ' ' if _SCANNED_TITLE_NOISE_RE.search(m.group(0)) else m.group(0),
                text,
            )
        return text

    cleaned = _strip_groups(title, '(', ')')
    cleaned = _strip_groups(cleaned, '[', ']')
    cleaned = _strip_groups(cleaned, '{', '}')

    # Drop trailing " - <noise...>" / " | <noise...>" segments (distributor/format tails).
    cleaned = re.sub(r'\s[-|/]\s*[^-|/]*(?:' + _SCANNED_TITLE_NOISE_RE.pattern + r')[^-|/]*$', ' ', cleaned, flags=re.I)
    # Drop bare trailing format/region tokens, iteratively so space-separated
    # multi-token tails like "4K Blu-ray" or "Ultra HD Blu-ray 3D" collapse to
    # the film title instead of leaving a dangling "4K".
    bare_tail_re = re.compile(r'[\s,;:/+&-]+(?:' + _SCANNED_TITLE_NOISE_RE.pattern + r')\s*$', re.I)
    prev = None
    while prev != cleaned:
        prev = cleaned
        cleaned = bare_tail_re.sub('', cleaned).rstrip()

    cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip(' -_/|,;:+&')
    return cleaned or title


# Map nationality/country adjectives or codes from "<X> Import" packaging hints to
# a canonical country name. Unknown values fall back to a free-text region note.
_IMPORT_COUNTRY_MAP = {
    "uk": "United Kingdom", "u.k.": "United Kingdom", "british": "United Kingdom",
    "england": "United Kingdom", "us": "United States", "u.s.": "United States",
    "usa": "United States", "american": "United States", "italian": "Italy",
    "italy": "Italy", "german": "Germany", "germany": "Germany", "french": "France",
    "france": "France", "japanese": "Japan", "japan": "Japan", "spanish": "Spain",
    "spain": "Spain", "dutch": "Netherlands", "netherlands": "Netherlands",
    "belgian": "Belgium", "belgium": "Belgium", "korean": "South Korea",
    "korea": "South Korea", "swedish": "Sweden", "sweden": "Sweden",
    "danish": "Denmark", "denmark": "Denmark", "norwegian": "Norway",
    "norway": "Norway", "finnish": "Finland", "finland": "Finland",
    "australian": "Australia", "australia": "Australia", "canadian": "Canada",
    "canada": "Canada", "nordic": "Nordic", "scandinavian": "Scandinavia",
    "european": "Europe", "austrian": "Austria", "austria": "Austria",
    "polish": "Poland", "poland": "Poland", "portuguese": "Portugal",
    "portugal": "Portugal", "russian": "Russia", "russia": "Russia",
    "chinese": "China", "china": "China",
}


def _parse_import_country(raw_title: str) -> tuple[str, str]:
    """Detect a country/region hint from a packaging title.

    Returns a (country, region_note) tuple. ``country`` is a canonical country
    name when the import hint is recognised; otherwise ``region_note`` carries
    the raw hint (e.g. "Region B") so it can land in the ``regions`` field.
    Either value may be empty."""
    text = raw_title or ""
    if not text:
        return "", ""

    for match in re.finditer(r'([A-Za-z.][A-Za-z. ]*?)\s+import\b', text, flags=re.I):
        phrase = match.group(1).strip().lower()
        token = phrase.split()[-1] if phrase.split() else phrase
        for key in (phrase, token):
            mapped = _IMPORT_COUNTRY_MAP.get(key)
            if mapped:
                return mapped, ""
        if phrase:
            return "", f"{match.group(1).strip().title()} Import"

    region_match = re.search(r'region[\s-]*(free|locked|[ABC]|[0-9])\b', text, flags=re.I)
    if region_match:
        return "", f"Region {region_match.group(1).upper()}"

    return "", ""


def canonicalize_plugin_result(plugin_id: str, entrypoint: str, result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        result = {"value": result}
    movie_source = result.get("movie") or result.get("details") or {}
    if not isinstance(movie_source, dict):
        movie_source = {}
    release_source = result.get("release") or {}
    if not isinstance(release_source, dict):
        release_source = {}
    if not movie_source and any(key in result for key in ("title", "overview", "plot", "posterUrl", "poster_url")):
        movie_source = result

    movie_updates: dict[str, Any] = {}
    metadata_updates: dict[str, Any] = {}
    technical_updates: dict[str, Any] = {}
    identifiers: dict[str, str] = {}

    def collect(source: dict[str, Any], *, release: bool = False) -> None:
        for raw_key, raw_value in source.items():
            key = metadata_field_name(str(raw_key))
            value = normalize_field_value(key, raw_value)
            if not value_present(value):
                continue
            if key in METADATA_IDENTIFIER_TYPES:
                provider, _identifier_type = METADATA_IDENTIFIER_TYPES[key]
                identifiers[provider] = str(value)
                continue
            if key in METADATA_TECHNICAL_FIELDS:
                technical_updates[key] = value
                continue
            if key == "runtime_minutes":
                parsed = parse_runtime_minutes(value)
                if parsed is not None:
                    movie_updates[key] = parsed
                continue
            if key == "release_date":
                parsed_date = normalize_date_value(value)
                if parsed_date:
                    movie_updates[key] = parsed_date
                continue
            if key in METADATA_MAIN_FIELDS:
                movie_updates[key] = value
                continue
            if key in METADATA_LOCAL_ONLY_FIELDS:
                continue
            if release and key in METADATA_RELEASE_FIELDS:
                technical_updates[key] = value
            else:
                metadata_updates[key] = value

    collect(movie_source)
    movie_source_title = clean_text(movie_updates.get("title"))
    collect(release_source, release=True)

    technical = result.get("technicalSpecs") or result.get("technical_specs") or {}
    if isinstance(technical, dict):
        collect(technical, release=True)

    # Split the clean film title from the raw packaging/scanned title. A
    # candidate that carries a `releaseTitle` (e.g. Blu-ray.com) or a `title`
    # full of format/edition/region noise is a packaging title: keep the raw
    # value in `release_title`, derive a clean film title, and parse import
    # hints into country/regions without overwriting richer resolved values.
    raw_release_title = (
        clean_text(result.get("releaseTitle") or result.get("release_title"))
        or clean_text(movie_updates.get("release_title"))
        or clean_text(release_source.get("title"))
    )
    candidate_title = clean_text(movie_updates.get("title"))
    if not raw_release_title and candidate_title and _SCANNED_TITLE_NOISE_RE.search(candidate_title):
        raw_release_title = candidate_title
    if raw_release_title:
        movie_updates.setdefault("release_title", raw_release_title)
        current_title = clean_text(movie_updates.get("title"))
        if (
            movie_source_title
            and movie_source_title != raw_release_title
            and not _SCANNED_TITLE_NOISE_RE.search(movie_source_title)
        ):
            # The plugin already supplied a clean canonical movie title (e.g.
            # Blu-ray.com derives one alongside the raw release title). Keep it
            # and don't let the raw release title collected afterwards clobber it.
            movie_updates["title"] = movie_source_title
        else:
            cleaned_title = _clean_scanned_title(raw_release_title)
            if cleaned_title and (
                not current_title
                or current_title == raw_release_title
                or _SCANNED_TITLE_NOISE_RE.search(current_title)
            ):
                movie_updates["title"] = cleaned_title
        import_country, import_region = _parse_import_country(raw_release_title)
        if import_country and not value_present(movie_updates.get("country")):
            movie_updates["country"] = import_country
        if import_region and not value_present(technical_updates.get("regions")):
            # `regions` is stored as a JSONB array in v26, so coerce the
            # free-text hint (e.g. "Region B") into a normalized list.
            technical_updates["regions"] = normalize_list_field("regions", import_region)

    images = result.get("images") or {}
    if isinstance(images, dict):
        collect(images)
    videos = result.get("videos")
    if value_present(videos):
        metadata_updates["videos"] = videos
    if value_present(result.get("trailerUrl") or result.get("trailer_url")):
        metadata_updates["trailer_url"] = result.get("trailerUrl") or result.get("trailer_url")

    for key, (provider, _identifier_type) in METADATA_IDENTIFIER_TYPES.items():
        value = result.get(key)
        if value_present(value):
            identifiers[provider] = str(value)
    raw_identifiers = result.get("identifiers") or {}
    if isinstance(raw_identifiers, dict):
        for key, value in raw_identifiers.items():
            mapped = METADATA_IDENTIFIER_TYPES.get(str(key))
            if mapped and value_present(value):
                identifiers[mapped[0]] = str(value)
    elif isinstance(raw_identifiers, list):
        for item in raw_identifiers:
            if not isinstance(item, dict):
                continue
            provider = clean_text(item.get("provider_id") or item.get("provider") or item.get("providerId"))
            identifier = clean_text(item.get("identifier") or item.get("value"))
            identifier_type = clean_text(item.get("identifier_type") or item.get("identifierType") or "movie_id")
            if provider and identifier and identifier_type == "movie_id":
                identifiers[provider] = identifier

    source_label = result.get("sourceLabel") or result.get("providerLabel") or plugin_id
    source_ref = result.get("sourceRef") or result.get("sourceUrl") or result.get("source_url") or ""
    credit_updates = plugin_credit_updates(
        result,
        movie_source,
        plugin_id=plugin_id,
        source_label=source_label,
        source_ref=source_ref,
    )
    localization_updates = normalize_localization_entries(
        result.get("localizations") or result.get("translations") or result,
        plugin_id=plugin_id,
        source_label=source_label,
        source_ref=source_ref,
    )

    source_format = (
        movie_updates.get("format")
        or technical_updates.get("format")
        or release_source.get("format")
        or result.get("sourceFormat")
        or result.get("source_format")
        or result.get("format")
        or ""
    )
    media_updates: dict[str, dict[str, Any]] = {}
    poster_options = image_url_options(
        metadata_updates.get("poster_url"),
        metadata_updates.get("posters"),
        result.get("posters"),
        (result.get("images") or {}).get("posters") if isinstance(result.get("images"), dict) else None,
    )
    if poster_options:
        media_updates["poster"] = {
            "kind": "poster",
            "field": "poster_url",
            "sourceUrl": poster_options[0],
            "options": poster_options,
            "providerId": plugin_id,
            "sourceLabel": source_label,
            "sourceRef": source_ref,
        }
    backdrop_options = image_url_options(
        metadata_updates.get("backdrop_url"),
        metadata_updates.get("backdrop_urls"),
        result.get("backdrops"),
        (result.get("images") or {}).get("backdrops") if isinstance(result.get("images"), dict) else None,
    )
    if backdrop_options:
        media_updates["backdrop"] = {
            "kind": "backdrop",
            "field": "backdrop_url",
            "sourceUrl": backdrop_options[0],
            "options": backdrop_options,
            "providerId": plugin_id,
            "sourceLabel": source_label,
            "sourceRef": source_ref,
        }
    box_set_proposal = normalize_box_set_proposal_contract(
        result.get("boxSetProposal") or result.get("box_set_proposal"),
        plugin_id=plugin_id,
        source_ref=source_ref,
    )
    box_set_proposals = [
        normalized
        for normalized in (
            normalize_box_set_proposal_contract(item, plugin_id=plugin_id, source_ref=source_ref)
            for item in (result.get("boxSetProposals") or result.get("box_set_proposals") or [])
        )
        if normalized
    ]
    if box_set_proposal and not box_set_proposals:
        box_set_proposals = [box_set_proposal]
    box_set_evidence = normalize_box_set_evidence(
        result.get("boxSetEvidence") or result.get("box_set_evidence"),
        proposal=box_set_proposal,
        plugin_id=plugin_id,
        source_ref=source_ref,
    )
    return {
        "pluginId": plugin_id,
        "entrypoint": entrypoint,
        "status": result.get("status") or "ok",
        "sourceLabel": result.get("sourceLabel") or result.get("providerLabel") or plugin_id,
        "sourceRef": result.get("sourceRef") or result.get("sourceUrl") or result.get("source_url") or "",
        "sourceFormat": source_format,
        "normalizedSourceFormat": normalize_media_format(source_format),
        "movieUpdates": movie_updates,
        "metadataUpdates": metadata_updates,
        "technicalUpdates": technical_updates,
        "releaseTitle": clean_text(movie_updates.get("release_title")) or raw_release_title,
        "cleanTitle": clean_text(movie_updates.get("title")),
        "mediaUpdates": media_updates,
        "identifiers": identifiers,
        "credits": credit_updates,
        "localizations": localization_updates,
        "candidates": result.get("items") or result.get("candidates") or [],
        "boxSetEvidence": box_set_evidence,
        "boxSetProposal": box_set_proposal,
        "boxSetProposals": box_set_proposals,
        "raw": result,
    }


def current_field_value(current: dict[str, Any], field: str) -> Any:
    if field in current:
        return current.get(field)
    metadata = current.get("metadata") or {}
    if isinstance(metadata, dict):
        return metadata.get(field)
    return None


def field_format_safe(
    field: str,
    target_format: str,
    source_format: str,
    *,
    source_context: str = "",
) -> tuple[bool, str]:
    target = normalize_media_format(target_format)
    source = normalize_media_format(source_format)
    if field == "content_ratings":
        return True, "format-neutral certification field"
    if field not in METADATA_RELEASE_FIELDS:
        return True, "format-neutral field"
    if not target or not source:
        if source_context == "box_set_parent" and field in {"audio_tracks", "subtitles", "regions", "content_ratings"}:
            return True, "box-set parent technical fallback without explicit format"
        return (field not in METADATA_TECHNICAL_FIELDS), "technical field needs same-format source"
    if target == source:
        return True, "same-format release data"
    return False, f"format mismatch: target={target}, source={source}"


def should_apply_field(
    *,
    field: str,
    current_value: Any,
    incoming_value: Any,
    overwrite_enabled: bool,
    target_format: str,
    source_format: str,
    release_priority: bool = False,
    source_context: str = "",
) -> tuple[bool, str]:
    if not value_present(incoming_value):
        return False, "incoming value is empty"
    if field in METADATA_LOCAL_ONLY_FIELDS:
        return False, "field is local-only"
    if (
        release_priority
        and field in METADATA_DISPLAY_TITLE_FIELDS
        and value_present(current_value)
    ):
        return False, "release source cannot overwrite canonical display title"
    format_ok, format_reason = field_format_safe(field, target_format, source_format, source_context=source_context)
    if not format_ok:
        return False, format_reason
    if not value_present(current_value):
        return True, "current field is empty"
    if field in METADATA_TECHNICAL_FIELDS and release_priority and format_reason == "same-format release data":
        return True, "same-format technical release refresh"
    if overwrite_enabled and field not in METADATA_LOCAL_ONLY_FIELDS:
        return True, "preferred provider overwrite is enabled"
    if field in METADATA_MANUAL_PROTECTED_FIELDS:
        return False, "manual/display field already has a value"
    return False, "existing value retained"


def metadata_decision_initial_value(
    *,
    target: str,
    field: str,
    current: dict[str, Any],
    technical_current: dict[str, Any],
) -> Any:
    if target == "technical":
        return technical_current.get(field)
    if target == "metadata":
        metadata = current.get("metadata") if isinstance(current.get("metadata"), dict) else {}
        return metadata.get(field)
    return current.get(field)


def metadata_decision_candidate(
    result: dict[str, Any],
    *,
    value: Any,
    accepted: bool,
    reason: str,
    order: int,
) -> dict[str, Any]:
    return {
        "pluginId": result["pluginId"],
        "entrypoint": result["entrypoint"],
        "sourceLabel": result.get("sourceLabel") or result["pluginId"],
        "sourceRef": result.get("sourceRef") or "",
        "sourceFormat": result.get("sourceFormat") or "",
        "order": order,
        "accepted": bool(accepted),
        "winner": False,
        "reason": reason,
        "value": json_ready(value),
    }


def merge_metadata_results(
    *,
    current: dict[str, Any] | None,
    technical_current: dict[str, Any] | None,
    results: list[dict[str, Any]],
    overwrite_enabled: bool,
    target_format: str,
) -> dict[str, Any]:
    current = current or {}
    technical_current = technical_current or {}
    movie_updates: dict[str, Any] = {}
    metadata_updates: dict[str, Any] = {}
    technical_updates: dict[str, Any] = {}
    media_updates: dict[str, dict[str, Any]] = {}
    identifiers: dict[str, str] = {}
    credits: list[dict[str, Any]] = []
    localizations: list[dict[str, Any]] = []
    credit_keys: set[tuple[str, str, str, str]] = set()
    localization_keys: set[str] = set()
    provenance: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    field_decisions_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    selected_field_keys: set[tuple[str, str]] = set()
    working_movie = dict(current)
    working_metadata = dict(current.get("metadata") or {})
    working_technical = dict(technical_current)
    locked_fields = movie_locked_fields(working_metadata)

    def decision_for(target: str, field: str) -> dict[str, Any]:
        key = (target, field)
        if key not in field_decisions_by_key:
            field_decisions_by_key[key] = {
                "target": target,
                "field": field,
                "initialValue": json_ready(
                    metadata_decision_initial_value(
                        target=target,
                        field=field,
                        current=current,
                        technical_current=technical_current,
                    )
                ),
                "finalValue": json_ready(
                    metadata_decision_initial_value(
                        target=target,
                        field=field,
                        current=current,
                        technical_current=technical_current,
                    )
                ),
                "winner": None,
                "candidates": [],
                "candidateCount": 0,
                "acceptedCandidateCount": 0,
                "conflict": False,
                "changed": False,
                "outcome": "retained_existing",
            }
        return field_decisions_by_key[key]

    def add_decision_candidate(
        *,
        target: str,
        field: str,
        result: dict[str, Any],
        value: Any,
        accepted: bool,
        reason: str,
    ) -> None:
        decision = decision_for(target, field)
        candidate = metadata_decision_candidate(
            result,
            value=value,
            accepted=accepted,
            reason=reason,
            order=len(decision["candidates"]) + 1,
        )
        decision["candidates"].append(candidate)
        decision["candidateCount"] = len(decision["candidates"])
        decision["acceptedCandidateCount"] = len([item for item in decision["candidates"] if item.get("accepted")])
        decision["conflict"] = decision["candidateCount"] > 1
        if accepted:
            for item in decision["candidates"]:
                item["winner"] = False
            candidate["winner"] = True
            decision["winner"] = {
                key: candidate[key]
                for key in ("pluginId", "entrypoint", "sourceLabel", "sourceRef", "sourceFormat", "reason", "order")
            }
            decision["finalValue"] = json_ready(value)
            decision["changed"] = decision["finalValue"] != decision["initialValue"]
            decision["outcome"] = "winner_selected"

    def merge_bucket(bucket: dict[str, Any], target: str, result: dict[str, Any], release_priority: bool) -> None:
        for field, value in bucket.items():
            if target == "technical":
                current_value = working_technical.get(field)
            elif target == "metadata":
                current_value = working_metadata.get(field)
            else:
                current_value = working_movie.get(field)
            decision_key = (target, field)
            if decision_key in selected_field_keys:
                allowed = False
                reason = "higher-priority provider already selected this field"
            elif field in locked_fields:
                allowed = False
                reason = "field is locked by user"
            else:
                allowed, reason = should_apply_field(
                    field=field,
                    current_value=current_value,
                    incoming_value=value,
                    overwrite_enabled=overwrite_enabled,
                    target_format=target_format,
                    source_format=result.get("sourceFormat") or "",
                    release_priority=release_priority,
                    source_context=(result.get("raw") or {}).get("sourceContext") or "",
                )
            item = {
                "field": field,
                "target": target,
                "pluginId": result["pluginId"],
                "entrypoint": result["entrypoint"],
                "reason": reason,
            }
            add_decision_candidate(
                target=target,
                field=field,
                result=result,
                value=value,
                accepted=allowed,
                reason=reason,
            )
            if not allowed:
                skipped.append(item)
                continue
            if target == "technical":
                technical_updates[field] = value
                working_technical[field] = value
            elif target == "metadata":
                metadata_updates[field] = value
                working_metadata[field] = value
            else:
                movie_updates[field] = value
                working_movie[field] = value
            selected_field_keys.add(decision_key)
            provenance.append({**item, "sourceRef": result.get("sourceRef") or "", "sourceLabel": result.get("sourceLabel") or result["pluginId"]})

    for result in results:
        release_priority = bool(result.get("technicalUpdates")) or bool(result.get("raw", {}).get("release"))
        merge_bucket(result.get("movieUpdates") or {}, "movie", result, release_priority)
        merge_bucket(result.get("metadataUpdates") or {}, "metadata", result, release_priority)
        merge_bucket(result.get("technicalUpdates") or {}, "technical", result, release_priority)
        for kind, media_update in (result.get("mediaUpdates") or {}).items():
            source_url = clean_text(media_update.get("sourceUrl") if isinstance(media_update, dict) else "")
            if kind not in {"poster", "backdrop"} or not source_url:
                continue
            options = image_url_options((media_update or {}).get("options"), source_url)
            item = {
                "field": kind,
                "target": "media",
                "pluginId": result["pluginId"],
                "entrypoint": result["entrypoint"],
                "sourceRef": result.get("sourceRef") or "",
                "sourceLabel": result.get("sourceLabel") or result["pluginId"],
            }
            if kind in media_updates:
                existing_options = image_url_options(media_updates[kind].get("options"))
                merged_options = image_url_options(existing_options, options)
                media_updates[kind]["options"] = merged_options
                add_decision_candidate(
                    target="media",
                    field=kind,
                    result=result,
                    value=source_url,
                    accepted=False,
                    reason="media already selected from an earlier provider",
                )
                skipped.append({**item, "reason": "media already selected from an earlier provider"})
                continue
            media_updates[kind] = {
                **media_update,
                "kind": kind,
                "sourceUrl": source_url,
                "options": options,
                "providerId": media_update.get("providerId") or result["pluginId"],
                "sourceLabel": media_update.get("sourceLabel") or result.get("sourceLabel") or result["pluginId"],
                "sourceRef": media_update.get("sourceRef") or result.get("sourceRef") or "",
            }
            add_decision_candidate(
                target="media",
                field=kind,
                result=result,
                value=source_url,
                accepted=True,
                reason="provider image selected as primary media asset",
            )
            provenance.append({**item, "reason": "provider image selected as primary media asset"})
        for provider, identifier in (result.get("identifiers") or {}).items():
            if not identifier:
                continue
            identifier_result = {
                **result,
                "sourceRef": result.get("sourceRef") or f"{provider}:{identifier}",
            }
            accepted_identifier = provider not in identifiers
            add_decision_candidate(
                target="identifier",
                field=str(provider),
                result=identifier_result,
                value=str(identifier),
                accepted=accepted_identifier,
                reason="identifier provider selected" if accepted_identifier else "identifier already selected from an earlier provider",
            )
            if accepted_identifier:
                identifiers[provider] = identifier
        for credit in result.get("credits") or []:
            if not isinstance(credit, dict):
                continue
            identity = clean_text(credit.get("tmdbId")) or (clean_text(credit.get("name")) or "").casefold()
            key = (
                identity,
                clean_text(credit.get("role")) or "",
                clean_text(credit.get("character")) or "",
                clean_text(credit.get("job")) or "",
            )
            if not identity or key in credit_keys:
                continue
            credit_keys.add(key)
            credits.append(credit)
        for localization in result.get("localizations") or []:
            if not isinstance(localization, dict):
                continue
            lang = clean_text(localization.get("lang") or localization.get("language") or localization.get("locale"))
            title = clean_text(localization.get("title"))
            overview = clean_text(localization.get("overview"))
            if not lang or not (title or overview):
                continue
            key = lang.lower()
            if key in localization_keys:
                continue
            localization_keys.add(key)
            localizations.append(
                {
                    **localization,
                    "lang": lang,
                    "title": title,
                    "overview": overview,
                }
            )

    field_decisions = list(field_decisions_by_key.values())
    return {
        "movieUpdates": movie_updates,
        "metadataUpdates": metadata_updates,
        "technicalUpdates": technical_updates,
        "mediaUpdates": media_updates,
        "identifiers": identifiers,
        "credits": credits,
        "localizations": localizations,
        "provenance": provenance,
        "skipped": skipped,
        "fieldDecisions": field_decisions,
    }


def count_update_fields(proposal: dict[str, Any]) -> int:
    field_count = sum(
        len(proposal.get(key) or {})
        for key in ("movieUpdates", "metadataUpdates", "technicalUpdates", "mediaUpdates", "identifiers")
    )
    return field_count + len(proposal.get("credits") or []) + len(proposal.get("localizations") or [])


def summarize_metadata_execution(
    *,
    plugins: list[dict[str, Any]],
    executions: list[dict[str, Any]],
    results: list[dict[str, Any]],
    proposal: dict[str, Any],
) -> list[dict[str, Any]]:
    provenance = proposal.get("provenance") or []
    skipped = proposal.get("skipped") or []
    summary: list[dict[str, Any]] = []

    for plugin in plugins:
        plugin_id = plugin["id"]
        plugin_executions = [item for item in executions if item.get("pluginId") == plugin_id]
        plugin_results = [item for item in results if item.get("pluginId") == plugin_id]
        accepted = [item for item in provenance if item.get("pluginId") == plugin_id]
        rejected = [item for item in skipped if item.get("pluginId") == plugin_id]
        format_rejected = [item for item in rejected if "format mismatch" in str(item.get("reason") or "")]

        if not plugin_executions:
            state = "skipped"
            reason = "no compatible entrypoint for query"
        elif any(item.get("state") == "needs_configuration" for item in plugin_executions):
            state = "needs_configuration"
            reason = "plugin settings or secrets are incomplete"
        elif any(item.get("status") == "error" for item in plugin_executions):
            state = "error"
            reason = "plugin execution failed"
        elif accepted:
            state = "applied"
            reason = "provider values passed merge policy"
        elif format_rejected:
            state = "blocked_by_format_policy"
            reason = "provider result did not match the physical release format"
        elif any(str(item.get("resultStatus") or "") in {"miss", "not_found", "no_match"} for item in plugin_executions):
            state = "no_match"
            reason = "provider returned no usable match"
        elif plugin_results:
            result_statuses = {str(item.get("status") or "ok") for item in plugin_results}
            if result_statuses.intersection({"miss", "not_found", "no_match"}):
                state = "no_match"
                reason = "provider returned no usable match"
            elif rejected:
                state = "retained_existing"
                reason = "local values were retained by merge policy"
            else:
                state = "hit"
                reason = "provider returned data"
        else:
            state = "no_match"
            reason = "provider returned no normalized metadata"

        summary.append(
            {
                "pluginId": plugin_id,
                "name": plugin.get("name") or plugin_id,
                "orderIndex": plugin.get("order_index"),
                "categories": sorted(plugin_categories(plugin)),
                "barcodeHintSource": plugin_is_bootstrap_metadata_source(plugin),
                "state": state,
                "reason": reason,
                "entrypoints": [item.get("entrypoint") for item in plugin_executions],
                "executed": bool(plugin_executions),
                "resultCount": len(plugin_results),
                "acceptedFields": len(accepted),
                "skippedFields": len(rejected),
                "formatBlockedFields": len(format_rejected),
                "elapsedMs": sum(int(item.get("elapsedMs") or 0) for item in plugin_executions),
            }
        )
    return summary


def audit_actor_values(actor: dict[str, Any] | None) -> dict[str, Any]:
    actor = actor or {}
    return {
        "id": str(actor.get("id")) if actor.get("id") else None,
        "username": actor.get("username"),
        "role": actor.get("role"),
    }


def insert_metadata_audit_event(
    conn,
    *,
    event_type: str,
    actor: dict[str, Any] | None,
    movie_id: UUID | str,
    summary: str,
    metadata: dict[str, Any],
) -> None:
    if not table_exists(conn, "audit_events"):
        return
    actor_values = audit_actor_values(actor)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit_events (
                event_type,
                category,
                actor_user_id,
                actor_username,
                actor_role,
                target_type,
                target_id,
                summary,
                metadata
            )
            VALUES (%s, 'metadata', %s, %s, %s, 'movie', %s, %s, %s)
            """,
            (
                event_type,
                actor_values.get("id"),
                actor_values.get("username"),
                actor_values.get("role"),
                str(movie_id),
                summary,
                Jsonb(json_ready(metadata)),
            ),
        )


def metadata_result_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "pluginId": result.get("pluginId"),
        "sourceLabel": result.get("sourceLabel") or result.get("pluginId"),
        "entrypoint": result.get("entrypoint"),
        "status": result.get("status"),
        "sourceRef": result.get("sourceRef") or "",
        "movieFields": sorted((result.get("movieUpdates") or {}).keys()),
        "metadataFields": sorted((result.get("metadataUpdates") or {}).keys()),
        "technicalFields": sorted((result.get("technicalUpdates") or {}).keys()),
        "mediaKinds": sorted((result.get("mediaUpdates") or {}).keys()),
        "identifierProviders": sorted((result.get("identifiers") or {}).keys()),
        "creditCount": len(result.get("credits") or []),
        "candidateCount": len(result.get("candidates") or []),
        "hasBoxSetProposal": bool(result.get("boxSetProposal")),
    }


def metadata_fetch_audit_payload(
    *,
    movie_id: UUID | str,
    movie: dict[str, Any],
    dry_run: bool,
    preview: dict[str, Any],
    applied: dict[str, Any] | None = None,
) -> dict[str, Any]:
    proposal = preview.get("proposal") or {}
    applied = applied or {}
    field_decisions = metadata_field_decisions_with_write_state(
        proposal.get("fieldDecisions") or [],
        applied=applied,
        dry_run=dry_run,
    )
    return {
        "movieId": str(movie_id),
        "title": movie.get("title"),
        "barcode": movie.get("barcode"),
        "format": movie.get("format"),
        "dryRun": dry_run,
        "changed": bool(applied.get("changed")) if applied else False,
        "revision": applied.get("revision") if applied else 0,
        "sourceOrder": preview.get("sourceOrder") or [],
        "sourceSummary": preview.get("sourceSummary") or [],
        "executions": [
            {
                "pluginId": item.get("pluginId"),
                "entrypoint": item.get("entrypoint"),
                "status": item.get("status"),
                "state": item.get("state"),
                "resultStatus": item.get("resultStatus"),
                "candidateCount": item.get("candidateCount"),
                "elapsedMs": item.get("elapsedMs"),
                "error": item.get("error"),
            }
            for item in (preview.get("executions") or [])
        ],
        "providerResults": [metadata_result_summary(item) for item in (preview.get("results") or [])],
        "acceptedFields": proposal.get("provenance") or [],
        "skippedFields": proposal.get("skipped") or [],
        "fieldDecisions": field_decisions,
        "finalWrites": [item for item in field_decisions if item.get("written")],
        "proposalStats": preview.get("proposalStats") or {},
        "applied": applied.get("applied") if applied else {},
        "creditStats": {
            "proposed": len(proposal.get("credits") or []),
            "applied": (applied.get("applied") or {}).get("credits") if applied else {},
        },
    }


def metadata_field_decisions_with_write_state(
    field_decisions: list[dict[str, Any]],
    *,
    applied: dict[str, Any] | None,
    dry_run: bool,
) -> list[dict[str, Any]]:
    applied = applied or {}
    applied_payload = applied.get("applied") if isinstance(applied.get("applied"), dict) else {}
    applied_payload = applied_payload if isinstance(applied_payload, dict) else {}
    applied_buckets = {
        "movie": applied_payload.get("movieUpdates") or {},
        "metadata": applied_payload.get("metadataUpdates") or {},
        "technical": applied_payload.get("technicalUpdates") or {},
        "media": applied_payload.get("mediaUpdates") or {},
        "identifier": applied_payload.get("identifiers") or {},
    }
    enriched: list[dict[str, Any]] = []
    for decision in field_decisions:
        if not isinstance(decision, dict):
            continue
        target = str(decision.get("target") or "")
        field = str(decision.get("field") or "")
        bucket = applied_buckets.get(target) or {}
        applied_value = None
        written = False
        write_state = "dry_run" if dry_run else "not_written"
        if not dry_run and isinstance(bucket, dict) and field in bucket:
            applied_value = bucket.get(field)
            written = True
            write_state = "written"
            if target == "media" and isinstance(applied_value, dict):
                if applied_value.get("lockedPrimary"):
                    write_state = "primary_locked_option_added"
                elif applied_value.get("option"):
                    write_state = "media_option_added"
        elif not dry_run and not applied.get("changed"):
            write_state = "unchanged"
        enriched.append(
            {
                **decision,
                "written": written,
                "writeState": write_state,
                "appliedValue": json_ready(applied_value) if written else None,
            }
        )
    return enriched


def metadata_provider_title_hints(results: list[dict[str, Any]]) -> list[dict[str, str]]:
    hints: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for result in results:
        if not isinstance(result, dict):
            continue
        plugin_id = clean_text(result.get("pluginId")) or ""
        movie_updates = result.get("movieUpdates") if isinstance(result.get("movieUpdates"), dict) else {}
        metadata_updates = result.get("metadataUpdates") if isinstance(result.get("metadataUpdates"), dict) else {}
        raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
        raw_movie = raw.get("movie") or raw.get("details") or {}
        if not isinstance(raw_movie, dict):
            raw_movie = {}
        title = (
            clean_text(movie_updates.get("title"))
            or clean_text(metadata_updates.get("title"))
            or clean_text(raw_movie.get("title") or raw_movie.get("name"))
            or clean_text(raw.get("title") or raw.get("name"))
        )
        original_title = (
            clean_text(movie_updates.get("original_title") or movie_updates.get("originalTitle"))
            or clean_text(metadata_updates.get("original_title") or metadata_updates.get("originalTitle"))
            or clean_text(raw_movie.get("originalTitle") or raw_movie.get("original_title"))
            or clean_text(raw.get("originalTitle") or raw.get("original_title"))
        )
        if not plugin_id or not (title or original_title):
            continue
        key = (plugin_id, title or "", original_title or "")
        if key in seen:
            continue
        seen.add(key)
        hint = {
            "pluginId": plugin_id,
            "sourceLabel": clean_text(result.get("sourceLabel")) or plugin_id,
        }
        if title:
            hint["title"] = title
        if original_title:
            hint["originalTitle"] = original_title
        hints.append(hint)
    return hints


def receiver_contribution_payload(
    *,
    movie_id: UUID | str,
    movie: dict[str, Any],
    preview: dict[str, Any],
    applied: dict[str, Any],
    credits: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    proposal = preview.get("proposal") or {}
    applied_fields = applied.get("applied") or {}
    metadata_payload: dict[str, Any] = {}
    metadata_payload.update(proposal.get("metadataUpdates") or {})
    metadata_payload.update(proposal.get("movieUpdates") or {})
    metadata_payload.update(proposal.get("technicalUpdates") or {})
    media_updates = proposal.get("mediaUpdates") or {}
    if isinstance(media_updates.get("poster"), dict):
        metadata_payload.setdefault("posterUrl", media_updates["poster"].get("sourceUrl"))
    if isinstance(media_updates.get("backdrop"), dict):
        metadata_payload.setdefault("backdropUrl", media_updates["backdrop"].get("sourceUrl"))
    identifiers = proposal.get("identifiers") or {}
    if identifiers.get("tmdb"):
        metadata_payload["tmdbId"] = identifiers.get("tmdb")
    if identifiers.get("imdb"):
        metadata_payload["imdbId"] = identifiers.get("imdb")
    metadata_payload.setdefault("title", movie.get("title"))
    metadata_payload.setdefault("originalTitle", movie.get("original_title"))
    metadata_payload.setdefault("year", movie.get("year"))
    metadata_payload.setdefault("barcode", movie.get("barcode"))
    metadata_payload.setdefault("format", movie.get("format"))
    # Always include the movie's current public metadata so receivers get the
    # actual content even when this refresh only changed a few fields (the
    # proposal/applied payload only carries the changed delta).
    metadata_payload.setdefault("sortTitle", movie.get("sort_title"))
    metadata_payload.setdefault("overview", movie.get("overview"))
    metadata_payload.setdefault("rating", movie.get("rating"))
    metadata_payload.setdefault("runtimeMinutes", movie.get("runtime_minutes"))
    metadata_payload.setdefault("edition", movie.get("edition"))
    metadata_payload.setdefault("editionType", movie.get("edition_type"))
    metadata_payload.setdefault("country", movie.get("country"))
    metadata_payload.setdefault("language", movie.get("language"))
    release_date = movie.get("release_date")
    if hasattr(release_date, "isoformat"):
        release_date = release_date.isoformat()
    metadata_payload.setdefault("releaseDate", release_date)
    movie_meta = movie.get("metadata") if isinstance(movie.get("metadata"), dict) else {}
    for source_key, target_key in (
        ("trailer_url", "trailerUrl"),
        ("trailerUrl", "trailerUrl"),
        ("videos", "videos"),
    ):
        if movie_meta.get(source_key) not in (None, "", [], {}):
            metadata_payload.setdefault(target_key, movie_meta.get(source_key))
    directors: list[str] = []
    cast: list[str] = []
    credit_entries: list[dict[str, Any]] = []
    for credit in credits or []:
        name = str(credit.get("name") or "").strip()
        if not name:
            continue
        credit_type = str(credit.get("credit_type") or credit.get("creditType") or "").strip().lower()
        job = str(credit.get("job") or "").strip()
        character = str(credit.get("character") or "").strip()
        entry = {"name": name}
        if credit_type:
            entry["creditType"] = credit_type
        if job:
            entry["job"] = job
        if character:
            entry["character"] = character
        credit_entries.append(entry)
        if "director" in job.lower():
            directors.append(name)
        if credit_type in {"actor", "cast", "performer", "acting"} or character:
            cast.append(name)
    if directors:
        metadata_payload.setdefault("director", ", ".join(dict.fromkeys(directors)))
    if cast:
        metadata_payload.setdefault("actor", ", ".join(list(dict.fromkeys(cast))[:15]))
    if credit_entries:
        metadata_payload.setdefault("credits", credit_entries[:60])
    metadata_payload = {
        key: value
        for key, value in metadata_payload.items()
        if value not in (None, "", [], {})
    }
    # Never push fields the user has locked to metadata receivers.
    locked_payload_keys = locked_receiver_payload_keys(movie.get("metadata"))
    if locked_payload_keys:
        metadata_payload = {
            key: value
            for key, value in metadata_payload.items()
            if key not in locked_payload_keys
        }
    # Forward per-language localizations (e.g. TMDB translations) so receivers
    # that support localized fields can store them. Locked base fields are
    # stripped so locked values never leave DiscVault.
    localization_field_map = (
        ("title", "title"),
        ("originalTitle", "originalTitle"),
        ("overview", "overview"),
        ("edition", "edition"),
    )
    localized_entries: list[dict[str, Any]] = []
    seen_localization_langs: set[str] = set()
    for localization in proposal.get("localizations") or []:
        if not isinstance(localization, dict):
            continue
        lang = str(
            localization.get("lang")
            or localization.get("language")
            or localization.get("locale")
            or ""
        ).strip()
        if not lang:
            continue
        entry: dict[str, Any] = {"lang": lang}
        for source_key, payload_key in localization_field_map:
            if payload_key in locked_payload_keys:
                continue
            value = localization.get(source_key)
            if isinstance(value, str):
                value = value.strip()
            if value:
                entry[payload_key] = value
        if len(entry) <= 1:
            continue
        key = lang.lower()
        if key in seen_localization_langs:
            continue
        seen_localization_langs.add(key)
        localized_entries.append(entry)
    if localized_entries:
        metadata_payload["localizations"] = localized_entries
    source_providers = sorted(
        {
            str(item.get("pluginId"))
            for item in (proposal.get("provenance") or [])
            if item.get("pluginId")
        }
    )
    provider_title_hints = metadata_provider_title_hints(preview.get("results") or [])
    tmdb_title_hint = next(
        (
            item
            for item in provider_title_hints
            if str(item.get("pluginId") or "").lower() == "tmdb"
        ),
        {},
    )
    metadata_context = {
        "movieId": str(movie_id),
        "sourceProviders": source_providers,
        "acceptedFields": proposal.get("provenance") or [],
        "fieldDecisions": applied.get("fieldDecisions") or proposal.get("fieldDecisions") or [],
        "finalWrites": [
            item
            for item in (applied.get("fieldDecisions") or [])
            if isinstance(item, dict) and item.get("written")
        ],
        "applied": applied_fields,
    }
    if provider_title_hints:
        metadata_context["providerTitleHints"] = provider_title_hints
    if tmdb_title_hint.get("title"):
        metadata_context["tmdbTitle"] = tmdb_title_hint["title"]
    if tmdb_title_hint.get("originalTitle"):
        metadata_context["tmdbOriginalTitle"] = tmdb_title_hint["originalTitle"]
    return {
        "entityType": "movie",
        "identity": str(movie.get("public_id") or movie_id),
        "sourceRef": str(movie.get("public_id") or movie_id),
        "sourceReference": {
            "type": "discvault_movie",
            "key": str(movie_id),
            "publicId": movie.get("public_id"),
            "barcode": movie.get("barcode"),
        },
        "payload": metadata_payload,
        "metadata": metadata_context,
    }


def receiver_result_summary(
    plugin: dict[str, Any],
    execution: dict[str, Any],
    *,
    details: dict[str, Any] | None = None,
    activity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
    summary = {
        "pluginId": plugin.get("id"),
        "name": plugin.get("name") or plugin.get("id"),
        "version": plugin.get("version"),
        "status": execution.get("status"),
        "state": execution.get("state"),
        "elapsedMs": execution.get("elapsedMs"),
        "error": execution.get("error"),
        "resultStatus": result.get("status"),
        "entityType": result.get("entityType"),
        "provider": result.get("provider") or plugin.get("id"),
        "reason": result.get("reason"),
        "idempotencyPrefix": result.get("idempotencyPrefix"),
        "templateVersion": result.get("templateVersion"),
    }
    if isinstance(result.get("acceptedFields"), list):
        summary["acceptedFields"] = result.get("acceptedFields")
    if isinstance(result.get("droppedFields"), list):
        summary["droppedFields"] = result.get("droppedFields")
    if details:
        summary["details"] = details
    if activity:
        summary["activity"] = activity
    return summary


def plugin_receiver_runtime_entrypoints(plugin: dict[str, Any]) -> set[str]:
    manifest = plugin.get("manifest") or {}
    runtime = manifest.get("runtime") if isinstance(manifest, dict) else {}
    if not isinstance(runtime, dict):
        runtime = {}
    return {str(item) for item in (runtime.get("entrypoints") or []) if str(item)}


def plugin_receiver_optional_detail(
    plugin: dict[str, Any],
    entrypoint: str,
    payload: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    if entrypoint not in plugin_receiver_runtime_entrypoints(plugin):
        return {}
    execution = run_plugin_entrypoint(plugin["id"], entrypoint, payload, context)
    result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
    if execution.get("status") == "ok" and result:
        return result
    return {
        "status": execution.get("status") or "error",
        "state": execution.get("state") or "unavailable",
        "error": execution.get("error"),
    }


def push_metadata_to_receivers(
    conn,
    *,
    movie_id: UUID | str,
    movie: dict[str, Any],
    preview: dict[str, Any],
    applied: dict[str, Any],
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = receiver_contribution_payload(
        movie_id=movie_id,
        movie=movie,
        preview=preview,
        applied=applied,
        credits=movie_contribution_credits(conn, UUID(str(movie_id))),
    )
    return push_receiver_payload_to_receivers(conn, payload=payload, actor=actor)


def push_receiver_payload_to_receivers(
    conn,
    *,
    payload: dict[str, Any],
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    receivers = [
        plugin
        for plugin in metadata_receiver_plugins(conn)
        if "receive_metadata" in set(plugin.get("capabilities") or (plugin.get("manifest") or {}).get("capabilities") or [])
    ]
    executions: list[dict[str, Any]] = []
    for plugin in receivers:
        config = plugin_config_from_db(conn, plugin["id"])
        context = plugin_execution_context(conn, plugin, config, actor)
        details = plugin_receiver_optional_detail(plugin, "describe_payload", payload, context)
        if plugin_requires_config(plugin, config, "receive_metadata"):
            executions.append(
                receiver_result_summary(
                    plugin,
                    {
                        "status": "skipped",
                        "state": "needs_configuration",
                        "result": {"status": "skipped", "reason": "needs_configuration"},
                    },
                    details=details,
                )
            )
            continue
        execution = run_plugin_entrypoint(plugin["id"], "receive_metadata", payload, context)
        activity = plugin_receiver_optional_detail(
            plugin,
            "activity_summary",
            {"payload": payload, "execution": execution.get("result") or {}, "runtime": execution},
            context,
        )
        executions.append(receiver_result_summary(plugin, execution, details=details, activity=activity))
    return {
        "receiverCount": len(receivers),
        "receivers": executions,
        "payloadSummary": {
            "entityType": payload.get("entityType"),
            "identity": payload.get("identity"),
            "fieldCount": len(payload.get("payload") or {}),
            "fields": sorted((payload.get("payload") or {}).keys()),
            "sourceProviders": ((payload.get("metadata") or {}).get("sourceProviders") or []),
        },
    }


def preview_enrichment_payload_from_results(query: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": clean_text(query.get("title")),
        "fallbackTitle": clean_text(query.get("fallbackTitle")),
        "year": clean_text(query.get("year")),
        "barcode": clean_text(query.get("externalBarcode") or query.get("barcode")),
        "format": clean_text(query.get("format")),
        "mediaFormat": clean_text(query.get("format")),
        "tmdbId": clean_text(query.get("tmdbId")),
        "imdbId": clean_text(query.get("imdbId")),
        "previewMode": False,
        "previewEnrichment": True,
    }

    def apply_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
        payload = dict(base)
        identifiers = candidate.get("identifiers") if isinstance(candidate.get("identifiers"), dict) else {}
        movie = candidate.get("movie") if isinstance(candidate.get("movie"), dict) else {}
        for source in (candidate, movie):
            if not isinstance(source, dict):
                continue
            payload["title"] = payload.get("title") or clean_text(
                source.get("title") or source.get("name") or source.get("originalTitle") or source.get("original_title")
            )
            payload["year"] = payload.get("year") or clean_text(source.get("year") or source.get("releaseYear") or source.get("release_year"))
            payload["tmdbId"] = payload.get("tmdbId") or clean_text(source.get("tmdbId") or source.get("tmdb_id") or source.get("tmdb"))
            payload["imdbId"] = payload.get("imdbId") or clean_text(source.get("imdbId") or source.get("imdb_id") or source.get("imdb"))
            payload["format"] = payload.get("format") or clean_text(source.get("format") or source.get("mediaFormat") or source.get("media_format"))
            payload["mediaFormat"] = payload.get("mediaFormat") or payload.get("format")
        payload["tmdbId"] = payload.get("tmdbId") or clean_text(identifiers.get("tmdb") or identifiers.get("tmdbId"))
        payload["imdbId"] = payload.get("imdbId") or clean_text(identifiers.get("imdb") or identifiers.get("imdbId"))
        return {key: value for key, value in payload.items() if value not in (None, "", [], {})}

    if base.get("title") or base.get("tmdbId") or base.get("imdbId"):
        return {key: value for key, value in base.items() if value not in (None, "", [], {})}

    for result in results:
        if not isinstance(result, dict):
            continue
        movie_updates = result.get("movieUpdates") if isinstance(result.get("movieUpdates"), dict) else {}
        identifiers = result.get("identifiers") if isinstance(result.get("identifiers"), dict) else {}
        if movie_updates or identifiers:
            payload = apply_candidate({**movie_updates, "identifiers": identifiers})
            if payload.get("title") or payload.get("tmdbId") or payload.get("imdbId"):
                return payload
        for candidate in result.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            payload = apply_candidate(candidate)
            if payload.get("title") or payload.get("tmdbId") or payload.get("imdbId"):
                return payload
    return {}


def run_metadata_source_pipeline(
    conn,
    *,
    query: dict[str, Any],
    current: dict[str, Any] | None = None,
    technical_current: dict[str, Any] | None = None,
    actor: dict[str, Any] | None = None,
    exclude_plugin_ids: set[str] | None = None,
    enable_metadata_lookup_bridge: bool = True,
) -> dict[str, Any]:
    excluded = {str(item) for item in (exclude_plugin_ids or set()) if str(item)}
    plugins = [
        plugin
        for plugin in metadata_source_plugins(conn)
        if str(plugin.get("id") or "") not in excluded
        and metadata_source_plugin_allowed(plugin, query)
    ]
    overwrite_enabled = preferred_provider_overwrite(conn)
    executions: list[dict[str, Any]] = []
    normalized_results: list[dict[str, Any]] = []
    target_format = query.get("format") or (current or {}).get("format") or ""

    def metadata_lookup_bridge(
        lookup_payload: dict[str, Any] | None = None,
        *,
        excludeProviders: list[str] | tuple[str, ...] | set[str] | None = None,
        excludePluginIds: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> dict[str, Any]:
        payload = lookup_payload if isinstance(lookup_payload, dict) else {}
        bridge_excluded = set(MOVIEVAULT_PLUGIN_IDS)
        bridge_excluded.update(str(item) for item in (excludeProviders or []) if str(item))
        bridge_excluded.update(str(item) for item in (excludePluginIds or []) if str(item))
        bridge_query = query_from_payload(payload)
        if not bridge_query.get("format") and query.get("format"):
            bridge_query["format"] = query.get("format")
            bridge_query["normalizedFormat"] = normalize_media_format(query.get("format"))
        return run_metadata_source_pipeline(
            conn,
            query=bridge_query,
            current={"format": bridge_query.get("format") or "", "metadata": {}},
            technical_current={},
            actor=actor,
            exclude_plugin_ids=bridge_excluded,
            enable_metadata_lookup_bridge=False,
        )

    for plugin in plugins:
        config = plugin_config_from_db(conn, plugin["id"])
        context = plugin_execution_context(conn, plugin, config, actor)
        if enable_metadata_lookup_bridge and is_movievault_plugin(str(plugin.get("id") or "")):
            context["metadataLookup"] = metadata_lookup_bridge
        for planned in plugin_execution_plan(plugin, query):
            entrypoint = planned["entrypoint"]
            if plugin_requires_config(plugin, config, entrypoint):
                executions.append(
                    {
                        "pluginId": plugin["id"],
                        "entrypoint": entrypoint,
                        "status": "skipped",
                        "state": "needs_configuration",
                        "configured": False,
                        "resultStatus": None,
                        "candidateCount": 0,
                    }
                )
                continue
            execution = run_plugin_entrypoint(plugin["id"], entrypoint, planned["payload"], context)
            execution_item = {
                "pluginId": plugin["id"],
                "entrypoint": entrypoint,
                "status": execution.get("status"),
                "state": execution.get("state"),
                "elapsedMs": execution.get("elapsedMs"),
                "error": execution.get("error"),
                "configured": True,
                "resultStatus": None,
                "candidateCount": 0,
                "normalizedSourceFormat": "",
            }
            executions.append(execution_item)
            if execution.get("status") != "ok":
                continue
            normalized = canonicalize_plugin_result(
                plugin["id"],
                entrypoint,
                execution.get("result") or {},
            )
            execution_item["resultStatus"] = normalized.get("status")
            execution_item["candidateCount"] = len(normalized.get("candidates") or [])
            execution_item["normalizedSourceFormat"] = normalized.get("normalizedSourceFormat") or ""
            if normalized.get("status") in {"miss", "not_found", "needs_configuration"}:
                continue
            normalized_results.append(normalized)

    preview_enrichment: dict[str, Any] = {"enabled": False, "payload": {}, "plugins": []}
    has_box_set_preview = any(
        bool(item.get("boxSetProposal") or item.get("boxSetProposals"))
        for item in normalized_results
        if isinstance(item, dict)
    )
    if query.get("previewMode") and not (query.get("detectBoxSets") and has_box_set_preview):
        enrichment_payload = preview_enrichment_payload_from_results(query, normalized_results)
        if enrichment_payload and (
            enrichment_payload.get("title")
            or enrichment_payload.get("tmdbId")
            or enrichment_payload.get("imdbId")
        ):
            preview_enrichment = {"enabled": True, "payload": enrichment_payload, "plugins": []}
            plugins_with_results = {str(item.get("pluginId") or "") for item in normalized_results}
            for plugin in plugins:
                plugin_id = str(plugin.get("id") or "")
                capabilities = set(plugin.get("capabilities") or (plugin.get("manifest") or {}).get("capabilities") or [])
                if plugin_id in plugins_with_results or "movie_details" not in capabilities:
                    continue
                config = plugin_config_from_db(conn, plugin_id)
                context = plugin_execution_context(conn, plugin, config, actor)
                if enable_metadata_lookup_bridge and is_movievault_plugin(plugin_id):
                    context["metadataLookup"] = metadata_lookup_bridge
                execution_item = {
                    "pluginId": plugin_id,
                    "entrypoint": "movie_details",
                    "status": "skipped",
                    "state": "preview_enrichment",
                    "elapsedMs": None,
                    "error": None,
                    "configured": True,
                    "resultStatus": None,
                    "candidateCount": 0,
                    "normalizedSourceFormat": "",
                    "previewEnrichment": True,
                }
                executions.append(execution_item)
                if plugin_requires_config(plugin, config, "movie_details"):
                    execution_item["state"] = "needs_configuration"
                    execution_item["configured"] = False
                    preview_enrichment["plugins"].append({"pluginId": plugin_id, "state": "needs_configuration"})
                    continue
                execution = run_plugin_entrypoint(plugin_id, "movie_details", enrichment_payload, context)
                execution_item.update(
                    {
                        "status": execution.get("status"),
                        "state": execution.get("state") or "preview_enrichment",
                        "elapsedMs": execution.get("elapsedMs"),
                        "error": execution.get("error"),
                    }
                )
                if execution.get("status") != "ok":
                    preview_enrichment["plugins"].append({"pluginId": plugin_id, "state": execution_item.get("state"), "status": execution.get("status")})
                    continue
                normalized = canonicalize_plugin_result(
                    plugin_id,
                    "movie_details",
                    execution.get("result") or {},
                )
                execution_item["resultStatus"] = normalized.get("status")
                execution_item["candidateCount"] = len(normalized.get("candidates") or [])
                execution_item["normalizedSourceFormat"] = normalized.get("normalizedSourceFormat") or ""
                preview_enrichment["plugins"].append(
                    {
                        "pluginId": plugin_id,
                        "state": execution_item.get("state"),
                        "status": execution.get("status"),
                        "resultStatus": normalized.get("status"),
                        "creditCount": len(normalized.get("credits") or []),
                        "localizationCount": len(normalized.get("localizations") or []),
                    }
                )
                if normalized.get("status") in {"miss", "not_found", "needs_configuration"}:
                    continue
                normalized_results.append(normalized)

    merge = merge_metadata_results(
        current=current,
        technical_current=technical_current,
        results=normalized_results,
        overwrite_enabled=overwrite_enabled,
        target_format=target_format,
    )
    source_summary = summarize_metadata_execution(
        plugins=plugins,
        executions=executions,
        results=normalized_results,
        proposal=merge,
    )
    field_decisions = merge.get("fieldDecisions") or []
    return {
        "query": query,
        "settings": {"preferredProviderOverwrite": overwrite_enabled},
        "sourceOrder": [plugin["id"] for plugin in plugins],
        "executions": executions,
        "sourceSummary": source_summary,
        "results": normalized_results,
        "previewEnrichment": preview_enrichment,
        "proposalStats": {
            "acceptedFields": len(merge.get("provenance") or []),
            "skippedFields": len(merge.get("skipped") or []),
            "creditUpdates": len(merge.get("credits") or []),
            "fieldDecisions": len(field_decisions),
            "conflictFields": len([item for item in field_decisions if item.get("conflict")]),
            "winnerFields": len([item for item in field_decisions if item.get("winner")]),
            "previewEnrichmentPlugins": len(preview_enrichment.get("plugins") or []),
            "updateFields": count_update_fields(merge),
            "formatBlockedFields": len(
                [
                    item
                    for item in (merge.get("skipped") or [])
                    if "format mismatch" in str(item.get("reason") or "")
                ]
            ),
        },
        "proposal": merge,
    }


def preview_movie_metadata(conn, movie_id: UUID | str, actor: dict[str, Any] | None = None) -> dict[str, Any]:
    movie_uuid = UUID(str(movie_id))
    context = movie_lookup_context(conn, movie_uuid)
    pipeline = run_metadata_source_pipeline(
        conn,
        query=context["query"],
        current=context["movie"],
        technical_current=context["technicalSpecs"],
        actor=actor,
    )
    return {"movie": context["movie"], "technicalSpecs": context["technicalSpecs"], **pipeline}


def lookup_metadata_sources(conn, payload: dict[str, Any], actor: dict[str, Any] | None = None) -> dict[str, Any]:
    query = query_from_payload(payload)
    return run_metadata_source_pipeline(
        conn,
        query=query,
        current={"format": query.get("format") or "", "metadata": {}},
        technical_current={},
        actor=actor,
    )


def ensure_sync_state(conn) -> int:
    if not table_exists(conn, "sync_state"):
        return 0
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sync_state (id, revision)
            VALUES ('global', 0)
            ON CONFLICT (id) DO NOTHING
            """
        )
        cur.execute("SELECT revision FROM sync_state WHERE id='global'")
        row = cur.fetchone()
    return int(row["revision"] if row else 0)


def next_revision(conn) -> int:
    if not table_exists(conn, "sync_state"):
        return 0
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE sync_state
            SET revision = revision + 1,
                updated_at = now()
            WHERE id='global'
            RETURNING revision
            """
        )
        row = cur.fetchone()
    if not row:
        ensure_sync_state(conn)
        return next_revision(conn)
    return int(row["revision"])


def record_sync_change(conn, movie_id: UUID, payload: dict[str, Any]) -> int:
    if not table_exists(conn, "sync_changes"):
        return 0
    revision = next_revision(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sync_changes (revision, entity_type, entity_id, operation, payload)
            VALUES (%s, 'movie', %s, 'movie.metadata_refresh', %s)
            """,
            (revision, str(movie_id), Jsonb(json_ready(payload))),
        )
    return revision


def media_url_fingerprint(kind: str, variant: str, source_url: str) -> str:
    return hashlib.sha256(f"{kind}:{variant}:{source_url}".encode("utf-8")).hexdigest()


def media_asset_uuid(storage_key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.UUID("7c76309b-063d-4c63-b925-2f49fdad332c"), f"media_assets:{storage_key}")


def ensure_remote_media_asset(conn, *, kind: str, source_url: str, provider_id: str) -> UUID | None:
    if kind not in {"poster", "backdrop", "profile"}:
        return None
    source_url = clean_text(source_url) or ""
    if not source_url.startswith(("http://", "https://")):
        return None
    variant = "original"
    digest = media_url_fingerprint(kind, variant, source_url)
    storage_key = f"remote/{kind}/{digest}"
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id
            FROM media_assets
            WHERE storage_backend='local' AND storage_key=%s
            """,
            (storage_key,),
        )
        row = cur.fetchone()
        if row:
            return row["id"] if isinstance(row, dict) else row[0]
        media_id = media_asset_uuid(storage_key)
        cur.execute(
            """
            INSERT INTO media_assets (
                id,
                kind,
                variant,
                storage_backend,
                storage_key,
                source_url,
                provider_id,
                content_type,
                sha256,
                metadata
            )
            VALUES (%s, %s, %s, 'local', %s, %s, %s, NULL, %s, %s)
            ON CONFLICT (storage_backend, storage_key) DO UPDATE SET
                source_url=EXCLUDED.source_url,
                provider_id=EXCLUDED.provider_id
            RETURNING id
            """,
            (
                media_id,
                kind,
                variant,
                storage_key,
                source_url,
                clean_text(provider_id),
                digest,
                Jsonb({"source": "metadata_refresh"}),
            ),
        )
        row = cur.fetchone()
        return row["id"] if isinstance(row, dict) else row[0]


def apply_primary_media_update(
    conn,
    *,
    movie_id: UUID,
    kind: str,
    source_url: str,
    provider_id: str,
) -> dict[str, Any] | None:
    media_id = ensure_remote_media_asset(conn, kind=kind, source_url=source_url, provider_id=provider_id)
    if not media_id or not table_exists(conn, "entity_media"):
        return None
    role = kind
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ma.id, ma.source_url, ma.storage_key, ma.metadata
            FROM entity_media em
            JOIN media_assets ma ON ma.id = em.media_id
            WHERE em.entity_type='movie'
              AND em.entity_id=%s
              AND em.deleted_at IS NULL
              AND em.role=%s
              AND ma.kind=%s
              AND em.is_primary=true
            ORDER BY em.sort_order, ma.created_at
            LIMIT 1
            """,
            (movie_id, role, kind),
        )
        current = cur.fetchone()
        if current and str(current["id"] if isinstance(current, dict) else current[0]) == str(media_id):
            return None
        cur.execute(
            """
            SELECT deleted_at
            FROM entity_media
            WHERE entity_type='movie'
              AND entity_id=%s
              AND media_id=%s
              AND role=%s
            """,
            (movie_id, media_id, role),
        )
        existing_link = cur.fetchone()
        if existing_link and (existing_link["deleted_at"] if isinstance(existing_link, dict) else existing_link[0]):
            return None
        current_metadata = current.get("metadata") if isinstance(current, dict) else (current[3] if current else {})
        current_metadata = current_metadata if isinstance(current_metadata, dict) else {}
        if current and (
            current_metadata.get("lockedPrimary")
            or current_metadata.get("locked_primary")
            or current_metadata.get("userSelectedPrimary")
            or current_metadata.get("user_selected_primary")
            or current_metadata.get("source") in {"upload", "user_upload", "manual_selection"}
        ):
            linked = link_media_option(
                conn,
                movie_id=movie_id,
                kind=kind,
                source_url=source_url,
                provider_id=provider_id,
                sort_order=30,
            )
            return {
                "kind": kind,
                "mediaId": str(media_id),
                "sourceUrl": source_url,
                "providerId": provider_id,
                "lockedPrimary": True,
                "option": linked,
            }
        cur.execute(
            """
            UPDATE entity_media
            SET is_primary=false, sort_order=GREATEST(sort_order, 1)
            WHERE entity_type='movie'
              AND entity_id=%s
              AND deleted_at IS NULL
              AND role=%s
              AND is_primary=true
            """,
            (movie_id, role),
        )
        cur.execute(
            """
            INSERT INTO entity_media (
                entity_type,
                entity_id,
                media_id,
                role,
                is_primary,
                sort_order
            )
            VALUES ('movie', %s, %s, %s, true, 0)
            ON CONFLICT (entity_type, entity_id, media_id, role) DO UPDATE SET
                is_primary=true,
                sort_order=0
            """,
            (movie_id, media_id, role),
        )
        cur.execute("UPDATE movies SET updated_at=now() WHERE id=%s", (movie_id,))
    return {
        "kind": kind,
        "mediaId": str(media_id),
        "sourceUrl": source_url,
        "providerId": provider_id,
    }


def has_locked_primary_media(conn, *, movie_id: UUID, kind: str) -> bool:
    if kind not in {"poster", "backdrop"} or not table_exists(conn, "entity_media") or not table_exists(conn, "media_assets"):
        return False
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ma.metadata
            FROM entity_media em
            JOIN media_assets ma ON ma.id = em.media_id
            WHERE em.entity_type='movie'
              AND em.entity_id=%s
              AND em.deleted_at IS NULL
              AND em.role=%s
              AND ma.kind=%s
              AND em.is_primary=true
            ORDER BY em.sort_order, ma.created_at
            LIMIT 1
            """,
            (movie_id, kind, kind),
        )
        row = cur.fetchone()
    metadata = row.get("metadata") if isinstance(row, dict) and row else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    return bool(
        metadata.get("lockedPrimary")
        or metadata.get("locked_primary")
        or metadata.get("userSelectedPrimary")
        or metadata.get("user_selected_primary")
        or metadata.get("source") in {"upload", "user_upload", "manual_selection"}
    )


def link_media_option(
    conn,
    *,
    movie_id: UUID,
    kind: str,
    source_url: str,
    provider_id: str,
    sort_order: int,
) -> dict[str, Any] | None:
    media_id = ensure_remote_media_asset(conn, kind=kind, source_url=source_url, provider_id=provider_id)
    if not media_id or not table_exists(conn, "entity_media"):
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT is_primary, deleted_at
            FROM entity_media
            WHERE entity_type='movie'
              AND entity_id=%s
              AND media_id=%s
              AND role=%s
            """,
            (movie_id, media_id, kind),
        )
        row = cur.fetchone()
        if row and (row["deleted_at"] if isinstance(row, dict) else row[1]):
            return None
        if row and bool(row["is_primary"] if isinstance(row, dict) else row[0]):
            return None
        cur.execute(
            """
            INSERT INTO entity_media (
                entity_type,
                entity_id,
                media_id,
                role,
                is_primary,
                sort_order
            )
            VALUES ('movie', %s, %s, %s, false, %s)
            ON CONFLICT (entity_type, entity_id, media_id, role) DO UPDATE SET
                sort_order=LEAST(entity_media.sort_order, EXCLUDED.sort_order)
            """,
            (movie_id, media_id, kind, sort_order),
        )
    return {
        "kind": kind,
        "mediaId": str(media_id),
        "sourceUrl": source_url,
        "providerId": provider_id,
    }


def metadata_person_public_id(credit: dict[str, Any]) -> str:
    tmdb_id = clean_text(credit.get("tmdbId"))
    if tmdb_id:
        return f"metadata-person-tmdb-{tmdb_id}"
    digest = hashlib.sha256((clean_text(credit.get("name")) or "").casefold().encode("utf-8")).hexdigest()[:20]
    return f"metadata-person-{digest}"


def ensure_metadata_person(conn, credit: dict[str, Any]) -> UUID | None:
    name = clean_text(credit.get("name"))
    if not name or not table_exists(conn, "people"):
        return None
    tmdb_id = clean_text(credit.get("tmdbId"))
    person_id: UUID | None = None
    metadata = {
        "source": "metadata_refresh",
        "source_provider": clean_text(credit.get("sourceProvider")),
        "source_label": clean_text(credit.get("sourceLabel")),
        "source_ref": clean_text(credit.get("sourceRef")),
    }
    if tmdb_id:
        metadata["tmdb_id"] = tmdb_id
    for image_key in ("profileUrl", "profile_url", "photoUrl", "photo_url", "profilePath", "profile_path", "photoFile", "photo_file"):
        if value_present(credit.get(image_key)):
            metadata[image_key] = credit.get(image_key)

    with conn.cursor() as cur:
        if tmdb_id and table_exists(conn, "person_identifiers"):
            cur.execute(
                """
                SELECT person_id
                FROM person_identifiers
                WHERE provider_id='tmdb'
                  AND identifier_type='person_id'
                  AND identifier=%s
                LIMIT 1
                """,
                (tmdb_id,),
            )
            row = cur.fetchone()
            if row:
                person_id = row["person_id"] if isinstance(row, dict) else row[0]
        if not person_id:
            cur.execute(
                """
                SELECT id
                FROM people
                WHERE lower(name)=lower(%s)
                ORDER BY created_at
                LIMIT 1
                """,
                (name,),
            )
            row = cur.fetchone()
            if row:
                person_id = row["id"] if isinstance(row, dict) else row[0]
        if person_id:
            cur.execute(
                """
                UPDATE people
                SET name=%s,
                    known_for=COALESCE(known_for, %s),
                    metadata=metadata || %s,
                    updated_at=now()
                WHERE id=%s
                """,
                (
                    name,
                    "Acting" if clean_text(credit.get("role")) == "actor" else clean_text(credit.get("job")),
                    Jsonb(json_ready(metadata)),
                    person_id,
                ),
            )
        else:
            public_id = metadata_person_public_id(credit)
            person_id = metadata_stable_uuid("people", public_id)
            cur.execute(
                """
                INSERT INTO people (
                    id, public_id, name, known_for, metadata, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, now(), now())
                ON CONFLICT (public_id) DO UPDATE SET
                    name=EXCLUDED.name,
                    known_for=COALESCE(people.known_for, EXCLUDED.known_for),
                    metadata=people.metadata || EXCLUDED.metadata,
                    updated_at=now()
                RETURNING id
                """,
                (
                    person_id,
                    public_id,
                    name,
                    "Acting" if clean_text(credit.get("role")) == "actor" else clean_text(credit.get("job")),
                    Jsonb(json_ready(metadata)),
                ),
            )
            row = cur.fetchone()
            if row:
                person_id = row["id"] if isinstance(row, dict) else row[0]
        if tmdb_id and person_id and table_exists(conn, "person_identifiers"):
            cur.execute(
                """
                INSERT INTO person_identifiers (person_id, provider_id, identifier_type, identifier)
                VALUES (%s, 'tmdb', 'person_id', %s)
                ON CONFLICT DO NOTHING
                """,
                (person_id, tmdb_id),
            )
    return UUID(str(person_id)) if person_id else None


def apply_credit_updates(conn, *, movie_id: UUID, credits: list[dict[str, Any]]) -> dict[str, Any]:
    if not credits:
        return {"written": 0, "updated": 0, "people": 0, "skipped": 0}
    if not table_exists(conn, "people") or not table_exists(conn, "movie_credits"):
        return {"written": 0, "updated": 0, "people": 0, "skipped": len(credits), "reason": "credit_tables_missing"}

    written = 0
    updated = 0
    people: set[str] = set()
    skipped = 0
    applied_items: list[dict[str, Any]] = []
    with conn.cursor() as cur:
        for credit in credits:
            if not isinstance(credit, dict):
                skipped += 1
                continue
            person_id = ensure_metadata_person(conn, credit)
            if not person_id:
                skipped += 1
                continue
            people.add(str(person_id))
            role = normalize_credit_role(credit.get("role"), default="credit")
            character = clean_text(credit.get("character")) or None
            job = clean_text(credit.get("job")) or None
            sort_order = parse_sort_order(credit.get("sortOrder"), 0)
            cur.execute(
                """
                SELECT id
                FROM movie_credits
                WHERE movie_id=%s
                  AND person_id=%s
                  AND credit_type=%s
                  AND job IS NOT DISTINCT FROM %s
                  AND character IS NOT DISTINCT FROM %s
                LIMIT 1
                """,
                (movie_id, person_id, role, job, character),
            )
            row = cur.fetchone()
            if row:
                credit_id = row["id"] if isinstance(row, dict) else row[0]
                cur.execute(
                    """
                    UPDATE movie_credits
                    SET sort_order=%s
                    WHERE id=%s
                    """,
                    (sort_order, credit_id),
                )
                updated += 1
            else:
                credit_id = metadata_stable_uuid(
                    "movie_credits",
                    f"{movie_id}:{person_id}:{role}:{job or ''}:{character or ''}",
                )
                cur.execute(
                    """
                    INSERT INTO movie_credits (
                        id, movie_id, person_id, credit_type, character, job, sort_order
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (credit_id, movie_id, person_id, role, character, job, sort_order),
                )
                written += int(cur.rowcount or 0)
            applied_items.append(
                {
                    "name": clean_text(credit.get("name")),
                    "role": role,
                    "character": character,
                    "job": job,
                    "sortOrder": sort_order,
                    "sourceProvider": clean_text(credit.get("sourceProvider")),
                    "sourceLabel": clean_text(credit.get("sourceLabel")),
                }
            )
    return {
        "written": written,
        "updated": updated,
        "people": len(people),
        "skipped": skipped,
        "items": applied_items[:40],
        "itemCount": len(applied_items),
    }


def apply_metadata_proposal(
    conn,
    movie_id: UUID | str,
    proposal: dict[str, Any],
    *,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    movie_uuid = UUID(str(movie_id))
    movie_updates = proposal.get("movieUpdates") or {}
    metadata_updates = proposal.get("metadataUpdates") or {}
    technical_updates = proposal.get("technicalUpdates") or {}
    media_updates = proposal.get("mediaUpdates") or {}
    identifiers = proposal.get("identifiers") or {}
    credit_updates = proposal.get("credits") or []
    localization_updates = proposal.get("localizations") or []
    provenance = proposal.get("provenance") or []
    metadata_updates = dict(metadata_updates)
    if metadata_updates:
        if has_locked_primary_media(conn, movie_id=movie_uuid, kind="poster"):
            for key in ("poster_url", "posterUrl", "poster"):
                metadata_updates.pop(key, None)
        if has_locked_primary_media(conn, movie_id=movie_uuid, kind="backdrop"):
            for key in ("backdrop_url", "backdropUrl", "backdrop"):
                metadata_updates.pop(key, None)
    changed = bool(movie_updates or metadata_updates or technical_updates or media_updates or identifiers or credit_updates or localization_updates)

    if not changed:
        return {
            "changed": False,
            "revision": 0,
            "applied": {},
            "fieldDecisions": metadata_field_decisions_with_write_state(
                proposal.get("fieldDecisions") or [],
                applied={"changed": False, "applied": {}},
                dry_run=False,
            ),
        }

    applied_credit_updates: dict[str, Any] = {}
    with conn.cursor() as cur:
        if movie_updates or metadata_updates:
            assignments = ["updated_at=now()"]
            values: list[Any] = []
            for field, value in movie_updates.items():
                if field not in METADATA_MAIN_FIELDS:
                    continue
                assignments.append(f"{field}=%s")
                values.append(value)
            if metadata_updates:
                assignments.append("metadata=metadata || %s")
                values.append(Jsonb(json_ready(metadata_updates)))
            values.append(movie_uuid)
            cur.execute(
                f"""
                UPDATE movies
                SET {', '.join(assignments)}
                WHERE id=%s
                """,
                values,
            )

        if technical_updates and table_exists(conn, "movie_technical_specs"):
            cur.execute(
                """
                INSERT INTO movie_technical_specs (
                    movie_id, hdr, packaging, screen_ratios, audio_tracks, subtitles, regions, content_ratings, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (movie_id) DO UPDATE SET
                    hdr=COALESCE(EXCLUDED.hdr, movie_technical_specs.hdr),
                    packaging=COALESCE(EXCLUDED.packaging, movie_technical_specs.packaging),
                    screen_ratios=COALESCE(EXCLUDED.screen_ratios, movie_technical_specs.screen_ratios),
                    audio_tracks=CASE
                        WHEN EXCLUDED.audio_tracks <> '[]'::jsonb THEN EXCLUDED.audio_tracks
                        ELSE movie_technical_specs.audio_tracks
                    END,
                    subtitles=CASE
                        WHEN EXCLUDED.subtitles <> '[]'::jsonb THEN EXCLUDED.subtitles
                        ELSE movie_technical_specs.subtitles
                    END,
                    regions=CASE
                        WHEN EXCLUDED.regions <> '[]'::jsonb THEN EXCLUDED.regions
                        ELSE movie_technical_specs.regions
                    END,
                    content_ratings=CASE
                        WHEN EXCLUDED.content_ratings <> '{}'::jsonb THEN EXCLUDED.content_ratings
                        ELSE movie_technical_specs.content_ratings
                    END,
                    updated_at=now()
                """,
                (
                    movie_uuid,
                    technical_updates.get("hdr"),
                    technical_updates.get("packaging"),
                    technical_updates.get("screen_ratios"),
                    Jsonb(json_ready(technical_updates.get("audio_tracks") or [])),
                    Jsonb(json_ready(technical_updates.get("subtitles") or [])),
                    Jsonb(json_ready(technical_updates.get("regions") or [])),
                    Jsonb(json_ready(technical_updates.get("content_ratings") or {})),
                ),
            )

        if identifiers and table_exists(conn, "movie_identifiers"):
            for provider, identifier in identifiers.items():
                if not identifier:
                    continue
                cur.execute(
                    """
                    INSERT INTO movie_identifiers (movie_id, provider_id, identifier_type, identifier)
                    VALUES (%s, %s, 'movie_id', %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (movie_uuid, provider, str(identifier)),
                )

        applied_localizations: list[dict[str, Any]] = []
        if localization_updates and table_exists(conn, "movie_localizations"):
            for item in localization_updates:
                if not isinstance(item, dict):
                    continue
                lang = clean_text(item.get("lang") or item.get("language") or item.get("locale"))
                title = clean_text(item.get("title"))
                overview = clean_text(item.get("overview"))
                if not lang or not (title or overview):
                    continue
                cur.execute(
                    """
                    INSERT INTO movie_localizations (movie_id, lang, title, overview, updated_at)
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (movie_id, lang) DO UPDATE SET
                        title=COALESCE(NULLIF(EXCLUDED.title, ''), movie_localizations.title),
                        overview=COALESCE(NULLIF(EXCLUDED.overview, ''), movie_localizations.overview),
                        updated_at=now()
                    """,
                    (movie_uuid, lang, title or None, overview or None),
                )
                applied_localizations.append({"lang": lang, "title": title, "overview": overview})

        applied_media_updates: dict[str, Any] = {}
        if media_updates and table_exists(conn, "media_assets") and table_exists(conn, "entity_media"):
            for kind, media_update in media_updates.items():
                if kind not in {"poster", "backdrop"} or not isinstance(media_update, dict):
                    continue
                source_url = clean_text(media_update.get("sourceUrl")) or ""
                provider_id = clean_text(media_update.get("providerId")) or "metadata"
                applied_media = apply_primary_media_update(
                    conn,
                    movie_id=movie_uuid,
                    kind=kind,
                    source_url=source_url,
                    provider_id=provider_id,
                )
                option_results = []
                for sort_order, option_url in enumerate(image_url_options(media_update.get("options")), start=1):
                    if option_url == source_url:
                        continue
                    option_result = link_media_option(
                        conn,
                        movie_id=movie_uuid,
                        kind=kind,
                        source_url=option_url,
                        provider_id=provider_id,
                        sort_order=sort_order,
                    )
                    if option_result:
                        option_results.append(option_result)
                if applied_media:
                    applied_media_updates[kind] = applied_media
                if option_results:
                    applied_media_updates.setdefault(kind, {"kind": kind, "sourceUrl": source_url, "providerId": provider_id})
                    applied_media_updates[kind]["optionsAdded"] = option_results

        if provenance and table_exists(conn, "metadata_field_provenance"):
            for item in provenance:
                plugin_id = clean_text(item.get("pluginId"))
                field_name = clean_text(item.get("field"))
                if not plugin_id or not field_name:
                    continue
                cur.execute(
                    """
                    INSERT INTO metadata_field_provenance (
                        entity_type, entity_id, field_name, plugin_id, source_ref, confidence, payload
                    )
                    VALUES ('movie', %s, %s, %s, %s, NULL, %s)
                    """,
                    (
                        movie_uuid,
                        field_name,
                        plugin_id,
                        clean_text(item.get("sourceRef")),
                        Jsonb(json_ready(item)),
                    ),
                )

    if credit_updates:
        applied_credit_updates = apply_credit_updates(conn, movie_id=movie_uuid, credits=credit_updates)

    revision = record_sync_change(
        conn,
        movie_uuid,
        {
            "movieId": str(movie_uuid),
            "movieUpdates": movie_updates,
            "metadataUpdates": metadata_updates,
            "technicalUpdates": technical_updates,
            "mediaUpdates": media_updates,
            "identifiers": identifiers,
            "credits": credit_updates,
            "localizations": localization_updates,
            "fieldDecisions": proposal.get("fieldDecisions") or [],
        },
    )
    applied_payload = {
        "movieUpdates": movie_updates,
        "metadataUpdates": metadata_updates,
        "technicalUpdates": technical_updates,
        "mediaUpdates": applied_media_updates,
        "identifiers": identifiers,
        "credits": applied_credit_updates,
        "localizations": applied_localizations,
        "provenance": len(provenance),
    }
    return {
        "changed": True,
        "revision": revision,
        "applied": applied_payload,
        "fieldDecisions": metadata_field_decisions_with_write_state(
            proposal.get("fieldDecisions") or [],
            applied={"changed": True, "applied": applied_payload},
            dry_run=False,
        ),
    }


def refresh_movie_metadata(
    conn,
    movie_id: UUID | str,
    *,
    dry_run: bool = False,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    preview = preview_movie_metadata(conn, movie_id, actor)
    if dry_run:
        insert_metadata_audit_event(
            conn,
            event_type="metadata.refresh_fetched",
            actor=actor,
            movie_id=movie_id,
            summary=f"Fetched metadata refresh preview for {preview['movie'].get('title') or movie_id}",
            metadata=metadata_fetch_audit_payload(
                movie_id=movie_id,
                movie=preview["movie"],
                dry_run=True,
                preview=preview,
                applied={"changed": False, "revision": 0, "applied": {}},
            ),
        )
        return {"dryRun": True, "preview": preview, "applied": {"changed": False}}
    applied = apply_metadata_proposal(conn, movie_id, preview["proposal"], actor=actor)
    insert_metadata_audit_event(
        conn,
        event_type="metadata.refresh_fetched",
        actor=actor,
        movie_id=movie_id,
        summary=f"Fetched and merged metadata for {preview['movie'].get('title') or movie_id}",
        metadata=metadata_fetch_audit_payload(
            movie_id=movie_id,
            movie=preview["movie"],
            dry_run=False,
            preview=preview,
            applied=applied,
        ),
    )
    receiver_summary: dict[str, Any]
    if applied.get("changed"):
        receiver_summary = push_metadata_to_receivers(
            conn,
            movie_id=movie_id,
            movie=preview["movie"],
            preview=preview,
            applied=applied,
            actor=actor,
        )
    else:
        receiver_summary = {
            "receiverCount": 0,
            "receivers": [],
            "skipped": True,
            "reason": "metadata_refresh_did_not_change_public_fields",
        }
    insert_metadata_audit_event(
        conn,
        event_type="metadata.receiver_pushed",
        actor=actor,
        movie_id=movie_id,
        summary=f"Pushed refreshed metadata to receiver plugins for {preview['movie'].get('title') or movie_id}",
        metadata={
            "movieId": str(movie_id),
            "title": preview["movie"].get("title"),
            "barcode": preview["movie"].get("barcode"),
            "format": preview["movie"].get("format"),
            "changed": bool(applied.get("changed")),
            **receiver_summary,
        },
    )
    return {"dryRun": False, "preview": preview, "applied": applied, "receivers": receiver_summary}
