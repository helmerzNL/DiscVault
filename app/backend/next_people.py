"""People and native filmography helpers for DiscVault Next backend.

This module owns person read-model helpers, native payload builders, and people
routes extracted from ``next_app.py``. ``next_app.py`` re-imports these names
for backward compatibility.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from flask import Flask, request

try:  # pragma: no cover - exercised indirectly by both layouts
    from .next_common import NextApiError, parse_uuid, table_exists, response
    from .next_import import clean_text
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_common import NextApiError, parse_uuid, table_exists, response
    from next_import import clean_text


def _next_app():
    try:  # pragma: no cover - import shape depends on runtime layout
        from . import next_app
    except ImportError:  # pragma: no cover - supports gunicorn next_app:app
        import next_app
    return next_app


def person_biography_value(localizations: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> str:
    metadata = metadata if isinstance(metadata, dict) else {}
    localized_by_lang = {
        str(row.get("lang") or "").lower(): clean_text(row.get("biography")) or ""
        for row in localizations
        if clean_text(row.get("biography"))
    }
    for lang in ("nl-nl", "nl", "en-us", "en"):
        if localized_by_lang.get(lang):
            return localized_by_lang[lang]
    if localized_by_lang:
        return next(iter(localized_by_lang.values()))
    for key in ("biography", "bio", "biography_nl", "biography_en"):
        value = clean_text(metadata.get(key))
        if value:
            return value
    return ""


def normalize_native_language(value: Any) -> str:
    language = (clean_text(value) or "").lower().replace("_", "-")
    if not language:
        return ""
    return language.split(",", 1)[0].split(";", 1)[0].strip()


def native_date_value(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return clean_text(value.isoformat())
    return clean_text(value)


def person_biography_value_for_language(
    localizations: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
    language: str = "",
) -> str:
    metadata = metadata if isinstance(metadata, dict) else {}
    language = normalize_native_language(language)
    localized_by_lang = {
        str(row.get("lang") or "").lower().replace("_", "-"): clean_text(row.get("biography")) or ""
        for row in localizations
        if clean_text(row.get("biography"))
    }
    candidates: list[str] = []
    if language:
        candidates.append(language)
        base = language.split("-", 1)[0]
        if base and base != language:
            candidates.append(base)
    candidates.extend(["nl-nl", "nl", "en-us", "en"])
    for candidate in dict.fromkeys(candidates):
        if localized_by_lang.get(candidate):
            return localized_by_lang[candidate]
    if localized_by_lang:
        return next(iter(localized_by_lang.values()))
    metadata_keys: list[str] = []
    if language:
        metadata_keys.append(f"biography_{language.replace('-', '_')}")
        base = language.split("-", 1)[0]
        if base:
            metadata_keys.append(f"biography_{base}")
    metadata_keys.extend(["biography", "bio", "biography_nl", "biography_en"])
    for key in dict.fromkeys(metadata_keys):
        value = clean_text(metadata.get(key))
        if value:
            return value
    return ""


def person_entity(conn, person_id: UUID) -> dict[str, Any] | None:
    if not table_exists(conn, "people"):
        return None
    media_join = table_exists(conn, "media_assets")
    profile_select = (
        """
                ma.storage_backend AS profile_storage_backend,
                ma.storage_key AS profile_storage_key,
                ma.source_url AS profile_source_url
        """
        if media_join
        else """
                NULL AS profile_storage_backend,
                NULL AS profile_storage_key,
                NULL AS profile_source_url
        """
    )
    profile_join = "LEFT JOIN media_assets ma ON ma.id = p.profile_asset_id" if media_join else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                p.id,
                p.public_id,
                p.name,
                p.birth_date,
                p.death_date,
                p.place_of_birth,
                p.known_for,
                p.profile_asset_id,
                p.metadata,
                p.created_at,
                p.updated_at,
{profile_select}
            FROM people p
            {profile_join}
            WHERE p.id=%s
            """,
            (person_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    data = dict(row)
    profile_url = _next_app().media_asset_public_url(
        {
            "id": data.get("profile_asset_id"),
            "storage_backend": data.pop("profile_storage_backend", None),
            "storage_key": data.pop("profile_storage_key", None),
            "source_url": data.pop("profile_source_url", None),
        }
    )
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    if not profile_url:
        profile_url = _next_app().server_usable_image(
            metadata.get("profile_url")
            or metadata.get("profileUrl")
            or metadata.get("photo_url")
            or metadata.get("photoUrl")
        )
    if profile_url:
        data["profile_url"] = profile_url
    return data


def people_list_entities(
    conn,
    *,
    query: str = "",
    role: str = "all",
    limit: int = 120,
    actor: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not table_exists(conn, "people"):
        return []
    limit = max(1, min(int(limit or 120), 500))
    search = clean_text(query).lower()
    role_filter = (clean_text(role) or "all").lower()
    if role_filter not in {"all", "actor", "crew"}:
        role_filter = "all"

    media_join = table_exists(conn, "media_assets")
    credits_join = table_exists(conn, "movie_credits")
    profile_select = (
        """
                ma.storage_backend AS profile_storage_backend,
                ma.storage_key AS profile_storage_key,
                ma.source_url AS profile_source_url
        """
        if media_join
        else """
                NULL AS profile_storage_backend,
                NULL AS profile_storage_key,
                NULL AS profile_source_url
        """
    )
    profile_join = "LEFT JOIN media_assets ma ON ma.id = p.profile_asset_id" if media_join else ""
    visible_people_clause = ""
    visibility_params: list[Any] = []
    if credits_join:
        if actor is not None and not _next_app().actor_collection_visibility_scope(actor)["all"]:
            movie_where, visibility_params = _next_app().visible_movie_where_sql(conn, actor, "m")
            counts_source = f"""
                FROM movie_credits mc
                JOIN movies m ON m.id = mc.movie_id
                WHERE {movie_where}
            """
            visible_people_clause = "AND COALESCE(cc.movie_count, 0) > 0"
        else:
            counts_source = "FROM movie_credits mc"
        counts_cte = f"""
            WITH credit_counts AS (
                SELECT
                    mc.person_id,
                    COUNT(*)::int AS credit_count,
                    COUNT(DISTINCT mc.movie_id)::int AS movie_count,
                    COUNT(*) FILTER (WHERE lower(mc.credit_type) IN ('actor', 'cast'))::int AS actor_count,
                    COUNT(*) FILTER (WHERE lower(mc.credit_type) NOT IN ('actor', 'cast'))::int AS crew_count
                {counts_source}
                GROUP BY mc.person_id
            )
        """
    else:
        counts_cte = "WITH credit_counts AS (SELECT NULL::uuid AS person_id, 0::int AS credit_count, 0::int AS movie_count, 0::int AS actor_count, 0::int AS crew_count WHERE false)"
    search_like = f"%{search}%"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            {counts_cte}
            SELECT
                p.id,
                p.public_id,
                p.name,
                p.birth_date,
                p.death_date,
                p.place_of_birth,
                p.known_for,
                p.profile_asset_id,
                p.metadata,
                p.created_at,
                p.updated_at,
                COALESCE(cc.credit_count, 0) AS credit_count,
                COALESCE(cc.movie_count, 0) AS movie_count,
                COALESCE(cc.actor_count, 0) AS actor_count,
                COALESCE(cc.crew_count, 0) AS crew_count,
{profile_select}
            FROM people p
            LEFT JOIN credit_counts cc ON cc.person_id = p.id
            {profile_join}
            WHERE (%s = '' OR lower(p.name) LIKE %s OR lower(COALESCE(p.known_for, '')) LIKE %s)
              {visible_people_clause}
              AND (
                %s = 'all'
                OR (%s = 'actor' AND COALESCE(cc.actor_count, 0) > 0)
                OR (%s = 'crew' AND COALESCE(cc.crew_count, 0) > 0)
              )
            ORDER BY COALESCE(cc.movie_count, 0) DESC, lower(p.name)
            LIMIT %s
            """,
            (*visibility_params, search, search_like, search_like, role_filter, role_filter, role_filter, limit),
        )
        rows = [dict(row) for row in cur.fetchall()]
    for row in rows:
        profile_url = _next_app().media_asset_public_url(
            {
                "id": row.get("profile_asset_id"),
                "storage_backend": row.pop("profile_storage_backend", None),
                "storage_key": row.pop("profile_storage_key", None),
                "source_url": row.pop("profile_source_url", None),
            }
        )
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if not profile_url:
            profile_url = _next_app().server_usable_image(
                metadata.get("profile_url")
                or metadata.get("profileUrl")
                or metadata.get("photo_url")
                or metadata.get("photoUrl")
            )
        if profile_url:
            row["profile_url"] = profile_url
    return rows


def person_identifier_entities(conn, person_id: UUID) -> list[dict[str, Any]]:
    if not table_exists(conn, "person_identifiers"):
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT provider_id, identifier_type, identifier, created_at
            FROM person_identifiers
            WHERE person_id=%s
            ORDER BY provider_id, identifier_type, identifier
            """,
            (person_id,),
        )
        return cur.fetchall()


def person_tmdb_identifier(person: dict[str, Any], identifiers: list[dict[str, Any]]) -> str:
    for item in identifiers:
        if str(item.get("provider_id") or "").lower() == "tmdb":
            value = clean_text(item.get("identifier"))
            if value:
                return value
    metadata = person.get("metadata") if isinstance(person.get("metadata"), dict) else {}
    return clean_text(metadata.get("tmdb_id") or metadata.get("tmdbId") or "") or ""


def person_localization_entities(conn, person_id: UUID) -> list[dict[str, Any]]:
    if not table_exists(conn, "person_localizations"):
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT lang, biography, created_at, updated_at
            FROM person_localizations
            WHERE person_id=%s
            ORDER BY
                CASE lower(lang)
                    WHEN 'nl-nl' THEN 0
                    WHEN 'nl' THEN 1
                    WHEN 'en-us' THEN 2
                    WHEN 'en' THEN 3
                    ELSE 4
                END,
                lang
            """,
            (person_id,),
        )
        return cur.fetchall()


def person_credit_entities(
    conn,
    person_id: UUID,
    *,
    limit: int = 240,
    actor: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not table_exists(conn, "movie_credits") or not table_exists(conn, "movies"):
        return []
    visibility_clause = ""
    visibility_params: list[Any] = []
    if actor is not None and not _next_app().actor_collection_visibility_scope(actor)["all"]:
        movie_where, visibility_params = _next_app().visible_movie_where_sql(conn, actor, "m")
        visibility_clause = f"AND {movie_where}"
    media_join = table_exists(conn, "entity_media") and table_exists(conn, "media_assets")
    identifiers_join = table_exists(conn, "movie_identifiers")
    tmdb_select = (
        """
                COALESCE(
                    (
                        SELECT mi.identifier
                        FROM movie_identifiers mi
                        WHERE mi.movie_id=m.id
                          AND mi.provider_id='tmdb'
                        ORDER BY mi.identifier_type, mi.identifier
                        LIMIT 1
                    ),
                    m.metadata->>'tmdbId',
                    m.metadata->>'tmdb_id'
                ) AS tmdb_id,
        """
        if identifiers_join
        else """
                COALESCE(m.metadata->>'tmdbId', m.metadata->>'tmdb_id') AS tmdb_id,
        """
    )
    media_select = (
        """
                poster_asset.id AS poster_asset_id,
                poster_asset.storage_backend AS poster_asset_storage_backend,
                poster_asset.storage_key AS poster_asset_storage_key,
                poster_asset.source_url AS poster_asset_source_url,
                backdrop_asset.id AS backdrop_asset_id,
                backdrop_asset.storage_backend AS backdrop_asset_storage_backend,
                backdrop_asset.storage_key AS backdrop_asset_storage_key,
                backdrop_asset.source_url AS backdrop_asset_source_url,
        """
        if media_join
        else """
                NULL AS poster_asset_id,
                NULL AS poster_asset_storage_backend,
                NULL AS poster_asset_storage_key,
                NULL AS poster_asset_source_url,
                NULL AS backdrop_asset_id,
                NULL AS backdrop_asset_storage_backend,
                NULL AS backdrop_asset_storage_key,
                NULL AS backdrop_asset_source_url,
        """
    )
    media_join_sql = (
        """
                LEFT JOIN LATERAL (
                    SELECT ma.id, ma.storage_backend, ma.storage_key, ma.source_url
                    FROM entity_media em
                    JOIN media_assets ma ON ma.id = em.media_id
                    WHERE em.entity_type='movie'
                      AND em.entity_id=m.id
                      AND em.deleted_at IS NULL
                      AND em.hidden_at IS NULL
                      AND ma.kind='poster'
                    ORDER BY em.is_primary DESC, em.sort_order, ma.created_at
                    LIMIT 1
                ) poster_asset ON true
                LEFT JOIN LATERAL (
                    SELECT ma.id, ma.storage_backend, ma.storage_key, ma.source_url
                    FROM entity_media em
                    JOIN media_assets ma ON ma.id = em.media_id
                    WHERE em.entity_type='movie'
                      AND em.entity_id=m.id
                      AND em.deleted_at IS NULL
                      AND em.hidden_at IS NULL
                      AND ma.kind='backdrop'
                    ORDER BY em.is_primary DESC, em.sort_order, ma.created_at
                    LIMIT 1
                ) backdrop_asset ON true
        """
        if media_join
        else ""
    )
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                mc.id,
                mc.movie_id,
                mc.credit_type,
                mc.character,
                mc.job,
                mc.sort_order,
                m.public_id AS movie_public_id,
                m.barcode,
                m.title,
                m.sort_title,
                m.original_title,
                m.year,
                m.release_date,
                m.format,
                m.edition,
{tmdb_select}
                m.metadata->>'poster_url' AS poster_url,
                m.metadata->>'backdrop_url' AS backdrop_url,
{media_select}
                m.updated_at AS movie_updated_at
            FROM movie_credits mc
            JOIN movies m ON m.id = mc.movie_id
            {media_join_sql}
            WHERE mc.person_id=%s
              {visibility_clause}
            ORDER BY
                lower(COALESCE(m.sort_title, m.title)),
                m.year NULLS LAST,
                mc.sort_order,
                mc.credit_type
            LIMIT %s
            """,
            (person_id, *visibility_params, limit),
        )
        return [_next_app().with_preview_media_urls(row) for row in cur.fetchall()]


def person_credit_type_is_acting(credit: dict[str, Any]) -> bool:
    return str(credit.get("credit_type") or credit.get("creditType") or "").lower() in {"actor", "cast"}


def group_person_credits_by_job(credits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for credit in credits:
        label = clean_text(credit.get("job")) or clean_text(credit.get("credit_type")) or "Other"
        key = label.lower()
        groups.setdefault(key, {"job": label, "count": 0, "items": []})
        groups[key]["items"].append(credit)
        groups[key]["count"] += 1
    return sorted(groups.values(), key=lambda group: str(group.get("job") or "").lower())


def person_digital_credit_entities(
    conn,
    person_id: UUID,
    *,
    limit: int = 240,
    actor: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if (
        not table_exists(conn, "movie_credits")
        or not table_exists(conn, "movies")
        or not table_exists(conn, "digital_media_items")
        or not table_exists(conn, "digital_media_sources")
    ):
        return []
    visibility_clause = ""
    visibility_params: list[Any] = []
    if actor is not None and not _next_app().actor_collection_visibility_scope(actor)["all"]:
        movie_where, visibility_params = _next_app().visible_movie_where_sql(conn, actor, "m")
        visibility_clause = f"AND {movie_where}"
    media_join = table_exists(conn, "entity_media") and table_exists(conn, "media_assets")
    identifiers_join = table_exists(conn, "movie_identifiers")
    tmdb_select = (
        """
                    COALESCE(
                        dmi.tmdb_id,
                        (
                            SELECT mi.identifier
                            FROM movie_identifiers mi
                            WHERE mi.movie_id=m.id
                              AND mi.provider_id='tmdb'
                            ORDER BY mi.identifier_type, mi.identifier
                            LIMIT 1
                        ),
                        m.metadata->>'tmdbId',
                        m.metadata->>'tmdb_id'
                    ) AS tmdb_id,
        """
        if identifiers_join
        else """
                    COALESCE(dmi.tmdb_id, m.metadata->>'tmdbId', m.metadata->>'tmdb_id') AS tmdb_id,
        """
    )
    media_select = (
        """
                poster_asset.id AS poster_asset_id,
                poster_asset.storage_backend AS poster_asset_storage_backend,
                poster_asset.storage_key AS poster_asset_storage_key,
                poster_asset.source_url AS poster_asset_source_url,
                backdrop_asset.id AS backdrop_asset_id,
                backdrop_asset.storage_backend AS backdrop_asset_storage_backend,
                backdrop_asset.storage_key AS backdrop_asset_storage_key,
                backdrop_asset.source_url AS backdrop_asset_source_url,
        """
        if media_join
        else """
                NULL AS poster_asset_id,
                NULL AS poster_asset_storage_backend,
                NULL AS poster_asset_storage_key,
                NULL AS poster_asset_source_url,
                NULL AS backdrop_asset_id,
                NULL AS backdrop_asset_storage_backend,
                NULL AS backdrop_asset_storage_key,
                NULL AS backdrop_asset_source_url,
        """
    )
    media_join_sql = (
        """
                LEFT JOIN LATERAL (
                    SELECT ma.id, ma.storage_backend, ma.storage_key, ma.source_url
                    FROM entity_media em
                    JOIN media_assets ma ON ma.id = em.media_id
                    WHERE em.entity_type='movie'
                      AND em.entity_id=m.id
                      AND em.deleted_at IS NULL
                      AND em.hidden_at IS NULL
                      AND ma.kind='poster'
                    ORDER BY em.is_primary DESC, em.sort_order, ma.created_at
                    LIMIT 1
                ) poster_asset ON true
                LEFT JOIN LATERAL (
                    SELECT ma.id, ma.storage_backend, ma.storage_key, ma.source_url
                    FROM entity_media em
                    JOIN media_assets ma ON ma.id = em.media_id
                    WHERE em.entity_type='movie'
                      AND em.entity_id=m.id
                      AND em.deleted_at IS NULL
                      AND em.hidden_at IS NULL
                      AND ma.kind='backdrop'
                    ORDER BY em.is_primary DESC, em.sort_order, ma.created_at
                    LIMIT 1
                ) backdrop_asset ON true
        """
        if media_join
        else ""
    )
    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH ranked AS (
                SELECT
                    dmi.id AS digital_item_id,
                    dmi.external_id,
                    dmi.media_type,
                    dmi.title AS digital_title,
                    dmi.year AS digital_year,
{tmdb_select}
                    dmi.imdb_id,
                    dmi.playback_url,
                    dmi.metadata AS digital_metadata,
                    dmi.synced_at,
                    dms.plugin_id,
                    dms.name AS source_name,
                    dms.source_type,
                    mc.id,
                    mc.movie_id,
                    mc.credit_type,
                    mc.character,
                    mc.job,
                    mc.sort_order,
                    m.public_id AS movie_public_id,
                    m.barcode,
                    m.title,
                    m.sort_title,
                    m.original_title,
                    m.year,
                    m.release_date,
                    m.format,
                    m.edition,
                    m.metadata->>'poster_url' AS poster_url,
                    m.metadata->>'backdrop_url' AS backdrop_url,
{media_select}
                    ROW_NUMBER() OVER (
                        PARTITION BY dms.id, dmi.matched_movie_id
                        ORDER BY
                            CASE
                                WHEN dmi.playback_url IS NULL OR dmi.playback_url = '' THEN 1
                                ELSE 0
                            END,
                            dmi.synced_at DESC,
                            dmi.title,
                            dmi.external_id
                    ) AS source_rank
                FROM movie_credits mc
                JOIN movies m ON m.id = mc.movie_id
                JOIN digital_media_items dmi ON dmi.matched_movie_id = m.id
                JOIN digital_media_sources dms ON dms.id = dmi.source_id
                {media_join_sql}
                WHERE mc.person_id=%s
                  {visibility_clause}
            )
            SELECT *
            FROM ranked
            WHERE source_rank = 1
            ORDER BY source_name, lower(COALESCE(sort_title, title, digital_title)), sort_order
            LIMIT %s
            """,
            (person_id, *visibility_params, limit),
        )
        rows = []
        for row in cur.fetchall():
            data = _next_app().with_preview_media_urls(row)
            data["source"] = data.get("source_name")
            rows.append(data)
        return rows


def person_filmography_entries_from_metadata(metadata: dict[str, Any] | None, *, limit: int = 240) -> list[dict[str, Any]]:
    metadata = metadata if isinstance(metadata, dict) else {}

    def add_entries(raw_items: Any, credit_type: str, result: list[dict[str, Any]], seen: set[tuple[str, str, str, str, str]]) -> None:
        if not isinstance(raw_items, list):
            return
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            media_type = (clean_text(raw.get("media_type") or raw.get("mediaType") or raw.get("type")) or "").lower()
            if media_type and media_type not in {"movie", "film"}:
                continue
            title = clean_text(raw.get("title") or raw.get("name") or raw.get("original_title") or raw.get("originalTitle"))
            if not title:
                continue
            tmdb_id = clean_text(raw.get("tmdb_id") or raw.get("tmdbId") or raw.get("id"))
            release_date = clean_text(raw.get("release_date") or raw.get("releaseDate") or raw.get("first_air_date") or raw.get("firstAirDate"))
            year = clean_text(raw.get("year")) or (release_date[:4] if release_date else "")
            poster_url = _next_app().first_usable_image(raw.get("poster_url"), raw.get("posterUrl"), raw.get("poster"))
            poster_path = clean_text(raw.get("poster_path") or raw.get("posterPath"))
            if not poster_url and poster_path:
                poster_url = f"https://image.tmdb.org/t/p/w342{poster_path}" if poster_path.startswith("/") else _next_app().server_usable_image(poster_path)
            normalized_credit_type = clean_text(raw.get("credit_type") or raw.get("creditType") or credit_type) or credit_type
            character = clean_text(raw.get("character")) or ""
            job = clean_text(raw.get("job")) or ""
            key = (tmdb_id or "", title.lower(), normalized_credit_type.lower(), character.lower(), job.lower())
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "tmdb_id": tmdb_id,
                    "title": title,
                    "year": year,
                    "release_date": release_date,
                    "poster_url": poster_url,
                    "character": character,
                    "job": job,
                    "credit_type": normalized_credit_type,
                    "source_name": clean_text(raw.get("source") or raw.get("provider")) or "TMDb",
                    "in_collection": False,
                    "in_digital": False,
                }
            )
            if len(result) >= limit:
                return

    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    filmography = metadata.get("filmography")
    if isinstance(filmography, dict):
        add_entries(filmography.get("cast"), "actor", result, seen)
        add_entries(filmography.get("crew"), "crew", result, seen)
    else:
        add_entries(filmography, "movie", result, seen)
    combined = metadata.get("combined_credits") or metadata.get("combinedCredits") or metadata.get("credits")
    if isinstance(combined, dict):
        add_entries(combined.get("cast"), "actor", result, seen)
        add_entries(combined.get("crew"), "crew", result, seen)
    add_entries(metadata.get("movie_credits") or metadata.get("movieCredits"), "movie", result, seen)
    add_entries(metadata.get("known_for") or metadata.get("knownFor"), "movie", result, seen)
    return result[:limit]


def person_local_filmography_entries(
    collection_credits: list[dict[str, Any]],
    digital_credits: list[dict[str, Any]] | None = None,
    *,
    limit: int = 240,
) -> list[dict[str, Any]]:
    digital_by_movie: dict[str, list[dict[str, Any]]] = {}
    for item in digital_credits or []:
        if not isinstance(item, dict):
            continue
        movie_id = str(item.get("movie_id") or "")
        if movie_id:
            digital_by_movie.setdefault(movie_id, []).append(item)

    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for credit in collection_credits or []:
        if not isinstance(credit, dict):
            continue
        title = clean_text(credit.get("title") or credit.get("original_title"))
        if not title:
            continue
        tmdb_id = clean_text(credit.get("tmdb_id") or credit.get("tmdbId"))
        credit_type = clean_text(credit.get("credit_type") or credit.get("creditType")) or "movie"
        character = clean_text(credit.get("character")) or ""
        job = clean_text(credit.get("job")) or ""
        key = (tmdb_id or "", title.lower(), credit_type.lower(), character.lower(), job.lower())
        if key in seen:
            continue
        seen.add(key)
        movie_id = str(credit.get("movie_id") or "")
        digital_items = digital_by_movie.get(movie_id) or []
        result.append(
            {
                "tmdb_id": tmdb_id,
                "movie_id": credit.get("movie_id"),
                "movie_public_id": credit.get("movie_public_id"),
                "title": title,
                "year": clean_text(credit.get("year")),
                "release_date": credit.get("release_date"),
                "poster_url": clean_text(credit.get("poster_url")),
                "character": character,
                "job": job,
                "credit_type": credit_type,
                "sort_order": credit.get("sort_order") or 0,
                "source_name": "DiscVault",
                "in_collection": True,
                "format": clean_text(credit.get("format")),
                "edition": clean_text(credit.get("edition")),
                "digital_items": digital_items,
                "in_digital": bool(digital_items),
            }
        )
        if len(result) >= limit:
            break
    return result


def merge_person_filmography_entries(
    external_entries: list[dict[str, Any]],
    local_entries: list[dict[str, Any]],
    *,
    limit: int = 240,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    def key_for(entry: dict[str, Any]) -> tuple[str, str, str, str, str]:
        tmdb_id = clean_text(entry.get("tmdb_id") or entry.get("tmdbId")) or ""
        title = (clean_text(entry.get("title")) or "").lower()
        credit_type = (clean_text(entry.get("credit_type") or entry.get("creditType")) or "").lower()
        character = (clean_text(entry.get("character")) or "").lower()
        job = (clean_text(entry.get("job")) or "").lower()
        return (tmdb_id, title, credit_type, character, job)

    for entry in [*(external_entries or []), *(local_entries or [])]:
        if not isinstance(entry, dict):
            continue
        key = key_for(entry)
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
        if len(result) >= limit:
            break
    return result


def person_filmography_entities(conn, person_metadata: dict[str, Any] | None, *, limit: int = 240) -> list[dict[str, Any]]:
    entries = person_filmography_entries_from_metadata(person_metadata, limit=limit)
    tmdb_ids = sorted({str(entry.get("tmdb_id")) for entry in entries if entry.get("tmdb_id")})
    if not entries or not tmdb_ids:
        return entries

    local_by_tmdb: dict[str, dict[str, Any]] = {}
    if table_exists(conn, "movie_identifiers") and table_exists(conn, "movies"):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    mi.identifier AS tmdb_id,
                    m.id AS movie_id,
                    m.title,
                    m.year,
                    m.format,
                    m.metadata->>'poster_url' AS poster_url
                FROM movie_identifiers mi
                JOIN movies m ON m.id = mi.movie_id
                WHERE mi.provider_id='tmdb'
                  AND mi.identifier = ANY(%s)
                """,
                (tmdb_ids,),
            )
            local_by_tmdb = {str(row["tmdb_id"]): row for row in cur.fetchall()}

    digital_by_tmdb: dict[str, list[dict[str, Any]]] = {}
    if table_exists(conn, "digital_media_items") and table_exists(conn, "digital_media_sources"):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    dmi.tmdb_id,
                    dmi.id AS digital_item_id,
                    dmi.external_id,
                    dmi.title,
                    dmi.year,
                    dmi.playback_url,
                    dms.name AS source_name,
                    dms.plugin_id,
                    dms.source_type
                FROM digital_media_items dmi
                JOIN digital_media_sources dms ON dms.id = dmi.source_id
                WHERE dmi.tmdb_id = ANY(%s)
                ORDER BY
                    dmi.tmdb_id,
                    dms.name,
                    CASE
                        WHEN dmi.playback_url IS NULL OR dmi.playback_url = '' THEN 1
                        ELSE 0
                    END,
                    dmi.synced_at DESC,
                    dmi.title,
                    dmi.external_id
                """,
                (tmdb_ids,),
            )
            for row in cur.fetchall():
                digital_by_tmdb.setdefault(str(row["tmdb_id"]), []).append(row)

    for entry in entries:
        tmdb_id = str(entry.get("tmdb_id") or "")
        local = local_by_tmdb.get(tmdb_id)
        if local:
            entry["movie_id"] = local.get("movie_id")
            entry["in_collection"] = True
            entry["format"] = local.get("format")
            entry["poster_url"] = entry.get("poster_url") or local.get("poster_url")
        digital_items = digital_by_tmdb.get(tmdb_id) or []
        if digital_items:
            digital = digital_items[0]
            entry["digital_items"] = digital_items
            entry["digital_item_id"] = digital.get("digital_item_id")
            entry["playback_url"] = digital.get("playback_url")
            entry["digital_source_name"] = digital.get("source_name")
            entry["digital_source_type"] = digital.get("source_type")
            entry["in_digital"] = True
            entry["source_name"] = digital.get("source_name") or entry.get("source_name")
    return entries


def native_person_biography_fields(
    localizations: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    metadata = metadata if isinstance(metadata, dict) else {}
    fields: dict[str, str] = {}
    by_language: dict[str, str] = {}

    def add(language: str, biography: Any) -> None:
        language = normalize_native_language(language)
        value = clean_text(biography)
        if not language or not value:
            return
        key = f"biography_{language.replace('-', '_')}"
        fields.setdefault(key, value)
        by_language.setdefault(language, value)
        base = language.split("-", 1)[0]
        if base:
            fields.setdefault(f"biography_{base}", value)
            by_language.setdefault(base, value)

    for row in localizations:
        add(row.get("lang"), row.get("biography"))
    for key, value in metadata.items():
        if not str(key).startswith("biography_"):
            continue
        add(str(key).replace("biography_", "", 1), value)
    return fields, by_language


def native_person_detail_payload(detail: dict[str, Any], *, language: str = "") -> dict[str, Any]:
    person = detail.get("person") if isinstance(detail.get("person"), dict) else {}
    metadata = person.get("metadata") if isinstance(person.get("metadata"), dict) else {}
    localizations = detail.get("localizations") if isinstance(detail.get("localizations"), list) else []
    counts = detail.get("counts") if isinstance(detail.get("counts"), dict) else {}
    biography_fields, biography_by_language = native_person_biography_fields(localizations, metadata)
    also_known_as = [
        clean_text(value)
        for value in (metadata.get("alsoKnownAs") or metadata.get("also_known_as") or [])
        if clean_text(value)
    ]
    awards = metadata.get("awards") if isinstance(metadata.get("awards"), list) else []
    award_groups = metadata.get("awardGroups") if isinstance(metadata.get("awardGroups"), list) else []
    person_media = detail.get("personMedia") if isinstance(detail.get("personMedia"), list) else []
    media_items: list[dict[str, Any]] = []
    profile_urls: list[str] = []
    for asset in person_media:
        if not isinstance(asset, dict):
            continue
        url = clean_text(asset.get("url") or asset.get("source_url"))
        if not url or url in profile_urls:
            continue
        media_items.append(
            {
                "id": str(asset.get("id") or ""),
                "url": url,
                "kind": clean_text(asset.get("kind")) or "profile",
                "isPrimary": bool(asset.get("is_primary")),
                "sortOrder": asset.get("sort_order"),
                "provider": clean_text(asset.get("provider_id")),
            }
        )
        profile_urls.append(url)
    if not profile_urls:
        for value in metadata.get("profiles") or []:
            url = clean_text(value)
            if url and url not in profile_urls:
                profile_urls.append(url)
    def _count_value(key: str) -> int:
        try:
            return int(counts.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    mentions_in_vault = _count_value("collection") + _count_value("digital")
    mentions_total = _count_value("filmography")
    mentions = max(mentions_in_vault, mentions_total)
    payload: dict[str, Any] = {
        "id": str(person.get("id") or ""),
        "publicId": clean_text(person.get("public_id")),
        "tmdbId": clean_text(detail.get("tmdbId") or person_tmdb_identifier(person, detail.get("identifiers") or [])),
        "imdbId": clean_text(metadata.get("imdbId") or metadata.get("imdb_id")),
        "name": clean_text(person.get("name")),
        "profileUrl": clean_text(person.get("profile_url") or metadata.get("profileUrl") or metadata.get("profile_url")),
        "birthday": native_date_value(person.get("birth_date") or metadata.get("birthday") or metadata.get("birthDate")),
        "deathday": native_date_value(person.get("death_date") or metadata.get("deathday") or metadata.get("deathDate")),
        "placeOfBirth": clean_text(person.get("place_of_birth") or metadata.get("placeOfBirth") or metadata.get("place_of_birth")),
        "biography": person_biography_value_for_language(localizations, metadata, language),
        "knownFor": clean_text(person.get("known_for") or metadata.get("knownFor") or metadata.get("known_for")),
        "biographyByLanguage": biography_by_language,
        "alsoKnownAs": also_known_as,
        "awards": awards,
        "awardGroups": award_groups,
        "media": media_items,
        "profiles": profile_urls,
        "mentions": mentions,
        "mentionsInVault": mentions_in_vault,
        "mentionsTotal": mentions_total,
        "counts": counts,
    }
    payload.update(biography_fields)
    return payload


def native_digital_platform_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("digital_item_id") or row.get("id") or ""),
        "externalId": clean_text(row.get("external_id")),
        "title": clean_text(row.get("title") or row.get("digital_title")),
        "year": clean_text(row.get("year") or row.get("digital_year")),
        "platform": clean_text(row.get("source_name")),
        "sourceName": clean_text(row.get("source_name")),
        "sourceType": clean_text(row.get("source_type")),
        "pluginId": clean_text(row.get("plugin_id")),
        "playbackUrl": clean_text(row.get("playback_url")),
    }


def native_person_filmography_item(entry: dict[str, Any]) -> dict[str, Any]:
    digital_items = [
        native_digital_platform_item(item)
        for item in (entry.get("digital_items") if isinstance(entry.get("digital_items"), list) else [])
        if isinstance(item, dict)
    ]
    if not digital_items and (entry.get("playback_url") or entry.get("digital_item_id")):
        digital_items = [native_digital_platform_item(entry)]
    platform_urls = [
        {"platform": item.get("platform") or item.get("sourceName") or "", "url": item.get("playbackUrl") or ""}
        for item in digital_items
        if item.get("playbackUrl")
    ]
    credit_type = clean_text(entry.get("credit_type") or entry.get("creditType"))
    acting = person_credit_type_is_acting({"credit_type": credit_type}) or bool(clean_text(entry.get("character")))
    department = "cast" if acting else "crew"
    return {
        "tmdbId": clean_text(entry.get("tmdb_id") or entry.get("tmdbId")),
        "movieId": str(entry.get("movie_id") or entry.get("movieId") or ""),
        "title": clean_text(entry.get("title")),
        "year": clean_text(entry.get("year")),
        "releaseDate": native_date_value(entry.get("release_date") or entry.get("releaseDate")),
        "posterUrl": clean_text(entry.get("poster_url") or entry.get("posterUrl")),
        "character": clean_text(entry.get("character")),
        "job": clean_text(entry.get("job")),
        "department": department,
        "creditType": credit_type or ("actor" if acting else "crew"),
        "inCollection": bool(entry.get("in_collection") or entry.get("inCollection")),
        "inDigital": bool(entry.get("in_digital") or entry.get("inDigital") or digital_items),
        "format": clean_text(entry.get("format")),
        "sourceName": clean_text(entry.get("source_name") or entry.get("sourceName")),
        "digitalItems": digital_items,
        "digitalPlatformUrls": platform_urls,
    }


def native_person_filmography_payload(detail: dict[str, Any], *, language: str = "") -> dict[str, Any]:
    person = native_person_detail_payload(detail, language=language)
    items = [native_person_filmography_item(entry) for entry in detail.get("filmography") or [] if isinstance(entry, dict)]
    cast = [item for item in items if item.get("department") == "cast"]
    crew = [item for item in items if item.get("department") == "crew"]
    return {
        "personId": person["id"],
        "tmdbId": person.get("tmdbId") or "",
        "language": normalize_native_language(language),
        "cast": cast,
        "crew": crew,
        "items": items,
        "counts": {"cast": len(cast), "crew": len(crew), "total": len(items)},
    }


def person_detail_entity(conn, person_id: UUID, actor: dict[str, Any] | None = None) -> dict[str, Any] | None:
    person = person_entity(conn, person_id)
    if not person:
        return None
    localizations = person_localization_entities(conn, person_id)
    person["biography"] = person_biography_value(localizations, person.get("metadata"))
    identifiers = person_identifier_entities(conn, person_id)
    collection_credits = person_credit_entities(conn, person_id, actor=actor)
    acting_credits = [credit for credit in collection_credits if person_credit_type_is_acting(credit)]
    crew_credits = [credit for credit in collection_credits if not person_credit_type_is_acting(credit)]
    digital_credits = person_digital_credit_entities(conn, person_id, actor=actor)
    external_filmography = person_filmography_entities(conn, person.get("metadata"))
    local_filmography = person_local_filmography_entries(collection_credits, digital_credits)
    filmography = merge_person_filmography_entries(external_filmography, local_filmography)
    person_media: list[dict[str, Any]] = []
    if table_exists(conn, "entity_media") and table_exists(conn, "media_assets"):
        try:
            person_media = _next_app().entity_media_asset_entities(conn, "person", person_id)
        except Exception:
            person_media = []
    return {
        "person": person,
        "identifiers": identifiers,
        "tmdbId": person_tmdb_identifier(person, identifiers),
        "localizations": localizations,
        "credits": collection_credits,
        "collectionCredits": collection_credits,
        "actingCredits": acting_credits,
        "crewCredits": crew_credits,
        "groupedCrew": group_person_credits_by_job(crew_credits),
        "digitalCredits": digital_credits,
        "filmography": filmography,
        "externalFilmography": external_filmography,
        "localFilmography": local_filmography,
        "personMedia": person_media,
        "counts": {
            "collection": len(collection_credits),
            "acting": len(acting_credits),
            "crew": len(crew_credits),
            "digital": len(digital_credits),
            "filmography": len(filmography),
            "externalFilmography": len(external_filmography),
            "localFilmography": len(local_filmography),
        },
    }


# TMDb recommends not re-fetching a person's data more often than every few
# months, since biographical/profile data changes rarely. Fixed per TMDb's own
# guidance -- not an admin-configurable setting.
PERSON_METADATA_CACHE_DAYS = 180


def person_metadata_is_fresh(fetched_at: Any) -> bool:
    parsed = _next_app().parse_client_timestamp(fetched_at)
    if parsed is None:
        return False
    return (datetime.now(timezone.utc) - parsed) < timedelta(days=PERSON_METADATA_CACHE_DAYS)


MOVIE_METADATA_PERSON_REFRESH_LIMIT = 12
MOVIE_METADATA_PERSON_REFRESH_CREW_JOBS = {
    "director",
    "writer",
    "screenplay",
    "producer",
    "executive producer",
    "cinematographer",
    "director of photography",
    "composer",
}


def normalize_movie_metadata_person_refresh_scope(value: Any) -> str:
    return "crew" if str(value or "").strip().lower() == "crew" else "all"


def movie_metadata_person_refresh_empty(
    *,
    requested: bool,
    dry_run: bool,
    limit: int = MOVIE_METADATA_PERSON_REFRESH_LIMIT,
    scope: str = "all",
) -> dict[str, Any]:
    return {
        "requested": requested,
        "dryRun": dry_run,
        "limit": limit,
        "scope": normalize_movie_metadata_person_refresh_scope(scope),
        "considered": 0,
        "selected": 0,
        "previewed": 0,
        "refreshed": 0,
        "cached": 0,
        "skipped": 0,
        "errorCount": 0,
        "errors": [],
        "people": [],
    }


def normalize_person_refresh_job(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def select_movie_metadata_person_refresh_credits(
    credits: list[dict[str, Any]],
    *,
    limit: int = MOVIE_METADATA_PERSON_REFRESH_LIMIT,
    scope: str = "all",
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    normalized_scope = normalize_movie_metadata_person_refresh_scope(scope)

    def add_credit(credit: dict[str, Any]) -> None:
        if len(selected) >= limit:
            return
        person_id = str(credit.get("person_id") or credit.get("personId") or "").strip()
        if not person_id or person_id in seen:
            return
        seen.add(person_id)
        selected.append(credit)

    priority_crew = [
        credit
        for credit in credits
        if not person_credit_type_is_acting(credit)
        and normalize_person_refresh_job(credit.get("job")) in MOVIE_METADATA_PERSON_REFRESH_CREW_JOBS
    ]
    cast = [credit for credit in credits if person_credit_type_is_acting(credit)]
    other_crew = [credit for credit in credits if not person_credit_type_is_acting(credit) and credit not in priority_crew]
    buckets = (priority_crew, other_crew) if normalized_scope == "crew" else (priority_crew, cast, other_crew)
    for bucket in buckets:
        for credit in bucket:
            add_credit(credit)
    return selected


def movie_metadata_person_refresh_skipped_people(selected: list[dict[str, Any]], reason: str) -> list[dict[str, Any]]:
    return [
        {
            "personId": str(credit.get("person_id") or credit.get("personId") or ""),
            "name": credit.get("name"),
            "creditType": credit.get("credit_type") or credit.get("creditType"),
            "job": credit.get("job"),
            "character": credit.get("character"),
            "status": "skipped",
            "reason": reason,
        }
        for credit in selected
    ]


def movie_metadata_person_refresh_cached_people(selected: list[dict[str, Any]], reason: str) -> list[dict[str, Any]]:
    return [
        {
            "personId": str(credit.get("person_id") or credit.get("personId") or ""),
            "name": credit.get("name"),
            "creditType": credit.get("credit_type") or credit.get("creditType"),
            "job": credit.get("job"),
            "character": credit.get("character"),
            "status": "cached",
            "reason": reason,
        }
        for credit in selected
    ]


def select_movie_metadata_person_refresh_credits_needing_fetch(
    selected: list[dict[str, Any]],
    *,
    force: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split already-selected credits into (needs_fetch, cached).

    A credit is considered cached when its joined ``person_metadata`` (already
    selected by ``movie_credit_entities()``, no extra query) has a
    ``person_metadata_fetched_at`` within ``PERSON_METADATA_CACHE_DAYS`` and the
    caller did not request a forced live refresh.
    """
    if force:
        return list(selected), []
    needs_fetch: list[dict[str, Any]] = []
    cached: list[dict[str, Any]] = []
    for credit in selected:
        person_metadata = credit.get("person_metadata")
        fetched_at = person_metadata.get("person_metadata_fetched_at") if isinstance(person_metadata, dict) else None
        if fetched_at and person_metadata_is_fresh(fetched_at):
            cached.append(credit)
        else:
            needs_fetch.append(credit)
    return needs_fetch, cached


def register_next_people_routes(flask_app: Flask, *, connect) -> None:  # pragma: no cover - Flask integration

    """Register the people routes on *flask_app*.

    ``connect`` must be a callable that returns a database connection context
    manager (i.e. the ``connect`` closure defined in ``next_app.py``).
    """

    _app = _next_app()

    @flask_app.get("/api/next/people")
    def people_list():
        query = clean_text(request.args.get("q"))
        role = clean_text(request.args.get("role")) or "all"
        limit = int(request.args.get("limit") or 120)
        with connect() as conn:
            actor = _app.require_any_next_permission(
                conn,
                ("collection.view", "collection.view_own", "collection.view_group", "collection.view_all"),
            )
            if not table_exists(conn, "people"):
                raise NextApiError("People table is not available", 503)
            people = people_list_entities(conn, query=query, role=role, limit=limit, actor=actor)
        return response({"status": "ok", "people": people, "query": query, "role": role, "limit": limit})

    @flask_app.get("/api/next/people/<person_id>")
    def person_detail(person_id: str):
        person_uuid = parse_uuid(person_id, "personId")
        language = request.args.get("language") or request.args.get("lang") or ""
        with connect() as conn:
            actor = _app.require_any_next_permission(
                conn,
                ("collection.view", "collection.view_own", "collection.view_group", "collection.view_all"),
            )
            if not table_exists(conn, "people"):
                raise NextApiError("People table is not available", 503)
            if not _app.actor_can_view_person(conn, actor, person_uuid):
                raise NextApiError("Person not found", 404)
            detail = person_detail_entity(conn, person_uuid, actor=actor)
        if not detail:
            raise NextApiError("Person not found", 404)
        native_person = native_person_detail_payload(detail, language=language)
        detail.update(native_person)
        return response({"status": "ok", "detail": detail, "person": native_person})

    @flask_app.get("/api/next/people/<person_id>/filmography")
    def person_filmography(person_id: str):
        person_uuid = parse_uuid(person_id, "personId")
        language = request.args.get("language") or request.args.get("lang") or ""
        refresh_info: dict[str, Any] | None = None
        with connect() as conn:
            actor = _app.require_any_next_permission(
                conn,
                ("collection.view", "collection.view_own", "collection.view_group", "collection.view_all"),
            )
            if not table_exists(conn, "people"):
                raise NextApiError("People table is not available", 503)
            if not _app.actor_can_view_person(conn, actor, person_uuid):
                raise NextApiError("Person not found", 404)
            detail = person_detail_entity(conn, person_uuid, actor=actor)
            person_metadata = ((detail or {}).get("person") or {}).get("metadata") if detail else {}
            person_metadata = person_metadata if isinstance(person_metadata, dict) else {}
            metadata_entries = person_filmography_entries_from_metadata(person_metadata)
            filmography_checked = bool(
                person_metadata.get("filmography_fetched_at")
                or person_metadata.get("filmography_source_ref")
                or person_metadata.get("filmography_source")
            )
            if detail and detail.get("tmdbId") and not metadata_entries and not filmography_checked:
                try:
                    refresh = _app.refresh_person_filmography(conn, person_uuid, actor=actor)
                    detail = refresh.get("detail") or detail
                    refresh_info = {
                        "status": "refreshed",
                        "plugin": refresh.get("plugin"),
                        "execution": refresh.get("execution"),
                    }
                except NextApiError as exc:
                    refresh_info = {
                        "status": "skipped",
                        "reason": str(exc),
                        "statusCode": exc.status_code,
                    }
        if not detail:
            raise NextApiError("Person not found", 404)
        filmography = native_person_filmography_payload(detail, language=language)
        return response({"status": "ok", "filmography": filmography, "refresh": refresh_info, **filmography})
