"""Deleting a movie takes it off the watchlist; watched history survives (#719).

The policy, settled on the issue: a movie deleted from the library must not be
visible on the Watchlist -- there is no disc left to watch -- while the Watched
list deliberately keeps its entries, because they record something that
happened. Before this, `watchlist_items.movie_id` went to NULL on delete
(migration 015) and the orphan row rendered as an unclickable card whose poster
may 404: the "ghost on the watchlist" half of #719.

Three layers enforce the policy, and each is tested against a real PostgreSQL
because each is a claim about what the database does:

  - `delete_movie_records()` deletes the movie's watchlist rows outright and
    tells each affected user's private sync stream;
  - the watchlist read path hides rows that are orphaned anyway (entries from
    before this change, or whose movie a sync client tombstoned), after the
    relink has had its chance to point them at a re-added copy;
  - the relink never matches a deleted movie, because that would rebuild the
    orphan it exists to repair.

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
class WatchlistDeletionTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)

    def setUp(self):
        self.tag = f"wl719-{uuid.uuid4()}"
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

    def _make_movie(self, conn, *, title=None, barcode=None, deleted=False):
        movie_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movies (id, public_id, title, barcode, deleted_at)
                VALUES (%s, %s, %s, %s, CASE WHEN %s THEN now() ELSE NULL END)
                """,
                (movie_id, str(uuid.uuid4()), title or f"{self.tag} movie", barcode, deleted),
            )
        self.movie_ids.append(movie_id)
        return movie_id

    def _add_watchlist_row(self, conn, user_id, movie_id, snapshot=None):
        item_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO watchlist_items (id, user_id, movie_id, added_at, snapshot)
                VALUES (%s, %s, %s, now(), %s)
                """,
                (item_id, user_id, movie_id, Jsonb(snapshot or {})),
            )
        return item_id

    def _watchlist_titles(self, conn, user_id):
        rows = next_app.personal_list_movie_entities(conn, user_id, kind="watchlist")
        return [row.get("title") for row in rows]

    def test_deleting_a_movie_removes_its_watchlist_rows_for_every_user(self):
        with self.connect() as conn:
            first = self._make_user(conn)
            second = self._make_user(conn)
            doomed = self._make_movie(conn, title=f"{self.tag} doomed")
            kept = self._make_movie(conn, title=f"{self.tag} kept")
            doomed_first = self._add_watchlist_row(conn, first, doomed)
            doomed_second = self._add_watchlist_row(conn, second, doomed)
            kept_first = self._add_watchlist_row(conn, first, kept)

            _, deleted = next_app.delete_movie_records(conn, doomed)

            self.assertEqual(deleted.get("watchlist_items"), 2)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM watchlist_items WHERE id = ANY(%s)",
                    ([doomed_first, doomed_second],),
                )
                self.assertEqual(cur.fetchall(), [], "the deleted movie's watchlist rows must be gone, not orphaned")
                cur.execute("SELECT id FROM watchlist_items WHERE id=%s", (kept_first,))
                self.assertEqual(len(cur.fetchall()), 1, "other movies' rows are untouched")

    def test_the_delete_reaches_each_watchers_sync_stream(self):
        with self.connect() as conn:
            watcher = self._make_user(conn)
            bystander = self._make_user(conn)
            doomed = self._make_movie(conn)
            self._add_watchlist_row(conn, watcher, doomed)

            next_app.delete_movie_records(conn, doomed)

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT operation FROM user_sync_changes
                    WHERE user_id=%s AND entity_type='watchlist' AND entity_id=%s
                    """,
                    (watcher, str(doomed)),
                )
                self.assertEqual([row["operation"] for row in cur.fetchall()], ["delete"])
                cur.execute(
                    "SELECT 1 FROM user_sync_changes WHERE user_id=%s AND entity_type='watchlist'",
                    (bystander,),
                )
                self.assertEqual(cur.fetchall(), [], "a user who never watchlisted the movie hears nothing")

    def test_watch_history_survives_the_delete_as_a_snapshot_entry(self):
        with self.connect() as conn:
            user = self._make_user(conn)
            doomed = self._make_movie(conn, title=f"{self.tag} watched once")
            entry_id = uuid.uuid4()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO watch_history (id, user_id, movie_id, watched_at, snapshot)
                    VALUES (%s, %s, %s, now(), %s)
                    """,
                    (entry_id, user, doomed, Jsonb({"movie_title": f"{self.tag} watched once"})),
                )

            next_app.delete_movie_records(conn, doomed)

            with conn.cursor() as cur:
                cur.execute("SELECT movie_id FROM watch_history WHERE id=%s", (entry_id,))
                row = cur.fetchone()
            self.assertIsNotNone(row, "history is history: the entry must survive the delete")
            self.assertIsNone(row["movie_id"])
            watched = next_app.personal_list_movie_entities(conn, user, kind="watched")
            titles = [item.get("title") for item in watched]
            self.assertIn(f"{self.tag} watched once", titles, "the watched list still shows the entry, from its snapshot")
            entry = next(item for item in watched if item.get("title") == f"{self.tag} watched once")
            self.assertFalse(entry.get("movie_exists"))

    def test_orphaned_watchlist_entries_are_hidden_not_rendered(self):
        # Rows orphaned before deletion cleaned them up (jeff's install has
        # them today): movie_id NULL, snapshot only, no matching live movie.
        with self.connect() as conn:
            user = self._make_user(conn)
            live = self._make_movie(conn, title=f"{self.tag} live")
            self._add_watchlist_row(conn, user, live)
            self._add_watchlist_row(
                conn, user, None, snapshot={"movie_title": f"{self.tag} ghost", "title": f"{self.tag} ghost"}
            )

            titles = self._watchlist_titles(conn, user)

            self.assertIn(f"{self.tag} live", titles)
            self.assertNotIn(f"{self.tag} ghost", titles, "an orphan entry renders as a dead card and must be hidden")
            counts = next_app.personal_list_counts(conn, user)
            self.assertEqual(counts["watchlist"], 1, "the badge must agree with what the list shows")

    def test_a_tombstoned_movie_is_off_the_watchlist_too(self):
        # The sync path soft-deletes (deleted_at); for the watchlist that is as
        # gone as a hard delete.
        with self.connect() as conn:
            user = self._make_user(conn)
            tombstoned = self._make_movie(conn, title=f"{self.tag} tombstoned", deleted=True)
            self._add_watchlist_row(conn, user, tombstoned)

            titles = self._watchlist_titles(conn, user)

            self.assertNotIn(f"{self.tag} tombstoned", titles)
            self.assertEqual(next_app.personal_list_counts(conn, user)["watchlist"], 0)

    def test_an_orphan_relinks_to_a_readded_copy_and_stays_visible(self):
        # The one way an orphan may come back: the same disc re-added. The
        # snapshot's barcode finds the live copy and the row re-attaches.
        barcode = f"719-{uuid.uuid4().hex[:12]}"
        with self.connect() as conn:
            user = self._make_user(conn)
            readded = self._make_movie(conn, title=f"{self.tag} readded", barcode=barcode)
            item_id = self._add_watchlist_row(
                conn, user, None, snapshot={"movie_title": f"{self.tag} old name", "barcode": barcode}
            )

            rows = next_app.personal_list_movie_entities(conn, user, kind="watchlist")

            self.assertEqual([str(row.get("id")) for row in rows], [str(readded)])
            with conn.cursor() as cur:
                cur.execute("SELECT movie_id FROM watchlist_items WHERE id=%s", (item_id,))
                self.assertEqual(cur.fetchone()["movie_id"], readded, "the relink is persisted, not recomputed forever")

    def test_the_relink_never_matches_a_deleted_movie(self):
        # Relinking to a tombstone would rebuild the orphan the relink exists
        # to repair.
        barcode = f"719-{uuid.uuid4().hex[:12]}"
        with self.connect() as conn:
            user = self._make_user(conn)
            self._make_movie(conn, title=f"{self.tag} dead copy", barcode=barcode, deleted=True)
            self._add_watchlist_row(
                conn, user, None, snapshot={"movie_title": f"{self.tag} orphan", "barcode": barcode}
            )

            match = next_app.personal_list_snapshot_match_movie(conn, {"barcode": barcode})
            self.assertIsNone(match)
            self.assertEqual(self._watchlist_titles(conn, user), [])

    def test_an_episode_entry_is_untouched_by_a_movie_delete(self):
        # Episode entries have their own lifecycle and their own "not on a
        # disc" presentation; a movie's deletion must not sweep them up, and
        # they keep counting.
        with self.connect() as conn:
            user = self._make_user(conn)
            doomed = self._make_movie(conn)
            self._add_watchlist_row(conn, user, doomed)
            episode_item = self._add_watchlist_row(
                conn, user, None, snapshot={"kind": "episode", "series_title": f"{self.tag} show"}
            )

            next_app.delete_movie_records(conn, doomed)

            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM watchlist_items WHERE id=%s", (episode_item,))
                self.assertEqual(len(cur.fetchall()), 1)
            self.assertEqual(next_app.personal_list_counts(conn, user)["watchlist"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
