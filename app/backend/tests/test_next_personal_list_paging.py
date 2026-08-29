"""A personal list must not stop at 500 while the counter claims more (#719 follow-up).

A 2,509-movie collection reported seeing only the first 500 entries of its
watchlist and watch history, with the Lists badge still showing the real total.
The ceiling was not in one place but in four, all of which had to agree before
the list could grow:

    next_app.personal_list_movie_entities    an internal clamp, min(..., 500)
    next_app.personal_list_episode_entities  the same clamp on the other half
    the /api/next/lists* routes              parse_int_arg(..., maximum=500)
    the Lists page                           a hardcoded ?limit=500

Raising any three of them changes nothing, which is why the contract tests below
assert on all four rather than on the one that happened to be edited. The
behavioural tests then prove the lists actually come back whole, because a
constant agreeing with itself is not evidence that a query returns more rows.

The watchlist is bounded by the collection, so the raised ceiling ends the
truncation there outright. Watch history is not -- it grows one row per viewing
-- so this raises the wall rather than removing it; paging that list properly is
follow-up work and cannot be a bigger LIMIT, because the list is merged from two
independently queried halves.
"""

import os
import re
import sys
import unittest
import uuid


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEXT_APP_PATH = os.path.join(BACKEND_DIR, "next_app.py")
NEXT_VIEWS_UI_PATH = os.path.join(BACKEND_DIR, "next_views_ui.py")

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ModuleNotFoundError:  # pragma: no cover - minimal environments
    psycopg = None
    dict_row = None
    Jsonb = None

try:
    from app.backend import next_app
except ModuleNotFoundError as exc:  # pragma: no cover - minimal environments
    if exc.name not in {"flask", "psycopg"}:
        raise
    next_app = None

DATABASE_URL = os.environ.get("DATABASE_URL")


class PersonalListCeilingContractTests(unittest.TestCase):
    """All four layers name the same ceiling, so none can silently cap the rest."""

    @classmethod
    def setUpClass(cls):
        with open(NEXT_APP_PATH, encoding="utf-8") as handle:
            cls.app_source = handle.read()
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.ui_source = handle.read()

    def _route_body(self, name):
        start = self.app_source.index(f"def {name}(")
        return self.app_source[start:start + 400]

    def test_the_ceiling_is_declared_once(self):
        self.assertIn("PERSONAL_LIST_MAX_PAGE_SIZE = 5000", self.app_source)

    def test_both_entity_builders_clamp_to_the_shared_ceiling(self):
        # Two halves, one list: an episode-only clamp would cut the merged
        # result just as effectively as a movie-only one.
        for builder in ("personal_list_movie_entities", "personal_list_episode_entities"):
            with self.subTest(builder=builder):
                start = self.app_source.index(f"def {builder}(")
                body = self.app_source[start:start + 1200]
                self.assertIn("PERSONAL_LIST_MAX_PAGE_SIZE)", body)
                self.assertNotIn("), 500)", body)

    def test_every_personal_list_route_uses_the_ceiling(self):
        # The public API pair serves the same two lists to the MCP tools, so a
        # 500 left there truncates the same data through a different door.
        for route in (
            "personal_lists",
            "personal_watchlist",
            "personal_watched_list",
            "public_api_watchlist",
            "public_api_watched",
        ):
            with self.subTest(route=route):
                body = self._route_body(route)
                self.assertIn("maximum=PERSONAL_LIST_MAX_PAGE_SIZE", body)
                self.assertNotIn("maximum=500", body)

    def test_the_lists_page_asks_for_the_whole_list(self):
        self.assertIn("const LISTS_MAX_PAGE_SIZE = 5000;", self.ui_source)
        self.assertIn("/api/next/lists?limit=${LISTS_MAX_PAGE_SIZE}", self.ui_source)
        self.assertNotIn("/api/next/lists?limit=500", self.ui_source)

    def test_the_two_constants_do_not_drift(self):
        # They live in different languages and different files; nothing but this
        # test stops one from being raised without the other.
        backend = re.search(r"PERSONAL_LIST_MAX_PAGE_SIZE = (\d+)", self.app_source)
        frontend = re.search(r"const LISTS_MAX_PAGE_SIZE = (\d+);", self.ui_source)
        self.assertIsNotNone(backend)
        self.assertIsNotNone(frontend)
        self.assertEqual(backend.group(1), frontend.group(1))


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and next_app is not None,
    "PostgreSQL test database is not configured",
)
class PersonalListCeilingBehaviourTests(unittest.TestCase):
    """Above the old cap the lists really do come back whole."""

    ROWS = 620  # comfortably past 500, cheap enough to seed per class

    @classmethod
    def connect(cls):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)

    @classmethod
    def setUpClass(cls):
        cls.tag = f"listcap-{uuid.uuid4()}"
        cls.user_id = uuid.uuid4()
        with cls.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, username, display_name, status) VALUES (%s,%s,'Cap','active')",
                (cls.user_id, cls.tag),
            )
            cur.execute(
                """
                INSERT INTO movies (id, public_id, title, sort_title, year, format)
                SELECT gen_random_uuid(), gen_random_uuid()::text, %s || ' ' || i, %s || ' ' || i,
                       (1970 + (i %% 50))::text, 'Blu-ray'
                FROM generate_series(1, %s) AS i
                """,
                (cls.tag, cls.tag, cls.ROWS),
            )
            cur.execute("SELECT id FROM movies WHERE title LIKE %s", (f"{cls.tag} %",))
            cls.movie_ids = [row["id"] for row in cur.fetchall()]
            for offset, movie_id in enumerate(cls.movie_ids):
                cur.execute(
                    "INSERT INTO watchlist_items (user_id, movie_id, added_at, snapshot)"
                    " VALUES (%s,%s, now() - (%s || ' minutes')::interval, %s)",
                    (cls.user_id, movie_id, offset, Jsonb({})),
                )
                cur.execute(
                    "INSERT INTO watch_history (user_id, movie_id, watched_at, snapshot)"
                    " VALUES (%s,%s, now() - (%s || ' minutes')::interval, %s)",
                    (cls.user_id, movie_id, offset, Jsonb({})),
                )

    @classmethod
    def tearDownClass(cls):
        with cls.connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM watchlist_items WHERE user_id=%s", (cls.user_id,))
            cur.execute("DELETE FROM watch_history WHERE user_id=%s", (cls.user_id,))
            cur.execute("DELETE FROM users WHERE id=%s", (cls.user_id,))
            cur.execute("DELETE FROM movies WHERE title LIKE %s", (f"{cls.tag} %",))

    def _entities(self, kind, limit=None):
        limit = next_app.PERSONAL_LIST_MAX_PAGE_SIZE if limit is None else limit
        with self.connect() as conn:
            return next_app.personal_list_movie_entities(
                conn, self.user_id, kind=kind, limit=limit
            )

    def test_the_watchlist_is_no_longer_cut_at_five_hundred(self):
        rows = self._entities("watchlist")
        self.assertEqual(len(rows), self.ROWS)
        self.assertNotEqual(len(rows), 500, "the old ceiling is back")

    def test_the_watch_history_is_no_longer_cut_at_five_hundred(self):
        rows = self._entities("watched")
        self.assertEqual(len(rows), self.ROWS)

    def test_an_explicit_smaller_limit_is_still_honoured(self):
        # Raising the ceiling must not turn `limit` into a suggestion; the API
        # still has callers that ask for a short page on purpose.
        self.assertEqual(len(self._entities("watchlist", limit=25)), 25)

    def test_the_badge_count_matches_what_the_list_returns(self):
        # The reported symptom was the pair disagreeing: 500 shown, the true
        # total counted. Below the ceiling they must be the same number.
        with self.connect() as conn:
            counts = next_app.personal_list_counts(conn, self.user_id)
        self.assertEqual(counts["watchlist"], self.ROWS)
        self.assertEqual(len(self._entities("watchlist")), counts["watchlist"])

    def test_the_merge_of_both_halves_does_not_re_truncate(self):
        # merge_personal_list_entries applies its own [:limit]; handed the
        # ceiling it must pass a 620-row list through intact.
        with self.connect() as conn:
            movies = next_app.personal_list_movie_entities(
                conn, self.user_id, kind="watchlist", limit=next_app.PERSONAL_LIST_MAX_PAGE_SIZE
            )
            episodes = next_app.personal_list_episode_entities(
                conn, self.user_id, kind="watchlist", limit=next_app.PERSONAL_LIST_MAX_PAGE_SIZE
            )
        merged = next_app.merge_personal_list_entries(
            movies, episodes, kind="watchlist", limit=next_app.PERSONAL_LIST_MAX_PAGE_SIZE
        )
        self.assertEqual(len(merged), len(movies) + len(episodes))
        self.assertGreater(len(merged), 500)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
