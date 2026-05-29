"""Functional collection backup and restore helpers for DiscVault Next.

The backup format intentionally covers shared collection data only. It excludes
auth, passkeys, invite codes, plugin configuration, plugin secrets, subscription
state, personal watchlists, and notifications.
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID


BACKUP_FORMAT = "discvault-next-functional-backup"
BACKUP_FORMAT_VERSION = 1
BACKUP_RESTORE_JOB_TYPE = "backup.restore_functional"

EXCLUDED_SCOPES = (
    "auth",
    "passkeys",
    "invite_codes",
    "plugin_configuration",
    "plugin_secrets",
    "subscriptions",
    "personal_watchlists",
    "watch_history",
    "notifications",
    "push_subscriptions",
)


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[str, ...]
    jsonb_columns: frozenset[str] = frozenset()


BACKUP_TABLE_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        "media_assets",
        (
            "id",
            "kind",
            "variant",
            "storage_backend",
            "storage_key",
            "source_url",
            "provider_id",
            "content_type",
            "width",
            "height",
            "size_bytes",
            "sha256",
            "metadata",
            "created_at",
        ),
        frozenset({"metadata"}),
    ),
    TableSpec(
        "movies",
        (
            "id",
            "public_id",
            "barcode",
            "title",
            "sort_title",
            "original_title",
            "year",
            "release_date",
            "format",
            "edition",
            "edition_type",
            "country",
            "language",
            "runtime_minutes",
            "overview",
            "notes",
            "rating",
            "purchase_date",
            "purchase_price",
            "location",
            "owner_id",
            "metadata",
            "created_at",
            "updated_at",
        ),
        frozenset({"metadata"}),
    ),
    TableSpec(
        "movie_identifiers",
        ("movie_id", "provider_id", "identifier", "identifier_type", "created_at"),
    ),
    TableSpec(
        "movie_localizations",
        ("movie_id", "lang", "title", "overview", "created_at", "updated_at"),
    ),
    TableSpec(
        "movie_technical_specs",
        (
            "movie_id",
            "hdr",
            "packaging",
            "screen_ratios",
            "audio_tracks",
            "subtitles",
            "regions",
            "content_ratings",
            "updated_at",
        ),
        frozenset({"audio_tracks", "subtitles", "regions", "content_ratings"}),
    ),
    TableSpec(
        "people",
        (
            "id",
            "public_id",
            "name",
            "birth_date",
            "death_date",
            "place_of_birth",
            "known_for",
            "profile_asset_id",
            "metadata",
            "created_at",
            "updated_at",
        ),
        frozenset({"metadata"}),
    ),
    TableSpec(
        "person_identifiers",
        ("person_id", "provider_id", "identifier_type", "identifier", "created_at"),
    ),
    TableSpec(
        "person_localizations",
        ("person_id", "lang", "biography", "created_at", "updated_at"),
    ),
    TableSpec(
        "movie_credits",
        (
            "id",
            "movie_id",
            "person_id",
            "credit_type",
            "character",
            "job",
            "sort_order",
            "created_at",
        ),
    ),
    TableSpec(
        "containers",
        (
            "id",
            "public_id",
            "container_type",
            "title",
            "barcode",
            "badge_label",
            "year",
            "description",
            "primary_movie_id",
            "metadata",
            "created_at",
            "updated_at",
        ),
        frozenset({"metadata"}),
    ),
    TableSpec(
        "container_identifiers",
        ("container_id", "provider_id", "identifier_type", "identifier", "created_at"),
    ),
    TableSpec(
        "container_movies",
        ("container_id", "movie_id", "sort_order", "created_at"),
    ),
    TableSpec(
        "collection_items",
        ("collection_id", "item_type", "item_id", "sort_order", "created_at"),
    ),
    TableSpec(
        "entity_media",
        ("entity_type", "entity_id", "media_id", "role", "is_primary", "sort_order", "created_at"),
    ),
)

BACKUP_TABLES = tuple(spec.name for spec in BACKUP_TABLE_SPECS)
TABLE_SPEC_BY_NAME = {spec.name: spec for spec in BACKUP_TABLE_SPECS}
RESTORE_DELETE_ORDER = (
    "entity_media",
    "collection_items",
    "container_movies",
    "container_identifiers",
    "containers",
    "movie_credits",
    "movie_technical_specs",
    "movie_localizations",
    "movie_identifiers",
    "person_localizations",
    "person_identifiers",
    "people",
    "movies",
)
RESTORE_INSERT_ORDER = BACKUP_TABLES


class BackupError(RuntimeError):
    """Raised when a backup archive cannot be validated or restored."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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
    return value


def backup_storage_dir(data_dir: Path | None = None) -> Path:
    configured = os.environ.get("DISCVAULT_NEXT_BACKUP_DIR") or os.environ.get("BACKUP_DIR")
    if configured:
        return Path(configured).expanduser()
    root = data_dir or Path(os.environ.get("DISCVAULT_LEGACY_DATA_DIR") or "/data")
    return root / "backups" / "next"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_storage_key(value: Any) -> str | None:
    text = str(value or "").replace("\\", "/").strip().lstrip("/")
    if not text or text.startswith("remote/"):
        return None
    parts = [part for part in text.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        return None
    return "/".join(parts)


def archive_path_for_storage_key(storage_key: str) -> str:
    return f"media/{storage_key}"


def safe_zip_name(name: str) -> bool:
    text = str(name or "").replace("\\", "/")
    if not text or text.startswith("/") or ":" in text:
        return False
    parts = [part for part in text.split("/") if part]
    return bool(parts) and all(part not in {".", ".."} for part in parts)


def table_exists(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS table_name", (f"public.{table_name}",))
        row = cur.fetchone()
    return bool(row and row.get("table_name"))


def fetch_table_rows(conn, spec: TableSpec) -> list[dict[str, Any]]:
    if not table_exists(conn, spec.name):
        return []
    from psycopg import sql

    columns = [sql.Identifier(column) for column in spec.columns]
    query = sql.SQL("SELECT {columns} FROM {table}").format(
        columns=sql.SQL(", ").join(columns),
        table=sql.Identifier(spec.name),
    )
    with conn.cursor() as cur:
        cur.execute(query)
        return [dict(row) for row in cur.fetchall()]


def write_json(zf: zipfile.ZipFile, name: str, payload: Any) -> None:
    zf.writestr(
        name,
        json.dumps(json_ready(payload), ensure_ascii=False, indent=2, sort_keys=True),
    )


def add_media_files(
    zf: zipfile.ZipFile,
    *,
    media_rows: list[dict[str, Any]],
    data_dir: Path,
) -> dict[str, Any]:
    root = data_dir.resolve()
    items: list[dict[str, Any]] = []
    included = 0
    missing = 0
    skipped = 0

    for row in media_rows:
        if str(row.get("storage_backend") or "local") != "local":
            skipped += 1
            continue
        storage_key = clean_storage_key(row.get("storage_key"))
        if not storage_key:
            skipped += 1
            continue
        archive_path = archive_path_for_storage_key(storage_key)
        candidate = (root / Path(*storage_key.split("/"))).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            skipped += 1
            continue
        item = {
            "mediaId": row.get("id"),
            "storageKey": storage_key,
            "archivePath": archive_path,
            "sourceUrl": row.get("source_url"),
            "sha256": row.get("sha256"),
            "status": "missing",
        }
        if candidate.is_file():
            zf.write(candidate, archive_path)
            item["status"] = "included"
            item["sizeBytes"] = candidate.stat().st_size
            included += 1
        else:
            missing += 1
        items.append(item)

    return {
        "mode": "embedded_local_files",
        "included": included,
        "missing": missing,
        "skipped": skipped,
        "items": items,
    }


def export_functional_backup(
    conn,
    output_path: Path,
    *,
    data_dir: Path,
    generator: dict[str, Any] | None = None,
    requested_by: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tables: dict[str, list[dict[str, Any]]] = {}
    table_summary: dict[str, dict[str, Any]] = {}
    for spec in BACKUP_TABLE_SPECS:
        rows = fetch_table_rows(conn, spec)
        if spec.name == "movies":
            rows = [{**row, "owner_id": None} for row in rows]
        tables[spec.name] = rows
        table_summary[spec.name] = {"count": len(rows)}

    manifest = {
        "format": BACKUP_FORMAT,
        "formatVersion": BACKUP_FORMAT_VERSION,
        "scope": "functional_collection",
        "createdAt": utc_now(),
        "generator": generator or {},
        "requestedBy": {
            "role": requested_by.get("role") if requested_by else None,
        },
        "excludedScopes": list(EXCLUDED_SCOPES),
        "tables": table_summary,
    }

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for table_name in BACKUP_TABLES:
            write_json(zf, f"data/{table_name}.json", tables[table_name])
        manifest["media"] = add_media_files(
            zf,
            media_rows=tables.get("media_assets") or [],
            data_dir=data_dir,
        )
        write_json(zf, "manifest.json", manifest)

    size = output_path.stat().st_size
    return {
        "fileName": output_path.name,
        "path": str(output_path),
        "sizeBytes": size,
        "sha256": sha256_file(output_path),
        "manifest": manifest,
    }


def load_json_member(zf: zipfile.ZipFile, name: str) -> Any:
    try:
        with zf.open(name) as handle:
            return json.loads(handle.read().decode("utf-8"))
    except KeyError as exc:
        raise BackupError(f"Missing archive member: {name}") from exc
    except json.JSONDecodeError as exc:
        raise BackupError(f"Invalid JSON in archive member: {name}") from exc


def load_backup_tables(zf: zipfile.ZipFile) -> dict[str, list[dict[str, Any]]]:
    tables: dict[str, list[dict[str, Any]]] = {}
    for table_name in BACKUP_TABLES:
        member = f"data/{table_name}.json"
        rows = load_json_member(zf, member)
        if not isinstance(rows, list):
            raise BackupError(f"Backup member must be a JSON array: {member}")
        tables[table_name] = [row for row in rows if isinstance(row, dict)]
        if len(tables[table_name]) != len(rows):
            raise BackupError(f"Backup member contains non-object rows: {member}")
    return tables


def validate_relationships(tables: dict[str, list[dict[str, Any]]]) -> list[str]:
    errors: list[str] = []
    movie_ids = {str(row.get("id")) for row in tables["movies"] if row.get("id")}
    person_ids = {str(row.get("id")) for row in tables["people"] if row.get("id")}
    container_ids = {str(row.get("id")) for row in tables["containers"] if row.get("id")}
    container_type_by_id = {
        str(row.get("id")): str(row.get("container_type") or "")
        for row in tables["containers"]
        if row.get("id")
    }
    media_ids = {str(row.get("id")) for row in tables["media_assets"] if row.get("id")}

    def check(table: str, column: str, valid: set[str], label: str) -> None:
        for index, row in enumerate(tables[table], start=1):
            value = row.get(column)
            if value and str(value) not in valid:
                errors.append(f"{table}[{index}].{column} references missing {label}: {value}")

    check("movie_identifiers", "movie_id", movie_ids, "movie")
    check("movie_localizations", "movie_id", movie_ids, "movie")
    check("movie_technical_specs", "movie_id", movie_ids, "movie")
    check("person_identifiers", "person_id", person_ids, "person")
    check("person_localizations", "person_id", person_ids, "person")
    check("movie_credits", "movie_id", movie_ids, "movie")
    check("movie_credits", "person_id", person_ids, "person")
    check("container_identifiers", "container_id", container_ids, "container")
    check("container_movies", "container_id", container_ids, "container")
    check("container_movies", "movie_id", movie_ids, "movie")
    check("collection_items", "collection_id", container_ids, "collection container")

    for index, row in enumerate(tables["containers"], start=1):
        primary = row.get("primary_movie_id")
        if primary and str(primary) not in movie_ids:
            errors.append(f"containers[{index}].primary_movie_id references missing movie: {primary}")

    valid_collection_item_sets = {
        "movie": movie_ids,
        "vault": container_ids,
        "box_set": container_ids,
        "collection": container_ids,
    }
    for index, row in enumerate(tables["collection_items"], start=1):
        item_type = str(row.get("item_type") or "")
        item_id = row.get("item_id")
        valid = valid_collection_item_sets.get(item_type)
        if not valid:
            errors.append(f"collection_items[{index}].item_type is invalid: {item_type}")
        elif item_id and str(item_id) not in valid:
            errors.append(f"collection_items[{index}].item_id references missing {item_type}: {item_id}")
        elif item_type in {"vault", "box_set", "collection"} and item_id:
            actual_type = container_type_by_id.get(str(item_id))
            if actual_type != item_type:
                errors.append(
                    f"collection_items[{index}].item_id references {actual_type or 'unknown'} container, expected {item_type}: {item_id}"
                )

    valid_entity_sets = {
        "movie": movie_ids,
        "person": person_ids,
        "container": container_ids,
    }
    for index, row in enumerate(tables["entity_media"], start=1):
        media_id = row.get("media_id")
        entity_type = str(row.get("entity_type") or "")
        entity_id = row.get("entity_id")
        if media_id and str(media_id) not in media_ids:
            errors.append(f"entity_media[{index}].media_id references missing media asset: {media_id}")
        valid = valid_entity_sets.get(entity_type)
        if valid is not None and entity_id and str(entity_id) not in valid:
            errors.append(f"entity_media[{index}].entity_id references missing {entity_type}: {entity_id}")

    return errors


def validate_backup_zip(backup_zip: Path | str) -> dict[str, Any]:
    backup_zip = Path(backup_zip)
    errors: list[str] = []
    warnings: list[str] = []
    if not backup_zip.exists() or not backup_zip.is_file():
        return {
            "valid": False,
            "errors": [f"Backup ZIP not found: {backup_zip}"],
            "warnings": warnings,
        }

    try:
        with zipfile.ZipFile(backup_zip) as zf:
            names = set(zf.namelist())
            unsafe = sorted(name for name in names if not safe_zip_name(name))
            if unsafe:
                errors.append(f"Archive contains unsafe paths: {', '.join(unsafe[:5])}")
            manifest = load_json_member(zf, "manifest.json")
            if manifest.get("format") != BACKUP_FORMAT:
                errors.append(f"Unsupported backup format: {manifest.get('format')}")
            if int(manifest.get("formatVersion") or 0) != BACKUP_FORMAT_VERSION:
                errors.append(f"Unsupported backup format version: {manifest.get('formatVersion')}")
            if manifest.get("scope") != "functional_collection":
                errors.append(f"Unsupported backup scope: {manifest.get('scope')}")
            tables = load_backup_tables(zf)
            errors.extend(validate_relationships(tables))

            table_report = {table: {"count": len(rows)} for table, rows in tables.items()}
            manifest_tables = manifest.get("tables") or {}
            for table, report in table_report.items():
                expected = (manifest_tables.get(table) or {}).get("count")
                if expected is not None and int(expected) != report["count"]:
                    warnings.append(
                        f"Manifest count for {table} is {expected}, but archive contains {report['count']} rows."
                    )

            missing_media = 0
            embedded_media = 0
            for row in tables["media_assets"]:
                if str(row.get("storage_backend") or "local") != "local":
                    continue
                storage_key = clean_storage_key(row.get("storage_key"))
                if not storage_key:
                    continue
                archive_path = archive_path_for_storage_key(storage_key)
                if archive_path in names:
                    embedded_media += 1
                else:
                    missing_media += 1
            if missing_media:
                warnings.append(f"{missing_media} local media assets are referenced but not embedded in the ZIP.")

            return {
                "valid": not errors,
                "format": manifest.get("format"),
                "formatVersion": manifest.get("formatVersion"),
                "scope": manifest.get("scope"),
                "createdAt": manifest.get("createdAt"),
                "generator": manifest.get("generator") or {},
                "excludedScopes": manifest.get("excludedScopes") or [],
                "tables": table_report,
                "media": {
                    "embedded": embedded_media,
                    "missing": missing_media,
                    "manifest": manifest.get("media") or {},
                },
                "errors": errors,
                "warnings": warnings,
            }
    except (BackupError, zipfile.BadZipFile, OSError, ValueError) as exc:
        return {
            "valid": False,
            "errors": [str(exc)],
            "warnings": warnings,
        }


def restore_media_files(backup_zip: Path, data_dir: Path, tables: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    root = data_dir.resolve()
    restored = 0
    missing = 0
    skipped = 0
    with zipfile.ZipFile(backup_zip) as zf:
        names = set(zf.namelist())
        for row in tables["media_assets"]:
            if str(row.get("storage_backend") or "local") != "local":
                skipped += 1
                continue
            storage_key = clean_storage_key(row.get("storage_key"))
            if not storage_key:
                skipped += 1
                continue
            archive_path = archive_path_for_storage_key(storage_key)
            if archive_path not in names:
                missing += 1
                continue
            target = (root / Path(*storage_key.split("/"))).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(archive_path) as source, target.open("wb") as destination:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    destination.write(chunk)
            restored += 1
    return {"restored": restored, "missing": missing, "skipped": skipped}


def adapt_value(spec: TableSpec, column: str, value: Any) -> Any:
    if column in spec.jsonb_columns:
        from psycopg.types.json import Jsonb

        return Jsonb(value if value is not None else ({} if column in {"metadata", "content_ratings"} else []))
    return value


def insert_rows(conn, spec: TableSpec, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    from psycopg import sql

    columns = list(spec.columns)
    query = sql.SQL("INSERT INTO {table} ({columns}) VALUES ({placeholders})").format(
        table=sql.Identifier(spec.name),
        columns=sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        placeholders=sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(query, [adapt_value(spec, column, row.get(column)) for column in columns])
    return len(rows)


def clear_functional_tables(conn) -> None:
    from psycopg import sql

    with conn.cursor() as cur:
        for table in RESTORE_DELETE_ORDER:
            if table_exists(conn, table):
                cur.execute(sql.SQL("DELETE FROM {table}").format(table=sql.Identifier(table)))
        if table_exists(conn, "media_assets"):
            if table_exists(conn, "users"):
                cur.execute(
                    """
                    DELETE FROM media_assets ma
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM users u
                        WHERE u.avatar_asset_id = ma.id
                    )
                    """
                )
            else:
                cur.execute("DELETE FROM media_assets")


def bump_restore_revision(conn, summary: dict[str, Any]) -> int | None:
    if not table_exists(conn, "sync_state"):
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sync_state (id, revision)
            VALUES ('global', 0)
            ON CONFLICT (id) DO NOTHING
            """
        )
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
        revision = int(row["revision"]) if row else None
        if revision and table_exists(conn, "sync_changes"):
            from psycopg.types.json import Jsonb

            cur.execute(
                """
                INSERT INTO sync_changes (revision, entity_type, entity_id, operation, payload)
                VALUES (%s, 'collection', 'functional', 'restore', %s)
                """,
                (revision, Jsonb(summary)),
            )
    return revision


def restore_functional_backup(
    conn,
    backup_zip: Path,
    *,
    data_dir: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    report = validate_backup_zip(backup_zip)
    if not report.get("valid"):
        raise BackupError("; ".join(report.get("errors") or ["Backup validation failed"]))

    with zipfile.ZipFile(backup_zip) as zf:
        tables = load_backup_tables(zf)
    for row in tables.get("movies") or []:
        row["owner_id"] = None

    if dry_run:
        return {"validated": True, "dryRun": True, "report": report}

    media_summary = restore_media_files(backup_zip, data_dir, tables)
    counters: dict[str, int] = {}
    with conn.transaction():
        clear_functional_tables(conn)
        for table_name in RESTORE_INSERT_ORDER:
            spec = TABLE_SPEC_BY_NAME[table_name]
            counters[table_name] = insert_rows(conn, spec, tables[table_name])
        revision = bump_restore_revision(
            conn,
            {
                "backupFile": backup_zip.name,
                "tables": counters,
                "media": media_summary,
            },
        )

    return {
        "validated": True,
        "dryRun": False,
        "backupFile": backup_zip.name,
        "tables": counters,
        "media": media_summary,
        "revision": revision,
        "report": report,
    }
