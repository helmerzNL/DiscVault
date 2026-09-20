import hashlib
import os
import pathlib
import sys
import time
import unittest
from unittest import mock

from authlib.jose import JsonWebKey, JsonWebToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.backend import next_oidc


OIDC_ENV = {
    next_oidc.OIDC_ISSUER_ENV: "https://id.example.test",
    next_oidc.OIDC_CLIENT_ID_ENV: "discvault",
    next_oidc.OIDC_CLIENT_SECRET_ENV: "unit-test-secret",
    next_oidc.OIDC_PROVIDER_NAME_ENV: "Pocket ID",
}


class OidcConfigurationTests(unittest.TestCase):
    def test_all_required_values_enable_oidc(self):
        config = next_oidc.oidc_config_from_env(OIDC_ENV)
        self.assertEqual(config.issuer, "https://id.example.test")
        self.assertEqual(config.client_id, "discvault")
        self.assertEqual(config.provider_name, "Pocket ID")

    def test_all_required_values_absent_disable_oidc(self):
        self.assertIsNone(next_oidc.oidc_config_from_env({}))

    def test_partial_configuration_fails_closed(self):
        with self.assertRaises(next_oidc.OidcConfigurationError):
            next_oidc.oidc_config_from_env(
                {next_oidc.OIDC_ISSUER_ENV: "https://id.example.test"}
            )

    def test_non_local_http_issuer_is_rejected(self):
        values = {**OIDC_ENV, next_oidc.OIDC_ISSUER_ENV: "http://id.example.test"}
        with self.assertRaises(next_oidc.OidcConfigurationError):
            next_oidc.oidc_config_from_env(values)

    def test_localhost_http_issuer_is_allowed_for_development(self):
        values = {**OIDC_ENV, next_oidc.OIDC_ISSUER_ENV: "http://localhost:1411"}
        self.assertEqual(
            next_oidc.oidc_config_from_env(values).issuer,
            "http://localhost:1411",
        )

    def test_status_exposes_no_secret_or_provider_endpoint(self):
        with mock.patch.dict(os.environ, OIDC_ENV, clear=True):
            payload = next_oidc.oidc_auth_status(True)
        self.assertEqual(
            payload,
            {
                "oidc_available": True,
                "oidc_provider_name": "Pocket ID",
            },
        )
        self.assertNotIn("client_secret", payload)
        self.assertNotIn("issuer", payload)


class OidcBrowserFlowTests(unittest.TestCase):
    def test_pkce_matches_rfc_7636_vector(self):
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        self.assertEqual(
            next_oidc.oidc_pkce_s256_challenge(verifier),
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        )

    def test_return_path_accepts_only_local_paths(self):
        self.assertEqual(
            next_oidc.safe_oidc_return_path("/app/profile?tab=security"),
            "/app/profile?tab=security",
        )
        for unsafe in (
            "https://evil.example/",
            "//evil.example/",
            r"\evil.example",
            "/api/next/auth/oidc/callback",
        ):
            with self.subTest(unsafe=unsafe):
                self.assertEqual(next_oidc.safe_oidc_return_path(unsafe), "/")

    def test_feedback_replaces_existing_oidc_markers(self):
        path = next_oidc.oidc_feedback_path(
            "/app/profile?oidc_error=old&tab=security",
            linked=True,
        )
        self.assertEqual(path, "/app/profile?tab=security&oidc_linked=1")

    def test_callback_uses_only_the_configured_origin(self):
        self.assertEqual(
            next_oidc._callback_url("https://vault.example.test"),
            "https://vault.example.test/api/next/auth/oidc/callback",
        )
        with self.assertRaises(next_oidc.OidcConfigurationError):
            next_oidc._callback_url("http://vault.example.test")

    def test_state_hash_does_not_store_the_bearer_value(self):
        state = "browser-visible-state"
        digest = next_oidc.oidc_state_hash(state)
        self.assertNotEqual(digest, state)
        self.assertEqual(digest, hashlib.sha256(state.encode()).hexdigest())


class OidcDiscoveryTests(unittest.TestCase):
    def setUp(self):
        next_oidc.clear_oidc_discovery_cache()
        self.config = next_oidc.oidc_config_from_env(OIDC_ENV)

    def test_discovery_accepts_matching_https_metadata(self):
        metadata = {
            "issuer": self.config.issuer,
            "authorization_endpoint": f"{self.config.issuer}/authorize",
            "token_endpoint": f"{self.config.issuer}/token",
            "jwks_uri": f"{self.config.issuer}/jwks",
        }
        with mock.patch.object(
            next_oidc,
            "fetch_oidc_document",
            return_value=metadata,
        ) as fetch:
            self.assertEqual(
                next_oidc.oidc_discovery(self.config)["token_endpoint"],
                f"{self.config.issuer}/token",
            )
            next_oidc.oidc_discovery(self.config)
        fetch.assert_called_once()

    def test_discovery_rejects_an_issuer_mismatch(self):
        with mock.patch.object(
            next_oidc,
            "fetch_oidc_document",
            return_value={
                "issuer": "https://other.example.test",
                "authorization_endpoint": "https://other.example.test/authorize",
                "token_endpoint": "https://other.example.test/token",
                "jwks_uri": "https://other.example.test/jwks",
            },
        ):
            with self.assertRaisesRegex(next_oidc.OidcFlowError, "provider_invalid"):
                next_oidc.oidc_discovery(self.config)

    def test_discovery_rejects_insecure_provider_endpoints(self):
        with mock.patch.object(
            next_oidc,
            "fetch_oidc_document",
            return_value={
                "issuer": self.config.issuer,
                "authorization_endpoint": "http://id.example.test/authorize",
                "token_endpoint": "https://id.example.test/token",
                "jwks_uri": "https://id.example.test/jwks",
            },
        ):
            with self.assertRaisesRegex(next_oidc.OidcFlowError, "provider_invalid"):
                next_oidc.oidc_discovery(self.config)


class OidcIdTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        options = {"kid": "test-key", "use": "sig", "alg": "RS256"}
        private_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        public_pem = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        cls.private_jwk = JsonWebKey.import_key(private_pem, options).as_dict(
            is_private=True
        )
        cls.public_jwk = JsonWebKey.import_key(
            public_pem,
            options,
        ).as_dict()
        cls.jwt = JsonWebToken(["RS256"])
        cls.config = next_oidc.oidc_config_from_env(OIDC_ENV)
        cls.discovery = {"jwks_uri": "https://id.example.test/jwks"}

    def _token(self, **overrides):
        now = int(time.time())
        claims = {
            "iss": self.config.issuer,
            "sub": "stable-subject",
            "aud": self.config.client_id,
            "iat": now,
            "exp": now + 300,
            "nonce": "expected-nonce",
            **overrides,
        }
        return {
            "id_token": self.jwt.encode(
                {"alg": "RS256", "kid": "test-key"},
                claims,
                self.private_jwk,
            ).decode("ascii")
        }

    def _validate(self, token, nonce="expected-nonce"):
        with mock.patch.object(
            next_oidc,
            "fetch_oidc_document",
            return_value={"keys": [self.public_jwk]},
        ):
            return next_oidc.validate_oidc_id_token(
                self.config,
                self.discovery,
                token,
                nonce_hash=next_oidc.oidc_state_hash(nonce),
                code="authorization-code",
            )

    def test_valid_signed_token_is_accepted(self):
        claims = self._validate(self._token())
        self.assertEqual(claims["sub"], "stable-subject")

    def test_wrong_nonce_is_rejected(self):
        with self.assertRaisesRegex(next_oidc.OidcFlowError, "id_token_invalid"):
            self._validate(self._token(), nonce="different-nonce")

    def test_wrong_issuer_is_rejected(self):
        with self.assertRaisesRegex(next_oidc.OidcFlowError, "id_token_invalid"):
            self._validate(self._token(iss="https://other.example.test"))

    def test_wrong_audience_is_rejected(self):
        with self.assertRaisesRegex(next_oidc.OidcFlowError, "id_token_invalid"):
            self._validate(self._token(aud="another-client"))

    def test_expired_token_is_rejected(self):
        now = int(time.time())
        with self.assertRaisesRegex(next_oidc.OidcFlowError, "id_token_invalid"):
            self._validate(self._token(iat=now - 600, exp=now - 300))

    def test_multiple_audiences_require_the_correct_authorized_party(self):
        with self.assertRaisesRegex(next_oidc.OidcFlowError, "id_token_invalid"):
            self._validate(
                self._token(
                    aud=[self.config.client_id, "another-client"],
                    azp="another-client",
                )
            )


class _TransactionCursor:
    def __init__(self, row):
        self.row = row
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        self.queries.append((" ".join(query.split()), params))

    def fetchone(self):
        return self.row


class _TransactionConnection:
    def __init__(self, row):
        self.cursor_instance = _TransactionCursor(row)

    def cursor(self):
        return self.cursor_instance


class OidcTransactionTests(unittest.TestCase):
    def test_transaction_lookup_is_hashed_single_use_and_expiring(self):
        row = {
            "id": "transaction",
            "nonce_hash": "hash",
            "code_verifier": "verifier",
            "mode": "login",
        }
        conn = _TransactionConnection(row)
        self.assertIs(next_oidc._transaction_row(conn, "raw-state"), row)
        queries = conn.cursor_instance.queries
        self.assertIn("DELETE FROM oidc_auth_transactions WHERE expires_at < now()", queries[0][0])
        self.assertIn("SET used_at=now()", queries[1][0])
        self.assertIn("used_at IS NULL", queries[1][0])
        self.assertIn("expires_at > now()", queries[1][0])
        self.assertEqual(
            queries[1][1],
            (next_oidc.oidc_state_hash("raw-state"),),
        )


class OidcWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        backend = REPO_ROOT / "app" / "backend"
        cls.auth = (backend / "next_auth.py").read_text(encoding="utf-8")
        cls.oidc = (backend / "next_oidc.py").read_text(encoding="utf-8")
        cls.profile = (backend / "next_profile.py").read_text(encoding="utf-8")
        cls.migration = (
            backend / "migrations_next" / "093_oidc_auth.sql"
        ).read_text(encoding="utf-8")
        cls.nonce_migration = (
            backend / "migrations_next" / "094_oidc_nonce_hash.sql"
        ).read_text(encoding="utf-8")

    def test_identity_constraints_are_exact_and_durable(self):
        self.assertIn("UNIQUE (issuer, subject)", self.migration)
        self.assertIn("UNIQUE (user_id, issuer)", self.migration)
        self.assertNotIn("lower(issuer", self.migration.lower())
        self.assertNotIn("lower(subject", self.migration.lower())

    def test_nonce_migration_drops_live_flows_before_renaming(self):
        delete = self.nonce_migration.index("DELETE FROM oidc_auth_transactions")
        rename = self.nonce_migration.index(
            "RENAME COLUMN nonce TO nonce_hash"
        )
        self.assertLess(delete, rename)

    def test_readiness_requires_both_oidc_tables(self):
        start = self.auth.index("def next_auth_ready(")
        end = self.auth.index("\n\n_AUTH_ENABLED_ATTR", start)
        body = self.auth[start:end]
        self.assertIn('table_exists(conn, "oidc_identities")', body)
        self.assertIn('table_exists(conn, "oidc_auth_transactions")', body)

    def test_callback_creation_does_not_use_request_host(self):
        start = self.oidc.index("def _callback_url(")
        end = self.oidc.index("\n\ndef _transaction_row", start)
        body = self.oidc[start:end]
        self.assertNotIn("request.host", body)
        self.assertNotIn("X-Forwarded-Proto", body)

    def test_no_email_or_username_auto_link_query_exists(self):
        callback = self.oidc[self.oidc.index("def oidc_callback():") :]
        self.assertNotIn("WHERE email=", callback)
        self.assertNotIn("WHERE username=", callback)
        self.assertIn("WHERE oi.issuer=%s AND oi.subject=%s", callback)

    def test_registration_and_owner_bootstrap_are_explicit(self):
        self.assertIn("not registration_enabled(conn)", self.oidc)
        self.assertIn('"owner" if user_count == 0', self.oidc)
        self.assertIn("SELECT pg_advisory_xact_lock", self.oidc)
        self.assertIn('user.get("status") != "active"', self.oidc)

    def test_linking_is_bound_to_the_initiating_user(self):
        self.assertIn(
            'str(actor["id"]) != str(initiating_user_id)',
            self.oidc,
        )

    def test_unlink_checks_another_usable_login_method(self):
        self.assertIn("next_auth_usable_login_method_count(", self.profile)
        self.assertIn("remaining < 1", self.profile)
        self.assertIn("credential_expires_at > now()", self.auth)
        self.assertIn("locked_until <= now()", self.auth)
        self.assertIn("legacy_auth_env_enabled()", self.auth)

    def test_api_tokens_and_mcp_are_not_part_of_oidc(self):
        self.assertNotIn("API_TOKEN_PREFIX", self.oidc)
        self.assertNotIn('"/mcp"', self.oidc)
        self.assertIn("def _bearer_api_token()", self.auth)


if __name__ == "__main__":
    unittest.main()
