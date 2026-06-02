import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_metadata import canonicalize_plugin_result
from app.backend.next_metadata import external_metadata_barcode
from app.backend.next_metadata import metadata_fetch_audit_payload
from app.backend.next_metadata import merge_metadata_results
from app.backend.next_metadata import normalize_media_format
from app.backend.next_metadata import plugin_execution_plan
from app.backend.next_metadata import query_from_payload
from app.backend.next_metadata import receiver_contribution_payload
from app.backend.next_metadata import summarize_metadata_execution


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
        self.assertEqual(merged["mediaUpdates"]["poster"]["sourceUrl"], "https://provider/poster.jpg")
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
        self.assertEqual(merged["mediaUpdates"]["poster"]["sourceUrl"], "https://provider/poster.jpg")

    def test_provider_image_options_are_kept_as_media_choices(self):
        current = {"title": "Manual Title", "format": "4K UHD", "metadata": {"poster_url": "https://local/poster.jpg"}}
        result = canonicalize_plugin_result(
            "tmdb",
            "movie_details",
            {
                "status": "hit",
                "movie": {
                    "posterUrl": "https://provider/poster-main.jpg",
                    "posters": [
                        "https://provider/poster-main.jpg",
                        "https://provider/poster-alt.jpg",
                    ],
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

        self.assertNotIn("poster_url", merged["metadataUpdates"])
        self.assertEqual(merged["mediaUpdates"]["poster"]["sourceUrl"], "https://provider/poster-main.jpg")
        self.assertEqual(
            merged["mediaUpdates"]["poster"]["options"],
            ["https://provider/poster-main.jpg", "https://provider/poster-alt.jpg"],
        )

    def test_synthetic_barcodes_are_not_sent_to_external_sources(self):
        self.assertEqual(external_metadata_barcode("IMPORT-BACK_TO_THE_FUTURE-1985"), "")
        self.assertEqual(external_metadata_barcode("032429316110-BOX-01"), "")
        self.assertEqual(external_metadata_barcode("8717418557683"), "8717418557683")

    def test_import_lookup_can_request_box_set_candidates(self):
        query = query_from_payload({"barcode": "5051892000000", "detectBoxSets": True})
        plan = plugin_execution_plan(
            {"capabilities": ["search_barcode", "movie_details", "box_set_candidates"]},
            query,
        )

        self.assertIn("box_set_candidates", [item["entrypoint"] for item in plan])

    def test_preview_lookup_uses_fast_barcode_plan(self):
        query = query_from_payload({"barcode": "5051892000000", "detectBoxSets": True, "previewMode": True})
        plan = plugin_execution_plan(
            {"capabilities": ["search_barcode", "movie_details", "box_set_candidates"]},
            query,
        )

        self.assertEqual([item["entrypoint"] for item in plan], ["search_barcode"])

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

    def test_technical_list_strings_are_split_and_deduped(self):
        result = canonicalize_plugin_result(
            "bluray_com",
            "technical_specs",
            {
                "technicalSpecs": {
                    "format": "4K UHD",
                    "audioTracks": "English: Dolby TrueHD 7.1 (48kHz, 24-bit), Spanish: Dolby Digital 5.1",
                    "subtitles": ["English SDH, French, Japanese, Spanish", "French"],
                    "regions": "A, B, C",
                },
            },
        )

        self.assertEqual(
            result["technicalUpdates"]["audio_tracks"],
            ["English: Dolby TrueHD 7.1 (48kHz, 24-bit)", "Spanish: Dolby Digital 5.1"],
        )
        self.assertEqual(result["technicalUpdates"]["subtitles"], ["English SDH", "French", "Japanese", "Spanish"])
        self.assertEqual(result["technicalUpdates"]["regions"], ["A", "B", "C"])

    def test_audio_track_codec_comma_is_preserved_without_parentheses(self):
        result = canonicalize_plugin_result(
            "bluray_com",
            "technical_specs",
            {
                "technicalSpecs": {
                    "format": "4K UHD",
                    "audioTracks": "English: Dolby TrueHD 7.1 48kHz, 24-bit, Spanish: Dolby Digital 5.1",
                },
            },
        )

        self.assertEqual(
            result["technicalUpdates"]["audio_tracks"],
            ["English: Dolby TrueHD 7.1 48kHz, 24-bit", "Spanish: Dolby Digital 5.1"],
        )

    def test_execution_summary_marks_format_blocked_and_applied_sources(self):
        plugins = [
            {"id": "tmdb", "name": "TMDb", "order_index": 10},
            {"id": "bluray_com", "name": "Blu-ray.com", "order_index": 20},
        ]
        executions = [
            {"pluginId": "tmdb", "entrypoint": "lookup_external_id", "status": "skipped", "state": "needs_configuration"},
            {"pluginId": "bluray_com", "entrypoint": "technical_specs", "status": "ok", "resultStatus": "hit", "elapsedMs": 20},
        ]
        proposal = {
            "provenance": [{"pluginId": "bluray_com", "field": "audio_tracks"}],
            "skipped": [{"pluginId": "bluray_com", "field": "hdr", "reason": "format mismatch: target=Blu-ray, source=4K UHD"}],
        }

        summary = summarize_metadata_execution(plugins=plugins, executions=executions, results=[], proposal=proposal)

        self.assertEqual(summary[0]["state"], "needs_configuration")
        self.assertEqual(summary[1]["state"], "applied")
        self.assertEqual(summary[1]["formatBlockedFields"], 1)

    def test_metadata_fetch_audit_payload_keeps_provider_field_details(self):
        movie = {"title": "Aladdin", "barcode": "8717418557683", "format": "4K UHD"}
        preview = {
            "sourceOrder": ["movievault_26", "tmdb"],
            "executions": [
                {
                    "pluginId": "movievault_26",
                    "entrypoint": "search_barcode",
                    "status": "ok",
                    "resultStatus": "hit",
                    "candidateCount": 1,
                    "elapsedMs": 50,
                }
            ],
            "results": [
                {
                    "pluginId": "movievault_26",
                    "sourceLabel": "MovieVault 26",
                    "entrypoint": "search_barcode",
                    "status": "hit",
                    "movieUpdates": {"rating": "7.1"},
                    "metadataUpdates": {"poster_url": "https://example/poster.jpg"},
                    "technicalUpdates": {"hdr": "HDR10"},
                    "mediaUpdates": {"poster": {"sourceUrl": "https://example/poster.jpg"}},
                    "identifiers": {"tmdb": "420817"},
                    "candidates": [{}],
                }
            ],
            "proposal": {
                "provenance": [{"pluginId": "movievault_26", "field": "rating", "target": "movie"}],
                "skipped": [{"pluginId": "tmdb", "field": "title", "reason": "existing value retained"}],
            },
            "proposalStats": {"acceptedFields": 1, "skippedFields": 1},
        }

        payload = metadata_fetch_audit_payload(
            movie_id="2b9e",
            movie=movie,
            dry_run=False,
            preview=preview,
            applied={"changed": True, "revision": 12, "applied": {"movieUpdates": {"rating": "7.1"}}},
        )

        self.assertEqual(payload["sourceOrder"], ["movievault_26", "tmdb"])
        self.assertEqual(payload["providerResults"][0]["pluginId"], "movievault_26")
        self.assertEqual(payload["providerResults"][0]["movieFields"], ["rating"])
        self.assertEqual(payload["providerResults"][0]["metadataFields"], ["poster_url"])
        self.assertEqual(payload["providerResults"][0]["technicalFields"], ["hdr"])
        self.assertEqual(payload["providerResults"][0]["mediaKinds"], ["poster"])
        self.assertEqual(payload["providerResults"][0]["identifierProviders"], ["tmdb"])
        self.assertNotIn("Authorization", str(payload))
        self.assertNotIn("apiToken", str(payload))

    def test_receiver_contribution_payload_uses_public_applied_metadata(self):
        movie = {
            "id": "2b9e",
            "public_id": "legacy-movie-95",
            "title": "Aladdin",
            "original_title": "Aladdin",
            "year": "2019",
            "barcode": "8717418557683",
            "format": "4K UHD",
        }
        preview = {
            "proposal": {
                "movieUpdates": {"rating": "7.1"},
                "metadataUpdates": {"genre": "Adventure"},
                "technicalUpdates": {"hdr": "HDR10"},
                "mediaUpdates": {"poster": {"sourceUrl": "https://example/poster.jpg"}},
                "identifiers": {"tmdb": "420817", "imdb": "tt6139732"},
                "provenance": [
                    {"pluginId": "tmdb", "field": "rating"},
                    {"pluginId": "bluray_com", "field": "hdr"},
                ],
            },
            "results": [
                {
                    "pluginId": "tmdb",
                    "sourceLabel": "TMDb",
                    "movieUpdates": {
                        "title": "Aladdin TMDb",
                        "original_title": "Aladdin Original TMDb",
                    },
                }
            ],
        }

        payload = receiver_contribution_payload(
            movie_id="2b9e",
            movie=movie,
            preview=preview,
            applied={"changed": True, "applied": {"movieUpdates": {"rating": "7.1"}}},
        )

        self.assertEqual(payload["entityType"], "movie")
        self.assertEqual(payload["identity"], "legacy-movie-95")
        self.assertEqual(payload["sourceReference"]["barcode"], "8717418557683")
        self.assertEqual(payload["payload"]["title"], "Aladdin")
        self.assertEqual(payload["payload"]["rating"], "7.1")
        self.assertEqual(payload["payload"]["hdr"], "HDR10")
        self.assertEqual(payload["payload"]["tmdbId"], "420817")
        self.assertEqual(payload["metadata"]["sourceProviders"], ["bluray_com", "tmdb"])
        self.assertEqual(payload["metadata"]["tmdbTitle"], "Aladdin TMDb")
        self.assertEqual(payload["metadata"]["tmdbOriginalTitle"], "Aladdin Original TMDb")
        self.assertEqual(
            payload["metadata"]["providerTitleHints"],
            [
                {
                    "pluginId": "tmdb",
                    "sourceLabel": "TMDb",
                    "title": "Aladdin TMDb",
                    "originalTitle": "Aladdin Original TMDb",
                }
            ],
        )
        self.assertNotIn("watchHistory", str(payload))
        self.assertNotIn("privateNotes", str(payload))


if __name__ == "__main__":
    unittest.main()
