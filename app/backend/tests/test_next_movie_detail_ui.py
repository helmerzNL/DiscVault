import os
import unittest


NEXT_VIEWS_UI_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "next_views_ui.py",
    )
)


class NextMovieDetailUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()

    def test_section_tabs_are_above_personal_lists(self):
        tabs_index = self.source.index(
            'class="detail-submenu movie-detail-section-tabs"'
        )
        release_panel_index = self.source.index('id="movieDetailReleasePanel"')
        personal_lists_index = self.source.index('id="movieListStateCard"')

        self.assertLess(tabs_index, release_panel_index)
        self.assertLess(release_panel_index, personal_lists_index)

    def test_section_tabs_use_localized_labels_and_separate_panels(self):
        expected_tabs = (
            (
                "movieDetail.release",
                "movieDetailReleasePanel",
                "movieDetailReleaseTab",
                "true",
            ),
            (
                "movieDetail.technical",
                "movieDetailTechnicalPanel",
                "movieDetailTechnicalTab",
                "false",
            ),
            (
                "movieDetail.collectors",
                "movieDetailCollectorsPanel",
                "movieDetailCollectorsTab",
                "false",
            ),
        )
        for key, panel_id, tab_id, selected_state in expected_tabs:
            self.assertIn(f'data-next-i18n="{key}"', self.source)
            self.assertIn(f'data-detail-panel="{panel_id}"', self.source)
            self.assertIn(
                f'id="{tab_id}" role="tab" aria-controls="{panel_id}" '
                f'aria-selected="{selected_state}"',
                self.source,
            )
            self.assertIn(
                f'id="{panel_id}" role="tabpanel" aria-labelledby="{tab_id}" '
                'data-detail-panel-group="movieSections"',
                self.source,
            )

        self.assertIn('id="movieDetailRelease"', self.source)
        self.assertIn('id="movieDetailTechnical"', self.source)
        self.assertIn('id="movieDetailCollectors"', self.source)

    def test_rendering_splits_technical_and_collectors_fields(self):
        self.assertIn(
            'document.getElementById("movieDetailTechnical").innerHTML = '
            "detailFieldRows(audioVideoFields);",
            self.source,
        )
        self.assertIn(
            'document.getElementById("movieDetailCollectors").innerHTML = '
            "detailFieldRows(collectorsFields);",
            self.source,
        )
        self.assertIn(
            'activateDetailTab("movieSections", "movieDetailReleasePanel");',
            self.source,
        )

    def test_cast_and_crew_block_precedes_media(self):
        self.assertIn('data-next-i18n="movieDetail.castCrew"', self.source)
        people_index = self.source.index('data-detail-tab="moviePeople"')
        media_index = self.source.index('data-detail-tab="movieMedia"')

        self.assertLess(people_index, media_index)

    def test_personal_lists_use_reference_style_primary_actions(self):
        actions_index = self.source.index('class="movie-list-primary-actions"')
        history_index = self.source.index('id="movieWatchHistoryPills"')

        self.assertLess(actions_index, history_index)
        self.assertIn(
            'class="movie-list-primary-action rewatch" '
            'id="movieLogRewatchButton" aria-haspopup="dialog"',
            self.source,
        )
        self.assertIn(
            'class="movie-list-primary-action watchlist" '
            'id="movieWatchlistToggleButton" aria-pressed="false"',
            self.source,
        )
        self.assertIn('data-next-i18n="lists.logRewatch"', self.source)
        self.assertIn('id="movieWatchlistToggleLabel"', self.source)

    def test_rewatch_action_sheet_has_localized_date_choices(self):
        self.assertIn("function openMovieRewatchDialog()", self.source)
        self.assertIn('tNext("lists.logRewatch", "Log rewatch")', self.source)
        self.assertIn('tNext("lists.watchedToday", "Watched today")', self.source)
        self.assertIn(
            'tNext("lists.watchedYesterday", "Watched yesterday")',
            self.source,
        )
        self.assertIn(
            'tNext("lists.chooseWatchedDate", "Choose a date")',
            self.source,
        )
        self.assertIn('data-rewatch-date="today"', self.source)
        self.assertIn('data-rewatch-date="yesterday"', self.source)
        self.assertIn('data-rewatch-date="choose"', self.source)

    def test_rewatch_custom_date_uses_native_date_picker(self):
        self.assertIn("function openMovieRewatchDatePicker(overlay, panel)", self.source)
        self.assertIn(
            '<input type="date" id="movieRewatchDateInput"',
            self.source,
        )
        self.assertIn('id="movieRewatchDateForm"', self.source)
        self.assertIn("markActiveMovieWatched(value);", self.source)
        self.assertNotIn('data-watch-date-choice="today"', self.source)

    def test_tags_use_plus_button_instead_of_inline_form(self):
        self.assertIn(
            'class="movie-tag-add-button" id="movieTagAddButton"',
            self.source,
        )
        self.assertIn('data-next-i18n-aria="lists.tagAdd"', self.source)
        self.assertNotIn('id="movieTagAddForm"', self.source)
        self.assertNotIn('id="movieTagAddInput"', self.source)

    def test_movie_tag_picker_reuses_existing_tag_api_and_colour_palette(self):
        self.assertIn("async function openMovieTagPicker()", self.source)
        self.assertIn('await authApiJson("/api/next/tags")', self.source)
        self.assertIn('data-tag-id="${escapeHtml(tag.id)}"', self.source)
        self.assertIn('class="movie-tag-color-options" role="radiogroup"', self.source)
        self.assertIn('data-create-tag', self.source)
        self.assertIn(
            "attachAndClose({name: query, color: selectedColor})",
            self.source,
        )
        self.assertIn(
            "document.getElementById(\"movieTagAddButton\")?.addEventListener",
            self.source,
        )

    def test_list_dialogs_handle_escape_and_restore_trigger_focus(self):
        self.assertIn(
            "overlay.returnFocusElement = returnFocus;",
            self.source,
        )
        self.assertIn('if (event.key !== "Escape") return;', self.source)
        self.assertIn("event.stopPropagation();", self.source)
        self.assertIn(
            'typeof returnFocus.focus === "function"',
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
