import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_metadata import canonicalize_plugin_result
from app.backend.next_metadata import external_metadata_barcode
from app.backend.next_metadata import merge_metadata_results
from app.backend.next_metadata import normalize_media_format


class NextMetadataPolicyTests(unittest.TestCase):
    def test_release_specs_do_not_upgrade_across_formats(self):
        current = {
            "title": "Example",
            "format": "Blu-ray",
            "metadata": {},
        }
        technical = {
            "audio_tracks": ["English DTS-HD MA 5.1"],
            "subtitles": ["English"],
        }
        result = canonicalize_plugin_result(
            "bluray_com",
            "technical_specs",
            {
                "status": "hit",
                "sourceLabel": "Blu-ray.com",
                "technicalSpecs": {
                    "format": "4K UHD",
                    "audioTracks": ["English Dolby Atmos"],
                    "subtitles": ["English SDH"],
                    "hdr": "HDR10",
                },
            },
        )
        merged = merge_metadata_results(
            current=current,
            technical_current=technical,
            results=[result],
            overwrite_enabled=True,
            target_format="Blu-ray",
        )

        self.assertEqual(merged["technicalUpdates"], {})
        self.assertTrue(any("format mismatch" in item["reason"] for item in merged["skipped"]))

    def test_same_format_release_specs_may_refresh_technical_fields(self):
        current = {"title": "Example", "format": "DVD", "metadata": {}}
        technical = {"audio_tracks": ["English Dolby Digital 2.0"]}
        result = canonicalize_plugin_result(
            "bluray_com",
            "technical_specs",
            {
                "status": "hit",
                "sourceLabel": "Blu-ray.com",
                "technicalSpecs": {
                    "format": "DVD",
                    "audioTracks": ["English Dolby Digital 5.1"],
                    "subtitles": ["Dutch"],
                },
            },
        )
        merged = merge_metadata_results(
            current=current,
            technical_current=technical,
            results=[result],
            overwrite_enabled=False,
            target_format="DVD",
        )

        self.assertEqual(merged["technicalUpdates"]["audio_tracks"], ["English Dolby Digital 5.1"])
        self.assertEqual(merged["technicalUpdates"]["subtitles"], ["Dutch"])

    def test_manual_fields_are_protected_without_preferred_overwrite(self):
        current = {
            "title": "Manual Title",
            "overview": "Manual overview",
            "format": "4K UHD",
            "metadata": {"poster_url": "https://local/poster.jpg"},
        }
        result = canonicalize_plugin_result(
            "tmdb",
            "movie_details",
            {
                "status": "hit",
                "movie": {
                    "title": "Provider Title",
                    "overview": "Provider overview",
                    "posterUrl": "https://provider/poster.jpg",
                    "rating": "8.1",
                },
            },
        )
        merged = merge_metadata_results(
            current=current,
            technical_current={},
            results=[result],
            overwrite_enabled=False,
            target_format="4K UHD",
        )

        self.assertNotIn("title", merged["movieUpdates"])
        self.assertNotIn("overview", merged["movieUpdates"])
        self.assertNotIn("poster_url", merged["metadataUpdates"])
        self.assertEqual(merged["movieUpdates"]["rating"], "8.1")

    def test_preferred_overwrite_allows_provider_to_replace_display_fields(self):
        current = {
            "title": "Manual Title",
            "overview": "Manual overview",
            "format": "4K UHD",
            "metadata": {"poster_url": "https://local/poster.jpg"},
        }
        result = canonicalize_plugin_result(
            "tmdb",
            "movie_details",
            {
                "status": "hit",
                "movie": {
                    "title": "Provider Title",
                    "overview": "Provider overview",
                    "posterUrl": "https://provider/poster.jpg",
                },
            },
        )
        merged = merge_metadata_results(
            current=current,
            technical_current={},
            results=[result],
            overwrite_enabled=True,
            target_format="4K UHD",
        )

        self.assertEqual(merged["movieUpdates"]["title"], "Provider Title")
        self.assertEqual(merged["movieUpdates"]["overview"], "Provider overview")
        self.assertEqual(merged["metadataUpdates"]["poster_url"], "https://provider/poster.jpg")

    def test_synthetic_barcodes_are_not_sent_to_external_sources(self):
        self.assertEqual(external_metadata_barcode("IMPORT-BACK_TO_THE_FUTURE-1985"), "")
        self.assertEqual(external_metadata_barcode("032429316110-BOX-01"), "")
        self.assertEqual(external_metadata_barcode("8717418557683"), "8717418557683")

    def test_media_format_normalization(self):
        self.assertEqual(normalize_media_format("Ultra HD Blu-ray"), "4K UHD")
        self.assertEqual(normalize_media_format("Blu ray"), "Blu-ray")
        self.assertEqual(normalize_media_format("DVD Video"), "DVD")

    def test_identifier_list_and_source_format_are_normalized(self):
        result = canonicalize_plugin_result(
            "movievault",
            "movie_details",
            {
                "sourceFormat": "Ultra HD Blu-ray",
                "identifiers": [
                    {"provider_id": "tmdb", "identifier_type": "movie_id", "identifier": "123"},
                    {"provider": "imdb", "identifierType": "movie_id", "value": "tt1234567"},
                ],
            },
        )

        self.assertEqual(result["normalizedSourceFormat"], "4K UHD")
        self.assertEqual(result["identifiers"], {"tmdb": "123", "imdb": "tt1234567"})


if __name__ == "__main__":
    unittest.main()
