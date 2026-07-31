import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_database


class _OperationalError(Exception):
    """Stand-in for psycopg.OperationalError so the suite runs without psycopg."""


_FAKE_PSYCOPG = SimpleNamespace(OperationalError=_OperationalError)


class _Clock:
    """Monotonic clock that only advances when the code under test sleeps."""

    def __init__(self):
        self.now = 0.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class WaitForDatabaseTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(next_database, "_load_psycopg", return_value=_FAKE_PSYCOPG)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.clock = _Clock()
        self.logged = []

    def _wait(self, connect_fn, **kwargs):
        return next_database.wait_for_database(
            connect_fn=connect_fn,
            sleep=self.clock.sleep,
            monotonic=self.clock.monotonic,
            log=self.logged.append,
            **kwargs,
        )

    def test_returns_the_connection_without_sleeping_when_postgres_is_up(self):
        sentinel = object()

        conn = self._wait(lambda: sentinel, timeout=30)

        self.assertIs(conn, sentinel)
        self.assertEqual(self.clock.slept, [])
        self.assertEqual(self.logged, [])

    def test_retries_while_postgres_refuses_connections(self):
        sentinel = object()
        attempts = []

        def connect_fn():
            attempts.append(1)
            if len(attempts) < 4:
                raise _OperationalError("the database system is starting up")
            return sentinel

        conn = self._wait(connect_fn, timeout=30)

        self.assertIs(conn, sentinel)
        self.assertEqual(len(attempts), 4)
        self.assertEqual(self.clock.slept, [0.5, 1.0, 2.0])
        # Only the first failure is announced, so a slow start is not a log flood.
        self.assertEqual(len(self.logged), 1)
        self.assertIn("the database system is starting up", self.logged[0])

    def test_backoff_is_capped(self):
        def connect_fn():
            raise _OperationalError("connection refused")

        with self.assertRaises(next_database.DatabaseUnavailableError):
            self._wait(connect_fn, timeout=60)

        self.assertTrue(self.clock.slept)
        self.assertLessEqual(max(self.clock.slept), next_database.DB_WAIT_MAX_DELAY)

    def test_gives_up_after_the_timeout_and_names_the_last_error(self):
        def connect_fn():
            raise _OperationalError("password authentication failed")

        with self.assertRaises(next_database.DatabaseUnavailableError) as caught:
            self._wait(connect_fn, timeout=10)

        message = str(caught.exception)
        self.assertIn("password authentication failed", message)
        self.assertIn("10s", message)
        # The timeout is a real ceiling, not a floor overshot by the last sleep.
        self.assertLessEqual(self.clock.now, 10)

    def test_a_timeout_failure_is_still_a_migration_error(self):
        # main() catches MigrationError; DatabaseUnavailableError must not escape it.
        def connect_fn():
            raise _OperationalError("connection refused")

        with self.assertRaises(next_database.MigrationError):
            self._wait(connect_fn, timeout=5)

    def test_non_operational_errors_are_not_retried(self):
        attempts = []

        def connect_fn():
            attempts.append(1)
            raise ValueError("DATABASE_URL is malformed")

        with self.assertRaises(ValueError):
            self._wait(connect_fn, timeout=30)

        self.assertEqual(len(attempts), 1)
        self.assertEqual(self.clock.slept, [])


class DbWaitTimeoutTests(unittest.TestCase):
    def test_defaults_when_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(next_database.db_wait_timeout(), next_database.DEFAULT_DB_WAIT_TIMEOUT)

    def test_reads_the_override(self):
        with patch.dict(os.environ, {next_database.DB_WAIT_TIMEOUT_ENV: "45"}, clear=True):
            self.assertEqual(next_database.db_wait_timeout(), 45.0)

    def test_falls_back_on_a_value_that_is_not_a_number_or_not_positive(self):
        for raw in ("", "   ", "abc", "0", "-5"):
            with self.subTest(raw=raw):
                with patch.dict(os.environ, {next_database.DB_WAIT_TIMEOUT_ENV: raw}, clear=True):
                    self.assertEqual(
                        next_database.db_wait_timeout(), next_database.DEFAULT_DB_WAIT_TIMEOUT
                    )


class MigrationLockTests(unittest.TestCase):
    def test_the_lock_key_is_stable(self):
        # Runners in different containers must derive the same key, so this value
        # is part of the contract and must not drift.
        self.assertEqual(
            next_database.MIGRATION_LOCK_KEY,
            int.from_bytes(
                __import__("hashlib").sha256(b"discvault_next_migrations").digest()[:8],
                "big",
                signed=True,
            ),
        )
        self.assertGreaterEqual(next_database.MIGRATION_LOCK_KEY, -(2**63))
        self.assertLess(next_database.MIGRATION_LOCK_KEY, 2**63)

    def test_it_takes_and_releases_the_advisory_lock(self):
        conn = _FakeConn()

        with next_database.migration_lock(conn):
            self.assertEqual(conn.calls, [("SELECT pg_advisory_lock(%s)", (next_database.MIGRATION_LOCK_KEY,))])

        self.assertEqual(
            conn.calls[-1], ("SELECT pg_advisory_unlock(%s)", (next_database.MIGRATION_LOCK_KEY,))
        )
        self.assertEqual(conn.commits, 2)

    def test_it_releases_the_lock_when_the_body_raises(self):
        conn = _FakeConn()

        with self.assertRaises(next_database.MigrationError):
            with next_database.migration_lock(conn):
                raise next_database.MigrationError("checksum mismatch")

        self.assertEqual(
            conn.calls[-1], ("SELECT pg_advisory_unlock(%s)", (next_database.MIGRATION_LOCK_KEY,))
        )

    def test_an_aborted_transaction_rolls_back_instead_of_masking_the_failure(self):
        conn = _FakeConn(fail_after=1)

        with self.assertRaises(next_database.MigrationError):
            with next_database.migration_lock(conn):
                raise next_database.MigrationError("migration 041 failed")

        self.assertEqual(conn.rollbacks, 1)


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        self._conn.calls.append((query, params))
        if self._conn.fail_after is not None and len(self._conn.calls) > self._conn.fail_after:
            raise RuntimeError("current transaction is aborted")


class _FakeConn:
    def __init__(self, fail_after=None):
        self.calls = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_after = fail_after

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


if __name__ == "__main__":
    unittest.main()
