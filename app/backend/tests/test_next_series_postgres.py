"""The series hierarchy against a real database.

Migration 063 pushes three rules into the schema rather than into code — a
series only on a SHOW, a season only from its own series, and zero seasons
meaning "complete series or unspecified". A fake connection enforces none of
them, so every test that matters here needs PostgreSQL.
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
except ModuleNotFoundError:
    psycopg = None
    dict_row = None

from app.backend import next_app
from app.backend.next_common import NextApiError


DATABASE_URL = os.environ.get("DATABASE_URL")

PREFIX = "series-test"


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class SeriesHierarchyPostgresTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM movie_seasons WHERE movie_id IN (
                        SELECT id FROM movies WHERE public_id LIKE %s
                    )
                    """,
                    (f"{PREFIX}-%",),
                )
                cur.execute("DELETE FROM movies WHERE public_id LIKE %s", (f"{PREFIX}-%",))
                cur.execute(
                    "DELETE FROM series_seasons WHERE public_id LIKE %s", (f"{PREFIX}-%",)
                )
                cur.execute("DELETE FROM series WHERE public_id LIKE %s", (f"{PREFIX}-%",))
            conn.commit()

    # --- fixtures -----------------------------------------------------------

    def _series(self, conn, title="Fargo"):
        series_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO series (id, public_id, title, sort_title)
                VALUES (%s, %s, %s, %s)
                """,
                (series_id, f"{PREFIX}-{series_id}", title, title),
            )
        conn.commit()
        return series_id

    def _season(self, conn, series_id, number):
        season_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO series_seasons (id, public_id, series_id, season_number)
                VALUES (%s, %s, %s, %s)
                """,
                (season_id, f"{PREFIX}-{season_id}", series_id, number),
            )
        conn.commit()
        return season_id

    def _movie(self, conn, *, media_type="SHOW"):
        movie_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movies (id, public_id, title, sort_title, media_type)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (movie_id, f"{PREFIX}-{movie_id}", "A Disc", "A Disc", media_type),
            )
        conn.commit()
        return movie_id

    def _assign(self, conn, movie_id, assignment, *, media_type="SHOW"):
        with conn.cursor() as cur:
            next_app.apply_movie_series_assignment(
                cur, movie_id, assignment, media_type=media_type
            )
        conn.commit()

    # --- the schema's three rules ------------------------------------------

    def test_a_film_cannot_be_given_a_series(self):
        """The CHECK would raise a 500; the caller should get a sentence."""
        with self.connect() as conn:
            series_id = self._series(conn)
            movie_id = self._movie(conn, media_type="MOVIE")
            with self.assertRaises(NextApiError) as caught:
                self._assign(
                    conn,
                    movie_id,
                    {"series_id": series_id, "season_ids": []},
                    media_type="MOVIE",
                )
        self.assertEqual(caught.exception.status_code, 400)

    def test_a_season_of_another_series_is_refused_by_name(self):
        """Named, not skipped -- a stale id must not silently store fewer seasons."""
        with self.connect() as conn:
            fargo = self._series(conn, "Fargo")
            other = self._series(conn, "Yellowstone")
            foreign_season = self._season(conn, other, 1)
            movie_id = self._movie(conn)
            with self.assertRaises(NextApiError) as caught:
                self._assign(
                    conn, movie_id, {"series_id": fargo, "season_ids": [foreign_season]}
                )
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn(str(foreign_season), str(caught.exception))

    def test_zero_seasons_is_a_link_not_an_absence(self):
        """A complete-series box: linked to the series, naming no season."""
        with self.connect() as conn:
            series_id = self._series(conn)
            movie_id = self._movie(conn)
            self._assign(conn, movie_id, {"series_id": series_id, "season_ids": []})
            payload = next_app.movie_series_payload(conn, movie_id)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["seasons"], [])

    def test_an_unlinked_disc_reports_no_series_at_all(self):
        with self.connect() as conn:
            movie_id = self._movie(conn)
            self.assertIsNone(next_app.movie_series_payload(conn, movie_id))

    # --- ordinary use -------------------------------------------------------

    def test_seasons_are_stored_and_read_back_in_season_order(self):
        with self.connect() as conn:
            series_id = self._series(conn)
            two = self._season(conn, series_id, 2)
            one = self._season(conn, series_id, 1)
            movie_id = self._movie(conn)
            self._assign(
                conn, movie_id, {"series_id": series_id, "season_ids": [two, one]}
            )
            payload = next_app.movie_series_payload(conn, movie_id)
        self.assertEqual([s["seasonNumber"] for s in payload["seasons"]], [1, 2])

    def test_reassigning_replaces_the_season_set_rather_than_adding_to_it(self):
        with self.connect() as conn:
            series_id = self._series(conn)
            one = self._season(conn, series_id, 1)
            two = self._season(conn, series_id, 2)
            movie_id = self._movie(conn)
            self._assign(conn, movie_id, {"series_id": series_id, "season_ids": [one, two]})
            self._assign(conn, movie_id, {"series_id": series_id, "season_ids": [two]})
            payload = next_app.movie_series_payload(conn, movie_id)
        self.assertEqual([s["seasonNumber"] for s in payload["seasons"]], [2])

    def test_clearing_the_series_also_clears_the_seasons(self):
        with self.connect() as conn:
            series_id = self._series(conn)
            one = self._season(conn, series_id, 1)
            movie_id = self._movie(conn)
            self._assign(conn, movie_id, {"series_id": series_id, "season_ids": [one]})
            self._assign(conn, movie_id, {"series_id": None, "season_ids": []})
            self.assertIsNone(next_app.movie_series_payload(conn, movie_id))
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS n FROM movie_seasons WHERE movie_id = %s",
                    (movie_id,),
                )
                self.assertEqual(cur.fetchone()["n"], 0)

    def test_seasons_without_a_series_are_refused(self):
        with self.connect() as conn:
            series_id = self._series(conn)
            one = self._season(conn, series_id, 1)
            movie_id = self._movie(conn)
            with self.assertRaises(NextApiError):
                self._assign(conn, movie_id, {"series_id": None, "season_ids": [one]})

    def test_an_absent_assignment_leaves_an_existing_link_alone(self):
        """A client that predates this field sends neither key.

        If absence unlinked, upgrading the server would strip every series the
        moment any older client saved an unrelated field.
        """
        with self.connect() as conn:
            series_id = self._series(conn)
            movie_id = self._movie(conn)
            self._assign(conn, movie_id, {"series_id": series_id, "season_ids": []})
            self._assign(conn, movie_id, None)
            self.assertIsNotNone(next_app.movie_series_payload(conn, movie_id))

    def test_switching_a_linked_disc_back_to_film_sheds_the_link(self):
        """Otherwise the type change itself trips movies_series_requires_show.

        This goes through the real edit payload rather than the helper, because
        the ordering that makes it work lives in write_movie_edit_record.
        """
        with self.connect() as conn:
            series_id = self._series(conn)
            one = self._season(conn, series_id, 1)
            movie_id = self._movie(conn)
            self._assign(conn, movie_id, {"series_id": series_id, "season_ids": [one]})
            payload = next_app.movie_update_payload(
                {"title": "A Disc", "mediaType": "MOVIE"},
                existing={"title": "A Disc", "media_type": "SHOW"},
            )
            with conn.cursor() as cur:
                next_app.write_movie_edit_record(cur, movie_id, payload)
            conn.commit()
            self.assertIsNone(next_app.movie_series_payload(conn, movie_id))
            with conn.cursor() as cur:
                cur.execute("SELECT media_type FROM movies WHERE id = %s", (movie_id,))
                self.assertEqual(cur.fetchone()["media_type"], "MOVIE")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
