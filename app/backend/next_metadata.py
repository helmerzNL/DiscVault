"""Metadata source execution and merge policy for DiscVault Next."""

from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import unicodedata
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
    from psycopg import OperationalError as _PsycopgOperationalError
    from psycopg import ProgrammingError as _PsycopgProgrammingError
except ModuleNotFoundError:  # pragma: no cover - allows policy tests without psycopg
    class _PsycopgProgrammingError(Exception):  # type: ignore[no-redef]
        pass

    class _PsycopgOperationalError(Exception):  # type: ignore[no-redef]
        pass

try:
    from .dedup_identity import MOVIEVAULT_IDENTIFIER_PROVIDERS
    from .dedup_identity import extract_identity_identifiers
    from .dedup_identity import MEDIA_TYPE_SHOW
    from .dedup_identity import normalize_media_type
    from .next_import import clean_text
    from .next_genres import genre_keys_from_tmdb_ids
    from .next_genres import normalize_genre_keys
    from .next_common import table_exists as _shared_table_exists
    from .next_movievault_connection import MOVIEVAULT_PLUGIN_ID
    from .next_movievault_connection import MOVIEVAULT_PLUGIN_IDS
    from .next_movievault_connection import is_movievault_plugin
    from .next_movievault_connection import movievault_plugin_context
    from .next_movievault_v2 import MOVIEVAULT_V2_PLUGIN_ID
    from .next_movievault_v2 import movievault_v2_plugin_context
    from .next_packaging import split_legacy_packaging
    from .next_plugin_runtime import discovered_plugin
    from .next_plugin_runtime import run_plugin_entrypoint
    from .next_plugin_runtime import sync_plugin_registry
    from .next_plugin_runtime import plugin_config_payload as resolved_plugin_config_payload
    from .next_series import SEASON_TEXT_LIMITS, SERIES_IDENTIFIER_TYPE, SERIES_TEXT_LIMITS
    from .next_series import ensure_seasons, ensure_series, provider_series_payload
    from .next_series import series_id_for_identifier
except ImportError:  # pragma: no cover - supports direct module execution
    from dedup_identity import MOVIEVAULT_IDENTIFIER_PROVIDERS
    from dedup_identity import extract_identity_identifiers
    from dedup_identity import MEDIA_TYPE_SHOW
    from dedup_identity import normalize_media_type
    from next_import import clean_text
    from next_genres import genre_keys_from_tmdb_ids
    from next_genres import normalize_genre_keys
    from next_common import table_exists as _shared_table_exists
    from next_movievault_connection import MOVIEVAULT_PLUGIN_ID
    from next_movievault_connection import MOVIEVAULT_PLUGIN_IDS
    from next_movievault_connection import is_movievault_plugin
    from next_movievault_connection import movievault_plugin_context
    from next_movievault_v2 import MOVIEVAULT_V2_PLUGIN_ID
    from next_movievault_v2 import movievault_v2_plugin_context
    from next_packaging import split_legacy_packaging
    from next_plugin_runtime import discovered_plugin
    from next_plugin_runtime import run_plugin_entrypoint
    from next_plugin_runtime import sync_plugin_registry
    from next_plugin_runtime import plugin_config_payload as resolved_plugin_config_payload
    from next_series import SEASON_TEXT_LIMITS, SERIES_IDENTIFIER_TYPE, SERIES_TEXT_LIMITS
    from next_series import ensure_seasons, ensure_series, provider_series_payload
    from next_series import series_id_for_identifier


logger = logging.getLogger(__name__)

METADATA_REFRESH_JOB_TYPE = "metadata.refresh_movie"
PERSON_METADATA_REFRESH_JOB_TYPE = "metadata.refresh_person"
# A series is enriched on its own schedule rather than as part of the disc that
# revealed it. One series is shared by every edition a collector owns, so
# attaching the work to a disc would repeat it once per pressing and make which
# disc happened to sync first decide when the series got described.
SERIES_METADATA_REFRESH_JOB_TYPE = "metadata.refresh_series"

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
    # Provider-writable, but only because a human states it.
    #
    # This began as local-only: nothing could tell a series from a film, so any
    # provider value would have been a guess wearing a fact's clothes. That
    # changed when MovieVault gained content.films.work_type -- an operator sets
    # it in the MovieVault admin, and a stated value is evidence rather than a
    # guess. Automatic detection still does not exist on either side, so nothing
    # infers this field; it is only ever carried.
    #
    # The field is structurally load-bearing (it vetoes the identity ladder and
    # gates whether a row may carry a series link), so the ordinary field-lock
    # machinery matters more here than elsewhere: a user who sets or locks the
    # type keeps it. See the merge policy's own precedence rules.
    "media_type",
}

METADATA_LOCAL_ONLY_FIELDS = {
    "barcode",
    "public_id",
    "owner_id",
    "purchase_date",
    "purchase_price",
    "estimated_value",
    # Local-only for the same reason as the amount: no metadata provider has
    # any business saying what currency the user values their copy in.
    "estimated_value_currency",
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
    "carrier_type",
    "outer_packaging",
    "finishes",
    "steelbook_format",
    "screen_ratios",
    "audio_tracks",
    "subtitles",
    "regions",
    "content_ratings",
    "video_resolution",
    "video_codecs",
}

# Technical fields a box-set member may inherit from its parent when neither
# side declares a media format. Derived rather than hand-listed: the literal set
# this replaced had silently drifted, omitting `hdr` and `screen_ratios` while
# the enum-shaped fields were added around them.
BOX_SET_INHERITABLE_TECHNICAL_FIELDS = set(METADATA_TECHNICAL_FIELDS)

MOVIE_METADATA_LOCKS_KEY = "field_locks"

# `genre` is intentionally absent: genres are read-only, sourced only from
# TMDB, and stored as relational movie_genres associations rather than a
# manually editable metadata field, so they cannot be locked.
MOVIE_LOCKABLE_FIELDS = {
    "title",
    "sort_title",
    "original_title",
    # Lockable since sync-contract 1.6. It was the one user-editable field the
    # PWA offered with no lock, so a metadata refresh could overwrite a
    # hand-typed release title and nothing stood in the way - while both mobile
    # clients already had a `releaseTitle` lock key.
    "release_title",
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
    # The merge policy already refuses to demote a series to a film, so the
    # lock's job here is the other direction: it is the one way to say "this
    # stays a film" to a provider that keeps stating tv. Both the field-set
    # comment on METADATA_MAIN_FIELDS and apply_movie_series_link's type gate
    # promise exactly that, and neither could be kept while the name was
    # missing from this set — a media_type lock was silently normalised away.
    "media_type",
    "director",
    "studios",
    "distributor",
    "hdr",
    "packaging",
    "carrier_type",
    "outer_packaging",
    "finishes",
    "steelbook_format",
    "screen_ratios",
    "audio_tracks",
    "subtitles",
    "content_ratings",
    "regions",
    "video_resolution",
    "video_codecs",
    "poster",
    "backdrop",
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
    "release_title": ("releaseTitle", "release_title"),
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
    "media_type": ("mediaType", "media_type", "workType"),
    "director": ("director",),
    "studios": ("studios", "studio"),
    "distributor": ("distributor",),
    "hdr": ("hdr", "hdrFormats"),
    "packaging": ("packaging",),
    "carrier_type": ("carrierType", "carrier_type"),
    "outer_packaging": ("outerPackaging", "outer_packaging"),
    "finishes": ("finishes",),
    "steelbook_format": ("steelbookFormat", "steelbook_format"),
    "screen_ratios": ("screenRatios", "screen_ratios", "aspectRatios"),
    "audio_tracks": ("audioTracks", "audio_tracks"),
    "subtitles": ("subtitles", "subtitleLanguages"),
    "content_ratings": ("contentRatings", "content_ratings"),
    "regions": ("regions", "discRegions"),
    "video_resolution": ("videoResolution", "video_resolution"),
    "video_codecs": ("videoCodecs", "video_codecs"),
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
    "packaging",
    "outer_packaging",
    "finishes",
    # Lists since migration 055 - MovieVault publishes both as arrays and a disc
    # genuinely carries more than one of each.
    "hdr",
    "screen_ratios",
    "video_codecs",
}

METADATA_RELEASE_FIELDS = {
    "format",
    "edition",
    "edition_type",
    "country",
    "language",
    *METADATA_TECHNICAL_FIELDS,
}

TMDB_PLUGIN_ID = "tmdb"
TMDB_API_KEY_URL = "https://www.themoviedb.org/settings/api"
LEGACY_GENRE_FIELDS = {"genre", "genres"}

METADATA_IDENTIFICATION_FIELDS = {
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
}

METADATA_ENRICHMENT_FIELDS = {
    "overview",
    "plot",
    "description",
    "runtime",
    "runtime_minutes",
    "rating",
    "audience_rating",
    "director",
    "directors",
    "actor",
    "actors",
    "cast",
    "crew",
    "producer",
    "producers",
    "studio",
    "studios",
    "poster",
    "poster_url",
    "posters",
    "backdrop",
    "backdrop_url",
    "backdrop_urls",
    "trailer_url",
    "videos",
}

# Artwork an identity source (MovieVault) is allowed to own. A physical release
# has its own front cover, which the identity source is the authority on -- the
# enrichment provider only knows the film's generic artwork. These fields stay
# in METADATA_ENRICHMENT_FIELDS so every other plugin is still blocked; only
# MovieVault identity sources may supply them. Trailers/videos remain
# enrichment-owned.
METADATA_IDENTITY_ARTWORK_FIELDS = {
    "poster",
    "poster_url",
    "posters",
    "backdrop",
    "backdrop_url",
    "backdrop_urls",
}

METADATA_IDENTIFICATION_CANDIDATE_FIELDS = {
    "id",
    "provider",
    "providerId",
    "providerLabel",
    "pluginId",
    "source",
    "sourceLabel",
    "sourceRef",
    "sourceUrl",
    "source_url",
    "detailUrl",
    "detail_url",
    "url",
    "barcode",
    "externalBarcode",
    "external_barcode",
    "ean",
    "upc",
    "title",
    "name",
    "originalTitle",
    "original_title",
    "releaseTitle",
    "release_title",
    "year",
    "releaseYear",
    "release_year",
    "releaseDate",
    "release_date",
    "format",
    "mediaFormat",
    "media_format",
    "edition",
    "editionType",
    "edition_type",
    "country",
    "language",
    "tmdbId",
    "tmdb_id",
    "imdbId",
    "imdb_id",
    # Artwork shown on the candidate card while the user picks a match. The
    # poster stays display-only: `poster_url` remains in
    # METADATA_ENRICHMENT_FIELDS, so an identity source still cannot write
    # artwork onto the movie (TMDB keeps enrichment ownership). Without these
    # keys _identification_candidate() drops the cover a scan already resolved
    # and the PWA falls back to a letter placeholder.
    "posterUrl",
    "poster_url",
    "poster",
    "coverUrl",
    "cover_url",
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
    # Recognised so a proposal naming the field is explicitly rejected as
    # local-only, rather than dropped as an unknown key.
    "mediaType": "media_type",
    "media_type": "media_type",
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
    "subtitleLanguages": "subtitles",
    "subtitles": "subtitles",
    "screenRatios": "screen_ratios",
    "screen_ratios": "screen_ratios",
    # distribution-4 spellings. Without these, canonicalize_plugin_result's
    # fall-through drops the raw camelCase key into the metadata blob under its
    # literal name - silent, invisible, and permanent.
    "hdrFormats": "hdr",
    "hdr_formats": "hdr",
    "aspectRatios": "screen_ratios",
    "aspect_ratios": "screen_ratios",
    "discRegions": "regions",
    "disc_regions": "regions",
    "regions": "regions",
    "videoResolution": "video_resolution",
    "video_resolution": "video_resolution",
    "videoCodecs": "video_codecs",
    "video_codecs": "video_codecs",
}


def table_exists(conn, table_name: str) -> bool:
    """Delegates to the shared memoised implementation.

    A metadata refresh asks this about the same dozen tables on every credit it
    walks, so it benefits most from the connection's memo -- and keeping a
    private copy meant it ran beside that memo rather than filling it.
    """

    return _shared_table_exists(conn, table_name)


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


# A 4K UHD + Blu-ray combo pack physically ships both discs, so release data for
# either leg legitimately describes it. The collection filter already treats the
# combo as matching both members; keeping the merge policy in step means the
# format shown to the user and the format used to accept release data agree.
# Deliberately one-directional: a combo accepts single-disc release data, but a
# Blu-ray-only movie must not inherit a combo's specs, which also cover its 4K disc.
FORMAT_COMBO_MEMBERS = {"4K UHD + Blu-ray": frozenset({"4K UHD", "Blu-ray"})}


def normalize_media_format(value: Any) -> str:
    """Normalize a media format onto DiscVault's canonical vocabulary.

    Mirrors the seven options the collection UI offers (`MOVIE_FORMAT_OPTIONS`
    in `next_views_ui`) and the vocabulary `physical_media_format_key` already
    recognises. Previously only 4K UHD, Blu-ray and DVD were understood and every
    other value normalized to "", which `field_format_safe` reads as "no format"
    and which blocks every technical field: an HD DVD, LaserDisc or VCD/SVCD
    could never receive audio tracks, subtitles or video facts from a release
    source. A genuinely unrecognised value still returns "" and stays blocked --
    an unknown format must not be guessed into matching a release it does not
    describe.
    """
    text = str(value or "").strip().lower()
    if not text:
        return ""
    has_4k = bool(re.search(r"4k|uhd|ultra\s*hd", text))
    has_bluray = bool(re.search(r"blu[-\s]?ray|(?:^|[^a-z])bd(?:[^a-z]|$)", text))
    if has_4k and has_bluray and re.search(r"[+/&]", text):
        return "4K UHD + Blu-ray"
    if has_4k:
        return "4K UHD"
    if has_bluray:
        return "Blu-ray"
    # HD DVD is matched ahead of DVD: it carries the literal "dvd" token, so the
    # plain-DVD rule would otherwise claim it and let DVD release data through.
    if re.search(r"hd[-\s]?dvd", text):
        return "HD DVD"
    if re.search(r"laser[-\s]?disc", text):
        return "LaserDisc"
    if re.search(r"\bs?vcd\b", text):
        return "VCD/SVCD"
    if re.search(r"\bdvd\b", text):
        return "DVD"
    return ""


def media_formats_compatible(target: str, source: str) -> bool:
    """True when release data for *source* may be applied to a *target* format."""
    return target == source or source in FORMAT_COMBO_MEMBERS.get(target, ())


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


def plugin_config_from_db(conn, plugin_id: str) -> dict[str, Any]:
    if not table_exists(conn, "plugin_settings"):
        return resolved_plugin_config_payload({}, {}, {})
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.settings_schema, s.settings, s.secrets_ref
            FROM plugins AS p
            LEFT JOIN plugin_settings AS s ON s.plugin_id = p.id
            WHERE p.id=%s
            """,
            (plugin_id,),
        )
        row = cur.fetchone()
    return resolved_plugin_config_payload(
        row.get("settings_schema") if row else {},
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


def is_movievault_identity_source(plugin_id: str) -> bool:
    return plugin_id == MOVIEVAULT_V2_PLUGIN_ID or is_movievault_plugin(plugin_id)


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
        "distributionContractRange": manifest.get("distributionContractRange"),
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
    plugin_id = str(plugin.get("id") or "")
    context = movievault_plugin_context(
        conn,
        plugin_id,
        context,
        ensure_token=is_movievault_plugin(plugin_id),
        actor_id=actor.get("id") if actor else None,
    )
    return movievault_v2_plugin_context(conn, plugin_id, context)


def plugin_requires_config(plugin: dict[str, Any], config: dict[str, Any], entrypoint: str) -> bool:
    if entrypoint == "health_check":
        return False
    plugin_id = str(plugin.get("id") or "")
    if plugin_id == MOVIEVAULT_V2_PLUGIN_ID:
        # The v2 endpoint is enforced (not user-supplied), so v2 never requires
        # user configuration.
        return False
    if is_movievault_plugin(plugin_id):
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
            ORDER BY provider_id, identifier_type, identifier
            """,
            (movie_id,),
        )
        rows = cur.fetchall()
    selected = extract_identity_identifiers(rows)
    values = {}
    if selected["tmdb"] is not None:
        values["tmdb_id"] = selected["tmdb"]
        values["tmdbId"] = selected["tmdb"]
    if selected["movievault"] is not None:
        values["movievault_id"] = selected["movievault"]
        values["movieVaultId"] = selected["movievault"]
    for row in rows:
        provider = str(row["provider_id"])
        identifier_type = str(row["identifier_type"])
        if provider == "imdb" and identifier_type == "movie_id":
            values["imdb_id"] = row["identifier"]
            values["imdbId"] = row["identifier"]
    return values


def movie_genre_keys(conn, movie_id: UUID) -> list[str]:
    """Return a movie's canonical genre keys, sorted by catalog order."""
    if not table_exists(conn, "movie_genres"):
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT mg.genre_key
            FROM movie_genres mg
            LEFT JOIN genres g ON g.key = mg.genre_key
            WHERE mg.movie_id=%s
            ORDER BY g.sort_order NULLS LAST, mg.genre_key
            """,
            (movie_id,),
        )
        return [str(row["genre_key"]) for row in cur.fetchall()]


def replace_movie_genres(conn, movie_id: UUID, genre_keys: list[str]) -> list[str]:
    """Transactionally replace a movie's genre associations.

    TMDB is the sole owner of this relation: an empty list is a legitimate,
    authoritative "no genres" answer and clears any existing associations.
    """
    if not table_exists(conn, "movie_genres"):
        return []
    normalized = normalize_genre_keys(genre_keys)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM movie_genres WHERE movie_id=%s", (movie_id,))
        if normalized:
            cur.executemany(
                """
                INSERT INTO movie_genres (movie_id, genre_key)
                VALUES (%s, %s)
                ON CONFLICT (movie_id, genre_key) DO NOTHING
                """,
                [(movie_id, key) for key in normalized],
            )
    return normalized


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
            SELECT hdr, packaging, carrier_type, outer_packaging, finishes, steelbook_format,
                   screen_ratios, audio_tracks, subtitles, regions, content_ratings,
                   -- Both are correctable release fields (`RELEASE_FIELD_SOURCES`
                   -- names them) and neither was selected here, so the reader
                   -- answered "nobody said" for a value the table was holding.
                   video_resolution, video_codecs
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
    # Surface each person's tmdb/imdb identifier for receivers that accept
    # credits. Prefer the canonical
    # person_identifiers row and fall back to the id stashed on people.metadata
    # by ensure_metadata_person. person_identifiers may be absent on older
    # schemas, so the joins are added conditionally.
    has_identifiers = table_exists(conn, "person_identifiers")
    if has_identifiers:
        id_select = (
            "COALESCE(pi_tmdb.identifier, p.metadata->>'tmdb_id') AS tmdb_id, "
            "COALESCE(pi_imdb.identifier, p.metadata->>'imdb_id') AS imdb_id"
        )
        id_join = (
            "LEFT JOIN person_identifiers pi_tmdb "
            "ON pi_tmdb.person_id = p.id AND pi_tmdb.provider_id='tmdb' "
            "AND pi_tmdb.identifier_type='person_id' "
            "LEFT JOIN person_identifiers pi_imdb "
            "ON pi_imdb.person_id = p.id AND pi_imdb.provider_id='imdb' "
            "AND pi_imdb.identifier_type='person_id'"
        )
    else:
        id_select = "p.metadata->>'tmdb_id' AS tmdb_id, p.metadata->>'imdb_id' AS imdb_id"
        id_join = ""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT mc.credit_type, mc.character, mc.job, mc.sort_order, p.name,
                   {id_select}
            FROM movie_credits mc
            JOIN people p ON p.id = mc.person_id
            {id_join}
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
                -- media_type and series_id feed the merge's demotion rule:
                -- without them the stored type reads as empty and any stated
                -- type wins, which on a linked series disc is not a field
                -- write but a movies_series_requires_show violation.
                media_type,
                series_id,
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
    current_genres = movie_genre_keys(conn, movie_id)
    movie_row = dict(movie)
    movie_row["genres"] = current_genres
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
        "movieVaultId": (
            identifiers.get("movieVaultId")
            or metadata.get("movievault_id")
            or metadata.get("movieVaultId")
            or ""
        ),
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
        "movie": movie_row,
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
        "movieVaultId": clean_text(
            payload.get("movieVaultId")
            or payload.get("movievaultId")
            or payload.get("movievault_id")
            or payload.get("releaseId")
            or payload.get("release_id")
        ),
        "memberOfBoxSet": bool(payload.get("memberOfBoxSet") or payload.get("member_of_box_set")),
        "detectBoxSets": bool(payload.get("detectBoxSets") or payload.get("detect_box_sets") or payload.get("importBoxSets") or payload.get("import_box_sets")),
        "previewMode": bool(payload.get("previewMode") or payload.get("preview_mode")),
        "releaseVariants": bool(
            payload.get("releaseVariants")
            or payload.get("release_variants")
            or payload.get("listReleases")
            or payload.get("list_releases")
            or payload.get("editions")
        ),
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
        if tmdb_id or imdb_id:
            add("lookup_external_id", base_payload)
            # And the television namespace, for the same reason the title branch
            # below asks it. An id is issued *by* a namespace and a bare number
            # does not say which: TMDB's 1516 is a film and a television series
            # that have nothing to do with each other, so asking only the film
            # namespace tells somebody looking for the series that no such thing
            # exists. Both answers are offered and the person picks, exactly as
            # they do with a title.
            #
            # Only when the number is the query's own. `resolvedIdentity` marks
            # an id another source resolved -- a barcode that MovieVault matched
            # to a film -- and there the namespace is already settled. Asking
            # the television one with that number would offer an unrelated show
            # beside the right film, with nobody having asked about television
            # at all, which is §7b's failure mode wearing a candidate card.
            if not query.get("resolvedIdentity"):
                add("lookup_external_series_id", base_payload)
            return plan
        if title:
            # A title search should drive the preview results even when a barcode
            # is also supplied. Strip the barcode so the title lookup mirrors a
            # title-only search (the barcode is still persisted on import). This
            # keeps a scanned barcode's unrelated box-set from hiding the movie hits.
            title_payload = {
                key: value
                for key, value in base_payload.items()
                if key not in ("externalBarcode", "barcode")
            }
            if "search_title" in capabilities:
                add("search_title", title_payload)
            # And the television namespace, when the source has one. Without this
            # a title search could only ever return films -- `search_title` is
            # `/search/movie` at TMDB -- so a television disc added here arrived
            # as a MOVIE with no series and no identifier, which is precisely the
            # state that makes a series unenrichable.
            #
            # `add` is already a no-op for a capability the source does not
            # declare, so a movie-only plugin gains no empty step.
            #
            # This does not reopen 7b. That rule forbids a *source* identifying a
            # series by title on its own initiative; here a person typed the title
            # and picks from what comes back, exactly as they do in the identity
            # picker on the series page.
            add("search_series", title_payload)
            add("movie_details", title_payload)
            if query.get("detectBoxSets"):
                add("box_set_candidates", title_payload)
            return plan
        if external_barcode:
            add("search_barcode", {**base_payload, "barcode": external_barcode})
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


# The two entrypoints that answer "a person typed this title and will choose".
# Everything else a source can do is enrichment, and enrichment belongs to the
# movie that was picked rather than to the picking.
PREVIEW_TITLE_SEARCH_ENTRYPOINTS = ("search_title", "search_series")


def preview_title_search_plan(plugin: dict[str, Any], query: dict[str, Any]) -> list[dict[str, Any]]:
    """What a supporting source may contribute to a title search, and no more.

    A preview is not a refresh. The sources outside the identity set are not run
    in full here -- a preview would then pay for technical specs, prices and
    box-set detection on a query that has not identified anything yet -- but
    that exclusion was doing more than it meant to: it also withheld the one
    question they *can* answer before anything is identified, which is which
    titles the typed text might mean.

    Filtered from `plugin_execution_plan` rather than assembled here, so a
    source's capabilities decide this in exactly one place. A barcode-only
    preview plans no search step and the source is skipped altogether: reaching
    a source with a barcode it never claimed to read is how a preview grows a
    call that can only fail.
    """
    if not clean_text(query.get("title")):
        return []
    return [
        planned
        for planned in plugin_execution_plan(plugin, query)
        if planned["entrypoint"] in PREVIEW_TITLE_SEARCH_ENTRYPOINTS
    ]


def movievault_identification_plan(plugin: dict[str, Any], query: dict[str, Any]) -> list[dict[str, Any]]:
    """Plan only MovieVault identity and box-set calls, never enrichment calls."""
    capabilities = set(plugin.get("capabilities") or (plugin.get("manifest") or {}).get("capabilities") or [])
    plan: list[dict[str, Any]] = []
    external_barcode = clean_text(query.get("externalBarcode"))
    title = clean_text(query.get("title"))
    fallback = clean_text(query.get("fallbackTitle"))
    movievault_id = clean_text(query.get("movieVaultId"))
    base_payload = dict(query)

    def add(entrypoint: str, payload: dict[str, Any]) -> None:
        if entrypoint in capabilities and not any(item["entrypoint"] == entrypoint for item in plan):
            plan.append({"entrypoint": entrypoint, "payload": payload})

    if query.get("previewMode") and title:
        identity_payload = {
            key: value
            for key, value in base_payload.items()
            if key not in ("externalBarcode", "barcode")
        }
        add("search_title", identity_payload)
    else:
        identity_payload = base_payload
        if movievault_id:
            # `movieVaultId` is only sometimes a MovieVault release id. A movie
            # that reached DiscVault through an import carries a locally derived
            # UUIDv5 here (`next_import`), which no catalog row will ever match.
            # It is forwarded as `releaseId` so `movie_details` can resolve it
            # when it genuinely is one -- the entrypoint reads `releaseId`/`id`
            # and the query supplied neither, so the lookup could only miss.
            identity_payload = {**base_payload, "releaseId": movievault_id}
            add("movie_details", identity_payload)
        if external_barcode:
            # Planned alongside `movie_details` rather than instead of it. As an
            # `elif` the mere presence of an identifier removed the barcode
            # lookup, so every movie holding a non-release `movieVaultId` was cut
            # off from the catalog permanently: the refresh reported a clean
            # "no usable match" and no MovieVault edit could ever reach it.
            identity_payload = {**identity_payload, "barcode": external_barcode}
            add("search_barcode", identity_payload)
        if not plan and title:
            add("search_title", identity_payload)

    if (query.get("memberOfBoxSet") or query.get("detectBoxSets")) and (title or fallback or external_barcode):
        add("box_set_candidates", identity_payload)
    return plan


def _identification_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    sources = [candidate]
    for key in ("movie", "details", "release"):
        nested = candidate.get(key)
        if isinstance(nested, dict):
            sources.append(nested)
    sanitized: dict[str, Any] = {}
    for source in sources:
        for key in METADATA_IDENTIFICATION_CANDIDATE_FIELDS:
            if key not in sanitized and value_present(source.get(key)):
                sanitized[key] = source[key]
    identifiers = candidate.get("identifiers") if isinstance(candidate.get("identifiers"), dict) else {}
    if identifiers:
        sanitized["identifiers"] = {
            key: value
            for key, value in identifiers.items()
            if key in {"tmdb", "tmdbId", "tmdb_id", "imdb", "imdbId", "imdb_id"} and value_present(value)
        }
    return sanitized


def metadata_source_policy_result(result: dict[str, Any]) -> dict[str, Any]:
    """Enforce MovieVault identity ownership and TMDB enrichment ownership."""
    constrained = dict(result)
    plugin_id = str(result.get("pluginId") or "")
    movie_updates = dict(result.get("movieUpdates") or {})
    metadata_updates = dict(result.get("metadataUpdates") or {})

    if plugin_id == TMDB_PLUGIN_ID:
        constrained["movieUpdates"] = {
            field: value
            for field, value in movie_updates.items()
            if field not in METADATA_IDENTIFICATION_FIELDS
        }
        constrained["candidates"] = []
        constrained["raw"] = {}
        return constrained

    # MovieVault owns the physical release's own artwork; every other
    # enrichment answer (plot, cast, runtime, ratings, trailers) stays with the
    # enrichment provider. Other plugins remain fully blocked from artwork.
    artwork_owner = is_movievault_identity_source(plugin_id)

    def _keep(field: str) -> bool:
        if field in LEGACY_GENRE_FIELDS:
            return False
        if field not in METADATA_ENRICHMENT_FIELDS:
            return True
        return artwork_owner and field in METADATA_IDENTITY_ARTWORK_FIELDS

    constrained["movieUpdates"] = {
        field: value for field, value in movie_updates.items() if _keep(field)
    }
    constrained["metadataUpdates"] = {
        field: value for field, value in metadata_updates.items() if _keep(field)
    }
    # The poster/backdrop media updates are what actually persist artwork, so an
    # artwork owner keeps them; anything else it might carry is still dropped.
    media_updates = result.get("mediaUpdates")
    constrained["mediaUpdates"] = (
        {
            kind: value
            for kind, value in media_updates.items()
            if kind in {"poster", "backdrop"}
        }
        if artwork_owner and isinstance(media_updates, dict)
        else {}
    )
    constrained["credits"] = []
    constrained["localizations"] = []
    # Genres are TMDB-only; any other plugin's genre answer is dropped here
    # regardless of what canonicalize_plugin_result produced.
    constrained["genreKeys"] = None
    if is_movievault_identity_source(plugin_id):
        constrained["candidates"] = [
            sanitized
            for candidate in (result.get("candidates") or [])
            if isinstance(candidate, dict)
            for sanitized in [_identification_candidate(candidate)]
            if sanitized
        ]
        constrained["raw"] = {}
    return constrained


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


AUDIO_TRACK_KEYS = ("languageCode", "codec", "channels", "immersiveFormat")
AUDIO_TRACK_KEY_ALIASES = {
    "language_code": "languageCode",
    "immersive_format": "immersiveFormat",
}
SUBTITLE_KEYS = ("languageCode", "subtitleType")
SUBTITLE_KEY_ALIASES = {
    "language_code": "languageCode",
    "subtitle_type": "subtitleType",
}
TRACK_LANGUAGE_PATTERN = re.compile(r"^[a-z]{2,8}(-[a-z0-9]{1,8})*$")
DEFAULT_SUBTITLE_TYPE = "full"
MAX_TRACK_ENTRIES = 50
MAX_LEGACY_TRACK_TEXT = 200


def _track_language(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("track language must be a string")
    clean = value.strip().casefold()
    if not clean or len(clean) > 35 or not TRACK_LANGUAGE_PATTERN.fullmatch(clean):
        raise ValueError(f"{value!r} is not a valid BCP-47 subtag")
    return clean


def _track_optional_text(value: Any, *, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("track field must be a string or null")
    clean = value.strip()
    if not clean:
        return None
    if len(clean) > maximum:
        raise ValueError("track field is too long")
    return clean


def normalize_audio_track_entry(value: Any) -> dict[str, Any] | str | None:
    """Normalize one audio track, which may be an object or legacy free text.

    The column has held plain strings since long before MovieVault published
    structured tracks, e.g. "English (DTS-HD MA 5.1)". Those are kept verbatim
    rather than parsed: reconstructing a codec enum from prose needs a synonym
    table that will misfire on "Commentary with the director" or "Audio
    Description", and a migration has no undo. Conversion happens per track,
    only when a user edits that track, and is visible before they save.

    Shape is validated strictly; enum membership is not. The edit form is filled
    *from stored data*, so rejecting a codec outside the allow-list would make
    any movie carrying a forward-compat value permanently unsaveable through the
    UI - the opposite of what the lenient feed parser was for.
    """
    if isinstance(value, str):
        clean = " ".join(value.split())
        if not clean:
            return None
        if len(clean) > MAX_LEGACY_TRACK_TEXT:
            raise ValueError("audio track text is too long")
        return clean
    if not isinstance(value, dict):
        raise ValueError("audio track must be an object or a string")
    entry = {AUDIO_TRACK_KEY_ALIASES.get(key, key): item for key, item in value.items()}
    unknown = set(entry) - set(AUDIO_TRACK_KEYS)
    if unknown:
        raise ValueError(f"unknown audio track keys: {sorted(unknown)}")
    # A codec used to be mandatory. That held while the only writers were the
    # MovieVault feed (which always has one) and the PWA form (which silently
    # dropped a track without one). It stopped holding once the clients could
    # edit tracks: a title saved before the structured columns existed knows a
    # language and nothing else, and every hand-entered track passes through
    # that state on its way to being filled in. Rejecting it would 422 the whole
    # sync mutation over a track the user is halfway through typing.
    #
    # A language is still required — a track that names neither a language nor a
    # codec describes nothing.
    codec = entry.get("codec")
    if codec is not None and not isinstance(codec, str):
        raise ValueError("audio track codec must be a string or null")
    return {
        "languageCode": _track_language(entry.get("languageCode")),
        "codec": (codec or "").strip()[:64],
        "channels": _track_optional_text(entry.get("channels"), maximum=16),
        "immersiveFormat": _track_optional_text(entry.get("immersiveFormat"), maximum=64),
    }


def normalize_subtitle_entry(value: Any) -> dict[str, Any] | str | None:
    """Normalize one subtitle, which may be an object, a bare language code or
    legacy free text.

    A bare language code becomes the `full` variant - that is not a guess, it is
    what an unqualified subtitle listing on a disc means and the only thing the
    pre-variant shape could express. Anything that is not a valid subtag is kept
    as legacy text, the same union the audio column carries.
    """
    if isinstance(value, str):
        clean = " ".join(value.split())
        if not clean:
            return None
        if len(clean) > MAX_LEGACY_TRACK_TEXT:
            raise ValueError("subtitle text is too long")
        try:
            return {
                "languageCode": _track_language(clean),
                "subtitleType": DEFAULT_SUBTITLE_TYPE,
            }
        except ValueError:
            return clean
    if not isinstance(value, dict):
        raise ValueError("subtitle must be an object or a string")
    entry = {SUBTITLE_KEY_ALIASES.get(key, key): item for key, item in value.items()}
    unknown = set(entry) - set(SUBTITLE_KEYS)
    if unknown:
        raise ValueError(f"unknown subtitle keys: {sorted(unknown)}")
    subtitle_type = entry.get("subtitleType") or DEFAULT_SUBTITLE_TYPE
    if not isinstance(subtitle_type, str) or not subtitle_type.strip():
        raise ValueError("subtitle variant must be a string")
    return {
        "languageCode": _track_language(entry.get("languageCode")),
        "subtitleType": subtitle_type.strip()[:24],
    }


def _normalize_track_list(field: str, value: Any, normalizer) -> list[Any]:
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        # A plain string is split by the historical splitter first, so API and
        # mobile clients posting comma-separated text keep working - but each
        # part then goes through the same per-entry normalizer as a structured
        # payload, so "en, nl" and ["en", "nl"] cannot disagree about the shape
        # that ends up in the column.
        value = normalize_list_field(field, value)
    if len(value) > MAX_TRACK_ENTRIES:
        raise ValueError(f"at most {MAX_TRACK_ENTRIES} entries are allowed")
    result: list[Any] = []
    seen: set[str] = set()
    for item in value:
        entry = normalizer(item)
        if entry is None:
            continue
        key = entry if isinstance(entry, str) else json.dumps(entry, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return result


def normalize_audio_tracks(value: Any) -> list[Any]:
    return _normalize_track_list("audio_tracks", value, normalize_audio_track_entry)


def normalize_subtitles(value: Any) -> list[Any]:
    return _normalize_track_list("subtitles", value, normalize_subtitle_entry)


def normalize_list_field(field: str, value: Any) -> list[Any]:
    normalized = normalize_value(value)
    raw_items = normalized if isinstance(normalized, list) else [normalized]
    items: list[Any] = []
    separators = {";", "|", "\n", "\r"}
    if field in {
        "subtitles",
        "regions",
        "backdrop_urls",
        "packaging",
        "outer_packaging",
        "finishes",
        "hdr",
        "screen_ratios",
        "video_codecs",
    }:
        # Comma is a separator for these, which is also what turns a legacy
        # scalar preserved by migration 055 as ["1.85:1, 2.39:1"] back into two
        # elements the next time it passes through here.
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
    for key in ("members", "movies", "boxSetMovies", "box_set_movies", "items"):
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


def normalize_year_value(value: Any) -> str | None:
    """Coerce a release year to the ``text`` shape ``movies.year`` stores.

    Sources disagree on the type: MovieVault v2's index column is an integer
    (``migrations_next/035_movievault_v2_index.sql``) and its plugin passes the
    value through untouched, while TMDb and the import paths supply a string.
    ``movies.year`` is ``text`` (``migrations_next/002_core_domain.sql``), and
    binding an int against it aborts the whole UPDATE, so the year has to be
    normalised before it reaches the writer.

    A full date is accepted and reduced to its year, because a source that has
    only ``release_date`` to offer still answers "what year is this".
    """
    if isinstance(value, bool):  # bool is an int subclass; never a year
        return None
    if isinstance(value, int):
        return str(value) if 1000 <= value <= 9999 else None
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"\d{4}", text)
    return match.group(0) if match else None


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


# Flat, comma-separated crew/cast fields that some metadata sources (notably
# MovieVault) expose instead of structured `credits`/`cast`/`crew` arrays. Each
# entry maps a source field name to a (role, job) pair. `job` is empty for
# acting roles. Used only as a fallback when a result carries no structured
# credits, so structured providers (e.g. TMDb) are never double-counted.
FLAT_CREDIT_FIELD_ROLES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("director", "directors"), "crew", "Director"),
    (("writer", "writers"), "crew", "Writer"),
    (("screenplay",), "crew", "Screenplay"),
    (("producer", "producers"), "crew", "Producer"),
    (("composer", "composers", "music"), "crew", "Original Music Composer"),
    (
        ("cinematographer", "cinematographers", "director_of_photography", "directorOfPhotography"),
        "crew",
        "Director of Photography",
    ),
    (("actor", "actors", "cast", "starring", "stars"), "actor", ""),
)


def _flat_credit_names(value: Any) -> list[str]:
    """Split a flat person field into individual names.

    Accepts either a list of names or a delimiter-separated string (the way flat
    providers join names, e.g. ``"A, B, C"``). Splits on commas, semicolons and
    slashes, trims whitespace, and drops empties while preserving order.
    """
    raw_items: list[str] = []
    if isinstance(value, (list, tuple)):
        for item in value:
            name = clean_text(item.get("name")) if isinstance(item, dict) else clean_text(item)
            if name:
                raw_items.append(name)
        return raw_items
    text = clean_text(value)
    if not text:
        return []
    for part in re.split(r"[,;/]|\s&\s", text):
        name = clean_text(part)
        if name:
            raw_items.append(name)
    return raw_items


def flat_credit_entries(
    source: Any,
    *,
    plugin_id: str,
    source_label: str,
    source_ref: str,
) -> list[dict[str, Any]]:
    """Synthesize structured credit entries from flat person fields.

    A safety net for metadata sources that only publish comma-separated
    ``director``/``actor``/``producer`` strings rather than structured
    ``credits``. Without this, a refresh from such a source would create zero
    linked people. Entries carry no ``tmdbId`` (names only), so downstream
    matching falls back to name lookup in ``ensure_metadata_person``.
    """
    if not isinstance(source, dict):
        return []
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    sort_order = 0
    for field_names, role, job in FLAT_CREDIT_FIELD_ROLES:
        collected: list[str] = []
        for field in field_names:
            if field in source:
                collected.extend(_flat_credit_names(source.get(field)))
        for name in collected:
            key = (name.casefold(), role, job.casefold())
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                {
                    "role": role,
                    "name": name,
                    "character": "",
                    "job": job,
                    "tmdbId": "",
                    "sortOrder": sort_order,
                    "sourceProvider": plugin_id,
                    "sourceLabel": source_label,
                    "sourceRef": source_ref,
                }
            )
            sort_order += 1
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
    if not merged:
        # No structured credits from this source. Fall back to synthesizing
        # people from flat director/actor/producer strings so sources that only
        # publish comma-separated names still create linked people on refresh.
        # Only runs when structured credits are absent, so TMDb-style providers
        # are never duplicated.
        for source in (movie_source, result):
            merged.extend(
                flat_credit_entries(
                    source,
                    plugin_id=plugin_id,
                    source_label=source_label,
                    source_ref=source_ref,
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
    r'|steel\s*book|steelbook|collector'
    r'|digibook|mediabook|slipcover|slipcase|box\s*set|boxset|gift\s*set'
    r'|\bimport\b|region[\s-]*(?:free|locked|[abc]|[0-9])'
    r'|\bpal\b|\bntsc\b|remaster|uncut'
    r'|\bunrated\b'
    # Generic "<qualifier> Edition"/"<qualifier> Cut" (e.g. "X-treme Edition",
    # "Deluxe Edition", "Director's Cut", "Final Cut") so editions/cuts are
    # recognised even without a format token. These subsume the specific
    # edition/cut phrases (limited/special/.../director's/theatrical/final/...).
    r"|\b[\w'\u2019-]+\s+editions?\b"
    r"|\b[\w'\u2019-]+\s+cut\b"
    r'|\bocard\b|o[- ]card|amaray|digipack|digipak',
    re.I,
)


# Short connector/function words that usually signal a *continuation subtitle*
# in a trailing bracket group (e.g. "(and Where to Find Them)") rather than a
# standalone local/original title (e.g. "(Der Untergang)"). Used to keep the
# heuristic fallback from stripping genuine subtitles.
_SUBTITLE_CONNECTOR_WORDS = {
    "and", "or", "the", "a", "an", "of", "to", "in", "for", "with", "part",
}


def _norm_alt_title(value: str) -> str:
    """Casefold + diacritics-fold a title down to a comparable identity key."""
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text.casefold())
    return text.strip()


def _looks_like_local_title(inner: str) -> bool:
    """Heuristic: does a trailing bracket group look like a standalone local/
    original title (short, not a continuation subtitle) rather than a genuine
    subtitle we must keep? Used only as the fallback when no metadata match is
    available."""
    words = re.findall(r"[^\W_]+", inner or "", flags=re.UNICODE)
    if not words or len(words) > 4:
        return False
    if words[0].casefold() in _SUBTITLE_CONNECTOR_WORDS:
        return False
    return True


def _clean_scanned_title(raw_title: str, *, alt_titles: "list[str] | None" = None) -> str:
    """Return the bare film title from a UPC/EAN packaging title.

    Strips bracket/parenthetical groups and trailing segments that contain
    format/edition/region noise (e.g. "(4K Ultra HD + Blu-ray)", "[UK Import]",
    "- Steelbook"). Also strips a trailing local/original-title group (e.g.
    "Downfall Blu-ray (Der Untergang)" -> "Downfall") when it matches a known
    alternate/original title (``alt_titles``) or, as a fallback, when the title
    already carried packaging noise and the group looks like a standalone local
    title. Keeps a subtitle after a colon. Returns the original title when
    nothing recognisable remains."""
    title = (raw_title or "").strip()
    if not title:
        return ""

    noise_present = bool(_SCANNED_TITLE_NOISE_RE.search(title))
    alt_norm = {_norm_alt_title(t) for t in (alt_titles or []) if t}
    alt_norm.discard("")

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

    def _strip_trailing_meta_groups(text: str) -> str:
        # Remove trailing bracketed groups that are pure region/import metadata
        # (e.g. "(France)", "[UK Import]", "(Region B)"). The noise stripper above
        # leaves these behind because a bare country name carries no format
        # keyword, yet they strand a preceding bare format token (e.g. the
        # "4K Blu-ray" in "Inception 4K Blu-ray (4K Ultra HD + Blu-ray) (France)")
        # so it can no longer be recognised as a trailing token. Also strips a
        # trailing local/original-title group (metadata match, or heuristic
        # fallback when packaging noise was present).
        pattern = re.compile(r'\s*[\(\[\{]([^\(\)\[\]\{\}]*)[\)\]\}]\s*$')
        prev_inner = None
        while prev_inner != text:
            prev_inner = text
            match = pattern.search(text)
            if not match:
                break
            inner = match.group(1)
            inner_norm = _norm_alt_title(inner)
            is_year = bool(re.fullmatch(r"\d{4}", inner.strip()))
            is_local_title = (inner_norm and inner_norm in alt_norm) or (
                noise_present and not is_year and _looks_like_local_title(inner)
            )
            if _SCANNED_TITLE_NOISE_RE.search(inner) or _group_is_region_meta(inner) or is_local_title:
                text = text[: match.start()].rstrip()
        return text

    # Drop trailing " - <noise...>" / " | <noise...>" segments (distributor/format tails).
    tail_re = re.compile(r'\s[-|/]\s*[^-|/]*(?:' + _SCANNED_TITLE_NOISE_RE.pattern + r')[^-|/]*$', re.I)
    # Drop bare trailing format/region tokens, iteratively so space-separated
    # multi-token tails like "4K Blu-ray" or "Ultra HD Blu-ray 3D" collapse to
    # the film title instead of leaving a dangling "4K".
    bare_tail_re = re.compile(r'[\s,;:/+&-]+(?:' + _SCANNED_TITLE_NOISE_RE.pattern + r')\s*$', re.I)

    cleaned = title
    prev_outer = None
    while prev_outer != cleaned:
        prev_outer = cleaned
        cleaned = _strip_groups(cleaned, '(', ')')
        cleaned = _strip_groups(cleaned, '[', ']')
        cleaned = _strip_groups(cleaned, '{', '}')
        cleaned = _strip_trailing_meta_groups(cleaned)
        cleaned = tail_re.sub(' ', cleaned)
        prev_bare = None
        while prev_bare != cleaned:
            prev_bare = cleaned
            cleaned = bare_tail_re.sub('', cleaned).rstrip()
        cleaned = cleaned.strip()

    cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip(' -_/|,;:+&')
    return cleaned or title


# Edition/cut phrases that should be *promoted* into the structured `edition`
# field when they appear in a scanned packaging title. This is the extraction
# counterpart to the display-stripping done by `_clean_scanned_title`: the same
# edition/cut vocabulary, but captured rather than discarded. Kept deliberately
# narrow (only the edition/cut subset, no format/region noise) so a genuine
# film title is never mislabelled as an edition.
_EDITION_CUT_PHRASE = (
    r"[\w'\u2019-]+\s+editions?"
    r"|[\w'\u2019-]+\s+cut"
    r"|uncut|unrated|remaster(?:ed)?"
)
_EDITION_CUT_PHRASE_RE = re.compile(r"(?:" + _EDITION_CUT_PHRASE + r")", re.I)
# A trailing edition/cut segment must be introduced by a *structural* separator
# (dash/slash/pipe/comma/colon/…), never a bare space, so a whole-title phrase
# like "The Final Cut" or "Short Cuts" is not mistaken for a trailing edition.
_EDITION_CUT_TAIL_RE = re.compile(
    r"[,;:/+&|\u00b7-][\s,;:/+&|\u00b7-]*(" + _EDITION_CUT_PHRASE + r")\s*$",
    re.I,
)
_BRACKET_GROUP_RE = re.compile(r"[\(\[\{]([^\(\)\[\]\{\}]*)[\)\]\}]")


def _normalize_edition_phrase(phrase: str) -> str:
    """Collapse whitespace and normalise SHOUTING/lowercase packaging text to
    word-initial caps while preserving intra-word punctuation (e.g.
    "X-TREME EDITION"/"director's cut" -> "X-treme Edition"/"Director's Cut").
    Mixed-case input is returned untouched."""
    text = re.sub(r"\s+", " ", phrase or "").strip()
    if not text:
        return ""
    if text.isupper() or text.islower():
        return " ".join(w[:1].upper() + w[1:].lower() for w in text.split(" "))
    return text


def _extract_edition_from_title(raw_title: str) -> "str | None":
    """Extract an edition/cut label (e.g. "Director's Cut", "X-treme Edition",
    "Unrated") from a scanned packaging title, mirroring the vocabulary stripped
    from the display title by `_clean_scanned_title`. Only inspects bracketed
    groups or a trailing segment introduced by a structural separator, so a
    genuine film title such as "The Final Cut" or "Short Cuts" is never treated
    as an edition. Returns ``None`` when no edition/cut phrase is present in
    those zones."""
    text = (raw_title or "").strip()
    if not text:
        return None
    # 1) Prefer an explicit bracketed group, e.g. "(Director's Cut)".
    for match in _BRACKET_GROUP_RE.finditer(text):
        inner = _EDITION_CUT_PHRASE_RE.search(match.group(1))
        if inner:
            return _normalize_edition_phrase(inner.group(0)) or None
    # 2) Fall back to a trailing "- Unrated" / ", Director's Cut" style segment.
    tail = _EDITION_CUT_TAIL_RE.search(text)
    if tail:
        return _normalize_edition_phrase(tail.group(1)) or None
    return None


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
    "united states": "United States", "united kingdom": "United Kingdom",
    "great britain": "United Kingdom",
}


# Region words that, together with the country names/adjectives above, mark a
# bracketed group as pure region/import metadata (e.g. "(France)", "(UK Import)",
# "(Region B)") rather than part of the film title.
_REGION_META_WORDS = frozenset(
    {word for key in _IMPORT_COUNTRY_MAP for word in key.split()}
    | {"import", "region", "free", "locked", "a", "b", "c"}
)


def _group_is_region_meta(inner: str) -> bool:
    """Return True when every word in a bracketed group is region/import metadata.

    Used to strip trailing region tags such as "(France)", "[UK Import]" or
    "(United States)" that carry no format keyword and therefore slip past the
    format-noise stripper."""
    tokens = re.findall(r"[a-z.]+", (inner or "").lower())
    if not tokens:
        return False
    return all(token in _REGION_META_WORDS for token in tokens)


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

    # Bare country tag in brackets, e.g. Blu-ray.com's "... (France)" / "[Italy]".
    for match in re.finditer(r'[\(\[]\s*([A-Za-z.][A-Za-z. ]*?)\s*[\)\]]', text):
        mapped = _IMPORT_COUNTRY_MAP.get(match.group(1).strip().lower())
        if mapped:
            return mapped, ""

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
            # `movies.year` is a text column, but a source can legitimately hold
            # it as an integer — MovieVault v2's index declares `release_year
            # integer`, and the value reaches the merge as a Python int. Binding
            # that raw fails the whole UPDATE (no implicit int -> text cast), so
            # every field in the same refresh is lost, not just the year.
            # Normalised here, at the boundary every plugin's output converges
            # on, so a future source with the same shape is covered too.
            if key == "year":
                parsed_year = normalize_year_value(value)
                if parsed_year:
                    movie_updates[key] = parsed_year
                continue
            # Same failure shape as `year` above, but enforced by a CHECK rather
            # than a cast: `movies.media_type` accepts only MOVIE and SHOW, so a
            # source offering its own spelling -- MovieVault says `tv`, and any
            # plugin may say `Movie` -- fails the whole UPDATE and loses every
            # other field in the same refresh. Lenient in, exact out
            # (sync-contract.md §3b); a value the vocabulary does not know is
            # dropped rather than guessed at, because guessing here means
            # confidently mislabelling a series as a film.
            if key == "media_type":
                normalized_media_type = normalize_media_type(value)
                if normalized_media_type:
                    movie_updates[key] = normalized_media_type
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
            alt_titles = [
                movie_updates.get("original_title"),
                metadata_updates.get("original_title"),
                movie_source.get("original_title") if isinstance(movie_source, dict) else None,
                movie_source.get("originalTitle") if isinstance(movie_source, dict) else None,
                result.get("original_title"),
                result.get("originalTitle"),
            ]
            cleaned_title = _clean_scanned_title(raw_release_title, alt_titles=alt_titles)
            if cleaned_title and (
                not current_title
                or current_title == raw_release_title
                or _SCANNED_TITLE_NOISE_RE.search(current_title)
            ):
                movie_updates["title"] = cleaned_title
        extracted_edition = _extract_edition_from_title(raw_release_title)
        if extracted_edition and not value_present(movie_updates.get("edition")):
            # Promote a scanned "(Director's Cut)"/"- Unrated" style edition into
            # the structured field, but never clobber a richer plugin-provided
            # edition value.
            movie_updates["edition"] = extracted_edition
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

    explicit_candidates = result.get("items") or result.get("candidates") or []
    candidates = explicit_candidates
    looks_like_box_set = bool(
        box_set_proposal
        or box_set_proposals
        or result.get("isBoxSetCandidate")
        or result.get("is_box_set_candidate")
        or result.get("isBoxSet")
        or result.get("is_box_set")
    )
    synthesized_title = clean_text(movie_updates.get("title")) or clean_text(
        movie_updates.get("original_title")
    )
    result_status = str(result.get("status") or "ok").strip().lower()
    non_hit_statuses = {
        "miss",
        "not_found",
        "no_match",
        "needs_configuration",
        "skipped",
        "error",
        "failed",
        "empty",
    }
    if (
        not explicit_candidates
        and not looks_like_box_set
        and synthesized_title
        and result_status not in non_hit_statuses
    ):
        synthesized_candidate: dict[str, Any] = {
            "title": synthesized_title,
            "provider": plugin_id,
            "providerLabel": source_label,
            "synthesized": True,
        }
        candidate_year = clean_text(movie_updates.get("year"))
        if candidate_year:
            synthesized_candidate["year"] = candidate_year
        candidate_format = clean_text(source_format)
        if candidate_format:
            synthesized_candidate["format"] = candidate_format
        candidate_release_title = clean_text(movie_updates.get("release_title")) or raw_release_title
        if candidate_release_title:
            synthesized_candidate["releaseTitle"] = candidate_release_title
        candidate_overview = clean_text(
            movie_updates.get("overview")
            or metadata_updates.get("overview")
            or metadata_updates.get("plot")
        )
        if candidate_overview:
            synthesized_candidate["overview"] = candidate_overview
        poster_media = media_updates.get("poster") if isinstance(media_updates.get("poster"), dict) else {}
        candidate_poster = poster_media.get("sourceUrl") if poster_media else ""
        if candidate_poster:
            synthesized_candidate["posterUrl"] = candidate_poster
        if source_ref:
            synthesized_candidate["sourceUrl"] = source_ref
            synthesized_candidate["sourceRef"] = source_ref
        if identifiers:
            synthesized_candidate["identifiers"] = dict(identifiers)
        candidates = [synthesized_candidate]

    # Genres are TMDB-owned and relational: only the tmdb plugin may supply
    # them, and only when it actually returned a `genreIds` list (which is
    # always present -- possibly empty -- on a /movie/{id} hit). `None` means
    # "this result carries no genre answer" so the merge step leaves any
    # existing associations untouched; an empty list is an authoritative
    # "TMDB says this movie has no genres" that clears associations.
    genre_keys: list[str] | None = None
    if plugin_id == TMDB_PLUGIN_ID and "genreIds" in result:
        genre_keys = genre_keys_from_tmdb_ids(result.get("genreIds"))

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
        "genreKeys": genre_keys,
        "candidates": candidates,
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
        if source_context == "box_set_parent" and field in BOX_SET_INHERITABLE_TECHNICAL_FIELDS:
            return True, "box-set parent technical fallback without explicit format"
        return (field not in METADATA_TECHNICAL_FIELDS), "technical field needs same-format source"
    if target == source:
        return True, "same-format release data"
    if media_formats_compatible(target, source):
        return True, "combo pack accepts release data for either disc"
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
    if field in METADATA_TECHNICAL_FIELDS and release_priority:
        if format_reason in (
            "same-format release data",
            "combo pack accepts release data for either disc",
        ):
            return True, "same-format technical release refresh"
        if field == "content_ratings" and format_reason == "format-neutral certification field":
            return True, "format-neutral certification refresh"
    if overwrite_enabled and field not in METADATA_LOCAL_ONLY_FIELDS:
        return True, "preferred provider overwrite is enabled"
    if field in METADATA_MANUAL_PROTECTED_FIELDS:
        return False, "manual/display field already has a value"
    return False, "existing value retained"


# Entrypoints that reach a release through an identifier the disc itself
# carries -- its barcode, or the catalog id it is already bound to -- rather
# than through its name. Only these may correct a stored format: a title search
# can return a different pressing of the same film, and a format written from
# one would attach that pressing's technical profile to this disc.
METADATA_IDENTITY_ENTRYPOINTS = frozenset({"search_barcode", "movie_details"})


def should_apply_format(
    *,
    current_value: Any,
    incoming_value: Any,
    overwrite_enabled: bool,
    release_priority: bool,
    entrypoint: str = "",
) -> tuple[bool, str]:
    """Decide a proposed ``format`` against the stored one, asymmetrically.

    The generic rules cannot carry this field either, and for a sharper reason
    than ``media_type``: they judge a release field by comparing the movie's
    format with the source's, so applying them to ``format`` gates the field on
    its own stale value. A blank format could be filled and a *wrong* one could
    never be corrected -- and because the veto is evaluated per field against
    the same pair, one wrong format silently rejected every other release field
    from that source too. That is how a 4K UHD + Blu-ray pack recorded as plain
    ``4K_UHD`` came to receive nothing at all from MovieVault.

    That also broke the contribution round trip, which is the reason this is a
    correction rule and not merely a refinement one. DiscVault contributes
    field corrections *to* MovieVault; a correction that the catalog accepts and
    no other device can then receive is a one-way street, and the field most
    likely to be corrected is the one nothing could overwrite.

    So the field is decided here, before ``field_format_safe`` sees it:

    - **A source that found this disc by identity may correct it.** `entrypoint`
      is the discriminator: ``search_barcode`` and ``movie_details`` reach a
      release through the disc's own barcode or the catalog id it is already
      bound to, so they describe *this* pressing. ``search_title`` can return a
      different pressing of the same film, and a format taken from one would
      attach the wrong technical profile -- silently, because format is what
      gates technical data.
    - **Refinement is allowed from any release-backed source.** A combo pack
      names the disc a single-format entry is a leg of, so `4K UHD` ->
      `4K UHD + Blu-ray` describes the same product more precisely rather than
      contradicting it. That direction cannot attach specs for a disc the user
      does not own, so it does not need the identity gate.
    - **Narrowing is refused.** A source describing one leg of a pack must never
      demote the pack to it -- the same asymmetry ``FORMAT_COMBO_MEMBERS``
      states for technical data, and the same reason: the receiving entry has to
      stay the superset. An operator who has turned on preferred-provider
      overwrite still gets it, like every other field.

    Correcting the format is safe for identity: tiers 3 and 4 key on
    ``physical_media_format_key``, which has no combo branch, so both spellings
    map to ``ultra_hd_blu_ray`` and the ladder key does not move.

    The technical data itself arrives on the *next* refresh. ``target_format``
    is read once before the merge loop, so a format corrected mid-pass cannot
    retroactively unblock fields already judged against the old one -- and
    recomputing it mid-pass would make field iteration order significant across
    three buckets and several results.
    """
    incoming = normalize_media_format(incoming_value)
    current = normalize_media_format(current_value)
    if not value_present(incoming_value):
        return False, "incoming value is empty"
    if not incoming:
        # Unrecognised vocabulary. Storing it would blank a known format into
        # something `field_format_safe` reads as "no format", which blocks every
        # technical field.
        return False, "incoming format is not a known media format"
    if not current:
        return True, "current field is empty"
    if incoming == current:
        return False, "existing value retained"
    if incoming in FORMAT_COMBO_MEMBERS.get(current, ()):
        if overwrite_enabled:
            return True, "preferred provider overwrite is enabled"
        return False, "a refresh may not narrow a combo pack to one of its discs"
    if current in FORMAT_COMBO_MEMBERS.get(incoming, ()) and release_priority:
        return True, "a release names the fuller pack this disc belongs to"
    if release_priority and entrypoint in METADATA_IDENTITY_ENTRYPOINTS:
        return True, "the release this disc was matched to states another format"
    if overwrite_enabled:
        return True, "preferred provider overwrite is enabled"
    return False, "existing value retained"


def should_apply_media_type(*, current_value: Any, incoming_value: Any) -> tuple[bool, str]:
    """Decide a proposed ``media_type`` against the stored one, asymmetrically.

    The generic rules cannot carry this field. The column is NOT NULL DEFAULT
    'MOVIE', so "current field is empty" never truthfully describes it, and the
    default is indistinguishable from a stated MOVIE — which cuts both ways:

    - **Promotion (→ SHOW) is allowed.** Nothing infers this field, so a SHOW
      can only come from a human statement upstream, and it is these updates
      that let ``apply_movie_series_link`` link the disc afterwards. Blocking
      it on the at-rest default would make TV enrichment unreachable for every
      disc that predates the type.
    - **Demotion (SHOW → MOVIE) is refused.** A stored SHOW was itself stated
      at some point, and the disc may carry a series link that
      ``movies_series_requires_show`` ties to the type — so a demoting refresh
      is not a field write, it is an unlink, and re-pointing a disc is a
      decision rather than a refresh. "This is a film after all" stays a
      manual edit, where shedding the link is deliberate.
    """
    incoming = normalize_media_type(incoming_value)
    current = normalize_media_type(current_value)
    if incoming is None:
        return False, "incoming value is empty"
    if incoming == current:
        return False, "existing value retained"
    if incoming == MEDIA_TYPE_SHOW:
        return True, "a stated series type promotes the disc to a series"
    if current == MEDIA_TYPE_SHOW:
        return False, "a refresh may not demote a series to a film"
    return True, "current field is empty"


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
    if target == "genre":
        return current.get("genres") or []
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
    # The series a television disc belongs to, if any provider states one. First
    # stater wins, exactly like an identifier: two providers naming different
    # series for one disc is a disagreement no automatic rule can settle, and
    # silently preferring the later one would make the outcome depend on source
    # ordering rather than on anything a reader could reason about.
    series: dict[str, Any] | None = None
    credits: list[dict[str, Any]] = []
    localizations: list[dict[str, Any]] = []
    credit_keys: set[tuple[str, str, str, str]] = set()
    localization_keys: set[str] = set()
    provenance: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    genre_keys: list[str] | None = None
    genre_keys_selected = False
    field_decisions_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    selected_field_keys: set[tuple[str, str]] = set()
    working_movie = dict(current)
    working_metadata = dict(current.get("metadata") or {})
    working_technical = dict(technical_current)
    current_genre_keys = normalize_genre_keys(current.get("genres"))
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
            elif target == "movie" and field == "media_type":
                allowed, reason = should_apply_media_type(
                    current_value=current_value,
                    incoming_value=value,
                )
            elif target == "movie" and field == "format":
                # Dispatched out of the generic path for the reason in
                # `should_apply_format`: the generic rules would judge this
                # field against its own current value.
                allowed, reason = should_apply_format(
                    current_value=current_value,
                    incoming_value=value,
                    overwrite_enabled=overwrite_enabled,
                    release_priority=release_priority,
                    entrypoint=str(result.get("entrypoint") or ""),
                )
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
        result_genre_keys_value = result.get("genreKeys")
        if result_genre_keys_value is not None:
            result_genre_keys = normalize_genre_keys(result_genre_keys_value)
            accepted_genres = not genre_keys_selected
            genres_changed = sorted(result_genre_keys) != sorted(current_genre_keys)
            decision_value = result_genre_keys if genres_changed else current_genre_keys
            if accepted_genres:
                reason = "TMDB genre answer selected" if genres_changed else "TMDB genre answer matches existing genres"
            else:
                reason = "genres already selected from an earlier TMDB answer"
            add_decision_candidate(
                target="genre",
                field="genre",
                result=result,
                value=decision_value,
                accepted=accepted_genres,
                reason=reason,
            )
            if accepted_genres:
                genre_keys_selected = True
                if genres_changed:
                    genre_keys = list(result_genre_keys)
                    provenance.append(
                        {
                            "field": "genre",
                            "target": "genre",
                            "pluginId": result["pluginId"],
                            "entrypoint": result["entrypoint"],
                            "reason": reason,
                            "sourceRef": result.get("sourceRef") or "",
                            "sourceLabel": result.get("sourceLabel") or result["pluginId"],
                        }
                    )
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
        candidate_series = result.get("series")
        if isinstance(candidate_series, dict) and clean_text(candidate_series.get("tmdbTvId")):
            accepted_series = series is None
            add_decision_candidate(
                target="series",
                field="tmdbTvId",
                result=result,
                value=clean_text(candidate_series.get("tmdbTvId")) or "",
                accepted=accepted_series,
                reason=(
                    "series identity selected"
                    if accepted_series
                    else "series already selected from an earlier provider"
                ),
            )
            if accepted_series:
                series = {
                    **candidate_series,
                    "providerId": candidate_series.get("provider") or result.get("pluginId"),
                }
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
        # None rather than {} when nobody stated one: absent means "no provider
        # spoke", which must stay distinguishable from a stated answer. Nothing
        # downstream may read absence as a reason to unlink a series.
        "series": series,
        "credits": credits,
        "localizations": localizations,
        "genreKeys": genre_keys,
        "provenance": provenance,
        "skipped": skipped,
        "fieldDecisions": field_decisions,
    }


def count_update_fields(proposal: dict[str, Any]) -> int:
    field_count = sum(
        len(proposal.get(key) or {})
        for key in ("movieUpdates", "metadataUpdates", "technicalUpdates", "mediaUpdates", "identifiers")
    )
    field_count += len(proposal.get("credits") or []) + len(proposal.get("localizations") or [])
    if proposal.get("genreKeys") is not None:
        field_count += 1
    return field_count


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
        elif any(item.get("releaseCandidates") for item in plugin_executions):
            # Checked before the miss branches on purpose: a candidates answer
            # arrives with resultStatus "miss" (nothing was confirmed, so the
            # merge pipeline must not apply it), but reporting it as "no usable
            # match" hides the one outcome the user can actually settle.
            state = "release_candidates"
            reason = "provider identified the film; the pressing is a choice for the user"
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
        genre_written = (
            target == "genre"
            and field == "genre"
            and applied_payload.get("genres") is not None
            and decision.get("changed") is not False
        )
        if not dry_run and genre_written:
            applied_value = applied_payload.get("genres")
            written = True
            write_state = "written"
        elif not dry_run and isinstance(bucket, dict) and field in bucket:
            applied_value = bucket.get(field)
            written = True
            write_state = "written"
            if target == "media" and isinstance(applied_value, dict):
                if applied_value.get("lockedPrimary"):
                    write_state = "primary_locked_option_added"
                elif applied_value.get("option"):
                    write_state = "media_option_added"
        elif not dry_run and (not applied.get("changed") or decision.get("changed") is False):
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
        person_tmdb_id = str(credit.get("tmdb_id") or credit.get("tmdbId") or "").strip()
        if person_tmdb_id:
            entry["tmdbId"] = person_tmdb_id
        person_imdb_id = str(credit.get("imdb_id") or credit.get("imdbId") or "").strip()
        if person_imdb_id:
            entry["imdbId"] = person_imdb_id
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


# Entrypoints whose answer is a lookup against a provider's catalogue: the same
# question gives the same answer until that catalogue moves. Everything else --
# health checks, connection flows, pushes, imports, syncs, price checks -- either
# has side effects or is about right now, and must always reach the provider.
#
# An allowlist rather than a denylist on purpose. A new entrypoint is not cached
# until someone decides it may be, so the failure mode of forgetting is a missed
# optimisation instead of a stale or replayed side effect.
CACHEABLE_LOOKUP_ENTRYPOINTS = frozenset(
    {
        "search_title",
        "search_barcode",
        "lookup_external_id",
        "movie_details",
        "series_details",
        "search_series",
        "lookup_external_series_id",
        "season_episodes",
        "box_set_candidates",
        "people_for_movie",
        "person_details",
        "person_filmography",
        "person_awards",
        "images_for_movie",
        "videos_for_movie",
        "technical_specs",
    }
)
# How long a cached lookup stands. Provider catalogues move in days, not minutes,
# and an explicit refresh bypasses this entirely (see `force` below), so the
# ceiling only bounds how stale an *automatic* refresh may be. Deliberately not
# an environment variable: that would make this a deployment change, and there is
# no evidence yet that anyone needs to tune it.
METADATA_LOOKUP_CACHE_TTL_SECONDS = 6 * 60 * 60


def plugin_version_for_cache(plugin_id: str) -> str:
    """The plugin's own version, so an upgrade invalidates its cached answers.

    Without this, upgrading a source leaves answers produced by the previous
    mapping standing for the rest of their TTL -- the one case where "the same
    question" quietly stops meaning the same thing. Discovery is memoised on the
    plugin files, so reading this costs nothing.
    """
    try:
        plugin, _ = discovered_plugin(plugin_id)
    except Exception:
        return ""
    if not plugin:
        return ""
    return str(plugin.manifest.get("version") or "")


def metadata_lookup_cache_key(
    plugin_id: str, entrypoint: str, payload: dict[str, Any] | None, context: dict[str, Any] | None
) -> str:
    """Derive an opaque key for one provider question.

    Everything that can change the answer goes in, and nothing readable comes
    out. The payload can carry what a person typed and the context carries
    plugin configuration including secrets, so the key is a digest rather than
    the values -- the table stores provider catalogue data, never the question
    that produced it.

    Configuration is part of the key because it changes answers: a different
    API key, region or language setting is a different question, and treating
    them as the same one would serve one tenant's answer for another's.
    """
    config = (context or {}).get("config")
    material = json.dumps(
        {
            "pluginId": plugin_id,
            "entrypoint": entrypoint,
            "version": plugin_version_for_cache(plugin_id),
            "payload": payload or {},
            "config": config if isinstance(config, (dict, list)) else str(config or ""),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def read_metadata_lookup_cache(conn, plugin_id: str, cache_key: str) -> dict[str, Any] | None:
    try:
        if not table_exists(conn, "metadata_lookup_cache"):
            return None
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT payload
                FROM metadata_lookup_cache
                WHERE plugin_id=%s AND cache_key=%s AND expires_at > now()
                """,
                (plugin_id, cache_key),
            )
            row = cur.fetchone()
    except Exception:
        # A cache is never worth failing a lookup over, and the pipeline is
        # deliberately callable with a stand-in connection that has no cursor --
        # several callers do exactly that. Either way: no cache, carry on.
        logger.debug("metadata lookup cache unavailable for %s", plugin_id, exc_info=True)
        return None
    if not row:
        return None
    payload = row["payload"] if isinstance(row, dict) else row[0]
    return payload if isinstance(payload, dict) else None


def write_metadata_lookup_cache(
    conn, plugin_id: str, cache_key: str, execution: dict[str, Any]
) -> None:
    try:
        if not table_exists(conn, "metadata_lookup_cache"):
            return
        with conn.cursor() as cur:
            # Sweep on write rather than on a schedule: the expiry index makes it
            # cheap, and it keeps the table from growing without adding another
            # thing that has to be remembered to run.
            cur.execute("DELETE FROM metadata_lookup_cache WHERE expires_at < now()")
            cur.execute(
                """
                INSERT INTO metadata_lookup_cache (plugin_id, cache_key, payload, expires_at)
                VALUES (%s, %s, %s, now() + make_interval(secs => %s))
                ON CONFLICT (plugin_id, cache_key) DO UPDATE SET
                    payload=EXCLUDED.payload,
                    expires_at=EXCLUDED.expires_at,
                    created_at=now()
                """,
                (plugin_id, cache_key, Jsonb(execution), METADATA_LOOKUP_CACHE_TTL_SECONDS),
            )
    except Exception:
        # Two expected causes, neither of them a problem. The table references
        # `metadata_plugins`, so a source not mirrored there cannot be cached;
        # and the pipeline is callable with a stand-in connection that has no
        # cursor. Either way: no cache, and the lookup itself is unaffected.
        logger.debug("metadata lookup cache write skipped for %s", plugin_id, exc_info=True)


def release_read_transaction(conn) -> bool:
    """Close the transaction a lookup's own reads opened, before the network call.

    This has to happen *immediately* before the provider is reached, not at the
    top of whichever refresh function is running. `connect()` uses
    `autocommit=False`, so every read reopens a transaction -- and the code
    between "start refreshing" and "call the source" is nothing but reads: the
    entity, the plugin list, each plugin's configuration, the lookup cache. A
    commit placed any earlier is undone by the next `SELECT`, which is exactly
    what a first attempt at this got wrong: the boundary moved and the provider
    call was still measured as `INTRANS`.

    Returns whether the transaction was actually released, and never raises.
    Three callers legitimately cannot be released, and all three keep the
    behaviour they had rather than failing:

    * a connection-shaped **stand-in** with no `commit` -- the pipeline is
      deliberately callable with one;
    * a caller inside an explicit ``with conn.transaction():`` block, where
      psycopg forbids committing. That caller has chosen atomicity across the
      call, and silently breaking that choice would be worse than a long
      transaction;
    * a connection that is already **closed**, which is the best case of all:
      ``execute_plugin`` ends its ``with connect()`` block before reaching the
      plugin, so there is no transaction left to release and no connection held
      either. Learned by breaking it -- a release added there answered every
      request with "DiscVault is temporarily offline".

    Like the lookup cache, this is an optimisation and must never be the reason
    something fails.
    """

    try:
        conn.commit()
    except AttributeError:
        return False
    except _PsycopgProgrammingError:
        logger.debug(
            "provider call runs inside a caller's transaction block; "
            "the read transaction stays open for its duration"
        )
        return False
    except _PsycopgOperationalError:
        # Already closed, or gone. Nothing is held, which is the outcome this
        # function exists to produce.
        logger.debug("no transaction to release: the connection is closed")
        return False
    return True


def run_cached_plugin_entrypoint(
    conn,
    plugin_id: str,
    entrypoint: str,
    payload: dict[str, Any] | None,
    context: dict[str, Any] | None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Ask a provider, unless the same question was already answered recently.

    `metadata_lookup_cache` has existed since migration 003 -- with a cache key,
    a payload, an expiry and a unique constraint -- and nothing in the codebase
    ever read or wrote it. Every lookup went to the provider, every time, and on
    a movie refresh that is one network round trip per credit.

    `force` is what keeps "everything still refreshes" true. The refresh routes
    already default it to True and only the automatic refresh-on-open sends
    False, so pressing refresh reaches the provider exactly as before while
    routine background work stops re-asking.
    """
    if force or entrypoint not in CACHEABLE_LOOKUP_ENTRYPOINTS:
        release_read_transaction(conn)
        return run_plugin_entrypoint(plugin_id, entrypoint, payload, context)
    cache_key = metadata_lookup_cache_key(plugin_id, entrypoint, payload, context)
    cached = read_metadata_lookup_cache(conn, plugin_id, cache_key)
    if cached is not None:
        return cached
    # After the cache read, which is itself a read and so reopened the
    # transaction. A cache hit needs no release: it never touches the network.
    release_read_transaction(conn)
    execution = run_plugin_entrypoint(plugin_id, entrypoint, payload, context)
    # Only a clean answer is worth keeping. Caching a failure would turn one bad
    # minute at a provider into six bad hours.
    if isinstance(execution, dict) and execution.get("status") == "ok":
        write_metadata_lookup_cache(conn, plugin_id, cache_key, execution)
    return execution


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
        # Everything below this line in the loop body is network -- describe,
        # receive, summarise -- and nothing in it reads from `conn` again. The
        # caller commits before entering the push, but the two reads above
        # reopened the transaction, which is exactly the mistake this whole
        # change is about: a release is only worth anything at the last read
        # before the call.
        release_read_transaction(conn)
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
    if base.get("title") or base.get("tmdbId") or base.get("imdbId"):
        return {key: value for key, value in base.items() if value not in (None, "", [], {})}
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
    force: bool = False,
) -> dict[str, Any]:
    excluded = {str(item) for item in (exclude_plugin_ids or set()) if str(item)}
    discovered_plugins = [
        plugin
        for plugin in metadata_source_plugins(conn)
        if str(plugin.get("id") or "") not in excluded
        and metadata_source_plugin_allowed(plugin, query)
    ]
    identity_plugins = [
        plugin
        for plugin in discovered_plugins
        if is_movievault_identity_source(str(plugin.get("id") or ""))
    ]
    tmdb_plugins = [plugin for plugin in discovered_plugins if str(plugin.get("id") or "") == TMDB_PLUGIN_ID]
    supporting_plugins = [
        plugin
        for plugin in discovered_plugins
        if not is_movievault_identity_source(str(plugin.get("id") or ""))
        and str(plugin.get("id") or "") != TMDB_PLUGIN_ID
    ]
    plugins = [*identity_plugins, *supporting_plugins, *tmdb_plugins]
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

    def run_source_plan(
        plugin: dict[str, Any],
        plan: list[dict[str, Any]],
        config: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        """Run one source's planned steps and record what each one answered.

        Shared by the two passes below rather than written twice. The second
        pass exists only because a title search has to reach sources the first
        one deliberately skips; how a step is run, recorded and normalised is
        the same act in both, and a copy is where the two would drift.
        """
        for planned in plan:
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
            execution = run_cached_plugin_entrypoint(
                conn, plugin["id"], entrypoint, planned["payload"], context, force=force
            )
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
            normalized = metadata_source_policy_result(normalized)
            execution_item["resultStatus"] = normalized.get("status")
            execution_item["candidateCount"] = len(normalized.get("candidates") or [])
            execution_item["normalizedSourceFormat"] = normalized.get("normalizedSourceFormat") or ""
            # Which route resolved this (or why it didn't) - currently only movievault_v2
            # emits these, on a local-index hit vs. a live anonymous bucket-fallback
            # attempt. Read off the plugin's own raw result, not `normalized["raw"]`:
            # metadata_source_policy_result() deliberately scrubs `raw` to `{}` for
            # every MovieVault identity source (privacy hygiene for the rest of the
            # pipeline), which would silently drop these fields at exactly the plugin
            # that sets them. Forwarded generically rather than gated to one plugin
            # id, so any other plugin that starts reporting a match source gets the
            # same audit-trail treatment for free.
            raw_result = execution.get("result") or {}
            if isinstance(raw_result, dict):
                if "matchSource" in raw_result:
                    execution_item["matchSource"] = raw_result["matchSource"]
                if "bucketFallback" in raw_result:
                    execution_item["bucketFallback"] = raw_result["bucketFallback"]
                if "resolverFallback" in raw_result:
                    execution_item["resolverFallback"] = raw_result["resolverFallback"]
                if "verificationStatus" in raw_result:
                    execution_item["verificationStatus"] = raw_result["verificationStatus"]
                # The v2 resolver's `candidates` answer: the film was identified
                # but no single pressing was confirmed, so the plugin reports a
                # miss (nothing may be merged) while carrying the list. Forwarded
                # here for the same reason as `resolverFallback` above - dropping
                # it turns a resolvable choice into a bare "no usable match",
                # which the person holding the disc can never act on.
                if "releaseCandidates" in raw_result:
                    execution_item["releaseCandidates"] = raw_result["releaseCandidates"]
            if normalized.get("status") in {"miss", "not_found", "needs_configuration"}:
                continue
            normalized_results.append(normalized)

    initial_plugins = identity_plugins if query.get("previewMode") else [*identity_plugins, *supporting_plugins]
    for plugin in initial_plugins:
        config = plugin_config_from_db(conn, plugin["id"])
        context = plugin_execution_context(conn, plugin, config, actor)
        if enable_metadata_lookup_bridge and is_movievault_plugin(str(plugin.get("id") or "")):
            context["metadataLookup"] = metadata_lookup_bridge
        plan = (
            movievault_identification_plan(plugin, query)
            if is_movievault_identity_source(str(plugin.get("id") or ""))
            else plugin_execution_plan(plugin, query)
        )
        run_source_plan(plugin, plan, config, context)

    enrichment_payload = preview_enrichment_payload_from_results(query, normalized_results)
    has_box_set_preview = any(
        bool(item.get("boxSetProposal") or item.get("boxSetProposals"))
        for item in normalized_results
        if isinstance(item, dict)
    )
    tmdb_enrichment: dict[str, Any] = {
        "provider": TMDB_PLUGIN_ID,
        "configured": False,
        "enabled": False,
        "state": "needs_configuration",
        "nonBlocking": True,
        "requestKeyUrl": TMDB_API_KEY_URL,
        "settingsTab": "plugins",
        "pluginId": TMDB_PLUGIN_ID,
    }
    tmdb_plugin = tmdb_plugins[0] if tmdb_plugins else None
    if tmdb_plugin:
        config = plugin_config_from_db(conn, TMDB_PLUGIN_ID)
        context = plugin_execution_context(conn, tmdb_plugin, config, actor)
        execution_item = {
            "pluginId": TMDB_PLUGIN_ID,
            "entrypoint": "movie_details",
            "status": "skipped",
            "state": "tmdb_enrichment",
            "elapsedMs": None,
            "error": None,
            "configured": True,
            "resultStatus": None,
            "candidateCount": 0,
            "normalizedSourceFormat": "",
            "tmdbEnrichment": True,
        }
        executions.append(execution_item)
        if plugin_requires_config(tmdb_plugin, config, "movie_details"):
            execution_item["state"] = "needs_configuration"
            execution_item["configured"] = False
        else:
            tmdb_enrichment["configured"] = True
            if query.get("detectBoxSets") and has_box_set_preview:
                execution_item["state"] = "box_set_identification"
                tmdb_enrichment["state"] = "box_set_identification"
            elif not (
                enrichment_payload.get("title")
                or enrichment_payload.get("tmdbId")
                or enrichment_payload.get("imdbId")
            ):
                execution_item["state"] = "missing_identity"
                tmdb_enrichment["state"] = "missing_identity"
            else:
                tmdb_enrichment["enabled"] = True
                tmdb_plan = []
                if query.get("previewMode"):
                    # Preview: let TMDB run its own search so every match surfaces
                    # with a title taken straight from the TMDB/IMDB response. A
                    # title query uses the multi-result `search_title` (all matches
                    # become selectable candidates); a tmdbId/imdbId query uses
                    # `lookup_external_id`. This replaces the single exact-match
                    # `movie_details` call, which silently dropped partial-title
                    # searches and hid multi-result matches.
                    tmdb_query = dict(query)
                    tmdb_query["previewMode"] = True
                    # An id another source resolved is borrowed, not typed. It is
                    # what a barcode preview has to go on -- MovieVault names the
                    # film and TMDB then describes exactly that one -- but on a
                    # *title* search it silently replaced the question: the plan
                    # for an id is `lookup_external_id` and nothing else, so both
                    # the film search and the television search disappeared, and
                    # "The A-Team" returned the one film MovieVault happened to
                    # match and no series at all.
                    #
                    # The person typed a title; the title is what TMDB is asked.
                    # A borrowed id may still fill a query that has no title of
                    # its own, which is the barcode case and the only one it was
                    # ever meant for.
                    borrowed = () if clean_text(query.get("title")) else ("tmdbId", "imdbId")
                    for key in ("title", "fallbackTitle", "year", *borrowed):
                        value = enrichment_payload.get(key)
                        if value and not clean_text(tmdb_query.get(key)):
                            tmdb_query[key] = value
                            if key in ("tmdbId", "imdbId"):
                                # Says where the id came from, which decides
                                # whether the television namespace is asked as
                                # well: a number somebody typed is ambiguous, a
                                # number a source resolved is not.
                                tmdb_query["resolvedIdentity"] = True
                    tmdb_plan = [
                        planned
                        for planned in plugin_execution_plan(tmdb_plugin, tmdb_query)
                        if planned.get("entrypoint") != "box_set_candidates"
                    ]
                    # A title query plans both `search_title` and `movie_details`;
                    # the multi-result search already covers every match with a
                    # title, so drop the redundant single exact-match detail call to
                    # avoid duplicate candidate cards (full details fetched on import).
                    if any(planned["entrypoint"] == "search_title" for planned in tmdb_plan):
                        tmdb_plan = [planned for planned in tmdb_plan if planned["entrypoint"] != "movie_details"]
                if tmdb_plan:
                    preview_candidate_total = 0
                    appended = False
                    for planned in tmdb_plan:
                        entrypoint = planned["entrypoint"]
                        plan_execution = run_cached_plugin_entrypoint(
                            conn, TMDB_PLUGIN_ID, entrypoint, planned["payload"], context, force=force
                        )
                        plan_item = {
                            "pluginId": TMDB_PLUGIN_ID,
                            "entrypoint": entrypoint,
                            "status": plan_execution.get("status"),
                            "state": plan_execution.get("state") or "tmdb_enrichment",
                            "elapsedMs": plan_execution.get("elapsedMs"),
                            "error": plan_execution.get("error"),
                            "configured": True,
                            "resultStatus": None,
                            "candidateCount": 0,
                            "normalizedSourceFormat": "",
                            "tmdbEnrichment": True,
                        }
                        executions.append(plan_item)
                        if plan_execution.get("status") != "ok":
                            continue
                        canonical = canonicalize_plugin_result(
                            TMDB_PLUGIN_ID,
                            entrypoint,
                            plan_execution.get("result") or {},
                        )
                        # Preview surfaces TMDB's own search results as selectable
                        # candidates. The enrichment policy strips them (TMDB does
                        # not own identity while enriching an already-identified
                        # movie), but in preview these candidates ARE the movies the
                        # user picks from -- each carries its title straight from the
                        # TMDB/IMDB response -- so keep them for the frontend list.
                        preview_candidates = canonical.get("candidates") or []
                        normalized = metadata_source_policy_result(canonical)
                        if preview_candidates:
                            normalized["candidates"] = preview_candidates
                        plan_item["resultStatus"] = normalized.get("status")
                        plan_item["candidateCount"] = len(normalized.get("candidates") or [])
                        plan_item["normalizedSourceFormat"] = normalized.get("normalizedSourceFormat") or ""
                        if normalized.get("status") in {"miss", "not_found", "needs_configuration"}:
                            continue
                        normalized_results.append(normalized)
                        preview_candidate_total += plan_item["candidateCount"]
                        appended = True
                    execution_item["status"] = "ok"
                    execution_item["state"] = "enriched" if appended else "no_match"
                    execution_item["resultStatus"] = "hit" if appended else "miss"
                    execution_item["candidateCount"] = preview_candidate_total
                    tmdb_enrichment["state"] = "enriched" if appended else "no_match"
                else:
                    execution = run_cached_plugin_entrypoint(
                        conn, TMDB_PLUGIN_ID, "movie_details", enrichment_payload, context, force=force
                    )
                    execution_item.update(
                        {
                            "status": execution.get("status"),
                            "state": execution.get("state") or "tmdb_enrichment",
                            "elapsedMs": execution.get("elapsedMs"),
                            "error": execution.get("error"),
                        }
                    )
                    if execution.get("status") == "ok":
                        normalized = canonicalize_plugin_result(
                            TMDB_PLUGIN_ID,
                            "movie_details",
                            execution.get("result") or {},
                        )
                        normalized = metadata_source_policy_result(normalized)
                        execution_item["resultStatus"] = normalized.get("status")
                        execution_item["candidateCount"] = len(normalized.get("candidates") or [])
                        execution_item["normalizedSourceFormat"] = normalized.get("normalizedSourceFormat") or ""
                        if normalized.get("status") not in {"miss", "not_found", "needs_configuration"}:
                            normalized_results.append(normalized)
                            tmdb_enrichment["state"] = "enriched"
                        else:
                            tmdb_enrichment["state"] = "no_match"
                    else:
                        tmdb_enrichment["state"] = execution_item.get("state") or "error"

    # Every other source that can answer a typed title. Without this the Add
    # screen's search was TMDB and nothing else: the preview pass above runs only
    # the identity sources, the block above it is TMDB by name, and a source
    # outside both -- TheTVDB, which is the only place a great many series are
    # described at all -- was never asked, however high the user had ranked it.
    #
    # Which sources those are is decided by declared capability, not by id. The
    # hard-coded `tmdb` that used to sit in the series path is exactly the shape
    # this avoids: a source is included because it says it can search a title or
    # a series namespace, so installing one is enough and no future source is a
    # code change here.
    #
    # Run last on purpose. The enrichment payload above is derived from what the
    # identity sources answered, and appending to `normalized_results` before it
    # is computed would let a search result -- a guess a person has not chosen
    # yet -- decide what TMDB is asked about. Ordering candidates after TMDB's
    # also keeps the pre-selected first candidate what it has always been.
    if query.get("previewMode"):
        for plugin in supporting_plugins:
            plan = preview_title_search_plan(plugin, query)
            if not plan:
                continue
            config = plugin_config_from_db(conn, plugin["id"])
            context = plugin_execution_context(conn, plugin, config, actor)
            run_source_plan(plugin, plan, config, context)

    preview_enrichment = {
        "enabled": bool(tmdb_enrichment.get("enabled")),
        "payload": enrichment_payload,
        "plugins": [
            {
                "pluginId": TMDB_PLUGIN_ID,
                "state": tmdb_enrichment.get("state"),
                "configured": tmdb_enrichment.get("configured"),
            }
        ],
    }

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
    # The first execution that carried a candidate list wins; there is at most
    # one, because only the movievault_v2 barcode lookup emits it. Lifted to the
    # top level so a client does not have to dig through executions to learn
    # that "no usable match" actually means "pick a pressing".
    release_candidates = next(
        (item["releaseCandidates"] for item in executions if item.get("releaseCandidates")),
        None,
    )
    return {
        "query": query,
        "settings": {"preferredProviderOverwrite": overwrite_enabled},
        "sourceOrder": [plugin["id"] for plugin in plugins],
        "executions": executions,
        "sourceSummary": source_summary,
        "releaseCandidates": release_candidates,
        "results": normalized_results,
        "previewEnrichment": preview_enrichment,
        "enrichment": {"tmdb": tmdb_enrichment},
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


def preview_movie_metadata(
    conn,
    movie_id: UUID | str,
    actor: dict[str, Any] | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    movie_uuid = UUID(str(movie_id))
    context = movie_lookup_context(conn, movie_uuid)
    pipeline = run_metadata_source_pipeline(
        conn,
        query=context["query"],
        current=context["movie"],
        technical_current=context["technicalSpecs"],
        actor=actor,
        force=force,
    )
    return {"movie": context["movie"], "technicalSpecs": context["technicalSpecs"], **pipeline}


def lookup_metadata_sources(
    conn,
    payload: dict[str, Any],
    actor: dict[str, Any] | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    query = query_from_payload(payload)
    return run_metadata_source_pipeline(
        conn,
        query=query,
        current={"format": query.get("format") or "", "metadata": {}},
        technical_current={},
        actor=actor,
        force=force,
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


def record_movie_identifier_change(conn, movie_id: UUID) -> int:
    """Emit a dedicated ``movie_identifier`` upsert delta for one movie.

    Carries the movie's full identifier set (same row shape as the sync
    ``movieIdentifiers`` bootstrap array) so an offline client replaces its
    local identifiers — including the ``movievault_26`` link — in one change.
    No-op when the sync tables are absent.
    """
    if not table_exists(conn, "sync_state") or not table_exists(conn, "sync_changes"):
        return 0
    if not table_exists(conn, "movie_identifiers"):
        return 0
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT provider_id, identifier_type, identifier, created_at
            FROM movie_identifiers
            WHERE movie_id=%s
            ORDER BY provider_id, identifier_type, identifier
            """,
            (movie_id,),
        )
        identifiers = cur.fetchall()
    revision = next_revision(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sync_changes (revision, entity_type, entity_id, operation, payload)
            VALUES (%s, 'movie_identifier', %s, 'upsert', %s)
            """,
            (
                revision,
                str(movie_id),
                Jsonb(json_ready({"movieId": str(movie_id), "identifiers": identifiers})),
            ),
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


# Which table owns an entity that can carry artwork. Everything below differs by
# nothing but this name and the `entity_type` string, so an entity joins the media
# path by adding a row here rather than by getting a parallel set of functions that
# then drift apart.
#
# `series_season` has no page of its own -- a season is shown inside its series --
# but it owns a poster, and `entity_media.entity_type` is unconstrained text
# (migration 003), so it needed no migration to become storable. That is also why
# a test is the only thing standing behind the value being spelled correctly.
ENTITY_ARTWORK_TABLES = {
    "movie": "movies",
    "container": "containers",
    "series": "series",
    "series_season": "series_seasons",
}


def apply_primary_media_update(
    conn,
    *,
    kind: str,
    source_url: str,
    provider_id: str,
    entity_type: str = "movie",
    entity_id: UUID | None = None,
    movie_id: UUID | None = None,
) -> dict[str, Any] | None:
    """Make one image the entity's primary artwork of its kind.

    Refuses when the current primary was chosen by a person -- an upload, or an
    explicit "use this one". That refusal is the only protection a series has
    against a later refresh, because a series has no lock button; for a movie it
    sits alongside one.
    """
    entity_id = entity_id if entity_id is not None else movie_id
    if entity_id is None:
        raise ValueError("apply_primary_media_update needs an entity_id")
    owner_table = ENTITY_ARTWORK_TABLES.get(entity_type)
    if not owner_table:
        raise ValueError(f"unknown artwork entity type: {entity_type}")
    movie_id = entity_id
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
            WHERE em.entity_type=%s
              AND em.entity_id=%s
              AND em.deleted_at IS NULL
              AND em.hidden_at IS NULL
              AND em.role=%s
              AND ma.kind=%s
              AND em.is_primary=true
            ORDER BY em.sort_order, ma.created_at
            LIMIT 1
            """,
            (entity_type, movie_id, role, kind),
        )
        current = cur.fetchone()
        if current and str(current["id"] if isinstance(current, dict) else current[0]) == str(media_id):
            return None
        cur.execute(
            """
            SELECT deleted_at, hidden_at
            FROM entity_media
            WHERE entity_type=%s
              AND entity_id=%s
              AND media_id=%s
              AND role=%s
            """,
            (entity_type, movie_id, media_id, role),
        )
        existing_link = cur.fetchone()
        if existing_link and (
            (existing_link["deleted_at"] or existing_link["hidden_at"])
            if isinstance(existing_link, dict)
            else (existing_link[0] or existing_link[1])
        ):
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
                entity_type=entity_type,
                entity_id=movie_id,
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
            WHERE entity_type=%s
              AND entity_id=%s
              AND deleted_at IS NULL
              AND hidden_at IS NULL
              AND role=%s
              AND is_primary=true
            """,
            (entity_type, movie_id, role),
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
            VALUES (%s, %s, %s, %s, true, 0)
            ON CONFLICT (entity_type, entity_id, media_id, role) DO UPDATE SET
                is_primary=true,
                sort_order=0
            """,
            (entity_type, movie_id, media_id, role),
        )
        # The owner table is looked up rather than interpolated from the caller:
        # this is the one place a table name reaches the SQL text, and a map with
        # four known keys is what keeps it from ever being caller-controlled.
        cur.execute(f"UPDATE {owner_table} SET updated_at=now() WHERE id=%s", (movie_id,))
    return {
        "kind": kind,
        "mediaId": str(media_id),
        "sourceUrl": source_url,
        "providerId": provider_id,
    }


def movie_field_locks_from_db(conn, movie_id: UUID | str) -> set[str]:
    """Read the explicit field_locks set stored on a movie's metadata."""
    if not table_exists(conn, "movies"):
        return set()
    with conn.cursor() as cur:
        cur.execute("SELECT metadata FROM movies WHERE id=%s", (movie_id,))
        row = cur.fetchone()
    metadata = row.get("metadata") if isinstance(row, dict) and row else None
    return movie_locked_fields(metadata)


_ARTWORK_METADATA_URL_KEYS: dict[str, tuple[str, ...]] = {
    "poster": ("poster_url", "posterUrl", "poster"),
    "backdrop": ("backdrop_url", "backdropUrl", "backdrop"),
}


def filter_locked_artwork_updates(
    metadata_updates: dict[str, Any],
    media_updates: dict[str, Any],
    *,
    metadata_locked_kinds: set[str],
    media_locked_kinds: set[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Strip locked poster/backdrop entries from a metadata proposal.

    ``metadata_locked_kinds`` are kinds whose stored artwork URL must not be
    changed by a plugin/sync (explicit field lock OR a user-selected/locked
    primary media asset). ``media_locked_kinds`` are kinds whose primary media
    asset must not be added/replaced (explicit field lock only).

    Returns fresh copies of ``metadata_updates`` and ``media_updates`` with the
    locked entries removed; the inputs are left untouched.
    """
    metadata_updates = dict(metadata_updates)
    media_updates = dict(media_updates)
    for kind in metadata_locked_kinds:
        for key in _ARTWORK_METADATA_URL_KEYS.get(kind, ()):
            metadata_updates.pop(key, None)
    if media_locked_kinds:
        media_updates = {
            kind: value
            for kind, value in media_updates.items()
            if kind not in media_locked_kinds
        }
    return metadata_updates, media_updates


def filter_hidden_artwork_updates(
    conn,
    movie_id: UUID,
    metadata_updates: dict[str, Any],
    media_updates: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prevent a metadata refresh from selecting an explicitly hidden asset."""
    metadata_updates = dict(metadata_updates)
    media_updates = dict(media_updates)
    if not table_exists(conn, "entity_media") or not table_exists(conn, "media_assets"):
        return metadata_updates, media_updates
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ma.id, ma.kind, ma.source_url
            FROM entity_media em
            JOIN media_assets ma ON ma.id = em.media_id
            WHERE em.entity_type='movie'
              AND em.entity_id=%s
              AND em.deleted_at IS NULL
              AND em.hidden_at IS NOT NULL
              AND ma.kind IN ('poster', 'backdrop')
            """,
            (movie_id,),
        )
        hidden_rows = cur.fetchall()
    hidden_urls: dict[str, set[str]] = {"poster": set(), "backdrop": set()}
    for row in hidden_rows:
        kind = clean_text(row.get("kind") if isinstance(row, dict) else row[1]) or ""
        media_id = row.get("id") if isinstance(row, dict) else row[0]
        source_url = clean_text(row.get("source_url") if isinstance(row, dict) else row[2])
        if kind not in hidden_urls:
            continue
        if source_url:
            hidden_urls[kind].add(source_url)
        if media_id:
            hidden_urls[kind].add(f"/api/next/media/assets/{media_id}")
    for kind, urls in hidden_urls.items():
        if not urls:
            continue
        media_update = media_updates.get(kind)
        if isinstance(media_update, dict) and clean_text(media_update.get("sourceUrl")) in urls:
            media_updates.pop(kind, None)
        for key in _ARTWORK_METADATA_URL_KEYS[kind]:
            value = clean_text(metadata_updates.get(key))
            if value in urls:
                metadata_updates.pop(key, None)
    return metadata_updates, media_updates


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
              AND em.hidden_at IS NULL
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
    kind: str,
    source_url: str,
    provider_id: str,
    sort_order: int,
    entity_type: str = "movie",
    entity_id: UUID | None = None,
    movie_id: UUID | None = None,
) -> dict[str, Any] | None:
    """Offer an image as an alternative without making it the primary one.

    This is what fills the Posters and Backdrops tabs with more than one card.
    A link that is already primary, or that somebody deleted or hid, is left
    alone -- re-offering a rejected image on every refresh would make the delete
    button meaningless.
    """
    entity_id = entity_id if entity_id is not None else movie_id
    if entity_id is None:
        raise ValueError("link_media_option needs an entity_id")
    if entity_type not in ENTITY_ARTWORK_TABLES:
        raise ValueError(f"unknown artwork entity type: {entity_type}")
    movie_id = entity_id
    media_id = ensure_remote_media_asset(conn, kind=kind, source_url=source_url, provider_id=provider_id)
    if not media_id or not table_exists(conn, "entity_media"):
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT is_primary, deleted_at, hidden_at
            FROM entity_media
            WHERE entity_type=%s
              AND entity_id=%s
              AND media_id=%s
              AND role=%s
            """,
            (entity_type, movie_id, media_id, kind),
        )
        row = cur.fetchone()
        if row and (
            (row["deleted_at"] or row["hidden_at"])
            if isinstance(row, dict)
            else (row[1] or row[2])
        ):
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
            VALUES (%s, %s, %s, %s, false, %s)
            ON CONFLICT (entity_type, entity_id, media_id, role) DO UPDATE SET
                sort_order=LEAST(entity_media.sort_order, EXCLUDED.sort_order)
            """,
            (entity_type, movie_id, media_id, kind, sort_order),
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


def _prune_movie_seasons(cur, movie_uuid: UUID, keep_ids: list[UUID]) -> None:
    """Drop the season links a refresh no longer names, and only those.

    The twin of ``next_app.prune_movie_seasons``, duplicated rather than
    imported because ``next_app`` imports this module and the other direction
    would close the loop -- the same seam ``next_series`` documents for its own
    resolver. Episodes go first: they cite the season directly, so the season
    link's cascade does not reach them, and an episode of a season the release
    no longer covers would otherwise be left behind.
    """
    if keep_ids:
        cur.execute(
            "DELETE FROM movie_episodes WHERE movie_id = %s AND NOT (season_id = ANY(%s))",
            (movie_uuid, list(keep_ids)),
        )
        cur.execute(
            "DELETE FROM movie_seasons WHERE movie_id = %s AND NOT (season_id = ANY(%s))",
            (movie_uuid, list(keep_ids)),
        )
        return
    cur.execute("DELETE FROM movie_episodes WHERE movie_id = %s", (movie_uuid,))
    cur.execute("DELETE FROM movie_seasons WHERE movie_id = %s", (movie_uuid,))


def apply_movie_disc_enrichment(conn, movie_uuid: UUID, stated: Any) -> dict[str, Any] | None:
    """Fill a release's disc breakdown from the catalogue, but only if empty.

    The standing precedence rule for discs, and the one place it is enforced: a
    user's own breakdown of the copy on their shelf outranks the catalogue's.
    They are holding the box. So this writes only when the release has **no**
    discs at all -- not "no matching disc", not "fewer discs than the feed
    says". One disc entered by hand is an answer, and a refresh must not
    reorganise it.

    Silence is not disagreement either: a feed that states no discs leaves the
    stored ones alone, the same posture `apply_movie_series_link` takes and for
    the same reason. A catalogue with nothing to say must never be able to
    empty something somebody filled in.

    Returns what it did, or None when it did nothing -- which is the common
    case, because most releases either have discs already or the feed has none.
    """
    if not isinstance(stated, list) or not stated:
        return None
    if not table_exists(conn, "movie_discs"):
        return None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS existing FROM movie_discs WHERE movie_id = %s",
            (movie_uuid,),
        )
        row = cur.fetchone()
        if row and int(dict(row)["existing"]) > 0:
            return None
        cur.execute("SELECT media_type FROM movies WHERE id = %s", (movie_uuid,))
        movie = cur.fetchone()
        if not movie:
            return None
        media_type = str(dict(movie).get("media_type") or "MOVIE")
        # Deferred, because `next_app` imports this module and the other
        # direction only works at call time -- the same reason the season
        # pruner above is duplicated rather than imported. Duplicating the
        # writer is not an option: it diffs discs by id so their episode links
        # survive, and a second copy of that would drift.
        try:  # pragma: no cover - import shape depends on runtime layout
            from .next_app import apply_movie_discs
            from .next_discs import discs_payload
        except ImportError:  # pragma: no cover - supports gunicorn next_app:app
            from next_app import apply_movie_discs
            from next_discs import discs_payload
        # Through the same reader the edit form and the sync route use. The
        # writer is entitled to a normalised entry -- every key present, ids
        # stated as null -- and giving it a partial dict from the feed is how a
        # catalogue disc met a KeyError instead of the writer's own documented
        # "an entry without an id is a new disc".
        apply_movie_discs(
            cur, movie_uuid, discs_payload({"discs": stated}), media_type=media_type
        )
    return {"created": len(stated), "source": "catalogue"}


def apply_movie_series_link(conn, movie_uuid: UUID, stated: Any) -> dict[str, Any] | None:
    """Link a disc to the series a metadata source named, creating it if new.

    Returns what it did, or None when it did nothing -- which is the common case
    and never an error. Four things stop it, and each is a rule rather than a
    guard:

    * **The source said nothing.** Silence is not disagreement. A provider with
      no opinion must never be able to unlink a series somebody established.
    * **The disc is not a series.** ``movies_series_requires_show`` forbids a
      ``series_id`` on a MOVIE, so linking one would be writing a constraint
      violation. This is also what makes a field lock work without any special
      case here: a user who locked the type to MOVIE keeps it, the type never
      becomes SHOW, and the link is skipped as a consequence rather than by a
      second rule that could drift from the first.
    * **The disc already belongs to a different series.** A stated identity does
      not outrank a link that is already there; re-pointing a disc is a decision,
      not a refresh.
    * **The tables are absent.** Older databases predate migration 063.

    Season links are replaced rather than merged when the series matches, because
    "these are the seasons on this disc" is a whole statement -- the same reason
    ``apply_movie_series_assignment`` replaces them wholesale.
    """
    payload = provider_series_payload(stated)
    if payload is None:
        return None
    if not table_exists(conn, "series") or not table_exists(conn, "series_identifiers"):
        return None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT media_type, series_id FROM movies WHERE id = %s AND deleted_at IS NULL",
            (movie_uuid,),
        )
        row = cur.fetchone()
        if not row:
            return None
        media_type = row["media_type"] if isinstance(row, dict) else row[0]
        current_series = row["series_id"] if isinstance(row, dict) else row[1]
        if media_type != MEDIA_TYPE_SHOW:
            return None
        existing_series = series_id_for_identifier(
            cur,
            provider_id=payload["provider_id"],
            identifier=payload["identifier"],
            identifier_type=payload["identifier_type"],
        )
        series_uuid = ensure_series(
            cur,
            provider_id=payload["provider_id"],
            identifier=payload["identifier"],
            title=payload["title"],
            identifier_type=payload["identifier_type"],
        )
        if current_series is not None and current_series != series_uuid:
            return None
        season_ids = ensure_seasons(cur, series_uuid, payload["seasons"])
        cur.execute(
            "UPDATE movies SET series_id = %s WHERE id = %s",
            (series_uuid, movie_uuid),
        )
        # Ordered after the movies update, unlike the edit API's own path: there
        # the disc may be moving *between* series and the old rows have to go
        # first, while here the series is either unchanged or newly set, so the
        # composite key is satisfied the moment series_id lands.
        #
        # Only the seasons the feed no longer names are removed. A refresh runs
        # unattended and re-states the same list almost every time; deleting and
        # re-inserting it would, since migration 075 hung the per-disc season
        # links off these rows, quietly erase which disc holds what every time a
        # metadata job ran. Nothing in the refresh is about discs, so nothing in
        # it should change them.
        _prune_movie_seasons(cur, movie_uuid, season_ids)
        for index, season_id in enumerate(season_ids):
            cur.execute(
                """
                INSERT INTO movie_seasons (movie_id, season_id, series_id, sort_order)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (movie_id, season_id)
                DO UPDATE SET sort_order = EXCLUDED.sort_order
                """,
                (movie_uuid, season_id, series_uuid, index),
            )
    if existing_series is None:
        # Queued only on creation. The row is empty exactly once, and enqueuing
        # on every sync of every edition would repeat the same lookup per
        # pressing for a series that is already described. Outside the cursor
        # block above because the job is a side effect of the link having
        # happened, not part of writing it.
        enqueue_series_metadata_refresh(conn, series_uuid)
    return {
        "seriesId": str(series_uuid),
        "created": current_series is None,
        "seasonCount": len(season_ids),
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
    # The merge already refuses to demote a series to a film, but a proposal can
    # be built against a different context than the row it lands on — an import,
    # a box-set parent, an older preview — so the write path re-checks against
    # the row as stored. A demoting media_type is dropped rather than written:
    # on a linked disc it is not a field write but a movies_series_requires_show
    # violation, which would cost the whole refresh. Dropping only this key
    # keeps the rest.
    if "media_type" in movie_updates:
        movie_updates = dict(movie_updates)
        incoming_media_type = normalize_media_type(movie_updates.pop("media_type"))
        stored_media_type = None
        if incoming_media_type is not None:
            with conn.cursor() as cur:
                cur.execute("SELECT media_type FROM movies WHERE id=%s", (movie_uuid,))
                row = cur.fetchone()
            if row:
                stored_media_type = row["media_type"] if isinstance(row, dict) else row[0]
        if incoming_media_type is not None and not (
            stored_media_type == MEDIA_TYPE_SHOW and incoming_media_type != MEDIA_TYPE_SHOW
        ):
            movie_updates["media_type"] = incoming_media_type
    metadata_updates = proposal.get("metadataUpdates") or {}
    technical_updates = proposal.get("technicalUpdates") or {}
    media_updates = proposal.get("mediaUpdates") or {}
    identifiers = proposal.get("identifiers") or {}
    credit_updates = proposal.get("credits") or []
    localization_updates = proposal.get("localizations") or []
    provenance = proposal.get("provenance") or []
    # `None` means "no provider answered with genres this run" (leave
    # associations untouched); a list (possibly empty) is an authoritative
    # TMDB answer that should replace the current associations.
    genre_keys_proposal = proposal.get("genreKeys")
    genre_keys_provided = genre_keys_proposal is not None
    normalized_genre_keys = normalize_genre_keys(genre_keys_proposal) if genre_keys_provided else []
    current_genre_keys: list[str] = []
    genre_changed = False
    if genre_keys_provided:
        current_genre_keys = movie_genre_keys(conn, movie_uuid)
        genre_changed = sorted(normalized_genre_keys) != sorted(current_genre_keys)
    explicit_locked_fields = movie_field_locks_from_db(conn, movie_uuid)
    # Kinds whose primary artwork must not be overwritten by a plugin/sync.
    # Two lock sources: the explicit field lock, and a user-selected (locked)
    # primary media asset. Both protect the metadata URL; the explicit field
    # lock additionally suppresses adding/replacing the primary media asset.
    metadata_locked_kinds: set[str] = set()
    media_locked_kinds: set[str] = set()
    for kind in ("poster", "backdrop"):
        field_locked = kind in explicit_locked_fields
        if field_locked or has_locked_primary_media(conn, movie_id=movie_uuid, kind=kind):
            metadata_locked_kinds.add(kind)
        if field_locked:
            media_locked_kinds.add(kind)
    metadata_updates, media_updates = filter_locked_artwork_updates(
        metadata_updates,
        media_updates,
        metadata_locked_kinds=metadata_locked_kinds,
        media_locked_kinds=media_locked_kinds,
    )
    metadata_updates, media_updates = filter_hidden_artwork_updates(
        conn,
        movie_uuid,
        metadata_updates,
        media_updates,
    )
    changed = bool(
        movie_updates
        or metadata_updates
        or technical_updates
        or media_updates
        or identifiers
        or credit_updates
        or localization_updates
        or genre_changed
    )

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
            _technical_carrier, _technical_outer = split_legacy_packaging(
                technical_updates.get("packaging") or []
            )
            cur.execute(
                """
                INSERT INTO movie_technical_specs (
                    movie_id, hdr, packaging, carrier_type, outer_packaging,
                    screen_ratios, audio_tracks, subtitles, regions,
                    content_ratings, video_resolution, video_codecs, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (movie_id) DO UPDATE SET
                    video_resolution=COALESCE(
                        EXCLUDED.video_resolution, movie_technical_specs.video_resolution
                    ),
                    hdr=CASE
                        WHEN EXCLUDED.hdr <> '[]'::jsonb THEN EXCLUDED.hdr
                        ELSE movie_technical_specs.hdr
                    END,
                    screen_ratios=CASE
                        WHEN EXCLUDED.screen_ratios <> '[]'::jsonb THEN EXCLUDED.screen_ratios
                        ELSE movie_technical_specs.screen_ratios
                    END,
                    video_codecs=CASE
                        WHEN EXCLUDED.video_codecs <> '[]'::jsonb THEN EXCLUDED.video_codecs
                        ELSE movie_technical_specs.video_codecs
                    END,
                    packaging=CASE
                        WHEN EXCLUDED.packaging <> '[]'::jsonb THEN EXCLUDED.packaging
                        ELSE movie_technical_specs.packaging
                    END,
                    -- Enrichment still speaks the flat list, so the two axes are
                    -- derived from it on the way in (next_packaging.split_legacy_
                    -- packaging). Without this an enriched release would fill only
                    -- the legacy mirror and the edit form would read as empty.
                    -- A user edit already stored on the axes outranks a derived
                    -- guess, hence COALESCE/non-empty rather than overwrite.
                    carrier_type=COALESCE(
                        movie_technical_specs.carrier_type, EXCLUDED.carrier_type
                    ),
                    outer_packaging=CASE
                        WHEN movie_technical_specs.outer_packaging <> '[]'::jsonb
                            THEN movie_technical_specs.outer_packaging
                        ELSE EXCLUDED.outer_packaging
                    END,
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
                    Jsonb(json_ready(technical_updates.get("hdr") or [])),
                    Jsonb(json_ready(technical_updates.get("packaging") or [])),
                    _technical_carrier,
                    Jsonb(json_ready(_technical_outer)),
                    Jsonb(json_ready(technical_updates.get("screen_ratios") or [])),
                    Jsonb(json_ready(technical_updates.get("audio_tracks") or [])),
                    Jsonb(json_ready(technical_updates.get("subtitles") or [])),
                    Jsonb(json_ready(technical_updates.get("regions") or [])),
                    Jsonb(json_ready(technical_updates.get("content_ratings") or {})),
                    technical_updates.get("video_resolution"),
                    Jsonb(json_ready(technical_updates.get("video_codecs") or [])),
                ),
            )

        if identifiers and table_exists(conn, "movie_identifiers"):
            for provider, identifier in identifiers.items():
                if not identifier:
                    continue
                if provider in MOVIEVAULT_IDENTIFIER_PROVIDERS:
                    # A MovieVault id names one *release*, and re-matching a
                    # movie to a different pressing is routine -- the barcode
                    # fallback picker exists to do exactly that. The primary key
                    # includes `identifier`, so plain insert-if-absent would
                    # accumulate one row per pressing the movie was ever matched
                    # to, and `movie_identifiers()` reads them ordered by
                    # identifier: the lexicographically smallest UUID would win,
                    # which is to say an arbitrary stale one. Superseding keeps
                    # the row saying what this movie is matched to *now*.
                    cur.execute(
                        """
                        DELETE FROM movie_identifiers
                        WHERE movie_id=%s AND provider_id=%s AND identifier_type='movie_id'
                          AND identifier <> %s
                        """,
                        (movie_uuid, provider, str(identifier)),
                    )
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

    applied_genre_keys: list[str] | None = None
    if genre_keys_provided:
        applied_genre_keys = (
            replace_movie_genres(conn, movie_uuid, normalized_genre_keys)
            if genre_changed
            else current_genre_keys
        )

    # After the movie updates, because the link is only permitted once the type
    # is SHOW, and it is those updates that may have made it one.
    applied_series = apply_movie_series_link(conn, movie_uuid, proposal.get("series"))
    # After the series link, because a disc may carry season and episode
    # references and those resolve against a series the link just established.
    applied_discs = apply_movie_disc_enrichment(conn, movie_uuid, proposal.get("discs"))

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
            "genres": normalized_genre_keys if genre_keys_provided else None,
            "fieldDecisions": proposal.get("fieldDecisions") or [],
        },
    )
    if identifiers:
        record_movie_identifier_change(conn, movie_uuid)
    applied_payload = {
        "movieUpdates": movie_updates,
        "metadataUpdates": metadata_updates,
        "technicalUpdates": technical_updates,
        "mediaUpdates": applied_media_updates,
        "identifiers": identifiers,
        "series": applied_series,
        "discs": applied_discs,
        "credits": applied_credit_updates,
        "localizations": applied_localizations,
        "genres": applied_genre_keys,
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


def enqueue_series_metadata_refresh(conn, series_id: UUID | str) -> None:
    """Queue the description lookup for a series that has just been created.

    Best effort on purpose. The series and the disc's link to it are already
    written and correct; the description is an enhancement on top. A queue that
    is unavailable -- an older database without `background_jobs`, a table the
    caller has no rights to -- must not undo a link that succeeded.

    The import is deferred because `next_app` imports this module, so naming it
    at module level would close the cycle. The same pattern is already used in
    `next_worker` for the person-refresh cascade.
    """
    try:
        try:
            from .next_app import create_background_job
        except ImportError:  # pragma: no cover - supports direct module execution
            from next_app import create_background_job

        create_background_job(
            conn,
            job_type=SERIES_METADATA_REFRESH_JOB_TYPE,
            payload={"seriesId": str(series_id)},
        )
    except Exception:
        logger.warning(
            "series metadata refresh could not be queued for %s", series_id, exc_info=True
        )


SERIES_DETAILS_CAPABILITY = "series_details"
SERIES_SEARCH_CAPABILITY = "search_series"


def search_series_candidates(conn, title: str, *, year: str = "") -> dict[str, Any]:
    """Ask every enabled source which series a person might mean.

    Deliberately *not* the same act as `refresh_series_metadata`. That one refuses
    to search, because a source matching on a title is a guess wearing an answer's
    clothes -- the same show is titled differently across regions and two unrelated
    shows can share a name. §7b of the media-type document turns that into a rule.

    The rule is about *automatic* identification. Here a person typed the title and
    will pick from what comes back, so the guess never becomes an assertion: this
    function writes nothing, and identity is established only by the choosing.

    Results are not merged across sources. A candidate is an offer from one source
    in one namespace, and blending two sources' lists would produce rows nobody
    can act on -- picking one has to yield exactly one identifier.
    """
    title = clean_text(title) or ""
    if not title:
        return {"status": "skipped", "reason": "no title", "candidates": []}

    plugins = [
        plugin
        for plugin in metadata_source_plugins(conn)
        if SERIES_SEARCH_CAPABILITY in plugin_capabilities(plugin)
    ]
    if not plugins:
        return {"status": "skipped", "reason": "no series search source", "candidates": []}

    candidates: list[dict[str, Any]] = []
    # `require_hit=False`: a search that answers `miss` has genuinely found
    # nothing, which is an answer. Only a detail lookup's `miss` is a failure.
    results, errors = consult_plugins(
        conn,
        plugins,
        SERIES_SEARCH_CAPABILITY,
        {"title": title, "year": clean_text(year) or ""},
        require_hit=False,
    )
    for plugin_id, result in results:
        for item in result.get("items") or []:
            if not isinstance(item, dict):
                continue
            identifier_type = clean_text(item.get("identifierType"))
            identifier = clean_text(item.get("identifier"))
            if not identifier_type or not identifier:
                # A source that names no namespace cannot be stored, and storing it
                # under a guessed one is how a wrong id becomes indistinguishable
                # from a right one.
                continue
            candidates.append(
                {
                    "pluginId": plugin_id,
                    "provider": clean_text(item.get("provider")) or plugin_id,
                    "providerLabel": clean_text(item.get("providerLabel")) or plugin_id,
                    "identifierType": identifier_type,
                    "identifier": identifier,
                    "title": clean_text(item.get("title")),
                    "originalTitle": clean_text(item.get("originalTitle")),
                    "year": clean_text(item.get("year")),
                    "overview": clean_text(item.get("overview")),
                    # Same filter the artwork path uses, so a candidate cannot show
                    # a thumbnail from a scheme the rest of the app would refuse.
                    "posterUrl": (image_url_options(item.get("posterUrl")) or [""])[0],
                }
            )

    return {
        "status": "ok" if candidates else "miss",
        "candidates": candidates,
        "errors": errors,
    }


def series_detail_source_plugins(conn) -> list[dict[str, Any]]:
    """The enabled metadata sources that can describe a series, in the user's order.

    Discovery is by declared capability, not by name. That is the whole point:
    adding TVDB or Fanart later must be a matter of installing a plugin that says
    `series_details`, not of editing this module. The hard-coded `tmdb` that used
    to sit here made every future source a code change.

    `metadata_source_plugins` already returns installed and enabled rows ordered
    by `order_index`, which is the order the user themselves ranked the sources
    in and the same order the movie pipeline consults them in.
    """
    return [
        plugin
        for plugin in metadata_source_plugins(conn)
        if SERIES_DETAILS_CAPABILITY in plugin_capabilities(plugin)
    ]


def series_identifier_map(conn, series_uuid: UUID) -> dict[str, str]:
    """Every identifier the series carries, keyed by identifier type.

    All of them travel to every source, and each source picks out the namespace
    it speaks. A source is the only thing that knows which identifiers it can
    use, so filtering here on its behalf would just be a second, staler copy of
    that knowledge.
    """
    if not table_exists(conn, "series_identifiers"):
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT identifier_type, identifier
            FROM series_identifiers
            WHERE series_id = %s
            ORDER BY provider_id, identifier_type
            """,
            (series_uuid,),
        )
        rows = cur.fetchall()
    identifiers: dict[str, str] = {}
    for row in rows:
        identifier_type = clean_text(row.get("identifier_type"))
        identifier = clean_text(row.get("identifier"))
        # First one wins: two rows for one type is a conflict nobody can resolve
        # here, and picking the later one at random would make the result depend
        # on insertion order.
        if identifier_type and identifier and identifier_type not in identifiers:
            identifiers[identifier_type] = identifier
    return identifiers


def series_lookup_payload(row: dict[str, Any], identifiers: dict[str, str]) -> dict[str, Any]:
    """What every series source is asked.

    `tmdbTvId` is repeated outside the map on purpose: the TMDB plugin shipped
    reading exactly that key, and an installation that has not taken the new
    plugin version yet must keep working. A consumer tolerating a field before
    the producer relies on it is the same rule the distribution feed follows.
    """
    return {
        "seriesIdentifiers": dict(identifiers),
        "tmdbTvId": identifiers.get(SERIES_IDENTIFIER_TYPE, ""),
        "title": clean_text(row.get("title")),
        "startYear": clean_text(row.get("start_year")),
        "endYear": clean_text(row.get("end_year")),
    }


def consult_plugins(
    conn,
    plugins: list[dict[str, Any]],
    entrypoint: str,
    payload: dict[str, Any],
    *,
    require_hit: bool = True,
) -> tuple[list[tuple[str, dict[str, Any]]], list[dict[str, str]]]:
    """Run one entrypoint across a set of sources and collect what came back.

    **The one place a series-side plugin context is built.** That is the whole
    reason this exists. The loop was written out four times, each copy carrying
    the same warning comment above it, and one copy had it wrong: it passed
    `plugin_config_from_db`'s output straight through as the context. The config
    carries `secretsRef` -- the *names* of the secrets -- while
    `plugin_execution_context` is what resolves them into `secrets`, which is
    where a plugin reads its API key. So every series lookup on a correctly
    configured installation failed with "TMDb API key is not configured", the
    error went into a list nothing displayed, and the page reported that no
    source had anything to add. The movie path was fine throughout because it
    used the context builder from the start, and that asymmetry is exactly what
    made it look like a series problem rather than a wiring one.

    A comment repeated above four copies did not prevent that, and would not
    prevent the fifth. One function can.

    `require_hit` is the only real difference between the callers. A detail
    lookup that answers `miss` has nothing to contribute and is reported as a
    failure; a *search* that answers `miss` has legitimately found nothing and
    its empty item list is a valid answer, not an error.

    Errors are collected, never raised. One source being unreachable must not
    cost the answers the others already gave -- and every caller has a reason to
    tell "no source could answer" apart from "no source has anything", which is
    a distinction it can only draw if it is handed the failures.
    """
    results: list[tuple[str, dict[str, Any]]] = []
    errors: list[dict[str, str]] = []
    for plugin in plugins:
        plugin_id = str(plugin.get("id") or "")
        if not plugin_id:
            continue
        context = plugin_execution_context(conn, plugin, plugin_config_from_db(conn, plugin_id))
        try:
            execution = run_cached_plugin_entrypoint(conn, plugin_id, entrypoint, payload, context)
        except Exception as exc:  # pragma: no cover - defensive, a source is not the job
            errors.append({"pluginId": plugin_id, "error": str(exc)})
            logger.warning("source %s failed on %s", plugin_id, entrypoint, exc_info=True)
            continue
        result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
        if execution.get("status") != "ok" or (require_hit and result.get("status") != "hit"):
            # `state` before the result status, and never a bare "miss" for an
            # execution that failed. Four runtime states -- `not_found`,
            # `manifest_only`, `entrypoint_unavailable`, and a load-time
            # `runtime_error` -- carry no `error` field at all, so the old chain
            # fell through to the literal word "miss" and reported a
            # configuration fault in the vocabulary of "this source has never
            # heard of your series". That sends a reader to check their spelling
            # instead of their setup, and it hid an unreachable entrypoint on
            # every series call for the whole of this feature's life.
            reason = clean_text(execution.get("error"))
            if not reason and execution.get("status") != "ok":
                reason = clean_text(execution.get("state")) or "unavailable"
            errors.append(
                {"pluginId": plugin_id, "error": reason or result.get("status") or "miss"}
            )
            continue
        results.append((plugin_id, result))
    return results, errors


def consult_series_sources(
    conn, row: dict[str, Any], identifiers: dict[str, str]
) -> tuple[list[tuple[str, dict[str, Any]]], list[dict[str, str]]]:
    """Ask every enabled series source to describe one series, in the user's order.

    The series-shaped wrapper over `consult_plugins`: it owns the payload, which
    is the part its two callers must agree on. The refresh writes what comes
    back, the season offer only shows it, and neither may ask a different
    question than the other.
    """
    return consult_plugins(
        conn,
        series_detail_source_plugins(conn),
        SERIES_DETAILS_CAPABILITY,
        series_lookup_payload(row, identifiers),
    )


def merge_series_details(results: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """Combine what several sources said into one answer, first source wins.

    "First" is the user's own plugin order, so precedence is a setting rather
    than a rule buried here. Only empty gaps are filled, which means a later
    source can complete a season the first source knew nothing about without
    being able to contradict it.

    Attribution travels with the text: `sources` records which plugin supplied
    the series description and which supplied each season, because "where did
    this sentence come from" stops being answerable once several sources can
    write into the same column.

    Each field competes on its own. A source that has a poster but no synopsis
    still supplies the poster, and a season that only one source has heard of
    still arrives -- which is the point of consulting more than one source. That
    is why a season is recorded when it offers *anything*, rather than only when
    it offers an overview as it did when text was all there was.

    Artwork carries its runner-up URLs along with the winner. The Posters tab
    shows them as alternatives, so "first source wins" decides the default
    without deciding the only option.
    """
    overview = ""
    overview_source = ""
    artwork: dict[str, dict[str, Any]] = {}
    seasons: dict[int, dict[str, Any]] = {}
    for plugin_id, result in results:
        stated = result.get("series") if isinstance(result.get("series"), dict) else {}
        candidate = clean_text(stated.get("overview"))
        if candidate and not overview:
            overview = candidate
            overview_source = plugin_id
        for kind, primary_key, options_key in (
            ("poster", "posterUrl", "posters"),
            ("backdrop", "backdropUrl", "backdropUrls"),
        ):
            if kind in artwork:
                continue
            options = image_url_options(stated.get(primary_key), stated.get(options_key))
            if not options:
                continue
            artwork[kind] = {"sourceUrl": options[0], "options": options, "source": plugin_id}
        for season in result.get("seasons") or []:
            if not isinstance(season, dict):
                continue
            number = season.get("seasonNumber")
            if not isinstance(number, int) or isinstance(number, bool):
                continue
            entry = seasons.setdefault(number, {})
            season_overview = clean_text(season.get("overview"))
            if season_overview and "overview" not in entry:
                entry["overview"] = season_overview
                entry["source"] = plugin_id
            poster = image_url_options(season.get("posterUrl"))
            if poster and "posterUrl" not in entry:
                entry["posterUrl"] = poster[0]
                entry["posterSource"] = plugin_id
            # The label fields, on the same first-source-wins terms as the rest.
            # They were dropped while the only consumer was the refresh, which
            # writes overviews and nothing else. The season picker is a second
            # consumer with a different need: "Season 3" alone is not enough to
            # choose from, and a title, a year and an episode count are what make
            # picking the right box an act of recognition rather than counting.
            for field in ("title", "year"):
                value = clean_text(season.get(field))
                if value and field not in entry:
                    entry[field] = value
            episode_count = season.get("episodeCount")
            if (
                isinstance(episode_count, int)
                and not isinstance(episode_count, bool)
                and episode_count >= 0
                and "episodeCount" not in entry
            ):
                entry["episodeCount"] = episode_count
    # Every season the source reported, including one that offered neither text
    # nor a poster. When text was all this merged, an empty entry was dropped
    # because it could only produce a write of zero rows. That stopped being true
    # once the refresh may *create* seasons: the existence of season 4 is itself
    # the answer, whether or not anybody wrote a synopsis for it.
    return {
        "overview": overview,
        "overviewSource": overview_source,
        "artwork": artwork,
        "seasons": seasons,
    }


SEASON_EPISODES_CAPABILITY = "season_episodes"

EPISODE_TEXT_LIMITS = {"title": 400, "overview": 4000, "air_date": 20, "still_url": 1000}


def refresh_season_episodes(conn, season_id: UUID | str) -> dict[str, Any]:
    """Fill one season's episode list from whichever sources can supply it.

    One season at a time, and on demand. `/tv/{id}/season/{n}` costs a request
    per season, which is the reason the series refresh deliberately does not
    reach for it: a ten-season show would turn one call into eleven for every
    user, most of whom never look at an episode list.

    Asking per season moves that cost onto the person who opened the season --
    which is also why the surface sits behind Collectors mode. The expensive
    thing is paid for by whoever wanted it.

    Never overwrites, for the same reason `ensure_seasons` does not: an episode
    title may have been corrected by hand, and a re-fetch is not a reason to
    undo that. Only missing episodes are inserted and only empty fields are
    filled, which also makes this safe to re-run.
    """
    season_uuid = UUID(str(season_id))
    if not table_exists(conn, "series_episodes") or not table_exists(conn, "series_seasons"):
        return {"status": "unavailable"}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id AS season_id, s.season_number, s.series_id,
                   sr.title, sr.start_year, sr.end_year
            FROM series_seasons s
            JOIN series sr ON sr.id = s.series_id
            WHERE s.id = %s AND s.deleted_at IS NULL AND sr.deleted_at IS NULL
            """,
            (season_uuid,),
        )
        row = cur.fetchone()
    if not row:
        return {"status": "missing"}

    series_uuid = row["series_id"]
    identifiers = series_identifier_map(conn, series_uuid)
    if not identifiers:
        # The same dead end the series refresh reports, and for the same reason.
        # Naming it identically is what lets one explanation cover both.
        return {"status": "skipped", "reason": "no series identifier"}

    plugins = [
        plugin
        for plugin in metadata_source_plugins(conn)
        if SEASON_EPISODES_CAPABILITY in plugin_capabilities(plugin)
    ]
    if not plugins:
        return {"status": "skipped", "reason": "no episode source"}

    payload = {
        "seriesIdentifiers": dict(identifiers),
        "tmdbTvId": identifiers.get(SERIES_IDENTIFIER_TYPE, ""),
        "seasonNumber": row["season_number"],
        "title": clean_text(row.get("title")),
    }
    episodes: dict[int, dict[str, Any]] = {}
    sources: dict[str, str] = {}
    results, errors = consult_plugins(conn, plugins, SEASON_EPISODES_CAPABILITY, payload)
    for plugin_id, result in results:
        for episode in result.get("episodes") or []:
            if not isinstance(episode, dict):
                continue
            number = episode.get("episodeNumber")
            if not isinstance(number, int) or isinstance(number, bool) or number < 0:
                continue
            # First source wins per episode, same rule as the series merge.
            if number in episodes:
                continue
            episodes[number] = episode
            sources[str(number)] = plugin_id

    if not episodes:
        return {"status": "miss", "errors": errors}

    inserted = 0
    updated = 0
    with conn.cursor() as cur:
        for number, episode in sorted(episodes.items()):
            episode_uuid = uuid.uuid4()
            cur.execute(
                """
                INSERT INTO series_episodes (
                    id, public_id, series_id, season_id, episode_number,
                    title, overview, air_date, runtime_minutes, still_url
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    episode_uuid,
                    f"next-episode-{episode_uuid.hex[:12]}",
                    series_uuid,
                    season_uuid,
                    number,
                    clean_text(episode.get("title"))[: EPISODE_TEXT_LIMITS["title"]] or None,
                    clean_text(episode.get("overview"))[: EPISODE_TEXT_LIMITS["overview"]] or None,
                    clean_text(episode.get("airDate"))[: EPISODE_TEXT_LIMITS["air_date"]] or None,
                    episode.get("runtimeMinutes") if isinstance(episode.get("runtimeMinutes"), int) else None,
                    clean_text(episode.get("stillUrl"))[: EPISODE_TEXT_LIMITS["still_url"]] or None,
                ),
            )
            if cur.rowcount:
                inserted += 1
                continue
            # The row already existed. Fill only what is empty -- the check and
            # the write are one statement, so a second worker cannot land between
            # them, and a title somebody corrected is left alone.
            cur.execute(
                """
                UPDATE series_episodes
                SET title = COALESCE(NULLIF(title, ''), %s),
                    overview = COALESCE(NULLIF(overview, ''), %s),
                    air_date = COALESCE(NULLIF(air_date, ''), %s),
                    runtime_minutes = COALESCE(runtime_minutes, %s),
                    still_url = COALESCE(NULLIF(still_url, ''), %s),
                    updated_at = now()
                WHERE season_id = %s AND episode_number = %s AND deleted_at IS NULL
                """,
                (
                    clean_text(episode.get("title"))[: EPISODE_TEXT_LIMITS["title"]] or None,
                    clean_text(episode.get("overview"))[: EPISODE_TEXT_LIMITS["overview"]] or None,
                    clean_text(episode.get("airDate"))[: EPISODE_TEXT_LIMITS["air_date"]] or None,
                    episode.get("runtimeMinutes") if isinstance(episode.get("runtimeMinutes"), int) else None,
                    clean_text(episode.get("stillUrl"))[: EPISODE_TEXT_LIMITS["still_url"]] or None,
                    season_uuid,
                    number,
                ),
            )
            updated += cur.rowcount or 0

    return {
        "status": "ok",
        "seasonId": str(season_uuid),
        "episodesInserted": inserted,
        "episodesUpdated": updated,
        "sources": {"consulted": [str(plugin.get("id")) for plugin in plugins], "episodes": sources},
        "errors": errors,
    }


def apply_season_poster_inheritance(cur, movie_uuids: list[UUID] | None = None) -> int:
    """Give a disc of a show the poster of the season it carries.

    A disc linked through a barcode or a TMDb/TVDb id normally arrives with a
    poster from the source. When it does not, the season it is filed under
    already has one, and that is a picture of what is in the box -- so the disc
    takes it and *has* a poster, rather than a surface deciding to draw one.

    Stored rather than resolved per read, and the difference is the whole point.
    A displayed fallback stops at the surface that implements it: the Library
    would show something, the sync delta would carry nothing, and the iOS app
    would still show an empty tile. "A disc of a series always has a poster" is a
    statement about the record, so it has to be true of the record.

    This is the same shape `container_metadata_proposal` already uses for a box
    set that has no cover of its own, down to the provenance flag -- and it is
    that flag that makes storing safe. `poster_from_season` marks the poster as
    inherited, so a later run may replace it when the season's artwork changes,
    while a poster the disc owns is left alone forever. Without it, "has a
    poster" and "has its own poster" become indistinguishable the moment this
    writes, and the disc could never be given a better one.

    Passing ``movie_uuids`` limits the run to those discs; ``None`` sweeps every
    linked disc, which is what the backfill in migration 078 needs.

    Three conditions, all of them in SQL so a sweep stays one statement:

    * The disc has no poster, or has one this function put there. An uploaded or
      fetched cover always wins -- that is the "unless one was uploaded" clause,
      and it needs no separate check because such a poster is simply not ours.
    * ``poster_locked`` is unset. The operator has said to leave the artwork
      alone, and this is artwork.
    * The value would actually change, so re-running writes nothing. `updated_at`
      feeds the sync delta, and a no-op write would wake every client for a
      poster that did not move.

    Only a season poster with a `source_url` is taken. Season artwork is stored
    as a remote provider URL, and a locally stored asset could not be served
    under a movie anyway: `actor_can_view_media_asset` has no branch for
    `series_season`, which §7o records as a live gap. Skipping those leaves the
    disc as it was rather than pointing it at a URL that would 403.
    """
    scoped = movie_uuids is not None
    if scoped and not movie_uuids:
        return 0
    ids = list(movie_uuids or [])
    cur.execute(
        f"""
        WITH candidate AS (
            SELECT DISTINCT ON (ms.movie_id)
                ms.movie_id,
                ma.source_url AS poster_url
            FROM movie_seasons ms
            JOIN series_seasons ss ON ss.id = ms.season_id AND ss.deleted_at IS NULL
            JOIN entity_media em
              ON em.entity_type='series_season'
             AND em.entity_id=ss.id
             AND em.deleted_at IS NULL
             AND em.hidden_at IS NULL
            JOIN media_assets ma ON ma.id = em.media_id AND ma.kind='poster'
            WHERE NULLIF(BTRIM(COALESCE(ma.source_url, '')), '') IS NOT NULL
              {"AND ms.movie_id = ANY(%s)" if scoped else ""}
            -- The lowest season number a set carries, which is what "the first
            -- of the selected seasons" means once the seasons are the disc's own
            -- rather than a guess. Season 0 is not excluded here: a curator who
            -- filed a disc under the specials has said what is in the box.
            ORDER BY ms.movie_id, ss.season_number, em.is_primary DESC, em.sort_order, ma.created_at
        )
        UPDATE movies m
        SET metadata = COALESCE(m.metadata, '{{}}'::jsonb)
                || jsonb_build_object('poster_url', c.poster_url, 'poster_from_season', true),
            updated_at = now()
        FROM candidate c
        WHERE m.id = c.movie_id
          AND m.deleted_at IS NULL
          AND LOWER(COALESCE(m.metadata->>'poster_locked', 'false')) NOT IN ('true', '1', 'yes')
          AND (
                NULLIF(BTRIM(COALESCE(m.metadata->>'poster_url', '')), '') IS NULL
                OR LOWER(COALESCE(m.metadata->>'poster_from_season', 'false')) IN ('true', '1', 'yes')
              )
          AND COALESCE(m.metadata->>'poster_url', '') IS DISTINCT FROM c.poster_url
        """,
        (ids,) if scoped else (),
    )
    return cur.rowcount or 0


def clear_orphaned_season_poster(cur, movie_uuid: UUID) -> None:
    """Drop an inherited poster from a disc that no longer carries that season.

    Unlinking a disc, or moving it to a season with no artwork, would otherwise
    leave the old season's poster behind as though the disc owned it -- and it
    would then survive every later run, because the check above stops at a disc
    that already has a poster it did not put there.

    Only ever removes what `apply_season_poster_inheritance` wrote. A poster the
    disc owns is not this function's to touch.
    """
    cur.execute(
        """
        UPDATE movies m
        SET metadata = (m.metadata - 'poster_url' - 'poster_from_season'),
            updated_at = now()
        WHERE m.id = %s
          AND LOWER(COALESCE(m.metadata->>'poster_from_season', 'false')) IN ('true', '1', 'yes')
          AND NOT EXISTS (
                SELECT 1
                FROM movie_seasons ms
                JOIN series_seasons ss ON ss.id = ms.season_id AND ss.deleted_at IS NULL
                JOIN entity_media em
                  ON em.entity_type='series_season'
                 AND em.entity_id=ss.id
                 AND em.deleted_at IS NULL
                 AND em.hidden_at IS NULL
                JOIN media_assets ma ON ma.id = em.media_id AND ma.kind='poster'
                WHERE ms.movie_id = m.id
                  AND NULLIF(BTRIM(COALESCE(ma.source_url, '')), '') IS NOT NULL
          )
        """,
        (movie_uuid,),
    )


def refresh_series_metadata(conn, series_id: UUID | str) -> dict[str, Any]:
    """Fill a series' description from whichever sources can describe it.

    Owns its transaction boundary: the sources are consulted over the network,
    so the reads above them are committed first and callers must not wrap this
    in an explicit transaction block.


    This is the second half of the division of labour the feed established.
    MovieVault states *identity and structure* -- which series, which seasons --
    and stops there, deliberately: it is not licensed to redistribute provider
    prose and has no reason to. The description therefore comes from sources
    DiscVault has its own relationship with, which is also why nothing here ever
    travels back over the feed.

    Several sources rather than one. TMDB, TVDB and Fanart know different things
    and cover different shows, and a season TMDB has never heard of is exactly
    the gap a second source closes. They are consulted in the user's own plugin
    order and the first non-empty answer wins, so precedence is something the
    user set rather than something decided here.

    Never overwrites. An existing overview may have been written by hand through
    the series API, and a refresh is not a reason to replace it -- the same
    posture `ensure_seasons` already takes for a season row somebody edited.
    Filling only what is empty also makes this safe to re-run, which matters
    because a failed job is retried.

    A series with no identifiers at all is skipped rather than searched. An id is
    what makes this exact; falling back to a title search would trade a known
    answer for a guess, and §7b of the media-type document is explicit that only
    a source querying a series namespace may speak to series identity. Which of
    the identifiers a given source can use is that source's own decision, so all
    of them are offered and a source that recognises none reports a miss.
    """
    series_uuid = UUID(str(series_id))
    if not table_exists(conn, "series") or not table_exists(conn, "series_identifiers"):
        return {"status": "unavailable"}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, overview, start_year, end_year
            FROM series
            WHERE id = %s AND deleted_at IS NULL
            """,
            (series_uuid,),
        )
        row = cur.fetchone()
    if not row:
        return {"status": "missing"}
    identifiers = series_identifier_map(conn, series_uuid)
    if not identifiers:
        return {"status": "skipped", "reason": "no series identifier"}

    if not series_detail_source_plugins(conn):
        return {"status": "skipped", "reason": "no series source"}

    # The sources are consulted over the network. The transaction the reads
    # above opened is released inside `run_cached_plugin_entrypoint`, at the
    # last moment before each call -- see `release_read_transaction` for why it
    # cannot be done here instead.
    results, errors = consult_series_sources(conn, row, identifiers)

    if not results:
        # Deliberately not raised. The series keeps the title it already has, and
        # a source being unreachable is not a reason to fail a job that will be
        # retried.
        return {"status": "miss", "errors": errors}

    merged = merge_series_details(results)
    updated_series = False
    season_sources: dict[str, str] = {}
    with conn.cursor() as cur:
        if merged["overview"] and not clean_text(row.get("overview")):
            cur.execute(
                "UPDATE series SET overview = %s, updated_at = now() WHERE id = %s",
                (merged["overview"][: SERIES_TEXT_LIMITS["overview"]], series_uuid),
            )
            updated_series = True
        updated_seasons = 0
        for number, season in sorted(merged["seasons"].items()):
            overview_text = season.get("overview")
            if not overview_text:
                # The season exists and was created above; there is simply no
                # synopsis to write. Running the update anyway would cost a query
                # that can only ever touch zero rows.
                continue
            # `overview IS NULL OR overview = ''` rather than a read-then-write:
            # the check and the write are one statement, so a second worker
            # running the same job cannot land between them.
            cur.execute(
                """
                UPDATE series_seasons
                SET overview = %s, updated_at = now()
                WHERE series_id = %s AND season_number = %s AND deleted_at IS NULL
                  AND (overview IS NULL OR overview = '')
                """,
                (
                    overview_text[: SEASON_TEXT_LIMITS["overview"]],
                    series_uuid,
                    number,
                ),
            )
            written = cur.rowcount or 0
            updated_seasons += written
            if written:
                season_sources[str(number)] = season.get("source", "")

    # Artwork is applied outside the cursor above because each write is its own
    # small transaction inside `apply_primary_media_update`, and because a series
    # with no artwork at all must still keep the text it just gained.
    applied_artwork = apply_series_artwork(conn, series_uuid, merged)

    # Season artwork has just landed, so every disc filed under one of these
    # seasons may now have an answer it did not have a moment ago. Applied here
    # rather than in the route because the background worker refreshes through
    # this same function and would otherwise leave those discs behind.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM movies WHERE series_id = %s AND deleted_at IS NULL",
            (series_uuid,),
        )
        linked = [row["id"] for row in cur.fetchall()]
        inherited_posters = apply_season_poster_inheritance(cur, linked) if linked else 0

    return {
        "status": "ok",
        "seriesId": str(series_uuid),
        "seriesUpdated": updated_series,
        "seasonsUpdated": updated_seasons,
        "artwork": applied_artwork["series"],
        "seasonArtwork": applied_artwork["seasons"],
        "discPostersInherited": inherited_posters,
        "sources": {
            "consulted": [plugin_id for plugin_id, _ in results],
            "series": merged["overviewSource"] if updated_series else "",
            "seasons": season_sources,
            "artwork": {kind: entry["source"] for kind, entry in merged["artwork"].items()},
        },
        "errors": errors,
    }


def available_series_seasons(conn, series_id: UUID | str) -> dict[str, Any]:
    """Which seasons the sources know about, and which of them already exist here.

    An offer, and nothing else -- the same separation `search_series_candidates`
    keeps for identity. Searching writes nothing and choosing is a separate act,
    because a route that both proposed and stored would make a source's guess
    indistinguishable from the owner's claim the moment somebody clicked.

    That separation is load-bearing here rather than merely tidy.
    `test_a_season_the_feed_never_recorded_is_not_created` fixes the rule that
    enrichment does not create seasons nobody owns: the source knows every season
    the show has, the collection knows the ones a disc covers, and those are
    different facts. This function serves the first so a person can assert the
    second; it must never do both.

    `exists` rather than filtering the row out. A season already in the
    collection stays visible, so the list reads as the whole show with your part
    of it marked -- a filtered list would silently answer a different question
    ("what is missing") and make a complete series look like an empty result.
    """
    series_uuid = UUID(str(series_id))
    if not table_exists(conn, "series") or not table_exists(conn, "series_seasons"):
        return {"status": "unavailable", "seasons": []}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, overview, start_year, end_year
            FROM series
            WHERE id = %s AND deleted_at IS NULL
            """,
            (series_uuid,),
        )
        row = cur.fetchone()
    if not row:
        return {"status": "missing", "seasons": []}
    identifiers = series_identifier_map(conn, series_uuid)
    if not identifiers:
        # The same dead end the identity picker exists to escape, reported in the
        # same words so the page can send the reader there instead of leaving
        # them to conclude the show has no seasons.
        return {"status": "skipped", "reason": "no series identifier", "seasons": []}
    if not series_detail_source_plugins(conn):
        return {"status": "skipped", "reason": "no series source", "seasons": []}

    results, errors = consult_series_sources(conn, row, identifiers)
    if not results:
        return {"status": "miss", "seasons": [], "errors": errors}

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT season_number FROM series_seasons
            WHERE series_id = %s AND deleted_at IS NULL
            """,
            (series_uuid,),
        )
        existing = {row["season_number"] for row in cur.fetchall()}

    merged = merge_series_details(results)
    seasons = []
    for number, season in sorted(merged["seasons"].items()):
        seasons.append(
            {
                "seasonNumber": number,
                "title": clean_text(season.get("title")) or "",
                "year": clean_text(season.get("year")) or "",
                "overview": clean_text(season.get("overview")) or "",
                "episodeCount": season.get("episodeCount"),
                "posterUrl": clean_text(season.get("posterUrl")) or "",
                "source": season.get("source") or season.get("posterSource") or "",
                "exists": number in existing,
            }
        )
    return {
        "status": "ok" if seasons else "miss",
        "seriesId": str(series_uuid),
        "seasons": seasons,
        "sources": {"consulted": [plugin_id for plugin_id, _ in results]},
        "errors": errors,
    }


def apply_series_artwork(conn, series_uuid: UUID, merged: dict[str, Any]) -> dict[str, Any]:
    """Store the merged artwork against the series and its seasons.

    Reuses the movie path's writers rather than a series-shaped copy of them:
    `apply_primary_media_update` is what already refuses to overwrite a primary
    somebody chose by hand, and that refusal is the *only* protection a series
    has -- it has no lock button. Re-implementing the write here would have meant
    re-implementing that refusal, or quietly not having it.

    Runner-up URLs are linked as options so the Posters and Backdrops tabs offer
    a choice. `sort_order` starts at 1 because 0 belongs to the primary.
    """
    applied: dict[str, Any] = {"series": {}, "seasons": {}}
    if not table_exists(conn, "media_assets") or not table_exists(conn, "entity_media"):
        return applied

    for kind, entry in merged.get("artwork", {}).items():
        provider_id = entry.get("source") or "metadata"
        primary = apply_primary_media_update(
            conn,
            entity_type="series",
            entity_id=series_uuid,
            kind=kind,
            source_url=entry["sourceUrl"],
            provider_id=provider_id,
        )
        options = []
        for sort_order, option_url in enumerate(entry.get("options") or [], start=1):
            if option_url == entry["sourceUrl"]:
                continue
            linked = link_media_option(
                conn,
                entity_type="series",
                entity_id=series_uuid,
                kind=kind,
                source_url=option_url,
                provider_id=provider_id,
                sort_order=sort_order,
            )
            if linked:
                options.append(linked)
        if primary or options:
            applied["series"][kind] = {"primary": primary, "options": options}

    season_posters = {
        number: entry for number, entry in merged.get("seasons", {}).items() if entry.get("posterUrl")
    }
    if not season_posters or not table_exists(conn, "series_seasons"):
        return applied

    # One read for every season rather than one per poster: the season row's id
    # is what the media link points at, and a series with twenty seasons should
    # not cost twenty lookups.
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, season_number
            FROM series_seasons
            WHERE series_id = %s AND deleted_at IS NULL
            """,
            (series_uuid,),
        )
        season_ids = {row["season_number"]: row["id"] for row in cur.fetchall()}

    for number, entry in sorted(season_posters.items()):
        season_id = season_ids.get(number)
        if not season_id:
            # A season the source knows about but this collection has no row for.
            # `ensure_seasons` owns creating those; inventing one here would let a
            # poster conjure a season nobody said existed.
            continue
        primary = apply_primary_media_update(
            conn,
            entity_type="series_season",
            entity_id=season_id,
            kind="poster",
            source_url=entry["posterUrl"],
            provider_id=entry.get("posterSource") or "metadata",
        )
        if primary:
            applied["seasons"][str(number)] = primary
    return applied


def refresh_movie_metadata(
    conn,
    movie_id: UUID | str,
    *,
    dry_run: bool = False,
    actor: dict[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Fetch, merge, apply and fan out provider metadata for one movie.

    Owns its transaction boundaries: applying the proposal locks the movie
    rows and the single global ``sync_state`` row (``record_sync_change``), so
    the apply is committed *before* the receiver-plugin push runs its network
    I/O. Holding those locks across a slow plugin call blocks every other
    writer in the app until the API-side ``lock_timeout`` fires, which used to
    surface as a bogus "DiscVault is temporarily offline" error. Callers must
    pass a connection that is not inside an explicit transaction block.

    That argument covered the *push* and silently skipped the *fetch*, which
    reaches the same providers and takes just as long: the caller's own
    pre-checks have opened a transaction before this function is entered, and
    ``preview_movie_metadata`` ran inside it -- measured as ``INTRANS`` at the
    provider call. The fetch is released by ``release_read_transaction`` at the
    last moment before each provider call, which is the only place it can be
    done; nothing between here and there does anything but read.
    """
    preview = preview_movie_metadata(conn, movie_id, actor, force=force)
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
    conn.commit()
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
    conn.commit()
    return {"dryRun": False, "preview": preview, "applied": applied, "receivers": receiver_summary}
