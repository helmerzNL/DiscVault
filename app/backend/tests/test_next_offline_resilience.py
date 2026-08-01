"""Guards for the service worker defects that resurrected the 200-movie library.

A user with 228 movies saw 200 of them, then saw the app stop working entirely
after a few minutes — first in Firefox, later in Chrome. None of it came from the
movie cap, which had already been lifted; it came from this worker:

  * every same-origin ``/api/`` request was classified as data, including the SPA
    document and the frontend modules served under ``/api/next/app/js/``. On any
    fetch failure they were answered with ``apiFallback``'s JSON stub. Strict MIME
    checking rejects a JSON body for a ``<script src>``, so ``library-paging.js``
    never ran and the library stayed on its first page; a navigation rendered the
    JSON blob instead of the app;
  * the Cache Storage write sat inside the same ``try`` as the network fetch, so a
    ``QuotaExceededError`` discarded a perfectly good ``200 OK`` in favour of a
    stale entry or that stub. Quota filled because every ``/api/`` GET was cached
    forever, including each offset-keyed hydration page, alongside hundreds of
    opaque cross-origin poster responses — and Firefox's per-origin quota is the
    smaller one, which is why it broke first;
  * the cache namespace came from a hand-maintained constant unrelated to the
    release, so a fallback could serve an app shell precached by a much older
    worker — literally re-running the pre-fix bundle.

These are source-level assertions: the worker cannot be imported into the test
process, and the ordering and try/except placement are the whole fix.
"""

import os
import re
import sys
import unittest
from pathlib import Path


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO_APP_DIR = os.path.abspath(os.path.join(BACKEND_DIR, ".."))
SERVICE_WORKER_PATH = Path(REPO_APP_DIR) / "frontend" / "service-worker.js"

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import next_push  # noqa: E402


class ServiceWorkerRequestRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SERVICE_WORKER_PATH.read_text(encoding="utf-8")
        cls.fetch_handler = cls.source[cls.source.index('self.addEventListener("fetch"'):]

    def test_navigations_and_app_scripts_are_classified_before_the_api_branch(self):
        navigate = self.fetch_handler.index('request.mode === "navigate"')
        script = self.fetch_handler.index("isAppScriptPath(url.pathname)")
        api = self.fetch_handler.index('url.pathname.startsWith("/api/")')
        self.assertLess(navigate, api)
        self.assertLess(script, api)

    def test_app_scripts_use_a_script_specific_strategy(self):
        self.assertIn("async function scriptNetworkFirst(", self.source)
        start = self.source.index("async function scriptNetworkFirst(")
        end = self.source.index("async function appShellNetworkFirst(", start)
        body = self.source[start:end]
        self.assertIn("scriptFailureResponse()", body)
        self.assertNotIn("jsonResponse", body)
        self.assertNotIn("apiFallback", body)

    def test_scripts_and_documents_never_receive_a_json_stub(self):
        self.assertIn("function isSubstitutableRequest(request)", self.source)
        self.assertIn("function apiFallbackFor(request, url)", self.source)
        # handleApi's terminal fallback must go through the classifier.
        start = self.source.index("async function handleApi(")
        end = self.source.index("async function cacheFirst(", start)
        body = self.source[start:end]
        self.assertIn("return apiFallbackFor(request, url);", body)
        self.assertNotIn("return apiFallback(url);", body)

    def test_typed_failure_responses_carry_a_matching_content_type(self):
        script = self.source[
            self.source.index("function scriptFailureResponse()"):
            self.source.index("function documentFailureResponse()")
        ]
        self.assertIn('"Content-Type": "application/javascript"', script)
        document = self.source[
            self.source.index("function documentFailureResponse()"):
            self.source.index("function unsubstitutableFailureResponse(")
        ]
        self.assertIn('"Content-Type": "text/html; charset=utf-8"', document)


class ServiceWorkerCacheWriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SERVICE_WORKER_PATH.read_text(encoding="utf-8")

    def test_a_cache_write_cannot_fail_a_live_response(self):
        start = self.source.index("async function handleApi(")
        end = self.source.index("async function cacheFirst(", start)
        body = self.source[start:end]
        # The write is fire-and-forget and lives after the fetch try/catch, so a
        # QuotaExceededError can no longer be mistaken for the request failing.
        self.assertIn("cachePutBackground(request, networkResp, API_CACHE, event);", body)
        self.assertNotIn("await cachePut(", body)

    def test_quota_errors_are_absorbed_after_reclaiming_the_runtime_cache(self):
        start = self.source.index("async function cachePut(")
        end = self.source.index("function cachePutBackground(", start)
        body = self.source[start:end]
        self.assertIn("isQuotaError(error)", body)
        self.assertIn("caches.delete(RUNTIME_CACHE)", body)

    def test_paged_library_responses_are_never_cached(self):
        # Offset-keyed URLs make one cache entry per page, so the cache grows with the
        # library; replaying a stale page mid-hydration also corrupts the loaded list.
        self.assertIn('"/api/next/collection/movies"', self.source)
        self.assertIn("function isNonCacheableApiRequest(request)", self.source)
        start = self.source.index("async function cachePut(")
        end = self.source.index("function cachePutBackground(", start)
        self.assertIn(
            "if (isNonCacheableApiRequest(request)) return;",
            self.source[start:end],
        )

    def test_the_runtime_cache_is_bounded(self):
        self.assertIn("const RUNTIME_CACHE_MAX_ENTRIES =", self.source)
        self.assertIn("async function trimCache(cacheName, maxEntries)", self.source)
        self.assertIn("trimCache(RUNTIME_CACHE, RUNTIME_CACHE_MAX_ENTRIES)", self.source)

    def test_snapshot_poster_prefetch_is_capped(self):
        match = re.search(r"const SNAPSHOT_PREFETCH_MAX_URLS = (\d+);", self.source)
        assert match is not None
        self.assertLessEqual(int(match.group(1)), 120)
        self.assertIn("urls.size >= SNAPSHOT_PREFETCH_MAX_URLS", self.source)


class ServiceWorkerAppShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SERVICE_WORKER_PATH.read_text(encoding="utf-8")
        block = cls.source[cls.source.index("const APP_SHELL = ["):]
        cls.app_shell = re.findall(r'"([^"]+)"', block[: block.index("];")])

    def test_precached_paths_are_all_served_by_the_backend(self):
        # `cache.add` failures are swallowed during install, so an entry the backend
        # does not serve silently leaves the app shell half-precached.
        for path in ("/index.html", "/styles.css", "/js/app.js", "/i18n/translations.json"):
            with self.subTest(path=path):
                self.assertNotIn(path, self.app_shell)
        self.assertNotIn("/index.html", self.source)

    def test_the_paging_and_export_modules_are_precached(self):
        self.assertIn("/api/next/app/js/library-paging.js", self.app_shell)
        self.assertIn("/api/next/app/js/library-export.js", self.app_shell)

    def test_the_offline_shell_is_served_as_html(self):
        start = self.source.index("async function appShellFallback()")
        end = self.source.index('self.addEventListener("fetch"', start)
        self.assertIn('"Content-Type": "text/html; charset=utf-8"', self.source[start:end])


class ServiceWorkerVersioningTests(unittest.TestCase):
    def test_the_served_worker_carries_the_running_build(self):
        token = next_push.service_worker_build_token()
        self.assertTrue(token)
        source = next_push.service_worker_source(SERVICE_WORKER_PATH)
        self.assertNotIn(next_push.SW_BUILD_PLACEHOLDER, source)
        self.assertIn(token, source)

    def test_the_build_token_is_safe_for_a_cache_name(self):
        self.assertRegex(next_push.service_worker_build_token(), r"^[A-Za-z0-9._-]+$")

    def test_the_raw_file_still_defines_a_usable_version(self):
        # Served without substitution (dev server, raw file read) the worker must
        # still be functional rather than syntactically broken.
        source = SERVICE_WORKER_PATH.read_text(encoding="utf-8")
        self.assertIn(next_push.SW_BUILD_PLACEHOLDER, source)
        self.assertIn('? "dev"', source)

    def test_clients_are_told_when_a_new_build_took_over(self):
        source = SERVICE_WORKER_PATH.read_text(encoding="utf-8")
        self.assertIn('client.postMessage({ type: "sw-updated" })', source)
        ui = (Path(BACKEND_DIR) / "next_views_ui.py").read_text(encoding="utf-8")
        self.assertIn('event.data.type === "sw-updated"', ui)
        self.assertIn("showServiceWorkerUpdateBanner", ui)


if __name__ == "__main__":
    unittest.main()
