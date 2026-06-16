#!/usr/bin/env python3
"""Generate a DiscVault database and media audit for DiscVault Next planning.

The script is intentionally standalone: it does not import the Flask app, so it
can inspect copied production databases without triggering migrations or app
startup side effects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
APP_DIR = SCRIPT_DIR.parent
REPO_ROOT = APP_DIR.parent
DEFAULT_DB = APP_DIR / ".local" / "discvault.db"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "app" / ".local" / "audits"

SQLITE_SPECIFIC_PATTERNS = (
    "sqlite3",
    "PRAGMA",
    "INSERT OR IGNORE",
    "INSERT OR REPLACE",
    "last_insert_rowid",
    "rowid",
    "AUTOINCREMENT",
    "COLLATE NOCASE",
)

SENSITIVE_TABLES = {
    "api_keys",
    "challenges",
    "credentials",
    "invite_codes",
    "mobile_codes",
    "mobile_flows",
    "push_subscriptions",
    "role_permissions",
    "user_roles",
    "users",
}

SECURITY_RELATED_TABLES = {
    "api_keys",
    "challenges",
    "credentials",
    "custom_roles",
    "group_invites",
    "invite_codes",
    "role_permissions",
    "user_groups",
    "user_roles",
    "users",
}

DEVICE_RELATED_TABLES = {
    "notification_prefs",
    "push_subscriptions",
    "user_preferences",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def connect_sqlite(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def rows_to_dicts(rows) -> list[dict]:
    return [dict(row) for row in rows]


def table_classification(table: str) -> str:
    if table in SECURITY_RELATED_TABLES:
        return "security"
    if table in DEVICE_RELATED_TABLES:
        return "device_personal"
    if table.startswith("sqlite_"):
        return "sqlite_internal"
    return "functional"


def audit_sqlite_schema(db_path: Path) -> dict:
    conn = connect_sqlite(db_path)
    try:
        objects = rows_to_dicts(
            conn.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE type IN ('table', 'index', 'trigger', 'view')
                ORDER BY type, name
                """
            ).fetchall()
        )
        tables = [
            obj["name"]
            for obj in objects
            if obj["type"] == "table" and not obj["name"].startswith("sqlite_")
        ]
        table_details = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            table_details[table] = {
                "classification": table_classification(table),
                "sensitive": table in SENSITIVE_TABLES,
                "columns": rows_to_dicts(conn.execute(f"PRAGMA table_info({quoted})").fetchall()),
                "foreign_keys": rows_to_dicts(
                    conn.execute(f"PRAGMA foreign_key_list({quoted})").fetchall()
                ),
                "indexes": [],
                "row_count": None,
            }
            for index in conn.execute(f"PRAGMA index_list({quoted})").fetchall():
                index_dict = dict(index)
                index_name = index_dict.get("name")
                if index_name:
                    index_quoted = '"' + str(index_name).replace('"', '""') + '"'
                    index_dict["columns"] = rows_to_dicts(
                        conn.execute(f"PRAGMA index_info({index_quoted})").fetchall()
                    )
                table_details[table]["indexes"].append(index_dict)
            try:
                table_details[table]["row_count"] = conn.execute(
                    f"SELECT COUNT(*) FROM {quoted}"
                ).fetchone()[0]
            except Exception as exc:  # pragma: no cover - defensive for damaged DBs
                table_details[table]["row_count_error"] = str(exc)

        integrity_check = None
        foreign_key_check = None
        try:
            integrity_check = [row[0] for row in conn.execute("PRAGMA integrity_check").fetchall()]
        except Exception as exc:
            integrity_check = [f"error: {exc}"]
        try:
            foreign_key_check = rows_to_dicts(conn.execute("PRAGMA foreign_key_check").fetchall())
        except Exception as exc:
            foreign_key_check = [{"error": str(exc)}]

        return {
            "database": {
                "path": str(db_path),
                "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
                "sha256": sha256_file(db_path) if db_path.exists() else "",
            },
            "objects": objects,
            "tables": table_details,
            "integrity_check": integrity_check,
            "foreign_key_check": foreign_key_check,
        }
    finally:
        conn.close()


def audit_media_dir(path: Path) -> dict:
    if not path.exists():
        return {"path": str(path), "exists": False}

    files = []
    extension_counts: Counter[str] = Counter()
    total_bytes = 0
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        try:
            stat = item.stat()
        except OSError:
            continue
        rel = item.relative_to(path).as_posix()
        ext = item.suffix.lower() or "(none)"
        total_bytes += stat.st_size
        extension_counts[ext] += 1
        files.append(
            {
                "relative_path": rel,
                "size_bytes": stat.st_size,
                "modified_utc": datetime.fromtimestamp(
                    stat.st_mtime, timezone.utc
                ).isoformat(timespec="seconds"),
            }
        )

    return {
        "path": str(path),
        "exists": True,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "extensions": dict(sorted(extension_counts.items())),
        "files": files[:500],
        "files_truncated": len(files) > 500,
    }


def audit_media(data_dir: Path) -> dict:
    candidates = {
        "data_dir": data_dir,
        "posters": data_dir / "posters",
        "profiles": data_dir / "profiles",
        "avatars": data_dir / "avatars",
        "backups": data_dir / "backups",
    }
    return {name: audit_media_dir(path) for name, path in candidates.items()}


def scan_sqlite_specific_code(root: Path) -> list[dict]:
    findings = []
    if not root.exists():
        return findings
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            for pattern in SQLITE_SPECIFIC_PATTERNS:
                if pattern.lower() in line.lower():
                    findings.append(
                        {
                            "file": str(path.relative_to(REPO_ROOT)),
                            "line": line_no,
                            "pattern": pattern,
                            "snippet": line.strip()[:240],
                        }
                    )
    return findings


def build_summary(audit: dict) -> dict:
    tables = audit["sqlite"]["tables"]
    classifications = Counter(
        details.get("classification", "unknown") for details in tables.values()
    )
    return {
        "table_count": len(tables),
        "row_counts": {
            name: details.get("row_count")
            for name, details in sorted(tables.items())
        },
        "table_classifications": dict(sorted(classifications.items())),
        "sqlite_specific_code_findings": len(audit["sqlite_specific_code"]),
        "media_total_bytes": sum(
            entry.get("total_bytes", 0)
            for entry in audit["media"].values()
            if isinstance(entry, dict)
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit a DiscVault SQLite database for DiscVault Next planning."
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("DB_PATH") or str(DEFAULT_DB),
        help="Path to the SQLite database to audit.",
    )
    parser.add_argument(
        "--data-dir",
        default="",
        help="Persistent data directory. Defaults to the database parent directory.",
    )
    parser.add_argument(
        "--source-root",
        default=str(APP_DIR / "backend"),
        help="Python source root to scan for SQLite-specific SQL.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory where the JSON audit file will be written.",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print the full JSON audit to stdout instead of writing a file.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).expanduser().resolve()
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else db_path.parent
    source_root = Path(args.source_root).expanduser().resolve()

    audit = {
        "generated_at": utc_now(),
        "tool": "app/scripts/db_audit.py",
        "purpose": "DiscVault Next PostgreSQL/import/plugin planning",
        "sqlite": audit_sqlite_schema(db_path),
        "media": audit_media(data_dir),
        "sqlite_specific_code": scan_sqlite_specific_code(source_root),
    }
    audit["summary"] = build_summary(audit)

    if args.stdout:
        print(json.dumps(audit, indent=2, ensure_ascii=False))
        return 0

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_path = output_dir / f"discvault-db-audit-{timestamp}.json"
    output_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote audit: {output_path}")
    print(json.dumps(audit["summary"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
