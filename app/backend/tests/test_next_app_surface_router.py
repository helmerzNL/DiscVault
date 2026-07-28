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


if __name__ == "__main__":
    unittest.main()
