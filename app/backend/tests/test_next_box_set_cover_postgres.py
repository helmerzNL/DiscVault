"""A box set's own cover follows the artwork rule, against a real database.

The artwork rule (App-Guidance `projects/discvault/specs/artwork-precedence.md`, §2.1 last bullet) is the
one a title follows: MovieVault's current cover replaces an older MovieVault cover, a provider's or a
client's cover and one aggregated from the members; the user's choice, a `poster` lock and a cover the
user hid are never touched.

The box refresh did not do that. It linked the catalogue's cached box cover through
`link_local_media_asset_to_container(primary=True)`, which only honours `primary` when the container has
no primary poster at all. A box that had ever shown a cover, of any source, kept it for good while
MovieVault moved on to the current one: the owner saw the new covers in MovieVault and the old ones in
DiscVault, for the box and (until the film rule landed) for its members.

The answer is which row `entity_media` ends up holding as primary, which a fake cursor cannot show.
Everything is written inside one transaction that is rolled back, so the tests leave no rows behind.

Skipped without ``DATABASE_URL``, like the other ``*_postgres`` suites.
"""

from __future__ import annotations

import hashlib
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
    from psycopg.types.json import Jsonb
except ModuleNotFoundError:  # pragma: no cover - exercised by the skip below
    psycopg = None
    dict_row = None
    Jsonb = None

try:
    from app.backend import next_app
except Exception:  # pragma: no cover - Flask or JWT_SECRET missing locally
    next_app = None


DATABASE_URL = os.environ.get("DATABASE_URL")
BARCODE = "5051891191549"
CURRENT_ASSET = "box-cover-test-current"


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and next_app is not None,
    "PostgreSQL test database is not configured",
)
class BoxSetOwnCoverPostgresTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        self.addCleanup(self.conn.close)
        # Never committed: the rollback is the whole cleanup.
        self.addCleanup(self.conn.rollback)
        self.generation = uuid.uuid4()
        self.box_set = uuid.uuid4()
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movievault_v2_sync_state (plugin_id, origin, revision, status, active_generation)
                VALUES ('movievault_v2', 'https://api.discvault.eu', 1, 'current', %s)
                ON CONFLICT (plugin_id) DO UPDATE SET active_generation = EXCLUDED.active_generation
                """,
                (self.generation,),
            )
            cur.execute(
                """
                INSERT INTO movievault_v2_box_sets (generation, box_set_id, title, revision, poster)
                VALUES (%s, %s, 'Rocky I-IV', 10, %s)
                """,
                (
                    self.generation,
                    self.box_set,
                    Jsonb({"assetId": CURRENT_ASSET, "display": {"checksum": "sum-current", "path": "/v2/assets/x"}}),
                ),
            )
            cur.execute(
                """
                INSERT INTO movievault_v2_lookup_hashes (generation, lookup_hash, entity_type, entity_id, source_type)
                VALUES (%s, %s, 'box_set', %s, 'box_set_ean')
                """,
                (self.generation, hashlib.sha256(BARCODE.encode()).hexdigest(), self.box_set),
            )
        # The plugin half of the refresh is not under test; only the catalogue's cached cover is.
        patcher = mock.patch.object(next_app, "box_set_metadata_source_plugins", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- the world -------------------------------------------------------------------------------

    def media(self, provider, *, metadata=None, source_url=None):
        media_id = uuid.uuid4()
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO media_assets (id, kind, variant, storage_backend, storage_key, sha256, provider_id, metadata, source_url)
                VALUES (%s, 'poster', 'display', 'local', %s, %s, %s, %s, %s)
                """,
                (media_id, f"box-cover-test/{media_id}", f"sum-{media_id}", provider, Jsonb(metadata or {}), source_url),
            )
        return media_id

    def cache_current_cover(self):
        """The catalogue's current box cover, cached on the instance and ready."""
        media_id = self.media("movievault_v2")
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movievault_v2_poster_cache (asset_id, variant, checksum, status, media_asset_id)
                VALUES (%s, 'display', 'sum-current', 'ready', %s)
                """,
                (CURRENT_ASSET, media_id),
            )
        return media_id

    def container(self, primary=None, *, hidden=None, metadata=None):
        container_id = uuid.uuid4()
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO containers (id, public_id, container_type, title, barcode, metadata)
                VALUES (%s, %s, 'box_set', 'Rocky I-IV', %s, %s)
                """,
                (container_id, f"box-cover-test-{container_id}", BARCODE, Jsonb(metadata or {})),
            )
            if primary is not None:
                cur.execute(
                    """
                    INSERT INTO entity_media (entity_type, entity_id, media_id, role, is_primary, sort_order)
                    VALUES ('container', %s, %s, 'poster', true, 0)
                    """,
                    (container_id, primary),
                )
            if hidden is not None:
                cur.execute(
                    """
                    INSERT INTO entity_media (entity_type, entity_id, media_id, role, is_primary, sort_order, hidden_at)
                    VALUES ('container', %s, %s, 'poster', false, 5, now())
                    """,
                    (container_id, hidden),
                )
        return container_id

    def refresh(self, container_id, *, metadata=None, write=True):
        container = {"id": str(container_id), "barcode": BARCODE, "title": "Rocky I-IV", "metadata": metadata or {}}
        return next_app.fetch_box_set_artwork_from_sources(self.conn, container, [], None, write=write)

    def primary(self, container_id):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT em.media_id
                FROM entity_media em
                WHERE em.entity_type='container' AND em.entity_id=%s AND em.role='poster'
                  AND em.is_primary AND em.deleted_at IS NULL
                """,
                (container_id,),
            )
            row = cur.fetchone()
        return row["media_id"] if row else None

    def links(self, container_id):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM entity_media WHERE entity_type='container' AND entity_id=%s AND role='poster' AND deleted_at IS NULL",
                (container_id,),
            )
            return cur.fetchone()["n"]

    # -- what replaces what ----------------------------------------------------------------------

    def test_a_box_without_a_cover_gets_the_catalogues(self):
        current = self.cache_current_cover()
        box = self.container()
        self.refresh(box)
        self.assertEqual(self.primary(box), current)

    def test_an_older_movievault_cover_is_replaced_and_stays_as_an_option(self):
        current = self.cache_current_cover()
        older = self.media("movievault_v2")
        box = self.container(older)
        self.refresh(box)
        self.assertEqual(self.primary(box), current)
        self.assertEqual(self.links(box), 2, "the replaced cover stays in the gallery")

    def test_a_provider_cover_is_replaced(self):
        current = self.cache_current_cover()
        box = self.container(self.media("tmdb", source_url="https://image.tmdb.org/t/p/original/old.jpg"))
        self.refresh(box)
        self.assertEqual(self.primary(box), current)

    def test_a_client_pushed_cover_is_replaced(self):
        current = self.cache_current_cover()
        box = self.container(self.media("client", source_url="https://example.invalid/pushed.jpg"))
        self.refresh(box)
        self.assertEqual(self.primary(box), current)

    def test_the_users_own_choice_is_never_replaced(self):
        self.cache_current_cover()
        chosen = self.media("movievault_v2", metadata={"userSelectedPrimary": True})
        box = self.container(chosen)
        self.refresh(box)
        self.assertEqual(self.primary(box), chosen)

    def test_an_uploaded_cover_is_never_replaced(self):
        self.cache_current_cover()
        uploaded = self.media("upload", metadata={"source": "upload"})
        box = self.container(uploaded)
        self.refresh(box)
        self.assertEqual(self.primary(box), uploaded)

    def test_a_locked_poster_is_never_replaced(self):
        self.cache_current_cover()
        older = self.media("movievault_v2")
        box = self.container(older, metadata={"poster_locked": True})
        self.refresh(box, metadata={"poster_locked": True})
        self.assertEqual(self.primary(box), older)

    def test_a_cover_the_user_hid_is_not_put_back(self):
        current = self.cache_current_cover()
        older = self.media("movievault_v2")
        # The user hid the catalogue's current cover; the box shows an older one.
        box = self.container(older, hidden=current)
        self.refresh(box)
        self.assertEqual(self.primary(box), older)

    def test_a_dry_run_writes_nothing(self):
        self.cache_current_cover()
        older = self.media("movievault_v2")
        box = self.container(older)
        self.refresh(box, write=False)
        self.assertEqual(self.primary(box), older)
        self.assertEqual(self.links(box), 1)

    def test_a_cover_that_is_not_cached_yet_changes_nothing(self):
        # No cache row: the cover is pending, so the box keeps what it shows until a refresh finds it.
        older = self.media("movievault_v2")
        box = self.container(older)
        self.refresh(box)
        self.assertEqual(self.primary(box), older)

    def test_refreshing_twice_changes_nothing(self):
        current = self.cache_current_cover()
        box = self.container(self.media("tmdb", source_url="https://image.tmdb.org/t/p/original/old.jpg"))
        self.refresh(box)
        links_after_first = self.links(box)
        self.refresh(box)
        self.assertEqual(self.primary(box), current)
        self.assertEqual(self.links(box), links_after_first)

    def test_a_cover_the_plugin_sends_follows_the_same_rule(self):
        """The plugin half of the box refresh: the box record's cover arrives as the instance's own path."""
        current = self.cache_current_cover()
        older = self.media("tmdb", source_url="https://image.tmdb.org/t/p/original/old.jpg")
        box = self.container(older)
        artwork = {
            "poster_url": f"/api/next/movievault-v2/posters/{current}",
            "provider_id": "movievault_v2",
            "backdrop_url": "",
            "backdrop_urls": [],
        }
        with mock.patch.object(next_app, "fetch_box_set_artwork_from_sources", return_value=artwork):
            dry = next_app.refresh_container_metadata(self.conn, box, dry_run=True)
            self.assertEqual(self.primary(box), older, "a dry run changes nothing")
            self.assertTrue(dry["dryRun"])
            next_app.refresh_container_metadata(self.conn, box, dry_run=False)
        self.assertEqual(self.primary(box), current)

    def test_a_locked_poster_keeps_its_cover_when_the_plugin_sends_one(self):
        current = self.cache_current_cover()
        older = self.media("movievault_v2")
        box = self.container(older, metadata={"poster_locked": True})
        artwork = {
            "poster_url": f"/api/next/movievault-v2/posters/{current}",
            "provider_id": "movievault_v2",
            "backdrop_url": "",
            "backdrop_urls": [],
        }
        with mock.patch.object(next_app, "fetch_box_set_artwork_from_sources", return_value=artwork):
            next_app.refresh_container_metadata(self.conn, box, dry_run=False)
        self.assertEqual(self.primary(box), older)

    def test_another_providers_image_never_replaces_a_movievault_cover(self):
        current = self.cache_current_cover()
        box = self.container(current)
        next_app.link_container_media_option(
            self.conn,
            container_id=box,
            kind="poster",
            source_url="https://image.example/box-cover-test/other.jpg",
            provider_id="tmdb",
            sort_order=0,
            primary=True,
        )
        self.assertEqual(self.primary(box), current)


if __name__ == "__main__":
    unittest.main()
