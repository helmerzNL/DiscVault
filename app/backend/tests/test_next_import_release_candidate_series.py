"""A series pressing picked from MovieVault's candidate list is added as a series.

When MovieVault can pin a barcode to the film but not to the pressing, the Add
flow shows "Which pressing is this?" and the person picks the edition in their
hand. The film behind that list states its work type and, for a series, its TMDB
television id; a catalogued pressing may name its seasons. None of it reached
the disc: the pick carried the edition's own facts only, so a series added this
way became a MOVIE with no series (helmerzNL/DiscVault#826).

The unit half pins `release_candidate_identity`; the PostgreSQL half drives the
real `POST /api/next/import/movie` and reads back what was written.
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


SERIES_FILM = {
    "title": "Band of Brothers",
    "year": 2001,
    "tmdbMovieId": None,
    "tmdbTvId": "4613",
    "imdbId": "tt0185906",
    "workType": "tv",
}
PRESSING = {
    "releaseRef": "10000000-0000-0000-0000-0000000000aa",
    "source": "canonical",
    "title": "Band of Brothers",
    "format": "Blu-ray",
    "seasons": [{"seasonNumber": 1, "title": None, "releaseYear": 2001, "episodeCount": 10}],
}


@unittest.skipUnless(next_app is not None, "Flask app dependencies are not installed")
class ReleaseCandidateIdentityTests(unittest.TestCase):
    def test_a_series_pressing_states_show_and_its_series(self):
        media_type, series = next_app.release_candidate_identity(SERIES_FILM, PRESSING)
        self.assertEqual(media_type, "SHOW")
        self.assertEqual(series["provider"], "movievault_v2")
        self.assertEqual(series["tmdbTvId"], "4613")
        self.assertEqual(series["title"], "Band of Brothers")
        # The plugin's season shape, which `provider_series_payload` reads.
        self.assertEqual(series["seasons"], [{"seasonNumber": 1, "year": 2001, "episodeCount": 10}])

    def test_a_film_pressing_states_movie_and_no_series(self):
        film = {"title": "Annihilation", "tmdbMovieId": "300668", "workType": "movie"}
        self.assertEqual(next_app.release_candidate_identity(film, PRESSING), ("MOVIE", None))

    def test_an_unstated_type_states_nothing(self):
        film = {key: value for key, value in SERIES_FILM.items() if key != "workType"}
        self.assertEqual(next_app.release_candidate_identity(film, PRESSING), (None, None))

    def test_a_series_without_a_television_id_is_a_show_with_no_series(self):
        """Nothing to resolve a series by; a title is not identity."""
        film = {**SERIES_FILM, "tmdbTvId": None}
        self.assertEqual(next_app.release_candidate_identity(film, PRESSING), ("SHOW", None))

    def test_a_pressing_without_seasons_states_none(self):
        _type, series = next_app.release_candidate_identity(SERIES_FILM, {"title": "Box"})
        self.assertNotIn("seasons", series)

    def test_the_id_is_also_read_from_nested_identifiers(self):
        film = {"title": "Band of Brothers", "workType": "tv", "identifiers": {"tmdbTvId": "4613"}}
        _type, series = next_app.release_candidate_identity(film, PRESSING)
        self.assertEqual(series["tmdbTvId"], "4613")


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and next_app is not None,
    "PostgreSQL test database is not configured",
)
class PickedSeriesPressingImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = next_app.create_app()

    def setUp(self):
        self.title = f"Release Candidate Series Probe {uuid.uuid4()}"
        self.client = self.app.test_client()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, series_id FROM movies WHERE title LIKE 'Release Candidate Series Probe %'")
                rows = cur.fetchall()
                movie_ids = [str(row["id"]) for row in rows]
                series_ids = [row["series_id"] for row in rows if row["series_id"]]
                if movie_ids:
                    cur.execute(
                        """
                        DELETE FROM background_jobs
                        WHERE EXISTS (
                            SELECT 1 FROM unnest(%s::text[]) AS probe(id)
                            WHERE payload::text LIKE '%%' || probe.id || '%%'
                        )
                        """,
                        (movie_ids,),
                    )
                if series_ids:
                    cur.execute(
                        "DELETE FROM background_jobs WHERE payload ->> 'seriesId' = ANY(%s)",
                        ([str(item) for item in series_ids],),
                    )
                cur.execute("DELETE FROM movie_seasons WHERE movie_id::text = ANY(%s)", (movie_ids,))
                cur.execute("DELETE FROM movies WHERE title LIKE 'Release Candidate Series Probe %'")
                if series_ids:
                    cur.execute("DELETE FROM series WHERE id = ANY(%s)", (series_ids,))
            conn.commit()

    def _import(self, film):
        def lookup(_conn, _payload, _actor, *args, **kwargs):
            # The resolver's candidate list is a miss for the merge: nothing in
            # the proposal says what the work is.
            return {"proposal": {"movieUpdates": {"title": self.title}}, "sources": []}

        with mock.patch.object(next_app, "lookup_metadata_sources", lookup):
            return self.client.post(
                "/api/next/import/movie",
                json={
                    "title": self.title,
                    "importMode": "movie",
                    "releaseCandidate": PRESSING,
                    "releaseFallbackFilm": {**film, "title": self.title},
                },
            )

    def _stored(self, movie_id):
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT media_type, series_id FROM movies WHERE id = %s", (movie_id,))
                movie = cur.fetchone()
                cur.execute(
                    """
                    SELECT ss.season_number
                    FROM movie_seasons ms JOIN series_seasons ss ON ss.id = ms.season_id
                    WHERE ms.movie_id = %s
                    """,
                    (movie_id,),
                )
                seasons = [row["season_number"] for row in cur.fetchall()]
        return movie, seasons

    def test_a_picked_series_pressing_is_a_show_in_its_series_with_its_season(self):
        response = self._import(SERIES_FILM)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        movie_id = response.get_json()["movie"]["id"]
        movie, seasons = self._stored(movie_id)
        self.assertEqual(movie["media_type"], "SHOW")
        self.assertIsNotNone(movie["series_id"])
        self.assertEqual(seasons, [1])

    def test_a_picked_film_pressing_stays_a_film(self):
        film = {"title": "x", "tmdbMovieId": "300668", "workType": "movie"}
        response = self._import(film)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        movie, seasons = self._stored(response.get_json()["movie"]["id"])
        self.assertEqual(movie["media_type"], "MOVIE")
        self.assertIsNone(movie["series_id"])
        self.assertEqual(seasons, [])


if __name__ == "__main__":
    unittest.main()
