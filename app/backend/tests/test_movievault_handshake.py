import hashlib
import hmac
import importlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest


class FakeResponse:
    def __init__(self, status_code=200, data=None, text=""):
        self.status_code = status_code
        self._data = data if data is not None else {}
        self.text = text or json.dumps(self._data)

    def json(self):
        return self._data


class MovieVaultHandshakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = os.path.join(self.tmp.name, "discvault.db")
        os.environ.update({
            "DB_PATH": self.db_path,
            "MOVIEVAULT_INGEST_URL": "https://movies.example.test",
            "MOVIEVAULT_DISCVAULT_HANDSHAKE_SECRET": "test-secret",
            "MOVIEVAULT_API_TOKEN": "",
            "MOVIEVAULT_API_KEY": "",
            "MOVIEVAULT_AUTH_METHOD": "hmac_handshake",
            "RP_NAME": "DiscVault Test",
        })
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        self.inserted_fake_requests = False
        if importlib.util.find_spec("requests") is None:
            fake_requests = types.ModuleType("requests")
            fake_requests.post = None
            fake_requests.request = None
            sys.modules["requests"] = fake_requests
            self.inserted_fake_requests = True
        for name in (
            "app.backend.config",
            "app.backend.logging_utils",
            "app.backend.movievault_client",
            "app.backend.settings.routes",
        ):
            sys.modules.pop(name, None)
        self.mv = importlib.import_module("app.backend.movievault_client")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("""
                CREATE TABLE logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    category TEXT NOT NULL,
                    message TEXT NOT NULL,
                    detail TEXT,
                    user_id TEXT
                )
            """)
            conn.commit()

    def tearDown(self):
        if self.inserted_fake_requests:
            sys.modules.pop("requests", None)
        self.tmp.cleanup()

    def setting(self, key):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else ""

    def test_handshake_signs_exact_raw_body_and_stores_token(self):
        captured = {}

        def fake_post(url, data=None, headers=None, timeout=None):
            captured.update({"url": url, "data": data, "headers": headers or {}})
            return FakeResponse(200, {
                "instance": {
                    "instanceId": json.loads(data.decode("utf-8"))["instanceId"],
                    "name": "DiscVault Test",
                    "lastHandshakeAt": "2026-05-24T12:00:00Z",
                    "rotated": False,
                },
                "client": {
                    "apiToken": "mv_full_token_123",
                    "tokenPrefix": "mv_full",
                    "scopes": ["search:read", "contributions:write", "contributions:read"],
                },
            })

        self.mv.requests.post = fake_post
        self.mv.perform_handshake()

        raw_body = captured["data"].decode("utf-8")
        headers = captured["headers"]
        expected = hmac.new(
            b"test-secret",
            f"{headers['X-DiscVault-Timestamp']}.{headers['X-DiscVault-Nonce']}.{raw_body}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(headers["X-DiscVault-Signature"], f"sha256={expected}")
        self.assertEqual(captured["url"], "https://movies.example.test/api/v1/internal/discvault/handshake")
        self.assertTrue(self.setting("movievault_instance_id").startswith("dv_"))
        self.assertEqual(self.mv.get_movievault_api_token(), "mv_full_token_123")
        self.assertEqual(self.setting("movievault_api_token"), "")
        self.assertTrue(self.setting("movievault_api_token_enc"))
        self.assertEqual(self.setting("movievault_token_prefix"), "mv_full")

    def test_repeated_handshake_keeps_instance_id_and_uses_fresh_nonce(self):
        nonces = []
        instance_ids = []

        def fake_post(url, data=None, headers=None, timeout=None):
            body = json.loads(data.decode("utf-8"))
            instance_ids.append(body["instanceId"])
            nonces.append(headers["X-DiscVault-Nonce"])
            return FakeResponse(200, {
                "instance": {
                    "instanceId": body["instanceId"],
                    "name": body["instanceName"],
                    "lastHandshakeAt": "2026-05-24T12:00:00Z",
                    "rotated": len(instance_ids) > 1,
                },
                "client": {
                    "apiToken": f"mv_token_{len(instance_ids)}",
                    "tokenPrefix": f"mv_t{len(instance_ids)}",
                    "scopes": ["search:read"],
                },
            })

        self.mv.requests.post = fake_post
        self.mv.perform_handshake()
        self.mv.perform_handshake()

        self.assertEqual(instance_ids[0], instance_ids[1])
        self.assertNotEqual(nonces[0], nonces[1])
        self.assertEqual(self.mv.get_movievault_api_token(), "mv_token_2")
        self.assertEqual(self.setting("movievault_api_token"), "")

    def test_unauthorized_request_recovers_token_once_and_retries(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                ("movievault_api_token", "mv_old"),
            )
            conn.commit()
        requests_seen = []

        def fake_request(method, url, headers=None, **kwargs):
            requests_seen.append(headers.get("Authorization"))
            if len(requests_seen) == 1:
                return FakeResponse(401, {"error": "unauthorized"})
            return FakeResponse(200, {"ok": True})

        def fake_post(url, data=None, headers=None, timeout=None):
            body = json.loads(data.decode("utf-8"))
            return FakeResponse(200, {
                "instance": {
                    "instanceId": body["instanceId"],
                    "name": body["instanceName"],
                    "lastHandshakeAt": "2026-05-24T12:00:00Z",
                    "rotated": True,
                },
                "client": {
                    "apiToken": "mv_new",
                    "tokenPrefix": "mv_new",
                    "scopes": ["search:read"],
                },
            })

        self.mv.requests.request = fake_request
        self.mv.requests.post = fake_post

        response = self.mv.movievault_request("GET", "https://search.example.test/api/v1/movies")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(requests_seen, ["Bearer mv_old", "Bearer mv_new"])
        self.assertEqual(self.mv.get_movievault_api_token(), "mv_new")
        self.assertEqual(self.setting("movievault_api_token"), "")

    def test_provisioned_token_takes_precedence_over_env_token(self):
        os.environ["MOVIEVAULT_API_TOKEN"] = "mv_env_old"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                ("movievault_api_token_enc", self.mv._encrypt_secret("mv_provisioned")),
            )
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                ("movievault_link_status", "active"),
            )
            conn.commit()

        requests_seen = []

        def fake_request(method, url, headers=None, **kwargs):
            requests_seen.append(headers.get("Authorization"))
            return FakeResponse(200, {"ok": True})

        def fake_post(*args, **kwargs):
            self.fail("stored provisioned token should avoid recovery")

        self.mv.requests.request = fake_request
        self.mv.requests.post = fake_post

        response = self.mv.movievault_request("GET", "https://search.example.test/api/v1/movies")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.mv.get_movievault_api_token(), "mv_provisioned")
        self.assertEqual(requests_seen, ["Bearer mv_provisioned"])

    def test_disabled_movievault_does_not_attach_token_or_recover(self):
        os.environ["MOVIEVAULT_API_TOKEN"] = "mv_env_token"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                ("movievault_enabled", "false"),
            )
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                ("movievault_api_token_enc", self.mv._encrypt_secret("mv_stored_token")),
            )
            conn.commit()

        requests_seen = []

        def fake_request(method, url, headers=None, **kwargs):
            requests_seen.append(headers.get("Authorization"))
            return FakeResponse(401, {"error": "unauthorized"})

        def fake_post(*args, **kwargs):
            self.fail("disabled MovieVault should not recover a token")

        self.mv.requests.request = fake_request
        self.mv.requests.post = fake_post

        response = self.mv.movievault_request("GET", "https://search.example.test/api/v1/movies")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(requests_seen, [None])
        self.assertEqual(self.mv.get_movievault_api_token(), "")

    def test_movievault_logs_show_api_path_not_full_endpoint(self):
        self.mv._log(
            "warn",
            "MovieVault URL test",
            "Endpoint: https://movies.example.test/api/v1/internal/discvault/bootstrap; "
            "Search: https://search.example.test/api/v1/movies; token=mv_log_secret",
        )

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT detail FROM logs ORDER BY id DESC LIMIT 1").fetchone()

        detail = row[0]
        self.assertNotIn("https://movies.example.test", detail)
        self.assertNotIn("https://search.example.test", detail)
        self.assertIn("/api/v1/internal/discvault/bootstrap", detail)
        self.assertIn("/api/v1/movies", detail)

    def test_instance_revoked_marks_link_and_removes_token(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                ("movievault_api_token", "mv_old"),
            )
            conn.commit()

        self.mv.requests.post = lambda *args, **kwargs: FakeResponse(403, {"error": "instance_revoked"})

        with self.assertRaises(self.mv.MovieVaultInstanceRevoked):
            self.mv.perform_handshake()

        self.assertEqual(self.setting("movievault_link_status"), "revoked")
        self.assertEqual(self.setting("movievault_api_token"), "")
        self.assertEqual(self.mv.get_movievault_api_token(), "")

    def test_settings_api_does_not_return_full_movievault_token(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                ("movievault_api_token", "mv_secret_full_token"),
            )
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                ("movievault_token_prefix", "mv_sec"),
            )
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                ("movievault_instance_id", "dv_test_instance"),
            )
            conn.commit()

        try:
            from flask import Flask
        except ModuleNotFoundError as exc:
            raise unittest.SkipTest("Flask dependency is not installed") from exc

        routes = importlib.import_module("app.backend.settings.routes")
        app = Flask(__name__)
        routes.register_settings_routes(
            app,
            require_admin=lambda: None,
            is_source_enabled=lambda key, default=False: default,
            is_bluray_scrape_enabled=lambda: False,
            is_bluraydiscde_scrape_enabled=lambda: False,
            is_registration_enabled=lambda: False,
        )
        data = app.test_client().get("/api/settings/api-keys").get_json()

        self.assertNotIn("movievault_api_token", data)
        self.assertNotIn("movievault_key", data)
        self.assertNotEqual(data.get("movievault_token_masked"), "mv_secret_full_token")
        self.assertEqual(data.get("movievault_token_prefix"), "mv_sec")
        self.assertEqual(data.get("movievault_instance_id"), "dv_test_instance")

        status = app.test_client().get("/api/settings/movievault/status").get_json()
        self.assertEqual(status.get("movievault_token_prefix"), "mv_sec")
        self.assertEqual(status.get("movievault_instance_id"), "dv_test_instance")

    def test_disabling_movievault_source_disconnects_and_blocks_reconnect(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                ("movievault_enabled", "true"),
            )
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                ("movievault_link_status", "active"),
            )
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                ("movievault_api_token_enc", self.mv._encrypt_secret("mv_secret_token")),
            )
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                ("movievault_token_prefix", "mv_secret"),
            )
            conn.commit()

        try:
            from flask import Flask
        except ModuleNotFoundError as exc:
            raise unittest.SkipTest("Flask dependency is not installed") from exc

        def read_enabled(key, default=False):
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return default if row is None else str(row[0]).lower() == "true"

        routes = importlib.import_module("app.backend.settings.routes")
        app = Flask(__name__)
        routes.register_settings_routes(
            app,
            require_admin=lambda: None,
            is_source_enabled=read_enabled,
            is_bluray_scrape_enabled=lambda: False,
            is_bluraydiscde_scrape_enabled=lambda: False,
            is_registration_enabled=lambda: False,
        )

        def fake_post(*args, **kwargs):
            self.fail("reconnect should be blocked while MovieVault is disabled")

        self.mv.requests.post = fake_post
        client = app.test_client()
        response = client.post("/api/settings/sources", json={
            "movievault_enabled": False,
            "omdb_enabled": True,
            "tmdb_enabled": True,
            "bluray_scrape_enabled": False,
            "bluraydiscde_scrape_enabled": False,
            "metadata_source_order": "movievault,tmdb,omdb,bluray_com,bluray_disc_de",
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.setting("movievault_enabled"), "false")
        self.assertEqual(self.setting("movievault_link_status"), "disabled")
        self.assertEqual(self.setting("movievault_api_token_enc"), "")
        self.assertEqual(self.mv.get_movievault_api_token(), "")

        reconnect = client.post("/api/settings/movievault/reconnect")
        self.assertEqual(reconnect.status_code, 409)
        self.assertFalse(reconnect.get_json().get("movievault_enabled"))


class MovieVaultZeroConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = os.path.join(self.tmp.name, "discvault.db")
        os.environ.update({
            "DB_PATH": self.db_path,
            "MOVIEVAULT_INGEST_URL": "https://movies.example.test",
            "MOVIEVAULT_DISCVAULT_HANDSHAKE_SECRET": "",
            "MOVIEVAULT_API_TOKEN": "",
            "MOVIEVAULT_API_KEY": "",
            "MOVIEVAULT_AUTH_METHOD": "",
            "RP_NAME": "DiscVault Test",
        })
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        if importlib.util.find_spec("cryptography") is None:
            raise unittest.SkipTest("cryptography dependency is not installed")
        self.inserted_fake_requests = False
        if importlib.util.find_spec("requests") is None:
            fake_requests = types.ModuleType("requests")
            fake_requests.post = None
            fake_requests.request = None
            sys.modules["requests"] = fake_requests
            self.inserted_fake_requests = True
        for name in ("app.backend.config", "app.backend.movievault_client"):
            sys.modules.pop(name, None)
        self.mv = importlib.import_module("app.backend.movievault_client")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("""
                CREATE TABLE logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    category TEXT NOT NULL,
                    message TEXT NOT NULL,
                    detail TEXT,
                    user_id TEXT
                )
            """)
            conn.commit()

    def tearDown(self):
        if getattr(self, "inserted_fake_requests", False):
            sys.modules.pop("requests", None)
        self.tmp.cleanup()

    def setting(self, key):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else ""

    def test_zero_config_bootstrap_is_default_and_stores_key_and_token(self):
        captured = {}

        def fake_post(url, data=None, headers=None, timeout=None):
            body = json.loads(data.decode("utf-8"))
            captured.update({"url": url, "body": body})
            return FakeResponse(201, {
                "instance": {
                    "id": 1,
                    "instanceId": body["instanceId"],
                    "name": body["instanceName"],
                    "keyId": "dvpk_testkey",
                    "lastHandshakeAt": "2026-05-24T12:00:00Z",
                    "rotated": False,
                },
                "client": {
                    "id": 12,
                    "apiToken": "mv_zero_token",
                    "tokenPrefix": "mv_zero",
                    "scopes": ["search:read"],
                },
            })

        self.mv.requests.post = fake_post
        self.mv.token_provider().refresh_token()

        self.assertEqual(self.mv.token_provider().name, "bootstrap_signed")
        self.assertEqual(captured["url"], "https://movies.example.test/api/v1/internal/discvault/bootstrap")
        self.assertEqual(captured["body"]["software"]["name"], "DiscVault")
        self.assertTrue(captured["body"]["publicKey"])
        self.assertEqual(self.setting("movievault_instance_public_key_id"), "dvpk_testkey")
        self.assertTrue(self.setting("movievault_instance_private_key_enc"))
        self.assertEqual(self.mv.get_movievault_api_token(), "mv_zero_token")

    def test_zero_config_signed_recovery_uses_key_headers(self):
        private_key_pem, _public_key, public_key_id = self.mv.get_or_create_instance_key_pair()
        captured = {}

        def fake_post(url, data=None, headers=None, timeout=None):
            body = json.loads(data.decode("utf-8"))
            captured.update({"url": url, "body": body, "headers": headers or {}})
            return FakeResponse(201, {
                "instance": {
                    "id": 1,
                    "instanceId": body["instanceId"],
                    "name": body["instanceName"],
                    "keyId": public_key_id,
                    "lastHandshakeAt": "2026-05-24T12:00:00Z",
                    "rotated": True,
                },
                "client": {
                    "id": 12,
                    "apiToken": "mv_recovered_token",
                    "tokenPrefix": "mv_rec",
                    "scopes": ["search:read"],
                },
            })

        self.mv.requests.post = fake_post
        self.mv.token_provider().recover_token()

        self.assertIn("BEGIN PRIVATE KEY", private_key_pem)
        self.assertEqual(captured["url"], "https://movies.example.test/api/v1/internal/discvault/handshake")
        self.assertEqual(captured["headers"]["X-DiscVault-Key-Id"], public_key_id)
        self.assertTrue(captured["headers"]["X-DiscVault-Signature"].startswith("key-v1="))
        self.assertEqual(captured["body"]["instanceId"], self.setting("movievault_instance_id"))
        self.assertEqual(self.mv.get_movievault_api_token(), "mv_recovered_token")


if __name__ == "__main__":
    unittest.main()
