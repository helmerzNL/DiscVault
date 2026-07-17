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

    def test_movie_admin_actions_overlay_the_backdrop_with_mdi_icons(self):
        hero_index = self.source.index('id="movieDetailHero"')
        actions_index = self.source.index('class="movie-detail-hero-actions"')
        summary_index = self.source.index('class="movie-detail-summary"', hero_index)

        self.assertLess(hero_index, actions_index)
        self.assertLess(actions_index, summary_index)
        for button_id, key in (
            ("movieEditToggleButton", "common.edit"),
            ("movieMetadataApplyButton", "movieDetail.applyMetadata"),
            ("movieDeleteButton", "movieDetail.deleteMovie"),
        ):
            button_start = self.source.index(f'id="{button_id}"')
            button_end = self.source.index("</button>", button_start)
            button_source = self.source[button_start:button_end]
            self.assertIn("<svg", button_source)
            self.assertIn(f'data-next-i18n-aria="{key}"', button_source)
            self.assertIn(f'data-next-i18n-title="{key}"', button_source)

    def test_removed_refresh_controls_are_not_rendered_or_bound(self):
        for control_id in (
            "movieMetadataPeopleOption",
            "movieMetadataPeopleToggle",
            "movieMetadataJobsButton",
            "movieCrewRefreshButton",
        ):
            self.assertNotIn(control_id, self.source)
        self.assertIn(
            'body: JSON.stringify({dryRun, refreshPeople: false, '
            'personRefreshScope: "all"})',
            self.source,
        )

    def test_mobile_movie_hero_matches_ios_composition(self):
        self.assertIn("#movieDetailPage .movie-detail-hero {", self.source)
        self.assertIn("width: calc(100% + 24px);", self.source)
        self.assertIn("grid-template-columns: 104px minmax(0, 1fr);", self.source)
        self.assertIn("#movieDetailPage .movie-detail-poster {", self.source)
        self.assertIn("#movieDetailPage .movie-detail-summary .eyebrow {", self.source)
        self.assertIn("#movieDetailPage .movie-detail-back .button-label", self.source)
        self.assertIn(
            'data-next-i18n-aria="movieDetail.backToLibrary"',
            self.source,
        )

    def test_mobile_header_is_hidden_and_personal_action_pills_are_compact(self):
        mobile_start = self.source.index("@media (max-width: 760px)")
        mobile_end = self.source.index("@media (max-width: 560px)", mobile_start)
        mobile_css = self.source[mobile_start:mobile_end]
        logo_start = mobile_css.index(".mobile-shell-logo {")
        logo_end = mobile_css.index("}", logo_start)
        logo_css = mobile_css[logo_start:logo_end]

        self.assertIn("display: none;", logo_css)
        self.assertNotIn("display: inline-flex;", logo_css)
        self.assertIn("min-height: 38px;", mobile_css)
        self.assertIn("font-size: .85rem;", mobile_css)

    def test_edit_action_preserves_icon_when_label_changes_to_save(self):
        self.assertIn('id="movieEditToggleLabel"', self.source)
        self.assertIn('id="movieEditToggleIcon"', self.source)
        self.assertIn("editLabel.textContent = label;", self.source)
        self.assertIn(
            'editIcon.setAttribute("d", show ? '
            "editIcon.dataset.savePath : editIcon.dataset.editPath);",
            self.source,
        )
        self.assertIn(
            "node.className = `detail-message movie-detail-status "
            '${tone || ""}`.trim();',
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
