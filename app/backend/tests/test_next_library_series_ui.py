"""The series as a third kind of Library item.

The Library already grouped by container. A series is a second grouping of the
same shelf, so what matters is not that the tile renders but that the two
groupings cannot both claim the same disc, and that nothing changes when the
merge switch is off.

These are source-text assertions, in the idiom the other Library UI tests use:
the frontend is a string built by `next_views_ui.py`, so there is no module to
import and no DOM to drive here.
"""

import os
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEXT_VIEWS_UI_PATH = os.path.join(BACKEND_DIR, "next_views_ui.py")
NEXT_APP_PATH = os.path.join(BACKEND_DIR, "next_app.py")
I18N_DIR = os.path.abspath(os.path.join(BACKEND_DIR, "..", "frontend", "i18n", "next"))


class SeriesLibraryGroupingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()
        with open(NEXT_APP_PATH, encoding="utf-8") as handle:
            cls.app_source = handle.read()

    def locale(self, name):
        import json

        with open(os.path.join(I18N_DIR, f"{name}.json"), encoding="utf-8") as handle:
            return json.load(handle)

    def _function_body(self, name):
        start = self.source.index(f"function {name}(")
        end = self.source.index("\n    function ", start + 1)
        return self.source[start:end]

    def test_the_snapshot_carries_series_and_not_the_sync_wire(self):
        """Layer 4 is a separate contract with its own bootstrap cost.

        `_MOVIE_SYNC_COLUMNS` reaches every client on every delta; the snapshot
        reaches one Library render. Putting the grouping in the snapshot is what
        lets this ship without touching the wire.
        """
        self.assertIn('"series": collection_series_preview_entities(conn, actor=user)', self.app_source)
        self.assertIn(
            '"seriesSeasonCoverage": collection_series_membership_entities(conn, actor=user)',
            self.app_source,
        )
        self.assertIn("movies = attach_movie_series_membership(conn, movies)", self.app_source)
        sync_columns_start = self.app_source.index("_MOVIE_SYNC_COLUMNS")
        sync_columns = self.app_source[sync_columns_start:sync_columns_start + 2000]
        self.assertNotIn("series_id", sync_columns)

    def test_an_empty_snapshot_declares_the_same_keys(self):
        """A logged-out or pre-migration render must not read `undefined`."""
        empty = self.app_source[self.app_source.index("def empty_collection_dashboard_snapshot"):]
        self.assertIn('"series": []', empty[:1200])
        self.assertIn('"seriesSeasonCoverage": []', empty[:1200])

    def test_the_series_helpers_are_guarded_on_migration_063(self):
        for name in (
            "attach_movie_series_membership",
            "collection_series_preview_entities",
            "collection_series_membership_entities",
        ):
            start = self.app_source.index(f"def {name}(")
            body = self.app_source[start:self.app_source.index("\ndef ", start + 1)]
            self.assertIn("series_tables_available(conn)", body, name)

    def test_a_series_with_no_visible_disc_is_not_listed(self):
        """Otherwise a shared instance hands every user the titles on every
        other user's shelf -- the series row itself carries no owner."""
        start = self.app_source.index("def collection_series_preview_entities(")
        body = self.app_source[start:self.app_source.index("\ndef ", start + 1)]
        self.assertIn("JOIN movies m ON m.series_id = s.id", body)
        self.assertIn("visible_movie_where_sql", body)

    def test_the_merge_switch_off_still_returns_a_flat_movie_list(self):
        body = self._function_body("libraryDisplayItems")
        early_return = body.index("if (!mergeEditionsAsTitleEnabled())")
        series_items = body.index("const seriesItems = visibleSeriesItems(")
        self.assertLess(
            early_return,
            series_items,
            "series grouping must sit behind the merge switch, not in front of it",
        )

    def test_a_series_claims_a_disc_before_a_container_does(self):
        """Both group the same shelf. The series fills itself from the feed's own
        television id, so it wins -- and the container keeps whatever is left
        rather than disappearing when it also holds films."""
        body = self._function_body("libraryDisplayItems")
        series_at = body.index("const seriesItems = visibleSeriesItems(")
        container_at = body.index("const containerItems = visibleContainerItems(")
        self.assertLess(series_at, container_at)
        self.assertIn(
            "visibleMovies: item.visibleMovies.filter((movie) => !representedMovieIds.has(String(movie.id || \"\")))",
            body,
        )
        self.assertIn(".filter((item) => item.visibleMovies.length > 0)", body)

    def test_every_disc_is_counted_once_whichever_tile_claims_it(self):
        body = self._function_body("libraryDisplayMovieCount")
        self.assertIn("if (itemIsGroup(item)) {", body)
        self.assertIn("const ids = new Set();", body)

    def test_a_series_tile_is_not_a_bulk_target(self):
        """There is nothing to move, lend or delete on a series that is not one
        of its discs, and a half-selected group is worse than none."""
        body = self._function_body("bulkSelectableLibraryItems")
        self.assertNotIn('item.kind === "series"', body)
        self.assertIn('if (item.kind === "container") return collectorsModeEnabled()', body)

    def test_a_series_tile_opens_the_series_page(self):
        """The tile used to drill into the Library itself, because there was no
        page to open. There is one now, and two ways into the same list is one
        too many -- so the focus mode is gone rather than kept alongside it."""
        self.assertIn("openAppSeriesDetail(button.dataset.previewSeries)", self.source)
        self.assertNotIn("librarySeriesFocusId", self.source)
        self.assertNotIn("focusLibrarySeries", self.source)
        self.assertNotIn("librarySeriesFocus", self.source)

    def test_a_complete_series_set_does_not_read_as_zero_seasons(self):
        body = self._function_body("seriesSeasonSummaryText")
        self.assertIn('if (!numbers.length) return "";', body)

    def test_series_membership_is_indexed_rather_than_rescanned(self):
        """One scan per series over the whole movie list is the same N+1 the
        container index was built to remove."""
        body = self._function_body("seriesMemberMovies")
        self.assertIn("seriesMemberIndex()", body)
        self.assertNotIn(".filter(", body)

    def test_the_series_page_is_a_real_route_and_surface(self):
        """A series must be linkable. Without the shell route the URL 404s before
        any JavaScript runs, and without the surface id the page can never be the
        visible one."""
        self.assertIn('@flask_app.get("/series/<series_id>")', self.app_source)
        self.assertIn('@flask_app.get("/app/series/<series_id>")', self.app_source)
        self.assertIn('"/app/series/"', self.app_source)
        self.assertIn('"seriesDetailPage",', self.source)
        self.assertIn('return {view: "series", seriesId:', self.source)
        # Every dispatch site, not just the boot one: back/forward and in-app
        # navigation each go through their own copy.
        self.assertEqual(self.source.count('route.view === "series"'), 3)

    def test_the_six_tabs_use_the_shared_submenu_mechanism(self):
        """No new JavaScript for tab switching. A page that rolled its own would
        also have to reimplement restoring the active tab after a re-render."""
        self.assertEqual(self.source.count('data-detail-tab="seriesDetail"'), 6)
        self.assertEqual(self.source.count('data-detail-panel-group="seriesDetail"'), 6)
        self.assertIn('activateDetailTab("seriesDetail", document.getElementById(activePanelId)', self.source)

    def test_a_three_way_view_control_is_normalised_three_ways(self):
        """`normalizeViewMode` knows only poster and list, so routing a Detail
        button through it silently produced Posters -- the button highlighted and
        nothing changed. The container control had the same bug."""
        self.assertIn("function normalizeMemberViewMode(value)", self.source)
        self.assertIn('["poster", "list", "detail"].includes(value)', self.source)
        self.assertIn("normalizeMemberViewMode(button.dataset.seriesViewMode)", self.source)
        self.assertIn("normalizeMemberViewMode(button.dataset.containerViewMode)", self.source)
        start = self.source.index("function setContainerMemberGridMode(")
        body = self.source[start:self.source.index("\n    function ", start + 1)]
        self.assertIn("normalizeMemberViewMode(mode)", body)

    def test_artwork_handlers_dispatch_on_the_entity_rather_than_a_boolean(self):
        """Three entities carry artwork now, and `isContainer ? a : b` cannot say
        three. A missed branch would have posted a series to the movies route."""
        self.assertIn("function detailArtworkEntity(entity)", self.source)
        for name in ("setPrimaryArtwork", "uploadDetailArtwork", "deleteDetailArtwork"):
            start = self.source.index(f"function {name}(")
            body = self.source[start:self.source.index("\n    async function ", start + 1)]
            self.assertNotIn("isContainer", body, name)
            self.assertIn("detailArtworkEntity(entity)", body, name)

    def test_the_series_artwork_tabs_carry_no_lock_button(self):
        """A lock stops an automatic source overwriting a chosen image, and
        nothing fetches series artwork yet. The button would guard nothing while
        implying it guards something."""
        self.assertNotIn("seriesPosterLockToggle", self.source)
        self.assertNotIn("seriesBackdropLockToggle", self.source)
        self.assertIn('data-upload-artwork="series"', self.source)

    def test_the_series_page_shares_the_container_layout_rules(self):
        """The submenu and the panels have to span `.movie-detail-body`, which is
        a two-column grid. Without `grid-column: 1 / -1` the tab bar renders as a
        narrow pill in the left column and the panels leave half the page empty --
        which is exactly what shipped, because the series classes carried no CSS
        at all.

        Asserting they are *the same selector list* rather than that each has its
        own copy: a second copy of these rules is how two pages that should look
        identical drift apart.
        """
        for selector in (
            ".container-detail-submenu,\n    .series-detail-submenu {",
            ".container-detail-panel,\n    .series-detail-panel {",
            ".container-detail-panel.hidden,\n    .series-detail-panel.hidden {",
        ):
            with self.subTest(selector=selector.split("{")[0].strip()):
                self.assertIn(selector, self.source)
        # The responsive overrides too -- a sticky bar that cannot scroll
        # horizontally on a phone is a bar with tabs you cannot reach.
        self.assertIn(
            "      .container-detail-submenu,\n      .series-detail-submenu {\n        position: static;",
            self.source,
        )
        self.assertIn(
            "      .container-detail-panel,\n      .series-detail-panel,\n      .container-overview-grid {",
            self.source,
        )

    def test_deleting_a_series_says_the_discs_survive(self):
        """The route already keeps them; the sentence is what makes that
        knowable before clicking. It also names the recreate, which is the part
        a reader would otherwise discover only after the next sync."""
        self.assertIn("seriesDetail.deleteConfirm", self.source)
        confirm = self.locale("en-US")["seriesDetail.deleteConfirm"]
        self.assertIn("discs stay", confirm)
        self.assertIn("recreate", confirm)

    def test_the_new_labels_exist_in_every_locale(self):
        import json

        keys = (
            "collection.seriesTile",
            "collection.seasonsCovered",
            "collection.discs",
            "collection.seasonNumber",
            "seriesDetail.title",
            "seriesDetail.discs",
            "seriesDetail.deleteSeries",
            "seriesDetail.deleteConfirm",
            "seriesDetail.completeSeries",
            "seriesDetail.seasonOwned",
            "seriesDetail.seasonMissing",
            "seriesDetail.episodeCount",
            "seriesDetail.videosHelp",
            "seriesDetail.metadataNoAnswer",
        )
        for name in sorted(os.listdir(I18N_DIR)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(I18N_DIR, name), encoding="utf-8") as handle:
                data = json.load(handle)
            for key in keys:
                with self.subTest(locale=name, key=key):
                    self.assertTrue(str(data.get(key) or "").strip(), f"{name} is missing {key}")
            with self.subTest(locale=name, key="placeholder"):
                self.assertIn("{covered}", data["collection.seasonsCovered"])


if __name__ == "__main__":
    unittest.main()
