"""A cursor that cannot belong to this stream must replay, not skip.

`GET /api/next/sync/delta` pages a client through `sync_changes` with
`WHERE revision > since`. Both halves of that comparison are bare integers and
nothing said which counter they came from, so a database that was rebuilt or
reset -- `sync_state.revision` back to 0, `sync_changes` empty -- looked exactly
like a database where nothing had happened.

The observed failure (iOS log, 2026-08-15): a clean database, films added in the
PWA, and an iOS app still holding a cursor minted against the *previous*
database. Its first pull asked for `revision > <old, large>`, matched nothing,
and the old response answered `nextSince: currentRevision` -- moving the cursor
onto the tip of a history it had never been sent. The films added in the PWA sat
at revisions below that tip and `>` only looks forward, so they were unreachable
from that device for good. The film added *on* iOS afterwards did appear in the
PWA, because a push does not consult the cursor at all -- which is why the sync
read as healthy from both ends.

Two rules close it, and they are independent:

1. **An empty page never moves the cursor.** `nextSince` is the revision of the
   last change actually handed over, or the cursor unchanged. It is never
   `currentRevision`, because that is a claim about changes the response did not
   contain.
2. **An impossible cursor replays from the start.** `since > currentRevision`
   cannot be reached by syncing -- the counter only climbs -- so it means the
   counter was rewound underneath the client. `streamId` says the same thing
   with certainty when the client sends one.

Rule 2 matters for the builds already installed: it needs nothing from the
client, so an app that never heard of `streamId` still recovers on its next
pull.
"""

import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_database import discover_migrations

try:
    from app.backend import next_app
except ModuleNotFoundError as exc:  # pragma: no cover - minimal environments
    if exc.name not in {"flask", "psycopg", "cbor2", "argon2", "jwt", "segno", "PIL"}:
        raise
    next_app = None


class SyncStreamIdentityMigrationTests(unittest.TestCase):
    """The stream identity has to exist before anything can compare against it."""

    def setUp(self):
        self.migrations = {m.version: m for m in discover_migrations()}

    def test_migration_is_present(self):
        self.assertIn("083", self.migrations)
        self.assertEqual(self.migrations["083"].name, "sync_stream_identity")

    def test_both_streams_get_an_identity(self):
        sql = self.migrations["083"].sql
        self.assertIn("ALTER TABLE sync_state", sql)
        self.assertIn("ALTER TABLE user_sync_state", sql)
        # Idempotent and defaulted: the column has to arrive on a live database
        # without a backfill step and without a window where it is null.
        self.assertEqual(sql.count("ADD COLUMN IF NOT EXISTS stream_id uuid"), 2)
        self.assertEqual(sql.count("NOT NULL DEFAULT gen_random_uuid()"), 2)


@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class ResolveSyncCursorTests(unittest.TestCase):
    """Where a pull starts, and when that start is a reset."""

    def resolve(self, since, revision, stream_id="stream-a", client_stream_id=None):
        return next_app.resolve_sync_cursor(
            since=since,
            revision=revision,
            stream_id=stream_id,
            client_stream_id=client_stream_id,
        )

    def test_ordinary_cursor_is_used_as_given(self):
        self.assertEqual(self.resolve(12, 40), (12, False))

    def test_cursor_at_the_tip_is_not_a_reset(self):
        # Caught up is the common case and must stay cheap: no replay.
        self.assertEqual(self.resolve(40, 40), (40, False))

    def test_cursor_above_the_counter_replays_from_zero(self):
        # The reported failure: a cursor from the previous database against a
        # counter that restarted. Nothing about it is reachable by syncing.
        self.assertEqual(self.resolve(4711, 14), (0, True))

    def test_matching_stream_id_is_not_a_reset(self):
        self.assertEqual(self.resolve(12, 40, client_stream_id="stream-a"), (12, False))

    def test_different_stream_id_replays_even_when_the_cursor_looks_sane(self):
        # A rebuilt database whose counter has already climbed past the client's
        # cursor passes the range check, so the range check alone is not enough.
        self.assertEqual(self.resolve(12, 40, client_stream_id="stream-b"), (0, True))

    def test_client_stream_id_is_ignored_before_the_migration_lands(self):
        # `stream_id` is None on a database that has not run migration 083. A
        # client that already sends one must not be reset on every pull for it.
        self.assertEqual(
            self.resolve(12, 40, stream_id=None, client_stream_id="stream-b"),
            (12, False),
        )


@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class DeltaCursorAdvanceTests(unittest.TestCase):
    """`nextSince` may only cover changes the response actually carried.

    Read off the source of both delta handlers rather than a live database: the
    rule is a property of the expression, and the regression it guards against
    was one literal (`revision if not changes else next_since`).
    """

    def setUp(self):
        source_path = os.path.join(repo_root, "app", "backend", "next_app.py")
        with open(source_path, encoding="utf-8") as handle:
            self.source = handle.read()

    def test_no_handler_advances_the_cursor_to_the_current_revision(self):
        self.assertNotIn('"nextSince": revision', self.source)
        self.assertNotIn('"nextSince": revision if not changes else next_since', self.source)

    def test_empty_page_keeps_the_resolved_cursor(self):
        self.assertIn('next_since = int(changes[-1]["revision"]) if changes else since', self.source)

    def test_both_delta_handlers_resolve_the_cursor_before_querying(self):
        self.assertEqual(self.source.count("resolve_sync_cursor("), 3)  # 1 definition + 2 callers

    def test_delta_reports_the_stream_and_whether_it_reset(self):
        self.assertIn('"streamId": stream_id', self.source)
        self.assertIn('"reset": reset', self.source)


@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class TestDatabaseResetMintsANewStreamTests(unittest.TestCase):
    """Rewinding the counter without renaming the stream is the trap itself."""

    def setUp(self):
        source_path = os.path.join(repo_root, "app", "backend", "next_app.py")
        with open(source_path, encoding="utf-8") as handle:
            self.source = handle.read()

    def test_reset_regenerates_the_stream_id(self):
        reset_start = self.source.index("def reset_next_test_database(")
        reset_body = self.source[reset_start : reset_start + 6000]
        self.assertIn("SET stream_id = gen_random_uuid()", reset_body)

    def test_reset_checks_for_the_column_outside_the_update(self):
        # PostgreSQL resolves column names at parse time, so guarding the UPDATE
        # with its own EXISTS clause would still fail on a pre-083 database.
        reset_start = self.source.index("def reset_next_test_database(")
        reset_body = self.source[reset_start : reset_start + 6000]
        update_at = reset_body.index("SET stream_id = gen_random_uuid()")
        lookup_at = reset_body.index("AND column_name='stream_id'")
        self.assertLess(lookup_at, update_at)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
