#!/usr/bin/env python3
"""Export a DiscVault SQLite database to canonical NDJSON files.

This is the first half of the DiscVault Next migration path. It creates a
portable export package that can later be imported into PostgreSQL after the
target schema and transformation rules are finalized.

By default the export excludes security and device-specific data such as users,
passkeys and push subscriptions. Include those only through explicit flags.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db_audit import (
    DEFAULT_DB,
    DEFAULT_OUTPUT_DIR,
    REPO_ROOT,
    sha256_file,
    table_classification,
    utc_now,
)


DEFAULT_SKIP_TABLES = {
    "challenges",
    "import_controls",
    "logs",
    "mobile_codes",
    "mobile_flows",
    "sqlite_sequence",
}


def json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {
            "__type": "bytes",
            "encoding": "base64",
            "value": base64.b64encode(value).decode("ascii"),
        }
    return value


def connect_sqlite(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        ORDER BY name
        """
    ).fetchall()
    return [row["name"] for row in rows if not str(row["name"]).startswith("sqlite_")]


def should_export_table(
    table: str,
    *,
    include_security: bool,
    include_device: bool,
    include_logs: bool,
    include_internal: bool,
) -> tuple[bool, str]:
    classification = table_classification(table)
    if table in DEFAULT_SKIP_TABLES:
        if table == "logs" and include_logs:
            return True, "included_logs"
        if include_internal:
            return True, "included_internal"
        return False, "default_skip"
    if classification == "security" and not include_security:
        return False, "security_excluded"
    if classification == "device_personal" and not include_device:
        return False, "device_personal_excluded"
    if classification == "sqlite_internal" and not include_internal:
        return False, "sqlite_internal_excluded"
    return True, "included"


def media_manifest(data_dir: Path, hash_media: bool) -> dict:
    if not data_dir.exists():
        return {
            "path": str(data_dir),
            "exists": False,
            "files": [],
            "file_count": 0,
            "total_bytes": 0,
        }

    files = []
    extensions: Counter[str] = Counter()
    total_bytes = 0
    for item in sorted(data_dir.rglob("*")):
        if not item.is_file():
            continue
        try:
            stat = item.stat()
        except OSError:
            continue
        rel = item.relative_to(data_dir).as_posix()
        entry = {
            "relative_path": rel,
            "size_bytes": stat.st_size,
            "modified_utc": datetime.fromtimestamp(
                stat.st_mtime, timezone.utc
            ).isoformat(timespec="seconds"),
        }
        if hash_media:
            entry["sha256"] = sha256_file(item)
        files.append(entry)
        total_bytes += stat.st_size
        extensions[item.suffix.lower() or "(none)"] += 1
    return {
        "path": str(data_dir),
        "exists": True,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "extensions": dict(sorted(extensions.items())),
        "files": files,
    }


def export_table(conn: sqlite3.Connection, table: str, output_path: Path) -> int:
    quoted = quote_identifier(table)
    count = 0
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in conn.execute(f"SELECT * FROM {quoted}"):
            payload = {key: json_safe(row[key]) for key in row.keys()}
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
            count += 1
    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a DiscVault SQLite database into canonical NDJSON files."
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("DB_PATH") or str(DEFAULT_DB),
        help="Path to the SQLite database to export.",
    )
    parser.add_argument(
        "--data-dir",
        default="",
        help="Persistent data directory. Defaults to the database parent directory.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR / "exports"),
        help="Directory where the export package directory will be created.",
    )
    parser.add_argument(
        "--include-security",
        action="store_true",
        help="Include users, passkeys, roles and other security tables.",
    )
    parser.add_argument(
        "--include-device",
        action="store_true",
        help="Include push subscriptions and device/personal preference tables.",
    )
    parser.add_argument(
        "--include-logs",
        action="store_true",
        help="Include logs. Logs are excluded by default.",
    )
    parser.add_argument(
        "--include-internal",
        action="store_true",
        help="Include internal/control tables that are skipped by default.",
    )
    parser.add_argument(
        "--hash-media",
        action="store_true",
        help="Hash media files in the media manifest. Slower on large libraries.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).expanduser().resolve()
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else db_path.parent
    output_root = Path(args.output_dir).expanduser().resolve()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    export_dir = output_root / f"discvault-sqlite-export-{timestamp}"
    tables_dir = export_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=False)

    manifest = {
        "generated_at": utc_now(),
        "tool": "app/scripts/sqlite_export.py",
        "purpose": "DiscVault Next PostgreSQL import preparation",
        "repository_root": str(REPO_ROOT),
        "source_database": {
            "path": str(db_path),
            "size_bytes": db_path.stat().st_size,
            "sha256": sha256_file(db_path),
        },
        "options": {
            "include_security": bool(args.include_security),
            "include_device": bool(args.include_device),
            "include_logs": bool(args.include_logs),
            "include_internal": bool(args.include_internal),
            "hash_media": bool(args.hash_media),
        },
        "tables": {},
        "excluded_tables": {},
        "media": media_manifest(data_dir, bool(args.hash_media)),
    }

    conn = connect_sqlite(db_path)
    try:
        for table in table_names(conn):
            exported, reason = should_export_table(
                table,
                include_security=bool(args.include_security),
                include_device=bool(args.include_device),
                include_logs=bool(args.include_logs),
                include_internal=bool(args.include_internal),
            )
            classification = table_classification(table)
            if not exported:
                manifest["excluded_tables"][table] = {
                    "classification": classification,
                    "reason": reason,
                }
                continue

            output_path = tables_dir / f"{table}.ndjson"
            row_count = export_table(conn, table, output_path)
            manifest["tables"][table] = {
                "classification": classification,
                "row_count": row_count,
                "file": output_path.relative_to(export_dir).as_posix(),
                "sha256": sha256_file(output_path),
            }
    finally:
        conn.close()

    manifest_path = export_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    summary = {
        "export_dir": str(export_dir),
        "exported_tables": len(manifest["tables"]),
        "excluded_tables": len(manifest["excluded_tables"]),
        "exported_rows": sum(t["row_count"] for t in manifest["tables"].values()),
        "media_files": manifest["media"].get("file_count", 0),
        "media_total_bytes": manifest["media"].get("total_bytes", 0),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
