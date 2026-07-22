import json
import unittest
from unittest import mock

from app.backend.next_plugins.bluray_com import plugin


DETAIL_URL = "https://www.blu-ray.com/movies/Example-Film-Blu-ray/123/"


class FakeResponse:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code


class BlurayComPluginTests(unittest.TestCase):
    def setUp(self):
        plugin._last_request_at = None

    def test_search_barcode_returns_complete_release(self):
        search = FakeResponse(f"var urls = new Array('{DETAIL_URL}');")
        detail = FakeResponse(
            """
            <html>
              <head><meta property="og:title" content="Example Film Blu-ray (2024)" /></head>
              <body>
                <div id="shortaudio">English: Dolby TrueHD 7.1</div>
                <div id="shortsubs">English Dutch</div>
                <div id="shortvideo">1080p AVC</div>
              </body>
            </html>
            """
        )
        with (
            mock.patch.object(plugin.requests, "post", return_value=search, create=True) as post,
            mock.patch.object(plugin.requests, "get", return_value=detail, create=True) as get,
            mock.patch.object(plugin.time, "sleep"),
        ):
            result = plugin.search_barcode({"barcode": "4020628666873"})

        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["movie"]["title"], "Example Film")
        self.assertEqual(result["movie"]["year"], "2024")
        self.assertEqual(result["release"]["format"], "Blu-ray")
        self.assertIn("technicalSpecs", result)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(get.call_count, 1)
        serialized = json.dumps(result)
        self.assertNotIn("sourceUrl", serialized)
        self.assertNotIn("posterUrl", serialized)
        self.assertNotIn("https://www.blu-ray.com/", serialized)
        self.assertEqual(
            post.call_args.kwargs["headers"]["User-Agent"],
            "VaultStack-Metadata/1.0.6 (+https://github.com/helmerzNL/DiscVault)",
        )
        self.assertFalse(post.call_args.kwargs["allow_redirects"])

    def test_search_barcode_maps_timeout_to_stable_failure(self):
        with mock.patch.object(
            plugin.requests,
            "post",
            side_effect=plugin.RequestTimeout("timed out"),
            create=True,
        ):
            result = plugin.search_barcode({"barcode": "4020628666873"})

        self.assertEqual(
            result,
            {
                "status": "failed",
                "provider": "bluray_com",
                "errorCode": "provider_timeout",
            },
        )

    def test_search_barcode_maps_access_denial_to_stable_failure(self):
        search = FakeResponse(f"var urls = new Array('{DETAIL_URL}');")
        with (
            mock.patch.object(plugin.requests, "post", return_value=search, create=True),
            mock.patch.object(
                plugin.requests,
                "get",
                return_value=FakeResponse(status_code=403),
                create=True,
            ),
            mock.patch.object(plugin.time, "sleep"),
        ):
            result = plugin.search_barcode({"barcode": "4020628666873"})

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["errorCode"], "provider_access_denied")

    def test_search_barcode_maps_missing_release_title_to_parser_drift(self):
        search = FakeResponse(f"var urls = new Array('{DETAIL_URL}');")
        with (
            mock.patch.object(plugin.requests, "post", return_value=search, create=True),
            mock.patch.object(
                plugin.requests,
                "get",
                return_value=FakeResponse("<html></html>"),
                create=True,
            ),
            mock.patch.object(plugin.time, "sleep"),
        ):
            result = plugin.search_barcode({"barcode": "4020628666873"})

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["errorCode"], "provider_parser_drift")

    def test_request_rejects_non_source_urls_without_network_call(self):
        with mock.patch.object(plugin.requests, "get", create=True) as get:
            with self.assertRaisesRegex(plugin.ProviderError, "provider_url_rejected"):
                plugin._request("GET", "https://example.test/release")

        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
