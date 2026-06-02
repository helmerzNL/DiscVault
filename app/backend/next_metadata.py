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

MOVIE_FIELD_ALIASES = {
    "sortTitle": "sort_title",
    "originalTitle": "original_title",
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
    return bool(
        query.get("previewMode")
        and query.get("externalBarcode")
        and not clean_text(query.get("title"))
        and not clean_text(query.get("fallbackTitle"))
        and not clean_text(query.get("tmdbId"))
        and not clean_text(query.get("imdbId"))
    )


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
            return plan
        if title:
            if "search_title" in capabilities:
                add("search_title", base_payload)
            else:
                add("movie_details", base_payload)
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
    collect(release_source, release=True)

    technical = result.get("technicalSpecs") or result.get("technical_specs") or {}
    if isinstance(technical, dict):
        collect(technical, release=True)

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
    source_label = result.get("sourceLabel") or result.get("providerLabel") or plugin_id
    source_ref = result.get("sourceRef") or result.get("sourceUrl") or result.get("source_url") or ""
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
        "mediaUpdates": media_updates,
        "identifiers": identifiers,
        "candidates": result.get("items") or result.get("candidates") or [],
        "boxSetProposal": result.get("boxSetProposal") or result.get("box_set_proposal"),
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
    if release_priority and field in METADATA_DISPLAY_TITLE_FIELDS:
        return False, "release source cannot update canonical display title"
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
    provenance: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    working_movie = dict(current)
    working_metadata = dict(current.get("metadata") or {})
    working_technical = dict(technical_current)

    def merge_bucket(bucket: dict[str, Any], target: str, result: dict[str, Any], release_priority: bool) -> None:
        for field, value in bucket.items():
            if target == "technical":
                current_value = working_technical.get(field)
            elif target == "metadata":
                current_value = working_metadata.get(field)
            else:
                current_value = working_movie.get(field)
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
            provenance.append({**item, "reason": "provider image selected as primary media asset"})
        for provider, identifier in (result.get("identifiers") or {}).items():
            if identifier and provider not in identifiers:
                identifiers[provider] = identifier

    return {
        "movieUpdates": movie_updates,
        "metadataUpdates": metadata_updates,
        "technicalUpdates": technical_updates,
        "mediaUpdates": media_updates,
        "identifiers": identifiers,
        "provenance": provenance,
        "skipped": skipped,
    }


def count_update_fields(proposal: dict[str, Any]) -> int:
    return sum(
        len(proposal.get(key) or {})
        for key in ("movieUpdates", "metadataUpdates", "technicalUpdates", "mediaUpdates", "identifiers")
    )


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
        "proposalStats": preview.get("proposalStats") or {},
        "applied": applied.get("applied") if applied else {},
    }


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
    metadata_payload = {
        key: value
        for key, value in metadata_payload.items()
        if value not in (None, "", [], {})
    }
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
    return {
        "query": query,
        "settings": {"preferredProviderOverwrite": overwrite_enabled},
        "sourceOrder": [plugin["id"] for plugin in plugins],
        "executions": executions,
        "sourceSummary": source_summary,
        "results": normalized_results,
        "proposalStats": {
            "acceptedFields": len(merge.get("provenance") or []),
            "skippedFields": len(merge.get("skipped") or []),
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
    if kind not in {"poster", "backdrop"}:
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
            SELECT is_primary
            FROM entity_media
            WHERE entity_type='movie'
              AND entity_id=%s
              AND media_id=%s
              AND role=%s
            """,
            (movie_id, media_id, kind),
        )
        row = cur.fetchone()
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
    provenance = proposal.get("provenance") or []
    metadata_updates = dict(metadata_updates)
    if metadata_updates:
        if has_locked_primary_media(conn, movie_id=movie_uuid, kind="poster"):
            for key in ("poster_url", "posterUrl", "poster"):
                metadata_updates.pop(key, None)
        if has_locked_primary_media(conn, movie_id=movie_uuid, kind="backdrop"):
            for key in ("backdrop_url", "backdropUrl", "backdrop"):
                metadata_updates.pop(key, None)
    changed = bool(movie_updates or metadata_updates or technical_updates or media_updates or identifiers)

    if not changed:
        return {"changed": False, "revision": 0, "applied": {}}

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
        },
    )
    return {
        "changed": True,
        "revision": revision,
        "applied": {
            "movieUpdates": movie_updates,
            "metadataUpdates": metadata_updates,
            "technicalUpdates": technical_updates,
            "mediaUpdates": applied_media_updates,
            "identifiers": identifiers,
            "provenance": len(provenance),
        },
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
