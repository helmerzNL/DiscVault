"""Tests for app/scripts/check_plugin_manifest_versions.py.

The guard exists because a bundled plugin edited without a version bump reaches
new installs and no existing one: plugins run from a writable install directory
seeded once, and an installed copy is replaced only when the bundled manifest
version is strictly newer.

`movievault_v2` is the case that exposed it -- `orderIndex` changed from 45 to
5 while the version stayed 1.8.0 -- and the last test here reconstructs exactly
that diff, because a guard that would not have caught the bug it was written for
is not a guard.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "check_plugin_manifest_versions.py")
)
SPEC = importlib.util.spec_from_file_location("check_plugin_manifest_versions", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

PLUGIN_ROOT = "app/backend/next_plugins"


def run_guard(repo_dir: str, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        cwd=repo_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class TempRepo:
    def __enter__(self) -> "TempRepo":
        self.path = tempfile.mkdtemp(prefix="discvault_plugin_versions_")
        subprocess.check_call(["git", "init", "-q", self.path], stdout=subprocess.DEVNULL)
        subprocess.check_call(["git", "-C", self.path, "config", "user.email", "test@example.com"])
        subprocess.check_call(["git", "-C", self.path, "config", "user.name", "Test User"])
        return self

    def __exit__(self, *_: object) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def write(self, rel_path: str, content: str = "x") -> None:
        full_path = os.path.join(self.path, rel_path)
        os.makedirs(os.path.dirname(full_path) or self.path, exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as handle:
            handle.write(content)

    def write_plugin(self, plugin_id: str, *, version: str, order_index: int = 50, body: str = "pass\n") -> None:
        self.write(
            f"{PLUGIN_ROOT}/{plugin_id}/manifest.json",
            json.dumps({"id": plugin_id, "name": plugin_id, "version": version, "orderIndex": order_index}),
        )
        self.write(f"{PLUGIN_ROOT}/{plugin_id}/plugin.py", body)

    def remove(self, rel_path: str) -> None:
        shutil.rmtree(os.path.join(self.path, rel_path), ignore_errors=True)

    def commit_all(self, message: str = "commit") -> str:
        subprocess.check_call(["git", "-C", self.path, "add", "-A"], stdout=subprocess.DEVNULL)
        subprocess.check_call(
            ["git", "-C", self.path, "commit", "-q", "-m", message],
            stdout=subprocess.DEVNULL,
        )
        return subprocess.check_output(
            ["git", "-C", self.path, "rev-parse", "HEAD"], text=True
        ).strip()


class PluginManifestVersionGuardTests(unittest.TestCase):
    def test_an_edited_plugin_without_a_bump_is_refused(self):
        with TempRepo() as repo:
            repo.write_plugin("tmdb", version="1.2.0")
            base = repo.commit_all("base")
            repo.write_plugin("tmdb", version="1.2.0", body="pass  # changed\n")
            head = repo.commit_all("edit without bump")

            result = run_guard(repo.path, "--base", base, "--head", head)

            self.assertEqual(result.returncode, 1)
            self.assertIn(b"tmdb", result.stderr)

    def test_an_edited_plugin_with_a_bump_passes(self):
        with TempRepo() as repo:
            repo.write_plugin("tmdb", version="1.2.0")
            base = repo.commit_all("base")
            repo.write_plugin("tmdb", version="1.2.1", body="pass  # changed\n")
            head = repo.commit_all("edit with bump")

            self.assertEqual(run_guard(repo.path, "--base", base, "--head", head).returncode, 0)

    def test_a_lowered_version_is_refused_like_an_unchanged_one(self):
        # Strictly greater, not merely different: the runtime replaces an
        # installed copy only when the bundled version is newer, so going
        # backwards ships nowhere just as surely as standing still.
        with TempRepo() as repo:
            repo.write_plugin("tmdb", version="1.2.0")
            base = repo.commit_all("base")
            repo.write_plugin("tmdb", version="1.1.9", body="pass  # changed\n")
            head = repo.commit_all("lowered")

            self.assertEqual(run_guard(repo.path, "--base", base, "--head", head).returncode, 1)

    def test_a_new_plugin_needs_no_bump(self):
        with TempRepo() as repo:
            repo.write("README.md", "start\n")
            base = repo.commit_all("base")
            repo.write_plugin("brand_new", version="1.0.0")
            head = repo.commit_all("add plugin")

            self.assertEqual(run_guard(repo.path, "--base", base, "--head", head).returncode, 0)

    def test_a_removed_plugin_needs_no_bump(self):
        with TempRepo() as repo:
            repo.write_plugin("movievault_26", version="1.8.3")
            base = repo.commit_all("base")
            repo.remove(f"{PLUGIN_ROOT}/movievault_26")
            head = repo.commit_all("remove plugin")

            self.assertEqual(run_guard(repo.path, "--base", base, "--head", head).returncode, 0)

    def test_a_change_outside_the_plugins_tree_is_ignored(self):
        with TempRepo() as repo:
            repo.write_plugin("tmdb", version="1.2.0")
            base = repo.commit_all("base")
            repo.write("app/backend/next_app.py", "print('hi')\n")
            head = repo.commit_all("unrelated")

            result = run_guard(repo.path, "--base", base, "--head", head)

            self.assertEqual(result.returncode, 0)
            self.assertIn(b"No bundled plugin files changed", result.stdout)

    def test_the_shared_import_base_is_out_of_scope(self):
        # `_collection_import_base.py` sits directly under the plugin root and
        # is shared by several plugins, so there is no single manifest to bump.
        with TempRepo() as repo:
            repo.write_plugin("import_letterboxd", version="1.0.0")
            base = repo.commit_all("base")
            repo.write(f"{PLUGIN_ROOT}/_collection_import_base.py", "pass  # changed\n")
            head = repo.commit_all("edit shared base")

            self.assertEqual(run_guard(repo.path, "--base", base, "--head", head).returncode, 0)

    def test_each_touched_plugin_is_judged_on_its_own_manifest(self):
        with TempRepo() as repo:
            repo.write_plugin("tmdb", version="1.2.0")
            repo.write_plugin("omdb", version="2.0.0")
            base = repo.commit_all("base")
            repo.write_plugin("tmdb", version="1.3.0", body="pass  # bumped\n")
            repo.write_plugin("omdb", version="2.0.0", body="pass  # not bumped\n")
            head = repo.commit_all("one of each")

            result = run_guard(repo.path, "--base", base, "--head", head)

            self.assertEqual(result.returncode, 1)
            self.assertIn(b"omdb", result.stderr)
            self.assertNotIn(b"tmdb", result.stderr)

    def test_the_version_comparison_matches_the_runtime_parser(self):
        # The guard is only meaningful if it agrees with the function that
        # actually decides whether an upgrade ships.
        self.assertEqual(MODULE.parse_plugin_version("1.8.0"), (1, 8, 0))
        self.assertEqual(MODULE.parse_plugin_version("1.5.1-beta"), (1, 5, 1))
        self.assertEqual(MODULE.parse_plugin_version(""), ())
        self.assertEqual(MODULE.parse_plugin_version(None), ())
        self.assertGreater(MODULE.parse_plugin_version("1.8.1"), MODULE.parse_plugin_version("1.8.0"))

    def test_the_movievault_v2_diff_that_shipped_would_have_been_refused(self):
        # Reconstruction of the real change: orderIndex 45 -> 5, version left at
        # 1.8.0. It reached new installs only, and looked correct because the
        # accompanying migration wrote the database row directly.
        with TempRepo() as repo:
            repo.write_plugin("movievault_v2", version="1.8.0", order_index=45)
            base = repo.commit_all("before #677")
            repo.write_plugin("movievault_v2", version="1.8.0", order_index=5)
            head = repo.commit_all("#677")

            result = run_guard(repo.path, "--base", base, "--head", head)

            self.assertEqual(result.returncode, 1, result.stdout.decode())
            self.assertIn(b"movievault_v2", result.stderr)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
