#!/usr/bin/env python3
"""One-time convergence of releases that had discs before the union rule.

Since 26.8.76 a release with discs no longer authors its technical description:
the release-level row is derived as the union of its discs on every save, and
a release-level value no disc holds is pushed onto disc 1 first so the
derivation cannot delete a fact nobody retracted.

That runs **on save**. A release broken down into discs before the rule existed
keeps its old release-level row until somebody edits it, so a library holds two
generations of row: derived, and historical. This script converges the second
kind.

Why it is a script and not a migration
--------------------------------------

A migration runs unattended on every deployment, and this rewrites the technical
description of potentially every release in a library — the row that feeds the
collection filters, the sync payload to the mobile clients, and the correction
proposals to MovieVault. It is the first true backfill in the disc family, and
it earns a report somebody reads before anything moves.

Three rules it is built around
------------------------------

* **Dry-run first.** The default mode performs no mutations and prints a JSON
  report of exactly which releases would change and how. ``--execute`` is the
  second, deliberate act.
* **It converges through the live derivation, never a copy of it.** The whole
  risk here is losing a fact, and
  `next_technical_specs.derive_release_technical_from_discs` is the function
  whose push-down prevents that — the same one the save path calls, and the one
  with the tests. A second implementation would be a second chance to get it
  wrong, on the one path where nobody is looking at the result. That shared
  reach is why the derivation and its writer live outside `next_app`: importing
  that module builds the Flask application and demands the runtime secrets,
  which a sweep pointed at a copied database has no business needing.
* **Nothing may be lost, and the script checks rather than trusting.** Every
  entry the release row held before must still be present after. A release that
  fails that check is rolled back on its own and reported; the run continues.
  The property is already tested, so a failure here means something the tests do
  not model, and the honest response is to leave that release alone and say so.

Because the derivation is a fixed point, a release that is already converged is
left untouched and the whole script is safe to re-run.

Usage::

    DATABASE_URL=postgres://… python app/scripts/backfill_disc_union.py
    DATABASE_URL=postgres://… python app/scripts/backfill_disc_union.py --report out.json
    DATABASE_URL=postgres://… python app/scripts/backfill_disc_union.py --execute
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse


if __package__ and __package__ != "scripts":
    from ..next_discs import UNION_LIST_COLUMNS, union_release_technical
    from ..next_technical_specs import derive_release_technical_from_discs
    from ..versioning import backend_version, build_sha
elif __package__ == "scripts":  # pragma: no cover - gunicorn top-level imports
    from next_discs import UNION_LIST_COLUMNS, union_release_technical
    from next_technical_specs import derive_release_technical_from_discs
    from versioning import backend_version, build_sha
else:  # pragma: no cover - exercised by the published-image CLI path
    backend_dir = Path(__file__).resolve().parents[1]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from next_discs import UNION_LIST_COLUMNS, union_release_technical
    from next_technical_specs import derive_release_technical_from_discs
    from versioning import backend_version, build_sha


#: Committed every this many converged releases. Small enough that an
#: interrupted run leaves a mostly-finished library rather than nothing, which
#: is safe precisely because the derivation is idempotent -- re-running picks up
#: where it stopped without redoing what landed.
BATCH_SIZE = 200

#: The scalar the union also derives, beside the list columns.
_RESOLUTION = "video_resolution"


def _movie_change_emitter():
    """`emit_movie_change`, when this process can reach it.

    A converged release should reach connected clients as a delta rather than
    waiting for their next full bootstrap, and the delta payload is the whole
    movie entity -- which only `next_app` can build, and importing `next_app`
    constructs the Flask application and demands the runtime secrets.

    So the emission is *optional* rather than required, and the difference is
    reported instead of hidden. Run inside the container and the deltas are
    published; run against a copied database with no secrets and the sweep
    still converges the rows, with the report saying plainly that clients will
    pick the change up on their next bootstrap. A backfill that refused to run
    without an unrelated secret would push operators toward inventing one,
    which is worse than the gap it protects.
    """
    try:  # pragma: no cover - the test environment always has the secrets
        if __package__ and __package__ != "scripts":
            from ..next_app import emit_movie_change
        else:
            from next_app import emit_movie_change
        return emit_movie_change
    except Exception:  # pragma: no cover - missing secrets, or no Flask
        return None


def _connect():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is required.", file=sys.stderr)
        raise SystemExit(2)
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:  # pragma: no cover - deployment always ships psycopg
        print("psycopg is required to run the backfill.", file=sys.stderr)
        raise SystemExit(2)
    return psycopg.connect(url, autocommit=False, row_factory=dict_row)


def _target_database_name():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return None
    parsed = urlparse(url)
    return (parsed.path or "").lstrip("/") or None


def _report_metadata():
    return {
        "script_commit": build_sha(),
        "target_database": _target_database_name(),
        "backend_version": backend_version(),
    }


def _release_row(cur, movie_id) -> dict:
    cur.execute(
        f"SELECT {', '.join(UNION_LIST_COLUMNS)}, {_RESOLUTION} "
        "FROM movie_technical_specs WHERE movie_id=%s",
        (movie_id,),
    )
    return dict(cur.fetchone() or {})


def _discs(cur, movie_id) -> list[dict]:
    cur.execute(
        "SELECT " + ", ".join(UNION_LIST_COLUMNS.values()) + f", {_RESOLUTION} "
        "FROM movie_discs WHERE movie_id=%s ORDER BY sort_order, created_at",
        (movie_id,),
    )
    return [dict(row) for row in cur.fetchall()]


def _entries(value) -> list:
    return value if isinstance(value, list) else []


def plan_release(stored: dict, discs: list[dict]) -> dict | None:
    """What converging this release would change, or ``None`` for nothing.

    Reported per column as three numbers a reader can act on: how many entries
    the release row holds now, how many it would hold, and how many of the
    current ones no disc has and would therefore be pushed onto disc 1. That
    last number is the interesting one -- it is the count of facts that only
    survive *because* of the push-down.
    """
    derived = union_release_technical(discs)
    if not derived:
        return None

    columns: dict[str, dict] = {}
    for release_column, disc_column in UNION_LIST_COLUMNS.items():
        held = _entries(stored.get(release_column))
        on_discs = _entries(derived.get(release_column))
        pushed = [item for item in held if item not in on_discs]
        after = on_discs + [item for item in pushed if item not in on_discs]
        # `pushed` on its own is enough to report the column. A release whose
        # only leftover is a value no disc has ends up with an unchanged release
        # row -- the union puts it straight back -- while disc 1 gains an
        # authored value it did not have. Reporting only the release row would
        # hide a write from the report a reader decides on.
        if list(held) == list(after) and not pushed:
            continue
        columns[release_column] = {
            "before": len(held),
            "after": len(after),
            "pushed_to_disc_one": len(pushed),
        }

    resolution_before = (stored.get(_RESOLUTION) or "") or None
    resolution_after = derived.get(_RESOLUTION, resolution_before) or None
    if resolution_before != resolution_after:
        columns[_RESOLUTION] = {"before": resolution_before, "after": resolution_after}

    if not columns:
        return None
    return {"columns": columns}


def _kept_everything(before: dict, after: dict) -> list[str]:
    """Columns that lost an entry. Empty is the only acceptable answer."""
    lost = []
    for column in UNION_LIST_COLUMNS:
        held = _entries(before.get(column))
        now = _entries(after.get(column))
        if any(item not in now for item in held):
            lost.append(column)
    # The scalar is not a list, and the derivation may legitimately raise it
    # (1080p -> 2160p when a UHD disc is in the box). Blanking it is the loss.
    if before.get(_RESOLUTION) and not after.get(_RESOLUTION):
        lost.append(_RESOLUTION)
    return lost


def build_report(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT m.id, m.public_id, m.title
            FROM movies m
            JOIN movie_discs d ON d.movie_id = m.id
            WHERE m.deleted_at IS NULL
            ORDER BY m.id
            """
        )
        candidates = [dict(row) for row in cur.fetchall()]

        pending = []
        for movie in candidates:
            plan = plan_release(
                _release_row(cur, movie["id"]), _discs(cur, movie["id"])
            )
            if plan is None:
                continue
            pending.append(
                {
                    "movie_id": str(movie["id"]),
                    "public_id": movie["public_id"],
                    "title": movie["title"],
                    **plan,
                }
            )

    return {
        "metadata": _report_metadata(),
        "releases_with_discs": len(candidates),
        "already_converged": len(candidates) - len(pending),
        "would_change": len(pending),
        "facts_pushed_to_disc_one": sum(
            entry["pushed_to_disc_one"]
            for item in pending
            for entry in item["columns"].values()
            if isinstance(entry, dict) and "pushed_to_disc_one" in entry
        ),
        "releases": pending,
    }


def execute_backfill(conn, report: dict) -> dict:
    """Converge every release the report names, one savepoint at a time.

    Per release rather than per batch, so a single refusal costs that release
    and nothing else. The batch boundary is only about commit size.
    """
    converged = 0
    published = 0
    refused = []
    pending_in_batch = 0
    emit = _movie_change_emitter()

    for item in report["releases"]:
        movie_id = item["movie_id"]
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT release_union")
            try:
                before = _release_row(cur, movie_id)
                derive_release_technical_from_discs(cur, movie_id)
                lost = _kept_everything(before, _release_row(cur, movie_id))
                if lost:
                    cur.execute("ROLLBACK TO SAVEPOINT release_union")
                    refused.append({"movie_id": movie_id, "lost_columns": lost})
                    continue
                cur.execute("RELEASE SAVEPOINT release_union")
            except Exception as error:  # pragma: no cover - defensive
                cur.execute("ROLLBACK TO SAVEPOINT release_union")
                refused.append({"movie_id": movie_id, "error": str(error)})
                continue
        # Outside the savepoint: a client holding the pre-union row would
        # otherwise keep showing it until its next full bootstrap. It cannot
        # push the old row back -- every save re-derives, so a stale push is
        # absorbed as a leftover onto disc 1 rather than un-converging the
        # release -- but a delta is still what makes the change visible.
        if emit is not None:
            emit(conn, movie_id)
            published += 1
        converged += 1
        pending_in_batch += 1
        if pending_in_batch >= BATCH_SIZE:
            conn.commit()
            pending_in_batch = 0

    conn.commit()
    return {"converged": converged, "published": published, "refused": refused}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply the convergence. WITHOUT this flag the script only reports (dry-run).",
    )
    parser.add_argument("--report", help="Write the JSON report to this file as well as stdout.")
    args = parser.parse_args(argv)

    conn = _connect()
    try:
        report = build_report(conn)
        text = json.dumps(report, indent=2, default=str)
        print(text)
        if args.report:
            with open(args.report, "w", encoding="utf-8") as fh:
                fh.write(text)

        if not args.execute:
            print(
                f"\n[dry-run] {report['releases_with_discs']} release(s) with discs, "
                f"{report['already_converged']} already converged, "
                f"{report['would_change']} would change, "
                f"{report['facts_pushed_to_disc_one']} release-level fact(s) would be "
                "pushed onto disc 1 rather than lost. "
                "Re-run with --execute after the report is approved.",
                file=sys.stderr,
            )
            return 0

        outcome = execute_backfill(conn, report)
        print(
            f"\n[executed] converged {outcome['converged']} release(s); "
            f"{len(outcome['refused'])} refused.",
            file=sys.stderr,
        )
        if outcome["converged"] and not outcome["published"]:
            print(
                "[note] no sync deltas were published -- this process could not "
                "load the application (usually: no runtime secrets outside the "
                "container). The rows are converged; connected clients will see "
                "them on their next full bootstrap rather than in a delta.",
                file=sys.stderr,
            )
        if outcome["refused"]:
            # Loud, and a non-zero exit: a refusal means the push-down did not
            # hold on that release, which is a fact about the data nobody has
            # modelled yet rather than a transient failure.
            print(json.dumps({"refused": outcome["refused"]}, indent=2), file=sys.stderr)
            return 1
        return 0
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
