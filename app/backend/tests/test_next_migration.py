import os
import sys
import unittest
from pathlib import Path
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
    from app.backend.next_auth import next_consume_recovery_code
    from app.backend.next_auth import next_generate_recovery_codes
    from app.backend.next_auth import next_normalize_recovery_code
    from app.backend.next_auth import next_replace_recovery_codes
    from app.backend.next_auth import next_recovery_code_hash
except ModuleNotFoundError as exc:
    if exc.name not in {"cbor2", "psycopg"}:
        raise
    next_consume_recovery_code = None
    next_generate_recovery_codes = None
    next_normalize_recovery_code = None
    next_replace_recovery_codes = None
    next_recovery_code_hash = None


class _RecoveryCursor:
    def __init__(self, rows=None):
        self.calls = []
        self.rows = list(rows or [])
        self.rowcount = 0

    def execute(self, query, params):
        self.calls.append((" ".join(query.split()), params))
        self.rowcount = 1 if "SET used_at=now()" in query else 0

    def fetchall(self):
        return list(self.rows)


class NextMigrationContractTests(unittest.TestCase):
    def test_movievault_person_cleanup_migration_rebuilds_from_tmdb(self):
        migration = (
            Path(__file__).resolve().parents[1]
            / "migrations_next"
            / "040_remove_movievault_person_data.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("lower(movievault_identifier.provider_id) IN ('movievault', 'movievault_26')", migration)
        self.assertIn("lower(tmdb_identifier.provider_id) = 'tmdb'", migration)
        self.assertIn("DELETE FROM person_identifiers", migration)
        self.assertIn("DELETE FROM person_localizations", migration)
        self.assertIn("DELETE FROM people", migration)
        self.assertIn("'metadata.refresh_person'", migration)
        self.assertIn("'metadata.refresh_movie'", migration)
        self.assertIn("existing.status IN ('pending', 'running')", migration)

    def test_recovery_code_migration_unifies_passkey_and_legacy_codes(self):
        migration = (
            Path(__file__).resolve().parents[1]
            / "migrations_next"
            / "042_unified_recovery_codes.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("ADD COLUMN IF NOT EXISTS legacy_code_hash text", migration)
        self.assertIn("FROM legacy_mfa_recovery_codes AS legacy", migration)
        self.assertIn("recovery.legacy_code_hash = legacy.code_hash", migration)

    def test_entity_media_hidden_migration_is_separate_from_trash(self):
        migration = (
            Path(__file__).resolve().parents[1]
            / "migrations_next"
            / "043_entity_media_hidden.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("ADD COLUMN IF NOT EXISTS hidden_at timestamptz", migration)
        self.assertIn("idx_entity_media_active_hidden_artwork", migration)
        self.assertIn("WHERE deleted_at IS NULL", migration)
        self.assertNotIn("purge_after", migration)

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

    @unittest.skipIf(next_replace_recovery_codes is None, "Passkey dependencies are not installed")
    def test_recovery_codes_store_both_hashes_and_share_consumption(self):
        replace_cursor = _RecoveryCursor()
        with (
            patch("app.backend.next_auth.next_recovery_code_hash", return_value="passkey-hash"),
            patch("app.backend.next_auth.hash_recovery_code", return_value="legacy-hash"),
        ):
            next_replace_recovery_codes(replace_cursor, "user-1", ["ABCD-EFGH"])

        insert_call = next(
            call for call in replace_cursor.calls if call[0].startswith("INSERT INTO recovery_codes")
        )
        self.assertEqual(
            insert_call[1],
            ("user-1", "passkey-hash", "legacy-hash", "Recovery code 1"),
        )

        consume_cursor = _RecoveryCursor(
            [{"id": "code-1", "code_hash": "not-a-match", "legacy_code_hash": "legacy-hash"}]
        )
        with patch("app.backend.next_auth.verify_recovery_code", return_value=True):
            self.assertTrue(
                next_consume_recovery_code(consume_cursor, "user-1", "ABCD-EFGH")
            )
        self.assertTrue(
            any(
                query.startswith("UPDATE recovery_codes SET used_at=now()")
                for query, _ in consume_cursor.calls
            )
        )


if __name__ == "__main__":
    unittest.main()
