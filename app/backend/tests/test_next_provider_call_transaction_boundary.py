"""A provider is never called with a transaction open.

The user's own `pg_stat_activity` sample, taken from the beta instance while
using it, is the specification for this module:

    pid    | txn_age         | state               | query
    156574 | 00:00:05.348942 | idle in transaction | RELEASE "_pg3_1"
    156575 | 00:00:04.729044 | idle in transaction | SELECT p.settings_schema, s.settings, s.secrets
    156582 | 00:00:04.689799 | idle in transaction | SELECT key, value FROM app_settings
    156585 | 00:00:04.689297 | idle in transaction | SELECT key, value FROM app_settings
    156612 | 00:00:02.880832 | idle in transaction | SELECT key, value FROM app_settings

Five backends, three to five seconds each, and the last statement every one of
them ran is a *read of the plugin's configuration*. That read is the last thing
that happens before the provider is called, and with `autocommit=False` it
opens the transaction that then sits idle for as long as the provider takes.

**Which is why the release has to happen immediately before the call, and not
at the top of the refresh function.** A first attempt put a `conn.commit()` at
the start of `refresh_movie_metadata`, `refresh_series_metadata` and
`refresh_person_metadata`. It measured no better at all: everything between
those commits and the network -- the entity, the plugin list, the plugin's
configuration, the lookup cache -- is a read, and each one reopened the
transaction the commit had just closed.

The baseline for the movie route reproduces the user's line exactly:

    1 backend(s) idle in transaction
    pid 11376: SELECT p.settings_schema, s.settings, s.secrets_ref

and with the release in place, none.

The routes had to change too. `refresh_series_metadata` and
`refresh_person_metadata` were called inside `with conn.transaction():`, and
psycopg refuses a commit under one -- correctly, since a caller that opened a
block asked for atomicity. So those wrappers are gone, and the two functions
now own their boundaries the way `refresh_movie_metadata` already claimed to.
The trade-off is the one that route already accepted: the audit event commits
with the connection rather than atomically with the refresh.
"""

import os
import pathlib
import re
import sys
import unittest
import uuid


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

BACKEND = pathlib.Path(__file__).resolve().parents[1]

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:  # pragma: no cover - minimal environments
    psycopg = None
    dict_row = None

DATABASE_URL = os.environ.get("DATABASE_URL")

try:
    from app.backend import next_app, next_metadata
except ModuleNotFoundError as exc:  # pragma: no cover - minimal environments
    if exc.name not in {"flask", "psycopg", "cbor2", "argon2", "jwt", "segno", "PIL"}:
        raise
    next_app = None
    next_metadata = None


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and next_metadata is not None,
    "PostgreSQL test database is not configured",
)
class ReleaseReadTransactionTests(unittest.TestCase):
    """The helper itself, including the two cases it must refuse."""

    def connect(self):
        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)
        self.addCleanup(conn.close)
        return conn

    def test_it_closes_a_transaction_a_read_opened(self):
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS x")
        self.assertEqual(conn.info.transaction_status, psycopg.pq.TransactionStatus.INTRANS)
        self.assertTrue(next_metadata.release_read_transaction(conn))
        self.assertEqual(conn.info.transaction_status, psycopg.pq.TransactionStatus.IDLE)

    def test_a_stand_in_connection_is_refused_rather_than_raising(self):
        # The metadata pipeline is deliberately callable with a connection-shaped
        # object. A transaction boundary must never be load-bearing, for the same
        # reason the lookup cache must not be.
        self.assertFalse(next_metadata.release_read_transaction(object()))

    def test_a_caller_holding_a_transaction_block_keeps_it(self):
        # psycopg forbids committing inside `with conn.transaction():`, and that
        # caller asked for atomicity across the call. Refusing is correct; the
        # long transaction is the price of the choice they made.
        conn = self.connect()
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT 1 AS x")
            self.assertFalse(next_metadata.release_read_transaction(conn))
            with conn.cursor() as cur:
                cur.execute("SELECT 2 AS x")
                self.assertEqual(cur.fetchone()["x"], 2, "the transaction must still be usable")

    def test_a_cache_hit_needs_no_release(self):
        # A hit never reaches the network, so committing there would end a
        # caller's transaction for no reason at all.
        source = (BACKEND / "next_metadata.py").read_text(encoding="utf-8")
        start = source.index("def run_cached_plugin_entrypoint(")
        body = source[start : source.index("\ndef ", start + 10)]
        cached_return = body.index("return cached")
        release_after = body.index("release_read_transaction", cached_return)
        network_after = body.index("run_plugin_entrypoint(", release_after)
        self.assertLess(release_after, network_after)


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and next_app is not None,
    "PostgreSQL test database is not configured",
)
class NoTransactionIsHeldDuringAProviderCallTests(unittest.TestCase):
    """The measurable claim, in the same terms the user measured it."""

    @classmethod
    def setUpClass(cls):
        cls.app = next_app.create_app()

    def setUp(self):
        self.tag = f"provider-boundary-{uuid.uuid4()}"
        self.addCleanup(self._cleanup)
        self.watcher = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)
        self.addCleanup(self.watcher.close)
        self.movie_id = self._admin(
            "INSERT INTO movies (public_id, title, year) "
            "VALUES (gen_random_uuid()::text, %s, 2001) RETURNING id",
            (self.tag,),
        )[0]["id"]

    def _admin(self, sql, params=None):
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params or ())
                rows = cur.fetchall() if cur.description else []
            conn.commit()
        return rows

    def _cleanup(self):
        movie_ids = [
            str(row["id"])
            for row in self._admin("SELECT id FROM movies WHERE title LIKE %s", ("provider-boundary-%",))
        ]
        if movie_ids:
            self._admin(
                "DELETE FROM background_jobs WHERE payload->>'movieId' = ANY(%s)", (movie_ids,)
            )
        self._admin("DELETE FROM movies WHERE title LIKE %s", ("provider-boundary-%",))

    def _idle_in_transaction(self):
        with self.watcher.cursor() as cur:
            cur.execute(
                """
                SELECT pid, left(query, 70) AS query
                FROM pg_stat_activity
                WHERE state = 'idle in transaction'
                  AND pid <> %s
                  AND datname = current_database()
                """,
                (self.watcher.info.backend_pid,),
            )
            return cur.fetchall()

    def test_nothing_is_idle_in_transaction_while_a_provider_is_called(self):
        from unittest import mock

        seen: list[list] = []

        def spy(plugin_id, entrypoint, payload, context=None, *args, **kwargs):
            seen.append(self._idle_in_transaction())
            return {"status": "ok", "result": {}}

        with mock.patch.object(next_metadata, "run_plugin_entrypoint", spy):
            response = self.app.test_client().post(
                f"/api/next/movies/{self.movie_id}/metadata/refresh", json={}
            )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(seen, "the refresh never reached a provider")
        for held in seen:
            self.assertEqual(
                held,
                [],
                "a backend sat idle in transaction while a provider was being called -- "
                "this is the 3-to-5-second state the beta instance was measured in",
            )


class EveryProviderCallIsPrecededByAReleaseTests(unittest.TestCase):
    """Structural, because a new call site is how this comes back."""

    @classmethod
    def setUpClass(cls):
        cls.metadata = (BACKEND / "next_metadata.py").read_text(encoding="utf-8")
        cls.app = (BACKEND / "next_app.py").read_text(encoding="utf-8")

    # Takes no connection, so it cannot release anything; the loop that builds
    # its context does it instead, once, before reaching any of the three calls.
    RELEASED_BY_ITS_CALLER = {"plugin_receiver_optional_detail"}

    @staticmethod
    def _enclosing_function(source: str, offset: int) -> tuple[str, str]:
        """The name and body of the top-level function containing this offset."""

        starts = [m.start() for m in re.finditer(r"^def (\w+)\(", source, re.M) if m.start() < offset]
        start = starts[-1]
        name = re.match(r"def (\w+)\(", source[start:]).group(1)
        end = source.find("\ndef ", start + 1)
        return name, source[start : offset if end == -1 else min(offset, end)]

    def test_every_function_that_can_release_does_so_before_calling_a_provider(self):
        for module, source in (("next_metadata.py", self.metadata), ("next_app.py", self.app)):
            for match in re.finditer(r"^\s+.*\brun_plugin_entrypoint\(", source, re.M):
                name, body_before_call = self._enclosing_function(source, match.start())
                if name in self.RELEASED_BY_ITS_CALLER:
                    continue
                self.assertIn(
                    "release_read_transaction",
                    body_before_call,
                    f"{module}: {name}() reaches a provider without releasing the transaction "
                    "its own reads opened -- the reads immediately above the call are what "
                    "reopen it, so a release anywhere earlier does not count",
                )

    def test_the_receiver_push_releases_inside_its_loop(self):
        # Not merely before it: the caller already commits before the push, and
        # the per-plugin configuration read undoes that on every iteration.
        name, _ = self._enclosing_function(
            self.metadata, self.metadata.index('"receive_metadata", payload, context')
        )
        start = self.metadata.index(f"def {name}(")
        body = self.metadata[start : self.metadata.index("\ndef ", start + 10)]
        loop = body.index("for plugin in receivers:")
        release = body.index("release_read_transaction", loop)
        first_call = body.index("plugin_receiver_optional_detail(", loop)
        self.assertLess(release, first_call)

    def test_the_series_routes_no_longer_wrap_the_refresh(self):
        for match in re.finditer(r"refresh_series_metadata\(conn, series_uuid\)", self.app):
            window = self.app[max(0, match.start() - 400) : match.start()]
            self.assertNotIn(
                "with conn.transaction():",
                window.rsplit("\n\n", 1)[-1],
                "a transaction block around the refresh makes the release impossible",
            )

    def test_the_person_callers_no_longer_wrap_the_refresh(self):
        for match in re.finditer(r"refresh_person_metadata\(\s*(conn|person_conn)", self.app):
            window = self.app[max(0, match.start() - 300) : match.start()]
            self.assertNotIn("with conn.transaction():", window.rsplit("\n\n", 1)[-1])
            self.assertNotIn("with person_conn.transaction():", window.rsplit("\n\n", 1)[-1])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
