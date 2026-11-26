"""Safety snapshots for the link wizard (sync-contract §5f).

Before an app may replace the server's library, the server takes a copy of what
is about to be replaced and refuses to go further without it. This module owns
that copy: creating it, remembering it, validating the id a later request
presents, and pruning old ones. `next_app` owns the routes and the permission
gate (`require_next_library_replace_actor`).

The copy is an ordinary functional backup -- the same engine and the same
archive the Backup page produces -- stored in the backup directory, so an
administrator restores it through the existing restore routes. The app never
restores anything.

Design choices worth keeping in mind:

* **Synchronous.** `POST /backup/create` already builds its archive inside the
  request, and the API's gunicorn timeout is 180 seconds. A library of about
  1500 films with artwork fits; a snapshot that outgrows it fails loudly
  (`snapshot_failed`) and nothing is recorded. The alternative -- a job plus a
  status route -- would give the client a third state to resume from and buy
  nothing a library this size needs.
* **A row means the copy exists.** The row is written only after the archive has
  been written, read back and validated, so "the id is valid" and "the file is
  whole" cannot come apart. An id whose file has since been deleted is refused
  (`snapshot_file_missing`) rather than trusted.
* **An id belongs to whoever took it.** Another user's id answers exactly like an
  unknown one, so ids cannot be probed.
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

try:  # pragma: no cover - exercised indirectly by both layouts
    from .next_backup import (
        BackupError,
        export_functional_backup,
        normalize_scopes,
        stored_backup_path,
        validate_backup_zip,
    )
    from .next_common import NextApiError, json_ready
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_backup import (
        BackupError,
        export_functional_backup,
        normalize_scopes,
        stored_backup_path,
        validate_backup_zip,
    )
    from next_common import NextApiError, json_ready

logger = logging.getLogger(__name__)

#: How long a snapshot id stays usable by `retain` and `supersedeTombstone`.
#: Long enough for a wizard run over a large library to push everything and then
#: retain, short enough that a stale id cannot be used for something decided
#: yesterday. Named so a deployment that needs a different window changes one
#: line, in code, with a test.
SNAPSHOT_VALID_FOR_SECONDS = 60 * 60
#: One snapshot per instance per this many seconds. A snapshot reads and
#: compresses the whole library, so it is the expensive call.
SNAPSHOT_MIN_INTERVAL_SECONDS = 30
#: How many snapshots are kept. Older ones are deleted together with their
#: archive. Only files this module recorded are ever deleted: an administrator's
#: own backups in the same directory are never touched.
SNAPSHOT_KEEP = 5
#: Free space required before starting, at least. The real requirement is
#: `SNAPSHOT_FREE_SPACE_FACTOR` times the newest snapshot, when that is larger.
SNAPSHOT_MIN_FREE_BYTES = 256 * 1024 * 1024
SNAPSHOT_FREE_SPACE_FACTOR = 1.5
SNAPSHOT_FILE_PREFIX = "discvault-next-sync-snapshot-"
#: What the safety copy covers: the films, containers and their links, every
#: person, the film artwork (which includes the user's own images, that exist
#: nowhere else once pushed) and the personal lists the wizard may replace.
#: People artwork is left out: it is re-fetchable and is the bulk of the size.
SNAPSHOT_SCOPES = ("collection", "people", "film_artwork", "media_groups", "personal_lists")

_ADVISORY_LOCK_KEY = "discvault.sync_library_snapshot"


def _snapshot_error(message: str, status: int, code: str) -> NextApiError:
    return NextApiError(message, status, code=code)


def parse_snapshot_id(value: Any) -> uuid.UUID:
    """The id a request presents, or a `snapshot_required` refusal."""
    text = str(value or "").strip()
    if not text:
        raise _snapshot_error("snapshotId is required", 400, "snapshot_required")
    try:
        return uuid.UUID(text)
    except (ValueError, AttributeError, TypeError) as exc:
        raise _snapshot_error("snapshotId is not valid", 400, "snapshot_required") from exc


def _row_to_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    created_at = row["created_at"]
    expires_at = created_at + timedelta(seconds=SNAPSHOT_VALID_FOR_SECONDS)
    return {
        "snapshotId": str(row["id"]),
        "createdAt": created_at.isoformat(),
        "expiresAt": expires_at.isoformat(),
        "counts": row.get("counts") or {},
        "fileName": row["file_name"],
        "sizeBytes": int(row["size_bytes"]),
        "sha256": row["sha256"],
    }


def require_valid_snapshot(conn, snapshot_id: Any, actor: dict[str, Any], *, backup_dir: Path) -> dict[str, Any]:
    """The one validation behind `retain` and `supersedeTombstone`.

    Raises a `NextApiError` with a stable `errorCode` for each way an id can be
    unusable: `snapshot_required` (missing or malformed), `snapshot_unknown`
    (no such id, or somebody else's), `snapshot_expired` (older than
    `SNAPSHOT_VALID_FOR_SECONDS`) and `snapshot_file_missing` (the archive is no
    longer on disk, or no longer the size it was).
    """
    sid = parse_snapshot_id(snapshot_id)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, created_by, created_at, file_name, sha256, size_bytes, counts,
                   EXTRACT(EPOCH FROM (clock_timestamp() - created_at)) AS age_seconds
            FROM sync_library_snapshots
            WHERE id=%s
            """,
            (sid,),
        )
        row = cur.fetchone()
    actor_id = str(actor.get("id") or "")
    if not row or not actor_id or str(row["created_by"] or "") != actor_id:
        # The same answer for "no such id" and "somebody else's", on purpose.
        raise _snapshot_error("Unknown snapshot", 404, "snapshot_unknown")
    if float(row["age_seconds"]) > SNAPSHOT_VALID_FOR_SECONDS:
        raise _snapshot_error(
            "This snapshot is too old to be used; take a new one", 410, "snapshot_expired"
        )
    try:
        path = stored_backup_path(backup_dir, row["file_name"])
        intact = path.is_file() and path.stat().st_size == int(row["size_bytes"])
    except (BackupError, OSError):
        intact = False
    if not intact:
        raise _snapshot_error(
            "The snapshot's archive is no longer available; take a new one", 409, "snapshot_file_missing"
        )
    snapshot = _row_to_snapshot(row)
    snapshot["ageSeconds"] = float(row["age_seconds"])
    return snapshot


def snapshot_is_valid(conn, snapshot_id: Any, actor: dict[str, Any], *, backup_dir: Path) -> bool:
    """`require_valid_snapshot` as a yes/no, for callers that ignore instead of refuse."""
    try:
        require_valid_snapshot(conn, snapshot_id, actor, backup_dir=backup_dir)
    except NextApiError:
        return False
    return True


def _check_rate_limit(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXTRACT(EPOCH FROM (clock_timestamp() - created_at)) AS age_seconds
            FROM sync_library_snapshots
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
    if row and float(row["age_seconds"]) < SNAPSHOT_MIN_INTERVAL_SECONDS:
        wait = max(1, int(SNAPSHOT_MIN_INTERVAL_SECONDS - float(row["age_seconds"])) + 1)
        raise _snapshot_error(
            f"A snapshot was taken moments ago; try again in {wait} seconds",
            429,
            "snapshot_rate_limited",
        )


def _check_free_space(conn, backup_dir: Path) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT size_bytes FROM sync_library_snapshots ORDER BY created_at DESC LIMIT 1")
        row = cur.fetchone()
    newest = int(row["size_bytes"]) if row else 0
    required = max(SNAPSHOT_MIN_FREE_BYTES, int(newest * SNAPSHOT_FREE_SPACE_FACTOR))
    free = shutil.disk_usage(backup_dir).free
    if free < required:
        raise _snapshot_error(
            f"Not enough free disk space for a snapshot: {free} bytes free, {required} needed",
            507,
            "snapshot_no_space",
        )


def prune_snapshots(conn, backup_dir: Path, *, keep: int = SNAPSHOT_KEEP) -> int:
    """Delete everything but the newest `keep` snapshots, rows and archives.

    Only rows in `sync_library_snapshots` are considered, so a backup an
    administrator made by hand in the same directory is never a candidate.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, file_name
            FROM sync_library_snapshots
            ORDER BY created_at DESC
            OFFSET %s
            """,
            (max(0, int(keep)),),
        )
        stale = cur.fetchall()
    removed = 0
    for row in stale:
        try:
            path = stored_backup_path(backup_dir, row["file_name"])
            path.unlink(missing_ok=True)
        except (BackupError, OSError):
            logger.warning("could not remove snapshot archive %s", row["file_name"], exc_info=True)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sync_library_snapshots WHERE id=%s", (row["id"],))
        removed += 1
    return removed


def create_library_snapshot(
    conn,
    *,
    actor: dict[str, Any],
    data_dir: Path,
    backup_dir: Path,
    counts: dict[str, int],
    generator: dict[str, Any],
) -> dict[str, Any]:
    """Take the safety copy and return what the client needs to keep.

    Order matters: serialise concurrent callers, apply the cheap refusals (rate
    limit, disk space), write and validate the archive, and only then record it.
    A failure after the file exists removes the file, so nothing half-made is
    ever left looking like a snapshot.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    with conn.cursor() as cur:
        # Two simultaneous requests would both pass the rate limit below.
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_ADVISORY_LOCK_KEY,))
    _check_rate_limit(conn)
    _check_free_space(conn, backup_dir)

    snapshot_id = uuid.uuid4()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    file_name = f"{SNAPSHOT_FILE_PREFIX}{stamp}-{snapshot_id.hex[:8]}.zip"
    output_path = stored_backup_path(backup_dir, file_name)
    try:
        summary = export_functional_backup(
            conn,
            output_path,
            data_dir=data_dir,
            scopes=normalize_scopes(list(SNAPSHOT_SCOPES)),
            generator=generator,
            requested_by=actor,
        )
        report = validate_backup_zip(output_path)
        if not report.get("valid"):
            raise BackupError("; ".join(str(item) for item in report.get("errors") or ["invalid archive"]))
        sha256 = summary.get("sha256")
        if not sha256:
            raise BackupError("the archive could not be checksummed")
        size_bytes = int(summary["sizeBytes"])
    except Exception:
        logger.exception("snapshot creation failed")
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise _snapshot_error("Could not create the snapshot", 500, "snapshot_failed") from None

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sync_library_snapshots
                    (id, created_by, created_at, file_name, sha256, size_bytes, counts)
                VALUES (%s, %s, clock_timestamp(), %s, %s, %s, %s)
                RETURNING id, created_by, created_at, file_name, sha256, size_bytes, counts
                """,
                (snapshot_id, actor.get("id"), file_name, sha256, size_bytes, Jsonb(json_ready(counts))),
            )
            row = cur.fetchone()
        prune_snapshots(conn, backup_dir)
    except Exception:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    snapshot = _row_to_snapshot(row)
    snapshot["validForSeconds"] = SNAPSHOT_VALID_FOR_SECONDS
    return snapshot
