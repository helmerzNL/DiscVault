"""The People list has to answer when nobody has searched yet.

`GET /api/next/people` answered HTTP 500 whenever `q` was absent or blank:
`clean_text()` returns `None` for a missing value and `people_list_entities()`
called `.lower()` on it. That is not an edge case. The People page only sends
`q` once something has been typed, so its *first* request -- the default
`/api/next/people?limit=180` -- was the one that failed, while every searched
request worked.

Nothing executed the route. The tests beside it exercise pure helpers or read
the source text, so the defect was as old as the module and no job could see
it. These tests run the route against a real database, with and without a
search term, and pin the echoed `query`: an absent search is the empty string,
never `null`, so the field stays a string for every client that decodes it.
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
    from app.backend.next_people import people_list_entities
except ModuleNotFoundError as exc:  # pragma: no cover - minimal environments
    if exc.name not in {"flask", "psycopg", "cbor2", "argon2", "jwt", "segno", "PIL"}:
        raise
    next_app = None
    people_list_entities = None


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and next_app is not None,
    "PostgreSQL test database is not configured",
)
class PeopleListRouteTests(unittest.TestCase):
    PREFIX = "people-list-probe-"
    MAX_LIMIT = 500

    @classmethod
    def setUpClass(cls):
        cls.app = next_app.create_app()

    def setUp(self):
        self.tag = f"{self.PREFIX}{uuid.uuid4().hex[:8]}"
        self.name = f"{self.tag} Person"
        self.client = self.app.test_client()
        self.addCleanup(self._cleanup)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO people (public_id, name) VALUES (gen_random_uuid()::text, %s)",
                    (self.name,),
                )
            conn.commit()

    def _connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)

    def _cleanup(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM people WHERE name LIKE %s", (f"{self.PREFIX}%",))
            conn.commit()

    def _people(self, query_string):
        response = self.client.get(f"/api/next/people{query_string}")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertIsInstance(payload["people"], list)
        return payload

    def _assert_unfiltered(self, payload):
        self.assertEqual(payload["query"], "")
        names = [person["name"] for person in payload["people"]]
        # An empty search filters nothing, so the probe is in the list -- unless
        # the database holds more people than one response carries, in which
        # case its absence says nothing either way.
        if len(names) < self.MAX_LIMIT:
            self.assertIn(self.name, names)

    def test_the_default_request_of_the_people_page_answers(self):
        # The defect, stated directly: exactly what the PWA sends on first load.
        payload = self._people("?limit=180")
        self.assertEqual(payload["query"], "")
        self.assertEqual(payload["limit"], 180)

    def test_no_search_term_lists_people_unfiltered(self):
        self._assert_unfiltered(self._people(f"?limit={self.MAX_LIMIT}"))

    def test_a_blank_search_term_is_no_search_term(self):
        self._assert_unfiltered(self._people(f"?q=&limit={self.MAX_LIMIT}"))
        self._assert_unfiltered(self._people(f"?q=%20%20&limit={self.MAX_LIMIT}"))

    def test_a_search_term_still_filters_and_is_echoed(self):
        # Case-insensitive, as before: the fix must not change a search that
        # already worked.
        payload = self._people(f"?q={self.tag.upper()}")
        self.assertEqual(payload["query"], self.tag.upper())
        self.assertEqual([person["name"] for person in payload["people"]], [self.name])

    def test_the_lister_itself_accepts_a_missing_query(self):
        # The route is not the only caller (next_app re-exports this function),
        # so the guard belongs here and not only at the edge.
        with self._connect() as conn:
            for missing in (None, "", "   "):
                people = people_list_entities(conn, query=missing, limit=self.MAX_LIMIT)
                self.assertIsInstance(people, list)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
