"""Structured subtitles on the v2 release-details route.

MovieVault publishes the same physical release on two anonymous routes and they
did not agree: `distribution-4` carries subtitles as `{languageCode,
subtitleType}` while `release-technical-1` carried a bare language list, with the
resolver's own query collapsing the variant away because the contract allowed
nothing else.

The half that matters here is the *order*. `_release_details_object` validates
with a closed key set, so an unknown key does not get ignored - it fails the
whole response. MovieVault emitting `subtitles` before this reader accepts it
would return `release_details_response_invalid` for every barcode that falls
through to the resolver. These tests exist so that acceptance is provably in
place before the producer changes.

App-Guidance `docs/apps/discvault/movievault-route-parity.md`.
"""

import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_movievault_v2 as mv2


def _release(**overrides):
    # `barcodes` and `title` are the only required keys, and `releaseId` is
    # deliberately absent - it is not in the allowed set, and including it here
    # would trip the very strictness these tests are about.
    item = {
        "title": "Dune: Part Two (4K Ultra HD + Blu-ray)",
        "barcodes": [{"value": "5051888273456", "type": "ean13", "scope": "package"}],
    }
    item.update(overrides)
    return item


class ReleaseDetailsSubtitleTests(unittest.TestCase):

    def test_the_flat_language_list_still_works(self):
        # Every deployed MovieVault sends this and only this. It must keep
        # working for as long as one of them does.
        result = mv2._release_details_release(_release(subtitleLanguages=["en", "nl"]))
        self.assertEqual(["en", "nl"], result["subtitleLanguages"])
        self.assertNotIn("subtitles", result)

    def test_structured_subtitles_are_accepted(self):
        result = mv2._release_details_release(
            _release(subtitles=[
                {"languageCode": "en", "subtitleType": "full"},
                {"languageCode": "en", "subtitleType": "sdh"},
            ])
        )
        self.assertEqual(
            [
                {"languageCode": "en", "subtitleType": "full"},
                {"languageCode": "en", "subtitleType": "sdh"},
            ],
            result["subtitles"],
        )

    def test_the_same_language_twice_is_the_whole_point(self):
        # A disc routinely carries both a full and an SDH track in one language.
        # The flat list could not express that - `subtitle_languages` rejects
        # duplicates - which is why the resolver collapsed them.
        result = mv2._release_details_release(
            _release(subtitles=[
                {"languageCode": "en", "subtitleType": "full"},
                {"languageCode": "en", "subtitleType": "sdh"},
            ])
        )
        self.assertEqual(2, len(result["subtitles"]))

    def test_structured_subtitles_replace_the_flat_list(self):
        # The two describe the same tracks. Keeping both would hand the merge two
        # representations of one fact and a question about which wins.
        result = mv2._release_details_release(
            _release(
                subtitleLanguages=["en"],
                subtitles=[{"languageCode": "en", "subtitleType": "sdh"}],
            )
        )
        self.assertNotIn("subtitleLanguages", result)
        self.assertEqual("sdh", result["subtitles"][0]["subtitleType"])

    def test_an_unknown_variant_is_carried_rather_than_refused(self):
        # Open enum, as on the distribution-4 side: MovieVault may add a variant
        # before this allow-list catches up, and losing a track over a value we
        # have not heard of is worse than carrying it.
        result = mv2._release_details_release(
            _release(subtitles=[{"languageCode": "en", "subtitleType": "karaoke"}])
        )
        self.assertEqual("karaoke", result["subtitles"][0]["subtitleType"])

    def test_an_unreadable_shape_is_still_refused(self):
        for bad in (
            [{"languageCode": "en"}],
            [{"languageCode": "en", "subtitleType": ""}],
            [{"languageCode": "EN", "subtitleType": "full"}],
            [{"languageCode": "en", "subtitleType": "full", "extra": 1}],
            "not-a-list",
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(mv2.MovieVaultV2Error):
                    mv2._release_details_release(_release(subtitles=bad))

    def test_an_unknown_key_still_fails_the_whole_response(self):
        # The behaviour that forces consumers-first. Pinned so nobody "fixes" it
        # into leniency without reading why it is strict.
        with self.assertRaises(mv2.MovieVaultV2Error):
            mv2._release_details_release(_release(somethingNew=[]))


if __name__ == "__main__":
    unittest.main()
