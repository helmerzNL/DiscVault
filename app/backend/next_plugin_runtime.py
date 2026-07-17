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
# Optional in-process override of the auto-update preference, set by the app
# layer from the database setting. ``None`` falls back to env/marker/default.
_plugin_auto_update_override: bool | None = None
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
    return {
        "isBundledDefault": bundled_default_plugin_path(plugin_id) is not None,
        "bundledVersion": bundled,
        "installedVersion": installed,
        "updateAvailable": update_available,
        "canRollback": plugin_has_backup(plugin_id),
        "rollbackVersion": plugin_backup_version(plugin_id),
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
    if not plugin_auto_update_enabled():
        result["disabled"] = True
        return result
    for source in bundled_default_plugin_dirs():
        target = install_dir / source.name
        if not target.exists():
            if source.name in marker_seeded:
                # Respect a user-deleted seeded default; never resurrect it.
                continue
            if marker_payload is None:
                # Legacy marker payloads do not track seeded plugin ids.
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
        try:
            _replace_installed_plugin(source.name, source)
            result["upgraded"].append(
                {
                    "plugin": source.name,
                    "from": installed_version,
                    "to": bundled_version,
                }
            )
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


def load_runtime_module(plugin: PluginDiscovery):
    if not plugin.module_path:
        return None
    spec = importlib.util.spec_from_file_location(
        module_name_for(plugin.plugin_id, plugin.module_path),
        plugin.module_path,
    )
    if not spec or not spec.loader:
        raise RuntimeError("Could not create module spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def discover_plugins() -> dict[str, Any]:
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
    for plugin in plugins:
        replaced = replacement_plugin_ids(plugin.manifest)
        if replaced:
            replacements[plugin.plugin_id] = replaced
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
                order_index = int(legacy_row.get("order_index") or replacement_row.get("order_index") or 100)
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


def sync_plugin_registry(conn, table_exists: TableExists, Jsonb: JsonbFactory) -> dict[str, Any]:
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
                            Jsonb(manifest["settingsSchema"]),
                            manifest.get("premiumFeatureKey"),
                            str(plugin.path),
                            str(plugin.module_path) if plugin.module_path else None,
                        ),
                    )
                    defaults = plugin_setting_defaults(manifest["settingsSchema"])
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
                            Jsonb(manifest["settingsSchema"]),
                            manifest.get("premiumFeatureKey"),
                        ),
                    )
                    defaults = plugin_setting_defaults(manifest["settingsSchema"])
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
    }


# Metadata/integration plugins surfaced in the "plugins still not configured"
# notice shown after a metadata refresh. Order is the order they appear in the
# message. Adding a future integration is a single extra entry here.
INTEGRATION_PLUGIN_NOTICE_IDS: tuple[str, ...] = (
    "tmdb",
    "bluray_com",
    "plex",
    "jellyfin",
    "trakt",
)

# Display-name fallbacks used when a plugin has not been discovered/installed
# yet (e.g. right after a version install), so it can never silently drop out
# of the notice.
INTEGRATION_PLUGIN_FALLBACK_NAMES: dict[str, str] = {
    "tmdb": "TMDb",
    "bluray_com": "Blu-ray.com",
    "plex": "Plex",
    "jellyfin": "Jellyfin",
    "trakt": "Trakt",
}


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
