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

    def test_drilling_into_a_series_shows_its_discs_flat(self):
        body = self._function_body("libraryDisplayItems")
        focus_at = body.index("if (librarySeriesFocusId) {")
        merge_at = body.index("if (!mergeEditionsAsTitleEnabled())")
        self.assertLess(
            focus_at,
            merge_at,
            "opening a series tile must show its discs whatever the merge switch says",
        )
        self.assertIn("function clearLibrarySeriesFocus()", self.source)
        self.assertIn('id="librarySeriesFocus"', self.source)

    def test_the_focus_is_dropped_when_the_series_leaves_the_snapshot(self):
        """Otherwise a refresh after the last disc is deleted leaves the Library
        stuck on an empty grid with no way back."""
        self.assertIn(
            "if (librarySeriesFocusId && !seriesList.some((entry) => String(entry.id) === librarySeriesFocusId))",
            self.source,
        )

    def test_a_complete_series_set_does_not_read_as_zero_seasons(self):
        body = self._function_body("seriesSeasonSummaryText")
        self.assertIn('if (!numbers.length) return "";', body)

    def test_series_membership_is_indexed_rather_than_rescanned(self):
        """One scan per series over the whole movie list is the same N+1 the
        container index was built to remove."""
        body = self._function_body("seriesMemberMovies")
        self.assertIn("seriesMemberIndex()", body)
        self.assertNotIn(".filter(", body)

    def test_the_new_labels_exist_in_every_locale(self):
        import json

        keys = (
            "collection.seriesTile",
            "collection.seasonsCovered",
            "collection.discs",
            "collection.backToLibrary",
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
