"""Converting DiscVault's technical description into what MovieVault accepts.

MovieVault-v2#227 made the whole technical description correctable, so these
fields can finally travel. Most of them are a straight copy — both sides took
the vocabulary from `release-technical-1` — and the tests worth having are
about the four places where the two systems do *not* line up:

* packaging is two axes here and one flat list there;
* screen ratios are free text here and a bounded form there;
* audio tracks and subtitles are a union of structured entries and legacy
  prose here, and strictly structured there;
* a set written as a list has no order, but is compared as if it did.

Each of those is somewhere a plausible-looking conversion silently loses or
invents a fact, which is why they are pinned rather than left to the wire.
"""

import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_movievault_v2_field_corrections as corrections


class VocabularyListTests(unittest.TestCase):
    def test_a_set_is_sorted_so_two_orderings_are_one_answer(self):
        """Upstream sorts these too. If either side did not, a whole-list
        comparison would call two spellings of one answer a conflict, and
        refuse a correction nobody disputed."""
        first = corrections._vocabulary_list(["hdr10", "dolby_vision"], corrections._MV_HDR_FORMATS)
        second = corrections._vocabulary_list(["dolby_vision", "hdr10"], corrections._MV_HDR_FORMATS)
        self.assertEqual(first, second)
        self.assertEqual(first, ["dolby_vision", "hdr10"])

    def test_a_value_upstream_does_not_know_is_dropped_not_sent(self):
        """Withheld rather than refused. A vocabulary that gains a value on one
        side first must not cost the record the values both sides do share."""
        self.assertEqual(
            corrections._vocabulary_list(["hevc", "prores"], corrections._MV_VIDEO_CODECS),
            ["hevc"],
        )

    def test_nothing_recorded_is_none_rather_than_an_empty_claim(self):
        """`[]` upstream means "there are none". A movie nobody has described
        is saying "nobody said", and the two must not collapse — an empty
        replacement list would delete what the catalogue holds."""
        self.assertIsNone(corrections._vocabulary_list([], corrections._MV_FINISHES))
        self.assertIsNone(corrections._vocabulary_list(None, corrections._MV_FINISHES))


class AspectRatioTests(unittest.TestCase):
    def test_only_the_bounded_form_travels_and_order_is_kept(self):
        """Upstream refuses anything else, so `16:9` cannot be sent. Not sorted,
        unlike the sets: the primary ratio comes first and that is information."""
        self.assertEqual(
            corrections._aspect_ratios(["2.39:1", "16:9", "1.85:1", "2.39:1"]),
            ["2.39:1", "1.85:1"],
        )

    def test_a_boutique_ratio_is_not_special_cased_away(self):
        self.assertEqual(corrections._aspect_ratios(["1.66:1"]), ["1.66:1"])


class AudioTrackTests(unittest.TestCase):
    def test_a_structured_track_travels_with_its_optional_parts(self):
        self.assertEqual(
            corrections._audio_tracks(
                [
                    {
                        "languageCode": "en",
                        "codec": "dolby_truehd",
                        "channels": "7.1",
                        "immersiveFormat": "dolby_atmos",
                    }
                ]
            ),
            [
                {
                    "languageCode": "en",
                    "codec": "dolby_truehd",
                    "channels": "7.1",
                    "immersiveFormat": "dolby_atmos",
                }
            ],
        )

    def test_legacy_prose_withholds_the_whole_list(self):
        """The column has held strings like this since before MovieVault
        published structured tracks. Parsing a codec back out of prose misfires
        on "Commentary with the director", and this is a *replacement* list —
        so sending the tracks that did convert would delete the one that did
        not. All or nothing is the only answer that cannot lose data."""
        self.assertIsNone(
            corrections._audio_tracks(
                [
                    {"languageCode": "en", "codec": "dts_hd_ma"},
                    "French (Dolby Digital 5.1)",
                ]
            )
        )

    def test_a_codec_outside_the_shared_set_withholds_too(self):
        self.assertIsNone(
            corrections._audio_tracks([{"languageCode": "en", "codec": "flac"}])
        )

    def test_an_unusable_channel_layout_is_dropped_without_losing_the_track(self):
        """Unlike the codec, channels are optional upstream — so an odd value is
        a detail to omit rather than a reason to withhold the track."""
        track = corrections._audio_tracks(
            [{"languageCode": "en", "codec": "dts", "channels": "9.1"}]
        )
        self.assertEqual(track[0]["channels"], None)
        self.assertEqual(track[0]["codec"], "dts")


class SubtitleTests(unittest.TestCase):
    def test_a_bare_language_code_is_the_full_variant(self):
        """Not a guess: an unqualified subtitle listing on a disc means the full
        track, and it is the only thing the pre-variant shape could express."""
        self.assertEqual(
            corrections._subtitles(["nl"]),
            [{"languageCode": "nl", "subtitleType": "full"}],
        )

    def test_prose_still_withholds_the_list(self):
        self.assertIsNone(corrections._subtitles(["English SDH for the hard of hearing"]))


class PackagingTests(unittest.TestCase):
    def test_the_two_axes_are_flattened_back_to_one_list(self):
        """067 split what MovieVault keeps flat. The round trip closes through
        the mapping that already existed for the other direction, rather than a
        second approximation of it."""
        self.assertEqual(
            corrections._packaging(
                {"carrier_type": "steelbook", "outer_packaging": ["slipcover"]}
            ),
            ["slipcover", "steelbook"],
        )

    def test_a_release_with_no_packaging_recorded_says_nothing(self):
        self.assertIsNone(corrections._packaging({}))


class DiscTests(unittest.TestCase):
    def test_a_disc_travels_without_its_position_or_its_local_links(self):
        """Position is restated by list order and refused if it disagrees, so
        stating it twice is one chance to disagree with itself. Season and
        episode ids are local identity — MovieVault holds its own season
        structure and a DiscVault uuid means nothing there."""
        discs = corrections._movie_discs(
            [
                {
                    "disc_type": "uhd_bluray",
                    "disc_role": "feature",
                    "video_resolution": "2160p",
                    "hdr": ["dolby_vision", "hdr10"],
                    "screen_ratios": ["2.39:1"],
                    "season_ids": ["6f1c0f3e-0000-4000-8000-000000000000"],
                    "audio_tracks": [{"languageCode": "en", "codec": "dolby_truehd"}],
                }
            ]
        )
        self.assertEqual(
            discs,
            [
                {
                    "discType": "uhd_bluray",
                    "discRole": "feature",
                    "videoResolution": "2160p",
                    "hdrFormats": ["dolby_vision", "hdr10"],
                    "aspectRatios": ["2.39:1"],
                    "audioTracks": [
                        {
                            "languageCode": "en",
                            "codec": "dolby_truehd",
                            "channels": None,
                            "immersiveFormat": None,
                        }
                    ],
                }
            ],
        )

    def test_one_disc_with_unconvertible_tracks_withholds_every_disc(self):
        """Same argument as the track list itself, one level up: this replaces
        the whole disc list, so a disc offered without the tracks it actually
        has would delete them upstream."""
        self.assertIsNone(
            corrections._movie_discs(
                [
                    {"disc_type": "uhd_bluray"},
                    {"disc_type": "bluray", "audio_tracks": ["Commentary track"]},
                ]
            )
        )

    def test_no_discs_recorded_says_nothing(self):
        self.assertIsNone(corrections._movie_discs([]))


class FieldTableTests(unittest.TestCase):
    def test_every_correctable_field_is_sourced_or_withheld_with_a_reason(self):
        """The table's own invariant, restated because the cost of breaking it
        is silence: a field in neither map is simply never offered, and nothing
        says why."""
        covered = set(corrections.RELEASE_FIELD_SOURCES) | set(
            corrections.RELEASE_FIELDS_WITHHELD
        )
        self.assertEqual(
            covered & {"alternateTitles", "discs", "packaging", "audioTracks"},
            {"alternateTitles", "discs", "packaging", "audioTracks"},
        )
        self.assertEqual(
            corrections.RELEASE_FIELDS_WITHHELD["alternateTitles"],
            "not_stored_by_discvault",
        )

    def test_each_technical_field_has_a_lock_the_user_can_pin_it_with(self):
        """A pinned value must not be published upstream. `regions` needed this
        row and so does every field beside it — without one, a lock the user set
        against metadata refresh silently stops covering the field."""
        for field in (
            "packaging",
            "finishes",
            "videoResolution",
            "videoCodecs",
            "hdrFormats",
            "aspectRatios",
            "audioTracks",
            "subtitles",
        ):
            with self.subTest(field=field):
                self.assertIn(field, corrections._FIELD_LOCK_NAMES)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class ExpectedSpellingTests(unittest.TestCase):
    """`expected` has to be spelled the way the catalogue spells it.

    This is the bug the first real disc contribution hit. MovieVault says "this
    release has no discs" as `[]`; DiscVault says it as `None`, because on the
    *proposing* side that distinction is load-bearing — `[]` is a replacement
    list that deletes, and a record with nothing recorded must never send one.

    Both spellings are right for their own side. They are only wrong against
    each other, and `expected` is the one place the two meet: MovieVault
    compares it against its canonical value as a whole and refuses an approved
    change when they differ. `None` against `[]` reads as "Moved Since" — the
    moderator is told the catalogue shifted under a field nobody touched, and
    Approve is disabled with Reject the only button left.
    """

    def test_an_empty_catalogue_list_is_spelled_the_catalogue_way(self):
        coerced = corrections._as_expected(
            {"discs": None, "packaging": None, "audioTracks": None, "edition": None}
        )
        self.assertEqual(coerced["discs"], [])
        self.assertEqual(coerced["packaging"], [])
        self.assertEqual(coerced["audioTracks"], [])
        # Scalars keep their null: MovieVault's canonical `edition` for a
        # release with none really is null, not "".
        self.assertIsNone(coerced["edition"])

    def test_a_value_the_catalogue_does_hold_is_left_alone(self):
        coerced = corrections._as_expected({"packaging": ["steelbook"], "discs": [{"discType": "dvd"}]})
        self.assertEqual(coerced["packaging"], ["steelbook"])
        self.assertEqual(coerced["discs"], [{"discType": "dvd"}])

    def test_every_list_shaped_correctable_field_is_covered(self):
        """The list is the whole point, so it must not drift from the source
        table. A field that grows a list shape later and is missed here comes
        back as an unapprovable contribution, not as a test failure."""
        listish = {
            field
            for field in corrections.RELEASE_FIELD_SOURCES
            if field in {
                "discRegions", "packaging", "finishes", "videoCodecs", "hdrFormats",
                "aspectRatios", "audioTracks", "subtitles", "discs",
            }
        }
        self.assertEqual(listish, set(corrections._EXPECTED_LIST_FIELDS))
