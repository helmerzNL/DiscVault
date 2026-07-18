import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch


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


class _RoleCursor:
    def __init__(self, role_name):
        self._role_name = role_name
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=None):
        if "to_regclass" in query:
            self._row = {"table_name": "public.roles"}
        elif "SELECT name FROM roles" in query:
            self._row = {"name": self._role_name}

    def fetchone(self):
        return self._row


class _RoleConn:
    def __init__(self, role_name):
        self._role_name = role_name

    def cursor(self):
        return _RoleCursor(self._role_name)


class _ProfileCursor:
    def __init__(self):
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=None):
        if "FROM users u" in query:
            self._row = {
                "id": "user-1",
                "username": "reviewer",
                "display_name": "Review User",
                "role": None,
                "avatar_asset_id": None,
                "avatar_id": None,
            }
        elif "SELECT name FROM roles" in query:
            self._row = {"name": "Media Viewer"}

    def fetchone(self):
        return self._row


class _ProfileConn:
    def cursor(self):
        return _ProfileCursor()


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

    def test_profile_role_uses_human_readable_name(self):
        self.assertEqual(
            next_profile._profile_role_display_name(_RoleConn("Media Viewer"), "media_viewer"),
            "Media Viewer",
        )

    def test_profile_role_falls_back_to_key_when_name_is_missing(self):
        self.assertEqual(
            next_profile._profile_role_display_name(_RoleConn(None), "custom_role"),
            "custom_role",
        )

    def test_profile_payload_keeps_role_key_and_adds_display_name(self):
        app_stub = SimpleNamespace(
            media_asset_public_url=lambda avatar: "",
            next_user_primary_role=lambda conn, user_id: "media_viewer",
        )
        with (
            patch.object(next_profile, "_next_app", return_value=app_stub),
            patch.object(next_profile, "table_exists", return_value=True),
        ):
            payload = next_profile.next_profile_user_payload(
                _ProfileConn(),
                {"id": "user-1"},
            )

        self.assertEqual(payload["role"], "media_viewer")
        self.assertEqual(payload["roleDisplayName"], "Media Viewer")
        self.assertEqual(payload["role_display_name"], "Media Viewer")


if __name__ == "__main__":
    unittest.main()
