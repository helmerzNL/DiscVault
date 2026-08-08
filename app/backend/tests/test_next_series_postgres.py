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
from app.backend import next_metadata
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


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class SeriesMetadataRefreshPostgresTests(unittest.TestCase):
    """Filling a series description from DiscVault's own TMDB plugin.

    The rule under test is *fill what is empty, never overwrite*. It is a
    database claim, not a Python one: the season write is a single conditional
    UPDATE rather than a read-then-write, so that a second worker running the
    same job cannot land between the check and the write.
    """

    TITLE = "Refresh Test Show"

    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)

    def _clear(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM series WHERE title = %s", (self.TITLE,))

    def setUp(self):
        self._clear()

    def tearDown(self):
        self._clear()

    def _series(self, conn, *, identifier="1399", overview=None):
        series_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO series (id, public_id, title, overview) VALUES (%s, %s, %s, %s)",
                (series_id, f"refresh-{series_id.hex[:12]}", self.TITLE, overview),
            )
            if identifier:
                cur.execute(
                    """
                    INSERT INTO series_identifiers
                        (series_id, provider_id, identifier_type, identifier)
                    VALUES (%s, 'movievault_v2', 'tmdb_tv', %s)
                    """,
                    (series_id, identifier),
                )
        return series_id

    def _season(self, conn, series_id, number, *, overview=None):
        season_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO series_seasons
                    (id, public_id, series_id, season_number, overview)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (season_id, f"refresh-s-{season_id.hex[:12]}", series_id, number, overview),
            )
        return season_id

    def _hit(self, **overrides):
        result = {
            "status": "hit",
            "series": {"overview": "Fetched overview."},
            "seasons": [
                {"seasonNumber": 1, "overview": "Fetched season one."},
                {"seasonNumber": 2, "overview": "Fetched season two."},
            ],
        }
        result.update(overrides)
        return {"status": "ok", "result": result}

    def _run(self, conn, series_id, execution, *, plugin_ids=("tmdb",)):
        """Drive the refresh with a stated set of sources.

        The refresh discovers its sources by declared capability rather than by
        name, so a test has to say which sources exist. Patching the discovery
        keeps these cases about the write rules -- never overwrite, empty is
        empty, a failure damages nothing -- rather than about whether this test
        database happens to have a configured TMDB key.
        """
        plugins = [{"id": plugin_id} for plugin_id in plugin_ids]
        with patch.object(next_metadata, "series_detail_source_plugins", return_value=plugins):
            with patch.object(next_metadata, "plugin_config_from_db", return_value={}):
                with patch.object(next_metadata, "run_plugin_entrypoint", return_value=execution):
                    return next_metadata.refresh_series_metadata(conn, series_id)

    def _run_many(self, conn, series_id, executions):
        """One execution per source, in the order the sources are consulted."""
        plugins = [{"id": plugin_id} for plugin_id, _ in executions]
        answers = {plugin_id: execution for plugin_id, execution in executions}
        with patch.object(next_metadata, "series_detail_source_plugins", return_value=plugins):
            with patch.object(next_metadata, "plugin_config_from_db", return_value={}):
                with patch.object(
                    next_metadata,
                    "run_plugin_entrypoint",
                    side_effect=lambda plugin_id, *args, **kwargs: answers[plugin_id],
                ):
                    return next_metadata.refresh_series_metadata(conn, series_id)

    def _overviews(self, conn, series_id):
        with conn.cursor() as cur:
            cur.execute("SELECT overview FROM series WHERE id = %s", (series_id,))
            series_overview = cur.fetchone()["overview"]
            cur.execute(
                "SELECT season_number, overview FROM series_seasons "
                "WHERE series_id = %s ORDER BY season_number",
                (series_id,),
            )
            seasons = {row["season_number"]: row["overview"] for row in cur.fetchall()}
        return series_overview, seasons

    def test_an_empty_series_and_its_seasons_are_filled(self):
        with self.connect() as conn:
            series_id = self._series(conn)
            self._season(conn, series_id, 1)
            self._season(conn, series_id, 2)
            result = self._run(conn, series_id, self._hit())
            series_overview, seasons = self._overviews(conn, series_id)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["seriesUpdated"])
        self.assertEqual(result["seasonsUpdated"], 2)
        self.assertEqual(series_overview, "Fetched overview.")
        self.assertEqual(seasons, {1: "Fetched season one.", 2: "Fetched season two."})

    def test_an_existing_overview_is_never_overwritten(self):
        """A description may have been written by hand through the series API.
        A refresh is not a reason to replace it, and this job is retried on
        failure, so re-running must be safe."""
        with self.connect() as conn:
            series_id = self._series(conn, overview="Written by a person.")
            self._season(conn, series_id, 1, overview="Also written by a person.")
            self._season(conn, series_id, 2)
            result = self._run(conn, series_id, self._hit())
            series_overview, seasons = self._overviews(conn, series_id)

        self.assertFalse(result["seriesUpdated"])
        self.assertEqual(result["seasonsUpdated"], 1)
        self.assertEqual(series_overview, "Written by a person.")
        self.assertEqual(seasons[1], "Also written by a person.")
        self.assertEqual(seasons[2], "Fetched season two.")

    def test_an_empty_string_counts_as_empty_not_as_an_answer(self):
        """063 has no NOT NULL here, so a row can carry `''` as easily as NULL.
        Treating one as written and the other as blank would make the outcome
        depend on which write path created the row."""
        with self.connect() as conn:
            series_id = self._series(conn, overview="")
            self._season(conn, series_id, 1, overview="")
            self._run(conn, series_id, self._hit())
            series_overview, seasons = self._overviews(conn, series_id)

        self.assertEqual(series_overview, "Fetched overview.")
        self.assertEqual(seasons[1], "Fetched season one.")

    def test_a_series_without_an_identifier_is_skipped(self):
        """There is nothing to look it up by. Falling back to the title is the
        mistake the whole identity design refuses to make."""
        with self.connect() as conn:
            series_id = self._series(conn, identifier=None)
            result = next_metadata.refresh_series_metadata(conn, series_id)

        self.assertEqual(result["status"], "skipped")

    def test_a_provider_failure_leaves_the_series_untouched(self):
        """A source being unreachable is not a reason to damage a row, and not a
        reason to fail a job that will be retried."""
        with self.connect() as conn:
            series_id = self._series(conn)
            self._season(conn, series_id, 1)
            result = self._run(conn, series_id, {"status": "error", "error": "boom"})
            series_overview, seasons = self._overviews(conn, series_id)

        self.assertEqual(result["status"], "miss")
        self.assertIsNone(series_overview)
        self.assertIsNone(seasons[1])

    # --- several sources ----------------------------------------------------

    def test_the_first_source_wins_and_the_next_only_fills_gaps(self):
        """Precedence is the user's plugin order, not a rule buried in code.

        A later source completing a season the first knew nothing about is the
        whole reason for consulting more than one; being able to contradict the
        first would make the result depend on which source answered fastest.
        """
        with self.connect() as conn:
            series_id = self._series(conn)
            self._season(conn, series_id, 1)
            self._season(conn, series_id, 2)
            result = self._run_many(
                conn,
                series_id,
                [
                    (
                        "tmdb",
                        {
                            "status": "ok",
                            "result": {
                                "status": "hit",
                                "series": {"overview": "From the first source."},
                                "seasons": [{"seasonNumber": 1, "overview": "First on one."}],
                            },
                        },
                    ),
                    (
                        "tvdb",
                        {
                            "status": "ok",
                            "result": {
                                "status": "hit",
                                "series": {"overview": "From the second source."},
                                "seasons": [
                                    {"seasonNumber": 1, "overview": "Second on one."},
                                    {"seasonNumber": 2, "overview": "Second on two."},
                                ],
                            },
                        },
                    ),
                ],
            )
            series_overview, seasons = self._overviews(conn, series_id)

        self.assertEqual(series_overview, "From the first source.")
        self.assertEqual(seasons[1], "First on one.")
        self.assertEqual(seasons[2], "Second on two.")
        self.assertEqual(result["sources"]["series"], "tmdb")
        self.assertEqual(result["sources"]["seasons"], {"1": "tmdb", "2": "tvdb"})

    def test_a_second_source_answers_when_the_first_misses(self):
        with self.connect() as conn:
            series_id = self._series(conn)
            self._season(conn, series_id, 1)
            result = self._run_many(
                conn,
                series_id,
                [
                    ("tmdb", {"status": "ok", "result": {"status": "miss"}}),
                    (
                        "tvdb",
                        {
                            "status": "ok",
                            "result": {
                                "status": "hit",
                                "series": {"overview": "Only the second knew."},
                                "seasons": [{"seasonNumber": 1, "overview": "Second on one."}],
                            },
                        },
                    ),
                ],
            )
            series_overview, seasons = self._overviews(conn, series_id)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(series_overview, "Only the second knew.")
        self.assertEqual(seasons[1], "Second on one.")
        self.assertEqual(result["sources"]["consulted"], ["tvdb"])

    def test_one_source_raising_does_not_cost_the_others_their_answers(self):
        """A source that throws is a source that is broken, not a job that is."""
        with self.connect() as conn:
            series_id = self._series(conn)
            plugins = [{"id": "tmdb"}, {"id": "tvdb"}]

            def answer(plugin_id, *args, **kwargs):
                if plugin_id == "tmdb":
                    raise RuntimeError("boom")
                return {
                    "status": "ok",
                    "result": {"status": "hit", "series": {"overview": "Survived."}},
                }

            with patch.object(next_metadata, "series_detail_source_plugins", return_value=plugins):
                with patch.object(next_metadata, "plugin_config_from_db", return_value={}):
                    with patch.object(next_metadata, "run_plugin_entrypoint", side_effect=answer):
                        result = next_metadata.refresh_series_metadata(conn, series_id)
            series_overview, _ = self._overviews(conn, series_id)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(series_overview, "Survived.")
        self.assertEqual([entry["pluginId"] for entry in result["errors"]], ["tmdb"])

    def test_with_no_series_source_installed_nothing_is_attempted(self):
        """Skipped rather than a miss: there was no question asked, so reporting
        that nobody answered it would be a different and misleading fact."""
        with self.connect() as conn:
            series_id = self._series(conn)
            with patch.object(next_metadata, "series_detail_source_plugins", return_value=[]):
                result = next_metadata.refresh_series_metadata(conn, series_id)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no series source")

    def test_every_source_is_offered_every_identifier(self):
        """A source is the only thing that knows which namespaces it speaks, so
        filtering on its behalf here would be a second, staler copy of that."""
        captured = {}

        with self.connect() as conn:
            series_id = self._series(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO series_identifiers
                        (series_id, provider_id, identifier_type, identifier)
                    VALUES (%s, 'tvdb', 'tvdb', '121361')
                    """,
                    (series_id,),
                )

            def answer(plugin_id, entrypoint, payload, context):
                captured[plugin_id] = payload
                return {"status": "ok", "result": {"status": "miss"}}

            with patch.object(
                next_metadata, "series_detail_source_plugins", return_value=[{"id": "tvdb"}]
            ):
                with patch.object(next_metadata, "plugin_config_from_db", return_value={}):
                    with patch.object(next_metadata, "run_plugin_entrypoint", side_effect=answer):
                        next_metadata.refresh_series_metadata(conn, series_id)

        payload = captured["tvdb"]
        self.assertEqual(payload["seriesIdentifiers"]["tvdb"], "121361")
        self.assertEqual(payload["seriesIdentifiers"]["tmdb_tv"], "1399")
        # Still outside the map: the shipped TMDB plugin reads exactly this key,
        # and an installation that has not taken the new plugin must keep working.
        self.assertEqual(payload["tmdbTvId"], "1399")

    def test_a_season_the_feed_never_recorded_is_not_created(self):
        """The plugin knows every season TMDB has; the collection knows the ones
        a disc actually covers. This job describes what is there, it does not
        add seasons nobody owns."""
        with self.connect() as conn:
            series_id = self._series(conn)
            self._season(conn, series_id, 1)
            self._run(conn, series_id, self._hit())
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS n FROM series_seasons WHERE series_id = %s",
                    (series_id,),
                )
                self.assertEqual(cur.fetchone()["n"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
