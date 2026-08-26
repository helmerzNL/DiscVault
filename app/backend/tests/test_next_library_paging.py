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
MIGRATIONS_DIR = os.path.join(BACKEND_DIR, "migrations_next")
LIBRARY_SORT_MIGRATION_PATH = os.path.join(
    MIGRATIONS_DIR, "086_movies_library_sort_index.sql"
)

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
        # Both branches, or paging is stable on one code path and not the other.
        self.assertEqual(body.count("m.year NULLS LAST, m.id"), 2)

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
            "getSnapshotEpoch:",
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
        # The guard is deliberately in two parts. The first rejects only what makes the
        # bridge unusable, so that the snapshot-epoch check below it runs even while a
        # truncation is standing - that check is the only way a truncation is ever
        # cleared, and gating it behind `state.truncated` would deadlock it (#715).
        entry_guard = self.js_source[
            self.js_source.index("if (!api ||", start):
            self.js_source.index("return;", start) + len("return;")
        ]
        self.assertNotIn("state.hydrated", entry_guard)
        self.assertNotIn("state.truncated", entry_guard)
        work_guard = self.js_source[
            self.js_source.index("if (state.hydrating ||", start):
            self.js_source.index("hasMoreMovies !==", start)
        ]
        self.assertIn("state.hydrating", work_guard)
        self.assertIn("state.truncated", work_guard)
        self.assertNotIn("state.hydrated", work_guard)
        self.assertIn("state.aborted", entry_guard)
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
        #
        # The anti-loop guard is `nextOffset <= offset` - the cursor failed to move -
        # and not the older "the page added nothing". Those are not the same condition,
        # and the difference is the whole of #715: a page can legitimately add nothing
        # (every row was already held, because an unstable sort order overlapped two
        # windows) while still advancing the cursor, and killing hydration for that is
        # how a library ends up permanently short. Only a cursor that does not move is
        # an actual loop.
        start = self.js_source.index("if (chunks >= MAX_CHUNKS)")
        end = self.js_source.index("chunks += 1;", start)
        self.assertNotIn("scheduleSlowRetry", self.js_source[start:end])
        self.assertNotIn("if (!added) {", self.js_source)
        start = self.js_source.index("if (nextOffset <= offset) {")
        end = self.js_source.index("cursor = nextOffset;", start)
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


class LibraryStalePageTests(unittest.TestCase):
    """#715: with 2,509 movies the library filled to 700 and stopped for good.

    700 is 200 (the first-paint snapshot) plus exactly one 500-row hydration chunk.
    The inline SPA reloads its snapshot on its own and resets `movies` back to those
    200 rows; a page already in flight then resolved and was appended blind, landing
    hundreds of rows past the end. That left a hole offset paging can never step back
    over, the next page came back entirely duplicated, and the anti-loop guard - which
    by design never retries - ended hydration permanently.

    Two rules close it, and both are asserted here: a page may only be appended to the
    array it was fetched against, and the cursor may only advance by what the server
    says it served.
    """

    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.ui_source = handle.read()
        with open(LIBRARY_PAGING_JS_PATH, encoding="utf-8") as handle:
            cls.js_source = handle.read()

    def _append_movies_body(self):
        start = self.ui_source.index("appendMovies: (rows")
        return self.ui_source[start:self.ui_source.index("setHydrationWarning:", start)]

    @staticmethod
    def _code_only(body):
        """Drop `//` comment lines.

        These assertions are about what the code does, and the comments beside it
        deliberately name the very things the code must not do ("slowAttempt is left
        alone", "hydrating is left alone"). Matching prose would make the test pass or
        fail on how the reasoning is worded.
        """
        return "\n".join(
            line for line in body.splitlines() if not line.strip().startswith("//")
        )

    def _epoch_block(self):
        start = self.js_source.index("var epoch = snapshotEpoch(api);")
        return self.js_source[start:self.js_source.index("if (state.hydrating ||", start)]

    def _stale_branch(self):
        start = self.js_source.index("if (added === null) {")
        return self.js_source[start:self.js_source.index("var served =", start)]

    def test_a_page_fetched_against_a_stale_movie_array_is_never_appended(self):
        body = self._append_movies_body()
        self.assertIn("appendMovies: (rows, expectedOffset)", self.ui_source)
        self.assertIn("!== movies.length", body)
        self.assertIn("return null", body)

    def test_the_stale_page_signal_is_distinct_from_an_empty_page(self):
        # `0` already means "every row was a duplicate", which is survivable. Collapsing
        # the two would make the caller unable to tell a reset from a repeated page.
        body = self._append_movies_body()
        self.assertIn("return null", body)
        self.assertIn("return 0;", body)
        self.assertLess(body.index("return null"), body.index("return 0;"))

    def test_the_offset_guard_is_optional_so_an_older_caller_still_works(self):
        # library-paging.js ships in the same image as this SPA, but a service worker
        # can serve a previous build's copy of it. A one-argument call must behave
        # exactly as it did before.
        self.assertIn("expectedOffset !== undefined", self._append_movies_body())

    def test_the_length_guard_is_measured_before_the_request_not_derived_from_the_offset(self):
        # appendMovies() de-duplicates, so after any overlapping page movies.length
        # trails the server's cursor. Reusing `offset` as the guard would then misread
        # every later page as stale and restart the cycle until the cap tripped.
        start = self.js_source.index("var offset = cursor;")
        end = self.js_source.index(".then(function (payload)", start)
        head = self.js_source[start:end]
        self.assertIn("var expectedLoaded = current.getLoadedCount();", head)
        self.assertLess(head.index("var expectedLoaded"), head.index("fetchPage("))
        self.assertIn("fetchPage(current, offset)", self.js_source)
        self.assertIn("appendMovies(items, expectedLoaded)", self.js_source)
        self.assertNotIn("fetchPage(current, current.getLoadedCount())", self.js_source)

    def test_a_stale_page_restarts_the_cycle_instead_of_truncating(self):
        # A snapshot reload is normal - a save, an import, returning to the Library.
        # Reporting it as "only part of the library could be loaded" is what turned a
        # routine race into a permanent stall.
        branch = self._stale_branch()
        self.assertIn("cursor = live.getLoadedCount();", branch)
        self.assertIn("step();", branch)
        head = branch[:branch.index("MAX_STALE_RESTARTS")]
        self.assertNotIn("stopTruncated(", head)

    def test_the_stale_restart_is_bounded(self):
        # A reset arriving on every retry is a loop like any other.
        self.assertIn("var MAX_STALE_RESTARTS =", self.js_source)
        branch = self._stale_branch()
        self.assertIn("staleRestarts += 1;", branch)
        self.assertIn("staleRestarts > MAX_STALE_RESTARTS", branch)
        self.assertIn("stopTruncated(", branch)
        # Unlike MAX_CHUNKS and the cursor guard, a burst of reloads is transient.
        self.assertIn("scheduleSlowRetry();", branch)

    def test_the_cursor_advances_on_what_the_server_served(self):
        # Paging by the de-duplicated loaded count leaves the cursor short by every
        # duplicate dropped, so an overlapping page is re-requested for as long as the
        # overlap lasts - and a fully overlapping one forever.
        start = self.js_source.index("var served =")
        body = self.js_source[start:self.js_source.index("requestRender(false);", start)]
        self.assertIn("payload.offset", body)
        self.assertIn("+ items.length", body)
        self.assertIn("cursor = nextOffset;", body)
        self.assertNotIn("getLoadedCount", body)

    def test_a_cycle_starts_from_the_loaded_count(self):
        # After a snapshot reset the array really is back to 200 rows, so that is the
        # only honest place to resume from. Starting a cycle and advancing within one
        # are different questions with different answers.
        start = self.js_source.index("function hydrate() {")
        end = self.js_source.index("function step()", start)
        self.assertIn("var cursor = api.getLoadedCount();", self.js_source[start:end])

    def test_a_snapshot_reset_clears_a_truncation_the_module_cannot_otherwise_escape(self):
        # loadAppSnapshot() wipes libraryHydrationWarning but cannot reach state.truncated,
        # so the library stayed short with nothing on screen saying why.
        self.assertIn("getSnapshotEpoch: () => librarySnapshotEpoch,", self.ui_source)
        self.assertIn("librarySnapshotEpoch += 1;", self.ui_source)
        self.assertIn("function snapshotEpoch(api)", self.js_source)
        body = self._epoch_block()
        self.assertIn("state.truncated = false;", body)
        self.assertIn("state.attempt = 0;", body)
        self.assertIn("state.retryTimer = null;", body)
        self.assertIn("state.slowRetryTimer = null;", body)

    def test_a_snapshot_reset_does_not_unwind_the_slow_retry_backoff(self):
        # A reload is no evidence the backend recovered; only a page that lands may
        # shorten the backoff. Pairs with the slow-retry test above.
        self.assertNotIn("slowAttempt", self._code_only(self._epoch_block()))

    def test_a_reset_does_not_stack_a_second_cycle_on_an_in_flight_one(self):
        # An in-flight cycle must discover the reset through its own appendMovies guard.
        self.assertNotIn("state.hydrating", self._code_only(self._epoch_block()))

    def test_the_snapshot_epoch_is_bumped_wherever_the_movie_array_is_replaced(self):
        # The epoch is only honest while every wholesale reset bumps it. Two exist:
        # first paint and loadAppSnapshot(). A third added later without a bump would
        # silently reopen #715, so count them here rather than trust a reviewer to spot it.
        # A wholesale replacement is one whose right-hand side does not read the array
        # it is replacing: `movies = (movies || []).map(...)` rewrites rows in place and
        # keeps the length, so a page in flight is still valid against it.
        wholesale = [
            rhs
            for rhs in re.findall(r"^\s*(?:let )?movies = (.+);$", self.ui_source, re.M)
            if "movies" not in rhs.replace("state.movies", "")
        ]
        self.assertEqual(wholesale, ["state.movies || []"] * 2, wholesale)
        self.assertIn("let librarySnapshotEpoch = 0;", self.ui_source)
        start = self.ui_source.index("async function loadAppSnapshot()")
        end = self.ui_source.index("librarySnapshotEpoch += 1;", start)
        self.assertIn("movies = state.movies || [];", self.ui_source[start:end])

    def test_returning_to_the_library_reuses_the_lazy_refresh_cooldown(self):
        # An unconditional reload per navigation re-paged the whole library and opened a
        # fresh stale-page window every time; showLibraryPage(), called on the very next
        # line by both callers, already refreshes on this cooldown.
        start = self.ui_source.index("function refreshAppSnapshotSilently()")
        body = self.ui_source[start:self.ui_source.index("\n    }", start)]
        self.assertIn(
            'shouldLazyRefresh("library", LIBRARY_LAZY_REFRESH_COOLDOWN_MS)', body
        )

    def test_every_offset_paged_movie_listing_has_a_total_order(self):
        # Title and year do not identify a row - a 4K and a Blu-ray of one film share
        # both - so without a unique tiebreaker LIMIT/OFFSET may serve a row on two
        # consecutive pages and another on neither.
        with open(NEXT_APP_PATH, encoding="utf-8") as handle:
            app_source = handle.read()
        for match in re.finditer(
            r"ORDER BY lower\(COALESCE\(m\.sort_title, m\.title\)\)[^\n]*\n\s*LIMIT %s OFFSET %s",
            app_source,
        ):
            with self.subTest(offset=match.start()):
                self.assertIn("m.id", match.group(0))

    def test_the_library_sort_index_migration_matches_the_order_by(self):
        with open(LIBRARY_SORT_MIGRATION_PATH, encoding="utf-8") as handle:
            sql = handle.read()
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_movies_library_sort_live", sql)
        self.assertIn("lower(COALESCE(sort_title, title))", sql)
        self.assertIn("WHERE deleted_at IS NULL", sql)
        # Comment lines out first: the header explains at length why CONCURRENTLY is
        # not used, and must be free to say the word without failing the check below.
        statement = "\n".join(
            line for line in sql.splitlines() if not line.strip().startswith("--")
        )
        self.assertIn("year", statement)
        self.assertIn("id", statement)
        # next_database.py applies every migration inside `with conn.transaction():`
        # and PostgreSQL refuses CREATE INDEX CONCURRENTLY in a transaction block.
        # Asserted against the statement rather than the file: the header explains at
        # length why CONCURRENTLY is not used, and must be free to say the word.
        self.assertNotIn("CONCURRENTLY", statement)


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
