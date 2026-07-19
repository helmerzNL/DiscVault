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
    from app.backend import next_price_alerts as price_alerts_module
    from app.backend import next_public_http
    from app.backend.next_price_alerts import (
        PRICE_ALERT_JOB_TYPE,
        _coerce_price,
        _extract_from_og_meta,
        _extract_price_from_profile,
        _extract_from_schema_org,
        _extract_via_regex,
        _extract_amazon_asin,
        _is_amazon_bot_blocked,
        _extract_price_from_amazon_html,
        _run_price_provider_check,
        evaluate_price_alert,
        extract_price_from_url,
        extract_price_from_url_with_source,
        run_price_alert_sweep,
    )
    from app.backend.next_public_http import PublicHttpError
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
class TestSelectorExtraction(unittest.TestCase):
    def test_css_text_selector_extracts_price(self):
        html = '<div id="price-now">€ 12,99</div>'
        price, currency = _extract_price_from_profile(
            html,
            selector_type="css_text",
            selector_value="#price-now",
            fallback_currency="EUR",
        )
        self.assertAlmostEqual(price, 12.99)
        self.assertEqual(currency, "EUR")

    def test_regex_capture_selector_extracts_price(self):
        html = '<span data-price="29.50 USD">Now</span>'
        price, currency = _extract_price_from_profile(
            html,
            selector_type="regex_capture",
            selector_value=r'data-price="([0-9.,]+)\s*USD"',
            fallback_currency="USD",
        )
        self.assertAlmostEqual(price, 29.50)
        self.assertEqual(currency, "USD")


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

    def test_returns_stable_source_on_public_http_failure(self):
        with patch(
            "app.backend.next_price_alerts._fetch_html",
            side_effect=PublicHttpError("url_blocked"),
        ):
            price, currency, source = extract_price_from_url_with_source(
                "http://127.0.0.1/private"
            )
        self.assertIsNone(price)
        self.assertIsNone(currency)
        self.assertEqual(source, "url_blocked")

    def test_uses_amazon_preset_before_generic_extractors(self):
        html = '<span class="a-offscreen">€ 18,49</span>'
        with patch("app.backend.next_price_alerts._fetch_html", return_value=html):
            price, currency, source = extract_price_from_url_with_source("https://www.amazon.de/dp/abc")
        self.assertAlmostEqual(price, 18.49)
        self.assertEqual(source, "preset_amazon")

    def test_uses_selector_profile_before_schema_org(self):
        html = (
            '<div class="price-now">€ 11,11</div>'
            '<script type="application/ld+json">'
            '{"@type":"Product","offers":{"price":"44.00","priceCurrency":"EUR"}}'
            "</script>"
        )
        with patch("app.backend.next_price_alerts._fetch_html", return_value=html):
            price, currency, source = extract_price_from_url_with_source(
                "https://shop.example.com/product",
                selector_type="css_text",
                selector_value=".price-now",
                selector_options={"currency": "EUR"},
            )
        self.assertAlmostEqual(price, 11.11)
        self.assertEqual(currency, "EUR")
        self.assertEqual(source, "profile")


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


@unittest.skipUnless(_MODULE_AVAILABLE, "next_price_alerts not importable in this environment")
class TestAmazonAsinExtraction(unittest.TestCase):
    def test_dp_url(self):
        self.assertEqual(
            _extract_amazon_asin("https://www.amazon.nl/dp/B09G9HD5XW"),
            "B09G9HD5XW",
        )

    def test_gp_product_url(self):
        self.assertEqual(
            _extract_amazon_asin("https://www.amazon.com/gp/product/B09G9HD5XW/ref=sr_1_1"),
            "B09G9HD5XW",
        )

    def test_query_param(self):
        self.assertEqual(
            _extract_amazon_asin("https://www.amazon.de/s?asin=B09G9HD5XW"),
            "B09G9HD5XW",
        )

    def test_non_amazon_returns_none(self):
        self.assertIsNone(_extract_amazon_asin("https://www.bol.com/nl/p/12345"))

    def test_url_without_asin_returns_none(self):
        self.assertIsNone(_extract_amazon_asin("https://www.amazon.nl/"))


@unittest.skipUnless(_MODULE_AVAILABLE, "next_price_alerts not importable in this environment")
class TestAmazonBotDetection(unittest.TestCase):
    def test_detects_robot_check_title(self):
        html = "<html><head><title>Robot Check</title></head></html>"
        self.assertTrue(_is_amazon_bot_blocked(html))

    def test_detects_captcha_form(self):
        html = '<input id="captchacharacters" type="text">'
        self.assertTrue(_is_amazon_bot_blocked(html))

    def test_clean_page_not_blocked(self):
        html = "<html><body><div class='a-offscreen'>€ 19,99</div></body></html>"
        self.assertFalse(_is_amazon_bot_blocked(html))


@unittest.skipUnless(_MODULE_AVAILABLE, "next_price_alerts not importable in this environment")
class TestAmazonHtmlExtraction(unittest.TestCase):
    def test_offscreen_price(self):
        html = (
            '<span class="a-price"><span class="a-offscreen">€ 24,99</span>'
            '<span aria-hidden="true">€24,99</span></span>'
        )
        price, currency = _extract_price_from_amazon_html(html)
        self.assertAlmostEqual(price, 24.99)
        self.assertEqual(currency, "EUR")

    def test_priceblock_ourprice(self):
        html = '<span id="priceblock_ourprice">$12.99</span>'
        price, currency = _extract_price_from_amazon_html(html)
        self.assertAlmostEqual(price, 12.99)
        self.assertEqual(currency, "USD")

    def test_whole_fraction_pattern(self):
        html = (
            '<span class="a-price-whole">29</span>'
            '<span class="a-price-fraction">99</span>'
        )
        price, _currency = _extract_price_from_amazon_html(html)
        self.assertAlmostEqual(price, 29.99)

    def test_schema_org_fallback(self):
        html = (
            '<script type="application/ld+json">'
            '{"@type":"Product","offers":{"price":"19.99","priceCurrency":"EUR"}}'
            "</script>"
        )
        price, currency = _extract_price_from_amazon_html(html)
        self.assertAlmostEqual(price, 19.99)
        self.assertEqual(currency, "EUR")

    def test_returns_none_when_empty(self):
        price, currency = _extract_price_from_amazon_html("<html></html>")
        self.assertIsNone(price)

    def test_blocked_page_returns_none(self):
        html = "<html><title>Robot Check</title></html>"
        # _extract_price_from_amazon_html itself doesn't check for blocks —
        # the caller (_extract_price_from_domain_preset) does.  Verify that
        # the extraction still returns None since no price markup exists.
        price, _currency = _extract_price_from_amazon_html(html)
        self.assertIsNone(price)


@unittest.skipUnless(_MODULE_AVAILABLE, "next_price_alerts not importable in this environment")
class TestAmazonBlockedSignal(unittest.TestCase):
    """extract_price_from_url_with_source returns blocked_amazon source."""

    def _captcha_html(self) -> str:
        return "<html><title>Robot Check</title><input id='captchacharacters'></html>"

    def test_blocked_returns_blocked_amazon_source(self):
        with patch(
            "app.backend.next_price_alerts._fetch_html",
            return_value=self._captcha_html(),
        ):
            price, currency, source = extract_price_from_url_with_source(
                "https://www.amazon.nl/dp/B09G9HD5XW"
            )
        self.assertIsNone(price)
        self.assertEqual(source, "blocked_amazon")


@unittest.skipUnless(_MODULE_AVAILABLE, "next_price_alerts not importable in this environment")
class TestProviderPluginParsing(unittest.TestCase):
    def test_provider_result_maps_price_and_metadata(self):
        plugin_result = {
            "status": "ok",
            "price": "19,95",
            "currency": "eur",
            "source_detail": "B09XY1F8QJ",
            "confidence": "0.9",
        }
        with patch(
            "app.backend.next_price_alerts.run_plugin_entrypoint",
            return_value={"status": "ok", "result": plugin_result},
        ):
            (
                price,
                currency,
                source,
                provider_status,
                provider_error,
                source_detail,
                confidence,
            ) = _run_price_provider_check(
                "keepa",
                item_id="00000000-0000-0000-0000-000000000111",
                movievault_id="mv-26",
                shop={"id": "s1", "price_url": "https://example.com/p", "shop_name": "Example", "price_currency": "EUR"},
            )
        self.assertAlmostEqual(price, 19.95)
        self.assertEqual(currency, "EUR")
        self.assertEqual(source, "provider:keepa")
        self.assertEqual(provider_status, "ok")
        self.assertIsNone(provider_error)
        self.assertEqual(source_detail, "B09XY1F8QJ")
        self.assertAlmostEqual(confidence, 0.9)

    def test_provider_execution_error_is_mapped(self):
        with patch(
            "app.backend.next_price_alerts.run_plugin_entrypoint",
            return_value={"status": "error", "error": "boom", "result": {"status": "throttled"}},
        ):
            (
                price,
                currency,
                source,
                provider_status,
                provider_error,
                source_detail,
                confidence,
            ) = _run_price_provider_check("priceapi", item_id="x")
        self.assertIsNone(price)
        self.assertIsNone(currency)
        self.assertIsNone(source)
        self.assertEqual(provider_status, "throttled")
        self.assertEqual(provider_error, "provider_error")
        self.assertIsNone(source_detail)
        self.assertIsNone(confidence)

    def test_private_shop_url_is_rejected_before_plugin_execution(self):
        with (
            patch(
                "app.backend.next_price_alerts.validate_public_url",
                side_effect=PublicHttpError("url_blocked"),
            ),
            patch("app.backend.next_price_alerts.run_plugin_entrypoint") as run_plugin,
        ):
            result = _run_price_provider_check(
                "amazon",
                item_id="x",
                shop={"id": "s1", "price_url": "http://127.0.0.1/private"},
            )
        self.assertEqual(result[3], "error")
        self.assertEqual(result[4], "url_blocked")
        run_plugin.assert_not_called()

    def test_provider_exception_detail_is_not_returned_or_logged(self):
        sensitive = "https://shop.example/product?token=secret-value"
        with (
            patch("app.backend.next_price_alerts.validate_public_url"),
            patch(
                "app.backend.next_price_alerts.run_plugin_entrypoint",
                return_value={
                    "status": "error",
                    "error": "socket failed at 10.0.0.8 with token=secret-value",
                },
            ),
            self.assertLogs("app.backend.next_price_alerts", level="WARNING") as logs,
        ):
            result = _run_price_provider_check(
                "custom",
                item_id="x",
                shop={"id": "s1", "price_url": sensitive},
            )
        self.assertEqual(result[4], "provider_error")
        output = "\n".join(logs.output)
        self.assertNotIn("secret-value", output)
        self.assertNotIn("10.0.0.8", output)


@unittest.skipUnless(_MODULE_AVAILABLE, "next_price_alerts not importable in this environment")
class TestPriceAlertSweepSafety(unittest.TestCase):
    def test_historical_private_url_is_blocked_without_connection_attempt(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [
            {
                "id": "item-1",
                "user_id": "user-1",
                "title": "Private target",
                "target_price": Decimal("10.00"),
                "alert_enabled": True,
                "price_url": "http://127.0.0.1/admin?token=sensitive",
                "last_seen_price": None,
                "price_currency": "EUR",
                "last_price_checked_at": None,
                "last_alerted_at": None,
                "movievault_id": None,
            }
        ]

        with (
            patch.object(price_alerts_module, "table_exists", return_value=True),
            patch.object(
                price_alerts_module,
                "_fetch_item_prices_from_shops",
                return_value=(None, None),
            ),
            patch.object(next_public_http.socket, "create_connection") as create_connection,
        ):
            result = run_price_alert_sweep(conn)

        self.assertEqual(
            result,
            {
                "checked": 0,
                "notified": 0,
                "errors": 0,
                "skipped": 1,
                "status": "ok",
            },
        )
        create_connection.assert_not_called()
        self.assertTrue(
            any(
                "UPDATE wishlist_items SET last_price_checked_at" in call.args[0]
                for call in cursor.execute.call_args_list
            )
        )


if __name__ == "__main__":
    unittest.main()
