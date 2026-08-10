import json
import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_plugins.movievault_v2 import plugin as movievault_v2
from app.backend import next_metadata, next_movievault_v2


_AUDIO_TRACKS = [
    {"languageCode": "en", "codec": "dolby_truehd", "channels": "7.1", "immersiveFormat": "dolby_atmos"},
    {"languageCode": "nl", "codec": "dolby_digital", "channels": "5.1", "immersiveFormat": None},
]
# Structured since MovieVault PR #162: the same language twice with different
# variants is exactly what the old bare-language list could not express.
_SUBTITLES = [
    {"languageCode": "en", "subtitleType": "full"},
    {"languageCode": "en", "subtitleType": "sdh"},
    {"languageCode": "nl", "subtitleType": "full"},
]
_SUBTITLE_LANGUAGES = ["en", "nl"]  # the resolver path, which has no variant concept
_PACKAGING = ["steelbook", "slipcover"]
_VIDEO = {
    "videoResolution": "2160p",
    "videoCodecs": ["hevc"],
    "hdrFormats": ["dolby_vision", "hdr10"],
    "aspectRatios": ["2.39:1"],
    "discRegions": ["B"],
}


def _synced_release(**overrides):
    record = {
        "recordType": "release",
        "releaseId": "10000000-0000-0000-0000-000000000001",
        "filmId": "20000000-0000-0000-0000-000000000001",
        "canonicalTitle": "Example Film",
        "releaseTitle": "Example Film",
        "releaseYear": 2024,
        "format": "4K UHD",
        "edition": "Theatrical",
        "studio": "Example Studio",
        "distributor": "Example Distribution",
        "runtimeMinutes": 122,
        "audioTracks": _AUDIO_TRACKS,
        "subtitles": _SUBTITLES,
        "packaging": _PACKAGING,
        **_VIDEO,
    }
    record.update(overrides)
    return record


class BarcodeHashTests(unittest.TestCase):
    """MovieVault assigned the barcode, not DiscVault - only shape (digits-only,
    a length EAN/UPC/GTIN actually use) gates whether a lookup is attempted.
    A check digit that disagrees with the textbook mod-10 formula is real, if
    unusual, in retail packaging and is MovieVault's call to make, not a reason
    for DiscVault to refuse to even ask. This matches DiscVaultApp's iOS hasher
    (`V4BarcodeHasher.normalize()`), which never validates the check digit."""

    def test_a_valid_length_barcode_hashes_regardless_of_its_check_digit(self):
        import hashlib

        # "4006381333931" is a real EAN-13 with a correct check digit; flipping
        # the last digit makes the mod-10 checksum wrong while keeping the
        # barcode a well-formed 13-digit EAN.
        wrong_check_digit = "4006381333930"
        self.assertEqual(
            movievault_v2._barcode_hash(wrong_check_digit),
            hashlib.sha256(wrong_check_digit.encode("ascii")).hexdigest(),
        )

    def test_hashes_any_digit_string_of_a_valid_length(self):
        import hashlib

        for digits in ("12345678", "123456789012", "1234567890123", "12345678901234"):
            with self.subTest(digits=digits):
                self.assertEqual(
                    movievault_v2._barcode_hash(digits),
                    hashlib.sha256(digits.encode("ascii")).hexdigest(),
                )

    def test_rejects_the_wrong_length(self):
        for digits in ("1234567", "123456789", "123456789012345"):
            with self.subTest(digits=digits):
                self.assertEqual(movievault_v2._barcode_hash(digits), "")

    def test_rejects_non_digit_characters(self):
        self.assertEqual(movievault_v2._barcode_hash("not-a-barcode"), "")

    def test_strips_separators_before_hashing(self):
        import hashlib

        self.assertEqual(
            movievault_v2._barcode_hash("4006-3813-33931"),
            hashlib.sha256(b"4006381333931").hexdigest(),
        )


class ReleaseMappingTests(unittest.TestCase):
    def test_release_carries_the_whole_technical_profile_into_the_movie_dict(self):
        item = movievault_v2._release(_synced_release())
        self.assertEqual(item["movie"]["audioTracks"], _AUDIO_TRACKS)
        self.assertEqual(item["movie"]["subtitles"], _SUBTITLES)
        self.assertEqual(item["movie"]["packaging"], _PACKAGING)
        self.assertEqual(item["movie"]["videoResolution"], "2160p")
        self.assertEqual(item["movie"]["videoCodecs"], ["hevc"])
        self.assertEqual(item["movie"]["hdrFormats"], ["dolby_vision", "hdr10"])
        self.assertEqual(item["movie"]["aspectRatios"], ["2.39:1"])
        self.assertEqual(item["movie"]["discRegions"], ["B"])
        # Already-wired fields stay wired.
        self.assertEqual(item["movie"]["edition"], "Theatrical")
        self.assertEqual(item["movie"]["releaseTitle"], "Example Film")

    def test_release_omits_empty_audio_and_subtitle_arrays(self):
        item = movievault_v2._release(_synced_release(audioTracks=[], subtitles=[]))
        self.assertNotIn("audioTracks", item["movie"])
        self.assertNotIn("subtitles", item["movie"])

    def test_release_omits_empty_packaging_array(self):
        item = movievault_v2._release(_synced_release(packaging=[]))
        self.assertNotIn("packaging", item["movie"])

    def test_the_release_id_travels_as_an_identifier_row(self):
        """`releaseId` on the result is read and discarded; only an identifier
        row is persisted, and that is what makes the catalog link survive."""
        item = movievault_v2._release(_synced_release())
        self.assertEqual(
            item["identifiers"],
            [{
                "provider_id": "movievault_v2",
                "identifier": "10000000-0000-0000-0000-000000000001",
                "identifier_type": "movie_id",
            }],
        )

    def test_the_film_id_is_not_offered_as_an_identifier(self):
        """A movie gets one `movie_id` slot per provider, and `movie_details`
        resolves releases -- a film id there would evict the id that works."""
        item = movievault_v2._release(_synced_release())
        emitted = {row["identifier"] for row in item["identifiers"]}
        self.assertNotIn("20000000-0000-0000-0000-000000000001", emitted)

    def test_a_resolver_hit_without_a_release_id_emits_no_identifier(self):
        """The resolver has no local identity for a release never synced, so
        there is nothing to claim -- an empty row would be a false link."""
        record = _synced_release()
        record.pop("releaseId")
        item = movievault_v2._release(record)
        self.assertNotIn("identifiers", item)


class SeriesIdentityTests(unittest.TestCase):
    """What the plugin says about the series a television release belongs to.

    The identity has to travel by id. Matching on a title instead would mint a
    fresh series for every edition of the same show, which is the failure this
    whole block exists to prevent.
    """

    def _tv(self, **overrides):
        record = _synced_release(
            workType="tv",
            providerIds={"tmdb": "1399", "tmdb_tv": "1399"},
            seasons=[
                {"seasonNumber": 1, "title": "Season One", "releaseYear": 2011, "episodeCount": 10},
                {"seasonNumber": 2, "title": None, "releaseYear": None, "episodeCount": None},
            ],
        )
        record.update(overrides)
        return record

    def test_a_television_release_carries_its_series(self):
        series = movievault_v2._release(self._tv())["series"]
        self.assertEqual(series["tmdbTvId"], "1399")
        self.assertEqual(series["title"], "Example Film")
        self.assertEqual([s["seasonNumber"] for s in series["seasons"]], [1, 2])
        # Absent facts are dropped rather than sent as nulls, matching the rest
        # of this mapper: a key the feed did not fill proposes nothing.
        self.assertEqual(series["seasons"][1], {"seasonNumber": 2})

    def test_a_film_carries_no_series_even_with_a_tv_id(self):
        """The schema forbids a series link on a MOVIE (`movies_series_requires_show`),
        so proposing one would be proposing a constraint violation."""
        record = self._tv(workType="movie")
        self.assertNotIn("series", movievault_v2._release(record))

    def test_a_release_with_no_work_type_carries_no_series(self):
        """An absent type is "the feed has not said", never "film". It must not
        be enough to establish a series either way."""
        record = self._tv()
        record.pop("workType")
        self.assertNotIn("series", movievault_v2._release(record))

    def test_a_series_without_a_tmdb_tv_id_is_not_proposed(self):
        """There is nothing to resolve it by. Falling back to the title here is
        exactly the mistake this design refuses to make."""
        record = self._tv(providerIds={"tmdb": "1399"})
        self.assertNotIn("series", movievault_v2._release(record))

    def test_the_movie_tv_id_is_never_mistaken_for_the_series_id(self):
        """`tmdb` and `tmdb_tv` are separate namespaces upstream precisely so a
        work can carry both. Reading the wrong one would resolve a series by a
        film's id."""
        record = self._tv(providerIds={"tmdb": "603", "tmdb_tv": "1399"})
        self.assertEqual(movievault_v2._release(record)["series"]["tmdbTvId"], "1399")

    def test_a_series_with_no_curated_seasons_still_travels(self):
        """A show whose seasons nobody has recorded yet is still a series, and
        linking the disc to it is worth doing on its own."""
        series = movievault_v2._release(self._tv(seasons=[]))["series"]
        self.assertEqual(series["tmdbTvId"], "1399")
        # Omitted rather than sent empty: an empty list would read as "this
        # release covers no seasons", which is a statement this plugin cannot
        # tell apart from "nobody has curated them".
        self.assertNotIn("seasons", series)

    def test_a_tvdb_identifier_never_appears(self):
        """It cannot reach us -- MovieVault filters `tvdb` out of providerIds
        while its licence question is open -- and nothing here would read it if
        it did."""
        record = self._tv(providerIds={"tmdb_tv": "1399", "tvdb": "121361"})
        series = movievault_v2._release(record)["series"]
        self.assertEqual(series["tmdbTvId"], "1399")
        self.assertNotIn("tvdbId", series)
        self.assertNotIn("121361", json.dumps(series))


class SearchBarcodeResolverFallbackTests(unittest.TestCase):
    def _context(self, *, lookup_results, resolver_result=None):
        calls = {"resolver": 0}

        def lookup(_request):
            return {"results": lookup_results}

        def resolver(_request):
            calls["resolver"] += 1
            return resolver_result or {}

        return {"movievaultV2Lookup": lookup, "movievaultV2ReleaseDetails": resolver}, calls

    def test_fills_missing_technical_specs_from_the_live_resolver(self):
        context, calls = self._context(
            lookup_results=[_synced_release(audioTracks=[], subtitles=[], packaging=[])],
            resolver_result={
                "status": "canonical_hit",
                "release": {
                    "audioTracks": _AUDIO_TRACKS,
                    "subtitleLanguages": _SUBTITLE_LANGUAGES,
                    "packaging": _PACKAGING,
                },
            },
        )
        result = movievault_v2.search_barcode({"barcode": "9781234567897"}, context)
        self.assertEqual(result["movie"]["audioTracks"], _AUDIO_TRACKS)
        # The resolver has no variant concept, so its languages are lifted to `full`
        # rather than left in a second, incompatible shape.
        self.assertEqual(
            result["movie"]["subtitles"],
            [
                {"languageCode": "en", "subtitleType": "full"},
                {"languageCode": "nl", "subtitleType": "full"},
            ],
        )
        self.assertEqual(result["movie"]["packaging"], _PACKAGING)
        self.assertEqual(calls["resolver"], 1)

    def test_does_not_overwrite_technical_specs_already_present_from_sync(self):
        context, calls = self._context(
            lookup_results=[_synced_release()],
            resolver_result={
                "status": "canonical_hit",
                "release": {"audioTracks": [], "subtitleLanguages": [], "packaging": []},
            },
        )
        result = movievault_v2.search_barcode({"barcode": "9781234567897"}, context)
        self.assertEqual(result["movie"]["audioTracks"], _AUDIO_TRACKS)
        # Crucially the SDH row from sync survives - the resolver cannot see it and
        # must not be allowed to flatten it away.
        self.assertEqual(result["movie"]["subtitles"], _SUBTITLES)
        self.assertEqual(result["movie"]["packaging"], _PACKAGING)

    def test_box_set_resolver_technical_fields_are_never_pulled_from_the_box_set_section(self):
        details = {
            "status": "canonical_hit",
            "boxSet": {
                "audioTracks": _AUDIO_TRACKS,
                "subtitleLanguages": _SUBTITLE_LANGUAGES,
                "packaging": _PACKAGING,
            },
        }
        self.assertEqual(movievault_v2._resolved_technical(details), {})


class MovieDetailsRefreshTests(unittest.TestCase):
    """A metadata refresh reaches this entrypoint, and `movievault_identification_plan` hands it
    the movie's stored `movieVaultId` — never a `releaseId`. Reading only `releaseId`/`id` meant
    the lookup ran with an empty string, the catalog raised `record_invalid`, and the whole
    plugin execution failed — so a refresh returned no audio/subtitles/packaging at all."""

    RELEASE_ID = "10000000-0000-0000-0000-000000000001"

    def _context(self, *, raises=False, seen=None):
        def lookup(request):
            if seen is not None:
                seen.append(request)
            if raises:
                raise RuntimeError("catalog unavailable")
            return {"results": [_synced_release()]}

        return {"movievaultV2Lookup": lookup}

    def test_a_refresh_payload_carrying_movie_vault_id_reaches_the_catalog(self):
        seen = []
        result = movievault_v2.movie_details(
            {"movieVaultId": self.RELEASE_ID, "title": "Example Film"},
            self._context(seen=seen),
        )
        self.assertEqual(result["status"], "hit")
        self.assertEqual(seen[0]["releaseId"], self.RELEASE_ID)
        self.assertEqual(result["movie"]["audioTracks"], _AUDIO_TRACKS)
        self.assertEqual(result["movie"]["packaging"], _PACKAGING)

    def test_a_non_uuid_legacy_id_is_not_looked_up_as_a_v4_release(self):
        # A movievault_26-era id lives in another namespace; treating it as a v4 release UUID
        # would silently return a different disc's technical data.
        seen = []
        result = movievault_v2.movie_details({"movieVaultId": "mv_matrix"}, self._context(seen=seen))
        self.assertEqual(result["status"], "miss")
        self.assertEqual(seen, [])

    def test_a_missing_id_is_a_clean_miss(self):
        seen = []
        result = movievault_v2.movie_details({"title": "Example Film"}, self._context(seen=seen))
        self.assertEqual(result["status"], "miss")
        self.assertEqual(seen, [])

    def test_a_raising_catalog_degrades_to_a_miss_instead_of_failing_the_execution(self):
        result = movievault_v2.movie_details(
            {"releaseId": self.RELEASE_ID}, self._context(raises=True)
        )
        self.assertEqual(result["status"], "miss")


class MetadataPipelineIntegrationTests(unittest.TestCase):
    """Confirms the plugin's output actually reaches next_metadata's
    technical_updates/movie_updates buckets end to end - not just that the
    plugin emits the right dict shape in isolation."""

    def test_canonicalize_plugin_result_buckets_tracks_edition_and_release_title(self):
        item = movievault_v2._release(_synced_release())
        canonical = next_metadata.canonicalize_plugin_result("movievault_v2", "search_barcode", item)
        # next_metadata's normalize_value() recursively strips None-valued
        # keys from nested dicts (e.g. a track with no immersiveFormat), so
        # the second track loses that key on the way through - by design,
        # not something this feature changes.
        self.assertEqual(
            canonical["technicalUpdates"]["audio_tracks"],
            [
                _AUDIO_TRACKS[0],
                {"languageCode": "nl", "codec": "dolby_digital", "channels": "5.1"},
            ],
        )
        # Structured objects survive canonicalisation intact: normalize_list_field
        # passes non-strings straight through, and `subtitles` is already a
        # technical field so it is bucketed without an alias.
        self.assertEqual(canonical["technicalUpdates"]["subtitles"], _SUBTITLES)
        self.assertEqual(canonical["technicalUpdates"]["packaging"], _PACKAGING)
        self.assertEqual(canonical["movieUpdates"]["edition"], "Theatrical")
        self.assertEqual(canonical["movieUpdates"]["release_title"], "Example Film")


if __name__ == "__main__":
    unittest.main()


class ContractNegotiationTests(unittest.TestCase):
    """The shipped manifest is what decides which feed DiscVault actually asks for.

    `_negotiated_contract` returns SUPPORTED_CONTRACTS[index(maximum)] and that value
    is handed to `run_sync` and `bucket_lookup`. While the manifest was pinned at
    `distribution-3` the whole v4 code path - poster, audio tracks, subtitles,
    packaging, video - was unreachable in production no matter how complete it was.
    """

    def _manifest(self) -> dict:
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "next_plugins",
            "movievault_v2",
            "manifest.json",
        )
        with open(path, "rb") as handle:
            return json.load(handle)

    def test_the_shipped_manifest_negotiates_distribution_6(self):
        """The declared maximum is not a preference, it is the request.

        `_negotiated_contract` does not negotiate despite its name: it returns
        this plugin's own maximum and never asks the origin what it serves. So
        raising it makes the very next sync request /v6/index/manifest, and an
        origin without v6 activated answers 503 - which fails the whole
        synchronisation rather than one record. #562 reverted exactly this line
        for exactly that reason, when v5 support had landed but no origin served
        it yet.

        Both halves of that precondition are met again. MovieVault has served
        distribution-6 since its #221/#222 shipped the producer and its proxy
        layer, and the operator has confirmed it is activated on the instance
        this syncs against. The second half cannot be checked from here, which
        is why it is stated as an operator instruction rather than assumed.

        This assertion is what makes lowering or raising the pin a deliberate,
        visible act rather than a silent drift.
        """
        manifest = self._manifest()
        self.assertEqual(
            next_movievault_v2._negotiated_contract(
                {"distributionContractRange": manifest["distributionContractRange"]}
            ),
            next_movievault_v2.MOVIEVAULT_V6_CONTRACT,
        )

    def test_the_manifest_range_stays_within_what_the_code_supports(self):
        contract_range = self._manifest()["distributionContractRange"]
        self.assertIn(contract_range["minimum"], next_movievault_v2.SUPPORTED_CONTRACTS)
        self.assertIn(contract_range["maximum"], next_movievault_v2.SUPPORTED_CONTRACTS)

    def test_an_older_pin_still_negotiates_the_older_contract(self):
        self.assertEqual(
            next_movievault_v2._negotiated_contract(
                {
                    "distributionContractRange": {
                        "minimum": "distribution-2",
                        "maximum": "distribution-3",
                    }
                }
            ),
            next_movievault_v2.MOVIEVAULT_V3_CONTRACT,
        )
