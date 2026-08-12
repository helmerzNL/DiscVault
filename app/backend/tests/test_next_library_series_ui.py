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
import re
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEXT_VIEWS_UI_PATH = os.path.join(BACKEND_DIR, "next_views_ui.py")
NEXT_APP_PATH = os.path.join(BACKEND_DIR, "next_app.py")
NEXT_LIBRARY_DATA_PATH = os.path.join(BACKEND_DIR, "next_library_data.py")
I18N_DIR = os.path.abspath(os.path.join(BACKEND_DIR, "..", "frontend", "i18n", "next"))


class SeriesLibraryGroupingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()
        with open(NEXT_APP_PATH, encoding="utf-8") as handle:
            cls.app_source = handle.read()
        # The Library is served from two modules, and the second one falling
        # behind the first is the bug this file now guards against.
        with open(NEXT_LIBRARY_DATA_PATH, encoding="utf-8") as handle:
            cls.library_data_source = handle.read()

    def locale(self, name):
        import json

        with open(os.path.join(I18N_DIR, f"{name}.json"), encoding="utf-8") as handle:
            return json.load(handle)

    def _function_body(self, name):
        start = self.source.index(f"function {name}(")
        end = self.source.index("\n    function ", start + 1)
        return self.source[start:end]

    def test_the_snapshot_keeps_its_own_shape_now_that_the_wire_carries_series_too(self):
        """The snapshot came first, deliberately: it reaches one Library render,
        while `_MOVIE_SYNC_COLUMNS` reaches every client on every delta, so
        staging the grouping here let it ship without touching a contract.

        The wire caught up in layer 4. The snapshot keeps its own shaping anyway
        rather than being replaced by the wire's -- it answers a different
        question (what this Library page draws, filtered to what this user may
        see) and collapsing the two would make one of them wrong.
        """
        self.assertIn('"series": collection_series_preview_entities(conn, actor=user)', self.app_source)
        self.assertIn(
            '"seriesSeasonCoverage": collection_series_membership_entities(conn, actor=user)',
            self.app_source,
        )
        self.assertIn("movies = attach_library_movie_enrichments(conn, movies, user)", self.app_source)

    def test_both_halves_of_the_library_enrich_a_movie_row_the_same_way(self):
        """The snapshot serves the first page and `library_movie_page` serves the
        rest, so a row must not depend on which of the two produced it. It did:
        paging shipped before series grouping and never gained the attach, so a
        disc past the boundary arrived with no series, its tile dropped it, and
        it reappeared beside the tile as a loose disc.

        The previous version of this test asserted the attach appeared in
        `next_app.py` and nothing more, which the snapshot alone satisfied -- so
        it passed for the whole life of the bug. Asserting the shared helper is
        what makes the two paths impossible to enrich differently.
        """
        start = self.app_source.index("def attach_library_movie_enrichments(")
        body = self.app_source[start:self.app_source.index("\ndef ", start + 1)]
        self.assertIn("attach_personal_list_state(conn, movies, user_id)", body)
        self.assertIn("attach_movie_series_membership(conn, movies)", body)

        self.assertIn(
            "items = app.attach_library_movie_enrichments(conn, items, user)",
            self.library_data_source,
        )
        self.assertNotIn(
            "attach_personal_list_state",
            self.library_data_source,
            "the paged path must not enrich rows itself -- that is how it fell behind",
        )

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

    def test_the_tile_prefers_the_series_own_poster_to_a_disc_s(self):
        """The tile borrowed a disc's cover because a series had no artwork of
        its own to borrow instead. That stopped being true when the series page
        gained upload and refresh, and the Library was the surface that never
        learned -- so a poster set on a series showed on its page and not on the
        tile beside it, and the two disagreed about the same show.

        The borrowing stays as the fallback: an empty tile reads as a bug rather
        than as a gap. What changed is the order, so the order is what this
        asserts."""
        body = self._function_body("itemPosterUrl")
        own = body.index("item.series?.posterUrl")
        borrowed = body.index("movie?.poster_url).find(Boolean)")
        self.assertLess(
            own,
            borrowed,
            "a chosen series poster must outrank a borrowed disc poster",
        )

    def test_a_season_s_poster_sits_between_the_series_own_and_a_disc_s(self):
        """Three stand-ins, in order of how much they are a picture of the show.

        A season poster is artwork *of the show*, published as such. A disc cover
        is a photograph of a package, with a studio's slipcase text and a format
        badge on it. Both are borrowed for a series nobody has given artwork to,
        but only one is a picture of the thing the tile names.

        The disc stays last rather than being dropped: no tile that shows
        something today may fall back to an empty one.
        """
        body = self._function_body("itemPosterUrl")
        own = body.index("item.series?.posterUrl")
        season = body.index("item.series?.seasonPosterUrl")
        borrowed = body.index("movie?.poster_url).find(Boolean)")
        self.assertLess(own, season)
        self.assertLess(season, borrowed)
        # Each step gets its own `usableImage` call rather than sharing one over
        # a `||` chain: the latter short-circuits on a truthy-but-unusable
        # earlier value and drops a good later one on the floor. The existing
        # comment in the function says so; this keeps the new step honouring it.
        self.assertIn("usableImage(item.series?.posterUrl)", body)
        self.assertIn("usableImage(item.series?.seasonPosterUrl)", body)

    def test_the_library_payload_carries_the_season_poster_it_may_borrow(self):
        """The frontend cannot prefer what it is never given, and the tile query
        deliberately carries no seasons -- so the one poster it may borrow is
        resolved in SQL beside the series' own."""
        start = self.app_source.index("def collection_series_preview_entities(")
        body = self.app_source[start:self.app_source.index("\ndef ", start + 1)]
        self.assertIn("em.entity_type='series_season'", body)
        # Lowest season number, not lowest id: a tile has no selection to follow,
        # and season 1 is the one a reader recognises a show by.
        self.assertIn("ORDER BY ss.season_number, em.is_primary DESC", body)
        self.assertIn("AND ss.season_number > 0", body)
        self.assertIn('"seasonPosterUrl": series_season_poster_url(row)', body)

    def test_the_borrowed_poster_is_a_field_of_its_own(self):
        """Folding it into `posterUrl` would leave no caller able to tell a
        poster the series owns from one it is standing in with -- and the series
        page needs that difference, because only a series without its own follows
        the season rail."""
        start = self.app_source.index("def series_season_poster_url(")
        body = self.app_source[start:self.app_source.index("\ndef ", start + 1)]
        self.assertIn('if not row.get("season_poster_id"):', body)
        self.assertIn("return None", body)

    def test_the_library_payload_carries_the_series_own_poster(self):
        """The frontend cannot prefer what it is never given. Artwork lives in
        `entity_media` under `entity_type='series'`, which is the one store both
        write paths share -- an upload also mirrors the URL into
        `series.metadata`, a refresh does not, so reading the mirror would serve
        an uploaded poster and silently miss a fetched one."""
        start = self.app_source.index("def collection_series_preview_entities(")
        body = self.app_source[start:self.app_source.index("\ndef ", start + 1)]
        self.assertIn("em.entity_type='series'", body)
        self.assertIn("ma.kind='poster'", body)
        # Prefer the primary, accept any -- the same order `mediaAssetImage`
        # applies on the series page. Filtering on is_primary instead would
        # blank the tile beside a page that shows a picture.
        self.assertIn("ORDER BY em.is_primary DESC, em.sort_order, ma.created_at", body)
        self.assertIn('"posterUrl": series_poster_url(row)', body)

    def test_a_disc_inherits_the_season_poster_rather_than_borrowing_it(self):
        """The first attempt resolved this per read, which stopped at whichever
        surface implemented it: the Library showed something, the sync delta
        carried nothing, and the iOS app still showed an empty tile. "A disc of a
        series always has a poster" is a statement about the record, so it has to
        be true of the record."""
        import os

        meta_path = os.path.join(BACKEND_DIR, "next_metadata.py")
        with open(meta_path, encoding="utf-8") as handle:
            meta_source = handle.read()
        start = meta_source.index("def apply_season_poster_inheritance(")
        body = meta_source[start:meta_source.index("\ndef ", start + 1)]
        self.assertIn("UPDATE movies m", body)
        self.assertIn("'poster_from_season', true", body)
        # The two conditions the feature was asked for with.
        self.assertIn("m.metadata->>'poster_locked'", body)
        self.assertIn("NULLIF(BTRIM(COALESCE(m.metadata->>'poster_url', '')), '') IS NULL", body)
        # Re-running must not touch `updated_at`, which feeds the sync delta.
        self.assertIn("IS DISTINCT FROM c.poster_url", body)
        # Lowest covered season, specials included -- the disc named them.
        self.assertIn("ORDER BY ms.movie_id, ss.season_number", body)
        self.assertNotIn("ss.season_number > 0", body)

    def test_every_path_that_can_change_the_answer_applies_it(self):
        """Two moments, and both had to be wired or a disc waits for someone to
        re-save it: the seasons a disc carries being assigned, and a series
        refresh bringing season artwork in. The refresh is applied inside
        `refresh_series_metadata` rather than in its route because the background
        worker calls the same function and would otherwise skip those discs."""
        import os

        with open(os.path.join(BACKEND_DIR, "next_metadata.py"), encoding="utf-8") as handle:
            meta_source = handle.read()
        start = self.app_source.index("def apply_movie_series_assignment(")
        assignment = self.app_source[start:self.app_source.index("\ndef ", start + 1)]
        self.assertIn("clear_orphaned_season_poster(cur, movie_uuid)", assignment)
        self.assertIn("apply_season_poster_inheritance(cur, [movie_uuid])", assignment)
        # Clearing first: a disc moved to a season with no artwork has to lose
        # the old season's poster rather than keep it as though it owned it.
        self.assertLess(
            assignment.index("clear_orphaned_season_poster"),
            assignment.index("apply_season_poster_inheritance"),
        )

        start = meta_source.index("def refresh_series_metadata(")
        refresh = meta_source[start:meta_source.index("\ndef ", start + 1)]
        self.assertIn("apply_season_poster_inheritance(cur, linked)", refresh)

    def test_the_migration_backfills_what_already_exists(self):
        """The application applies this from now on; the discs a user is looking
        at today were added before it existed."""
        import os

        path = os.path.join(BACKEND_DIR, "migrations_next", "078_disc_inherits_season_poster.sql")
        with open(path, encoding="utf-8") as handle:
            sql = handle.read()
        self.assertIn("'poster_from_season', true", sql)
        self.assertIn("m.metadata->>'poster_locked'", sql)
        self.assertIn("NULLIF(BTRIM(COALESCE(m.metadata->>'poster_url', '')), '') IS NULL", sql)
        # Guarded, like every other migration here: the tables may not exist yet.
        self.assertIn("to_regclass('public.movie_seasons') IS NULL", sql)

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

    def test_every_tab_uses_the_shared_submenu_mechanism(self):
        """No new JavaScript for tab switching. A page that rolled its own would
        also have to reimplement restoring the active tab after a re-render.

        Counted as tabs-equal-panels rather than against a fixed number. The
        fixed six was a proxy for the real rule and it broke the moment a
        seventh tab was justified -- while the thing worth protecting, that no
        tab is left without a panel and no panel without a tab, was never
        actually asserted. A button pointing at a panel id that does not exist
        switches to a blank page in silence.
        """
        tabs = self.source.count('data-detail-tab="seriesDetail"')
        panels = self.source.count('data-detail-panel-group="seriesDetail"')
        self.assertGreater(tabs, 0)
        self.assertEqual(tabs, panels)
        targets = set(re.findall(r'data-detail-panel="(seriesDetail\w+)"', self.source))
        ids = set(re.findall(r'data-detail-panel-group="seriesDetail" id="(seriesDetail\w+)"', self.source))
        self.assertEqual(targets, ids)
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
        """Sources do fetch series artwork now, so the reason has changed even
        though the answer has not.

        A chosen primary is already safe: `apply_primary_media_update` refuses to
        replace one whose link says it was uploaded or picked by hand, and that
        refusal is what protects a series. A lock button would be a second, more
        visible mechanism for a guarantee that already holds -- and the failure
        mode of two mechanisms is that people trust the wrong one. Add it if a
        source ever proves pushy enough to need overriding deliberately.
        """
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
