"""Permanent removal of artwork that has sat in the trash long enough.

Hiding a poster or backdrop marks its `entity_media` link `deleted_at` and sets
a `purge_after` from the configured retention window; this module is what
finally deletes the link, the `media_assets` row nothing points at any more, and
the local file underneath it.

**Why it lives here rather than in `next_app`.** It used to run inside
`GET /api/next/health` -- a liveness probe fired every ten seconds by the
container health check, doing database deletes and file unlinks on each call.
Under I/O load that probe could exceed its own timeout and mark a healthy
container unhealthy, which is a restart caused by cleanup that was never urgent
in the first place (App-Guidance PERF-02, `Flux76HQ/App-Guidance#153`). The
worker owns it now, and the worker cannot import `next_app`, so the logic moved
to a module both sides can reach.

Nothing about *what* gets purged changed -- only how often, and by whom.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from .next_common import table_exists
    from .next_import import clean_text
except ImportError:  # pragma: no cover - direct execution from app/backend
    from next_common import table_exists
    from next_import import clean_text


#: Queued by the worker's poll loop, not by anything a user waits on.
ARTWORK_TRASH_PURGE_JOB_TYPE = "artwork.trash_purge"

#: How often the worker enqueues a purge. Retention is measured in days (7 by
#: default), so an hour of slack costs nothing and keeps the queue quiet.
DEFAULT_PURGE_INTERVAL_HOURS = 1.0

EMPTY_PURGE_SUMMARY: dict[str, Any] = {"purgedLinks": 0, "purgedAssets": 0, "purgedFiles": 0}


def _artwork_data_dir() -> Path:
    """Self-contained duplicate of next_app.legacy_data_dir(). Kept local to
    avoid importing next_app from a module the worker loads without the Flask
    app -- the same reason next_movievault_v2_posters keeps its own copy."""
    raw = (
        os.environ.get("DISCVAULT_LEGACY_DATA_DIR")
        or os.environ.get("DISCVAULT_SQLITE_IMPORT_DATA")
        or "/data"
    )
    return Path(raw).expanduser()


def delete_local_media_asset_file(asset: dict[str, Any]) -> bool:
    if clean_text(asset.get("storage_backend")) != "local":
        return False
    storage_key = clean_text(asset.get("storage_key"))
    if not storage_key:
        return False
    data_dir = _artwork_data_dir().resolve()
    target = (data_dir / storage_key).resolve()
    try:
        target.relative_to(data_dir)
    except ValueError:
        return False
    if not target.is_file():
        return False
    target.unlink()
    return True


def purge_expired_artwork_trash(conn) -> dict[str, Any]:
    if not table_exists(conn, "entity_media") or not table_exists(conn, "media_assets"):
        return dict(EMPTY_PURGE_SUMMARY)
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM entity_media
            WHERE deleted_at IS NOT NULL
              AND purge_after IS NOT NULL
              AND purge_after <= now()
            RETURNING media_id
            """
        )
        deleted_media_ids = [row["media_id"] for row in cur.fetchall()]
        purged_assets = 0
        purged_files = 0
        for media_id in deleted_media_ids:
            cur.execute("SELECT COUNT(*)::int AS refs FROM entity_media WHERE media_id=%s", (media_id,))
            if int((cur.fetchone() or {}).get("refs") or 0) > 0:
                continue
            cur.execute(
                """
                DELETE FROM media_assets
                WHERE id=%s
                  AND kind IN ('poster', 'backdrop')
                RETURNING id, storage_backend, storage_key
                """,
                (media_id,),
            )
            asset = cur.fetchone()
            if not asset:
                continue
            purged_assets += 1
            try:
                if delete_local_media_asset_file(asset):
                    purged_files += 1
            except OSError:
                pass
    return {"purgedLinks": len(deleted_media_ids), "purgedAssets": purged_assets, "purgedFiles": purged_files}


def purge_interval_hours() -> float:
    try:
        value = float(os.environ.get("DISCVAULT_ARTWORK_TRASH_PURGE_INTERVAL_HOURS", ""))
    except ValueError:
        return DEFAULT_PURGE_INTERVAL_HOURS
    if value <= 0:
        return DEFAULT_PURGE_INTERVAL_HOURS
    return value
