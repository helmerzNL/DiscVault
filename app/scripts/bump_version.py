#!/usr/bin/env python3
"""Bump app/VERSION so every app/runtime change satisfies the DiscVault version guard.

The companion guard ``check_version_bumped.py`` fails any commit/PR that touches a
protected app/runtime path without changing ``app/VERSION``. This helper bumps the
patch component automatically and stages the file. It is safe to run manually and is
wired up as a ``pre-commit`` hook (see ``.githooks/pre-commit``) so routine commits do
not need to remember the bump.

Behaviour:
* Looks at the staged change set by default (``--staged``); pass ``--working`` to
  inspect tracked + untracked working-tree changes instead.
* Only bumps when a protected path changed and ``app/VERSION`` itself was not already
  modified, so an explicit manual bump is never doubled.
* Checks the target branch (``--base-ref``, default ``release/v26-beta``) and ensures
  the new version is strictly greater than both the local file and the target branch.
  This prevents the version-collision scenario where two parallel PRs independently
  bump to the same value (see PR #473/#474).
* Never raises on routine problems; it simply exits 0 without touching anything so it
  can never block a commit.
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
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(.*)$")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL).strip()


def _normalize(lines: list[str]) -> list[str]:
    return [line.strip().replace("\\", "/") for line in lines if line.strip()]


def staged_files() -> list[str]:
    return _normalize(git("diff", "--cached", "--name-only").splitlines())


def working_files() -> list[str]:
    tracked = _normalize(git("diff", "--name-only").splitlines())
    untracked = _normalize(git("ls-files", "--others", "--exclude-standard").splitlines())
    return tracked + untracked


def requires_version_bump(files: list[str]) -> bool:
    for path in files:
        if path == VERSION_FILE:
            continue
        if path.endswith(IGNORED_SUFFIXES):
            continue
        if path.startswith(PROTECTED_PREFIXES):
            return True
    return False


def bump_patch(version: str) -> str | None:
    match = SEMVER_RE.match(version.strip())
    if not match:
        return None
    major, minor, patch, suffix = match.groups()
    return f"{major}.{minor}.{int(patch) + 1}{suffix}"


def _parse_semver(version: str) -> tuple[int, int, int] | None:
    match = SEMVER_RE.match(version.strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _read_base_ref_version(base_ref: str) -> str | None:
    """Return the VERSION at origin/<base_ref> or <base_ref>, fetching if needed.

    Returns None when the ref cannot be resolved or does not carry app/VERSION.
    Network/auth errors are silently swallowed so they can never block a commit.
    """
    for candidate in (f"origin/{base_ref}", base_ref):
        try:
            return subprocess.check_output(
                ["git", "show", f"{candidate}:{VERSION_FILE}"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except subprocess.CalledProcessError:
            pass
    # Best-effort fetch and retry once.
    subprocess.run(
        ["git", "fetch", "--quiet", "--no-tags", "origin", base_ref],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for candidate in (f"origin/{base_ref}", base_ref):
        try:
            return subprocess.check_output(
                ["git", "show", f"{candidate}:{VERSION_FILE}"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except subprocess.CalledProcessError:
            pass
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--working", action="store_true", help="Inspect working-tree changes instead of the staged set.")
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Bump without inspecting any change set. This is the mode CI uses on "
            "release/v26-beta, where there is nothing staged: the workflow already "
            "decided a bump is due because the push touched app/**, and re-deriving "
            "that from an empty index would silently skip the bump."
        ),
    )
    parser.add_argument("--stage", dest="stage", action="store_true", default=True, help="git add the bumped file (default).")
    parser.add_argument("--no-stage", dest="stage", action="store_false", help="Bump the file without staging it.")
    parser.add_argument(
        "--base-ref",
        default="release/v26-beta",
        metavar="REF",
        help=(
            "Branch whose VERSION the bump must exceed (default: release/v26-beta). "
            "When two parallel PRs independently bump to the same value and one lands "
            "first, the local file version alone is stale; this flag ensures the new "
            "version is always strictly greater than the target branch tip too. "
            "Pass an empty string to skip the check."
        ),
    )
    args = parser.parse_args()

    if not args.force:
        try:
            files = working_files() if args.working else staged_files()
        except subprocess.CalledProcessError:
            return 0

        if not files or not requires_version_bump(files):
            return 0
        if VERSION_FILE in files:
            # An explicit bump is already part of this change set.
            return 0

    try:
        with open(VERSION_FILE, "rb") as handle:
            raw = handle.read()
    except OSError:
        return 0

    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    current = raw.decode("utf-8", "replace").strip()
    bumped = bump_patch(current)
    if not bumped:
        sys.stderr.write(f"bump_version: could not parse '{current}', leaving app/VERSION untouched\n")
        return 0

    # If the target branch has already advanced past our local bump, start from
    # its version instead so we stay strictly ahead.  This is the fix for the
    # version-collision scenario where two parallel PRs both bump to the same
    # value: whichever one runs bump_version.py after the other has landed will
    # now automatically produce a version that exceeds the branch tip.
    if args.base_ref:
        base_ver = _read_base_ref_version(args.base_ref)
        if base_ver:
            base_semver = _parse_semver(base_ver)
            bumped_semver = _parse_semver(bumped)
            if base_semver and bumped_semver and bumped_semver <= base_semver:
                alt = bump_patch(base_ver)
                if alt:
                    sys.stderr.write(
                        f"bump_version: {args.base_ref} is already at {base_ver}; "
                        f"bumping past that: {current} -> {alt}\n"
                    )
                    bumped = alt

    with open(VERSION_FILE, "wb") as handle:
        handle.write(bumped.encode("utf-8") + newline)

    if args.stage:
        subprocess.call(["git", "add", VERSION_FILE])

    sys.stderr.write(f"bump_version: app/VERSION {current} -> {bumped}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
