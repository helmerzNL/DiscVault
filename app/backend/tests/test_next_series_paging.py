"""A series library larger than one page is reachable, and says when it is cut.

`/api/next/series` had a hard `LIMIT 500` in the query, no `limit` parameter on
the route, and no field in the response about either. A library above five
hundred series was therefore **silently short**: the client received a
well-formed list, had no way to ask for the rest, and nothing indicated that
there was a rest.

That is the failure this fixes, and it is worth being precise about which half
matters. The size of the answer was never the problem -- five hundred rows is a
perfectly reasonable page. The problem was that an incomplete answer was
indistinguishable from a complete one.

**Today's behaviour is preserved exactly.** The default and the ceiling are both
500, so a client that sends no parameters gets the same first five hundred rows
it got before. `limit`, `offset`, `hasMore` and `nextOffset` are additive, and a
client that ignores them is no worse off than it was -- except that it is now
possible for it to find out.
"""

import os
import sys
import unittest
import uuid


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
class SeriesPagingTests(unittest.TestCase):
    PREFIX = "paging-probe-"
    COUNT = 7

    @classmethod
    def setUpClass(cls):
        cls.app = next_app.create_app()

    def setUp(self):
        self.tag = f"{self.PREFIX}{uuid.uuid4().hex[:8]}-"
        self.client = self.app.test_client()
        self.addCleanup(self._cleanup)
        with self._connect() as conn:
            with conn.cursor() as cur:
                for index in range(self.COUNT):
                    cur.execute(
                        "INSERT INTO series (public_id, title, sort_title) "
                        "VALUES (gen_random_uuid()::text, %s, %s)",
                        (f"{self.tag}{index:02d}", f"{self.tag}{index:02d}"),
                    )
            conn.commit()

    def _connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)

    def _cleanup(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM series WHERE title LIKE %s", (f"{self.PREFIX}%",))
            conn.commit()

    def _page(self, **params):
        query = "&".join(f"{key}={value}" for key, value in params.items())
        query = f"q={self.tag}" + (f"&{query}" if query else "")
        response = self.client.get(f"/api/next/series?{query}")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def test_a_complete_page_says_there_is_no_more(self):
        payload = self._page()
        self.assertEqual(len(payload["series"]), self.COUNT)
        self.assertFalse(payload["hasMore"])
        self.assertIsNone(payload["nextOffset"])

    def test_a_truncated_page_says_so_rather_than_looking_complete(self):
        # The whole point. Before this change the response for a cut list was
        # indistinguishable from the response for a complete one.
        payload = self._page(limit=3)
        self.assertEqual(len(payload["series"]), 3)
        self.assertTrue(payload["hasMore"])
        self.assertEqual(payload["nextOffset"], 3)

    def test_following_nextOffset_reaches_every_row_exactly_once(self):
        seen = []
        offset = 0
        for _ in range(self.COUNT + 2):  # bounded: a runaway loop is a failure too
            payload = self._page(limit=2, offset=offset)
            seen.extend(row["title"] for row in payload["series"])
            if not payload["hasMore"]:
                break
            offset = payload["nextOffset"]
        else:  # pragma: no cover - only on a broken cursor
            self.fail("paging never reported the end of the list")
        self.assertEqual(len(seen), self.COUNT)
        self.assertEqual(len(set(seen)), self.COUNT, "a row was served twice")
        self.assertEqual(seen, sorted(seen), "paging must not disturb the ordering")

    def test_the_last_page_is_not_reported_as_having_more(self):
        # The off-by-one worth pinning: `has_more` is answered by fetching one
        # row beyond the page, so a page that ends exactly on the boundary must
        # not claim a further page that would come back empty.
        payload = self._page(limit=self.COUNT)
        self.assertEqual(len(payload["series"]), self.COUNT)
        self.assertFalse(payload["hasMore"])

    def test_the_default_is_unchanged_for_a_client_that_sends_nothing(self):
        payload = self._page()
        self.assertEqual(payload["limit"], 500)
        self.assertEqual(payload["offset"], 0)

    def test_an_oversized_limit_is_clamped_rather_than_honoured(self):
        self.assertEqual(self._page(limit=99999)["limit"], 500)

    def test_a_malformed_limit_is_a_400(self):
        response = self.client.get("/api/next/series?limit=abc")
        self.assertEqual(response.status_code, 400, response.get_data(as_text=True))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
