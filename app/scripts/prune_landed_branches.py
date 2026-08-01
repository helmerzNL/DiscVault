#!/usr/bin/env python3
"""Report — and optionally delete — remote branches whose work already reached the base.

Stale branches pile up because a branch usually lands through a *different* commit than
the one it carries: a squash, a cherry-pick, or a re-implementation on top of newer beta.
"Commits ahead of beta" therefore stays greater than zero long after the work itself has
shipped, which makes that number useless for deciding what is safe to prune.

This tool answers the real question — *is anything on this branch still missing from the
base?* — with three checks, each of which only ever declares a branch landed when it is
certain:

1. **Merged.** The branch has no commits the base lacks.
2. **Patch-equivalent.** ``git cherry`` finds an equivalent commit on the base for every
   commit the branch carries, so a squash or cherry-pick already delivered them.
3. **Content-identical.** Every file the branch touched (``app/VERSION`` excluded, since a
   bare version bump carries no work) is byte-identical on the base.

Anything else is reported as ``keep``. That asymmetry is deliberate: a branch whose work
was re-implemented differently on the base still reads as ``keep`` here, because the tool
cannot tell that apart from genuinely unmerged work without human judgement. Being wrong in
that direction costs a branch that lingers; being wrong in the other costs code.

Three guards apply on top, and each one **fails closed** — a branch it cannot clear is kept:

* permanent branches (``main``, ``release/v26-beta``, ``legacy``) are never candidates;
* a branch with an **open pull request** is never deleted;
* a branch touched more recently than ``--min-age-days`` is never deleted, because session
  branches are reused across several PRs and an active one must survive.

Deletion requires ``--apply``; the default run only reports. Without a usable GitHub token
the open-PR guard cannot be evaluated, so ``--apply`` refuses to delete anything at all.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone


VERSION_FILE = "app/VERSION"
PERMANENT_BRANCHES = ("main", "release/v26-beta", "legacy")
API_ROOT = "https://api.github.com"

MERGED = "merged"
PATCH_EQUIVALENT = "patch-equivalent"
VERSION_ONLY = "version-only"
CONTENT_IDENTICAL = "content-identical"
KEEP = "keep"

LANDED_REASONS = (MERGED, PATCH_EQUIVALENT, VERSION_ONLY, CONTENT_IDENTICAL)


class GitError(RuntimeError):
    """A git command failed in a way the caller has to handle."""


def git(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], text=True, stderr=subprocess.PIPE
        ).strip()
    except subprocess.CalledProcessError as exc:  # pragma: no cover - defensive
        raise GitError(f"git {' '.join(args)} failed: {exc.stderr.strip()}") from exc


def remote_branches(remote: str) -> list[str]:
    """Every branch on ``remote``, as plain names without the remote prefix."""

    prefix = f"refs/remotes/{remote}/"
    names = []
    for line in git("for-each-ref", "--format=%(refname)", prefix).splitlines():
        name = line[len(prefix) :]
        if name and name != "HEAD":
            names.append(name)
    return names


def branch_age_days(ref: str, now: datetime) -> float:
    """Days since the tip of ``ref`` was committed."""

    stamp = int(git("log", "-1", "--format=%ct", ref))
    return (now - datetime.fromtimestamp(stamp, tz=timezone.utc)).total_seconds() / 86400


def touched_files(base_ref: str, ref: str) -> list[str] | None:
    """Files the branch changed relative to where it forked, ignoring the version file.

    ``None`` means the branch shares no ancestor with the base — a re-created or imported
    history — so "what did this branch change" has no answer and the caller must not guess.
    """

    try:
        merge_base = git("merge-base", base_ref, ref)
    except GitError:
        return None
    return [
        path
        for path in git("diff", "--name-only", merge_base, ref).splitlines()
        if path.strip() and path.strip() != VERSION_FILE
    ]


def classify(base_ref: str, ref: str) -> tuple[str, str]:
    """Return ``(reason, detail)`` for one branch. ``reason`` is ``KEEP`` or a landed reason."""

    ahead = int(git("rev-list", "--count", f"{base_ref}..{ref}"))
    if ahead == 0:
        return MERGED, "no commits the base lacks"

    unique = [
        line for line in git("cherry", base_ref, ref).splitlines() if line.startswith("+")
    ]
    if not unique:
        return PATCH_EQUIVALENT, f"all {ahead} commit(s) have an equivalent on the base"

    files = touched_files(base_ref, ref)
    if files is None:
        return KEEP, "no common ancestor with the base"
    if not files:
        return VERSION_ONLY, f"{ahead} commit(s) touch nothing but {VERSION_FILE}"

    remaining = [
        path
        for path in git("diff", "--name-only", base_ref, ref, "--", *files).splitlines()
        if path.strip()
    ]
    if not remaining:
        return CONTENT_IDENTICAL, f"all {len(files)} touched file(s) match the base"

    return KEEP, f"{len(remaining)} of {len(files)} touched file(s) still differ"


def open_pull_request_heads(repo: str, token: str) -> set[str]:
    """Head branch names of every open PR in ``repo``.

    Raises on any failure. The caller must treat that as "cannot verify" and keep every
    branch, rather than deleting on an assumption.
    """

    heads: set[str] = set()
    page = 1
    while True:
        request = urllib.request.Request(
            f"{API_ROOT}/repos/{repo}/pulls?state=open&per_page=100&page={page}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "discvault-prune-landed-branches",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        if not payload:
            break
        for pull in payload:
            ref = ((pull or {}).get("head") or {}).get("ref")
            if ref:
                heads.add(ref)
        if len(payload) < 100:
            break
        page += 1
    return heads


def delete_branch(remote: str, branch: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["git", "push", remote, "--delete", branch],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return result.returncode == 0, result.stdout.strip()


def build_report(
    remote: str,
    base: str,
    min_age_days: float,
    keep: set[str],
    now: datetime,
) -> list[dict]:
    base_ref = f"{remote}/{base}"
    rows = []
    for branch in sorted(remote_branches(remote)):
        if branch == base or branch in keep:
            continue
        ref = f"{remote}/{branch}"
        reason, detail = classify(base_ref, ref)
        age = branch_age_days(ref, now)
        rows.append(
            {
                "branch": branch,
                "sha": git("rev-parse", ref),
                "reason": reason,
                "detail": detail,
                "ageDays": round(age, 1),
                "tooYoung": age < min_age_days,
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--remote", default="origin")
    parser.add_argument(
        "--base",
        default="release/v26-beta",
        help="branch every candidate is measured against (default: release/v26-beta)",
    )
    parser.add_argument(
        "--min-age-days",
        type=float,
        default=14.0,
        help="never delete a branch touched more recently than this (default: 14)",
    )
    parser.add_argument(
        "--keep",
        action="append",
        default=[],
        help="extra branch to protect; repeatable",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually delete; without it the run only reports",
    )
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY", ""),
        help="owner/name used for the open-PR guard (default: $GITHUB_REPOSITORY)",
    )
    args = parser.parse_args()

    keep = set(PERMANENT_BRANCHES) | set(args.keep)
    now = datetime.now(tz=timezone.utc)

    try:
        rows = build_report(args.remote, args.base, args.min_age_days, keep, now)
    except GitError as exc:
        print(f"prune_landed_branches: {exc}", file=sys.stderr)
        return 1

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    open_heads: set[str] | None = None
    if token and args.repo:
        try:
            open_heads = open_pull_request_heads(args.repo, token)
        except (urllib.error.URLError, ValueError, TimeoutError) as exc:
            print(f"prune_landed_branches: cannot list open PRs ({exc})", file=sys.stderr)

    for row in rows:
        row["hasOpenPr"] = None if open_heads is None else row["branch"] in open_heads
        row["deletable"] = bool(
            row["reason"] in LANDED_REASONS
            and not row["tooYoung"]
            and row["hasOpenPr"] is False
        )

    deletable = [row for row in rows if row["deletable"]]
    landed = [row for row in rows if row["reason"] in LANDED_REASONS]

    if args.json:
        print(json.dumps({"branches": rows}, indent=2))
    else:
        print(f"Base: {args.remote}/{args.base}   branches examined: {len(rows)}")
        print(f"Landed: {len(landed)}   deletable after guards: {len(deletable)}\n")
        for row in sorted(rows, key=lambda item: (item["reason"] == KEEP, item["branch"])):
            blockers = []
            if row["tooYoung"]:
                blockers.append(f"younger than {args.min_age_days:g}d")
            if row["hasOpenPr"]:
                blockers.append("open PR")
            if row["hasOpenPr"] is None:
                blockers.append("open PRs unverified")
            suffix = f"  [held: {', '.join(blockers)}]" if blockers else ""
            print(
                f"  {row['reason']:<18} {row['branch']:<52} "
                f"{row['ageDays']:>6.1f}d  {row['detail']}{suffix}"
            )

    if not args.apply:
        if not args.json:
            print(
                f"\nDry run: nothing was deleted. "
                f"{len(deletable)} branch(es) would be deleted by --apply."
            )
        return 0

    if open_heads is None:
        print(
            "\nRefusing to delete: the open-PR guard could not be evaluated. "
            "Set GITHUB_TOKEN and --repo (or $GITHUB_REPOSITORY).",
            file=sys.stderr,
        )
        return 1

    failures = 0
    for row in deletable:
        ok, output = delete_branch(args.remote, row["branch"])
        state = "deleted" if ok else "FAILED"
        print(f"{state}: {row['branch']} ({row['sha'][:9]}, {row['reason']})")
        if not ok:
            failures += 1
            print(f"  {output}", file=sys.stderr)
    if not deletable:
        print("\nNothing to delete.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
