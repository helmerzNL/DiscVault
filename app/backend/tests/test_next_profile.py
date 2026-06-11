import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend import next_app
    from app.backend import next_profile
except ModuleNotFoundError as exc:  # Local minimal test environments may omit optional backend deps.
    if exc.name not in {"flask", "psycopg"}:
        raise
    next_app = None
    next_profile = None


class _FakeCursor:
    def __init__(self, table_name):
        self._table_name = table_name
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *args):
        # ``table_exists`` probes ``to_regclass`` first.
        if "to_regclass" in args[0]:
            self._row = {"table_name": self._table_name}
        else:
            self._row = {"active_count": 2, "used_count": 1, "last_generated_at": "2026-01-01"}

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, table_name):
        self._table_name = table_name

    def cursor(self):
        return _FakeCursor(self._table_name)


@unittest.skipIf(next_profile is None, "Flask is not installed in this test environment")
class NextProfileHelperTests(unittest.TestCase):
    def test_profile_helpers_are_reexported_from_next_app(self):
        for name in ("next_profile_user_payload", "next_profile_recovery_payload", "register_next_profile_routes"):
            self.assertIs(getattr(next_app, name), getattr(next_profile, name), name)

    def test_recovery_payload_reports_unavailable_when_table_missing(self):
        payload = next_profile.next_profile_recovery_payload(_FakeConn(None), "user-1")
        self.assertEqual(
            payload,
            {"available": False, "activeCount": 0, "usedCount": 0, "lastGeneratedAt": None},
        )

    def test_recovery_payload_counts_active_and_used_codes(self):
        payload = next_profile.next_profile_recovery_payload(_FakeConn("public.recovery_codes"), "user-1")
        self.assertEqual(
            payload,
            {"available": True, "activeCount": 2, "usedCount": 1, "lastGeneratedAt": "2026-01-01"},
        )


if __name__ == "__main__":
    unittest.main()
