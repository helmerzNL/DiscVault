"""The series detail page, against a real database.

The page is mostly assembled from helpers the container page already uses, so
what these tests pin is the part that is genuinely new: what a series owns
versus what it borrows from its discs, who may see which discs, and that the
artwork plumbing really does write `entity_type='series'` rather than quietly
landing somewhere else.
"""

import io
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch


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

PREFIX = "series-detail-test"


def _png(width=6, height=9):
    try:
        from PIL import Image
    except ModuleNotFoundError:  # pragma: no cover - Pillow ships with the backend
        return None
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (30, 60, 90)).save(buffer, "PNG")
    return buffer.getvalue()


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class SeriesDetailPostgresTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM entity_media WHERE entity_type='series' AND entity_id IN (
                        SELECT id FROM series WHERE public_id LIKE %s
                    )
                    """,
                    (f"{PREFIX}-%",),
                )
                # A season owns a poster too, and `series_seasons` is deleted
                # below -- an orphaned entity_media row would outlive the run and
                # be counted by the next one.
                cur.execute(
                    """
                    DELETE FROM entity_media WHERE entity_type='series_season' AND entity_id IN (
                        SELECT id FROM series_seasons WHERE public_id LIKE %s
                    )
                    """,
                    (f"{PREFIX}-%",),
                )
                cur.execute(
                    """
                    DELETE FROM movie_seasons WHERE movie_id IN (
                        SELECT id FROM movies WHERE public_id LIKE %s
                    )
                    """,
                    (f"{PREFIX}-%",),
                )
                cur.execute("DELETE FROM movies WHERE public_id LIKE %s", (f"{PREFIX}-%",))
                cur.execute("DELETE FROM series_seasons WHERE public_id LIKE %s", (f"{PREFIX}-%",))
                cur.execute("DELETE FROM series_identifiers WHERE series_id IN (SELECT id FROM series WHERE public_id LIKE %s)", (f"{PREFIX}-%",))
                cur.execute("DELETE FROM series WHERE public_id LIKE %s", (f"{PREFIX}-%",))
                cur.execute("DELETE FROM users WHERE username LIKE %s", (f"{PREFIX}-%",))
            conn.commit()

    # --- fixtures -----------------------------------------------------------

    def _series(self, conn, title="Fargo"):
        series_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO series (id, public_id, title, sort_title) VALUES (%s,%s,%s,%s)",
                (series_id, f"{PREFIX}-{series_id}", title, title),
            )
        conn.commit()
        return series_id

    def _season(self, conn, series_id, number):
        season_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO series_seasons (id, public_id, series_id, season_number) VALUES (%s,%s,%s,%s)",
                (season_id, f"{PREFIX}-{season_id}", series_id, number),
            )
        conn.commit()
        return season_id

    def _disc(self, conn, series_id, *, title="A Disc", owner_id=None, metadata="{}"):
        movie_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movies (id, public_id, title, sort_title, media_type, series_id, owner_id, metadata)
                VALUES (%s,%s,%s,%s,'SHOW',%s,%s,%s::jsonb)
                """,
                (movie_id, f"{PREFIX}-{movie_id}", title, title, series_id, owner_id, metadata),
            )
        conn.commit()
        return movie_id

    def _user(self, conn):
        user_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, username, display_name, status) VALUES (%s,%s,'Other','active')",
                (user_id, f"{PREFIX}-{user_id.hex[:8]}"),
            )
        conn.commit()
        return user_id

    # --- what the page is given ---------------------------------------------

    def test_the_detail_carries_the_series_its_discs_and_its_seasons(self):
        with self.connect() as conn:
            series_id = self._series(conn)
            self._season(conn, series_id, 1)
            self._disc(conn, series_id, title="Season One Box")

            detail = next_app.series_detail_entity(conn, series_id)

        self.assertEqual(detail["series"]["title"], "Fargo")
        self.assertEqual(len(detail["series"]["seasons"]), 1)
        self.assertEqual([disc["title"] for disc in detail["discs"]], ["Season One Box"])

    def test_a_missing_series_is_none_rather_than_an_empty_shell(self):
        """The route turns this into a 404. An empty-but-present detail would
        render a page for a series that does not exist."""
        with self.connect() as conn:
            self.assertIsNone(next_app.series_detail_entity(conn, uuid.uuid4()))

    def test_artwork_and_videos_are_borrowed_from_the_discs(self):
        """A series owns neither. Videos are a `movies.metadata` fact and there is
        no series-shaped source for one, so an empty tab would be a dead end
        where the discs already have the answer."""
        metadata = (
            '{"poster_url":"https://example.test/p.jpg",'
            '"videos":[{"url":"https://www.youtube.com/watch?v=x","label":"Trailer","type":"Trailer"}]}'
        )
        with self.connect() as conn:
            series_id = self._series(conn)
            self._disc(conn, series_id, title="Boxed", metadata=metadata)

            detail = next_app.series_detail_entity(conn, series_id)

        self.assertEqual(detail["mediaAssets"], [])
        self.assertEqual([asset["kind"] for asset in detail["aggregateMediaAssets"]], ["poster"])
        # Labelled with the disc it came from, so "the series' poster" cannot
        # quietly come to mean "some disc's poster".
        self.assertEqual(detail["aggregateMediaAssets"][0]["sourceMovieTitle"], "Boxed")
        self.assertEqual(detail["aggregateVideos"][0]["sourceMovieTitle"], "Boxed")

    def test_a_disc_the_actor_may_not_see_is_not_listed(self):
        with self.connect() as conn:
            series_id = self._series(conn)
            other = self._user(conn)
            self._disc(conn, series_id, title="Someone else's", owner_id=other)
            actor = {"id": self._user(conn), "permissions": ["collection.view"]}

            detail = next_app.series_detail_entity(conn, series_id, actor=actor)

        self.assertEqual(detail["discs"], [])
        # And nothing leaks through the aggregation either: the artwork and video
        # tabs are built from the same list.
        self.assertEqual(detail["aggregateMediaAssets"], [])
        self.assertEqual(detail["aggregateVideos"], [])


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class SeriesDetailRouteTests(SeriesDetailPostgresTests):
    def setUp(self):
        self.client = next_app.app.test_client()

    def test_the_detail_route_answers_under_detail_not_series(self):
        """A sibling of `GET /api/next/series/<id>`, which answers under `series`
        and is read by the movie edit form's season picker. Reshaping that one
        would have broken the picker to save a route."""
        with self.connect() as conn:
            series_id = self._series(conn)

        response = self.client.get(f"/api/next/series/{series_id}/detail")
        self.assertEqual(response.status_code, 200)
        self.assertIn("detail", response.get_json())
        self.assertEqual(
            self.client.get(f"/api/next/series/{uuid.uuid4()}/detail").status_code, 404
        )

    def test_series_artwork_is_stored_against_the_series(self):
        png = _png()
        if png is None:  # pragma: no cover - Pillow is a backend dependency
            self.skipTest("Pillow is not installed")
        with self.connect() as conn:
            series_id = self._series(conn)

        # An upload writes a real file under the data directory, which defaults
        # to `/data` and is not writable on a CI runner. Redirected rather than
        # mocked away: the point of the test is that the whole route runs, and a
        # mocked file step would stop covering the part that produces the storage
        # key the media row is built from.
        data_dir = tempfile.mkdtemp(prefix="series-artwork-")
        self.addCleanup(shutil.rmtree, data_dir, True)
        with patch.dict(os.environ, {"DISCVAULT_LEGACY_DATA_DIR": data_dir}):
            upload = self.client.post(
                f"/api/next/series/{series_id}/media/upload",
                data={"kind": "poster", "file": (io.BytesIO(png), "p.png")},
                content_type="multipart/form-data",
            )
        self.assertEqual(upload.status_code, 200, upload.data[:200])
        body = upload.get_json()
        # Keyed by entity, so a caller can tell what it just wrote to.
        self.assertEqual(body["seriesId"], str(series_id))
        media_id = body["media"]["id"]

        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT entity_type FROM entity_media WHERE entity_id=%s", (series_id,)
                )
                # migration 003 leaves `entity_type` unconstrained, which is why
                # this needed no migration -- and why a test has to check it.
                self.assertEqual([row["entity_type"] for row in cur.fetchall()], ["series"])

        self.assertEqual(
            self.client.post(
                f"/api/next/series/{series_id}/media/primary",
                json={"mediaId": media_id, "kind": "poster"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.delete(
                f"/api/next/series/{series_id}/media/{media_id}?kind=poster"
            ).status_code,
            200,
        )
        with self.connect() as conn:
            self.assertEqual(next_app.series_detail_entity(conn, series_id)["mediaAssets"], [])

    def test_fetched_artwork_lands_on_the_series_and_its_seasons(self):
        """The one thing no source reading can prove.

        `entity_media.entity_type` is unconstrained text, so writing 'serie' or
        'season' instead would insert cleanly, return success, and simply never
        be read back by anything.
        """
        from app.backend import next_metadata

        with self.connect() as conn:
            series_id = self._series(conn)
            season_id = self._season(conn, series_id, 1)

            next_metadata.apply_series_artwork(
                conn,
                series_id,
                {
                    "artwork": {
                        "poster": {
                            "sourceUrl": "https://example.test/p1.jpg",
                            "options": ["https://example.test/p1.jpg", "https://example.test/p2.jpg"],
                            "source": "tmdb",
                        }
                    },
                    "seasons": {1: {"posterUrl": "https://example.test/s1.jpg", "posterSource": "tmdb"}},
                },
            )
            conn.commit()

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT entity_type, entity_id, is_primary
                    FROM entity_media
                    WHERE entity_id IN (%s, %s)
                    ORDER BY entity_type, is_primary DESC
                    """,
                    (series_id, season_id),
                )
                rows = cur.fetchall()

        by_type = {}
        for row in rows:
            by_type.setdefault(row["entity_type"], []).append(row)
        self.assertEqual(sorted(by_type), ["series", "series_season"])
        # The runner-up is linked but not primary: it fills the Posters tab with
        # a choice without quietly becoming the choice.
        self.assertEqual([row["is_primary"] for row in by_type["series"]], [True, False])
        self.assertEqual(len(by_type["series_season"]), 1)

        with self.connect() as conn:
            seasons = next_app.series_detail_entity(conn, series_id)["series"]["seasons"]
        self.assertTrue(seasons[0]["posterUrl"], "the season poster must reach the page")

    def test_a_refresh_does_not_replace_a_poster_somebody_chose(self):
        """A series has no lock button, so this refusal is the whole protection.

        It fails silently if it regresses: the fetched poster simply appears and
        the uploaded one is still *present* as an option, so nothing looks broken
        until somebody notices their choice was overruled.
        """
        from app.backend import next_metadata

        png = _png()
        if png is None:  # pragma: no cover - Pillow is a backend dependency
            self.skipTest("Pillow is not installed")
        with self.connect() as conn:
            series_id = self._series(conn)

        data_dir = tempfile.mkdtemp(prefix="series-artwork-lock-")
        self.addCleanup(shutil.rmtree, data_dir, True)
        with patch.dict(os.environ, {"DISCVAULT_LEGACY_DATA_DIR": data_dir}):
            upload = self.client.post(
                f"/api/next/series/{series_id}/media/upload",
                data={"kind": "poster", "file": (io.BytesIO(png), "mine.png")},
                content_type="multipart/form-data",
            )
        self.assertEqual(upload.status_code, 200, upload.data[:200])
        mine = upload.get_json()["media"]["id"]
        self.assertEqual(
            self.client.post(
                f"/api/next/series/{series_id}/media/primary",
                json={"mediaId": mine, "kind": "poster"},
            ).status_code,
            200,
        )

        with self.connect() as conn:
            applied = next_metadata.apply_series_artwork(
                conn,
                series_id,
                {
                    "artwork": {
                        "poster": {
                            "sourceUrl": "https://example.test/theirs.jpg",
                            "options": [],
                            "source": "tmdb",
                        }
                    },
                    "seasons": {},
                },
            )
            conn.commit()

        self.assertTrue(applied["series"]["poster"]["primary"]["lockedPrimary"])
        with self.connect() as conn:
            detail = next_app.series_detail_entity(conn, series_id)
        primary = [a for a in detail["mediaAssets"] if a["kind"] == "poster" and a["is_primary"]]
        self.assertEqual([str(asset["id"]) for asset in primary], [str(mine)])

    def test_the_refresh_route_runs_the_multi_source_fill(self):
        """A miss is a 200 with a status, not an error: nothing was damaged and
        the existing text stands."""
        with self.connect() as conn:
            series_id = self._series(conn)

        response = self.client.post(f"/api/next/series/{series_id}/metadata/refresh")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertIn(body["result"]["status"], {"ok", "miss", "skipped", "unavailable"})
        # The refreshed detail comes back with it, so the page can repaint without
        # a second round trip.
        self.assertIn("detail", body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
