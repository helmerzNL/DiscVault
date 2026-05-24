import os
import secrets
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo


DB_PATH = os.environ.get("DB_PATH", "/data/discvault.db")
SQLITE_BUSY_TIMEOUT_MS = int(os.environ.get("SQLITE_BUSY_TIMEOUT_MS", "30000"))
SQLITE_TIMEOUT_SECONDS = max(SQLITE_BUSY_TIMEOUT_MS / 1000.0, 1.0)
POSTER_DIR = os.environ.get("POSTER_DIR", "/data/posters")
PROFILE_DIR = os.environ.get("PROFILE_DIR", "/data/profiles")
BACKUP_DIR = os.environ.get("BACKUP_DIR", "/data/backups")
AVATAR_DIR = os.environ.get("AVATAR_DIR", "/data/avatars")

_OMDB_API_KEY_ENV = os.environ.get("OMDB_API_KEY", "")
_TMDB_API_KEY_ENV = os.environ.get("TMDB_API_KEY", "")
_MOVIEVAULT_API_TOKEN_ENV = os.environ.get(
    "MOVIEVAULT_API_TOKEN", os.environ.get("MOVIEVAULT_API_KEY", "")
)
_MOVIEVAULT_SEARCH_URL_ENV = os.environ.get(
    "MOVIEVAULT_SEARCH_URL",
    os.environ.get("MOVIEVAULT_BASE_URL", "https://search.discvault.eu"),
)
_MOVIEVAULT_INGEST_URL_ENV = os.environ.get(
    "MOVIEVAULT_INGEST_URL", "https://movies.discvault.eu"
)
_MOVIEVAULT_CONTRIBUTION_URL_ENV = os.environ.get("MOVIEVAULT_CONTRIBUTION_URL", "")
_MOVIEVAULT_SHARING_MODE_ENV = os.environ.get("MOVIEVAULT_SHARING_MODE", "opt_in")
_MOVIEVAULT_DISCVAULT_HANDSHAKE_SECRET_ENV = os.environ.get(
    "MOVIEVAULT_DISCVAULT_HANDSHAKE_SECRET", ""
)


class LiveKey:
    """String-like API key resolved from DB settings with env fallback."""

    def __init__(self, db_setting: str, env_fallback: str):
        self._db_setting = db_setting
        self._env_fallback = env_fallback

    def _resolve(self) -> str:
        try:
            with sqlite3.connect(DB_PATH, timeout=SQLITE_TIMEOUT_SECONDS) as conn:
                conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
                row = conn.execute(
                    "SELECT value FROM settings WHERE key=?", (self._db_setting,)
                ).fetchone()
            if row and row[0]:
                return row[0].strip()
        except Exception:
            pass
        return self._env_fallback

    def __bool__(self):
        return bool(self._resolve())

    def __str__(self):
        return self._resolve()

    def __repr__(self):
        return repr(self._resolve())

    def __format__(self, fmt):
        return format(self._resolve(), fmt)

    def __eq__(self, other):
        return self._resolve() == other

    def __hash__(self):
        return hash(self._resolve())


OMDB_API_KEY = LiveKey("omdb_api_key", _OMDB_API_KEY_ENV)
TMDB_API_KEY = LiveKey("tmdb_api_key", _TMDB_API_KEY_ENV)
MOVIEVAULT_API_TOKEN = LiveKey("movievault_api_token", _MOVIEVAULT_API_TOKEN_ENV)
MOVIEVAULT_API_KEY = LiveKey("movievault_api_key", "")
MOVIEVAULT_SEARCH_URL = LiveKey("movievault_search_url", _MOVIEVAULT_SEARCH_URL_ENV)
MOVIEVAULT_BASE_URL = LiveKey("movievault_base_url", "")
MOVIEVAULT_INGEST_URL = LiveKey("movievault_ingest_url", _MOVIEVAULT_INGEST_URL_ENV)
MOVIEVAULT_CONTRIBUTION_URL = LiveKey(
    "movievault_contribution_url", _MOVIEVAULT_CONTRIBUTION_URL_ENV
)
MOVIEVAULT_SHARING_MODE = LiveKey(
    "movievault_sharing_mode", _MOVIEVAULT_SHARING_MODE_ENV
)
MOVIEVAULT_DISCVAULT_HANDSHAKE_SECRET = LiveKey(
    "movievault_discvault_handshake_secret",
    _MOVIEVAULT_DISCVAULT_HANDSHAKE_SECRET_ENV,
)

TMDB_LANGUAGES = [
    ("nl", "nl-NL"),
    ("fr", "fr-FR"),
    ("de", "de-DE"),
    ("es", "es-ES"),
    ("pt", "pt-PT"),
    ("it", "it-IT"),
]
RATING_COUNTRIES = {"US", "GB", "CA", "NL", "FR", "DE", "ES", "PT", "IT"}

MCP_API_KEY = os.environ.get("MCP_API_KEY", "")
JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32))
RP_ID = os.environ.get("RP_ID", "localhost")
RP_NAME = os.environ.get("RP_NAME", "DiscVault")

_rp_origins_raw = os.environ.get("RP_ORIGINS") or os.environ.get(
    "RP_ORIGIN", "http://localhost:6080"
)
RP_ORIGINS = [o.strip().rstrip("/") for o in _rp_origins_raw.split(",") if o.strip()]
RP_ORIGIN = RP_ORIGINS[0]

OMDB_ENABLED_DEFAULT = os.environ.get("OMDB_ENABLED", "true").strip().lower() == "true"
TMDB_ENABLED_DEFAULT = os.environ.get("TMDB_ENABLED", "true").strip().lower() == "true"
MOVIEVAULT_ENABLED_DEFAULT = os.environ.get("MOVIEVAULT_ENABLED", "true").strip().lower() == "true"
BLURAY_SCRAPE_ENABLED_DEFAULT = (
    os.environ.get("BLURAY_SCRAPE_ENABLED", "false").strip().lower() == "true"
)
BLURAYDISCDE_SCRAPE_ENABLED_DEFAULT = (
    os.environ.get("BLURAYDISCDE_SCRAPE_ENABLED", "false").strip().lower() == "true"
)
MOVIEVAULT_CONTRIBUTION_ENABLED_DEFAULT = (
    os.environ.get("MOVIEVAULT_CONTRIBUTION_ENABLED", "true").strip().lower() == "true"
)
METADATA_SOURCE_ORDER_DEFAULT = os.environ.get(
    "METADATA_SOURCE_ORDER",
    "movievault,tmdb,omdb,bluray_com",
)
APP_TZ = os.environ.get("TZ", "Europe/Amsterdam").strip() or "Europe/Amsterdam"


def local_now() -> datetime:
    try:
        return datetime.now(ZoneInfo(APP_TZ))
    except Exception:
        return datetime.now().astimezone()


def local_now_iso() -> str:
    return local_now().isoformat(timespec="seconds")
