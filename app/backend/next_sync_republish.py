"""Re-publishing the live catalog into the sync change log.

Why this exists
---------------

`GET /api/next/sync/delta` pages a client through `sync_changes` with
`WHERE revision > since`. Until 26.9.50 an empty page was answered with
`nextSince: currentRevision`, which moved a client's cursor onto the tip of a
history it had never been sent. A cursor from a *previous* database -- one
rebuilt or reset, so its counter restarted below the client's number -- matched
nothing on its first pull and took exactly that jump. Everything already in the
new database then sat below the cursor, and `>` only looks forward.

The delta handler no longer does this. But a device that already took the jump
holds a cursor sitting neatly *within* range (`since == currentRevision`), which
from the server is indistinguishable from a device that is up to date. Nothing
in the protocol can tell those apart, so nothing in the protocol can repair it.

Why not "just bootstrap again"
------------------------------

`GET /api/next/sync/bootstrap` is capped: `limit` defaults to 1000 and the
server clamps it at 5000, the cut is alphabetical by sort title, and the
satellite arrays have their own lower caps. A library of 1295 films
re-bootstrapping at the default gets a 1000-film prefix; a library above 5000
films cannot be bootstrapped completely at all (sync-contract §5c, and §5c.1 for
the decision that those caps have to go). So for exactly the collections where
losing the sync hurts most, the bootstrap is not a recovery path.

The delta is. It pages properly -- `hasMore` and `nextSince` -- so a client
walks the whole stream however large it is. This module puts the catalog *into*
that stream: every live entity re-emitted as an ordinary upsert change at a
fresh revision above every client's cursor. On its next pull each device
receives the lot, whatever cursor it holds, with no client change and no
reinstall.

Two callers, one implementation
-------------------------------

* `next_worker.process_sync_catalog_republish` -- the automatic path. Migration
  084 enqueues one `sync.catalog_republish` job, so the repair happens by itself
  on upgrade and an operator has nothing to roll out.
* `app/scripts/republish_sync_stream.py` -- the manual escape hatch, for a
  device that strands later or a run that needs repeating.

Not idempotent, by construction
-------------------------------

Each run appends a new generation of changes; it does not converge on a fixed
point the way a backfill does. That is the mechanism, not an oversight -- a
change already below a cursor cannot be made visible again except by writing a
new one above it. Which is also why the automatic path is a *migration* rather
than a startup hook: a migration runs once per version, and running this on
every boot would make every client re-download the whole catalog on every
restart, forever.
"""

from __future__ import annotations

from typing import Any


#: Job type the worker dispatches on, enqueued by migration 084.
SYNC_CATALOG_REPUBLISH_JOB_TYPE = "sync.catalog_republish"

#: Commit boundary. Only about transaction size -- each emission stands alone.
BATCH_SIZE = 200

#: Entity kinds in emission order: referenced entities before the things that
#: point at them, so a client applying the stream in revision order never holds
#: a dangling reference for longer than one change.
ENTITY_ORDER = (
    "location",
    "series",
    "container",
    "movie",
    "movie_identifier",
    "container_membership",
)

#: How to enumerate the live rows of each kind. `movie_identifier` and
#: `container_membership` are keyed by their owner, because each emission
#: carries that owner's *full* set in one change. Tombstoned rows are excluded
#: everywhere: a delete travels as its own change, and re-emitting one as an
#: upsert would resurrect it on every client at once.
SOURCE_QUERIES = {
    "location": ("locations", "SELECT id FROM locations ORDER BY created_at"),
    "series": ("series", "SELECT id FROM series WHERE deleted_at IS NULL ORDER BY created_at"),
    "container": (
        "containers",
        "SELECT id FROM containers WHERE deleted_at IS NULL ORDER BY created_at",
    ),
    "movie": ("movies", "SELECT id FROM movies WHERE deleted_at IS NULL ORDER BY created_at"),
    "movie_identifier": (
        "movie_identifiers",
        """
        SELECT DISTINCT m.id AS id
        FROM movies m
        JOIN movie_identifiers i ON i.movie_id = m.id
        WHERE m.deleted_at IS NULL
        ORDER BY m.id
        """,
    ),
    "container_membership": (
        "container_movies",
        """
        SELECT DISTINCT c.id AS id
        FROM containers c
        JOIN container_movies cm ON cm.container_id = c.id
        WHERE c.deleted_at IS NULL
        ORDER BY c.id
        """,
    ),
}


def emitters():
    """The `emit_*` helpers, or a message saying why they are out of reach.

    Returns ``(emitters, error)``. A delta payload is the whole entity and only
    `next_app` can build one, so the import is deliberate rather than avoidable
    -- and deliberately *lazy*, because importing `next_app` constructs the
    Flask application and demands the runtime secrets. The worker and the API
    share a container, so both reach them; a sweep pointed at a copied database
    does not, and gets a reason instead of a traceback.
    """

    try:
        try:
            from .next_app import (
                emit_container_change,
                emit_container_membership_change,
                emit_location_change,
                emit_movie_change,
                emit_movie_identifiers_change,
                emit_series_change,
            )
        except ImportError:
            from next_app import (  # type: ignore[no-redef]
                emit_container_change,
                emit_container_membership_change,
                emit_location_change,
                emit_movie_change,
                emit_movie_identifiers_change,
                emit_series_change,
            )
    except Exception as exc:  # pragma: no cover - missing secrets, or no Flask
        return None, f"{type(exc).__name__}: {exc}"

    return (
        {
            "location": lambda conn, entity_id: emit_location_change(
                conn, entity_id, operation="upsert"
            ),
            "series": lambda conn, entity_id: emit_series_change(
                conn, entity_id, operation="upsert"
            ),
            "container": lambda conn, entity_id: emit_container_change(
                conn, entity_id, operation="upsert"
            ),
            "movie": lambda conn, entity_id: emit_movie_change(conn, entity_id),
            "movie_identifier": lambda conn, entity_id: emit_movie_identifiers_change(
                conn, entity_id
            ),
            "container_membership": lambda conn, entity_id: emit_container_membership_change(
                conn, entity_id
            ),
        },
        None,
    )


def table_exists(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS name", (f"public.{table_name}",))
        row = cur.fetchone()
    return bool(row and row["name"])


def current_revision(conn) -> int:
    if not table_exists(conn, "sync_state"):
        return 0
    with conn.cursor() as cur:
        cur.execute("SELECT revision FROM sync_state WHERE id='global'")
        row = cur.fetchone()
    return int(row["revision"]) if row else 0


def parse_entities(raw: str | None) -> list[str]:
    """Validate a comma-separated kind list, or default to all of them.

    Raises `ValueError` on an unknown kind rather than dropping it: a typo that
    quietly republished less than asked would look like a successful run and
    leave the device still stranded.
    """

    if not raw:
        return list(ENTITY_ORDER)
    chosen = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [kind for kind in chosen if kind not in ENTITY_ORDER]
    if unknown:
        raise ValueError(
            f"Unknown entity kind(s): {', '.join(unknown)}. "
            f"Choose from: {', '.join(ENTITY_ORDER)}"
        )
    return chosen


def collect_targets(conn, entities: list[str]) -> dict[str, list]:
    """Which ids each selected kind would republish.

    A kind whose table is absent reports an empty list rather than failing: the
    schema families here (locations, series, containers) arrived in different
    releases and a database may predate any of them.
    """

    targets: dict[str, list] = {}
    for kind in ENTITY_ORDER:
        if kind not in entities:
            continue
        table, query = SOURCE_QUERIES[kind]
        if not table_exists(conn, table):
            targets[kind] = []
            continue
        with conn.cursor() as cur:
            cur.execute(query)
            targets[kind] = [row["id"] for row in cur.fetchall()]
    return targets


def republish(conn, targets: dict[str, list], emitter_map: dict) -> dict:
    """Emit every target, committing per batch. Returns per-kind counts.

    A kind is emitted whole before the next one starts, so the dependency order
    above holds on the wire.
    """

    emitted: dict[str, int] = {}
    skipped: dict[str, int] = {}
    pending = 0
    for kind in ENTITY_ORDER:
        ids = targets.get(kind) or []
        emitted[kind] = 0
        skipped[kind] = 0
        emit = emitter_map[kind]
        for entity_id in ids:
            # A helper returns 0 when the sync tables are absent or the entity
            # no longer builds. Counted rather than raised: one unpublishable
            # row must not cost the rest of the run.
            if emit(conn, entity_id):
                emitted[kind] += 1
            else:
                skipped[kind] += 1
            pending += 1
            if pending >= BATCH_SIZE:
                conn.commit()
                pending = 0
    conn.commit()
    return {"emitted": emitted, "skipped": skipped}


def run_catalog_republish(conn, *, entities: list[str] | None = None) -> dict[str, Any]:
    """Do the whole job on an open connection, and describe what happened.

    Raises `RuntimeError` when the emitters are out of reach: there is no
    reduced mode worth offering, and a run that published nothing must not
    report success -- that would leave the stranded device stranded while the
    job log says it was repaired.
    """

    emitter_map, error = emitters()
    if emitter_map is None:
        raise RuntimeError(
            "Cannot reach the delta emitters, so there is nothing this run could "
            f"publish ({error}). Run it where the runtime secrets are set."
        )
    selected = list(entities) if entities else list(ENTITY_ORDER)
    targets = collect_targets(conn, selected)
    revision_before = current_revision(conn)
    result = republish(conn, targets, emitter_map)
    return {
        "entities": selected,
        "targets": {kind: len(ids) for kind, ids in targets.items()},
        "totalTargets": sum(len(ids) for ids in targets.values()),
        "revisionBefore": revision_before,
        "revisionAfter": current_revision(conn),
        **result,
    }
