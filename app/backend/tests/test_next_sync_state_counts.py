"""`GET /api/next/sync/state?counts=1` says how big the library is, without a bootstrap.

A client that is about to link to a server wants one number before it decides
anything: how many films and containers the server holds. Until now the only way
to learn it was to download the bootstrap, which is capped (1000 by default,
5000 at most) and so cannot answer for a large collection -- the very collections
where the answer matters most.

`counts` is that number, and it is deliberately the *same population* the
bootstrap would return: live rows only, the whole instance, no per-user
visibility. Anything else would make "the server has N films" and "the bootstrap
delivered N films" two different statements.

Two properties are pinned here because they are the ones a later edit would
break quietly:

* **The plain call stays the cheap one.** `/sync/state` is the cheapest route the
  server has (it authenticates and reads one integer). `counts` is opt-in, and
  without the parameter the response is the response it always was.
* **It is not a public route.** Nothing in the handler checks who is asking; the
  protection is the global `before_request` gate. That is fine exactly as long as
  the path never joins the public list, and nothing else would notice if it did.

Point `DATABASE_URL` at a database with every migration applied for the
database-backed cases; without it those skip.
"""

import os
import sys
import unittest
import uuid
from unittest.mock import patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:  # pragma: no cover - minimal environments
    psycopg = None
    dict_row = None

try:
    from app.backend import next_app
except ModuleNotFoundError as exc:  # pragma: no cover - minimal environments
    if exc.name not in {"flask", "psycopg", "cbor2", "argon2", "jwt", "segno", "PIL"}:
        raise
    next_app = None

DATABASE_URL = os.environ.get("DATABASE_URL")

PREFIX = "sync-state-counts-test"


@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class SyncStateCountsRouteTests(unittest.TestCase):
    """No database: the connection is faked and only the route's own logic runs."""

    def _get(self, query=""):
        client = next_app.app.test_client()
        with (
            patch.object(next_app, "connect") as connect,
            patch.object(next_app, "table_exists", return_value=False),
            patch.object(next_app, "current_revision", return_value=7),
            patch.object(
                next_app,
                "sync_library_counts",
                return_value={"movies": 3, "containers": 2},
            ) as counts,
        ):
            connect.return_value.__enter__.return_value = object()
            response = client.get(f"/api/next/sync/state{query}")
        return response, counts

    def test_the_plain_call_carries_no_counts_and_never_counts(self):
        response, counts = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("counts", response.get_json())
        counts.assert_not_called()

    def test_the_counts_parameter_adds_both_numbers(self):
        for spelling in ("1", "true", "TRUE"):
            with self.subTest(counts=spelling):
                response, counts = self._get(f"?counts={spelling}")

                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.get_json()["counts"], {"movies": 3, "containers": 2}
                )
                counts.assert_called_once()

    def test_anything_else_is_not_a_request_for_counts(self):
        for spelling in ("0", "false", "", "yes"):
            with self.subTest(counts=spelling):
                response, counts = self._get(f"?counts={spelling}")

                self.assertNotIn("counts", response.get_json())
                counts.assert_not_called()

    def test_the_rest_of_the_response_is_untouched_by_the_parameter(self):
        plain, _ = self._get()
        with_counts, _ = self._get("?counts=1")

        expected = plain.get_json()
        actual = with_counts.get_json()
        actual.pop("counts")
        # `serverTime` ticks; everything else must be identical.
        expected.pop("serverTime")
        actual.pop("serverTime")
        self.assertEqual(actual, expected)

    def test_an_anonymous_caller_is_refused_when_auth_is_enabled(self):
        # The handler checks nothing itself. The refusal comes from the global
        # `before_request` gate, so this pins that the path is not public.
        for query in ("", "?counts=1"):
            with self.subTest(query=query):
                client = next_app.app.test_client()
                with (
                    patch.object(next_app, "connect") as connect,
                    patch.object(next_app, "next_auth_effective_enabled", return_value=True),
                    patch.object(next_app, "next_auth_current_user", return_value=None),
                ):
                    connect.return_value.__enter__.return_value = object()
                    response = client.get(f"/api/next/sync/state{query}")

                self.assertEqual(response.status_code, 401)
                body = response.get_json()
                self.assertIs(body["auth_required"], True)
                self.assertNotIn("counts", body)

    def test_the_path_is_not_on_the_public_list(self):
        self.assertFalse(next_app.is_public_next_path("/api/next/sync/state"))


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and next_app is not None,
    "PostgreSQL test database is not configured",
)
class SyncStateCountsDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = next_app.create_app()

    def setUp(self):
        self.client = self.app.test_client()
        self.addCleanup(self._cleanup)

    def _connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)

    def _cleanup(self):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM movies WHERE title LIKE %s", (f"{PREFIX}%",))
            cur.execute("DELETE FROM containers WHERE title LIKE %s", (f"{PREFIX}%",))

    def _counts(self):
        response = self.client.get("/api/next/sync/state?counts=1")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["counts"]

    def _insert_movie(self, *, deleted):
        movie_id = uuid.uuid4()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movies (id, public_id, title, sort_title, year, deleted_at)
                VALUES (%s, %s, %s, %s, 2001, CASE WHEN %s THEN now() END)
                """,
                (movie_id, str(movie_id), f"{PREFIX} {movie_id}", f"{PREFIX} {movie_id}", deleted),
            )

    def _insert_container(self, *, deleted):
        container_id = uuid.uuid4()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO containers (id, public_id, container_type, title, deleted_at)
                VALUES (%s, %s, 'box_set', %s, CASE WHEN %s THEN now() END)
                """,
                (container_id, str(container_id), f"{PREFIX} {container_id}", deleted),
            )

    def test_tombstones_are_not_counted(self):
        before = self._counts()

        for _ in range(2):
            self._insert_movie(deleted=False)
        self._insert_movie(deleted=True)
        self._insert_container(deleted=False)
        for _ in range(2):
            self._insert_container(deleted=True)

        after = self._counts()
        self.assertEqual(after["movies"], before["movies"] + 2)
        self.assertEqual(after["containers"], before["containers"] + 1)

    def test_the_counts_are_the_population_the_bootstrap_returns(self):
        # The point of the field: "the server has N" and "the bootstrap
        # delivered N" must be one statement. Only comparable while the
        # bootstrap is not cut, so a database too big for that skips the check
        # rather than asserting something false.
        self._insert_movie(deleted=False)
        self._insert_movie(deleted=True)
        self._insert_container(deleted=False)
        self._insert_container(deleted=True)

        bootstrap = self.client.get("/api/next/sync/bootstrap?limit=5000").get_json()
        if not bootstrap["complete"]:
            self.skipTest("the test database is larger than one bootstrap")

        counts = self._counts()
        self.assertEqual(counts["movies"], len(bootstrap["payload"]["movies"]))
        self.assertEqual(counts["containers"], len(bootstrap["payload"]["containers"]))

    def test_the_plain_call_still_has_no_counts(self):
        response = self.client.get("/api/next/sync/state")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("counts", response.get_json())


if __name__ == "__main__":
    unittest.main()
