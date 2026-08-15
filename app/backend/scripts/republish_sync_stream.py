#!/usr/bin/env python3
"""Re-publish the live catalog into the sync change log, to heal stranded clients.

Why a client can be stranded
----------------------------

`GET /api/next/sync/delta` pages a client through `sync_changes` with
`WHERE revision > since`. Until 26.9.x an empty page was answered with
`nextSince: currentRevision`, which moved a client's cursor onto the tip of a
history it had never been sent. A cursor from a *previous* database -- one
rebuilt or reset, so its counter restarted below the client's number -- matched
nothing on its first pull and took exactly that jump. Everything already in the
new database then sat below the cursor, and `>` only looks forward.

The delta handler no longer does this, and an out-of-range cursor now replays
from the start. But a device that already took the jump holds a cursor sitting
neatly *within* range (`since == currentRevision`), which from the server is
indistinguishable from a device that is simply up to date. Nothing in the
protocol can tell those apart, so nothing in the protocol can repair it.

Why this and not "just bootstrap again"
---------------------------------------

`GET /api/next/sync/bootstrap` is capped: `limit` defaults to 1000 and the
server clamps it at 5000, the cut is alphabetical by sort title, and the
satellite arrays have their own lower caps. A library of 1295 films
re-bootstrapping at the default gets a 1000-film prefix; a library above 5000
films cannot be bootstrapped completely at all (sync-contract §5c). So for
exactly the collections where losing the sync hurts most, the bootstrap is not a
recovery path.

The delta is. It pages properly -- `hasMore` and `nextSince` -- so a client
walks the whole stream however large it is. This script puts the catalog *into*
that stream: every live entity re-emitted as an ordinary upsert change at a
fresh revision above every client's cursor. On its next pull each device
receives the lot, whatever cursor it holds, with no client change and no
reinstall.

What it is not
--------------

**Not idempotent, by construction.** Each run appends a new generation of
changes; it does not converge on a fixed point the way a backfill does. That is
the mechanism, not an oversight -- a change already below a cursor cannot be
made visible again except by writing a new one above it. Run it when a device is
known to be stranded, not on a schedule.

**Not a repair for divergent data.** It republishes what the server holds. A
client with local edits it never pushed will see the server's version win for
the fields the entity carries, exactly as any other delta.

Order of emission
-----------------

Locations, then series, then containers, then movies, then movie identifiers,
then container membership. Referenced entities are published before the things
that point at them, so a client applying the stream in revision order never
holds a dangling reference for longer than one change.

Usage::

    DATABASE_URL=postgres://… python app/scripts/republish_sync_stream.py
    DATABASE_URL=postgres://… python app/scripts/republish_sync_stream.py --report out.json
    DATABASE_URL=postgres://… python app/scripts/republish_sync_stream.py --execute
    DATABASE_URL=postgres://… python app/scripts/republish_sync_stream.py --execute \
        --entities movie,movie_identifier

Run it inside the application container: building a delta payload needs
`next_app`, which constructs the Flask application and demands the runtime
secrets. Unlike a backfill, emission here *is* the whole job, so the script
refuses rather than degrading when it cannot reach them.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


if __package__ and __package__ != "scripts":
    from ..versioning import backend_version, build_sha
elif __package__ == "scripts":  # pragma: no cover - gunicorn top-level imports
    from versioning import backend_version, build_sha
else:  # pragma: no cover - direct execution from a source checkout
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from versioning import backend_version, build_sha


#: Commit boundary. Only about transaction size -- each emission stands alone.
BATCH_SIZE = 200

#: Entity kinds in emission order. The order is the dependency order described
#: in the module docstring; `--entities` filters this list without reordering it.
ENTITY_ORDER = (
    "location",
    "series",
    "container",
    "movie",
    "movie_identifier",
    "container_membership",
)


def _emitters():
    """The `emit_*` helpers, or a message saying why they are out of reach.

    Returns ``(emitters, error)``. A delta payload is the whole entity and only
    `next_app` can build one, so there is no reduced mode worth offering: a run
    that cannot emit has nothing to do.
    """

    try:
        if __package__ and __package__ != "scripts":
            from ..next_app import (
                emit_container_change,
                emit_container_membership_change,
                emit_location_change,
                emit_movie_change,
                emit_movie_identifiers_change,
                emit_series_change,
            )
        else:
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


def _connect():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is required.", file=sys.stderr)
        raise SystemExit(2)
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:  # pragma: no cover - deployment always ships psycopg
        print("psycopg is required to run the republish.", file=sys.stderr)
        raise SystemExit(2)
    return psycopg.connect(url, autocommit=False, row_factory=dict_row)


def _table_exists(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS name", (f"public.{table_name}",))
        row = cur.fetchone()
    return bool(row and row["name"])


#: How to enumerate the live rows of each kind. `movie_identifier` and
#: `container_membership` are keyed by their owner, because each emission
#: carries that owner's *full* set in one change.
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
        if not _table_exists(conn, table):
            targets[kind] = []
            continue
        with conn.cursor() as cur:
            cur.execute(query)
            targets[kind] = [row["id"] for row in cur.fetchall()]
    return targets


def current_revision(conn) -> int:
    if not _table_exists(conn, "sync_state"):
        return 0
    with conn.cursor() as cur:
        cur.execute("SELECT revision FROM sync_state WHERE id='global'")
        row = cur.fetchone()
    return int(row["revision"]) if row else 0


def republish(conn, targets: dict[str, list], emitters: dict) -> dict:
    """Emit every target, committing per batch. Returns per-kind counts.

    A kind is emitted whole before the next one starts, so the revision ranges
    in the report describe contiguous blocks and the dependency order in the
    docstring holds on the wire.
    """

    emitted: dict[str, int] = {}
    skipped: dict[str, int] = {}
    pending = 0
    for kind in ENTITY_ORDER:
        ids = targets.get(kind) or []
        emitted[kind] = 0
        skipped[kind] = 0
        emit = emitters[kind]
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


def build_report(conn, targets: dict[str, list], *, executed: bool) -> dict:
    return {
        "script": "republish_sync_stream",
        "backend_version": backend_version(),
        "script_commit": build_sha(),
        "executed": executed,
        "revision_before": current_revision(conn),
        "targets": {kind: len(ids) for kind, ids in targets.items()},
        "total_targets": sum(len(ids) for ids in targets.values()),
    }


def parse_entities(raw: str | None) -> list[str]:
    if not raw:
        return list(ENTITY_ORDER)
    chosen = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [kind for kind in chosen if kind not in ENTITY_ORDER]
    if unknown:
        raise SystemExit(
            f"Unknown entity kind(s): {', '.join(unknown)}. "
            f"Choose from: {', '.join(ENTITY_ORDER)}"
        )
    return chosen


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually emit the changes. Without it the run only reports what it would do.",
    )
    parser.add_argument(
        "--entities",
        help=f"Comma-separated subset of: {', '.join(ENTITY_ORDER)}. Defaults to all.",
    )
    parser.add_argument("--report", help="Write the JSON report to this path as well as stdout.")
    args = parser.parse_args(argv)

    entities = parse_entities(args.entities)
    emitters, emitter_error = _emitters()
    if args.execute and emitters is None:
        print(
            "Cannot reach the delta emitters, so there is nothing this run could publish.\n"
            f"Reason: {emitter_error}\n"
            "Run this inside the application container, where the runtime secrets are set.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    conn = _connect()
    try:
        targets = collect_targets(conn, entities)
        report = build_report(conn, targets, executed=bool(args.execute))
        report["emitters_reachable"] = emitters is not None
        if emitters is None:
            report["emitters_error"] = emitter_error
        if args.execute:
            report.update(republish(conn, targets, emitters))
            report["revision_after"] = current_revision(conn)
        else:
            conn.rollback()
    finally:
        conn.close()

    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        Path(args.report).write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
