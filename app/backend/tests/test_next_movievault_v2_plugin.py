import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_plugins.movievault_v2 import plugin as movievault_v2
from app.backend import next_metadata


_AUDIO_TRACKS = [
    {"languageCode": "en", "codec": "dolby_truehd", "channels": "7.1", "immersiveFormat": "dolby_atmos"},
    {"languageCode": "nl", "codec": "dolby_digital", "channels": "5.1", "immersiveFormat": None},
]
_SUBTITLE_LANGUAGES = ["en", "nl"]
_PACKAGING = ["steelbook", "slipcover"]


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
        "subtitleLanguages": _SUBTITLE_LANGUAGES,
        "packaging": _PACKAGING,
    }
    record.update(overrides)
    return record


class ReleaseMappingTests(unittest.TestCase):
    def test_release_carries_audio_tracks_and_subtitle_languages_into_the_movie_dict(self):
        item = movievault_v2._release(_synced_release())
        self.assertEqual(item["movie"]["audioTracks"], _AUDIO_TRACKS)
        self.assertEqual(item["movie"]["subtitleLanguages"], _SUBTITLE_LANGUAGES)
        self.assertEqual(item["movie"]["packaging"], _PACKAGING)
        # Already-wired fields stay wired.
        self.assertEqual(item["movie"]["edition"], "Theatrical")
        self.assertEqual(item["movie"]["releaseTitle"], "Example Film")

    def test_release_omits_empty_audio_and_subtitle_arrays(self):
        item = movievault_v2._release(_synced_release(audioTracks=[], subtitleLanguages=[]))
        self.assertNotIn("audioTracks", item["movie"])
        self.assertNotIn("subtitleLanguages", item["movie"])

    def test_release_omits_empty_packaging_array(self):
        item = movievault_v2._release(_synced_release(packaging=[]))
        self.assertNotIn("packaging", item["movie"])


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
            lookup_results=[_synced_release(audioTracks=[], subtitleLanguages=[], packaging=[])],
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
        self.assertEqual(result["movie"]["subtitleLanguages"], _SUBTITLE_LANGUAGES)
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
        self.assertEqual(result["movie"]["subtitleLanguages"], _SUBTITLE_LANGUAGES)
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
        self.assertEqual(canonical["technicalUpdates"]["subtitles"], _SUBTITLE_LANGUAGES)
        self.assertEqual(canonical["technicalUpdates"]["packaging"], _PACKAGING)
        self.assertEqual(canonical["movieUpdates"]["edition"], "Theatrical")
        self.assertEqual(canonical["movieUpdates"]["release_title"], "Example Film")


if __name__ == "__main__":
    unittest.main()
