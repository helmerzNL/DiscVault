import os
import sys
import unittest
from unittest.mock import patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)


from app.backend.next_plugins.arrow import plugin as arrow_plugin
from app.backend.next_plugins.zavvi import plugin as zavvi_plugin


class _Response:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

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
        with patch("app.backend.next_plugins.zavvi.plugin.requests.get", return_value=_Response(html)):
            result = zavvi_plugin.price_check({"url": "https://www.zavvi.com/blu-ray/example/123"}, {})
        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("source"), "zavvi")
        self.assertAlmostEqual(result.get("price"), 21.99)
        self.assertEqual(result.get("currency"), "GBP")

    def test_price_check_uses_fallback_currency(self):
        html = '<div class="price">£14.95</div>'
        with patch("app.backend.next_plugins.zavvi.plugin.requests.get", return_value=_Response(html)):
            result = zavvi_plugin.price_check(
                {"providerProductRef": "https://www.zavvi.com/blu-ray/example/123"},
                {"settings": {"currency": "EUR"}},
            )
        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("currency"), "GBP")


class TestArrowProviderPlugin(unittest.TestCase):
    def test_health_check_is_available(self):
        result = arrow_plugin.health_check({})
        self.assertEqual(result.get("status"), "available")

    def test_price_check_uses_open_graph_price(self):
        html = (
            '<meta property="product:price:amount" content="29.50">'
            '<meta property="product:price:currency" content="USD">'
        )
        with patch("app.backend.next_plugins.arrow.plugin.requests.get", return_value=_Response(html)):
            result = arrow_plugin.price_check({"url": "https://www.arrowfilms.com/product/example"}, {})
        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("source"), "arrow")
        self.assertAlmostEqual(result.get("price"), 29.50)
        self.assertEqual(result.get("currency"), "USD")

    def test_price_check_rejects_non_arrow_urls(self):
        result = arrow_plugin.price_check({"url": "https://shop.example.com/item"}, {})
        self.assertEqual(result.get("status"), "no_match")
        self.assertIn("not an Arrow", result.get("error", ""))


if __name__ == "__main__":
    unittest.main()

