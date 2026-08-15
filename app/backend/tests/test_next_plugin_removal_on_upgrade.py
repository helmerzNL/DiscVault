"""Removing a plugin from the image has to remove it from the install directory.

Plugins do not run from the image. They run from a writable install directory
(`/data/plugins`) that is seeded once and is authoritative afterwards
(`plugin_paths` puts `plugin_install_dir()` first). `upgrade_seeded_default_plugins`
iterated over the *bundled* plugins, so a plugin dropped from the image was never
visited and its files stayed behind forever.

That is not cosmetic, and the consequences were both observed on a real install:

1. `discover_plugins()` kept finding it, loading and executing its `plugin.py`
   on every scan -- 110 KB of it, in the case of `movievault_26`.
2. `sync_plugin_registry` upserts a row per discovered plugin, so it re-created
   the `plugins` and `metadata_plugins` rows migration 080 had deleted. The
   uninstall was undone on the next sync.

So "removed and uninstalled" held on a fresh install and nowhere else -- and no
test could see it, because every test run starts with no install directory and
therefore an install directory identical to the image. These tests build the
upgraded-deployment case explicitly: seed, then take the plugin out of the
bundle.

What must NOT be removed matters as much. A plugin the operator installed by
hand is not in the marker's `seeded` list and is never touched, and an
unreadable bundle directory means "cannot tell", not "everything was dropped".
"""

import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

sys.modules.setdefault(
    "requests",
    types.SimpleNamespace(
        get=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("requests is stubbed in tests")),
        HTTPError=Exception,
    ),
)

from app.backend import next_plugin_runtime


class PluginRemovalOnUpgradeTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        root = Path(self._temp.name)
        self.bundle = root / "bundled"
        self.install = root / "install"
        self.backups = root / "backups"
        self.bundle.mkdir()

        self._env = {
            "DISCVAULT_BUNDLED_PLUGIN_DIR": str(self.bundle),
            "DISCVAULT_PLUGIN_INSTALL_DIR": str(self.install),
            "DISCVAULT_PLUGIN_BACKUP_DIR": str(self.backups),
            "DISCVAULT_PLUGIN_AUTO_UPDATE": "1",
        }
        self._previous = {key: os.environ.get(key) for key in self._env}
        os.environ.update(self._env)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _write_plugin(self, directory: Path, plugin_id: str, *, version: str = "1.0.0") -> None:
        target = directory / plugin_id
        target.mkdir(parents=True, exist_ok=True)
        (target / "manifest.json").write_text(
            json.dumps(
                {
                    "id": plugin_id,
                    "name": plugin_id,
                    "version": version,
                    "categories": ["metadata_source"],
                    "capabilities": [],
                    "entrypoints": {},
                }
            ),
            encoding="utf-8",
        )
        (target / "plugin.py").write_text("def noop():\n    return None\n", encoding="utf-8")

    def _seed(self, plugin_ids: list[str]) -> None:
        """Put the install directory in the state seeding would leave it in."""

        self.install.mkdir(parents=True, exist_ok=True)
        for plugin_id in plugin_ids:
            shutil.copytree(self.bundle / plugin_id, self.install / plugin_id)
        (self.install / next_plugin_runtime.PLUGIN_INITIALIZED_MARKER).write_text(
            json.dumps(
                {
                    "initializedAt": 1_700_000_000,
                    "source": str(self.bundle),
                    "seeded": sorted(plugin_ids),
                }
            ),
            encoding="utf-8",
        )

    def _marker(self) -> dict:
        return json.loads(
            (self.install / next_plugin_runtime.PLUGIN_INITIALIZED_MARKER).read_text(encoding="utf-8")
        )

    # -- the defect ------------------------------------------------------

    def test_a_plugin_dropped_from_the_image_is_removed_from_the_install_directory(self):
        self._write_plugin(self.bundle, "tmdb")
        self._write_plugin(self.bundle, "movievault_26")
        self._seed(["tmdb", "movievault_26"])
        shutil.rmtree(self.bundle / "movievault_26")  # the image no longer ships it

        result = next_plugin_runtime.upgrade_seeded_default_plugins()

        self.assertEqual(result["removed"], ["movievault_26"])
        self.assertFalse((self.install / "movievault_26").exists())
        self.assertTrue((self.install / "tmdb").exists())

    def test_the_removed_plugin_is_no_longer_discovered(self):
        # The assertion that ties the file removal to the two symptoms: while
        # discovery still finds it, it is still executed and still written back
        # into the registry.
        self._write_plugin(self.bundle, "tmdb")
        self._write_plugin(self.bundle, "movievault_26")
        self._seed(["tmdb", "movievault_26"])
        shutil.rmtree(self.bundle / "movievault_26")

        next_plugin_runtime.reset_plugin_discovery_cache()
        next_plugin_runtime.upgrade_seeded_default_plugins()
        next_plugin_runtime.reset_plugin_discovery_cache()

        found = {plugin.plugin_id for plugin in next_plugin_runtime.discover_plugins()["plugins"]}
        self.assertNotIn("movievault_26", found)
        self.assertIn("tmdb", found)

    def test_the_removal_is_recoverable(self):
        # Snapshotted before deletion, so the existing rollback route can put a
        # wrongly-dropped plugin back.
        self._write_plugin(self.bundle, "tmdb")
        self._write_plugin(self.bundle, "movievault_26")
        self._seed(["tmdb", "movievault_26"])
        shutil.rmtree(self.bundle / "movievault_26")

        next_plugin_runtime.upgrade_seeded_default_plugins()

        self.assertTrue(next_plugin_runtime.plugin_has_backup("movievault_26"))

    def test_the_marker_stops_claiming_it_was_seeded(self):
        # That list answers "did the user delete this, so never resurrect it".
        # This deletion was ours, so the entry has to go with the files.
        self._write_plugin(self.bundle, "tmdb")
        self._write_plugin(self.bundle, "movievault_26")
        self._seed(["tmdb", "movievault_26"])
        shutil.rmtree(self.bundle / "movievault_26")

        next_plugin_runtime.upgrade_seeded_default_plugins()

        self.assertEqual(self._marker()["seeded"], ["tmdb"])

    def test_removal_happens_even_with_auto_update_disabled(self):
        # Auto-update governs whether the operator's plugin files are rewritten
        # underneath them. A plugin the product no longer ships cannot be
        # updated, supported or reinstalled, and its presence reverts an
        # uninstall migration -- so it goes either way.
        os.environ["DISCVAULT_PLUGIN_AUTO_UPDATE"] = "0"
        self._write_plugin(self.bundle, "tmdb")
        self._write_plugin(self.bundle, "movievault_26")
        self._seed(["tmdb", "movievault_26"])
        shutil.rmtree(self.bundle / "movievault_26")

        result = next_plugin_runtime.upgrade_seeded_default_plugins()

        self.assertEqual(result["removed"], ["movievault_26"])
        self.assertFalse((self.install / "movievault_26").exists())

    # -- what must survive ------------------------------------------------

    def test_a_plugin_the_operator_installed_by_hand_is_never_touched(self):
        # Not in the marker's seeded list, so it is not ours to remove -- even
        # though it is absent from the bundle, which is exactly the shape of the
        # plugin that does get removed.
        self._write_plugin(self.bundle, "tmdb")
        self._seed(["tmdb"])
        self._write_plugin(self.install, "operator_plugin")

        result = next_plugin_runtime.upgrade_seeded_default_plugins()

        self.assertEqual(result["removed"], [])
        self.assertTrue((self.install / "operator_plugin").exists())

    def test_an_empty_bundle_directory_removes_nothing(self):
        # The failure mode worth guarding: an unreadable or misconfigured bundle
        # would make every seeded plugin look dropped and delete the lot.
        self._write_plugin(self.bundle, "tmdb")
        self._write_plugin(self.bundle, "omdb")
        self._seed(["tmdb", "omdb"])
        shutil.rmtree(self.bundle)

        result = next_plugin_runtime.upgrade_seeded_default_plugins()

        self.assertEqual(result["removed"], [])
        self.assertTrue((self.install / "tmdb").exists())
        self.assertTrue((self.install / "omdb").exists())

    def test_a_legacy_marker_without_a_seeded_list_removes_nothing(self):
        # Same rule the surrounding code already applies: without proof of what
        # we seeded, nothing is ours to delete.
        self._write_plugin(self.bundle, "tmdb")
        self._seed(["tmdb"])
        self._write_plugin(self.install, "movievault_26")
        (self.install / next_plugin_runtime.PLUGIN_INITIALIZED_MARKER).write_text(
            "seeded", encoding="utf-8"
        )

        result = next_plugin_runtime.upgrade_seeded_default_plugins()

        self.assertEqual(result["removed"], [])
        self.assertTrue((self.install / "movievault_26").exists())

    def test_an_uninitialized_install_directory_is_left_alone(self):
        self._write_plugin(self.bundle, "tmdb")

        result = next_plugin_runtime.upgrade_seeded_default_plugins()

        self.assertEqual(result["removed"], [])
        self.assertFalse(self.install.exists())

    def test_a_bundled_plugin_the_user_deleted_is_still_not_resurrected(self):
        # The pre-existing contract, re-asserted: the new pass must not turn
        # into a reason to copy a deleted default back in.
        self._write_plugin(self.bundle, "tmdb")
        self._write_plugin(self.bundle, "omdb")
        self._seed(["tmdb", "omdb"])
        shutil.rmtree(self.install / "omdb")

        result = next_plugin_runtime.upgrade_seeded_default_plugins()

        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], [])
        self.assertFalse((self.install / "omdb").exists())

    def test_a_newer_bundled_version_still_upgrades_in_place(self):
        self._write_plugin(self.bundle, "tmdb", version="1.0.0")
        self._seed(["tmdb"])
        self._write_plugin(self.bundle, "tmdb", version="1.1.0")

        result = next_plugin_runtime.upgrade_seeded_default_plugins()

        self.assertEqual([entry["plugin"] for entry in result["upgraded"]], ["tmdb"])
        self.assertEqual(next_plugin_runtime.installed_plugin_version("tmdb"), "1.1.0")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
