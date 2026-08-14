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
    def test_movievault_default_source_order_migration_flips_both_tables(self):
        migration = (
            Path(__file__).resolve().parents[1]
            / "migrations_next"
            / "058_movievault_default_source_order.sql"
        ).read_text(encoding="utf-8")

        # Guarded so it is a no-op where the plugin tables do not exist yet.
        for table in (
            "public.plugins",
            "public.metadata_plugins",
            "public.plugin_settings",
            "public.metadata_plugin_settings",
        ):
            self.assertIn(f"to_regclass('{table}')", migration)

        # Both registry tables must be written: sync_plugin_registry() mirrors
        # plugins <- metadata_plugins, so writing only one is reverted or stale.
        for table in ("UPDATE plugins", "UPDATE metadata_plugins"):
            self.assertIn(table, migration)
        self.assertEqual(migration.count("order_index = 45"), 2)
        self.assertEqual(migration.count("order_index = 55"), 2)
        # v2 twice for the enable/order flip and twice for the schema scrub.
        self.assertEqual(migration.count("WHERE id = 'movievault_v2'"), 4)
        self.assertEqual(migration.count("WHERE id = 'movievault_26'"), 2)

        # Idempotent: every flip is conditional on the row not already matching.
        self.assertIn("enabled IS DISTINCT FROM true", migration)
        self.assertIn("enabled IS DISTINCT FROM false", migration)

        # The enforced bucket fallback is scrubbed from schema and stored values.
        self.assertIn("<> 'bucketFallback'", migration)
        self.assertEqual(migration.count("settings - 'bucketFallback'"), 2)

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

    def test_movievault_26_removal_migration_uninstalls_and_repromotes_v2(self):
        """The removal is an uninstall, not a disable, and v2 moves to the top.

        Deleting the plugin directory only makes sync_plugin_registry() flip the
        row to installed=false; the row, its settings, its cached lookups and its
        stored MovieVault credentials all survive that. What this migration is
        for is everything the file deletion does not do, so the assertions are
        about each of those surviving pieces by name.
        """
        migration = (
            Path(__file__).resolve().parents[1]
            / "migrations_next"
            / "080_remove_movievault_26_plugin.sql"
        ).read_text(encoding="utf-8")

        # Guarded so it is a no-op where the tables do not exist yet.
        for table in (
            "public.plugins",
            "public.metadata_plugins",
            "public.plugin_settings",
            "public.metadata_plugin_settings",
            "public.metadata_lookup_cache",
            "public.metadata_field_provenance",
            "public.app_settings",
            "public.background_jobs",
        ):
            self.assertIn(f"to_regclass('{table}')", migration)

        # Both ids go: `movievault_26` and the v25 `movievault` it replaced.
        for statement in (
            "DELETE FROM metadata_field_provenance",
            "DELETE FROM metadata_lookup_cache",
            "DELETE FROM metadata_plugin_settings",
            "DELETE FROM plugin_settings",
            "DELETE FROM metadata_plugins",
            "DELETE FROM plugins",
        ):
            self.assertIn(statement, migration)
        # Six deletes plus the queued-job step, which names the same two ids.
        self.assertEqual(migration.count("IN ('movievault_26', 'movievault')"), 7)
        self.assertIn("UPDATE background_jobs", migration)
        self.assertIn("WHERE status = 'pending'", migration)

        # Provenance is deleted before the registry row it references, because
        # that foreign key is ON DELETE RESTRICT and would otherwise refuse.
        self.assertLess(
            migration.index("DELETE FROM metadata_field_provenance"),
            migration.index("DELETE FROM metadata_plugins"),
        )

        # The stored connection is destroyed, not left at rest.
        for key in (
            "'plugin_secret:movievault_26:token'",
            "'movievault_instance_private_key'",
            "'movievault_v3_api_token'",
            "'movievault_contribution_enabled'",
        ):
            self.assertIn(key, migration)
        # ...but nothing belonging to the plugin that stays, nor the v25 flag the
        # one-shot legacy import reconciliation still reads.
        self.assertNotIn("'movievault_v2_contribution_enabled'", migration)
        self.assertNotIn("'movievault_enabled'", migration)

        # v2 becomes the highest-priority source in both registry tables, which
        # sync_plugin_registry() mirrors one into the other.
        self.assertEqual(migration.count("order_index = 5"), 2)
        self.assertEqual(migration.count("WHERE id = 'movievault_v2'"), 2)
        # Idempotent: the flip is conditional on the row not already matching.
        self.assertIn("enabled IS DISTINCT FROM true", migration)
        self.assertIn("order_index IS DISTINCT FROM 5", migration)

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

    def test_container_ownership_migration_backfills_instance_owner(self):
        migration = (
            Path(__file__).resolve().parents[1]
            / "migrations_next"
            / "044_container_ownership.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("ADD COLUMN IF NOT EXISTS owner_id uuid REFERENCES users(id) ON DELETE SET NULL", migration)
        self.assertIn("WHERE r.key = 'owner'", migration)
        self.assertIn("UPDATE containers c", migration)
        self.assertIn("WHERE c.owner_id IS NULL", migration)
        self.assertIn("idx_containers_owner_id", migration)

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
            patch.object(next_app, "next_auth_ready", return_value=False),
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
    def test_startup_reports_legacy_password_as_ready_authentication(self):
        readiness = {
            "state": "not_required",
            "canStart": False,
            "requiresConfirmation": False,
            "requiredActions": [],
            "activeJob": None,
            "latestRun": None,
            "legacyData": {
                "found": False,
                "readable": False,
                "sourceCounts": {},
                "mediaExtensions": {},
            },
            "importSources": [],
            "migrations": {"state": "ready"},
        }
        counts = {
            "users": 1,
            "passkey_credentials": 0,
            "legacy_password_credentials": 1,
        }

        with (
            patch.object(next_app, "migration_readiness", return_value=readiness),
            patch.object(next_app, "next_auth_effective_enabled", return_value=True),
            patch.object(next_app, "next_auth_ready", return_value=True),
            patch.object(
                next_app,
                "count_table",
                side_effect=lambda _conn, table_name: counts[table_name],
            ),
            patch.object(next_app, "next_auth_current_user", return_value=None),
        ):
            payload = next_app.startup_status_payload(object())

        auth_step = payload["steps"][0]
        self.assertEqual(auth_step["key"], "auth")
        self.assertEqual(auth_step["label"], "Owner account")
        self.assertEqual(auth_step["state"], "complete")
        self.assertEqual(
            auth_step["detail"],
            "1 user(s), 1 authentication credential(s)",
        )
        self.assertNotIn("passkey", auth_step["detail"].lower())
        self.assertTrue(payload["auth"]["ready"])
        self.assertEqual(payload["auth"]["credentialCount"], 0)

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
            patch.object(next_app, "next_auth_ready", return_value=False),
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
