"""Tests for next_price_alerts.py – price extraction and alert evaluation logic."""

import os
import sys
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_price_alerts import (
        PRICE_ALERT_JOB_TYPE,
        _coerce_price,
        _extract_from_og_meta,
        _extract_from_schema_org,
        _extract_via_regex,
        evaluate_price_alert,
        extract_price_from_url,
    )
    _MODULE_AVAILABLE = True
except ModuleNotFoundError:
    _MODULE_AVAILABLE = False


@unittest.skipUnless(_MODULE_AVAILABLE, "next_price_alerts not importable in this environment")
class TestCoercePrice(unittest.TestCase):
    def test_float_passthrough(self):
        self.assertEqual(_coerce_price(9.99), 9.99)

    def test_string_integer(self):
        self.assertEqual(_coerce_price("10"), 10.0)

    def test_string_decimal_dot(self):
        self.assertAlmostEqual(_coerce_price("29.99"), 29.99)

    def test_string_decimal_comma(self):
        self.assertAlmostEqual(_coerce_price("29,99"), 29.99)

    def test_string_with_currency_symbol(self):
        self.assertAlmostEqual(_coerce_price("€ 19,95"), 19.95)

    def test_none_returns_none(self):
        self.assertIsNone(_coerce_price(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_coerce_price(""))

    def test_non_numeric_returns_none(self):
        self.assertIsNone(_coerce_price("free"))


@unittest.skipUnless(_MODULE_AVAILABLE, "next_price_alerts not importable in this environment")
class TestSchemaOrgExtraction(unittest.TestCase):
    def _html(self, json_content: str) -> str:
        return f'<script type="application/ld+json">{json_content}</script>'

    def test_extracts_price_and_currency(self):
        html = self._html(
            '{"@type":"Product","offers":{"@type":"Offer","price":"19.99","priceCurrency":"EUR"}}'
        )
        price, currency = _extract_from_schema_org(html)
        self.assertAlmostEqual(price, 19.99)
        self.assertEqual(currency, "EUR")

    def test_returns_none_when_no_schema_org(self):
        price, currency = _extract_from_schema_org("<html><body>nothing</body></html>")
        self.assertIsNone(price)

    def test_handles_list_of_offers(self):
        html = self._html(
            '{"@type":"Product","offers":[{"@type":"Offer","price":"5.00","priceCurrency":"USD"}]}'
        )
        price, currency = _extract_from_schema_org(html)
        self.assertAlmostEqual(price, 5.0)
        self.assertEqual(currency, "USD")


@unittest.skipUnless(_MODULE_AVAILABLE, "next_price_alerts not importable in this environment")
class TestOgMetaExtraction(unittest.TestCase):
    def test_extracts_price_and_currency(self):
        html = (
            '<meta property="product:price:amount" content="12.50">'
            '<meta property="product:price:currency" content="GBP">'
        )
        price, currency = _extract_from_og_meta(html)
        self.assertAlmostEqual(price, 12.50)
        self.assertEqual(currency, "GBP")

    def test_reversed_attribute_order(self):
        html = (
            '<meta content="8.99" property="product:price:amount">'
            '<meta content="EUR" property="product:price:currency">'
        )
        price, currency = _extract_from_og_meta(html)
        self.assertAlmostEqual(price, 8.99)

    def test_no_meta_returns_none(self):
        price, currency = _extract_from_og_meta("<html></html>")
        self.assertIsNone(price)


@unittest.skipUnless(_MODULE_AVAILABLE, "next_price_alerts not importable in this environment")
class TestRegexExtraction(unittest.TestCase):
    def test_euro_symbol(self):
        html = '<span class="price">€ 24,99</span>'
        price, currency = _extract_via_regex(html)
        self.assertAlmostEqual(price, 24.99)

    def test_dollar_symbol(self):
        html = '<span class="itemprice">$9.99</span>'
        price, currency = _extract_via_regex(html)
        self.assertAlmostEqual(price, 9.99)


@unittest.skipUnless(_MODULE_AVAILABLE, "next_price_alerts not importable in this environment")
class TestExtractPriceFromUrl(unittest.TestCase):
    def test_uses_schema_org_first(self):
        schema_html = (
            '<script type="application/ld+json">'
            '{"@type":"Product","offers":{"price":"14.99","priceCurrency":"EUR"}}'
            '</script>'
        )
        with patch("app.backend.next_price_alerts._fetch_html", return_value=schema_html):
            price, currency = extract_price_from_url("https://example.com/product")
        self.assertAlmostEqual(price, 14.99)
        self.assertEqual(currency, "EUR")

    def test_falls_back_to_og_when_no_schema_org(self):
        og_html = (
            '<meta property="product:price:amount" content="7.50">'
            '<meta property="product:price:currency" content="USD">'
        )
        with patch("app.backend.next_price_alerts._fetch_html", return_value=og_html):
            price, currency = extract_price_from_url("https://example.com/product")
        self.assertAlmostEqual(price, 7.50)
        self.assertEqual(currency, "USD")

    def test_returns_none_on_fetch_failure(self):
        with patch("app.backend.next_price_alerts._fetch_html", side_effect=ValueError("timeout")):
            price, currency = extract_price_from_url("https://example.com/product")
        self.assertIsNone(price)
        self.assertIsNone(currency)


@unittest.skipUnless(_MODULE_AVAILABLE, "next_price_alerts not importable in this environment")
class TestEvaluatePriceAlert(unittest.TestCase):
    def _make_item(self, **overrides):
        base = {
            "id": "00000000-0000-0000-0000-000000000001",
            "user_id": "00000000-0000-0000-0000-000000000002",
            "title": "Test Movie",
            "alert_enabled": True,
            "target_price": 15.00,
            "price_currency": "EUR",
            "last_seen_price": None,
            "last_alerted_at": None,
        }
        base.update(overrides)
        return base

    def _make_conn(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value = cur
        conn.transaction.return_value.__enter__ = lambda s: None
        conn.transaction.return_value.__exit__ = MagicMock(return_value=False)
        return conn, cur

    def test_fires_notification_when_price_at_target(self):
        conn, cur = self._make_conn()
        item = self._make_item()
        with patch("app.backend.next_price_alerts.create_user_notification") as mock_notify:
            evaluate_price_alert(conn, item, new_price=14.99, currency="EUR")
        mock_notify.assert_called_once()

    def test_no_notification_when_price_above_target(self):
        conn, cur = self._make_conn()
        item = self._make_item()
        with patch("app.backend.next_price_alerts.create_user_notification") as mock_notify:
            evaluate_price_alert(conn, item, new_price=20.00, currency="EUR")
        mock_notify.assert_not_called()

    def test_no_notification_when_alert_disabled(self):
        conn, cur = self._make_conn()
        item = self._make_item(alert_enabled=False)
        with patch("app.backend.next_price_alerts.create_user_notification") as mock_notify:
            evaluate_price_alert(conn, item, new_price=10.00, currency="EUR")
        mock_notify.assert_not_called()


@unittest.skipUnless(_MODULE_AVAILABLE, "next_price_alerts not importable in this environment")
class TestJobTypeConstant(unittest.TestCase):
    def test_job_type_value(self):
        self.assertEqual(PRICE_ALERT_JOB_TYPE, "price_alert.sweep")


if __name__ == "__main__":
    unittest.main()
