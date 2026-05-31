import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_app import NextApiError
    from app.backend.next_app import container_payload
    from app.backend.next_app import container_detail_image
    from app.backend.next_app import legacy_media_public_url
    from app.backend.next_app import normalize_container_type
    from app.backend.next_app import with_preview_media_urls
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name != "flask":
        raise
    NextApiError = None
    container_payload = None
    container_detail_image = None
    legacy_media_public_url = None
    normalize_container_type = None
    with_preview_media_urls = None


@unittest.skipIf(container_payload is None, "Flask is not installed in this test environment")
class NextContainerPolicyTests(unittest.TestCase):
    def test_normalizes_supported_container_types(self):
        self.assertEqual(normalize_container_type("box-set"), "box_set")
        self.assertEqual(normalize_container_type("collection"), "collection")
        self.assertEqual(normalize_container_type("Vault"), "vault")

    def test_rejects_unknown_container_types(self):
        with self.assertRaises(NextApiError):
            normalize_container_type("playlist")

    def test_container_payload_allows_optional_fields_to_be_cleared(self):
        payload = container_payload(
            {
                "title": "Updated",
                "description": "",
                "year": "",
                "barcode": "",
                "badgeLabel": "",
            },
            existing={
                "title": "Existing",
                "description": "Old",
                "year": "2026",
                "barcode": "123",
                "badge_label": "Old badge",
                "metadata": {"sync_revision": 1},
            },
        )

        self.assertEqual(payload["title"], "Updated")
        self.assertIsNone(payload["description"])
        self.assertIsNone(payload["year"])
        self.assertIsNone(payload["barcode"])
        self.assertIsNone(payload["badge_label"])
        self.assertEqual(payload["metadata"], {"sync_revision": 1})

    def test_preview_media_urls_falls_back_to_container_metadata_artwork(self):
        row = {
            "id": "container-1",
            "metadata": {
                "poster_url": "https://image.example/poster.jpg",
                "backdrop_url": "https://image.example/backdrop.jpg",
            },
            "poster_asset_id": None,
            "poster_asset_storage_backend": None,
            "poster_asset_storage_key": None,
            "poster_asset_source_url": None,
            "backdrop_asset_id": None,
            "backdrop_asset_storage_backend": None,
            "backdrop_asset_storage_key": None,
            "backdrop_asset_source_url": None,
        }

        preview = with_preview_media_urls(row)

        self.assertEqual(preview["poster_url"], "https://image.example/poster.jpg")
        self.assertEqual(preview["backdrop_url"], "https://image.example/backdrop.jpg")

    def test_preview_media_urls_falls_back_to_legacy_container_poster_file(self):
        import tempfile
        from pathlib import Path

        old_data_dir = os.environ.get("DISCVAULT_LEGACY_DATA_DIR")
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            poster_dir = data_dir / "posters"
            poster_dir.mkdir()
            (poster_dir / "box.jpg").write_bytes(b"poster")
            os.environ["DISCVAULT_LEGACY_DATA_DIR"] = str(data_dir)
            try:
                row = {
                    "id": "container-1",
                    "metadata": {"poster_file": "box.jpg"},
                    "poster_asset_id": None,
                    "poster_asset_storage_backend": None,
                    "poster_asset_storage_key": None,
                    "poster_asset_source_url": None,
                    "backdrop_asset_id": None,
                    "backdrop_asset_storage_backend": None,
                    "backdrop_asset_storage_key": None,
                    "backdrop_asset_source_url": None,
                }

                preview = with_preview_media_urls(row)

                self.assertEqual(preview["poster_url"], "/api/next/media/legacy/poster/posters/box.jpg")
                self.assertEqual(
                    container_detail_image([], {"poster_file": "box.jpg"}, "poster"),
                    "/api/next/media/legacy/poster/posters/box.jpg",
                )
                self.assertEqual(
                    legacy_media_public_url("poster", "box.jpg"),
                    "/api/next/media/legacy/poster/posters/box.jpg",
                )
            finally:
                if old_data_dir is None:
                    os.environ.pop("DISCVAULT_LEGACY_DATA_DIR", None)
                else:
                    os.environ["DISCVAULT_LEGACY_DATA_DIR"] = old_data_dir


if __name__ == "__main__":
    unittest.main()
