"""Minimal PostgreSQL-backed API surface for DiscVault Next.

This Flask app is deliberately separate from the current SQLite runtime in
``app.py``. It is the first executable Next backend: it can verify PostgreSQL
connectivity, expose migration state, list metadata plugins, and read a small
collection summary from the new schema.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from flask import Flask, jsonify, request
from flask_cors import CORS
from psycopg.rows import dict_row

try:
    from .next_database import discover_migrations
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_database import discover_migrations


def create_app() -> Flask:
    flask_app = Flask(__name__)
    CORS(flask_app, supports_credentials=True)
    register_routes(flask_app)
    return flask_app


app = create_app()


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


def register_routes(flask_app: Flask) -> None:
    @flask_app.errorhandler(NextApiError)
    def handle_next_error(error: NextApiError):
        return response({"status": "error", "error": str(error)}, error.status_code)

    @flask_app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception):  # pragma: no cover - Flask integration
        return response({"status": "error", "error": str(error)}, 500)

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
        return response(
            {
                "status": "ok" if migrations["state"] in {"ready", "pending_migrations"} else "degraded",
                "service": "discvault-next-api",
                "version": build_version(),
                "sha": build_sha(),
                "database": db,
                "migrations": migrations,
            }
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
