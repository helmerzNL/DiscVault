import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_app import create_app
    from app.backend.next_app import next_i18n_messages
    from app.backend.next_app import normalize_next_locale
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name != "flask":
        raise
    create_app = None
    next_i18n_messages = None
    normalize_next_locale = None


@unittest.skipIf(create_app is None, "Flask is not installed in this test environment")
class NextI18nTests(unittest.TestCase):
    def test_normalizes_legacy_and_bcp47_locale_codes(self):
        self.assertEqual(normalize_next_locale("nl"), "nl-NL")
        self.assertEqual(normalize_next_locale("en_GB"), "en-US")
        self.assertEqual(normalize_next_locale("no"), "nb-NO")
        self.assertEqual(normalize_next_locale("unknown"), "nl-NL")

    def test_locale_catalog_falls_back_to_source_messages(self):
        messages = next_i18n_messages("nl")
        self.assertEqual(messages["shell.product"], "DiscVault Next")
        self.assertEqual(messages["collection.title"], "Collectie")
        self.assertIn("migration.start", messages)

    def test_i18n_routes_are_public_without_database(self):
        app = create_app()
        client = app.test_client()

        manifest = client.get("/api/next/i18n")
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest.get_json()["defaultLocale"], "nl-NL")

        catalog = client.get("/api/next/i18n/nl")
        self.assertEqual(catalog.status_code, 200)
        payload = catalog.get_json()
        self.assertEqual(payload["locale"], "nl-NL")
        self.assertEqual(payload["messages"]["collection.title"], "Collectie")


if __name__ == "__main__":
    unittest.main()
