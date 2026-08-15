"""Tests for the sync-republish CLI (no live DB).

The script exists because a stranded client cannot be repaired through the
protocol: its cursor sits within range, so the server cannot tell it apart from
a device that is up to date. Re-emitting the catalog above every cursor is the
only channel left, and the bootstrap is not a substitute for a large library --
it caps at 5000 and cuts alphabetically (sync-contract §5c).

What is worth testing without a database is the part that decides *what* gets
written: the selection, the order, and the refusal to pretend a run succeeded
when it could not emit anything.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from unittest import mock

from app.backend.scripts import republish_sync_stream as republish


class EntitySelectionTests(unittest.TestCase):
    def test_default_is_every_kind_in_dependency_order(self):
        self.assertEqual(republish.parse_entities(None), list(republish.ENTITY_ORDER))
        self.assertEqual(republish.parse_entities(""), list(republish.ENTITY_ORDER))

    def test_subset_is_honoured(self):
        self.assertEqual(
            republish.parse_entities("movie,movie_identifier"),
            ["movie", "movie_identifier"],
        )

    def test_whitespace_and_empty_segments_are_tolerated(self):
        self.assertEqual(republish.parse_entities(" movie , , container "), ["movie", "container"])

    def test_unknown_kind_is_refused_rather_than_silently_dropped(self):
        # A typo that quietly republished less than asked would look like a
        # successful run and leave the device still stranded.
        with self.assertRaises(SystemExit) as raised:
            republish.parse_entities("movie,fillums")
        self.assertIn("fillums", str(raised.exception))

    def test_referenced_entities_are_published_before_their_referrers(self):
        order = republish.ENTITY_ORDER
        self.assertLess(order.index("container"), order.index("container_membership"))
        self.assertLess(order.index("movie"), order.index("container_membership"))
        self.assertLess(order.index("movie"), order.index("movie_identifier"))
        self.assertLess(order.index("series"), order.index("movie"))

    def test_every_kind_knows_where_its_rows_come_from(self):
        self.assertEqual(set(republish.SOURCE_QUERIES), set(republish.ENTITY_ORDER))

    def test_tombstoned_rows_are_never_republished(self):
        # A tombstone travels as its own `delete` change. Re-emitting a deleted
        # record as an upsert would resurrect it on every client at once.
        for kind in ("series", "container", "movie"):
            _table, query = republish.SOURCE_QUERIES[kind]
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

        self.emitters = {kind: emitter(kind) for kind in republish.ENTITY_ORDER}
        self.conn = mock.Mock()

    def test_emits_every_target_in_kind_order(self):
        targets = {"movie": ["m1", "m2"], "container": ["c1"]}
        result = republish.republish(self.conn, targets, self.emitters)
        self.assertEqual(
            self.calls, [("container", "c1"), ("movie", "m1"), ("movie", "m2")]
        )
        self.assertEqual(result["emitted"]["movie"], 2)
        self.assertEqual(result["emitted"]["container"], 1)
        self.conn.commit.assert_called()

    def test_a_helper_that_declines_is_counted_not_raised(self):
        # `emit_*` returns 0 when the sync tables are absent or the entity no
        # longer builds. One unpublishable row must not cost the rest of the run.
        self.emitters["movie"] = lambda _conn, _id: 0
        result = republish.republish(self.conn, {"movie": ["m1", "m2"]}, self.emitters)
        self.assertEqual(result["emitted"]["movie"], 0)
        self.assertEqual(result["skipped"]["movie"], 2)

    def test_kinds_absent_from_targets_contribute_nothing(self):
        result = republish.republish(self.conn, {}, self.emitters)
        self.assertEqual(self.calls, [])
        self.assertEqual(sum(result["emitted"].values()), 0)


class ExecuteRequiresEmittersTests(unittest.TestCase):
    """`--execute` without the emitters is a failed run, not a quiet one."""

    def test_execute_exits_when_the_emitters_are_out_of_reach(self):
        with mock.patch.object(republish, "_emitters", return_value=(None, "ImportError: nope")):
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                republish.main(["--execute"])
        self.assertEqual(raised.exception.code, 2)
        # The operator needs to know where to run it, not just that it failed.
        self.assertIn("inside the application container", stderr.getvalue())

    def test_a_dry_run_still_reports_without_the_emitters(self):
        # Dry-run answers "how much would move", which does not need a payload
        # builder -- and is the one thing an operator can check from anywhere.
        conn = mock.Mock()
        with mock.patch.object(republish, "_emitters", return_value=(None, "ImportError: nope")), \
             mock.patch.object(republish, "_connect", return_value=conn), \
             mock.patch.object(republish, "collect_targets", return_value={"movie": ["m1"]}), \
             mock.patch.object(republish, "current_revision", return_value=41):
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                self.assertEqual(republish.main([]), 0)
        printed = stdout.getvalue()
        self.assertIn('"executed": false', printed)
        self.assertIn('"emitters_reachable": false', printed)
        self.assertIn('"total_targets": 1', printed)
        conn.rollback.assert_called_once()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
