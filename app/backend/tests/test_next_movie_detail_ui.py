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
                # Attribute-wise rather than as one literal string: the
                # wrapper also carries `data-derived-from-discs`, and a test
                # that pins the whole opening tag breaks every time an
                # orthogonal attribute is added -- which says nothing about
                # whether the track editor still works.
                self.assertIn(f'<div id="{container}" class="movie-edit-track-editor wide"', self.source)
                self.assertIn('data-lock-container="self"', self.source)
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
        # The row literals carry a leading field-name tag since the derived
        # rows became droppable, so match the rendering call rather than the
        # tuple shape -- the tuple shape is not what these tests are about.
        self.assertIn(
            'tNext("movieDetail.audio", "Audio"), audioTracksText(specs.audio_tracks || metadata.audio_tracks)',
            self.source,
        )
        self.assertIn(
            'tNext("movieDetail.subtitles", "Subtitles"), subtitlesText(specs.subtitles || metadata.subtitles)',
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
            "const heroContentRatingHtml = heroContentRatingPillHtml(contentRatingInfo);",
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

    def test_hero_content_rating_pill_pairs_flag_with_the_rating(self):
        """A country flag never appears in the hero without the rating beside it.

        The flag alone says nothing about the film, so the pill is built only
        when a rating is actually known — and when it is, the rating text is
        always rendered next to the flag rather than left to a tooltip.
        """
        start = self.source.index("function heroContentRatingPillHtml(info)")
        end = self.source.index("\n    function ", start + 1)
        pill_source = self.source[start:end]

        self.assertIn('if (!rating || info?.unknown) return "";', pill_source)
        self.assertIn("flagIconHtml(info.country", pill_source)
        self.assertIn(
            '<span class="content-rating-hero-value">${escapeHtml(rating)}</span>',
            pill_source,
        )

    def test_personal_lists_card_opens_collapsed(self):
        """Personal lists reopens collapsed on every film.

        The card carries tags, loans and the whole watch history; leaving it
        expanded pushes the cast below the fold for the majority of openings
        that never touch it.
        """
        self.assertIn('id="movieListStateDetails"', self.source)
        self.assertIn(
            '<details class="collapse-card-details" id="movieListStateDetails">',
            self.source,
        )
        # No `open` attribute in the markup, and the reset path strips one that a
        # previous film left behind.
        self.assertNotIn(
            '<details class="collapse-card-details" id="movieListStateDetails" open',
            self.source,
        )
        self.assertIn(
            'document.getElementById("movieListStateDetails")?.removeAttribute("open");',
            self.source,
        )

    def test_tags_and_loan_share_a_two_column_row_on_large_screens(self):
        self.assertIn('<div class="movie-list-subsections">', self.source)
        self.assertIn(
            "      .movie-list-subsections {\n"
            "        grid-template-columns: repeat(2, minmax(0, 1fr));",
            self.source,
        )

    def test_imdb_and_tmdb_render_as_hero_chips_only_when_known(self):
        """IMDb/TMDb only render when the identifier is actually known.

        A chip that cannot open anything reads as "we have this id" when we do
        not, so a service without a usable identifier is dropped entirely.
        """
        start = self.source.index("function movieExternalLinkChipsHtml(identifiers)")
        end = self.source.index("\n    // Relationship entries", start + 1)
        chips_source = self.source[start:end]

        self.assertIn('if (provider !== "imdb" && provider !== "tmdb") return;', chips_source)
        self.assertIn('if (!String(item.identifier || "").trim()) return;', chips_source)
        self.assertIn('if (!href) return "";', chips_source)
        self.assertIn('/api/next/assets/tmdb-logo.svg', chips_source)
        self.assertIn('id="movieDetailExternalLinks"', self.source)

    def test_external_links_close_out_the_release_panel(self):
        """IMDb/TMDb belong with the release's identifying facts, not the hero.

        The Release panel already carries barcode, format, release date and
        country — which is what an external-database link is. In the hero the
        strip read as chrome bolted onto the artwork.
        """
        release_fields_index = self.source.index('id="movieDetailRelease"')
        links_index = self.source.index('id="movieDetailExternalLinks"')
        technical_panel_index = self.source.index('id="movieDetailTechnicalPanel"')

        self.assertLess(release_fields_index, links_index)
        self.assertLess(links_index, technical_panel_index)
        self.assertIn(
            '<div class="detail-panel-links hidden" id="movieDetailExternalLinks"></div>',
            self.source,
        )

        # Gone from the hero copy block entirely.
        hero_start = self.source.index('<div class="movie-detail-copy">')
        hero_end = self.source.index('id="movieDetailOverview"', hero_start)
        self.assertNotIn("movieDetailExternalLinks", self.source[hero_start:hero_end])
        self.assertNotIn("hero-external-links", self.source)

    def test_digital_playback_links_live_in_the_collectors_panel(self):
        panel_index = self.source.index('id="movieDetailCollectorsPanel"')
        links_index = self.source.index('id="movieDetailCollectorsLinks"')
        release_panel_index = self.source.index('id="movieDetailReleasePanel"')

        self.assertLess(panel_index, links_index)
        self.assertLess(release_panel_index, panel_index)
        self.assertIn(
            'collectorsLinksNode.innerHTML = digital.join("");',
            self.source,
        )

    def test_digital_playback_renders_as_a_compact_chip(self):
        """Plex/Jellyfin are single-line pills, not cards.

        The old `.detail-service-card` repeated the film's own title and year
        back on the film's own page and wrapped "Play with Plex" around a name
        that says it already. The phrase survives as the accessible name.
        """
        start = self.source.index("function digitalPlaybackLinkCard(item, serviceItemCount = 1)")
        end = self.source.index("\n    function ", start + 1)
        chip_source = self.source[start:end]

        self.assertIn('class="detail-service-chip"', chip_source)
        self.assertIn('class="detail-service-chip-logo ${logoClass}"', chip_source)
        self.assertIn("digitalSourceLogoHtml(service)", chip_source)
        self.assertIn('aria-label="${escapeHtml(label)}"', chip_source)
        self.assertNotIn("detail-service-card", chip_source)
        self.assertNotIn("item.title", chip_source)

        # The container is a flex row, not a 168px `.detail-grid` track — that
        # minimum was what stretched two services into card-sized blocks.
        self.assertIn(
            '<div class="movie-collectors-links hidden" id="movieDetailCollectorsLinks"></div>',
            self.source,
        )
        self.assertIn(
            "    .movie-collectors-links {\n"
            "      display: flex;\n"
            "      flex-wrap: wrap;\n",
            self.source,
        )

    def test_digital_items_carry_a_browser_openable_web_url(self):
        """Plex stores an app deep link, which is dead without the app installed.

        `plex://server/...` looks live and does nothing on a desktop that has no
        Plex app. The web form is derived rather than replacing what is stored,
        so native clients keep opening the app directly.
        """
        self.assertIn("def digital_item_web_url(", self.app_source)
        self.assertIn('row["web_url"] = digital_item_web_url(row)', self.app_source)
        # `machine_id` is the one piece the query did not already select.
        self.assertIn("dms.machine_id,", self.app_source)
        self.assertIn(
            'return f"https://app.plex.tv/desktop/#!/server/{machine_id}/details?key={key}"',
            self.app_source,
        )
        # The Plex `key` is a path and must be percent-encoded, or the fragment
        # breaks at the first slash.
        self.assertIn('key = quote(f"/library/metadata/{external_id}", safe="")', self.app_source)

    def test_missing_plex_machine_id_yields_no_link_at_all(self):
        """An empty machine id means Plex's /identity call failed at sync time.

        There is no honest web URL to build from that, and a malformed one is
        worse than none.
        """
        start = self.app_source.index("def digital_item_web_url(")
        end = self.app_source.index("\ndef movie_digital_item_entities(", start)
        builder = self.app_source[start:end]

        self.assertIn("if machine_id and external_id:", builder)
        self.assertIn("if base_url and external_id:", builder)
        self.assertEqual(builder.count("return None"), 3)

    def test_chip_prefers_the_web_url_over_the_stored_playback_url(self):
        self.assertIn(
            "const href = item.web_url || item.webUrl "
            "|| item.playback_url || item.playbackUrl || \"\";",
            self.source,
        )

    def test_native_clients_still_receive_the_raw_playback_url(self):
        """Native apps *want* the deep link — it opens Plex directly there.

        The web URL is derived alongside the stored value, never in place of it,
        so this path is deliberately untouched.
        """
        people_path = os.path.join(os.path.dirname(NEXT_APP_PATH), "next_people.py")
        with open(people_path, encoding="utf-8") as handle:
            people_source = handle.read()

        self.assertIn("playbackUrl", people_source)
        self.assertNotIn("digital_item_web_url", people_source)

    def test_debug_panel_separates_selected_from_written(self):
        """`accepted` only means the merge chose the candidate, not that it landed.

        Reporting acceptance alone made a field that was selected and then never
        written look identical to one that saved — which is how an int-into-a-
        text-column failure sat in plain sight reading "used".
        """
        self.assertIn('written = decision.get("written")', self.app_source)
        self.assertIn(
            '"written": None if written is None else (accepted and bool(written)),',
            self.app_source,
        )
        self.assertIn('"writeState": decision.get("writeState"),', self.app_source)

        # Three states in the UI, and a pre-write-tracking event (written null)
        # must keep reading "used" rather than claiming a failure it cannot see.
        self.assertIn('movieDetail.debugSourcesNotWritten', self.source)
        self.assertIn("const fieldLanded = (field) => field.accepted && field.written !== false;", self.source)
        self.assertIn('if (field.accepted && field.written === false) {', self.source)
        self.assertIn(".debug-source-marker.not-written {", self.source)

    def test_oversized_service_card_treatment_is_gone(self):
        """Nothing renders `.detail-service-card` any more, so it is removed.

        `externalServiceCard` / `externalServiceLogoHtml` existed only to build
        it, and `digitalPlaybackLinkCard` was their only caller.
        """
        for dead in (
            "externalServiceCard",
            "externalServiceLogoHtml",
            "detail-service-card",
            "detail-service-logo",
            "detail-service-copy",
            "detail-service-meta",
        ):
            self.assertNotIn(dead, self.source)

    def test_links_card_hides_when_no_other_identifier_remains(self):
        self.assertIn('<div class="detail-card hidden" id="movieDetailLinksCard">', self.source)
        self.assertIn(
            'if (linksCard) linksCard.classList.toggle("hidden", !otherIdentifiers.length);',
            self.source,
        )

    def test_relationships_card_is_full_width_with_poster_and_single_line_title(self):
        self.assertIn(
            '<div class="detail-card full" id="movieDetailRelationshipsCard">',
            self.source,
        )
        self.assertIn(
            '<div class="detail-relations-grid" id="movieDetailRelationships">',
            self.source,
        )
        self.assertIn("function relationCardHtml(", self.source)
        self.assertIn('<span class="detail-relation-art">', self.source)
        # One line, ellipsised — a long release name must not push the cards out
        # of alignment.
        self.assertIn(
            "    .detail-relation-copy strong {\n"
            "      display: block;\n"
            "      min-width: 0;\n"
            "      overflow: hidden;\n"
            "      white-space: nowrap;\n"
            "      text-overflow: ellipsis;\n"
            "    }",
            self.source,
        )

    def test_identity_debug_card_follows_the_metadata_debug_card(self):
        metadata_index = self.source.index('id="movieDetailDebugMetadataCard"')
        identity_index = self.source.index('id="movieDetailDebugIdentityCard"')

        self.assertLess(metadata_index, identity_index)
        self.assertIn(
            'class="detail-card full debug-card hidden" '
            'id="movieDetailDebugIdentityCard">',
            self.source,
        )
        self.assertIn('data-next-i18n="movieDetail.debugIdentityTitle"', self.source)
        self.assertIn(
            'if (debugIdentityCard) debugIdentityCard.classList.toggle("hidden", !appDebugMode);',
            self.source,
        )
        self.assertIn(
            "movieIdentityDebugState = appDebugMode ? movieIdentityDebugRows(detail) : [];",
            self.source,
        )

    def test_identity_debug_labels_stay_in_english(self):
        """The row labels name columns in the shared sync contract.

        A translated field name breaks the link to the spec section that
        governs it, and this output gets pasted into bug reports read against
        that spec — so the labels are literals, never `tNext` lookups.
        """
        start = self.source.index("function movieIdentityDebugRows(detail)")
        end = self.source.index("\n    function movieIdentityDebugHtml", start + 1)
        rows_source = self.source[start:end]

        self.assertIn('movieIdentityDebugRow("client_id (record token, tier 1)"', rows_source)
        self.assertIn('movieIdentityDebugRow("barcode (normalized, tier 2)"', rows_source)
        self.assertIn('movieIdentityDebugRow("title (normalized, tier 4)"', rows_source)
        self.assertIn('client mappings (iOS/Android)', rows_source)
        self.assertNotIn("tNext(", rows_source)

    def test_identity_debug_ladder_flags_a_match_against_another_record(self):
        start = self.source.index("function movieIdentityLadderRows(ladder)")
        end = self.source.index("\n    function movieIdentityDebugRows", start + 1)
        ladder_source = self.source[start:end]

        self.assertIn('if (entry.isSelf) return movieIdentityDebugRow(label, `self-match', ladder_source)
        self.assertIn("MATCHES OTHER RECORD", ladder_source)
        self.assertIn('"alert"', ladder_source)

    def test_identity_debug_payload_reuses_the_real_ladder(self):
        """Rule one of the iOS panel: never re-normalize.

        The values and verdicts are produced by the same `normalize_*` /
        `find_movie_by_*` functions the sync merge path calls, so the panel
        cannot show something subtly different from what actually matched.
        """
        self.assertIn("def movie_identity_debug_entity(", self.app_source)
        for call in (
            "normalize_barcode(barcode)",
            "normalize_title(title)",
            "find_movie_by_client_id(",
            "find_movie_by_barcode_match(",
            "find_movie_by_tmdb_edition(",
            "find_movie_by_title_year(",
        ):
            self.assertIn(call, self.app_source)
        self.assertIn('"identityDebug": identity_debug,', self.app_source)

    def test_client_id_mapping_splits_device_and_record_token(self):
        """`client_id_mappings.client_id` is a composite the sync API writes.

        It stores `"<device client id>:<record token>"`; the two halves are the
        two different things the sync contract calls `client_id`, so they are
        split apart rather than shown as one opaque string.
        """
        self.assertIn("def movie_client_id_mappings(", self.app_source)
        self.assertIn('device, _, record_token = composite.partition(":")', self.app_source)

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

    def test_packaging_is_edited_as_axes_rather_than_one_flat_list(self):
        """Migrations 067/071 replaced the flat nine-value group with the axes.

        This test and the one below asserted the old group long after it was
        gone, and stayed red without anyone noticing because neither this module
        nor the flat list's other guards are named in the smoke workflow.
        """
        # A scalar carrier and a scalar generation; two checkbox groups.
        self.assertIn('<select id="movieEditCarrierType" name="carrierType">', self.source)
        self.assertIn(
            '<select id="movieEditSteelbookFormat" name="steelbookFormat">', self.source
        )
        self.assertIn('<fieldset id="movieEditOuterPackaging"', self.source)
        self.assertIn('<fieldset id="movieEditFinishes"', self.source)
        # The flat list is derived and read-only for clients, so it must not be
        # editable at all - neither as the old free-text input nor as a group.
        self.assertNotIn('<fieldset id="movieEditPackaging"', self.source)
        self.assertNotIn(
            '<input id="movieEditPackaging" name="packaging" maxlength="160" autocomplete="off">',
            self.source,
        )

    def test_the_axis_controls_are_filled_and_submitted(self):
        self.assertIn('fillMovieEditCaseAxes(specs, metadata, specList)', self.source)
        self.assertIn(
            'fillMovieEditCheckboxGroup("movieEditFinishes", specList("finishes"));',
            self.source,
        )
        self.assertIn('fillMovieEditCheckboxGroup(\n        "movieEditOuterPackaging"', self.source)
        # The submit body carries the four axis keys and no `packaging`.
        for line in (
            'carrierType: formTextValue("movieEditCarrierType"),',
            'steelbookFormat: formTextValue("movieEditSteelbookFormat"),',
            'outerPackaging: collectMovieEditCheckboxGroup("movieEditOuterPackaging"),',
            'finishes: collectMovieEditCheckboxGroup("movieEditFinishes"),',
        ):
            with self.subTest(line=line):
                self.assertIn(line, self.source)
        self.assertNotIn(
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
        # The optional element id lets the box-set form reuse the same picker
        # rather than growing a second copy of the currency list.
        self.assertIn("function fillMovieEditEstimatedValueCurrency(stored, elementId)", self.source)

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
            'tNext("movieDetail.format", "Format"), '
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
