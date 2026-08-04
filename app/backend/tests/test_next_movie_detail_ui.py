import os
import unittest


NEXT_VIEWS_UI_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "next_views_ui.py",
    )
)
NEXT_APP_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "next_app.py",
    )
)
TMDB_PLUGIN_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "next_plugins",
        "tmdb",
        "plugin.py",
    )
)


class NextMovieDetailUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()
        with open(NEXT_APP_PATH, encoding="utf-8") as handle:
            cls.app_source = handle.read()
        with open(TMDB_PLUGIN_PATH, encoding="utf-8") as handle:
            cls.tmdb_plugin_source = handle.read()

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

    def test_unknown_content_rating_placeholder_is_not_written_to_edit_form(self):
        self.assertIn(
            'movieEditContentRating: contentRatingInfo.unknown ? "" : '
            '(contentRatingInfo.rating || ""),',
            self.source,
        )
        self.assertNotIn(
            'movieEditContentRating: contentRatingInfo.rating || "",',
            self.source,
        )
        self.assertIn(
            'return contentRatingInfo.unknown ? "" : valueText(contentRatingInfo.rating);',
            self.source,
        )

    def test_audio_and_subtitles_are_edited_as_per_track_rows(self):
        """Replaces the old free-text textareas. A track is a language plus a
        codec, channel layout and immersive format; a subtitle is a language plus
        a variant. Typing that as prose cannot express "English SDH"."""
        for container, rows, add in (
            ("movieEditAudioTracks", "movieEditAudioTrackRows", "movieEditAudioTrackAdd"),
            ("movieEditSubtitles", "movieEditSubtitleRows", "movieEditSubtitleAdd"),
        ):
            with self.subTest(container=container):
                self.assertIn(
                    f'<div id="{container}" class="movie-edit-track-editor wide" '
                    'data-lock-container="self">',
                    self.source,
                )
                self.assertIn(f'id="{rows}"', self.source)
                self.assertIn(f'id="{add}"', self.source)
        self.assertNotIn('<textarea id="movieEditAudioTracks"', self.source)
        self.assertNotIn('<textarea id="movieEditSubtitles"', self.source)

    def test_a_legacy_audio_string_is_re_emitted_verbatim_when_untouched(self):
        """The lossless promise: someone who never opens the editor keeps their
        hand-entered "English (DTS-HD MA 5.1)" exactly as it was."""
        self.assertIn(
            'if (legacy && audioTrackRowSignature(row) === (row.dataset.guess || "")) return legacy;',
            self.source,
        )

    def test_the_track_editors_are_covered_by_clear_detection(self):
        """A track editor is a <div>, so `input.value` is undefined. Without this
        branch every save reads as "user cleared the field" and silently fires a
        metadata refresh."""
        self.assertIn('input.dataset.lockContainer === "self"', self.source)
        self.assertIn(
            '? (input.querySelectorAll(".movie-edit-track-row").length ? "x" : "")',
            self.source,
        )

    def test_language_names_are_resolved_for_the_display_locale(self):
        """The code is what gets stored; the name is what the user reads. 8000
        languages across 29 locales is not a hand-translation task."""
        self.assertIn('new Intl.DisplayNames([localeState.locale], {type: "language"})', self.source)
        self.assertIn("function languageWithCode(code)", self.source)

    def test_structured_tracks_never_render_as_object_object(self):
        """`[].join()` calls String() per element, so an object rendered as
        "[object Object]" on the detail page, in the edit form and in the
        metadata comparison."""
        self.assertIn("function audioTracksText(value)", self.source)
        self.assertIn("function subtitlesText(value)", self.source)
        self.assertIn(
            '[tNext("movieDetail.audio", "Audio"), audioTracksText(specs.audio_tracks || metadata.audio_tracks)],',
            self.source,
        )
        self.assertIn(
            '[tNext("movieDetail.subtitles", "Subtitles"), subtitlesText(specs.subtitles || metadata.subtitles)]',
            self.source,
        )
        self.assertIn(
            '.map((item) => (typeof item === "object" ? JSON.stringify(item) : String(item)))',
            self.source,
        )

    def test_every_subtitle_variant_is_selectable(self):
        for variant in ("full", "sdh", "forced", "commentary", "closed_caption"):
            with self.subTest(variant=variant):
                self.assertIn(f'"{variant}"', self.source)
        self.assertIn(
            'const SUBTITLE_TYPE_VALUES = ["full", "sdh", "forced", "commentary", "closed_caption"];',
            self.source,
        )

    def test_header_content_rating_reuses_release_value_markup(self):
        render_start = self.source.index("function renderMovieDetail(detail)")
        render_end = self.source.index("\n    function ", render_start + 1)
        render_source = self.source[render_start:render_end]

        # The hero pill is omitted entirely while the rating is unknown, instead
        # of rendering the full "Unknown content rating" sentence in a compact
        # tag row (which used to make the badge look oversized).
        self.assertIn(
            'const heroContentRatingHtml = contentRatingInfo.unknown ? "" : contentRatingValueHtml(contentRatingInfo);',
            render_source,
        )
        self.assertIn(
            "heroContentRatingHtml ? {html: heroContentRatingHtml}",
            render_source,
        )
        self.assertNotIn(
            "heroContentRatingTag = contentRatingBadgeHtml(contentRatingInfo)",
            render_source,
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

    def test_metadata_compare_card_is_only_rendered_in_debug_mode(self):
        self.assertIn(
            'class="detail-card full debug-card hidden" '
            'id="movieMetadataCompareCard">\n'
            '            <div class="detail-card-head">\n'
            '              <h3 data-next-i18n="movieDetail.metadataCompare">',
            self.source,
        )
        self.assertIn(
            'card.classList.toggle("hidden", !appDebugMode);',
            self.source,
        )
        self.assertIn(
            'if (!appDebugMode) {\n'
            '        node.innerHTML = "";\n'
            "        return;\n"
            "      }",
            self.source,
        )

    def test_empty_movie_status_does_not_leave_space_below_hero(self):
        self.assertIn(".movie-detail-status:empty {\n      display: none;", self.source)
        self.assertIn(
            "#movieDetailPage {\n      gap: 8px;",
            self.source,
        )

    def test_cast_and_crew_use_responsive_portrait_cards_with_release_age(self):
        credits_start = self.app_source.index("def movie_credit_entities(")
        credits_end = self.app_source.index("\ndef ", credits_start + 1)
        self.assertIn("p.birth_date", self.app_source[credits_start:credits_end])
        self.assertIn("function personAgeAtMovieRelease(credit, movie)", self.source)
        self.assertIn(
            "const displayName = age == null ? name : `${name} (${age})`;",
            self.source,
        )
        self.assertIn('tNext("discover.castAs", "as")} ${role}', self.source)
        self.assertIn(
            '<div class="movie-people-grid" id="movieDetailCast">',
            self.source,
        )
        self.assertIn(
            '<div class="movie-people-grid" id="movieDetailCrew">',
            self.source,
        )
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", self.source)
        self.assertIn(
            'configureResponsiveGridLimit("movieDetailCast", "movieDetailCastMore", '
            "{mobileRows: 4, desktopRows: 4});",
            self.source,
        )
        self.assertIn(
            'configureResponsiveGridLimit("movieDetailCrew", "movieDetailCrewMore", '
            "{mobileRows: 4, desktopRows: 4});",
            self.source,
        )

    def test_movie_credit_entities_row_limit_fits_full_cast_and_crew(self):
        # TMDb's plugin caps cast at 20 and crew at 75 (next_plugins/tmdb/plugin.py's
        # CREW_LIMIT), so a movie can legitimately have up to 95 movie_credits rows.
        # movie_credit_entities()'s own LIMIT must stay above that, or its
        # `ORDER BY sort_order, name` silently truncates crew before the frontend
        # ever sees them -- exactly the "not all crew show up" bug this guards.
        credits_start = self.app_source.index("def movie_credit_entities(")
        credits_end = self.app_source.index("\ndef ", credits_start + 1)
        function_source = self.app_source[credits_start:credits_end]
        self.assertIn("limit: int = 100", function_source)

        self.assertIn('credits.get("cast") or [])[:20]', self.tmdb_plugin_source)
        self.assertIn("CREW_LIMIT = 75", self.tmdb_plugin_source)

    def test_media_grids_are_responsive_and_row_limited(self):
        self.assertIn(
            'grid-template-columns: repeat(4, minmax(0, 1fr));',
            self.source,
        )
        self.assertIn(
            'grid-template-columns: repeat(2, minmax(0, 1fr));',
            self.source,
        )
        self.assertIn(
            "grid-template-columns: repeat(auto-fit, minmax(min(200px, 100%), 300px));",
            self.source,
        )
        for grid_id, button_id in (
            ("movieDetailPosterArtwork", "movieDetailPosterMore"),
            ("movieDetailBackdropArtwork", "movieDetailBackdropMore"),
            ("movieDetailVideos", "movieDetailVideoMore"),
        ):
            self.assertIn(
                f'configureResponsiveGridLimit("{grid_id}", "{button_id}", '
                "{mobileRows: 4, desktopRows: 2});",
                self.source,
            )
        self.assertIn(
            'document.getElementById(panelId)?.querySelectorAll("[data-more-button]")'
            ".forEach(updateResponsiveGridLimit);",
            self.source,
        )

    def test_artwork_manager_heading_is_debug_only(self):
        self.assertIn(
            'class="artwork-manager-status hidden" id="movieArtworkManagerStatus">',
            self.source,
        )
        self.assertIn(
            'node.classList.toggle("hidden", !appDebugMode);',
            self.source,
        )

    def test_movie_artwork_action_sheet_supports_hide_unhide_share_and_delete(self):
        self.assertIn("function bindMovieArtworkLongPressMenus()", self.source)
        self.assertIn('element.addEventListener("contextmenu"', self.source)
        self.assertIn('if (!["Enter", " "].includes(event.key)) return;', self.source)
        self.assertIn("await navigator.share({title: movieTitle", self.source)
        self.assertIn("await copyArtworkUrl(url);", self.source)
        hide_start = self.source.index("async function hideMovieArtwork")
        hide_end = self.source.index("async function unhideMovieArtwork", hide_start)
        hide_source = self.source[hide_start:hide_end]
        self.assertIn("/hide`", hide_source)
        self.assertIn('method: "POST"', hide_source)
        self.assertNotIn('method: "DELETE"', hide_source)
        delete_start = self.source.index("async function deleteDetailArtwork")
        delete_end = self.source.index("\n    function ", delete_start)
        self.assertIn('method: "DELETE"', self.source[delete_start:delete_end])
        self.assertIn('tNext("movieDetail.setPrimary", "Set primary")', self.source)
        self.assertIn('tNext("common.share", "Share")', self.source)
        self.assertIn('tNext("common.hide", "Hide")', self.source)
        self.assertIn('tNext("common.unhide", "Unhide")', self.source)
        self.assertIn('tNext("movieDetail.deleteArtwork", "Delete")', self.source)
        self.assertIn('tNext("movieDetail.artworkHidden", "Artwork hidden.")', self.source)
        self.assertIn('tNext("movieDetail.artworkUnhidden", "Artwork unhidden.")', self.source)

    def test_hidden_artwork_is_filtered_by_default_and_revealed_dimmed(self):
        self.assertIn("const movieArtworkHiddenKinds = new Set();", self.source)
        self.assertIn("const visibleAssets = kindAssets.filter((asset) => !asset.hidden);", self.source)
        self.assertIn("const hiddenAssets = kindAssets.filter((asset) => asset.hidden);", self.source)
        self.assertIn('tNext("movieDetail.showHidden", "Show hidden ({count})")', self.source)
        self.assertIn('tNext("movieDetail.hideAgain", "Hide again")', self.source)
        self.assertIn('data-artwork-hidden="${asset.hidden ? "true" : "false"}"', self.source)
        self.assertIn(".movie-art-option.is-hidden .art-option-preview", self.source)

    def test_packaging_edit_uses_a_checkbox_group_of_all_nine_enum_values(self):
        self.assertIn('<fieldset id="movieEditPackaging"', self.source)
        # The old single free-text input must be gone, not just superseded.
        self.assertNotIn(
            '<input id="movieEditPackaging" name="packaging" maxlength="160" autocomplete="off">',
            self.source,
        )
        for value in (
            "keep_case",
            "amaray",
            "steelbook",
            "slipcover",
            "slipcase",
            "digibook",
            "mediabook",
            "digipak",
            "box",
        ):
            with self.subTest(value=value):
                self.assertIn(f'<input type="checkbox" value="{value}">', self.source)

    def test_packaging_checkbox_fill_and_submit_read_checked_values(self):
        self.assertIn(
            'fillMovieEditCheckboxGroup("movieEditPackaging", specList("packaging"));',
            self.source,
        )
        self.assertIn(
            'packaging: collectMovieEditCheckboxGroup("movieEditPackaging"),',
            self.source,
        )
        self.assertIn(
            'document.querySelectorAll(`#${containerId} input[type=checkbox]:checked`)',
            self.source,
        )

    def test_movie_edit_locks_handle_all_three_container_shapes(self):
        """A <label>-wrapped input, a <fieldset> of checkboxes, and a track editor
        that is neither. The editor opts in by data attribute rather than being
        matched by tag name, so a fourth shape needs no change here."""
        self.assertIn("function movieEditLockContainer(input)", self.source)
        self.assertIn('input.dataset && input.dataset.lockContainer === "self"', self.source)
        self.assertIn('input.tagName === "FIELDSET" ? input : input.closest("label")', self.source)
        self.assertIn("function movieEditLockAnchor(container)", self.source)
        self.assertIn('container.querySelector("[data-lock-anchor]")', self.source)

    def test_the_new_technical_fields_are_lockable(self):
        for element_id, field in (
            ("movieEditRegions", "regions"),
            ("movieEditVideoResolution", "video_resolution"),
            ("movieEditVideoCodecs", "video_codecs"),
        ):
            with self.subTest(field=field):
                self.assertIn(f'{element_id}: "{field}"', self.source)

    def test_hdr_and_video_codecs_are_checkbox_groups_of_all_their_enum_values(self):
        for value in ("hdr", "hdr10", "hdr10_plus", "hlg", "dolby_vision"):
            with self.subTest(value=value):
                self.assertIn(f'<label><input type="checkbox" value="{value}">', self.source)
        for value in ("mpeg2", "vc1", "h264", "hevc", "av1"):
            with self.subTest(value=value):
                self.assertIn(f'<label><input type="checkbox" value="{value}">', self.source)

    def test_hdr_is_no_longer_a_hardcoded_english_literal(self):
        """It shipped as a bare "HDR" span in the form, a bare label on the detail
        page, and an empty i18n key in the comparison table."""
        self.assertIn('data-next-i18n="movieDetail.hdr"', self.source)
        self.assertIn('"technical:hdr": ["movieDetail.hdr", "HDR"]', self.source)
        self.assertNotIn("<span>HDR</span>", self.source)

    def test_the_estimated_value_is_editable_at_all(self):
        """It shipped as an API/sync-only field: the server accepted it, both apps
        edited it, and the PWA had no input for it anywhere."""
        self.assertIn('<input id="movieEditEstimatedValue" name="estimated_value"', self.source)
        self.assertIn('estimatedValue: formTextValue("movieEditEstimatedValue"),', self.source)

    def test_the_currency_sits_next_to_the_value_and_is_submitted(self):
        self.assertIn('<select id="movieEditEstimatedValueCurrency"', self.source)
        self.assertIn(
            'estimatedValueCurrency: formTextValue("movieEditEstimatedValueCurrency"),', self.source
        )
        self.assertIn("function fillMovieEditEstimatedValueCurrency(stored)", self.source)

    def test_the_currency_picker_offers_what_the_converter_can_actually_convert(self):
        """A currency outside the price-display set could never be converted to
        the preferred one, which is the reason for recording it."""
        self.assertIn("priceDisplay?.supportedCurrencies", self.source)
        self.assertIn("DEFAULT_PRICE_DISPLAY_CURRENCIES", self.source)

    def test_a_stored_currency_is_never_dropped_from_the_picker(self):
        self.assertIn("[...available, ...DEFAULT_PRICE_DISPLAY_CURRENCIES, chosen]", self.source)

    def test_a_value_without_a_currency_renders_as_a_bare_number(self):
        """Stating a unit nobody entered is worse than stating none."""
        self.assertIn("function formatEstimatedValue(value, currency)", self.source)
        self.assertIn(
            "return Number.isFinite(numeric) ? numeric.toLocaleString(undefined, {maximumFractionDigits: 2}) : \"\";",
            self.source,
        )

    def test_a_value_with_a_currency_reuses_the_existing_conversion_helper(self):
        """formatWishlistPrice already renders "original (converted to preferred)";
        a second implementation would drift from it."""
        self.assertIn("return formatWishlistPrice(value, code);", self.source)

    def test_the_estimated_value_is_shown_on_the_detail_page(self):
        self.assertIn(
            'formatEstimatedValue(movie.estimated_value, movie.estimated_value_currency)', self.source
        )

    def test_the_checkbox_group_has_a_style_rule(self):
        """The packaging fieldset shipped without one and rendered with the
        browser default fieldset border."""
        self.assertIn(".movie-edit-checkbox-group {", self.source)
        self.assertIn(".movie-edit-track-row {", self.source)

    def test_movie_format_is_never_displayed_raw(self):
        """movies.format is an unconstrained free-text column -- providers and
        sync clients can (and do, per sync/fixtures/identity-ladder.json) write
        raw codes like "4K_UHD" or "BLURAY" into it. Every place a movie's
        format is shown to a user must go through physicalFormatLabel() (or
        another normalizer, e.g. physicalFormatBadgeHtml/
        renderMovieEditFormatOptions), which already turns those into "4K UHD"
        and "Blu-ray" -- not read movie.format directly."""
        self.assertIn(
            "document.getElementById(\"movieDetailTags\").innerHTML = detailTagHtml([\n"
            "        movie.year,\n"
            "        physicalFormatLabel(movie.format),",
            self.source,
        )
        self.assertIn(
            '[tNext("movieDetail.format", "Format"), physicalFormatLabel(movie.format)],',
            self.source,
        )
        self.assertIn(
            '[tNext("movieDetail.format", "Format"), '
            "physicalFormatLabel(movie.format || specs.format || metadata.format)],",
            self.source,
        )
        self.assertIn(
            "const subtitle = [movie.year, physicalFormatLabel(movie.format), movie.edition]"
            ".filter(Boolean).join(\" / \");",
            self.source,
        )
        self.assertIn(
            "return [movie.year, physicalFormatLabel(movie.format), movie.barcode].filter(Boolean);",
            self.source,
        )
        self.assertIn(
            "const metaParts = [movie.year, physicalFormatLabel(movie.format)]"
            '.map((part) => String(part || "").trim()).filter(Boolean);',
            self.source,
        )
        self.assertIn(
            'const label = [movie.title || tNext("common.untitled", "Untitled"), movie.year, '
            "physicalFormatLabel(movie.format), movie.barcode].filter(Boolean).join(\" / \");",
            self.source,
        )
        self.assertIn(
            "const meta = [movie.year, physicalFormatLabel(movie.format), movie.barcode, "
            "actionLabel || movie.action].filter(Boolean).join(\" / \");",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
