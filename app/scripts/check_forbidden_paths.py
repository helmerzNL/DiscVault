#!/usr/bin/env python3
"""Reject native iOS artifacts that were purged from the DiscVault repository."""

from __future__ import annotations

import argparse
import subprocess
import sys


FORBIDDEN_DIRECTORY = "ios"
FORBIDDEN_FILE = "iosnativeapp.patch"


def normalize_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def is_forbidden_path(path: str) -> bool:
    normalized = normalize_path(path).casefold()
    return (
        normalized == FORBIDDEN_DIRECTORY
        or normalized.startswith(f"{FORBIDDEN_DIRECTORY}/")
        or normalized == FORBIDDEN_FILE
    )


def git_paths(*args: str) -> list[str]:
    output = subprocess.check_output(
        ["git", *args],
        stderr=subprocess.PIPE,
    )
    return [
        path.decode("utf-8", "surrogateescape")
        for path in output.split(b"\0")
        if path
    ]


def staged_paths() -> list[str]:
    return git_paths(
        "diff",
        "--cached",
        "--name-only",
        "--diff-filter=ACMR",
        "-z",
    )


def tree_paths(ref: str) -> list[str]:
    return git_paths("ls-tree", "-r", "--name-only", "-z", ref)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--staged",
        action="store_true",
        help="Inspect paths added, copied, modified, or renamed in the index.",
    )
    source.add_argument(
        "--tree",
        metavar="REF",
        help="Inspect every tracked path in the given Git tree.",
    )
    args = parser.parse_args()

    try:
        paths = staged_paths() if args.staged else tree_paths(args.tree)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", "replace").strip()
        print("DiscVault forbidden-path guard could not inspect Git.", file=sys.stderr)
        if detail:
            print(detail, file=sys.stderr)
        return 2

    forbidden = sorted(
        {normalize_path(path) for path in paths if is_forbidden_path(path)},
        key=str.casefold,
    )
    if not forbidden:
        print("DiscVault forbidden-path guard ok.")
        return 0

    print("DiscVault forbidden-path guard failed.", file=sys.stderr)
    print(
        "Native iOS artifacts were purged and must remain outside this repository.",
        file=sys.stderr,
    )
    for path in forbidden:
        print(f"- forbidden tracked path: {path}", file=sys.stderr)
    print(
        "Recovery: move the files outside the repository, then run "
        "'git rm -r --cached --ignore-unmatch ios iosnativeapp.patch'.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
