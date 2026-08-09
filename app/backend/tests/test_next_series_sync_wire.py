"""Series and seasons on the sync wire (layer 4).

Until now a series existed only in the dashboard snapshot, which is one Library
render on one client. That was a deliberate staging decision -- the snapshot let
the grouping ship without touching a contract every client reads -- but it means
the answer this whole line exists to give, *which seasons are on this box*, never
left the browser.

Two failure modes shape what is asserted here, and both have already happened
once in this file's neighbourhood:

**A bootstrap-only entity.** `release_title` and `location_id` were each added to
one payload builder and not the other. The delta is the path anyone testing by
hand exercises, so the gap only surfaces on a fresh install -- or, for a
delta-only field, never surfaces for anyone who reinstalls.

**A membership array beside the entity.** Season membership is a property of the
disc, so it rides on the movie entity rather than in an array of its own. An
array would be bootstrap-only by construction, since a delta carries one entity
at a time.
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


DATABASE_URL = os.environ.get("DATABASE_URL")
PREFIX = "series-sync-test"


class SeriesWireShapeTests(unittest.TestCase):
    """No database. The shape decisions, asserted where they are made."""

    def test_the_disc_publishes_which_series_it_belongs_to(self):
        self.assertIn("series_id", next_app._MOVIE_SYNC_COLUMNS)

    def test_series_id_is_published_but_not_accepted_on_push(self):
        """A client may see the grouping; it may not reassign a disc over the
        wire. Doing so means naming a series that must already exist on the
        server, and §7b keeps establishing a series' identity a deliberate act.

        The parity guard permits this direction. What it forbids is the reverse --
        a field taken on push and never sent back, which is write-only and drifts
        with no symptom.
        """
        self.assertNotIn("series_id", next_app.movie_payload_fields({}))

    def test_season_membership_rides_on_the_disc_rather_than_beside_it(self):
        """A separate array would be bootstrap-only by construction: a delta
        carries one entity at a time, so nothing would ever refresh it."""
        self.assertTrue(hasattr(next_app, "attach_movie_seasons"))
        self.assertFalse(hasattr(next_app, "movie_season_sync_entities"))

    def test_a_series_change_has_a_delta_emitter_like_its_siblings(self):
        """Containers and locations both have one. A series without it would
        reach a client only through a bootstrap."""
        for name in ("emit_series_change", "single_series_sync_entity", "series_sync_entities"):
            self.assertTrue(hasattr(next_app, name), name)


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class SeriesWirePostgresTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM movie_seasons WHERE movie_id IN (SELECT id FROM movies WHERE public_id LIKE %s)",
                    (f"{PREFIX}-%",),
                )
                cur.execute("DELETE FROM movies WHERE public_id LIKE %s", (f"{PREFIX}-%",))
                cur.execute("DELETE FROM series_seasons WHERE public_id LIKE %s", (f"{PREFIX}-%",))
                cur.execute("DELETE FROM series WHERE public_id LIKE %s", (f"{PREFIX}-%",))
            conn.commit()

    def _fixture(self, conn):
        series_id = uuid.uuid4()
        season_id = uuid.uuid4()
        movie_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO series (id, public_id, title, sort_title) VALUES (%s,%s,'Fargo','Fargo')",
                (series_id, f"{PREFIX}-{series_id}"),
            )
            cur.execute(
                "INSERT INTO series_seasons (id, public_id, series_id, season_number) VALUES (%s,%s,%s,2)",
                (season_id, f"{PREFIX}-{season_id}", series_id),
            )
            cur.execute(
                """
                INSERT INTO movies (id, public_id, title, sort_title, media_type, series_id)
                VALUES (%s,%s,'Season Two Box','Season Two Box','SHOW',%s)
                """,
                (movie_id, f"{PREFIX}-{movie_id}", series_id),
            )
            cur.execute(
                "INSERT INTO movie_seasons (movie_id, season_id, series_id, sort_order) VALUES (%s,%s,%s,0)",
                (movie_id, season_id, series_id),
            )
        conn.commit()
        return series_id, season_id, movie_id

    def test_the_bootstrap_and_the_delta_agree_about_a_disc_s_seasons(self):
        """The guard for the bug class that motivated the parity file next door.
        A field on one builder and not the other syncs on edit and vanishes on
        reinstall, or the reverse."""
        with self.connect() as conn:
            series_id, season_id, movie_id = self._fixture(conn)
            delta = next_app.movie_entity(conn, movie_id)
            bootstrap = next(
                movie
                for movie in next_app.all_movie_entities(conn, limit=1000)
                if str(movie["id"]) == str(movie_id)
            )

        self.assertEqual(str(delta["series_id"]), str(series_id))
        self.assertEqual(delta["seasons"], bootstrap["seasons"])
        self.assertEqual([entry["seasonId"] for entry in delta["seasons"]], [str(season_id)])
        # The curator's order, not the numeric one.
        self.assertEqual(delta["seasons"][0]["sortOrder"], 0)

    def test_a_disc_with_no_series_carries_an_empty_list_not_a_missing_key(self):
        """`None` and an absent key both force a client to special-case the
        common shape."""
        movie_id = uuid.uuid4()
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO movies (id, public_id, title, sort_title) VALUES (%s,%s,'Plain','Plain')",
                    (movie_id, f"{PREFIX}-{movie_id}"),
                )
            conn.commit()
            entity = next_app.movie_entity(conn, movie_id)
        self.assertEqual(entity["seasons"], [])
        self.assertIsNone(entity["series_id"])

    def test_the_bootstrap_carries_the_series_and_its_seasons(self):
        with self.connect() as conn:
            series_id, season_id, _ = self._fixture(conn)
            series = {row["id"]: row for row in next_app.series_sync_entities(conn)}
            seasons = [
                row
                for row in next_app.series_season_sync_entities(conn)
                if row["seriesId"] == str(series_id)
            ]

        self.assertIn(str(series_id), series)
        self.assertEqual(series[str(series_id)]["title"], "Fargo")
        # Deliberately no artwork on the wire: a series' poster reaches a client
        # through the media path, and a URL here would be a second place to
        # disagree about which image is current.
        self.assertNotIn("posterUrl", series[str(series_id)])
        self.assertEqual([row["seasonNumber"] for row in seasons], [2])

    def test_deleting_a_series_tells_clients_about_the_discs_too(self):
        """Their `series_id` is cleared by the same request. A client holding the
        old value would keep showing them under a series the server no longer
        has."""
        with self.connect() as conn:
            series_id, _, movie_id = self._fixture(conn)

        client = next_app.app.test_client()
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(max(revision), 0) AS r FROM sync_changes")
                before = cur.fetchone()["r"]

        self.assertEqual(client.delete(f"/api/next/series/{series_id}").status_code, 200)

        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT entity_type, entity_id, operation FROM sync_changes WHERE revision > %s",
                    (before,),
                )
                changes = cur.fetchall()

        kinds = {(row["entity_type"], row["operation"]) for row in changes}
        self.assertIn(("series", "delete"), kinds)
        self.assertIn(str(movie_id), {row["entity_id"] for row in changes})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
