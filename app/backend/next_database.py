"""DiscVault Next PostgreSQL migration helpers.

This module is intentionally separate from the current SQLite app runtime. It is
the first PostgreSQL foundation for DiscVault Next and can be executed as a
standalone migration/status tool while the existing app remains unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path


MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations_next"
SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    name       text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now(),
    checksum   text NOT NULL
)
"""


class MigrationError(RuntimeError):
    """Raised when DiscVault Next migrations cannot be applied safely."""


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    path: Path
    checksum: str
    sql: str


def _load_psycopg():
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise MigrationError(
            "psycopg is required for DiscVault Next PostgreSQL migrations. "
            "Install backend requirements first."
        ) from exc
    return psycopg


def database_url() -> str:
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise MigrationError("DATABASE_URL is required for DiscVault Next migrations.")
    return value


def discover_migrations(migrations_dir: Path = MIGRATIONS_DIR) -> list[Migration]:
    if not migrations_dir.exists():
        raise MigrationError(f"Migration directory not found: {migrations_dir}")

    migrations: list[Migration] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        stem = path.stem
        if "_" not in stem:
            raise MigrationError(f"Migration filename must be '<version>_<name>.sql': {path.name}")
        version, name = stem.split("_", 1)
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        migrations.append(Migration(version=version, name=name, path=path, checksum=checksum, sql=sql))

    versions = [m.version for m in migrations]
    if len(versions) != len(set(versions)):
        raise MigrationError("Duplicate migration versions found.")
    return migrations


def connect():
    psycopg = _load_psycopg()
    return psycopg.connect(database_url(), autocommit=False)


def ensure_migration_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_MIGRATIONS_SQL)
    conn.commit()


def applied_migrations(conn) -> dict[str, dict]:
    ensure_migration_table(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT version, name, checksum, applied_at FROM schema_migrations ORDER BY version")
        rows = cur.fetchall()
    return {
        row[0]: {
            "version": row[0],
            "name": row[1],
            "checksum": row[2],
            "applied_at": row[3].isoformat() if row[3] else None,
        }
        for row in rows
    }


def migration_status(conn, migrations: list[Migration]) -> list[dict]:
    applied = applied_migrations(conn)
    status = []
    for migration in migrations:
        row = applied.get(migration.version)
        state = "pending"
        if row:
            state = "applied" if row["checksum"] == migration.checksum else "checksum_mismatch"
        status.append(
            {
                "version": migration.version,
                "name": migration.name,
                "path": str(migration.path),
                "checksum": migration.checksum,
                "state": state,
                "applied_at": row.get("applied_at") if row else None,
            }
        )
    return status


def apply_migrations(conn, migrations: list[Migration]) -> list[dict]:
    ensure_migration_table(conn)
    applied = applied_migrations(conn)
    results = []

    for migration in migrations:
        existing = applied.get(migration.version)
        if existing:
            if existing["checksum"] != migration.checksum:
                raise MigrationError(
                    f"Checksum mismatch for migration {migration.version}_{migration.name}. "
                    "Refusing to continue."
                )
            results.append({"version": migration.version, "name": migration.name, "state": "already_applied"})
            continue

        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(migration.sql)
                    cur.execute(
                        """
                        INSERT INTO schema_migrations (version, name, checksum)
                        VALUES (%s, %s, %s)
                        """,
                        (migration.version, migration.name, migration.checksum),
                    )
            results.append({"version": migration.version, "name": migration.name, "state": "applied"})
        except Exception as exc:
            raise MigrationError(f"Failed migration {migration.version}_{migration.name}: {exc}") from exc

    return results


def print_table(rows: list[dict]) -> None:
    if not rows:
        print("No migrations found.")
        return
    for row in rows:
        version = row.get("version", "")
        name = row.get("name", "")
        state = row.get("state", "")
        applied_at = row.get("applied_at") or ""
        print(f"{version:>4}  {state:<18}  {name:<36} {applied_at}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DiscVault Next PostgreSQL migration runner.")
    parser.add_argument(
        "command",
        choices=("status", "migrate"),
        help="Show migration status or apply pending migrations.",
    )
    parser.add_argument(
        "--migrations-dir",
        default=str(MIGRATIONS_DIR),
        help="Directory containing DiscVault Next SQL migrations.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    migrations = discover_migrations(Path(args.migrations_dir).resolve())
    try:
        with connect() as conn:
            if args.command == "status":
                print_table(migration_status(conn, migrations))
                return 0
            results = apply_migrations(conn, migrations)
            for result in results:
                print(f"{result['version']} {result['name']}: {result['state']}")
            return 0
    except MigrationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
