import json
import os
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)


from app.backend.next_plugins.arrow import plugin as arrow_plugin
from app.backend.next_plugins.amazon import plugin as amazon_plugin
from app.backend.next_plugins.bol import plugin as bol_plugin
from app.backend.next_plugins.keepa import plugin as keepa_plugin
from app.backend.next_plugins.priceapi import plugin as priceapi_plugin
from app.backend.next_plugins.zavvi import plugin as zavvi_plugin
from app.backend.next_public_http import PublicHttpError


class _Response:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code
        self.content = text.encode("utf-8")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http error")


class TestZavviProviderPlugin(unittest.TestCase):
    def test_health_check_is_available(self):
        result = zavvi_plugin.health_check({})
        self.assertEqual(result.get("status"), "available")

    def test_price_check_uses_schema_org_price(self):
        html = (
            '<script type="application/ld+json">'
            '{"@type":"Product","offers":{"@type":"Offer","price":"21.99","priceCurrency":"GBP"}}'
            "</script>"
        )
        with patch("app.backend.next_plugins.zavvi.plugin.fetch_public_text", return_value=html):
            result = zavvi_plugin.price_check({"url": "https://www.zavvi.com/blu-ray/example/123"}, {})
        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("source"), "zavvi")
        self.assertAlmostEqual(result.get("price"), 21.99)
        self.assertEqual(result.get("currency"), "GBP")

    def test_price_check_uses_fallback_currency(self):
        html = '<div class="price">£14.95</div>'
        with patch("app.backend.next_plugins.zavvi.plugin.fetch_public_text", return_value=html):
            result = zavvi_plugin.price_check(
                {"providerProductRef": "https://www.zavvi.com/blu-ray/example/123"},
                {"settings": {"currency": "EUR"}},
            )
        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("currency"), "GBP")

    def test_public_http_failure_returns_stable_error(self):
        with patch(
            "app.backend.next_plugins.zavvi.plugin.fetch_public_text",
            side_effect=PublicHttpError("url_blocked"),
        ):
            result = zavvi_plugin.price_check(
                {"url": "https://www.zavvi.com/blu-ray/example/123"},
                {},
            )
        self.assertEqual(result, {"status": "error", "error": "url_blocked"})


class TestArrowProviderPlugin(unittest.TestCase):
    def test_health_check_is_available(self):
        result = arrow_plugin.health_check({})
        self.assertEqual(result.get("status"), "available")

    def test_price_check_uses_open_graph_price(self):
        html = (
            '<meta property="product:price:amount" content="29.50">'
            '<meta property="product:price:currency" content="USD">'
        )
        with patch("app.backend.next_plugins.arrow.plugin.fetch_public_text", return_value=html):
            result = arrow_plugin.price_check({"url": "https://www.arrowfilms.com/product/example"}, {})
        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("source"), "arrow")
        self.assertAlmostEqual(result.get("price"), 29.50)
        self.assertEqual(result.get("currency"), "USD")

    def test_price_check_rejects_non_arrow_urls(self):
        result = arrow_plugin.price_check({"url": "https://shop.example.com/item"}, {})
        self.assertEqual(result.get("status"), "no_match")
        self.assertIn("not an Arrow", result.get("error", ""))

    def test_price_check_rejects_spoofed_arrow_hostname(self):
        result = arrow_plugin.price_check(
            {"url": "https://example.com/product?brand=arrowfilms.com"},
            {},
        )
        self.assertEqual(result.get("status"), "no_match")


class TestBolProviderPlugin(unittest.TestCase):
    def test_health_check_is_available(self):
        result = bol_plugin.health_check({})
        self.assertEqual(result.get("status"), "available")

    def test_price_check_uses_schema_org_price(self):
        html = (
            '<script type="application/ld+json">'
            '{"@type":"Product","offers":{"@type":"Offer","price":"19.99","priceCurrency":"EUR"}}'
            "</script>"
        )
        with patch("app.backend.next_plugins.bol.plugin.fetch_public_text", return_value=html):
            result = bol_plugin.price_check({"url": "https://www.bol.com/nl/nl/p/example/9300000000000/"}, {})
        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("source"), "bol")
        self.assertAlmostEqual(result.get("price"), 19.99)
        self.assertEqual(result.get("currency"), "EUR")

    def test_price_check_rejects_non_bol_urls(self):
        result = bol_plugin.price_check({"url": "https://shop.example.com/item"}, {})
        self.assertEqual(result.get("status"), "no_match")
        self.assertIn("not a bol.com", result.get("error", ""))

    def test_price_check_rejects_spoofed_bol_hostname(self):
        result = bol_plugin.price_check(
            {"url": "https://bol.com.example.test/product"},
            {},
        )
        self.assertEqual(result.get("status"), "no_match")


class TestAmazonProviderPlugin(unittest.TestCase):
    def test_health_check_is_available(self):
        result = amazon_plugin.health_check({})
        self.assertEqual(result.get("status"), "available")

    def test_price_check_extracts_price_from_offscreen_markup(self):
        html = '<span class="a-offscreen">EUR 105.24</span>'
        with patch("app.backend.next_plugins.amazon.plugin.fetch_public_text", return_value=html):
            result = amazon_plugin.price_check({"url": "https://www.amazon.nl/dp/B09G9HD5XW"}, {})
        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("source"), "amazon")
        self.assertEqual(result.get("providerProductRef"), "B09G9HD5XW")
        self.assertAlmostEqual(result.get("price"), 105.24)
        self.assertEqual(result.get("currency"), "EUR")

    def test_price_check_rejects_non_amazon_urls(self):
        result = amazon_plugin.price_check({"url": "https://shop.example.com/item"}, {})
        self.assertEqual(result.get("status"), "no_match")
        self.assertIn("not an Amazon", result.get("error", ""))

    def test_price_check_uses_asin_provider_ref(self):
        html = '<span class="a-offscreen">$12.49</span>'
        with patch("app.backend.next_plugins.amazon.plugin.fetch_public_text", return_value=html):
            result = amazon_plugin.price_check({"providerProductRef": "B09G9HD5XW"}, {})
        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("providerProductRef"), "B09G9HD5XW")
        self.assertAlmostEqual(result.get("price"), 12.49)
        self.assertEqual(result.get("currency"), "USD")


class TestKeepaProviderPlugin(unittest.TestCase):
    def test_health_check_needs_configuration_without_key(self):
        result = keepa_plugin.health_check({})
        self.assertEqual(result.get("status"), "needs_configuration")

    def test_price_check_extracts_asin_from_provider_ref(self):
        result = keepa_plugin.price_check(
            {"providerProductRef": "b09g9hd5xw"},
            {"secrets": {"apiKey": "test_keepa"}},
        )
        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("providerProductRef"), "B09G9HD5XW")
        self.assertEqual(result.get("source"), "keepa")

    def test_price_check_extracts_asin_from_url(self):
        with patch(
            "app.backend.next_plugins.keepa.plugin.fetch_public_text",
            return_value=json.dumps({"products": [{"buyBoxPrice": 1234}]}),
        ) as fetch:
            result = keepa_plugin.price_check(
                {"url": "https://www.amazon.nl/dp/B09G9HD5XW?tag=test"},
                {"secrets": {"apiKey": "live_key"}, "settings": {"domainId": "4"}},
            )
        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("providerProductRef"), "B09G9HD5XW")
        self.assertAlmostEqual(result.get("price"), 12.34)
        endpoint = urlsplit(fetch.call_args.args[0])
        self.assertEqual(endpoint.hostname, "api.keepa.com")
        self.assertEqual(
            parse_qs(endpoint.query),
            {
                "key": ["live_key"],
                "domain": ["4"],
                "asin": ["B09G9HD5XW"],
                "buybox": ["1"],
                "history": ["0"],
                "stats": ["0"],
            },
        )
        self.assertEqual(fetch.call_args.kwargs["maximum_redirects"], 0)

    def test_public_http_failure_returns_stable_error(self):
        with patch(
            "app.backend.next_plugins.keepa.plugin.fetch_public_text",
            side_effect=PublicHttpError("url_blocked"),
        ):
            result = keepa_plugin.price_check(
                {"providerProductRef": "B09G9HD5XW"},
                {"secrets": {"apiKey": "live_key"}},
            )
        self.assertEqual(result, {"status": "error", "error": "url_blocked"})

    def test_test_mode_does_not_open_network(self):
        with patch("app.backend.next_plugins.keepa.plugin.fetch_public_text") as fetch:
            result = keepa_plugin.price_check(
                {"providerProductRef": "B09G9HD5XW"},
                {"secrets": {"apiKey": "test_keepa"}},
            )
        self.assertEqual(result.get("status"), "ok")
        fetch.assert_not_called()


class TestPriceApiProviderPlugin(unittest.TestCase):
    def test_health_check_needs_configuration_without_key(self):
        result = priceapi_plugin.health_check({})
        self.assertEqual(result.get("status"), "needs_configuration")

    def test_price_check_posts_existing_payload_to_public_custom_endpoint(self):
        endpoint = "https://prices.example/v2/jobs"
        with patch(
            "app.backend.next_plugins.priceapi.plugin.request_public_text",
            return_value=json.dumps({"price": "24,99", "currency": "eur"}),
        ) as request:
            result = priceapi_plugin.price_check(
                {
                    "providerProductRef": "sku-123",
                    "url": "https://shop.example/product",
                },
                {
                    "secrets": {"apiKey": "live_key"},
                    "settings": {"endpoint": endpoint, "country": "de"},
                },
            )

        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("price"), 24.99)
        self.assertEqual(result.get("currency"), "EUR")
        self.assertEqual(request.call_args.args[0], endpoint)
        self.assertEqual(request.call_args.kwargs["method"], "POST")
        self.assertEqual(request.call_args.kwargs["maximum_redirects"], 0)
        self.assertEqual(
            json.loads(request.call_args.kwargs["body"]),
            {
                "source": "discvault",
                "country": "de",
                "product_ref": "sku-123",
                "url": "https://shop.example/product",
                "include_history": False,
            },
        )
        self.assertEqual(
            request.call_args.kwargs["headers"]["Content-Type"],
            "application/json",
        )

    def test_public_http_failure_returns_stable_error(self):
        with patch(
            "app.backend.next_plugins.priceapi.plugin.request_public_text",
            side_effect=PublicHttpError("url_blocked"),
        ):
            result = priceapi_plugin.price_check(
                {"providerProductRef": "sku-123"},
                {
                    "secrets": {"apiKey": "live_key"},
                    "settings": {"endpoint": "http://127.0.0.1/jobs"},
                },
            )
        self.assertEqual(result, {"status": "error", "error": "url_blocked"})

    def test_test_mode_does_not_open_network(self):
        with patch("app.backend.next_plugins.priceapi.plugin.request_public_text") as request:
            result = priceapi_plugin.price_check(
                {"providerProductRef": "sku-123"},
                {"secrets": {"apiKey": "test_priceapi"}},
            )
        self.assertEqual(result.get("status"), "ok")
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
