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
    from .next_ownership import actor_or_instance_owner_id
    from .next_movievault_connection import MOVIEVAULT_PLUGIN_ID
    from .next_movievault_connection import is_movievault_plugin
    from .next_movievault_connection import movievault_plugin_context
    from .next_movievault_v2 import MOVIEVAULT_V2_PLUGIN_ID
    from .next_movievault_v2 import movievault_v2_plugin_context
    from .next_plugin_runtime import plugin_config_payload as resolved_plugin_config_payload
    from .next_plugin_runtime import run_plugin_entrypoint
    from .next_metadata import METADATA_REFRESH_JOB_TYPE
    from .next_metadata import lookup_metadata_sources
    from .next_metadata import refresh_movie_metadata
    from .next_backup import BACKUP_RESTORE_JOB_TYPE
    from .next_backup import BackupError as NextBackupError
    from .next_backup import restore_functional_backup
    from .next_price_alerts import PRICE_ALERT_JOB_TYPE
    from .next_price_alerts import run_price_alert_sweep
    from .next_database import wait_for_database
    from .next_runtime_secrets import validate_runtime_secrets
    from .next_movievault_v2_posters import POSTER_CACHE_JOB_TYPE
    from .next_movievault_v2_posters import POSTER_CLEANUP_JOB_TYPE
    from .next_movievault_v2_posters import link_box_set_poster_to_containers
    from .next_movievault_v2_posters import run_poster_cache_job
    from .next_movievault_v2_posters import run_poster_cleanup
except ImportError:  # pragma: no cover - supports python next_worker.py
    from next_import import ImportError as NextImportError
    from next_import import NextImporter
    from next_import import clean_text
    from next_ownership import actor_or_instance_owner_id
    from next_movievault_connection import MOVIEVAULT_PLUGIN_ID
    from next_movievault_connection import is_movievault_plugin
    from next_movievault_connection import movievault_plugin_context
    from next_movievault_v2 import MOVIEVAULT_V2_PLUGIN_ID
    from next_movievault_v2 import movievault_v2_plugin_context
    from next_plugin_runtime import plugin_config_payload as resolved_plugin_config_payload
    from next_plugin_runtime import run_plugin_entrypoint
    from next_metadata import METADATA_REFRESH_JOB_TYPE
    from next_metadata import lookup_metadata_sources
    from next_metadata import refresh_movie_metadata
    from next_backup import BACKUP_RESTORE_JOB_TYPE
    from next_backup import BackupError as NextBackupError
    from next_backup import restore_functional_backup
    from next_price_alerts import PRICE_ALERT_JOB_TYPE
    from next_price_alerts import run_price_alert_sweep
    from next_database import wait_for_database
    from next_runtime_secrets import validate_runtime_secrets
    from next_movievault_v2_posters import POSTER_CACHE_JOB_TYPE
    from next_movievault_v2_posters import POSTER_CLEANUP_JOB_TYPE
    from next_movievault_v2_posters import link_box_set_poster_to_containers
    from next_movievault_v2_posters import run_poster_cache_job
    from next_movievault_v2_posters import run_poster_cleanup


STOP = False
MOVIEVAULT_V2_SCHEDULER_LOCK_KEY = 2_026_262
PERSON_METADATA_REFRESH_JOB_TYPE = "metadata.refresh_person"


class JobFailure(RuntimeError):
    """Raise when a job produced a useful failure summary."""

    def __init__(self, message: str, result: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.result = result or {}


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


def physical_media_format_key(value: Any) -> str:
    text = (clean_text(value) or "").casefold().replace("-", " ").replace("_", " ").replace("/", " ")
    text = " ".join(text.split())
    if not text:
        return ""
    if "ultra hd" in text or "uhd" in text or "4k" in text:
        return "ultra_hd_blu_ray"
    if "blu ray" in text or "bluray" in text or text == "bd":
        return "blu_ray"
    if text in {"dvd", "dvd video"}:
        return "dvd"
    if "hd dvd" in text or "hddvd" in text:
        return "hd_dvd"
    if "laserdisc" in text or "laser disc" in text:
        return "laserdisc"
    if "svcd" in text or "vcd" in text:
        return "vcd_svcd"
    return text


def physical_media_formats_compatible(candidate: Any, expected: Any) -> bool:
    candidate_key = physical_media_format_key(candidate)
    expected_key = physical_media_format_key(expected)
    return not candidate_key or not expected_key or candidate_key == expected_key


def import_container_release_key(
    container_type: str,
    title: str,
    *,
    barcode: Any = "",
    media_format: Any = "",
) -> tuple[str, str, str]:
    type_key = clean_text(container_type)
    title_key = (clean_text(title) or "").casefold()
    if type_key == "box_set":
        barcode_key = (clean_text(barcode) or "").casefold()
        if barcode_key:
            return (type_key, "barcode", barcode_key)
        format_key = physical_media_format_key(media_format)
        if format_key:
            return (type_key, "title_format", f"{title_key}:{format_key}")
    return (type_key, "title", title_key)


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
    plugin_id = str(plugin.get("id") or "")
    if plugin_id == MOVIEVAULT_V2_PLUGIN_ID:
        # Origin is enforced (hardcoded default + optional MOVIEVAULT_V2_ORIGIN env),
        # never user-supplied, so v2 requires no user config for any entrypoint.
        return False
    if is_movievault_plugin(plugin_id):
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
        "distributionContractRange": manifest.get("distributionContractRange"),
        "settings": config.get("settings") or {},
        "secrets": plugin_secret_values(conn, config),
        "settingsConfigured": bool(config.get("settingsConfigured")),
        "secretNames": config.get("secretNames") or [],
        "secretsConfigured": bool(config.get("secretsConfigured")),
        "actor": queued_actor or {"id": None, "username": None, "role": None},
    }
    context = movievault_plugin_context(
        conn,
        plugin_id,
        context,
        ensure_token=is_movievault_plugin(plugin_id) and entrypoint != "health_check",
        actor_id=(queued_actor or {}).get("id") if queued_actor else None,
    )
    return movievault_v2_plugin_context(
        conn,
        plugin_id,
        context,
        connection_factory=connect,
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

    if job_type == PERSON_METADATA_REFRESH_JOB_TYPE:
        return process_person_metadata_refresh(payload, worker_id)

    if job_type == BACKUP_RESTORE_JOB_TYPE:
        return process_functional_restore(payload, worker_id)

    if job_type == PRICE_ALERT_JOB_TYPE:
        return process_price_alert_sweep(payload, worker_id)

    if job_type == POSTER_CACHE_JOB_TYPE:
        return process_movievault_v2_poster_cache(payload, worker_id)

    if job_type == POSTER_CLEANUP_JOB_TYPE:
        return process_movievault_v2_poster_cleanup(payload, worker_id)

    raise RuntimeError(f"Unsupported job type: {job_type}")


def process_metadata_refresh(payload: dict[str, Any], worker_id: str) -> dict[str, Any]:
    movie_id = clean_text(payload.get("movieId") or payload.get("movie_id"))
    if not movie_id:
        raise RuntimeError("movieId is required for metadata refresh jobs")
    dry_run = bool_value(payload.get("dryRun", payload.get("dry_run")), default=False)
    refresh_people = bool_value(payload.get("refreshPeople", payload.get("refresh_people")), default=False)
    person_refresh_scope = clean_text(
        payload.get("personRefreshScope")
        or payload.get("person_refresh_scope")
        or payload.get("refreshPeopleScope")
        or payload.get("refresh_people_scope")
        or "all"
    )
    if person_refresh_scope.lower() != "crew":
        person_refresh_scope = "all"
    actor = payload.get("requestedBy") or payload.get("requested_by") or {}
    if not isinstance(actor, dict):
        actor = {}
    with connect() as conn:
        result = refresh_movie_metadata(conn, movie_id, dry_run=dry_run, actor=actor)
        if refresh_people:
            try:
                from .next_app import refresh_movie_person_metadata_cascade
            except ImportError:  # pragma: no cover - supports python next_worker.py
                from next_app import refresh_movie_person_metadata_cascade
            result["personRefresh"] = refresh_movie_person_metadata_cascade(conn, movie_id, dry_run=dry_run, actor=actor, scope=person_refresh_scope)
    return {
        "workerId": worker_id,
        "handled": True,
        "jobType": METADATA_REFRESH_JOB_TYPE,
        "movieId": movie_id,
        "dryRun": dry_run,
        "refreshPeople": refresh_people,
        "personRefreshScope": person_refresh_scope,
        "result": result,
    }


def process_person_metadata_refresh(payload: dict[str, Any], worker_id: str) -> dict[str, Any]:
    person_id = clean_text(payload.get("personId") or payload.get("person_id"))
    if not person_id:
        raise RuntimeError("personId is required for person metadata refresh jobs")
    actor = payload.get("requestedBy") or payload.get("requested_by") or {}
    if not isinstance(actor, dict):
        actor = {}
    try:
        person_uuid = UUID(person_id)
    except ValueError as exc:
        raise RuntimeError("personId must be a valid UUID") from exc
    try:
        from .next_app import refresh_person_metadata
    except ImportError:  # pragma: no cover - supports python next_worker.py
        from next_app import refresh_person_metadata
    with connect() as conn:
        result = refresh_person_metadata(conn, person_uuid, dry_run=False, actor=actor)
    return {
        "workerId": worker_id,
        "handled": True,
        "jobType": PERSON_METADATA_REFRESH_JOB_TYPE,
        "personId": person_id,
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
                mode=str(payload.get("mode") or "full"),
                scopes=payload.get("scopes") if isinstance(payload.get("scopes"), list) else None,
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


def process_price_alert_sweep(payload: dict[str, Any], worker_id: str) -> dict[str, Any]:
    limit = int(payload.get("limit") or 50)
    with connect() as conn:
        summary = run_price_alert_sweep(conn, limit=limit)
    return {
        "workerId": worker_id,
        "handled": True,
        "jobType": PRICE_ALERT_JOB_TYPE,
        **summary,
    }


def process_movievault_v2_poster_cache(payload: dict[str, Any], worker_id: str) -> dict[str, Any]:
    with connect() as conn:
        summary = run_poster_cache_job(conn, payload)
        # Auto-link ready display posters to matching DiscVault containers.
        if summary.get("outcome") == "ready" and str(payload.get("variant", "")) == "display":
            asset_id = str(summary.get("assetId") or payload.get("assetId") or "")
            media_asset_id = str(summary.get("mediaAssetId") or "")
            if asset_id and media_asset_id:
                linked = link_box_set_poster_to_containers(
                    conn, asset_id=asset_id, media_asset_id=media_asset_id
                )
                summary["containersLinked"] = linked
    return {
        "workerId": worker_id,
        "jobType": POSTER_CACHE_JOB_TYPE,
        **summary,
    }


def process_movievault_v2_poster_cleanup(payload: dict[str, Any], worker_id: str) -> dict[str, Any]:
    limit = int(payload.get("limit") or 200)
    with connect() as conn:
        summary = run_poster_cleanup(conn, limit=limit)
    return {
        "workerId": worker_id,
        "jobType": POSTER_CLEANUP_JOB_TYPE,
        **summary,
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
    job_result = {
        "workerId": worker_id,
        "handled": True,
        "jobType": "plugin.execute",
        "pluginId": plugin_id,
        "entrypoint": entrypoint,
        "execution": execution,
        "persistence": persist_summary,
    }
    if persist_summary.get("failed"):
        raise JobFailure(
            clean_text(persist_summary.get("error")) or "Plugin execution persistence failed",
            job_result,
        )
    return job_result


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
        box_set_proposal = decision.get("boxSetProposal") or decision.get("box_set_proposal")
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
        if isinstance(box_set_proposal, dict):
            proposal_format = clean_text(box_set_proposal.get("format") or reviewed.get("format"))
            members = []
            for index, raw_member in enumerate(
                (
                    box_set_proposal.get("members")
                    or box_set_proposal.get("movies")
                    or box_set_proposal.get("boxSetMovies")
                    or box_set_proposal.get("box_set_movies")
                    or []
                ),
                start=1,
            ):
                if not isinstance(raw_member, dict):
                    continue
                member_title = clean_text(raw_member.get("title") or raw_member.get("name"))
                if not member_title:
                    continue
                member = dict(raw_member)
                member["title"] = member_title
                member_format = clean_text(member.get("format"))
                if proposal_format and (not member_format or not physical_media_formats_compatible(member_format, proposal_format)):
                    member["format"] = proposal_format
                else:
                    member["format"] = member_format
                member["sortOrder"] = int(member.get("sortOrder") or member.get("sort_order") or index)
                members.append({key: value for key, value in member.items() if value not in (None, "", [], {})})
            proposal_payload = {
                key: value
                for key, value in box_set_proposal.items()
                if value not in (None, "", [], {})
            }
            if members:
                proposal_payload["members"] = members
                proposal_payload["movies"] = members
                proposal_payload["boxSetMovies"] = members
                proposal_payload["box_set_movies"] = members
                proposal_payload["memberCount"] = len(members)
                proposal_payload["member_count"] = len(members)
                proposal_payload["membersAreExplicit"] = True
                proposal_payload["members_are_explicit"] = True
            reviewed.update(
                {
                    "itemType": "box_set",
                    "entityType": "box_set",
                    "isBoxSet": True,
                    "title": clean_text(box_set_proposal.get("title") or box_set_proposal.get("name") or reviewed.get("title")),
                    "boxSetTitle": clean_text(box_set_proposal.get("title") or box_set_proposal.get("name") or reviewed.get("boxSetTitle") or reviewed.get("title")),
                    "barcode": clean_text(box_set_proposal.get("barcode") or reviewed.get("barcode")),
                    "format": proposal_format,
                    "boxSetProposal": proposal_payload,
                    "boxSetMembers": members,
                    "containers": [
                        {
                            "containerType": "box_set",
                            "title": clean_text(box_set_proposal.get("title") or box_set_proposal.get("name") or reviewed.get("title")),
                            "barcode": clean_text(box_set_proposal.get("barcode") or reviewed.get("barcode")),
                            "format": proposal_format,
                            "memberCount": len(members),
                            "membersAreExplicit": bool(members),
                            "boxSetProposal": proposal_payload,
                        }
                    ],
                }
            )
            reviewed["importReviewBoxSetProposal"] = reviewed["boxSetProposal"]
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
            if decision.get("metadataMatch") or decision.get("manualOverride") or decision.get("boxSetProposal") or decision.get("box_set_proposal"):
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
    item_format = clean_text(item.get("format"))

    def row_format_matches(row: dict[str, Any]) -> bool:
        return physical_media_formats_compatible(row.get("format"), item_format)

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
                    SELECT m.id AS movie_id, m.format
                    FROM movie_identifiers mi
                    JOIN movies m ON m.id = mi.movie_id
                    WHERE mi.provider_id=%s AND mi.identifier_type='movie_id' AND mi.identifier=%s
                    ORDER BY m.updated_at DESC
                    """,
                    (provider, identifier),
                )
                for row in cur.fetchall():
                    if row_format_matches(row):
                        return row["movie_id"]
        title = clean_text(item.get("title"))
        year = clean_text(item.get("year"))
        if title and year:
            cur.execute(
                """
                SELECT id, format
                FROM movies
                WHERE lower(title)=lower(%s) AND year=%s
                ORDER BY updated_at DESC
                """,
                (title, year),
            )
            for row in cur.fetchall():
                if row_format_matches(row):
                    return row["id"]
    return None


def import_movie_metadata(item: dict[str, Any], plugin_id: str) -> dict[str, Any]:
    metadata = {
        "import_source": plugin_id,
        "source_provider": item.get("sourceProvider") or plugin_id,
        "source_file": item.get("sourceFile"),
        "source_url": item.get("sourceUrl"),
        "director": item.get("director"),
        "actor": item.get("actor"),
        "poster_url": item.get("posterUrl") or item.get("poster_url"),
        "backdrop_url": item.get("backdropUrl") or item.get("backdrop_url"),
        "personal": item.get("personal"),
    }
    return {key: value for key, value in metadata.items() if value not in (None, "", [], {})}


def import_item_is_box_set_container(item: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    item_type = clean_text(item.get("itemType") or item.get("item_type") or item.get("entityType") or item.get("entity_type"))
    return item_type == "box_set" or item.get("isBoxSet") is True or item.get("is_box_set") is True


def import_item_container_specs(item: dict[str, Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for container_type, title in (
        ("collection", item.get("collectionTitle") or item.get("collection") or item.get("collection_title")),
        ("box_set", item.get("boxSetTitle") or item.get("boxSet") or item.get("box_set") or item.get("box_set_title")),
        ("vault", item.get("vaultTitle") or item.get("vault") or item.get("vault_title")),
    ):
        clean_title = clean_text(title)
        if clean_title:
            spec: dict[str, Any] = {"containerType": container_type, "title": clean_title}
            if container_type == "box_set" and import_item_is_box_set_container(item):
                spec.update(
                    {
                        "barcode": clean_text(item.get("barcode")),
                        "format": clean_text(item.get("format")),
                        "memberCount": len(item.get("boxSetMembers") or []),
                        "membersAreExplicit": bool(item.get("boxSetMembers")),
                        "boxSetProposal": item.get("boxSetProposal") if isinstance(item.get("boxSetProposal"), dict) else {},
                    }
                )
            specs.append(spec)
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
            specs.append(
                {
                    "containerType": container_type,
                    "title": title,
                    "barcode": clean_text(raw.get("barcode")),
                    "format": clean_text(raw.get("format")),
                    "memberCount": raw.get("memberCount") or raw.get("member_count"),
                    "membersAreExplicit": raw.get("membersAreExplicit") or raw.get("members_are_explicit"),
                    "boxSetProposal": raw.get("boxSetProposal") if isinstance(raw.get("boxSetProposal"), dict) else {},
                }
            )
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for spec in specs:
        key = import_container_release_key(
            spec["containerType"],
            spec["title"],
            barcode=spec.get("barcode"),
            media_format=spec.get("format"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(spec)
    return unique


def import_box_set_member_list(proposal: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(proposal, dict):
        return []
    for key in ("members", "movies", "boxSetMovies", "box_set_movies", "items", "releases"):
        value = proposal.get(key)
        if isinstance(value, list):
            members = [item for item in value if isinstance(item, dict) and clean_text(item.get("title") or item.get("name"))]
            if members:
                return members
    return []


def import_box_set_evidence(proposal: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(proposal, dict):
        return {}
    evidence = proposal.get("boxSetEvidence") or proposal.get("box_set_evidence")
    return evidence if isinstance(evidence, dict) else {}


def import_box_set_evidence_bool(evidence: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        if key in evidence:
            return bool_value(evidence.get(key))
    return False


def import_box_set_provider_text(proposal: dict[str, Any]) -> str:
    return (clean_text(
        proposal.get("provider")
        or proposal.get("source")
        or proposal.get("sourceLabel")
        or proposal.get("memberSource")
        or proposal.get("member_source")
    ) or "").casefold()


def import_box_set_candidate_only(proposal: dict[str, Any]) -> bool:
    evidence = import_box_set_evidence(proposal)
    confidence = (clean_text(
        evidence.get("memberConfidence")
        or evidence.get("member_confidence")
        or proposal.get("memberConfidence")
        or proposal.get("member_confidence")
    ) or "").casefold()
    member_source = (clean_text(
        evidence.get("memberSource")
        or evidence.get("member_source")
        or proposal.get("memberSource")
        or proposal.get("member_source")
    ) or "").casefold()
    return (
        bool_value(proposal.get("candidateOnly") or proposal.get("candidate_only"))
        or bool_value(proposal.get("detectedWithoutMembers") or proposal.get("detected_without_members"))
        or bool_value(evidence.get("detectedWithoutMembers") or evidence.get("detected_without_members"))
        or confidence in {"candidate", "fallback", "needs_confirmation"}
        or member_source in {"metadata_candidates", "candidate_search"}
    )


def import_box_set_auto_importable(proposal: dict[str, Any]) -> bool:
    if "upcitemdb" in import_box_set_provider_text(proposal):
        return False
    members = import_box_set_member_list(proposal)
    if len(members) < 2 or import_box_set_candidate_only(proposal):
        return False
    evidence = import_box_set_evidence(proposal)
    explicit = import_box_set_evidence_bool(evidence, "membersAreExplicit", "members_are_explicit")
    if not explicit:
        confidence = (clean_text(proposal.get("memberConfidence") or proposal.get("member_confidence")) or "").casefold()
        explicit = confidence in {"confirmed", "exact", "source_verified", "needs_member_confirmation"}
    barcode_match = import_box_set_evidence_bool(evidence, "barcodeMatch", "barcode_match")
    return barcode_match and explicit


def import_metadata_box_set_proposals(metadata_result: dict[str, Any]) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    seen: set[str] = set()
    query = metadata_result.get("query") if isinstance(metadata_result.get("query"), dict) else {}
    query_barcode = clean_text(query.get("externalBarcode") or query.get("barcode"))
    for result in metadata_result.get("results") or []:
        if not isinstance(result, dict):
            continue
        raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
        candidates: list[dict[str, Any]] = []
        for key in ("boxSetProposal", "box_set_proposal"):
            value = result.get(key)
            if isinstance(value, dict):
                candidates.append(value)
            raw_value = raw.get(key)
            if isinstance(raw_value, dict):
                candidates.append(raw_value)
        for key in ("boxSetProposals", "box_set_proposals"):
            value = result.get(key)
            if isinstance(value, list):
                candidates.extend(item for item in value if isinstance(item, dict))
            raw_value = raw.get(key)
            if isinstance(raw_value, list):
                candidates.extend(item for item in raw_value if isinstance(item, dict))
        for candidate in candidates:
            members = import_box_set_member_list(candidate)
            if len(members) < 2:
                continue
            normalized = {**candidate, "members": members, "movies": members}
            normalized.setdefault(
                "provider",
                clean_text(result.get("pluginId") or result.get("provider") or candidate.get("provider") or candidate.get("source")),
            )
            evidence = dict(import_box_set_evidence(normalized))
            if query_barcode and clean_text(normalized.get("barcode")) == query_barcode:
                evidence["barcodeMatch"] = True
            if evidence:
                normalized["boxSetEvidence"] = evidence
                normalized["box_set_evidence"] = evidence
            key = "|".join(
                [
                    clean_text(normalized.get("provider")) or "",
                    clean_text(normalized.get("barcode")) or "",
                    (clean_text(normalized.get("title") or normalized.get("name")) or "").casefold(),
                    ",".join((clean_text(member.get("title") or member.get("name")) or "").casefold() for member in members[:20]),
                ]
            )
            if key in seen:
                continue
            seen.add(key)
            proposals.append(normalized)
    return sorted(proposals, key=import_box_set_sort_key)


def import_box_set_sort_key(proposal: dict[str, Any]) -> tuple[int, int, int, int]:
    provider = import_box_set_provider_text(proposal)
    evidence = import_box_set_evidence(proposal)
    exact = import_box_set_evidence_bool(evidence, "barcodeMatch", "barcode_match")
    explicit = import_box_set_evidence_bool(evidence, "membersAreExplicit", "members_are_explicit")
    members = len(import_box_set_member_list(proposal))
    if "movievault_26" in provider or "movievault 26" in provider:
        provider_rank = 0
    elif "movievault" in provider:
        provider_rank = 1
    elif "bluray" in provider or "blu-ray" in provider:
        provider_rank = 2
    else:
        provider_rank = 5
    return (0 if exact and explicit else 1, provider_rank, 1 if import_box_set_candidate_only(proposal) else 0, -members)


def detect_import_item_box_set(conn, item: dict[str, Any], actor: dict[str, Any] | None) -> dict[str, Any] | None:
    barcode = clean_text(item.get("barcode"))
    if not barcode:
        return None
    query = {
        "barcode": barcode,
        "externalBarcode": barcode,
        "title": clean_text(item.get("title")),
        "year": clean_text(item.get("year")),
        "format": clean_text(item.get("format")),
        "detectBoxSets": True,
        "importBoxSets": True,
        "previewMode": True,
    }
    try:
        metadata_result = lookup_metadata_sources(conn, query, actor)
    except Exception as exc:
        return {"error": str(exc), "barcode": barcode}
    for proposal in import_metadata_box_set_proposals(metadata_result):
        if import_box_set_auto_importable(proposal):
            return {"proposal": proposal, "metadata": metadata_result, "barcode": barcode}
    return None


def import_container_id(
    plugin_id: str,
    source_hash: str,
    container_type: str,
    title: str,
    *,
    barcode: Any = "",
    media_format: Any = "",
) -> UUID:
    release_key = import_container_release_key(
        container_type,
        title,
        barcode=barcode,
        media_format=media_format,
    )
    return stable_uuid(f"container-import:{plugin_id}:{source_hash}:{':'.join(release_key)}")


def upsert_import_container(
    conn,
    *,
    plugin_id: str,
    source_hash: str,
    container_type: str,
    title: str,
    source_path: str,
    source_kind: str,
    barcode: str = "",
    metadata_extra: dict[str, Any] | None = None,
    actor: dict[str, Any] | None = None,
) -> tuple[UUID, bool]:
    metadata_extra = metadata_extra if isinstance(metadata_extra, dict) else {}
    incoming_format = clean_text(metadata_extra.get("format") or metadata_extra.get("media_format"))
    barcode = clean_text(barcode)
    container_id = import_container_id(
        plugin_id,
        source_hash,
        container_type,
        title,
        barcode=barcode,
        media_format=incoming_format,
    )
    public_identity = f"{plugin_id}-{source_hash[:12]}-{title}"
    if container_type == "box_set" and (barcode or incoming_format):
        public_identity = f"{public_identity}-{barcode or incoming_format}"
    public_id = source_public_id(
        f"import-{container_type}",
        public_identity,
        fallback=str(container_id),
    )
    with conn.cursor() as cur:
        if barcode:
            cur.execute(
                """
                SELECT id
                FROM containers
                WHERE container_type=%s AND barcode=%s
                LIMIT 1
                """,
                (container_type, barcode),
            )
            row = cur.fetchone()
            if row:
                container_id = row["id"]
        elif container_type == "box_set" and incoming_format:
            cur.execute(
                """
                SELECT id, barcode, metadata
                FROM containers
                WHERE container_type=%s AND lower(title)=lower(%s)
                ORDER BY updated_at DESC
                """,
                (container_type, title),
            )
            for row in cur.fetchall():
                existing_barcode = clean_text(row.get("barcode"))
                existing_metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                existing_format = clean_text(existing_metadata.get("format") or existing_metadata.get("media_format"))
                if not existing_barcode and existing_format and physical_media_formats_compatible(existing_format, incoming_format):
                    container_id = row["id"]
                    break
        cur.execute("SELECT id FROM containers WHERE id=%s", (container_id,))
        exists = bool(cur.fetchone())
        metadata = {
            "import_source": plugin_id,
            "source_kind": source_kind,
            "source_path": source_path,
            "source_hash": source_hash,
        }
        metadata.update({key: value for key, value in metadata_extra.items() if value not in (None, "", [], {})})
        owner_id = actor_or_instance_owner_id(conn, actor)
        cur.execute(
            """
            INSERT INTO containers (
                id, public_id, container_type, title, barcode, owner_id, metadata, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now())
            ON CONFLICT (id) DO UPDATE SET
                title=EXCLUDED.title,
                barcode=COALESCE(NULLIF(containers.barcode, ''), EXCLUDED.barcode),
                metadata=containers.metadata || EXCLUDED.metadata,
                updated_at=now()
            """,
            (
                container_id,
                public_id,
                container_type,
                title,
                barcode or None,
                owner_id,
                Jsonb(
                    json_ready(
                        metadata
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


def import_box_set_member_item(parent_item: dict[str, Any], proposal: dict[str, Any], member: dict[str, Any], index: int) -> dict[str, Any]:
    parent_format = clean_text(parent_item.get("format") or proposal.get("format"))
    title = clean_text(member.get("title") or member.get("name") or member.get("originalTitle") or member.get("original_title"))
    parent_release_ref = clean_text(proposal.get("barcode")) or clean_text(parent_item.get("barcode")) or parent_format or "box-set"
    member_format = clean_text(member.get("format"))
    if parent_format and (not member_format or not physical_media_formats_compatible(member_format, parent_format)):
        member_format = parent_format
    item = {
        "title": title,
        "originalTitle": clean_text(member.get("originalTitle") or member.get("original_title") or title),
        "year": clean_text(member.get("year") or member.get("releaseYear") or member.get("release_year")),
        "releaseDate": clean_text(member.get("releaseDate") or member.get("release_date")),
        "format": member_format,
        "barcode": clean_text(member.get("barcode")),
        "tmdbId": clean_text(member.get("tmdbId") or member.get("tmdb_id")),
        "imdbId": clean_text(member.get("imdbId") or member.get("imdb_id")),
        "overview": clean_text(member.get("overview") or member.get("plot")),
        "posterUrl": clean_text(member.get("posterUrl") or member.get("poster_url") or member.get("poster")),
        "backdropUrl": clean_text(member.get("backdropUrl") or member.get("backdrop_url") or member.get("backdrop")),
        "sourceProvider": clean_text(member.get("source") or member.get("provider") or proposal.get("provider")),
        "sourceUrl": clean_text(member.get("sourceUrl") or member.get("source_url")),
        "sourceFile": parent_item.get("sourceFile"),
        "externalId": clean_text(
            member.get("externalId")
            or member.get("external_id")
            or member.get("sourceRef")
            or member.get("source_ref")
            or f"{parent_release_ref}:member:{index}:{title}"
        ),
    }
    return {key: value for key, value in item.items() if value not in (None, "", [], {})}


def persist_import_box_set_item(
    conn,
    *,
    plugin_id: str,
    source_hash: str,
    source_path: str,
    source_kind: str,
    item: dict[str, Any],
    detection: dict[str, Any],
    index: int,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    proposal = detection.get("proposal") if isinstance(detection.get("proposal"), dict) else {}
    barcode = clean_text(detection.get("barcode") or item.get("barcode") or proposal.get("barcode"))
    title = clean_text(proposal.get("title") or proposal.get("name") or item.get("title"))
    media_format = clean_text(proposal.get("format") or item.get("format"))
    if not title:
        raise RuntimeError("Detected box-set is missing a title")
    members = import_box_set_member_list(proposal)
    if len(members) < 2:
        raise RuntimeError("Detected box-set does not contain enough members")
    evidence = import_box_set_evidence(proposal)
    container_id, was_container_created = upsert_import_container(
        conn,
        plugin_id=plugin_id,
        source_hash=source_hash,
        container_type="box_set",
        title=title,
        source_path=source_path,
        source_kind=source_kind,
        barcode=barcode,
        metadata_extra={
            "format": media_format,
            "box_set_evidence": evidence,
            "boxSetEvidence": evidence,
            "member_source": clean_text(proposal.get("memberSource") or proposal.get("member_source") or proposal.get("source")),
            "member_confidence": clean_text(proposal.get("memberConfidence") or proposal.get("member_confidence")),
            "member_count": len(members),
            "import_detected_box_set": True,
        },
        actor=actor,
    )
    imported_members: list[dict[str, Any]] = []
    movie_ids: list[str] = []
    created = 0
    updated = 0
    links = 0
    for member_index, member in enumerate(members[:50], start=1):
        member_item = import_box_set_member_item(item, proposal, member, member_index)
        if not clean_text(member_item.get("title")):
            continue
        movie_id, was_created = upsert_import_movie(conn, plugin_id, member_item)
        if link_import_movie_to_container(
            conn,
            container_id=container_id,
            container_type="box_set",
            movie_id=movie_id,
            sort_order=member_index,
        ):
            links += 1
        movie_ids.append(str(movie_id))
        created += 1 if was_created else 0
        updated += 0 if was_created else 1
        imported_members.append(
            {
                "index": member_index,
                "id": str(movie_id),
                "title": clean_text(member_item.get("title")),
                "year": clean_text(member_item.get("year")),
                "barcode": clean_text(member_item.get("barcode")),
                "action": "created" if was_created else "updated",
            }
        )
    return {
        "containerId": str(container_id),
        "containerTitle": title,
        "containerCreated": was_container_created,
        "memberMovieIds": movie_ids,
        "members": imported_members,
        "created": created,
        "updated": updated,
        "links": links,
        "memberCount": len(imported_members),
        "barcode": barcode,
        "format": media_format,
        "provider": clean_text(proposal.get("provider") or proposal.get("source")),
        "evidence": evidence,
    }


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
    total_items = len(items)
    with connect() as conn:
        if not table_exists(conn, "movies"):
            return {"skipped": True, "reason": "movies table is not initialized"}
        with conn.transaction():
            source_hash = clean_text(result.get("sourceDatabaseHash")) or hashlib.sha256(
                f"{plugin_id}:{result.get('sourcePath')}".encode("utf-8")
            ).hexdigest()
            source_path = clean_text(result.get("sourcePath"))
            source_kind = clean_text(result.get("sourceKind") or plugin_id)
            container_cache: dict[tuple[str, str, str], UUID] = {}
            container_titles: dict[tuple[str, str, str], str] = {}
            container_created: set[UUID] = set()
            container_links = 0
            box_sets_imported = 0
            box_sets_created = 0
            box_sets_updated = 0
            imported = 0
            created = 0
            updated = 0
            linked = 0
            errors = []
            box_set_detection_errors = []
            detected_box_sets: list[dict[str, Any]] = []
            imported_movies: list[dict[str, Any]] = []
            imported_movie_ids: list[str] = []
            created_movie_ids: list[str] = []
            for index, item in enumerate(items[:5000], start=1):
                if not isinstance(item, dict):
                    continue
                try:
                    box_set_detection = detect_import_item_box_set(conn, item, actor)
                    if box_set_detection and box_set_detection.get("error"):
                        box_set_detection_errors.append(
                            {
                                "index": index,
                                "title": clean_text(item.get("title")),
                                "barcode": clean_text(item.get("barcode")),
                                "error": box_set_detection.get("error"),
                            }
                        )
                    if box_set_detection and isinstance(box_set_detection.get("proposal"), dict):
                        with conn.transaction():
                            box_set_result = persist_import_box_set_item(
                                conn,
                                plugin_id=plugin_id,
                                source_hash=source_hash,
                                source_path=source_path,
                                source_kind=source_kind,
                                item=item,
                                detection=box_set_detection,
                                index=index,
                                actor=actor,
                            )
                        imported += 1
                        created += int(box_set_result.get("created") or 0)
                        updated += int(box_set_result.get("updated") or 0)
                        container_links += int(box_set_result.get("links") or 0)
                        container_id = UUID(str(box_set_result["containerId"]))
                        container_key = import_container_release_key(
                            "box_set",
                            clean_text(box_set_result.get("containerTitle")),
                            barcode=box_set_result.get("barcode"),
                            media_format=box_set_result.get("format"),
                        )
                        container_cache[container_key] = container_id
                        container_titles[container_key] = clean_text(box_set_result.get("containerTitle"))
                        if box_set_result.get("containerCreated"):
                            container_created.add(container_id)
                        member_ids = [str(value) for value in box_set_result.get("memberMovieIds") or [] if value]
                        imported_movie_ids.extend(member_ids)
                        created_movie_ids.extend(
                            str(member.get("id"))
                            for member in box_set_result.get("members") or []
                            if member.get("action") == "created" and member.get("id")
                        )
                        detected_box_sets.append(
                            {
                                "index": index,
                                "id": box_set_result.get("containerId"),
                                "title": box_set_result.get("containerTitle"),
                                "barcode": box_set_result.get("barcode"),
                                "provider": box_set_result.get("provider"),
                                "memberCount": box_set_result.get("memberCount"),
                                "created": box_set_result.get("containerCreated"),
                            }
                        )
                        imported_movies.append(
                            {
                                "index": index,
                                "id": box_set_result.get("containerId"),
                                "title": box_set_result.get("containerTitle"),
                                "barcode": box_set_result.get("barcode"),
                                "action": "box_set_created" if box_set_result.get("containerCreated") else "box_set_updated",
                                "memberCount": box_set_result.get("memberCount"),
                                "members": (box_set_result.get("members") or [])[:20],
                                "reviewAction": clean_text(item.get("importReviewAction")),
                            }
                        )
                        continue
                    local_container_cache: dict[tuple[str, str, str], UUID] = {}
                    local_container_titles: dict[tuple[str, str, str], str] = {}
                    local_container_created: set[UUID] = set()
                    local_container_links = 0
                    if import_item_is_box_set_container(item):
                        with conn.transaction():
                            if table_exists(conn, "containers"):
                                for spec in import_item_container_specs(item):
                                    if spec["containerType"] != "box_set":
                                        continue
                                    key = import_container_release_key(
                                        spec["containerType"],
                                        spec["title"],
                                        barcode=spec.get("barcode") or item.get("barcode"),
                                        media_format=spec.get("format") or item.get("format"),
                                    )
                                    spec_container_id = container_cache.get(key) or local_container_cache.get(key)
                                    if not spec_container_id:
                                        local_container_titles[key] = spec["title"]
                                        proposal = spec.get("boxSetProposal") if isinstance(spec.get("boxSetProposal"), dict) else {}
                                        spec_container_id, was_container_created = upsert_import_container(
                                            conn,
                                            plugin_id=plugin_id,
                                            source_hash=source_hash,
                                            container_type=spec["containerType"],
                                            title=spec["title"],
                                            source_path=source_path,
                                            source_kind=source_kind,
                                            barcode=clean_text(spec.get("barcode") or item.get("barcode")),
                                            metadata_extra={
                                                "format": clean_text(spec.get("format") or item.get("format")),
                                                "import_item_type": "box_set",
                                                "box_set_proposal": proposal,
                                                "member_count": spec.get("memberCount") or len(item.get("boxSetMembers") or []),
                                                "members_are_explicit": spec.get("membersAreExplicit") or bool(item.get("boxSetMembers")),
                                            },
                                            actor=actor,
                                        )
                                        local_container_cache[key] = spec_container_id
                                        if was_container_created:
                                            local_container_created.add(spec_container_id)
                        imported += 1
                        box_sets_imported += 1
                        if local_container_created:
                            box_sets_created += 1
                            created += 1
                        else:
                            box_sets_updated += 1
                            updated += 1
                        container_cache.update(local_container_cache)
                        container_titles.update(local_container_titles)
                        container_created.update(local_container_created)
                        imported_movies.append(
                            {
                                "index": index,
                                "id": str(next(iter(local_container_cache.values()), "")),
                                "title": clean_text(item.get("title")),
                                "year": clean_text(item.get("year")),
                                "barcode": clean_text(item.get("barcode")),
                                "action": "container_created" if local_container_created else "container_updated",
                                "reviewAction": clean_text(item.get("importReviewAction")),
                                "itemType": "box_set",
                            }
                        )
                        continue
                    with conn.transaction():
                        movie_id, was_created = upsert_import_movie(conn, plugin_id, item)
                        if table_exists(conn, "containers"):
                            for spec in import_item_container_specs(item):
                                key = import_container_release_key(
                                    spec["containerType"],
                                    spec["title"],
                                    barcode=spec.get("barcode"),
                                    media_format=spec.get("format"),
                                )
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
                                        barcode=clean_text(spec.get("barcode")),
                                        metadata_extra={
                                            "format": clean_text(spec.get("format")),
                                            "member_count": spec.get("memberCount"),
                                            "members_are_explicit": spec.get("membersAreExplicit"),
                                        },
                                        actor=actor,
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

    review = result.get("review") if isinstance(result.get("review"), dict) else {}
    warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
    if box_set_detection_errors:
        warnings = [
            *warnings,
            {
                "code": "box_set_detection_lookup_failed",
                "message": "Some import rows could not be checked for box-set metadata and were imported with the regular movie flow.",
                "items": box_set_detection_errors[:20],
            },
        ]
    summary = {
        "sourceKind": result.get("sourceKind"),
        "sourcePath": result.get("sourcePath"),
        "items": total_items,
        "imported": imported,
        "created": created,
        "updated": updated,
        "linkedToCollection": linked,
        "linkedToContainers": container_links,
        "boxSetsImported": box_sets_imported,
        "boxSetsCreated": box_sets_created,
        "boxSetsUpdated": box_sets_updated,
        "containersCreated": len(container_created),
        "containersTouched": len(container_cache),
        "collectionId": None,
        "collectionCreated": False,
        "metadataRefreshQueued": len(metadata_job_ids),
        "metadataJobIds": metadata_job_ids[:200],
        "boxSetsDetected": len(detected_box_sets),
        "detectedBoxSets": detected_box_sets[:50],
        "rollbackMovieIds": created_movie_ids,
        "movies": imported_movies[:200],
        "review": review,
        "warnings": warnings,
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
    if total_items and imported == 0 and errors:
        first_error = errors[0]
        summary["failed"] = True
        summary["error"] = (
            f"Import source returned {total_items} item(s), but none could be written. "
            f"First error at row {first_error.get('index')}: {first_error.get('error')}"
        )
    elif total_items and imported == 0:
        skipped = review.get("skipped") if isinstance(review.get("skipped"), list) else []
        summary["warnings"] = [
            *warnings,
            {
                "code": "import_no_items_written",
                "message": "Import source returned items, but no rows were written. Check review decisions and skipped rows.",
                "reviewSkipped": len(skipped),
            },
        ]
    return summary


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
            failure_result = getattr(exc, "result", None)
            fail_job(conn, job["id"], str(exc), failure_result if isinstance(failure_result, dict) else {"workerId": worker_id})
            print(json.dumps({"status": "failed", "jobId": str(job["id"]), "error": str(exc)}))
            return 1


def _maybe_enqueue_price_sweep(worker_id: str) -> None:
    """Auto-enqueue a price-alert sweep job if none is pending/running.

    The interval is controlled by the ``DISCVAULT_PRICE_SWEEP_INTERVAL_HOURS``
    environment variable (default: 6 hours).
    """
    try:
        interval_hours = float(os.environ.get("DISCVAULT_PRICE_SWEEP_INTERVAL_HOURS", "6"))
    except ValueError:
        interval_hours = 6.0
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 FROM background_jobs
                    WHERE job_type = %s
                      AND status IN ('pending', 'running')
                    LIMIT 1
                    """,
                    (PRICE_ALERT_JOB_TYPE,),
                )
                if cur.fetchone():
                    return
                cur.execute(
                    """
                    SELECT 1 FROM background_jobs
                    WHERE job_type = %s
                      AND created_at >= now() - (%s * interval '1 hour')
                    LIMIT 1
                    """,
                    (PRICE_ALERT_JOB_TYPE, interval_hours),
                )
                if cur.fetchone():
                    return
                cur.execute(
                    """
                    INSERT INTO background_jobs (job_type, payload)
                    VALUES (%s, %s)
                    """,
                    (PRICE_ALERT_JOB_TYPE, Jsonb(json_ready({"source": "scheduler", "workerId": worker_id}))),
                )
    except Exception:  # noqa: BLE001 - never crash the poll loop
        pass


def _maybe_enqueue_movievault_v2_sync(worker_id: str) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    to_regclass('public.plugins') AS plugins,
                    to_regclass('public.plugin_settings') AS plugin_settings,
                    to_regclass('public.background_jobs') AS background_jobs,
                    to_regclass('public.movievault_v2_sync_state') AS sync_state
                """
            )
            tables = cur.fetchone()
            if not tables or not all(tables.values()):
                return
            cur.execute(
                "SELECT pg_try_advisory_xact_lock(%s) AS acquired",
                (MOVIEVAULT_V2_SCHEDULER_LOCK_KEY,),
            )
            lock_row = cur.fetchone()
            if not bool(lock_row and lock_row.get("acquired")):
                return
            cur.execute(
                """
                SELECT ps.settings, state.last_success_at, state.last_attempt_at
                FROM plugins AS plugin
                JOIN plugin_settings AS ps ON ps.plugin_id = plugin.id
                LEFT JOIN movievault_v2_sync_state AS state
                    ON state.plugin_id = plugin.id
                WHERE plugin.id = %s
                  AND plugin.installed = true
                  AND plugin.enabled = true
                """,
                (MOVIEVAULT_V2_PLUGIN_ID,),
            )
            row = cur.fetchone()
            if not row:
                return
            settings = row.get("settings")
            if not isinstance(settings, dict) or not clean_text(settings.get("origin")):
                return
            interval_hours = _movievault_v2_sync_interval(settings)
            attempts = [
                value
                for value in (row.get("last_success_at"), row.get("last_attempt_at"))
                if isinstance(value, datetime)
            ]
            if attempts:
                latest_attempt = max(attempts)
                age_seconds = (
                    datetime.now(timezone.utc) - latest_attempt.astimezone(timezone.utc)
                ).total_seconds()
                if age_seconds < interval_hours * 3600:
                    return
            cur.execute(
                """
                SELECT 1
                FROM background_jobs
                WHERE job_type = 'plugin.execute'
                  AND status IN ('pending', 'running')
                  AND payload ->> 'pluginId' = %s
                  AND payload ->> 'entrypoint' = 'sync_index'
                LIMIT 1
                """,
                (MOVIEVAULT_V2_PLUGIN_ID,),
            )
            if cur.fetchone():
                return
            cur.execute(
                """
                INSERT INTO background_jobs (job_type, payload)
                VALUES ('plugin.execute', %s)
                """,
                (
                    Jsonb(
                        {
                            "pluginId": MOVIEVAULT_V2_PLUGIN_ID,
                            "entrypoint": "sync_index",
                            "payload": {},
                            "source": "scheduler",
                            "workerId": worker_id,
                        }
                    ),
                ),
            )


def _movievault_v2_sync_interval(settings: dict[str, Any]) -> int:
    value = settings.get("syncIntervalHours")
    if value is None or value == "" or isinstance(value, bool):
        return 6
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 6
    return min(max(parsed, 1), 168)


def work_loop(worker_id: str, poll_interval: float) -> int:
    while not STOP:
        _maybe_enqueue_price_sweep(worker_id)
        _maybe_enqueue_movievault_v2_sync(worker_id)
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
    validate_runtime_secrets()
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    if args.command == "run-once":
        return run_once(args.worker_id)
    # `work` is the container entry point and only depends on postgres having
    # *started*, so it may be the first thing knocking while the database is
    # still coming up. `run-once` is invoked by hand against a stack that is
    # already up, so it fails fast instead — the same split the `tools`-profile
    # compose services use.
    wait_for_database(connect_fn=connect).close()
    return work_loop(args.worker_id, args.poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
