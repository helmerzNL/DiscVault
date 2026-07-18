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


class NextLibraryListUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()
        with open(NEXT_APP_PATH, encoding="utf-8") as handle:
            cls.app_source = handle.read()

    def test_library_snapshot_includes_studios_and_concrete_tags(self):
        self.assertEqual(
            self.app_source.count("m.metadata->>'studios' AS studios"),
            2,
        )
        self.assertEqual(
            self.app_source.count("m.metadata->>'director' AS director"),
            2,
        )
        self.assertEqual(
            self.app_source.count("m.metadata->>'actor' AS actor"),
            2,
        )
        self.assertIn(
            "SELECT mt.movie_id, t.id, t.name, t.slug, t.color",
            self.app_source,
        )
        self.assertIn('row["tags"] = tags_by_movie.get(movie_id, [])', self.app_source)
        self.assertIn('row["has_tags"] = movie_id in tagged_movies', self.app_source)

    def test_main_library_list_uses_a_semantic_table(self):
        self.assertIn("function libraryListTableHtml(items)", self.source)
        self.assertIn('<table class="library-list-table" aria-label=', self.source)
        self.assertIn("<thead>", self.source)
        self.assertIn("<tbody>", self.source)
        self.assertIn("? libraryListTableHtml(displayItems)", self.source)

    def test_compact_and_desktop_column_sets_switch_at_1024_pixels(self):
        self.assertIn("@media (max-width: 1024px)", self.source)
        self.assertIn(".library-list-desktop-column {", self.source)
        self.assertIn("width: 72px;", self.source)
        self.assertIn(".library-list-title-target strong,", self.source)
        self.assertIn("overflow-wrap: anywhere;", self.source)
        self.assertIn(
            'libraryListSortHeaderHtml("format", '
            'tNext("movieDetail.format", "Format"), normalizedSort, '
            '"library-list-format-column")',
            self.source,
        )
        self.assertIn(
            '<td class="library-list-format-column">'
            "${libraryListFormatsHtml(item)}</td>",
            self.source,
        )
        self.assertNotIn("library-list-compact-column", self.source)
        for key in (
            '"format"',
            '"director"',
            '"actors"',
            '"studios"',
            '"rating"',
            '"tags"',
            '"behavior"',
        ):
            self.assertIn(f"libraryListSortHeaderHtml({key}", self.source)

    def test_library_does_not_show_a_redundant_panel_header(self):
        self.assertNotIn('id="libraryPanelTitle"', self.source)
        self.assertNotIn('id="shownCount"', self.source)

    def test_poster_and_title_are_separate_detail_links_with_release_year(self):
        self.assertIn(
            'class="library-list-poster-target" ${targetAttr}',
            self.source,
        )
        self.assertIn(
            'class="library-list-title-target" ${targetAttr}',
            self.source,
        )
        self.assertIn(
            '${year ? `<span class="library-list-year">'
            '(${escapeHtml(year)})</span>` : ""}',
            self.source,
        )
        self.assertNotIn('<tr ${targetAttr}', self.source)

    def test_desktop_metadata_is_deduplicated_and_actor_count_is_capped(self):
        self.assertIn("function itemMovieRows(item)", self.source)
        self.assertIn(
            "if (Array.isArray(item.visibleMovies)) return item.visibleMovies;",
            self.source,
        )
        self.assertIn(
            "if (!key || seen.has(key) || result.length >= 5) return;",
            self.source,
        )
        self.assertIn(
            ".library-list-format-badges .physical-format-badge {",
            self.source,
        )
        self.assertIn("function itemStudioValues(item)", self.source)
        self.assertIn("function itemRatingValues(item)", self.source)
        self.assertIn("function itemTagValues(item)", self.source)
        self.assertIn(
            ": splitCreditText(metadata.director || movie?.director, 3);",
            self.source,
        )
        self.assertIn(
            ": splitCreditText(metadata.actor || movie?.actor, 5);",
            self.source,
        )

    def test_behavior_filters_are_persisted_counted_and_reset(self):
        self.assertIn('localStorage.getItem("dv_next_hide_watchlist")', self.source)
        self.assertIn('localStorage.getItem("dv_next_hide_watched")', self.source)
        self.assertIn("if (hideWatchlist) count += 1;", self.source)
        self.assertIn("if (hideWatched) count += 1;", self.source)
        self.assertIn("hideWatchlist = false;", self.source)
        self.assertIn("hideWatched = false;", self.source)
        self.assertIn(
            "&& movieMatchesBehaviorExclusions(movie)",
            self.source,
        )

    def test_container_aggregation_only_uses_movies_that_survive_filters(self):
        self.assertIn(
            "const visibleMovies = containerMemberMovies(container.id)",
            self.source,
        )
        self.assertIn(
            "return {kind: \"container\", container, visibleMovies, "
            'title: container.title || ""};',
            self.source,
        )
        self.assertIn(
            ".filter((item) => item.visibleMovies.length > 0);",
            self.source,
        )
        self.assertIn(
            "itemMovieRows(item).forEach((movie) => "
            'ids.add(String(movie?.id || "")));',
            self.source,
        )

    def test_member_group_library_groups_shared_container_movies(self):
        self.assertIn("function memberGroupLibrarySelected()", self.source)
        self.assertIn(
            "return memberGroupLibrarySelected() || "
            "(collectorsModeEnabled() && preferences.merge_editions_as_title === true);",
            self.source,
        )
        self.assertIn("if (!containerGroupingEnabled()) return [];", self.source)

    def test_member_group_owner_can_rename_and_safely_delete_group(self):
        self.assertIn('data-member-group-rename="${id}"', self.source)
        self.assertIn('canDelete ? `<button type="button" class="danger-button" data-member-group-delete="${id}"', self.source)
        self.assertIn('method: "PATCH"', self.source)
        self.assertIn('method: "DELETE"', self.source)
        self.assertIn("await loadAppSnapshot();", self.source)
        self.assertIn('@flask_app.patch("/api/next/media-groups/<group_id>")', self.app_source)
        self.assertIn('@flask_app.delete("/api/next/media-groups/<group_id>")', self.app_source)
        self.assertIn('owner_state.get("actor_role") != "owner"', self.app_source)
        self.assertIn("media_group_owner_can_delete(actor, owner_state)", self.app_source)

    def test_column_sorting_is_accessible_and_disables_toolbar_sort(self):
        self.assertIn("function libraryListSortHeaderHtml(", self.source)
        self.assertIn('aria-sort="${sortState.direction', self.source)
        self.assertIn('data-detail-sort-scope="library"', self.source)
        self.assertIn(".localeCompare(", self.source)
        self.assertIn("compareLibraryBehavior(a, b)", self.source)
        self.assertIn("sortTrigger.disabled = mainLibraryListActive;", self.source)
        self.assertIn(
            'sortTrigger.setAttribute("aria-disabled", '
            'mainLibraryListActive ? "true" : "false");',
            self.source,
        )

    def test_list_rows_share_bulk_selection_highlight(self):
        self.assertIn(
            'button.closest("tr")?.classList.toggle("bulk-selected", selected);',
            self.source,
        )
        self.assertIn(
            'node.closest("tr")?.classList.toggle("bulk-selected", selected);',
            self.source,
        )
        self.assertIn(
            ".library-list-table tbody tr.bulk-selected td {",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
