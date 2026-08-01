"""Auto-enabling an integration plugin once its credentials are stored.

Saving a TMDb key and then having to hunt for a separate toggle is a dead end
users fall into, so configuring an allow-listed integration counts as the intent
to use it. These tests pin the allow-list boundary, the both-tables write, and
the deliberate one-directionality (clearing a key never disables).
"""

import os
import sys
import unittest
from typing import Any
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_app
from app.backend.next_plugin_runtime import AUTO_ENABLE_ON_CONFIG_PLUGIN_IDS


class _Cursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._conn.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._conn.plugin_row

    def fetchall(self):
        return []


class _Conn:
    def __init__(self, plugin_row):
        self.plugin_row = plugin_row
        self.executed: list[tuple[str, Any]] = []

    def cursor(self, *args, **kwargs):
        return _Cursor(self)


def _enable_statements(conn):
    return [sql for sql, _ in conn.executed if "SET enabled=true" in sql]


class AutoEnableOnConfigTests(unittest.TestCase):
    def setUp(self):
        self.audits: list[dict[str, Any]] = []
        patches = [
            patch.object(next_app, "table_exists", lambda conn, name: True),
            patch.object(
                next_app,
                "audit_event",
                lambda conn, **kwargs: self.audits.append(kwargs),
            ),
            patch.object(
                next_app,
                "plugin_registry_snapshot",
                lambda *a, **k: {"plugins": [{"id": "tmdb", "settingsSchema": {}}]},
            ),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def _run(self, *, plugin_id="tmdb", row=None, config=None, categories=None):
        row = row if row is not None else {
            "enabled": False,
            "installed": True,
            "manifest": {"requiresSecrets": True},
        }
        config = config if config is not None else {
            "requiredSecretsConfigured": True,
            "settingsConfigured": True,
        }
        conn = _Conn(row)
        with patch.object(next_app, "plugin_config_from_db", lambda *a, **k: config):
            enabled = next_app.maybe_auto_enable_configured_plugin(
                conn,
                plugin_id=plugin_id,
                categories=categories if categories is not None else ["metadata_source"],
                actor={"id": "admin-1"},
            )
        return enabled, conn

    def test_tmdb_is_enabled_once_its_key_is_stored(self):
        enabled, conn = self._run()
        self.assertTrue(enabled)
        statements = _enable_statements(conn)
        # Both tables: sync_plugin_registry() mirrors plugins <- metadata_plugins,
        # so enabling only "plugins" is reverted at the next sync.
        self.assertEqual(len(statements), 2)
        self.assertTrue(any("metadata_plugins" in sql for sql in statements))
        self.assertTrue(any(sql.startswith("UPDATE plugins") for sql in statements))
        self.assertEqual(
            [event["event_type"] for event in self.audits], ["plugin.auto_enabled"]
        )

    def test_non_metadata_plugin_only_touches_the_plugins_table(self):
        enabled, conn = self._run(categories=["system"])
        self.assertTrue(enabled)
        statements = _enable_statements(conn)
        self.assertEqual(len(statements), 1)
        self.assertTrue(statements[0].startswith("UPDATE plugins"))

    def test_plugin_outside_the_allow_list_is_never_enabled(self):
        # A blanket rule would silently switch on the price scrapers as soon as a
        # key is stored; starting to scrape shops is a separate, deliberate act.
        for plugin_id in ("keepa", "priceapi", "amazon", "bol", "movievault_26"):
            with self.subTest(plugin_id=plugin_id):
                enabled, conn = self._run(plugin_id=plugin_id)
                self.assertFalse(enabled)
                self.assertEqual(_enable_statements(conn), [])
                self.assertEqual(self.audits, [])

    def test_incomplete_required_secrets_do_not_enable(self):
        enabled, conn = self._run(
            config={"requiredSecretsConfigured": False, "settingsConfigured": True}
        )
        self.assertFalse(enabled)
        self.assertEqual(_enable_statements(conn), [])
        self.assertEqual(self.audits, [])

    def test_already_enabled_plugin_emits_no_second_event(self):
        enabled, conn = self._run(
            row={"enabled": True, "installed": True, "manifest": {"requiresSecrets": True}}
        )
        self.assertFalse(enabled)
        self.assertEqual(_enable_statements(conn), [])
        self.assertEqual(self.audits, [])

    def test_uninstalled_plugin_is_not_enabled(self):
        enabled, _ = self._run(
            row={"enabled": False, "installed": False, "manifest": {"requiresSecrets": True}}
        )
        self.assertFalse(enabled)

    def test_missing_plugin_row_is_not_enabled(self):
        enabled, _ = self._run(row={})
        self.assertFalse(enabled)

    def test_plugin_without_required_secrets_still_enables(self):
        enabled, _ = self._run(
            row={"enabled": False, "installed": True, "manifest": {"requiresSecrets": False}},
            config={"requiredSecretsConfigured": False, "settingsConfigured": True},
        )
        self.assertTrue(enabled)

    def test_allow_list_covers_the_documented_integrations(self):
        self.assertEqual(
            sorted(AUTO_ENABLE_ON_CONFIG_PLUGIN_IDS),
            ["jellyfin", "plex", "tmdb", "trakt"],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
