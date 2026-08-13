"""Adding a film must not hold a transaction open while a provider thinks.

`POST /api/next/import/movie` resolved its metadata -- TMDB, OMDb, MovieVault --
and then wrote the film, and it did both inside one transaction. The route's own
`with conn.transaction():` was not the whole story: `connect()` runs with
`autocommit=False`, so the pre-checks above it (permission, table presence, the
two barcode lookups) had *already* opened a transaction, and it stayed open
across every provider call regardless of where the explicit block began.

That is the shape the user's `pg_stat_activity` samples caught twice: one
transaction `idle in transaction` for 14.5 seconds and another for 10.1, doing
nothing but waiting on somebody else's server. A transaction in that state pins
the snapshot horizon, so autovacuum cannot clean up behind it, and once it does
start writing it holds row locks on `movies`, `containers`, `movie_credits`,
`people` and the single global `sync_state` row for the rest of its life.

Nobody can promise how fast an external provider answers, so the fix is not to
make the lookup quicker. The resolution now runs with no transaction held at
all, and the transaction opens when there is something to write.

The measurable claim is exactly one thing, and the first test states it
directly rather than by proxy: at the moment a provider is called, the
connection's transaction status is IDLE. Before this change it was INTRANS.
"""

import os
import sys
import unittest
import uuid
from unittest import mock


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:  # pragma: no cover - minimal environments
    psycopg = None
    dict_row = None

DATABASE_URL = os.environ.get("DATABASE_URL")

try:
    from app.backend import next_app
except ModuleNotFoundError as exc:  # pragma: no cover - minimal environments
    if exc.name not in {"flask", "psycopg", "cbor2", "argon2", "jwt", "segno", "PIL"}:
        raise
    next_app = None


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and next_app is not None,
    "PostgreSQL test database is not configured",
)
class ProviderCallsRunOutsideATransactionTests(unittest.TestCase):
    """The measurable claim, stated directly."""

    @classmethod
    def setUpClass(cls):
        cls.app = next_app.create_app()

    def setUp(self):
        self.title = f"Transaction Boundary Probe {uuid.uuid4()}"
        self.client = self.app.test_client()
        self.addCleanup(self._delete_probe_films)

    def _delete_probe_films(self):
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM movies WHERE title LIKE 'Transaction Boundary Probe %'")
            conn.commit()

    def _import(self, spy, body=None):
        with mock.patch.object(next_app, "lookup_metadata_sources", spy):
            return self.client.post(
                "/api/next/import/movie",
                json=body or {"title": self.title, "importMode": "movie"},
            )

    def test_the_provider_is_called_with_no_transaction_open(self):
        observed = []

        def spy(conn, payload, actor, *args, **kwargs):
            observed.append(conn.info.transaction_status)
            return {"proposal": {"movieUpdates": {"title": self.title, "year": "1999"}}, "sources": []}

        response = self._import(spy)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertTrue(observed, "the import did not reach a metadata lookup at all")
        for status in observed:
            self.assertEqual(
                status,
                psycopg.pq.TransactionStatus.IDLE,
                "a provider call must not be made with a transaction open -- this is "
                "the 10-to-14-second `idle in transaction` the change removes",
            )

    def test_the_film_is_still_created(self):
        # The boundary move must not cost the route its actual job.
        def spy(conn, payload, actor, *args, **kwargs):
            return {"proposal": {"movieUpdates": {"title": self.title, "year": "1999"}}, "sources": []}

        response = self._import(spy)
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload.get("state"), "created")
        self.assertEqual((payload.get("movie") or {}).get("title"), self.title)

    def test_the_write_still_runs_inside_a_transaction(self):
        # Taking the lookup out must not take the atomicity out with it: the
        # upsert, the proposal, the queued refresh job and the audit event still
        # have to land together or not at all.
        observed = []
        original = next_app.apply_movie_upsert

        def watching_upsert(conn, *args, **kwargs):
            observed.append(conn.info.transaction_status)
            return original(conn, *args, **kwargs)

        def spy(conn, payload, actor, *a, **kw):
            return {"proposal": {"movieUpdates": {"title": self.title, "year": "1999"}}, "sources": []}

        with mock.patch.object(next_app, "apply_movie_upsert", watching_upsert):
            response = self._import(spy)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(observed, [psycopg.pq.TransactionStatus.INTRANS])

    def test_a_failing_provider_leaves_nothing_behind(self):
        # With the lookup outside the transaction, a provider that raises must
        # still not half-create a film.
        def exploding(conn, payload, actor, *args, **kwargs):
            raise RuntimeError("provider unavailable")

        # The app's catch-all error handler turns this into a 500 rather than
        # letting it escape; what matters is what is left in the database.
        response = self._import(exploding)
        self.assertEqual(response.status_code, 500)
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) AS count FROM movies WHERE title = %s", (self.title,))
                count = cur.fetchone()["count"]
        self.assertEqual(count, 0)

    def test_the_barcode_pre_check_still_refuses_a_duplicate(self):
        # The pre-checks read before the commit that opens the resolution. If
        # committing there had cost them their answer, a second import of the
        # same barcode would create a second film.
        barcode = f"9{uuid.uuid4().int % 10**11:011d}"

        def spy(conn, payload, actor, *args, **kwargs):
            return {"proposal": {"movieUpdates": {"title": self.title, "year": "1999"}}, "sources": []}

        first = self._import(spy, {"title": self.title, "barcode": barcode, "importMode": "movie"})
        self.assertEqual(first.status_code, 201)
        second = self._import(spy, {"title": self.title, "barcode": barcode, "importMode": "movie"})
        self.assertEqual(second.get_json().get("state"), "already_exists")


class TheRouteStructureTests(unittest.TestCase):
    """Read the route back, so the boundary cannot drift without failing here."""

    @classmethod
    def setUpClass(cls):
        import pathlib

        source = (
            pathlib.Path(__file__).resolve().parents[1] / "next_app.py"
        ).read_text(encoding="utf-8")
        start = source.index("def import_movie_from_metadata():")
        end = source.index("\n    @flask_app.", start)
        cls.route = source[start:end]

    def test_no_provider_lookup_remains_inside_the_transaction(self):
        transaction_at = self.route.index("with conn.transaction():")
        after = self.route[transaction_at:]
        self.assertNotIn(
            "lookup_metadata_sources(",
            after,
            "a lookup below the transaction opener is a network call holding locks",
        )

    def test_every_lookup_sits_above_the_transaction(self):
        transaction_at = self.route.index("with conn.transaction():")
        before = self.route[:transaction_at]
        self.assertGreaterEqual(
            before.count("lookup_metadata_sources("),
            3,
            "the route resolves in up to three lookups; all of them belong above",
        )

    def test_the_transaction_is_closed_before_the_lookups_and_before_the_write(self):
        commits = [i for i in range(len(self.route)) if self.route.startswith("conn.commit()", i)]
        self.assertEqual(
            len(commits),
            2,
            "one commit to end the pre-check snapshot, one to end the lookups' own "
            "transaction before the write takes its locks",
        )
        first_lookup = self.route.index("lookup_metadata_sources(")
        transaction_at = self.route.index("with conn.transaction():")
        self.assertLess(commits[0], first_lookup)
        self.assertLess(first_lookup, commits[1])
        self.assertLess(commits[1], transaction_at)

    def test_the_box_set_branch_decides_before_the_transaction(self):
        # Whether this is a box-set import is resolved above, so the branch
        # inside the transaction only writes.
        transaction_at = self.route.index("with conn.transaction():")
        self.assertLess(self.route.index("importing_box_set = "), transaction_at)
        self.assertIn("if importing_box_set:", self.route[transaction_at:])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
