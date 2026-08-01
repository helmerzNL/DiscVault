"""Tests for app/scripts/prune_landed_branches.py."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "prune_landed_branches.py"
)


def _git_available() -> bool:
    try:
        subprocess.check_output(["git", "--version"], stderr=subprocess.DEVNULL)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


class TempRepo:
    """A throwaway clone plus its origin, so remote-tracking refs behave for real.

    ``classify()`` reads ``origin/<branch>`` refs, so a bare single repository is not
    enough — the tests need an actual remote to fetch from.
    """

    def __init__(self) -> None:
        self.root = tempfile.mkdtemp(prefix="prune-branches-")
        self.origin = os.path.join(self.root, "origin.git")
        self.path = os.path.join(self.root, "work")

    def __enter__(self) -> "TempRepo":
        subprocess.check_call(
            ["git", "init", "--bare", "-b", "release/v26-beta", self.origin],
            stdout=subprocess.DEVNULL,
        )
        subprocess.check_call(
            ["git", "clone", self.origin, self.path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for key, value in (
            ("user.email", "tester@example.com"),
            ("user.name", "Tester"),
            ("commit.gpgsign", "false"),
        ):
            self.git("config", key, value)
        return self

    def __exit__(self, *exc_info: object) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def git(self, *args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=self.path, text=True, stderr=subprocess.DEVNULL
        ).strip()

    def write(self, relative: str, content: str) -> None:
        target = os.path.join(self.path, relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(content)

    def commit(self, message: str, **files: str) -> str:
        for name, content in files.items():
            self.write(name.replace("__", "/"), content)
        self.git("add", "-A")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD")

    def publish(self, *branches: str) -> None:
        self.git("push", "--quiet", "origin", *branches)
        self.git("fetch", "--quiet", "origin")


def _run(repo: TempRepo, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, SCRIPT, "--base", "release/v26-beta", *extra],
        cwd=repo.path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "GITHUB_TOKEN": "", "GITHUB_REPOSITORY": ""},
    )


def _report(repo: TempRepo, *extra: str) -> dict[str, dict]:
    result = _run(repo, "--json", "--min-age-days", "0", *extra)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    return {row["branch"]: row for row in payload["branches"]}


@unittest.skipUnless(_git_available(), "git is required")
class ClassificationTests(unittest.TestCase):
    def test_branch_with_unmerged_work_is_kept(self) -> None:
        with TempRepo() as repo:
            repo.commit("base", app__backend__thing_py="one\n")
            repo.publish("release/v26-beta")

            repo.git("checkout", "-q", "-b", "feat/pending")
            repo.commit("feature", app__backend__thing_py="one\ntwo\n")
            repo.publish("feat/pending")

            row = _report(repo)["feat/pending"]
            self.assertEqual(row["reason"], "keep")
            self.assertFalse(row["deletable"])

    def test_fully_merged_branch_is_landed(self) -> None:
        with TempRepo() as repo:
            repo.commit("base", app__backend__thing_py="one\n")
            repo.publish("release/v26-beta")

            repo.git("checkout", "-q", "-b", "feat/merged")
            repo.commit("feature", app__backend__thing_py="one\ntwo\n")
            repo.publish("feat/merged")
            repo.git("checkout", "-q", "release/v26-beta")
            repo.git("merge", "--quiet", "--no-ff", "-m", "merge", "feat/merged")
            repo.publish("release/v26-beta")

            self.assertEqual(_report(repo)["feat/merged"]["reason"], "merged")

    def test_cherry_picked_branch_is_patch_equivalent(self) -> None:
        """The squash/cherry-pick case: same change, different commit."""

        with TempRepo() as repo:
            repo.commit("base", app__backend__thing_py="one\n")
            repo.publish("release/v26-beta")

            repo.git("checkout", "-q", "-b", "feat/picked")
            picked = repo.commit("feature", app__backend__thing_py="one\ntwo\n")
            repo.publish("feat/picked")

            repo.git("checkout", "-q", "release/v26-beta")
            # Move the base on first: cherry-picking straight onto the shared parent
            # reproduces the branch commit byte for byte, which would make the branch
            # merged rather than patch-equivalent.
            repo.commit("unrelated base work", docs__note_md="x\n")
            repo.git("cherry-pick", picked)
            repo.publish("release/v26-beta")

            self.assertEqual(_report(repo)["feat/picked"]["reason"], "patch-equivalent")

    def test_branch_carrying_only_a_version_bump_is_landed(self) -> None:
        with TempRepo() as repo:
            repo.commit("base", app__VERSION="26.7.1\n")
            repo.publish("release/v26-beta")

            repo.git("checkout", "-q", "-b", "chore/bump")
            repo.commit("bump", app__VERSION="26.7.2\n")
            repo.publish("chore/bump")

            self.assertEqual(_report(repo)["chore/bump"]["reason"], "version-only")

    def test_branch_whose_files_match_the_base_is_landed(self) -> None:
        """Re-applied by hand on the base: different commit, identical content."""

        with TempRepo() as repo:
            repo.commit("base", app__backend__thing_py="one\n")
            repo.publish("release/v26-beta")

            repo.git("checkout", "-q", "-b", "fix/reapplied")
            repo.commit("fix", app__backend__thing_py="one\nfixed\n")
            repo.publish("fix/reapplied")

            repo.git("checkout", "-q", "release/v26-beta")
            # Reach the same end state by a different route, so no single commit on the
            # base is patch-equivalent to the branch's — only the content matches.
            repo.commit("first attempt", app__backend__thing_py="one\nbroken\n")
            repo.commit("corrected", app__backend__thing_py="one\nfixed\n")
            repo.commit("unrelated later work", app__backend__other_py="x\n")
            repo.publish("release/v26-beta")

            self.assertEqual(_report(repo)["fix/reapplied"]["reason"], "content-identical")

    def test_branch_with_no_common_ancestor_is_kept(self) -> None:
        """An orphaned history has no fork point, so nothing about it can be concluded."""

        with TempRepo() as repo:
            repo.commit("base", app__backend__thing_py="one\n")
            repo.publish("release/v26-beta")

            repo.git("checkout", "-q", "--orphan", "import/legacy")
            repo.git("rm", "-rq", "--cached", ".")
            repo.commit("imported history", app__backend__imported_py="x\n")
            repo.publish("import/legacy")

            row = _report(repo)["import/legacy"]
            self.assertEqual(row["reason"], "keep")
            self.assertIn("no common ancestor", row["detail"])

    def test_permanent_branches_are_never_candidates(self) -> None:
        with TempRepo() as repo:
            repo.commit("base", app__backend__thing_py="one\n")
            repo.publish("release/v26-beta")
            repo.git("branch", "main")
            repo.git("branch", "legacy")
            repo.publish("main", "legacy")

            report = _report(repo)
            self.assertNotIn("main", report)
            self.assertNotIn("legacy", report)
            self.assertNotIn("release/v26-beta", report)

    def test_keep_flag_protects_an_extra_branch(self) -> None:
        with TempRepo() as repo:
            repo.commit("base", app__backend__thing_py="one\n")
            repo.publish("release/v26-beta")
            repo.git("checkout", "-q", "-b", "claude/session")
            repo.commit("bump", app__VERSION="1\n")
            repo.publish("claude/session")

            self.assertIn("claude/session", _report(repo))
            self.assertNotIn("claude/session", _report(repo, "--keep", "claude/session"))


@unittest.skipUnless(_git_available(), "git is required")
class GuardTests(unittest.TestCase):
    def test_recent_branch_is_held_back(self) -> None:
        with TempRepo() as repo:
            repo.commit("base", app__VERSION="26.7.1\n")
            repo.publish("release/v26-beta")
            repo.git("checkout", "-q", "-b", "chore/fresh")
            repo.commit("bump", app__VERSION="26.7.2\n")
            repo.publish("chore/fresh")

            result = _run(repo, "--json", "--min-age-days", "14")
            row = {r["branch"]: r for r in json.loads(result.stdout)["branches"]}["chore/fresh"]
            self.assertEqual(row["reason"], "version-only")
            self.assertTrue(row["tooYoung"])
            self.assertFalse(row["deletable"])

    def test_unverifiable_open_prs_block_deletion(self) -> None:
        """No token means the open-PR guard cannot run, so --apply must refuse."""

        with TempRepo() as repo:
            repo.commit("base", app__VERSION="26.7.1\n")
            repo.publish("release/v26-beta")
            repo.git("checkout", "-q", "-b", "chore/stale")
            repo.commit("bump", app__VERSION="26.7.2\n")
            repo.publish("chore/stale")

            result = _run(repo, "--min-age-days", "0", "--apply")
            self.assertEqual(result.returncode, 1)
            self.assertIn("Refusing to delete", result.stderr)
            self.assertIn("chore/stale", repo.git("branch", "-r"))

    def test_dry_run_never_deletes(self) -> None:
        with TempRepo() as repo:
            repo.commit("base", app__VERSION="26.7.1\n")
            repo.publish("release/v26-beta")
            repo.git("checkout", "-q", "-b", "chore/stale")
            repo.commit("bump", app__VERSION="26.7.2\n")
            repo.publish("chore/stale")

            result = _run(repo, "--min-age-days", "0")
            self.assertEqual(result.returncode, 0)
            self.assertIn("Dry run: nothing was deleted", result.stdout)
            self.assertIn("chore/stale", repo.git("branch", "-r"))


if __name__ == "__main__":
    unittest.main()
