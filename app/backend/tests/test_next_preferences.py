import os
import sys
import unittest
from unittest.mock import patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend import next_app
    from app.backend import next_preferences
except ModuleNotFoundError as exc:
    if exc.name not in {"flask", "psycopg"}:
        raise
    next_app = None
    next_preferences = None


@unittest.skipIf(next_preferences is None, "Flask is not installed in this test environment")
class NextPreferencesModuleTests(unittest.TestCase):
    def test_preferences_helpers_are_reexported_from_next_app(self):
        names = [
            "APP_PREFERENCE_DEFAULTS",
            "APP_BOOLEAN_PREFERENCES",
            "APP_CHOICE_PREFERENCES",
            "APP_PREFERENCE_ALIASES",
            "APP_PREFERENCE_SECTIONS",
            "normalized_app_preference_key",
            "app_effective_preferences",
            "app_preference_sections_payload",
            "mobile_feature_capabilities",
            "mobile_endpoint_contract_payload",
            "register_next_preferences_routes",
        ]
        for name in names:
            self.assertIs(getattr(next_app, name), getattr(next_preferences, name), name)

    def test_normalized_app_preference_key_uses_alias(self):
        self.assertEqual(next_preferences.normalized_app_preference_key("show_search_button"), "show_collection_search")

    def test_mobile_endpoint_contract_includes_stats_endpoint(self):
        contract = next_preferences.mobile_endpoint_contract_payload()
        self.assertIn("stats", contract)
        self.assertEqual(contract["stats"]["personal"], "/api/next/stats/personal")
        self.assertEqual(contract["stats"]["wishlistPriceTrendField"], "wishlistPriceTrend")

    def test_mobile_capabilities_expose_statistics_flags(self):
        class _Conn:
            pass

        actor = {"permissions": ["watchlist.manage"], "role": "member"}
        capabilities = next_preferences.mobile_feature_capabilities(_Conn(), actor)
        self.assertTrue(capabilities["personal"]["statistics"])
        self.assertTrue(capabilities["personal"]["wishlistPriceTrendStats"])

    def test_validate_preferred_price_currency_accepts_empty_and_uppercases(self):
        self.assertEqual(next_preferences.validate_app_preference("preferred_price_currency", ""), "")
        self.assertEqual(next_preferences.validate_app_preference("preferred_price_currency", "usd"), "USD")

    def test_price_display_context_skips_rates_without_preferred_currency(self):
        payload = next_preferences.price_display_context({"price_monitoring_enabled": True, "preferred_price_currency": ""})
        self.assertTrue(payload["monitoringEnabled"])
        self.assertIsNone(payload["preferredCurrency"])
        self.assertEqual(payload["exchangeRates"], {})

    def test_price_display_context_loads_exchange_rates_for_preferred_currency(self):
        class _Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"date": "2026-07-14", "rates": {"USD": 1.2, "GBP": 0.8}}

        next_preferences._PRICE_DISPLAY_RATE_CACHE["payload"] = None
        next_preferences._PRICE_DISPLAY_RATE_CACHE["expires_at"] = None
        with patch("app.backend.next_preferences.requests.get", return_value=_Response()):
            payload = next_preferences.price_display_context(
                {"price_monitoring_enabled": True, "preferred_price_currency": "USD"}
            )
        self.assertEqual(payload["preferredCurrency"], "USD")
        self.assertEqual(payload["source"], "frankfurter")
        self.assertEqual(payload["updatedAt"], "2026-07-14")
        self.assertEqual(payload["exchangeRates"]["EUR"], 1.0)
        self.assertEqual(payload["exchangeRates"]["USD"], 1.2)


if __name__ == "__main__":
    unittest.main()
