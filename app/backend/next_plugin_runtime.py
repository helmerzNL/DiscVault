"""DiscVault Next plugin discovery and registry synchronization."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

try:  # pragma: no cover - import style depends on how this module is loaded
    from .next_movievault_v2 import enforced_bucket_fallback
except ImportError:  # pragma: no cover - supports direct module execution
    from next_movievault_v2 import enforced_bucket_fallback


JsonbFactory = Callable[[Any], Any]
TableExists = Callable[[Any, str], bool]

DEFAULT_PLUGIN_DIR = Path(__file__).resolve().parent / "next_plugins"
# Marker file written inside the writable plugin install directory once the
# bundled default plugins have been seeded into it. Its presence means the
# install directory is the authoritative plugin source and default plugins may
# be deleted without reappearing on the next boot.
PLUGIN_INITIALIZED_MARKER = ".initialized"
# Marker file (inside the install directory) that disables automatic plugin
# upgrades. Persisting it next to the plugins lets the connection-less runtime
# honor the preference across restarts, independent of the database.
PLUGIN_AUTO_UPDATE_DISABLED_MARKER = ".auto_update_disabled"
# Tracks install directories whose bundled-default upgrade check already ran in
# this process, so the (rare) file copy work happens at most once per boot.
_DEFAULT_PLUGIN_UPGRADE_DONE: set[str] = set()
# Discovery and registry-sync memos, both keyed on a fingerprint of the plugin
# files on disk. See `plugin_source_fingerprint` for why they are keyed that way
# and what the memo deliberately does not protect against.
_DISCOVERY_MEMO: dict[str, Any] = {"fingerprint": None, "result": None}
_REGISTRY_SYNC_MEMO: dict[str, Any] = {"fingerprint": None, "result": None}
# Loaded plugin modules, keyed by plugin id, each held with the stamp of the
# file it came from. See `load_runtime_module` for why a module has to survive
# between calls at all.
_RUNTIME_MODULE_MEMO: dict[str, tuple] = {}
# Optional in-process override of the auto-update preference, set by the app
# layer from the database setting. ``None`` falls back to env/marker/default.
_plugin_auto_update_override: bool | None = None
# These bundled versions route outbound provider requests through the pinned
# public-network transport. Older copies would reopen SSRF paths, so these
# floors override the routine auto-update preference.
SECURITY_MINIMUM_BUNDLED_PLUGIN_VERSIONS = {
    "amazon": "1.0.2",
    "arrow": "1.0.2",
    "bol": "1.0.2",
    "keepa": "1.0.2",
    "priceapi": "1.0.1",
    "zavvi": "1.0.2",
}
VALID_CATEGORIES = {
    "metadata_source",
    "metadata_bootstrap",
    "metadata_receiver",
    "digital_media_source",
    "import_source",
    "personal_list_source",
    "price_provider",
    "system",
    "mcp",
    "api",
}
PLUGIN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,80}$")
DISCVAULT_PLUGIN_API_VERSION = "next-1"
SUPPORTED_PLUGIN_API_VERSIONS = {DISCVAULT_PLUGIN_API_VERSION}
PLUGIN_ENTRYPOINTS = (
    "health_check",
    "connection_request",
    "connection_recovery_action",
    "search_title",
    "search_barcode",
    "lookup_external_id",
    "movie_details",
    # Television. Absent until now, which made every series source call return
    # `entrypoint_unavailable` -- the whole series chain (refresh, identity
    # search, the season offer, episodes) was calling functions the runtime
    # refused to reach, on both bundled sources. The plugins had the code and
    # the manifests declared the capability; this list is what decides whether
    # a name may run, and nothing compared the two. See
    # `test_next_plugin_entrypoint_coverage.py`.
    "series_details",
    "search_series",
    # The television sibling of `lookup_external_id`. Separate from
    # `series_details` for the reason that one is separate from `search_series`:
    # this answers a number a *person* typed, in `items` so the answer can sit
    # beside the film namespace's in one list to choose from.
    "lookup_external_series_id",
    "season_episodes",
    "box_set_candidates",
    "people_for_movie",
    "person_details",
    "person_filmography",
    "person_awards",
    "images_for_movie",
    "videos_for_movie",
    "technical_specs",
    "receive_metadata",
    "describe_payload",
    "activity_summary",
    "prepare_barcode_update",
    "prepare_container_update",
    "member_intelligence",
    "discover_library",
    "sync_library",
    "sync_index",
    "sync_personal_lists",
    "playback_deeplink",
    "inspect_source",
    "plan_import",
    "import_source",
    "price_check",
)


@dataclass(frozen=True)
class PluginDiscovery:
    manifest: dict[str, Any]
    path: Path
    module_path: Path | None
    runtime: dict[str, Any]

    @property
    def plugin_id(self) -> str:
        return str(self.manifest["id"])


def plugin_install_dir() -> Path:
    configured = os.environ.get("DISCVAULT_PLUGIN_INSTALL_DIR", "").strip()
    data_dir = Path(os.environ.get("DISCVAULT_DATA_DIR") or "/data")
    return Path(configured) if configured else data_dir / "plugins"


def bundled_plugin_dir() -> Path:
    raw = os.environ.get("DISCVAULT_BUNDLED_PLUGIN_DIR", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_PLUGIN_DIR


def plugin_dir_contains_plugins(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    return any(item.is_dir() and (item / "manifest.json").exists() for item in path.iterdir())


def bundled_default_plugin_dirs() -> list[Path]:
    source_root = bundled_plugin_dir()
    if not source_root.exists() or not source_root.is_dir():
        return []
    return sorted(
        item
        for item in source_root.iterdir()
        if item.is_dir() and (item / "manifest.json").exists()
    )


def seed_default_plugins_if_needed() -> dict[str, Any]:
    """Copy the bundled default plugins into the writable install directory once.

    After seeding, a ``.initialized`` marker is written so the install directory
    becomes the authoritative plugin source. This lets default plugins be
    deleted (they live in a writable location) without reappearing on the next
    boot, and gives a future plugin portal a single place to add plugins.

    Seeding is skipped (leaving the bundled directory as a read fallback) when
    the data directory is absent or read-only, so the app is never left without
    plugins.
    """

    install_dir = plugin_install_dir()
    marker = install_dir / PLUGIN_INITIALIZED_MARKER
    result: dict[str, Any] = {
        "path": str(install_dir),
        "marker": str(marker),
        "initialized": marker.exists(),
        "seeded": [],
        "skippedExisting": [],
        "errors": [],
    }
    if result["initialized"]:
        return result

    # Do not fabricate a data root (e.g. "/data") that does not exist; that is
    # the signal we are running without a writable data directory (such as in
    # unit tests), where the bundled directory should remain the source.
    if not install_dir.parent.exists():
        return result

    try:
        install_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result["errors"].append({"path": str(install_dir), "error": str(exc)})
        return result

    for source in bundled_default_plugin_dirs():
        target = install_dir / source.name
        if target.exists():
            result["skippedExisting"].append(source.name)
            continue
        try:
            shutil.copytree(source, target)
            result["seeded"].append(source.name)
        except OSError as exc:
            result["errors"].append({"path": str(target), "error": str(exc)})

    try:
        marker.write_text(
            json.dumps(
                {
                    "initializedAt": int(time.time()),
                    "source": str(bundled_plugin_dir()),
                    "seeded": result["seeded"],
                }
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        result["errors"].append({"path": str(marker), "error": str(exc)})

    result["initialized"] = marker.exists()
    return result


def _parse_plugin_version(value: Any) -> tuple[int, ...]:
    """Parse a semver-ish version string into a comparable integer tuple.

    Numeric, dot/dash/underscore-separated components are read left-to-right
    until the first non-numeric component (so ``1.5.1`` -> ``(1, 5, 1)`` and a
    trailing ``-beta`` is ignored). Returns an empty tuple for blank/garbage
    input, which compares lower than any real version.
    """

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


def _read_manifest_version(plugin_dir: Path) -> str:
    manifest_path = plugin_dir / "manifest.json"
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(raw, dict):
        return ""
    return str(raw.get("version") or "").strip()


def _plugin_meets_security_minimum(plugin_id: str, version: str) -> bool:
    minimum = SECURITY_MINIMUM_BUNDLED_PLUGIN_VERSIONS.get(plugin_id)
    return minimum is None or _parse_plugin_version(version) >= _parse_plugin_version(minimum)


def plugin_backup_dir() -> Path:
    """Directory holding the previous version of a plugin for rollback.

    Kept *outside* the install directory so plugin discovery never scans it.
    """

    configured = os.environ.get("DISCVAULT_PLUGIN_BACKUP_DIR", "").strip()
    if configured:
        return Path(configured)
    return plugin_install_dir().parent / "plugin_backups"


def bundled_default_plugin_path(plugin_id: str) -> Path | None:
    for source in bundled_default_plugin_dirs():
        if source.name == plugin_id:
            return source
    return None


def installed_plugin_version(plugin_id: str) -> str:
    return _read_manifest_version(plugin_install_dir() / plugin_id)


def bundled_plugin_version(plugin_id: str) -> str:
    source = bundled_default_plugin_path(plugin_id)
    return _read_manifest_version(source) if source else ""


def plugin_backup_version(plugin_id: str) -> str:
    return _read_manifest_version(plugin_backup_dir() / plugin_id)


def plugin_has_backup(plugin_id: str) -> bool:
    return (plugin_backup_dir() / plugin_id / "manifest.json").exists()


def plugin_update_state(plugin_id: str, installed_version: str | None = None) -> dict[str, Any]:
    """Update/rollback availability for a single plugin (filesystem-derived)."""

    installed = (installed_version or installed_plugin_version(plugin_id)).strip()
    bundled = bundled_plugin_version(plugin_id)
    update_available = bool(bundled) and _parse_plugin_version(bundled) > _parse_plugin_version(installed)
    backup_version = plugin_backup_version(plugin_id)
    return {
        "isBundledDefault": bundled_default_plugin_path(plugin_id) is not None,
        "bundledVersion": bundled,
        "installedVersion": installed,
        "updateAvailable": update_available,
        "canRollback": plugin_has_backup(plugin_id)
        and _plugin_meets_security_minimum(plugin_id, backup_version),
        "rollbackVersion": backup_version,
    }


def set_plugin_auto_update_enabled(enabled: bool | None) -> None:
    """Override the auto-update preference for this process (``None`` clears)."""

    global _plugin_auto_update_override
    _plugin_auto_update_override = None if enabled is None else bool(enabled)


def _auto_update_marker_path() -> Path:
    return plugin_install_dir() / PLUGIN_AUTO_UPDATE_DISABLED_MARKER


def write_plugin_auto_update_marker(enabled: bool) -> None:
    """Persist the auto-update preference next to the installed plugins."""

    marker = _auto_update_marker_path()
    try:
        if enabled:
            marker.unlink(missing_ok=True)
        else:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(str(int(time.time())), encoding="utf-8")
    except OSError:
        pass


def plugin_auto_update_enabled() -> bool:
    """Whether bundled plugin updates apply automatically (default enabled)."""

    if _plugin_auto_update_override is not None:
        return _plugin_auto_update_override
    raw = os.environ.get("DISCVAULT_PLUGIN_AUTO_UPDATE", "").strip().lower()
    if raw in {"0", "false", "off", "no", "disabled"}:
        return False
    if raw in {"1", "true", "on", "yes", "enabled"}:
        return True
    if _auto_update_marker_path().exists():
        return False
    return True


def _snapshot_plugin_backup(plugin_id: str) -> None:
    target = plugin_install_dir() / plugin_id
    if not target.exists():
        return
    backup_target = plugin_backup_dir() / plugin_id
    backup_target.parent.mkdir(parents=True, exist_ok=True)
    if backup_target.exists():
        shutil.rmtree(backup_target)
    shutil.copytree(target, backup_target)


def _replace_installed_plugin(plugin_id: str, source_dir: Path) -> None:
    """Replace the installed plugin with ``source_dir``, snapshotting a backup
    of the previous version first so the change can be rolled back."""

    install_dir = plugin_install_dir()
    target = install_dir / plugin_id
    _snapshot_plugin_backup(plugin_id)
    if target.exists():
        shutil.rmtree(target)
    install_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, target)


def upgrade_seeded_default_plugins() -> dict[str, Any]:
    """Refresh installed default plugins when a newer bundled version ships.

    Plugins run only from the writable install directory (``/data/plugins``).
    Because seeding is one-shot, a bundled bug-fix would otherwise never reach
    an already-initialized install directory. When auto-update is enabled we
    therefore upgrade *in place* any default plugin whose bundled manifest
    version is strictly newer than the installed copy, snapshotting the prior
    version for rollback. A plugin that the user deleted (absent from the
    install directory) is left deleted, preserving the seed-once deletion
    contract. Plugin enable/config state lives in the database, not in these
    files, so replacing the files keeps that state intact.
    """

    install_dir = plugin_install_dir()
    marker = install_dir / PLUGIN_INITIALIZED_MARKER
    result: dict[str, Any] = {
        "path": str(install_dir),
        "upgraded": [],
        "added": [],
        "skipped": [],
        "errors": [],
    }
    # Only manage an initialized, writable install directory. When seeding did
    # not run (no writable data dir, e.g. unit tests) the bundled directory is
    # the live source and there is nothing to upgrade.
    if not marker.exists():
        return result
    marker_payload: dict[str, Any] | None = None
    marker_seeded: set[str] = set()
    marker_changed = False
    try:
        loaded_marker = json.loads(marker.read_text(encoding="utf-8"))
        if isinstance(loaded_marker, dict):
            marker_payload = loaded_marker
            marker_seeded = {
                str(item).strip()
                for item in (marker_payload.get("seeded") or [])
                if str(item).strip()
            }
    except (OSError, ValueError):
        # Keep backward-compatible behavior for legacy/non-JSON markers:
        # do not install missing bundled defaults when we cannot prove
        # whether they were previously seeded and user-deleted.
        marker_payload = None
    auto_update_enabled = plugin_auto_update_enabled()
    if not auto_update_enabled:
        result["disabled"] = True
    for source in bundled_default_plugin_dirs():
        target = install_dir / source.name
        if not target.exists():
            if source.name in marker_seeded:
                # Respect a user-deleted seeded default; never resurrect it.
                continue
            if marker_payload is None:
                # Legacy marker payloads do not track seeded plugin ids.
                continue
            if not auto_update_enabled:
                result["skipped"].append(source.name)
                continue
            try:
                install_dir.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, target)
                marker_seeded.add(source.name)
                result["added"].append(source.name)
                marker_changed = True
            except OSError as exc:
                result["errors"].append({"path": str(target), "error": str(exc)})
            continue
        bundled_version = _read_manifest_version(source)
        installed_version = _read_manifest_version(target)
        if _parse_plugin_version(bundled_version) <= _parse_plugin_version(installed_version):
            result["skipped"].append(source.name)
            continue
        security_upgrade_required = (
            not _plugin_meets_security_minimum(source.name, installed_version)
            and _plugin_meets_security_minimum(source.name, bundled_version)
        )
        if not auto_update_enabled and not security_upgrade_required:
            result["skipped"].append(source.name)
            continue
        try:
            _replace_installed_plugin(source.name, source)
            upgrade = {
                "plugin": source.name,
                "from": installed_version,
                "to": bundled_version,
            }
            if security_upgrade_required:
                upgrade["securityRequired"] = True
            result["upgraded"].append(upgrade)
        except OSError as exc:
            result["errors"].append({"path": str(target), "error": str(exc)})
    if marker_changed and marker_payload is not None:
        marker_payload["seeded"] = sorted(marker_seeded)
        marker_payload.setdefault("source", str(bundled_plugin_dir()))
        marker_payload.setdefault("initializedAt", int(time.time()))
        try:
            marker.write_text(json.dumps(marker_payload), encoding="utf-8")
        except OSError as exc:
            result["errors"].append({"path": str(marker), "error": str(exc)})
    return result


def install_bundled_plugin_update(plugin_id: str) -> dict[str, Any]:
    """Manually install the bundled version of a default plugin over the
    installed copy (writing to ``/data/plugins``), keeping a rollback backup."""

    source = bundled_default_plugin_path(plugin_id)
    if source is None:
        raise ValueError(f"No bundled version is available for plugin {plugin_id}")
    install_dir = plugin_install_dir()
    if not (install_dir / PLUGIN_INITIALIZED_MARKER).exists():
        raise ValueError("Plugin install directory is not initialized")
    installed_version = installed_plugin_version(plugin_id)
    bundled_version = _read_manifest_version(source)
    if _parse_plugin_version(bundled_version) <= _parse_plugin_version(installed_version):
        raise ValueError(
            f"Bundled version {bundled_version or '?'} is not newer than installed "
            f"{installed_version or '?'}"
        )
    _replace_installed_plugin(plugin_id, source)
    return {"pluginId": plugin_id, "from": installed_version, "to": bundled_version}


def rollback_plugin_update(plugin_id: str) -> dict[str, Any]:
    """Restore the previous version of a plugin from its rollback backup."""

    backup_target = plugin_backup_dir() / plugin_id
    if not (backup_target / "manifest.json").exists():
        raise ValueError(f"No backup is available to roll back plugin {plugin_id}")
    install_dir = plugin_install_dir()
    target = install_dir / plugin_id
    current_version = installed_plugin_version(plugin_id)
    backup_version = _read_manifest_version(backup_target)
    if not _plugin_meets_security_minimum(plugin_id, backup_version):
        minimum = SECURITY_MINIMUM_BUNDLED_PLUGIN_VERSIONS[plugin_id]
        raise ValueError(
            f"Rollback for plugin {plugin_id} would restore a version below "
            f"the required security minimum {minimum}"
        )
    if target.exists():
        shutil.rmtree(target)
    install_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(backup_target, target)
    shutil.rmtree(backup_target)
    return {"pluginId": plugin_id, "from": current_version, "to": backup_version}


def _upgrade_default_plugins_once() -> None:
    install_key = str(plugin_install_dir().resolve()).lower()
    if install_key in _DEFAULT_PLUGIN_UPGRADE_DONE:
        return
    _DEFAULT_PLUGIN_UPGRADE_DONE.add(install_key)
    try:
        upgrade_seeded_default_plugins()
    except Exception:
        # Never let an upgrade failure break plugin discovery; allow a retry.
        _DEFAULT_PLUGIN_UPGRADE_DONE.discard(install_key)


def plugin_paths() -> list[Path]:
    seed = seed_default_plugins_if_needed()
    # Once the install directory is authoritative, bring any default plugin
    # whose bundled version is newer up to date so shipped bug-fixes apply.
    if seed.get("initialized"):
        _upgrade_default_plugins_once()
    configured = os.environ.get("DISCVAULT_PLUGIN_PATHS", "").strip()
    # The writable install directory is the authoritative source. The bundled
    # directory is only used as a fallback when seeding did not complete, so a
    # deleted default plugin does not reappear from the read-only image.
    paths = [plugin_install_dir()]
    if not seed.get("initialized"):
        paths.append(DEFAULT_PLUGIN_DIR)
    if configured:
        for item in configured.split(os.pathsep):
            item = item.strip()
            if item:
                paths.append(Path(item))
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        key = str(resolved).lower()
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def normalize_categories(manifest: dict[str, Any]) -> list[str]:
    categories = manifest.get("categories")
    if not isinstance(categories, list):
        kind = str(manifest.get("kind") or "").strip()
        if kind == "metadata_provider":
            kind = "metadata_source"
        categories = [kind] if kind else []
    normalized = []
    for category in categories:
        value = str(category or "").strip()
        if value in VALID_CATEGORIES and value not in normalized:
            normalized.append(value)
    return normalized


def plugin_setting_items(settings_schema: Any, kind: str = "settings") -> list[dict[str, Any]]:
    if not isinstance(settings_schema, dict):
        return []
    raw_items = settings_schema.get(kind)
    if isinstance(raw_items, list):
        return [dict(item) for item in raw_items if isinstance(item, dict)]
    if isinstance(raw_items, dict):
        return [
            {"name": str(name), **(dict(item) if isinstance(item, dict) else {})}
            for name, item in raw_items.items()
        ]
    return []


def _setting_name(field: dict[str, Any]) -> str:
    return str(field.get("name") or field.get("key") or "").strip()


# DiscVault enforces certain plugin endpoint/settings server-side (a fixed value
# or an env override) so they must not render as editable fields in the generic
# admin plugin-settings UI. Any field named here is stripped from the plugin's
# settingsSchema at sync time, which removes it from the UI and from the
# required-settings validation set. Runtime always resolves the enforced value
# itself (e.g. movievault_v2 via enforced_origin()), regardless of stored data.
#
# movievault_v2.bucketFallback is enforced for the same reason: the anonymous
# bucket lookup is what finds a disc that the locally synced index does not carry
# yet, so leaving it switchable turns a core part of barcode resolution into an
# operator footgun. It is always on (enforced_bucket_fallback()), overridable only
# out of band via MOVIEVAULT_V2_BUCKET_FALLBACK.
ENFORCED_PLUGIN_SETTINGS: dict[str, frozenset[str]] = {
    "movievault_v2": frozenset({"origin", "bucketFallback"}),
}


def enforced_settings_schema(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the manifest settingsSchema with DiscVault-enforced fields removed.

    For plugins whose endpoint is fixed by DiscVault (e.g. the MovieVault v2
    origin), the enforced field(s) are dropped from a shallow copy of the schema
    so they never reach ``plugins.settings_schema`` and therefore never appear as
    editable inputs. Non-dict / unlisted schemas pass through unchanged.
    """
    schema = manifest.get("settingsSchema")
    if not isinstance(schema, dict):
        return {} if schema is None else schema
    enforced = ENFORCED_PLUGIN_SETTINGS.get(str(manifest.get("id") or ""))
    if not enforced:
        return schema
    result = dict(schema)
    for kind in ("settings", "secrets"):
        raw_items = schema.get(kind)
        if isinstance(raw_items, list):
            result[kind] = [
                item
                for item in raw_items
                if not (isinstance(item, dict) and _setting_name(item) in enforced)
            ]
        elif isinstance(raw_items, dict):
            result[kind] = {
                name: item
                for name, item in raw_items.items()
                if str(name).strip() not in enforced
            }
    return result


def _setting_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return value not in ([], {})


def _normalize_plugin_setting(field: dict[str, Any], value: Any) -> Any:
    name = _setting_name(field)
    setting_type = str(field.get("type") or "text").strip().lower()
    if value is None and not field.get("required"):
        return None
    if setting_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"Plugin setting {name} must be a boolean")
        return value
    if setting_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Plugin setting {name} must be a number")
        minimum = field.get("minimum")
        maximum = field.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            raise ValueError(f"Plugin setting {name} must be at least {minimum}")
        if isinstance(maximum, (int, float)) and value > maximum:
            raise ValueError(f"Plugin setting {name} must be at most {maximum}")
        return value
    if setting_type == "array":
        if not isinstance(value, list):
            raise ValueError(f"Plugin setting {name} must be an array")
        return value
    if not isinstance(value, str):
        raise ValueError(f"Plugin setting {name} must be text")
    return value.strip()


def plugin_setting_defaults(settings_schema: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    for field in plugin_setting_items(settings_schema):
        name = _setting_name(field)
        if name and "default" in field:
            defaults[name] = _normalize_plugin_setting(field, field["default"])
    return defaults


def resolve_plugin_settings(settings_schema: Any, stored_settings: Any) -> dict[str, Any]:
    resolved = plugin_setting_defaults(settings_schema)
    if isinstance(stored_settings, dict):
        resolved.update(stored_settings)
    return resolved


def plugin_settings_configured(settings_schema: Any, settings: Any) -> bool:
    resolved = resolve_plugin_settings(settings_schema, settings)
    fields = plugin_setting_items(settings_schema)
    for field in fields:
        if field.get("required") and not _setting_present(resolved.get(_setting_name(field))):
            return False
    return bool(resolved) if fields else False


def validate_plugin_settings(settings_schema: Any, settings: Any) -> dict[str, Any]:
    if not isinstance(settings, dict):
        raise ValueError("Plugin settings must be an object")
    fields = {
        _setting_name(field): field
        for field in plugin_setting_items(settings_schema)
        if _setting_name(field)
    }
    normalized = dict(settings)
    for name, value in settings.items():
        field = fields.get(str(name))
        if field is not None:
            normalized[str(name)] = _normalize_plugin_setting(field, value)
    resolved = resolve_plugin_settings(settings_schema, normalized)
    for name, field in fields.items():
        if field.get("required") and not _setting_present(resolved.get(name)):
            raise ValueError(f"Plugin setting {name} is required")
    return resolved


def required_secrets_configured(settings_schema: Any, secrets_ref: Any) -> bool:
    """Return whether every REQUIRED secret of a plugin has a stored value.

    Distinct from the ``secretsConfigured`` flag, which is only "at least one
    secret exists" - true as soon as any one of a multi-secret plugin's fields is
    filled in. Auto-enable needs the stricter question, and the two must not be
    conflated: plugin_requires_config() in next_app/next_worker depends on the
    loose meaning.
    """
    refs = secrets_ref if isinstance(secrets_ref, dict) else {}
    for field in plugin_setting_items(settings_schema, "secrets"):
        name = _setting_name(field)
        if name and field.get("required") and not refs.get(name):
            return False
    return True


def plugin_config_payload(
    settings_schema: Any,
    settings: Any,
    secrets_ref: Any,
) -> dict[str, Any]:
    resolved = resolve_plugin_settings(settings_schema, settings)
    refs = secrets_ref if isinstance(secrets_ref, dict) else {}
    safe_refs: dict[str, dict[str, Any]] = {}
    for name, ref in refs.items():
        key = ref.get("key") if isinstance(ref, dict) else ref
        item: dict[str, Any] = {"configured": True}
        if key:
            item["key"] = str(key)
        safe_refs[str(name)] = item
    return {
        "settings": resolved,
        "settingsConfigured": plugin_settings_configured(settings_schema, resolved),
        "secretNames": sorted(safe_refs),
        "secretsConfigured": bool(safe_refs),
        "requiredSecretsConfigured": required_secrets_configured(settings_schema, safe_refs),
        "secretsRef": safe_refs,
    }


def manifest_plugin_api_versions(manifest: dict[str, Any]) -> set[str]:
    declared = manifest.get("discVaultPluginApi", manifest.get("pluginApi"))
    if declared is None:
        declared = manifest.get("discVaultPluginApis")
    if declared is None and isinstance(manifest.get("compatibleDiscVault"), dict):
        compatible = manifest["compatibleDiscVault"]
        declared = compatible.get("pluginApi", compatible.get("pluginApis"))
    if isinstance(declared, str):
        return {declared.strip()} if declared.strip() else set()
    if isinstance(declared, list):
        return {str(item).strip() for item in declared if str(item).strip()}
    return set()


def validate_manifest_compatibility(manifest: dict[str, Any], require_declared: bool = False) -> None:
    versions = manifest_plugin_api_versions(manifest)
    if require_declared and not versions:
        raise ValueError("Plugin manifest must declare discVaultPluginApi")
    if versions and not versions.intersection(SUPPORTED_PLUGIN_API_VERSIONS):
        supported = ", ".join(sorted(SUPPORTED_PLUGIN_API_VERSIONS))
        declared = ", ".join(sorted(versions))
        raise ValueError(f"Plugin API version is not compatible: {declared}; supported: {supported}")


def normalize_manifest(raw: dict[str, Any], path: Path) -> dict[str, Any]:
    manifest = dict(raw)
    plugin_id = str(manifest.get("id") or path.name).strip()
    if not PLUGIN_ID_PATTERN.match(plugin_id):
        raise ValueError(f"Invalid plugin id: {plugin_id}")
    manifest["id"] = plugin_id
    manifest["name"] = str(manifest.get("name") or plugin_id).strip()
    manifest["version"] = str(manifest.get("version") or "").strip()
    if not manifest["version"]:
        raise ValueError(f"Plugin {plugin_id} must declare a version")
    manifest["manifestVersion"] = int(manifest.get("manifestVersion") or 1)
    manifest["discVaultPluginApi"] = str(
        manifest.get("discVaultPluginApi") or DISCVAULT_PLUGIN_API_VERSION
    ).strip()
    validate_manifest_compatibility(manifest)
    manifest["categories"] = normalize_categories(manifest)
    if not manifest["categories"]:
        raise ValueError(f"Plugin {plugin_id} does not declare a valid category")
    capabilities = manifest.get("capabilities")
    manifest["capabilities"] = capabilities if isinstance(capabilities, list) else []
    settings_schema = manifest.get("settingsSchema", manifest.get("settings_schema", {}))
    manifest["settingsSchema"] = settings_schema if isinstance(settings_schema, dict) else {}
    plugin_setting_defaults(manifest["settingsSchema"])
    entitlements = manifest.get("entitlements")
    manifest["entitlements"] = entitlements if isinstance(entitlements, dict) else {}
    if "kind" not in manifest:
        manifest["kind"] = manifest["categories"][0]
    return manifest


def load_manifest(plugin_dir: Path) -> dict[str, Any]:
    manifest_path = plugin_dir / "manifest.json"
    if not manifest_path.exists():
        raise ValueError("manifest.json is missing")
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("manifest.json must contain an object")
    return normalize_manifest(raw, plugin_dir)


def module_name_for(plugin_id: str, module_path: Path) -> str:
    digest = hashlib.sha256(str(module_path).encode("utf-8")).hexdigest()[:12]
    safe_id = re.sub(r"[^a-zA-Z0-9_]", "_", plugin_id)
    return f"discvault_next_plugin_{safe_id}_{digest}"


def load_runtime(manifest: dict[str, Any], plugin_dir: Path) -> tuple[Path | None, dict[str, Any]]:
    module_path = plugin_dir / "plugin.py"
    if not module_path.exists():
        return None, {"loaded": False, "entrypoints": [], "error": None}
    runtime = {"loaded": False, "entrypoints": [], "error": None}
    try:
        spec = importlib.util.spec_from_file_location(
            module_name_for(manifest["id"], module_path),
            module_path,
        )
        if not spec or not spec.loader:
            raise RuntimeError("Could not create module spec")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        entrypoints = [name for name in PLUGIN_ENTRYPOINTS if callable(getattr(module, name, None))]
        runtime.update({"loaded": True, "entrypoints": entrypoints})
    except Exception as exc:
        runtime["error"] = str(exc)
    return module_path, runtime


def _module_file_stamp(module_path: Path) -> tuple:
    try:
        stat = module_path.stat()
    except OSError:
        return (str(module_path), None, None)
    return (str(module_path), stat.st_mtime_ns, stat.st_size)


def load_runtime_module(plugin: PluginDiscovery):
    """Load a plugin's module, once per process per version of its file.

    This used to re-execute `plugin.py` on **every** entrypoint call. The
    execution itself is cheap -- measured at 0.5 ms for the TMDB plugin and
    2.1 ms for the largest one -- so that was never the problem. What it cost
    was everything a module is allowed to keep.

    Two plugins were written expecting a module to survive between calls, and
    quietly got nothing: `tvdb` caches its auth token in `_TOKENS`, so it
    re-authenticated on every single call, and the since-removed `movievault_26`
    kept a template cache that never once produced a hit. Both were keyed by the
    configuration they belong to (`api_key`, a context key), so persisting them
    shares nothing across configurations -- their authors had already thought
    about that.

    It also makes connection reuse possible at all: `requests.get` builds a new
    connection per call, and a module-level `requests.Session` cannot help while
    the module holding it is discarded a moment later.

    Keyed on the file rather than a clock, so a plugin upgrade rewrites
    `plugin.py` and the next call loads the new code -- the same rule
    `plugin_source_fingerprint` uses for discovery.
    """
    if not plugin.module_path:
        return None
    stamp = _module_file_stamp(plugin.module_path)
    cached = _RUNTIME_MODULE_MEMO.get(plugin.plugin_id)
    if cached and cached[0] == stamp:
        return cached[1]
    spec = importlib.util.spec_from_file_location(
        module_name_for(plugin.plugin_id, plugin.module_path),
        plugin.module_path,
    )
    if not spec or not spec.loader:
        raise RuntimeError("Could not create module spec")
    module = importlib.util.module_from_spec(spec)
    # Compile the source rather than letting the loader do it, because the
    # loader may not. CPython validates a cached .pyc on the source's
    # modification time *in whole seconds* plus its size -- so a plugin
    # rewritten within the same second, to the same length, runs the previous
    # version's bytecode. Reproduced here before this line existed.
    #
    # The image sets PYTHONDONTWRITEBYTECODE, so no .pyc is written and the
    # hazard cannot arise in production today. That is a reason to be careful
    # rather than relaxed: it means correctness rests on an environment
    # variable someone could reasonably remove to speed up start-up, and the
    # failure it would buy back is a plugin upgrade silently running the old
    # code. Compiling here costs about two milliseconds for the largest plugin,
    # once per process per version of the file.
    source = plugin.module_path.read_text(encoding="utf-8")
    exec(compile(source, str(plugin.module_path), "exec"), module.__dict__)
    _RUNTIME_MODULE_MEMO[plugin.plugin_id] = (stamp, module)
    return module


def plugin_source_fingerprint() -> tuple:
    """Identify the plugin files on disk cheaply enough to check per request.

    Discovery is expensive in a way that is easy to miss: it *executes* every
    plugin's `plugin.py` to enumerate entrypoints, and the image sets
    PYTHONDONTWRITEBYTECODE, so there is no `__pycache__` and roughly a megabyte
    of Python is recompiled from source each time. Stat-ing the same files is
    two syscalls per plugin.

    Both the search paths and each plugin's two significant files are part of
    the fingerprint. The paths matter because `plugin_paths` reads the
    environment, so a caller can point discovery somewhere else between calls --
    which is exactly what the runtime tests do. Size is included alongside the
    modification time so a rewrite that lands inside the same clock tick is
    still seen.
    """
    parts: list[tuple] = []
    for base_path in plugin_paths():
        parts.append((str(base_path),))
        try:
            entries = sorted(item for item in base_path.iterdir() if item.is_dir())
        except OSError:
            continue
        for plugin_dir in entries:
            for name in ("manifest.json", "plugin.py"):
                candidate = plugin_dir / name
                try:
                    stat = candidate.stat()
                except OSError:
                    parts.append((str(candidate), None, None))
                else:
                    parts.append((str(candidate), stat.st_mtime_ns, stat.st_size))
    return tuple(parts)


def reset_plugin_discovery_cache() -> None:
    """Forget both memos.

    Needed when the `plugins` table can have changed without the files on disk
    changing -- a restore, or a test that rebuilds the schema. Installing or
    deleting a plugin does not need this: those rewrite the directory, so the
    fingerprint moves on its own.
    """
    _DISCOVERY_MEMO.update({"fingerprint": None, "result": None})
    _REGISTRY_SYNC_MEMO.update({"fingerprint": None, "result": None})
    # Loaded modules go too: a test that rebuilds a plugin directory expects the
    # next call to run the new code, and a module carrying a live HTTP session
    # should not outlive the discovery it belonged to.
    _RUNTIME_MODULE_MEMO.clear()


def discover_plugins() -> dict[str, Any]:
    fingerprint = plugin_source_fingerprint()
    if _DISCOVERY_MEMO["fingerprint"] == fingerprint and _DISCOVERY_MEMO["result"] is not None:
        return _DISCOVERY_MEMO["result"]
    result = _discover_plugins_uncached()
    _DISCOVERY_MEMO.update({"fingerprint": fingerprint, "result": result})
    return result


def _discover_plugins_uncached() -> dict[str, Any]:
    plugins: list[PluginDiscovery] = []
    errors: list[dict[str, Any]] = []
    seen_plugin_ids: set[str] = set()
    for base_path in plugin_paths():
        if not base_path.exists():
            continue
        for plugin_dir in sorted(item for item in base_path.iterdir() if item.is_dir()):
            try:
                manifest = load_manifest(plugin_dir)
                plugin_id = str(manifest.get("id") or "")
                if plugin_id in seen_plugin_ids:
                    continue
                module_path, runtime = load_runtime(manifest, plugin_dir)
                seen_plugin_ids.add(plugin_id)
                plugins.append(
                    PluginDiscovery(
                        manifest=manifest,
                        path=plugin_dir,
                        module_path=module_path,
                        runtime=runtime,
                    )
                )
            except Exception as exc:
                errors.append({"path": str(plugin_dir), "error": str(exc)})
    return {
        "paths": [str(path) for path in plugin_paths()],
        "plugins": plugins,
        "errors": errors,
    }


def discovered_plugin(plugin_id: str) -> tuple[PluginDiscovery | None, dict[str, Any]]:
    discovery = discover_plugins()
    for plugin in discovery["plugins"]:
        if plugin.plugin_id == plugin_id:
            return plugin, discovery
    return None, discovery


def run_plugin_health(plugin_id: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    plugin, discovery = discovered_plugin(plugin_id)
    if not plugin:
        return {
            "status": "error",
            "state": "not_found",
            "pluginId": plugin_id,
            "errors": discovery["errors"],
        }

    runtime = dict(plugin.runtime)
    result: dict[str, Any] = {
        "status": "ok",
        "state": "available",
        "pluginId": plugin_id,
        "runtime": runtime,
        "sourcePath": str(plugin.path),
        "runtimeModule": str(plugin.module_path) if plugin.module_path else None,
    }
    if runtime.get("error"):
        result.update({"status": "error", "state": "runtime_error"})
        return result
    if not runtime.get("loaded"):
        result.update({"state": "manifest_only"})
        return result
    if "health_check" not in (runtime.get("entrypoints") or []):
        result.update({"state": "no_health_check"})
        return result

    started = time.perf_counter()
    try:
        module = load_runtime_module(plugin)
        health_check = getattr(module, "health_check")
        health = health_check(context or {})
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if not isinstance(health, dict):
            health = {"result": health}
        result.update(
            {
                "health": health,
                "elapsedMs": elapsed_ms,
                "state": str(health.get("status") or "available"),
            }
        )
    except Exception as exc:
        result.update(
            {
                "status": "error",
                "state": "runtime_error",
                "error": str(exc),
            }
        )
    return result


def run_plugin_entrypoint(
    plugin_id: str,
    entrypoint: str,
    payload: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entrypoint = str(entrypoint or "").strip()
    plugin, discovery = discovered_plugin(plugin_id)
    if not plugin:
        return {
            "status": "error",
            "state": "not_found",
            "pluginId": plugin_id,
            "entrypoint": entrypoint,
            "errors": discovery["errors"],
        }

    runtime = dict(plugin.runtime)
    result: dict[str, Any] = {
        "status": "ok",
        "state": "completed",
        "pluginId": plugin_id,
        "entrypoint": entrypoint,
        "runtime": runtime,
        "sourcePath": str(plugin.path),
        "runtimeModule": str(plugin.module_path) if plugin.module_path else None,
    }
    if runtime.get("error"):
        result.update({"status": "error", "state": "runtime_error"})
        return result
    if not runtime.get("loaded"):
        result.update({"status": "error", "state": "manifest_only"})
        return result
    if entrypoint not in (runtime.get("entrypoints") or []):
        result.update({"status": "error", "state": "entrypoint_unavailable"})
        return result

    started = time.perf_counter()
    try:
        module = load_runtime_module(plugin)
        handler = getattr(module, entrypoint)
        if entrypoint == "health_check":
            value = handler(context or {})
        else:
            value = handler(payload or {}, context or {})
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        result.update(
            {
                "elapsedMs": elapsed_ms,
                "result": value if isinstance(value, dict) else {"value": value},
            }
        )
    except NotImplementedError as exc:
        result.update({"status": "error", "state": "not_implemented", "error": str(exc)})
    except Exception as exc:
        result.update({"status": "error", "state": "runtime_error", "error": str(exc)})
    return result


def plugin_attribution(manifest: dict[str, Any]) -> dict[str, Any] | None:
    """The credit a source requires DiscVault to display, as its manifest states it.

    Attribution is a licence obligation, not decoration: TMDB, TVDB and Fanart
    each require a specific credit, in specific words, and the words belong to
    the source. So the manifest carries them and DiscVault renders them -- there
    is no place here where a statement is composed, shortened or improved.

    Two shapes, and the difference matters:

    * ``statementKey`` / ``disclaimerKey`` name an existing i18n key. Use this
      only where the source's own terms are satisfied by a translated rendering,
      or where the translations were checked against those terms. TMDB's card is
      on this path because its copy was already translated and reviewed.
    * ``statement`` / ``disclaimer`` are literal text, rendered **untranslated**.
      This is the safe default for a new source: a machine translation of a
      required sentence is a different sentence, and nobody here can tell whether
      the licence still accepts it.

    A source that declares no attribution gets no card. That is a real answer --
    not every source asks for one -- and it is better than inventing wording to
    fill a gap.
    """
    raw = manifest.get("attribution")
    if not isinstance(raw, dict):
        return None
    statement = str(raw.get("statement") or "").strip()
    statement_key = str(raw.get("statementKey") or "").strip()
    if not statement and not statement_key:
        return None
    logo = str(raw.get("logo") or "").strip()
    # A path, not a name, is a mistake worth refusing rather than sanitising:
    # silently reading `logo.svg` when the manifest asked for `../../secret.svg`
    # would hide the mistake instead of surfacing it.
    if logo and Path(logo).name != logo:
        logo = ""
    url = str(raw.get("url") or "").strip()
    if url and not url.startswith("https://"):
        url = ""
    return {
        "statement": statement,
        "statementKey": statement_key,
        "disclaimer": str(raw.get("disclaimer") or "").strip(),
        "disclaimerKey": str(raw.get("disclaimerKey") or "").strip(),
        "logo": logo,
        "url": url,
    }


def replacement_plugin_ids(manifest: dict[str, Any]) -> list[str]:
    raw = (
        manifest.get("replacesPlugins")
        or manifest.get("replacesPluginIds")
        or manifest.get("replaces")
        or []
    )
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    plugin_id = str(manifest.get("id") or "")
    values: list[str] = []
    for item in raw:
        value = str(item or "").strip()
        if value and value != plugin_id and value not in values:
            values.append(value)
    return values


def reconcile_plugin_replacements(
    conn,
    *,
    plugins: list[PluginDiscovery],
    has_plugins_table: bool,
    has_metadata_plugins_table: bool,
) -> None:
    if not has_metadata_plugins_table:
        return
    replacements: dict[str, list[str]] = {}
    manifest_orders: dict[str, int] = {}
    for plugin in plugins:
        replaced = replacement_plugin_ids(plugin.manifest)
        if replaced:
            replacements[plugin.plugin_id] = replaced
            try:
                manifest_orders[plugin.plugin_id] = int(plugin.manifest.get("orderIndex") or 100)
            except (TypeError, ValueError):
                manifest_orders[plugin.plugin_id] = 100
    if not replacements:
        return

    for replacement_id, replaced_ids in replacements.items():
        ids = [replacement_id, *replaced_ids]
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, enabled, order_index
                    FROM metadata_plugins
                    WHERE id = ANY(%s)
                    """,
                    (ids,),
                )
                rows = {str(row["id"]): row for row in cur.fetchall()}
                replacement_row = rows.get(replacement_id)
                if not replacement_row:
                    continue
                active_legacy_rows = [
                    row
                    for plugin_id, row in rows.items()
                    if plugin_id != replacement_id and bool(row.get("enabled"))
                ]
                if not active_legacy_rows:
                    continue
                legacy_row = min(
                    active_legacy_rows,
                    key=lambda row: int(row.get("order_index") or 9999),
                )
                inherited_order = int(
                    legacy_row.get("order_index") or replacement_row.get("order_index") or 100
                )
                # A replacement inherits the legacy plugin's enabled state, not a
                # licence to outrank where DiscVault ships it. Without this floor
                # a legacy row sitting at a low order_index (a v25 import writes
                # index * 10) would promote the replacement above sources that are
                # deliberately ranked higher.
                order_index = max(inherited_order, manifest_orders.get(replacement_id, 100))
                cur.execute(
                    """
                    UPDATE metadata_plugins
                    SET enabled=true,
                        order_index=%s,
                        updated_at=now()
                    WHERE id=%s
                    """,
                    (order_index, replacement_id),
                )
                cur.execute(
                    """
                    UPDATE metadata_plugins
                    SET enabled=false,
                        updated_at=now()
                    WHERE id = ANY(%s)
                    """,
                    (replaced_ids,),
                )
                if has_plugins_table:
                    cur.execute(
                        """
                        UPDATE plugins
                        SET enabled=true,
                            order_index=%s,
                            updated_at=now()
                        WHERE id=%s
                        """,
                        (order_index, replacement_id),
                    )
                    cur.execute(
                        """
                        UPDATE plugins
                        SET enabled=false,
                            updated_at=now()
                        WHERE id = ANY(%s)
                        """,
                        (replaced_ids,),
                    )
        except Exception:
            continue


def sync_plugin_registry(
    conn, table_exists: TableExists, Jsonb: JsonbFactory, *, force: bool = False
) -> dict[str, Any]:
    """Bring the `plugins` tables in step with the plugin files on disk.

    Skipped entirely when the files have not changed since this process last
    completed a sync, because the cost is not the discovery -- it is the writes.
    Every call upserts a row per plugin, and those rows stay row-locked until
    the surrounding transaction commits.

    That mattered far more than it looks. Every "list the plugins" helper called
    this, including ones on plain read paths (`collection_plugin_preview_entities`
    behind the dashboard snapshot, `metadata_plugin_entities` behind the sync
    bootstrap), so a page load was a writer on this table. Meanwhile the metadata
    pipeline calls it too -- and then, inside the same transaction, waits on a
    provider over the network. Observed on a live instance: one transaction idle
    in transaction for 14.5 s holding those locks, and three page loads stalled
    behind it on `INSERT INTO plugins`. A slow provider froze the whole app,
    through a table none of those pages had any reason to write to.

    Skipping on an unchanged fingerprint takes no locks at all, which is what
    breaks that coupling. The memo is per process and keyed on the files, so a
    plugin installed or deleted anywhere -- by another worker, or by hand --
    changes the fingerprint and the next call syncs. It does *not* notice the
    table being emptied while the files stay put; `reset_plugin_discovery_cache`
    exists for that, and `force=True` for a caller that must not be skipped.
    """
    fingerprint = plugin_source_fingerprint()
    if (
        not force
        and _REGISTRY_SYNC_MEMO["fingerprint"] == fingerprint
        and _REGISTRY_SYNC_MEMO["result"] is not None
    ):
        return dict(_REGISTRY_SYNC_MEMO["result"])
    result = _sync_plugin_registry_uncached(conn, table_exists, Jsonb)
    _REGISTRY_SYNC_MEMO.update({"fingerprint": fingerprint, "result": dict(result)})
    return result


def _sync_plugin_registry_uncached(
    conn, table_exists: TableExists, Jsonb: JsonbFactory
) -> dict[str, Any]:
    discovery = discover_plugins()
    plugins: list[PluginDiscovery] = discovery["plugins"]
    synced_plugin_ids: list[str] = []
    synced_metadata_ids: list[str] = []
    has_plugins_table = table_exists(conn, "plugins")
    has_metadata_plugins_table = table_exists(conn, "metadata_plugins")
    has_plugin_settings_table = table_exists(conn, "plugin_settings")
    has_metadata_plugin_settings_table = table_exists(conn, "metadata_plugin_settings")

    if has_plugins_table:
        with conn.transaction():
            with conn.cursor() as cur:
                for plugin in plugins:
                    manifest = plugin.manifest
                    manifest_payload = manifest_with_runtime(plugin)
                    settings_schema = enforced_settings_schema(manifest)
                    manifest_payload["settingsSchema"] = settings_schema
                    categories = manifest["categories"]
                    capabilities = manifest["capabilities"]
                    cur.execute(
                        """
                        INSERT INTO plugins (
                            id,
                            name,
                            version,
                            enabled,
                            installed,
                            categories,
                            capabilities,
                            order_index,
                            manifest,
                            settings_schema,
                            premium_feature_key,
                            source_path,
                            runtime_module,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, true, %s, %s, %s, %s, %s, %s, %s, %s, now())
                        ON CONFLICT (id) DO UPDATE SET
                            name=EXCLUDED.name,
                            version=EXCLUDED.version,
                            installed=true,
                            categories=EXCLUDED.categories,
                            capabilities=EXCLUDED.capabilities,
                            manifest=EXCLUDED.manifest,
                            settings_schema=EXCLUDED.settings_schema,
                            premium_feature_key=EXCLUDED.premium_feature_key,
                            source_path=EXCLUDED.source_path,
                            runtime_module=EXCLUDED.runtime_module,
                            updated_at=now()
                        """,
                        (
                            manifest["id"],
                            manifest["name"],
                            manifest["version"],
                            bool(manifest.get("defaultEnabled", False)),
                            Jsonb(categories),
                            Jsonb(capabilities),
                            int(manifest.get("orderIndex") or 100),
                            Jsonb(manifest_payload),
                            Jsonb(settings_schema),
                            manifest.get("premiumFeatureKey"),
                            str(plugin.path),
                            str(plugin.module_path) if plugin.module_path else None,
                        ),
                    )
                    defaults = plugin_setting_defaults(settings_schema)
                    if has_plugin_settings_table and defaults:
                        cur.execute(
                            """
                            INSERT INTO plugin_settings (plugin_id, settings, secrets_ref)
                            VALUES (%s, %s, '{}'::jsonb)
                            ON CONFLICT (plugin_id) DO UPDATE SET
                                settings=EXCLUDED.settings || plugin_settings.settings,
                                updated_at=now()
                            WHERE plugin_settings.settings IS DISTINCT FROM
                                EXCLUDED.settings || plugin_settings.settings
                            """,
                            (manifest["id"], Jsonb(defaults)),
                        )
                    synced_plugin_ids.append(manifest["id"])
                if synced_plugin_ids:
                    cur.execute(
                        """
                        UPDATE plugins
                        SET installed=false,
                            enabled=false,
                            updated_at=now()
                        WHERE NOT (id = ANY(%s))
                          AND installed=true
                        """,
                        (synced_plugin_ids,),
                    )

    if has_metadata_plugins_table:
        with conn.transaction():
            with conn.cursor() as cur:
                for plugin in plugins:
                    manifest = plugin.manifest
                    manifest_payload = manifest_with_runtime(plugin)
                    settings_schema = enforced_settings_schema(manifest)
                    manifest_payload["settingsSchema"] = settings_schema
                    categories = manifest["categories"]
                    if not {"metadata_source", "metadata_receiver"}.intersection(categories):
                        continue
                    cur.execute(
                        """
                        INSERT INTO metadata_plugins (
                            id,
                            name,
                            version,
                            enabled,
                            installed,
                            order_index,
                            manifest,
                            settings_schema,
                            premium_feature_key,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, true, %s, %s, %s, %s, now())
                        ON CONFLICT (id) DO UPDATE SET
                            name=EXCLUDED.name,
                            version=EXCLUDED.version,
                            installed=true,
                            manifest=EXCLUDED.manifest,
                            settings_schema=EXCLUDED.settings_schema,
                            premium_feature_key=EXCLUDED.premium_feature_key,
                            updated_at=now()
                        """,
                        (
                            manifest["id"],
                            manifest["name"],
                            manifest["version"],
                            bool(manifest.get("defaultEnabled", False)),
                            int(manifest.get("orderIndex") or 100),
                            Jsonb(manifest_payload),
                            Jsonb(settings_schema),
                            manifest.get("premiumFeatureKey"),
                        ),
                    )
                    defaults = plugin_setting_defaults(settings_schema)
                    if has_metadata_plugin_settings_table and defaults:
                        cur.execute(
                            """
                            INSERT INTO metadata_plugin_settings (plugin_id, settings, secrets_ref)
                            VALUES (%s, %s, '{}'::jsonb)
                            ON CONFLICT (plugin_id) DO UPDATE SET
                                settings=EXCLUDED.settings || metadata_plugin_settings.settings,
                                updated_at=now()
                            WHERE metadata_plugin_settings.settings IS DISTINCT FROM
                                EXCLUDED.settings || metadata_plugin_settings.settings
                            """,
                            (manifest["id"], Jsonb(defaults)),
                        )
                    synced_metadata_ids.append(manifest["id"])
                if synced_metadata_ids:
                    cur.execute(
                        """
                        UPDATE metadata_plugins
                        SET installed=false,
                            enabled=false,
                            updated_at=now()
                        WHERE NOT (id = ANY(%s))
                          AND installed=true
                        """,
                        (synced_metadata_ids,),
                    )

                if has_plugins_table:
                    cur.execute(
                        """
                        UPDATE plugins p
                        SET
                            enabled=mp.enabled,
                            order_index=mp.order_index,
                            updated_at=now()
                        FROM metadata_plugins mp
                        WHERE p.id=mp.id
                        """
                    )
        with conn.transaction():
            reconcile_plugin_replacements(
                conn,
                plugins=plugins,
                has_plugins_table=has_plugins_table,
                has_metadata_plugins_table=has_metadata_plugins_table,
            )

    return {
        "paths": discovery["paths"],
        "discovered": len(plugins),
        "syncedPlugins": synced_plugin_ids,
        "syncedMetadataPlugins": synced_metadata_ids,
        "errors": discovery["errors"],
    }


def manifest_with_runtime(plugin: PluginDiscovery) -> dict[str, Any]:
    manifest = dict(plugin.manifest)
    manifest["runtime"] = plugin.runtime
    return manifest


def plugin_registry_snapshot(conn, table_exists: TableExists, Jsonb: JsonbFactory) -> dict[str, Any]:
    sync = sync_plugin_registry(conn, table_exists, Jsonb)
    rows: list[dict[str, Any]] = []
    if table_exists(conn, "plugins"):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    p.id,
                    p.name,
                    p.version,
                    p.enabled,
                    p.installed,
                    p.categories,
                    p.capabilities,
                    p.order_index,
                    p.manifest,
                    p.settings_schema,
                    p.premium_feature_key,
                    p.source_path,
                    p.runtime_module,
                    p.updated_at,
                    s.settings,
                    s.secrets_ref
                FROM plugins p
                LEFT JOIN plugin_settings s ON s.plugin_id = p.id
                WHERE p.installed = true
                ORDER BY p.order_index, p.name
                """
            )
            rows = cur.fetchall()
    return {
        "status": "ok",
        "paths": sync["paths"],
        "sync": sync,
        "plugins": [plugin_registry_row(row) for row in rows],
    }


def plugin_registry_row(row: dict[str, Any]) -> dict[str, Any]:
    manifest = row.get("manifest") or {}
    categories = row.get("categories") or manifest.get("categories") or []
    capabilities = row.get("capabilities") or manifest.get("capabilities") or []
    settings_schema = row.get("settings_schema") or {}
    config = plugin_config_payload(
        settings_schema,
        row.get("settings"),
        row.get("secrets_ref"),
    )
    update_state = plugin_update_state(str(row["id"]), str(row.get("version") or ""))
    return {
        "id": row["id"],
        "name": row["name"],
        "version": row["version"],
        "enabled": bool(row["enabled"]),
        "installed": bool(row["installed"]),
        "categories": categories,
        "capabilities": capabilities,
        "orderIndex": row["order_index"],
        "manifest": manifest,
        "requiresSecrets": bool(manifest.get("requiresSecrets", False)),
        "settingsSchema": settings_schema,
        "settingsConfigured": config["settingsConfigured"],
        "secretsConfigured": config["secretsConfigured"],
        "premiumFeatureKey": row.get("premium_feature_key"),
        "sourcePath": row.get("source_path"),
        "runtimeModule": row.get("runtime_module"),
        "runtime": manifest.get("runtime") or {},
        "updatedAt": row.get("updated_at"),
        "isBundledDefault": update_state["isBundledDefault"],
        "bundledVersion": update_state["bundledVersion"],
        "updateAvailable": update_state["updateAvailable"],
        "canRollback": update_state["canRollback"],
        "rollbackVersion": update_state["rollbackVersion"],
        # movievault_v2's anonymous bucket fallback is enforced server-side (see
        # ENFORCED_PLUGIN_SETTINGS above) and has no UI control of its own, so
        # there is nothing in settingsSchema for App Admin to render it from.
        # Surface the live value directly, analogous to premiumFeatureKey, so
        # the plugin card can show it's always on instead of saying nothing.
        **(
            {"bucketFallbackEnforced": enforced_bucket_fallback()}
            if row["id"] == "movievault_v2"
            else {}
        ),
    }


# Metadata/integration plugins surfaced in the "plugins still not configured"
# notice shown after a metadata refresh. Order is the order they appear in the
# message. Adding a future integration is a single extra entry here.
INTEGRATION_PLUGIN_NOTICE_IDS: tuple[str, ...] = (
    "tmdb",
    "plex",
    "jellyfin",
    "trakt",
)

# Display-name fallbacks used when a plugin has not been discovered/installed
# yet (e.g. right after a version install), so it can never silently drop out
# of the notice.
INTEGRATION_PLUGIN_FALLBACK_NAMES: dict[str, str] = {
    "tmdb": "TMDb",
    "plex": "Plex",
    "jellyfin": "Jellyfin",
    "trakt": "Trakt",
}

# Plugins that switch themselves on the moment their required credentials are
# stored. Saving a TMDb key and then having to hunt for a second toggle is a dead
# end users fall into, so configuring one of these IS the intent to use it.
#
# Scoped to the integrations DiscVault already nags about being unconfigured
# rather than applied to every plugin: a blanket rule would silently switch on the
# price scrapers (keepa, priceapi, amazon, bol, zavvi, arrow) as soon as a key is
# stored, and starting to scrape shops is a separate, deliberate act. Widening
# this is one entry.
AUTO_ENABLE_ON_CONFIG_PLUGIN_IDS: frozenset[str] = frozenset(INTEGRATION_PLUGIN_NOTICE_IDS)


def _plugin_has_required_settings(plugin: dict[str, Any]) -> bool:
    schema = plugin.get("settingsSchema") or {}
    settings = schema.get("settings") if isinstance(schema, dict) else None
    if not isinstance(settings, list):
        return False
    return any(isinstance(item, dict) and item.get("required") for item in settings)


def _plugin_is_active(plugin: dict[str, Any]) -> bool:
    """A plugin counts as active/configured when it is enabled and any required
    secrets/settings have been provided."""
    if not plugin.get("enabled"):
        return False
    if plugin.get("requiresSecrets") and not plugin.get("secretsConfigured"):
        return False
    if _plugin_has_required_settings(plugin) and not plugin.get("settingsConfigured"):
        return False
    return True


def unconfigured_integration_plugins(
    conn, table_exists: TableExists, Jsonb: JsonbFactory
) -> list[dict[str, str]]:
    """Return display info for metadata/integration plugins that are still
    disabled or not configured, in notice order. Each entry is
    ``{"id": ..., "name": ...}``."""
    try:
        snapshot = plugin_registry_snapshot(conn, table_exists, Jsonb)
    except Exception:
        snapshot = {"plugins": []}
    by_id = {plugin.get("id"): plugin for plugin in snapshot.get("plugins", [])}
    result: list[dict[str, str]] = []
    for plugin_id in INTEGRATION_PLUGIN_NOTICE_IDS:
        plugin = by_id.get(plugin_id)
        fallback_name = INTEGRATION_PLUGIN_FALLBACK_NAMES.get(plugin_id, plugin_id)
        if plugin is None:
            result.append({"id": plugin_id, "name": fallback_name})
            continue
        if not _plugin_is_active(plugin):
            result.append({"id": plugin_id, "name": plugin.get("name") or fallback_name})
    return result
