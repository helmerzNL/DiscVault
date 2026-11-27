"""A film's MovieVault cover, against a real database: which cover, who may replace it, who may not.

`test_next_movievault_poster_precedence` holds the rules a function can hold. These hold the ones that
live in SQL and in two connections' worth of state: which release's cover a title borrows (the order of
"the first other release of the film"), that a cover not cached yet is *pending* rather than a different
release's, that a cover is linked by its media asset id, and what a refresh may and may not replace.

The case that made the last one necessary: a title that shows a MovieVault cover was refreshed once with
MovieVault silent (its cover not cached yet, or withdrawn) and TMDB answering, and the cinema poster took
the primary place. The artwork rule puts MovieVault first and lets another provider stand in only where
MovieVault has nothing; a refresh in which MovieVault has nothing says nothing about a cover the title
already shows. A fake cursor cannot show that, because the answer is which row `entity_media` ends up
holding as primary.

Skipped without ``DATABASE_URL``, like the other ``*_postgres`` suites.
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
except ModuleNotFoundError:  # pragma: no cover - depends on environment
    psycopg = None
    dict_row = None
    Jsonb = None

from app.backend import next_metadata as md
from app.backend import next_movievault_v2_artwork as art


DATABASE_URL = os.environ.get("DATABASE_URL")
PREFIX = "poster-precedence-test"


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class MovieVaultPosterPostgresTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def setUp(self):
        self.tag = uuid.uuid4().hex[:12]
        self.generation = uuid.uuid4()
        self.film = uuid.uuid4()
        self.movie_ids: list[uuid.UUID] = []
        self.release_ids: list[uuid.UUID] = []
        self.asset_ids: list[str] = []
        self.media_ids: list[uuid.UUID] = []
        self.conn = self.connect()
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM movievault_v2_sync_state WHERE plugin_id='movievault_v2'")
            self.saved_state = cur.fetchone()
            cur.execute(
                """
                INSERT INTO movievault_v2_sync_state (plugin_id, origin, revision, status, active_generation)
                VALUES ('movievault_v2', 'https://api.discvault.eu', 1, 'current', %s)
                ON CONFLICT (plugin_id) DO UPDATE SET active_generation = EXCLUDED.active_generation
                """,
                (self.generation,),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.rollback()
        with self.conn.cursor() as cur:
            if self.movie_ids:
                cur.execute("DELETE FROM entity_media WHERE entity_type='movie' AND entity_id = ANY(%s)", (self.movie_ids,))
                cur.execute("DELETE FROM movies WHERE id = ANY(%s)", (self.movie_ids,))
            if self.asset_ids:
                cur.execute("DELETE FROM movievault_v2_poster_cache WHERE asset_id = ANY(%s)", (self.asset_ids,))
            cur.execute("DELETE FROM movievault_v2_releases WHERE generation=%s", (self.generation,))
            cur.execute(
                "DELETE FROM media_assets WHERE storage_key LIKE %s OR source_url LIKE %s",
                (f"{PREFIX}/{self.tag}/%", f"https://image.example/{PREFIX}/{self.tag}/%"),
            )
            if self.saved_state is None:
                cur.execute("DELETE FROM movievault_v2_sync_state WHERE plugin_id='movievault_v2'")
            else:
                cur.execute(
                    "UPDATE movievault_v2_sync_state SET active_generation=%s WHERE plugin_id='movievault_v2'",
                    (self.saved_state["active_generation"],),
                )
        self.conn.commit()
        self.conn.close()

    # -- the world -------------------------------------------------------------------------------

    def release(self, revision, *, cover=None):
        rid = uuid.uuid4()
        self.release_ids.append(rid)
        poster = None
        if cover:
            asset = f"{PREFIX}-{self.tag}-{cover}"
            poster = {"assetId": asset, "display": {"checksum": f"sum-{asset}", "path": f"/v2/assets/{asset}/display"}}
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movievault_v2_releases (generation, release_id, film_id, canonical_title, release_title, revision, poster)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (self.generation, rid, self.film, f"{PREFIX} {self.tag}", f"{PREFIX} {self.tag}", revision,
                 Jsonb(poster) if poster else None),
            )
        self.conn.commit()
        return rid

    def cache(self, cover):
        asset = f"{PREFIX}-{self.tag}-{cover}"
        self.asset_ids.append(asset)
        media_id = uuid.uuid4()
        self.media_ids.append(media_id)
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO media_assets (id, kind, variant, storage_backend, storage_key, sha256, provider_id)
                VALUES (%s, 'poster', 'display', 'local', %s, %s, 'movievault_v2')
                """,
                (media_id, f"{PREFIX}/{self.tag}/{cover}", f"sum-{asset}"),
            )
            cur.execute(
                """
                INSERT INTO movievault_v2_poster_cache (asset_id, variant, checksum, status, media_asset_id)
                VALUES (%s, 'display', %s, 'ready', %s)
                """,
                (asset, f"sum-{asset}", media_id),
            )
        self.conn.commit()
        return media_id

    def movie(self):
        movie_id = uuid.uuid4()
        self.movie_ids.append(movie_id)
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO movies (id, public_id, title) VALUES (%s, %s, %s)",
                (movie_id, f"{PREFIX}-{movie_id}", f"{PREFIX} {self.tag}"),
            )
        self.conn.commit()
        return movie_id

    def tmdb_url(self, name):
        return f"https://image.example/{PREFIX}/{self.tag}/{name}.jpg"

    def apply(self, movie_id, source_url, provider_id):
        out = md.apply_primary_media_update(
            self.conn, movie_id=movie_id, kind="poster", source_url=source_url, provider_id=provider_id
        )
        self.conn.commit()
        return out

    def primary(self, movie_id):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT ma.id, ma.provider_id
                FROM entity_media em JOIN media_assets ma ON ma.id = em.media_id
                WHERE em.entity_type='movie' AND em.entity_id=%s AND em.role='poster'
                  AND em.is_primary AND em.deleted_at IS NULL AND em.hidden_at IS NULL
                """,
                (movie_id,),
            )
            row = cur.fetchone()
        return (row["id"], row["provider_id"]) if row else None

    def links(self, movie_id):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM entity_media WHERE entity_type='movie' AND entity_id=%s AND role='poster' AND deleted_at IS NULL",
                (movie_id,),
            )
            return cur.fetchone()["n"]

    # -- which cover ---------------------------------------------------------------------------------

    def test_the_first_other_release_by_revision_is_chosen_and_pending_is_not_skipped(self):
        own = self.release(100)
        first = self.release(150, cover="first")   # lower revision: chosen, whether cached or not
        self.release(200, cover="second")
        self.cache("second")                       # the later one is cached, the first is not
        self.assertIsNone(
            art.film_poster_for_release(self.conn, own),
            "a different answer today than tomorrow is what the order exists to prevent",
        )
        first_media = self.cache("first")
        got = art.film_poster_for_release(self.conn, own)
        self.assertEqual((got["mediaAssetId"], got["releaseId"], got["source"]), (str(first_media), str(first), "film"))

    def test_ties_on_revision_go_to_the_lower_release_id(self):
        own = self.release(100)
        a, b = sorted((uuid.uuid4(), uuid.uuid4()))
        for rid, cover in ((b, "b"), (a, "a")):
            self.release_ids.append(rid)
            asset = f"{PREFIX}-{self.tag}-{cover}"
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO movievault_v2_releases (generation, release_id, film_id, canonical_title, release_title, revision, poster)
                    VALUES (%s, %s, %s, 'tie', 'tie', 150, %s)
                    """,
                    (self.generation, rid, self.film,
                     Jsonb({"assetId": asset, "display": {"checksum": f"sum-{asset}"}})),
                )
            self.conn.commit()
        a_media, _ = self.cache("a"), self.cache("b")
        got = art.film_poster_for_release(self.conn, own)
        self.assertEqual((got["mediaAssetId"], got["releaseId"]), (str(a_media), str(a)))

    def test_a_release_with_its_own_cached_cover_uses_it_and_never_borrows(self):
        with_cover = self.release(300, cover="own")
        self.release(100, cover="older")
        self.cache("older")
        own_media = self.cache("own")
        got = art.film_poster_for_release(self.conn, with_cover)
        self.assertEqual((got["mediaAssetId"], got["source"]), (str(own_media), "release"))

    def test_an_own_cover_that_is_not_cached_is_pending_not_replaced_by_a_sibling(self):
        with_cover = self.release(300, cover="own")   # not cached
        self.release(100, cover="older")
        self.cache("older")
        self.assertIsNone(art.film_poster_for_release(self.conn, with_cover))

    def test_nothing_is_borrowed_from_another_film_or_without_an_active_generation(self):
        own = self.release(100)
        other_film = uuid.uuid4()
        rid = uuid.uuid4()
        self.release_ids.append(rid)
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movievault_v2_releases (generation, release_id, film_id, canonical_title, release_title, revision, poster)
                VALUES (%s, %s, %s, 'elsewhere', 'elsewhere', 50, %s)
                """,
                (self.generation, rid, other_film,
                 Jsonb({"assetId": f"{PREFIX}-{self.tag}-x", "display": {"checksum": f"sum-{PREFIX}-{self.tag}-x"}})),
            )
        self.conn.commit()
        self.cache("x")
        self.assertIsNone(art.film_poster_for_release(self.conn, own))
        with self.conn.cursor() as cur:
            cur.execute("UPDATE movievault_v2_sync_state SET active_generation=%s WHERE plugin_id='movievault_v2'", (uuid.uuid4(),))
        self.conn.commit()
        self.assertIsNone(art.film_poster_for_release(self.conn, own))

    # -- who may replace what ---------------------------------------------------------------------

    def test_a_movievault_cover_replaces_a_provider_poster_which_stays_an_option(self):
        movie = self.movie()
        cover = self.cache("a")
        self.apply(movie, self.tmdb_url("tmdb"), "tmdb")
        self.assertEqual(self.primary(movie)[1], "tmdb")
        out = self.apply(movie, art.local_poster_url(cover), "movievault_v2")
        self.assertEqual(self.primary(movie), (cover, "movievault_v2"))
        self.assertEqual(out["mediaId"], str(cover))
        self.assertEqual(self.links(movie), 2)
        self.assertIsNone(self.apply(movie, art.local_poster_url(cover), "movievault_v2"), "applying it again changes nothing")

    def test_another_provider_never_replaces_a_movievault_cover(self):
        # The refresh in which MovieVault is silent: its cover is pending, withdrawn or unreachable.
        movie = self.movie()
        cover = self.cache("a")
        self.apply(movie, art.local_poster_url(cover), "movievault_v2")
        out = self.apply(movie, self.tmdb_url("tmdb"), "tmdb")
        self.assertEqual(self.primary(movie), (cover, "movievault_v2"))
        self.assertEqual(out["keptPrimary"], "movievault_v2")
        self.assertEqual(self.links(movie), 2, "the provider's poster is kept as an option")

    def test_a_newer_movievault_cover_replaces_an_older_one(self):
        movie = self.movie()
        old, new = self.cache("old"), self.cache("new")
        self.apply(movie, art.local_poster_url(old), "movievault_v2")
        self.apply(movie, art.local_poster_url(new), "movievault_v2")
        self.assertEqual(self.primary(movie), (new, "movievault_v2"))

    def test_a_primary_the_user_selected_is_not_replaced_by_anyone(self):
        movie = self.movie()
        picked, cover = self.cache("picked"), self.cache("cover")
        self.apply(movie, art.local_poster_url(picked), "movievault_v2")
        with self.conn.cursor() as cur:
            cur.execute("UPDATE media_assets SET metadata = metadata || '{\"userSelectedPrimary\": true}'::jsonb WHERE id=%s", (picked,))
        self.conn.commit()
        for url, provider in ((art.local_poster_url(cover), "movievault_v2"), (self.tmdb_url("tmdb"), "tmdb")):
            out = self.apply(movie, url, provider)
            self.assertEqual(self.primary(movie), (picked, "movievault_v2"), provider)
            self.assertTrue(out.get("lockedPrimary"), provider)

    def test_a_user_selected_provider_poster_is_not_replaced_by_a_movievault_cover(self):
        movie = self.movie()
        cover = self.cache("cover")
        self.apply(movie, self.tmdb_url("mine"), "tmdb")
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE media_assets SET metadata = metadata || '{\"userSelectedPrimary\": true}'::jsonb WHERE source_url=%s",
                (self.tmdb_url("mine"),),
            )
        self.conn.commit()
        self.apply(movie, art.local_poster_url(cover), "movievault_v2")
        self.assertEqual(self.primary(movie)[1], "tmdb")

    def test_a_cover_the_user_hid_is_not_put_back(self):
        movie = self.movie()
        cover = self.cache("a")
        self.apply(movie, art.local_poster_url(cover), "movievault_v2")
        with self.conn.cursor() as cur:
            cur.execute("UPDATE entity_media SET hidden_at=now(), is_primary=false WHERE entity_id=%s AND media_id=%s", (movie, cover))
        self.conn.commit()
        self.assertIsNone(self.apply(movie, art.local_poster_url(cover), "movievault_v2"))
        self.assertIsNone(self.primary(movie))
        _, media_updates = md.filter_hidden_artwork_updates(
            self.conn, movie, {}, {"poster": {"sourceUrl": art.local_poster_url(cover)}}
        )
        self.assertNotIn("poster", media_updates)

    def test_an_address_that_names_no_media_asset_links_nothing(self):
        self.assertIsNone(md.local_media_asset_for_poster_url(self.conn, art.local_poster_url(uuid.uuid4()), "poster"))
        movie = self.movie()
        self.assertIsNone(self.apply(movie, art.local_poster_url(uuid.uuid4()), "movievault_v2"))
        self.assertIsNone(self.primary(movie))


if __name__ == "__main__":
    unittest.main()
