"""A MovieVault poster must resolve to the route that will serve it.

`/api/next/media/assets/<id>` deliberately refuses a MovieVault v2 poster: those
keep their own lifecycle (a cache status the dedicated route checks, and
`no-store` rather than a revalidating cache header). A box-set links its
MovieVault cover into `entity_media` like any other artwork, so every reader of
that link has to arrive at `/api/next/movievault-v2/posters/<id>` instead --
otherwise the refresh that fetched the cover leaves the page rendering a broken
image against a 404.

These tests pin the decision at `media_asset_public_url` and at the two folders
that feed it partial dicts built from joined columns, because that is where the
provider gets dropped if a query forgets to select it.
"""

from __future__ import annotations

import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_app


ASSET_ID = "3f1d2c7e-5a4b-4c3d-9e8f-1a2b3c4d5e6f"


def _local_poster(**overrides):
    asset = {
        "id": ASSET_ID,
        "kind": "poster",
        "storage_backend": "local",
        "storage_key": "posters/godfather-trilogy.jpg",
        "source_url": "https://movievault.example/assets/original.jpg",
        "provider_id": "tmdb",
    }
    asset.update(overrides)
    return asset


class MediaAssetPublicUrlTests(unittest.TestCase):
    def test_movievault_poster_uses_its_own_route(self):
        url = next_app.media_asset_public_url(
            _local_poster(provider_id="movievault_v2:box-set-asset-1")
        )
        self.assertEqual(url, f"/api/next/movievault-v2/posters/{ASSET_ID}")

    def test_ordinary_local_poster_keeps_the_generic_route(self):
        url = next_app.media_asset_public_url(_local_poster())
        self.assertEqual(url, f"/api/next/media/assets/{ASSET_ID}")

    def test_backdrop_from_movievault_is_not_a_poster_route(self):
        """The predicate is poster-only: only posters have the v4 cache."""
        url = next_app.media_asset_public_url(
            _local_poster(kind="backdrop", provider_id="movievault_v2:box-set-asset-1")
        )
        self.assertEqual(url, f"/api/next/media/assets/{ASSET_ID}")

    def test_remote_asset_still_falls_back_to_its_source_url(self):
        url = next_app.media_asset_public_url(
            _local_poster(
                provider_id="movievault_v2:box-set-asset-1",
                storage_backend="remote",
                storage_key="remote/posters/godfather.jpg",
            )
        )
        self.assertEqual(url, "https://movievault.example/assets/original.jpg")


class ContainerListRowTests(unittest.TestCase):
    """The library tile reads the same asset through joined columns."""

    def test_movievault_poster_column_folds_to_its_own_route(self):
        item = next_app.container_list_row(
            {
                "id": "container-1",
                "title": "The Godfather Trilogy",
                "poster_asset_id": ASSET_ID,
                "poster_storage_backend": "local",
                "poster_storage_key": "posters/godfather-trilogy.jpg",
                "poster_source_url": None,
                "poster_provider_id": "movievault_v2:box-set-asset-1",
            }
        )
        self.assertEqual(item["poster_url"], f"/api/next/movievault-v2/posters/{ASSET_ID}")
        self.assertNotIn("poster_provider_id", item)

    def test_ordinary_poster_column_keeps_the_generic_route(self):
        item = next_app.container_list_row(
            {
                "id": "container-1",
                "poster_asset_id": ASSET_ID,
                "poster_storage_backend": "local",
                "poster_storage_key": "posters/godfather-trilogy.jpg",
                "poster_source_url": None,
                "poster_provider_id": "sync",
            }
        )
        self.assertEqual(item["poster_url"], f"/api/next/media/assets/{ASSET_ID}")

    def test_query_without_the_provider_column_still_resolves(self):
        """A row from a query that never selected the provider must not raise;
        it simply reads as an ordinary asset, which is right for every source
        except MovieVault."""
        item = next_app.container_list_row(
            {
                "id": "container-1",
                "poster_asset_id": ASSET_ID,
                "poster_storage_backend": "local",
                "poster_storage_key": "posters/godfather-trilogy.jpg",
                "poster_source_url": None,
            }
        )
        self.assertEqual(item["poster_url"], f"/api/next/media/assets/{ASSET_ID}")


class PreviewMediaUrlTests(unittest.TestCase):
    """The container preview folds `<kind>_asset_*` columns the same way."""

    def test_movievault_poster_preview_uses_its_own_route(self):
        data = next_app.with_preview_media_urls(
            {
                "id": "container-1",
                "metadata": {},
                "poster_asset_id": ASSET_ID,
                "poster_asset_storage_backend": "local",
                "poster_asset_storage_key": "posters/godfather-trilogy.jpg",
                "poster_asset_source_url": None,
                "poster_asset_provider_id": "movievault_v2:box-set-asset-1",
            }
        )
        self.assertEqual(data["poster_url"], f"/api/next/movievault-v2/posters/{ASSET_ID}")
        self.assertNotIn("poster_asset_provider_id", data)

    def test_backdrop_columns_are_unaffected(self):
        data = next_app.with_preview_media_urls(
            {
                "id": "container-1",
                "metadata": {},
                "backdrop_asset_id": ASSET_ID,
                "backdrop_asset_storage_backend": "local",
                "backdrop_asset_storage_key": "posters/_variants/backdrop/godfather.jpg",
                "backdrop_asset_source_url": None,
                "backdrop_asset_provider_id": "movievault_v2:box-set-asset-1",
            }
        )
        self.assertEqual(data["backdrop_url"], f"/api/next/media/assets/{ASSET_ID}")


class EntityMediaUrlContractTests(unittest.TestCase):
    """The detail page reads full media_assets rows, which already carry both
    fields -- this pins that the predicate reads them under their real names."""

    def test_full_row_shape_is_understood(self):
        row = {
            "id": ASSET_ID,
            "kind": "poster",
            "variant": "display",
            "storage_backend": "local",
            "storage_key": "posters/godfather-trilogy.jpg",
            "source_url": None,
            "provider_id": "movievault_v2:box-set-asset-1",
            "is_primary": True,
        }
        self.assertTrue(next_app.is_movievault_v2_poster_media_asset(row))
        self.assertEqual(
            next_app.media_asset_public_url(row),
            f"/api/next/movievault-v2/posters/{ASSET_ID}",
        )


if __name__ == "__main__":
    unittest.main()
