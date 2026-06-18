"""DiscVault Next plugin discovery and registry synchronization."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


JsonbFactory = Callable[[Any], Any]
TableExists = Callable[[Any, str], bool]

DEFAULT_PLUGIN_DIR = Path(__file__).resolve().parent / "next_plugins"
VALID_CATEGORIES = {
    "metadata_source",
    "metadata_bootstrap",
    "metadata_receiver",
    "digital_media_source",
    "import_source",
    "personal_list_source",
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
    "sync_personal_lists",
    "playback_deeplink",
    "inspect_source",
    "plan_import",
    "import_source",
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


def plugin_paths() -> list[Path]:
    configured = os.environ.get("DISCVAULT_PLUGIN_PATHS", "").strip()
    paths = [plugin_install_dir(), DEFAULT_PLUGIN_DIR]
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
    settings = row.get("settings") or {}
    secrets_ref = row.get("secrets_ref") or {}
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
        "settingsSchema": row.get("settings_schema") or {},
        "settingsConfigured": bool(settings),
        "secretsConfigured": bool(secrets_ref),
        "premiumFeatureKey": row.get("premium_feature_key"),
        "sourcePath": row.get("source_path"),
        "runtimeModule": row.get("runtime_module"),
        "runtime": manifest.get("runtime") or {},
        "updatedAt": row.get("updated_at"),
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
