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
from pathlib import Path
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
    """Fixtures are in the **wire** shape on purpose.

    `_movie_discs` is fed by `_local_discs`, which renders rows through
    `MOVIE_DISC_WIRE_KEYS` before they arrive. These tests used to pass column
    names, which agreed with the converter's old reads and with nothing that
    ever calls it -- so both sides were wrong together and the suite was green
    while a disc's audio tracks reached no correction at all. See
    `WireShapeAgreementTests` below, which pins the two shapes to each other.
    """

    def test_a_disc_travels_without_its_position_or_its_local_links(self):
        """Position is restated by list order and refused if it disagrees, so
        stating it twice is one chance to disagree with itself. Season and
        episode ids are local identity — MovieVault holds its own season
        structure and a DiscVault uuid means nothing there."""
        discs = corrections._movie_discs(
            [
                {
                    "discType": "uhd_bluray",
                    "discRole": "feature",
                    "videoResolution": "2160p",
                    "hdr": ["dolby_vision", "hdr10"],
                    "screenRatios": ["2.39:1"],
                    "seasonIds": ["6f1c0f3e-0000-4000-8000-000000000000"],
                    "audioTracks": [{"languageCode": "en", "codec": "dolby_truehd"}],
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
                    {"discType": "uhd_bluray"},
                    {"discType": "bluray", "audioTracks": ["Commentary track"]},
                ]
            )
        )

    def test_no_discs_recorded_says_nothing(self):
        self.assertIsNone(corrections._movie_discs([]))


class RefusingOutLoudTests(unittest.TestCase):
    """A field withheld for a reason must say the reason.

    The all-or-nothing rule above is right -- these are replacement lists, so a
    partial one deletes what it could not express. What was wrong was that the
    refusal was indistinguishable from agreement: `build_changes` skips a `None`
    proposal, so the field left the sheet with nothing said. A user who edits a
    disc's audio track and sees no change offered concludes the edit did not
    register.
    """

    def test_a_free_text_release_track_is_a_refusal_not_a_silence(self):
        reasons, details = corrections._untravellable_reasons(
            {"audio_tracks": ["English (DTS-HD MA 5.1)"]}, []
        )
        self.assertEqual(reasons["audioTracks"], "local_tracks_are_free_text")
        # Quoted back, because the next action is to go and fix that one row.
        self.assertEqual(details["audioTracks"], "English (DTS-HD MA 5.1)")

    def test_a_free_text_disc_track_names_the_disc_and_the_track(self):
        reasons, details = corrections._untravellable_reasons(
            {},
            [
                {"discType": "uhd_bluray", "label": "Feature"},
                {
                    "discType": "bluray",
                    "label": "Bonus",
                    "audioTracks": ["Commentary with the director"],
                },
            ],
        )
        self.assertEqual(reasons["discs"], "disc_tracks_are_free_text")
        self.assertEqual(details["discs"], "Bonus - Commentary with the director")

    def test_an_unlabelled_disc_is_named_by_its_position(self):
        """Position rather than nothing: a box set of six identical-looking
        discs is a search if the answer only says "one disc"."""
        _, details = corrections._untravellable_reasons(
            {}, [{"discType": "bluray"}, {"discType": "bluray", "audioTracks": ["prose"]}]
        )
        self.assertEqual(details["discs"], "Disc 2 - prose")

    def test_a_track_missing_only_its_codec_is_described_by_what_it_has(self):
        _, details = corrections._untravellable_reasons(
            {"audio_tracks": [{"languageCode": "en"}]}, []
        )
        self.assertEqual(details["audioTracks"], "en / ?")

    def test_tracks_that_all_convert_are_not_a_refusal(self):
        reasons, details = corrections._untravellable_reasons(
            {"audio_tracks": [{"languageCode": "en", "codec": "dts_hd_ma"}]},
            [{"discType": "bluray", "audioTracks": [{"languageCode": "nl", "codec": "dts_hd_ma"}]}],
        )
        self.assertEqual(reasons, {})
        self.assertEqual(details, {})

    def test_nothing_recorded_is_not_a_refusal_either(self):
        """The distinction the converters could not express on their own:
        `None` meant both "nobody said" and "said, but unsendable", and only the
        second is a withholding."""
        reasons, _ = corrections._untravellable_reasons({"audio_tracks": []}, [])
        self.assertEqual(reasons, {})

    def test_every_reason_it_produces_is_one_the_sheet_can_render(self):
        """The codes are a shared vocabulary with `CONTRIBUTE_WITHHELD_REASONS`
        in the UI, and an unknown code reaches the screen raw -- the renderer
        falls back to printing it. Checked against the UI source rather than
        against a copy of the list, because a copy drifts silently."""
        produced: set[str] = set()
        for technical, discs in (
            ({"audio_tracks": ["prose"]}, []),
            ({"subtitles": [{"languageCode": "en", "subtitleType": "nonsense"}]}, []),
            ({}, [{"discType": "bluray", "audioTracks": ["prose"]}]),
        ):
            reasons, _ = corrections._untravellable_reasons(technical, discs)
            produced.update(reasons.values())
        self.assertEqual(produced, {"local_tracks_are_free_text", "disc_tracks_are_free_text"})

        ui_source = (
            Path(__file__).resolve().parents[1] / "next_views_ui.py"
        ).read_text(encoding="utf-8")
        for code in produced:
            self.assertIn(f"{code}: [", ui_source, f"{code} is not in CONTRIBUTE_WITHHELD_REASONS")


class WireShapeAgreementTests(unittest.TestCase):
    """The readers and the converters must be asking for the same keys.

    Three defects of one shape reached production together, and none of them
    failed anything: a reader answered in a vocabulary the converter did not
    read, so the converter answered "nobody said" about data the database was
    holding. `_movie_discs` read column names while `_local_discs` returns wire
    names; `movie_technical_specs` did not select the two columns that
    `RELEASE_FIELD_SOURCES` names for `videoResolution` and `videoCodecs`.

    A unit test with a hand-written fixture cannot catch this -- it agrees with
    whichever side wrote it. These two compare the sides to each other.
    """

    def test_the_disc_converter_reads_what_the_disc_reader_writes(self):
        from app.backend import next_app

        # A disc populated entirely through the reader's own vocabulary. If the
        # converter reads anything else, the value silently fails to travel.
        row = {
            "discType": "uhd_bluray",
            "discRole": "feature",
            "discTypeOther": None,
            "label": "Feature",
            "videoResolution": "2160p",
            "videoCodecs": ["hevc"],
            "hdr": ["hdr10"],
            "screenRatios": ["2.39:1"],
            "audioTracks": [{"languageCode": "en", "codec": "dolby_truehd"}],
            "subtitles": [{"languageCode": "nl", "subtitleType": "full"}],
            # Upper case, which is what the editor stores and what
            # `DISC_REGIONS` accepts -- the per-disc path does not case-fold the
            # way `_mirror_disc_regions` does at release level.
            "regions": ["FREE"],
            "notes": "ignored upstream",
        }
        self.assertEqual(set(row) - {"notes"}, set(next_app.MOVIE_DISC_WIRE_KEYS.values()) - {"notes"})

        entry = corrections._movie_discs([row])[0]
        for key in (
            "discType",
            "discRole",
            "label",
            "videoResolution",
            "videoCodecs",
            "hdrFormats",
            "aspectRatios",
            "audioTracks",
            "subtitles",
            "regions",
        ):
            self.assertIn(key, entry, f"{key} was dropped between the reader and the wire")

    def test_the_technical_reader_selects_every_column_a_correction_needs(self):
        """`RELEASE_FIELD_SOURCES` declares which column each technical field
        comes from. A column the reader does not select reads as absent, and
        the field is quietly never proposed."""
        source = (
            Path(__file__).resolve().parents[2] / "backend" / "next_metadata.py"
        ).read_text(encoding="utf-8")
        select = source.split("FROM movie_technical_specs")[0].rsplit("SELECT", 1)[1]
        selected = {part.strip() for line in select.splitlines() for part in line.split(",")}
        for field, (kind, column) in corrections.RELEASE_FIELD_SOURCES.items():
            if kind != "technical" or column == "packaging":
                continue
            self.assertIn(column, selected, f"{field} reads {column}, which is not selected")


class FormatSpellingTests(unittest.TestCase):
    """`movies.format` is free text, and a contribution is a display of it.

    The column is unconstrained on purpose -- providers and sync clients write
    raw codes into it, which is why every screen in the PWA routes a format
    through `physicalFormatLabel` before showing it. The correction sheet was
    the one surface that read the column raw and sent it onward, so a shelf
    holding `4K_UHD` proposed replacing the catalogue's `4K UHD` with an
    underscore: a change that is not a change, offered on release after
    release.
    """

    def test_a_raw_code_is_spelled_the_way_the_catalogue_spells_it(self):
        self.assertEqual(corrections._format_display("4K_UHD"), "4K UHD")
        self.assertEqual(corrections._format_display("BLURAY"), "Blu-ray")
        self.assertEqual(corrections._format_display("dvd"), "DVD")

    def test_a_combo_keeps_both_halves(self):
        for raw in ("4K UHD + Blu-Ray", "4K UHD / Blu-ray", "UHD & BD"):
            with self.subTest(raw=raw):
                self.assertEqual(corrections._format_display(raw), "4K UHD + Blu-ray")

    def test_a_spelling_this_does_not_know_is_left_alone(self):
        """Free text on purpose. A person may have typed something this does
        not recognise, and inventing a spelling for it would propose a
        correction nobody can defend."""
        for raw in ("HD DVD", "LaserDisc", "VHS", "Betamax"):
            with self.subTest(raw=raw):
                self.assertEqual(corrections._format_display(raw), raw)

    def test_nothing_recorded_stays_nothing(self):
        self.assertIsNone(corrections._format_display(None))
        self.assertIsNone(corrections._format_display("   "))

    def test_the_normalisation_runs_before_the_diff_not_after(self):
        """The reported symptom, stated as the invariant behind it: the local
        value and the catalogue value have to be compared *after* spelling is
        settled, or two spellings of one answer read as a disagreement.

        Only the local side is normalised. `expected` is the catalogue's
        literal value and is what upstream checks the correction against, so
        rewriting it here would submit an `expected` the catalogue does not
        hold and have the contribution refused for it.
        """
        local = {"format": corrections._format_display("4K_UHD")}
        mirror = {"format": "4K UHD"}
        self.assertEqual(
            corrections.build_changes(allowed=["format"], local=local, mirror=mirror),
            [],
        )

    def test_a_real_difference_still_travels(self):
        local = {"format": corrections._format_display("BLURAY")}
        mirror = {"format": "4K UHD"}
        self.assertEqual(
            corrections.build_changes(allowed=["format"], local=local, mirror=mirror),
            [{"field": "format", "expected": "4K UHD", "proposed": "Blu-ray"}],
        )


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
