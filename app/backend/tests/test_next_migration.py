import os
import sys
import unittest
from unittest.mock import patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_import import clean_date
from app.backend.next_import import legacy_group_member_role
from app.backend.next_import import legacy_role_key
try:
    from app.backend import next_app
except ModuleNotFoundError as exc:
    if exc.name not in {"flask", "psycopg"}:
        raise
    next_app = None
try:
    from app.backend.next_auth import next_generate_recovery_codes
    from app.backend.next_auth import next_normalize_recovery_code
    from app.backend.next_auth import next_recovery_code_hash
except ModuleNotFoundError as exc:
    if exc.name not in {"cbor2", "psycopg"}:
        raise
    next_generate_recovery_codes = None
    next_normalize_recovery_code = None
    next_recovery_code_hash = None


class NextMigrationContractTests(unittest.TestCase):
    def test_legacy_membergroups_maps_to_basic_viewer_role(self):
        self.assertEqual(legacy_role_key("MemberGroups"), "media_viewer")
        self.assertEqual(legacy_role_key("admin"), "admin")
        self.assertEqual(legacy_role_key("anything-custom"), "media_viewer")
        self.assertEqual(legacy_role_key("MemberGroups", is_owner=True), "owner")

    def test_legacy_group_member_role_is_conservative(self):
        self.assertEqual(legacy_group_member_role("manager"), "manager")
        self.assertEqual(legacy_group_member_role("unknown"), "member")

    def test_legacy_date_values_are_normalized_for_postgres(self):
        self.assertEqual(clean_date("1985"), "1985-01-01")
        self.assertEqual(clean_date("2024-07"), "2024-07-01")
        self.assertEqual(clean_date("2024-07-18T12:34:56Z"), "2024-07-18")
        self.assertEqual(clean_date("18/07/2024"), "2024-07-18")
        self.assertIsNone(clean_date("0000-00-00"))
        self.assertIsNone(clean_date("not a date"))

    @unittest.skipIf(next_app is None, "PostgreSQL dependencies are not installed")
    def test_startup_owner_setup_hides_migration_context(self):
        readiness = {
            "state": "ready_for_confirmation",
            "canStart": True,
            "requiresConfirmation": True,
            "requiredActions": ["Confirm migration."],
            "activeJob": None,
            "latestRun": None,
            "legacyData": {
                "pluginId": "discvault_legacy_import",
                "pluginName": "DiscVault Legacy",
                "sourceKind": "sqlite",
                "status": "ready",
                "found": True,
                "readable": True,
                "sourceCounts": {"movies": 12, "groups": 2},
                "mediaExtensions": {},
            },
            "importSources": [{"pluginId": "discvault_legacy_import"}],
            "migrations": {"state": "ready"},
        }

        with (
            patch.object(next_app, "migration_readiness", return_value=readiness),
            patch.object(next_app, "next_auth_effective_enabled", return_value=True),
            patch.object(next_app, "count_table", return_value=0),
            patch.object(next_app, "next_auth_current_user", return_value=None),
        ):
            payload = next_app.startup_status_payload(object())

        self.assertEqual(payload["phase"], "owner_setup")
        self.assertTrue(payload["canCreateOwner"])
        self.assertEqual([step["key"] for step in payload["steps"]], ["auth", "collection"])
        self.assertEqual(payload["migration"]["state"], "")
        self.assertFalse(payload["migration"]["legacyData"]["found"])

    @unittest.skipIf(next_app is None, "PostgreSQL dependencies are not installed")
    def test_startup_uses_legacy_passkeys_before_owner_setup(self):
        readiness = {
            "state": "ready_for_confirmation",
            "canStart": True,
            "requiresConfirmation": True,
            "requiredActions": ["Confirm migration."],
            "activeJob": None,
            "latestRun": None,
            "legacyData": {
                "pluginId": "discvault_legacy_import",
                "pluginName": "DiscVault Legacy",
                "sourceKind": "sqlite",
                "status": "ready",
                "found": True,
                "readable": True,
                "sourceCounts": {"movies": 12, "users": 1, "groups": 2, "credentials": 1},
                "mediaExtensions": {},
            },
            "importSources": [{"pluginId": "discvault_legacy_import"}],
            "migrations": {"state": "ready"},
        }

        with (
            patch.object(next_app, "migration_readiness", return_value=readiness),
            patch.object(next_app, "next_auth_effective_enabled", return_value=False),
            patch.object(next_app, "count_table", return_value=0),
            patch.object(next_app, "next_auth_current_user", return_value=None),
        ):
            payload = next_app.startup_status_payload(object())

        self.assertEqual(payload["phase"], "migration_required")
        self.assertFalse(payload["canCreateOwner"])
        self.assertEqual([step["key"] for step in payload["steps"]], ["auth", "source", "migration", "collection"])
        self.assertEqual(payload["migration"]["state"], "ready_for_confirmation")
        self.assertTrue(payload["migration"]["legacyData"]["found"])

    @unittest.skipIf(next_generate_recovery_codes is None, "Passkey dependencies are not installed")
    def test_recovery_codes_are_normalized_and_one_time_hashable(self):
        codes = next_generate_recovery_codes(4)

        self.assertEqual(len(codes), 4)
        self.assertEqual(len(set(codes)), 4)
        for code in codes:
            self.assertRegex(code, r"^[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$")

        self.assertEqual(next_normalize_recovery_code("ab12-cd34 ef56"), "AB12CD34EF56")
        self.assertEqual(
            next_recovery_code_hash("ab12-cd34"),
            next_recovery_code_hash("AB12CD34"),
        )


if __name__ == "__main__":
    unittest.main()
