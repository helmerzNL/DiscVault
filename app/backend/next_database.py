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
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations_next"

# Startup ordering is not guaranteed by Compose: services depend on postgres
# having *started*, not on it reporting healthy, so that a slow or recovering
# database cannot abort a whole deployment. Callers wait here instead.
DB_WAIT_TIMEOUT_ENV = "DISCVAULT_NEXT_DB_WAIT_TIMEOUT"
DEFAULT_DB_WAIT_TIMEOUT = 300.0
DB_WAIT_MAX_DELAY = 5.0

# Session-scoped lock taken while migrations are applied. next-api migrates on
# every start and the `tools` profile exposes a standalone `migrate` service, so
# two runners can genuinely overlap.
MIGRATION_LOCK_KEY = int.from_bytes(
    hashlib.sha256(b"discvault_next_migrations").digest()[:8], "big", signed=True
)
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


class DatabaseUnavailableError(MigrationError):
    """Raised when PostgreSQL does not accept connections within the timeout."""


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


def db_wait_timeout() -> float:
    raw = os.environ.get(DB_WAIT_TIMEOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_DB_WAIT_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_DB_WAIT_TIMEOUT
    return value if value > 0 else DEFAULT_DB_WAIT_TIMEOUT


def wait_for_database(
    timeout: float | None = None,
    connect_fn=None,
    *,
    initial_delay: float = 0.5,
    max_delay: float = DB_WAIT_MAX_DELAY,
    sleep=time.sleep,
    monotonic=time.monotonic,
    log=None,
):
    """Open a connection, retrying while PostgreSQL refuses one.

    PostgreSQL rejects connections while it replays WAL after an unclean
    shutdown, and is not listening at all for the first moments of a container
    start. Both are transient, so retry rather than exit and rely on Docker to
    restart the container. Returns the open connection; the caller owns it.
    """
    psycopg = _load_psycopg()
    connect_fn = connect_fn or connect
    log = log or (lambda message: print(message, file=sys.stderr, flush=True))
    if timeout is None:
        timeout = db_wait_timeout()

    deadline = monotonic() + timeout
    delay = initial_delay
    attempt = 0
    last_error: Exception | None = None

    while True:
        attempt += 1
        try:
            return connect_fn()
        except psycopg.OperationalError as exc:
            last_error = exc
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            if attempt == 1:
                log(f"Waiting for PostgreSQL to accept connections: {exc}")
            sleep(min(delay, max_delay, remaining))
            delay = min(delay * 2, max_delay)

    raise DatabaseUnavailableError(
        f"PostgreSQL did not accept a connection within {timeout:g}s "
        f"({attempt} attempts). Last error: {last_error}"
    )


@contextmanager
def migration_lock(conn):
    """Hold the advisory lock that serializes concurrent migration runners."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
    conn.commit()
    try:
        yield
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
            conn.commit()
        except Exception:
            # A failed migration leaves the transaction aborted, so the unlock
            # cannot run. PostgreSQL drops session locks when the connection
            # closes, which is imminent either way. Guard the rollback itself
            # so a broken connection here can't replace whatever exception is
            # already propagating out of this `finally`.
            try:
                conn.rollback()
            except Exception:
                pass


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
    with migration_lock(conn):
        return _apply_migrations_locked(conn, migrations)


def _apply_migrations_locked(conn, migrations: list[Migration]) -> list[dict]:
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
        with wait_for_database() as conn:
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
