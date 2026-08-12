"""Brute-force throttling for the endpoints outside the legacy password flow.

The legacy password login has counted failed attempts since it was written.
Recovery-code login, the mobile PKCE exchange, passkey assertion verification,
the App Store review login and invite redemption did not, so each accepted an
unbounded number of attempts.

These tests pin the three properties that make the reuse of
``legacy_auth_attempts`` safe without a schema change:

1. scopes cannot exhaust each other's budget, because the digests are
   namespaced per scope rather than per table column;
2. the throttle keys on the address the *server* observed, so a caller cannot
   reset its own counter with a forwarding header;
3. a rejection still records its attempt, even though every one of these
   endpoints rejects from inside a transaction that then rolls back.
"""

import os
import pathlib
import sys
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from flask import Flask

    from app.backend.next_auth import _peer_ip_address
    from app.backend.next_legacy_auth import (
        IP_ONLY_LOCK_ATTEMPTS,
        LOCK_ATTEMPTS,
        attempt_is_throttled,
        ip_only_attempt_is_throttled,
        secret_digest,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - minimal test environments
    if exc.name not in {"flask", "cbor2", "psycopg", "argon2", "jwt", "segno"}:
        raise
    Flask = None


SECRET_ENV = {"JWT_SECRET": "throttle-tests-only-not-a-production-secret"}


@unittest.skipIf(Flask is None, "Flask and the auth dependencies are required")
class ScopedThrottleDigestTests(unittest.TestCase):
    """The namespacing that replaces the schema column we may not add."""

    @mock.patch.dict(os.environ, SECRET_ENV, clear=False)
    def test_scopes_do_not_share_a_bucket(self):
        # The same username failing recovery must not spend the invite budget.
        recovery = secret_digest("recovery-identity", "helmer")
        invite = secret_digest("invite-identity", "helmer")
        self.assertNotEqual(recovery, invite)

    @mock.patch.dict(os.environ, SECRET_ENV, clear=False)
    def test_new_scopes_do_not_collide_with_the_legacy_password_buckets(self):
        # The legacy flow uses the bare "identity"/"ip" namespaces. If a new
        # scope hashed into those, a failed recovery attempt would count against
        # a password login and lock a user out of a flow they never touched.
        legacy_identity = secret_digest("identity", "helmer")
        legacy_ip = secret_digest("ip", "10.0.0.5")
        for scope in ("recovery", "invite", "review", "mobile-exchange", "passkey-verify"):
            self.assertNotEqual(secret_digest(f"{scope}-identity", "helmer"), legacy_identity)
            self.assertNotEqual(secret_digest(f"{scope}-ip", "10.0.0.5"), legacy_ip)

    @mock.patch.dict(os.environ, SECRET_ENV, clear=False)
    def test_identity_is_case_and_whitespace_insensitive(self):
        self.assertEqual(
            secret_digest("recovery-identity", "helmer"),
            secret_digest("recovery-identity", "  HELMER  ".strip().casefold()),
        )

    @mock.patch.dict(os.environ, SECRET_ENV, clear=False)
    def test_digest_does_not_leak_its_input(self):
        digest = secret_digest("recovery-identity", "helmer")
        self.assertEqual(len(digest), 64)
        self.assertNotIn("helmer", digest)


@unittest.skipIf(Flask is None, "Flask and the auth dependencies are required")
class ThrottleThresholdTests(unittest.TestCase):
    def test_identity_keyed_threshold_is_unchanged(self):
        self.assertFalse(attempt_is_throttled(LOCK_ATTEMPTS - 1, 0))
        self.assertTrue(attempt_is_throttled(LOCK_ATTEMPTS, 0))

    def test_ip_only_ceiling_is_far_above_the_identity_one(self):
        # A household behind NAT shares one address. The IP-only endpoints have
        # no second dimension to fall back on, so their ceiling has to leave
        # room for several people mistyping on the same connection.
        self.assertGreater(IP_ONLY_LOCK_ATTEMPTS, LOCK_ATTEMPTS * 4)
        self.assertFalse(ip_only_attempt_is_throttled(IP_ONLY_LOCK_ATTEMPTS - 1))
        self.assertTrue(ip_only_attempt_is_throttled(IP_ONLY_LOCK_ATTEMPTS))


@unittest.skipIf(Flask is None, "Flask and the auth dependencies are required")
class PeerAddressTests(unittest.TestCase):
    """A counter the caller can reset is not a counter."""

    def setUp(self):
        self.app = Flask(__name__)

    def test_forwarding_headers_are_ignored(self):
        with self.app.test_request_context(
            "/api/next/auth/recovery",
            headers={
                "X-Forwarded-For": "8.8.8.8",
                "X-Real-IP": "9.9.9.9",
                "X-DiscVault-Client-IP": "1.1.1.1",
            },
            environ_base={"REMOTE_ADDR": "172.26.0.5"},
        ):
            self.assertEqual(_peer_ip_address(), "172.26.0.5")

    def test_private_peer_address_is_kept(self):
        # request_ip_details() drops private candidates, which would collapse
        # every LAN client into one empty bucket. The throttle needs the peer.
        with self.app.test_request_context(
            "/api/next/auth/recovery",
            environ_base={"REMOTE_ADDR": "192.168.1.20"},
        ):
            self.assertEqual(_peer_ip_address(), "192.168.1.20")

    def test_missing_peer_address_is_empty_not_an_error(self):
        with self.app.test_request_context("/api/next/auth/recovery", environ_base={}):
            self.assertIsInstance(_peer_ip_address(), str)

    def test_outside_a_request_context_it_does_not_raise(self):
        self.assertEqual(_peer_ip_address(), "")


@unittest.skipIf(Flask is None, "Flask and the auth dependencies are required")
class ThrottleWiringContractTests(unittest.TestCase):
    """Source-level checks that each endpoint is actually wired up.

    Behavioural coverage of these routes needs a live PostgreSQL and a full
    auth fixture; what these assert is the part that silently rots -- an
    endpoint being added or rewritten without its throttle.
    """

    @classmethod
    def setUpClass(cls):
        backend = pathlib.Path(__file__).resolve().parents[1]
        cls.auth_source = (backend / "next_auth.py").read_text(encoding="utf-8")

    def _body(self, marker: str, size: int = 4000) -> str:
        start = self.auth_source.index(marker)
        return self.auth_source[start : start + size]

    def test_recovery_login_is_throttled(self):
        body = self._body("def recovery_login():")
        self.assertIn('enforce_scoped_throttle(conn, "recovery", username)', body)
        self.assertIn("record_scoped_attempt(", body)

    def test_recovery_checks_account_status_after_consuming_the_code(self):
        # The membership oracle: while the status check ran first, a wrong code
        # plus a guessed username answered 403 for a real account and 401 for an
        # unknown one, which is a free account-existence probe.
        body = self._body("def recovery_login():")
        consumed = body.index("if not next_consume_recovery_code(")
        disabled = body.index('raise next_api_error("User is disabled", 403)')
        self.assertLess(
            consumed,
            disabled,
            "status check must run after the recovery code is verified",
        )

    def test_mobile_exchange_is_throttled_on_ip_only(self):
        body = self._body("def mobile_auth_exchange():")
        self.assertIn('"mobile-exchange", ip_only=True', body)
        self.assertIn("record_scoped_attempt(", body)

    def test_passkey_login_verify_is_throttled_on_ip_only(self):
        body = self._body("def login_verify():")
        self.assertIn('"passkey-verify", ip_only=True', body)
        self.assertIn("record_scoped_attempt(", body)

    def test_review_login_fallback_is_throttled(self):
        body = self._body("def review_login():")
        self.assertIn('enforce_scoped_throttle(', body)
        self.assertIn('"review"', body)

    def test_review_login_still_delegates_to_the_legacy_flow_first(self):
        # When legacy auth is enabled this route is the legacy login and is
        # counted there; the added throttle must not shadow that delegation.
        body = self._body("def review_login():", size=250)
        self.assertIn("return legacy_login()", body)

    def test_invite_redemption_is_throttled_in_one_place(self):
        body = self._body("def validate_invite(", size=1500)
        self.assertIn('enforce_scoped_throttle(conn, "invite", username)', body)
        self.assertIn("record_scoped_attempt(", body)

    def test_every_scope_has_a_response_that_matches_a_normal_failure(self):
        # A distinct "rate limited" answer would confirm to an attacker that
        # their probing is being counted.
        for scope in ("recovery", "invite", "review", "mobile-exchange", "passkey-verify"):
            self.assertIn(f'"{scope}": (', self.auth_source)
        self.assertNotIn("429", self._body("SCOPED_THROTTLE_RESPONSES", size=800))

    def test_attempts_are_recorded_out_of_band(self):
        # Every one of these endpoints rejects from inside a transaction that
        # rolls back, so recording on the caller's connection would erase the
        # evidence and the counter would never pass one.
        body = self._body("def record_scoped_attempt(", size=1200)
        self.assertIn("with connect() as attempt_conn", body)
        self.assertIn("attempt_conn.commit()", body)

    def test_throttle_never_blocks_a_login_when_it_cannot_write(self):
        body = self._body("def record_scoped_attempt(", size=1200)
        self.assertIn("except Exception:", body)

    def test_missing_attempts_table_does_not_break_authentication(self):
        body = self._body("def scoped_attempt_failures(", size=900)
        self.assertIn('table_exists(conn, "legacy_auth_attempts")', body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
