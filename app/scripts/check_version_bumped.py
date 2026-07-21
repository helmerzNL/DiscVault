#!/usr/bin/env python3
"""Fail commits that change app/runtime code without bumping app/VERSION.

In ``--aggregate`` mode this does more than check that ``app/VERSION`` merely
appears in the diff: it reads the actual value at HEAD and at the *real* base
(preferring a live ``--base-ref`` branch tip over a possibly-stale recorded
base sha) and requires HEAD's value to be valid ``MAJOR.MINOR.PATCH`` semver
that is strictly greater than the base's value. This catches the class of bug
where two branches independently bump to the same next version and the first
one to land silently invalidates the second (see #340/#341): a stale PR base
sha can make a redundant bump look legitimate even though the target branch
already carries that exact version.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys


VERSION_FILE = "app/VERSION"
PROTECTED_PREFIXES = (
    ".github/workflows/",
    "app/Dockerfile",
    "app/backend/",
    "app/deploy/",
    "app/docker-compose",
    "app/frontend/",
    "app/mcp-server/",
    "app/scripts/",
    "dist/plugins/",
)
IGNORED_SUFFIXES = (".md", ".txt")
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL).strip()


def commit_sha(ref: str) -> str | None:
    """Resolve ``ref`` to a commit sha, or None if it does not exist."""
    try:
        return git("rev-parse", "--verify", f"{ref}^{{commit}}")
    except subprocess.CalledProcessError:
        return None


def valid_commit(ref: str) -> bool:
    return commit_sha(ref) is not None


def parse_semver(value: str) -> tuple[int, int, int] | None:
    match = SEMVER_RE.match(value.strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def read_version_at(ref: str) -> str | None:
    """Return the trimmed app/VERSION contents at ``ref`` (or the worktree)."""
    if ref == "WORKTREE":
        try:
            with open(VERSION_FILE, "r", encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return None
    try:
        return git("show", f"{ref}:{VERSION_FILE}").strip()
    except subprocess.CalledProcessError:
        return None


def resolve_actual_base(base: str, base_ref: str | None) -> str:
    """Return the commit to treat as the authoritative base for version checks.

    ``base`` is whatever the CI event handed us (a PR's recorded base sha, or
    the previous push sha). For pull_request events that sha can be stale if
    the target branch advanced after the PR was opened/last synced (exactly
    what happened between PR #340 and #341). When ``base_ref`` (a branch name,
    e.g. ``github.event.pull_request.base.ref``) is supplied, prefer its
    current tip whenever it is strictly ahead of ``base`` so the check
    reflects reality instead of a snapshot in time.
    """
    base_sha = commit_sha(base)
    if not base_ref:
        return base_sha or base

    candidate_sha = commit_sha(f"origin/{base_ref}") or commit_sha(base_ref)
    if candidate_sha is None:
        # Best-effort fetch; never let network/auth issues break the guard.
        subprocess.run(
            ["git", "fetch", "--quiet", "--no-tags", "origin", base_ref],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        candidate_sha = commit_sha(f"origin/{base_ref}") or commit_sha(base_ref)

    if not candidate_sha:
        return base_sha or base
    if not base_sha:
        return candidate_sha
    if candidate_sha == base_sha:
        return base_sha
    try:
        git("merge-base", "--is-ancestor", base_sha, candidate_sha)
    except subprocess.CalledProcessError:
        # candidate is not a descendant of base (e.g. diverged history) -
        # trust the explicitly provided base instead of guessing.
        return base_sha
    return candidate_sha


def changed_files_for_commit(commit: str) -> list[str]:
    return [
        line.strip().replace("\\", "/")
        for line in git("diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines()
        if line.strip()
    ]


def changed_files_for_worktree(base: str) -> list[str]:
    tracked = [
        line.strip().replace("\\", "/")
        for line in git("diff", "--name-only", base, "--").splitlines()
        if line.strip()
    ]
    untracked = [
        line.strip().replace("\\", "/")
        for line in git("ls-files", "--others", "--exclude-standard").splitlines()
        if line.strip()
    ]
    return tracked + untracked


def commit_list(base: str, head: str) -> list[str]:
    if head == "WORKTREE":
        return ["WORKTREE"]
    if not valid_commit(head):
        raise SystemExit(f"Unknown head ref: {head}")
    if not valid_commit(base):
        parent = f"{head}~1"
        if valid_commit(parent):
            base = parent
        else:
            return [head]
    commits = git("rev-list", "--reverse", f"{base}..{head}").splitlines()
    return commits or [head]


def requires_version_bump(files: list[str]) -> bool:
    for path in files:
        if path == VERSION_FILE:
            continue
        if path.endswith(IGNORED_SUFFIXES):
            continue
        if path.startswith(PROTECTED_PREFIXES):
            return True
    return False


def changed_files_for_range(base: str, head: str) -> list[str]:
    return [
        line.strip().replace("\\", "/")
        for line in git("diff", "--name-only", f"{base}...{head}").splitlines()
        if line.strip()
    ]


def validate_version_progression(base: str, head: str, base_ref: str | None) -> list[tuple[str, str]]:
    """Validate that HEAD's app/VERSION is valid semver and strictly ahead of base.

    Returns a list of ``(label, reason)`` failures. An empty list means the
    version progression is acceptable.
    """
    label = f"{base[:12]}...{head if head == 'WORKTREE' else head[:12]}"

    head_version = read_version_at(head)
    if head_version is None:
        return [(label, f"could not read {VERSION_FILE} at HEAD")]

    head_semver = parse_semver(head_version)
    if head_semver is None:
        return [
            (
                label,
                f"{VERSION_FILE} at HEAD is '{head_version}', which is not valid MAJOR.MINOR.PATCH semver",
            )
        ]

    actual_base = resolve_actual_base(base, base_ref)
    base_version = read_version_at(actual_base)
    if base_version is None:
        # No comparable baseline (e.g. root commit) - semver validity is enough.
        return []

    base_semver = parse_semver(base_version)
    if base_semver is not None and head_semver <= base_semver:
        return [
            (
                label,
                f"{VERSION_FILE} {head_version} is not strictly greater than the actual base "
                f"{base_version} ({actual_base[:12]}) - this is stale/redundant, not a real bump",
            )
        ]
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="HEAD~1")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument(
        "--base-ref",
        default=None,
        help=(
            "Branch name of the true merge target (e.g. github.event.pull_request.base.ref). "
            "Used to detect a base sha that went stale because the target branch advanced "
            "after the PR was opened/last synced."
        ),
    )
    parser.add_argument("--aggregate", action="store_true", default=False)
    args = parser.parse_args()

    failures: list[tuple[str, str]] = []

    if args.aggregate:
        base = args.base
        head = args.head
        if not valid_commit(base):
            if valid_commit(head):
                files = changed_files_for_commit(head)
            else:
                print("DiscVault version guard ok.")
                return 0
        else:
            files = changed_files_for_range(base, head)
        if requires_version_bump(files):
            failures.extend(validate_version_progression(base, head, args.base_ref))
    else:
        for commit in commit_list(args.base, args.head):
            files = changed_files_for_worktree(args.base) if commit == "WORKTREE" else changed_files_for_commit(commit)
            if requires_version_bump(files) and VERSION_FILE not in files:
                failures.append((commit, f"{VERSION_FILE} not touched by this commit"))

    if failures:
        print("DiscVault version guard failed.", file=sys.stderr)
        print(f"Every app/runtime change must bump {VERSION_FILE} to a new, valid semver value.", file=sys.stderr)
        for commit, reason in failures:
            label = commit if commit == "WORKTREE" or "..." in commit else commit[:12]
            print(f"- {label}: {reason}", file=sys.stderr)
        print("", file=sys.stderr)
        target_branch = args.base_ref or "release/v26-beta"
        print("To fix:", file=sys.stderr)
        print(f"  1. Update/rebase from {target_branch} so app/VERSION reflects the real base:", file=sys.stderr)
        print(f"     git fetch origin {target_branch} && git rebase origin/{target_branch}", file=sys.stderr)
        print("  2. Re-run the bump helper so app/VERSION moves strictly past the (new) base:", file=sys.stderr)
        print("     python app/scripts/bump_version.py", file=sys.stderr)
        return 1

    print("DiscVault version guard ok.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
