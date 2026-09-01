"""A watch-history entry that outlives its movie outlives its poster too (#719).

The Watched list keeps its entries when the movie goes -- deliberately, because
history records something that happened (`personal-lists-on-deletion.md` 2).
What it did not keep was a poster anyone could load. The snapshot froze
whatever address the movie had at the time, and for locally stored artwork that
is `/api/next/media/assets/<id>`: a route that 404s the moment the movie's
`entity_media` links are deleted with it. The list still emitted an `<img>` at
that address, the fetch failed, and with `alt=""` nothing painted -- the poster
frame's gradient showing through as the "black poster" the reporter described.

The rule this pins: **a list that survives its movie survives with an address
that outlives it.** Two mechanisms, and the tests are about the difference
between them:

  - `freeze_surviving_snapshot_images()` runs inside `delete_movie_records()`,
    before the artwork links go, and rewrites the surviving snapshots to the
    artwork's remote `source_url` -- or removes the address entirely when there
    is no durable one, because a value that cannot load is worse than none;
  - `repair_orphaned_snapshot_images()` runs on the read path, for the entries
    already stranded by a delete that predates the rule or by a sync client's
    tombstone, which never runs the delete path at all.

The negative assertions carry as much weight as the positive ones: a local
address must never be left in place, and the watchlist contrast from #728 must
survive this change untouched.

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

REMOTE_POSTER = "https://images.example.test/poster-719.jpg"
REMOTE_BACKDROP = "https://images.example.test/backdrop-719.jpg"


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and next_app is not None,
    "PostgreSQL test database is not configured",
)
class WatchHistoryPosterTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)

    def setUp(self):
        self.tag = f"whp719-{uuid.uuid4()}"
        self.user_ids = []
        self.movie_ids = []
        self.asset_ids = []
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
                cur.execute("DELETE FROM entity_media WHERE entity_id=%s", (movie_id,))
                cur.execute("DELETE FROM movies WHERE id=%s", (movie_id,))
            for asset_id in self.asset_ids:
                cur.execute("DELETE FROM entity_media WHERE media_id=%s", (asset_id,))
                cur.execute("DELETE FROM media_assets WHERE id=%s", (asset_id,))

    def _make_user(self, conn):
        user_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, username, display_name, status) VALUES (%s, %s, 'W', 'active')",
                (user_id, f"{self.tag}-{len(self.user_ids)}"),
            )
        self.user_ids.append(user_id)
        return user_id

    def _make_movie(self, conn, *, title=None, metadata=None):
        movie_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO movies (id, public_id, title, metadata) VALUES (%s, %s, %s, %s)",
                (movie_id, str(uuid.uuid4()), title or f"{self.tag} movie", Jsonb(metadata or {})),
            )
        self.movie_ids.append(movie_id)
        return movie_id

    def _attach_artwork(self, conn, movie_id, *, kind="poster", source_url=None):
        """Link a locally stored asset, so the movie's public URL is the local
        route -- exactly the shape that stops resolving when the movie goes."""
        asset_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO media_assets (id, kind, variant, storage_backend, storage_key, source_url, sha256)
                VALUES (%s, %s, 'original', 'local', %s, %s, %s)
                """,
                (asset_id, kind, f"posters/{asset_id}.jpg", source_url, str(asset_id)),
            )
            cur.execute(
                """
                INSERT INTO entity_media (entity_type, entity_id, media_id, role, is_primary)
                VALUES ('movie', %s, %s, 'primary', true)
                """,
                (movie_id, asset_id),
            )
        self.asset_ids.append(asset_id)
        return asset_id

    def _add_watch_entry(self, conn, user_id, movie_id, snapshot):
        entry_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO watch_history (id, user_id, movie_id, watched_at, snapshot)
                VALUES (%s, %s, %s, now(), %s)
                """,
                (entry_id, user_id, movie_id, Jsonb(snapshot)),
            )
        return entry_id

    def _snapshot(self, conn, entry_id):
        with conn.cursor() as cur:
            cur.execute("SELECT snapshot FROM watch_history WHERE id=%s", (entry_id,))
            return (cur.fetchone() or {}).get("snapshot") or {}

    def _watched_rows(self, conn, user_id):
        return next_app.personal_list_movie_entities(conn, user_id, kind="watched")

    # -- the delete-time freeze -------------------------------------------

    def test_the_delete_freezes_the_artworks_remote_address(self):
        with self.connect() as conn:
            user = self._make_user(conn)
            movie = self._make_movie(conn, title=f"{self.tag} remote artwork")
            asset = self._attach_artwork(conn, movie, source_url=REMOTE_POSTER)
            entry = self._add_watch_entry(
                conn, user, movie, {"movie_title": f"{self.tag} remote artwork", "poster_url": f"/api/next/media/assets/{asset}"}
            )

            next_app.delete_movie_records(conn, movie)

            self.assertEqual(self._snapshot(conn, entry).get("poster_url"), REMOTE_POSTER)
            rows = self._watched_rows(conn, user)
            self.assertEqual([row.get("poster_url") for row in rows], [REMOTE_POSTER])

    def test_a_backdrop_is_frozen_alongside_the_poster(self):
        with self.connect() as conn:
            user = self._make_user(conn)
            movie = self._make_movie(conn)
            self._attach_artwork(conn, movie, kind="poster", source_url=REMOTE_POSTER)
            self._attach_artwork(conn, movie, kind="backdrop", source_url=REMOTE_BACKDROP)
            entry = self._add_watch_entry(
                conn,
                user,
                movie,
                {"poster_url": "/api/next/media/assets/" + str(uuid.uuid4()), "backdrop_url": "/api/next/media/assets/" + str(uuid.uuid4())},
            )

            next_app.delete_movie_records(conn, movie)

            snapshot = self._snapshot(conn, entry)
            self.assertEqual(snapshot.get("poster_url"), REMOTE_POSTER)
            self.assertEqual(snapshot.get("backdrop_url"), REMOTE_BACKDROP)

    def test_movie_metadata_supplies_the_address_when_the_asset_has_no_remote_one(self):
        with self.connect() as conn:
            user = self._make_user(conn)
            movie = self._make_movie(conn, metadata={"poster_url": REMOTE_POSTER})
            self._attach_artwork(conn, movie, source_url=None)
            entry = self._add_watch_entry(conn, user, movie, {"poster_url": "/api/next/media/assets/" + str(uuid.uuid4())})

            next_app.delete_movie_records(conn, movie)

            self.assertEqual(self._snapshot(conn, entry).get("poster_url"), REMOTE_POSTER)

    def test_an_address_that_cannot_outlive_the_movie_is_removed_not_kept(self):
        """Self-uploaded artwork has no remote original. The entry still
        survives -- it is history -- but with no poster rather than a dead one,
        so the list renders its ordinary placeholder."""
        with self.connect() as conn:
            user = self._make_user(conn)
            movie = self._make_movie(conn, title=f"{self.tag} own scan")
            asset = self._attach_artwork(conn, movie, source_url=None)
            entry = self._add_watch_entry(
                conn,
                user,
                movie,
                {"movie_title": f"{self.tag} own scan", "poster_url": f"/api/next/media/assets/{asset}"},
            )

            next_app.delete_movie_records(conn, movie)

            snapshot = self._snapshot(conn, entry)
            self.assertNotIn("poster_url", snapshot)
            rows = self._watched_rows(conn, user)
            self.assertEqual(len(rows), 1, "the entry itself still survives the delete")
            self.assertFalse(rows[0].get("poster_url"), "a dead address must not reach the client")
            self.assertEqual(rows[0].get("title"), f"{self.tag} own scan")

    def test_the_movievault_poster_route_is_not_treated_as_durable(self):
        """That route serves an asset with the same lifecycle as any other, so
        it dies with the movie exactly like the generic one."""
        with self.connect() as conn:
            user = self._make_user(conn)
            movie = self._make_movie(conn, metadata={"poster_url": "/api/next/movievault-v2/posters/" + str(uuid.uuid4())})
            entry = self._add_watch_entry(conn, user, movie, {"poster_url": "/api/next/media/assets/" + str(uuid.uuid4())})

            next_app.delete_movie_records(conn, movie)

            self.assertNotIn("poster_url", self._snapshot(conn, entry))

    def test_a_legacy_snapshot_poster_key_is_cleared_with_the_url(self):
        """`apply_personal_list_snapshot_fallback` reads `poster_file` and
        `poster` when `poster_url` is missing, so clearing only the one it
        writes would leave the same dead address reachable by another key."""
        with self.connect() as conn:
            user = self._make_user(conn)
            movie = self._make_movie(conn)
            entry = self._add_watch_entry(
                conn,
                user,
                movie,
                {"poster_url": "/api/next/media/assets/" + str(uuid.uuid4()), "poster_file": "posters/legacy.jpg", "poster": "posters/legacy.jpg"},
            )

            next_app.delete_movie_records(conn, movie)

            snapshot = self._snapshot(conn, entry)
            for key in ("poster_url", "poster_file", "poster"):
                self.assertNotIn(key, snapshot)
            self.assertFalse(self._watched_rows(conn, user)[0].get("poster_url"))

    def test_a_live_movies_own_artwork_is_untouched_by_the_freeze(self):
        with self.connect() as conn:
            user = self._make_user(conn)
            kept = self._make_movie(conn, title=f"{self.tag} kept")
            asset = self._attach_artwork(conn, kept, source_url=REMOTE_POSTER)
            self._add_watch_entry(conn, user, kept, {})
            doomed = self._make_movie(conn)
            self._add_watch_entry(conn, user, doomed, {})

            next_app.delete_movie_records(conn, doomed)

            rows = [row for row in self._watched_rows(conn, user) if row.get("title") == f"{self.tag} kept"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(
                rows[0].get("poster_url"),
                f"/api/next/media/assets/{asset}",
                "a movie still in the library keeps serving its own local artwork",
            )

    # -- the read-path repair for entries stranded earlier -----------------

    def test_an_entry_stranded_before_the_rule_recovers_the_assets_remote_url(self):
        """The asset row outlives its links until the artwork trash purges it,
        so a row orphaned by an older delete can still be repaired on read."""
        with self.connect() as conn:
            user = self._make_user(conn)
            asset_id = uuid.uuid4()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO media_assets (id, kind, variant, storage_backend, storage_key, source_url, sha256)
                    VALUES (%s, 'poster', 'original', 'local', %s, %s, %s)
                    """,
                    (asset_id, f"posters/{asset_id}.jpg", REMOTE_POSTER, str(asset_id)),
                )
            self.asset_ids.append(asset_id)
            entry_id = uuid.uuid4()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO watch_history (id, user_id, movie_id, watched_at, snapshot)
                    VALUES (%s, %s, NULL, now(), %s)
                    """,
                    (entry_id, user, Jsonb({"movie_title": f"{self.tag} stranded", "poster_url": f"/api/next/media/assets/{asset_id}"})),
                )

            rows = self._watched_rows(conn, user)

            self.assertEqual([row.get("title") for row in rows], [f"{self.tag} stranded"])
            self.assertEqual(rows[0].get("poster_url"), REMOTE_POSTER)

    def test_a_stranded_entry_with_nothing_to_recover_reports_no_poster(self):
        with self.connect() as conn:
            user = self._make_user(conn)
            entry_id = uuid.uuid4()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO watch_history (id, user_id, movie_id, watched_at, snapshot)
                    VALUES (%s, %s, NULL, now(), %s)
                    """,
                    (entry_id, user, Jsonb({"movie_title": f"{self.tag} gone", "poster_url": "/api/next/media/assets/" + str(uuid.uuid4())})),
                )

            rows = self._watched_rows(conn, user)

            self.assertEqual(len(rows), 1, "the entry is history and stays listed")
            self.assertFalse(rows[0].get("poster_url"), "but not with an address that 404s")

    def test_a_legacy_relative_snapshot_path_is_dropped_rather_than_looked_up(self):
        with self.connect() as conn:
            user = self._make_user(conn)
            entry_id = uuid.uuid4()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO watch_history (id, user_id, movie_id, watched_at, snapshot)
                    VALUES (%s, %s, NULL, now(), %s)
                    """,
                    (entry_id, user, Jsonb({"movie_title": f"{self.tag} legacy", "poster_url": "posters/legacy-719.jpg"})),
                )

            rows = self._watched_rows(conn, user)

            self.assertEqual(len(rows), 1)
            self.assertFalse(rows[0].get("poster_url"))

    def test_a_remote_snapshot_address_is_left_exactly_as_it_is(self):
        """127 Hours, from the report: its snapshot held an external URL and it
        did show a poster. Nothing about this change may take that away."""
        with self.connect() as conn:
            user = self._make_user(conn)
            entry_id = uuid.uuid4()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO watch_history (id, user_id, movie_id, watched_at, snapshot)
                    VALUES (%s, %s, NULL, now(), %s)
                    """,
                    (entry_id, user, Jsonb({"movie_title": f"{self.tag} 127", "poster_url": REMOTE_POSTER})),
                )

            rows = self._watched_rows(conn, user)

            self.assertEqual(rows[0].get("poster_url"), REMOTE_POSTER)

    # -- the contrast with the watchlist must survive ----------------------

    def test_the_watchlist_still_loses_its_rows_to_the_delete(self):
        with self.connect() as conn:
            user = self._make_user(conn)
            movie = self._make_movie(conn)
            self._attach_artwork(conn, movie, source_url=REMOTE_POSTER)
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO watchlist_items (id, user_id, movie_id, added_at, snapshot) VALUES (%s, %s, %s, now(), %s)",
                    (uuid.uuid4(), user, movie, Jsonb({})),
                )
            self._add_watch_entry(conn, user, movie, {})

            _, deleted = next_app.delete_movie_records(conn, movie)

            self.assertEqual(deleted.get("watchlist_items"), 1)
            self.assertEqual(next_app.personal_list_movie_entities(conn, user, kind="watchlist"), [])
            self.assertEqual(len(self._watched_rows(conn, user)), 1)


@unittest.skipUnless(next_app is not None, "backend module is unavailable")
class ListPosterRenderingContractTests(unittest.TestCase):
    """The client half, read out of the source it is emitted from.

    An `<img>` whose address fails is what produced the black rectangle, so the
    assertion is that the four personal-list renderers all go through the one
    helper that marks its images for the fallback -- not that any particular
    line looks a certain way.
    """

    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(__file__), "..", "next_views_ui.py")
        with open(path, encoding="utf-8") as handle:
            cls.source = handle.read()

    def test_every_personal_list_renderer_uses_the_shared_poster_helper(self):
        for renderer in (
            "listMovieCardHtml",
            "watchedPosterCardHtml",
            "watchedListItemHtml",
            "watchlistListItemHtml",
        ):
            start = self.source.index(f"function {renderer}(")
            body = self.source[start:start + 1600]
            self.assertIn(
                "listsPosterHtml(",
                body,
                f"{renderer} must build its poster through the shared helper, which marks the image for the fallback",
            )
            self.assertNotIn(
                'usableImage(entry.poster_url)',
                body,
                f"{renderer} must not resolve a poster address of its own again",
            )

    def test_the_helper_marks_its_images_and_a_capture_listener_replaces_them(self):
        helper_start = self.source.index("function listsPosterHtml(")
        helper = self.source[helper_start:helper_start + 600]
        self.assertIn("data-list-poster", helper)
        self.assertIn("collection.noPoster", helper)

        binder_start = self.source.index("function bindListsPosterFallback(")
        binder = self.source[binder_start:binder_start + 900]
        # `error` does not bubble; without capture the listener never fires.
        self.assertIn('addEventListener("error"', binder)
        self.assertIn("true)", binder)
        self.assertIn("data-list-poster", binder)

    def test_the_fallback_is_bound_when_the_lists_view_renders(self):
        render_start = self.source.index("function renderListsView(")
        render = self.source[render_start:self.source.index("async function loadListsView(")]
        self.assertIn("bindListsPosterFallback();", render)


if __name__ == "__main__":
    unittest.main()
