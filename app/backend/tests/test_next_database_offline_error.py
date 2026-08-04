import os
import sys
import unittest
from unittest.mock import patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_app import create_app
    from app.backend import next_app
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name != "flask":
        raise
    create_app = None
    next_app = None


@unittest.skipIf(next_app is None, "Flask is not installed in this test environment")
class ConnectStatementTimeoutTests(unittest.TestCase):
    """next-api connections must fail fast on a stuck query/lock instead of
    hanging for gunicorn's full --timeout; next-worker/migrations are
    deliberately left unbounded (imports/sync can legitimately run long) and
    have their own separate connect(), untouched by this."""

    def _captured_options(self, env_overrides=None):
        captured = {}

        def fake_connect(dsn, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop before an actual connection is attempted")

        with (
            patch.dict(os.environ, {"DATABASE_URL": "postgresql://u:p@127.0.0.1:1/db", **(env_overrides or {})}),
            patch("psycopg.connect", side_effect=fake_connect),
        ):
            with self.assertRaises(RuntimeError):
                next_app.connect()
        return captured["options"]

    def test_defaults_are_10s_statement_and_5s_lock_timeout(self):
        options = self._captured_options()
        self.assertIn("statement_timeout=10000", options)
        self.assertIn("lock_timeout=5000", options)

    def test_env_vars_override_the_defaults(self):
        options = self._captured_options(
            {
                "DISCVAULT_NEXT_API_STATEMENT_TIMEOUT_MS": "25000",
                "DISCVAULT_NEXT_API_LOCK_TIMEOUT_MS": "2000",
            }
        )
        self.assertIn("statement_timeout=25000", options)
        self.assertIn("lock_timeout=2000", options)

    def test_invalid_env_value_falls_back_to_default(self):
        options = self._captured_options({"DISCVAULT_NEXT_API_STATEMENT_TIMEOUT_MS": "not-a-number"})
        self.assertIn("statement_timeout=10000", options)


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
