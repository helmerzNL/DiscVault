import os
import sys
import unittest
from datetime import timezone
from unittest import mock


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_auth import _env_flag
    from app.backend.next_auth import _parse_review_login_expires_at
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name not in {"flask", "cbor2", "psycopg"}:
        raise
    _env_flag = None
    _parse_review_login_expires_at = None


@unittest.skipIf(_parse_review_login_expires_at is None, "Flask is not installed in this test environment")
class NextReviewLoginConfigTests(unittest.TestCase):
    def test_parse_review_login_expires_at_accepts_z_suffix(self):
        parsed = _parse_review_login_expires_at("2026-07-01T12:00:00Z")

        self.assertEqual(parsed.tzinfo, timezone.utc)
        self.assertEqual(parsed.isoformat(), "2026-07-01T12:00:00+00:00")

    def test_parse_review_login_expires_at_accepts_naive_timestamp(self):
        parsed = _parse_review_login_expires_at("2026-07-01T12:00:00")

        self.assertEqual(parsed.tzinfo, timezone.utc)
        self.assertEqual(parsed.isoformat(), "2026-07-01T12:00:00+00:00")

    def test_env_flag_uses_default_when_absent(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(_env_flag("REVIEW_LOGIN_ENABLED", default=True))
            self.assertFalse(_env_flag("REVIEW_LOGIN_ENABLED", default=False))

    def test_env_flag_parses_truthy_values(self):
        with mock.patch.dict(os.environ, {"REVIEW_LOGIN_ENABLED": "YES"}, clear=True):
            self.assertTrue(_env_flag("REVIEW_LOGIN_ENABLED", default=False))


if __name__ == "__main__":
    unittest.main()
