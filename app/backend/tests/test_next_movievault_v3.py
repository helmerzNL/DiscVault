"""Unit tests for the MovieVault /api/v3 signed client and at-rest key encryption.

These tests exercise the server-side signing client added to
``next_movievault_connection``: base64url encoding, Fernet encryption of the
instance key at rest, the ``X-MV-*`` per-request signature (message composition
+ signature verification), the ``/api/v3/clients/bootstrap`` flow, and the
signed-request transport including the single auto re-bootstrap + retry guard.

They run without Postgres by swapping the module's settings accessors for an
in-memory store and stubbing the HTTP sender.
"""

import base64
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_movievault_connection as mv


class _FakeSender:
    """Records outgoing requests and returns queued _HttpJsonResponse objects."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, data=None):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "data": data})
        if not self._responses:
            raise AssertionError("unexpected extra HTTP call: %s %s" % (method, url))
        status, payload = self._responses.pop(0)
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        return mv._HttpJsonResponse(status, body)


def _resp(status, payload):
    return (status, payload)


class MovieVaultV3ClientTests(unittest.TestCase):
    def setUp(self):
        os.environ["DISCVAULT_NEXT_KEY_ENCRYPTION_KEY"] = "unit-test-kek"
        self.store = {}  # key -> (value, is_secret)
        self._orig = {
            name: getattr(mv, name)
            for name in ("_setting_value", "_set_setting", "_delete_setting", "_table_exists", "_http_request")
        }

        def fake_setting_value(_conn, key, default=None, *, include_secret=False):
            if key not in self.store:
                return default
            value, is_secret = self.store[key]
            if is_secret and not include_secret:
                return default
            return value

        def fake_set_setting(_conn, key, value, *, is_secret=False, actor_id=None):
            self.store[key] = (value, is_secret)

        def fake_delete_setting(_conn, key):
            self.store.pop(key, None)

        mv._setting_value = fake_setting_value
        mv._set_setting = fake_set_setting
        mv._delete_setting = fake_delete_setting
        mv._table_exists = lambda _conn, _name: False

    def tearDown(self):
        for name, value in self._orig.items():
            setattr(mv, name, value)
        os.environ.pop("DISCVAULT_NEXT_KEY_ENCRYPTION_KEY", None)

    def _install_sender(self, responses):
        sender = _FakeSender(responses)
        mv._http_request = sender
        return sender

    def _raw_setting(self, key):
        return self.store.get(key, (None, None))[0]

    # --- base64url ---------------------------------------------------------

    def test_b64url_has_no_padding_and_url_alphabet(self):
        encoded = mv._b64url(bytes(range(32)))
        self.assertNotIn("=", encoded)
        self.assertNotIn("+", encoded)
        self.assertNotIn("/", encoded)
        # Round-trips when padding is restored.
        restored = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        self.assertEqual(restored, bytes(range(32)))

    # --- at-rest encryption ------------------------------------------------

    def test_encrypt_decrypt_roundtrip(self):
        secret = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n"
        encrypted = mv._encrypt_secret_value(secret)
        self.assertTrue(encrypted.startswith(mv.SECRET_ENC_PREFIX))
        self.assertNotIn("BEGIN PRIVATE KEY", encrypted)
        self.assertEqual(mv._decrypt_secret_value(encrypted), secret)

    def test_decrypt_passes_through_legacy_plaintext(self):
        self.assertEqual(mv._decrypt_secret_value("legacy-plaintext"), "legacy-plaintext")
        self.assertFalse(mv._is_encrypted_secret("legacy-plaintext"))

    def test_legacy_database_encrypted_key_requires_visible_reset(self):
        database_url = "postgresql://legacy:secret@db/discvault"
        legacy_material = hashlib.sha256(
            ("discvault-next-kek:" + database_url).encode("utf-8")
        ).hexdigest()
        legacy_key = base64.urlsafe_b64encode(
            hashlib.sha256(legacy_material.encode("utf-8")).digest()
        )
        legacy_ciphertext = Fernet(legacy_key).encrypt(b"legacy-private-key").decode("ascii")
        self.store[mv.INSTANCE_PRIVATE_KEY_KEY] = (mv.SECRET_ENC_PREFIX + legacy_ciphertext, True)
        self.store[mv.INSTANCE_PUBLIC_KEY_KEY] = ("legacy-public-key", False)
        self.store[mv.INSTANCE_PUBLIC_KEY_ID_KEY] = ("legacy-key-id", False)
        self.store[mv.V3_API_TOKEN_KEY] = (
            mv.SECRET_ENC_PREFIX
            + Fernet(legacy_key).encrypt(b"legacy-v3-token").decode("ascii"),
            True,
        )
        for key in (
            mv.V3_KEY_ID_KEY,
            mv.V3_INSTANCE_ID_KEY,
            mv.V3_TOKEN_PREFIX_KEY,
            mv.V3_SCOPES_KEY,
            mv.V3_LAST_BOOTSTRAP_AT_KEY,
        ):
            self.store[key] = ("legacy-v3-state", False)

        status = mv.movievault_connection_status(None)

        self.assertTrue(status["privateKeySet"])
        self.assertTrue(status["requiresReset"])

        mv.reset_movievault_connection(None)

        for key in (
            mv.INSTANCE_PRIVATE_KEY_KEY,
            mv.INSTANCE_PUBLIC_KEY_KEY,
            mv.INSTANCE_PUBLIC_KEY_ID_KEY,
            mv.V3_API_TOKEN_KEY,
            mv.V3_KEY_ID_KEY,
            mv.V3_INSTANCE_ID_KEY,
            mv.V3_TOKEN_PREFIX_KEY,
            mv.V3_SCOPES_KEY,
            mv.V3_LAST_BOOTSTRAP_AT_KEY,
        ):
            self.assertNotIn(key, self.store)
        reset_status = mv.movievault_connection_status(None)
        self.assertFalse(reset_status["privateKeySet"])
        self.assertFalse(reset_status["requiresReset"])

    def test_instance_key_stored_encrypted_and_legacy_migrated(self):
        # Fresh keypair is persisted encrypted.
        pem, public_key, _kid = mv._instance_key_pair(None)
        self.assertTrue(self._raw_setting(mv.INSTANCE_PRIVATE_KEY_KEY).startswith(mv.SECRET_ENC_PREFIX))
        self.assertTrue(public_key)

        # Simulate a legacy plaintext key already on disk -> migrated on read.
        self.store[mv.INSTANCE_PRIVATE_KEY_KEY] = (pem, True)
        pem2, _pk2, _kid2 = mv._instance_key_pair(None)
        self.assertEqual(pem2, pem.strip())
        self.assertTrue(self._raw_setting(mv.INSTANCE_PRIVATE_KEY_KEY).startswith(mv.SECRET_ENC_PREFIX))

    # --- signature headers -------------------------------------------------

    def _load_public_key(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        raw = self._raw_setting(mv.INSTANCE_PUBLIC_KEY_KEY)
        raw_bytes = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        return Ed25519PublicKey.from_public_bytes(raw_bytes)

    def _verify(self, headers, body_bytes):
        signature = headers["X-MV-Signature"]
        self.assertTrue(signature.startswith("key-v1="))
        raw_sig = signature[len("key-v1="):]
        sig_bytes = base64.urlsafe_b64decode(raw_sig + "=" * (-len(raw_sig) % 4))
        message = (headers["X-MV-Timestamp"] + "." + headers["X-MV-Nonce"] + ".").encode("utf-8") + body_bytes
        self._load_public_key().verify(sig_bytes, message)  # raises on mismatch

    def test_signature_headers_present_and_verify_empty_body(self):
        mv._instance_key_pair(None)
        self.store[mv.V3_KEY_ID_KEY] = ("iosk_abc", False)
        headers = mv._v3_signature_headers(None, b"")
        self.assertEqual(
            set(headers),
            {"X-MV-Key-Id", "X-MV-Timestamp", "X-MV-Nonce", "X-MV-Signature"},
        )
        self.assertEqual(headers["X-MV-Key-Id"], "iosk_abc")
        self._verify(headers, b"")

    def test_signature_headers_verify_with_body(self):
        mv._instance_key_pair(None)
        self.store[mv.V3_KEY_ID_KEY] = ("iosk_abc", False)
        body = json.dumps({"z": 1, "a": 2}, separators=(",", ":")).encode("utf-8")
        headers = mv._v3_signature_headers(None, body)
        self._verify(headers, body)

    def test_signature_headers_empty_without_keyid(self):
        mv._instance_key_pair(None)
        self.assertEqual(mv._v3_signature_headers(None, b""), {})

    # --- bootstrap ---------------------------------------------------------

    def test_bootstrap_v3_body_shape_and_storage(self):
        sender = self._install_sender([
            _resp(200, {
                "status": "linked",
                "instanceId": "web_server_x",
                "keyId": "iosk_from_server",
                "platform": "web",
                "keyAlgorithm": "ed25519",
                "apiToken": "mv_live_secrettoken",
                "tokenPrefix": "mv_live_secr",
                "scopes": ["metadata:read"],
            }),
        ])
        result = mv.bootstrap_v3(None)

        # Request shape
        self.assertEqual(len(sender.calls), 1)
        call = sender.calls[0]
        self.assertTrue(call["url"].endswith(mv.V3_BOOTSTRAP_PATH))
        body = json.loads(call["data"].decode("utf-8"))
        self.assertTrue(body["instanceId"].startswith("web_"))
        self.assertEqual(body["platform"], "web")
        self.assertEqual(body["keyAlgorithm"], "ed25519")
        self.assertEqual(body["appName"], "DiscVault")
        self.assertEqual(body["app"], {"name": "DiscVault", "platform": "web"})
        self.assertTrue(body["publicKey"])

        # Response persisted; server keyId always adopted.
        self.assertEqual(result["keyId"], "iosk_from_server")
        self.assertEqual(mv._v3_key_id(None), "iosk_from_server")
        self.assertEqual(mv._v3_token(None), "mv_live_secrettoken")
        self.assertEqual(self.store[mv.V3_INSTANCE_ID_KEY][0], "web_server_x")
        # Token encrypted at rest.
        self.assertTrue(self._raw_setting(mv.V3_API_TOKEN_KEY).startswith(mv.SECRET_ENC_PREFIX))

    def test_bootstrap_v3_reuses_instance_id_across_calls(self):
        self._install_sender([
            _resp(200, {"keyId": "iosk_1", "apiToken": "mv_live_1"}),
            _resp(200, {"keyId": "iosk_2", "apiToken": "mv_live_2"}),
        ])
        first = mv._v3_instance_id(None)
        mv.bootstrap_v3(None)
        mv.bootstrap_v3(None)
        # No server instanceId echoed -> the locally generated web_ id persists.
        self.assertEqual(self.store[mv.V3_INSTANCE_ID_KEY][0], first)

    def test_bootstrap_v3_instance_blocked(self):
        self._install_sender([_resp(403, {"error": {"code": "instance_blocked", "message": "no"}})])
        with self.assertRaises(mv.MovieVaultInstanceRevoked):
            mv.bootstrap_v3(None)

    def test_linked_instance_from_message(self):
        self.assertEqual(
            mv._linked_instance_from_message(
                "This signing key is already linked to "
                "web_1c6b372f-fbdd-4efb-b986-773558874116; reset that link or reuse its instanceId"
            ),
            "web_1c6b372f-fbdd-4efb-b986-773558874116",
        )
        self.assertEqual(mv._linked_instance_from_message("no instance id here"), "")
        self.assertEqual(mv._linked_instance_from_message(""), "")

    def test_bootstrap_v3_self_heals_on_linked_instance(self):
        linked = "web_1c6b372f-fbdd-4efb-b986-773558874116"
        sender = self._install_sender([
            _resp(400, {"error": {
                "code": "validation_error",
                "message": (
                    "This signing key is already linked to "
                    f"{linked}; reset that link or reuse its instanceId"
                ),
            }}),
            _resp(200, {"keyId": "iosk_relinked", "apiToken": "mv_live_relinked", "instanceId": linked}),
        ])
        # Seed a drifted local instanceId so the first attempt uses it.
        self.store[mv.V3_INSTANCE_ID_KEY] = ("web_drifted", False)

        result = mv.bootstrap_v3(None)

        self.assertEqual(len(sender.calls), 2)
        # Linked instanceId persisted before the retry.
        self.assertEqual(self.store[mv.V3_INSTANCE_ID_KEY][0], linked)
        # Retry POST carried the linked instanceId.
        retry_body = json.loads(sender.calls[1]["data"].decode("utf-8"))
        self.assertEqual(retry_body["instanceId"], linked)
        self.assertEqual(result["keyId"], "iosk_relinked")
        self.assertEqual(mv._v3_token(None), "mv_live_relinked")

    def test_bootstrap_v3_relink_retry_guarded_against_loops(self):
        linked = "web_1c6b372f-fbdd-4efb-b986-773558874116"
        message = (
            "This signing key is already linked to "
            f"{linked}; reset that link or reuse its instanceId"
        )
        sender = self._install_sender([
            _resp(400, {"error": {"code": "validation_error", "message": message}}),
            _resp(400, {"error": {"code": "validation_error", "message": message}}),
        ])
        self.store[mv.V3_INSTANCE_ID_KEY] = ("web_drifted", False)

        with self.assertRaises(mv.MovieVaultConnectionError):
            mv.bootstrap_v3(None)
        # Exactly one relink retry — no infinite recursion.
        self.assertEqual(len(sender.calls), 2)

    # --- signed transport --------------------------------------------------

    def _seed_link(self):
        mv._instance_key_pair(None)
        self.store[mv.V3_KEY_ID_KEY] = ("iosk_live", False)
        self.store[mv.V3_API_TOKEN_KEY] = (mv._encrypt_secret_value("mv_live_tok"), True)

    def test_signed_get_attaches_all_headers_and_signs_empty_body(self):
        self._seed_link()
        sender = self._install_sender([_resp(200, {"results": []})])
        response = mv.signed_request_v3(None, "GET", "/api/v3/search?q=matrix")
        self.assertEqual(response.status_code, 200)
        call = sender.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertIsNone(call["data"])
        self.assertEqual(call["headers"]["Authorization"], "Bearer mv_live_tok")
        for header in ("X-MV-Key-Id", "X-MV-Timestamp", "X-MV-Nonce", "X-MV-Signature"):
            self.assertIn(header, call["headers"])
        self.assertNotIn("Content-Type", call["headers"])
        self._verify(call["headers"], b"")

    def test_signed_post_signs_transmitted_bytes(self):
        self._seed_link()
        sender = self._install_sender([_resp(200, {"ok": True})])
        payload = {"title": "Matrix", "year": 1999}
        mv.signed_request_v3(None, "POST", "/api/v3/releases", body_obj=payload)
        call = sender.calls[0]
        expected = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertEqual(call["data"], expected)
        self.assertEqual(call["headers"]["Content-Type"], "application/json")
        self._verify(call["headers"], expected)

    def test_bootstraps_when_no_token(self):
        mv._instance_key_pair(None)
        sender = self._install_sender([
            _resp(200, {"keyId": "iosk_boot", "apiToken": "mv_live_boot"}),
            _resp(200, {"results": []}),
        ])
        mv.signed_request_v3(None, "GET", "/api/v3/search?q=x")
        self.assertEqual(len(sender.calls), 2)
        self.assertTrue(sender.calls[0]["url"].endswith(mv.V3_BOOTSTRAP_PATH))
        self.assertEqual(sender.calls[1]["headers"]["Authorization"], "Bearer mv_live_boot")

    def test_rebootstrap_and_retry_once_on_401(self):
        self._seed_link()
        sender = self._install_sender([
            _resp(401, {"error": {"code": "unauthorized", "message": "expired"}}),
            _resp(200, {"keyId": "iosk_new", "apiToken": "mv_live_new"}),
            _resp(200, {"results": ["hit"]}),
        ])
        response = mv.signed_request_v3(None, "GET", "/api/v3/search?q=x")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"results": ["hit"]})
        # bootstrap happened between the two search calls; retry used the new token.
        self.assertTrue(sender.calls[1]["url"].endswith(mv.V3_BOOTSTRAP_PATH))
        self.assertEqual(sender.calls[2]["headers"]["Authorization"], "Bearer mv_live_new")

    def test_401_retry_guarded_against_loops(self):
        self._seed_link()
        sender = self._install_sender([
            _resp(401, {"error": {"code": "unauthorized"}}),
            _resp(200, {"keyId": "iosk_new", "apiToken": "mv_live_new"}),
            _resp(401, {"error": {"code": "unauthorized"}}),
        ])
        response = mv.signed_request_v3(None, "GET", "/api/v3/search?q=x")
        # Second 401 is returned as-is; no infinite re-bootstrap loop.
        self.assertEqual(response.status_code, 401)
        self.assertEqual(len(sender.calls), 3)

    def test_instance_blocked_raises_and_marks_revoked(self):
        self._seed_link()
        self._install_sender([_resp(403, {"error": {"code": "instance_blocked"}})])
        with self.assertRaises(mv.MovieVaultInstanceRevoked):
            mv.signed_request_v3(None, "GET", "/api/v3/movies/1")
        self.assertEqual(self.store[mv.LINK_STATUS_KEY][0], "revoked")

    def test_health_probe(self):
        self._install_sender([_resp(200, {"status": "ok"})])
        self.assertTrue(mv.movievault_v3_health(None))


if __name__ == "__main__":
    unittest.main()
