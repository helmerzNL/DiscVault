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
    from app.backend.next_tmdb_discover import discover_detail, discover_feed, normalize_locale
except ModuleNotFoundError as exc:  # Local minimal test environments may omit optional backend deps.
    if exc.name not in {"flask", "psycopg"}:
        raise
    Flask = None
    NextApiError = None
    register_next_discover_routes = None
    discover_detail = None
    discover_feed = None
    normalize_locale = None


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


if __name__ == "__main__":
    unittest.main()
