"""Minimal PostgreSQL-backed API surface for DiscVault Next.

This Flask app is deliberately separate from the current SQLite runtime in
``app.py``. It is the first executable Next backend: it can verify PostgreSQL
connectivity, expose migration state, list metadata plugins, and read a small
collection summary from the new schema.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from flask import Flask, jsonify, request
from flask_cors import CORS
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

try:
    from .next_database import discover_migrations
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_database import discover_migrations


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
            ORDER BY key
            """
        )
        return cur.fetchall()


def metadata_plugin_entities(conn) -> list[dict[str, Any]]:
    if not table_exists(conn, "metadata_plugins"):
        return []
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
