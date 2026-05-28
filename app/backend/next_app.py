"""Minimal PostgreSQL-backed API surface for DiscVault Next.

This Flask app is deliberately separate from the current SQLite runtime in
``app.py``. It is the first executable Next backend: it can verify PostgreSQL
connectivity, expose migration state, list metadata plugins, and read a small
collection summary from the new schema.
"""

from __future__ import annotations

import html as html_lib
import json as json_lib
import os
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from werkzeug.exceptions import HTTPException

try:
    from .next_database import discover_migrations
    from .next_import import CLIENT_SYNC_SETTING_KEYS
    from .next_import import ImportError as NextImportError
    from .next_import import NextImporter
    from .next_import import apply_legacy_metadata_plugin_plan
    from .next_import import clean_text
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_database import discover_migrations
    from next_import import CLIENT_SYNC_SETTING_KEYS
    from next_import import ImportError as NextImportError
    from next_import import NextImporter
    from next_import import apply_legacy_metadata_plugin_plan
    from next_import import clean_text


MIGRATION_JOB_TYPE = "migration.import_sqlite"
TARGET_DATA_TABLES = (
    "movies",
    "people",
    "movie_credits",
    "containers",
    "container_movies",
    "collection_items",
    "media_assets",
    "users",
)


def create_app() -> Flask:
    flask_app = Flask(__name__)
    CORS(flask_app, supports_credentials=True)
    register_routes(flask_app)
    return flask_app

class NextApiError(RuntimeError):
    """Expected API error with a caller-facing status code."""

    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


def database_url() -> str:
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise NextApiError("DATABASE_URL is not configured", 503)
    return value


def connect():
    import psycopg

    return psycopg.connect(database_url(), row_factory=dict_row, autocommit=False)


def build_version() -> str:
    return (
        os.environ.get("DISCVAULT_BACKEND_VERSION")
        or os.environ.get("BUILD_VERSION")
        or "next-dev"
    )


def build_sha() -> str:
    return os.environ.get("DISCVAULT_BUILD_SHA") or os.environ.get("BUILD_SHA") or "unknown"


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [json_ready(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def response(payload: dict[str, Any], status: int = 200):
    return jsonify(json_ready(payload)), status


def html_response(html: str):
    result = Response(html, mimetype="text/html")
    result.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    result.headers["Pragma"] = "no-cache"
    result.headers["Expires"] = "0"
    return result


def table_exists(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS table_name", (f"public.{table_name}",))
        row = cur.fetchone()
    return bool(row and row["table_name"])


def count_table(conn, table_name: str) -> int:
    if not table_exists(conn, table_name):
        return 0
    with conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) AS count FROM "{table_name}"')
        row = cur.fetchone()
    return int(row["count"] if row else 0)


def legacy_data_dir() -> Path:
    raw = (
        os.environ.get("DISCVAULT_LEGACY_DATA_DIR")
        or os.environ.get("DISCVAULT_SQLITE_IMPORT_DATA")
        or "/data"
    )
    return Path(raw).expanduser()


def legacy_sqlite_db(data_dir: Path) -> Path:
    raw = os.environ.get("DISCVAULT_LEGACY_SQLITE_DB")
    if raw:
        return Path(raw).expanduser()
    return data_dir / "discvault.db"


def target_data_counts(conn) -> dict[str, int]:
    return {table: count_table(conn, table) for table in TARGET_DATA_TABLES}


def target_database_empty(counts: dict[str, int]) -> bool:
    return all(value == 0 for value in counts.values())


def migration_run_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": row["id"],
        "sourceKind": row["source_kind"],
        "sourceVersion": row.get("source_version"),
        "sourceDatabaseHash": row.get("source_database_hash"),
        "exportManifest": row.get("export_manifest") or {},
        "status": row["status"],
        "result": row.get("result") or {},
        "error": row.get("error"),
        "startedAt": row.get("started_at"),
        "completedAt": row.get("completed_at"),
    }


def latest_migration_run(conn, source_hash: str | None = None) -> dict[str, Any] | None:
    if not table_exists(conn, "migration_runs"):
        return None
    params: list[Any] = []
    where = ""
    if source_hash:
        where = "WHERE source_database_hash = %s"
        params.append(source_hash)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                id,
                source_kind,
                source_version,
                source_database_hash,
                export_manifest,
                status,
                result,
                error,
                started_at,
                completed_at
            FROM migration_runs
            {where}
            ORDER BY started_at DESC
            LIMIT 1
            """,
            params,
        )
        return cur.fetchone()


def active_migration_job(conn, source_hash: str | None = None) -> dict[str, Any] | None:
    if not table_exists(conn, "background_jobs"):
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id,
                job_type,
                status,
                requested_by,
                payload,
                result,
                error,
                created_at,
                started_at,
                finished_at
            FROM background_jobs
            WHERE job_type=%s AND status IN ('pending', 'running')
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (MIGRATION_JOB_TYPE,),
        )
        rows = cur.fetchall()
    if not source_hash:
        return rows[0] if rows else None
    for row in rows:
        payload = row.get("payload") or {}
        if payload.get("sourceDatabaseHash") == source_hash:
            return row
    return None


def migration_job_summary(conn, job_id: UUID | None = None) -> dict[str, Any] | None:
    if not table_exists(conn, "background_jobs"):
        return None
    params: list[Any] = [MIGRATION_JOB_TYPE]
    where = "WHERE job_type=%s"
    if job_id:
        where += " AND id=%s"
        params.append(job_id)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                id,
                job_type,
                status,
                requested_by,
                payload,
                result,
                error,
                created_at,
                started_at,
                finished_at
            FROM background_jobs
            {where}
            ORDER BY created_at DESC
            LIMIT 1
            """,
            params,
        )
        row = cur.fetchone()
    return job_row(row) if row else None


def plugin_summary(conn) -> dict[str, Any]:
    if not table_exists(conn, "metadata_plugins"):
        return {"total": 0, "enabled": 0, "disabled": 0, "items": []}
    plugins = metadata_plugin_entities(conn)
    enabled = [plugin for plugin in plugins if plugin["enabled"]]
    return {
        "total": len(plugins),
        "enabled": len(enabled),
        "disabled": len(plugins) - len(enabled),
        "order": [plugin["id"] for plugin in plugins],
        "enabledOrder": [plugin["id"] for plugin in enabled],
        "items": [
            {
                "id": plugin["id"],
                "name": plugin["name"],
                "enabled": plugin["enabled"],
                "orderIndex": plugin["orderIndex"],
                "settingsConfigured": plugin["settingsConfigured"],
                "secretsConfigured": plugin["secretsConfigured"],
                "premiumFeatureKey": plugin["premiumFeatureKey"],
            }
            for plugin in plugins
        ],
    }


def sqlite_readiness_probe(data_dir: Path, sqlite_db: Path) -> dict[str, Any]:
    found = sqlite_db.exists() and sqlite_db.is_file()
    source: dict[str, Any] = {
        "dataDir": str(data_dir),
        "sqliteDb": str(sqlite_db),
        "found": found,
        "readable": False,
        "sourceDatabaseHash": None,
        "sourceCounts": {},
        "mediaExtensions": {},
        "mediaMigrationMode": "reference_existing_files",
        "options": {
            "includeSecurity": False,
            "includePersonal": False,
            "importMediaReferences": True,
        },
    }
    warnings: list[str] = []
    if not found:
        warnings.append(f"No legacy SQLite database found at {sqlite_db}.")
        return {"source": source, "warnings": warnings}

    importer: NextImporter | None = None
    try:
        importer = NextImporter(
            sqlite_db,
            data_dir,
            include_security=False,
            include_personal=False,
            import_media=True,
            owner_username=None,
        )
        dry_run = importer.dry_run()
        source.update(
            {
                "readable": True,
                "sourceDatabaseHash": dry_run.get("source_database_sha256"),
                "sourceCounts": dry_run.get("source_counts") or {},
                "mediaExtensions": dry_run.get("media_extensions") or {},
            }
        )
    except (NextImportError, OSError, RuntimeError, ValueError) as exc:
        warnings.append(f"Legacy SQLite database could not be inspected: {exc}")
    finally:
        if importer is not None:
            importer.sqlite.close()
    return {"source": source, "warnings": warnings}


def migration_readiness(conn) -> dict[str, Any]:
    migrations = migration_overview(conn)
    data_dir = legacy_data_dir()
    sqlite_db = legacy_sqlite_db(data_dir)
    probe = sqlite_readiness_probe(data_dir, sqlite_db)
    source = probe["source"]
    warnings = list(probe["warnings"])
    target_counts = target_data_counts(conn)
    target_empty = target_database_empty(target_counts)
    source_hash = clean_text(source.get("sourceDatabaseHash"))
    latest_run = latest_migration_run(conn, source_hash)
    active_job = active_migration_job(conn, source_hash)
    latest = migration_run_row(latest_run)
    active = job_row(active_job) if active_job else None
    required_actions: list[str] = []

    if migrations["state"] != "ready":
        state = "blocked_schema_not_ready"
        required_actions.append("Apply pending PostgreSQL migrations before starting import.")
    elif active:
        state = "running"
        required_actions.append("Wait for the active migration job to finish.")
    elif not source["found"]:
        state = "not_required"
        required_actions.append("Mount the legacy DiscVault data directory at /data if migration is expected.")
    elif not source["readable"]:
        state = "blocked_source_unreadable"
        required_actions.append("Fix the legacy SQLite database path or file permissions.")
    elif latest and latest["status"] == "completed":
        state = "already_completed"
        required_actions.append("A completed migration for this source database already exists.")
    elif not target_empty:
        state = "blocked_target_not_empty"
        required_actions.append("Use an empty PostgreSQL target database or start an explicit conflict-aware import later.")
    else:
        state = "ready_for_confirmation"
        required_actions.append("Confirm the migration in the UI or call the migration start endpoint.")

    can_start = state == "ready_for_confirmation"
    return {
        "state": state,
        "canStart": can_start,
        "requiresConfirmation": can_start,
        "legacyData": source,
        "targetDatabase": {
            "empty": target_empty,
            "counts": target_counts,
        },
        "migrations": migrations,
        "activeJob": active,
        "latestRun": latest,
        "warnings": warnings,
        "requiredActions": required_actions,
    }


def migration_report(conn) -> dict[str, Any]:
    readiness = migration_readiness(conn)
    latest_run = readiness.get("latestRun")
    latest_job = None
    if latest_run:
        latest_job = migration_job_summary(conn)
    elif readiness.get("activeJob"):
        active_id = UUID(str(readiness["activeJob"]["id"]))
        latest_job = migration_job_summary(conn, active_id)

    latest_result = latest_run.get("result") if latest_run else {}
    source_counts = readiness.get("legacyData", {}).get("sourceCounts") or {}
    imported = latest_result.get("counters") if isinstance(latest_result, dict) else {}
    skipped = latest_result.get("skipped") if isinstance(latest_result, dict) else {}
    warnings = list(readiness.get("warnings") or [])
    if isinstance(latest_result, dict):
        warnings.extend(latest_result.get("warnings") or [])

    return {
        "state": readiness["state"],
        "canStart": readiness["canStart"],
        "requiresConfirmation": readiness["requiresConfirmation"],
        "source": {
            "found": readiness["legacyData"]["found"],
            "readable": readiness["legacyData"]["readable"],
            "dataDir": readiness["legacyData"]["dataDir"],
            "sqliteDb": readiness["legacyData"]["sqliteDb"],
            "sourceDatabaseHash": readiness["legacyData"]["sourceDatabaseHash"],
            "counts": source_counts,
            "mediaExtensions": readiness["legacyData"]["mediaExtensions"],
            "mediaMigrationMode": readiness["legacyData"]["mediaMigrationMode"],
        },
        "target": readiness["targetDatabase"],
        "latestRun": latest_run,
        "latestJob": latest_job,
        "summary": {
            "imported": imported or {},
            "skipped": skipped or {},
            "warnings": warnings,
        },
        "metadataPlugins": plugin_summary(conn),
        "requiredActions": readiness["requiredActions"],
    }


def migration_dashboard_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DiscVault Next Migration</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #111217;
      --panel: #1a1d25;
      --panel-2: #222632;
      --line: #343949;
      --text: #f3f4f8;
      --muted: #a9afbf;
      --accent: #e8c547;
      --blue: #7aa7ff;
      --green: #48c78e;
      --red: #ff6b6b;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main {
      width: min(1180px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 32px 0 48px;
    }
    header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 24px;
    }
    h1, h2, p { margin: 0; }
    h1 { font-size: clamp(1.7rem, 3vw, 2.5rem); font-weight: 760; letter-spacing: 0; }
    h2 { font-size: 1rem; margin-bottom: 12px; }
    p { color: var(--muted); line-height: 1.55; }
    button, a.button {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-2);
      color: var(--text);
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 38px;
      padding: 0 14px;
      text-decoration: none;
      white-space: nowrap;
    }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #111217;
      font-weight: 700;
    }
    button:disabled {
      cursor: not-allowed;
      opacity: .48;
    }
    .actions { display: flex; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }
    .grid {
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 14px;
    }
    .card {
      grid-column: span 4;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      min-width: 0;
    }
    .wide { grid-column: span 8; }
    .full { grid-column: 1 / -1; }
    .metric {
      color: var(--text);
      font-size: 2rem;
      font-weight: 780;
      line-height: 1.1;
    }
    .label {
      color: var(--muted);
      font-size: .82rem;
      margin-top: 6px;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      border: 1px solid var(--line);
      padding: 4px 10px;
      font-size: .8rem;
      color: var(--muted);
      background: rgba(255,255,255,.03);
    }
    .badge.ok { color: var(--green); border-color: rgba(72,199,142,.45); }
    .badge.warn { color: var(--accent); border-color: rgba(232,197,71,.45); }
    .badge.error { color: var(--red); border-color: rgba(255,107,107,.45); }
    .list {
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }
    .row {
      display: grid;
      grid-template-columns: minmax(120px, .8fr) minmax(0, 1.3fr);
      gap: 12px;
      padding: 9px 0;
      border-bottom: 1px solid rgba(255,255,255,.08);
      color: var(--muted);
      min-width: 0;
    }
    .row:last-child { border-bottom: 0; }
    .row strong {
      color: var(--text);
      font-weight: 620;
      overflow-wrap: anywhere;
    }
    .plugins {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 10px;
    }
    .plugin {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-2);
      padding: 12px;
    }
    .plugin strong { display: block; margin-bottom: 6px; }
    .mono {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: .82rem;
      overflow-wrap: anywhere;
    }
    #message {
      margin-top: 12px;
      min-height: 20px;
      color: var(--muted);
    }
    @media (max-width: 860px) {
      main { width: min(100vw - 20px, 720px); padding-top: 18px; }
      header { flex-direction: column; }
      .actions { justify-content: flex-start; }
      .card, .wide { grid-column: 1 / -1; }
      .row { grid-template-columns: 1fr; gap: 4px; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>DiscVault Next Migration</h1>
        <p>PostgreSQL import status, legacy data checks, and metadata plugin readiness.</p>
      </div>
      <div class="actions">
        <button type="button" onclick="loadReport()">Refresh</button>
        <button type="button" class="primary" id="startButton" onclick="startMigration()" disabled>Start Migration</button>
        <a class="button" href="/api/next/migration/report">JSON</a>
      </div>
    </header>

    <section class="grid" aria-live="polite">
      <div class="card">
        <h2>State</h2>
        <div id="stateBadge" class="badge">Loading</div>
        <div id="message"></div>
      </div>
      <div class="card">
        <h2>Movies</h2>
        <div class="metric" id="moviesCount">-</div>
        <div class="label">Imported movie records</div>
      </div>
      <div class="card">
        <h2>People</h2>
        <div class="metric" id="peopleCount">-</div>
        <div class="label">Imported people records</div>
      </div>
      <div class="card">
        <h2>Media Assets</h2>
        <div class="metric" id="mediaCount">-</div>
        <div class="label">References to existing filesystem media</div>
      </div>
      <div class="card">
        <h2>Containers</h2>
        <div class="metric" id="containerCount">-</div>
        <div class="label">Box-sets and collections</div>
      </div>
      <div class="card">
        <h2>Plugins</h2>
        <div class="metric" id="pluginCount">-</div>
        <div class="label">Enabled metadata plugins</div>
      </div>

      <div class="card wide">
        <h2>Source</h2>
        <div class="list" id="sourceList"></div>
      </div>
      <div class="card">
        <h2>Skipped</h2>
        <div class="list" id="skippedList"></div>
      </div>
      <div class="card full">
        <h2>Metadata Plugins</h2>
        <div class="plugins" id="pluginsList"></div>
      </div>
      <div class="card full">
        <h2>Required Actions</h2>
        <div class="list" id="actionsList"></div>
      </div>
      <div class="card full" id="warningsCard" hidden>
        <h2>Warnings</h2>
        <div class="list" id="warningsList"></div>
      </div>
    </section>
  </main>

  <script>
    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, function (char) {
        const escapes = {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"};
        return escapes[char] || char;
      });
    }
    function formatNumber(value) {
      if (value === null || value === undefined || value === "") return "-";
      return Number(value).toLocaleString();
    }
    function statusClass(state) {
      if (state === "already_completed" || state === "ready_for_confirmation") return "ok";
      if (String(state || "").startsWith("blocked")) return "error";
      return "warn";
    }
    function row(label, value) {
      return `<div class="row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
    }
    function rowsFromObject(data) {
      const entries = Object.entries(data || {});
      if (!entries.length) return row("None", "-");
      return entries.map(([key, value]) => row(key, typeof value === "object" ? JSON.stringify(value) : value)).join("");
    }
    function renderList(id, items) {
      const node = document.getElementById(id);
      const values = Array.isArray(items) ? items : [];
      node.innerHTML = values.length
        ? values.map((item) => row("", item)).join("")
        : row("None", "-");
    }
    async function loadReport() {
      const message = document.getElementById("message");
      message.textContent = "Loading report...";
      const response = await fetch("/api/next/migration/report", {cache: "no-store"});
      if (!response.ok) throw new Error(`Report failed: HTTP ${response.status}`);
      const payload = await response.json();
      const report = payload.report || {};
      const imported = report.summary?.imported || {};
      const target = report.target?.counts || {};
      const source = report.source || {};
      const plugins = report.metadataPlugins || {};
      const state = report.state || "unknown";

      const stateBadge = document.getElementById("stateBadge");
      stateBadge.className = `badge ${statusClass(state)}`;
      stateBadge.textContent = state.replaceAll("_", " ");
      document.getElementById("startButton").disabled = !report.canStart;
      message.textContent = report.latestRun?.status
        ? `Latest run: ${report.latestRun.status}`
        : "No migration run has been recorded yet.";

      document.getElementById("moviesCount").textContent = formatNumber(imported.movies ?? target.movies);
      document.getElementById("peopleCount").textContent = formatNumber(imported.people ?? target.people);
      document.getElementById("mediaCount").textContent = formatNumber(imported.media_assets ?? target.media_assets);
      document.getElementById("containerCount").textContent = formatNumber(target.containers);
      document.getElementById("pluginCount").textContent = formatNumber(plugins.enabled);

      document.getElementById("sourceList").innerHTML = [
        row("Legacy data", source.found ? "found" : "not found"),
        row("Data directory", source.dataDir || "-"),
        row("SQLite database", source.sqliteDb || "-"),
        row("Database hash", source.sourceDatabaseHash || "-"),
        row("Media mode", source.mediaMigrationMode || "-"),
        row("Source counts", JSON.stringify(source.counts || {})),
        row("Media extensions", JSON.stringify(source.mediaExtensions || {}))
      ].join("");
      document.getElementById("skippedList").innerHTML = rowsFromObject(report.summary?.skipped || {});
      document.getElementById("pluginsList").innerHTML = (plugins.items || []).map((plugin) => `
        <div class="plugin">
          <strong>${escapeHtml(plugin.name)}</strong>
          <span class="badge ${plugin.enabled ? "ok" : "warn"}">${plugin.enabled ? "enabled" : "disabled"}</span>
          <div class="label mono">${escapeHtml(plugin.id)} / order ${escapeHtml(plugin.orderIndex)}</div>
        </div>
      `).join("") || row("No plugins", "-");
      renderList("actionsList", report.requiredActions || []);

      const warnings = report.summary?.warnings || [];
      document.getElementById("warningsCard").hidden = warnings.length === 0;
      renderList("warningsList", warnings);
    }
    async function startMigration() {
      const button = document.getElementById("startButton");
      const message = document.getElementById("message");
      button.disabled = true;
      message.textContent = "Starting migration...";
      const response = await fetch("/api/next/migration/start", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: "{}"
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(`Migration start failed: HTTP ${response.status} ${text}`);
      }
      message.textContent = "Migration job queued.";
      await loadReport();
    }
    window.addEventListener("load", () => {
      loadReport().catch((error) => {
        document.getElementById("message").textContent = error.message;
        document.getElementById("stateBadge").className = "badge error";
        document.getElementById("stateBadge").textContent = "error";
      });
    });
  </script>
</body>
</html>
"""


def collection_movie_preview_entities(conn, *, limit: int = 200) -> list[dict[str, Any]]:
    if not table_exists(conn, "movies"):
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id,
                public_id,
                barcode,
                title,
                sort_title,
                original_title,
                year,
                format,
                edition,
                metadata->>'poster_url' AS poster_url,
                metadata->>'backdrop_url' AS backdrop_url,
                created_at,
                updated_at
            FROM movies
            ORDER BY lower(COALESCE(sort_title, title)), year NULLS LAST
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()


def collection_container_preview_entities(conn, *, limit: int = 200) -> list[dict[str, Any]]:
    if not table_exists(conn, "containers"):
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id,
                public_id,
                container_type,
                title,
                barcode,
                badge_label,
                year,
                description,
                metadata,
                created_at,
                updated_at
            FROM containers
            ORDER BY container_type, lower(title)
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()


def collection_plugin_preview_entities(conn) -> list[dict[str, Any]]:
    if not table_exists(conn, "metadata_plugins"):
        return []
    reconcile_legacy_metadata_plugins(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, enabled, order_index
            FROM metadata_plugins
            ORDER BY order_index, name
            """
        )
        return cur.fetchall()


def collection_dashboard_snapshot(conn) -> dict[str, Any]:
    counts = {
        "movies": count_table(conn, "movies"),
        "people": count_table(conn, "people"),
        "movieCredits": count_table(conn, "movie_credits"),
        "containers": count_table(conn, "containers"),
        "mediaAssets": count_table(conn, "media_assets"),
        "metadataPlugins": count_table(conn, "metadata_plugins"),
        "users": count_table(conn, "users"),
    }
    return {
        "counts": counts,
        "movies": collection_movie_preview_entities(conn),
        "containers": collection_container_preview_entities(conn),
        "plugins": collection_plugin_preview_entities(conn),
        "build": {
            "version": build_version(),
            "sha": build_sha(),
            "generatedAt": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        },
    }


def h(value: Any) -> str:
    return html_lib.escape(str(value or ""), quote=True)


def server_usable_image(value: Any) -> str:
    text = str(value or "")
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return ""


def server_movie_cards(movies: list[dict[str, Any]]) -> str:
    if not movies:
        return '<div class="empty">No movies imported yet.</div>'
    cards = []
    for movie in movies:
        poster = server_usable_image(movie.get("poster_url"))
        poster_html = f'<img src="{h(poster)}" alt="">' if poster else "<span>No poster</span>"
        tags = []
        if movie.get("year"):
            tags.append(f'<span class="tag good">{h(movie.get("year"))}</span>')
        if movie.get("format"):
            tags.append(f'<span class="tag blue">{h(movie.get("format"))}</span>')
        if movie.get("barcode"):
            tags.append(f'<span class="tag">{h(movie.get("barcode"))}</span>')
        movie_id = h(movie.get("id"))
        cards.append(
            f"""
          <article class="movie" role="button" tabindex="0" onclick="openMovieDetail('{movie_id}')" onkeydown="if(event.key==='Enter'||event.key===' '){{event.preventDefault();openMovieDetail('{movie_id}')}}">
            <div class="poster">{poster_html}</div>
            <div class="movie-body">
              <div class="movie-title">{h(movie.get("title") or "Untitled")}</div>
              <div class="tags">{''.join(tags)}</div>
            </div>
          </article>
            """.strip()
        )
    return "\n".join(cards)


def server_container_cards(containers: list[dict[str, Any]]) -> str:
    if not containers:
        return '<div class="empty">No containers imported yet.</div>'
    cards = []
    for container in containers:
        label = str(container.get("container_type") or "container").replace("_", " ")
        tags = [f'<span class="tag blue">{h(label)}</span>']
        if container.get("year"):
            tags.append(f'<span class="tag good">{h(container.get("year"))}</span>')
        if container.get("barcode"):
            tags.append(f'<span class="tag">{h(container.get("barcode"))}</span>')
        cards.append(
            f"""
        <div class="container-card">
          <strong>{h(container.get("title") or "Untitled")}</strong>
          <div class="tags">{''.join(tags)}</div>
        </div>
            """.strip()
        )
    return "\n".join(cards)


def collection_dashboard_html(snapshot: dict[str, Any] | None = None) -> str:
    snapshot = snapshot or {"counts": {}, "movies": [], "containers": [], "plugins": [], "build": {}}
    counts = snapshot.get("counts") or {}
    movies = snapshot.get("movies") or []
    containers = snapshot.get("containers") or []
    plugins = snapshot.get("plugins") or []
    enabled_plugins = [plugin for plugin in plugins if plugin.get("enabled")]
    initial_state_json = html_lib.escape(json_lib.dumps(json_ready(snapshot), separators=(",", ":")), quote=False)
    movie_cards = server_movie_cards(movies)
    container_cards = server_container_cards(containers)
    client_status = (
        f"Server rendered {len(movies)} movies and {len(containers)} containers. "
        f"Build {snapshot.get('build', {}).get('sha') or 'unknown'}."
    )
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DiscVault Next Collection</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #101116;
      --surface: #191c24;
      --surface-2: #222633;
      --line: #343a4c;
      --text: #f4f5f8;
      --muted: #aab0bd;
      --accent: #e8c547;
      --blue: #82aaff;
      --green: #48c78e;
      --red: #ff6b6b;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main {
      width: min(1360px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 48px;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 18px;
      margin-bottom: 18px;
    }
    h1, h2, h3, p { margin: 0; }
    h1 { font-size: clamp(1.7rem, 3vw, 2.5rem); letter-spacing: 0; }
    h2 { font-size: 1rem; }
    p { color: var(--muted); line-height: 1.5; }
    a, button {
      color: inherit;
      font: inherit;
    }
    button, a.button {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-2);
      color: var(--text);
      cursor: pointer;
      min-height: 38px;
      padding: 0 13px;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      white-space: nowrap;
    }
    button.active {
      border-color: rgba(232,197,71,.65);
      color: var(--accent);
    }
    .actions, .filters {
      display: flex;
      gap: 9px;
      flex-wrap: wrap;
      align-items: center;
    }
    .toolbar {
      display: grid;
      grid-template-columns: minmax(240px, 1fr) auto;
      gap: 12px;
      margin-bottom: 16px;
      align-items: center;
    }
    input {
      width: 100%;
      min-height: 42px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      color: var(--text);
      padding: 0 13px;
      font: inherit;
    }
    input:focus {
      border-color: rgba(130,170,255,.75);
      outline: none;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .stat, .panel {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 15px;
      min-width: 0;
    }
    .stat strong {
      display: block;
      font-size: 1.8rem;
      line-height: 1.1;
    }
    .stat span, .muted {
      color: var(--muted);
      font-size: .86rem;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 310px;
      gap: 14px;
      align-items: start;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(155px, 1fr));
      gap: 12px;
    }
    .movie {
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: var(--surface);
      min-width: 0;
    }
    .poster {
      aspect-ratio: 2 / 3;
      background: linear-gradient(145deg, #232836, #141720);
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
      font-size: .82rem;
      overflow: hidden;
    }
    .poster img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }
    .movie-body {
      padding: 10px;
    }
    .movie-title {
      font-weight: 700;
      font-size: .94rem;
      line-height: 1.25;
      min-height: 2.35em;
      overflow-wrap: anywhere;
    }
    .tags {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      margin-top: 8px;
    }
    .tag {
      border: 1px solid rgba(255,255,255,.13);
      border-radius: 999px;
      padding: 3px 7px;
      color: var(--muted);
      font-size: .72rem;
      line-height: 1.2;
    }
    .tag.good { color: var(--green); border-color: rgba(72,199,142,.38); }
    .tag.blue { color: var(--blue); border-color: rgba(130,170,255,.38); }
    .section-head {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: baseline;
      margin-bottom: 12px;
    }
    .containers {
      display: grid;
      gap: 9px;
      margin-top: 12px;
    }
    .container-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 11px;
      background: var(--surface-2);
    }
    .container-card strong {
      display: block;
      line-height: 1.3;
      overflow-wrap: anywhere;
    }
    .empty {
      min-height: 220px;
      display: flex;
      align-items: center;
      justify-content: center;
      text-align: center;
      color: var(--muted);
      border: 1px dashed var(--line);
      border-radius: 8px;
      background: rgba(255,255,255,.02);
    }
    .detail-overlay {
      position: fixed;
      inset: 0;
      z-index: 20;
      display: none;
      background: rgba(0,0,0,.68);
      padding: 18px;
      overflow-y: auto;
    }
    .detail-overlay.open {
      display: block;
    }
    .detail-panel {
      width: min(1120px, 100%);
      margin: 0 auto;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 10px;
      overflow: hidden;
      box-shadow: 0 24px 80px rgba(0,0,0,.45);
    }
    .detail-hero {
      min-height: 210px;
      background: linear-gradient(135deg, #242938, #11141d);
      background-size: cover;
      background-position: center;
      position: relative;
    }
    .detail-hero::after {
      content: "";
      position: absolute;
      inset: 0;
      background: linear-gradient(180deg, rgba(16,17,22,.2), var(--surface));
    }
    .detail-close {
      position: absolute;
      top: 14px;
      right: 14px;
      z-index: 2;
      min-width: 40px;
      padding: 0;
      font-size: 1.2rem;
    }
    .detail-content {
      display: grid;
      grid-template-columns: 190px minmax(0, 1fr);
      gap: 18px;
      padding: 0 18px 18px;
      margin-top: -96px;
      position: relative;
      z-index: 2;
    }
    .detail-poster {
      aspect-ratio: 2 / 3;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: var(--surface-2);
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
    }
    .detail-poster img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }
    .detail-main {
      min-width: 0;
      padding-top: 100px;
    }
    .detail-title {
      font-size: clamp(1.6rem, 3vw, 2.6rem);
      line-height: 1.05;
      margin-bottom: 8px;
      overflow-wrap: anywhere;
    }
    .detail-overview {
      color: var(--muted);
      line-height: 1.6;
      max-width: 76ch;
      margin-top: 12px;
    }
    .detail-sections {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      padding: 0 18px 18px;
    }
    .detail-section {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255,255,255,.025);
      padding: 14px;
      min-width: 0;
    }
    .detail-section.full {
      grid-column: 1 / -1;
    }
    .field-list {
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }
    .field {
      display: grid;
      grid-template-columns: minmax(110px, .6fr) minmax(0, 1.4fr);
      gap: 10px;
      border-bottom: 1px solid rgba(255,255,255,.07);
      padding-bottom: 8px;
    }
    .field:last-child {
      border-bottom: 0;
      padding-bottom: 0;
    }
    .field span {
      color: var(--muted);
      font-size: .82rem;
    }
    .field strong {
      font-weight: 560;
      overflow-wrap: anywhere;
    }
    .credit-list {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
      gap: 8px;
      margin-top: 10px;
    }
    .credit {
      border: 1px solid rgba(255,255,255,.1);
      border-radius: 8px;
      padding: 9px;
      background: var(--surface-2);
      min-width: 0;
    }
    .credit strong {
      display: block;
      overflow-wrap: anywhere;
    }
    .credit span {
      color: var(--muted);
      font-size: .8rem;
      display: block;
      margin-top: 3px;
      overflow-wrap: anywhere;
    }
    @media (max-width: 980px) {
      main { width: min(100vw - 20px, 760px); padding-top: 18px; }
      header, .toolbar { grid-template-columns: 1fr; flex-direction: column; align-items: stretch; }
      .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .layout { grid-template-columns: 1fr; }
      .grid { grid-template-columns: repeat(auto-fill, minmax(132px, 1fr)); }
      .actions, .filters { justify-content: flex-start; }
      .detail-content {
        grid-template-columns: 120px minmax(0, 1fr);
        margin-top: -62px;
      }
      .detail-main {
        padding-top: 66px;
      }
      .detail-sections {
        grid-template-columns: 1fr;
      }
      .field {
        grid-template-columns: 1fr;
        gap: 3px;
      }
    }
    @media (max-width: 520px) {
      .stats { grid-template-columns: 1fr; }
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .detail-overlay {
        padding: 0;
      }
      .detail-panel {
        min-height: 100vh;
        border-radius: 0;
      }
      .detail-content {
        grid-template-columns: 92px minmax(0, 1fr);
        gap: 12px;
        padding: 0 12px 14px;
      }
      .detail-sections {
        padding: 0 12px 14px;
      }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>DiscVault Next Collection</h1>
        <p>Read-only PostgreSQL collection view for validating the migrated library.</p>
        <p class="muted" id="clientStatus">""" + h(client_status) + """</p>
      </div>
      <div class="actions">
        <button type="button" onclick="loadCollection()">Refresh</button>
        <a class="button" href="/api/next/migration">Migration</a>
        <a class="button" href="/api/next/movies?limit=200">Movies JSON</a>
      </div>
    </header>

    <section class="stats">
      <div class="stat"><strong id="movieCount">""" + h(counts.get("movies", 0)) + """</strong><span>Movies</span></div>
      <div class="stat"><strong id="peopleCount">""" + h(counts.get("people", 0)) + """</strong><span>People</span></div>
      <div class="stat"><strong id="assetCount">""" + h(counts.get("mediaAssets", 0)) + """</strong><span>Media assets</span></div>
      <div class="stat"><strong id="pluginCount">""" + h(len(enabled_plugins)) + """</strong><span>Enabled plugins</span></div>
    </section>

    <div class="toolbar">
      <input id="searchInput" type="search" placeholder="Search title, barcode, format..." oninput="renderMovies()">
      <div class="filters" id="formatFilters"></div>
    </div>

    <section class="layout">
      <div class="panel">
        <div class="section-head">
          <h2>Movies</h2>
          <span class="muted" id="resultCount">""" + h(len(movies)) + """ server rendered</span>
        </div>
        <div class="grid" id="movieGrid">""" + movie_cards + """</div>
      </div>
      <aside class="panel">
        <div class="section-head">
          <h2>Containers</h2>
          <span class="muted" id="containerCount">""" + h(len(containers)) + """</span>
        </div>
        <div class="containers" id="containerList">""" + container_cards + """</div>
      </aside>
    </section>

    <div class="detail-overlay" id="movieDetailOverlay" onclick="if(event.target === this) closeMovieDetail()">
      <article class="detail-panel" aria-modal="true" role="dialog" aria-labelledby="detailTitle">
        <div class="detail-hero" id="detailHero">
          <button class="detail-close" type="button" onclick="closeMovieDetail()" aria-label="Close">x</button>
        </div>
        <div class="detail-content">
          <div class="detail-poster" id="detailPoster">No poster</div>
          <div class="detail-main">
            <h2 class="detail-title" id="detailTitle">Loading...</h2>
            <div class="tags" id="detailTags"></div>
            <p class="detail-overview" id="detailOverview"></p>
          </div>
        </div>
        <div class="detail-sections">
          <section class="detail-section">
            <h3>Release</h3>
            <div class="field-list" id="detailRelease"></div>
          </section>
          <section class="detail-section">
            <h3>Identifiers</h3>
            <div class="field-list" id="detailIdentifiers"></div>
          </section>
          <section class="detail-section">
            <h3>Technical Specs</h3>
            <div class="field-list" id="detailSpecs"></div>
          </section>
          <section class="detail-section">
            <h3>Containers</h3>
            <div class="field-list" id="detailContainers"></div>
          </section>
          <section class="detail-section full">
            <h3>Cast & Crew</h3>
            <div class="credit-list" id="detailCredits"></div>
          </section>
        </div>
      </article>
    </div>
  </main>

  <script>
    var initialState = JSON.parse(""" + json_lib.dumps(initial_state_json) + """);
    var state = {
      movies: initialState.movies || [],
      containers: initialState.containers || [],
      stats: {counts: initialState.counts || {}},
      plugins: initialState.plugins || [],
      activeFormat: "all"
    };

    function escapeHtml(value) {
      return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
        const escapes = {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"};
        return escapes[char] || char;
      });
    }
    function setClientStatus(message) {
      const node = document.getElementById("clientStatus");
      if (node) node.textContent = message;
    }
    function number(value) {
      return Number(value || 0).toLocaleString();
    }
    function usableImage(url) {
      if (!url) return "";
      if (String(url).startsWith("http://") || String(url).startsWith("https://")) return url;
      return "";
    }
    function cssUrl(url) {
      return String(url || "").replace(/["\\]/g, "");
    }
    function valueOrDash(value) {
      if (value === null || value === undefined || value === "") return "-";
      if (Array.isArray(value)) return value.length ? value.join(", ") : "-";
      if (typeof value === "object") return JSON.stringify(value);
      return value;
    }
    function field(label, value) {
      return `<div class="field"><span>${escapeHtml(label)}</span><strong>${escapeHtml(valueOrDash(value))}</strong></div>`;
    }
    function fieldsFromObject(entries) {
      const rows = entries.filter(([, value]) => value !== null && value !== undefined && value !== "");
      return rows.length ? rows.map(([label, value]) => field(label, value)).join("") : field("None", "-");
    }
    function movieMatches(movie, query) {
      if (!query) return true;
      const haystack = [
        movie.title,
        movie.original_title,
        movie.barcode,
        movie.format,
        movie.year,
        movie.edition
      ].filter(Boolean).join(" ").toLowerCase();
      return haystack.includes(query.toLowerCase());
    }
    function renderFormatFilters() {
      const formats = Array.from(new Set(state.movies.map((movie) => movie.format).filter(Boolean))).sort();
      const buttons = ["all", ...formats].map((format) => {
        const label = format === "all" ? "All formats" : format;
        return `<button type="button" class="${state.activeFormat === format ? "active" : ""}" onclick="setFormat('${escapeHtml(format)}')">${escapeHtml(label)}</button>`;
      });
      document.getElementById("formatFilters").innerHTML = buttons.join("");
    }
    function setFormat(format) {
      state.activeFormat = format;
      renderFormatFilters();
      renderMovies();
    }
    function renderStats() {
      const counts = state.stats.counts || {};
      document.getElementById("movieCount").textContent = number(counts.movies);
      document.getElementById("peopleCount").textContent = number(counts.people);
      document.getElementById("assetCount").textContent = number(counts.mediaAssets);
      document.getElementById("pluginCount").textContent = number(state.plugins.filter((plugin) => plugin.enabled).length);
    }
    function renderMovies() {
      const query = document.getElementById("searchInput").value.trim();
      const filtered = state.movies.filter((movie) => {
        const formatOk = state.activeFormat === "all" || movie.format === state.activeFormat;
        return formatOk && movieMatches(movie, query);
      });
      document.getElementById("resultCount").textContent = `${number(filtered.length)} shown`;
      document.getElementById("movieGrid").innerHTML = filtered.length ? filtered.map((movie) => {
        const poster = usableImage(movie.poster_url);
        const posterHtml = poster
          ? `<img src="${escapeHtml(poster)}" alt="">`
          : `<span>No poster</span>`;
        return `
          <article class="movie" role="button" tabindex="0" onclick="openMovieDetail('${escapeHtml(movie.id)}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openMovieDetail('${escapeHtml(movie.id)}')}">
            <div class="poster">${posterHtml}</div>
            <div class="movie-body">
              <div class="movie-title">${escapeHtml(movie.title || "Untitled")}</div>
              <div class="tags">
                ${movie.year ? `<span class="tag good">${escapeHtml(movie.year)}</span>` : ""}
                ${movie.format ? `<span class="tag blue">${escapeHtml(movie.format)}</span>` : ""}
                ${movie.barcode ? `<span class="tag">${escapeHtml(movie.barcode)}</span>` : ""}
              </div>
            </div>
          </article>
        `;
      }).join("") : `<div class="empty">No movies match the current filter.</div>`;
    }
    function renderContainers() {
      document.getElementById("containerCount").textContent = number(state.containers.length);
      document.getElementById("containerList").innerHTML = state.containers.length ? state.containers.map((container) => `
        <div class="container-card">
          <strong>${escapeHtml(container.title || "Untitled")}</strong>
          <div class="tags">
            <span class="tag blue">${escapeHtml((container.container_type || "container").replaceAll("_", " "))}</span>
            ${container.year ? `<span class="tag good">${escapeHtml(container.year)}</span>` : ""}
            ${container.barcode ? `<span class="tag">${escapeHtml(container.barcode)}</span>` : ""}
          </div>
        </div>
      `).join("") : `<div class="empty">No containers imported yet.</div>`;
    }
    function renderMovieDetail(detail) {
      const movie = detail.movie || {};
      const metadata = movie.metadata || {};
      const specs = detail.technicalSpecs || {};
      const poster = usableImage(metadata.poster_url || metadata.posterUrl || metadata.poster);
      const backdrop = usableImage(metadata.backdrop_url || metadata.backdropUrl || metadata.backdrop);
      document.getElementById("detailHero").style.backgroundImage = backdrop
        ? `linear-gradient(180deg, rgba(16,17,22,.18), var(--surface)), url("${cssUrl(backdrop)}")`
        : "";
      document.getElementById("detailPoster").innerHTML = poster
        ? `<img src="${escapeHtml(poster)}" alt="">`
        : "No poster";
      document.getElementById("detailTitle").textContent = movie.title || "Untitled";
      document.getElementById("detailOverview").textContent = movie.overview || "No overview imported yet.";
      document.getElementById("detailTags").innerHTML = [
        movie.year,
        movie.format,
        movie.runtime_minutes ? `${movie.runtime_minutes} min` : "",
        movie.rating ? `Rating ${movie.rating}` : ""
      ].filter(Boolean).map((item) => `<span class="tag good">${escapeHtml(item)}</span>`).join("");
      document.getElementById("detailRelease").innerHTML = fieldsFromObject([
        ["Original title", movie.original_title],
        ["Barcode", movie.barcode],
        ["Format", movie.format],
        ["Edition", movie.edition],
        ["Edition type", movie.edition_type],
        ["Release date", movie.release_date],
        ["Country", movie.country],
        ["Language", movie.language],
        ["Location", movie.location],
        ["Director", metadata.director],
        ["Producer", metadata.producer],
        ["Studios", metadata.studios],
        ["Genre", metadata.genre],
        ["Distributor", metadata.distributor],
        ["Trailer", metadata.trailer_url]
      ]);
      document.getElementById("detailIdentifiers").innerHTML = (detail.identifiers || []).length
        ? detail.identifiers.map((identifier) => field(`${identifier.provider_id} ${identifier.identifier_type}`, identifier.identifier)).join("")
        : field("None", "-");
      document.getElementById("detailSpecs").innerHTML = fieldsFromObject([
        ["HDR", specs.hdr || metadata.hdr],
        ["Packaging", specs.packaging || metadata.packaging],
        ["Screen ratio", specs.screen_ratios || metadata.screen_ratios],
        ["Audio", specs.audio_tracks || metadata.audio_tracks],
        ["Subtitles", specs.subtitles || metadata.subtitles],
        ["Regions", specs.regions || metadata.regions],
        ["Content ratings", specs.content_ratings || metadata.content_ratings]
      ]);
      document.getElementById("detailContainers").innerHTML = (detail.containers || []).length
        ? detail.containers.map((container) => field(`${container.container_type} / ${container.relationship}`, container.title)).join("")
        : field("None", "-");
      document.getElementById("detailCredits").innerHTML = (detail.credits || []).length
        ? detail.credits.map((credit) => `
          <div class="credit">
            <strong>${escapeHtml(credit.name)}</strong>
            <span>${escapeHtml(credit.character || credit.job || credit.credit_type || "credit")}</span>
          </div>
        `).join("")
        : `<div class="empty">No credits imported for this movie.</div>`;
    }
    function renderMovieDetailError(error) {
      const message = error.message || String(error);
      document.getElementById("detailHero").style.backgroundImage = "";
      document.getElementById("detailPoster").textContent = "Error";
      document.getElementById("detailTitle").textContent = "Could not load movie details";
      document.getElementById("detailTags").innerHTML = `<span class="tag">${escapeHtml(message)}</span>`;
      document.getElementById("detailOverview").textContent = "The movie detail request failed. Check the Next API logs for the backend error.";
      document.getElementById("detailRelease").innerHTML = [
        field("Error", message),
        field("URL", error.url || "-"),
        field("Response", error.body || "-")
      ].join("");
      document.getElementById("detailIdentifiers").innerHTML = field("None", "-");
      document.getElementById("detailSpecs").innerHTML = field("None", "-");
      document.getElementById("detailContainers").innerHTML = field("None", "-");
      document.getElementById("detailCredits").innerHTML = `<div class="empty">No credits loaded.</div>`;
    }
    async function openMovieDetail(movieId) {
      const overlay = document.getElementById("movieDetailOverlay");
      overlay.classList.add("open");
      document.getElementById("detailTitle").textContent = "Loading...";
      document.getElementById("detailOverview").textContent = "";
      document.getElementById("detailPoster").textContent = "Loading";
      document.getElementById("detailTags").innerHTML = "";
      try {
        const payload = await json(`/api/next/movies/${encodeURIComponent(movieId)}`, 15000);
        renderMovieDetail(payload.detail || {});
      } catch (error) {
        renderMovieDetailError(error);
      }
    }
    function closeMovieDetail() {
      document.getElementById("movieDetailOverlay").classList.remove("open");
    }
    async function json(url, timeoutMs = 20000) {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
      let response;
      try {
        response = await fetch(url, {cache: "no-store", signal: controller.signal});
      } catch (error) {
        if (error.name === "AbortError") {
          throw new Error(`${url} timed out after ${Math.round(timeoutMs / 1000)} seconds`);
        }
        throw error;
      } finally {
        window.clearTimeout(timeout);
      }
      if (!response.ok) {
        const body = await response.text();
        const error = new Error(`${url} failed with HTTP ${response.status}`);
        error.url = url;
        error.body = body.slice(0, 800);
        throw error;
      }
      return response.json();
    }
    async function loadCollection() {
      setClientStatus("Loading collection data...");
      document.getElementById("resultCount").textContent = "Loading...";
      const stats = await json("/api/next/stats");
      setClientStatus("Stats loaded; loading movies...");
      const movies = await json("/api/next/movies?limit=200");
      setClientStatus("Movies loaded; loading containers...");
      const containers = await json("/api/next/containers");
      setClientStatus("Containers loaded; loading metadata plugins...");
      const plugins = await json("/api/next/metadata/plugins");
      state.stats = stats || {};
      state.movies = movies.items || [];
      state.containers = containers.items || [];
      state.plugins = plugins.plugins || [];
      renderStats();
      renderFormatFilters();
      renderMovies();
      renderContainers();
      setClientStatus(`Loaded ${number(state.movies.length)} movies and ${number(state.containers.length)} containers.`);
    }
    function bootCollection() {
      setClientStatus("Client script started.");
      loadCollection().catch((error) => {
        document.getElementById("movieGrid").innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
        document.getElementById("resultCount").textContent = "Error";
        setClientStatus(`Load failed: ${error.message}`);
      });
    }
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", bootCollection);
    } else {
      bootCollection();
    }
    window.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeMovieDetail();
    });
  </script>
</body>
</html>
"""


def parse_int_arg(name: str, default: int, *, minimum: int = 0, maximum: int = 1000) -> int:
    raw = request.args.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise NextApiError(f"Invalid integer value for {name}", 400) from exc
    return min(max(value, minimum), maximum)


def parse_uuid(value: Any, field_name: str) -> UUID | None:
    if value in (None, ""):
        return None
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise NextApiError(f"Invalid UUID for {field_name}", 400) from exc


def parse_bool_value(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def ensure_sync_state(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sync_state (id, revision)
            VALUES ('global', 0)
            ON CONFLICT (id) DO NOTHING
            """
        )
        cur.execute("SELECT revision FROM sync_state WHERE id='global'")
        row = cur.fetchone()
    return int(row["revision"] if row else 0)


def current_revision(conn) -> int:
    if not table_exists(conn, "sync_state"):
        return 0
    return ensure_sync_state(conn)


def next_revision(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE sync_state
            SET revision = revision + 1,
                updated_at = now()
            WHERE id='global'
            RETURNING revision
            """
        )
        row = cur.fetchone()
    if not row:
        ensure_sync_state(conn)
        return next_revision(conn)
    return int(row["revision"])


def sync_change(
    conn,
    *,
    revision: int,
    entity_type: str,
    entity_id: str,
    operation: str,
    payload: dict[str, Any],
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sync_changes (
                revision,
                entity_type,
                entity_id,
                operation,
                payload
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (revision, entity_type, entity_id, operation, Jsonb(json_ready(payload))),
        )


def idempotency_key(client_id: str, client_mutation_id: str) -> str:
    return f"sync:{client_id}:{client_mutation_id}"


def stored_idempotency_response(conn, key: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute("SELECT response FROM idempotency_records WHERE key=%s", (key,))
        row = cur.fetchone()
    return dict(row["response"]) if row else None


def store_idempotency_response(
    conn,
    *,
    key: str,
    operation: str,
    status: str,
    entity_type: str,
    entity_id: UUID | None,
    response_payload: dict[str, Any],
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO idempotency_records (
                key,
                operation,
                status,
                entity_type,
                entity_id,
                response
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (key) DO UPDATE SET
                updated_at=now()
            """,
            (key, operation, status, entity_type, entity_id, Jsonb(json_ready(response_payload))),
        )


def app_settings_map(conn) -> dict[str, Any]:
    if not table_exists(conn, "app_settings"):
        return {}
    with conn.cursor() as cur:
        cur.execute("SELECT key, value FROM app_settings WHERE is_secret = false")
        return {row["key"]: row["value"] for row in cur.fetchall()}


def reconcile_legacy_metadata_plugins(conn) -> dict[str, Any] | None:
    if not table_exists(conn, "app_settings") or not table_exists(conn, "metadata_plugins"):
        return None
    settings = app_settings_map(conn)
    if not settings or "legacy_metadata_plugins_reconciled" in settings:
        return None
    legacy_keys = {
        "metadata_source_order",
        "tmdb_enabled",
        "omdb_enabled",
        "movievault_enabled",
        "bluray_scrape_enabled",
        "bluray_com_enabled",
        "upcitemdb_enabled",
    }
    if not any(key in settings for key in legacy_keys):
        return None
    return apply_legacy_metadata_plugin_plan(conn, settings, Jsonb)


def client_entity_mapping(
    conn,
    *,
    client_id: str,
    entity_type: str,
    client_entity_id: str | None,
) -> UUID | None:
    if not client_entity_id:
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT entity_id
            FROM client_id_mappings
            WHERE user_id IS NULL
              AND client_id=%s
              AND entity_type=%s
            """,
            (f"{client_id}:{client_entity_id}", entity_type),
        )
        row = cur.fetchone()
    return row["entity_id"] if row else None


def store_client_entity_mapping(
    conn,
    *,
    client_id: str,
    client_entity_id: str | None,
    entity_type: str,
    entity_id: UUID,
    idem_key: str,
) -> None:
    if not client_entity_id:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO client_id_mappings (
                user_id,
                client_id,
                entity_type,
                entity_id,
                idempotency_key
            )
            VALUES (NULL, %s, %s, %s, %s)
            ON CONFLICT (user_id, client_id, entity_type) DO NOTHING
            """,
            (f"{client_id}:{client_entity_id}", entity_type, entity_id, idem_key),
        )


def movie_payload_fields(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    for source, target in (
        ("posterUrl", "poster_url"),
        ("backdropUrl", "backdrop_url"),
        ("poster_url", "poster_url"),
        ("backdrop_url", "backdrop_url"),
    ):
        if payload.get(source):
            metadata[target] = payload[source]
    return {
        "barcode": payload.get("barcode"),
        "title": payload.get("title"),
        "sort_title": payload.get("sortTitle") or payload.get("sort_title"),
        "original_title": payload.get("originalTitle") or payload.get("original_title"),
        "year": payload.get("year"),
        "release_date": payload.get("releaseDate") or payload.get("release_date"),
        "format": payload.get("format"),
        "edition": payload.get("edition"),
        "edition_type": payload.get("editionType") or payload.get("edition_type"),
        "country": payload.get("country"),
        "language": payload.get("language"),
        "runtime_minutes": payload.get("runtimeMinutes") or payload.get("runtime_minutes"),
        "overview": payload.get("overview"),
        "notes": payload.get("notes"),
        "rating": payload.get("rating"),
        "purchase_date": payload.get("purchaseDate") or payload.get("purchase_date"),
        "purchase_price": payload.get("purchasePrice") or payload.get("purchase_price"),
        "location": payload.get("location"),
        "metadata": metadata,
    }


def movie_entity(conn, movie_id: UUID) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id,
                public_id,
                barcode,
                title,
                sort_title,
                original_title,
                year,
                release_date,
                format,
                edition,
                edition_type,
                country,
                language,
                runtime_minutes,
                overview,
                notes,
                rating,
                purchase_date,
                purchase_price,
                location,
                metadata,
                created_at,
                updated_at
            FROM movies
            WHERE id=%s
            """,
            (movie_id,),
        )
        row = cur.fetchone()
    return row


def movie_identifier_entities(conn, movie_id: UUID) -> list[dict[str, Any]]:
    if not table_exists(conn, "movie_identifiers"):
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT provider_id, identifier_type, identifier, created_at
            FROM movie_identifiers
            WHERE movie_id=%s
            ORDER BY provider_id, identifier_type, identifier
            """,
            (movie_id,),
        )
        return cur.fetchall()


def movie_technical_spec_entity(conn, movie_id: UUID) -> dict[str, Any] | None:
    if not table_exists(conn, "movie_technical_specs"):
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                hdr,
                packaging,
                screen_ratios,
                audio_tracks,
                subtitles,
                regions,
                content_ratings,
                updated_at
            FROM movie_technical_specs
            WHERE movie_id=%s
            """,
            (movie_id,),
        )
        return cur.fetchone()


def movie_credit_entities(conn, movie_id: UUID, *, limit: int = 80) -> list[dict[str, Any]]:
    if not table_exists(conn, "movie_credits") or not table_exists(conn, "people"):
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                mc.id,
                mc.credit_type,
                mc.character,
                mc.job,
                mc.sort_order,
                p.id AS person_id,
                p.public_id AS person_public_id,
                p.name,
                p.known_for,
                p.metadata AS person_metadata
            FROM movie_credits mc
            JOIN people p ON p.id = mc.person_id
            WHERE mc.movie_id=%s
            ORDER BY mc.sort_order, p.name
            LIMIT %s
            """,
            (movie_id, limit),
        )
        return cur.fetchall()


def movie_container_entities(conn, movie_id: UUID) -> list[dict[str, Any]]:
    if not table_exists(conn, "containers"):
        return []
    links: list[dict[str, Any]] = []
    if table_exists(conn, "container_movies"):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    c.id,
                    c.public_id,
                    c.container_type,
                    c.title,
                    c.barcode,
                    c.badge_label,
                    c.year,
                    c.description,
                    c.metadata,
                    cm.sort_order,
                    'member' AS relationship
                FROM container_movies cm
                JOIN containers c ON c.id = cm.container_id
                WHERE cm.movie_id=%s
                ORDER BY c.container_type, lower(c.title), cm.sort_order
                """,
                (movie_id,),
            )
            links.extend(cur.fetchall())
    if table_exists(conn, "collection_items"):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    c.id,
                    c.public_id,
                    c.container_type,
                    c.title,
                    c.barcode,
                    c.badge_label,
                    c.year,
                    c.description,
                    c.metadata,
                    ci.sort_order,
                    'collection_item' AS relationship
                FROM collection_items ci
                JOIN containers c ON c.id = ci.collection_id
                WHERE ci.item_type='movie' AND ci.item_id=%s
                ORDER BY lower(c.title), ci.sort_order
                """,
                (movie_id,),
            )
            links.extend(cur.fetchall())
    return links


def entity_media_asset_entities(conn, entity_type: str, entity_id: UUID) -> list[dict[str, Any]]:
    if not table_exists(conn, "entity_media") or not table_exists(conn, "media_assets"):
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                ma.id,
                ma.kind,
                ma.variant,
                ma.storage_backend,
                ma.storage_key,
                ma.source_url,
                ma.provider_id,
                ma.content_type,
                ma.width,
                ma.height,
                ma.size_bytes,
                ma.sha256,
                ma.metadata,
                em.role,
                em.is_primary,
                em.sort_order
            FROM entity_media em
            JOIN media_assets ma ON ma.id = em.media_id
            WHERE em.entity_type=%s AND em.entity_id=%s
            ORDER BY em.role, em.sort_order, ma.kind
            """,
            (entity_type, entity_id),
        )
        return cur.fetchall()


def movie_detail_entity(conn, movie_id: UUID) -> dict[str, Any] | None:
    movie = movie_entity(conn, movie_id)
    if not movie:
        return None
    return {
        "movie": movie,
        "identifiers": movie_identifier_entities(conn, movie_id),
        "technicalSpecs": movie_technical_spec_entity(conn, movie_id),
        "credits": movie_credit_entities(conn, movie_id),
        "containers": movie_container_entities(conn, movie_id),
        "mediaAssets": entity_media_asset_entities(conn, "movie", movie_id),
    }


def all_movie_entities(conn, *, limit: int = 1000) -> list[dict[str, Any]]:
    if not table_exists(conn, "movies"):
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id,
                public_id,
                barcode,
                title,
                sort_title,
                original_title,
                year,
                release_date,
                format,
                edition,
                edition_type,
                country,
                language,
                runtime_minutes,
                overview,
                notes,
                rating,
                purchase_date,
                purchase_price,
                location,
                metadata,
                created_at,
                updated_at
            FROM movies
            ORDER BY lower(COALESCE(sort_title, title)), year NULLS LAST
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()


def all_container_entities(conn, *, limit: int = 1000) -> list[dict[str, Any]]:
    if not table_exists(conn, "containers"):
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id,
                public_id,
                container_type,
                title,
                barcode,
                badge_label,
                year,
                description,
                metadata,
                created_at,
                updated_at
            FROM containers
            ORDER BY container_type, lower(title)
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()


def non_secret_settings(conn) -> list[dict[str, Any]]:
    if not table_exists(conn, "app_settings"):
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT key, value, updated_at
            FROM app_settings
            WHERE is_secret = false
              AND key = ANY(%s::text[])
            ORDER BY key
            """,
            (list(sorted(CLIENT_SYNC_SETTING_KEYS)),),
        )
        return cur.fetchall()


def metadata_plugin_entities(conn) -> list[dict[str, Any]]:
    if not table_exists(conn, "metadata_plugins"):
        return []
    reconcile_legacy_metadata_plugins(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                p.id,
                p.name,
                p.version,
                p.enabled,
                p.installed,
                p.order_index,
                p.manifest,
                p.settings_schema,
                p.premium_feature_key,
                p.updated_at,
                s.settings,
                s.secrets_ref
            FROM metadata_plugins p
            LEFT JOIN metadata_plugin_settings s ON s.plugin_id = p.id
            ORDER BY p.order_index, p.name
            """
        )
        return [plugin_row(row) for row in cur.fetchall()]


def apply_movie_upsert(
    conn,
    *,
    client_id: str,
    idem_key: str,
    mutation: dict[str, Any],
) -> dict[str, Any]:
    payload = mutation.get("payload")
    if not isinstance(payload, dict):
        raise NextApiError("Movie upsert payload must be an object", 400)

    client_entity_id = str(mutation.get("clientEntityId") or "").strip() or None
    entity_id = parse_uuid(mutation.get("entityId"), "entityId")
    entity_id = entity_id or client_entity_mapping(
        conn,
        client_id=client_id,
        entity_type="movie",
        client_entity_id=client_entity_id,
    )
    entity_id = entity_id or uuid.uuid4()
    existing = movie_entity(conn, entity_id)
    fields = movie_payload_fields(payload)
    title = fields["title"] or (existing or {}).get("title")
    if not title:
        raise NextApiError("Movie title is required for upsert", 400)

    public_id = payload.get("publicId") or payload.get("public_id")
    public_id = public_id or (existing or {}).get("public_id") or f"next-movie-{entity_id}"

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO movies (
                id,
                public_id,
                barcode,
                title,
                sort_title,
                original_title,
                year,
                release_date,
                format,
                edition,
                edition_type,
                country,
                language,
                runtime_minutes,
                overview,
                notes,
                rating,
                purchase_date,
                purchase_price,
                location,
                metadata,
                created_at,
                updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                now(), now()
            )
            ON CONFLICT (id) DO UPDATE SET
                barcode=COALESCE(EXCLUDED.barcode, movies.barcode),
                title=COALESCE(EXCLUDED.title, movies.title),
                sort_title=COALESCE(EXCLUDED.sort_title, movies.sort_title),
                original_title=COALESCE(EXCLUDED.original_title, movies.original_title),
                year=COALESCE(EXCLUDED.year, movies.year),
                release_date=COALESCE(EXCLUDED.release_date, movies.release_date),
                format=COALESCE(EXCLUDED.format, movies.format),
                edition=COALESCE(EXCLUDED.edition, movies.edition),
                edition_type=COALESCE(EXCLUDED.edition_type, movies.edition_type),
                country=COALESCE(EXCLUDED.country, movies.country),
                language=COALESCE(EXCLUDED.language, movies.language),
                runtime_minutes=COALESCE(EXCLUDED.runtime_minutes, movies.runtime_minutes),
                overview=COALESCE(EXCLUDED.overview, movies.overview),
                notes=COALESCE(EXCLUDED.notes, movies.notes),
                rating=COALESCE(EXCLUDED.rating, movies.rating),
                purchase_date=COALESCE(EXCLUDED.purchase_date, movies.purchase_date),
                purchase_price=COALESCE(EXCLUDED.purchase_price, movies.purchase_price),
                location=COALESCE(EXCLUDED.location, movies.location),
                metadata=movies.metadata || EXCLUDED.metadata,
                updated_at=now()
            """,
            (
                entity_id,
                public_id,
                fields["barcode"],
                title,
                fields["sort_title"],
                fields["original_title"],
                fields["year"],
                fields["release_date"],
                fields["format"],
                fields["edition"],
                fields["edition_type"],
                fields["country"],
                fields["language"],
                fields["runtime_minutes"],
                fields["overview"],
                fields["notes"],
                fields["rating"],
                fields["purchase_date"],
                fields["purchase_price"],
                fields["location"],
                Jsonb(fields["metadata"]),
            ),
        )

    store_client_entity_mapping(
        conn,
        client_id=client_id,
        client_entity_id=client_entity_id,
        entity_type="movie",
        entity_id=entity_id,
        idem_key=idem_key,
    )
    entity = movie_entity(conn, entity_id) or {}
    revision = next_revision(conn)
    change_payload = {
        "entity": entity,
        "clientId": client_id,
        "clientEntityId": client_entity_id,
        "clientMutationId": mutation["clientMutationId"],
    }
    sync_change(
        conn,
        revision=revision,
        entity_type="movie",
        entity_id=str(entity_id),
        operation="upsert",
        payload=change_payload,
    )
    return {
        "clientMutationId": mutation["clientMutationId"],
        "status": "applied",
        "entityType": "movie",
        "operation": "upsert",
        "entityId": entity_id,
        "clientEntityId": client_entity_id,
        "revision": revision,
        "entity": entity,
    }


def apply_movie_delete(
    conn,
    *,
    client_id: str,
    idem_key: str,
    mutation: dict[str, Any],
) -> dict[str, Any]:
    client_entity_id = str(mutation.get("clientEntityId") or "").strip() or None
    entity_id = parse_uuid(mutation.get("entityId"), "entityId")
    entity_id = entity_id or client_entity_mapping(
        conn,
        client_id=client_id,
        entity_type="movie",
        client_entity_id=client_entity_id,
    )
    if not entity_id:
        raise NextApiError("Movie delete requires entityId or mapped clientEntityId", 400)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM movies WHERE id=%s RETURNING id", (entity_id,))
        deleted = cur.fetchone()
    if not deleted:
        raise NextApiError("Movie not found", 404)
    revision = next_revision(conn)
    payload = {
        "entityId": entity_id,
        "clientId": client_id,
        "clientEntityId": client_entity_id,
        "clientMutationId": mutation["clientMutationId"],
    }
    sync_change(
        conn,
        revision=revision,
        entity_type="movie",
        entity_id=str(entity_id),
        operation="delete",
        payload=payload,
    )
    return {
        "clientMutationId": mutation["clientMutationId"],
        "status": "applied",
        "entityType": "movie",
        "operation": "delete",
        "entityId": entity_id,
        "clientEntityId": client_entity_id,
        "revision": revision,
    }


def apply_sync_mutation(conn, *, client_id: str, mutation: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(mutation, dict):
        raise NextApiError("Each mutation must be an object", 400)
    client_mutation_id = str(mutation.get("clientMutationId") or "").strip()
    if not client_mutation_id:
        raise NextApiError("clientMutationId is required", 400)
    mutation["clientMutationId"] = client_mutation_id
    entity_type = str(mutation.get("entityType") or "").strip()
    operation = str(mutation.get("operation") or "").strip()
    key = idempotency_key(client_id, client_mutation_id)
    existing = stored_idempotency_response(conn, key)
    if existing:
        existing["replayed"] = True
        return existing

    if entity_type == "movie" and operation == "upsert":
        result = apply_movie_upsert(conn, client_id=client_id, idem_key=key, mutation=mutation)
    elif entity_type == "movie" and operation == "delete":
        result = apply_movie_delete(conn, client_id=client_id, idem_key=key, mutation=mutation)
    else:
        raise NextApiError(f"Unsupported mutation: {entity_type}.{operation}", 400)

    store_idempotency_response(
        conn,
        key=key,
        operation=f"{entity_type}.{operation}",
        status=result["status"],
        entity_type=entity_type,
        entity_id=result.get("entityId"),
        response_payload=result,
    )
    return result


def migration_overview(conn) -> dict[str, Any]:
    migrations = discover_migrations()
    if not table_exists(conn, "schema_migrations"):
        return {
            "state": "not_initialized",
            "applied": 0,
            "pending": len(migrations),
            "checksum_mismatch": 0,
            "items": [
                {
                    "version": migration.version,
                    "name": migration.name,
                    "state": "pending",
                }
                for migration in migrations
            ],
        }

    with conn.cursor() as cur:
        cur.execute("SELECT version, name, checksum, applied_at FROM schema_migrations")
        applied = {row["version"]: row for row in cur.fetchall()}

    items = []
    counters = {"applied": 0, "pending": 0, "checksum_mismatch": 0}
    for migration in migrations:
        row = applied.get(migration.version)
        if not row:
            state = "pending"
        elif row["checksum"] == migration.checksum:
            state = "applied"
        else:
            state = "checksum_mismatch"
        counters[state] += 1
        items.append(
            {
                "version": migration.version,
                "name": migration.name,
                "state": state,
                "applied_at": row["applied_at"] if row else None,
            }
        )

    state = "ready"
    if counters["checksum_mismatch"]:
        state = "blocked"
    elif counters["pending"]:
        state = "pending_migrations"

    return {
        "state": state,
        **counters,
        "items": items,
    }


def plugin_row(row: dict[str, Any]) -> dict[str, Any]:
    manifest = row.get("manifest") or {}
    settings_schema = row.get("settings_schema") or {}
    settings = row.get("settings") or {}
    secrets_ref = row.get("secrets_ref") or {}
    return {
        "id": row["id"],
        "name": row["name"],
        "version": row["version"],
        "enabled": bool(row["enabled"]),
        "installed": bool(row["installed"]),
        "orderIndex": row["order_index"],
        "capabilities": manifest.get("capabilities", []) if isinstance(manifest, dict) else [],
        "manifest": manifest,
        "settingsSchema": settings_schema,
        "settingsConfigured": bool(settings),
        "secretsConfigured": bool(secrets_ref),
        "premiumFeatureKey": row.get("premium_feature_key"),
        "updatedAt": row.get("updated_at"),
    }


def job_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "jobType": row["job_type"],
        "status": row["status"],
        "requestedBy": row.get("requested_by"),
        "payload": row.get("payload") or {},
        "result": row.get("result") or {},
        "error": row.get("error"),
        "createdAt": row.get("created_at"),
        "startedAt": row.get("started_at"),
        "finishedAt": row.get("finished_at"),
    }


def create_background_job(conn, *, job_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not table_exists(conn, "background_jobs"):
        raise NextApiError("Background job table is not available", 503)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO background_jobs (job_type, payload)
            VALUES (%s, %s)
            RETURNING
                id,
                job_type,
                status,
                requested_by,
                payload,
                result,
                error,
                created_at,
                started_at,
                finished_at
            """,
            (job_type, Jsonb(json_ready(payload))),
        )
        row = cur.fetchone()
    return job_row(row)


def register_routes(flask_app: Flask) -> None:
    @flask_app.errorhandler(NextApiError)
    def handle_next_error(error: NextApiError):
        return response({"status": "error", "error": str(error)}, error.status_code)

    @flask_app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        return response(
            {
                "status": "error",
                "error": error.description,
                "path": request.path,
            },
            error.code or 500,
        )

    @flask_app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception):  # pragma: no cover - Flask integration
        flask_app.logger.exception("Unhandled Next API error on %s", request.path)
        return response({"status": "error", "error": str(error), "path": request.path}, 500)

    @flask_app.get("/api/next/health")
    def health():
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        current_database() AS database,
                        current_user AS user,
                        version() AS postgres_version,
                        now() AS checked_at
                    """
                )
                db = cur.fetchone()
            migrations = migration_overview(conn)
        migration_state = migrations["state"]
        is_ready = migration_state == "ready"
        return response(
            {
                "status": "ok" if is_ready else "degraded",
                "service": "discvault-next-api",
                "version": build_version(),
                "sha": build_sha(),
                "database": db,
                "migrations": migrations,
            },
            200 if is_ready else 503,
        )

    @flask_app.get("/api/next/stats")
    def stats():
        with connect() as conn:
            counts = {
                "movies": count_table(conn, "movies"),
                "people": count_table(conn, "people"),
                "movieCredits": count_table(conn, "movie_credits"),
                "containers": count_table(conn, "containers"),
                "mediaAssets": count_table(conn, "media_assets"),
                "metadataPlugins": count_table(conn, "metadata_plugins"),
                "users": count_table(conn, "users"),
            }
            container_counts = []
            plugin_counts = []
            latest_import = None
            if table_exists(conn, "containers"):
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT container_type, COUNT(*) AS count
                        FROM containers
                        GROUP BY container_type
                        ORDER BY container_type
                        """
                    )
                    container_counts = cur.fetchall()
            if table_exists(conn, "metadata_plugins"):
                reconcile_legacy_metadata_plugins(conn)
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT enabled, COUNT(*) AS count
                        FROM metadata_plugins
                        GROUP BY enabled
                        ORDER BY enabled DESC
                        """
                    )
                    plugin_counts = cur.fetchall()
            if table_exists(conn, "migration_runs"):
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, source_kind, status, started_at, completed_at, result
                        FROM migration_runs
                        ORDER BY started_at DESC
                        LIMIT 1
                        """
                    )
                    latest_import = cur.fetchone()
        return response(
            {
                "status": "ok",
                "counts": counts,
                "containersByType": container_counts,
                "metadataPluginsByEnabled": plugin_counts,
                "latestImport": latest_import,
            }
        )

    @flask_app.get("/api/next/settings")
    def settings():
        with connect() as conn:
            if not table_exists(conn, "app_settings"):
                return response({"status": "ok", "settings": []})
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT key, value, is_secret, updated_at
                    FROM app_settings
                    WHERE is_secret = false
                    ORDER BY key
                    """
                )
                rows = cur.fetchall()
        return response({"status": "ok", "settings": rows})

    @flask_app.get("/api/next/metadata/plugins")
    def metadata_plugins():
        with connect() as conn:
            if not table_exists(conn, "metadata_plugins"):
                return response({"status": "ok", "plugins": []})
            reconcile_legacy_metadata_plugins(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        p.id,
                        p.name,
                        p.version,
                        p.enabled,
                        p.installed,
                        p.order_index,
                        p.manifest,
                        p.settings_schema,
                        p.premium_feature_key,
                        p.updated_at,
                        s.settings,
                        s.secrets_ref
                    FROM metadata_plugins p
                    LEFT JOIN metadata_plugin_settings s ON s.plugin_id = p.id
                    ORDER BY p.order_index, p.name
                    """
                )
                plugins = [plugin_row(row) for row in cur.fetchall()]
        return response({"status": "ok", "plugins": plugins})

    @flask_app.get("/api/next/movies")
    def movies():
        limit = min(max(int(request.args.get("limit", 50)), 1), 200)
        offset = max(int(request.args.get("offset", 0)), 0)
        query = (request.args.get("q") or "").strip()
        with connect() as conn:
            if not table_exists(conn, "movies"):
                return response({"status": "ok", "items": [], "limit": limit, "offset": offset})
            params: list[Any] = []
            where = ""
            if query:
                where = "WHERE lower(title) LIKE lower(%s)"
                params.append(f"%{query}%")
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        id,
                        public_id,
                        barcode,
                        title,
                        sort_title,
                        original_title,
                        year,
                        format,
                        edition,
                        metadata->>'poster_url' AS poster_url,
                        metadata->>'backdrop_url' AS backdrop_url,
                        created_at,
                        updated_at
                    FROM movies
                    {where}
                    ORDER BY lower(COALESCE(sort_title, title)), year NULLS LAST
                    LIMIT %s OFFSET %s
                    """,
                    (*params, limit, offset),
                )
                items = cur.fetchall()
        return response({"status": "ok", "items": items, "limit": limit, "offset": offset})

    @flask_app.get("/api/next/movies/<movie_id>")
    def movie_detail(movie_id: str):
        movie_uuid = parse_uuid(movie_id, "movieId")
        with connect() as conn:
            if not table_exists(conn, "movies"):
                raise NextApiError("Movie table is not available", 503)
            detail = movie_detail_entity(conn, movie_uuid)
        if not detail:
            raise NextApiError("Movie not found", 404)
        return response({"status": "ok", "detail": detail})

    @flask_app.get("/api/next/containers")
    def containers():
        container_type = (request.args.get("type") or "").strip()
        with connect() as conn:
            if not table_exists(conn, "containers"):
                return response({"status": "ok", "items": []})
            params: list[Any] = []
            where = ""
            if container_type:
                where = "WHERE container_type = %s"
                params.append(container_type)
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        id,
                        public_id,
                        container_type,
                        title,
                        barcode,
                        badge_label,
                        year,
                        description,
                        metadata,
                        created_at,
                        updated_at
                    FROM containers
                    {where}
                    ORDER BY container_type, lower(title)
                    LIMIT 200
                    """,
                    params,
                )
                items = cur.fetchall()
        return response({"status": "ok", "items": items})

    @flask_app.get("/api/next/jobs")
    def jobs():
        limit = parse_int_arg("limit", 100, minimum=1, maximum=500)
        status = (request.args.get("status") or "").strip()
        with connect() as conn:
            if not table_exists(conn, "background_jobs"):
                return response({"status": "ok", "jobs": []})
            params: list[Any] = []
            where = ""
            if status:
                where = "WHERE status = %s"
                params.append(status)
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        id,
                        job_type,
                        status,
                        requested_by,
                        payload,
                        result,
                        error,
                        created_at,
                        started_at,
                        finished_at
                    FROM background_jobs
                    {where}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (*params, limit),
                )
                rows = cur.fetchall()
        return response({"status": "ok", "jobs": [job_row(row) for row in rows]})

    @flask_app.post("/api/next/jobs")
    def create_job():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise NextApiError("Job request body must be an object", 400)
        job_type = str(body.get("jobType") or body.get("job_type") or "").strip()
        if not job_type:
            raise NextApiError("jobType is required", 400)
        payload = body.get("payload") or {}
        if not isinstance(payload, dict):
            raise NextApiError("payload must be an object", 400)
        with connect() as conn:
            with conn.transaction():
                job = create_background_job(conn, job_type=job_type, payload=payload)
        return response({"status": "ok", "job": job}, 201)

    @flask_app.get("/api/next/migration/readiness")
    def get_migration_readiness():
        with connect() as conn:
            readiness = migration_readiness(conn)
        return response({"status": "ok", "readiness": readiness})

    @flask_app.post("/api/next/migration/start")
    def start_migration():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise NextApiError("Migration request body must be an object", 400)

        include_security = parse_bool_value(body.get("includeSecurity", body.get("include_security")), default=False)
        include_personal = parse_bool_value(body.get("includePersonal", body.get("include_personal")), default=False)
        if include_personal and not include_security:
            raise NextApiError("includePersonal requires includeSecurity", 400)
        owner_username = clean_text(body.get("ownerUsername") or body.get("owner_username"))
        import_media_references = body.get("importMediaReferences", body.get("import_media_references", True))
        import_media_references = parse_bool_value(import_media_references, default=True)

        with connect() as conn:
            readiness = migration_readiness(conn)
            active = readiness.get("activeJob")
            if active:
                return response({"status": "ok", "job": active, "readiness": readiness, "replayed": True}, 200)
            if not readiness["canStart"]:
                raise NextApiError(
                    f"Migration cannot start while readiness state is {readiness['state']}",
                    409,
                )
            legacy = readiness["legacyData"]
            payload = {
                "dataDir": legacy["dataDir"],
                "sqliteDb": legacy["sqliteDb"],
                "sourceDatabaseHash": legacy.get("sourceDatabaseHash"),
                "includeSecurity": include_security,
                "includePersonal": include_personal,
                "ownerUsername": owner_username,
                "importMediaReferences": import_media_references,
                "mediaMigrationMode": "reference_existing_files",
            }
            with conn.transaction():
                job = create_background_job(conn, job_type=MIGRATION_JOB_TYPE, payload=payload)
        return response({"status": "ok", "job": job, "readiness": readiness}, 201)

    @flask_app.get("/api/next/migration/status")
    def migration_status():
        with connect() as conn:
            readiness = migration_readiness(conn)
        return response(
            {
                "status": "ok",
                "state": readiness["state"],
                "canStart": readiness["canStart"],
                "activeJob": readiness["activeJob"],
                "latestRun": readiness["latestRun"],
                "warnings": readiness["warnings"],
                "requiredActions": readiness["requiredActions"],
            }
        )

    @flask_app.get("/api/next/migration/report")
    def get_migration_report():
        with connect() as conn:
            report = migration_report(conn)
        return response({"status": "ok", "report": report})

    @flask_app.get("/api/next/migration")
    @flask_app.get("/api/next/migration/")
    def migration_dashboard():
        return html_response(migration_dashboard_html())

    @flask_app.get("/api/next/collection")
    @flask_app.get("/api/next/collection/")
    def collection_dashboard():
        with connect() as conn:
            snapshot = collection_dashboard_snapshot(conn)
        return html_response(collection_dashboard_html(snapshot))

    @flask_app.get("/api/next/migration/jobs/<job_id>")
    def migration_job(job_id: str):
        job_uuid = parse_uuid(job_id, "jobId")
        with connect() as conn:
            if not table_exists(conn, "background_jobs"):
                raise NextApiError("Background job table is not available", 503)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        job_type,
                        status,
                        requested_by,
                        payload,
                        result,
                        error,
                        created_at,
                        started_at,
                        finished_at
                    FROM background_jobs
                    WHERE id=%s AND job_type=%s
                    """,
                    (job_uuid, MIGRATION_JOB_TYPE),
                )
                row = cur.fetchone()
        if not row:
            raise NextApiError("Migration job not found", 404)
        return response({"status": "ok", "job": job_row(row)})

    @flask_app.get("/api/next/jobs/<job_id>")
    def get_job(job_id: str):
        job_uuid = parse_uuid(job_id, "jobId")
        with connect() as conn:
            if not table_exists(conn, "background_jobs"):
                raise NextApiError("Background job table is not available", 503)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        job_type,
                        status,
                        requested_by,
                        payload,
                        result,
                        error,
                        created_at,
                        started_at,
                        finished_at
                    FROM background_jobs
                    WHERE id=%s
                    """,
                    (job_uuid,),
                )
                row = cur.fetchone()
        if not row:
            raise NextApiError("Job not found", 404)
        return response({"status": "ok", "job": job_row(row)})

    @flask_app.get("/api/next/sync/state")
    def sync_state():
        with connect() as conn:
            revision = current_revision(conn)
        return response(
            {
                "status": "ok",
                "currentRevision": revision,
                "serverTime": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "supportedEntityTypes": [
                    "movie",
                    "container",
                    "metadata_plugin",
                    "setting",
                ],
                "supportedMutations": [
                    "movie.upsert",
                    "movie.delete",
                ],
            }
        )

    @flask_app.get("/api/next/sync/bootstrap")
    def sync_bootstrap():
        limit = parse_int_arg("limit", 1000, minimum=1, maximum=5000)
        with connect() as conn:
            revision = current_revision(conn)
            payload = {
                "movies": all_movie_entities(conn, limit=limit),
                "containers": all_container_entities(conn, limit=limit),
                "metadataPlugins": metadata_plugin_entities(conn),
                "settings": non_secret_settings(conn),
            }
        return response(
            {
                "status": "ok",
                "currentRevision": revision,
                "serverTime": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "payload": payload,
            }
        )

    @flask_app.get("/api/next/sync/delta")
    def sync_delta():
        since = parse_int_arg("since", 0, minimum=0, maximum=9_000_000_000)
        limit = parse_int_arg("limit", 500, minimum=1, maximum=1000)
        with connect() as conn:
            revision = current_revision(conn)
            if not table_exists(conn, "sync_changes"):
                return response(
                    {
                        "status": "ok",
                        "since": since,
                        "currentRevision": revision,
                        "changes": [],
                        "hasMore": False,
                        "nextSince": revision,
                    }
                )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        revision,
                        entity_type,
                        entity_id,
                        operation,
                        payload,
                        created_at
                    FROM sync_changes
                    WHERE revision > %s
                    ORDER BY revision ASC
                    LIMIT %s
                    """,
                    (since, limit),
                )
                changes = cur.fetchall()
        next_since = int(changes[-1]["revision"]) if changes else since
        return response(
            {
                "status": "ok",
                "since": since,
                "currentRevision": revision,
                "changes": changes,
                "hasMore": bool(changes and next_since < revision),
                "nextSince": revision if not changes else next_since,
            }
        )

    @flask_app.post("/api/next/sync/mutations")
    def sync_mutations():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise NextApiError("Mutation request body must be an object", 400)
        client_id = str(body.get("clientId") or "").strip()
        if not client_id:
            raise NextApiError("clientId is required", 400)
        base_revision = body.get("baseRevision", 0)
        try:
            base_revision = int(base_revision or 0)
        except (TypeError, ValueError) as exc:
            raise NextApiError("baseRevision must be an integer", 400) from exc
        mutations = body.get("mutations")
        if not isinstance(mutations, list):
            raise NextApiError("mutations must be a list", 400)
        if len(mutations) > 100:
            raise NextApiError("At most 100 mutations can be submitted at once", 400)

        results = []
        with connect() as conn:
            ensure_sync_state(conn)
            for mutation in mutations:
                client_mutation_id = None
                if isinstance(mutation, dict):
                    client_mutation_id = mutation.get("clientMutationId")
                try:
                    with conn.transaction():
                        results.append(apply_sync_mutation(conn, client_id=client_id, mutation=mutation))
                except NextApiError as exc:
                    results.append(
                        {
                            "clientMutationId": client_mutation_id,
                            "status": "rejected",
                            "error": str(exc),
                        }
                    )
                except Exception as exc:  # pragma: no cover - safety net for per-mutation failures
                    results.append(
                        {
                            "clientMutationId": client_mutation_id,
                            "status": "error",
                            "error": str(exc),
                        }
                    )
            revision = current_revision(conn)
        return response(
            {
                "status": "ok",
                "baseRevision": base_revision,
                "currentRevision": revision,
                "results": results,
            }
        )


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
