"""Tests for the catalog republish — the automatic path and the manual one.

The republish exists because a stranded client cannot be repaired through the
protocol: its cursor sits within range, so the server cannot tell it apart from
a device that is up to date. Re-emitting the catalog above every cursor is the
only channel left, and the bootstrap is not a substitute for a large library --
it caps at 5000 and cuts alphabetically (sync-contract §5c).

What matters here is that it happens **without an operator**: migration 084
enqueues the job, the worker runs it on upgrade. So the tests cover the wiring
of that path as much as the logic itself -- a repair nobody triggers is a
runbook, which is the thing it was built to replace.

No live database. What is worth testing without one is what decides *what* gets
written: the selection, the order, the once-only guard, and the refusal to
report success when nothing could be published.
"""

from __future__ import annotations

import io
import re
import unittest
from pathlib import Path
from unittest import mock

from app.backend import next_sync_republish as republish_mod
from app.backend.scripts import republish_sync_stream as cli


REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION = REPO_ROOT / "app" / "backend" / "migrations_next" / "084_sync_catalog_republish_job.sql"
ARTWORK_MIGRATION = (
    REPO_ROOT / "app" / "backend" / "migrations_next" / "085_sync_catalog_republish_artwork.sql"
)
WORKER = REPO_ROOT / "app" / "backend" / "next_worker.py"


class EntitySelectionTests(unittest.TestCase):
    def test_default_is_every_kind_in_dependency_order(self):
        self.assertEqual(republish_mod.parse_entities(None), list(republish_mod.ENTITY_ORDER))
        self.assertEqual(republish_mod.parse_entities(""), list(republish_mod.ENTITY_ORDER))

    def test_subset_is_honoured(self):
        self.assertEqual(
            republish_mod.parse_entities("movie,movie_identifier"),
            ["movie", "movie_identifier"],
        )

    def test_whitespace_and_empty_segments_are_tolerated(self):
        self.assertEqual(
            republish_mod.parse_entities(" movie , , container "), ["movie", "container"]
        )

    def test_unknown_kind_is_refused_rather_than_silently_dropped(self):
        # A typo that quietly republished less than asked would look like a
        # successful run and leave the device still stranded.
        with self.assertRaises(ValueError) as raised:
            republish_mod.parse_entities("movie,fillums")
        self.assertIn("fillums", str(raised.exception))

    def test_referenced_entities_are_published_before_their_referrers(self):
        order = republish_mod.ENTITY_ORDER
        self.assertLess(order.index("container"), order.index("container_membership"))
        self.assertLess(order.index("movie"), order.index("container_membership"))
        self.assertLess(order.index("movie"), order.index("movie_identifier"))
        self.assertLess(order.index("series"), order.index("movie"))

    def test_every_kind_knows_where_its_rows_come_from(self):
        self.assertEqual(set(republish_mod.SOURCE_QUERIES), set(republish_mod.ENTITY_ORDER))

    def test_tombstoned_rows_are_never_republished(self):
        # A tombstone travels as its own `delete` change. Re-emitting a deleted
        # record as an upsert would resurrect it on every client at once.
        for kind in ("series", "container", "movie"):
            _table, query = republish_mod.SOURCE_QUERIES[kind]
            self.assertIn("deleted_at IS NULL", query, kind)


class RepublishOrderTests(unittest.TestCase):
    """The emission walks kinds in order and counts what the helpers refused."""

    def setUp(self):
        self.calls = []

        def emitter(kind, result=1):
            def emit(_conn, entity_id):
                self.calls.append((kind, entity_id))
                return result

            return emit

        self.emitters = {kind: emitter(kind) for kind in republish_mod.ENTITY_ORDER}
        self.conn = mock.Mock()

    def test_emits_every_target_in_kind_order(self):
        targets = {"movie": ["m1", "m2"], "container": ["c1"]}
        result = republish_mod.republish(self.conn, targets, self.emitters)
        self.assertEqual(self.calls, [("container", "c1"), ("movie", "m1"), ("movie", "m2")])
        self.assertEqual(result["emitted"]["movie"], 2)
        self.assertEqual(result["emitted"]["container"], 1)
        self.conn.commit.assert_called()

    def test_a_helper_that_declines_is_counted_not_raised(self):
        # `emit_*` returns 0 when the sync tables are absent or the entity no
        # longer builds. One unpublishable row must not cost the rest of the run.
        self.emitters["movie"] = lambda _conn, _id: 0
        result = republish_mod.republish(self.conn, {"movie": ["m1", "m2"]}, self.emitters)
        self.assertEqual(result["emitted"]["movie"], 0)
        self.assertEqual(result["skipped"]["movie"], 2)

    def test_kinds_absent_from_targets_contribute_nothing(self):
        result = republish_mod.republish(self.conn, {}, self.emitters)
        self.assertEqual(self.calls, [])
        self.assertEqual(sum(result["emitted"].values()), 0)


class RunCatalogRepublishTests(unittest.TestCase):
    """The shared entry point both the job and the CLI go through."""

    def test_refuses_when_the_emitters_are_out_of_reach(self):
        # Reporting a successful repair while publishing nothing would leave the
        # device stranded and the job log claiming otherwise.
        with mock.patch.object(republish_mod, "emitters", return_value=(None, "ImportError: nope")):
            with self.assertRaises(RuntimeError) as raised:
                republish_mod.run_catalog_republish(mock.Mock())
        self.assertIn("nothing this run could", str(raised.exception))

    def test_summarises_what_it_published(self):
        emitters = {kind: (lambda _c, _i: 7) for kind in republish_mod.ENTITY_ORDER}
        with mock.patch.object(republish_mod, "emitters", return_value=(emitters, None)), \
             mock.patch.object(republish_mod, "collect_targets", return_value={"movie": ["a", "b"]}), \
             mock.patch.object(republish_mod, "current_revision", side_effect=[10, 12]):
            summary = republish_mod.run_catalog_republish(mock.Mock())
        self.assertEqual(summary["totalTargets"], 2)
        self.assertEqual(summary["emitted"]["movie"], 2)
        self.assertEqual(summary["revisionBefore"], 10)
        self.assertEqual(summary["revisionAfter"], 12)


class AutomaticPathTests(unittest.TestCase):
    """Migration 084 schedules it; the worker dispatches it. Nobody runs a script."""

    def setUp(self):
        self.migration = MIGRATION.read_text(encoding="utf-8")
        self.worker = WORKER.read_text(encoding="utf-8")

    def test_migration_enqueues_the_job(self):
        self.assertIn("INSERT INTO background_jobs", self.migration)
        self.assertIn("'sync.catalog_republish'", self.migration)
        self.assertIn("'pending'", self.migration)

    def test_migration_job_type_matches_the_worker_constant(self):
        self.assertEqual(republish_mod.SYNC_CATALOG_REPUBLISH_JOB_TYPE, "sync.catalog_republish")
        self.assertIn(f"'{republish_mod.SYNC_CATALOG_REPUBLISH_JOB_TYPE}'", self.migration)

    def test_migration_skips_an_empty_library(self):
        # Nothing to repair, and no client holding a cursor into a history that
        # never existed. Keeps a fresh install's job list clean too.
        self.assertIn("WHERE EXISTS (SELECT 1 FROM movies WHERE deleted_at IS NULL)", self.migration)

    def test_migration_cannot_enqueue_twice(self):
        # An accidental repeat costs every device a full catalog download, so
        # once-per-version from the runner is backed by a guard in the SQL.
        self.assertIn("NOT EXISTS", self.migration)
        self.assertIn("payload->>'migration' = '084'", self.migration)

    def test_worker_dispatches_the_job_type(self):
        self.assertIn("if job_type == SYNC_CATALOG_REPUBLISH_JOB_TYPE:", self.worker)
        self.assertIn("return process_sync_catalog_republish(payload, worker_id)", self.worker)

    def test_worker_imports_it_on_both_import_paths(self):
        # `next_worker` is imported as a package module and as a top-level
        # script; a name added to only one branch fails in exactly one of them.
        self.assertEqual(
            len(re.findall(r"from \.?next_sync_republish import run_catalog_republish", self.worker)),
            2,
        )


class PayloadShapeRepublishTests(unittest.TestCase):
    """A reshape reaches nobody on its own, and two sweeps are not two fixes.

    #690 changed what movies and containers *carry* without changing a row, so
    nothing was marked changed and the delta had nothing to send. That is the
    general shape of a payload-shape fix: it needs the catalog re-sent, and 085
    schedules that the way 084 did.

    The interesting half is the coalescing. Someone who updates once a week runs
    084 and 085 back to back. A republish re-sends every entity *as it is now*,
    built by the code that is running -- so a sweep that has not started yet
    already carries both fixes, and enqueueing a second one buys nothing while
    costing every device another full catalog download.
    """

    def setUp(self):
        self.migration = ARTWORK_MIGRATION.read_text(encoding="utf-8")

    def test_it_enqueues_the_same_job_type_the_worker_handles(self):
        self.assertIn("INSERT INTO background_jobs", self.migration)
        self.assertIn(f"'{republish_mod.SYNC_CATALOG_REPUBLISH_JOB_TYPE}'", self.migration)
        self.assertIn("'pending'", self.migration)

    def test_it_coalesces_with_a_sweep_that_has_not_run_yet(self):
        self.assertIn("status IN ('pending', 'running')", self.migration)

    def test_a_completed_or_failed_sweep_does_not_suppress_it(self):
        # A completed sweep published the *old* shape and cannot carry this one;
        # a failed sweep published nothing. Either must be followed by a fresh
        # one, so neither status may appear in the coalescing guard.
        guard_start = self.migration.index("status IN (")
        guard = self.migration[guard_start : guard_start + 40]
        self.assertNotIn("completed", guard)
        self.assertNotIn("failed", guard)

    def test_it_keeps_its_own_once_ever_guard(self):
        self.assertIn("payload->>'migration' = '085'", self.migration)

    def test_it_skips_an_empty_library(self):
        self.assertIn("WHERE EXISTS (SELECT 1 FROM movies WHERE deleted_at IS NULL)", self.migration)

    def test_it_records_why_it_ran(self):
        # The job list is where an operator finds out a sweep happened and what
        # prompted it; "a republish appeared" with no reason is a mystery.
        self.assertIn("'artwork_resolution_changed'", self.migration)


class ManualCliTests(unittest.TestCase):
    """The escape hatch beside the automatic path."""

    def test_execute_exits_when_the_emitters_are_out_of_reach(self):
        with mock.patch.object(cli, "emitters", return_value=(None, "ImportError: nope")):
            stderr = io.StringIO()
            with mock.patch("sys.stderr", stderr), self.assertRaises(SystemExit) as raised:
                cli.main(["--execute"])
        self.assertEqual(raised.exception.code, 2)
        # The operator needs to know where to run it, not just that it failed.
        self.assertIn("inside the application container", stderr.getvalue())

    def test_unknown_entity_exits_rather_than_running_a_partial_republish(self):
        with self.assertRaises(SystemExit) as raised:
            cli.main(["--entities", "fillums"])
        self.assertIn("fillums", str(raised.exception))

    def test_a_dry_run_still_reports_without_the_emitters(self):
        # Dry-run answers "how much would move", which does not need a payload
        # builder -- and is the one thing an operator can check from anywhere.
        conn = mock.Mock()
        with mock.patch.object(cli, "emitters", return_value=(None, "ImportError: nope")), \
             mock.patch.object(cli, "_connect", return_value=conn), \
             mock.patch.object(cli, "collect_targets", return_value={"movie": ["m1"]}), \
             mock.patch.object(cli, "current_revision", return_value=41):
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                self.assertEqual(cli.main([]), 0)
        printed = stdout.getvalue()
        self.assertIn('"executed": false', printed)
        self.assertIn('"emitters_reachable": false', printed)
        self.assertIn('"total_targets": 1', printed)
        conn.rollback.assert_called_once()

    def test_cli_and_job_share_one_implementation(self):
        # Two copies of the emission order or the tombstone rule would drift the
        # moment one is edited, and only one of the two paths would be tested.
        self.assertIs(cli.republish, republish_mod.republish)
        self.assertIs(cli.collect_targets, republish_mod.collect_targets)
        self.assertIs(cli.parse_entities, republish_mod.parse_entities)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
