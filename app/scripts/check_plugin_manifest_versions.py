#!/usr/bin/env python3
"""Fail a change to a bundled plugin that does not raise its manifest version.

Plugins do not run from the image. They run from a writable install directory
(``/data/plugins``) that is seeded **once**, and after that
``upgrade_seeded_default_plugins`` only replaces an installed plugin when the
bundled manifest version is *strictly newer* than the installed copy. So a
bundled plugin edited without a version bump reaches new installs and no
existing one -- silently, and with nothing failing.

That is not hypothetical. `movievault_v2` shipped `orderIndex: 45` and was
changed to `5` while the version stayed `1.8.0`, so every upgraded instance kept
45 on disk. It only looked correct because the accompanying migration wrote the
database row directly; the next manifest edit would have no migration behind it.

No test could catch it either. Every test and CI run starts with no install
directory, so the install directory is by construction identical to the image --
the one environment where this bug exists is the one that never gets tested.
This check works on the diff instead, where the evidence actually is.

Version comparison mirrors ``_parse_plugin_version`` in ``next_plugin_runtime``
exactly, because that function is what decides at runtime whether the change
ships. A rule that disagreed with it would pass changes that still never land.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys


PLUGIN_ROOT = "app/backend/next_plugins/"
MANIFEST_NAME = "manifest.json"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL).strip()


def parse_plugin_version(value) -> tuple[int, ...]:
    """Mirror of next_plugin_runtime._parse_plugin_version."""

    text = str(value or "").strip()
    if not text:
        return ()
    parts: list[int] = []
    for chunk in re.split(r"[._-]+", text):
        match = re.match(r"\d+", chunk)
        if not match:
            break
        parts.append(int(match.group(0)))
    return tuple(parts)


def changed_files(base: str, head: str) -> list[str]:
    try:
        output = git("diff", "--name-only", f"{base}...{head}")
    except subprocess.CalledProcessError:
        output = git("diff", "--name-only", base, head)
    return [line.strip() for line in output.splitlines() if line.strip()]


def plugin_id_for(path: str) -> str | None:
    """The plugin a changed path belongs to, or None.

    Only files inside a plugin's own directory count. `_collection_import_base.py`
    sits directly under the plugin root and is shared by several plugins, so it
    has no single manifest to bump and is deliberately out of scope here.
    """

    if not path.startswith(PLUGIN_ROOT):
        return None
    remainder = path[len(PLUGIN_ROOT) :]
    if "/" not in remainder:
        return None
    plugin_id = remainder.split("/", 1)[0]
    if not plugin_id or plugin_id.startswith("__"):
        return None
    return plugin_id


def manifest_version_at(ref: str, plugin_id: str) -> str | None:
    """The manifest version at ``ref``, or None when the plugin is not there."""

    path = f"{PLUGIN_ROOT}{plugin_id}/{MANIFEST_NAME}"
    try:
        blob = git("show", f"{ref}:{path}")
    except subprocess.CalledProcessError:
        return None
    try:
        manifest = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if not isinstance(manifest, dict):
        return None
    version = manifest.get("version")
    return str(version) if version is not None else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Base ref or sha to compare against.")
    parser.add_argument("--head", default="HEAD", help="Head ref or sha (default: HEAD).")
    args = parser.parse_args()

    touched = sorted({
        plugin_id
        for plugin_id in (plugin_id_for(path) for path in changed_files(args.base, args.head))
        if plugin_id
    })
    if not touched:
        print("No bundled plugin files changed.")
        return 0

    failures: list[str] = []
    for plugin_id in touched:
        base_version = manifest_version_at(args.base, plugin_id)
        head_version = manifest_version_at(args.head, plugin_id)
        if base_version is None:
            print(f"{plugin_id}: new plugin ({head_version or 'no version'}) - no bump required")
            continue
        if head_version is None:
            print(f"{plugin_id}: removed from the bundle - no bump required")
            continue
        if parse_plugin_version(head_version) > parse_plugin_version(base_version):
            print(f"{plugin_id}: {base_version} -> {head_version}")
            continue
        failures.append(
            f"  {plugin_id}: version is {head_version!r} and was {base_version!r}. "
            f"Files changed, so it must be strictly greater."
        )

    if failures:
        print(
            "\nBundled plugin files changed without a higher manifest version:\n"
            + "\n".join(failures)
            + "\n\nPlugins run from the writable install directory, and an installed copy is\n"
            "only replaced when the bundled version is strictly newer. Without the bump\n"
            "this change reaches new installs only, and every existing instance keeps the\n"
            "old files with nothing reporting a problem.\n\n"
            "Raise \"version\" in the plugin's manifest.json.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
