"""The disc payload, and the four things it refuses.

A release grew a `discs` list. Everything here is about the boundary between
"the caller said nothing" and "the caller said none", because that distinction
is the whole reason an older client can keep saving a release without deleting
the discs somebody entered on it from another screen.

No database. The schema's own guarantees are in
``test_next_discs_postgres.py``; these are the ones a foreign key cannot make.
"""

import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_discs
from app.backend.next_common import NextApiError


class DiscVocabularyTests(unittest.TestCase):
    def test_the_two_axes_do_not_share_values(self):
        """`disc_type` is the medium and `disc_role` is what is on it. The moment
        one value appears in both, a row can answer the same question twice and
        the split has bought nothing -- which is exactly the state migration 067
        found `packaging` in."""
        self.assertEqual(
            set(next_discs.DISC_TYPES) & set(next_discs.DISC_ROLES), set()
        )

    def test_the_escape_hatch_is_in_the_vocabulary(self):
        """`other` is not a magic string the payload checks for -- it is a value
        a user can pick, and the schema's CHECK constraint names it too."""
        self.assertIn(next_discs.DISC_TYPE_OTHER, next_discs.DISC_TYPES)

    def test_the_disc_ceiling_matches_the_disc_count_column(self):
        """`movies.disc_count` is CHECKed to 1..999 (migration 070). A release
        that may hold more discs than its own count column can express is a
        contradiction waiting for someone to hit it."""
        self.assertEqual(next_discs.MAX_DISCS, 999)

    def test_an_unknown_medium_is_refused_rather_than_folded_into_other(self):
        """Coercing it would throw away the only copy of what the caller said
        while claiming a value they never picked."""
        with self.assertRaises(NextApiError) as caught:
            next_discs.normalize_disc_type("betamax")
        self.assertIn("betamax", str(caught.exception))
        self.assertEqual(caught.exception.status_code, 400)

    def test_case_and_padding_do_not_make_a_new_value(self):
        self.assertEqual(next_discs.normalize_disc_type("  UHD_Bluray "), "uhd_bluray")
        self.assertEqual(next_discs.normalize_disc_role("Feature"), "feature")

    def test_nothing_said_is_not_an_error(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                self.assertIsNone(next_discs.normalize_disc_type(value))
                self.assertIsNone(next_discs.normalize_disc_role(value))


class DiscsPayloadPresenceTests(unittest.TestCase):
    def test_an_absent_key_says_nothing_about_discs(self):
        """The rule the whole feature rests on. A client that predates discs --
        the iOS app, a script, an older PWA cached in a browser -- sends a movie
        edit body with no `discs` key, and must not thereby delete a disc list it
        does not know exists."""
        self.assertIsNone(next_discs.discs_payload({"title": "Alien"}))

    def test_an_explicit_empty_list_is_an_answer(self):
        self.assertEqual(next_discs.discs_payload({"discs": []}), [])

    def test_an_explicit_null_clears_the_list(self):
        self.assertEqual(next_discs.discs_payload({"discs": None}), [])

    def test_a_string_is_not_a_list(self):
        with self.assertRaises(NextApiError):
            next_discs.discs_payload({"discs": "bluray"})

    def test_more_discs_than_the_ceiling_are_refused(self):
        body = {"discs": [{"discType": "dvd"}] * (next_discs.MAX_DISCS + 1)}
        with self.assertRaises(NextApiError) as caught:
            next_discs.discs_payload(body)
        self.assertEqual(caught.exception.status_code, 400)


class DiscEntryTests(unittest.TestCase):
    def test_free_text_requires_the_other_type(self):
        """A row naming both a known medium and a free-text one is two answers to
        one question. The schema refuses it as a constraint violation; this turns
        that into a sentence saying what to do about it."""
        with self.assertRaises(NextApiError) as caught:
            next_discs.discs_payload(
                {"discs": [{"discType": "bluray", "discTypeOther": "MiniDisc"}]}
            )
        self.assertIn("other", str(caught.exception))

    def test_free_text_is_kept_alongside_the_other_type(self):
        discs = next_discs.discs_payload(
            {"discs": [{"discType": "other", "discTypeOther": "MiniDisc"}]}
        )
        self.assertEqual(discs[0]["disc_type"], "other")
        self.assertEqual(discs[0]["disc_type_other"], "MiniDisc")

    def test_the_same_disc_named_twice_is_refused_rather_than_deduplicated(self):
        """Two entries carrying one id are two descriptions of the same disc.
        Keeping whichever came last would discard an edit the user made, and say
        nothing about it."""
        disc_id = "11111111-1111-4111-8111-111111111111"
        with self.assertRaises(NextApiError) as caught:
            next_discs.discs_payload(
                {"discs": [{"id": disc_id, "discType": "dvd"}, {"id": disc_id}]}
            )
        self.assertIn(disc_id, str(caught.exception))

    def test_an_untouched_new_row_says_nothing(self):
        self.assertTrue(next_discs.disc_is_empty(next_discs.disc_payload({}, index=0)))

    def test_a_list_of_nothing_but_blanks_is_discarded(self):
        """Somebody opened the editor and left it alone. Storing that would flip
        a release from "nobody has broken this down" to "broken down into one
        unknown disc" — a different claim, made by accident."""
        blanks = [next_discs.disc_payload({}, index=i) for i in range(2)]
        self.assertEqual(next_discs.drop_blank_discs(blanks), [])

    def test_a_blank_disc_beside_a_described_one_is_kept(self):
        """The case the seeding editor produces on every non-combo format: Disc 1
        carries the release's details, Disc 2 is the one the user has just said
        exists and not yet described. Dropping it would delete that disc, and
        silently — a release that saves one disc back looks exactly like one that
        only ever had one."""
        discs = next_discs.discs_payload({"discs": [{"discType": "dvd"}, {}]})
        kept = next_discs.drop_blank_discs(discs)
        self.assertEqual(len(kept), 2)
        self.assertEqual([d["disc_type"] for d in kept], ["dvd", None])

    def test_an_existing_disc_emptied_on_purpose_is_not_dropped(self):
        """Blanking every field of a saved disc is an edit, not an abandonment --
        and the disc still has episodes hanging off it."""
        disc = next_discs.disc_payload(
            {"id": "22222222-2222-4222-8222-222222222222"}, index=0
        )
        self.assertFalse(next_discs.disc_is_empty(disc))

    def test_a_bad_id_is_named(self):
        with self.assertRaises(NextApiError) as caught:
            next_discs.discs_payload({"discs": [{"id": "not-a-uuid"}]})
        self.assertIn("not-a-uuid", str(caught.exception))


class DiscSpecReuseTests(unittest.TestCase):
    """The per-disc technical fields are the release-level ones, not lookalikes.

    Same column names, same normalizers, same value shapes -- so one renderer and
    one set of i18n labels serve both levels, and a track cannot mean one thing
    on a release and another on a disc inside it.
    """

    def test_audio_tracks_normalize_exactly_as_release_level_tracks_do(self):
        from app.backend.next_metadata import normalize_audio_tracks

        raw = [{"languageCode": "EN", "codec": "dolby_truehd", "channels": "7.1",
                "immersiveFormat": "dolby_atmos"}]
        disc = next_discs.discs_payload({"discs": [{"discType": "uhd_bluray",
                                                    "audioTracks": raw}]})[0]
        self.assertEqual(disc["audio_tracks"], normalize_audio_tracks(raw))

    def test_a_bare_language_becomes_a_full_subtitle_track(self):
        disc = next_discs.discs_payload(
            {"discs": [{"discType": "dvd", "subtitles": ["en", "nl"]}]}
        )[0]
        self.assertEqual(
            disc["subtitles"],
            [
                {"languageCode": "en", "subtitleType": "full"},
                {"languageCode": "nl", "subtitleType": "full"},
            ],
        )

    def test_comma_separated_text_splits_the_way_the_release_level_fields_do(self):
        disc = next_discs.discs_payload(
            {"discs": [{"discType": "uhd_bluray", "hdr": "HDR10, Dolby Vision"}]}
        )[0]
        self.assertEqual(disc["hdr"], ["HDR10", "Dolby Vision"])

    def test_the_camel_and_snake_spellings_of_a_field_agree(self):
        camel = next_discs.discs_payload(
            {"discs": [{"discType": "dvd", "videoResolution": "576p",
                        "aspectRatios": "1.85:1"}]}
        )[0]
        snake = next_discs.discs_payload(
            {"discs": [{"disc_type": "dvd", "video_resolution": "576p",
                        "screen_ratios": "1.85:1"}]}
        )[0]
        self.assertEqual(next_discs.disc_signature(camel), next_discs.disc_signature(snake))

    def test_every_writable_column_is_produced_by_the_payload(self):
        """DISC_COLUMNS drives the INSERT and the UPDATE in next_app. A column
        listed there that the payload never fills would write NULL over stored
        data on every save."""
        disc = next_discs.disc_payload({}, index=0)
        for column in next_discs.DISC_COLUMNS:
            self.assertIn(column, disc)


class DiscContentLinkTests(unittest.TestCase):
    def test_season_and_episode_ids_are_parsed_and_deduplicated_in_order(self):
        first = "33333333-3333-4333-8333-333333333333"
        second = "44444444-4444-4444-8444-444444444444"
        disc = next_discs.discs_payload(
            {"discs": [{"discType": "bluray", "episodeIds": [second, first, second]}]}
        )[0]
        self.assertEqual([str(value) for value in disc["episode_ids"]], [second, first])

    def test_no_seasons_named_is_an_answer_not_a_gap(self):
        disc = next_discs.discs_payload({"discs": [{"discType": "bluray"}]})[0]
        self.assertEqual(disc["season_ids"], [])
        self.assertEqual(disc["episode_ids"], [])

    def test_a_non_uuid_reference_is_kept_for_public_id_resolution(self):
        """The sync wire carries both spellings of a season or episode -- the
        uuid and the public_id -- and a client may hold either. A non-uuid
        reference is therefore not an error here: it is opaque text the write
        path resolves against public_id, where an unknown one is refused by
        name."""
        disc = next_discs.discs_payload(
            {"discs": [{"seasonIds": ["next-season-abc123def456"]}]}
        )[0]
        self.assertEqual(disc["season_ids"], ["next-season-abc123def456"])

    def test_an_absurdly_long_reference_is_still_refused(self):
        with self.assertRaises(NextApiError):
            next_discs.discs_payload({"discs": [{"seasonIds": ["x" * 200]}]})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
