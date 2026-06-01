"""Functional collection backup and restore helpers for DiscVault Next.

The backup format intentionally covers movie-related collection data only. It
excludes auth, passkeys, invite codes, plugin configuration, plugin secrets,
subscription state, and notifications. Personal watchlists and watch history can
be included explicitly.
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

OPTIONAL_PERSONAL_SCOPES = ("personal_watchlists", "watch_history")


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[str, ...]
    jsonb_columns: frozenset[str] = frozenset()


CORE_BACKUP_TABLE_SPECS: tuple[TableSpec, ...] = (
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

MEDIA_GROUP_BACKUP_TABLE_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        "media_groups",
        (
            "id",
            "public_id",
            "name",
            "created_by",
            "hide_digital",
            "metadata",
            "created_at",
            "updated_at",
        ),
        frozenset({"metadata"}),
    ),
    TableSpec(
        "media_group_movies",
        ("group_id", "movie_id", "metadata", "created_at", "updated_at"),
        frozenset({"metadata"}),
    ),
)

PERSONAL_LIST_BACKUP_TABLE_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        "watchlist_items",
        ("id", "user_id", "movie_id", "added_at", "snapshot"),
        frozenset({"snapshot"}),
    ),
    TableSpec(
        "watch_history",
        ("id", "user_id", "movie_id", "watched_at", "created_at", "snapshot"),
        frozenset({"snapshot"}),
    ),
)

BACKUP_TABLE_SPECS: tuple[TableSpec, ...] = (
    *CORE_BACKUP_TABLE_SPECS,
    *MEDIA_GROUP_BACKUP_TABLE_SPECS,
    *PERSONAL_LIST_BACKUP_TABLE_SPECS,
)

BACKUP_TABLES = tuple(spec.name for spec in BACKUP_TABLE_SPECS)
TABLE_SPEC_BY_NAME = {spec.name: spec for spec in BACKUP_TABLE_SPECS}
OPTIONAL_BACKUP_TABLES = tuple(
    spec.name for spec in (*MEDIA_GROUP_BACKUP_TABLE_SPECS, *PERSONAL_LIST_BACKUP_TABLE_SPECS)
)
RESTORE_DELETE_ORDER = (
    "media_group_movies",
    "watchlist_items",
    "watch_history",
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
RESTORE_INSERT_ORDER = tuple(spec.name for spec in CORE_BACKUP_TABLE_SPECS)
PERSONAL_LIST_INSERT_ORDER = tuple(spec.name for spec in PERSONAL_LIST_BACKUP_TABLE_SPECS)


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
    return root / "backups"


def safe_backup_filename(value: str) -> str:
    name = Path(str(value or "")).name
    if not name.lower().endswith(".zip") or name in {"", ".", ".."}:
        raise BackupError("Invalid backup file name")
    if "/" in name or "\\" in name:
        raise BackupError("Invalid backup file name")
    return name


def stored_backup_path(backup_dir: Path, file_name: str) -> Path:
    safe_name = safe_backup_filename(file_name)
    root = backup_dir.resolve()
    path = (root / safe_name).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise BackupError("Backup file is outside the backup directory") from exc
    return path


def list_backup_archives(backup_dir: Path, *, limit: int = 10) -> list[dict[str, Any]]:
    if not backup_dir.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(backup_dir.glob("*.zip"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            stat = path.stat()
            report = validate_backup_zip(path)
        except OSError:
            continue
        manifest = report.get("generator") or {}
        items.append(
            {
                "fileName": path.name,
                "sizeBytes": stat.st_size,
                "modifiedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z"),
                "createdAt": report.get("createdAt"),
                "valid": bool(report.get("valid")),
                "scope": report.get("scope"),
                "description": manifest.get("description") or "DiscVault movie collection backup",
                "sha256": sha256_file(path),
                "tables": report.get("tables") or {},
                "warnings": report.get("warnings") or [],
                "errors": report.get("errors") or [],
            }
        )
        if len(items) >= limit:
            break
    return items


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


def backup_specs(*, include_personal_lists: bool = False) -> tuple[TableSpec, ...]:
    specs = (*CORE_BACKUP_TABLE_SPECS, *MEDIA_GROUP_BACKUP_TABLE_SPECS)
    if include_personal_lists:
        specs = (*specs, *PERSONAL_LIST_BACKUP_TABLE_SPECS)
    return specs


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
    include_personal_lists: bool = False,
    generator: dict[str, Any] | None = None,
    requested_by: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tables: dict[str, list[dict[str, Any]]] = {}
    table_summary: dict[str, dict[str, Any]] = {}
    for spec in backup_specs(include_personal_lists=include_personal_lists):
        rows = fetch_table_rows(conn, spec)
        if spec.name == "movies":
            rows = [{**row, "owner_id": None} for row in rows]
        if spec.name == "media_groups":
            rows = [{**row, "created_by": None} for row in rows]
        tables[spec.name] = rows
        table_summary[spec.name] = {"count": len(rows)}

    excluded_scopes = [
        scope
        for scope in EXCLUDED_SCOPES
        if not (include_personal_lists and scope in OPTIONAL_PERSONAL_SCOPES)
    ]
    manifest = {
        "format": BACKUP_FORMAT,
        "formatVersion": BACKUP_FORMAT_VERSION,
        "scope": "functional_collection",
        "createdAt": utc_now(),
        "generator": generator or {},
        "requestedBy": {
            "role": requested_by.get("role") if requested_by else None,
        },
        "options": {
            "includePersonalLists": bool(include_personal_lists),
            "includeMediaGroups": True,
        },
        "excludedScopes": excluded_scopes,
        "tables": table_summary,
    }

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for table_name, rows in tables.items():
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
    names = set(zf.namelist())
    for table_name in BACKUP_TABLES:
        member = f"data/{table_name}.json"
        if member not in names and table_name in OPTIONAL_BACKUP_TABLES:
            tables[table_name] = []
            continue
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
    media_group_ids = {str(row.get("id")) for row in tables.get("media_groups", []) if row.get("id")}
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
    if tables.get("media_group_movies"):
        check("media_group_movies", "group_id", media_group_ids, "media group")
        check("media_group_movies", "movie_id", movie_ids, "movie")
    if tables.get("watchlist_items"):
        check("watchlist_items", "movie_id", movie_ids, "movie")
    if tables.get("watch_history"):
        check("watch_history", "movie_id", movie_ids, "movie")

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


def backup_restore_plan(conn, backup_zip: Path | str) -> dict[str, Any]:
    backup_zip = Path(backup_zip)
    report = validate_backup_zip(backup_zip)
    if not report.get("valid"):
        return {"valid": False, "report": report, "mediaGroups": {"missing": [], "existing": []}}
    with zipfile.ZipFile(backup_zip) as zf:
        tables = load_backup_tables(zf)

    backup_groups = tables.get("media_groups") or []
    if not backup_groups or not table_exists(conn, "media_groups"):
        return {"valid": True, "report": report, "mediaGroups": {"missing": [], "existing": []}}

    with conn.cursor() as cur:
        cur.execute("SELECT id, public_id, name FROM media_groups")
        existing_rows = [dict(row) for row in cur.fetchall()]
    existing_by_id = {str(row.get("id") or ""): row for row in existing_rows}
    existing_by_public_id = {str(row.get("public_id") or ""): row for row in existing_rows}
    existing_by_name = {str(row.get("name") or "").strip().lower(): row for row in existing_rows}

    missing: list[dict[str, Any]] = []
    existing: list[dict[str, Any]] = []
    for group in backup_groups:
        public_id = str(group.get("public_id") or "")
        name = str(group.get("name") or "")
        source_id = str(group.get("id") or "")
        match = existing_by_id.get(source_id) or existing_by_public_id.get(public_id) or existing_by_name.get(name.strip().lower())
        item = {
            "backupGroupId": group.get("id"),
            "publicId": public_id,
            "name": name,
            "movieCount": sum(1 for row in tables.get("media_group_movies", []) if str(row.get("group_id")) == str(group.get("id"))),
        }
        if match:
            existing.append(
                {
                    **item,
                    "targetGroupId": match.get("id"),
                    "targetPublicId": match.get("public_id"),
                    "targetName": match.get("name"),
                }
            )
        else:
            missing.append(item)
    return {
        "valid": True,
        "report": report,
        "mediaGroups": {
            "missing": missing,
            "existing": existing,
            "availableTargets": existing_rows,
        },
    }


def normalize_group_resolution(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"create": [], "map": {}, "skip": [], "createMissing": False}
    create = value.get("create") if isinstance(value.get("create"), list) else []
    skip = value.get("skip") if isinstance(value.get("skip"), list) else []
    mapping = value.get("map") if isinstance(value.get("map"), dict) else {}
    return {
        "create": [str(item) for item in create],
        "skip": [str(item) for item in skip],
        "map": {str(key): str(target) for key, target in mapping.items() if target},
        "createMissing": bool(value.get("createMissing")),
    }


def group_resolution_key(group: dict[str, Any]) -> str:
    return str(group.get("public_id") or group.get("id") or group.get("name") or "")


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


def clear_functional_tables(conn, *, include_personal_lists: bool = False) -> None:
    from psycopg import sql

    with conn.cursor() as cur:
        for table in RESTORE_DELETE_ORDER:
            if table in PERSONAL_LIST_INSERT_ORDER and not include_personal_lists:
                continue
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


def _row_by_uuid(conn, table: str, row_id: Any) -> dict[str, Any] | None:
    if not row_id or not table_exists(conn, table):
        return None
    from psycopg import sql

    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT * FROM {table} WHERE id=%s").format(table=sql.Identifier(table)),
            (row_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def restore_media_group_links(
    conn,
    tables: dict[str, list[dict[str, Any]]],
    *,
    group_resolution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not table_exists(conn, "media_groups") or not table_exists(conn, "media_group_movies"):
        return {"groupsCreated": 0, "groupsMapped": 0, "groupsSkipped": 0, "linksRestored": 0, "missing": []}

    resolution = normalize_group_resolution(group_resolution)
    backup_groups = tables.get("media_groups") or []
    links = tables.get("media_group_movies") or []
    with conn.cursor() as cur:
        cur.execute("SELECT id, public_id, name FROM media_groups")
        existing_rows = [dict(row) for row in cur.fetchall()]
    existing_by_id = {str(row.get("id") or ""): row for row in existing_rows}
    existing_by_public_id = {str(row.get("public_id") or ""): row for row in existing_rows}
    existing_by_name = {str(row.get("name") or "").strip().lower(): row for row in existing_rows}

    group_map: dict[str, Any] = {}
    created = 0
    mapped = 0
    skipped = 0
    missing: list[dict[str, Any]] = []

    with conn.cursor() as cur:
        for group in backup_groups:
            source_id = str(group.get("id") or "")
            public_id = str(group.get("public_id") or "")
            name = str(group.get("name") or "")
            key = group_resolution_key(group)
            if key in resolution["skip"] or source_id in resolution["skip"] or public_id in resolution["skip"]:
                skipped += 1
                continue
            explicit_target = (
                resolution["map"].get(source_id)
                or resolution["map"].get(public_id)
                or resolution["map"].get(key)
            )
            if explicit_target:
                target = _row_by_uuid(conn, "media_groups", explicit_target)
                if target:
                    group_map[source_id] = target["id"]
                    mapped += 1
                    continue
            match = existing_by_id.get(source_id) or existing_by_public_id.get(public_id) or existing_by_name.get(name.strip().lower())
            if match:
                group_map[source_id] = match["id"]
                mapped += 1
                continue
            should_create = resolution["createMissing"] or key in resolution["create"] or source_id in resolution["create"] or public_id in resolution["create"]
            if not should_create:
                missing.append(
                    {
                        "backupGroupId": source_id,
                        "publicId": public_id,
                        "name": name,
                        "movieCount": sum(1 for row in links if str(row.get("group_id")) == source_id),
                    }
                )
                continue
            cur.execute(
                """
                INSERT INTO media_groups (
                    id, public_id, name, created_by, hide_digital, metadata, created_at, updated_at
                )
                VALUES (%s, %s, %s, NULL, %s, %s, COALESCE(%s, now()), COALESCE(%s, now()))
                ON CONFLICT (public_id) DO UPDATE SET
                    name=EXCLUDED.name,
                    hide_digital=EXCLUDED.hide_digital,
                    metadata=EXCLUDED.metadata,
                    updated_at=now()
                RETURNING id
                """,
                (
                    group.get("id"),
                    public_id or f"restored-group-{source_id[:12]}",
                    name or "Restored group",
                    bool(group.get("hide_digital")),
                    adapt_value(TABLE_SPEC_BY_NAME["media_groups"], "metadata", group.get("metadata") or {}),
                    group.get("created_at"),
                    group.get("updated_at"),
                ),
            )
            row = cur.fetchone()
            if row:
                group_map[source_id] = row["id"]
                created += 1

        restored = 0
        for link in links:
            target_group_id = group_map.get(str(link.get("group_id") or ""))
            movie_id = link.get("movie_id")
            if not target_group_id or not movie_id:
                continue
            cur.execute(
                """
                INSERT INTO media_group_movies (group_id, movie_id, metadata, created_at, updated_at)
                VALUES (%s, %s, %s, COALESCE(%s, now()), COALESCE(%s, now()))
                ON CONFLICT (group_id, movie_id) DO UPDATE SET
                    metadata=EXCLUDED.metadata,
                    updated_at=now()
                """,
                (
                    target_group_id,
                    movie_id,
                    adapt_value(TABLE_SPEC_BY_NAME["media_group_movies"], "metadata", link.get("metadata") or {}),
                    link.get("created_at"),
                    link.get("updated_at"),
                ),
            )
            restored += 1

    if missing:
        raise BackupError("Media group restore resolution is required")
    return {
        "groupsCreated": created,
        "groupsMapped": mapped,
        "groupsSkipped": skipped,
        "linksRestored": restored,
        "missing": missing,
    }


def restore_personal_lists(conn, tables: dict[str, list[dict[str, Any]]], *, user_id: Any | None) -> dict[str, Any]:
    if not user_id:
        return {"watchlist_items": 0, "watch_history": 0, "skipped": "No target user was provided"}
    if table_exists(conn, "users"):
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE id=%s", (user_id,))
            if not cur.fetchone():
                return {"watchlist_items": 0, "watch_history": 0, "skipped": "Target user does not exist"}
    counters: dict[str, int] = {"watchlist_items": 0, "watch_history": 0}
    with conn.cursor() as cur:
        if table_exists(conn, "watchlist_items"):
            for row in tables.get("watchlist_items") or []:
                if not row.get("movie_id"):
                    continue
                cur.execute(
                    """
                    INSERT INTO watchlist_items (id, user_id, movie_id, added_at, snapshot)
                    VALUES (%s, %s, %s, COALESCE(%s, now()), %s)
                    ON CONFLICT (id) DO UPDATE SET
                        user_id=EXCLUDED.user_id,
                        movie_id=EXCLUDED.movie_id,
                        added_at=EXCLUDED.added_at,
                        snapshot=EXCLUDED.snapshot
                    """,
                    (
                        row.get("id"),
                        user_id,
                        row.get("movie_id"),
                        row.get("added_at"),
                        adapt_value(TABLE_SPEC_BY_NAME["watchlist_items"], "snapshot", row.get("snapshot") or {}),
                    ),
                )
                counters["watchlist_items"] += 1
        if table_exists(conn, "watch_history"):
            for row in tables.get("watch_history") or []:
                cur.execute(
                    """
                    INSERT INTO watch_history (id, user_id, movie_id, watched_at, created_at, snapshot)
                    VALUES (%s, %s, %s, %s, COALESCE(%s, now()), %s)
                    ON CONFLICT (id) DO UPDATE SET
                        user_id=EXCLUDED.user_id,
                        movie_id=EXCLUDED.movie_id,
                        watched_at=EXCLUDED.watched_at,
                        snapshot=EXCLUDED.snapshot
                    """,
                    (
                        row.get("id"),
                        user_id,
                        row.get("movie_id"),
                        row.get("watched_at"),
                        row.get("created_at"),
                        adapt_value(TABLE_SPEC_BY_NAME["watch_history"], "snapshot", row.get("snapshot") or {}),
                    ),
                )
                counters["watch_history"] += 1
    return counters


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
    include_personal_lists: bool = False,
    personal_list_user_id: Any | None = None,
    group_resolution: dict[str, Any] | None = None,
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
        clear_functional_tables(conn, include_personal_lists=include_personal_lists)
        for table_name in RESTORE_INSERT_ORDER:
            spec = TABLE_SPEC_BY_NAME[table_name]
            counters[table_name] = insert_rows(conn, spec, tables[table_name])
        group_summary = restore_media_group_links(
            conn,
            tables,
            group_resolution=group_resolution,
        )
        personal_summary = (
            restore_personal_lists(conn, tables, user_id=personal_list_user_id)
            if include_personal_lists
            else {"watchlist_items": 0, "watch_history": 0}
        )
        revision = bump_restore_revision(
            conn,
            {
                "backupFile": backup_zip.name,
                "tables": counters,
                "media": media_summary,
                "mediaGroups": group_summary,
                "personalLists": personal_summary,
            },
        )

    return {
        "validated": True,
        "dryRun": False,
        "backupFile": backup_zip.name,
        "tables": counters,
        "media": media_summary,
        "mediaGroups": group_summary,
        "personalLists": personal_summary,
        "revision": revision,
        "report": report,
    }
