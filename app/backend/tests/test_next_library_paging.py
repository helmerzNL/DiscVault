import json
import os
import re
import sys
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO_APP_DIR = os.path.abspath(os.path.join(BACKEND_DIR, ".."))
NEXT_VIEWS_UI_PATH = os.path.join(BACKEND_DIR, "next_views_ui.py")
NEXT_APP_PATH = os.path.join(BACKEND_DIR, "next_app.py")
LIBRARY_PAGING_JS_PATH = os.path.join(REPO_APP_DIR, "frontend", "js", "library-paging.js")
LIBRARY_EXPORT_JS_PATH = os.path.join(REPO_APP_DIR, "frontend", "js", "library-export.js")
I18N_DIR = os.path.join(REPO_APP_DIR, "frontend", "i18n", "next")

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import next_library_data  # noqa: E402
import next_static  # noqa: E402


class FakeArgs:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


class LibraryPageParamTests(unittest.TestCase):
    def test_defaults_are_applied_when_no_arguments_are_given(self):
        limit, offset = next_library_data.parse_page_params(FakeArgs({}))
        self.assertEqual(limit, next_library_data.DEFAULT_PAGE_SIZE)
        self.assertEqual(offset, 0)

    def test_limit_is_capped_at_the_maximum_page_size(self):
        limit, _ = next_library_data.parse_page_params(FakeArgs({"limit": "100000"}))
        self.assertEqual(limit, next_library_data.MAX_PAGE_SIZE)

    def test_offset_is_parsed(self):
        limit, offset = next_library_data.parse_page_params(
            FakeArgs({"limit": "50", "offset": "250"})
        )
        self.assertEqual((limit, offset), (50, 250))

    def test_invalid_values_are_rejected(self):
        for args in (
            {"limit": "abc"},
            {"offset": "abc"},
            {"limit": "0"},
            {"limit": "-1"},
            {"offset": "-1"},
        ):
            with self.subTest(args=args):
                with self.assertRaises(next_library_data.NextApiError):
                    next_library_data.parse_page_params(FakeArgs(args))


class LibraryStaticScriptTests(unittest.TestCase):
    def test_only_whitelisted_scripts_are_addressable(self):
        self.assertIn("library-paging.js", next_static.NEXT_SCRIPT_ASSETS)
        with self.assertRaises(KeyError):
            next_static.next_script_url("../next_app.py")

    def test_every_whitelisted_script_exists_on_disk(self):
        for name in sorted(next_static.NEXT_SCRIPT_ASSETS):
            path = os.path.join(REPO_APP_DIR, "frontend", "js", name)
            with self.subTest(script=name):
                self.assertTrue(os.path.isfile(path), f"missing frontend script: {path}")

    def test_script_url_uses_the_public_prefix(self):
        self.assertEqual(
            next_static.next_script_url("library-paging.js"),
            "/api/next/app/js/library-paging.js",
        )


class LibraryPagingSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.ui_source = handle.read()
        with open(NEXT_APP_PATH, encoding="utf-8") as handle:
            cls.app_source = handle.read()
        with open(LIBRARY_PAGING_JS_PATH, encoding="utf-8") as handle:
            cls.js_source = handle.read()

    def test_backend_snapshot_reports_paging_metadata(self):
        self.assertIn('"moviesTotal": movies_total,', self.app_source)
        self.assertIn('"moviesPageSize": COLLECTION_MOVIE_PAGE_SIZE,', self.app_source)
        self.assertIn('"moviesHasMore": len(movies) < movies_total,', self.app_source)
        self.assertIn("def collection_movie_total_count(", self.app_source)

    def test_movie_preview_query_supports_an_offset(self):
        start = self.app_source.index("def collection_movie_preview_entities(")
        end = self.app_source.index("\ndef ", start + 1)
        body = self.app_source[start:end]
        self.assertIn("offset: int = 0", body)
        self.assertEqual(body.count("LIMIT %s OFFSET %s"), 2)
        self.assertEqual(body.count("limit, offset),"), 2)

    def test_new_route_modules_are_registered(self):
        self.assertIn("register_next_static_routes(flask_app, connect=connect)", self.app_source)
        self.assertIn(
            "register_next_library_data_routes(flask_app, connect=connect)", self.app_source
        )
        self.assertIn('f"{NEXT_SCRIPT_URL_PREFIX}/",', self.app_source)

    def test_library_render_cap_is_gone(self):
        self.assertNotIn(".slice(0, 80)", self.ui_source)
        self.assertIn("const LIBRARY_RENDER_STEP = 120;", self.ui_source)
        self.assertIn("function libraryVisibleSlice(items)", self.ui_source)
        self.assertIn("function libraryRenderSentinelHtml(total)", self.ui_source)
        self.assertIn("data-library-render-sentinel", self.ui_source)

    def test_counters_use_the_server_side_total(self):
        self.assertIn("${visibleMovieCount} / ${libraryMovieTotal} ${movieLabel}", self.ui_source)
        self.assertIn(
            "if (navMovieCount) navMovieCount.textContent = String(libraryMovieTotal);",
            self.ui_source,
        )

    def test_bridge_and_script_tag_are_present(self):
        self.assertIn("window.DiscVaultLibrary = {", self.ui_source)
        for member in (
            "appendMovies:",
            "getLoadedCount:",
            "hasMoreMovies:",
            "setMovieTotal:",
            "setHydrationComplete:",
            "growRenderLimit:",
            "getRenderStep:",
            "getFilteredMovies:",
            "authHeaders:",
            "onRender:",
        ):
            with self.subTest(member=member):
                self.assertIn(member, self.ui_source)
        self.assertIn(
            f'<script src="{next_static.next_script_url("library-paging.js")}" defer></script>',
            self.ui_source,
        )

    def test_paging_script_talks_to_the_documented_endpoint(self):
        self.assertIn('var MOVIES_ENDPOINT = "/api/next/collection/movies";', self.js_source)
        self.assertIn("IntersectionObserver", self.js_source)
        self.assertIn("data-library-render-sentinel", self.js_source)
        self.assertIn("collection.hydrationFailed", self.js_source)

    def test_a_failed_page_is_retried_instead_of_disabling_hydration(self):
        # A single failed request used to set a terminal `failed` flag, which left the
        # library stuck on its first page for the rest of the page load.
        self.assertNotIn("state.failed", self.js_source)
        self.assertIn("var RETRY_DELAYS_MS =", self.js_source)
        self.assertIn("function scheduleRetry()", self.js_source)
        self.assertIn('window.addEventListener("online"', self.js_source)

    def test_truncated_hydration_is_not_reported_as_complete(self):
        # setHydrationComplete() rewrites the total to the loaded count, so calling it
        # on a truncated load makes the counter claim a short library is whole.
        self.assertIn("function stopTruncated(", self.js_source)
        start = self.js_source.index("function stopTruncated(")
        end = self.js_source.index("function scheduleRetry(", start)
        self.assertNotIn("setHydrationComplete", self.js_source[start:end])
        self.assertIn("collection.hydrationTruncated", self.js_source)

    def test_a_finished_hydration_can_restart_after_a_snapshot_reset(self):
        # The inline SPA reloads its snapshot on its own (returning from a movie, an
        # import, a bulk action) and resets the bridge's movies/hasMore straight back to
        # the small first-paint set, entirely outside this module. A `state.hydrated`
        # latch that blocks hydrate() forever after the first completion would leave the
        # library stuck short again on every one of those refreshes, with no error and
        # no way back short of a full reload - hasMoreMovies() must be the only thing
        # that decides whether there is work to do, checked fresh on every call.
        start = self.js_source.index("function hydrate() {")
        end = self.js_source.index("var chunks = 0;", start)
        body = self.js_source[start:end]
        guard_line = self.js_source[
            self.js_source.index("if (!api ||", start):
            self.js_source.index("return;", start) + len("return;")
        ]
        self.assertNotIn("state.hydrated", guard_line)
        self.assertIn("state.hydrating", guard_line)
        self.assertIn("state.truncated", guard_line)
        self.assertIn("state.aborted", guard_line)
        self.assertIn("hasMoreMovies", body)
        self.assertIn("state.hydrated = false;", body)

    def test_a_network_failure_give_up_can_still_retry_later(self):
        # A page fetch that fails 3 times used to give up forever: nothing but a real
        # `online` transition or a full reload ever called hydrate() again, and a
        # transient server hiccup (not a connectivity loss) never fires `online`.
        for name in ("SLOW_RETRY_DELAYS_MS", "function wakeFromTruncation(", "function scheduleSlowRetry("):
            with self.subTest(name=name):
                self.assertIn(name, self.js_source)
        start = self.js_source.index("function onPageError(")
        end = self.js_source.index("function hydrate(", start)
        body = self.js_source[start:end]
        self.assertIn("stopTruncated(", body)
        self.assertIn("scheduleSlowRetry();", body)
        self.assertLess(body.index("stopTruncated("), body.index("scheduleSlowRetry();"))

    def test_max_chunks_and_the_anti_loop_guard_never_auto_retry(self):
        # Unlike a failed fetch, these two cannot self-resolve: MAX_CHUNKS is a
        # deliberate ceiling, and the anti-loop guard is a genuine data/offset anomaly
        # that an indefinite silent retry would turn into a bug nobody ever reports.
        start = self.js_source.index("if (chunks >= MAX_CHUNKS)")
        end = self.js_source.index("chunks += 1;", start)
        self.assertNotIn("scheduleSlowRetry", self.js_source[start:end])
        start = self.js_source.index("if (!added) {")
        end = self.js_source.index("requestRender(false);", start)
        self.assertNotIn("scheduleSlowRetry", self.js_source[start:end])

    def test_online_and_visibility_regain_both_wake_a_truncated_hydration(self):
        # Both triggers must go through the same consolidated entry point, or a stale
        # slow-retry timer from one path can survive and double-fire hydrate() later,
        # racing whatever retry the other path already started.
        start = self.js_source.index('window.addEventListener("online"')
        end = self.js_source.index('document.addEventListener("visibilitychange"', start)
        self.assertIn("wakeFromTruncation();", self.js_source[start:end])
        start = self.js_source.index('document.addEventListener("visibilitychange"')
        end = self.js_source.index('window.addEventListener("pagehide"', start)
        visibility_body = self.js_source[start:end]
        self.assertIn("wakeFromTruncation();", visibility_body)
        self.assertIn("state.truncated", visibility_body)

    def test_wake_from_truncation_clears_the_slow_retry_timer_first(self):
        # Otherwise a wake triggered by focus/visibility regain leaves the original
        # slow-retry timer armed, and it fires later mid-retry, double-triggering
        # hydrate() and resetting the backoff counter it was supposed to be escalating.
        start = self.js_source.index("function wakeFromTruncation(")
        end = self.js_source.index("function scheduleSlowRetry(", start)
        body = self.js_source[start:end]
        self.assertIn("clearTimeout(state.slowRetryTimer)", body)
        self.assertLess(body.index("clearTimeout"), body.index("hydrate();"))

    def test_slow_retry_backoff_only_resets_on_real_success(self):
        # Every wake attempt against a still-down backend must keep escalating the
        # backoff; only finishHydration() (an actual successful page) may reset it, or
        # a focus/visibility regain would pin it back at the shortest delay forever.
        self.assertIn("state.slowAttempt = 0;", self.js_source)
        start = self.js_source.index("function finishHydration(")
        end = self.js_source.index("function stopTruncated(", start)
        self.assertIn("state.slowAttempt = 0;", self.js_source[start:end])
        self.assertNotIn("slowAttempt = 0", self.js_source[
            self.js_source.index("function wakeFromTruncation("):
            self.js_source.index("function scheduleSlowRetry(")
        ])

    def test_render_growth_is_deferred_to_an_animation_frame(self):
        # Growing the window synchronously inside the observer callback re-renders,
        # emits a new sentinel still inside the root margin and fires again — a
        # cascade of full re-renders in a single burst.
        self.assertIn("function scheduleGrowth()", self.js_source)
        self.assertIn("requestAnimationFrame", self.js_source)
        start = self.js_source.index("new window.IntersectionObserver(")
        end = self.js_source.index("function onRender()", start)
        callback = self.js_source[start:end]
        self.assertIn("scheduleGrowth()", callback)
        self.assertNotIn("growRenderLimit", callback)

    def test_client_chunk_size_is_below_the_server_ceiling(self):
        # When the two are equal the server clamp lands exactly on the client's
        # request, so raising CHUNK_SIZE alone would silently do nothing.
        match = re.search(r"var CHUNK_SIZE = (\d+);", self.js_source)
        assert match is not None
        self.assertLess(int(match.group(1)), next_library_data.MAX_PAGE_SIZE)

    def test_hydration_warning_is_surfaced_in_the_ui(self):
        self.assertIn("setHydrationWarning:", self.ui_source)
        self.assertIn("libraryHydrationWarning", self.ui_source)
        self.assertIn("library-hydration-warning", self.ui_source)

    def test_container_membership_lookups_are_indexed(self):
        # Resolving members per container used to scan the whole membership table and
        # the whole movie list, for every container, several times per render.
        self.assertIn("function containerMemberIndex()", self.ui_source)
        start = self.ui_source.index("function containerMemberMovies(")
        end = self.ui_source.index("function containerIsNestedChild(", start)
        body = self.ui_source[start:end]
        self.assertIn("containerMemberIndex()", body)
        self.assertNotIn(".filter(", body)

    def test_no_movie_listing_surface_still_caps_at_200(self):
        # Leftovers of the removed library cap: the legacy collection view asked for
        # 200 movies, and /api/next/movies clamped to 200 so the query string could
        # not have raised it anyway.
        #
        # The parsing moved to the shared parse_int_arg (PERF-04) so a malformed
        # value answers 400 rather than 500. The numbers this test exists for --
        # default 50, ceiling 1000, not 200 -- are unchanged, and are what is
        # asserted here rather than the expression that carries them.
        self.assertIn(
            'parse_int_arg("limit", 50, minimum=1, maximum=1000)',
            self.app_source,
        )
        legacy = os.path.join(BACKEND_DIR, "next_views_collection.py")
        with open(legacy, encoding="utf-8") as handle:
            legacy_source = handle.read()
        self.assertNotIn("/api/next/movies?limit=200", legacy_source)

    def test_the_dead_page_size_ceiling_constant_is_gone(self):
        # It was never referenced; the real ceiling is next_library_data.MAX_PAGE_SIZE.
        self.assertNotIn("COLLECTION_MOVIE_MAX_PAGE_SIZE", self.app_source)

    def test_library_posters_are_lazily_loaded(self):
        self.assertIn(
            '<img src="${escapeHtml(poster)}" alt="" loading="lazy" decoding="async">',
            self.ui_source,
        )

    def test_paging_i18n_keys_exist_in_every_locale(self):
        locales = sorted(name for name in os.listdir(I18N_DIR) if name.endswith(".json"))
        self.assertTrue(locales)
        keys = (
            "collection.loadingMoreRows",
            "collection.hydrationFailed",
            "collection.hydrationTruncated",
            "collection.exportIncomplete",
            "app.updateAvailable",
            "app.updateReload",
            "common.close",
        )
        for name in locales:
            with open(os.path.join(I18N_DIR, name), encoding="utf-8") as handle:
                data = json.load(handle)
            for key in keys:
                with self.subTest(locale=name, key=key):
                    self.assertIn(key, data)
                    self.assertTrue(str(data[key]).strip())


class LibraryExportHydrationCountTests(unittest.TestCase):
    """The export dialog's "Only {count} loaded so far" warning must describe the
    library's raw hydration progress, not the filtered/sorted export row set - those
    two only agree when no search/format filter is active.
    """

    @classmethod
    def setUpClass(cls):
        with open(LIBRARY_EXPORT_JS_PATH, encoding="utf-8") as handle:
            cls.js_source = handle.read()

    def test_loaded_movie_count_helper_reads_from_the_bridge(self):
        self.assertIn("function loadedMovieCount(fallback)", self.js_source)
        start = self.js_source.index("function loadedMovieCount(fallback)")
        end = self.js_source.index("\n  }\n", start)
        self.assertIn("getLoadedCount", self.js_source[start:end])

    def test_the_incomplete_warning_uses_the_loaded_count_not_the_filtered_row_count(self):
        self.assertIn("collection.exportIncomplete", self.js_source)
        start = self.js_source.index("if (hydrationIncomplete())")
        end = self.js_source.index("dialog.appendChild(incomplete);", start)
        body = self.js_source[start:end]
        self.assertIn("loadedMovieCount(rowCount)", body)
        self.assertNotRegex(body, r",\s*rowCount\s*\)\s*;\s*$")

    def test_the_export_summary_still_uses_the_filtered_row_count(self):
        # The "{count} movies will be exported" line is correctly scoped to what's
        # actually being exported - only the "loaded so far" warning was wrong.
        start = self.js_source.index("collection.exportSummary")
        end = self.js_source.index("dialog.appendChild(summary);", start)
        self.assertIn("rowCount", self.js_source[start:end])


if __name__ == "__main__":
    unittest.main()
