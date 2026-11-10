"""The installation count's two "exactly once" claims, against PostgreSQL.

``test_next_installation_count`` replaces the database with a dict, which is
enough to hold the rules and not enough to hold the two statements they rest
on. Both are claims about what a *second* connection sees:

* the attempt is taken with a compare-and-set, so two workers that both read
  the same state cannot both report on the same day;
* the owner's notice is recorded with an insert that does nothing on conflict,
  in the same transaction as the notification, so two workers or a restart
  cannot tell the owner twice -- and a notice that failed to be written leaves
  nothing behind that says it was.

A dict cannot make a second writer wait, cannot roll a transaction back, and
compares JSON as Python objects rather than as ``jsonb``. So these run against
the real thing, and skip without ``DATABASE_URL`` like the other
``*_postgres`` suites.
"""

import logging
import os
import sys
import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ModuleNotFoundError:  # pragma: no cover - depends on environment
    psycopg = None
    dict_row = None
    Jsonb = None

try:
    from app.backend import next_installation_count as count
    from app.backend import next_notifications
    from app.backend.next_ownership import instance_owner_id
except ModuleNotFoundError as exc:  # pragma: no cover - minimal test environments
    if exc.name not in {"flask", "psycopg"}:
        raise
    count = None
    next_notifications = None
    instance_owner_id = None


DATABASE_URL = os.environ.get("DATABASE_URL")
PREFIX = "installation-count-test"
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
ATTEMPT = {"day": "2026-11-09", "count": 1, "lastAttemptAt": NOW.isoformat()}

# A refused report logs on purpose; keep that line out of the test output.
_QUIET = logging.NullHandler()


def setUpModule():
    if count is not None:
        count.logger.addHandler(_QUIET)


def tearDownModule():
    if count is not None:
        count.logger.removeHandler(_QUIET)


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and count is not None,
    "PostgreSQL test database is not configured",
)
class InstallationCountPostgresTests(unittest.TestCase):
    def connect(self):
        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)
        # A claim that waits on another transaction must give up rather than
        # hang the suite if the test itself is wrong.
        conn.execute("SET lock_timeout = '15s'")
        conn.commit()
        return conn

    def setUp(self):
        # The keys are this server's own state. Whatever the database held is
        # put back afterwards, so the suite can run against a database that is
        # in use without rewriting its history.
        with self.connect() as conn:
            self._saved = conn.execute(
                "SELECT key, value, is_secret FROM app_settings WHERE key = ANY(%s)",
                (list(count._STATE_KEYS),),
            ).fetchall()
            conn.execute("DELETE FROM app_settings WHERE key = ANY(%s)", (list(count._STATE_KEYS),))
        self._users = []
        self._started = None
        guard = patch.object(
            next_notifications,
            "_next_app",
            side_effect=AssertionError("the notice must not be pushed to any device"),
        )
        guard.start()
        self.addCleanup(guard.stop)

    def tearDown(self):
        with self.connect() as conn:
            if self._started is not None:
                conn.execute(
                    "DELETE FROM user_notifications WHERE payload->>'kind' = %s AND created_at >= %s",
                    (count.NOTICE_KIND, self._started),
                )
            if self._users:
                conn.execute("DELETE FROM users WHERE id = ANY(%s)", (self._users,))
            conn.execute("DELETE FROM app_settings WHERE key = ANY(%s)", (list(count._STATE_KEYS),))
            for row in self._saved:
                conn.execute(
                    "INSERT INTO app_settings (key, value, is_secret) VALUES (%s, %s, %s)",
                    (row["key"], Jsonb(row["value"]), row["is_secret"]),
                )

    # -- helpers ---------------------------------------------------------------

    def _user(self, conn, role_key):
        user_id = uuid4()
        conn.execute(
            "INSERT INTO users (id, username, display_name, status) VALUES (%s, %s, %s, 'active')",
            (user_id, f"{PREFIX}-{role_key}-{user_id.hex[:8]}", f"{PREFIX} {role_key}"),
        )
        conn.execute(
            "INSERT INTO user_roles (user_id, role_id) SELECT %s, id FROM roles WHERE key = %s",
            (user_id, role_key),
        )
        self._users.append(user_id)
        return user_id

    def _people(self):
        """An owner to tell, and two people who must not be told."""
        with self.connect() as conn:
            self._started = conn.execute("SELECT now() AS now").fetchone()["now"]
            if instance_owner_id(conn) is None:
                self._user(conn, "owner")
            admin_id = self._user(conn, "admin")
            viewer_id = self._user(conn, "viewer")
            owner_id = instance_owner_id(conn)
        return owner_id, admin_id, viewer_id

    def _notices(self):
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT user_id, title, body, payload, read_at
                FROM user_notifications
                WHERE payload->>'kind' = %s AND created_at >= %s
                """,
                (count.NOTICE_KIND, self._started),
            ).fetchall()

    def _setting(self, key):
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = %s", (key,)).fetchone()
        return row["value"] if row else None

    def _in_thread(self, work):
        """Run ``work`` on another thread and hand back how it ended."""
        result = {}

        def body():
            try:
                result["value"] = work()
            except Exception as exc:  # noqa: BLE001 - reported by the caller
                result["error"] = exc

        thread = threading.Thread(target=body, daemon=True)
        thread.start()
        return thread, result

    # -- the attempt -----------------------------------------------------------

    def test_the_first_attempt_can_be_claimed_once(self):
        with self.connect() as conn:
            self.assertTrue(count._claim_attempt(conn, {}, ATTEMPT))
        with self.connect() as conn:
            self.assertFalse(count._claim_attempt(conn, {}, {**ATTEMPT, "count": 9}))
        self.assertEqual(self._setting(count.ATTEMPT_KEY), ATTEMPT)

    def test_a_second_worker_waits_for_the_first_and_then_loses(self):
        """Neither has committed when the second asks. It must not be told
        "yours" while the first can still commit."""
        first = self.connect()
        second = self.connect()
        try:
            self.assertTrue(count._claim_attempt(first, {}, ATTEMPT))
            thread, result = self._in_thread(lambda: count._claim_attempt(second, {}, ATTEMPT))
            thread.join(0.5)
            self.assertTrue(thread.is_alive(), "the second claim answered before the first committed")
            first.commit()
            thread.join(20)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result, {"value": False})
        finally:
            first.close()
            second.close()

    def test_the_second_worker_wins_when_the_first_gives_up(self):
        first = self.connect()
        second = self.connect()
        try:
            self.assertTrue(count._claim_attempt(first, {}, ATTEMPT))
            thread, result = self._in_thread(lambda: count._claim_attempt(second, {}, ATTEMPT))
            thread.join(0.5)
            first.rollback()
            thread.join(20)
            self.assertEqual(result, {"value": True})
            second.commit()
        finally:
            first.close()
            second.close()
        self.assertEqual(self._setting(count.ATTEMPT_KEY), ATTEMPT)

    def test_a_stale_reader_loses_the_compare_and_set(self):
        yesterday = {"day": "2026-11-08", "count": 3, "lastAttemptAt": "2026-11-08T09:00:00+00:00"}
        with self.connect() as conn:
            self.assertTrue(count._claim_attempt(conn, {}, yesterday))
        with self.connect() as conn:
            read_by_both = count._read_state(conn)
        self.assertEqual(read_by_both[count.ATTEMPT_KEY], yesterday)

        with self.connect() as conn:
            self.assertTrue(count._claim_attempt(conn, read_by_both, ATTEMPT))
        with self.connect() as conn:
            self.assertFalse(count._claim_attempt(conn, read_by_both, {**ATTEMPT, "count": 9}))
        self.assertEqual(self._setting(count.ATTEMPT_KEY), ATTEMPT)

    def test_a_stale_reader_loses_even_while_the_winner_is_uncommitted(self):
        yesterday = {"day": "2026-11-08", "count": 3, "lastAttemptAt": "2026-11-08T09:00:00+00:00"}
        with self.connect() as conn:
            count._claim_attempt(conn, {}, yesterday)
        stored = {count.ATTEMPT_KEY: yesterday}
        first = self.connect()
        second = self.connect()
        try:
            self.assertTrue(count._claim_attempt(first, stored, ATTEMPT))
            thread, result = self._in_thread(lambda: count._claim_attempt(second, stored, ATTEMPT))
            thread.join(0.5)
            self.assertTrue(thread.is_alive(), "the second claim answered before the first committed")
            first.commit()
            thread.join(20)
            self.assertEqual(result, {"value": False})
        finally:
            first.close()
            second.close()

    def test_the_comparison_is_on_the_value_not_on_its_spelling(self):
        """``jsonb`` equality ignores key order and spacing, so a row another
        writer serialised differently is still recognised as the one read."""
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO app_settings (key, value, is_secret) VALUES (%s, %s::jsonb, false)",
                (
                    count.ATTEMPT_KEY,
                    '{ "lastAttemptAt" : "2026-11-08T09:00:00+00:00", "count": 3,   "day": "2026-11-08" }',
                ),
            )
        with self.connect() as conn:
            stored = count._read_state(conn)
            self.assertTrue(count._claim_attempt(conn, stored, ATTEMPT))

    def test_a_malformed_row_can_still_be_replaced(self):
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO app_settings (key, value, is_secret) VALUES (%s, 'null'::jsonb, false)",
                (count.ATTEMPT_KEY,),
            )
        with self.connect() as conn:
            stored = count._read_state(conn)
            self.assertIn(count.ATTEMPT_KEY, stored)
            self.assertTrue(count._claim_attempt(conn, stored, ATTEMPT))
        self.assertEqual(self._setting(count.ATTEMPT_KEY), ATTEMPT)

    # -- the whole check -------------------------------------------------------

    def test_a_check_reports_once_and_remembers_the_day(self):
        self._people()
        sent = []

        def post(url, body):
            sent.append((url, body))
            return 204, None

        def check(now):
            return count.report_if_due(self.connect, now=now, environ={}, version="26.11.0", post=post)

        self.assertEqual(check(NOW), "accepted")
        self.assertEqual(check(NOW.replace(hour=20)), "already_reported")
        self.assertEqual(len(sent), 1)
        self.assertEqual(
            sent[0],
            (
                "https://api.discvault.eu/v2/presence",
                b'{"protocol":"presence-1","platform":"server","version":"26.11.0","sinceDays":null}',
            ),
        )
        self.assertEqual(self._setting(count.LAST_ACCEPTED_ON_KEY), "2026-11-09")
        self.assertEqual(len(self._notices()), 1)

    def test_a_refused_report_leaves_the_last_accepted_date_alone(self):
        self._people()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO app_settings (key, value, is_secret) VALUES (%s, %s, false)",
                (count.LAST_ACCEPTED_ON_KEY, Jsonb("2026-11-06")),
            )
        outcome = count.report_if_due(
            self.connect, now=NOW, environ={}, version="26.11.0", post=lambda url, body: (429, "7200")
        )
        self.assertEqual(outcome, "failed")
        self.assertEqual(self._setting(count.LAST_ACCEPTED_ON_KEY), "2026-11-06")
        self.assertEqual(
            self._setting(count.ATTEMPT_KEY),
            {**ATTEMPT, "retryNotBefore": NOW.replace(hour=14).isoformat()},
        )

    # -- the owner's notice ----------------------------------------------------

    def test_the_owner_is_told_once_and_nobody_else_is(self):
        owner_id, admin_id, viewer_id = self._people()
        with self.connect() as conn:
            self.assertTrue(count.ensure_owner_notice(conn, NOW))
        with self.connect() as conn:
            self.assertTrue(count.ensure_owner_notice(conn, NOW))

        notices = self._notices()
        self.assertEqual([row["user_id"] for row in notices], [owner_id])
        self.assertNotIn(admin_id, [row["user_id"] for row in notices])
        self.assertNotIn(viewer_id, [row["user_id"] for row in notices])
        notice = notices[0]
        self.assertEqual(notice["title"], count.NOTICE_TITLE)
        self.assertEqual(notice["body"], count.NOTICE_BODY)
        self.assertEqual(notice["payload"]["kind"], count.NOTICE_KIND)
        self.assertEqual(notice["payload"]["url"], "/profile")
        self.assertIsNone(notice["read_at"])
        self.assertEqual(self._setting(count.NOTICE_CREATED_AT_KEY), NOW.isoformat())

    def test_a_notice_that_was_rolled_back_was_never_recorded(self):
        self._people()
        conn = self.connect()
        try:
            self.assertTrue(count.ensure_owner_notice(conn, NOW))
            conn.rollback()
        finally:
            conn.close()
        self.assertEqual(self._notices(), [])
        self.assertIsNone(self._setting(count.NOTICE_CREATED_AT_KEY))

        with self.connect() as conn:
            self.assertTrue(count.ensure_owner_notice(conn, NOW))
        self.assertEqual(len(self._notices()), 1)

    def test_two_workers_starting_together_tell_the_owner_once(self):
        self._people()
        first = self.connect()
        second = self.connect()
        try:
            self.assertTrue(count.ensure_owner_notice(first, NOW))

            def second_worker():
                told = count.ensure_owner_notice(second, NOW)
                second.commit()
                return told

            thread, result = self._in_thread(second_worker)
            thread.join(0.5)
            self.assertTrue(thread.is_alive(), "the second worker did not wait for the first")
            first.commit()
            thread.join(20)
            self.assertEqual(result, {"value": True})
        finally:
            first.close()
            second.close()
        self.assertEqual(len(self._notices()), 1)


if __name__ == "__main__":
    unittest.main()
