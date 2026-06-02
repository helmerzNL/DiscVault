"""DiscVault Next background worker.

The worker is intentionally small at this stage: it proves the runtime pattern
for long-running work without coupling metadata providers or imports into API
requests. Jobs are claimed with PostgreSQL row locks so multiple workers can run
without processing the same job twice.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import sys
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

try:
    from .next_import import ImportError as NextImportError
    from .next_import import NextImporter
    from .next_import import clean_text
    from .next_movievault_connection import MOVIEVAULT_PLUGIN_ID
    from .next_movievault_connection import is_movievault_plugin
    from .next_movievault_connection import movievault_plugin_context
    from .next_plugin_runtime import run_plugin_entrypoint
    from .next_metadata import METADATA_REFRESH_JOB_TYPE
    from .next_metadata import refresh_movie_metadata
    from .next_backup import BACKUP_RESTORE_JOB_TYPE
    from .next_backup import BackupError as NextBackupError
    from .next_backup import restore_functional_backup
except ImportError:  # pragma: no cover - supports python next_worker.py
    from next_import import ImportError as NextImportError
    from next_import import NextImporter
    from next_import import clean_text
    from next_movievault_connection import MOVIEVAULT_PLUGIN_ID
    from next_movievault_connection import is_movievault_plugin
    from next_movievault_connection import movievault_plugin_context
    from next_plugin_runtime import run_plugin_entrypoint
    from next_metadata import METADATA_REFRESH_JOB_TYPE
    from next_metadata import refresh_movie_metadata
    from next_backup import BACKUP_RESTORE_JOB_TYPE
    from next_backup import BackupError as NextBackupError
    from next_backup import restore_functional_backup


STOP = False


def database_url() -> str:
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is required for the DiscVault Next worker.")
    return value


def connect():
    import psycopg

    return psycopg.connect(database_url(), row_factory=dict_row, autocommit=False)


def background_jobs_ready(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.background_jobs') AS table_name")
        row = cur.fetchone()
    return bool(row and row.get("table_name"))


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
    values: dict[str, Any] = {}
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
    for name, ref in refs.items():
        key = ref.get("key") if isinstance(ref, dict) else None
        if key in by_key:
            values[str(name)] = by_key[key]
    return values


def plugin_record(conn, plugin_id: str) -> dict[str, Any]:
    if not table_exists(conn, "plugins"):
        return {
            "id": plugin_id,
            "name": plugin_id,
            "enabled": True,
            "categories": [],
            "capabilities": [],
            "manifest": {},
        }
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, enabled, categories, capabilities, manifest
            FROM plugins
            WHERE id=%s
            """,
            (plugin_id,),
        )
        row = cur.fetchone()
    if not row:
        raise RuntimeError(f"Plugin not found: {plugin_id}")
    return row


def plugin_requires_config(plugin: dict[str, Any], config: dict[str, Any], entrypoint: str) -> bool:
    if entrypoint in {"health_check", "discover_library", "playback_deeplink"}:
        return False
    if is_movievault_plugin(str(plugin.get("id") or "")):
        return False
    manifest = plugin.get("manifest") or {}
    return bool(manifest.get("requiresSecrets")) and not bool(config.get("secretsConfigured"))


def plugin_execution_context_from_db(
    conn,
    plugin_id: str,
    entrypoint: str,
    queued_actor: dict[str, Any] | None,
) -> dict[str, Any]:
    plugin = plugin_record(conn, plugin_id)
    if entrypoint != "health_check" and not plugin.get("enabled"):
        raise RuntimeError("Plugin must be enabled before execution")
    config = plugin_config_from_db(conn, plugin_id)
    if plugin_requires_config(plugin, config, entrypoint):
        raise RuntimeError("Plugin configuration is incomplete")
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
        "actor": queued_actor or {"id": None, "username": None, "role": None},
    }
    return movievault_plugin_context(
        conn,
        plugin_id,
        context,
        ensure_token=is_movievault_plugin(plugin_id) and entrypoint != "health_check",
        actor_id=(queued_actor or {}).get("id") if queued_actor else None,
    )


def claim_job(conn, worker_id: str) -> dict[str, Any] | None:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM background_jobs
                WHERE status='pending'
                ORDER BY created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            row = cur.fetchone()
            if not row:
                return None
            cur.execute(
                """
                UPDATE background_jobs
                SET status='running',
                    started_at=now(),
                    result = result || %s
                WHERE id=%s
                RETURNING
                    id,
                    job_type,
                    status,
                    payload,
                    result,
                    created_at,
                    started_at
                """,
                (Jsonb({"workerId": worker_id}), row["id"]),
            )
            return cur.fetchone()


def complete_job(conn, job_id: UUID, result: dict[str, Any]) -> None:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE background_jobs
                SET status='completed',
                    result=%s,
                    error=NULL,
                    finished_at=now()
                WHERE id=%s
                """,
                (Jsonb(json_ready(result)), job_id),
            )


def fail_job(conn, job_id: UUID, error: str, result: dict[str, Any] | None = None) -> None:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE background_jobs
                SET status='failed',
                    result=%s,
                    error=%s,
                    finished_at=now()
                WHERE id=%s
                """,
                (Jsonb(json_ready(result or {})), error, job_id),
            )


def process_job(job: dict[str, Any], worker_id: str) -> dict[str, Any]:
    job_type = job["job_type"]
    payload = job.get("payload") or {}

    if job_type == "sync.noop":
        return {
            "workerId": worker_id,
            "handled": True,
            "jobType": job_type,
            "message": payload.get("message") or "No operation executed.",
            "echo": payload,
        }

    if job_type == "migration.import_sqlite":
        return process_sqlite_import(payload, worker_id)

    if job_type == "plugin.execute":
        return process_plugin_execute(payload, worker_id)

    if job_type == METADATA_REFRESH_JOB_TYPE:
        return process_metadata_refresh(payload, worker_id)

    if job_type == BACKUP_RESTORE_JOB_TYPE:
        return process_functional_restore(payload, worker_id)

    raise RuntimeError(f"Unsupported job type: {job_type}")


def process_metadata_refresh(payload: dict[str, Any], worker_id: str) -> dict[str, Any]:
    movie_id = clean_text(payload.get("movieId") or payload.get("movie_id"))
    if not movie_id:
        raise RuntimeError("movieId is required for metadata refresh jobs")
    dry_run = bool_value(payload.get("dryRun", payload.get("dry_run")), default=False)
    actor = payload.get("requestedBy") or payload.get("requested_by") or {}
    if not isinstance(actor, dict):
        actor = {}
    with connect() as conn:
        result = refresh_movie_metadata(conn, movie_id, dry_run=dry_run, actor=actor)
    return {
        "workerId": worker_id,
        "handled": True,
        "jobType": METADATA_REFRESH_JOB_TYPE,
        "movieId": movie_id,
        "dryRun": dry_run,
        "result": result,
    }


def process_functional_restore(payload: dict[str, Any], worker_id: str) -> dict[str, Any]:
    backup_zip = Path(str(payload.get("backupZip") or "")).expanduser()
    data_dir = Path(str(payload.get("dataDir") or "/data")).expanduser()
    if not backup_zip.exists() or not backup_zip.is_file():
        raise RuntimeError(f"Backup ZIP not found: {backup_zip}")
    if not data_dir.exists() or not data_dir.is_dir():
        raise RuntimeError(f"DiscVault data directory not found: {data_dir}")
    try:
        with connect() as conn:
            summary = restore_functional_backup(
                conn,
                backup_zip,
                data_dir=data_dir,
                include_personal_lists=bool_value(payload.get("includePersonalLists"), default=False),
                personal_list_user_id=payload.get("personalListUserId"),
                group_resolution=payload.get("groupResolution") if isinstance(payload.get("groupResolution"), dict) else {},
            )
    except NextBackupError as exc:
        raise RuntimeError(str(exc)) from exc
    return {
        "workerId": worker_id,
        "handled": True,
        "jobType": BACKUP_RESTORE_JOB_TYPE,
        "phase": "completed",
        "source": {
            "backupZip": str(backup_zip),
            "dataDir": str(data_dir),
        },
        "summary": summary,
    }


def process_plugin_execute(payload: dict[str, Any], worker_id: str) -> dict[str, Any]:
    plugin_id = clean_text(payload.get("pluginId"))
    entrypoint = clean_text(payload.get("entrypoint"))
    if not plugin_id:
        raise RuntimeError("pluginId is required for plugin execution jobs")
    if not entrypoint:
        raise RuntimeError("entrypoint is required for plugin execution jobs")

    execution_payload = payload.get("payload") or {}
    if not isinstance(execution_payload, dict):
        raise RuntimeError("Plugin execution payload must be an object")
    queued_actor = payload.get("requestedBy") or payload.get("requested_by") or None
    if not isinstance(queued_actor, dict):
        queued_actor = None
    legacy_context = payload.get("context") or {}
    if not isinstance(legacy_context, dict):
        legacy_context = {}
    with connect() as conn:
        context = plugin_execution_context_from_db(conn, plugin_id, entrypoint, queued_actor)
    if legacy_context:
        # Backward compatibility for jobs queued before context was loaded at worker time.
        for key, value in legacy_context.items():
            if key not in {"settings", "secrets", "actor"} and key not in context:
                context[key] = value
    context = {
        **context,
        "queued": True,
        "workerId": worker_id,
    }
    execution = run_plugin_entrypoint(plugin_id, entrypoint, execution_payload, context)
    if execution.get("status") != "ok":
        raise RuntimeError(str(execution.get("error") or execution.get("state") or "Plugin execution failed"))
    persist_summary = {}
    if entrypoint == "sync_library":
        persist_summary = persist_digital_sync(plugin_id, execution.get("result") or {})
    elif entrypoint == "sync_personal_lists":
        persist_summary = persist_personal_list_sync(plugin_id, execution.get("result") or {}, queued_actor)
    elif entrypoint == "import_source":
        reviewed_result = apply_collection_import_review(
            execution.get("result") or {},
            payload.get("importReview") or payload.get("import_review") or {},
        )
        execution = {**execution, "result": reviewed_result}
        persist_summary = persist_collection_import(plugin_id, reviewed_result, queued_actor)
    return {
        "workerId": worker_id,
        "handled": True,
        "jobType": "plugin.execute",
        "pluginId": plugin_id,
        "entrypoint": entrypoint,
        "execution": execution,
        "persistence": persist_summary,
    }


def apply_collection_import_review(result: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict) or not isinstance(review, dict):
        return result
    items = result.get("items")
    decisions = review.get("decisions")
    if not isinstance(items, list) or not isinstance(decisions, list):
        return result
    decision_by_index: dict[int, dict[str, Any]] = {}
    for raw in decisions:
        if not isinstance(raw, dict):
            continue
        try:
            index = int(raw.get("index"))
        except (TypeError, ValueError):
            continue
        action = clean_text(raw.get("action"))
        if index >= 1 and action in {"import", "update", "create", "skip"}:
            decision_by_index[index] = raw
    if not decision_by_index:
        return result

    def apply_review_values(item: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
        reviewed = dict(item)

        def apply_value(target: str, value: Any, *, only_blank: bool = False) -> None:
            text = clean_text(value)
            if not text:
                return
            if only_blank and clean_text(reviewed.get(target)):
                return
            reviewed[target] = text

        def apply_match(match: dict[str, Any], *, only_blank: bool) -> None:
            apply_value("title", match.get("title"), only_blank=only_blank)
            apply_value("year", match.get("year"), only_blank=only_blank)
            apply_value("format", match.get("format"), only_blank=only_blank)
            apply_value("overview", match.get("overview"), only_blank=only_blank)
            apply_value("posterUrl", match.get("posterUrl"), only_blank=only_blank)
            apply_value("backdropUrl", match.get("backdropUrl"), only_blank=only_blank)
            identifiers = match.get("identifiers") if isinstance(match.get("identifiers"), dict) else {}
            tmdb_id = match.get("tmdbId") or match.get("tmdb_id") or identifiers.get("tmdb")
            imdb_id = match.get("imdbId") or match.get("imdb_id") or identifiers.get("imdb")
            apply_value("tmdbId", tmdb_id, only_blank=only_blank)
            apply_value("imdbId", imdb_id, only_blank=only_blank)

        metadata_match = decision.get("metadataMatch")
        manual_override = decision.get("manualOverride")
        if isinstance(metadata_match, dict):
            apply_match(metadata_match, only_blank=False)
            reviewed["importReviewMatch"] = {
                key: value
                for key, value in metadata_match.items()
                if value not in (None, "", [], {})
            }
        if isinstance(manual_override, dict):
            apply_match(manual_override, only_blank=False)
            reviewed["importReviewManualOverride"] = {
                key: value
                for key, value in manual_override.items()
                if value not in (None, "", [], {})
            }
        return reviewed

    kept: list[Any] = []
    skipped: list[dict[str, Any]] = []
    reviewed = 0
    for index, item in enumerate(items, start=1):
        decision = decision_by_index.get(index, {"action": "import"})
        action = clean_text(decision.get("action")) or "import"
        if action == "skip":
            skipped.append(
                {
                    "index": index,
                    "title": item.get("title") if isinstance(item, dict) else None,
                    "reason": "review_skip",
                }
            )
            continue
        if isinstance(item, dict):
            item = apply_review_values(item, decision)
            item = {**item, "importReviewAction": action}
            if decision.get("metadataMatch") or decision.get("manualOverride"):
                reviewed += 1
        kept.append(item)
    counts = dict(result.get("counts") or {})
    counts["movies"] = len(kept)
    counts["reviewSkipped"] = len(skipped)
    counts["reviewMatched"] = reviewed
    return {
        **result,
        "items": kept,
        "counts": counts,
        "review": {
            "decisions": len(decision_by_index),
            "matched": reviewed,
            "skipped": skipped[:100],
        },
    }


def stable_uuid(seed: str) -> UUID:
    import uuid

    return uuid.uuid5(uuid.UUID("0e3a8e91-caf6-4b6e-911f-59d5f4cf8d9e"), seed)


def source_public_id(prefix: str, value: str, *, fallback: str) -> str:
    raw = clean_text(value) or fallback
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw).strip("-").lower()[:90]
    return f"{prefix}-{safe or fallback}"


def import_movie_existing_id(conn, item: dict[str, Any], *, public_id: str = "") -> UUID | None:
    barcode = clean_text(item.get("barcode"))
    with conn.cursor() as cur:
        public_id = clean_text(public_id)
        if public_id:
            cur.execute("SELECT id FROM movies WHERE public_id=%s LIMIT 1", (public_id,))
            row = cur.fetchone()
            if row:
                return row["id"]
        if barcode:
            cur.execute("SELECT id FROM movies WHERE barcode=%s LIMIT 1", (barcode,))
            row = cur.fetchone()
            if row:
                return row["id"]
        identifiers = {
            "tmdb": clean_text(item.get("tmdbId") or item.get("tmdb_id")),
            "imdb": clean_text(item.get("imdbId") or item.get("imdb_id")),
        }
        if table_exists(conn, "movie_identifiers"):
            for provider, identifier in identifiers.items():
                if not identifier:
                    continue
                cur.execute(
                    """
                    SELECT movie_id
                    FROM movie_identifiers
                    WHERE provider_id=%s AND identifier_type='movie_id' AND identifier=%s
                    LIMIT 1
                    """,
                    (provider, identifier),
                )
                row = cur.fetchone()
                if row:
                    return row["movie_id"]
        title = clean_text(item.get("title"))
        year = clean_text(item.get("year"))
        if title and year:
            cur.execute(
                """
                SELECT id
                FROM movies
                WHERE lower(title)=lower(%s) AND year=%s
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (title, year),
            )
            row = cur.fetchone()
            if row:
                return row["id"]
    return None


def import_movie_metadata(item: dict[str, Any], plugin_id: str) -> dict[str, Any]:
    metadata = {
        "import_source": plugin_id,
        "source_provider": item.get("sourceProvider") or plugin_id,
        "source_file": item.get("sourceFile"),
        "source_url": item.get("sourceUrl"),
        "genre": item.get("genre"),
        "director": item.get("director"),
        "actor": item.get("actor"),
        "poster_url": item.get("posterUrl") or item.get("poster_url"),
        "backdrop_url": item.get("backdropUrl") or item.get("backdrop_url"),
        "personal": item.get("personal"),
    }
    return {key: value for key, value in metadata.items() if value not in (None, "", [], {})}


def import_item_container_specs(item: dict[str, Any]) -> list[dict[str, str]]:
    specs: list[dict[str, str]] = []
    for container_type, title in (
        ("collection", item.get("collectionTitle") or item.get("collection") or item.get("collection_title")),
        ("box_set", item.get("boxSetTitle") or item.get("boxSet") or item.get("box_set") or item.get("box_set_title")),
        ("vault", item.get("vaultTitle") or item.get("vault") or item.get("vault_title")),
    ):
        clean_title = clean_text(title)
        if clean_title:
            specs.append({"containerType": container_type, "title": clean_title})
    for raw in item.get("containers") or []:
        if not isinstance(raw, dict):
            continue
        container_type = clean_text(raw.get("containerType") or raw.get("type") or raw.get("container_type"))
        if container_type in {"boxset", "box-set"}:
            container_type = "box_set"
        if container_type not in {"collection", "box_set", "vault"}:
            continue
        title = clean_text(raw.get("title") or raw.get("name"))
        if title:
            specs.append({"containerType": container_type, "title": title})
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
    for spec in specs:
        key = (spec["containerType"], spec["title"].casefold())
        if key in seen:
            continue
        seen.add(key)
        unique.append(spec)
    return unique


def import_container_id(plugin_id: str, source_hash: str, container_type: str, title: str) -> UUID:
    return stable_uuid(f"container-import:{plugin_id}:{source_hash}:{container_type}:{title.casefold()}")


def upsert_import_container(
    conn,
    *,
    plugin_id: str,
    source_hash: str,
    container_type: str,
    title: str,
    source_path: str,
    source_kind: str,
) -> tuple[UUID, bool]:
    container_id = import_container_id(plugin_id, source_hash, container_type, title)
    public_id = source_public_id(
        f"import-{container_type}",
        f"{plugin_id}-{source_hash[:12]}-{title}",
        fallback=str(container_id),
    )
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM containers WHERE id=%s", (container_id,))
        exists = bool(cur.fetchone())
        cur.execute(
            """
            INSERT INTO containers (
                id, public_id, container_type, title, metadata, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, now(), now())
            ON CONFLICT (id) DO UPDATE SET
                title=EXCLUDED.title,
                metadata=containers.metadata || EXCLUDED.metadata,
                updated_at=now()
            """,
            (
                container_id,
                public_id,
                container_type,
                title,
                Jsonb(
                    json_ready(
                        {
                            "import_source": plugin_id,
                            "source_kind": source_kind,
                            "source_path": source_path,
                            "source_hash": source_hash,
                        }
                    )
                ),
            ),
        )
    return container_id, not exists


def link_import_movie_to_container(
    conn,
    *,
    container_id: UUID,
    container_type: str,
    movie_id: UUID,
    sort_order: int,
) -> bool:
    if container_type == "collection":
        if not table_exists(conn, "collection_items"):
            return False
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO collection_items (collection_id, item_type, item_id, sort_order, created_at)
                VALUES (%s, 'movie', %s, %s, now())
                ON CONFLICT (collection_id, item_type, item_id) DO UPDATE SET sort_order=EXCLUDED.sort_order
                """,
                (container_id, movie_id, sort_order),
            )
        return True
    if not table_exists(conn, "container_movies"):
        return False
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO container_movies (container_id, movie_id, sort_order, created_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (container_id, movie_id) DO UPDATE SET sort_order=EXCLUDED.sort_order
            """,
            (container_id, movie_id, sort_order),
        )
    return True


def import_year(value: Any) -> str:
    raw = clean_text(value)
    for idx in range(0, max(len(raw) - 3, 0)):
        candidate = raw[idx : idx + 4]
        if candidate.isdigit() and 1800 <= int(candidate) <= 2200:
            return candidate
    return ""


def import_release_date(value: Any) -> str | None:
    raw = clean_text(value)
    if not raw or re.fullmatch(r"\d{4}", raw):
        return None
    normalized = raw.replace("/", "-").strip()
    for pattern, order in (
        (r"^(\d{4})-(\d{1,2})-(\d{1,2})$", "ymd"),
        (r"^(\d{1,2})-(\d{1,2})-(\d{4})$", "dmy"),
    ):
        match = re.match(pattern, normalized)
        if not match:
            continue
        parts = [int(part) for part in match.groups()]
        year, month, day = parts if order == "ymd" else (parts[2], parts[1], parts[0])
        try:
            if not 1800 <= year <= 2200:
                return None
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    return None


def upsert_import_movie(conn, plugin_id: str, item: dict[str, Any]) -> tuple[UUID, bool]:
    title = clean_text(item.get("title"))
    if not title:
        raise RuntimeError("Import item is missing title")
    external_id = clean_text(item.get("externalId") or item.get("external_id"))
    raw_release_date = item.get("releaseDate") or item.get("release_date")
    year = clean_text(item.get("year")) or import_year(raw_release_date)
    release_date = import_release_date(raw_release_date)
    seed = f"{plugin_id}:{external_id or title}:{year}:{clean_text(item.get('barcode'))}"
    proposed_public_id = source_public_id(f"import-{plugin_id}", external_id or seed, fallback=seed)
    existing_id = import_movie_existing_id(conn, item, public_id=proposed_public_id)
    movie_id = existing_id or stable_uuid(f"movie:{seed}")
    public_id = proposed_public_id if external_id else source_public_id(f"import-{plugin_id}", str(movie_id), fallback=str(movie_id))
    metadata = import_movie_metadata(item, plugin_id)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO movies (
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
                country,
                language,
                runtime_minutes,
                overview,
                rating,
                metadata,
                created_at,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
            ON CONFLICT (id) DO UPDATE SET
                barcode=COALESCE(EXCLUDED.barcode, movies.barcode),
                title=COALESCE(EXCLUDED.title, movies.title),
                sort_title=COALESCE(EXCLUDED.sort_title, movies.sort_title),
                original_title=COALESCE(EXCLUDED.original_title, movies.original_title),
                year=COALESCE(EXCLUDED.year, movies.year),
                release_date=COALESCE(EXCLUDED.release_date, movies.release_date),
                format=COALESCE(EXCLUDED.format, movies.format),
                edition=COALESCE(EXCLUDED.edition, movies.edition),
                country=COALESCE(EXCLUDED.country, movies.country),
                language=COALESCE(EXCLUDED.language, movies.language),
                runtime_minutes=COALESCE(EXCLUDED.runtime_minutes, movies.runtime_minutes),
                overview=COALESCE(EXCLUDED.overview, movies.overview),
                rating=COALESCE(EXCLUDED.rating, movies.rating),
                metadata=movies.metadata || EXCLUDED.metadata,
                updated_at=now()
            """,
            (
                movie_id,
                public_id,
                clean_text(item.get("barcode")) or None,
                title,
                clean_text(item.get("sortTitle") or item.get("sort_title") or title),
                clean_text(item.get("originalTitle") or item.get("original_title")) or None,
                year or None,
                release_date,
                clean_text(item.get("format")) or None,
                clean_text(item.get("edition")) or None,
                clean_text(item.get("country")) or None,
                clean_text(item.get("language")) or None,
                item.get("runtimeMinutes") or item.get("runtime_minutes") or None,
                clean_text(item.get("overview") or item.get("plot")) or None,
                clean_text(item.get("rating")) or None,
                Jsonb(json_ready(metadata)),
            ),
        )
        if table_exists(conn, "movie_identifiers"):
            for provider, identifier in {
                "tmdb": clean_text(item.get("tmdbId") or item.get("tmdb_id")),
                "imdb": clean_text(item.get("imdbId") or item.get("imdb_id")),
            }.items():
                if not identifier:
                    continue
                cur.execute(
                    """
                    INSERT INTO movie_identifiers (movie_id, provider_id, identifier_type, identifier)
                    VALUES (%s, %s, 'movie_id', %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (movie_id, provider, identifier),
                )
    return movie_id, existing_id is None


def queue_import_metadata_refresh_jobs(
    conn,
    movie_ids: list[str],
    *,
    actor: dict[str, Any] | None,
    plugin_id: str,
    source_kind: str,
) -> list[str]:
    if not movie_ids or not background_jobs_ready(conn):
        return []
    requested_by = actor if isinstance(actor, dict) else {}
    queued_ids: list[str] = []
    with conn.cursor() as cur:
        for movie_id in dict.fromkeys(str(value) for value in movie_ids if value):
            cur.execute(
                """
                INSERT INTO background_jobs (job_type, payload)
                VALUES (%s, %s)
                RETURNING id
                """,
                (
                    METADATA_REFRESH_JOB_TYPE,
                    Jsonb(
                        json_ready(
                            {
                                "movieId": movie_id,
                                "dryRun": False,
                                "requestedBy": requested_by,
                                "source": "import",
                                "importPluginId": plugin_id,
                                "sourceKind": source_kind,
                            }
                        )
                    ),
                ),
            )
            row = cur.fetchone()
            if row and row.get("id"):
                queued_ids.append(str(row["id"]))
    return queued_ids


def persist_collection_import(plugin_id: str, result: dict[str, Any], actor: dict[str, Any] | None) -> dict[str, Any]:
    if result.get("status") not in {"completed", "ok"}:
        return {"skipped": True, "reason": result.get("error") or result.get("status") or "import did not complete"}
    items = result.get("items") or []
    if not isinstance(items, list):
        return {"skipped": True, "reason": "import_source result did not contain items"}
    with connect() as conn:
        if not table_exists(conn, "movies"):
            return {"skipped": True, "reason": "movies table is not initialized"}
        with conn.transaction():
            source_hash = clean_text(result.get("sourceDatabaseHash")) or hashlib.sha256(
                f"{plugin_id}:{result.get('sourcePath')}".encode("utf-8")
            ).hexdigest()
            source_path = clean_text(result.get("sourcePath"))
            source_kind = clean_text(result.get("sourceKind") or plugin_id)
            container_cache: dict[tuple[str, str], UUID] = {}
            container_titles: dict[tuple[str, str], str] = {}
            container_created: set[UUID] = set()
            container_links = 0
            imported = 0
            created = 0
            updated = 0
            linked = 0
            errors = []
            imported_movies: list[dict[str, Any]] = []
            imported_movie_ids: list[str] = []
            created_movie_ids: list[str] = []
            for index, item in enumerate(items[:5000], start=1):
                if not isinstance(item, dict):
                    continue
                try:
                    local_container_cache: dict[tuple[str, str], UUID] = {}
                    local_container_titles: dict[tuple[str, str], str] = {}
                    local_container_created: set[UUID] = set()
                    local_container_links = 0
                    with conn.transaction():
                        movie_id, was_created = upsert_import_movie(conn, plugin_id, item)
                        if table_exists(conn, "containers"):
                            for spec in import_item_container_specs(item):
                                key = (spec["containerType"], spec["title"].casefold())
                                spec_container_id = container_cache.get(key) or local_container_cache.get(key)
                                if not spec_container_id:
                                    local_container_titles[key] = spec["title"]
                                    spec_container_id, was_container_created = upsert_import_container(
                                        conn,
                                        plugin_id=plugin_id,
                                        source_hash=source_hash,
                                        container_type=spec["containerType"],
                                        title=spec["title"],
                                        source_path=source_path,
                                        source_kind=source_kind,
                                    )
                                    local_container_cache[key] = spec_container_id
                                    if was_container_created:
                                        local_container_created.add(spec_container_id)
                                if link_import_movie_to_container(
                                    conn,
                                    container_id=spec_container_id,
                                    container_type=spec["containerType"],
                                    movie_id=movie_id,
                                    sort_order=index,
                                ):
                                    local_container_links += 1
                    imported += 1
                    created += 1 if was_created else 0
                    updated += 0 if was_created else 1
                    if was_created:
                        created_movie_ids.append(str(movie_id))
                    imported_movie_ids.append(str(movie_id))
                    container_links += local_container_links
                    container_cache.update(local_container_cache)
                    container_titles.update(local_container_titles)
                    container_created.update(local_container_created)
                    imported_movies.append(
                        {
                            "index": index,
                            "id": str(movie_id),
                            "title": clean_text(item.get("title")),
                            "year": clean_text(item.get("year")),
                            "barcode": clean_text(item.get("barcode")),
                            "action": "created" if was_created else "updated",
                            "reviewAction": clean_text(item.get("importReviewAction")),
                        }
                    )
                except Exception as exc:
                    errors.append({"index": index, "title": item.get("title"), "error": str(exc)})
            metadata_job_ids = queue_import_metadata_refresh_jobs(
                conn,
                imported_movie_ids,
                actor=actor,
                plugin_id=plugin_id,
                source_kind=source_kind,
            )

    return {
        "sourceKind": result.get("sourceKind"),
        "sourcePath": result.get("sourcePath"),
        "items": len(items),
        "imported": imported,
        "created": created,
        "updated": updated,
        "linkedToCollection": linked,
        "linkedToContainers": container_links,
        "containersCreated": len(container_created),
        "containersTouched": len(container_cache),
        "collectionId": None,
        "collectionCreated": False,
        "metadataRefreshQueued": len(metadata_job_ids),
        "metadataJobIds": metadata_job_ids[:200],
        "rollbackMovieIds": created_movie_ids,
        "movies": imported_movies[:200],
        "review": result.get("review") if isinstance(result.get("review"), dict) else {},
        "warnings": result.get("warnings") if isinstance(result.get("warnings"), list) else [],
        "containers": [
            {
                "containerType": key[0],
                "title": container_titles.get(key, key[1]),
                "id": str(value),
                "created": value in container_created,
            }
            for key, value in container_cache.items()
        ][:50],
        "errors": errors[:20],
    }


def match_digital_movie(conn, *, tmdb_id: str = "", imdb_id: str = "", title: str = "", year: str = ""):
    with conn.cursor() as cur:
        if tmdb_id:
            cur.execute(
                """
                SELECT movie_id
                FROM movie_identifiers
                WHERE provider_id='tmdb'
                  AND identifier_type='movie_id'
                  AND identifier=%s
                LIMIT 1
                """,
                (str(tmdb_id),),
            )
            row = cur.fetchone()
            if row:
                return row["movie_id"]
        if imdb_id:
            cur.execute(
                """
                SELECT movie_id
                FROM movie_identifiers
                WHERE provider_id='imdb'
                  AND identifier_type='movie_id'
                  AND identifier=%s
                LIMIT 1
                """,
                (str(imdb_id),),
            )
            row = cur.fetchone()
            if row:
                return row["movie_id"]
        if title and year:
            cur.execute(
                """
                SELECT id
                FROM movies
                WHERE lower(title)=lower(%s)
                  AND year=%s
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (title, str(year)),
            )
            row = cur.fetchone()
            if row:
                return row["id"]
    return None


def parsed_plugin_datetime(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except ValueError:
        return clean_text(value)


def personal_list_items(result: dict[str, Any], key: str) -> list[dict[str, Any]]:
    lists = result.get("personalLists") or result.get("personal_lists") or {}
    if not isinstance(lists, dict):
        lists = {}
    values = lists.get(key) or result.get(key) or []
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def personal_list_match_movie(conn, item: dict[str, Any]):
    return match_digital_movie(
        conn,
        tmdb_id=clean_text(item.get("tmdbId") or item.get("tmdb_id")),
        imdb_id=clean_text(item.get("imdbId") or item.get("imdb_id")),
        title=clean_text(item.get("title")),
        year=clean_text(item.get("year")),
    )


def personal_list_movie_snapshot(conn, movie_id: UUID | str | None, item: dict[str, Any] | None = None, *, plugin_id: str = "") -> dict[str, Any]:
    item = item or {}
    snapshot: dict[str, Any] = {
        "source": plugin_id,
        "title": item.get("title"),
        "movie_title": item.get("title"),
        "year": item.get("year"),
        "movie_year": item.get("year"),
        "format": item.get("format") or item.get("mediaFormat") or item.get("media_format"),
        "movie_format": item.get("format") or item.get("mediaFormat") or item.get("media_format"),
        "poster_url": item.get("posterUrl") or item.get("poster_url") or item.get("poster"),
        "poster": item.get("posterUrl") or item.get("poster_url") or item.get("poster"),
        "watchedAt": item.get("watchedAt") or item.get("lastWatchedAt"),
        "addedAt": item.get("addedAt") or item.get("listedAt"),
        "plays": item.get("plays"),
        "traktId": item.get("traktId") or item.get("trakt_id"),
        "tmdbId": item.get("tmdbId") or item.get("tmdb_id"),
        "imdbId": item.get("imdbId") or item.get("imdb_id"),
        "sourceUrl": item.get("sourceUrl") or item.get("source_url"),
        "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
    }
    if movie_id and table_exists(conn, "movies"):
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
                    format,
                    edition,
                    edition_type,
                    metadata
                FROM movies
                WHERE id=%s
                LIMIT 1
                """,
                (movie_id,),
            )
            movie = cur.fetchone() or {}
        metadata = movie.get("metadata") if isinstance(movie.get("metadata"), dict) else {}
        poster_url = (
            metadata.get("poster_url")
            or metadata.get("posterUrl")
            or metadata.get("poster")
            or snapshot.get("poster_url")
        )
        backdrop_url = metadata.get("backdrop_url") or metadata.get("backdropUrl") or metadata.get("backdrop")
        snapshot.update(
            {
                "movie_id": str(movie.get("id") or movie_id),
                "public_id": movie.get("public_id"),
                "barcode": movie.get("barcode"),
                "title": movie.get("title") or snapshot.get("title"),
                "movie_title": movie.get("title") or snapshot.get("movie_title"),
                "sort_title": movie.get("sort_title"),
                "original_title": movie.get("original_title"),
                "year": movie.get("year") or snapshot.get("year"),
                "movie_year": movie.get("year") or snapshot.get("movie_year"),
                "format": movie.get("format") or snapshot.get("format"),
                "movie_format": movie.get("format") or snapshot.get("movie_format"),
                "edition": movie.get("edition"),
                "edition_type": movie.get("edition_type"),
                "poster_url": poster_url,
                "poster": poster_url,
                "backdrop_url": backdrop_url,
            }
        )
        if table_exists(conn, "movie_identifiers"):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT provider_id, identifier_type, identifier
                    FROM movie_identifiers
                    WHERE movie_id=%s
                    """,
                    (movie_id,),
                )
                snapshot["identifiers"] = cur.fetchall()
        if table_exists(conn, "digital_media_items") and table_exists(conn, "digital_media_sources"):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT dms.plugin_id, dms.name AS source_name, dms.source_type
                    FROM digital_media_items dmi
                    JOIN digital_media_sources dms ON dms.id=dmi.source_id
                    WHERE dmi.matched_movie_id=%s
                    GROUP BY dms.plugin_id, dms.name, dms.source_type
                    ORDER BY dms.name
                    """,
                    (movie_id,),
                )
                snapshot["digital_sources"] = cur.fetchall()
    return json_ready({key: value for key, value in snapshot.items() if value not in (None, "", [], {})})


def persist_personal_list_sync(
    plugin_id: str,
    result: dict[str, Any],
    queued_actor: dict[str, Any] | None,
) -> dict[str, Any]:
    actor_id = clean_text((queued_actor or {}).get("id"))
    if not actor_id:
        return {"skipped": True, "reason": "personal list sync requires a queued user"}
    watchlist = personal_list_items(result, "watchlist")
    watched = personal_list_items(result, "watched")
    summary = {
        "pluginId": plugin_id,
        "watchlist": {"items": len(watchlist), "matched": 0, "upserted": 0, "unmatched": []},
        "watched": {"items": len(watched), "matched": 0, "created": 0, "existing": 0, "unmatched": []},
    }
    with connect() as conn:
        if not table_exists(conn, "watchlist_items") or not table_exists(conn, "watch_history"):
            return {"skipped": True, "reason": "personal list tables are not initialized"}

        with conn.transaction():
            with conn.cursor() as cur:
                for item in watchlist:
                    movie_id = personal_list_match_movie(conn, item)
                    if not movie_id:
                        summary["watchlist"]["unmatched"].append(
                            {
                                "title": clean_text(item.get("title")),
                                "year": clean_text(item.get("year")),
                                "tmdbId": clean_text(item.get("tmdbId") or item.get("tmdb_id")),
                                "imdbId": clean_text(item.get("imdbId") or item.get("imdb_id")),
                            }
                        )
                        continue
                    summary["watchlist"]["matched"] += 1
                    added_at = parsed_plugin_datetime(item.get("addedAt") or item.get("listedAt")) or datetime.utcnow().isoformat()
                    snapshot = personal_list_movie_snapshot(conn, movie_id, item, plugin_id=plugin_id)
                    cur.execute(
                        """
                        SELECT id
                        FROM watchlist_items
                        WHERE user_id=%s AND movie_id=%s
                        LIMIT 1
                        """,
                        (actor_id, movie_id),
                    )
                    existing = cur.fetchone()
                    if existing:
                        cur.execute(
                            """
                            UPDATE watchlist_items
                            SET added_at=%s, snapshot=%s
                            WHERE id=%s
                            """,
                            (added_at, Jsonb(snapshot), existing.get("id")),
                        )
                    else:
                        cur.execute(
                            """
                            INSERT INTO watchlist_items (user_id, movie_id, added_at, snapshot)
                            VALUES (%s, %s, %s, %s)
                            """,
                            (actor_id, movie_id, added_at, Jsonb(snapshot)),
                        )
                    summary["watchlist"]["upserted"] += 1

                for item in watched:
                    movie_id = personal_list_match_movie(conn, item)
                    watched_at = parsed_plugin_datetime(item.get("watchedAt") or item.get("lastWatchedAt"))
                    if not movie_id or not watched_at:
                        summary["watched"]["unmatched"].append(
                            {
                                "title": clean_text(item.get("title")),
                                "year": clean_text(item.get("year")),
                                "tmdbId": clean_text(item.get("tmdbId") or item.get("tmdb_id")),
                                "imdbId": clean_text(item.get("imdbId") or item.get("imdb_id")),
                                "reason": "not_matched" if not movie_id else "missing_watched_at",
                            }
                        )
                        continue
                    summary["watched"]["matched"] += 1
                    cur.execute(
                        """
                        SELECT id
                        FROM watch_history
                        WHERE user_id=%s
                          AND movie_id=%s
                          AND watched_at BETWEEN (%s::timestamptz - interval '2 minutes')
                                           AND (%s::timestamptz + interval '2 minutes')
                        LIMIT 1
                        """,
                        (actor_id, movie_id, watched_at, watched_at),
                    )
                    if cur.fetchone():
                        summary["watched"]["existing"] += 1
                        continue
                    snapshot = personal_list_movie_snapshot(conn, movie_id, item, plugin_id=plugin_id)
                    cur.execute(
                        """
                        INSERT INTO watch_history (user_id, movie_id, watched_at, snapshot)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (
                            actor_id,
                            movie_id,
                            watched_at,
                            Jsonb(snapshot),
                        ),
                    )
                    summary["watched"]["created"] += 1
    return summary


def persist_digital_sync(plugin_id: str, result: dict[str, Any]) -> dict[str, Any]:
    source = result.get("source") or {}
    items = result.get("items") or []
    if not isinstance(items, list):
        items = []
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.digital_media_sources') AS table_name")
            ready = cur.fetchone()
        if not ready or not ready.get("table_name"):
            return {"skipped": True, "reason": "digital media tables are not initialized"}

        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO digital_media_sources (
                        plugin_id,
                        name,
                        source_type,
                        base_url,
                        machine_id,
                        enabled,
                        last_synced_at,
                        item_count,
                        last_status,
                        last_error,
                        metadata,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, true, now(), %s, %s, NULL, %s, now())
                    ON CONFLICT (plugin_id) DO UPDATE SET
                        name=EXCLUDED.name,
                        source_type=EXCLUDED.source_type,
                        base_url=EXCLUDED.base_url,
                        machine_id=EXCLUDED.machine_id,
                        enabled=true,
                        last_synced_at=now(),
                        item_count=EXCLUDED.item_count,
                        last_status=EXCLUDED.last_status,
                        last_error=NULL,
                        metadata=EXCLUDED.metadata,
                        updated_at=now()
                    RETURNING id
                    """,
                    (
                        plugin_id,
                        str(source.get("name") or plugin_id),
                        str(source.get("type") or result.get("connector") or plugin_id),
                        source.get("baseUrl"),
                        source.get("machineId"),
                        len(items),
                        str(result.get("status") or "completed"),
                        Jsonb(json_ready({"libraries": result.get("libraries") or []})),
                    ),
                )
                source_id = cur.fetchone()["id"]
                cur.execute("DELETE FROM digital_media_items WHERE source_id=%s", (source_id,))

                matched = 0
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    external_id = clean_text(item.get("externalId") or item.get("external_id"))
                    title = clean_text(item.get("title"))
                    if not external_id or not title:
                        continue
                    tmdb_id = clean_text(item.get("tmdbId") or item.get("tmdb_id"))
                    imdb_id = clean_text(item.get("imdbId") or item.get("imdb_id"))
                    year = clean_text(item.get("year"))
                    movie_id = match_digital_movie(
                        conn,
                        tmdb_id=tmdb_id,
                        imdb_id=imdb_id,
                        title=title,
                        year=year,
                    )
                    if movie_id:
                        matched += 1
                    cur.execute(
                        """
                        INSERT INTO digital_media_items (
                            source_id,
                            external_id,
                            media_type,
                            title,
                            year,
                            tmdb_id,
                            imdb_id,
                            playback_url,
                            matched_movie_id,
                            metadata,
                            synced_at,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
                        ON CONFLICT (source_id, external_id) DO UPDATE SET
                            media_type=EXCLUDED.media_type,
                            title=EXCLUDED.title,
                            year=EXCLUDED.year,
                            tmdb_id=EXCLUDED.tmdb_id,
                            imdb_id=EXCLUDED.imdb_id,
                            playback_url=EXCLUDED.playback_url,
                            matched_movie_id=EXCLUDED.matched_movie_id,
                            metadata=EXCLUDED.metadata,
                            synced_at=now(),
                            updated_at=now()
                        """,
                        (
                            source_id,
                            external_id,
                            clean_text(item.get("mediaType") or item.get("media_type")) or "movie",
                            title,
                            year or None,
                            tmdb_id or None,
                            imdb_id or None,
                            clean_text(item.get("playbackUrl") or item.get("playback_url")) or None,
                            movie_id,
                            Jsonb(json_ready(item.get("metadata") or {})),
                        ),
                    )
    return {"sourceId": source_id, "items": len(items), "matched": matched}


def process_sqlite_import(payload: dict[str, Any], worker_id: str) -> dict[str, Any]:
    sqlite_db = Path(str(payload.get("sqliteDb") or "/data/discvault.db")).expanduser()
    data_dir = Path(str(payload.get("dataDir") or sqlite_db.parent or "/data")).expanduser()
    if not sqlite_db.exists() or not sqlite_db.is_file():
        raise RuntimeError(f"Legacy SQLite database not found: {sqlite_db}")
    if not data_dir.exists() or not data_dir.is_dir():
        raise RuntimeError(f"Legacy data directory not found: {data_dir}")
    include_security = bool_value(payload.get("includeSecurity"), default=True)
    include_personal = bool_value(payload.get("includePersonal"), default=False)
    if include_personal and not include_security:
        raise RuntimeError("includePersonal requires includeSecurity")
    import_media_references = bool_value(payload.get("importMediaReferences"), default=True)
    expected_hash = clean_text(payload.get("sourceDatabaseHash"))

    importer = NextImporter(
        sqlite_db,
        data_dir,
        include_security=include_security,
        include_personal=include_personal,
        import_media=import_media_references,
        owner_username=clean_text(payload.get("ownerUsername")),
    )
    try:
        dry_run = importer.dry_run()
        actual_hash = clean_text(dry_run.get("source_database_sha256"))
        if expected_hash and actual_hash and expected_hash != actual_hash:
            raise RuntimeError("Legacy SQLite database changed after readiness check; restart migration readiness.")
        summary = importer.run()
    except NextImportError as exc:
        raise RuntimeError(str(exc)) from exc
    finally:
        importer.sqlite.close()

    return {
        "workerId": worker_id,
        "handled": True,
        "jobType": "migration.import_sqlite",
        "phase": "completed",
        "mediaMigrationMode": "reference_existing_files",
        "source": {
            "sqliteDb": str(sqlite_db),
            "dataDir": str(data_dir),
            "sourceDatabaseHash": expected_hash,
        },
        "options": {
            "includeSecurity": include_security,
            "includePersonal": include_personal,
            "importMediaReferences": import_media_references,
        },
        "dryRun": dry_run,
        "summary": summary,
    }


def run_once(worker_id: str, *, quiet_idle: bool = False) -> int:
    with connect() as conn:
        if not background_jobs_ready(conn):
            print(
                json.dumps(
                    {
                        "status": "waiting",
                        "reason": "database schema is not initialized",
                        "missing": "background_jobs",
                    }
                )
            )
            return 2
        job = claim_job(conn, worker_id)
        if not job:
            if not quiet_idle:
                print("No pending jobs.")
            return 0
        try:
            result = process_job(job, worker_id)
            complete_job(conn, job["id"], result)
            print(
                json.dumps(
                    {
                        "status": "completed",
                        "jobId": str(job["id"]),
                        "result": json_ready(result),
                    }
                )
            )
            return 0
        except Exception as exc:
            fail_job(conn, job["id"], str(exc), {"workerId": worker_id})
            print(json.dumps({"status": "failed", "jobId": str(job["id"]), "error": str(exc)}))
            return 1


def work_loop(worker_id: str, poll_interval: float) -> int:
    while not STOP:
        run_once(worker_id, quiet_idle=True)
        if STOP:
            break
        time.sleep(poll_interval)
    return 0


def request_stop(_signum, _frame) -> None:
    global STOP
    STOP = True


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DiscVault Next background worker.")
    parser.add_argument("command", choices=("run-once", "work"))
    parser.add_argument(
        "--worker-id",
        default=os.environ.get("DISCVAULT_WORKER_ID") or "next-worker",
        help="Stable worker label used in job results.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.environ.get("DISCVAULT_WORKER_POLL_INTERVAL", "2")),
        help="Seconds to wait between polling attempts in work mode.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    if args.command == "run-once":
        return run_once(args.worker_id)
    return work_loop(args.worker_id, args.poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
