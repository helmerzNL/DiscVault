import os
import sqlite3

try:
    from .config import DB_PATH
except ImportError:  # pragma: no cover - supports running app.py directly
    from config import DB_PATH


SQLITE_BUSY_TIMEOUT_MS = int(os.environ.get("SQLITE_BUSY_TIMEOUT_MS", "30000"))
SQLITE_TIMEOUT_SECONDS = max(SQLITE_BUSY_TIMEOUT_MS / 1000.0, 1.0)


def configure_connection(conn):
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def connect_db(row_factory=False):
    conn = sqlite3.connect(DB_PATH, timeout=SQLITE_TIMEOUT_SECONDS)
    configure_connection(conn)
    if row_factory:
        conn.row_factory = sqlite3.Row
    return conn


def enable_wal(conn):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA wal_autocheckpoint=1000")
    return conn


def get_db():
    conn = connect_db(row_factory=True)
    conn.row_factory = sqlite3.Row
    return conn
