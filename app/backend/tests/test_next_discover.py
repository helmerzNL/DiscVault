import os
import sys
import unittest
from unittest import mock


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from flask import Flask

    from app.backend.next_common import NextApiError
    from app.backend.next_discover import register_next_discover_routes
    from app.backend.next_tmdb_discover import (
        DISCOVER_PILL_MODES,
        discover_detail,
        discover_feed,
        normalize_discover_mode,
        normalize_locale,
    )
    from app.backend.next_views_ui import ui_preview_html
except ModuleNotFoundError as exc:  # Local minimal test environments may omit optional backend deps.
    if exc.name not in {"flask", "psycopg"}:
        raise
    Flask = None
    NextApiError = None
    register_next_discover_routes = None
    DISCOVER_PILL_MODES = None
    discover_detail = None
    discover_feed = None
    normalize_discover_mode = None
    normalize_locale = None
    ui_preview_html = None


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@unittest.skipIf(register_next_discover_routes is None, "Flask is not installed in this test environment")
class NextDiscoverRouteTests(unittest.TestCase):
    def _build_client(self):
        app = Flask(__name__)
        register_next_discover_routes(app, connect=lambda: _FakeConn())
        return app.test_client()

    def test_feed_returns_unconfigured_payload_when_tmdb_unavailable(self):
        client = self._build_client()
        with (
            mock.patch("app.backend.next_discover._require_discover_actor", return_value={"id": "actor-1"}),
            mock.patch(
                "app.backend.next_discover.tmdb_discover_context",
                return_value={"configured": False, "message": "TMDb API key is missing."},
            ),
        ):
            response = client.get("/api/next/discover")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["configured"])
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["message"], "TMDb API key is missing.")

    def test_detail_returns_unconfigured_payload_when_tmdb_unavailable(self):
        client = self._build_client()
        with (
            mock.patch("app.backend.next_discover._require_discover_actor", return_value={"id": "actor-1"}),
            mock.patch(
                "app.backend.next_discover.tmdb_discover_context",
                return_value={"configured": False, "message": "TMDb API key is missing."},
            ),
        ):
            response = client.get("/api/next/discover/movie/123")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["configured"])
        self.assertIsNone(payload["detail"])
        self.assertEqual(payload["message"], "TMDb API key is missing.")


@unittest.skipIf(discover_feed is None, "Flask is not installed in this test environment")
class NextDiscoverLogicTests(unittest.TestCase):
    def test_happy_path_feed_pagination_maps_results(self):
        with mock.patch(
            "app.backend.next_tmdb_discover.tmdb_plugin._request",
            return_value={
                "results": [{"id": 11, "title": "Movie One", "release_date": "2024-01-01"}],
                "total_pages": 3,
            },
        ) as request_mock:
            payload = discover_feed({"settings": {"language": "nl_nl"}}, media_type="movie", mode="popular", page=2)
        self.assertEqual(payload["page"], 2)
        self.assertEqual(payload["totalPages"], 3)
        self.assertTrue(payload["hasMore"])
        self.assertEqual(payload["items"][0]["tmdbId"], "11")
        self.assertEqual(payload["items"][0]["title"], "Movie One")
        self.assertEqual(request_mock.call_args.kwargs["language"], "nl-NL")

    def test_invalid_media_type_and_id_raise_400(self):
        with self.assertRaises(NextApiError) as media_error:
            discover_feed({"settings": {"language": "en-US"}}, media_type="person", mode="popular", page=1)
        self.assertEqual(media_error.exception.status_code, 400)

        with self.assertRaises(NextApiError) as id_error:
            discover_detail({"settings": {"language": "en-US"}}, media_type="movie", tmdb_id="abc")
        self.assertEqual(id_error.exception.status_code, 400)

    def test_locale_normalization_uses_default_and_region_format(self):
        self.assertEqual(normalize_locale(None), "en-US")
        self.assertEqual(normalize_locale(""), "en-US")
        self.assertEqual(normalize_locale("nl_nl"), "nl-NL")
        self.assertEqual(normalize_locale("fr"), "fr")

    def test_discover_mode_normalization_accepts_aliases_and_falls_back(self):
        self.assertEqual(normalize_discover_mode(None), "popular")
        self.assertEqual(normalize_discover_mode("bogus"), "popular")
        self.assertEqual(normalize_discover_mode("Now Playing"), "now_playing")
        self.assertEqual(normalize_discover_mode("nowplaying"), "now_playing")
        self.assertEqual(normalize_discover_mode("top-rated"), "top_rated")
        self.assertEqual(normalize_discover_mode("soon"), "upcoming")

    def test_pill_modes_hit_their_dedicated_tmdb_endpoints(self):
        expected = {
            "popular": "/discover/movie",
            "now_playing": "/movie/now_playing",
            "upcoming": "/movie/upcoming",
            "top_rated": "/movie/top_rated",
        }
        self.assertEqual(set(DISCOVER_PILL_MODES), set(expected))
        for mode, path in expected.items():
            with self.subTest(mode=mode):
                with mock.patch(
                    "app.backend.next_tmdb_discover.tmdb_plugin._request",
                    return_value={"results": [], "total_pages": 1},
                ) as request_mock:
                    payload = discover_feed(
                        {"settings": {"language": "en-US"}},
                        media_type="movie",
                        mode=mode,
                        page=1,
                    )
                self.assertEqual(payload["mode"], mode)
                self.assertEqual(request_mock.call_args.args[1], path)


@unittest.skipIf(ui_preview_html is None, "backend UI module unavailable")
class DiscoverWishlistBadgeUiTests(unittest.TestCase):
    """Guard the Discover wishlist affordances that mirror the iOS/Android apps."""

    @classmethod
    def setUpClass(cls):
        cls.html = ui_preview_html(None, app_mode=True)

    def test_wish_badge_is_styled_as_a_red_top_right_overlay(self):
        self.assertIn(".discover-wish-badge {", self.html)
        badge_css = self.html.split(".discover-wish-badge {", 1)[1].split("}", 1)[0]
        self.assertIn("position: absolute", badge_css)
        self.assertIn("top: 6px", badge_css)
        self.assertIn("right: 6px", badge_css)
        self.assertIn("pointer-events: none", badge_css)
        self.assertIn(".discover-wish-badge svg {", self.html)
        svg_css = self.html.split(".discover-wish-badge svg {", 1)[1].split("}", 1)[0]
        self.assertIn("#ef4444", svg_css)

    def test_discover_cards_render_the_badge_only_for_wishlisted_items(self):
        self.assertIn('class="discover-wish-badge"', self.html)
        self.assertIn('const onWishlist = isDiscoverItemOnWishlist(item);', self.html)
        self.assertIn('${onWishlist ? " on-wishlist" : ""}', self.html)

    def test_wishlist_matching_falls_back_to_title_and_year(self):
        for symbol in (
            "function normalizeDiscoverTitleKey(",
            "function normalizeDiscoverYear(",
            "function discoverMatchesWishlistTitleYear(",
            "function findDiscoverWishlistEntry(",
        ):
            self.assertIn(symbol, self.html)

    def test_discover_preloads_wishlist_state(self):
        self.assertIn("async function ensureDiscoverWishlistLoaded()", self.html)
        self.assertIn("ensureDiscoverWishlistLoaded();", self.html)
        self.assertIn("function mergeWishlistEntryIntoState(", self.html)

    def test_duplicate_wishlist_adds_are_blocked_in_the_ui(self):
        self.assertIn("disabled: alreadyOnWishlist", self.html)
        self.assertIn(".lists-actionsheet-btn:disabled {", self.html)


if __name__ == "__main__":
    unittest.main()
