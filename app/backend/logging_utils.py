import sqlite3

try:
    from .config import DB_PATH, local_now_iso
except ImportError:  # pragma: no cover - supports running app.py directly
    from config import DB_PATH, local_now_iso


def add_log(category: str, message: str, detail: str = "", level: str = "info",
            user_id: str = None):
    """Write a structured log entry to the database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO logs (timestamp, level, category, message, detail, user_id) VALUES (?,?,?,?,?,?)",
            (local_now_iso(), level, category, message, detail or "", user_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
