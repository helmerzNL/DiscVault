import os
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

sys.modules.setdefault(
    "requests",
    types.SimpleNamespace(
        get=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("requests is stubbed in tests")),
        post=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("requests is stubbed in tests")),
    ),
)

from app.backend.next_metadata import canonicalize_plugin_result
from app.backend.next_metadata import count_update_fields
from app.backend.next_metadata import flat_credit_entries
from app.backend.next_metadata import plugin_credit_updates
from app.backend.next_metadata import _clean_scanned_title
from app.backend.next_metadata import _parse_import_country
from app.backend.next_metadata import external_metadata_barcode
from app.backend.next_metadata import filter_locked_artwork_updates
from app.backend.next_metadata import filter_hidden_artwork_updates
from app.backend.next_metadata import metadata_field_decisions_with_write_state
from app.backend.next_metadata import metadata_fetch_audit_payload
from app.backend.next_metadata import metadata_source_policy_result
from app.backend.next_metadata import metadata_source_plugin_allowed
from app.backend.next_metadata import merge_metadata_results
from app.backend.next_metadata import MOVIE_LOCKABLE_FIELDS
from app.backend.next_metadata import movievault_identification_plan
from app.backend.next_metadata import movie_locked_fields
from app.backend.next_metadata import normalize_media_format
from app.backend.next_metadata import plugin_execution_plan
from app.backend.next_metadata import preview_enrichment_payload_from_results
from app.backend.next_metadata import query_from_payload
from app.backend.next_metadata import receiver_contribution_payload
from app.backend.next_metadata import run_metadata_source_pipeline
from app.backend.next_metadata import summarize_metadata_execution
from app.backend.next_app import metadata_refresh_job_counts
from app.backend.next_plugins.tmdb import plugin as tmdb_plugin


class _HiddenArtworkCursor:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _query, _params):
        return None

    def fetchall(self):
        return [
            {
                "id": "hidden-poster",
                "kind": "poster",
                "source_url": "https://example.test/hidden.jpg",
            }
        ]


class _HiddenArtworkConnection:
    def cursor(self):
        return _HiddenArtworkCursor()


class _MetadataJobCountCursor:
    def __init__(self, rows):
        self.rows = rows
        self.query = ""
        self.params = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params):
        self.query = query
        self.params = params

    def fetchall(self):
        return self.rows


class _MetadataJobCountConnection:
    def __init__(self, rows):
        self.cursor_instance = _MetadataJobCountCursor(rows)

    def cursor(self):
        return self.cursor_instance


class NextScannedTitleTests(unittest.TestCase):
    def test_metadata_refresh_job_counts_are_not_limited_to_recent_jobs(self):
        connection = _MetadataJobCountConnection(
            [
                {"status": "completed", "count": 37},
                {"status": "pending", "count": 2},
                {"status": "failed", "count": 4},
            ]
        )

        with mock.patch("app.backend.next_app.table_exists", return_value=True):
            counts = metadata_refresh_job_counts(connection)

        self.assertEqual(counts["total"], 43)
        self.assertEqual(counts["byStatus"], {"completed": 37, "pending": 2, "failed": 4})
        self.assertIn("GROUP BY status", connection.cursor_instance.query)
        self.assertNotIn("LIMIT", connection.cursor_instance.query)

    def test_hidden_artwork_cannot_be_reselected_by_metadata_refresh(self):
        with mock.patch("app.backend.next_metadata.table_exists", return_value=True):
            metadata, media = filter_hidden_artwork_updates(
                _HiddenArtworkConnection(),
                uuid.UUID("00000000-0000-0000-0000-000000000001"),
                {
                    "poster_url": "https://example.test/hidden.jpg",
                    "overview": "Keep me",
                },
                {
                    "poster": {
                        "sourceUrl": "https://example.test/hidden.jpg",
                        "providerId": "unit",
                    },
                    "backdrop": {
                        "sourceUrl": "https://example.test/backdrop.jpg",
                        "providerId": "unit",
                    },
                },
            )

        self.assertNotIn("poster_url", metadata)
        self.assertEqual(metadata["overview"], "Keep me")
        self.assertNotIn("poster", media)
        self.assertIn("backdrop", media)

    def test_clean_scanned_title_strips_packaging_noise(self):
        self.assertEqual(
            _clean_scanned_title("John Wick (4K Ultra HD + Blu-ray) (UK Import)"),
            "John Wick",
        )
        self.assertEqual(_clean_scanned_title("Heat [Steelbook]"), "Heat")
        self.assertEqual(
            _clean_scanned_title("Blade Runner 2049 - Limited Edition"),
            "Blade Runner 2049",
        )
        # Space-separated multi-token format tails collapse to the film title.
        self.assertEqual(_clean_scanned_title("Inception 4K Blu-ray"), "Inception")
        self.assertEqual(_clean_scanned_title("Dune Ultra HD Blu-ray 3D"), "Dune")
        # A trailing region tag after a format group strands a bare format token
        # ("4K Blu-ray") that the format-noise stripper alone cannot reach. The
        # exact shape Blu-ray.com returns for an Inception 4K disc.
        self.assertEqual(
            _clean_scanned_title("Inception 4K Blu-ray (4K Ultra HD + Blu-ray) (France)"),
            "Inception",
        )
        self.assertEqual(
            _clean_scanned_title(
                "A Star Is Born 4K Blu-ray (4K Ultra HD + Blu-ray + Digital) (France)"
            ),
            "A Star Is Born",
        )
        # Multi-word country tags strip too, without eating a leading title word.
        self.assertEqual(
            _clean_scanned_title("Skyfall Blu-ray (United Kingdom)"), "Skyfall"
        )
        self.assertEqual(
            _clean_scanned_title("United 93 Blu-ray (United States)"), "United 93"
        )
        # A subtitle after a colon must be preserved.
        self.assertEqual(_clean_scanned_title("Mad Max: Fury Road (Blu-ray)"), "Mad Max: Fury Road")
        # Titles without noise are returned unchanged.
        self.assertEqual(_clean_scanned_title("Inception"), "Inception")
        # Never return an empty string when only noise is present.
        self.assertTrue(_clean_scanned_title("(Blu-ray)"))

    def test_parse_import_country_maps_known_hints(self):
        self.assertEqual(_parse_import_country("John Wick (UK Import)"), ("United Kingdom", ""))
        self.assertEqual(_parse_import_country("Suspiria (Italian Import)"), ("Italy", ""))
        self.assertEqual(_parse_import_country("The Matrix (US Import)"), ("United States", ""))
        # Bare country tag in brackets (no "Import" keyword), e.g. Blu-ray.com.
        self.assertEqual(
            _parse_import_country("Inception 4K Blu-ray (4K Ultra HD + Blu-ray) (France)"),
            ("France", ""),
        )
        self.assertEqual(_parse_import_country("Skyfall Blu-ray (United Kingdom)"), ("United Kingdom", ""))
        # Unknown nationality falls back to a free-text region note.
        self.assertEqual(_parse_import_country("Some Film (Xyz Import)"), ("", "Xyz Import"))
        # Region codes land in the region note.
        self.assertEqual(_parse_import_country("Some Film (Region B)"), ("", "Region B"))
        # No hint at all.
        self.assertEqual(_parse_import_country("Plain Title"), ("", ""))

    def test_canonicalize_splits_release_title_from_clean_title(self):
        normalized = canonicalize_plugin_result(
            "bluray_com",
            "movie_details",
            {
                "status": "hit",
                "releaseTitle": "John Wick (4K Ultra HD + Blu-ray) (UK Import)",
                "movie": {"title": "John Wick (4K Ultra HD + Blu-ray) (UK Import)"},
            },
        )
        movie_updates = normalized["movieUpdates"]
        self.assertEqual(movie_updates["title"], "John Wick")
        self.assertEqual(
            movie_updates["release_title"],
            "John Wick (4K Ultra HD + Blu-ray) (UK Import)",
        )
        self.assertEqual(movie_updates["country"], "United Kingdom")
        self.assertEqual(normalized["releaseTitle"], "John Wick (4K Ultra HD + Blu-ray) (UK Import)")
        self.assertEqual(normalized["cleanTitle"], "John Wick")

    def test_canonicalize_keeps_clean_title_untouched_without_noise(self):
        normalized = canonicalize_plugin_result(
            "tmdb",
            "movie_details",
            {"status": "hit", "movie": {"title": "Inception"}},
        )
        self.assertEqual(normalized["movieUpdates"]["title"], "Inception")
        self.assertNotIn("release_title", normalized["movieUpdates"])

    def test_canonicalize_bluray_release_with_trailing_country(self):
        # Reproduces the real Blu-ray.com payload for the Inception 4K disc when
        # MovieVault is disabled: the plugin's movie.title still carries format
        # noise and the raw release title ends in a "(France)" region tag. The
        # canonical title must still resolve to the bare film title.
        normalized = canonicalize_plugin_result(
            "bluray_com",
            "search_barcode",
            {
                "status": "hit",
                "releaseTitle": "Inception 4K Blu-ray (4K Ultra HD + Blu-ray) (France)",
                "movie": {"title": "Inception 4K Blu-ray (4K Ultra HD + Blu-ray)"},
                "release": {
                    "title": "Inception 4K Blu-ray (4K Ultra HD + Blu-ray) (France)"
                },
            },
        )
        movie_updates = normalized["movieUpdates"]
        self.assertEqual(movie_updates["title"], "Inception")
        self.assertEqual(
            movie_updates["release_title"],
            "Inception 4K Blu-ray (4K Ultra HD + Blu-ray) (France)",
        )
        self.assertEqual(movie_updates["country"], "France")

    def test_canonicalize_region_hint_lands_as_regions_array(self):
        normalized = canonicalize_plugin_result(
            "bluray_com",
            "movie_details",
            {
                "status": "hit",
                "releaseTitle": "Some Film (Region B)",
                "movie": {"title": "Some Film (Region B)"},
            },
        )
        # `regions` must be a JSONB-friendly list, not a bare string.
        self.assertEqual(normalized["technicalUpdates"]["regions"], ["Region B"])
        self.assertEqual(normalized["movieUpdates"]["title"], "Some Film")


class NextMetadataPolicyTests(unittest.TestCase):
    def test_canonicalize_plugin_result_normalizes_box_set_evidence_contract(self):
        result = canonicalize_plugin_result(
            "movievault_26",
            "search_barcode",
            {
                "status": "hit",
                "sourceLabel": "MovieVault 26",
                "sourceRef": "barcode:5050582369601",
                "boxSetProposal": {
                    "title": "Back to the Future Trilogy DVD",
                    "barcode": "5050582369601",
                    "format": "DVD",
                    "members": [
                        {"title": "Back to the Future", "year": "1985"},
                        {"title": "Back to the Future Part II", "year": "1989"},
                        {"title": "Back to the Future Part III", "year": "1990"},
                    ],
                    "boxSetEvidence": {
                        "barcodeMatch": True,
                        "entityType": "box_set",
                        "memberSource": "MovieVault 26",
                        "memberConfidence": "identified",
                        "membersAreExplicit": True,
                    },
                },
            },
        )

        evidence = result["boxSetProposal"]["boxSetEvidence"]
        self.assertTrue(evidence["barcodeMatch"])
        self.assertEqual(evidence["entityType"], "box_set")
        self.assertEqual(evidence["memberCount"], 3)
        self.assertTrue(evidence["membersAreExplicit"])
        self.assertEqual(evidence["format"], "DVD")

    def test_canonicalize_synthesizes_candidate_for_single_movie_hit(self):
        result = canonicalize_plugin_result(
            "bluray_com",
            "search_barcode",
            {
                "status": "hit",
                "sourceLabel": "Blu-ray.com",
                "sourceRef": "https://www.blu-ray.com/movies/Lethal-Weapon",
                "movie": {"title": "Lethal Weapon", "year": "1987", "posterUrl": "https://images.example/lethal.jpg"},
                "release": {"title": "Lethal Weapon 4K UHD"},
                "format": "4K UHD",
            },
        )

        candidates = result["candidates"]
        self.assertEqual(len(candidates), 1)
        synthesized = candidates[0]
        self.assertTrue(synthesized.get("synthesized"))
        self.assertEqual(synthesized["title"], "Lethal Weapon")
        self.assertEqual(synthesized["provider"], "bluray_com")
        self.assertEqual(synthesized["format"], "4K UHD")
        self.assertEqual(synthesized["sourceRef"], "https://www.blu-ray.com/movies/Lethal-Weapon")
        self.assertEqual(synthesized["posterUrl"], "https://images.example/lethal.jpg")

    def test_canonicalize_does_not_synthesize_candidate_on_miss(self):
        result = canonicalize_plugin_result(
            "dvd_fr",
            "search_barcode",
            {"status": "miss", "sourceLabel": "DVDFr"},
        )

        self.assertEqual(result["candidates"], [])

    def test_canonicalize_does_not_synthesize_candidate_for_box_set(self):
        result = canonicalize_plugin_result(
            "movievault_26",
            "search_barcode",
            {
                "status": "hit",
                "sourceLabel": "MovieVault 26",
                "sourceRef": "barcode:5050582369601",
                "movie": {"title": "Back to the Future Trilogy"},
                "boxSetProposal": {
                    "title": "Back to the Future Trilogy DVD",
                    "barcode": "5050582369601",
                    "format": "DVD",
                    "members": [
                        {"title": "Back to the Future", "year": "1985"},
                        {"title": "Back to the Future Part II", "year": "1989"},
                    ],
                },
            },
        )

        self.assertEqual(result["candidates"], [])

    def test_canonicalize_preserves_explicit_candidates(self):
        explicit = [{"title": "Heat", "year": "1995", "provider": "tmdb"}]
        result = canonicalize_plugin_result(
            "tmdb",
            "search_title",
            {
                "status": "hit",
                "sourceLabel": "TMDB",
                "items": explicit,
                "movie": {"title": "Heat"},
            },
        )

        self.assertEqual(result["candidates"], explicit)

    def test_canonicalize_maps_tmdb_genre_ids_to_canonical_keys(self):
        result = canonicalize_plugin_result(
            "tmdb",
            "movie_details",
            {
                "status": "hit",
                "sourceLabel": "TMDb",
                "movie": {"title": "Heat"},
                "genreIds": [28, 80, 18],
            },
        )

        self.assertEqual(result["genreKeys"], ["action", "crime", "drama"])
        self.assertNotIn("genre", result["movieUpdates"])
        self.assertNotIn("genre", result["metadataUpdates"])

    def test_canonicalize_tmdb_explicit_empty_genres_clears_association(self):
        result = canonicalize_plugin_result(
            "tmdb",
            "movie_details",
            {
                "status": "hit",
                "sourceLabel": "TMDb",
                "movie": {"title": "Untitled Documentary"},
                "genreIds": [],
            },
        )

        self.assertEqual(result["genreKeys"], [])

    def test_canonicalize_without_genre_ids_leaves_genre_keys_unset(self):
        result = canonicalize_plugin_result(
            "tmdb",
            "movie_details",
            {"status": "hit", "sourceLabel": "TMDb", "movie": {"title": "Heat"}},
        )

        self.assertIsNone(result["genreKeys"])

    def test_canonicalize_ignores_genre_ids_from_non_tmdb_plugins(self):
        result = canonicalize_plugin_result(
            "bluray_com",
            "search_barcode",
            {
                "status": "hit",
                "sourceLabel": "Blu-ray.com",
                "movie": {"title": "Heat"},
                "genreIds": [28],
            },
        )

        self.assertIsNone(result["genreKeys"])

    def test_metadata_source_policy_result_strips_genre_keys_for_non_tmdb(self):
        constrained = metadata_source_policy_result(
            {
                "pluginId": "bluray_com",
                "entrypoint": "search_barcode",
                "movieUpdates": {},
                "metadataUpdates": {},
                "genreKeys": ["action"],
            }
        )

        self.assertIsNone(constrained["genreKeys"])

    def test_merge_metadata_results_selects_first_tmdb_genre_answer(self):
        merged = merge_metadata_results(
            current={"genres": ["comedy"]},
            technical_current={},
            results=[
                {
                    "pluginId": "tmdb",
                    "entrypoint": "movie_details",
                    "sourceLabel": "TMDb",
                    "movieUpdates": {},
                    "metadataUpdates": {},
                    "genreKeys": ["action", "thriller"],
                },
            ],
            overwrite_enabled=False,
            target_format="",
        )

        self.assertEqual(merged["genreKeys"], ["action", "thriller"])
        genre_decision = next(item for item in merged["fieldDecisions"] if item["target"] == "genre")
        self.assertEqual(genre_decision["initialValue"], ["comedy"])
        self.assertEqual(genre_decision["finalValue"], ["action", "thriller"])
        self.assertTrue(genre_decision["changed"])

    def test_merge_metadata_results_does_not_count_unchanged_genres(self):
        merged = merge_metadata_results(
            current={"genres": ["action", "crime"]},
            technical_current={},
            results=[
                {
                    "pluginId": "tmdb",
                    "entrypoint": "movie_details",
                    "sourceLabel": "TMDb",
                    "movieUpdates": {},
                    "metadataUpdates": {},
                    "genreKeys": ["crime", "action"],
                },
            ],
            overwrite_enabled=False,
            target_format="",
        )

        self.assertIsNone(merged["genreKeys"])
        self.assertEqual(count_update_fields(merged), 0)
        self.assertFalse(any(item["target"] == "genre" for item in merged["provenance"]))
        genre_decision = next(item for item in merged["fieldDecisions"] if item["target"] == "genre")
        self.assertFalse(genre_decision["changed"])
        self.assertEqual(genre_decision["finalValue"], ["action", "crime"])

    def test_merge_metadata_results_leaves_genres_untouched_without_tmdb_answer(self):
        merged = merge_metadata_results(
            current={"genres": ["comedy"]},
            technical_current={},
            results=[
                {
                    "pluginId": "bluray_com",
                    "entrypoint": "search_barcode",
                    "sourceLabel": "Blu-ray.com",
                    "movieUpdates": {"format": "4K UHD"},
                    "metadataUpdates": {},
                    "genreKeys": None,
                },
            ],
            overwrite_enabled=False,
            target_format="",
        )

        self.assertIsNone(merged["genreKeys"])

    def test_merge_metadata_results_ignores_conflicting_second_genre_answer(self):
        merged = merge_metadata_results(
            current={},
            technical_current={},
            results=[
                {
                    "pluginId": "tmdb",
                    "entrypoint": "movie_details",
                    "sourceLabel": "TMDb",
                    "movieUpdates": {},
                    "metadataUpdates": {},
                    "genreKeys": ["action"],
                },
                {
                    "pluginId": "tmdb",
                    "entrypoint": "movie_details",
                    "sourceLabel": "TMDb (bridge)",
                    "movieUpdates": {},
                    "metadataUpdates": {},
                    "genreKeys": ["comedy"],
                },
            ],
            overwrite_enabled=False,
            target_format="",
        )

        self.assertEqual(merged["genreKeys"], ["action"])

    def test_tmdb_plugin_normalizes_genres_to_tmdb_ids(self):
        details = tmdb_plugin._normalize_details(
            {
                "id": 1234,
                "title": "Heat",
                "genres": [{"id": 28, "name": "Action"}, {"id": 80, "name": "Crime"}],
            }
        )

        self.assertEqual(details["genreIds"], [28, 80])
        self.assertNotIn("genre", details["movie"])

    def test_tmdb_plugin_reports_explicit_empty_genre_list(self):
        details = tmdb_plugin._normalize_details({"id": 1234, "title": "Heat", "genres": []})

        self.assertEqual(details["genreIds"], [])

    @mock.patch("app.backend.next_plugins.tmdb.plugin._details")
    @mock.patch("app.backend.next_plugins.tmdb.plugin.search_title")
    def test_tmdb_movie_details_selects_exact_title_instead_of_first_result(self, search_title, details):
        search_title.return_value = {
            "status": "hit",
            "items": [
                {"tmdbId": 361743, "title": "Top Gun: Maverick", "year": "2022"},
                {"tmdbId": 744, "title": "Top Gun", "year": "1986"},
            ],
        }
        details.return_value = {"id": 744, "title": "Top Gun", "release_date": "1986-05-16"}

        result = tmdb_plugin.movie_details({"title": "Top Gun"}, {})

        details.assert_called_once_with({}, 744)
        self.assertEqual(result["movie"]["title"], "Top Gun")
        self.assertEqual(result["tmdbId"], 744)

    @mock.patch("app.backend.next_plugins.tmdb.plugin._details")
    @mock.patch("app.backend.next_plugins.tmdb.plugin.search_title")
    def test_tmdb_movie_details_rejects_different_title(self, search_title, details):
        search_title.return_value = {
            "status": "hit",
            "items": [
                {"tmdbId": 361743, "title": "Top Gun: Maverick", "year": "2022"},
            ],
        }

        result = tmdb_plugin.movie_details({"title": "Top Gun"}, {})

        details.assert_not_called()
        self.assertEqual(result, {"status": "miss", "provider": "tmdb"})

    @mock.patch("app.backend.next_plugins.tmdb.plugin._details")
    @mock.patch("app.backend.next_plugins.tmdb.plugin.search_title")
    def test_tmdb_movie_details_requires_requested_year(self, search_title, details):
        search_title.return_value = {
            "status": "hit",
            "items": [
                {"tmdbId": 100, "title": "The Thing", "year": "2011"},
                {"tmdbId": 1091, "title": "The Thing", "year": "1982"},
            ],
        }
        details.return_value = {"id": 1091, "title": "The Thing", "release_date": "1982-06-25"}

        result = tmdb_plugin.movie_details({"title": "The Thing", "year": "1982"}, {})

        details.assert_called_once_with({}, 1091)
        self.assertEqual(result["tmdbId"], 1091)

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

    def test_release_source_cannot_replace_canonical_display_title(self):
        current = {"title": "A Minecraft Movie", "format": "4K UHD", "metadata": {}}
        result = canonicalize_plugin_result(
            "bluray_com",
            "search_barcode",
            {
                "status": "hit",
                "sourceLabel": "Blu-ray.com",
                "movie": {
                    "title": "A Minecraft Movie 4K Blu-ray (SteelBook) (France)",
                    "format": "4K UHD",
                },
                "release": {
                    "title": "A Minecraft Movie 4K Blu-ray (SteelBook) (France)",
                    "format": "4K UHD",
                },
                "technicalSpecs": {"format": "4K UHD", "subtitles": ["French"]},
            },
        )
        merged = merge_metadata_results(
            current=current,
            technical_current={},
            results=[result],
            overwrite_enabled=True,
            target_format="4K UHD",
        )

        self.assertNotIn("title", merged["movieUpdates"])
        self.assertEqual(merged["technicalUpdates"]["subtitles"], ["French"])
        self.assertTrue(
            any(item["field"] == "title" and "release source" in item["reason"] for item in merged["skipped"])
        )

    def test_release_source_seeds_canonical_title_when_movie_has_none(self):
        # Mirrors a real barcode scan where MovieVault 500s and TMDb misses, so
        # the only hit is Blu-ray.com (a release source). Its plugin already
        # derives a clean movie.title with the raw packaging string in
        # release.title; the clean title must seed the empty canonical title
        # instead of leaving it blank.
        current = {"title": "", "format": "4K UHD", "metadata": {}}
        result = canonicalize_plugin_result(
            "bluray_com",
            "search_barcode",
            {
                "status": "hit",
                "sourceLabel": "Blu-ray.com",
                "movie": {
                    "title": "Inception",
                    "format": "4K UHD",
                },
                "release": {
                    "title": "Inception 4K Blu-ray (UK Import)",
                    "format": "4K UHD",
                },
                "technicalSpecs": {"format": "4K UHD", "subtitles": ["English"]},
            },
        )
        merged = merge_metadata_results(
            current=current,
            technical_current={},
            results=[result],
            overwrite_enabled=False,
            target_format="4K UHD",
        )

        self.assertEqual(merged["movieUpdates"]["title"], "Inception")
        self.assertEqual(
            merged["movieUpdates"]["release_title"],
            "Inception 4K Blu-ray (UK Import)",
        )
        self.assertEqual(merged["movieUpdates"]["country"], "United Kingdom")
        self.assertFalse(
            any(item["field"] == "title" and "release source" in item["reason"] for item in merged["skipped"])
        )

    def test_release_source_still_cannot_overwrite_existing_canonical_title(self):
        current = {"title": "Inception", "format": "4K UHD", "metadata": {}}
        result = canonicalize_plugin_result(
            "bluray_com",
            "search_barcode",
            {
                "status": "hit",
                "sourceLabel": "Blu-ray.com",
                "movie": {"title": "Inception 4K Blu-ray (UK Import)", "format": "4K UHD"},
                "release": {"title": "Inception 4K Blu-ray (UK Import)", "format": "4K UHD"},
                "technicalSpecs": {"format": "4K UHD"},
            },
        )
        merged = merge_metadata_results(
            current=current,
            technical_current={},
            results=[result],
            overwrite_enabled=True,
            target_format="4K UHD",
        )

        self.assertNotIn("title", merged["movieUpdates"])
        self.assertTrue(
            any(
                item["field"] == "title" and "release source cannot overwrite" in item["reason"]
                for item in merged["skipped"]
            )
        )

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

    def test_plugin_credits_are_kept_in_metadata_refresh_proposal(self):
        current = {"title": "Aladdin", "format": "4K UHD", "metadata": {}}
        result = canonicalize_plugin_result(
            "tmdb",
            "movie_details",
            {
                "status": "hit",
                "sourceLabel": "TMDb",
                "sourceRef": "tmdb:420817",
                "credits": [
                    {"role": "actor", "name": "Will Smith", "character": "Genie", "tmdbId": 2888, "sortOrder": 0},
                    {"role": "crew", "name": "Guy Ritchie", "job": "Director", "tmdbId": 956, "sortOrder": 0},
                ],
            },
        )
        merged = merge_metadata_results(
            current=current,
            technical_current={},
            results=[result],
            overwrite_enabled=False,
            target_format="4K UHD",
        )

        self.assertEqual(len(result["credits"]), 2)
        self.assertEqual(len(merged["credits"]), 2)
        self.assertEqual(merged["credits"][0]["name"], "Will Smith")
        self.assertEqual(merged["credits"][0]["role"], "actor")
        self.assertEqual(merged["credits"][1]["job"], "Director")
        self.assertEqual(merged["credits"][1]["sourceLabel"], "TMDb")

    def test_flat_person_fields_synthesize_credits_when_no_structured_credits(self):
        result = canonicalize_plugin_result(
            "movievault_26",
            "movie_details",
            {
                "status": "hit",
                "sourceLabel": "MovieVault 26",
                "sourceRef": "title:Heat",
                "movie": {
                    "title": "Heat",
                    "director": "Michael Mann",
                    "actor": "Al Pacino, Robert De Niro",
                    "producer": "Art Linson",
                },
            },
        )
        credits = result.get("credits") or []
        by_key = {(c["name"], c["role"], c.get("job") or "") for c in credits}
        self.assertIn(("Michael Mann", "crew", "Director"), by_key)
        self.assertIn(("Al Pacino", "actor", ""), by_key)
        self.assertIn(("Robert De Niro", "actor", ""), by_key)
        self.assertIn(("Art Linson", "crew", "Producer"), by_key)
        # Flat-derived people carry no tmdbId; they match on name downstream.
        self.assertTrue(all(c.get("tmdbId", "") == "" for c in credits))

    def test_structured_credits_suppress_flat_field_fallback(self):
        result = canonicalize_plugin_result(
            "movievault_26",
            "movie_details",
            {
                "status": "hit",
                "sourceLabel": "MovieVault 26",
                "sourceRef": "title:Heat",
                "credits": [
                    {"role": "actor", "name": "Al Pacino", "tmdbId": 1158},
                ],
                "movie": {
                    "title": "Heat",
                    "director": "Michael Mann",
                    "actor": "Al Pacino, Robert De Niro",
                },
            },
        )
        credits = result.get("credits") or []
        self.assertEqual(len(credits), 1)
        self.assertEqual(credits[0]["name"], "Al Pacino")
        self.assertEqual(credits[0]["tmdbId"], "1158")

    def test_flat_credit_entries_dedupes_and_maps_roles(self):
        entries = flat_credit_entries(
            {
                "director": "Jane Doe",
                "writer": ["Jane Doe", "John Roe"],
                "actor": "A Star; B Star",
            },
            plugin_id="movievault_26",
            source_label="MovieVault 26",
            source_ref="title:Sample",
        )
        keyed = {(e["name"], e["role"], e["job"]) for e in entries}
        self.assertIn(("Jane Doe", "crew", "Director"), keyed)
        self.assertIn(("Jane Doe", "crew", "Writer"), keyed)
        self.assertIn(("John Roe", "crew", "Writer"), keyed)
        self.assertIn(("A Star", "actor", ""), keyed)
        self.assertIn(("B Star", "actor", ""), keyed)

    def test_plugin_credit_updates_prefers_structured_over_flat(self):
        merged = plugin_credit_updates(
            {"title": "Heat", "actor": "Al Pacino, Robert De Niro"},
            {"title": "Heat", "actor": "Al Pacino, Robert De Niro"},
            plugin_id="movievault_26",
            source_label="MovieVault 26",
            source_ref="title:Heat",
        )
        # movie_source == result here, flat fallback fires exactly once (deduped).
        names = sorted(entry["name"] for entry in merged)
        self.assertEqual(names, ["Al Pacino", "Robert De Niro"])

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

    def test_field_decisions_record_priority_winner_for_conflicting_fields(self):
        current = {"title": "Manual Title", "overview": "Manual overview", "format": "4K UHD", "metadata": {}}
        tmdb_result = {
            "pluginId": "tmdb",
            "entrypoint": "lookup_external_id",
            "sourceLabel": "TMDb",
            "sourceRef": "tmdb:1",
            "movieUpdates": {"overview": "TMDb overview"},
        }
        movievault_result = {
            "pluginId": "movievault_26",
            "entrypoint": "movie_details",
            "sourceLabel": "MovieVault 26",
            "sourceRef": "movievault:1",
            "movieUpdates": {"overview": "MovieVault overview"},
        }

        merged = merge_metadata_results(
            current=current,
            technical_current={},
            results=[tmdb_result, movievault_result],
            overwrite_enabled=True,
            target_format="4K UHD",
        )

        decision = next(item for item in merged["fieldDecisions"] if item["target"] == "movie" and item["field"] == "overview")
        self.assertEqual(merged["movieUpdates"]["overview"], "TMDb overview")
        self.assertTrue(decision["conflict"])
        self.assertEqual(decision["winner"]["pluginId"], "tmdb")
        self.assertEqual(decision["finalValue"], "TMDb overview")
        self.assertEqual(len(decision["candidates"]), 2)
        self.assertTrue(decision["candidates"][0]["winner"])
        self.assertFalse(decision["candidates"][1]["accepted"])
        self.assertEqual(decision["candidates"][1]["reason"], "higher-priority provider already selected this field")

    def test_field_decisions_mark_later_provider_rejected_without_overwrite(self):
        current = {"title": "Manual Title", "format": "4K UHD", "metadata": {}}
        tmdb_result = {
            "pluginId": "tmdb",
            "entrypoint": "lookup_external_id",
            "sourceLabel": "TMDb",
            "movieUpdates": {"rating": "7.1"},
        }
        omdb_result = {
            "pluginId": "omdb",
            "entrypoint": "lookup_external_id",
            "sourceLabel": "OMDb",
            "movieUpdates": {"rating": "7.2"},
        }

        merged = merge_metadata_results(
            current=current,
            technical_current={},
            results=[tmdb_result, omdb_result],
            overwrite_enabled=False,
            target_format="4K UHD",
        )

        decision = next(item for item in merged["fieldDecisions"] if item["target"] == "movie" and item["field"] == "rating")
        self.assertEqual(merged["movieUpdates"]["rating"], "7.1")
        self.assertEqual(decision["winner"]["pluginId"], "tmdb")
        self.assertEqual(decision["acceptedCandidateCount"], 1)
        self.assertFalse(decision["candidates"][1]["accepted"])
        self.assertEqual(decision["candidates"][1]["reason"], "higher-priority provider already selected this field")

    def test_tmdb_content_ratings_are_format_neutral(self):
        current = {"title": "Manual Title", "format": "4K UHD", "metadata": {}}
        result = canonicalize_plugin_result(
            "tmdb",
            "movie_details",
            {
                "status": "hit",
                "sourceRef": "tmdb:105",
                "movie": {"title": "Back to the Future"},
                "technicalSpecs": {"contentRatings": {"NL": "AL", "US": "PG"}},
            },
        )

        self.assertEqual(result["technicalUpdates"]["content_ratings"], {"NL": "AL", "US": "PG"})

        merged = merge_metadata_results(
            current=current,
            technical_current={},
            results=[result],
            overwrite_enabled=False,
            target_format="4K UHD",
        )

        self.assertEqual(merged["technicalUpdates"]["content_ratings"], {"NL": "AL", "US": "PG"})
        decision = next(item for item in merged["fieldDecisions"] if item["target"] == "technical" and item["field"] == "content_ratings")
        self.assertEqual(decision["winner"]["pluginId"], "tmdb")
        self.assertEqual(decision["winner"]["reason"], "current field is empty")

    def test_tmdb_content_ratings_replace_an_unlocked_existing_placeholder(self):
        current = {"title": "Manual Title", "format": "4K UHD", "metadata": {}}
        result = canonicalize_plugin_result(
            "tmdb",
            "movie_details",
            {
                "status": "hit",
                "sourceRef": "tmdb:11814",
                "technicalSpecs": {"contentRatings": {"NL": "12", "US": "PG-13"}},
            },
        )

        merged = merge_metadata_results(
            current=current,
            technical_current={"content_ratings": {"NL": "Onbekende leeftijdsclassificatie"}},
            results=[result],
            overwrite_enabled=False,
            target_format="Blu-ray",
        )

        self.assertEqual(
            merged["technicalUpdates"]["content_ratings"],
            {"NL": "12", "US": "PG-13"},
        )
        decision = next(
            item
            for item in merged["fieldDecisions"]
            if item["target"] == "technical" and item["field"] == "content_ratings"
        )
        self.assertEqual(decision["winner"]["reason"], "format-neutral certification refresh")

    def test_locked_content_ratings_still_retain_the_existing_value(self):
        current = {
            "title": "Manual Title",
            "format": "Blu-ray",
            "metadata": {"field_locks": ["content_ratings"]},
        }
        result = canonicalize_plugin_result(
            "tmdb",
            "movie_details",
            {
                "status": "hit",
                "sourceRef": "tmdb:11814",
                "technicalSpecs": {"contentRatings": {"NL": "12"}},
            },
        )

        merged = merge_metadata_results(
            current=current,
            technical_current={"content_ratings": {"NL": "16"}},
            results=[result],
            overwrite_enabled=False,
            target_format="Blu-ray",
        )

        self.assertNotIn("content_ratings", merged["technicalUpdates"])
        decision = next(
            item
            for item in merged["fieldDecisions"]
            if item["target"] == "technical" and item["field"] == "content_ratings"
        )
        self.assertEqual(decision["candidates"][0]["reason"], "field is locked by user")

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
        decision = next(item for item in merged["fieldDecisions"] if item["target"] == "media" and item["field"] == "poster")
        self.assertEqual(decision["winner"]["pluginId"], "tmdb")
        self.assertEqual(decision["finalValue"], "https://provider/poster-main.jpg")

    def test_field_decisions_are_marked_written_from_applied_payload(self):
        decisions = [
            {
                "target": "movie",
                "field": "overview",
                "winner": {"pluginId": "tmdb"},
                "candidates": [],
            },
            {
                "target": "metadata",
                "field": "poster_url",
                "winner": {"pluginId": "tmdb"},
                "candidates": [],
            },
            {
                "target": "genre",
                "field": "genre",
                "changed": True,
                "winner": {"pluginId": "tmdb"},
                "candidates": [],
            },
        ]

        enriched = metadata_field_decisions_with_write_state(
            decisions,
            applied={
                "changed": True,
                "applied": {
                    "movieUpdates": {"overview": "Fresh overview"},
                    "metadataUpdates": {},
                    "genres": ["action", "crime"],
                },
            },
            dry_run=False,
        )

        self.assertTrue(enriched[0]["written"])
        self.assertEqual(enriched[0]["writeState"], "written")
        self.assertFalse(enriched[1]["written"])
        self.assertEqual(enriched[1]["writeState"], "not_written")
        self.assertTrue(enriched[2]["written"])
        self.assertEqual(enriched[2]["writeState"], "written")
        self.assertEqual(enriched[2]["appliedValue"], ["action", "crime"])

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

        self.assertEqual([item["entrypoint"] for item in plan], ["search_barcode", "box_set_candidates"])

    def test_movievault_identification_plan_never_requests_enrichment(self):
        query = query_from_payload({"title": "Alien", "detectBoxSets": True, "previewMode": True})
        plan = movievault_identification_plan(
            {"capabilities": ["search_title", "movie_details", "box_set_candidates"]},
            query,
        )

        self.assertEqual([item["entrypoint"] for item in plan], ["search_title", "box_set_candidates"])

    def test_movievault_identification_plan_uses_known_v3_movie_id_directly(self):
        query = query_from_payload({"title": "Alien", "movieVaultId": "mv_alien"})
        plan = movievault_identification_plan(
            {"capabilities": ["search_title", "movie_details"]},
            query,
        )

        self.assertEqual(query["movieVaultId"], "mv_alien")
        self.assertEqual([item["entrypoint"] for item in plan], ["movie_details"])
        self.assertEqual(plan[0]["payload"]["movieVaultId"], "mv_alien")

    def test_movievault_result_keeps_identity_and_filters_enrichment(self):
        for plugin_id in ("movievault_26", "movievault_v2"):
            with self.subTest(plugin_id=plugin_id):
                constrained = metadata_source_policy_result(
                    {
                        "pluginId": plugin_id,
                        "movieUpdates": {"title": "Alien", "year": "1979", "overview": "MovieVault plot"},
                        "metadataUpdates": {"director": "Ridley Scott", "packaging": "SteelBook"},
                        "technicalUpdates": {"hdr": "HDR10"},
                        "mediaUpdates": {"poster": {"sourceUrl": "https://movievault.example/poster.jpg"}},
                        "identifiers": {"tmdb": "348"},
                        "credits": [{"name": "Sigourney Weaver", "role": "actor"}],
                        "localizations": [{"lang": "nl", "title": "Alien"}],
                        "candidates": [
                            {
                                "title": "Alien",
                                "year": "1979",
                                "tmdbId": "348",
                                "posterUrl": "https://movievault.example/poster.jpg",
                                "overview": "MovieVault plot",
                            }
                        ],
                        "raw": {"movie": {"title": "Alien", "overview": "MovieVault plot"}},
                    }
                )

                self.assertEqual(constrained["movieUpdates"], {"title": "Alien", "year": "1979"})
                self.assertEqual(constrained["metadataUpdates"], {"packaging": "SteelBook"})
                self.assertEqual(constrained["technicalUpdates"], {"hdr": "HDR10"})
                self.assertEqual(constrained["mediaUpdates"], {})
                self.assertEqual(constrained["credits"], [])
                self.assertEqual(constrained["localizations"], [])
                self.assertEqual(constrained["candidates"], [{"title": "Alien", "year": "1979", "tmdbId": "348"}])
                self.assertEqual(constrained["raw"], {})

    def test_non_tmdb_result_drops_legacy_free_text_genres(self):
        constrained = metadata_source_policy_result(
            {
                "pluginId": "movievault_26",
                "movieUpdates": {"title": "Alien", "genre": "Action, Thriller"},
                "metadataUpdates": {"genres": ["Action", "Thriller"]},
                "genreKeys": None,
            }
        )

        self.assertEqual(constrained["movieUpdates"], {"title": "Alien"})
        self.assertEqual(constrained["metadataUpdates"], {})

    def test_tmdb_result_enriches_without_becoming_identification_candidate(self):
        constrained = metadata_source_policy_result(
            {
                "pluginId": "tmdb",
                "movieUpdates": {
                    "title": "Alien",
                    "year": "1979",
                    "overview": "TMDb plot",
                    "runtime_minutes": 117,
                },
                "metadataUpdates": {"director": "Ridley Scott"},
                "technicalUpdates": {"content_ratings": {"US": "R"}},
                "mediaUpdates": {"poster": {"sourceUrl": "https://image.tmdb.org/poster.jpg"}},
                "credits": [{"name": "Sigourney Weaver", "role": "actor"}],
                "candidates": [{"title": "Alien", "tmdbId": "348"}],
                "raw": {"movie": {"title": "Alien"}},
            }
        )

        self.assertEqual(constrained["movieUpdates"], {"overview": "TMDb plot", "runtime_minutes": 117})
        self.assertEqual(constrained["metadataUpdates"], {"director": "Ridley Scott"})
        self.assertEqual(constrained["candidates"], [])
        self.assertEqual(constrained["raw"], {})
        self.assertEqual(len(constrained["credits"]), 1)
        self.assertIn("poster", constrained["mediaUpdates"])

    def test_enrichment_payload_prefers_movievault_identifier_over_title_only_query(self):
        payload = preview_enrichment_payload_from_results(
            {"title": "Alien", "previewMode": True},
            [
                {
                    "pluginId": "movievault_26",
                    "movieUpdates": {"title": "Alien", "year": "1979"},
                    "identifiers": {"tmdb": "348"},
                }
            ],
        )

        self.assertEqual(payload["title"], "Alien")
        self.assertEqual(payload["year"], "1979")
        self.assertEqual(payload["tmdbId"], "348")

    @mock.patch("app.backend.next_metadata.preferred_provider_overwrite", return_value=False)
    @mock.patch("app.backend.next_metadata.plugin_requires_config")
    @mock.patch("app.backend.next_metadata.run_plugin_entrypoint")
    @mock.patch("app.backend.next_metadata.plugin_execution_context", return_value={})
    @mock.patch("app.backend.next_metadata.plugin_config_from_db", return_value={})
    @mock.patch("app.backend.next_metadata.metadata_source_plugins")
    def test_missing_tmdb_key_is_non_blocking_and_movievault_still_identifies(
        self,
        source_plugins,
        _plugin_config,
        _execution_context,
        run_entrypoint,
        requires_config,
        _overwrite,
    ):
        source_plugins.return_value = [
            {
                "id": "tmdb",
                "name": "TMDb",
                "categories": ["metadata_source"],
                "capabilities": ["movie_details"],
                "manifest": {"capabilities": ["movie_details"]},
                "order_index": 10,
            },
            {
                "id": "movievault_26",
                "name": "MovieVault",
                "categories": ["metadata_source"],
                "capabilities": ["search_barcode"],
                "manifest": {"capabilities": ["search_barcode"]},
                "order_index": 51,
            },
        ]
        requires_config.side_effect = lambda plugin, _config, _entrypoint: plugin["id"] == "tmdb"
        run_entrypoint.return_value = {
            "status": "ok",
            "state": "available",
            "elapsedMs": 2,
            "result": {
                "status": "hit",
                "provider": "movievault_26",
                "movie": {
                    "title": "Alien",
                    "year": "1979",
                    "overview": "MovieVault plot",
                    "posterUrl": "https://movievault.example/poster.jpg",
                },
                "tmdbId": "348",
            },
        }

        result = run_metadata_source_pipeline(
            object(),
            query=query_from_payload({"barcode": "8717418557683", "previewMode": True}),
            current={"metadata": {}},
            technical_current={},
        )

        self.assertEqual(result["proposal"]["movieUpdates"]["title"], "Alien")
        self.assertNotIn("overview", result["proposal"]["movieUpdates"])
        self.assertEqual(result["enrichment"]["tmdb"]["state"], "needs_configuration")
        self.assertFalse(result["enrichment"]["tmdb"]["configured"])
        self.assertTrue(result["enrichment"]["tmdb"]["nonBlocking"])
        self.assertEqual(run_entrypoint.call_count, 1)

    @mock.patch("app.backend.next_metadata.preferred_provider_overwrite", return_value=False)
    @mock.patch("app.backend.next_metadata.plugin_requires_config", return_value=False)
    @mock.patch("app.backend.next_metadata.plugin_execution_context", return_value={})
    @mock.patch("app.backend.next_metadata.plugin_config_from_db", return_value={"apiKey": "configured"})
    @mock.patch("app.backend.next_metadata.metadata_source_plugins")
    @mock.patch("app.backend.next_metadata.run_plugin_entrypoint")
    def test_pipeline_identifies_with_movievault_then_enriches_directly_with_tmdb(
        self,
        run_entrypoint,
        source_plugins,
        _plugin_config,
        _execution_context,
        _requires_config,
        _overwrite,
    ):
        source_plugins.return_value = [
            {
                "id": "tmdb",
                "name": "TMDb",
                "categories": ["metadata_source"],
                "capabilities": ["movie_details"],
                "manifest": {"capabilities": ["movie_details"]},
                "order_index": 10,
            },
            {
                "id": "movievault_26",
                "name": "MovieVault",
                "categories": ["metadata_source"],
                "capabilities": ["search_barcode"],
                "manifest": {"capabilities": ["search_barcode"]},
                "order_index": 51,
            },
        ]

        def execute(plugin_id, entrypoint, payload, _context):
            if plugin_id == "movievault_26":
                self.assertEqual(entrypoint, "search_barcode")
                return {
                    "status": "ok",
                    "state": "available",
                    "elapsedMs": 2,
                    "result": {
                        "status": "hit",
                        "provider": "movievault_26",
                        "movie": {"title": "Alien", "year": "1979", "overview": "MovieVault plot"},
                        "tmdbId": "348",
                    },
                }
            self.assertEqual(plugin_id, "tmdb")
            self.assertEqual(entrypoint, "movie_details")
            self.assertEqual(payload["tmdbId"], "348")
            return {
                "status": "ok",
                "state": "available",
                "elapsedMs": 3,
                "result": {
                    "status": "hit",
                    "provider": "tmdb",
                    "movie": {
                        "title": "Alien",
                        "year": "1979",
                        "overview": "TMDb plot",
                        "posterUrl": "https://image.tmdb.org/poster.jpg",
                    },
                    "credits": [{"name": "Sigourney Weaver", "role": "actor"}],
                    "tmdbId": "348",
                },
            }

        run_entrypoint.side_effect = execute
        result = run_metadata_source_pipeline(
            object(),
            query=query_from_payload({"barcode": "8717418557683", "previewMode": True}),
            current={"metadata": {}},
            technical_current={},
        )

        self.assertEqual(
            [(call.args[0], call.args[1]) for call in run_entrypoint.call_args_list],
            [("movievault_26", "search_barcode"), ("tmdb", "movie_details")],
        )
        self.assertEqual(result["proposal"]["movieUpdates"]["title"], "Alien")
        self.assertEqual(result["proposal"]["movieUpdates"]["overview"], "TMDb plot")
        self.assertEqual(result["proposal"]["mediaUpdates"]["poster"]["providerId"], "tmdb")
        self.assertEqual(len(result["proposal"]["credits"]), 1)
        self.assertEqual(result["enrichment"]["tmdb"]["state"], "enriched")
        self.assertTrue(result["enrichment"]["tmdb"]["configured"])

    @mock.patch("app.backend.next_metadata.preferred_provider_overwrite", return_value=False)
    @mock.patch("app.backend.next_metadata.plugin_secret_values", return_value={})
    @mock.patch(
        "app.backend.next_metadata.plugin_config_from_db",
        return_value={
            "settings": {"origin": "https://movievault.example", "bucketFallback": False},
            "settingsConfigured": True,
            "secretNames": [],
            "secretsConfigured": True,
        },
    )
    @mock.patch("app.backend.next_metadata.metadata_source_plugins")
    def test_external_movievault_v2_preview_executes_identity_entrypoints(
        self,
        source_plugins,
        _plugin_config,
        _secret_values,
        _overwrite,
    ):
        plugin = {
            "id": "movievault_v2",
            "name": "MovieVault v2",
            "categories": ["metadata_source"],
            "capabilities": ["search_barcode", "search_title", "box_set_candidates"],
            "manifest": {
                "categories": ["metadata_source"],
                "capabilities": ["search_barcode", "search_title", "box_set_candidates"],
                "requiresSecrets": False,
            },
            "order_index": 52,
            "enabled": True,
        }
        source_plugins.return_value = [plugin]

        with tempfile.TemporaryDirectory() as temp_dir:
            install_dir = Path(temp_dir)
            (install_dir / ".initialized").write_text("1", encoding="utf-8")
            plugin_dir = install_dir / "movievault_v2"
            plugin_dir.mkdir()
            (plugin_dir / "manifest.json").write_text(
                """
                {
                  "id": "movievault_v2",
                  "name": "MovieVault v2",
                  "version": "1.0.4",
                  "discVaultPluginApi": "next-1",
                  "categories": ["metadata_source"],
                  "capabilities": ["search_barcode", "search_title", "box_set_candidates"],
                  "requiresSecrets": false
                }
                """,
                encoding="utf-8",
            )
            (plugin_dir / "plugin.py").write_text(
                """
def _check_context(context):
    if not callable(context.get("movievaultV2Lookup")):
        raise RuntimeError("movievault_v2_lookup_missing")
    if context.get("metadataLookup") is not None:
        raise RuntimeError("legacy_metadata_lookup_present")
    if context.get("secrets"):
        raise RuntimeError("legacy_secrets_present")


def _lookup(payload, context):
    _check_context(context)
    return {"status": "miss", "provider": "movievault_v2", "items": []}


search_barcode = _lookup
search_title = _lookup
box_set_candidates = _lookup
                """,
                encoding="utf-8",
            )

            with mock.patch.dict(
                os.environ,
                {
                    "DISCVAULT_PLUGIN_INSTALL_DIR": str(install_dir),
                    "DISCVAULT_PLUGIN_PATHS": "",
                },
            ):
                barcode_result = run_metadata_source_pipeline(
                    object(),
                    query=query_from_payload({"barcode": "4006381333931", "previewMode": True}),
                    current={"metadata": {}},
                    technical_current={},
                )
                title_result = run_metadata_source_pipeline(
                    object(),
                    query=query_from_payload(
                        {
                            "title": "Example Film",
                            "detectBoxSets": True,
                            "previewMode": True,
                        }
                    ),
                    current={"metadata": {}},
                    technical_current={},
                )

        self.assertEqual(
            [item["entrypoint"] for item in barcode_result["executions"]],
            ["search_barcode"],
        )
        self.assertEqual(
            [item["entrypoint"] for item in title_result["executions"]],
            ["search_title", "box_set_candidates"],
        )
        self.assertTrue(all(item["status"] == "ok" for item in barcode_result["executions"]))
        self.assertTrue(all(item["status"] == "ok" for item in title_result["executions"]))

    def test_preview_title_lookup_can_request_box_set_candidates(self):
        query = query_from_payload({"title": "Back to the Future Trilogy", "detectBoxSets": True, "previewMode": True})
        plan = plugin_execution_plan(
            {"capabilities": ["search_title", "movie_details", "box_set_candidates"]},
            query,
        )

        self.assertEqual([item["entrypoint"] for item in plan], ["search_title", "movie_details", "box_set_candidates"])

    def test_preview_title_lookup_propagates_release_variants_flag(self):
        query = query_from_payload({
            "title": "E.T. the Extra-Terrestrial",
            "previewMode": True,
            "releaseVariants": True,
        })
        self.assertTrue(query.get("releaseVariants"))
        plan = plugin_execution_plan(
            {"capabilities": ["search_title", "movie_details"]},
            query,
        )
        title_step = next(item for item in plan if item["entrypoint"] == "search_title")
        self.assertTrue(title_step["payload"].get("releaseVariants"))

    def test_preview_title_lookup_release_variants_flag_defaults_off(self):
        query = query_from_payload({"title": "E.T. the Extra-Terrestrial", "previewMode": True})
        self.assertFalse(query.get("releaseVariants"))


        query = query_from_payload({
            "barcode": "5051892000000",
            "title": "Lethal Weapon",
            "detectBoxSets": True,
            "previewMode": True,
        })
        plan = plugin_execution_plan(
            {"capabilities": ["search_barcode", "search_title", "movie_details", "box_set_candidates"]},
            query,
        )

        entrypoints = [item["entrypoint"] for item in plan]
        self.assertEqual(entrypoints, ["search_title", "movie_details", "box_set_candidates"])
        self.assertNotIn("search_barcode", entrypoints)
        for item in plan:
            self.assertNotIn("externalBarcode", item["payload"])
            self.assertNotIn("barcode", item["payload"])
            self.assertEqual(item["payload"].get("title"), "Lethal Weapon")

    def test_bootstrap_metadata_source_runs_for_public_barcode_queries(self):
        plugin = {
            "id": "upcitemdb",
            "categories": ["metadata_source", "metadata_bootstrap"],
            "capabilities": ["search_barcode", "title_hint", "bootstrap_lookup"],
            "manifest": {"bootstrap": {"metadataSource": True}},
        }

        self.assertTrue(
            metadata_source_plugin_allowed(
                plugin,
                query_from_payload({"barcode": "5051892000000", "previewMode": True}),
            )
        )
        self.assertTrue(
            metadata_source_plugin_allowed(
                plugin,
                query_from_payload({"barcode": "5051892000000"}),
            )
        )
        self.assertTrue(
            metadata_source_plugin_allowed(
                plugin,
                query_from_payload({"barcode": "5051892000000", "title": "Alien", "previewMode": True}),
            )
        )
        self.assertTrue(
            metadata_source_plugin_allowed(
                plugin,
                query_from_payload({"barcode": "5051892000000", "tmdbId": "348", "previewMode": True}),
            )
        )
        self.assertFalse(
            metadata_source_plugin_allowed(
                plugin,
                query_from_payload({"title": "Alien", "previewMode": True}),
            )
        )

    def test_regular_metadata_source_is_allowed_outside_bootstrap_context(self):
        plugin = {
            "id": "tmdb",
            "categories": ["metadata_source"],
            "capabilities": ["search_title", "lookup_external_id", "movie_details"],
        }

        self.assertTrue(metadata_source_plugin_allowed(plugin, query_from_payload({"title": "Alien"})))

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
                    "credits": [{"role": "crew", "name": "Guy Ritchie", "job": "Director"}],
                    "candidates": [{}],
                }
            ],
            "proposal": {
                "provenance": [{"pluginId": "movievault_26", "field": "rating", "target": "movie"}],
                "skipped": [{"pluginId": "tmdb", "field": "title", "reason": "existing value retained"}],
                "credits": [{"role": "crew", "name": "Guy Ritchie", "job": "Director"}],
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
        self.assertEqual(payload["providerResults"][0]["creditCount"], 1)
        self.assertEqual(payload["creditStats"]["proposed"], 1)
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
                "metadataUpdates": {"distributor": "Walt Disney Pictures"},
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

    def test_receiver_contribution_payload_includes_person_tmdb_ids(self):
        movie = {
            "id": "m1",
            "public_id": "legacy-movie-42",
            "title": "Heat",
            "barcode": "0000000000001",
            "format": "Blu-ray",
        }
        preview = {"proposal": {}}
        payload = receiver_contribution_payload(
            movie_id="m1",
            movie=movie,
            preview=preview,
            applied={"changed": True, "applied": {}},
            credits=[
                {"name": "Al Pacino", "credit_type": "actor", "character": "Hanna", "tmdb_id": "1158"},
                {"name": "Michael Mann", "credit_type": "crew", "job": "Director", "tmdb_id": "7715", "imdb_id": "nm0000520"},
            ],
        )
        entries = payload["payload"]["credits"]
        by_name = {entry["name"]: entry for entry in entries}
        self.assertEqual(by_name["Al Pacino"]["tmdbId"], "1158")
        self.assertEqual(by_name["Michael Mann"]["tmdbId"], "7715")
        self.assertEqual(by_name["Michael Mann"]["imdbId"], "nm0000520")

    def test_receiver_contribution_payload_includes_full_current_metadata(self):
        movie = {
            "id": "9f12",
            "public_id": "legacy-movie-73",
            "title": "A Star Is Born",
            "original_title": "A Star Is Born",
            "sort_title": "Star Is Born, A",
            "year": "2018",
            "barcode": "0883929598083",
            "format": "4K UHD",
            "edition": "Special Edition",
            "country": "US",
            "language": "en",
            "runtime_minutes": 136,
            "overview": "A musician helps a young singer find fame.",
            "rating": "7.5",
            "release_date": "2018-10-05",
        }
        preview = {
            "proposal": {
                "technicalUpdates": {"audio_tracks": ["English: Dolby Atmos"]},
                "identifiers": {"tmdb": "332562", "imdb": "tt1517451"},
                "provenance": [{"pluginId": "bluray_com", "field": "audio_tracks"}],
            },
            "results": [],
        }
        credits = [
            {"name": "Bradley Cooper", "credit_type": "crew", "job": "Director", "character": ""},
            {"name": "Lady Gaga", "credit_type": "actor", "job": "", "character": "Ally"},
            {"name": "Bradley Cooper", "credit_type": "actor", "job": "", "character": "Jackson"},
        ]

        payload = receiver_contribution_payload(
            movie_id="9f12",
            movie=movie,
            preview=preview,
            applied={"changed": True, "applied": {}},
            credits=credits,
        )

        fields = payload["payload"]
        self.assertEqual(fields["overview"], "A musician helps a young singer find fame.")
        self.assertEqual(fields["releaseDate"], "2018-10-05")
        self.assertEqual(fields["runtimeMinutes"], 136)
        self.assertEqual(fields["country"], "US")
        self.assertEqual(fields["language"], "en")
        self.assertEqual(fields["rating"], "7.5")
        self.assertEqual(fields["edition"], "Special Edition")
        self.assertEqual(fields["director"], "Bradley Cooper")
        self.assertIn("Lady Gaga", fields["actor"])
        self.assertEqual(fields["audio_tracks"], ["English: Dolby Atmos"])
        self.assertEqual(len(fields["credits"]), 3)

    def test_receiver_contribution_payload_release_date_object_serialized(self):
        from datetime import date

        movie = {
            "id": "aa01",
            "public_id": "legacy-movie-12",
            "title": "Dune",
            "year": "2021",
            "format": "4K UHD",
            "release_date": date(2021, 10, 22),
        }
        payload = receiver_contribution_payload(
            movie_id="aa01",
            movie=movie,
            preview={"proposal": {}, "results": []},
            applied={"changed": True, "applied": {}},
        )
        self.assertEqual(payload["payload"]["releaseDate"], "2021-10-22")


    def test_receiver_contribution_payload_excludes_locked_fields(self):
        movie = {
            "id": "cc05",
            "public_id": "legacy-movie-44",
            "title": "Heat",
            "original_title": "Heat",
            "year": "1995",
            "barcode": "0883929000000",
            "format": "4K UHD",
            "overview": "Hand-curated synopsis kept by the collector.",
            "country": "US",
            "language": "en",
            "metadata": {
                "director": "Michael Mann",
                "distributor": "Warner",
                "field_locks": ["overview", "director", "country"],
            },
        }
        preview = {
            "proposal": {
                "metadataUpdates": {"director": "Provider Director", "distributor": "Warner Bros"},
                "provenance": [{"pluginId": "tmdb", "field": "distributor"}],
            },
            "results": [],
        }
        payload = receiver_contribution_payload(
            movie_id="cc05",
            movie=movie,
            preview=preview,
            applied={"changed": True, "applied": {}},
        )
        sent = payload["payload"]
        self.assertNotIn("overview", sent)
        self.assertNotIn("director", sent)
        self.assertNotIn("country", sent)
        self.assertEqual(sent.get("distributor"), "Warner Bros")
        self.assertEqual(sent.get("title"), "Heat")
        self.assertEqual(sent.get("language"), "en")

    def test_receiver_contribution_payload_includes_localizations(self):
        movie = {
            "id": "dd07",
            "public_id": "legacy-movie-77",
            "title": "Spirited Away",
            "year": "2001",
            "format": "4K UHD",
        }
        preview = {
            "proposal": {
                "localizations": [
                    {"lang": "fr", "title": "Le Voyage de Chihiro", "overview": "Une fille..."},
                    {"lang": "de-DE", "title": "Chihiros Reise", "overview": "Ein Maedchen..."},
                    {"lang": "pt", "overview": ""},
                ],
            },
            "results": [],
        }
        payload = receiver_contribution_payload(
            movie_id="dd07",
            movie=movie,
            preview=preview,
            applied={"changed": True, "applied": {}},
        )
        localizations = payload["payload"]["localizations"]
        self.assertEqual(len(localizations), 2)
        by_lang = {item["lang"]: item for item in localizations}
        self.assertEqual(by_lang["fr"]["title"], "Le Voyage de Chihiro")
        self.assertEqual(by_lang["fr"]["overview"], "Une fille...")
        self.assertEqual(by_lang["de-DE"]["title"], "Chihiros Reise")
        self.assertNotIn("pt", by_lang)

    def test_receiver_contribution_payload_localizations_respect_locks(self):
        movie = {
            "id": "ee08",
            "public_id": "legacy-movie-78",
            "title": "Heat",
            "year": "1995",
            "format": "4K UHD",
            "metadata": {"field_locks": ["overview"]},
        }
        preview = {
            "proposal": {
                "localizations": [
                    {"lang": "fr", "title": "Heat FR", "overview": "Synopsis FR"},
                    {"lang": "es", "overview": "Sinopsis ES"},
                ],
            },
            "results": [],
        }
        payload = receiver_contribution_payload(
            movie_id="ee08",
            movie=movie,
            preview=preview,
            applied={"changed": True, "applied": {}},
        )
        localizations = payload["payload"]["localizations"]
        by_lang = {item["lang"]: item for item in localizations}
        self.assertIn("fr", by_lang)
        self.assertEqual(by_lang["fr"].get("title"), "Heat FR")
        self.assertNotIn("overview", by_lang["fr"])
        self.assertNotIn("es", by_lang)

    @mock.patch("app.backend.next_metadata.preferred_provider_overwrite", return_value=False)
    @mock.patch("app.backend.next_metadata.plugin_requires_config", return_value=False)
    @mock.patch("app.backend.next_metadata.plugin_execution_context", return_value={})
    @mock.patch("app.backend.next_metadata.plugin_config_from_db", return_value={"apiKey": "configured"})
    @mock.patch("app.backend.next_metadata.metadata_source_plugins")
    @mock.patch("app.backend.next_metadata.run_plugin_entrypoint")
    def test_preview_tmdb_id_surfaces_titled_candidate_via_lookup_external_id(
        self,
        run_entrypoint,
        source_plugins,
        _plugin_config,
        _execution_context,
        _requires_config,
        _overwrite,
    ):
        # Bug 1: a tmdbId-only preview must resolve the title straight from the
        # TMDB response (via lookup_external_id) so the "Add Movie" import has a
        # title instead of failing with "No movie title was found".
        source_plugins.return_value = [
            {
                "id": "tmdb",
                "name": "TMDb",
                "categories": ["metadata_source"],
                "capabilities": ["search_title", "lookup_external_id", "movie_details"],
                "manifest": {"capabilities": ["search_title", "lookup_external_id", "movie_details"]},
                "order_index": 10,
            },
            {
                "id": "movievault_26",
                "name": "MovieVault",
                "categories": ["metadata_source"],
                "capabilities": ["search_title"],
                "manifest": {"capabilities": ["search_title"]},
                "order_index": 51,
            },
        ]

        def execute(plugin_id, entrypoint, payload, _context):
            self.assertFalse(
                plugin_id == "tmdb" and entrypoint == "movie_details",
                "movie_details must not run for a tmdbId preview lookup",
            )
            if plugin_id == "tmdb" and entrypoint == "lookup_external_id":
                self.assertEqual(payload.get("tmdbId"), "348")
                return {
                    "status": "ok",
                    "state": "available",
                    "elapsedMs": 3,
                    "result": {
                        "status": "hit",
                        "provider": "tmdb",
                        "movie": {"title": "Alien", "year": "1979", "overview": "TMDb plot"},
                        "tmdbId": "348",
                    },
                }
            return {"status": "ok", "state": "available", "elapsedMs": 1, "result": {"status": "miss"}}

        run_entrypoint.side_effect = execute
        result = run_metadata_source_pipeline(
            object(),
            query=query_from_payload({"tmdbId": "348", "previewMode": True}),
            current={"metadata": {}},
            technical_current={},
        )

        calls = [(call.args[0], call.args[1]) for call in run_entrypoint.call_args_list]
        self.assertIn(("tmdb", "lookup_external_id"), calls)
        self.assertNotIn(("tmdb", "movie_details"), calls)
        tmdb_results = [item for item in result["results"] if item.get("pluginId") == "tmdb"]
        self.assertTrue(tmdb_results)
        # The enrichment policy strips the title from movieUpdates for TMDB, but
        # preview must still surface a selectable candidate carrying the title
        # straight from the TMDB response so the "Add Movie" import succeeds.
        candidate_titles = [
            str(candidate.get("title") or "").strip()
            for item in tmdb_results
            for candidate in (item.get("candidates") or [])
        ]
        self.assertIn("Alien", candidate_titles)
        self.assertEqual(result["enrichment"]["tmdb"]["state"], "enriched")

    @mock.patch("app.backend.next_metadata.preferred_provider_overwrite", return_value=False)
    @mock.patch("app.backend.next_metadata.plugin_requires_config", return_value=False)
    @mock.patch("app.backend.next_metadata.plugin_execution_context", return_value={})
    @mock.patch("app.backend.next_metadata.plugin_config_from_db", return_value={"apiKey": "configured"})
    @mock.patch("app.backend.next_metadata.metadata_source_plugins")
    @mock.patch("app.backend.next_metadata.run_plugin_entrypoint")
    def test_preview_partial_title_surfaces_all_tmdb_matches_via_search_title(
        self,
        run_entrypoint,
        source_plugins,
        _plugin_config,
        _execution_context,
        _requires_config,
        _overwrite,
    ):
        # Bug 2: a partial-title preview must surface EVERY TMDB match (the
        # multi-result search_title), not just one fuzzy match. The redundant
        # exact-match movie_details detail call is dropped for title queries.
        source_plugins.return_value = [
            {
                "id": "tmdb",
                "name": "TMDb",
                "categories": ["metadata_source"],
                "capabilities": ["search_title", "lookup_external_id", "movie_details"],
                "manifest": {"capabilities": ["search_title", "lookup_external_id", "movie_details"]},
                "order_index": 10,
            },
            {
                "id": "movievault_26",
                "name": "MovieVault",
                "categories": ["metadata_source"],
                "capabilities": ["search_title"],
                "manifest": {"capabilities": ["search_title"]},
                "order_index": 51,
            },
        ]

        matches = [
            {"title": "The Devil Wears Prada", "tmdbId": "782", "year": "2006", "provider": "tmdb"},
            {"title": "The Devil Wears Prada 2", "tmdbId": "1195506", "year": "2026", "provider": "tmdb"},
            {"title": "The Devil Wears Nada", "tmdbId": "55555", "year": "2009", "provider": "tmdb"},
        ]

        def execute(plugin_id, entrypoint, payload, _context):
            self.assertFalse(
                plugin_id == "tmdb" and entrypoint == "movie_details",
                "movie_details must not run when search_title covers a title preview",
            )
            if plugin_id == "tmdb" and entrypoint == "search_title":
                self.assertEqual(payload.get("title"), "The Devil Wears")
                return {
                    "status": "ok",
                    "state": "available",
                    "elapsedMs": 4,
                    "result": {"status": "hit", "provider": "tmdb", "items": matches},
                }
            return {"status": "ok", "state": "available", "elapsedMs": 1, "result": {"status": "miss"}}

        run_entrypoint.side_effect = execute
        result = run_metadata_source_pipeline(
            object(),
            query=query_from_payload({"title": "The Devil Wears", "previewMode": True}),
            current={"metadata": {}},
            technical_current={},
        )

        calls = [(call.args[0], call.args[1]) for call in run_entrypoint.call_args_list]
        self.assertIn(("tmdb", "search_title"), calls)
        self.assertNotIn(("tmdb", "movie_details"), calls)
        tmdb_results = [
            item
            for item in result["results"]
            if item.get("pluginId") == "tmdb" and item.get("entrypoint") == "search_title"
        ]
        self.assertTrue(tmdb_results)
        self.assertEqual(len(tmdb_results[0]["candidates"]), 3)
        self.assertEqual(result["enrichment"]["tmdb"]["state"], "enriched")


class NextArtworkLockTests(unittest.TestCase):
    def test_poster_and_backdrop_are_lockable_fields(self):
        self.assertIn("poster", MOVIE_LOCKABLE_FIELDS)
        self.assertIn("backdrop", MOVIE_LOCKABLE_FIELDS)

    def test_movie_locked_fields_recognizes_artwork_locks(self):
        metadata = {"field_locks": ["poster", "backdrop", "overview"]}
        locked = movie_locked_fields(metadata)
        self.assertIn("poster", locked)
        self.assertIn("backdrop", locked)

    def test_locked_poster_metadata_url_is_stripped(self):
        metadata_updates = {"poster_url": "http://new/poster.jpg", "overview": "New"}
        media_updates = {"poster": {"url": "http://new/poster.jpg"}}
        result_meta, result_media = filter_locked_artwork_updates(
            metadata_updates,
            media_updates,
            metadata_locked_kinds={"poster"},
            media_locked_kinds={"poster"},
        )
        self.assertNotIn("poster_url", result_meta)
        self.assertEqual(result_meta.get("overview"), "New")
        self.assertNotIn("poster", result_media)

    def test_locked_backdrop_variants_are_stripped(self):
        metadata_updates = {
            "backdrop_url": "http://x/b.jpg",
            "backdropUrl": "http://x/b2.jpg",
            "backdrop": "http://x/b3.jpg",
        }
        result_meta, _ = filter_locked_artwork_updates(
            metadata_updates,
            {},
            metadata_locked_kinds={"backdrop"},
            media_locked_kinds=set(),
        )
        self.assertEqual(result_meta, {})

    def test_media_asset_lock_keeps_media_option(self):
        # metadata URL protected, but media still allowed as a non-primary option
        metadata_updates = {"poster_url": "http://new/poster.jpg"}
        media_updates = {"poster": {"url": "http://new/poster.jpg"}}
        result_meta, result_media = filter_locked_artwork_updates(
            metadata_updates,
            media_updates,
            metadata_locked_kinds={"poster"},
            media_locked_kinds=set(),
        )
        self.assertNotIn("poster_url", result_meta)
        self.assertIn("poster", result_media)

    def test_unlocked_artwork_is_untouched(self):
        metadata_updates = {"poster_url": "http://new/poster.jpg"}
        media_updates = {"poster": {"url": "http://new/poster.jpg"}}
        result_meta, result_media = filter_locked_artwork_updates(
            metadata_updates,
            media_updates,
            metadata_locked_kinds=set(),
            media_locked_kinds=set(),
        )
        self.assertEqual(result_meta.get("poster_url"), "http://new/poster.jpg")
        self.assertIn("poster", result_media)

    def test_inputs_are_not_mutated(self):
        metadata_updates = {"poster_url": "http://new/poster.jpg"}
        media_updates = {"poster": {"url": "http://new/poster.jpg"}}
        filter_locked_artwork_updates(
            metadata_updates,
            media_updates,
            metadata_locked_kinds={"poster"},
            media_locked_kinds={"poster"},
        )
        self.assertIn("poster_url", metadata_updates)
        self.assertIn("poster", media_updates)


if __name__ == "__main__":
    unittest.main()
