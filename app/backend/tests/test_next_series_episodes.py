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
                    "DELETE FROM watchlist_items WHERE episode_id IN (SELECT id FROM series_episodes WHERE public_id LIKE %s)",
                    (f"{PREFIX}-%",),
                )
                cur.execute(
                    """
                    DELETE FROM entity_media WHERE media_id IN (
                        SELECT id FROM media_assets WHERE source_url LIKE %s
                    )
                    """,
                    (f"https://{PREFIX}%",),
                )
                cur.execute("DELETE FROM media_assets WHERE source_url LIKE %s", (f"https://{PREFIX}%",))
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


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class EpisodePresentationTests(EpisodeSchemaTests):
    """What the episode list owes the person reading it.

    Three questions the earlier shape could not answer: what does this episode
    look like, do I still want to see it, and where in my collection is it.

    These act as a signed-in user, which the watchlist requires and
    `watch_history` does not: `watchlist_items.user_id` is NOT NULL while
    `watch_history.user_id` is nullable, so an anonymous actor can record a
    watch and cannot hold a list.
    """

    def setUp(self):
        self.user_id = uuid.uuid4()
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (id, username, display_name, status) VALUES (%s,%s,'U','active')",
                    (self.user_id, f"{PREFIX}-{self.user_id.hex[:8]}"),
                )
            conn.commit()
        permission = patch(
            "app.backend.next_app.require_next_permission",
            return_value={"id": self.user_id},
        )
        permission.start()
        self.addCleanup(permission.stop)
        self.client = next_app.app.test_client()

    def episodes(self, conn, season_id):
        return next_app.season_episode_entities(conn, season_id, actor={"id": self.user_id})

    def _artwork(self, cur, entity_type, entity_id, url):
        """A remote asset, which is what a season poster actually is today: the
        provider URL is served directly rather than copied into local storage."""
        media_id = uuid.uuid4()
        cur.execute(
            """
            INSERT INTO media_assets (id, kind, variant, storage_backend, storage_key, source_url, sha256)
            VALUES (%s, 'poster', 'original', 'remote', %s, %s, %s)
            """,
            (media_id, f"remote/{media_id}", url, uuid.uuid4().hex),
        )
        cur.execute(
            """
            INSERT INTO entity_media (entity_type, entity_id, media_id, role, is_primary)
            VALUES (%s, %s, %s, 'poster', true)
            """,
            (entity_type, entity_id, media_id),
        )

    def test_an_episode_without_a_still_borrows_the_season_then_the_series(self):
        """The fallback is a chain, not a single alternative. Falling straight
        through to the series poster while a season poster exists would show the
        worse of the two images the reader could have had."""
        with self.connect() as conn:
            ids = self._fixture(conn)
            with conn.cursor() as cur:
                self._artwork(cur, "series", ids["series"], f"https://{PREFIX}-series.jpg")
            conn.commit()
            series_only = self.episodes(conn, ids["season1"])[0]

            with conn.cursor() as cur:
                self._artwork(cur, "series_season", ids["season1"], f"https://{PREFIX}-season.jpg")
            conn.commit()
            with_season = self.episodes(conn, ids["season1"])[0]

            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE series_episodes SET still_url = %s WHERE id = %s",
                    (f"https://{PREFIX}-still.jpg", ids["episode"]),
                )
            conn.commit()
            with_still = self.episodes(conn, ids["season1"])[0]

        self.assertEqual(series_only["posterSource"], "series")
        self.assertIn(f"{PREFIX}-series.jpg", series_only["posterUrl"])
        self.assertEqual(with_season["posterSource"], "season")
        self.assertIn(f"{PREFIX}-season.jpg", with_season["posterUrl"])
        self.assertEqual(with_still["posterSource"], "episode")
        self.assertIn(f"{PREFIX}-still.jpg", with_still["posterUrl"])

    def test_an_episode_with_no_image_anywhere_says_so_rather_than_guessing(self):
        with self.connect() as conn:
            ids = self._fixture(conn)
            episode = self.episodes(conn, ids["season1"])[0]
        self.assertIsNone(episode["posterUrl"])
        self.assertIsNone(episode["posterSource"])

    def test_the_disc_is_the_one_that_named_the_episode_before_the_one_that_covers_it(self):
        """Both are honest answers, but they are not equally precise. A disc that
        listed this episode beats a disc that merely covers its season."""
        with self.connect() as conn:
            ids = self._fixture(conn)
            precise = uuid.uuid4()
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO movie_seasons (movie_id, season_id, series_id) VALUES (%s,%s,%s)",
                    (ids["movie"], ids["season1"], ids["series"]),
                )
            conn.commit()
            season_only = self.episodes(conn, ids["season1"])[0]

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO movies (id, public_id, title, sort_title, media_type, series_id)
                    VALUES (%s,%s,'Single','Single','SHOW',%s)
                    """,
                    (precise, f"{PREFIX}-{precise}", ids["series"]),
                )
                cur.execute(
                    "INSERT INTO movie_episodes (movie_id, episode_id, season_id, series_id) VALUES (%s,%s,%s,%s)",
                    (precise, ids["episode"], ids["season1"], ids["series"]),
                )
            conn.commit()
            with_episode = self.episodes(conn, ids["season1"])[0]

        self.assertEqual(season_only["discId"], str(ids["movie"]))
        self.assertEqual(with_episode["discId"], str(precise))

    def test_an_episode_no_disc_carries_has_nowhere_to_go(self):
        """`None` rather than the series page: an entry that looks clickable and
        lands somewhere unrelated is worse than one that does not."""
        with self.connect() as conn:
            ids = self._fixture(conn)
            episode = self.episodes(conn, ids["season1"])[0]
        self.assertIsNone(episode["discId"])
        self.assertFalse(episode["onDisc"])

    def test_an_episode_can_be_wanted_as_well_as_seen(self):
        """The half that was missing: `watch_history` could record "I saw it"
        while the watchlist had no way to say "I still want to"."""
        with self.connect() as conn:
            ids = self._fixture(conn)

        added = self.client.post(f"/api/next/series/episodes/{ids['episode']}/watchlist")
        self.assertEqual(added.status_code, 200, added.data[:300])
        with self.connect() as conn:
            self.assertTrue(self.episodes(conn, ids["season1"])[0]["onWatchlist"])

        removed = self.client.delete(f"/api/next/series/episodes/{ids['episode']}/watchlist")
        self.assertEqual(removed.status_code, 200, removed.data[:300])
        with self.connect() as conn:
            self.assertFalse(self.episodes(conn, ids["season1"])[0]["onWatchlist"])

    def test_one_persons_watchlist_is_not_anothers(self):
        with self.connect() as conn:
            ids = self._fixture(conn)
            stranger = uuid.uuid4()
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (id, username, display_name, status) VALUES (%s,%s,'U','active')",
                    (stranger, f"{PREFIX}-{stranger.hex[:8]}"),
                )
            conn.commit()
        self.client.post(f"/api/next/series/episodes/{ids['episode']}/watchlist")
        with self.connect() as conn:
            theirs = next_app.season_episode_entities(conn, ids["season1"], actor={"id": stranger})
        self.assertFalse(theirs[0]["onWatchlist"])

    def test_a_watchlisted_episode_keeps_its_name_after_the_episode_is_gone(self):
        """Re-linking a series deletes and recreates the whole episode tree. The
        snapshot is what stops that from turning a list entry into a blank line."""
        with self.connect() as conn:
            ids = self._fixture(conn)
        self.client.post(f"/api/next/series/episodes/{ids['episode']}/watchlist")

        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM series_episodes WHERE id = %s", (ids["episode"],))
            conn.commit()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT episode_id, snapshot FROM watchlist_items WHERE snapshot->>'episode_id' = %s",
                    (str(ids["episode"]),),
                )
                row = cur.fetchone()

        self.assertIsNotNone(row, "the entry must outlive the episode row")
        self.assertIsNone(row["episode_id"], "the reference is cleared, the record is not")
        self.assertEqual(row["snapshot"]["kind"], "episode")
        self.assertEqual(row["snapshot"]["title"], "Third")
        self.assertEqual(row["snapshot"]["episode_number"], 3)
        self.assertEqual(row["snapshot"]["season_number"], 1)

    def test_a_season_is_added_by_its_episodes_and_leaves_existing_ones_alone(self):
        """"Add the whole season" is the per-episode act repeated, so the list
        carries one kind of row. Episodes already on it keep their place."""
        with self.connect() as conn:
            ids = self._fixture(conn)
            other = uuid.uuid4()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO series_episodes (id, public_id, series_id, season_id, episode_number, title)
                    VALUES (%s,%s,%s,%s,4,'Fourth')
                    """,
                    (other, f"{PREFIX}-{other}", ids["series"], ids["season1"]),
                )
            conn.commit()

        self.client.post(f"/api/next/series/episodes/{ids['episode']}/watchlist")
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT added_at FROM watchlist_items WHERE episode_id = %s", (ids["episode"],)
                )
                before = cur.fetchone()["added_at"]

        payload = self.client.post(f"/api/next/series/seasons/{ids['season1']}/watchlist").get_json()
        self.assertEqual(payload["episodesAdded"], 1, "only the episode not already listed")

        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT added_at FROM watchlist_items WHERE episode_id = %s", (ids["episode"],)
                )
                self.assertEqual(cur.fetchone()["added_at"], before, "must not be reshuffled")
            self.assertTrue(all(row["onWatchlist"] for row in self.episodes(conn, ids["season1"])))

        removed = self.client.delete(f"/api/next/series/seasons/{ids['season1']}/watchlist").get_json()
        self.assertEqual(removed["episodesRemoved"], 2)

    def test_marking_a_season_watched_does_not_claim_a_rewatch(self):
        """`watch_history` is append-only and a second row means "seen again".
        A bulk button pressed on a half-seen season is not that claim."""
        with self.connect() as conn:
            ids = self._fixture(conn)
            other = uuid.uuid4()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO series_episodes (id, public_id, series_id, season_id, episode_number, title)
                    VALUES (%s,%s,%s,%s,4,'Fourth')
                    """,
                    (other, f"{PREFIX}-{other}", ids["series"], ids["season1"]),
                )
            conn.commit()

        self.client.post(f"/api/next/series/episodes/{ids['episode']}/watched", json={})
        payload = self.client.post(f"/api/next/series/seasons/{ids['season1']}/watched", json={}).get_json()
        self.assertEqual(payload["episodesMarked"], 1, "only the unseen one")

        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT count(*) AS n FROM watch_history
                    WHERE episode_id IN (SELECT id FROM series_episodes WHERE season_id = %s)
                    """,
                    (ids["season1"],),
                )
                self.assertEqual(cur.fetchone()["n"], 2, "one row each, no duplicate for the seen one")

        cleared = self.client.delete(f"/api/next/series/seasons/{ids['season1']}/watched").get_json()
        self.assertEqual(cleared["entriesCleared"], 2)

    def test_a_season_nobody_has_opened_yet_is_answerable_rather_than_an_error(self):
        """Episodes arrive only when somebody opens the season. Acting on a
        season with none stored is a truthful zero, not a 404."""
        with self.connect() as conn:
            ids = self._fixture(conn)
        empty = self.client.post(f"/api/next/series/seasons/{ids['season2']}/watchlist")
        self.assertEqual(empty.status_code, 200, empty.data[:300])
        self.assertEqual(empty.get_json()["episodesAdded"], 0)

        self.assertEqual(
            self.client.post(f"/api/next/series/seasons/{uuid.uuid4()}/watchlist").status_code, 404
        )

    def test_a_row_cannot_name_a_film_and_an_episode_at_once(self):
        """The check constraint, not a code rule: a row that claims both has no
        meaning, and every reader would have to pick one to believe."""
        with self.connect() as conn:
            ids = self._fixture(conn)
            with self.assertRaises(psycopg.errors.CheckViolation):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO watchlist_items (user_id, movie_id, episode_id) VALUES (%s,%s,%s)",
                        (self.user_id, ids["movie"], ids["episode"]),
                    )
            conn.rollback()


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class EpisodesOnThePersonalListsTests(EpisodePresentationTests):
    """Episodes on the same two lists films use, rather than a third place.

    The point of reusing `watchlist_items` and `watch_history` is that one page
    answers "what do I still want to see" for a collection that is half films
    and half box sets. That only works if the episode rows reach the page, and
    if the film query stops trying to render them.
    """

    def _linked_fixture(self, conn):
        ids = self._fixture(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO movie_episodes (movie_id, episode_id, season_id, series_id) VALUES (%s,%s,%s,%s)",
                (ids["movie"], ids["episode"], ids["season1"], ids["series"]),
            )
        conn.commit()
        return ids

    def test_an_episode_reaches_both_lists_and_points_at_its_disc(self):
        with self.connect() as conn:
            ids = self._linked_fixture(conn)
        self.client.post(f"/api/next/series/episodes/{ids['episode']}/watchlist")
        self.client.post(f"/api/next/series/episodes/{ids['episode']}/watched", json={})

        payload = self.client.get("/api/next/lists?limit=50").get_json()
        for key in ("watchlist", "watched"):
            rows = [row for row in payload[key] if row.get("kind") == "episode"]
            self.assertEqual(len(rows), 1, f"{key} should carry the episode")
            row = rows[0]
            self.assertEqual(row["title"], "Third")
            self.assertEqual(row["series_title"], "Show")
            self.assertEqual(row["season_number"], 1)
            self.assertEqual(row["episode_number"], 3)
            self.assertEqual(
                row["disc_movie_id"], str(ids["movie"]), "the click has to land on the disc"
            )
            self.assertTrue(row["movie_exists"], "which is also what makes it clickable")

    def test_an_episode_is_not_rendered_as_a_film_that_no_longer_exists(self):
        """The failure this replaces: `LEFT JOIN movies` matches nothing for an
        episode row, so it arrived on the page as a titleless entry in the
        deleted-film state -- a confident claim about a film that never was."""
        with self.connect() as conn:
            ids = self._linked_fixture(conn)
        self.client.post(f"/api/next/series/episodes/{ids['episode']}/watchlist")
        self.client.post(f"/api/next/series/episodes/{ids['episode']}/watched", json={})

        with self.connect() as conn:
            for kind in ("watchlist", "watched"):
                rows = next_app.personal_list_movie_entities(conn, self.user_id, kind=kind)
                self.assertEqual(rows, [], f"the film query must not claim the episode ({kind})")

    def test_the_two_lists_are_ordered_together_rather_than_appended(self):
        """A film added after an episode belongs above it. Appending one list to
        the other would put every episode last regardless of when it was added."""
        with self.connect() as conn:
            ids = self._linked_fixture(conn)
            film = uuid.uuid4()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO movies (id, public_id, title, sort_title)
                    VALUES (%s,%s,'A Film','A Film')
                    """,
                    (film, f"{PREFIX}-{film}"),
                )
            conn.commit()

        self.client.post(f"/api/next/series/episodes/{ids['episode']}/watchlist")
        # Written directly: the film routes check collection visibility, which
        # needs a fuller actor than this fixture builds, and the ordering is a
        # property of the merge rather than of how a row got there.
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO watchlist_items (user_id, movie_id, added_at)
                    VALUES (%s, %s, now() + interval '1 minute')
                    """,
                    (self.user_id, film),
                )
            conn.commit()

        rows = self.client.get("/api/next/lists?limit=50").get_json()["watchlist"]
        self.assertEqual(
            [row.get("kind") or "movie" for row in rows[:2]],
            ["movie", "episode"],
            "newest first, whatever kind it is",
        )

    def test_films_and_episodes_are_counted_apart(self):
        """`watchedMovies` says films. Counting episodes into it would have made
        a season of thirteen read as thirteen films -- or, when the episodes are
        untitled, as one."""
        with self.connect() as conn:
            ids = self._linked_fixture(conn)
            film = uuid.uuid4()
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO movies (id, public_id, title, sort_title) VALUES (%s,%s,'A Film','A Film')",
                    (film, f"{PREFIX}-{film}"),
                )
            conn.commit()

        self.client.post(f"/api/next/series/episodes/{ids['episode']}/watched", json={})
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO watch_history (user_id, movie_id, watched_at) VALUES (%s, %s, now())",
                    (self.user_id, film),
                )
            conn.commit()

        counts = self.client.get("/api/next/lists?limit=50").get_json()["counts"]
        self.assertEqual(counts["watchedMovies"], 1)
        self.assertEqual(counts["watchedEpisodes"], 1)
        self.assertEqual(counts["watchHistory"], 2)

    def test_a_watched_episode_survives_its_episode_being_deleted(self):
        """Same guarantee as the watchlist, and the reason the watched insert
        writes a snapshot at all: `episode_id` is SET NULL on delete, so without
        one the row would decay into an entry naming nothing."""
        with self.connect() as conn:
            ids = self._linked_fixture(conn)
        self.client.post(f"/api/next/series/episodes/{ids['episode']}/watched", json={})
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM series_episodes WHERE id = %s", (ids["episode"],))
            conn.commit()

        rows = [
            row
            for row in self.client.get("/api/next/lists?limit=50").get_json()["watched"]
            if row.get("kind") == "episode"
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Third")
        self.assertEqual(rows[0]["series_title"], "Show")
        self.assertIsNone(rows[0]["disc_movie_id"], "the disc link went with the episode")
        self.assertFalse(rows[0]["movie_exists"], "so the entry is not clickable")
