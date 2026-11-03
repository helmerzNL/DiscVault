"""A box set nested in a collection shows the cover its own page shows.

`containers.metadata` never carries a container's artwork:
`link_container_media_option` writes an `entity_media` row and leaves the
metadata JSON alone. The collection-items query read nothing else, so a box set
filed inside a collection rendered an empty tile while its own page, which
resolves artwork from `entity_media`, showed the cover (#811). The library list
and the library snapshot had already been given the join; this is the third
surface reading the same containers.

Its own cover was only half of it. A box set that owns no artwork borrows a
member film's poster on every other surface -- the library tile and the library
snapshot both carry `container_member_poster_lateral` -- and most box sets own
none, so the reporter was still looking at an empty tile after the first half
shipped. The borrowed cover arrives in its own `member_poster_url` column,
never folded into `poster_url`: which cover wins belongs to the client resolver,
and the "missing poster" filter has to keep being able to tell a container's own
artwork from one it is only standing in with.

Postgres-backed on purpose: the defect is in the SQL and in which columns it
selects, and neither is decidable against a fake cursor. Everything is written
inside one transaction that is rolled back, so the tests leave no rows behind.
"""

from __future__ import annotations

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
except ModuleNotFoundError:  # pragma: no cover - exercised by the skip below
    psycopg = None
    dict_row = None

try:
    from app.backend import next_app
except Exception:  # pragma: no cover - Flask or JWT_SECRET missing locally
    next_app = None


DATABASE_URL = os.environ.get("DATABASE_URL")


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and next_app is not None,
    "PostgreSQL test database is not configured",
)
class CollectionItemPosterTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        self.addCleanup(self.conn.close)
        # Never committed: the rollback is the whole cleanup.
        self.addCleanup(self.conn.rollback)
        self.collection_id = self._container("collection", "Nolan shelf")

    # --- fixtures ------------------------------------------------------------

    def _container(self, container_type: str, title: str, metadata: str = "{}") -> str:
        container_id = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO containers (id, public_id, container_type, title, metadata)
                VALUES (%s, %s, %s, %s, %s::jsonb)
                """,
                (container_id, f"test-{container_id}", container_type, title, metadata),
            )
        return container_id

    def _link(self, item_id: str, item_type: str, sort_order: int = 0) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO collection_items (collection_id, item_type, item_id, sort_order)
                VALUES (%s, %s, %s, %s)
                """,
                (self.collection_id, item_type, item_id, sort_order),
            )

    def _artwork(
        self,
        container_id: str,
        *,
        provider_id: str | None = "tmdb",
        is_primary: bool = True,
        sort_order: int = 0,
        deleted: bool = False,
        hidden: bool = False,
    ) -> str:
        asset_id = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO media_assets (id, kind, variant, storage_backend, storage_key, provider_id, sha256)
                VALUES (%s, 'poster', 'original', 'local', %s, %s, %s)
                """,
                (asset_id, f"posters/{asset_id}.jpg", provider_id, asset_id.replace("-", "")),
            )
            cur.execute(
                """
                INSERT INTO entity_media (
                    entity_type, entity_id, media_id, role, is_primary, sort_order,
                    deleted_at, hidden_at
                )
                VALUES (
                    'container', %s, %s, 'poster', %s, %s,
                    CASE WHEN %s THEN now() END,
                    CASE WHEN %s THEN now() END
                )
                """,
                (container_id, asset_id, is_primary, sort_order, deleted, hidden),
            )
        return asset_id

    def _movie(self, title: str, metadata: str = "{}") -> str:
        movie_id = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movies (id, public_id, title, metadata)
                VALUES (%s, %s, %s, %s::jsonb)
                """,
                (movie_id, f"test-{movie_id}", title, metadata),
            )
        return movie_id

    def _movie_artwork(
        self,
        movie_id: str,
        *,
        provider_id: str | None = "tmdb",
        deleted: bool = False,
        hidden: bool = False,
    ) -> str:
        asset_id = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO media_assets (id, kind, variant, storage_backend, storage_key, provider_id, sha256)
                VALUES (%s, 'poster', 'original', 'local', %s, %s, %s)
                """,
                (asset_id, f"posters/{asset_id}.jpg", provider_id, asset_id.replace("-", "")),
            )
            cur.execute(
                """
                INSERT INTO entity_media (
                    entity_type, entity_id, media_id, role, is_primary, sort_order,
                    deleted_at, hidden_at
                )
                VALUES (
                    'movie', %s, %s, 'poster', true, 0,
                    CASE WHEN %s THEN now() END,
                    CASE WHEN %s THEN now() END
                )
                """,
                (movie_id, asset_id, deleted, hidden),
            )
        return asset_id

    def _member(self, container_id: str, movie_id: str, sort_order: int = 0) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO container_movies (container_id, movie_id, sort_order)
                VALUES (%s, %s, %s)
                """,
                (container_id, movie_id, sort_order),
            )

    def _items(self) -> list[dict]:
        return next_app.collection_item_entities(self.conn, uuid.UUID(self.collection_id))

    def _only_item(self) -> dict:
        items = self._items()
        self.assertEqual(len(items), 1, items)
        return items[0]

    # --- the reported bug ----------------------------------------------------

    def test_a_nested_box_sets_own_artwork_reaches_the_item(self):
        box_set_id = self._container("box_set", "The Dark Knight Trilogy")
        asset_id = self._artwork(box_set_id)
        self._link(box_set_id, "box_set")
        self.assertEqual(
            self._only_item().get("poster_url"),
            f"/api/next/media/assets/{asset_id}",
        )

    def test_a_vault_borrows_nothing_but_still_shows_its_own_cover(self):
        """Not box-set specific: every container type reads the same column."""
        vault_id = self._container("vault", "Shelf B")
        asset_id = self._artwork(vault_id)
        self._link(vault_id, "vault")
        self.assertEqual(
            self._only_item().get("poster_url"),
            f"/api/next/media/assets/{asset_id}",
        )

    # --- what must not change ------------------------------------------------

    def test_a_metadata_cover_still_wins_when_there_is_no_artwork_row(self):
        """The old behaviour is the fallback, not a casualty of the fix."""
        box_set_id = self._container(
            "box_set",
            "Alien Anthology",
            '{"poster_url": "https://images.example/alien.jpg"}',
        )
        self._link(box_set_id, "box_set")
        self.assertEqual(
            self._only_item().get("poster_url"),
            "https://images.example/alien.jpg",
        )

    def test_a_container_with_no_cover_anywhere_reports_none(self):
        """An empty tile is the honest answer when there is no artwork; the
        renderer falls back to the type label, and inventing a URL here would
        hide a genuine gap from the "missing poster" filter."""
        box_set_id = self._container("box_set", "Unidentified set")
        self._link(box_set_id, "box_set")
        self.assertIsNone(self._only_item().get("poster_url"))

    # --- the join's own filters ----------------------------------------------

    def test_artwork_the_user_deleted_or_hid_is_not_shown(self):
        box_set_id = self._container("box_set", "Removed cover")
        self._artwork(box_set_id, deleted=True)
        self._artwork(box_set_id, hidden=True, sort_order=1)
        self._link(box_set_id, "box_set")
        self.assertIsNone(self._only_item().get("poster_url"))

    def test_the_chosen_primary_outranks_the_other_options(self):
        box_set_id = self._container("box_set", "Several covers")
        self._artwork(box_set_id, is_primary=False, sort_order=0)
        chosen = self._artwork(box_set_id, is_primary=True, sort_order=5)
        self._link(box_set_id, "box_set")
        self.assertEqual(
            self._only_item().get("poster_url"),
            f"/api/next/media/assets/{chosen}",
        )

    def test_a_movievault_cover_resolves_to_its_own_route(self):
        """Why the query has to select `provider_id`: a MovieVault poster is
        refused by the generic media route, so an asset read without its
        provider renders a broken image against a 404."""
        box_set_id = self._container("box_set", "MovieVault cover")
        asset_id = self._artwork(box_set_id, provider_id="movievault_v2:box-set-asset-1")
        self._link(box_set_id, "box_set")
        self.assertEqual(
            self._only_item().get("poster_url"),
            f"/api/next/movievault-v2/posters/{asset_id}",
        )

    # --- the rest of the payload ---------------------------------------------

    def test_the_item_still_carries_what_the_renderer_reads(self):
        box_set_id = self._container("box_set", "Payload shape")
        self._artwork(box_set_id)
        self._link(box_set_id, "box_set", sort_order=3)
        item = self._only_item()
        self.assertEqual(item.get("entity_type"), "container")
        self.assertEqual(item.get("item_type"), "box_set")
        self.assertEqual(item.get("container_type"), "box_set")
        self.assertEqual(str(item.get("entity_id")), box_set_id)
        self.assertEqual(item.get("title"), "Payload shape")
        self.assertEqual(item.get("sort_order"), 3)
        # The joined columns are folded away rather than shipped to the client.
        for key in (
            "poster_asset_id",
            "poster_asset_storage_backend",
            "poster_asset_storage_key",
            "poster_asset_source_url",
            "poster_asset_provider_id",
        ):
            self.assertNotIn(key, item)

    # --- the borrowed cover ---------------------------------------------------

    def test_a_nested_box_set_with_no_artwork_borrows_its_members_poster(self):
        """The reported bug after the first fix shipped. The box set owns no
        artwork, so the library tile shows a member film's poster and the
        collection tile showed the type label -- two surfaces disagreeing about
        the same box set, which is the whole defect in #811."""
        box_set_id = self._container("box_set", "The Dark Knight Trilogy")
        movie_id = self._movie("Batman Begins")
        asset_id = self._movie_artwork(movie_id)
        self._member(box_set_id, movie_id)
        self._link(box_set_id, "box_set")
        self.assertEqual(
            self._only_item().get("member_poster_url"),
            f"/api/next/media/assets/{asset_id}",
        )

    def test_a_borrowed_metadata_poster_is_resolved_too(self):
        """A member whose cover never became an artwork row still answers: most
        of the library was imported with the poster in the metadata JSON."""
        box_set_id = self._container("box_set", "Alien Anthology")
        movie_id = self._movie("Alien", '{"poster_url": "https://images.example/alien.jpg"}')
        self._member(box_set_id, movie_id)
        self._link(box_set_id, "box_set")
        self.assertEqual(
            self._only_item().get("member_poster_url"),
            "https://images.example/alien.jpg",
        )

    def test_the_borrowed_cover_follows_the_member_order(self):
        """The same member the library tile borrows from, so the two surfaces
        cannot pick different films."""
        box_set_id = self._container("box_set", "Ordered set")
        first = self._movie("First disc")
        second = self._movie("Second disc")
        first_asset = self._movie_artwork(first)
        self._movie_artwork(second)
        self._member(box_set_id, second, sort_order=1)
        self._member(box_set_id, first, sort_order=0)
        self._link(box_set_id, "box_set")
        self.assertEqual(
            self._only_item().get("member_poster_url"),
            f"/api/next/media/assets/{first_asset}",
        )

    def test_a_member_with_no_cover_is_skipped_for_one_that_has_it(self):
        """The lateral filters the coverless member out rather than borrowing
        from the first member and reporting nothing."""
        box_set_id = self._container("box_set", "Partly covered")
        bare = self._movie("No cover")
        covered = self._movie("Has a cover")
        asset_id = self._movie_artwork(covered)
        self._member(box_set_id, bare, sort_order=0)
        self._member(box_set_id, covered, sort_order=1)
        self._link(box_set_id, "box_set")
        self.assertEqual(
            self._only_item().get("member_poster_url"),
            f"/api/next/media/assets/{asset_id}",
        )

    def test_artwork_the_user_deleted_or_hid_is_not_borrowed_either(self):
        box_set_id = self._container("box_set", "Removed member cover")
        movie_id = self._movie("Batman Begins")
        self._movie_artwork(movie_id, deleted=True)
        self._member(box_set_id, movie_id)
        self._link(box_set_id, "box_set")
        self.assertIsNone(self._only_item().get("member_poster_url"))

    def test_a_nested_collection_borrows_from_its_own_linked_films(self):
        """A collection's members are `collection_items` rows, not
        `container_movies` -- the lateral reads both membership tables."""
        inner_id = self._container("collection", "Nolan films")
        movie_id = self._movie("Inception")
        asset_id = self._movie_artwork(movie_id)
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO collection_items (collection_id, item_type, item_id, sort_order)
                VALUES (%s, 'movie', %s, 0)
                """,
                (inner_id, movie_id),
            )
        self._link(inner_id, "collection")
        self.assertEqual(
            self._only_item().get("member_poster_url"),
            f"/api/next/media/assets/{asset_id}",
        )

    def test_a_borrowed_movievault_cover_resolves_to_its_own_route(self):
        box_set_id = self._container("box_set", "MovieVault member cover")
        movie_id = self._movie("Heat")
        asset_id = self._movie_artwork(movie_id, provider_id="movievault_v2:member-asset-1")
        self._member(box_set_id, movie_id)
        self._link(box_set_id, "box_set")
        self.assertEqual(
            self._only_item().get("member_poster_url"),
            f"/api/next/movievault-v2/posters/{asset_id}",
        )

    # --- what the borrowed cover must not do ----------------------------------

    def test_a_borrowed_cover_never_overwrites_the_containers_own(self):
        """Ownership is not display: laundering a member's poster into
        `poster_url` would make the box set look like it owns artwork, and the
        "missing poster" filter reads that column."""
        box_set_id = self._container("box_set", "Own cover wins")
        own_asset = self._artwork(box_set_id)
        movie_id = self._movie("Batman Begins")
        member_asset = self._movie_artwork(movie_id)
        self._member(box_set_id, movie_id)
        self._link(box_set_id, "box_set")
        item = self._only_item()
        self.assertEqual(item.get("poster_url"), f"/api/next/media/assets/{own_asset}")
        self.assertEqual(
            item.get("member_poster_url"),
            f"/api/next/media/assets/{member_asset}",
        )
        self.assertNotEqual(item.get("poster_url"), item.get("member_poster_url"))

    def test_a_container_with_no_cover_and_no_member_still_reports_none(self):
        """An empty tile stays the honest answer; inventing a URL here would
        hide a genuine gap from the "missing poster" filter."""
        box_set_id = self._container("box_set", "Nothing anywhere")
        self._link(box_set_id, "box_set")
        item = self._only_item()
        self.assertIsNone(item.get("poster_url"))
        self.assertIsNone(item.get("member_poster_url"))

    def test_the_borrowed_join_columns_are_folded_away(self):
        box_set_id = self._container("box_set", "Folded payload")
        movie_id = self._movie("Batman Begins")
        self._movie_artwork(movie_id)
        self._member(box_set_id, movie_id)
        self._link(box_set_id, "box_set")
        item = self._only_item()
        for key in (
            "member_poster_asset_id",
            "member_poster_storage_backend",
            "member_poster_storage_key",
            "member_poster_source_url",
            "member_poster_provider_id",
            "member_poster_metadata_url",
        ):
            self.assertNotIn(key, item)

    def test_a_linked_movie_item_is_untouched_by_the_borrowed_join(self):
        """The movie branch of the query is a separate statement; it must not
        grow a borrowed column it has no use for."""
        movie_id = self._movie("Standalone", '{"poster_url": "https://images.example/solo.jpg"}')
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO collection_items (collection_id, item_type, item_id, sort_order)
                VALUES (%s, 'movie', %s, 0)
                """,
                (self.collection_id, movie_id),
            )
        item = self._only_item()
        self.assertEqual(item.get("entity_type"), "movie")
        self.assertEqual(item.get("poster_url"), "https://images.example/solo.jpg")
        self.assertNotIn("member_poster_url", item)

    def test_movies_and_containers_still_arrive_in_one_sorted_list(self):
        first = self._container("box_set", "A set")
        second = self._container("vault", "B vault")
        self._artwork(second)
        self._link(second, "vault", sort_order=1)
        self._link(first, "box_set", sort_order=0)
        titles = [item.get("title") for item in self._items()]
        self.assertEqual(titles, ["A set", "B vault"])


if __name__ == "__main__":
    unittest.main()
