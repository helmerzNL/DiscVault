"""What the statistics page counts as *one thing*, and what the media filter cuts.

A shelf holds releases, not works. Two editions of the same film are two
`movies` rows, and a season box set is one row per disc. Counting rows makes a
director score twice for owning a film twice, and a series' cast score once per
season - which describes how the collection was bought rather than what is in
it.

`STATS_WORK_KEY_SQL` collapses those rows onto the identity ladder the rest of
the server already uses, one rung at a time:

    1. every disc of one series is that series      -> 'series:<uuid>'
    2. editions of one film share a TMDB movie_id   -> 'tmdb:<id>'
    3. anything else stands for itself              -> 'row:<uuid>'

The direction of the risk matters and is asserted below. Under-merging is safe
and is what happens without metadata: an unresolved film keeps its own key and
counts as it always did. Over-merging would need two different works to carry
the same TMDB id, and the tests pin that neither a shared title, a shared year,
nor a shared director is enough to merge anything.

`STATS_MEDIA_TYPES` is the other half: the same charts narrowed to films or to
TV, using the `movies.media_type` column migration 063 added.

Both are database-shaped claims - what a GROUP BY counts and what a WHERE cuts
- so they run against a real PostgreSQL against the endpoint's own SQL,
imported rather than copied so it cannot drift from what ships.
"""

import os
import sys
import unittest
import uuid


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_root not in sys.path:
    sys.path.insert(0, backend_root)

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:  # pragma: no cover - minimal environments
    psycopg = None
    dict_row = None

DATABASE_URL = os.environ.get("DATABASE_URL")


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class StatsWorkDedupTests(unittest.TestCase):
    PREFIX = "work-key-probe-"

    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)

    def setUp(self):
        from next_app import (
            STATS_MEDIA_TYPES,
            STATS_TOP_CREDIT_FILTERS,
            STATS_WORK_KEY_JOIN,
            STATS_WORK_KEY_SQL,
        )

        self.work_key = STATS_WORK_KEY_SQL
        self.work_join = STATS_WORK_KEY_JOIN
        self.credit_filters = STATS_TOP_CREDIT_FILTERS
        self.media_types = STATS_MEDIA_TYPES
        self.tag = f"{self.PREFIX}{uuid.uuid4()}"
        self.addCleanup(self._cleanup)
        self.director = self._person("Director")

    def _cleanup(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM movies WHERE public_id LIKE %s", (f"{self.PREFIX}%",))
            cur.execute("DELETE FROM people WHERE name LIKE %s", (f"{self.PREFIX}%",))
            cur.execute("DELETE FROM series WHERE public_id LIKE %s", (f"{self.PREFIX}%",))

    def _person(self, name):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO people (id, public_id, name)
                VALUES (gen_random_uuid(), %s, %s)
                RETURNING id
                """,
                (f"{self.tag}-{uuid.uuid4()}", f"{self.PREFIX}{name}"),
            )
            return cur.fetchone()["id"]

    def _series(self, title="Show"):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO series (id, public_id, title)
                VALUES (gen_random_uuid(), %s, %s)
                RETURNING id
                """,
                (f"{self.PREFIX}{uuid.uuid4()}", f"{self.PREFIX}{title}"),
            )
            return cur.fetchone()["id"]

    def _release(self, title, *, media="MOVIE", tmdb=None, series_id=None,
                 fmt="BLURAY", year="2010", director=True):
        """One disc on the shelf."""
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movies (id, public_id, title, media_type, series_id, format, year)
                VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (f"{self.PREFIX}{uuid.uuid4()}", title, media, series_id, fmt, year),
            )
            movie_id = cur.fetchone()["id"]
            if tmdb:
                cur.execute(
                    """
                    INSERT INTO movie_identifiers (movie_id, provider_id, identifier_type, identifier)
                    VALUES (%s, 'tmdb', 'movie_id', %s)
                    """,
                    (movie_id, tmdb),
                )
            if director:
                cur.execute(
                    """
                    INSERT INTO movie_credits (id, movie_id, person_id, credit_type, job, sort_order)
                    VALUES (gen_random_uuid(), %s, %s, 'crew', 'Director', 0)
                    """,
                    (movie_id, self.director),
                )
            return movie_id

    def _counts(self, *, media="all"):
        """Rows and works the director chart would report, for one media slice."""
        where = "m.deleted_at IS NULL AND m.public_id LIKE %s"
        params = [f"{self.PREFIX}%"]
        stored = self.media_types[media]
        if stored is not None:
            where += " AND m.media_type = %s"
            params.append(stored)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT COUNT(DISTINCT m.id)::int AS rows,
                       COUNT(DISTINCT {self.work_key})::int AS works
                FROM movies m
                JOIN movie_credits mc ON mc.movie_id = m.id
                JOIN people p ON p.id = mc.person_id
                {self.work_join}
                WHERE {where} AND {self.credit_filters['director']}
                """,
                params,
            )
            row = cur.fetchone()
            return row["rows"], row["works"]

    # -- the two cases the user named --------------------------------------

    def test_two_editions_of_one_film_count_once(self):
        self._release("Inception", tmdb="27205", fmt="BLURAY")
        self._release("Inception", tmdb="27205", fmt="4K_UHD")
        rows, works = self._counts()
        self.assertEqual(rows, 2, "two discs are on the shelf")
        self.assertEqual(works, 1, "but the director directed one film")

    def test_a_season_box_set_counts_once(self):
        series_id = self._series()
        for season in (1, 2, 3):
            self._release(f"Show S{season}", media="SHOW", series_id=series_id)
        rows, works = self._counts()
        self.assertEqual(rows, 3)
        self.assertEqual(works, 1, "a box set is one series, not three")

    # -- the direction of the risk ------------------------------------------

    def test_an_unresolved_film_keeps_its_own_key(self):
        # No TMDB id: the safe direction is to under-merge, which is exactly
        # the behaviour that shipped before the key existed.
        self._release("Unknown A", tmdb=None)
        self._release("Unknown B", tmdb=None)
        self.assertEqual(self._counts(), (2, 2))

    def test_a_shared_title_and_year_do_not_merge_two_works(self):
        self._release("Same Title", tmdb="111", year="1999")
        self._release("Same Title", tmdb="222", year="1999")
        self.assertEqual(self._counts(), (2, 2))

    def test_two_different_films_by_one_director_stay_two(self):
        self._release("First", tmdb="500")
        self._release("Second", tmdb="501")
        self.assertEqual(self._counts(), (2, 2))

    def test_a_blank_identifier_does_not_merge_everything(self):
        # An empty string in the column must not become a shared key.
        self._release("Blank A", tmdb="   ")
        self._release("Blank B", tmdb="   ")
        self.assertEqual(self._counts(), (2, 2))

    def test_two_series_stay_two_works(self):
        for _ in range(2):
            series_id = self._series()
            for season in (1, 2):
                self._release("Show", media="SHOW", series_id=series_id)
        rows, works = self._counts()
        self.assertEqual(rows, 4)
        self.assertEqual(works, 2)

    # -- the media filter ----------------------------------------------------

    def test_the_media_filter_splits_films_from_television(self):
        self._release("Inception", tmdb="27205", fmt="BLURAY")
        self._release("Inception", tmdb="27205", fmt="4K_UHD")
        series_id = self._series()
        for season in (1, 2, 3):
            self._release(f"Show S{season}", media="SHOW", series_id=series_id)

        self.assertEqual(self._counts(media="all"), (5, 2))
        self.assertEqual(self._counts(media="movie"), (2, 1))
        self.assertEqual(self._counts(media="show"), (3, 1))

    def test_the_media_slices_add_up_to_everything(self):
        self._release("A film", tmdb="900")
        series_id = self._series()
        self._release("A show", media="SHOW", series_id=series_id)
        self._release("A show", media="SHOW", series_id=series_id)
        all_rows, all_works = self._counts(media="all")
        movie_rows, movie_works = self._counts(media="movie")
        show_rows, show_works = self._counts(media="show")
        self.assertEqual(movie_rows + show_rows, all_rows)
        # Works add up too here, because no work spans both types. That is a
        # property of the data rather than of the key, so it is asserted on a
        # fixture where it is known to hold rather than assumed in general.
        self.assertEqual(movie_works + show_works, all_works)

    def test_an_unknown_media_type_is_not_silently_everything(self):
        self.assertNotIn("tv", self.media_types)
        self.assertNotIn("", self.media_types)
        self.assertIsNone(self.media_types["all"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
