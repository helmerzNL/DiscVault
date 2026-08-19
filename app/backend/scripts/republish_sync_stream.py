#!/usr/bin/env python3
"""Manual re-publish of the catalog into the sync change log.

The repair itself is automatic: migration 084 enqueues one
`sync.catalog_republish` job and the worker runs it on upgrade, so a person
whose films stopped syncing updates the app and it works. Nothing here needs to
be run for that.

This CLI is the escape hatch beside it, for the cases a one-time migration
cannot cover: a device that strands later, a run that failed and needs
repeating, or a republish of one entity kind while diagnosing. It shares its
implementation with the job -- `next_sync_republish` -- so the manual path and
the automatic one cannot drift apart.

Dry-run is the default and `--execute` the second, deliberate act, because the
republish is not idempotent: every run appends a generation of changes and costs
each connected device a full catalog download. See `next_sync_republish` for why
that is the mechanism rather than a shortcoming, and why the bootstrap is not a
recovery path for a large library.

Usage::

    DATABASE_URL=postgres://… python app/scripts/republish_sync_stream.py
    DATABASE_URL=postgres://… python app/scripts/republish_sync_stream.py --report out.json
    DATABASE_URL=postgres://… python app/scripts/republish_sync_stream.py --execute
    DATABASE_URL=postgres://… python app/scripts/republish_sync_stream.py --execute \
        --entities movie,movie_identifier

Run it inside the application container: building a delta payload needs
`next_app`, which constructs the Flask application and demands the runtime
secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


if __package__ and __package__ != "scripts":
    from ..next_sync_republish import (
        ENTITY_ORDER,
        collect_targets,
        current_revision,
        emitters,
        parse_entities,
        republish,
    )
    from ..versioning import backend_version, build_sha
elif __package__ == "scripts":  # pragma: no cover - gunicorn top-level imports
    from next_sync_republish import (
        ENTITY_ORDER,
        collect_targets,
        current_revision,
        emitters,
        parse_entities,
        republish,
    )
    from versioning import backend_version, build_sha
else:  # pragma: no cover - direct execution from a source checkout
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from next_sync_republish import (
        ENTITY_ORDER,
        collect_targets,
        current_revision,
        emitters,
        parse_entities,
        republish,
    )
    from versioning import backend_version, build_sha


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

    try:
        entities = parse_entities(args.entities)
    except ValueError as exc:
        raise SystemExit(str(exc))

    emitter_map, emitter_error = emitters()
    if args.execute and emitter_map is None:
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
        report["emitters_reachable"] = emitter_map is not None
        if emitter_map is None:
            report["emitters_error"] = emitter_error
        if args.execute:
            report.update(republish(conn, targets, emitter_map))
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
