import sqlite3

try:
    from .config import DB_PATH
except ImportError:  # pragma: no cover - supports running app.py directly
    from config import DB_PATH


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
