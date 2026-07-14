import json
import os
import sys
import unittest
from pathlib import Path


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)


from app.backend.next_price_provider_detection import detect_price_provider
from app.backend.next_price_provider_detection import extract_amazon_asin
from app.backend.next_price_provider_detection import price_provider_entity


PLUGIN_ROOT = Path(repo_root) / "app" / "backend" / "next_plugins"


def _provider(plugin_id: str, *, enabled: bool = True, secrets_configured: bool = False):
    manifest = json.loads((PLUGIN_ROOT / plugin_id / "manifest.json").read_text(encoding="utf-8"))
    return {
        "id": manifest["id"],
        "enabled": enabled,
        "installed": True,
        "orderIndex": manifest["orderIndex"],
        "requiresSecrets": manifest.get("requiresSecrets", False),
        "secretsConfigured": secrets_configured,
        "manifest": manifest,
    }


class PriceProviderDetectionTests(unittest.TestCase):
    def test_provider_entity_exposes_manifest_metadata_and_usable_state(self):
        provider = price_provider_entity(_provider("amazon"))

        self.assertTrue(provider["usable"])
        self.assertEqual(provider["priceProvider"]["hostPatterns"], ["amazon."])
        self.assertEqual(provider["priceProvider"]["productRef"], {"type": "amazon_asin"})

    def test_provider_requiring_missing_secret_is_not_usable(self):
        provider = price_provider_entity(_provider("keepa", secrets_configured=False))

        self.assertFalse(provider["usable"])

    def test_disabled_provider_is_not_usable(self):
        provider = price_provider_entity(_provider("amazon", enabled=False))

        self.assertFalse(provider["usable"])

    def test_detects_exact_domain_suffix_without_matching_spoofed_host(self):
        provider_id, product_ref = detect_price_provider(
            "https://www.bol.com/nl/nl/p/example/9300000000000/",
            [_provider("bol")],
        )
        spoofed_id, _ = detect_price_provider(
            "https://bol.com.example.test/product",
            [_provider("bol")],
        )

        self.assertEqual(provider_id, "bol")
        self.assertIsNone(product_ref)
        self.assertIsNone(spoofed_id)

    def test_prefers_lowest_order_usable_amazon_provider(self):
        provider_id, product_ref = detect_price_provider(
            "https://www.amazon.nl/dp/B09G9HD5XW?tag=test",
            [
                _provider("amazon"),
                _provider("keepa", secrets_configured=True),
            ],
        )

        self.assertEqual(provider_id, "keepa")
        self.assertEqual(product_ref, "B09G9HD5XW")

    def test_skips_unusable_provider_and_falls_back_to_amazon(self):
        provider_id, product_ref = detect_price_provider(
            "https://smile.amazon.com/gp/product/B09G9HD5XW/ref=test",
            [
                _provider("keepa", secrets_configured=False),
                _provider("amazon"),
            ],
        )

        self.assertEqual(provider_id, "amazon")
        self.assertEqual(product_ref, "B09G9HD5XW")

    def test_extracts_supported_amazon_asin_url_forms(self):
        self.assertEqual(
            extract_amazon_asin("https://www.amazon.nl/dp/B09G9HD5XW"),
            "B09G9HD5XW",
        )
        self.assertEqual(
            extract_amazon_asin("https://www.amazon.com/gp/product/B09G9HD5XW/ref=test"),
            "B09G9HD5XW",
        )
        self.assertEqual(
            extract_amazon_asin("https://www.amazon.de/s?k=disc&asin=B09G9HD5XW"),
            "B09G9HD5XW",
        )


if __name__ == "__main__":
    unittest.main()
