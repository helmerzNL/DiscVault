import os
import subprocess
from pathlib import Path


APP_NAME = "DiscVault"
PLACEHOLDER_VERSIONS = {"", "dev", "next-dev", "unknown"}


def _clean(value: str | None) -> str:
    return str(value or "").strip()


def _version_file_candidates() -> list[Path]:
    here = Path(__file__).resolve()
    parents = list(here.parents)
    candidates = [here.parent / "VERSION"]
    if len(parents) > 1:
        candidates.append(parents[1] / "VERSION")
    if len(parents) > 2:
        candidates.append(parents[2] / "app" / "VERSION")
    candidates.append(Path.cwd() / "VERSION")
    return candidates


def packaged_version() -> str:
    for path in _version_file_candidates():
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value and value.lower() not in PLACEHOLDER_VERSIONS:
            return value
    return ""


def build_sha() -> str:
    for key in (
        "DISCVAULT_IMAGE_SHA",
        "DISCVAULT_BUILD_SHA",
        "BUILD_SHA",
        "GITHUB_SHA",
        "COMMIT_SHA",
        "SOURCE_COMMIT",
    ):
        value = _clean(os.environ.get(key))
        if value and value.lower() not in {"dev", "unknown"}:
            return value
    try:
        root = Path(__file__).resolve().parents[2]
        value = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1,
        ).strip()
        if value:
            return value
    except Exception:
        pass
    return "unknown"


def backend_version() -> str:
    for key in (
        "DISCVAULT_IMAGE_VERSION",
        "DISCVAULT_BACKEND_VERSION",
        "BUILD_VERSION",
        "APP_VERSION",
        "VERSION",
    ):
        value = _clean(os.environ.get(key))
        if value and value.lower() not in PLACEHOLDER_VERSIONS:
            return value
    value = packaged_version()
    if value:
        return value
    try:
        root = Path(__file__).resolve().parents[2]
        value = subprocess.check_output(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=str(root),
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1,
        ).strip()
        if value:
            return value
    except Exception:
        pass
    sha = build_sha()
    if sha and sha != "unknown":
        return f"build-{sha[:12]}"
    return "unknown"


def software_identity() -> dict:
    version = backend_version()
    return {
        "name": APP_NAME,
        "version": version,
        "backendVersion": version,
        "buildSha": build_sha(),
    }
