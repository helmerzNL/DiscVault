import json
import os
import sys
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO_APP_DIR = os.path.abspath(os.path.join(BACKEND_DIR, ".."))
NEXT_VIEWS_UI_PATH = os.path.join(BACKEND_DIR, "next_views_ui.py")
NEXT_APP_PATH = os.path.join(BACKEND_DIR, "next_app.py")
LIBRARY_PAGING_JS_PATH = os.path.join(REPO_APP_DIR, "frontend", "js", "library-paging.js")
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

    def test_paging_i18n_keys_exist_in_every_locale(self):
        locales = sorted(name for name in os.listdir(I18N_DIR) if name.endswith(".json"))
        self.assertTrue(locales)
        for name in locales:
            with open(os.path.join(I18N_DIR, name), encoding="utf-8") as handle:
                data = json.load(handle)
            with self.subTest(locale=name):
                self.assertIn("collection.loadingMoreRows", data)
                self.assertIn("collection.hydrationFailed", data)
                self.assertTrue(data["collection.loadingMoreRows"].strip())
                self.assertTrue(data["collection.hydrationFailed"].strip())


if __name__ == "__main__":
    unittest.main()
