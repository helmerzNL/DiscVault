import base64
import hashlib
import os
import sys
import unittest
from unittest import mock


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_auth import next_mobile_auth_code_hash
    from app.backend.next_auth import next_pkce_s256_challenge
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name not in {"flask", "cbor2", "psycopg"}:
        raise
    next_mobile_auth_code_hash = None
    next_pkce_s256_challenge = None


@unittest.skipIf(next_pkce_s256_challenge is None, "Flask is not installed in this test environment")
class NextMobileAuthTests(unittest.TestCase):
    def test_pkce_s256_challenge_matches_standard_encoding(self):
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")

        self.assertEqual(next_pkce_s256_challenge(verifier), expected)

    def test_mobile_code_hash_is_secret_bound_and_not_plaintext(self):
        with mock.patch.dict(os.environ, {"JWT_SECRET": "test-secret"}):
            first = next_mobile_auth_code_hash("one-time-code")
            second = next_mobile_auth_code_hash("one-time-code")

        self.assertEqual(first, second)
        self.assertNotIn("one-time-code", first)
        self.assertEqual(len(first), 64)


if __name__ == "__main__":
    unittest.main()
