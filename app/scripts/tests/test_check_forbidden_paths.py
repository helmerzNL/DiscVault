"""Tests for app/scripts/check_forbidden_paths.py."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "check_forbidden_paths.py")
)
SPEC = importlib.util.spec_from_file_location("check_forbidden_paths", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


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
        self.path = tempfile.mkdtemp(prefix="discvault_forbidden_paths_")
        subprocess.check_call(
            ["git", "init", "-q", self.path],
            stdout=subprocess.DEVNULL,
        )
        subprocess.check_call(
            ["git", "-C", self.path, "config", "user.email", "test@example.com"]
        )
        subprocess.check_call(
            ["git", "-C", self.path, "config", "user.name", "Test User"]
        )
        return self

    def __exit__(self, *_: object) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def write(self, rel_path: str, content: str = "x") -> None:
        full_path = os.path.join(self.path, rel_path)
        os.makedirs(os.path.dirname(full_path) or self.path, exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as handle:
            handle.write(content)

    def commit_all(self, message: str = "commit") -> None:
        subprocess.check_call(
            ["git", "-C", self.path, "add", "-A"],
            stdout=subprocess.DEVNULL,
        )
        subprocess.check_call(
            ["git", "-C", self.path, "commit", "-q", "-m", message],
            stdout=subprocess.DEVNULL,
        )


class TestForbiddenPathMatching(unittest.TestCase):
    def test_rejects_ios_directory_case_insensitively(self) -> None:
        for path in ("ios", "ios/App.swift", "iOS\\DiscVault\\App.swift"):
            with self.subTest(path=path):
                self.assertTrue(MODULE.is_forbidden_path(path))

    def test_rejects_root_patch_file_case_insensitively(self) -> None:
        self.assertTrue(MODULE.is_forbidden_path("./IOSNATIVEAPP.PATCH"))

    def test_allows_similar_non_root_paths(self) -> None:
        for path in ("docs/ios", "docs/iosnativeapp.patch", "ios-notes.md"):
            with self.subTest(path=path):
                self.assertFalse(MODULE.is_forbidden_path(path))


class TestForbiddenPathCLI(unittest.TestCase):
    def test_staged_mode_rejects_new_ios_file(self) -> None:
        with TempRepo() as repo:
            repo.write("README.md")
            repo.commit_all("initial")
            repo.write("ios/App.swift")
            subprocess.check_call(
                ["git", "-C", repo.path, "add", "ios/App.swift"],
                stdout=subprocess.DEVNULL,
            )

            result = run_guard(repo.path, "--staged")

            self.assertEqual(result.returncode, 1)
            self.assertIn(b"ios/App.swift", result.stderr)
            self.assertIn(b"git rm -r --cached", result.stderr)

    def test_staged_mode_allows_deleting_forbidden_file(self) -> None:
        with TempRepo() as repo:
            repo.write("iosnativeapp.patch")
            repo.commit_all("initial")
            os.remove(os.path.join(repo.path, "iosnativeapp.patch"))
            subprocess.check_call(
                ["git", "-C", repo.path, "add", "-u"],
                stdout=subprocess.DEVNULL,
            )

            result = run_guard(repo.path, "--staged")

            self.assertEqual(result.returncode, 0)

    def test_tree_mode_rejects_forbidden_path(self) -> None:
        with TempRepo() as repo:
            repo.write("IOS/DiscVault/App.swift")
            repo.commit_all("initial")

            result = run_guard(repo.path, "--tree", "HEAD")

            self.assertEqual(result.returncode, 1)
            self.assertIn(b"IOS/DiscVault/App.swift", result.stderr)

    def test_tree_mode_allows_clean_tree(self) -> None:
        with TempRepo() as repo:
            repo.write("docs/ios-notes.md")
            repo.commit_all("initial")

            result = run_guard(repo.path, "--tree", "HEAD")

            self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
