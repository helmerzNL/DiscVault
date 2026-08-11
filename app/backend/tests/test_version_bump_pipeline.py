"""Where the version bump happens, and where it must not.

`app/VERSION` used to be bumped inside every pull request. That is only valid
against the base as it stood when the bump was written, and GitHub does not
re-run a check when the base moves -- so three pull requests merging within 26
seconds all bumped to the same value, two guards went red on `release/v26-beta`,
and no image was published for beta's head (#573, after #473/#474 and
#516/#517).

The bump now happens once, on beta, after the merge. That turns a race between
authors into no race at all, but it only holds while three things stay true:
a pull request may not carry a bump, the bump job must run before the build in
the same workflow run, and the build must be built from the bumped commit.

Each of those is invisible in normal operation and each fails silently: a
missing bump just means the next image quietly carries the previous version
number. So they are pinned here, in the same source-reading idiom the other
workflow tests use.
"""

import os
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
WORKFLOW_DIR = os.path.join(REPO_ROOT, ".github", "workflows")
SCRIPTS_DIR = os.path.join(REPO_ROOT, "app", "scripts")


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class WorkflowsParseTests(unittest.TestCase):
    """Every workflow file must be loadable YAML.

    The tests below read the workflows as text, which proves the wiring is
    written but says nothing about whether GitHub can load the file. It cannot
    load one whose `if:` expression is an unquoted scalar containing
    "chore: bump app/VERSION" -- a colon followed by a space turns the rest into
    a nested mapping. GitHub reports that as a run with **zero jobs**, named
    after the file path rather than the workflow, so it reads like an
    infrastructure hiccup rather than a syntax error in the diff that caused it.
    """

    def test_every_workflow_is_valid_yaml(self):
        import yaml

        names = sorted(n for n in os.listdir(WORKFLOW_DIR) if n.endswith((".yml", ".yaml")))
        self.assertTrue(names, "no workflow files found")
        for name in names:
            with self.subTest(workflow=name):
                yaml.safe_load(read(os.path.join(WORKFLOW_DIR, name)))


class DockerPublishWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = read(os.path.join(WORKFLOW_DIR, "docker-publish.yml"))

    def test_the_bump_runs_only_on_a_push_to_beta(self):
        """Not on main -- main receives promotion merges that already carry beta's
        bump commits, and a second bump there would make the two branches diverge
        on the one file promotions conflict on."""
        self.assertIn(
            "if: \"github.event_name == 'push' && github.ref == 'refs/heads/release/v26-beta'",
            self.source,
        )

    def test_the_build_is_built_from_the_bumped_commit(self):
        """The image takes BUILD_VERSION from `app/VERSION` in its checkout. Building
        `github.sha` would tag every beta image with the version from before the
        bump -- a number the previous image already carries."""
        self.assertIn("ref: ${{ needs.version-bump.outputs.sha || github.sha }}", self.source)
        self.assertIn("needs: [version-guard, version-bump]", self.source)

    def test_the_build_survives_one_of_its_two_gates_being_skipped(self):
        """`version-guard` and `version-bump` are mutually exclusive by event, so
        the default all-must-succeed rule would skip the build on every event."""
        self.assertIn(
            "if: \"${{ !cancelled() && needs.version-guard.result != 'failure' "
            "&& needs.version-bump.result != 'failure' "
            "&& !(github.event_name == 'push' "
            "&& startsWith(github.event.head_commit.message, 'chore: bump app/VERSION')) }}",
            self.source,
        )

    def test_the_bump_job_is_serialised_and_never_forces(self):
        """Two merges seconds apart give two bump jobs. Without a concurrency group
        the race moves from two humans to two runners rather than disappearing --
        and `Block force pushes` is on for every branch, so a rejected push may
        only be retried from the new tip."""
        self.assertIn("group: beta-version-bump", self.source)
        self.assertIn("cancel-in-progress: false", self.source)
        bump = self.source[self.source.index("  version-bump:"):self.source.index("  build-and-push:")]
        self.assertIn("git push origin release/v26-beta", bump)
        self.assertNotIn("--force", bump.replace("--force --no-stage", ""))
        self.assertNotIn("+release/v26-beta", bump)
        # Restarting from the new tip, not rebasing: one line in one file cannot
        # conflict when it is recomputed against the base it will land on.
        self.assertIn("git checkout --quiet -B release/v26-beta origin/release/v26-beta", bump)

    def test_the_bump_commit_cannot_retrigger_the_workflow(self):
        """A push made with GITHUB_TOKEN does not start a new run -- that is the
        real guarantee. A human replaying the commit is covered by a condition on
        this workflow's own jobs rather than by `[skip ci]` in the message.

        The marker cannot come back. GitHub honours it for `pull_request` as well
        as `push`, and beta's tip is always a bump commit, so it skipped every
        check on every promotion PR opened from beta -- and a required check that
        never reports blocks the merge exactly like a red one (#621)."""
        self.assertNotIn("[skip ci]", self.source.replace("`[skip ci]`", ""))
        self.assertIn('git commit --quiet -m "chore: bump app/VERSION to ${version}"', self.source)
        self.assertIn("permissions:\n      contents: write", self.source)
        # Every job of this workflow stands down on a replayed bump commit. A skipped
        # need is not a failed one, so the build needs the clause too or it publishes
        # the replay after both gates skip.
        replay = "startsWith(github.event.head_commit.message, 'chore: bump app/VERSION')"
        self.assertEqual(3, self.source.count(replay))

    def test_the_guard_job_stands_down_on_beta_pushes(self):
        """It asks whether the push bumped. On beta nothing does any more, so
        leaving it enabled would fail every merge by construction."""
        self.assertIn(
            "if: \"(github.ref != 'refs/heads/release/v26-beta' || github.event_name != 'push')",
            self.source,
        )


class VersionGuardWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = read(os.path.join(WORKFLOW_DIR, "version-guard.yml"))

    def test_a_pull_request_is_checked_for_the_opposite_thing(self):
        self.assertIn("--forbid-change", self.source)
        self.assertIn("if: github.event_name == 'pull_request'", self.source)

    def test_a_promotion_into_main_is_exempt_from_the_forbid_rule(self):
        """"Leave app/VERSION alone" is a rule for pull requests into beta, where CI
        applies the bump after the merge. A promotion into main exists to carry those
        bumps to production, so its diff always contains the file -- applying the rule
        there refused every promotion PR (#621)."""
        self.assertIn(
            "if: github.event_name == 'pull_request' "
            "&& github.event.pull_request.base.ref != 'main'",
            self.source,
        )

    def test_a_promotion_must_still_move_the_version_forward(self):
        """Exempt from "do not touch" is not exempt from checking: a promotion that
        somehow carried an equal or lower version would publish `:stable` under a
        number another image already holds."""
        self.assertIn(
            "if: github.event_name == 'pull_request' "
            "&& github.event.pull_request.base.ref == 'main'",
            self.source,
        )
        promotion = self.source[self.source.index("Check the promotion carries a newer version"):]
        self.assertIn("--aggregate", promotion)
        self.assertIn("--base-ref", promotion)

    def test_main_keeps_the_progression_check(self):
        """A promotion carries beta's bump commits. If one is ever lost, main would
        publish `:stable` under a version another image already holds."""
        self.assertIn(
            "if: github.event_name != 'pull_request' && github.ref != 'refs/heads/release/v26-beta'",
            self.source,
        )
        self.assertIn("--aggregate", self.source)

    def test_the_pre_commit_hook_no_longer_bumps(self):
        """It would write the one file a pull request is now forbidden to touch,
        so every commit would fail the check the hook exists to satisfy."""
        hook = read(os.path.join(REPO_ROOT, ".githooks", "pre-commit"))
        self.assertNotIn("bump_version.py", hook.split("# Enable once")[-1])
        self.assertIn("check_forbidden_paths.py", hook)


class ForbidChangeModeTests(unittest.TestCase):
    """The guard's new mode, run against a real throwaway repository.

    Reading the workflow proves it is wired up; only running it proves it says
    no.
    """

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="version-guard-")
        self.addCleanup(__import__("shutil").rmtree, self.repo, True)
        self.git("init", "--quiet", "-b", "main")
        self.git("config", "user.email", "t@example.test")
        self.git("config", "user.name", "T")
        os.makedirs(os.path.join(self.repo, "app", "backend"))
        self.write("app/VERSION", "26.8.40\n")
        self.write("app/backend/thing.py", "x = 1\n")
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", "base")
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", self.repo, *args], text=True)

    def write(self, relative, text):
        path = os.path.join(self.repo, relative)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def run_guard(self, *args):
        return subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "check_version_bumped.py"), *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )

    def test_a_code_change_without_a_bump_is_now_accepted(self):
        self.write("app/backend/thing.py", "x = 2\n")
        self.git("commit", "--quiet", "-am", "change")
        head = self.git("rev-parse", "HEAD").strip()
        result = self.run_guard("--base", self.base, "--head", head, "--forbid-change")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_hand_written_bump_is_refused(self):
        self.write("app/backend/thing.py", "x = 2\n")
        self.write("app/VERSION", "26.8.41\n")
        self.git("commit", "--quiet", "-am", "change and bump")
        head = self.git("rev-parse", "HEAD").strip()
        result = self.run_guard("--base", self.base, "--head", head, "--forbid-change")
        self.assertEqual(result.returncode, 1)
        self.assertIn("must not be changed in a pull request", result.stderr)

    def test_a_bump_hidden_behind_a_later_revert_is_still_refused(self):
        """Two commits that cancel out leave `app/VERSION` out of the range diff.
        Checking the range rather than each commit is deliberate: the merged result
        is what lands on beta, and a bump that is not in it cannot collide."""
        self.write("app/VERSION", "26.8.41\n")
        self.git("commit", "--quiet", "-am", "bump")
        self.write("app/VERSION", "26.8.40\n")
        self.git("commit", "--quiet", "-am", "unbump")
        head = self.git("rev-parse", "HEAD").strip()
        result = self.run_guard("--base", self.base, "--head", head, "--forbid-change")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_forced_bump_moves_the_file_with_nothing_staged(self):
        """CI checks out beta's tip, where nothing is staged and no change set can
        be derived. The default mode would read that as 'no bump due' and skip."""
        result = subprocess.run(
            [
                sys.executable,
                os.path.join(SCRIPTS_DIR, "bump_version.py"),
                "--force",
                "--no-stage",
                "--base-ref",
                "",
            ],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(os.path.join(self.repo, "app", "VERSION"), encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), "26.8.41")

    def test_without_force_an_empty_change_set_bumps_nothing(self):
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "bump_version.py"), "--base-ref", ""],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(os.path.join(self.repo, "app", "VERSION"), encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), "26.8.40")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
