"""A bootstrap that is not complete has to say so.

The sync contract calls `GET /api/next/sync/bootstrap` a *complete snapshot*
(`docs/discvault-sync-server-opdracht.md`: "volledige snapshot"). It was not
necessarily one. `limit` defaults to 1000 and caps the movies, containers,
membership, identifiers and credits, and the response carried nothing about it
-- so a collection above a thousand films returned a well-formed, short list,
and a client had no way to tell that apart from having everything.

That is the inversion worth holding on to. PERF-04 filed this under *unbounded*
responses being too large. The caps were already there; the danger was the
opposite one, and quieter: the answer was too **small** and looked complete. A
client syncing at the stated ~10,000-title target would have been missing most
of the library with nothing to indicate it.

The fix is not a smaller answer or a larger one. It is an honest one:
`complete` says whether anything was cut, and `collections` says which list and
at what cap, so a client that cannot trust the snapshot knows exactly which part
to distrust.

**Nothing about the payload changed.** Both fields are additive, the caps are
untouched, and a client that reads neither receives exactly what it received
before.
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
class BootstrapCompletenessTests(unittest.TestCase):
    PREFIX = "bootstrap-probe-"
    COUNT = 4

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
                        "INSERT INTO movies (public_id, title, sort_title, year) "
                        "VALUES (gen_random_uuid()::text, %s, %s, 2001)",
                        (f"{self.tag}{index:02d}", f"{self.tag}{index:02d}"),
                    )
            conn.commit()

    def _connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)

    def _cleanup(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM movies WHERE title LIKE %s", (f"{self.PREFIX}%",))
            conn.commit()

    def _bootstrap(self, **params):
        query = "&".join(f"{key}={value}" for key, value in params.items())
        response = self.client.get(f"/api/next/sync/bootstrap?{query}")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def test_a_cut_snapshot_reports_itself_incomplete(self):
        # The defect, stated directly: this response used to be indistinguishable
        # from a complete one.
        payload = self._bootstrap(limit=1)
        self.assertFalse(payload["complete"])
        self.assertTrue(payload["collections"]["movies"]["truncated"])
        self.assertEqual(len(payload["payload"]["movies"]), 1)

    def test_a_whole_snapshot_reports_itself_complete(self):
        payload = self._bootstrap(limit=5000)
        self.assertTrue(payload["complete"])
        self.assertFalse(payload["collections"]["movies"]["truncated"])

    def test_the_cap_that_bit_is_named_with_its_value(self):
        # "Something was cut" is not actionable; which list, and at what cap, is.
        payload = self._bootstrap(limit=2)
        movies = payload["collections"]["movies"]
        self.assertEqual(movies["limit"], 2)
        self.assertTrue(movies["truncated"])

    def test_every_capped_collection_is_accounted_for(self):
        # A collection that is capped but missing from this block is exactly the
        # silent truncation this change removes, one level down.
        collections = self._bootstrap()["collections"]
        self.assertEqual(
            set(collections),
            {
                "movies",
                "containers",
                "containerMembership",
                "movieIdentifiers",
                "moviePeople",
                # The user's own images. Capped by *owning entity* rather than
                # by picture -- the list is short per entity and the cap counts
                # entities -- but capped all the same, so it is named here.
                "ownImages",
            },
        )
        for name, entry in collections.items():
            self.assertIsInstance(entry["limit"], int, name)
            self.assertIsInstance(entry["truncated"], bool, name)

    def test_the_deliberately_unbounded_collections_are_not_claimed_as_capped(self):
        # `locations` is unbounded on purpose -- sync-contract §4c, since a
        # movie's location_id is meaningless without the row it points at.
        # Listing it as capped would invite a client to page something that has
        # no pages, and truncating it would reintroduce the unresolvable ids
        # that rule exists to remove.
        collections = self._bootstrap()["collections"]
        for name in ("locations", "series", "seriesSeasons", "settings"):
            self.assertNotIn(name, collections)

    def test_the_payload_shape_is_unchanged(self):
        # Both new fields sit beside the payload rather than inside it, so a
        # client that reads neither is exactly where it was.
        payload = self._bootstrap()
        self.assertIn("payload", payload)
        self.assertIn("movies", payload["payload"])
        self.assertNotIn("complete", payload["payload"])
        self.assertNotIn("collections", payload["payload"])

    def test_the_returned_rows_never_include_the_probe_row(self):
        # `truncated` is answered by fetching one row past the cap. That row is
        # a signal, not data, and handing it to the client would make every
        # capped list one longer than it asked for.
        payload = self._bootstrap(limit=2)
        self.assertEqual(len(payload["payload"]["movies"]), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
