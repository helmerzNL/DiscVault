"""Tests for app/scripts/bump_version.py."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "bump_version.py")


def _git_available() -> bool:
    try:
        subprocess.check_output(["git", "--version"], stderr=subprocess.DEVNULL)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


class TempRepo:
    """Context manager that creates a throwaway git repository."""

    def __enter__(self) -> "TempRepo":
        self.path = tempfile.mkdtemp(prefix="discvault_bumpver_test_")
        subprocess.check_call(["git", "init", self.path], stdout=subprocess.DEVNULL)
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
        full = os.path.join(self.path, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write(content)

    def read(self, rel_path: str) -> str:
        with open(os.path.join(self.path, rel_path)) as fh:
            return fh.read().strip()

    def commit(self, *rel_paths: str, message: str = "commit") -> str:
        for p in rel_paths:
            subprocess.check_call(
                ["git", "-C", self.path, "add", p], stdout=subprocess.DEVNULL
            )
        subprocess.check_call(
            ["git", "-C", self.path, "commit", "-m", message],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return subprocess.check_output(
            ["git", "-C", self.path, "rev-parse", "HEAD"], text=True
        ).strip()

    def stage(self, *rel_paths: str) -> None:
        for p in rel_paths:
            subprocess.check_call(
                ["git", "-C", self.path, "add", p], stdout=subprocess.DEVNULL
            )

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, SCRIPT, *args],
            cwd=self.path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )


@unittest.skipUnless(_git_available(), "git not available")
class TestBumpVersionImport(unittest.TestCase):
    """Unit tests for bump_version module functions."""

    def setUp(self) -> None:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import bump_version as m  # noqa: PLC0415

        self.m = m

    def test_bump_patch_basic(self) -> None:
        self.assertEqual(self.m.bump_patch("1.2.3"), "1.2.4")

    def test_bump_patch_zeroes(self) -> None:
        self.assertEqual(self.m.bump_patch("0.0.0"), "0.0.1")

    def test_bump_patch_large(self) -> None:
        self.assertEqual(self.m.bump_patch("26.7.35"), "26.7.36")

    def test_bump_patch_invalid(self) -> None:
        self.assertIsNone(self.m.bump_patch("not-a-version"))

    def test_parse_semver_valid(self) -> None:
        self.assertEqual(self.m._parse_semver("1.2.3"), (1, 2, 3))

    def test_parse_semver_invalid(self) -> None:
        self.assertIsNone(self.m._parse_semver("not-a-version"))


@unittest.skipUnless(_git_available(), "git not available")
class TestBumpVersionCLI(unittest.TestCase):
    """End-to-end tests using a throwaway git repo."""

    def test_no_protected_files_noop(self) -> None:
        """Bumper should not touch VERSION when no protected files are staged."""
        with TempRepo() as repo:
            repo.write("app/VERSION", "1.0.0")
            repo.commit("app/VERSION", message="init")

            repo.write("README.md", "docs only")
            repo.stage("README.md")
            repo.run("--no-stage")

            self.assertEqual(repo.read("app/VERSION"), "1.0.0")

    def test_protected_file_bumps_version(self) -> None:
        """Staging a protected file should increment the patch version."""
        with TempRepo() as repo:
            repo.write("app/VERSION", "1.0.0")
            repo.commit("app/VERSION", message="init")

            repo.write("app/backend/foo.py", "# foo")
            repo.stage("app/backend/foo.py")
            result = repo.run("--no-stage")

            self.assertEqual(result.returncode, 0)
            self.assertEqual(repo.read("app/VERSION"), "1.0.1")

    def test_explicit_version_bump_not_doubled(self) -> None:
        """When app/VERSION is already staged, the bumper should not touch it."""
        with TempRepo() as repo:
            repo.write("app/VERSION", "1.0.0")
            repo.commit("app/VERSION", message="init")

            repo.write("app/backend/foo.py", "# foo")
            repo.write("app/VERSION", "2.0.0")
            repo.stage("app/backend/foo.py")
            repo.stage("app/VERSION")
            repo.run("--no-stage")

            self.assertEqual(repo.read("app/VERSION"), "2.0.0")

    def test_base_ref_bump_exceeds_branch_version(self) -> None:
        """When the target branch already has the next version, bump past it.

        Reproduces the root cause of the version-guard failure in check run
        91480277439: two parallel PRs both bumped from 1.0.0 to 1.0.1; the
        second branch's bump must be raised to 1.0.2 to pass the guard.
        """
        with TempRepo() as repo:
            # Establish the "target branch" (main) at 1.0.1
            repo.write("app/VERSION", "1.0.0")
            repo.commit("app/VERSION", message="init")

            repo.write("app/backend/a.py", "# first PR")
            repo.write("app/VERSION", "1.0.1")
            repo.commit("app/backend/a.py", "app/VERSION", message="first PR: bump to 1.0.1")
            subprocess.check_call(
                ["git", "-C", repo.path, "branch", "-f", "main"],
                stdout=subprocess.DEVNULL,
            )

            # Simulate second PR: branch is still at 1.0.0, staged protected change.
            subprocess.check_call(
                ["git", "-C", repo.path, "checkout", "-q", "-b", "feature"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Reset VERSION to the stale value the second PR was opened at
            repo.write("app/VERSION", "1.0.0")
            subprocess.check_call(
                ["git", "-C", repo.path, "add", "app/VERSION"],
                stdout=subprocess.DEVNULL,
            )
            subprocess.check_call(
                ["git", "-C", repo.path, "commit", "--amend", "--no-edit"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            repo.write("app/backend/b.py", "# second PR")
            repo.stage("app/backend/b.py")

            # Without --base-ref, bump would produce 1.0.1 (equal to branch tip).
            result_no_ref = repo.run("--no-stage")
            self.assertEqual(result_no_ref.returncode, 0)
            self.assertEqual(repo.read("app/VERSION"), "1.0.1")

            # Reset version file for next check
            repo.write("app/VERSION", "1.0.0")
            repo.stage("app/backend/b.py")

            # With --base-ref main, bump should produce 1.0.2 (past branch tip).
            result_with_ref = repo.run("--no-stage", "--base-ref", "main")
            self.assertEqual(result_with_ref.returncode, 0)
            self.assertEqual(repo.read("app/VERSION"), "1.0.2")
            self.assertIn("1.0.2", result_with_ref.stderr)

    def test_base_ref_not_found_falls_back_gracefully(self) -> None:
        """When the base-ref does not exist, bump from the local version."""
        with TempRepo() as repo:
            repo.write("app/VERSION", "1.0.0")
            repo.commit("app/VERSION", message="init")

            repo.write("app/backend/foo.py", "# foo")
            repo.stage("app/backend/foo.py")

            # Use a ref that doesn't exist
            result = repo.run("--no-stage", "--base-ref", "nonexistent-branch")
            self.assertEqual(result.returncode, 0)
            # Falls back to local bump: 1.0.0 -> 1.0.1
            self.assertEqual(repo.read("app/VERSION"), "1.0.1")

    def test_empty_base_ref_skips_check(self) -> None:
        """Passing --base-ref '' disables the target-branch check."""
        with TempRepo() as repo:
            repo.write("app/VERSION", "1.0.0")
            repo.commit("app/VERSION", message="init")

            repo.write("app/backend/foo.py", "# foo")
            repo.stage("app/backend/foo.py")

            result = repo.run("--no-stage", "--base-ref", "")
            self.assertEqual(result.returncode, 0)
            self.assertEqual(repo.read("app/VERSION"), "1.0.1")

    def test_base_ref_already_ahead_no_extra_bump(self) -> None:
        """When local version is already above the base ref, no extra bump is needed."""
        with TempRepo() as repo:
            # target branch at 1.0.1
            repo.write("app/VERSION", "1.0.1")
            repo.commit("app/VERSION", message="init")
            subprocess.check_call(
                ["git", "-C", repo.path, "branch", "-f", "main"],
                stdout=subprocess.DEVNULL,
            )

            # Feature branch at 1.0.3 (already above base ref)
            repo.write("app/VERSION", "1.0.3")
            repo.commit("app/VERSION", message="feature: at 1.0.3")

            repo.write("app/backend/foo.py", "# foo")
            repo.stage("app/backend/foo.py")

            result = repo.run("--no-stage", "--base-ref", "main")
            self.assertEqual(result.returncode, 0)
            # Bumps from 1.0.3 -> 1.0.4 (not from main's 1.0.1)
            self.assertEqual(repo.read("app/VERSION"), "1.0.4")


if __name__ == "__main__":
    unittest.main()
