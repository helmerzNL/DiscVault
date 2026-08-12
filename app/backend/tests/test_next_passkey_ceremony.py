"""Passkey login ceremony: per-ceremony challenges and the signature counter.

Two defects this pins, both in the one path a passkey-only instance depends on:

1. The login challenge lived in a single row keyed ``"login"``. Two people
   signing in at the same moment overwrote each other's challenge, and the
   first to finish failed against one that no longer existed. The failure looks
   like a broken passkey rather than a race, which is why it can sit unnoticed.

2. ``sign_count`` was written on every login and compared on none. The counter
   exists to reveal one thing -- the same credential answering from two places
   -- and nothing looked.
"""

import os
import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from app.backend.next_auth import _b64url_decode, _b64url_encode
except ModuleNotFoundError as exc:  # pragma: no cover - minimal test environments
    if exc.name not in {"flask", "cbor2", "psycopg", "argon2", "jwt", "segno"}:
        raise
    _b64url_encode = None


@unittest.skipIf(_b64url_encode is None, "auth dependencies are required")
class LoginChallengeKeyTests(unittest.TestCase):
    """The key has to separate concurrent ceremonies and survive a round trip."""

    def _key(self, challenge: bytes) -> str:
        # Mirrors login_challenge_key, which is a closure inside
        # register_next_auth_routes and cannot be imported directly.
        return f"login:{_b64url_encode(challenge)}"

    def test_two_ceremonies_get_two_keys(self):
        first = self._key(b"\x01" * 32)
        second = self._key(b"\x02" * 32)
        self.assertNotEqual(first, second)

    def test_the_key_survives_the_client_round_trip(self):
        # The browser receives base64url, decodes to bytes, and re-encodes it
        # into clientDataJSON without padding. Both sides must land on the same
        # key or a valid login finds no row.
        challenge = bytes(range(32))
        as_sent = _b64url_encode(challenge)
        as_returned_by_client = as_sent.rstrip("=")
        self.assertEqual(
            self._key(challenge),
            self._key(_b64url_decode(as_returned_by_client)),
        )

    def test_padding_differences_do_not_change_the_key(self):
        challenge = b"\x00" * 31 + b"\x7f"
        padded = _b64url_encode(challenge) + "=="
        self.assertEqual(self._key(challenge), self._key(_b64url_decode(padded)))


@unittest.skipIf(_b64url_encode is None, "auth dependencies are required")
class SignCountPolicyTests(unittest.TestCase):
    """When a counter regression is a clone signal, and when it is noise."""

    @staticmethod
    def _regressed(stored: int, presented: int) -> bool:
        # Mirrors the condition in login_verify.
        return bool(presented and stored and presented <= stored)

    def test_a_counter_that_goes_backwards_is_refused(self):
        self.assertTrue(self._regressed(stored=42, presented=41))

    def test_a_repeated_counter_is_refused(self):
        self.assertTrue(self._regressed(stored=42, presented=42))

    def test_a_counter_that_advances_is_accepted(self):
        self.assertFalse(self._regressed(stored=42, presented=43))

    def test_authenticators_that_never_count_are_exempt(self):
        # Passkeys synced through iCloud Keychain or a password manager report
        # 0 forever by design. Treating that as a regression would reject every
        # login from the authenticators most people actually use.
        self.assertFalse(self._regressed(stored=0, presented=0))

    def test_a_first_login_after_enrolment_is_exempt(self):
        self.assertFalse(self._regressed(stored=0, presented=1))

    def test_an_authenticator_that_stops_counting_is_exempt(self):
        # Going from counting to 0 is a firmware or platform change, not a
        # clone: a cloned credential would present a stale non-zero value.
        self.assertFalse(self._regressed(stored=7, presented=0))


@unittest.skipIf(_b64url_encode is None, "auth dependencies are required")
class LoginVerifyWiringTests(unittest.TestCase):
    """Source-level checks for the parts that silently rot."""

    @classmethod
    def setUpClass(cls):
        backend = pathlib.Path(__file__).resolve().parents[1]
        cls.source = (backend / "next_auth.py").read_text(encoding="utf-8")

    def _body(self, marker: str, size: int = 4500) -> str:
        start = self.source.index(marker)
        return self.source[start : start + size]

    def test_the_global_login_challenge_slot_is_gone(self):
        self.assertNotIn('store_challenge(conn, "login", challenge)', self.source)
        self.assertNotIn('pop_challenge(conn, "login")', self.source)

    def test_options_stores_a_per_ceremony_challenge(self):
        self.assertIn("store_challenge(conn, login_challenge_key(challenge), challenge)", self.source)

    def test_verify_looks_the_challenge_up_by_its_own_value(self):
        body = self._body("def login_verify():")
        self.assertIn("pop_challenge(conn, login_challenge_key(presented_challenge))", body)

    def test_verify_still_consumes_the_challenge(self):
        # pop_challenge deletes the row it returns, so a replay of the same
        # assertion finds nothing. Losing that would make the new per-ceremony
        # rows replayable, which is worse than the single slot ever was.
        body = self._body("def pop_challenge(", size=600)
        self.assertIn("DELETE FROM auth_challenges", body)
        self.assertIn("RETURNING challenge", body)

    def test_the_sign_count_is_compared_and_not_merely_stored(self):
        body = self._body("def login_verify():", size=7000)
        compared = body.index("new_sign_count <= stored_sign_count")
        written = body.index("SET sign_count=%s")
        self.assertLess(compared, written, "the counter must be checked before it is overwritten")

    def test_a_counter_regression_is_audited(self):
        body = self._body("def login_verify():", size=7000)
        self.assertIn("auth.passkey_sign_count_regressed", body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
