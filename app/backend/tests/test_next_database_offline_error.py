import os
import sys
import unittest
from unittest.mock import patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_app import create_app
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name != "flask":
        raise
    create_app = None


@unittest.skipIf(create_app is None, "Flask is not installed in this test environment")
class DatabaseOfflineErrorTests(unittest.TestCase):
    """A Postgres outage must render a friendly, translated page/response --
    never the raw psycopg connection error (host/port/FATAL text)."""

    @classmethod
    def setUpClass(cls):
        # A syntactically valid DSN pointing at a closed port: real psycopg
        # OperationalError, without needing a live (or fake) Postgres server.
        env_overrides = {"DATABASE_URL": "postgresql://nouser:nopass@127.0.0.1:1/nonexistentdb"}
        cls._env_patcher = patch.dict(os.environ, env_overrides)
        cls._env_patcher.start()
        cls.app = create_app()
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls._env_patcher.stop()

    def test_html_shell_route_renders_translated_offline_page(self):
        response = self.client.get("/", headers={"Accept-Language": "nl-NL,nl;q=0.9"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers.get("Retry-After"), "20")
        self.assertTrue(response.content_type.startswith("text/html"))
        body = response.get_data(as_text=True)
        self.assertIn("DiscVault is tijdelijk offline", body)
        self.assertNotIn("127.0.0.1", body)
        self.assertNotIn("nouser", body)
        self.assertNotIn("FATAL", body)

    def test_html_shell_route_falls_back_to_default_locale(self):
        response = self.client.get("/")
        body = response.get_data(as_text=True)
        self.assertIn("DiscVault is tijdelijk offline", body)  # nl-NL is the app default

    def test_api_route_returns_clean_json_not_raw_exception_text(self):
        response = self.client.get(
            "/api/next/collection/movies", headers={"Accept-Language": "nl-NL,nl;q=0.9"}
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers.get("Retry-After"), "20")
        payload = response.get_json()
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["error"], "DiscVault is tijdelijk offline")
        self.assertNotIn("127.0.0.1", payload["error"])
        self.assertNotIn("FATAL", payload["error"])

    def test_english_locale_offline_message(self):
        response = self.client.get("/", headers={"Accept-Language": "en-US,en;q=0.9"})
        body = response.get_data(as_text=True)
        self.assertIn("DiscVault is temporarily offline", body)


if __name__ == "__main__":
    unittest.main()
