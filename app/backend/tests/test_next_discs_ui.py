"""The disc editor as it reaches the browser.

Source-text assertions, in the idiom the other UI tests here use: the frontend
is a string built by `next_views_ui.py`, so there is no module to import and no
DOM to drive.

What is worth pinning is not that the markup exists but that the disc editor is
a *narrowing* of the release-level one rather than a second implementation of
it -- same vocabularies, same track rows, same labels -- and that the two places
which have to stay scoped stay scoped.
"""

import json
import os
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEXT_VIEWS_UI_PATH = os.path.join(BACKEND_DIR, "next_views_ui.py")
NEXT_APP_PATH = os.path.join(BACKEND_DIR, "next_app.py")
I18N_DIR = os.path.abspath(os.path.join(BACKEND_DIR, "..", "frontend", "i18n", "next"))


class DiscEditorSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()
        with open(NEXT_APP_PATH, encoding="utf-8") as handle:
            cls.app_source = handle.read()

    def test_the_edit_form_has_a_disc_section(self):
        self.assertIn('id="movieEditDiscsSection"', self.source)
        self.assertIn('id="movieEditDiscRows"', self.source)
        self.assertIn('id="movieEditDiscAdd"', self.source)

    def test_the_detail_view_renders_discs_below_the_release_level_specs(self):
        """Below and not instead: a disc narrows what the release says, so both
        have to be readable at once."""
        technical = self.source.index('id="movieDetailTechnical"')
        discs = self.source.index('id="movieDetailDiscsBlock"')
        self.assertLess(technical, discs)
        self.assertIn("renderMovieDetailDiscs(detail.discs)", self.source)

    def test_the_save_body_always_carries_the_full_disc_list(self):
        self.assertIn("discs: collectMovieEditDiscs()", self.source)

    def test_the_track_collectors_are_scoped_to_a_container(self):
        """A disc has its own audio and subtitle rows. A collector reaching for
        `#movieEditAudioTrackRows` would read the release's rows for every disc
        on the page, so the scoped form is the one that must exist."""
        self.assertIn("function collectAudioTrackRows(root)", self.source)
        self.assertIn("function collectSubtitleRows(root)", self.source)
        self.assertIn(
            'collectAudioTrackRows(row.querySelector(\'[data-disc-tracks="audioTracks"]\'))',
            self.source,
        )

    def test_a_disc_reuses_the_release_level_track_rows_rather_than_copying_them(self):
        """`audioTrackRowHtml` and `subtitleRowHtml` are the release-level row
        builders. A disc renders the same markup so a track means the same thing
        at both levels -- legacy free text included."""
        row = self.source[
            self.source.index("function discRowHtml") : self.source.index(
                "function renderMovieEditDiscs"
            )
        ]
        self.assertIn("audioTrackRowHtml", row)
        self.assertIn("subtitleRowHtml", row)

    def test_a_disc_reuses_the_release_level_vocabularies(self):
        row = self.source[
            self.source.index("function discRowHtml") : self.source.index(
                "function renderMovieEditDiscs"
            )
        ]
        for name in (
            "HDR_FORMAT_VALUES",
            "VIDEO_CODEC_VALUES",
            "DISC_REGION_VALUES",
            "VIDEO_RESOLUTION_VALUES",
        ):
            with self.subTest(name=name):
                self.assertIn(name, row)

    def test_the_free_text_field_is_only_offered_under_the_other_type(self):
        """The schema refuses the pair, so the form must not invite it."""
        self.assertIn('select[data-disc-field="discType"]', self.source)
        self.assertIn('other.hidden = select.value !== "other"', self.source)
        self.assertIn('if (disc.discType !== "other") disc.discTypeOther = "";', self.source)

    def test_episodes_are_only_sent_under_a_ticked_season(self):
        """An episode left checked below a season the user has since unticked is
        not a statement they are still making -- and sending it would tick the
        season straight back on, because the server treats an episode as implying
        its season."""
        self.assertIn("tickedSeasons.has(", self.source)

    def test_the_season_picker_offers_only_the_seasons_the_release_covers(self):
        """The schema makes a disc's seasons a subset of the release's. Offering
        more would offer a save the database is going to widen behind the user."""
        picker = self.source[
            self.source.index("function discSeasonPickerHtml") : self.source.index(
                "function discRowHtml"
            )
        ]
        self.assertIn("collectMovieEditSeasonIds()", picker)

    def test_the_disc_count_disagreement_is_shown_rather_than_corrected(self):
        self.assertIn('id="movieEditDiscCountWarning"', self.source)
        self.assertIn("function syncMovieEditDiscCountWarning", self.source)
        self.assertIn("movieDetail.discCountMismatch", self.source)
        # And the server only fills the column when nobody has answered it.
        self.assertIn(
            "UPDATE movies SET disc_count = %s WHERE id = %s AND disc_count IS NULL",
            self.app_source,
        )


class DiscTranslationTests(unittest.TestCase):
    """Every string the disc editor shows has a key, in every locale.

    `test_next_i18n_completeness` already guards locale-versus-locale drift. What
    it cannot see is a key the UI asks for that no locale has, which falls back
    to the English default silently.
    """

    KEYS = (
        "movieDetail.discs",
        "movieDetail.discsHint",
        "movieDetail.discAdd",
        "movieDetail.discRemove",
        "movieDetail.discNone",
        "movieDetail.discNumber",
        "movieDetail.discType",
        "movieDetail.discTypeOther",
        "movieDetail.discRole",
        "movieDetail.discLabel",
        "movieDetail.discContent",
        "movieDetail.discContentHint",
        "movieDetail.discEpisodes",
        "movieDetail.discEpisodesLoad",
        "movieDetail.discEpisodesNone",
        "movieDetail.discCountMismatch",
        "movieDiscType.unset",
        "movieDiscRole.unset",
    )

    def test_every_disc_key_is_translated_in_every_locale(self):
        for name in sorted(os.listdir(I18N_DIR)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(I18N_DIR, name), encoding="utf-8") as handle:
                messages = json.load(handle)
            for key in self.KEYS:
                with self.subTest(locale=name, key=key):
                    self.assertIn(key, messages)
                    self.assertTrue(messages[key].strip())

    def test_the_placeholders_survive_translation(self):
        """`{number}`, `{described}` and `{stated}` are substituted by string
        replacement. A locale that drops or renames one renders the placeholder
        to the user, or silently loses the number."""
        placeholders = {
            "movieDetail.discNumber": ["{number}"],
            "movieDetail.discCountMismatch": ["{described}", "{stated}"],
        }
        for name in sorted(os.listdir(I18N_DIR)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(I18N_DIR, name), encoding="utf-8") as handle:
                messages = json.load(handle)
            for key, tokens in placeholders.items():
                for token in tokens:
                    with self.subTest(locale=name, key=key, token=token):
                        self.assertIn(token, messages[key])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
