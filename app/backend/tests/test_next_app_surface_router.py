import os
import re
import unittest


NEXT_VIEWS_UI_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "next_views_ui.py",
    )
)

EXPECTED_SURFACE_IDS = [
    "libraryView",
    "listsView",
    "peopleView",
    "statisticsView",
    "notificationsView",
    "importView",
    "discoverView",
    "profileView",
    "adminView",
    "movieDetailPage",
    "containerDetailPage",
    "personDetailPage",
    "locationDetailPage",
    "discoverDetailPage",
]

EXPECTED_HANDLER_SURFACES = {
    "showDiscoverPage": "discoverView",
    "showDiscoverDetailPage": "discoverDetailPage",
    "showMovieDetailPage": "movieDetailPage",
    "showContainerDetailPage": "containerDetailPage",
    "showPersonDetailPage": "personDetailPage",
    "showLocationDetailPage": "locationDetailPage",
    "showLibraryPage": "libraryView",
    "showPeoplePage": "peopleView",
    "showListsPage": "listsView",
    "showStatisticsPage": "statisticsView",
    "showNotificationsPage": "notificationsView",
    "showProfilePage": "profileView",
    "showAdminPage": "adminView",
    "showImportPage": "importView",
}


def _function_body(source, start):
    """Return the body of the function whose signature starts at ``start``."""
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1:index]
    raise AssertionError("unbalanced braces while parsing route handler")


class NextAppSurfaceRouterTests(unittest.TestCase):
    """Guards against route handlers leaving another view rendered underneath.

    Every ``show*Page`` handler must delegate to ``showAppSurface`` so exactly one
    app surface stays visible. Hand-rolled hide lists silently drift whenever a new
    view is added, which is how the Discover view ended up rendering below the
    Import Center page.
    """

    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()
        cls.handlers = {
            match.group(1): _function_body(cls.source, match.end())
            for match in re.finditer(r"\n    (?:async )?function (show\w+Page)\(", cls.source)
        }

    def test_surface_helper_exists(self):
        self.assertIn("function showAppSurface(activeId) {", self.source)
        self.assertIn(
            'document.getElementById(id)?.classList.toggle("hidden", id !== activeId);',
            self.source,
        )

    def test_surface_id_list_is_complete(self):
        block = re.search(
            r"const APP_VIEW_SURFACE_IDS = \[(.*?)\];",
            self.source,
            re.S,
        )
        self.assertIsNotNone(block, "APP_VIEW_SURFACE_IDS declaration is missing")
        ids = re.findall(r'"(\w+)"', block.group(1))
        self.assertEqual(sorted(ids), sorted(EXPECTED_SURFACE_IDS))
        self.assertEqual(len(ids), len(set(ids)), "duplicate surface ids")

    def test_every_route_handler_uses_the_surface_helper(self):
        self.assertEqual(sorted(self.handlers), sorted(EXPECTED_HANDLER_SURFACES))
        for name, expected in EXPECTED_HANDLER_SURFACES.items():
            body = self.handlers[name]
            calls = re.findall(r'showAppSurface\("(\w+)"\)', body)
            self.assertEqual(calls, [expected], f"{name} must call showAppSurface once")

    def test_route_handlers_do_not_hand_roll_hide_lists(self):
        pattern = re.compile(
            r'getElementById\("(\w+)"\)\?\.classList\.(?:add|remove)\("hidden"\)'
        )
        for name, body in self.handlers.items():
            leaked = [
                surface
                for surface in pattern.findall(body)
                if surface in EXPECTED_SURFACE_IDS
            ]
            self.assertEqual(
                leaked,
                [],
                f"{name} toggles app surfaces directly instead of using showAppSurface",
            )


class NextDiscoverNavGatingTests(unittest.TestCase):
    """Discover is a TMDb surface; with the plugin off it cannot render anything.

    Offering the nav entry anyway is a promise the app cannot keep — the page's
    only possible content is its "not configured" panel.
    """

    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()
        cls.handlers = {
            match.group(1): _function_body(cls.source, match.end())
            for match in re.finditer(r"\n    (?:async )?function (\w+)\(", cls.source)
        }

    def test_nav_entry_is_hidden_when_the_tmdb_plugin_is_disabled(self):
        # One selector, because the sidebar item and the mobile tab both carry
        # data-app-route="discover".
        self.assertIn(
            "setVisible('[data-app-route=\"discover\"]', tmdbPluginEnabled());",
            self.source,
        )
        # Must sit in the function that re-runs on every snapshot and auth change.
        self.assertIn(
            "setVisible('[data-app-route=\"discover\"]', tmdbPluginEnabled());",
            self.handlers["applyAppPermissionVisibility"],
        )

    def test_unloaded_snapshot_counts_as_unknown_and_stays_visible(self):
        """An empty plugin list means "not loaded yet", not "TMDb is off".

        The unauthenticated snapshot ships `plugins: []`, so a bare
        `=== true` check would blink the tab away from someone entitled to it.
        """
        body = self.handlers["tmdbPluginEnabled"]
        self.assertIn("if (!plugins.length) return true;", body)
        self.assertIn('plugin.id === "tmdb"', body)
        self.assertIn("?.enabled === true", body)

    def test_discover_routes_bail_out_when_the_plugin_is_disabled(self):
        """Hiding the nav is not enough — /discover survives as a URL.

        Typed addresses, bookmarks and the back button all still reach it, and
        landing on a dead surface is worse than never offering it.
        """
        for handler in ("showDiscoverPage", "openDiscoverDetail"):
            body = self.handlers[handler]
            self.assertIn("if (!tmdbPluginEnabled()) {", body, handler)
            self.assertIn("showLibraryPage(pushUrl);", body, handler)

    def test_person_page_reuses_the_shared_helper(self):
        """The lazy-refresh gate asked the same question inline; one source now."""
        self.assertNotIn(
            'const tmdbEnabled = state.plugins?.find((plugin) => plugin.id === "tmdb")?.enabled === true;',
            self.source,
        )
        self.assertIn(
            'hasPermission("metadata.refresh_one") && tmdbPluginEnabled()',
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
