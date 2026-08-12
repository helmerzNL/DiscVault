"""Reading the plugin list must not make the reader a writer.

The defect this pins was measured on a live instance, not reasoned about. One
transaction sat `idle in transaction` for 14.5 seconds -- the metadata pipeline
waiting on a provider over the network -- while three page loads stalled behind
it on `INSERT INTO plugins`. None of those pages had any reason to write to that
table. They did because every "list the plugins" helper began by syncing the
registry, and the sync upserts a row per plugin, and those rows stay locked until
the surrounding transaction commits.

So a slow provider froze the entire app, through a table the frozen requests were
only trying to read.

Two properties fix it and both are pinned here: discovery is not repeated while
the files are unchanged, and -- the one that actually breaks the coupling -- the
sync takes no locks at all in that case, because it does not run.
"""

import os
import pathlib
import sys
import unittest
from unittest import mock


BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from app.backend import next_plugin_runtime as runtime
except ModuleNotFoundError as exc:  # pragma: no cover - minimal environments
    if exc.name not in {"flask", "psycopg", "requests"}:
        raise
    runtime = None


@unittest.skipIf(runtime is None, "plugin runtime dependencies are required")
class FingerprintTests(unittest.TestCase):
    def setUp(self):
        runtime.reset_plugin_discovery_cache()
        self.addCleanup(runtime.reset_plugin_discovery_cache)

    def test_the_fingerprint_is_stable_across_calls(self):
        self.assertEqual(runtime.plugin_source_fingerprint(), runtime.plugin_source_fingerprint())

    def test_it_covers_the_files_discovery_actually_reads(self):
        parts = runtime.plugin_source_fingerprint()
        named = [entry[0] for entry in parts if len(entry) == 3]
        self.assertTrue(any(name.endswith("manifest.json") for name in named))
        self.assertTrue(
            any(name.endswith("plugin.py") for name in named),
            "plugin.py is the file discovery executes; a fingerprint blind to it "
            "would serve a stale runtime after an edit",
        )

    def test_it_includes_size_as_well_as_mtime(self):
        # A rewrite landing inside the same clock tick must still be seen.
        entry = next(part for part in runtime.plugin_source_fingerprint() if len(part) == 3)
        self.assertEqual(len(entry), 3, "expected (path, mtime_ns, size)")

    def test_it_follows_the_configured_search_paths(self):
        before = runtime.plugin_source_fingerprint()
        with mock.patch.dict(os.environ, {"DISCVAULT_PLUGIN_PATHS": "/nonexistent-probe-path"}):
            after = runtime.plugin_source_fingerprint()
        self.assertNotEqual(
            before,
            after,
            "plugin_paths reads the environment, so a fingerprint that ignored the "
            "paths would serve one directory's discovery for another's",
        )


@unittest.skipIf(runtime is None, "plugin runtime dependencies are required")
class DiscoveryMemoTests(unittest.TestCase):
    def setUp(self):
        runtime.reset_plugin_discovery_cache()
        self.addCleanup(runtime.reset_plugin_discovery_cache)

    def test_unchanged_files_are_discovered_once(self):
        with mock.patch.object(
            runtime, "_discover_plugins_uncached", wraps=runtime._discover_plugins_uncached
        ) as uncached:
            first = runtime.discover_plugins()
            second = runtime.discover_plugins()
        self.assertEqual(uncached.call_count, 1)
        self.assertIs(first, second)

    def test_a_changed_fingerprint_rediscovers(self):
        runtime.discover_plugins()
        with mock.patch.object(
            runtime, "plugin_source_fingerprint", return_value=("something-else",)
        ):
            with mock.patch.object(
                runtime, "_discover_plugins_uncached", return_value={"plugins": [], "paths": [], "errors": []}
            ) as uncached:
                runtime.discover_plugins()
        self.assertEqual(uncached.call_count, 1)

    def test_the_memo_can_be_dropped(self):
        runtime.discover_plugins()
        runtime.reset_plugin_discovery_cache()
        with mock.patch.object(
            runtime, "_discover_plugins_uncached", wraps=runtime._discover_plugins_uncached
        ) as uncached:
            runtime.discover_plugins()
        self.assertEqual(uncached.call_count, 1)


@unittest.skipIf(runtime is None, "plugin runtime dependencies are required")
class RegistrySyncTakesNoLocksWhenNothingChangedTests(unittest.TestCase):
    """The property that stops a page load blocking behind a provider call."""

    def setUp(self):
        runtime.reset_plugin_discovery_cache()
        self.addCleanup(runtime.reset_plugin_discovery_cache)

    @staticmethod
    def _table_exists(_conn, _name):
        return True

    def test_a_second_sync_does_not_touch_the_database(self):
        with mock.patch.object(
            runtime,
            "_sync_plugin_registry_uncached",
            return_value={"paths": [], "discovered": 3, "syncedPlugins": ["a"],
                          "syncedMetadataPlugins": [], "errors": []},
        ) as worker:
            runtime.sync_plugin_registry(object(), self._table_exists, object())
            runtime.sync_plugin_registry(object(), self._table_exists, object())
            runtime.sync_plugin_registry(object(), self._table_exists, object())
        self.assertEqual(
            worker.call_count,
            1,
            "each extra call would upsert a row per plugin and hold those rows "
            "locked until its transaction commits",
        )

    def test_the_skipped_call_still_answers(self):
        payload = {"paths": ["p"], "discovered": 2, "syncedPlugins": ["a", "b"],
                   "syncedMetadataPlugins": ["a"], "errors": []}
        with mock.patch.object(runtime, "_sync_plugin_registry_uncached", return_value=payload):
            first = runtime.sync_plugin_registry(object(), self._table_exists, object())
            second = runtime.sync_plugin_registry(object(), self._table_exists, object())
        self.assertEqual(first, second)

    def test_the_answer_cannot_be_mutated_through_the_memo(self):
        payload = {"paths": [], "discovered": 1, "syncedPlugins": ["a"],
                   "syncedMetadataPlugins": [], "errors": []}
        with mock.patch.object(runtime, "_sync_plugin_registry_uncached", return_value=payload):
            first = runtime.sync_plugin_registry(object(), self._table_exists, object())
            first["discovered"] = 999
            second = runtime.sync_plugin_registry(object(), self._table_exists, object())
        self.assertEqual(second["discovered"], 1)

    def test_changed_files_sync_again(self):
        with mock.patch.object(
            runtime,
            "_sync_plugin_registry_uncached",
            return_value={"paths": [], "discovered": 0, "syncedPlugins": [],
                          "syncedMetadataPlugins": [], "errors": []},
        ) as worker:
            runtime.sync_plugin_registry(object(), self._table_exists, object())
            with mock.patch.object(
                runtime, "plugin_source_fingerprint", return_value=("after-an-install",)
            ):
                runtime.sync_plugin_registry(object(), self._table_exists, object())
        self.assertEqual(
            worker.call_count,
            2,
            "installing or deleting a plugin rewrites the directory, so the next "
            "call must sync rather than serve the memo",
        )

    def test_force_overrides_the_memo(self):
        # For a caller that substitutes discovery, where the on-disk fingerprint
        # no longer describes what would be written.
        with mock.patch.object(
            runtime,
            "_sync_plugin_registry_uncached",
            return_value={"paths": [], "discovered": 0, "syncedPlugins": [],
                          "syncedMetadataPlugins": [], "errors": []},
        ) as worker:
            runtime.sync_plugin_registry(object(), self._table_exists, object())
            runtime.sync_plugin_registry(object(), self._table_exists, object(), force=True)
        self.assertEqual(worker.call_count, 2)

    def test_a_reset_forces_the_next_sync(self):
        with mock.patch.object(
            runtime,
            "_sync_plugin_registry_uncached",
            return_value={"paths": [], "discovered": 0, "syncedPlugins": [],
                          "syncedMetadataPlugins": [], "errors": []},
        ) as worker:
            runtime.sync_plugin_registry(object(), self._table_exists, object())
            runtime.reset_plugin_discovery_cache()
            runtime.sync_plugin_registry(object(), self._table_exists, object())
        self.assertEqual(worker.call_count, 2)


@unittest.skipIf(runtime is None, "plugin runtime dependencies are required")
class ReadPathWiringTests(unittest.TestCase):
    """The read paths that turned out to be writers."""

    @classmethod
    def setUpClass(cls):
        cls.app_source = (BACKEND / "next_app.py").read_text(encoding="utf-8")

    def _body(self, marker: str, size: int = 700) -> str:
        start = self.app_source.index(marker)
        return self.app_source[start : start + size]

    def test_the_snapshot_helper_still_reaches_the_sync(self):
        # It is deliberately left in place rather than deleted: the registry does
        # have to follow the files, and after this change the call is free when
        # they have not moved. Deleting it instead would leave a fresh install
        # with an unpopulated table until some other path happened to sync.
        body = self._body("def collection_plugin_preview_entities(")
        self.assertIn("sync_metadata_plugin_registry(conn)", body)

    def test_the_sync_bootstrap_helper_still_reaches_the_sync(self):
        body = self._body("def metadata_plugin_entities(")
        self.assertIn("sync_metadata_plugin_registry(conn)", body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
