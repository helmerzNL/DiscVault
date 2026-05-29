import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_plugin_runtime import discover_plugins
from app.backend.next_plugin_runtime import run_plugin_entrypoint


class NextPluginRuntimeTests(unittest.TestCase):
    def test_legacy_import_plugin_is_discoverable(self):
        discovery = discover_plugins()
        plugins = {plugin.plugin_id: plugin for plugin in discovery["plugins"]}

        self.assertIn("discvault_legacy_import", plugins)
        plugin = plugins["discvault_legacy_import"]
        self.assertIn("import_source", plugin.manifest["categories"])
        self.assertIn("inspect_source", plugin.runtime["entrypoints"])
        self.assertIn("plan_import", plugin.runtime["entrypoints"])

    def test_legacy_import_plugin_inspects_sqlite_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            sqlite_db = data_dir / "discvault.db"
            conn = sqlite3.connect(sqlite_db)
            try:
                conn.execute("CREATE TABLE movies (id INTEGER PRIMARY KEY)")
                conn.execute("CREATE TABLE groups (id INTEGER PRIMARY KEY)")
                conn.executemany("INSERT INTO movies (id) VALUES (?)", [(1,), (2,)])
                conn.execute("INSERT INTO groups (id) VALUES (1)")
                conn.commit()
            finally:
                conn.close()
            posters_dir = data_dir / "posters"
            posters_dir.mkdir()
            (posters_dir / "poster.jpg").write_bytes(b"poster")

            execution = run_plugin_entrypoint(
                "discvault_legacy_import",
                "inspect_source",
                {"dataDir": str(data_dir)},
                {},
            )

        result = execution["result"]
        self.assertEqual(execution["status"], "ok")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["found"])
        self.assertTrue(result["readable"])
        self.assertEqual(result["sourceCounts"]["movies"], 2)
        self.assertEqual(result["sourceCounts"]["groups"], 1)
        self.assertEqual(result["mediaExtensions"][".jpg"], 1)
        self.assertIsNotNone(result["sourceDatabaseHash"])

    def test_legacy_import_plugin_plans_sqlite_import_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            sqlite_db = data_dir / "discvault.db"
            conn = sqlite3.connect(sqlite_db)
            try:
                conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
                conn.commit()
            finally:
                conn.close()

            execution = run_plugin_entrypoint(
                "discvault_legacy_import",
                "plan_import",
                {
                    "dataDir": str(data_dir),
                    "includeSecurity": False,
                    "includePersonal": False,
                    "importMediaReferences": True,
                    "ownerUsername": "admin",
                },
                {},
            )

        plan = execution["result"]
        self.assertEqual(execution["status"], "ok")
        self.assertTrue(plan["canStart"])
        self.assertEqual(plan["jobType"], "migration.import_sqlite")
        self.assertEqual(plan["jobPayload"]["includeSecurity"], False)
        self.assertEqual(plan["jobPayload"]["ownerUsername"], "admin")
        self.assertEqual(plan["jobPayload"]["importSource"]["pluginId"], "discvault_legacy_import")


if __name__ == "__main__":
    unittest.main()
