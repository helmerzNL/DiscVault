import hashlib
import os
import sqlite3
from pathlib import Path


COUNT_TABLES = (
    "settings",
    "users",
    "credentials",
    "movies",
    "people",
    "movie_people",
    "box_sets",
    "box_set_movies",
    "collections",
    "collection_items",
    "vaults",
    "vault_movies",
    "groups",
    "group_members",
    "group_movies",
    "watchlist",
    "watch_history",
)

MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif"}


def _settings(context):
    return (context or {}).get("settings") or {}


def _path_value(payload, context, key, fallback):
    payload = payload or {}
    settings = _settings(context)
    return str(payload.get(key) or settings.get(key) or fallback).strip()


def _data_dir(payload=None, context=None):
    return Path(_path_value(payload, context, "dataDir", os.environ.get("DISCVAULT_DATA_DIR") or "/data"))


def _sqlite_db(payload=None, context=None):
    value = _path_value(payload, context, "sqliteDb", "")
    return Path(value) if value else _data_dir(payload, context) / "discvault.db"


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _table_exists(conn, table_name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return bool(row)


def _source_counts(sqlite_db):
    counts = {}
    conn = sqlite3.connect(sqlite_db)
    try:
        for table_name in COUNT_TABLES:
            if not _table_exists(conn, table_name):
                continue
            counts[table_name] = int(conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0])
    finally:
        conn.close()
    return counts


def _media_extensions(data_dir):
    counts = {}
    if not data_dir.exists() or not data_dir.is_dir():
        return counts
    for root, _, files in os.walk(data_dir):
        if ".git" in Path(root).parts:
            continue
        for filename in files:
            ext = Path(filename).suffix.lower()
            if ext in MEDIA_EXTENSIONS:
                counts[ext] = counts.get(ext, 0) + 1
    return dict(sorted(counts.items()))


def inspect_source(payload=None, context=None):
    payload = payload or {}
    context = context or {}
    data_dir = _data_dir(payload, context)
    sqlite_db = _sqlite_db(payload, context)
    found = sqlite_db.exists() and sqlite_db.is_file()
    result = {
        "status": "ok",
        "sourceKind": "legacy_discvault_sqlite_data_dir",
        "provider": "discvault_legacy_import",
        "dataDir": str(data_dir),
        "sqliteDb": str(sqlite_db),
        "found": found,
        "readable": False,
        "sourceDatabaseHash": None,
        "sourceCounts": {},
        "mediaExtensions": {},
        "mediaMigrationMode": "reference_existing_files",
        "options": {
            "includeSecurity": True,
            "includePersonal": False,
            "importMediaReferences": True,
        },
        "warnings": [],
    }
    if not found:
        result["status"] = "not_found"
        result["warnings"].append(f"No legacy SQLite database found at {sqlite_db}.")
        return result
    try:
        result["sourceDatabaseHash"] = _sha256(sqlite_db)
        result["sourceCounts"] = _source_counts(sqlite_db)
        result["mediaExtensions"] = _media_extensions(data_dir)
        result["readable"] = True
    except Exception as exc:
        result["status"] = "unreadable"
        result["warnings"].append(f"Legacy SQLite database could not be inspected: {exc}")
    return result


def health_check(context=None):
    inspection = inspect_source({}, context or {})
    if inspection["found"] and inspection["readable"]:
        return {
            "status": "available",
            "message": "Legacy DiscVault SQLite source detected.",
            "sourceDatabaseHash": inspection.get("sourceDatabaseHash"),
            "sourceCounts": inspection.get("sourceCounts", {}),
        }
    if inspection["found"]:
        return {"status": "unavailable", "message": "Legacy DiscVault SQLite source is present but unreadable."}
    return {"status": "no_source", "message": "No legacy DiscVault SQLite source detected."}


def plan_import(payload=None, context=None):
    inspection = inspect_source(payload or {}, context or {})
    can_start = bool(inspection.get("found") and inspection.get("readable"))
    return {
        "status": "ready" if can_start else "blocked",
        "canStart": can_start,
        "source": inspection,
        "jobType": "migration.import_sqlite",
        "jobPayload": {
            "sqliteDb": inspection["sqliteDb"],
            "dataDir": inspection["dataDir"],
            "sourceDatabaseHash": inspection.get("sourceDatabaseHash"),
            "mediaMigrationMode": inspection.get("mediaMigrationMode"),
            "includeSecurity": True,
            "includePersonal": False,
            "importMediaReferences": True,
            "ownerUsername": None,
        },
    }
