"""The v2 fallback for a disc the local route could not identify.

Covers the two halves the Add flow depends on: how a resolver answer is
normalized for the client (and in particular how a *failure* is described, so a
client never reports "not in the catalogue" for an outage), and how a chosen
edition is mapped onto a movie-upsert payload.
"""

import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_app


def candidates_result() -> dict:
    return {
        "contractVersion": "release-technical-1",
        "status": "candidates",
        "verificationStatus": "unreviewed_external",
        "film": {
            "title": "Example Film",
            "year": 2024,
            "identifiers": {"tmdbMovieId": "123", "imdbId": "tt1234567"},
            "links": {
                "tmdb": "https://www.themoviedb.org/movie/123",
                "imdb": "https://www.imdb.com/title/tt1234567/",
            },
        },
        "releases": [
            {"releaseRef": "a", "source": "external", "title": "Example Film"},
            {"releaseRef": "b", "source": "external", "title": "Example Film"},
        ],
    }


class ReleaseDetailsSearchPayloadTests(unittest.TestCase):
    def test_candidates_are_passed_through_with_the_film_identifiers(self):
        payload = next_app.release_details_search_payload(
            candidates_result(),
            entrypoint="search",
        )

        self.assertEqual(payload["status"], "candidates")
        self.assertEqual(payload["entrypoint"], "search")
        self.assertTrue(payload["answered"])
        self.assertEqual(payload["verificationStatus"], "unreviewed_external")
        self.assertEqual(
            payload["film"],
            {
                "title": "Example Film",
                "year": 2024,
                "tmdbMovieId": "123",
                "imdbId": "tt1234567",
            },
        )
        self.assertEqual([item["releaseRef"] for item in payload["releases"]], ["a", "b"])

    def test_a_hit_is_expressed_as_a_single_row_in_the_same_shape(self):
        # A confirmed release and a list of pressings are different answers -
        # `status` keeps them apart - but each row describes one physical
        # release, so they share one shape rather than drifting into two.
        payload = next_app.release_details_search_payload(
            {
                "contractVersion": "release-technical-1",
                "status": "external_hit",
                "verificationStatus": "unreviewed_external",
                "film": {"title": "Example Film", "year": 2024, "identifiers": {}},
                "release": {
                    "title": "Example Film",
                    "format": "4K UHD",
                    "edition": "SteelBook",
                    "regions": ["B"],
                    "subtitleLanguages": ["en"],
                    "barcodes": [
                        {"type": "ean13", "value": "4006381333931", "scope": "package"}
                    ],
                },
            },
            entrypoint="resolve",
        )

        self.assertEqual(payload["status"], "external_hit")
        self.assertEqual(len(payload["releases"]), 1)
        row = payload["releases"][0]
        self.assertEqual(row["source"], "external")
        self.assertEqual(row["releaseRef"], "")
        self.assertEqual(row["discRegions"], ["B"])
        self.assertEqual(row["subtitleLanguages"], ["en"])

    def test_a_transport_failure_is_never_reported_as_answered(self):
        for code, kind, retryable in (
            ("release_details_unreachable", "transport", True),
            ("release_details_network_error", "transport", False),
            ("release_details_unavailable", "unavailable", True),
            ("release_details_poll_timeout", "pending", True),
            ("release_details_expired", "expired", True),
        ):
            with self.subTest(code=code):
                payload = next_app.release_details_search_payload(
                    {"status": "failed", "errorCode": code},
                    entrypoint="resolve",
                )
                self.assertEqual(payload["failureKind"], kind)
                self.assertEqual(payload["retryable"], retryable)
                self.assertFalse(payload["answered"])

    def test_a_server_owned_no_data_failure_is_an_answer(self):
        for code, kind in (
            ("no_approved_sources", "no_data"),
            ("ambiguous_title", "needs_year"),
            ("canonical_release_unusable", "catalog_defect"),
        ):
            with self.subTest(code=code):
                payload = next_app.release_details_search_payload(
                    {"status": "failed", "errorCode": code},
                    entrypoint="search",
                )
                self.assertEqual(payload["failureKind"], kind)
                self.assertFalse(payload["retryable"])
                self.assertTrue(payload["answered"])

    def test_a_miss_carries_no_releases_and_no_failure(self):
        payload = next_app.release_details_search_payload(
            {"status": "miss"},
            entrypoint="resolve",
        )

        self.assertEqual(payload["status"], "miss")
        self.assertEqual(payload["releases"], [])
        self.assertTrue(payload["answered"])
        self.assertNotIn("failureKind", payload)


class ReleaseCandidateMoviePayloadTests(unittest.TestCase):
    def test_every_field_the_edition_governs_is_emitted(self):
        payload = next_app.release_candidate_movie_payload(
            {
                "releaseRef": "a",
                "source": "external",
                "title": "Example Film - Collector's Edition",
                "format": "4K UHD",
                "edition": "SteelBook",
                "countryCode": "NL",
                "languageCode": "en",
                "releaseDate": "2024-05-01",
                "runtimeMinutes": 170,
                "discRegions": ["B"],
                "packaging": ["steelbook"],
                "audioTracks": [{"languageCode": "en", "codec": "dolby_truehd"}],
                "subtitles": [{"languageCode": "nl", "subtitleType": "full"}],
                "video": {
                    "resolution": "2160p",
                    "codecs": ["hevc"],
                    "hdrFormats": ["dolby_vision"],
                    "aspectRatios": ["2.39:1"],
                },
            }
        )

        self.assertEqual(
            payload,
            {
                "releaseTitle": "Example Film - Collector's Edition",
                "format": "4K UHD",
                "edition": "SteelBook",
                "country": "NL",
                "language": "en",
                "releaseDate": "2024-05-01",
                "runtimeMinutes": 170,
                "packaging": ["steelbook"],
                "discRegions": ["B"],
                "audioTracks": [{"languageCode": "en", "codec": "dolby_truehd"}],
                "subtitles": [{"languageCode": "nl", "subtitleType": "full"}],
                "videoResolution": "2160p",
                "videoCodecs": ["hevc"],
                "hdrFormats": ["dolby_vision"],
                "aspectRatios": ["2.39:1"],
            },
        )

    def test_a_candidate_without_a_format_leaves_the_format_alone(self):
        # Format has no unset value, so blanking it would be a guess - and a
        # wrong format attaches a wrong technical profile to the disc.
        payload = next_app.release_candidate_movie_payload(
            {"releaseRef": "a", "source": "canonical", "title": "Example Film"}
        )

        self.assertNotIn("format", payload)
        self.assertEqual(payload, {"releaseTitle": "Example Film"})

    def test_flat_subtitle_languages_are_lifted_only_without_tracks(self):
        lifted = next_app.release_candidate_movie_payload(
            {"title": "Example Film", "subtitleLanguages": ["en", "nl"]}
        )
        self.assertEqual(
            lifted["subtitles"],
            [
                {"languageCode": "en", "subtitleType": "full"},
                {"languageCode": "nl", "subtitleType": "full"},
            ],
        )

        structured = next_app.release_candidate_movie_payload(
            {
                "title": "Example Film",
                "subtitles": [{"languageCode": "en", "subtitleType": "sdh"}],
                "subtitleLanguages": ["en"],
            }
        )
        self.assertEqual(
            structured["subtitles"],
            [{"languageCode": "en", "subtitleType": "sdh"}],
        )

    def test_an_empty_list_is_kept_so_a_second_pick_can_clear_the_first(self):
        # Picking a second edition must fully reassign what an edition governs;
        # an explicit empty list is how the previous choice's tracks are cleared.
        payload = next_app.release_candidate_movie_payload(
            {"title": "Example Film", "audioTracks": [], "packaging": []}
        )

        self.assertEqual(payload["audioTracks"], [])
        self.assertEqual(payload["packaging"], [])

    def test_a_missing_candidate_contributes_nothing(self):
        self.assertEqual(next_app.release_candidate_movie_payload({}), {})
        self.assertEqual(next_app.release_candidate_movie_payload(None), {})


class ReleaseCandidateOnExistingMovieTests(unittest.TestCase):
    """A chosen edition must be applicable to a disc that is already on the shelf.

    The movie detail page fills the disc locally through the ordinary edit
    route - MovieVault has no server-side choice endpoint - so every key the
    candidate mapper emits has to be one the movie edit payload actually reads.
    A key that neither `movie_update_payload` nor `movie_technical_edits`
    consumes would be dropped silently, and the picked edition would only
    partially land."""

    CANDIDATE = {
        "releaseRef": "discovery_abcdef123456",
        "source": "external",
        "title": "Example Film - Collector's Edition",
        "edition": "Collector's Edition",
        "format": "4K UHD",
        "countryCode": "NL",
        "languageCode": "en",
        "releaseDate": "2024-05-01",
        "discCount": 2,
        "runtimeMinutes": 148,
        "discRegions": ["FREE"],
        "packaging": ["steelbook"],
        "video": {
            "resolution": "2160p",
            "codecs": ["hevc"],
            "hdrFormats": ["dolby_vision"],
            "aspectRatios": ["2.39:1"],
        },
        "audioTracks": [
            {"languageCode": "en", "codec": "dolby_truehd", "channels": "7.1", "immersiveFormat": "dolby_atmos"}
        ],
        "subtitles": [{"languageCode": "en", "subtitleType": "sdh"}],
    }

    def test_candidate_payload_lands_on_movie_columns_and_technical_edits(self):
        body = next_app.release_candidate_movie_payload(dict(self.CANDIDATE))
        existing = {"title": "Example Film", "barcode": "4006381333931"}

        payload = next_app.movie_update_payload(body, existing=existing)

        self.assertEqual(payload["title"], "Example Film")
        self.assertEqual(payload["release_title"], "Example Film - Collector's Edition")
        self.assertEqual(payload["edition"], "Collector's Edition")
        self.assertEqual(payload["format"], "4K UHD")
        self.assertEqual(payload["country"], "NL")
        self.assertEqual(payload["language"], "en")
        self.assertEqual(payload["runtime_minutes"], 148)
        self.assertEqual(str(payload["release_date"]), "2024-05-01")
        technical = payload["technical_edits"]
        self.assertEqual(technical["regions"], ["FREE"])
        self.assertEqual(technical["packaging"], ["steelbook"])
        self.assertEqual(technical["video_resolution"], "2160p")
        self.assertEqual(technical["video_codecs"], ["hevc"])
        self.assertEqual(technical["hdr"], ["dolby_vision"])
        self.assertEqual(technical["screen_ratios"], ["2.39:1"])
        self.assertEqual(technical["audio_tracks"][0]["codec"], "dolby_truehd")
        self.assertEqual(technical["subtitles"][0]["subtitleType"], "sdh")

    def test_a_second_pick_reassigns_the_first_picks_tracks(self):
        # The first pick carried audio and subtitles; the second names none.
        # An explicit empty list clears them - fill-if-empty would let the
        # first edition's tracks survive into the second.
        body = next_app.release_candidate_movie_payload(
            {"title": "Example Film", "audioTracks": [], "subtitles": [], "packaging": []}
        )

        payload = next_app.movie_update_payload(
            body, existing={"title": "Example Film", "barcode": "4006381333931"}
        )

        technical = payload["technical_edits"]
        self.assertEqual(technical["audio_tracks"], [])
        self.assertEqual(technical["subtitles"], [])
        self.assertEqual(technical["packaging"], [])


if __name__ == "__main__":
    unittest.main()
