"""Recording a watch takes its subject off the watchlist (#719).

Reported as: "The system should automatically remove a movie from Watchlist
when a Watched Date is added. I notice that it keeps both, which is an
oxymoron." Oxymoron is the right word -- nothing downstream breaks when a film
sits on both lists, it is simply not a state that means anything. The watchlist
answers "what do I intend to watch"; the watched list answers "what have I
watched". Answering the second settles the first.

The interesting half of this module is what the rule does *not* touch:

  - the plugin sync and the CSV import mirror a stated dataset. The plugin sync
    in particular imports a watchlist and then a watch history in the same run,
    in that order -- so applying the rule there would make the second half of
    one operation delete what the first half just wrote, and would prune an
    external service's watchlist using its own history;
  - a backup restore reproduces a saved state of *both* tables. Clearing there
    would silently edit the backup;
  - another user's watchlist entry for the same film, since a watch is personal.

One assumption this module started with turned out to be wrong, and the
corrected version is worth keeping: the delete matches the user with
`IS NOT DISTINCT FROM` (as the removal routes do), and with authentication off
the actor really does carry no id -- but `watchlist_items.user_id` is NOT NULL,
so no row can be owned by nobody and the clause protects nothing. What matters
there is the property it must *not* have: an actor without an id must clear
nothing rather than everything, which is the way this shape of query goes
wrong. That is what the test below pins.

Skipped without DATABASE_URL, like the other post-migration suites.
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


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and next_app is not None,
    "PostgreSQL test database is not configured",
)
class WatchClearsWatchlistTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)

    def setUp(self):
        self.tag = f"wcw719-{uuid.uuid4()}"
        self.user_ids = []
        self.movie_ids = []
        self.addCleanup(self._remove_fixture_rows)

    def _remove_fixture_rows(self):
        with self.connect() as conn, conn.cursor() as cur:
            for user_id in self.user_ids:
                cur.execute("DELETE FROM user_sync_changes WHERE user_id=%s", (user_id,))
                cur.execute("DELETE FROM user_sync_state WHERE user_id=%s", (user_id,))
                cur.execute("DELETE FROM watchlist_items WHERE user_id=%s", (user_id,))
                cur.execute("DELETE FROM watch_history WHERE user_id=%s", (user_id,))
                cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
            cur.execute("DELETE FROM watchlist_items WHERE user_id IS NULL")
            cur.execute("DELETE FROM watch_history WHERE user_id IS NULL")
            for movie_id in self.movie_ids:
                cur.execute("DELETE FROM movies WHERE id=%s", (movie_id,))

    def _make_user(self, conn):
        user_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, username, display_name, status) VALUES (%s, %s, 'W', 'active')",
                (user_id, f"{self.tag}-{len(self.user_ids)}"),
            )
        self.user_ids.append(user_id)
        return user_id

    def _make_movie(self, conn):
        movie_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO movies (id, public_id, title) VALUES (%s, %s, %s)",
                (movie_id, str(uuid.uuid4()), f"{self.tag} movie"),
            )
        self.movie_ids.append(movie_id)
        return movie_id

    def _watchlist(self, conn, user_id, movie_id):
        item_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO watchlist_items (id, user_id, movie_id, added_at, snapshot) VALUES (%s, %s, %s, now(), %s)",
                (item_id, user_id, movie_id, Jsonb({})),
            )
        return item_id

    def _watchlist_movie_ids(self, conn, user_id):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT movie_id FROM watchlist_items WHERE user_id IS NOT DISTINCT FROM %s AND episode_id IS NULL",
                (user_id,),
            )
            return [row["movie_id"] for row in cur.fetchall()]

    # -- the rule itself ----------------------------------------------------

    def test_a_watch_removes_the_watchlist_entry(self):
        with self.connect() as conn:
            user = self._make_user(conn)
            movie = self._make_movie(conn)
            self._watchlist(conn, user, movie)

            removed = next_app.clear_watchlist_after_watch(conn, user, movie_id=movie)

            self.assertEqual(removed, 1)
            self.assertEqual(self._watchlist_movie_ids(conn, user), [])

    def test_it_reaches_the_users_sync_stream_like_an_explicit_removal(self):
        with self.connect() as conn:
            user = self._make_user(conn)
            movie = self._make_movie(conn)
            self._watchlist(conn, user, movie)

            next_app.clear_watchlist_after_watch(conn, user, movie_id=movie)

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT operation FROM user_sync_changes
                    WHERE user_id=%s AND entity_type='watchlist' AND entity_id=%s
                    """,
                    (user, str(movie)),
                )
                self.assertEqual([row["operation"] for row in cur.fetchall()], ["delete"])

    def test_watching_something_that_was_not_on_the_watchlist_is_a_no_op(self):
        """And emits nothing: a sync client must not be told about a removal
        that did not happen."""
        with self.connect() as conn:
            user = self._make_user(conn)
            movie = self._make_movie(conn)

            removed = next_app.clear_watchlist_after_watch(conn, user, movie_id=movie)

            self.assertEqual(removed, 0)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM user_sync_changes WHERE user_id=%s AND entity_type='watchlist'",
                    (user,),
                )
                self.assertEqual(cur.fetchall(), [])

    def test_only_this_film_leaves_the_watchlist(self):
        with self.connect() as conn:
            user = self._make_user(conn)
            watched = self._make_movie(conn)
            still_wanted = self._make_movie(conn)
            self._watchlist(conn, user, watched)
            self._watchlist(conn, user, still_wanted)

            next_app.clear_watchlist_after_watch(conn, user, movie_id=watched)

            self.assertEqual(self._watchlist_movie_ids(conn, user), [still_wanted])

    def test_another_users_watchlist_entry_is_untouched(self):
        """A watch is personal. One person seeing a film says nothing about
        whether anyone else still intends to."""
        with self.connect() as conn:
            viewer = self._make_user(conn)
            other = self._make_user(conn)
            movie = self._make_movie(conn)
            self._watchlist(conn, viewer, movie)
            self._watchlist(conn, other, movie)

            next_app.clear_watchlist_after_watch(conn, viewer, movie_id=movie)

            self.assertEqual(self._watchlist_movie_ids(conn, viewer), [])
            self.assertEqual(self._watchlist_movie_ids(conn, other), [movie])

    def test_an_actor_without_an_id_clears_nothing_rather_than_everything(self):
        """With authentication off the actor carries no id. `user_id` is NOT
        NULL, so there is nothing of theirs to clear -- and the query must
        resolve to that, not to "every user's entry for this film". A `WHERE`
        that degrades into a match-all is the failure worth pinning, because it
        would be invisible on a single-user instance and destructive on any
        other."""
        with self.connect() as conn:
            owner = self._make_user(conn)
            movie = self._make_movie(conn)
            self._watchlist(conn, owner, movie)

            removed = next_app.clear_watchlist_after_watch(conn, None, movie_id=movie)

            self.assertEqual(removed, 0)
            self.assertEqual(
                self._watchlist_movie_ids(conn, owner),
                [movie],
                "an anonymous actor must not clear a real user's watchlist entry",
            )

    def test_an_episode_entry_is_not_swept_up_by_a_movie_watch(self):
        """Episode entries have their own lifecycle; a film's watch must not
        reach them even when the ids happen to line up."""
        with self.connect() as conn:
            user = self._make_user(conn)
            movie = self._make_movie(conn)
            self._watchlist(conn, user, movie)
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO watchlist_items (id, user_id, episode_id, added_at, snapshot) VALUES (%s, %s, NULL, now(), %s)",
                    (uuid.uuid4(), user, Jsonb({"kind": "episode"})),
                )

            next_app.clear_watchlist_after_watch(conn, user, movie_id=movie)

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*)::int AS count FROM watchlist_items WHERE user_id=%s",
                    (user,),
                )
                self.assertEqual(cur.fetchone()["count"], 1, "the episode-shaped entry survives")

    def test_the_rule_is_one_directional(self):
        """Watching clears the watchlist; putting it back afterwards is an
        intention to see it again and must survive. Without this the two rules
        would fight and a rewatch could never be planned -- and it is what lets
        the episode-presentation tests still exercise both lists with one
        subject."""
        with self.connect() as conn:
            user = self._make_user(conn)
            movie = self._make_movie(conn)
            self._watchlist(conn, user, movie)
            next_app.clear_watchlist_after_watch(conn, user, movie_id=movie)
            self.assertEqual(self._watchlist_movie_ids(conn, user), [])

            # Re-added after the watch, the way someone plans a rewatch.
            self._watchlist(conn, user, movie)

            self.assertEqual(
                self._watchlist_movie_ids(conn, user),
                [movie],
                "nothing re-applies the rule to an entry added after the watch",
            )

    def test_neither_id_removes_nothing(self):
        with self.connect() as conn:
            user = self._make_user(conn)
            movie = self._make_movie(conn)
            self._watchlist(conn, user, movie)

            self.assertEqual(next_app.clear_watchlist_after_watch(conn, user), 0)
            self.assertEqual(self._watchlist_movie_ids(conn, user), [movie])


@unittest.skipUnless(next_app is not None, "backend module is unavailable")
class WatchClearsWatchlistWiringTests(unittest.TestCase):
    """Which paths apply the rule, read out of the source that defines them.

    The scope is the argument, not the mechanism: the rule belongs to the act
    of recording a watch, and not to a path that reproduces a dataset somebody
    else already decided.
    """

    @classmethod
    def setUpClass(cls):
        here = os.path.dirname(__file__)
        with open(os.path.join(here, "..", "next_app.py"), encoding="utf-8") as handle:
            cls.app_source = handle.read()
        for name in ("next_worker.py", "next_import.py", "next_backup.py"):
            with open(os.path.join(here, "..", name), encoding="utf-8") as handle:
                setattr(cls, name.replace(".py", "_source"), handle.read())

    def _route_body(self, name, span=3200):
        start = self.app_source.index(f"def {name}(")
        return self.app_source[start:start + span]

    def test_marking_a_movie_watched_clears_its_watchlist_entry(self):
        body = self._route_body("mark_movie_watched")
        self.assertIn("clear_watchlist_after_watch(", body)
        self.assertIn("movie_id=movie_uuid", body)

    def test_marking_an_episode_watched_clears_its_watchlist_entry(self):
        body = self._route_body("mark_episode_watched")
        self.assertIn("clear_watchlist_after_watch(", body)
        self.assertIn("episode_id=episode_uuid", body)

    def test_marking_a_season_watched_clears_the_whole_seasons_entries(self):
        body = self._route_body("mark_season_watched")
        self.assertIn("clear_watchlist_for_watched_season(", body)

    def test_the_clear_happens_inside_the_inserts_transaction(self):
        """A watch recorded while the watchlist entry survived is exactly the
        state being fixed, so the two must not be separable."""
        body = self._route_body("mark_movie_watched")
        insert = body.index("INSERT INTO watch_history")
        clear = body.index("clear_watchlist_after_watch(")
        transaction = body.index("with conn.transaction():")
        self.assertLess(transaction, insert)
        self.assertLess(insert, clear)
        self.assertNotIn("return response", body[transaction:clear])

    def test_the_mirroring_paths_deliberately_do_not_apply_the_rule(self):
        """The plugin sync imports a watchlist and then a watch history in the
        same run. Applying the rule there would make one operation delete what
        it had just written, and would prune an external service's watchlist
        with its own history import. The CSV import and the backup restore
        reproduce a stated dataset for the same reason."""
        for name in ("next_worker_source", "next_import_source", "next_backup_source"):
            source = getattr(self, name)
            self.assertIn("watch_history", source, f"{name} should still write watch history")
            self.assertNotIn(
                "clear_watchlist_after_watch",
                source,
                f"{name} mirrors a dataset and must not prune the watchlist from it",
            )

    def test_the_worker_still_imports_a_watchlist_before_a_watch_history(self):
        """The ordering that makes the exclusion above necessary. If these ever
        swap, the reasoning in that test stops describing the code."""
        source = self.worker_source_or_none()
        if source is None:
            self.skipTest("worker source unavailable")
        watchlist_insert = source.index("INSERT INTO watchlist_items")
        watched_insert = source.index("INSERT INTO watch_history")
        self.assertLess(watchlist_insert, watched_insert)

    def worker_source_or_none(self):
        return getattr(self, "next_worker_source", None)


if __name__ == "__main__":
    unittest.main()
