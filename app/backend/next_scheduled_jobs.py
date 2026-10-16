"""Built-in scheduler for recurring background jobs (issue #812).

Two jobs can run on a timer without any external cron:

* ``backup`` -- a functional collection backup, skipped when nothing changed
  since the last scheduled one, and pruned to the newest ``keep`` archives.
* ``jellyfin`` -- the Jellyfin library sync, optionally followed by the
  watched-history sync.

Configuration and run state live in ``app_settings`` (``scheduled_jobs`` and
``scheduled_jobs_state``). The worker calls :func:`enqueue_due_jobs` from its
poll loop; the jobs themselves run through the normal job queue, so they show up
in the job list like any manual run.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

try:
    from .next_backup import SCOPE_COLLECTION
    from .next_backup import SCOPE_FILM_ARTWORK
    from .next_backup import SCOPE_MEDIA_GROUPS
    from .next_backup import SCOPE_PEOPLE
    from .next_backup import SCOPE_PEOPLE_ARTWORK
    from .next_backup import SCOPE_PERSONAL_LISTS
    from .next_backup import backup_specs
    from .next_backup import export_functional_backup
    from .next_backup import normalize_scopes
    from .next_backup import table_exists
except ImportError:  # pragma: no cover - supports python next_worker.py
    from next_backup import SCOPE_COLLECTION
    from next_backup import SCOPE_FILM_ARTWORK
    from next_backup import SCOPE_MEDIA_GROUPS
    from next_backup import SCOPE_PEOPLE
    from next_backup import SCOPE_PEOPLE_ARTWORK
    from next_backup import SCOPE_PERSONAL_LISTS
    from next_backup import backup_specs
    from next_backup import export_functional_backup
    from next_backup import normalize_scopes
    from next_backup import table_exists

SCHEDULED_BACKUP_JOB_TYPE = "backup.scheduled"
JELLYFIN_PLUGIN_ID = "jellyfin"

CONFIG_KEY = "scheduled_jobs"
STATE_KEY = "scheduled_jobs_state"

SCHEDULED_BACKUP_PREFIX = "discvault-scheduled-"
_SCHEDULED_BACKUP_RE = re.compile(r"^discvault-scheduled-\d{8}-\d{6}\.zip$")

MIN_INTERVAL_HOURS = 1
MAX_INTERVAL_HOURS = 24 * 30
MIN_KEEP = 1
MAX_KEEP = 90

DEFAULT_BACKUP_SCOPES = (
    SCOPE_COLLECTION,
    SCOPE_PEOPLE,
    SCOPE_FILM_ARTWORK,
    SCOPE_PEOPLE_ARTWORK,
    SCOPE_MEDIA_GROUPS,
)

DEFAULT_CONFIG: dict[str, Any] = {
    "backup": {
        "enabled": False,
        "intervalHours": 24,
        "onlyIfChanged": True,
        "keep": 7,
        "scopes": list(DEFAULT_BACKUP_SCOPES),
    },
    "jellyfin": {
        "enabled": False,
        "intervalHours": 24,
        "includeWatched": True,
        # Who the watched history is attached to. Set from the admin who saved
        # the schedule; the watched-history sync is skipped without one.
        "requestedBy": None,
    },
}


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _clamped_int(value: Any, default: int, low: int, high: int) -> int:
    if value is None or value == "" or isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, low), high)


def normalize_config(raw: Any) -> dict[str, Any]:
    """Return a complete, validated config; unknown or malformed input falls back to defaults."""
    raw = raw if isinstance(raw, dict) else {}
    backup_raw = raw.get("backup") if isinstance(raw.get("backup"), dict) else {}
    jellyfin_raw = raw.get("jellyfin") if isinstance(raw.get("jellyfin"), dict) else {}
    defaults = DEFAULT_CONFIG

    scopes_raw = backup_raw.get("scopes")
    scopes = normalize_scopes(scopes_raw) if isinstance(scopes_raw, list) and scopes_raw else list(DEFAULT_BACKUP_SCOPES)

    actor = jellyfin_raw.get("requestedBy")
    if isinstance(actor, dict) and actor.get("id"):
        actor = {
            "id": str(actor.get("id")),
            "username": str(actor.get("username") or ""),
            "role": str(actor.get("role") or ""),
        }
    else:
        actor = None

    return {
        "backup": {
            "enabled": _bool(backup_raw.get("enabled"), defaults["backup"]["enabled"]),
            "intervalHours": _clamped_int(
                backup_raw.get("intervalHours"), defaults["backup"]["intervalHours"], MIN_INTERVAL_HOURS, MAX_INTERVAL_HOURS
            ),
            "onlyIfChanged": _bool(backup_raw.get("onlyIfChanged"), defaults["backup"]["onlyIfChanged"]),
            "keep": _clamped_int(backup_raw.get("keep"), defaults["backup"]["keep"], MIN_KEEP, MAX_KEEP),
            "scopes": scopes,
        },
        "jellyfin": {
            "enabled": _bool(jellyfin_raw.get("enabled"), defaults["jellyfin"]["enabled"]),
            "intervalHours": _clamped_int(
                jellyfin_raw.get("intervalHours"), defaults["jellyfin"]["intervalHours"], MIN_INTERVAL_HOURS, MAX_INTERVAL_HOURS
            ),
            "includeWatched": _bool(jellyfin_raw.get("includeWatched"), defaults["jellyfin"]["includeWatched"]),
            "requestedBy": actor,
        },
    }


def _setting(conn, key: str) -> Any:
    if not table_exists(conn, "app_settings"):
        return None
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM app_settings WHERE key = %s AND is_secret = false", (key,))
        row = cur.fetchone()
    return row["value"] if row else None


def _store_setting(conn, key: str, value: Any, actor_id: Any = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app_settings (key, value, is_secret, updated_at, updated_by)
            VALUES (%s, %s, false, now(), %s)
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                updated_at = now(),
                updated_by = EXCLUDED.updated_by
            """,
            (key, Jsonb(value), actor_id),
        )


def load_config(conn) -> dict[str, Any]:
    return normalize_config(_setting(conn, CONFIG_KEY))


def save_config(conn, raw: Any, *, actor_id: Any = None) -> dict[str, Any]:
    config = normalize_config(raw)
    _store_setting(conn, CONFIG_KEY, config, actor_id)
    return config


def load_state(conn) -> dict[str, Any]:
    state = _setting(conn, STATE_KEY)
    return state if isinstance(state, dict) else {}


def _update_state(conn, job: str, **values: Any) -> None:
    state = load_state(conn)
    entry = dict(state.get(job) or {})
    entry.update(values)
    state[job] = entry
    _store_setting(conn, STATE_KEY, state)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def is_due(last_enqueued_at: Any, interval_hours: int, *, now: datetime | None = None) -> bool:
    last = _parse_time(last_enqueued_at)
    if last is None:
        return True
    now = now or datetime.now(timezone.utc)
    return (now - last).total_seconds() >= interval_hours * 3600


def data_fingerprint(conn, scopes: list[str]) -> str:
    """Hash of the rows a backup with these scopes would contain.

    Row contents rather than a count or a newest-timestamp: edits, deletes and
    tables without an ``updated_at`` all change it, so "only if changed" never
    skips a backup that would have differed.
    """
    from psycopg import sql

    digest = hashlib.sha256()
    for spec in backup_specs(scopes):
        digest.update(spec.name.encode("utf-8"))
        if not table_exists(conn, spec.name):
            continue
        columns = sql.SQL(", ").join(sql.Identifier(column) for column in spec.columns)
        query = sql.SQL(
            "SELECT md5(coalesce(string_agg(row_text, '|' ORDER BY row_text), '')) AS h "
            "FROM (SELECT ROW({columns})::text AS row_text FROM {table}) AS rows"
        ).format(columns=columns, table=sql.Identifier(spec.name))
        with conn.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()
        digest.update(str((row or {}).get("h") or "").encode("utf-8"))
    return digest.hexdigest()


def prune_scheduled_backups(backup_dir: Path, keep: int) -> list[str]:
    """Delete all but the newest ``keep`` scheduled archives; manual backups are never touched."""
    if not backup_dir.exists():
        return []
    archives = sorted(
        (path for path in backup_dir.iterdir() if path.is_file() and _SCHEDULED_BACKUP_RE.match(path.name)),
        key=lambda path: path.name,
        reverse=True,
    )
    removed: list[str] = []
    for path in archives[max(keep, MIN_KEEP):]:
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(path.name)
    return removed


def run_scheduled_backup(
    conn,
    *,
    backup_dir: Path,
    data_dir: Path,
    generator: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one scheduled backup according to the stored config. Returns a job-result dict."""
    config = load_config(conn)["backup"]
    if not config["enabled"]:
        return {"status": "skipped", "reason": "disabled"}
    scopes = config["scopes"]
    now = now or datetime.now(timezone.utc)
    fingerprint = data_fingerprint(conn, scopes) if config["onlyIfChanged"] else None
    previous = load_state(conn).get("backup") or {}
    if (
        config["onlyIfChanged"]
        and previous.get("fingerprint") == fingerprint
        and previous.get("fileName")
        and (backup_dir / str(previous["fileName"])).is_file()
    ):
        _update_state(conn, "backup", lastRunAt=now.isoformat(), lastStatus="skipped_unchanged")
        return {"status": "skipped", "reason": "unchanged", "lastBackup": previous.get("fileName")}

    file_name = f"{SCHEDULED_BACKUP_PREFIX}{now.strftime('%Y%m%d-%H%M%S')}.zip"
    summary = export_functional_backup(
        conn,
        backup_dir / file_name,
        data_dir=data_dir,
        scopes=scopes,
        include_personal_lists=SCOPE_PERSONAL_LISTS in scopes,
        generator={**(generator or {}), "description": "Scheduled DiscVault backup"},
        requested_by={"role": "system"},
    )
    removed = prune_scheduled_backups(backup_dir, config["keep"])
    _update_state(
        conn,
        "backup",
        lastRunAt=now.isoformat(),
        lastStatus="created",
        fingerprint=fingerprint,
        fileName=file_name,
        sizeBytes=summary.get("sizeBytes"),
    )
    return {
        "status": "created",
        "fileName": file_name,
        "sizeBytes": summary.get("sizeBytes"),
        "sha256": summary.get("sha256"),
        "pruned": removed,
    }


def _job_active(cur, job_type: str, plugin_id: str | None = None, entrypoint: str | None = None) -> bool:
    if plugin_id:
        cur.execute(
            """
            SELECT 1 FROM background_jobs
            WHERE job_type = 'plugin.execute'
              AND status IN ('pending', 'running')
              AND payload ->> 'pluginId' = %s
              AND payload ->> 'entrypoint' = %s
            LIMIT 1
            """,
            (plugin_id, entrypoint),
        )
    else:
        cur.execute(
            "SELECT 1 FROM background_jobs WHERE job_type = %s AND status IN ('pending', 'running') LIMIT 1",
            (job_type,),
        )
    return cur.fetchone() is not None


def _jellyfin_ready(cur) -> bool:
    cur.execute(
        "SELECT 1 FROM plugins WHERE id = %s AND installed = true AND enabled = true",
        (JELLYFIN_PLUGIN_ID,),
    )
    return cur.fetchone() is not None


def enqueue_due_jobs(conn, worker_id: str, *, now: datetime | None = None) -> list[str]:
    """Queue every scheduled job that is enabled and due. Returns what was queued."""
    if not (table_exists(conn, "app_settings") and table_exists(conn, "background_jobs")):
        return []
    now = now or datetime.now(timezone.utc)
    config = load_config(conn)
    state = load_state(conn)
    queued: list[str] = []
    source = {"source": "scheduler", "workerId": worker_id}

    with conn.cursor() as cur:
        backup = config["backup"]
        if (
            backup["enabled"]
            and is_due((state.get("backup") or {}).get("lastEnqueuedAt"), backup["intervalHours"], now=now)
            and not _job_active(cur, SCHEDULED_BACKUP_JOB_TYPE)
        ):
            cur.execute(
                "INSERT INTO background_jobs (job_type, payload) VALUES (%s, %s)",
                (SCHEDULED_BACKUP_JOB_TYPE, Jsonb(dict(source))),
            )
            queued.append("backup")

        jellyfin = config["jellyfin"]
        if (
            jellyfin["enabled"]
            and is_due((state.get("jellyfin") or {}).get("lastEnqueuedAt"), jellyfin["intervalHours"], now=now)
            and table_exists(conn, "plugins")
            and _jellyfin_ready(cur)
        ):
            entrypoints = ["sync_library"]
            if jellyfin["includeWatched"] and jellyfin["requestedBy"]:
                entrypoints.append("sync_personal_lists")
            for entrypoint in entrypoints:
                if _job_active(cur, "plugin.execute", JELLYFIN_PLUGIN_ID, entrypoint):
                    continue
                job_payload: dict[str, Any] = {
                    "pluginId": JELLYFIN_PLUGIN_ID,
                    "entrypoint": entrypoint,
                    "payload": {},
                    **source,
                }
                if entrypoint == "sync_personal_lists":
                    job_payload["requestedBy"] = jellyfin["requestedBy"]
                cur.execute(
                    "INSERT INTO background_jobs (job_type, payload) VALUES ('plugin.execute', %s)",
                    (Jsonb(job_payload),),
                )
                queued.append(f"jellyfin.{entrypoint}")

    for job in ("backup", "jellyfin"):
        if any(item == job or item.startswith(f"{job}.") for item in queued):
            _update_state(conn, job, lastEnqueuedAt=now.isoformat())
    return queued
