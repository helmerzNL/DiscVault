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
            {
                "releaseRef": "a",
                "source": "external",
                "title": "Example Film",
                "finishes": [],
            },
            {
                "releaseRef": "b",
                "source": "external",
                "title": "Example Film",
                "finishes": ["holofoil"],
            },
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

    def test_barcode_confirmation_is_carried_over_when_movievault_states_it(self):
        for confirmed in (True, False):
            with self.subTest(confirmed=confirmed):
                result = candidates_result()
                result["barcodeConfirmed"] = confirmed
                payload = next_app.release_details_search_payload(
                    result,
                    entrypoint="resolve",
                )

                self.assertEqual(payload["barcodeConfirmed"], confirmed)

    def test_an_unstated_barcode_confirmation_stays_unstated(self):
        """Absent is a third state, and the client has to be able to see it.

        The picker's cautious line - "none of these pressings confirmed the
        barcode" - is true on the title routes and false on the barcode one, so
        the client picks between two sentences on this field. Defaulting the
        absent case to `False` would put that claim in its hands on the say-so
        of an older MovieVault that said nothing at all, so the key is left out
        entirely and the client keeps the sentence that cannot be wrong.
        """
        for value in (None, "true", 1, {}):
            with self.subTest(value=value):
                result = candidates_result()
                if value is not None:
                    result["barcodeConfirmed"] = value
                payload = next_app.release_details_search_payload(
                    result,
                    entrypoint="resolve",
                )

                self.assertNotIn("barcodeConfirmed", payload)

    def test_barcode_confirmation_is_not_invented_for_a_single_release(self):
        # It describes a choice between pressings. A `canonical_hit` presents no
        # choice, so there is nothing for the field to qualify.
        payload = next_app.release_details_search_payload(
            {
                "status": "canonical_hit",
                "barcodeConfirmed": True,
                "film": {"title": "Example Film", "year": 2024, "identifiers": {}},
                "release": {"title": "Example Film", "format": "4K UHD"},
            },
            entrypoint="resolve",
        )

        self.assertNotIn("barcodeConfirmed", payload)

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

    def test_a_failure_discvault_owns_is_not_reported_as_the_servers(self):
        """A payload this client refused is a DiscVault defect.

        These four codes are raised on DiscVault's side of the wire, so falling
        through to the `server` default sent the next investigation at
        MovieVault - which is what happened for the `finishes` outage, whose
        barcode MovieVault had resolved correctly. `answered` stays true because
        MovieVault did reply; nothing here may be phrased as a miss.
        """
        for code in (
            "release_details_response_invalid",
            "release_details_request_invalid",
            "release_details_retry_after_invalid",
            "release_details_response_too_large",
        ):
            with self.subTest(code=code):
                payload = next_app.release_details_search_payload(
                    {"status": "failed", "errorCode": code},
                    entrypoint="resolve",
                )
                self.assertEqual(payload["failureKind"], "client")
                self.assertFalse(payload["retryable"])
                self.assertTrue(payload["answered"])

    def test_an_unclassified_code_still_falls_back_to_server(self):
        payload = next_app.release_details_search_payload(
            {"status": "failed", "errorCode": "something_new"},
            entrypoint="resolve",
        )

        self.assertEqual(payload["failureKind"], "server")

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
                "finishes": ["holofoil"],
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
                # The flat list is split onto the axes here; the mapper never
                # emits `packaging`, which the movie edit refuses from a caller.
                "carrierType": "steelbook",
                "outerPackaging": [],
                "finishes": ["holofoil"],
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
        # The empty flat list splits into an empty carrier and an empty outer
        # list - both of which clear, rather than being omitted as "no opinion".
        self.assertEqual(payload["carrierType"], "")
        self.assertEqual(payload["outerPackaging"], [])
        self.assertNotIn("packaging", payload)

    def test_a_finish_this_repo_does_not_know_is_dropped_rather_than_refused(self):
        """`finishes` is the one key here the movie edit validates strictly.

        `_movie_edit_case_axes` answers an unknown value with a 422, while the
        resolver deliberately keeps vocabulary it has not heard of so that one
        new finish name cannot cost a whole resolve answer. Unfiltered, the two
        rules would collide on exactly the discs where MovieVault is ahead of
        DiscVault, and "Use this edition" would fail outright.
        """
        payload = next_app.release_candidate_movie_payload(
            {"title": "Example Film", "finishes": ["holofoil", "mirror_foil"]}
        )

        self.assertEqual(payload["finishes"], ["holofoil"])

    def test_an_all_unknown_finish_list_becomes_empty_rather_than_absent(self):
        # Absent means "no opinion" and an empty list clears, so a candidate
        # that named only finishes DiscVault cannot represent still says that
        # it knows of none - it does not leave the previous pick's finish on.
        payload = next_app.release_candidate_movie_payload(
            {"title": "Example Film", "finishes": ["mirror_foil"]}
        )

        self.assertEqual(payload["finishes"], [])

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
        "finishes": ["holofoil"],
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
        # MovieVault's flat list is split onto the axes at the mapper. The flat
        # column is derived from them at write time, so it is not an edit key.
        self.assertEqual(technical["carrier_type"], "steelbook")
        self.assertEqual(technical["outer_packaging"], [])
        self.assertNotIn("packaging", technical)
        self.assertTrue(technical["_derive_packaging"])
        self.assertEqual(technical["video_resolution"], "2160p")
        self.assertEqual(technical["video_codecs"], ["hevc"])
        self.assertEqual(technical["hdr"], ["dolby_vision"])
        self.assertEqual(technical["screen_ratios"], ["2.39:1"])
        self.assertEqual(technical["audio_tracks"][0]["codec"], "dolby_truehd")
        self.assertEqual(technical["subtitles"][0]["subtitleType"], "sdh")
        self.assertEqual(technical["finishes"], ["holofoil"])

    def test_a_finish_does_not_wipe_the_picked_packaging(self):
        """`finishes` and the case axes are separate axes on the same edit.

        A candidate carrying both must land both: the finish is an orthogonal
        tag set and must not displace the carrier the same pick supplied. If
        that ever changes, a picked edition quietly loses its packaging.
        """
        body = next_app.release_candidate_movie_payload(dict(self.CANDIDATE))

        payload = next_app.movie_update_payload(
            body, existing={"title": "Example Film", "barcode": "4006381333931"}
        )

        technical = payload["technical_edits"]
        self.assertEqual(technical["carrier_type"], "steelbook")
        self.assertEqual(technical["finishes"], ["holofoil"])

    def test_the_finer_vocabulary_survives_a_pick(self):
        """A distribution-5 candidate names terms the flat nine cannot express.

        Passing the list through used to discard them - only the six legacy
        carrier aliases were recognised anywhere - so `futurepak` and `fullslip`
        reached the column as raw text and never reached the axes at all.
        """
        candidate = dict(self.CANDIDATE, packaging=["futurepak", "fullslip"])
        body = next_app.release_candidate_movie_payload(candidate)

        payload = next_app.movie_update_payload(
            body, existing={"title": "Example Film", "barcode": "4006381333931"}
        )

        technical = payload["technical_edits"]
        self.assertEqual(technical["carrier_type"], "futurepak")
        self.assertEqual(technical["outer_packaging"], ["fullslip"])

    def test_an_unknown_packaging_term_is_dropped_rather_than_rejected(self):
        """Lenient from the feed, strict from a client (§4.7a).

        The resolver keeps vocabulary it has not heard of so one new term cannot
        cost a whole resolve answer. That value must not then reach the strict
        validation and turn "Use this edition" into a 422.
        """
        candidate = dict(self.CANDIDATE, packaging=["steelbook", "shrinkwrapped"])
        body = next_app.release_candidate_movie_payload(candidate)

        payload = next_app.movie_update_payload(
            body, existing={"title": "Example Film", "barcode": "4006381333931"}
        )

        technical = payload["technical_edits"]
        self.assertEqual(technical["carrier_type"], "steelbook")
        self.assertEqual(technical["outer_packaging"], [])

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
        # The carrier is a scalar, so its clear is a None rather than a []. The
        # mirror follows from the cleared axes at write time.
        self.assertIsNone(technical["carrier_type"])
        self.assertEqual(technical["outer_packaging"], [])


if __name__ == "__main__":
    unittest.main()
