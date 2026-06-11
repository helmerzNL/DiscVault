#!/usr/bin/env python3
"""Fail commits that change app/runtime code without bumping app/VERSION."""

from __future__ import annotations

import argparse
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


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL).strip()


def valid_commit(ref: str) -> bool:
    try:
        git("rev-parse", "--verify", f"{ref}^{{commit}}")
        return True
    except subprocess.CalledProcessError:
        return False


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="HEAD~1")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--aggregate", action="store_true", default=False)
    args = parser.parse_args()

    failures: list[str] = []

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
        if requires_version_bump(files) and VERSION_FILE not in files:
            label = f"{base[:12]}...{head[:12]}"
            failures.append(label)
    else:
        for commit in commit_list(args.base, args.head):
            files = changed_files_for_worktree(args.base) if commit == "WORKTREE" else changed_files_for_commit(commit)
            if requires_version_bump(files) and VERSION_FILE not in files:
                failures.append(commit)

    if failures:
        print("DiscVault version guard failed.", file=sys.stderr)
        print(f"Every app/runtime commit must update {VERSION_FILE}.", file=sys.stderr)
        for commit in failures:
            label = commit if commit == "WORKTREE" else commit[:12]
            print(f"- missing version bump: {label}", file=sys.stderr)
        return 1

    print("DiscVault version guard ok.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
