"""Tests for app/scripts/check_version_bumped.py."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# Path to the script under test
SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "check_version_bumped.py"
)


def _git_available() -> bool:
    try:
        subprocess.check_output(["git", "--version"], stderr=subprocess.DEVNULL)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _run_script_result(
    repo_dir: str, *extra_args: str
) -> subprocess.CompletedProcess[str]:
    """Run the version-guard script in repo_dir and capture its result."""
    return subprocess.run(
        [sys.executable, SCRIPT, *extra_args],
        cwd=repo_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _run_script(repo_dir: str, *extra_args: str) -> int:
    """Run the version-guard script in repo_dir and return the exit code."""
    return _run_script_result(repo_dir, *extra_args).returncode


class TempRepo:
    """Context manager that creates a throwaway git repository."""

    def __enter__(self) -> "TempRepo":
        self.path = tempfile.mkdtemp(prefix="discvault_test_")
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

    def run(self, *args: str) -> int:
        return _run_script(self.path, *args)


@unittest.skipUnless(_git_available(), "git not available")
class TestRequiresVersionBump(unittest.TestCase):
    """Pure unit tests for requires_version_bump() — import the module directly."""

    def setUp(self) -> None:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import check_version_bumped as m  # noqa: PLC0415

        self.m = m

    def test_protected_prefix_triggers_true(self) -> None:
        self.assertTrue(self.m.requires_version_bump(["app/backend/foo.py"]))

    def test_md_ignored(self) -> None:
        self.assertFalse(self.m.requires_version_bump(["README.md"]))

    def test_txt_ignored(self) -> None:
        self.assertFalse(self.m.requires_version_bump(["docs/notes.txt"]))

    def test_version_file_only_returns_false(self) -> None:
        self.assertFalse(self.m.requires_version_bump(["app/VERSION"]))

    def test_version_file_plus_protected_returns_true(self) -> None:
        self.assertTrue(
            self.m.requires_version_bump(["app/backend/bar.py", "app/VERSION"])
        )

    def test_workflows_prefix_triggers_true(self) -> None:
        self.assertTrue(
            self.m.requires_version_bump([".github/workflows/some.yml"])
        )


@unittest.skipUnless(_git_available(), "git not available")
class TestVersionGuardCLI(unittest.TestCase):
    """End-to-end CLI tests that spin up a throwaway git repo."""

    def test_pr23_scenario_per_commit_fails_aggregate_passes(self) -> None:
        """
        Reproduce PR #23:
        - Commit A: bumps app/VERSION + touches app/backend/foo.py   → OK per commit
        - Commit B: touches app/backend/bar.py only (no bump)        → FAILS per commit
        The aggregate of A..B *does* include the VERSION bump, so --aggregate passes.
        """
        with TempRepo() as repo:
            # Initial commit so HEAD~1 exists
            repo.write("app/VERSION", "1.0.0")
            root_sha = repo.commit("app/VERSION", message="init")

            # Commit A: backend change + version bump
            repo.write("app/backend/foo.py", "# foo")
            repo.write("app/VERSION", "1.0.1")
            sha_a = repo.commit("app/backend/foo.py", "app/VERSION", message="feat+bump")

            # Commit B: backend change, NO version bump
            repo.write("app/backend/bar.py", "# bar")
            sha_b = repo.commit("app/backend/bar.py", message="fix: no bump")

            # Per-commit run over A..B: commit B should fail → exit 1
            rc_per_commit = repo.run("--base", sha_a, "--head", sha_b)
            self.assertEqual(rc_per_commit, 1, "per-commit should fail on commit B")

            # Aggregate run over root..B: VERSION was bumped somewhere → exit 0
            rc_aggregate = repo.run("--base", root_sha, "--head", sha_b, "--aggregate")
            self.assertEqual(rc_aggregate, 0, "aggregate should pass (VERSION bumped in A)")

    def test_aggregate_fails_when_no_version_bump_anywhere(self) -> None:
        """Aggregate must still fail when no commit in the range bumps app/VERSION."""
        with TempRepo() as repo:
            repo.write("app/VERSION", "1.0.0")
            root_sha = repo.commit("app/VERSION", message="init")

            repo.write("app/backend/foo.py", "# foo")
            sha_a = repo.commit("app/backend/foo.py", message="feat: no bump")

            repo.write("app/backend/bar.py", "# bar")
            sha_b = repo.commit("app/backend/bar.py", message="fix: still no bump")

            rc = repo.run("--base", root_sha, "--head", sha_b, "--aggregate")
            self.assertEqual(rc, 1, "aggregate should fail: no VERSION bump in range")

    def test_aggregate_passes_docs_only_change(self) -> None:
        """Aggregate should pass when only docs (*.md) are changed."""
        with TempRepo() as repo:
            repo.write("app/VERSION", "1.0.0")
            root_sha = repo.commit("app/VERSION", message="init")

            repo.write("README.md", "# docs")
            sha_a = repo.commit("README.md", message="docs: update readme")

            rc = repo.run("--base", root_sha, "--head", sha_a, "--aggregate")
            self.assertEqual(rc, 0, "aggregate should pass: docs-only change")

    def test_per_commit_default_still_passes_for_clean_bump(self) -> None:
        """Default (non-aggregate) path should pass when the single commit bumps VERSION."""
        with TempRepo() as repo:
            repo.write("app/VERSION", "1.0.0")
            repo.commit("app/VERSION", message="init")

            repo.write("app/backend/foo.py", "# foo")
            repo.write("app/VERSION", "1.0.1")
            repo.commit("app/backend/foo.py", "app/VERSION", message="feat+bump")

            # --base HEAD~1 --head HEAD (default)
            rc = repo.run()
            self.assertEqual(rc, 0)

    def test_aggregate_fails_invalid_semver_at_head(self) -> None:
        """A protected change bumping to a non-semver value must fail with a clear reason."""
        with TempRepo() as repo:
            repo.write("app/VERSION", "1.0.0")
            root_sha = repo.commit("app/VERSION", message="init")

            repo.write("app/backend/foo.py", "# foo")
            repo.write("app/VERSION", "not-a-version")
            sha_a = repo.commit("app/backend/foo.py", "app/VERSION", message="feat+bad bump")

            result = subprocess.run(
                [sys.executable, SCRIPT, "--base", root_sha, "--head", sha_a, "--aggregate"],
                cwd=repo.path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("semver", result.stderr)

    def test_aggregate_fails_when_head_version_equals_base(self) -> None:
        """Merely re-writing the same version value must not satisfy the guard."""
        with TempRepo() as repo:
            repo.write("app/VERSION", "1.0.0")
            root_sha = repo.commit("app/VERSION", message="init")

            repo.write("app/backend/foo.py", "# foo")
            repo.write("app/VERSION", "1.0.0")
            sha_a = repo.commit("app/backend/foo.py", "app/VERSION", message="feat, no real bump")

            rc = repo.run("--base", root_sha, "--head", sha_a, "--aggregate")
            self.assertEqual(rc, 1, "identical version value is not a real bump")

    def test_two_branches_same_next_version_stale_equal_head_fails(self) -> None:
        """
        Reproduce the PR #340/#341 class of bug end-to-end:
        - main starts at 1.0.0.
        - Branch A forks from main, independently bumps to 1.0.1 with a protected
          change, and lands first (main's real tip is now 1.0.1).
        - Branch B forked from the SAME original commit (before A merged) and,
          unaware of A, also bumps to the SAME 1.0.1 with its own protected change.
        - Checked against the actual (now-updated) base, B's "bump" is stale/equal,
          not a real increment, and must fail even though it looks fine relative to
          the old fork point.
        """
        with TempRepo() as repo:
            repo.write("app/VERSION", "1.0.0")
            root_sha = repo.commit("app/VERSION", message="init")

            # Branch A: bumps to 1.0.1 and lands first -> becomes the new base tip.
            repo.write("app/backend/a.py", "# a")
            repo.write("app/VERSION", "1.0.1")
            sha_a = repo.commit("app/backend/a.py", "app/VERSION", message="feat A + bump")

            # Branch B: forked from the same root, independently chose the same
            # next version (1.0.1) without knowledge of A.
            subprocess.check_call(
                ["git", "-C", repo.path, "checkout", "-q", root_sha, "-b", "branch-b"]
            )
            repo.write("app/backend/b.py", "# b")
            repo.write("app/VERSION", "1.0.1")
            sha_b = repo.commit("app/backend/b.py", "app/VERSION", message="feat B + same bump")

            # Against the REAL current base (sha_a, now at 1.0.1) this must fail:
            # B's version does not actually advance past the true base.
            rc_against_true_base = repo.run("--base", sha_a, "--head", sha_b, "--aggregate")
            self.assertEqual(
                rc_against_true_base,
                1,
                "stale/equal version vs the real (updated) base must fail",
            )

            # Sanity check: against the stale original fork point alone, the same
            # diff looks like a legitimate bump (1.0.0 -> 1.0.1). This is exactly
            # why comparing against the *actual* base value (not just diff
            # presence) matters.
            rc_against_stale_base = repo.run("--base", root_sha, "--head", sha_b, "--aggregate")
            self.assertEqual(rc_against_stale_base, 0)

    def test_base_ref_resolves_moved_target_branch(self) -> None:
        """--base-ref lets the guard detect a target branch that moved forward
        since the (possibly stale) --base sha was recorded, without needing a
        real network remote."""
        with TempRepo() as repo:
            repo.write("app/VERSION", "1.0.0")
            root_sha = repo.commit("app/VERSION", message="init")

            repo.write("app/backend/a.py", "# a")
            repo.write("app/VERSION", "1.0.1")
            sha_a = repo.commit("app/backend/a.py", "app/VERSION", message="feat A + bump")
            # Simulate the target branch ("main") having already moved to sha_a.
            subprocess.check_call(["git", "-C", repo.path, "branch", "-f", "main", sha_a])

            subprocess.check_call(
                ["git", "-C", repo.path, "checkout", "-q", root_sha, "-b", "branch-b"]
            )
            repo.write("app/backend/b.py", "# b")
            repo.write("app/VERSION", "1.0.1")
            sha_b = repo.commit("app/backend/b.py", "app/VERSION", message="feat B + stale bump")

            # The recorded/stale base (root_sha) alone would pass; --base-ref
            # should resolve to the moved branch and catch the redundant bump.
            rc = repo.run("--base", root_sha, "--head", sha_b, "--base-ref", "main", "--aggregate")
            self.assertEqual(rc, 1, "base-ref should resolve to the moved target and fail")

            # Without --base-ref, the stale base alone looks fine (regression guard).
            rc_without_base_ref = repo.run("--base", root_sha, "--head", sha_b, "--aggregate")
            self.assertEqual(rc_without_base_ref, 0)

    def test_failure_reports_full_range_and_actual_target_branch(self) -> None:
        """Failure output keeps the aggregate range and uses --base-ref guidance."""
        with TempRepo() as repo:
            repo.write("app/VERSION", "1.0.0")
            root_sha = repo.commit("app/VERSION", message="init")

            repo.write("app/backend/foo.py", "# foo")
            head_sha = repo.commit("app/backend/foo.py", message="feat: no bump")

            result = _run_script_result(
                repo.path,
                "--base",
                root_sha,
                "--head",
                head_sha,
                "--base-ref",
                "main",
                "--aggregate",
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn(f"{root_sha[:12]}...{head_sha[:12]}", result.stderr)
            self.assertIn("git fetch origin main && git rebase origin/main", result.stderr)
            self.assertNotIn("origin/release/v26-beta", result.stderr)


if __name__ == "__main__":
    unittest.main()
