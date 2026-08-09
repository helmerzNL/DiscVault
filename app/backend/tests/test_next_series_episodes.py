"""Episodes, and the two things the schema is there to make impossible.

Seasons answered "which seasons are in this box". Episodes answer what a season
cannot: a set that carries 1-12 of a 13-episode season, a pilot on a sampler, a
special that shipped under the wrong season. That gap is the reason to model
them at all, so the assertions here are mostly about the gap being visible
rather than about episodes rendering.

Two constraints carry the design and both are schema-level rather than code
rules, because code rules drift and a foreign key does not:

* an episode cannot be attached to a disc "under" a season it does not belong to;
* a disc may carry *some* of a season's episodes, so season membership does not
  imply episode membership.
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
from app.backend.next_plugins.tmdb import plugin as tmdb


DATABASE_URL = os.environ.get("DATABASE_URL")
PREFIX = "series-episodes-test"


class SeasonEpisodesSourceTests(unittest.TestCase):
    """The plugin side. No network, no database."""

    def test_it_asks_the_season_endpoint_the_series_refresh_refuses_to_use(self):
        """`_normalize_series` argues against this call because it costs one
        request per season. The argument still holds -- what changed is that this
        one is made per season, on demand, from a surface behind Collectors mode,
        so the cost falls on whoever asked for episode detail."""
        calls = []

        def fake_request(context, path, **params):
            calls.append(path)
            return {"episodes": []}

        original = tmdb._request
        tmdb._request = fake_request
        try:
            tmdb.season_episodes({"tmdbTvId": "1399", "seasonNumber": 2}, {})
        finally:
            tmdb._request = original

        self.assertEqual(calls, ["/tv/1399/season/2"])

    def test_episodes_are_shaped_and_season_zero_is_allowed(self):
        def fake_request(context, path, **params):
            return {
                "episodes": [
                    {"episode_number": 0, "name": "Recap", "air_date": "2011-04-10"},
                    {"episode_number": 1, "name": "Pilot", "overview": "x", "runtime": 62},
                    {"episode_number": "two", "name": "dropped"},
                ]
            }

        original = tmdb._request
        tmdb._request = fake_request
        try:
            result = tmdb.season_episodes({"tmdbTvId": "1399", "seasonNumber": 1}, {})
        finally:
            tmdb._request = original

        self.assertEqual([e["episodeNumber"] for e in result["episodes"]], [0, 1])
        self.assertEqual(result["episodes"][1]["runtimeMinutes"], 62)

    def test_a_missing_id_or_season_is_a_miss_rather_than_a_request(self):
        for payload in (
            {},
            {"tmdbTvId": "1399"},
            {"seasonNumber": 1},
            {"tmdbTvId": "abc", "seasonNumber": 1},
            {"tmdbTvId": "1399", "seasonNumber": -1},
            {"tmdbTvId": "1399", "seasonNumber": True},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(
                    tmdb.season_episodes(payload, {}),
                    {"status": "miss", "provider": "tmdb"},
                )


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class EpisodeSchemaTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM watch_history WHERE episode_id IN (SELECT id FROM series_episodes WHERE public_id LIKE %s)",
                    (f"{PREFIX}-%",),
                )
                cur.execute(
                    "DELETE FROM movie_episodes WHERE movie_id IN (SELECT id FROM movies WHERE public_id LIKE %s)",
                    (f"{PREFIX}-%",),
                )
                cur.execute("DELETE FROM movies WHERE public_id LIKE %s", (f"{PREFIX}-%",))
                cur.execute("DELETE FROM series_episodes WHERE public_id LIKE %s", (f"{PREFIX}-%",))
                cur.execute("DELETE FROM series_seasons WHERE public_id LIKE %s", (f"{PREFIX}-%",))
                cur.execute("DELETE FROM series WHERE public_id LIKE %s", (f"{PREFIX}-%",))
                cur.execute("DELETE FROM users WHERE username LIKE %s", (f"{PREFIX}-%",))
            conn.commit()

    def _fixture(self, conn):
        ids = {
            "series": uuid.uuid4(),
            "season1": uuid.uuid4(),
            "season2": uuid.uuid4(),
            "episode": uuid.uuid4(),
            "movie": uuid.uuid4(),
        }
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO series (id, public_id, title) VALUES (%s,%s,'Show')",
                (ids["series"], f"{PREFIX}-{ids['series']}"),
            )
            for key, number in (("season1", 1), ("season2", 2)):
                cur.execute(
                    "INSERT INTO series_seasons (id, public_id, series_id, season_number) VALUES (%s,%s,%s,%s)",
                    (ids[key], f"{PREFIX}-{ids[key]}", ids["series"], number),
                )
            cur.execute(
                """
                INSERT INTO series_episodes (id, public_id, series_id, season_id, episode_number, title)
                VALUES (%s,%s,%s,%s,3,'Third')
                """,
                (ids["episode"], f"{PREFIX}-{ids['episode']}", ids["series"], ids["season1"]),
            )
            cur.execute(
                """
                INSERT INTO movies (id, public_id, title, sort_title, media_type, series_id)
                VALUES (%s,%s,'Box','Box','SHOW',%s)
                """,
                (ids["movie"], f"{PREFIX}-{ids['movie']}", ids["series"]),
            )
        conn.commit()
        return ids

    def test_an_episode_cannot_be_filed_under_the_wrong_season(self):
        """The composite foreign key, doing the work a code check would drift
        away from. Episode 3 belongs to season 1; claiming it under season 2 has
        to be refused by the database itself."""
        with self.connect() as conn:
            ids = self._fixture(conn)
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO movie_episodes (movie_id, episode_id, season_id, series_id)
                        VALUES (%s,%s,%s,%s)
                        """,
                        (ids["movie"], ids["episode"], ids["season2"], ids["series"]),
                    )
            conn.rollback()

    def test_a_disc_may_carry_some_of_a_season_without_carrying_all_of_it(self):
        """The whole reason episodes exist. A season link says the box covers the
        season; the episode rows say which parts of it are actually on there, and
        a set that stops short must be representable."""
        with self.connect() as conn:
            ids = self._fixture(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO movie_seasons (movie_id, season_id, series_id) VALUES (%s,%s,%s)",
                    (ids["movie"], ids["season1"], ids["series"]),
                )
                cur.execute(
                    "INSERT INTO movie_episodes (movie_id, episode_id, season_id, series_id) VALUES (%s,%s,%s,%s)",
                    (ids["movie"], ids["episode"], ids["season1"], ids["series"]),
                )
                # A second episode of the same season that the box does not carry.
                other = uuid.uuid4()
                cur.execute(
                    """
                    INSERT INTO series_episodes (id, public_id, series_id, season_id, episode_number, title)
                    VALUES (%s,%s,%s,%s,4,'Fourth')
                    """,
                    (other, f"{PREFIX}-{other}", ids["series"], ids["season1"]),
                )
            conn.commit()
            episodes = next_app.season_episode_entities(conn, ids["season1"])

        by_number = {row["episodeNumber"]: row for row in episodes}
        self.assertTrue(by_number[3]["onDisc"])
        self.assertFalse(by_number[4]["onDisc"], "a season link must not imply every episode")

    def test_watched_state_is_per_user_and_not_per_instance(self):
        """A shared shelf must not show one person's viewing to everyone."""
        with self.connect() as conn:
            ids = self._fixture(conn)
            mine = uuid.uuid4()
            theirs = uuid.uuid4()
            with conn.cursor() as cur:
                for user_id in (mine, theirs):
                    cur.execute(
                        "INSERT INTO users (id, username, display_name, status) VALUES (%s,%s,'U','active')",
                        (user_id, f"{PREFIX}-{user_id.hex[:8]}"),
                    )
                cur.execute(
                    "INSERT INTO watch_history (user_id, episode_id, watched_at) VALUES (%s,%s, now())",
                    (theirs, ids["episode"]),
                )
            conn.commit()

            mine_rows = next_app.season_episode_entities(conn, ids["season1"], actor={"id": mine})
            their_rows = next_app.season_episode_entities(conn, ids["season1"], actor={"id": theirs})

        self.assertIsNone(mine_rows[0]["watchedAt"])
        self.assertIsNotNone(their_rows[0]["watchedAt"])

    def test_an_episode_watch_names_no_disc(self):
        """Several discs can carry one episode. Picking one to record would be
        inventing a fact nobody stated."""
        client = next_app.app.test_client()
        with self.connect() as conn:
            ids = self._fixture(conn)

        response = client.post(f"/api/next/series/episodes/{ids['episode']}/watched", json={})
        self.assertEqual(response.status_code, 200, response.data[:200])

        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT movie_id FROM watch_history WHERE episode_id = %s", (ids["episode"],)
                )
                rows = cur.fetchall()
        self.assertEqual([row["movie_id"] for row in rows], [None])

        # And clearing removes every entry, not just the newest -- otherwise the
        # button flips back the moment the list is re-read.
        client.post(f"/api/next/series/episodes/{ids['episode']}/watched", json={})
        self.assertEqual(
            client.delete(f"/api/next/series/episodes/{ids['episode']}/watched").status_code, 200
        )
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS n FROM watch_history WHERE episode_id = %s", (ids["episode"],)
                )
                self.assertEqual(cur.fetchone()["n"], 0)

    def test_deleting_a_series_takes_its_episodes_with_it(self):
        with self.connect() as conn:
            ids = self._fixture(conn)
            with conn.cursor() as cur:
                cur.execute("DELETE FROM movies WHERE id = %s", (ids["movie"],))
                cur.execute("DELETE FROM series WHERE id = %s", (ids["series"],))
            conn.commit()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS n FROM series_episodes WHERE id = %s", (ids["episode"],)
                )
                self.assertEqual(cur.fetchone()["n"], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
