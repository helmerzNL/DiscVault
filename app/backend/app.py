import os
import io
import re
import csv
import uuid
import json
import shutil
import sqlite3
import secrets
import hashlib
import hmac
import base64
import time
import struct
import threading
import requests
import jwt
import cbor2
import xml.etree.ElementTree as ET
from functools import wraps
from urllib.parse import quote_plus, quote
from flask import Flask, request, jsonify, send_from_directory, send_file, Response, make_response, g, stream_with_context
from flask_cors import CORS
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from PIL import Image, ImageOps, UnidentifiedImageError
from bs4 import BeautifulSoup
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ec import ECDSA, EllipticCurvePublicKey
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import load_der_public_key
from cryptography.x509 import load_der_x509_certificate
from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, EllipticCurvePublicNumbers

app = Flask(__name__)
CORS(app, supports_credentials=True)

DB_PATH    = os.environ.get("DB_PATH",    "/data/discvault.db")
POSTER_DIR = os.environ.get("POSTER_DIR", "/data/posters")
PROFILE_DIR = os.environ.get("PROFILE_DIR", "/data/profiles")
BACKUP_DIR = os.environ.get("BACKUP_DIR", "/data/backups")
AVATAR_DIR = os.environ.get("AVATAR_DIR", "/data/avatars")
OMDB_API_KEY  = os.environ.get("OMDB_API_KEY",  "")
TMDB_API_KEY  = os.environ.get("TMDB_API_KEY",  "")
# DiscVault supported languages — used for multilingual TMDb title/plot lookups
TMDB_LANGUAGES = [("nl", "nl-NL"), ("fr", "fr-FR"), ("de", "de-DE"), ("es", "es-ES"), ("pt", "pt-PT"), ("it", "it-IT")]
RATING_COUNTRIES = {"US", "GB", "CA", "NL", "FR", "DE", "ES", "PT", "IT"}
MCP_API_KEY   = os.environ.get("MCP_API_KEY", "")
JWT_SECRET    = os.environ.get("JWT_SECRET", secrets.token_hex(32))
RP_ID         = os.environ.get("RP_ID", "localhost")
RP_NAME       = os.environ.get("RP_NAME", "DiscVault")
# RP_ORIGINS accepts a comma-separated list of allowed origins, e.g.:
#   RP_ORIGINS=https://discvault.eu,https://discvault.nl
# Legacy single-value RP_ORIGIN is also supported.
_rp_origins_raw = os.environ.get("RP_ORIGINS") or os.environ.get("RP_ORIGIN", "http://localhost:6080")
RP_ORIGINS    = [o.strip().rstrip("/") for o in _rp_origins_raw.split(",") if o.strip()]
RP_ORIGIN     = RP_ORIGINS[0]  # primary origin (used in registration)
OMDB_ENABLED_DEFAULT = os.environ.get("OMDB_ENABLED", "true").strip().lower() == "true"
TMDB_ENABLED_DEFAULT = os.environ.get("TMDB_ENABLED", "true").strip().lower() == "true"
BLURAY_SCRAPE_ENABLED_DEFAULT = os.environ.get("BLURAY_SCRAPE_ENABLED", "false").strip().lower() == "true"
BLURAYDISCDE_SCRAPE_ENABLED_DEFAULT = os.environ.get("BLURAYDISCDE_SCRAPE_ENABLED", "false").strip().lower() == "true"
APP_TZ = os.environ.get("TZ", "Europe/Amsterdam").strip() or "Europe/Amsterdam"


def local_now() -> datetime:
    """Return timezone-aware local datetime using TZ from environment.

    Falls back to system local time when TZ is invalid.
    """
    try:
        return datetime.now(ZoneInfo(APP_TZ))
    except Exception:
        return datetime.now().astimezone()


def local_now_iso() -> str:
    """ISO-8601 local timestamp with offset for persisted logs."""
    return local_now().isoformat(timespec="seconds")

def _set_import_cancel(import_id: str, value: bool):
    if not import_id:
        return
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO import_controls (import_id, cancel_requested, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(import_id) DO UPDATE SET
            cancel_requested=excluded.cancel_requested,
            updated_at=excluded.updated_at
        """,
        (import_id, 1 if value else 0, time.time())
    )
    conn.commit()
    conn.close()


def _is_import_cancelled(import_id: str) -> bool:
    if not import_id:
        return False
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT cancel_requested FROM import_controls WHERE import_id = ?",
        (import_id,)
    ).fetchone()
    conn.close()
    return bool(row and int(row[0]) == 1)


def _clear_import_cancel(import_id: str):
    if not import_id:
        return
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM import_controls WHERE import_id = ?", (import_id,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Token encryption (Fernet / AES-128-CBC via cryptography)
# ---------------------------------------------------------------------------

def _get_fernet() -> Fernet:
    """Derive a Fernet key from JWT_SECRET so tokens are encrypted at rest."""
    key_bytes = hashlib.sha256(JWT_SECRET.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(fernet_key)


def _encrypt_token(token: str) -> str:
    if not token:
        return ""
    try:
        return _get_fernet().encrypt(token.encode()).decode()
    except Exception:
        return ""


def _decrypt_token(enc: str) -> str:
    if not enc:
        return ""
    try:
        return _get_fernet().decrypt(enc.encode()).decode()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Digital library sync job state (in-memory, per process)
# ---------------------------------------------------------------------------

_sync_jobs: dict = {}  # source_id -> {"status": "running"|"done"|"error", "progress": int, "total": int, "error": str}


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

SCHEMA_COLUMNS = [
    # Core identity
    ("barcode",              "TEXT UNIQUE NOT NULL"),
    ("title",                "TEXT NOT NULL"),
    ("sort_title",           "TEXT"),
    ("original_title",       "TEXT"),
    # Release info
    ("year",                 "TEXT"),
    ("release_date",         "TEXT"),
    ("edition",              "TEXT"),
    ("edition_release_year", "TEXT"),
    ("edition_release_date", "TEXT"),
    ("edition_type",         "TEXT DEFAULT 'standard'"),
    ("custom_edition_label", "TEXT"),
    ("edition_group_id",     "INTEGER"),
    ("super_group_id",       "INTEGER"),
    ("collection_id",        "INTEGER"),
    ("country",              "TEXT"),
    ("language",             "TEXT"),
    # People
    ("director",             "TEXT"),
    ("actor",                "TEXT"),
    ("producer",             "TEXT"),
    ("studios",              "TEXT"),
    # Classification
    ("genre",                "TEXT"),
    ("audience_rating",      "TEXT"),
    ("content_ratings",      "TEXT"),
    # Technical
    ("format",               "TEXT DEFAULT '4K UHD'"),
    ("runtime",              "TEXT"),
    ("hdr",                  "TEXT"),
    ("packaging",            "TEXT"),
    ("screen_ratios",        "TEXT"),
    ("audio_tracks",         "TEXT"),
    ("subtitles",            "TEXT"),
    ("regions",              "TEXT"),
    # Content
    ("plot",                 "TEXT"),
    ("title_nl",             "TEXT"),
    ("title_fr",             "TEXT"),
    ("title_de",             "TEXT"),
    ("title_es",             "TEXT"),
    ("title_pt",             "TEXT"),
    ("title_it",             "TEXT"),
    ("plot_nl",              "TEXT"),
    ("plot_fr",              "TEXT"),
    ("plot_de",              "TEXT"),
    ("plot_es",              "TEXT"),
    ("plot_pt",              "TEXT"),
    ("plot_it",              "TEXT"),
    ("extras",               "TEXT"),
    ("box_set",              "TEXT"),
    # External IDs & links
    ("imdb_id",              "TEXT"),
    ("imdb_url",             "TEXT"),
    ("tmdb_id",              "TEXT"),
    # Media
    ("poster",               "TEXT"),
    ("poster_file",          "TEXT"),
    ("backdrop",             "TEXT"),
    ("backdrops",            "TEXT"),
    ("trailer_url",          "TEXT"),
    ("videos",               "TEXT"),
    # Distribution
    ("distributor",          "TEXT"),
    # Purchase
    ("purchase_date",        "TEXT"),
    ("purchase_price",       "TEXT"),
    # Personal
    ("rating",               "TEXT"),
    ("location",             "TEXT"),
    ("notes",                "TEXT"),
    # Timestamps
    ("added_at",             "TEXT NOT NULL"),
]

# Fields allowed in INSERT/UPDATE (everything except id and added_at handling)
ALL_FIELDS = [col for col, _ in SCHEMA_COLUMNS if col not in ("barcode", "added_at")]


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(POSTER_DIR, exist_ok=True)
    os.makedirs(PROFILE_DIR, exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    os.makedirs(AVATAR_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    # Build CREATE TABLE with all columns
    col_defs = ",\n            ".join(
        f"{col} {defn}" for col, defn in [("id", "INTEGER PRIMARY KEY AUTOINCREMENT")] + SCHEMA_COLUMNS
    )
    conn.execute(f"CREATE TABLE IF NOT EXISTS movies (\n            {col_defs}\n        )")

    # Logs table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp  TEXT NOT NULL,
            level      TEXT NOT NULL DEFAULT 'info',
            category   TEXT NOT NULL DEFAULT 'general',
            message    TEXT NOT NULL,
            detail     TEXT,
            user_id    TEXT
        )
    """)
    # Migration: add user_id column to existing logs tables that predate this column
    try:
        conn.execute("ALTER TABLE logs ADD COLUMN user_id TEXT")
    except Exception:
        pass  # Column already exists

    # Import control flags (cross-worker safe cancel state)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS import_controls (
            import_id         TEXT PRIMARY KEY,
            cancel_requested  INTEGER NOT NULL DEFAULT 0,
            updated_at        REAL NOT NULL
        )
    """)

    # Push notification subscriptions
    conn.execute("""
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            endpoint   TEXT NOT NULL UNIQUE,
            p256dh     TEXT NOT NULL,
            auth       TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notification_prefs (
            user_id  TEXT NOT NULL,
            pref_key TEXT NOT NULL,
            enabled  INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (user_id, pref_key),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id TEXT NOT NULL,
            key     TEXT NOT NULL,
            value   TEXT,
            PRIMARY KEY (user_id, key),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            key_hash   TEXT NOT NULL UNIQUE,
            label      TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Auth: users
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            TEXT PRIMARY KEY,
            username      TEXT UNIQUE NOT NULL,
            display_name  TEXT,
            role          TEXT NOT NULL DEFAULT 'user',
            recovery_hash TEXT,
            first_name    TEXT,
            last_name     TEXT,
            avatar        TEXT,
            created_at    TEXT NOT NULL
        )
    """)

    # Auth: passkey credentials
    conn.execute("""
        CREATE TABLE IF NOT EXISTS credentials (
            id              TEXT PRIMARY KEY,
            user_id         TEXT NOT NULL,
            public_key      BLOB NOT NULL,
            sign_count      INTEGER NOT NULL DEFAULT 0,
            credential_name TEXT,
            created_at      TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Groups: shared collections
    conn.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT UNIQUE NOT NULL,
            created_by  TEXT,
            created_at  TEXT NOT NULL,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        )
    """)

    # User-Group membership
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_groups (
            user_id  TEXT NOT NULL,
            group_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, group_id),
            FOREIGN KEY (user_id)  REFERENCES users(id)  ON DELETE CASCADE,
            FOREIGN KEY (group_id) REFERENCES groups(id)  ON DELETE CASCADE
        )
    """)

    # Movie-Group membership (many-to-many)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS movie_groups (
            movie_id INTEGER NOT NULL,
            group_id INTEGER NOT NULL,
            PRIMARY KEY (movie_id, group_id),
            FOREIGN KEY (movie_id) REFERENCES movies(id)  ON DELETE CASCADE,
            FOREIGN KEY (group_id) REFERENCES groups(id)  ON DELETE CASCADE
        )
    """)

    # Group invitations (user-driven collaboration)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS group_invites (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id    INTEGER NOT NULL,
            inviter_id  TEXT NOT NULL,
            invitee_id  TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            created_at  TEXT NOT NULL,
            FOREIGN KEY (group_id)   REFERENCES groups(id) ON DELETE CASCADE,
            FOREIGN KEY (inviter_id) REFERENCES users(id)  ON DELETE CASCADE,
            FOREIGN KEY (invitee_id) REFERENCES users(id)  ON DELETE CASCADE,
            UNIQUE(group_id, invitee_id)
        )
    """)

    # Watchlist & Watch History
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL DEFAULT '__global__',
            movie_id   INTEGER NOT NULL,
            added_at   TEXT NOT NULL,
            FOREIGN KEY (movie_id) REFERENCES movies(id) ON DELETE CASCADE,
            UNIQUE(user_id, movie_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watch_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL DEFAULT '__global__',
            movie_id   INTEGER NOT NULL,
            watched_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (movie_id) REFERENCES movies(id) ON DELETE CASCADE
        )
    """)

    # Migration: drop UNIQUE(user_id, movie_id) from watch_history if it still exists
    try:
        # Clean up _wh_old left by a previously interrupted migration
        wh_old = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='_wh_old'").fetchone()
        if wh_old:
            wh = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='watch_history'").fetchone()
            if not wh:
                conn.execute("ALTER TABLE _wh_old RENAME TO watch_history")
            else:
                conn.execute("DROP TABLE _wh_old")
            conn.commit()

        # Use index [0] because init_db conn has no row_factory set (plain tuples)
        pragma = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='watch_history'").fetchone()
        sql_norm = (pragma[0] or '').replace(' ', '').upper() if pragma else ''
        has_inline_unique = 'UNIQUE(USER_ID,MOVIE_ID)' in sql_norm or 'UNIQUE(MOVIE_ID,USER_ID)' in sql_norm

        # Also check via PRAGMA index_list in case SQLite stored it differently
        if not has_inline_unique:
            idx_rows = conn.execute("PRAGMA index_list(watch_history)").fetchall()
            for idx in idx_rows:
                if idx[2] == 1 and idx[3] == 'u':  # unique=1, origin='u' (table constraint)
                    idx_info = conn.execute(f"PRAGMA index_info({idx[1]})").fetchall()
                    col_names = {row[2].lower() for row in idx_info}
                    if 'user_id' in col_names and 'movie_id' in col_names:
                        has_inline_unique = True
                        break

        if has_inline_unique:
            conn.execute("ALTER TABLE watch_history RENAME TO _wh_old")
            conn.execute("""
                CREATE TABLE watch_history (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    TEXT NOT NULL DEFAULT '__global__',
                    movie_id   INTEGER NOT NULL,
                    watched_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (movie_id) REFERENCES movies(id) ON DELETE CASCADE
                )
            """)
            conn.execute("INSERT INTO watch_history (id,user_id,movie_id,watched_at,created_at) SELECT id,user_id,movie_id,watched_at,created_at FROM _wh_old")
            conn.execute("DROP TABLE _wh_old")
            conn.commit()
    except Exception:
        pass

    # Auth: settings
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Auth: invite codes for admin-controlled registration
    conn.execute("""
        CREATE TABLE IF NOT EXISTS invite_codes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code_hash   TEXT NOT NULL UNIQUE,
            username    TEXT NOT NULL,
            created_by  TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            expires_at  TEXT NOT NULL,
            used_at     TEXT,
            used_by     TEXT
        )
    """)

    # People: actors, directors, crew
    conn.execute("""
        CREATE TABLE IF NOT EXISTS people (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tmdb_id     INTEGER UNIQUE,
            name        TEXT NOT NULL,
            photo_file  TEXT,
            biography   TEXT,
            biography_nl TEXT,
            biography_fr TEXT,
            biography_de TEXT,
            biography_es TEXT,
            biography_pt TEXT,
            biography_it TEXT,
            birthday    TEXT,
            deathday    TEXT,
            place_of_birth TEXT,
            known_for   TEXT,
            updated_at  TEXT
        )
    """)

    # Movie-Person relationship
    conn.execute("""
        CREATE TABLE IF NOT EXISTS movie_people (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            movie_id  INTEGER NOT NULL,
            person_id INTEGER NOT NULL,
            role      TEXT NOT NULL,
            character TEXT,
            job       TEXT,
            sort_order INTEGER DEFAULT 0,
            FOREIGN KEY (movie_id) REFERENCES movies(id) ON DELETE CASCADE,
            FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE,
            UNIQUE(movie_id, person_id, role, job)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_movie_people_movie ON movie_people(movie_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_movie_people_person ON movie_people(person_id)")
    # Default: auth disabled until user sets up a passkey
    existing_auth = conn.execute("SELECT value FROM settings WHERE key='auth_enabled'").fetchone()
    if not existing_auth:
        conn.execute("INSERT INTO settings (key, value) VALUES ('auth_enabled', 'false')")

    for skey, sdefault in [
        ("omdb_enabled", OMDB_ENABLED_DEFAULT),
        ("tmdb_enabled", TMDB_ENABLED_DEFAULT),
        ("bluray_scrape_enabled", BLURAY_SCRAPE_ENABLED_DEFAULT),
        ("bluraydiscde_scrape_enabled", BLURAYDISCDE_SCRAPE_ENABLED_DEFAULT),
        ("mcp_enabled", True),
        ("debug_enabled", False),
        ("show_local_title", True),
    ]:
        existing = conn.execute("SELECT value FROM settings WHERE key=?", (skey,)).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                (skey, "true" if sdefault else "false")
            )

    # Migrate: add any missing columns to existing DB
    existing = {row[1] for row in conn.execute("PRAGMA table_info(movies)")}
    for col, defn in SCHEMA_COLUMNS:
        if col not in existing:
            bare = defn.split(" NOT NULL")[0].split(" DEFAULT")[0].strip()
            conn.execute(f"ALTER TABLE movies ADD COLUMN {col} {bare}")

    # Migrate movies: add owner_id if missing
    if "owner_id" not in existing:
        conn.execute("ALTER TABLE movies ADD COLUMN owner_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_movies_owner ON movies(owner_id)")
    if "super_group_id" not in existing:
        conn.execute("ALTER TABLE movies ADD COLUMN super_group_id INTEGER")

    # Migrate: move old group_id column data to movie_groups table
    if "group_id" in existing:
        # Migrate any existing group_id references to movie_groups
        conn.execute("""
            INSERT OR IGNORE INTO movie_groups (movie_id, group_id)
            SELECT id, group_id FROM movies WHERE group_id IS NOT NULL
        """)

    # Migrate users: add role and recovery_hash if missing
    user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "role" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
    if "recovery_hash" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN recovery_hash TEXT")
    if "first_name" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
    if "last_name" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN last_name TEXT")
    if "avatar" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN avatar TEXT")

    # Migrate user_groups: add role column for group ownership
    ug_cols = {row[1] for row in conn.execute("PRAGMA table_info(user_groups)")}
    if "role" not in ug_cols:
        conn.execute("ALTER TABLE user_groups ADD COLUMN role TEXT NOT NULL DEFAULT 'member'")
    # Set existing group creators as owner in user_groups
    conn.execute("""
        UPDATE user_groups SET role='owner'
        WHERE rowid IN (
            SELECT ug.rowid FROM user_groups ug
            JOIN groups g ON g.id=ug.group_id AND g.created_by=ug.user_id
        )
    """)
    # Migrate groups: add hide_digital flag
    g_cols = {row[1] for row in conn.execute("PRAGMA table_info(groups)")}
    if "hide_digital" not in g_cols:
        conn.execute("ALTER TABLE groups ADD COLUMN hide_digital INTEGER NOT NULL DEFAULT 0")

    # Migrate people: add multilingual biography columns if missing
    people_cols = {row[1] for row in conn.execute("PRAGMA table_info(people)")}
    for lang_code, _ in TMDB_LANGUAGES:
        col = f"biography_{lang_code}"
        if col not in people_cols:
            conn.execute(f"ALTER TABLE people ADD COLUMN {col} TEXT")

    # Auto-assign existing movies (no owner) to first admin user
    first_user = conn.execute("SELECT id FROM users ORDER BY created_at ASC LIMIT 1").fetchone()
    if first_user:
        conn.execute("UPDATE users SET role='admin' WHERE id=? AND role='user'", (first_user[0],))
        orphan_count = conn.execute("SELECT COUNT(*) FROM movies WHERE owner_id IS NULL").fetchone()[0]
        if orphan_count > 0:
            conn.execute("UPDATE movies SET owner_id=? WHERE owner_id IS NULL", (first_user[0],))

    # Edition groups: group multiple physical copies of the same film
    conn.execute("""
        CREATE TABLE IF NOT EXISTS edition_groups (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            tmdb_id          TEXT,
            imdb_id          TEXT,
            title            TEXT NOT NULL,
            year             TEXT,
            primary_movie_id INTEGER,
            created_at       TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_edition_groups_tmdb ON edition_groups(tmdb_id)")
    # Migrate edition_groups: add badge_label and parent_group_id if missing
    eg_cols = {row[1] for row in conn.execute("PRAGMA table_info(edition_groups)")}
    if "badge_label" not in eg_cols:
        conn.execute("ALTER TABLE edition_groups ADD COLUMN badge_label TEXT")
    if "parent_group_id" not in eg_cols:
        conn.execute("ALTER TABLE edition_groups ADD COLUMN parent_group_id INTEGER")
    if "collection_id" not in eg_cols:
        conn.execute("ALTER TABLE edition_groups ADD COLUMN collection_id INTEGER")
    if "backdrop" not in eg_cols:
        conn.execute("ALTER TABLE edition_groups ADD COLUMN backdrop TEXT")
    if "description" not in eg_cols:
        conn.execute("ALTER TABLE edition_groups ADD COLUMN description TEXT")
    if "poster_file" not in eg_cols:
        conn.execute("ALTER TABLE edition_groups ADD COLUMN poster_file TEXT")
    if "group_type" not in eg_cols:
        conn.execute("ALTER TABLE edition_groups ADD COLUMN group_type TEXT DEFAULT 'vault'")

    # Collections: top-level grouping of Vaults, Box Sets and loose movies
    conn.execute("""
        CREATE TABLE IF NOT EXISTS collections (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            badge_label TEXT,
            created_at  TEXT NOT NULL
        )
    """)
    col_cols = {row[1] for row in conn.execute("PRAGMA table_info(collections)")}
    if "backdrop" not in col_cols:
        conn.execute("ALTER TABLE collections ADD COLUMN backdrop TEXT")
    if "description" not in col_cols:
        conn.execute("ALTER TABLE collections ADD COLUMN description TEXT")
    if "poster_file" not in col_cols:
        conn.execute("ALTER TABLE collections ADD COLUMN poster_file TEXT")

    # Digital library sources (Plex / Jellyfin)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS digital_library_sources (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            type        TEXT NOT NULL,
            base_url    TEXT NOT NULL,
            token_enc   TEXT,
            library_ids TEXT,
            last_synced TEXT,
            item_count  INTEGER DEFAULT 0,
            enabled     INTEGER DEFAULT 1,
            owner_id    TEXT
        )
    """)

    # Cached items from digital library syncs
    conn.execute("""
        CREATE TABLE IF NOT EXISTS digital_library_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id   INTEGER NOT NULL REFERENCES digital_library_sources(id) ON DELETE CASCADE,
            external_id TEXT NOT NULL,
            title       TEXT NOT NULL,
            year        TEXT,
            tmdb_id     TEXT,
            imdb_id     TEXT,
            media_type  TEXT DEFAULT 'movie',
            synced_at   TEXT NOT NULL,
            UNIQUE(source_id, external_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dli_tmdb ON digital_library_items(tmdb_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dli_imdb ON digital_library_items(imdb_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dli_source ON digital_library_items(source_id)")

    # Migrate: re-generate VAPID keys if stored in old PEM format (any format starting with -----BEGIN)
    # New format: raw base64url 32-byte EC scalar, as expected by pywebpush/py_vapid from_string()
    old_priv = conn.execute("SELECT value FROM settings WHERE key='vapid_private_key'").fetchone()
    if old_priv and ("BEGIN" in (old_priv[0] or "") or len(old_priv[0] or "") > 100):
        conn.execute("DELETE FROM settings WHERE key IN ('vapid_private_key', 'vapid_public_key')")
        conn.execute("DELETE FROM push_subscriptions")  # old subs used old public key, must re-subscribe

    # RBAC: custom roles, permissions and user-role assignments
    conn.execute("""
        CREATE TABLE IF NOT EXISTS custom_roles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT UNIQUE NOT NULL,
            description TEXT,
            created_by  TEXT,
            created_at  TEXT NOT NULL,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS role_permissions (
            role_id    INTEGER NOT NULL,
            permission TEXT NOT NULL,
            PRIMARY KEY (role_id, permission),
            FOREIGN KEY (role_id) REFERENCES custom_roles(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_roles (
            user_id TEXT NOT NULL,
            role_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, role_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (role_id) REFERENCES custom_roles(id) ON DELETE CASCADE
        )
    """)

    conn.commit()
    conn.close()

init_db()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------

def add_log(category: str, message: str, detail: str = "", level: str = "info",
            user_id: str = None):
    """Write a structured log entry to the database.
    Levels: info, warn, error, success
    Categories: import, refresh, lookup, add, delete, scan, api, general, mcp
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO logs (timestamp, level, category, message, detail, user_id) VALUES (?,?,?,?,?,?)",
            (local_now_iso(), level, category, message, detail or "", user_id)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Never let logging break the app


# ---------------------------------------------------------------------------
# Poster helpers
# ---------------------------------------------------------------------------

def download_poster(url: str):
    if not url or url == "N/A":
        return None
    try:
        resp = requests.get(url, timeout=10, stream=True)
        if resp.status_code != 200:
            return None
        ext = ".jpg"
        ct = resp.headers.get("content-type", "")
        if "png" in ct:
            ext = ".png"
        elif "webp" in ct:
            ext = ".webp"
        filename = uuid.uuid4().hex + ext
        with open(os.path.join(POSTER_DIR, filename), "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        return filename
    except Exception:
        return None


def save_uploaded_poster(file_storage):
    if not file_storage or not file_storage.filename:
        return None, "Geen bestand geupload"
    try:
        file_storage.stream.seek(0)
        img = Image.open(file_storage.stream)
        # Normalize orientation from EXIF and keep deterministic RGB output.
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        elif img.mode == "L":
            img = img.convert("RGB")

        # Resize to a sane poster size while preserving aspect ratio.
        img.thumbnail((900, 1400), Image.Resampling.LANCZOS)

        filename = uuid.uuid4().hex + ".jpg"
        out_path = os.path.join(POSTER_DIR, filename)
        img.save(out_path, format="JPEG", quality=88, optimize=True)
        return filename, None
    except UnidentifiedImageError:
        return None, "Bestand is geen geldige afbeelding"
    except Exception as e:
        return None, f"Upload mislukt: {str(e)}"


def save_uploaded_backdrop(file_storage):
    """Save an uploaded backdrop image. Stored in the poster directory and
    served via /api/posters/<filename>. Resized to a max 1920x1080 box while
    preserving aspect ratio."""
    if not file_storage or not file_storage.filename:
        return None, "Geen bestand geupload"
    try:
        file_storage.stream.seek(0)
        img = Image.open(file_storage.stream)
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        elif img.mode == "L":
            img = img.convert("RGB")
        img.thumbnail((1920, 1080), Image.Resampling.LANCZOS)
        filename = uuid.uuid4().hex + ".jpg"
        out_path = os.path.join(POSTER_DIR, filename)
        img.save(out_path, format="JPEG", quality=85, optimize=True)
        return filename, None
    except UnidentifiedImageError:
        return None, "Bestand is geen geldige afbeelding"
    except Exception as e:
        return None, f"Upload mislukt: {str(e)}"


@app.route("/api/posters/<path:filename>")
def serve_poster(filename):
    raw = (filename or "").strip()
    safe_name = os.path.basename(raw)
    if not safe_name:
        return jsonify({"error": "Poster not found"}), 404

    # Primary: serve by basename from configured poster directory.
    if os.path.isfile(os.path.join(POSTER_DIR, safe_name)):
        return send_from_directory(POSTER_DIR, safe_name)

    # Legacy compatibility: DB might contain full path.
    if os.path.isfile(raw):
        real_poster_dir = os.path.realpath(POSTER_DIR)
        real_raw = os.path.realpath(raw)
        if real_raw == real_poster_dir or real_raw.startswith(real_poster_dir + os.sep):
            return send_file(real_raw)

    return jsonify({"error": "Poster not found"}), 404


def download_profile_photo(url: str):
    """Download a TMDb profile photo and save to PROFILE_DIR."""
    if not url or url == "N/A":
        return None
    try:
        resp = requests.get(url, timeout=10, stream=True)
        if resp.status_code != 200:
            return None
        filename = uuid.uuid4().hex + ".jpg"
        with open(os.path.join(PROFILE_DIR, filename), "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        return filename
    except Exception:
        return None


def _has_local_profile_photo(photo_file: str | None) -> bool:
    """Return True only when a profile filename points to an existing local file."""
    if not photo_file:
        return False
    safe_name = os.path.basename(str(photo_file).strip())
    if not safe_name:
        return False
    return os.path.isfile(os.path.join(PROFILE_DIR, safe_name))


def _profile_signature(filename: str, exp: int) -> str:
    payload = f"{filename}:{int(exp)}".encode("utf-8")
    return hmac.new(JWT_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _make_signed_profile_url(photo_file: str | None, ttl_seconds: int = 3600) -> str | None:
    if not photo_file:
        return None
    safe_name = os.path.basename(str(photo_file).strip().replace("\\", "/"))
    if not safe_name:
        return None
    exp = int(time.time()) + max(60, int(ttl_seconds))
    sig = _profile_signature(safe_name, exp)
    return f"/api/profiles/{quote(safe_name)}?exp={exp}&sig={sig}"


def _is_valid_profile_request_signature(filename: str) -> bool:
    try:
        exp_raw = request.args.get("exp", "")
        sig = request.args.get("sig", "")
        exp = int(exp_raw)
    except Exception:
        return False

    if not sig or exp < int(time.time()):
        return False

    expected = _profile_signature(filename, exp)
    return hmac.compare_digest(expected, sig)


@app.route("/api/profiles/<path:filename>")
def serve_profile(filename):
    raw = (filename or "").strip()
    # Accept plain filename, full path, or Windows-style path separators.
    safe_name = os.path.basename(raw.replace("\\", "/"))
    if not safe_name:
        return jsonify({"error": "Profile not found"}), 404

    # If app auth is enabled, only allow signed temporary image URLs.
    if _is_auth_enabled() and not _is_valid_profile_request_signature(safe_name):
        return jsonify({"error": "Unauthorized image URL"}), 401

    if os.path.isfile(os.path.join(PROFILE_DIR, safe_name)):
        resp = send_from_directory(PROFILE_DIR, safe_name)
        # Avoid sticky 404/image cache in browsers after a later refresh downloads the file.
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp
    return jsonify({"error": "Profile not found"}), 404


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_year(raw: str) -> str:
    if not raw:
        return ""
    m = re.search(r'\b(19|20)\d{2}\b', str(raw))
    return m.group(0) if m else str(raw).strip()


def _extract_imdb_id(url_or_id: str) -> str:
    """Extract tt1234567 from a full IMDb URL or return as-is if already an ID."""
    if not url_or_id:
        return ""
    m = re.search(r'(tt\d+)', url_or_id)
    return m.group(1) if m else url_or_id.strip()


def _title_candidates_from_upc(raw_title: str) -> list[str]:
    title = (raw_title or "").strip()
    if not title:
        return []

    out = []

    def _add(v: str):
        s = (v or "").strip()
        if s and s not in out:
            out.append(s)

    _add(title)

    # Remove common media suffixes/noise often present in UPC titles.
    cleaned = re.sub(r'\[[^\]]*\]', ' ', title)
    cleaned = re.sub(r'\([^\)]*(blu[- ]?ray|uhd|ultra\s*hd|4k|dvd)[^\)]*\)', ' ', cleaned, flags=re.I)
    cleaned = re.sub(r'\s[-|]\s*(blu[- ]?ray|uhd|ultra\s*hd|4k|dvd).*$', ' ', cleaned, flags=re.I)
    cleaned = re.sub(r'\b(blu[- ]?ray|uhd|ultra\s*hd|4k|dvd)\b', ' ', cleaned, flags=re.I)
    cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip(' -_/')
    _add(cleaned)

    # If title has colon subtitle, try main title too.
    if ':' in cleaned:
        _add(cleaned.split(':', 1)[0])

    # Handle article variants: "The X" <-> "X, The"
    article_match = re.match(r'^(The|An|A)\s+(.+)$', cleaned, flags=re.I)
    if article_match:
        art = article_match.group(1)
        rest = article_match.group(2)
        _add(f"{rest}, {art.title()}")

    comma_article_match = re.match(r'^(.+),\s*(The|An|A)$', cleaned, flags=re.I)
    if comma_article_match:
        rest = comma_article_match.group(1)
        art = comma_article_match.group(2)
        _add(f"{art.title()} {rest}")

    # Also try each dash-separated segment as a standalone candidate.
    # Sort longer segments first: movie titles tend to be longer than distributor names.
    # e.g. "Studio Canal - Honey" → tries "Honey" before "Studio Canal"
    if ' - ' in cleaned:
        segs = sorted(
            [s.strip() for s in cleaned.split(' - ') if len(s.strip()) > 3],
            key=len, reverse=True
        )
        for seg in segs:
            _add(seg)

    return out


def _detect_format_from_upc(raw_title: str) -> str:
    """Detect disc format from a UPC/EAN title string.
    Returns '4K UHD', 'Blu-ray', 'DVD', or '' (unknown)."""
    t = (raw_title or "").lower()
    # 4K UHD takes priority — explicit labels or 4K+Blu-ray combo
    if re.search(r'4k\s*uhd|ultra[\s-]*hd|uhd', t):
        return "4K UHD"
    if re.search(r'\b4k\b', t) and re.search(r'blu[- ]?ray', t):
        return "4K UHD"
    if re.search(r'\b4k\b', t):
        return "4K UHD"
    if re.search(r'blu[- ]?ray', t):
        return "Blu-ray"
    if re.search(r'\bdvd\b', t):
        return "DVD"
    return ""


def _titles_overlap(candidate: str, tmdb_title: str, min_ratio: float = 0.30) -> bool:
    """Return True if the TMDb title shares enough significant words with the search candidate.
    Prevents a distributor name like 'Studio Canal' from matching an unrelated TMDb film.
    Short candidates (≤2 meaningful words) require a full word-for-word match to avoid
    false positives like "Studio Canal" → "The Canal"."""
    stop = {'the', 'a', 'an', 'of', 'in', 'on', 'at', 'for', 'and', 'or', 'but', 'with', 'by', 'to', 'de', 'le', 'la', 'les'}
    def words(s):
        return {w.lower() for w in re.findall(r'\w+', s) if w.lower() not in stop and len(w) > 2}
    wa, wb = words(candidate), words(tmdb_title)
    if not wa or not wb:
        return True  # can't compare — don't filter
    # For short candidates (≤2 meaningful words), ALL candidate words must appear in
    # the TMDb title to avoid distributor names matching unrelated films.
    effective_min = 1.0 if len(wa) <= 2 else min_ratio
    ratio = len(wa & wb) / max(len(wa), len(wb))
    return ratio >= effective_min


# ---------------------------------------------------------------------------
# External API lookups
# ---------------------------------------------------------------------------

def _fetch_tmdb_ratings(tmdb_id):
    """Fetch content certifications for supported countries via TMDb release_dates."""
    if not TMDB_API_KEY or not tmdb_id:
        return {}
    try:
        r = requests.get(
            f"https://api.themoviedb.org/3/movie/{tmdb_id}/release_dates"
            f"?api_key={TMDB_API_KEY}",
            timeout=6
        )
        if r.status_code != 200:
            return {}
        ratings = {}
        for entry in r.json().get("results", []):
            country = entry.get("iso_3166_1", "")
            if country not in RATING_COUNTRIES:
                continue
            cert = ""
            for rd in sorted(entry.get("release_dates", []), key=lambda x: 0 if x.get("type") == 3 else 1):
                c = (rd.get("certification") or "").strip()
                if c:
                    cert = c
                    break
            if cert:
                ratings[country] = cert
        return ratings
    except Exception:
        return {}


def lookup_by_barcode_upcitemdb(barcode: str):
    try:
        r = requests.get(
            f"https://api.upcitemdb.com/prod/trial/lookup?upc={barcode}", timeout=5
        )
        if r.status_code == 200:
            items = r.json().get("items", [])
            if items:
                return items[0].get("title", "")
    except Exception:
        pass
    return None


def lookup_movie_omdb(title=None, imdb_id=None):
    if not OMDB_API_KEY or not _is_omdb_enabled():
        return None
    try:
        if imdb_id:
            url = f"http://www.omdbapi.com/?i={imdb_id}&apikey={OMDB_API_KEY}"
        elif title:
            url = f"http://www.omdbapi.com/?t={requests.utils.quote(title)}&apikey={OMDB_API_KEY}"
        else:
            return None
        r = requests.get(url, timeout=6)
        if r.status_code == 200:
            d = r.json()
            if d.get("Response") == "True":
                return {
                    "title":        d.get("Title", ""),
                    "year":         _parse_year(d.get("Year", "")),
                    "release_date": d.get("Released", ""),
                    "director":     d.get("Director", ""),
                    "actor":        d.get("Actors", ""),
                    "genre":        d.get("Genre", ""),
                    "plot":         d.get("Plot", ""),
                    "poster":       d.get("Poster", ""),
                    "runtime":      re.sub(r'[^\d]', '', d.get("Runtime", "")),
                    "rating":       d.get("imdbRating", ""),
                    "imdb_id":      d.get("imdbID", ""),
                    "country":        d.get("Country", ""),
                    "language":       d.get("Language", ""),
                    "tmdb_id":        "",
                    "audience_rating": d.get("Rated", "") if d.get("Rated", "") not in ("", "N/A", "Not Rated") else "",
                    "_content_ratings": {"US": d["Rated"]} if d.get("Rated") and d.get("Rated") not in ("N/A", "Not Rated", "") else {},
                }
    except Exception:
        pass
    return None


def lookup_movie_tmdb(title, year=""):
    if not TMDB_API_KEY or not _is_tmdb_enabled():
        return None
    try:
        # 1. Search
        params = f"query={requests.utils.quote(title)}&api_key={TMDB_API_KEY}"
        if year:
            params += f"&year={year}"
        r = requests.get(
            f"https://api.themoviedb.org/3/search/movie?{params}", timeout=6
        )
        if r.status_code != 200:
            return None
        results = r.json().get("results", [])
        if not results:
            return None
        movie_id = results[0]["id"]

        # 2. Fetch English details + credits + videos + images in one call
        rd = requests.get(
            f"https://api.themoviedb.org/3/movie/{movie_id}"
            f"?api_key={TMDB_API_KEY}&language=en-US"
            f"&append_to_response=credits,videos,images"
            f"&include_image_language=null%2Cen",
            timeout=8
        )
        if rd.status_code != 200:
            return None
        d = rd.json()

        crew      = d.get("credits", {}).get("crew", [])
        cast      = d.get("credits", {}).get("cast", [])
        directors = [c["name"] for c in crew if c["job"] == "Director"]
        producers = [c["name"] for c in crew if c["job"] == "Producer"]
        actors    = [c["name"] for c in cast[:5]]
        genres    = [g["name"] for g in d.get("genres", [])]
        studios   = [p["name"] for p in d.get("production_companies", [])]
        release   = d.get("release_date", "") or ""

        # Poster in original (highest) resolution
        poster_url = ""
        if d.get("poster_path"):
            poster_url = f"https://image.tmdb.org/t/p/original{d['poster_path']}"

        # Backdrops: up to 10 sorted by vote_average descending
        backdrop_list = d.get("images", {}).get("backdrops", [])
        backdrop_list.sort(key=lambda x: x.get("vote_average", 0), reverse=True)
        backdrops = [
            f"https://image.tmdb.org/t/p/original{b['file_path']}"
            for b in backdrop_list[:10] if b.get("file_path")
        ]
        if not backdrops and d.get("backdrop_path"):
            backdrops = [f"https://image.tmdb.org/t/p/original{d['backdrop_path']}"]

        # Trailer: first YouTube trailer from videos
        trailer_url = ""
        extra_videos = []
        for v in d.get("videos", {}).get("results", []):
            if v.get("site") != "YouTube" or not v.get("key"):
                continue
            url = f"https://www.youtube.com/watch?v={v['key']}"
            if v.get("type") == "Trailer" and not trailer_url:
                trailer_url = url
            elif v.get("type") in ("Featurette", "Behind the Scenes", "Clip", "Bloopers", "Teaser"):
                extra_videos.append({"url": url, "label": v.get("name") or v.get("type"), "type": v.get("type"), "source": "tmdb"})

        # Build full cast/crew list for people table
        cast_crew = []
        for i, c in enumerate(cast[:20]):
            cast_crew.append({
                "tmdb_id": c.get("id"),
                "name": c.get("name", ""),
                "role": "actor",
                "character": c.get("character", ""),
                "job": None,
                "sort_order": i,
                "profile_path": c.get("profile_path"),
            })
        for c in crew:
            if c.get("job") in ("Director", "Producer", "Screenplay", "Writer",
                                "Director of Photography", "Original Music Composer", "Editor"):
                cast_crew.append({
                    "tmdb_id": c.get("id"),
                    "name": c.get("name", ""),
                    "role": "crew",
                    "character": None,
                    "job": c.get("job", ""),
                    "sort_order": 0,
                    "profile_path": c.get("profile_path"),
                })

        result = {
            "title":          d.get("title", ""),
            "original_title": d.get("original_title", ""),
            "year":           release[:4],
            "release_date":   release,
            "director":       ", ".join(directors),
            "actor":          ", ".join(actors),
            "producer":       ", ".join(producers[:3]),
            "studios":        ", ".join(studios),
            "genre":          ", ".join(genres),
            "plot":           d.get("overview", ""),
            "poster":         poster_url,
            "backdrop":       backdrops[0] if backdrops else "",
            "backdrops":      json.dumps(backdrops),
            "trailer_url":    trailer_url,
            "videos":         json.dumps(extra_videos) if extra_videos else "",
            "runtime":        str(d.get("runtime", "")),
            "rating":         str(round(d.get("vote_average", 0), 1)),
            "imdb_id":        d.get("imdb_id", "") or "",
            "tmdb_id":        str(movie_id),
            "language":       d.get("original_language", ""),
            "_cast_crew":     cast_crew,
        }

        # 3. Fetch translated title + plot for each DiscVault language
        for lang_code, tmdb_lang in TMDB_LANGUAGES:
            try:
                rt = requests.get(
                    f"https://api.themoviedb.org/3/movie/{movie_id}"
                    f"?api_key={TMDB_API_KEY}&language={tmdb_lang}",
                    timeout=5
                )
                if rt.status_code == 200:
                    td = rt.json()
                    result[f"title_{lang_code}"] = td.get("title", "") or ""
                    result[f"plot_{lang_code}"]  = td.get("overview", "") or ""
            except Exception:
                pass

        # 4. Fetch content ratings per country
        _cr = _fetch_tmdb_ratings(movie_id)
        if _cr:
            result["_content_ratings"] = _cr
            if not result.get("audience_rating") and _cr.get("US"):
                result["audience_rating"] = _cr["US"]

        return result
    except Exception:
        pass
    return None


def _tmdb_search_top5(title, year=""):
    """Search TMDb and return up to 5 candidates for disambiguation."""
    if not TMDB_API_KEY or not _is_tmdb_enabled():
        return []
    try:
        params = f"query={requests.utils.quote(title)}&api_key={TMDB_API_KEY}"
        if year:
            params += f"&year={year}"
        r = requests.get(
            f"https://api.themoviedb.org/3/search/movie?{params}", timeout=6
        )
        if r.status_code != 200:
            return []
        candidates = []
        for res in r.json().get("results", [])[:5]:
            candidates.append({
                "tmdb_id":      str(res.get("id", "")),
                "title":        res.get("title", ""),
                "year":         (res.get("release_date", "") or "")[:4],
                "overview":     (res.get("overview", "") or "")[:150],
                "vote_average": round(res.get("vote_average") or 0, 1),
                "poster":       f"https://image.tmdb.org/t/p/w185{res['poster_path']}"
                                if res.get("poster_path") else "",
            })
        return candidates
    except Exception:
        return []


def lookup_movie_tmdb_by_id(tmdb_id):
    """Fetch full movie data by TMDb ID, bypassing the title-search step."""
    if not TMDB_API_KEY or not _is_tmdb_enabled():
        return None
    try:
        rd = requests.get(
            f"https://api.themoviedb.org/3/movie/{tmdb_id}"
            f"?api_key={TMDB_API_KEY}&language=en-US"
            f"&append_to_response=credits,videos,images"
            f"&include_image_language=null%2Cen",
            timeout=8
        )
        if rd.status_code != 200:
            return None
        d = rd.json()
        crew      = d.get("credits", {}).get("crew", [])
        cast      = d.get("credits", {}).get("cast", [])
        directors = [c["name"] for c in crew if c["job"] == "Director"]
        producers = [c["name"] for c in crew if c["job"] == "Producer"]
        actors    = [c["name"] for c in cast[:5]]
        genres    = [g["name"] for g in d.get("genres", [])]
        studios   = [p["name"] for p in d.get("production_companies", [])]
        release   = d.get("release_date", "") or ""
        poster_url = ""
        if d.get("poster_path"):
            poster_url = f"https://image.tmdb.org/t/p/original{d['poster_path']}"
        backdrop_list = d.get("images", {}).get("backdrops", [])
        backdrop_list.sort(key=lambda x: x.get("vote_average", 0), reverse=True)
        backdrops = [
            f"https://image.tmdb.org/t/p/original{b['file_path']}"
            for b in backdrop_list[:10] if b.get("file_path")
        ]
        if not backdrops and d.get("backdrop_path"):
            backdrops = [f"https://image.tmdb.org/t/p/original{d['backdrop_path']}"]
        trailer_url = ""
        extra_videos = []
        for v in d.get("videos", {}).get("results", []):
            if v.get("site") != "YouTube" or not v.get("key"):
                continue
            url = f"https://www.youtube.com/watch?v={v['key']}"
            if v.get("type") == "Trailer" and not trailer_url:
                trailer_url = url
            elif v.get("type") in ("Featurette", "Behind the Scenes", "Clip", "Bloopers", "Teaser"):
                extra_videos.append({"url": url, "label": v.get("name") or v.get("type"), "type": v.get("type"), "source": "tmdb"})
        cast_crew = []
        for i, c in enumerate(cast[:20]):
            cast_crew.append({
                "tmdb_id": c.get("id"),
                "name": c.get("name", ""),
                "role": "actor",
                "character": c.get("character", ""),
                "job": None,
                "sort_order": i,
                "profile_path": c.get("profile_path"),
            })
        for c in crew:
            if c.get("job") in ("Director", "Producer", "Screenplay", "Writer",
                                "Director of Photography", "Original Music Composer", "Editor"):
                cast_crew.append({
                    "tmdb_id": c.get("id"),
                    "name": c.get("name", ""),
                    "role": "crew",
                    "character": None,
                    "job": c.get("job", ""),
                    "sort_order": 0,
                    "profile_path": c.get("profile_path"),
                })
        result = {
            "title":          d.get("title", ""),
            "original_title": d.get("original_title", ""),
            "year":           release[:4],
            "release_date":   release,
            "director":       ", ".join(directors),
            "actor":          ", ".join(actors),
            "producer":       ", ".join(producers[:3]),
            "studios":        ", ".join(studios),
            "genre":          ", ".join(genres),
            "plot":           d.get("overview", ""),
            "poster":         poster_url,
            "backdrop":       backdrops[0] if backdrops else "",
            "backdrops":      json.dumps(backdrops),
            "trailer_url":    trailer_url,
            "videos":         json.dumps(extra_videos) if extra_videos else "",
            "runtime":        str(d.get("runtime", "")),
            "rating":         str(round(d.get("vote_average", 0), 1)),
            "imdb_id":        d.get("imdb_id", "") or "",
            "tmdb_id":        str(tmdb_id),
            "language":       d.get("original_language", ""),
            "_cast_crew":     cast_crew,
        }
        for lang_code, tmdb_lang in TMDB_LANGUAGES:
            try:
                rt = requests.get(
                    f"https://api.themoviedb.org/3/movie/{tmdb_id}"
                    f"?api_key={TMDB_API_KEY}&language={tmdb_lang}",
                    timeout=5
                )
                if rt.status_code == 200:
                    td = rt.json()
                    result[f"title_{lang_code}"] = td.get("title", "") or ""
                    result[f"plot_{lang_code}"]  = td.get("overview", "") or ""
            except Exception:
                pass

        # Fetch content ratings per country
        _cr = _fetch_tmdb_ratings(tmdb_id)
        if _cr:
            result["_content_ratings"] = _cr
            if not result.get("audience_rating") and _cr.get("US"):
                result["audience_rating"] = _cr["US"]

        return result
    except Exception:
        pass
    return None


def _fetch_tmdb_cast_crew(tmdb_id):
    """Fetch cast/crew directly by TMDb movie ID — no search needed."""
    if not TMDB_API_KEY or not tmdb_id:
        return None
    try:
        r = requests.get(
            f"https://api.themoviedb.org/3/movie/{tmdb_id}/credits"
            f"?api_key={TMDB_API_KEY}",
            timeout=6
        )
        if r.status_code != 200:
            return None
        d = r.json()
        crew = d.get("crew", [])
        cast = d.get("cast", [])
        cast_crew = []
        for i, c in enumerate(cast[:20]):
            cast_crew.append({
                "tmdb_id": c.get("id"),
                "name": c.get("name", ""),
                "role": "actor",
                "character": c.get("character", ""),
                "job": None,
                "sort_order": i,
                "profile_path": c.get("profile_path"),
            })
        for c in crew:
            if c.get("job") in ("Director", "Producer", "Screenplay", "Writer",
                                "Director of Photography", "Original Music Composer", "Editor"):
                cast_crew.append({
                    "tmdb_id": c.get("id"),
                    "name": c.get("name", ""),
                    "role": "crew",
                    "character": None,
                    "job": c.get("job", ""),
                    "sort_order": 0,
                    "profile_path": c.get("profile_path"),
                })
        return cast_crew if cast_crew else None
    except Exception:
        return None


def _sync_movie_cast_crew(conn, movie_id: int, cast_crew: list, download_photos: bool = True):
    """Sync cast/crew data from TMDb into people + movie_people tables.
    Downloads profile photos for the top 10 cast + key crew members."""
    if not cast_crew:
        return
    # Remove existing relationships for this movie
    conn.execute("DELETE FROM movie_people WHERE movie_id = ?", (movie_id,))

    # Determine which people get photos (top 10 actors + all crew)
    photo_count = 0
    MAX_PHOTOS = 15

    for entry in cast_crew:
        tmdb_id = entry.get("tmdb_id")
        name = entry.get("name", "")
        if not name:
            continue

        # Upsert person
        existing = None
        if tmdb_id:
            existing = conn.execute("SELECT id, photo_file FROM people WHERE tmdb_id = ?", (tmdb_id,)).fetchone()

        if existing:
            person_id = existing[0]
            has_photo = _has_local_profile_photo(existing[1])
        else:
            # Insert new person
            conn.execute(
                "INSERT OR IGNORE INTO people (tmdb_id, name, updated_at) VALUES (?, ?, ?)",
                (tmdb_id, name, datetime.utcnow().isoformat())
            )
            if tmdb_id:
                row = conn.execute("SELECT id FROM people WHERE tmdb_id = ?", (tmdb_id,)).fetchone()
            else:
                row = conn.execute("SELECT id FROM people WHERE name = ? ORDER BY id DESC LIMIT 1", (name,)).fetchone()
            if not row:
                continue
            person_id = row[0]
            has_photo = False

        # Download profile photo if needed
        profile_path = entry.get("profile_path")
        if download_photos and not has_photo and profile_path and photo_count < MAX_PHOTOS:
            photo_url = f"https://image.tmdb.org/t/p/w185{profile_path}"
            photo_file = download_profile_photo(photo_url)
            if photo_file:
                conn.execute("UPDATE people SET photo_file = ? WHERE id = ?", (photo_file, person_id))
                photo_count += 1

        # Insert movie-person relationship
        try:
            conn.execute(
                """INSERT OR IGNORE INTO movie_people
                   (movie_id, person_id, role, character, job, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (movie_id, person_id, entry.get("role", "actor"),
                 entry.get("character"), entry.get("job"), entry.get("sort_order", 0))
            )
        except Exception:
            pass


def _trace_add(attempts: list[str], backend: str, result: str, query: str = "", extra: str = ""):
    part = f"{backend}: {result}"
    if query:
        part += f" [{query}]"
    if extra:
        part += f" ({extra})"
    attempts.append(part)


def _trace_summary(attempts: list[str]) -> str:
    return " | ".join(attempts) if attempts else "geen backend attempts"


def _extract_hdr_tokens(text: str) -> str:
    if not text:
        return ""
    low = text.lower()
    tokens = []
    checks = [
        ("dolby vision", "Dolby Vision"),
        ("hdr10+", "HDR10+"),
        ("hdr10", "HDR10"),
        ("hlg", "HLG"),
        ("hdr", "HDR"),
    ]
    for needle, label in checks:
        if needle in low and label not in tokens:
            tokens.append(label)
    return ", ".join(tokens)


def _bluray_find_first_movie_url(query: str) -> str | None:
    """Search Blu-ray.com via their quicksearch AJAX endpoint and return the
    first movie detail URL, or None if nothing was found."""
    if not query:
        return None
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.blu-ray.com/",
    }
    payload = {
        "section": "bluraymovies",
        "userid": "-1",
        "country": "US",
        "keyword": query.strip(),
    }
    try:
        sr = requests.post(
            "https://www.blu-ray.com/search/quicksearch.php",
            data=payload, headers=headers, timeout=10,
        )
        if sr.status_code != 200:
            return None
        # The response contains a JS block with:  var urls = new Array('url1', 'url2', ...);
        m = re.search(r"var\s+urls\s*=\s*new\s+Array\(([^)]+)\)", sr.text)
        if m:
            raw = m.group(1)
            urls = [u.strip().strip("'\"") for u in raw.split(",") if "/movies/" in u]
            if urls:
                return urls[0]
    except Exception:
        pass

    # Fallback: try the old GET search page and look for <a href="/movies/...">
    try:
        q = quote_plus(query.strip())
        search_url = (
            "https://www.blu-ray.com/search/?quicksearch=1"
            "&quicksearch_country=all&section=bluraymovies&quicksearch_keyword=" + q
        )
        sr = requests.get(search_url, headers=headers, timeout=8)
        if sr.status_code != 200:
            return None
        soup = BeautifulSoup(sr.text, "html.parser")
        for a in soup.select('a[href*="/movies/"]'):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            if href.startswith("/"):
                href = "https://www.blu-ray.com" + href
            if "blu-ray.com/movies/" in href and re.search(r"/\d{4,}/", href):
                return href
    except Exception:
        pass
    return None


def _bluray_parse_movie_page(detail_url: str) -> dict | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (DiscVault/1.0; +https://example.local)"
    }
    dr = requests.get(detail_url, headers=headers, timeout=8)
    if dr.status_code != 200:
        return None
    dsoup = BeautifulSoup(dr.text, "html.parser")

    title = ""
    og_title = dsoup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        title = og_title.get("content", "").strip()
    if not title:
        h1 = dsoup.find("h1")
        if h1:
            title = h1.get_text(" ", strip=True)

    poster = ""
    og_image = dsoup.find("meta", attrs={"property": "og:image"})
    if og_image and og_image.get("content"):
        poster = og_image.get("content", "").strip()

    def _clean_text(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "")).strip()

    def _node_text_by_id(*node_ids: str) -> str:
        for node_id in node_ids:
            node = dsoup.find(id=node_id)
            if node:
                txt = _clean_text(node.get_text(" ", strip=True).replace(" less", ""))
                if txt:
                    return txt
        return ""

    video_text = ""
    audio_text = _node_text_by_id("shortaudio", "longaudio")
    subs_text = _node_text_by_id("shortsubs", "longsubs")

    # Fallback for pages that still use tabular specs.
    if not audio_text or not subs_text or not video_text:
        for tr in dsoup.select("tr"):
            th = tr.find("th")
            td = tr.find("td")
            if not th or not td:
                continue
            label = th.get_text(" ", strip=True).lower()
            value = _clean_text(td.get_text(" ", strip=True))
            if not value:
                continue
            if "video" in label and not video_text:
                video_text = value
            elif "audio" in label and not audio_text:
                audio_text = value
            elif "subtitle" in label and not subs_text:
                subs_text = value

    # Some Blu-ray.com pages expose Video/Audio/Subtitles in a free-form block.
    if not video_text or not audio_text or not subs_text:
        page_text = dsoup.get_text("\n", strip=True)
        if not video_text:
            mv = re.search(r"Video\s*\n+([^\n]+)", page_text, flags=re.IGNORECASE)
            if mv:
                video_text = _clean_text(mv.group(1))
        if not audio_text:
            ma = re.search(r"Audio\s*\n+([^\n]+(?:\n+[^\n]+){0,2})", page_text, flags=re.IGNORECASE)
            if ma:
                audio_text = _clean_text(ma.group(1))
        if not subs_text:
            ms = re.search(r"Subtitles\s*\n+([^\n]+(?:\n+[^\n]+){0,2})", page_text, flags=re.IGNORECASE)
            if ms:
                subs_text = _clean_text(ms.group(1))

    hdr_text = _extract_hdr_tokens(video_text)
    if not hdr_text:
        hdr_text = _extract_hdr_tokens(dsoup.get_text(" ", strip=True)[:5000])

    year = ""
    m = re.search(r"\((\d{4})\)", title)
    if m:
        year = m.group(1)

    out = {
        "title": title,
        "year": year,
        "poster": poster,
        "hdr": hdr_text,
        "audio_tracks": audio_text,
        "subtitles": subs_text,
    }
    return {k: v for k, v in out.items() if v}


def lookup_movie_bluray_specs_traced(title: str, year: str = "", barcode: str = ""):
    attempts = []
    if not title and not barcode:
        attempts.append({"result": "skipped", "query": "geen titel of barcode"})
        return None, attempts

    detail_href = None

    if title:
        query = f"title={title}, year={year}"
        try:
            detail_href = _bluray_find_first_movie_url(f"{title} {year}".strip())
            attempts.append({"result": "hit" if detail_href else "miss", "query": query})
        except Exception as ex:
            attempts.append({"result": "error", "query": query, "extra": str(ex)})

    if not detail_href and barcode:
        query = f"barcode={barcode}"
        try:
            detail_href = _bluray_find_first_movie_url(barcode)
            attempts.append({"result": "hit" if detail_href else "miss", "query": query})
        except Exception as ex:
            attempts.append({"result": "error", "query": query, "extra": str(ex)})

    if not detail_href:
        return None, attempts

    try:
        parsed = _bluray_parse_movie_page(detail_href)
        if not parsed:
            attempts.append({"result": "miss", "query": "detail parse"})
            return None, attempts
        out = {
            "hdr": parsed.get("hdr", ""),
            "audio_tracks": parsed.get("audio_tracks", ""),
            "subtitles": parsed.get("subtitles", ""),
        }
        return {k: v for k, v in out.items() if v}, attempts
    except Exception as ex:
        attempts.append({"result": "error", "query": "detail parse", "extra": str(ex)})
        return None, attempts


def lookup_movie_bluray_specs(title: str, year: str = "", barcode: str = "") -> dict | None:
    specs, _ = lookup_movie_bluray_specs_traced(title, year, barcode)
    return specs


def lookup_movie_bluray_by_barcode(barcode: str) -> dict | None:
    if not barcode:
        return None
    try:
        detail_href = _bluray_find_first_movie_url(barcode)
        if not detail_href:
            return None
        parsed = _bluray_parse_movie_page(detail_href)
        if not parsed:
            return None
        return parsed
    except Exception:
        return None


def _bluraydiscde_find_first_movie_url(query: str) -> str | None:
    """Search bluray-disc.de via POST to /suche/ and return the best matching
    movie detail URL (matching /blu-ray-filme/<id>-<slug>), or None."""
    if not query:
        return None
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    }
    try:
        r = requests.post(
            "https://bluray-disc.de/suche/",
            data={"sq": query.strip(), "suchen": ""},
            headers=headers, timeout=12,
        )
        if r.status_code != 200:
            return None
        # Collect all /blu-ray-filme/<numeric_id>-<slug> links (skip review anchors)
        candidates = []
        for m in re.finditer(
            r'href\s*=\s*"((?:https?://bluray-disc\.de)?/blu-ray-filme/(\d+)-([^"#]*))"',
            r.text, re.IGNORECASE,
        ):
            href = m.group(1)
            if href.startswith("/"):
                href = "https://bluray-disc.de" + href
            candidates.append(href)
        if not candidates:
            return None
        # Try to pick the best match: prefer URLs whose slug contains all query words
        query_words = [w.lower() for w in query.strip().split() if len(w) > 1]
        for url in candidates:
            slug = url.rsplit("/", 1)[-1].lower().replace("_", " ").replace("-", " ")
            if all(w in slug for w in query_words):
                return url
        # Fall back to first candidate
        return candidates[0]
    except Exception:
        pass
    return None


def _bluraydiscde_parse_movie_page(detail_url: str) -> dict | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (DiscVault/1.0; +https://example.local)"
    }
    r = requests.get(detail_url, headers=headers, timeout=8)
    if r.status_code != 200:
        return None
    soup = BeautifulSoup(r.text, "html.parser")

    title = ""
    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        title = (og_title.get("content") or "").strip()
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(" ", strip=True)

    poster = ""
    og_image = soup.find("meta", attrs={"property": "og:image"})
    if og_image and og_image.get("content"):
        poster = (og_image.get("content") or "").strip()

    full_text = soup.get_text("\n", strip=True)

    def _clean_text(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "")).strip()

    audio = ""
    subs = ""

    # Primary parse path: key/val blocks used on bluray-disc.de detail pages.
    for key_node in soup.select("div.key"):
        key = _clean_text(key_node.get_text(" ", strip=True)).lower().rstrip(":")
        val_node = key_node.find_next_sibling("div", class_="val")
        if not val_node:
            continue
        value = _clean_text(val_node.get_text(" ", strip=True))
        if not value:
            continue
        if ("ton" in key or "audio" in key or "sprach" in key) and not audio:
            audio = value
        elif "untertitel" in key and not subs:
            subs = value

    # Fallback for text-only extraction when key/val blocks are not available.
    if not audio:
        ma = re.search(r"(?:Ton|Audio)\s*:\s*(.+)", full_text, flags=re.IGNORECASE)
        if ma:
            audio = _clean_text(ma.group(1).split("\n")[0])
    if not subs:
        ms = re.search(r"Untertitel\s*:\s*(.+)", full_text, flags=re.IGNORECASE)
        if ms:
            subs = _clean_text(ms.group(1).split("\n")[0])

    hdr = _extract_hdr_tokens(full_text[:8000])

    year = ""
    m = re.search(r"\((\d{4})\)", title)
    if m:
        year = m.group(1)

    out = {
        "title": title,
        "year": year,
        "poster": poster,
        "hdr": hdr,
        "audio_tracks": audio,
        "subtitles": subs,
    }
    return {k: v for k, v in out.items() if v}


def lookup_movie_bluraydiscde_specs_traced(title: str, year: str = "", barcode: str = ""):
    attempts = []
    if not title and not barcode:
        attempts.append({"result": "skipped", "query": "geen titel of barcode"})
        return None, attempts

    detail_href = None
    if title:
        query = f"title={title}, year={year}"
        try:
            detail_href = _bluraydiscde_find_first_movie_url(f"{title} {year}".strip())
            attempts.append({"result": "hit" if detail_href else "miss", "query": query})
        except Exception as ex:
            attempts.append({"result": "error", "query": query, "extra": str(ex)})

    if not detail_href and barcode:
        query = f"barcode={barcode}"
        try:
            detail_href = _bluraydiscde_find_first_movie_url(barcode)
            attempts.append({"result": "hit" if detail_href else "miss", "query": query})
        except Exception as ex:
            attempts.append({"result": "error", "query": query, "extra": str(ex)})

    if not detail_href:
        return None, attempts

    try:
        parsed = _bluraydiscde_parse_movie_page(detail_href)
        if not parsed:
            attempts.append({"result": "miss", "query": "detail parse"})
            return None, attempts
        out = {
            "hdr": parsed.get("hdr", ""),
            "audio_tracks": parsed.get("audio_tracks", ""),
            "subtitles": parsed.get("subtitles", ""),
            "poster": parsed.get("poster", ""),
        }
        return {k: v for k, v in out.items() if v}, attempts
    except Exception as ex:
        attempts.append({"result": "error", "query": "detail parse", "extra": str(ex)})
        return None, attempts


def _merge_disc_specs(target: dict, specs: dict | None) -> dict:
    if not specs:
        return target
    for key in ("hdr", "audio_tracks", "subtitles", "poster"):
        if specs.get(key) and not target.get(key):
            target[key] = specs[key]
    return target


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"error": "Method not allowed"}), 405

@app.errorhandler(Exception)
def unhandled(e):
    return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Routes: health / stats
# ---------------------------------------------------------------------------

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "version": "3.0.0"})


@app.route("/api/stats")
def stats():
    conn = get_db()
    uid = _get_current_user_id()
    if uid:
        owner_clause = " AND owner_id = ?"
        owner_params = [uid]
    else:
        owner_clause = ""
        owner_params = []
    total     = conn.execute(f"SELECT COUNT(*) FROM movies WHERE 1=1{owner_clause}", owner_params).fetchone()[0]
    by_format = conn.execute(
        f"SELECT format, COUNT(*) as count FROM movies WHERE 1=1{owner_clause} GROUP BY format", owner_params
    ).fetchall()
    recent    = conn.execute(
        f"SELECT * FROM movies WHERE 1=1{owner_clause} ORDER BY added_at DESC LIMIT 5", owner_params
    ).fetchall()
    conn.close()
    return jsonify({
        "total":     total,
        "by_format": [dict(r) for r in by_format],
        "recent":    [dict(r) for r in recent],
    })


# ---------------------------------------------------------------------------
# Routes: logs
# ---------------------------------------------------------------------------

@app.route("/api/logs", methods=["GET"])
def get_logs():
    """Retrieve logs. Supports ?level=error&category=refresh&limit=200&since=<id>"""
    err = _require_admin()
    if err:
        return err
    conn   = get_db()
    level  = request.args.get("level", "")
    cat    = request.args.get("category", "")
    since  = request.args.get("since", "0")
    limit  = min(int(request.args.get("limit", "200")), 1000)

    sql    = "SELECT * FROM logs WHERE id > ?"
    params = [int(since)]
    if level:
        sql += " AND level = ?"
        params.append(level)
    if cat:
        sql += " AND category = ?"
        params.append(cat)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/logs", methods=["DELETE"])
def clear_logs():
    err = _require_admin()
    if err:
        return err
    conn = get_db()
    conn.execute("DELETE FROM logs")
    conn.commit()
    conn.close()
    add_log("general", "Logs gewist")
    return jsonify({"status": "cleared"})


# ---------------------------------------------------------------------------
# Routes: movies CRUD
# ---------------------------------------------------------------------------

@app.route("/api/movies", methods=["GET"])
def list_movies():
    conn = get_db()
    q   = request.args.get("q", "")
    fmt = request.args.get("format", "")
    group_editions = request.args.get("group_editions", "false").lower() == "true"
    owner_clause, owner_params = _movie_owner_filter()
    sql = ("SELECT movies.*, users.display_name AS owner_name"
           " FROM movies LEFT JOIN users ON movies.owner_id = users.id"
           " WHERE 1=1" + owner_clause)
    params = list(owner_params)
    if q:
        sql += (" AND (title LIKE ? OR original_title LIKE ? OR director LIKE ?"
                " OR actor LIKE ? OR genre LIKE ? OR distributor LIKE ? OR box_set LIKE ?)")
        params += [f"%{q}%"] * 7
    if fmt:
        sql += " AND format = ?"
        params.append(fmt)
    sql += " ORDER BY COALESCE(NULLIF(sort_title,''), title) ASC"
    rows = conn.execute(sql, params).fetchall()
    # Enrich with group ids
    movies = [dict(r) for r in rows]
    if movies:
        movie_ids = [m["id"] for m in movies]
        placeholders = ",".join("?" * len(movie_ids))
        mg_rows = conn.execute(
            f"SELECT movie_id, group_id FROM movie_groups WHERE movie_id IN ({placeholders})",
            movie_ids
        ).fetchall()
        groups_map = {}
        for mg in mg_rows:
            groups_map.setdefault(mg["movie_id"], []).append(mg["group_id"])
        for m in movies:
            m["group_ids"] = groups_map.get(m["id"], [])
        # Enrich with watchlist / watch history for current user
        uid = _get_current_user_id() or "__global__"
        wl_rows = conn.execute(
            f"SELECT movie_id FROM watchlist WHERE user_id=? AND movie_id IN ({placeholders})",
            [uid] + movie_ids
        ).fetchall()
        wl_set = {r["movie_id"] for r in wl_rows}
        wh_rows = conn.execute(
            f"SELECT movie_id, MAX(watched_at) as watched_at FROM watch_history WHERE user_id=? AND movie_id IN ({placeholders}) GROUP BY movie_id",
            [uid] + movie_ids
        ).fetchall()
        wh_map = {r["movie_id"]: r["watched_at"] for r in wh_rows}
        for m in movies:
            m["on_watchlist"] = m["id"] in wl_set
            m["last_watched"] = wh_map.get(m["id"])

    # Collapse grouped editions: one representative per edition_group_id
    if group_editions and movies:
        grouped = {}   # edition_group_id -> list of movies
        ungrouped = []
        for m in movies:
            gid = m.get("edition_group_id")
            if gid:
                grouped.setdefault(gid, []).append(m)
            else:
                ungrouped.append(m)
        # For each group, pick primary (primary_movie_id) or best format (4K > Blu-ray > DVD > first)
        format_rank = {"4K UHD": 0, "Blu-ray": 1, "DVD": 2}
        eg_rows = {}
        if grouped:
            gid_list = list(grouped.keys())
            gplaceholders = ",".join("?" * len(gid_list))
            eg_rows_raw = conn.execute(
                f"SELECT id, primary_movie_id, title, badge_label, parent_group_id, collection_id, poster_file, backdrop FROM edition_groups WHERE id IN ({gplaceholders})",
                gid_list
            ).fetchall()
            eg_rows = {r["id"]: {"primary_id": r["primary_movie_id"], "group_title": r["title"],
                                  "badge_label": r["badge_label"], "parent_group_id": r["parent_group_id"],
                                  "collection_id": r["collection_id"],
                                  "poster_file": r["poster_file"], "backdrop": r["backdrop"]} for r in eg_rows_raw}
        collapsed = []
        for gid, members in grouped.items():
            eg_info  = eg_rows.get(gid) or {}
            primary_id = eg_info.get("primary_id")
            primary = next((m for m in members if m["id"] == primary_id), None)
            if not primary:
                primary = sorted(members, key=lambda x: format_rank.get(x.get("format", ""), 99))[0]
            primary = dict(primary)
            primary["editions"]       = members
            primary["editions_count"] = len(members)
            primary["_is_group"]      = True
            # Container's own poster / backdrop take precedence over the
            # primary movie's media so vaults / box-sets / collections can be
            # styled independently.  Store in _container_poster_file so the
            # film's own poster_file is not overwritten (movie detail must
            # always show the film's real poster).
            if eg_info.get("poster_file"):
                primary["_container_poster_file"] = eg_info["poster_file"]
            if eg_info.get("backdrop"):
                primary["backdrop"] = eg_info["backdrop"]
            if eg_info.get("group_title"):
                primary["_group_title"] = eg_info["group_title"]
            if eg_info.get("badge_label"):
                primary["_group_badge_label"] = eg_info["badge_label"]
            if eg_info.get("parent_group_id"):
                primary["_parent_group_id"] = eg_info["parent_group_id"]
            collapsed.append(primary)
        # Build super-group cards for groups that have a parent_group_id
        # AND for loose movies that have super_group_id set
        child_by_parent = {}
        direct_collapsed = []
        for card in collapsed:
            gid      = card.get("edition_group_id")
            par_gid  = (eg_rows.get(gid) or {}).get("parent_group_id")
            if par_gid:
                child_by_parent.setdefault(par_gid, []).append(card)
            else:
                direct_collapsed.append(card)

        # Collect loose movies (no edition_group_id) that have super_group_id
        loose_by_sgid = {}
        new_ungrouped = []
        for m in ungrouped:
            sgid = m.get("super_group_id")
            if sgid:
                loose_by_sgid.setdefault(sgid, []).append(m)
            else:
                new_ungrouped.append(m)
        ungrouped = new_ungrouped

        all_sg_ids = set(child_by_parent.keys()) | set(loose_by_sgid.keys())
        if all_sg_ids:
            par_eg = {}
            for r in conn.execute(
                f"SELECT id, title, badge_label, poster_file, backdrop FROM edition_groups WHERE id IN ({','.join('?'*len(all_sg_ids))})",
                list(all_sg_ids)
            ).fetchall():
                par_eg[r["id"]] = dict(r)

            def _apply_container_media(sg, meta):
                if meta.get("poster_file"):
                    sg["_container_poster_file"] = meta["poster_file"]
                if meta.get("backdrop"):
                    sg["backdrop"] = meta["backdrop"]

            # Super-groups that have child edition-groups
            for par_gid, child_cards in child_by_parent.items():
                lm = loose_by_sgid.pop(par_gid, [])
                rep = child_cards[0]
                sg  = dict(rep)
                sg["_is_super_group"]   = True
                sg["_parent_group_id"]  = par_gid
                meta = par_eg.get(par_gid) or {}
                sg["_group_title"]      = meta.get("title") or sg.get("_group_title") or sg.get("title")
                sg["_group_badge_label"]= meta.get("badge_label")
                _apply_container_media(sg, meta)
                sg["_sub_groups"]       = child_cards
                sg["_sub_group_count"]  = len(child_cards)
                sg["_loose_movies"]     = lm
                sg["editions_count"]    = sum(c["editions_count"] for c in child_cards) + len(lm)
                sg["editions"]          = []
                direct_collapsed.append(sg)

            # Super-groups with only loose movies (no child edition-groups)
            for par_gid, lm in loose_by_sgid.items():
                rep = lm[0]
                sg  = dict(rep)
                sg["_is_super_group"]   = True
                sg["_parent_group_id"]  = par_gid
                meta = par_eg.get(par_gid) or {}
                sg["_group_title"]      = meta.get("title") or sg.get("title")
                sg["_group_badge_label"]= meta.get("badge_label")
                _apply_container_media(sg, meta)
                sg["_sub_groups"]       = []
                sg["_sub_group_count"]  = 0
                sg["_loose_movies"]     = lm
                sg["editions_count"]    = len(lm)
                sg["editions"]          = []
                direct_collapsed.append(sg)

            collapsed = direct_collapsed
        movies = ungrouped + collapsed

        # ── Collection grouping ──────────────────────────────────────
        # A Collection groups Box Sets, standalone Vaults, and loose
        # movies into one top-level card.
        # Sources of collection_id:
        #   - edition_groups.collection_id  → Vault / Box Set belongs to a Collection
        #   - movies.collection_id          → loose movie belongs to a Collection
        col_children = {}   # collection_id -> list of items (cards / movies)
        final_movies = []
        for item in movies:
            cid = None
            # Box Set (super group) – check collection_id on its parent edition_group
            if item.get("_is_super_group"):
                par_gid = item.get("_parent_group_id")
                if par_gid:
                    cid = (eg_rows.get(par_gid) or {}).get("collection_id")
                    if not cid:
                        # check the parent EG we fetched earlier (par_eg dict)
                        cid_row = conn.execute("SELECT collection_id FROM edition_groups WHERE id=?", (par_gid,)).fetchone()
                        if cid_row:
                            cid = cid_row["collection_id"]
            # Vault (edition group card, not super group)
            elif item.get("_is_group"):
                gid = item.get("edition_group_id")
                if gid:
                    cid = (eg_rows.get(gid) or {}).get("collection_id")
                    if not cid:
                        cid_row = conn.execute("SELECT collection_id FROM edition_groups WHERE id=?", (gid,)).fetchone()
                        if cid_row:
                            cid = cid_row["collection_id"]
            # Loose movie
            else:
                cid = item.get("collection_id")
            if cid:
                col_children.setdefault(cid, []).append(item)
            else:
                final_movies.append(item)

        if col_children:
            col_ids = list(col_children.keys())
            col_meta = {}
            for r in conn.execute(
                f"SELECT id, title, badge_label, poster_file, backdrop FROM collections WHERE id IN ({','.join('?'*len(col_ids))})",
                col_ids
            ).fetchall():
                col_meta[r["id"]] = dict(r)

            for cid, children in col_children.items():
                meta = col_meta.get(cid) or {}
                # Separate into box_sets, vaults, loose_movies
                box_sets = [c for c in children if c.get("_is_super_group")]
                vaults   = [c for c in children if c.get("_is_group") and not c.get("_is_super_group")]
                loose    = [c for c in children if not c.get("_is_group") and not c.get("_is_super_group")]
                rep = children[0]
                cc  = dict(rep)
                cc["_is_collection"]       = True
                cc["_collection_id"]       = cid
                cc["_group_title"]         = meta.get("title") or cc.get("title", "")
                cc["_group_badge_label"]   = meta.get("badge_label")
                # Collection's own poster / backdrop go into _container_poster_file
                # so the representative film's poster_file stays untouched.
                if meta.get("poster_file"):
                    cc["_container_poster_file"] = meta["poster_file"]
                if meta.get("backdrop"):
                    cc["backdrop"] = meta["backdrop"]
                cc["_box_sets"]            = box_sets
                cc["_vaults"]              = vaults
                cc["_loose_movies"]        = loose
                total = 0
                for bs in box_sets:
                    total += bs.get("editions_count", 0)
                for v in vaults:
                    total += v.get("editions_count", 0)
                total += len(loose)
                cc["editions_count"]       = total
                cc["editions"]             = []
                final_movies.append(cc)

        movies = final_movies
        movies.sort(key=lambda m: (m.get("sort_title") or m.get("title") or "").lower())

    conn.close()
    return jsonify(movies)


@app.route("/api/movies/<int:movie_id>", methods=["GET"])
def get_movie(movie_id):
    conn  = get_db()
    movie = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
    conn.close()
    if not movie:
        return jsonify({"error": "Not found"}), 404
    return jsonify(dict(movie))


@app.route("/api/movies/<int:movie_id>/cast", methods=["GET"])
def get_movie_cast(movie_id):
    """Get cast & crew for a movie with person details."""
    conn = get_db()
    rows = conn.execute("""
        SELECT mp.role, mp.character, mp.job, mp.sort_order,
               p.id as person_id, p.tmdb_id, p.name, p.photo_file
        FROM movie_people mp
        JOIN people p ON p.id = mp.person_id
        WHERE mp.movie_id = ?
        ORDER BY mp.role, mp.sort_order
    """, (movie_id,)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["photo_url"] = _make_signed_profile_url(d.get("photo_file"))
        out.append(d)
    return jsonify(out)


@app.route("/api/people/<int:person_id>", methods=["GET"])
def get_person(person_id):
    """Get person details + all movies they appear in from the collection."""
    conn = get_db()
    person = conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
    if not person:
        conn.close()
        return jsonify({"error": "Not found"}), 404

    # Check if we have biography, if not and tmdb_id exists, fetch from TMDb
    p = dict(person)
    if not p.get("biography") and p.get("tmdb_id") and TMDB_API_KEY:
        try:
            r = requests.get(
                f"https://api.themoviedb.org/3/person/{p['tmdb_id']}"
                f"?api_key={TMDB_API_KEY}",
                timeout=6
            )
            if r.status_code == 200:
                pd = r.json()
                bio = pd.get("biography", "")
                birthday = pd.get("birthday", "")
                deathday = pd.get("deathday", "")
                birthplace = pd.get("place_of_birth", "")
                known_for = pd.get("known_for_department", "")
                # Download photo if missing
                photo_file = p.get("photo_file")
                if (not _has_local_profile_photo(photo_file)) and pd.get("profile_path"):
                    photo_url = f"https://image.tmdb.org/t/p/w185{pd['profile_path']}"
                    photo_file = download_profile_photo(photo_url)
                conn.execute(
                    """UPDATE people SET biography=?, birthday=?, deathday=?,
                       place_of_birth=?, known_for=?, photo_file=COALESCE(?, photo_file),
                       updated_at=? WHERE id=?""",
                    (bio, birthday, deathday, birthplace, known_for,
                     photo_file, datetime.utcnow().isoformat(), person_id)
                )
                conn.commit()
                p["biography"] = bio
                p["birthday"] = birthday
                p["deathday"] = deathday
                p["place_of_birth"] = birthplace
                p["known_for"] = known_for
                if photo_file:
                    p["photo_file"] = photo_file
        except Exception:
            pass

    # Fetch multilingual biographies if not yet stored
    if p.get("tmdb_id") and TMDB_API_KEY:
        missing_langs = [
            (lc, tl) for lc, tl in TMDB_LANGUAGES
            if not p.get(f"biography_{lc}")
        ]
        if missing_langs:
            try:
                for lang_code, tmdb_lang in missing_langs:
                    rl = requests.get(
                        f"https://api.themoviedb.org/3/person/{p['tmdb_id']}"
                        f"?api_key={TMDB_API_KEY}&language={tmdb_lang}",
                        timeout=5
                    )
                    if rl.status_code == 200:
                        lang_bio = rl.json().get("biography", "") or ""
                        conn.execute(
                            f"UPDATE people SET biography_{lang_code}=? WHERE id=?",
                            (lang_bio, person_id)
                        )
                        p[f"biography_{lang_code}"] = lang_bio
                conn.commit()
            except Exception:
                pass

    # Get all movies this person appears in
    movies = conn.execute("""
        SELECT m.id, m.title, m.year, m.poster_file, m.poster, m.format,
               mp.role, mp.character, mp.job
        FROM movie_people mp
        JOIN movies m ON m.id = mp.movie_id
        WHERE mp.person_id = ?
        ORDER BY m.year DESC
    """, (person_id,)).fetchall()
    conn.close()

    p["photo_url"] = _make_signed_profile_url(p.get("photo_file"))
    p["movies"] = [dict(m) for m in movies]
    return jsonify(p)


@app.route("/api/debug/people/<int:person_id>/photo", methods=["GET"])
def debug_person_photo(person_id):
    """Debug endpoint to verify why a person profile image may not render."""
    conn = get_db()
    person = conn.execute(
        "SELECT id, tmdb_id, name, photo_file FROM people WHERE id = ?",
        (person_id,)
    ).fetchone()
    conn.close()

    if not person:
        return jsonify({"error": "Not found"}), 404

    p = dict(person)
    raw_photo_file = (p.get("photo_file") or "").strip()
    safe_photo_file = os.path.basename(raw_photo_file.replace("\\", "/")) if raw_photo_file else ""
    disk_path = os.path.join(PROFILE_DIR, safe_photo_file) if safe_photo_file else ""
    exists_on_disk = bool(safe_photo_file and os.path.isfile(disk_path))

    return jsonify({
        "person_id": p.get("id"),
        "name": p.get("name"),
        "tmdb_id": p.get("tmdb_id"),
        "photo_file_raw": raw_photo_file,
        "photo_file_safe": safe_photo_file,
        "profile_dir": PROFILE_DIR,
        "expected_disk_path": disk_path,
        "exists_on_disk": exists_on_disk,
        "frontend_url": f"/api/profiles/{safe_photo_file}" if safe_photo_file else "",
        "frontend_url_signed": _make_signed_profile_url(safe_photo_file),
        "frontend_url_cache_busted": f"/api/profiles/{safe_photo_file}?v={safe_photo_file}" if safe_photo_file else "",
    })


@app.route("/api/movies", methods=["POST"])
def add_movie():
    data = request.json or {}
    if not data.get("title"):
        return jsonify({"error": "title is required"}), 400
    if not data.get("barcode"):
        safe = re.sub(r'[^A-Za-z0-9]', '_', data["title"])[:30].upper()
        data = {**data, "barcode": f"MANUAL-{safe}-{int(datetime.utcnow().timestamp() * 1000) % 1000000}"}
    poster_file = data.get("poster_file") or ""
    if not poster_file and data.get("poster"):
        poster_file = download_poster(data["poster"]) or ""
    conn = get_db()
    try:
        row = {col: data.get(col, "") for col in [c for c, _ in SCHEMA_COLUMNS]}
        row["barcode"]     = data["barcode"]
        row["title"]       = data["title"]
        row["poster_file"] = poster_file
        row["format"]      = data.get("format", "4K UHD")
        row["added_at"]    = datetime.utcnow().isoformat()
        # Set owner to current user (or None when auth disabled)
        owner_id = _get_current_user_id()
        cols   = [c for c, _ in SCHEMA_COLUMNS] + ["owner_id"]
        row["owner_id"] = owner_id
        places = ", ".join(f":{c}" for c in cols)
        colstr = ", ".join(cols)
        conn.execute(f"INSERT INTO movies ({colstr}) VALUES ({places})", row)
        conn.commit()
        movie = conn.execute(
            "SELECT * FROM movies WHERE barcode = ?", (data["barcode"],)
        ).fetchone()
        movie_id = movie["id"]

        # Sync cast/crew if tmdb_id is available
        tmdb_id = data.get("tmdb_id") or (movie["tmdb_id"] if movie else "")
        if tmdb_id and TMDB_API_KEY:
            try:
                cast_crew = _fetch_tmdb_cast_crew(tmdb_id)
                if cast_crew:
                    _sync_movie_cast_crew(conn, movie_id, cast_crew, download_photos=True)
                    conn.commit()
            except Exception:
                pass

        conn.close()
        add_log("add", f"Film toegevoegd: {data['title']}", f"Barcode: {data['barcode']}, Format: {data.get('format','')}", "success")

        # Check for duplicate TMDb ID to suggest edition grouping
        hint = None
        tmdb_id_new = dict(movie).get("tmdb_id", "")
        if tmdb_id_new:
            dup_conn = get_db()
            dups = dup_conn.execute(
                "SELECT id, title, edition_type, format, edition_group_id FROM movies"
                " WHERE tmdb_id = ? AND id != ?",
                (tmdb_id_new, movie["id"])
            ).fetchall()
            dup_conn.close()
            if dups:
                hint = {
                    "existing_movies": [
                        {"id": d["id"], "title": d["title"], "edition_type": d["edition_type"] or "standard",
                         "format": d["format"], "edition_group_id": d["edition_group_id"]}
                        for d in dups
                    ]
                }
                # Suggest existing group if one of the dupes is already in a group
                existing_gid = next((d["edition_group_id"] for d in dups if d["edition_group_id"]), None)
                hint["suggested_group_id"] = existing_gid

        return jsonify({"status": "added", "movie": dict(movie), "duplicate_tmdb_hint": hint}), 201
    except sqlite3.IntegrityError:
        conn.close()
        add_log("add", f"Duplicaat barcode: {data['barcode']}", f"Titel: {data['title']}", "warn")
        return jsonify({"error": "Barcode already exists"}), 409


@app.route("/api/movies/<int:movie_id>", methods=["PUT"])
def update_movie(movie_id):
    data = request.json or {}
    conn = get_db()
    existing_row = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
    if not existing_row:
        conn.close()
        return jsonify({"error": "Not found"}), 404
    if not _check_movie_owner(existing_row):
        conn.close()
        return jsonify({"error": "Not your movie"}), 403

    existing = dict(existing_row)
    updates = {k: data[k] for k in ALL_FIELDS if k in data}
    if not updates:
        conn.close()
        return jsonify({"error": "Nothing to update"}), 400

    attempts = []

    if _is_bluray_scrape_enabled():
        merged = dict(existing)
        merged.update(updates)
        lookup_title = (merged.get("original_title") or merged.get("title") or "").strip()
        lookup_year = _parse_year(merged.get("year") or merged.get("release_date") or "")

        missing_spec_fields = []
        for key in ("hdr", "audio_tracks", "subtitles"):
            value = merged.get(key)
            if value is None or str(value).strip() == "":
                missing_spec_fields.append(key)

        if lookup_title and missing_spec_fields:
            specs, bluray_attempts = lookup_movie_bluray_specs_traced(lookup_title, lookup_year, merged.get("barcode") or "")
            for a in bluray_attempts:
                _trace_add(attempts, "Blu-ray.com", a.get("result", "miss"), a.get("query", ""), a.get("extra", ""))
            if specs:
                filled = []
                for key in missing_spec_fields:
                    if specs.get(key):
                        updates[key] = specs[key]
                        filled.append(key)
                if filled:
                    add_log(
                        "refresh",
                        f'Blu-ray.com supplement for manual update: "{merged.get("title") or lookup_title}"',
                        f"Aangevuld: {', '.join(filled)}",
                        "info"
                    )
        elif lookup_title:
            _trace_add(attempts, "Blu-ray.com", "skipped", f"title={lookup_title}", "geen missende hdr/audio/subtitles")
    else:
        _trace_add(attempts, "Blu-ray.com", "skipped", "source toggle uit")

    # If the film is joining an existing vault, lock that vault's current
    # representative first so the new film doesn't take over the poster.
    new_eg_id = updates.get("edition_group_id")
    if new_eg_id and new_eg_id != (existing.get("edition_group_id") or None):
        _lock_edition_group_primary(conn, new_eg_id)
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE movies SET {set_clause} WHERE id = ?",
        list(updates.values()) + [movie_id]
    )
    # If any container linkage changed, copy the container's existing group
    # memberships onto this movie so it joins the set in lock-step.
    container_fields = ("edition_group_id", "super_group_id", "collection_id")
    container_changed = any(
        k in updates and (updates[k] or None) != (existing.get(k) or None)
        for k in container_fields
    )
    if container_changed:
        _inherit_groups_from_container_siblings(conn, movie_id)
    conn.commit()
    movie = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
    conn.close()
    add_log("refresh", f'Manual update: "{(movie["title"] if movie else existing.get("title","?"))}"', f"Backends: {_trace_summary(attempts)}", "info")
    return jsonify(dict(movie))


@app.route("/api/movies/<int:movie_id>/poster", methods=["POST"])
def upload_movie_poster(movie_id):
    if "poster" not in request.files:
        return jsonify({"error": "Geen posterbestand meegegeven"}), 400

    conn = get_db()
    movie = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
    if not movie:
        conn.close()
        return jsonify({"error": "Film niet gevonden"}), 404
    if not _check_movie_owner(movie):
        conn.close()
        return jsonify({"error": "Not your movie"}), 403

    new_file, err = save_uploaded_poster(request.files["poster"])
    if err:
        conn.close()
        return jsonify({"error": err}), 400

    old_file = (movie["poster_file"] or "").strip()
    if old_file:
        try:
            os.remove(os.path.join(POSTER_DIR, os.path.basename(old_file)))
        except OSError:
            pass

    conn.execute("UPDATE movies SET poster_file = ? WHERE id = ?", (new_file, movie_id))
    conn.commit()
    updated = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
    conn.close()

    add_log("refresh", f"Eigen cover geupload voor \"{updated['title']}\"", f"Bestand: {new_file}", "success")
    return jsonify({"status": "updated", "movie": dict(updated), "poster_file": new_file})


@app.route("/api/movies/bulk-delete", methods=["POST"])
def bulk_delete():
    ids = (request.json or {}).get("ids", [])
    if not ids:
        return jsonify({"error": "No ids provided"}), 400
    conn = get_db()
    deleted = 0
    for movie_id in ids:
        row = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
        if not row or not _check_movie_owner(row):
            continue
        if row and row["poster_file"]:
            try:
                os.remove(os.path.join(POSTER_DIR, row["poster_file"]))
            except OSError:
                pass
        conn.execute("DELETE FROM movie_people WHERE movie_id = ?", (movie_id,))
        conn.execute("DELETE FROM movies WHERE id = ?", (movie_id,))
        deleted += 1
    conn.commit()
    conn.close()
    add_log("delete", f"Bulk verwijderd: {deleted} film(s)", f"IDs: {ids[:20]}", "success")
    return jsonify({"status": "done", "deleted": deleted})


@app.route("/api/movies/<int:movie_id>/refresh", methods=["POST"])
def refresh_single(movie_id):
    """Refresh metadata for one movie. Returns details of what changed."""
    fetch_posters = (request.json or {}).get("fetch_posters", True)
    conn = get_db()
    row = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"status": "skipped", "reason": "not_found"})
    if not _check_movie_owner(row):
        conn.close()
        return jsonify({"error": "Not your movie"}), 403

    movie = dict(row)
    title          = movie.get("title", "")
    original_title = movie.get("original_title", "")
    search_title   = original_title or title
    year           = movie.get("year", "")
    imdb_id        = movie.get("imdb_id", "")

    try:
        info = None
        omdb_info = None
        source_parts = []
        attempts = []
        tmdb_id_known = movie.get("tmdb_id", "")

        # 1. TMDb — primary source (multilingual titles/plots, backdrops, trailer)
        try:
            if tmdb_id_known:
                info = lookup_movie_tmdb_by_id(tmdb_id_known)
                _trace_add(attempts, "TMDb", "hit" if info else "miss", f"tmdb_id={tmdb_id_known}")
            if not info:
                info = lookup_movie_tmdb(search_title, year)
                _trace_add(attempts, "TMDb", "hit" if info else "miss", f"title={search_title}, year={year}")
            if not info and original_title and original_title != title:
                info = lookup_movie_tmdb(title, year)
                _trace_add(attempts, "TMDb", "hit" if info else "miss", f"fallback title={title}, year={year}")
            if info:
                source_parts.append("TMDb")
        except Exception as ex:
            _trace_add(attempts, "TMDb", "error", f"title={search_title}", str(ex))

        # 2. OMDb — supplementary: fills imdb_id, rating, and any fields TMDb missed
        if OMDB_API_KEY:
            try:
                if imdb_id:
                    omdb_info = lookup_movie_omdb(imdb_id=imdb_id)
                    _trace_add(attempts, "OMDb", "hit" if omdb_info else "miss", f"imdb_id={imdb_id}")
                if not omdb_info:
                    omdb_info = lookup_movie_omdb(title=search_title)
                    _trace_add(attempts, "OMDb", "hit" if omdb_info else "miss", f"title={search_title}")
                if omdb_info:
                    source_parts.append("OMDb")
            except Exception as ex:
                _trace_add(attempts, "OMDb", "error", f"title={search_title}", str(ex))
        elif imdb_id:
            _trace_add(attempts, "OMDb", "skipped", f"imdb_id={imdb_id}", "OMDB_API_KEY ontbreekt")

        # Merge: fill any missing fields from OMDb into TMDb result
        if info and omdb_info:
            for k, v in omdb_info.items():
                if v and not info.get(k):
                    info[k] = v
        elif not info:
            info = omdb_info

        source = " + ".join(source_parts) if source_parts else ""
        if info and _is_bluray_scrape_enabled():
            specs, bluray_attempts = lookup_movie_bluray_specs_traced(
                info.get("title") or search_title,
                info.get("year") or year,
                movie.get("barcode") or ""
            )
            for a in bluray_attempts:
                _trace_add(attempts, "Blu-ray.com", a.get("result", "miss"), a.get("query", ""), a.get("extra", ""))
            before = dict(info)
            info = _merge_disc_specs(info, specs)
            if specs and info != before:
                source = f"{source} + Blu-ray.com"
        elif info:
            _trace_add(attempts, "Blu-ray.com", "skipped", "source toggle uit")
        if not info:
            conn.close()
            add_log("refresh", f"Geen resultaat voor \"{search_title}\"", f"Backends: {_trace_summary(attempts)}", "warn")
            return jsonify({"status": "skipped", "reason": "not_found_in_api", "title": title})

        refresh_fields = [
            "plot", "rating", "imdb_id", "tmdb_id", "release_date",
            "actor", "producer", "studios", "original_title",
            "language", "country", "runtime", "genre",
            "hdr", "audio_tracks", "subtitles",
            "title_nl", "title_fr", "title_de", "title_es",
            "plot_nl",  "plot_fr",  "plot_de",  "plot_es",
            "backdrop", "backdrops", "trailer_url", "videos",
        ]
        updates = {f: info[f] for f in refresh_fields if info.get(f)}

        new_poster_url = info.get("poster", "")
        if new_poster_url and new_poster_url != movie.get("poster"):
            if movie.get("poster_file"):
                try: os.remove(os.path.join(POSTER_DIR, movie["poster_file"]))
                except OSError: pass
            updates["poster"] = new_poster_url
            if fetch_posters:
                nf = download_poster(new_poster_url)
                updates["poster_file"] = nf or ""
        elif not movie.get("poster_file") and movie.get("poster") and fetch_posters:
            nf = download_poster(movie["poster"])
            if nf: updates["poster_file"] = nf

        if updates:
            sc = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE movies SET {sc} WHERE id = ?", list(updates.values()) + [movie_id])

        # Sync cast/crew — use tmdb_id directly for reliable credits fetch
        cast_crew = info.get("_cast_crew")
        if not cast_crew and TMDB_API_KEY:
            tid = info.get("tmdb_id") or movie.get("tmdb_id") or ""
            if tid:
                cast_crew = _fetch_tmdb_cast_crew(tid)
        if cast_crew:
            _sync_movie_cast_crew(conn, movie_id, cast_crew, download_photos=fetch_posters)

        conn.commit()
        conn.close()

        fields_updated = list(updates.keys())
        has_poster = "poster_file" in updates and updates.get("poster_file")
        add_log(
            "refresh",
            f"Bijgewerkt: \"{title}\"",
            f"Bron: {source}. Velden: {', '.join(fields_updated)}. Backends: {_trace_summary(attempts)}",
            "success"
        )
        return jsonify({
            "status": "updated", "title": title, "source": source,
            "fields": fields_updated, "has_poster": bool(has_poster)
        })
    except Exception as e:
        conn.close()
        add_log("refresh", f"Fout bij \"{title}\"", str(e), "error")
        return jsonify({"status": "error", "title": title, "error": str(e)})


@app.route("/api/movies/<int:movie_id>/sync-all", methods=["POST"])
def sync_single_all_backends(movie_id):
    """Sync one movie by consulting all active backends, then merge best-effort metadata."""
    fetch_posters = (request.json or {}).get("fetch_posters", True)
    conn = get_db()
    row = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"status": "skipped", "reason": "not_found"})
    if not _check_movie_owner(row):
        conn.close()
        return jsonify({"error": "Not your movie"}), 403

    movie = dict(row)
    title = movie.get("title", "")
    original_title = movie.get("original_title", "")
    search_title = original_title or title
    year = movie.get("year", "")
    imdb_id = movie.get("imdb_id", "")

    try:
        attempts = []
        collected = []

        if imdb_id and OMDB_API_KEY:
            try:
                omdb_by_id = lookup_movie_omdb(imdb_id=imdb_id)
                _trace_add(attempts, "OMDb", "hit" if omdb_by_id else "miss", f"imdb_id={imdb_id}")
                if omdb_by_id:
                    collected.append(("OMDb (imdb_id)", omdb_by_id))
            except Exception as ex:
                _trace_add(attempts, "OMDb", "error", f"imdb_id={imdb_id}", str(ex))
        elif imdb_id and not OMDB_API_KEY:
            _trace_add(attempts, "OMDb", "skipped", f"imdb_id={imdb_id}", "OMDB_API_KEY ontbreekt")

        try:
            tmdb_id_known = movie.get("tmdb_id", "")
            tmdb = (lookup_movie_tmdb_by_id(tmdb_id_known) if tmdb_id_known else None) \
                   or lookup_movie_tmdb(search_title, year)
            _trace_add(attempts, "TMDb", "hit" if tmdb else "miss",
                       f"tmdb_id={tmdb_id_known}" if tmdb_id_known else f"title={search_title}, year={year}")
            if tmdb:
                collected.append(("TMDb", tmdb))
        except Exception as ex:
            _trace_add(attempts, "TMDb", "error", f"title={search_title}, year={year}", str(ex))

        try:
            omdb_by_title = lookup_movie_omdb(title=search_title)
            _trace_add(attempts, "OMDb", "hit" if omdb_by_title else "miss", f"title={search_title}")
            if omdb_by_title:
                collected.append(("OMDb (title)", omdb_by_title))
        except Exception as ex:
            _trace_add(attempts, "OMDb", "error", f"title={search_title}", str(ex))

        if original_title and original_title != title:
            try:
                tmdb_fallback = lookup_movie_tmdb(title, year)
                _trace_add(attempts, "TMDb", "hit" if tmdb_fallback else "miss", f"fallback title={title}, year={year}")
                if tmdb_fallback:
                    collected.append(("TMDb (fallback)", tmdb_fallback))
            except Exception as ex:
                _trace_add(attempts, "TMDb", "error", f"fallback title={title}, year={year}", str(ex))

            try:
                omdb_fallback = lookup_movie_omdb(title=title)
                _trace_add(attempts, "OMDb", "hit" if omdb_fallback else "miss", f"fallback title={title}")
                if omdb_fallback:
                    collected.append(("OMDb (fallback)", omdb_fallback))
            except Exception as ex:
                _trace_add(attempts, "OMDb", "error", f"fallback title={title}", str(ex))

        if not collected:
            conn.close()
            add_log("refresh", f'Sync all sources: no result for "{search_title}"', f"Backends: {_trace_summary(attempts)}", "warn")
            return jsonify({"status": "skipped", "reason": "not_found_in_api", "title": title})

        # Merge best-effort: prefer first provider that has a value for each field.
        info = {}
        used_sources = []
        for src, data in collected:
            used = False
            for k, v in (data or {}).items():
                if v and not info.get(k):
                    info[k] = v
                    used = True
            if used:
                used_sources.append(src)

        # Special merge for content ratings — union of all sources (first wins per country)
        merged_ratings = {}
        for _, data in collected:
            if data and isinstance(data.get("_content_ratings"), dict):
                for country, cert in data["_content_ratings"].items():
                    if country not in merged_ratings:
                        merged_ratings[country] = cert
        if merged_ratings:
            info["content_ratings"] = json.dumps(merged_ratings, ensure_ascii=False)
            if not info.get("audience_rating") and merged_ratings.get("US"):
                info["audience_rating"] = merged_ratings["US"]

        if _is_bluray_scrape_enabled():
            specs, bluray_attempts = lookup_movie_bluray_specs_traced(
                info.get("title") or search_title,
                info.get("year") or year,
                movie.get("barcode") or ""
            )
            for a in bluray_attempts:
                _trace_add(attempts, "Blu-ray.com", a.get("result", "miss"), a.get("query", ""), a.get("extra", ""))
            before = dict(info)
            info = _merge_disc_specs(info, specs)
            if info != before:
                used_sources.append("Blu-ray.com")
        else:
            _trace_add(attempts, "Blu-ray.com", "skipped", "source toggle uit")

        if _is_bluraydiscde_scrape_enabled():
            de_specs, de_attempts = lookup_movie_bluraydiscde_specs_traced(
                info.get("title") or search_title,
                info.get("year") or year,
                movie.get("barcode") or ""
            )
            for a in de_attempts:
                _trace_add(attempts, "bluray-disc.de", a.get("result", "miss"), a.get("query", ""), a.get("extra", ""))
            before_de = dict(info)
            info = _merge_disc_specs(info, de_specs)
            if de_specs and de_specs.get("poster") and not info.get("poster"):
                info["poster"] = de_specs.get("poster")
            if info != before_de:
                used_sources.append("bluray-disc.de")
        else:
            _trace_add(attempts, "bluray-disc.de", "skipped", "source toggle uit")

        refresh_fields = [
            "plot", "rating", "imdb_id", "tmdb_id", "release_date",
            "actor", "producer", "studios", "original_title",
            "language", "country", "runtime", "genre",
            "hdr", "audio_tracks", "subtitles",
            "title_nl", "title_fr", "title_de", "title_es",
            "plot_nl",  "plot_fr",  "plot_de",  "plot_es",
            "backdrop", "backdrops", "trailer_url", "videos",
            "audience_rating", "content_ratings",
        ]
        updates = {f: info[f] for f in refresh_fields if info.get(f)}

        new_poster_url = info.get("poster", "")
        if new_poster_url and new_poster_url != movie.get("poster"):
            if movie.get("poster_file"):
                try:
                    os.remove(os.path.join(POSTER_DIR, movie["poster_file"]))
                except OSError:
                    pass
            updates["poster"] = new_poster_url
            if fetch_posters:
                nf = download_poster(new_poster_url)
                updates["poster_file"] = nf or ""
        elif not movie.get("poster_file") and movie.get("poster") and fetch_posters:
            nf = download_poster(movie["poster"])
            if nf:
                updates["poster_file"] = nf

        if updates:
            sc = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE movies SET {sc} WHERE id = ?", list(updates.values()) + [movie_id])

        # Sync cast/crew — use tmdb_id directly for reliable credits fetch
        cast_crew = info.get("_cast_crew")
        if not cast_crew and TMDB_API_KEY:
            tid = info.get("tmdb_id") or movie.get("tmdb_id") or ""
            if tid:
                cast_crew = _fetch_tmdb_cast_crew(tid)
        if cast_crew:
            _sync_movie_cast_crew(conn, movie_id, cast_crew, download_photos=fetch_posters)

        conn.commit()
        conn.close()

        fields_updated = list(updates.keys())
        has_poster = "poster_file" in updates and updates.get("poster_file")
        source_label = " + ".join(dict.fromkeys(used_sources)) or "alle actieve backends"
        add_log(
            "refresh",
            f'Sync all sources: "{title}"',
            f"Sources: {source_label}. Fields: {', '.join(fields_updated)}. Backends: {_trace_summary(attempts)}",
            "success"
        )
        return jsonify({
            "status": "updated",
            "title": title,
            "source": source_label,
            "fields": fields_updated,
            "has_poster": bool(has_poster)
        })
    except Exception as e:
        conn.close()
        add_log("refresh", f'Error syncing all sources "{title}"', str(e), "error")
        return jsonify({"status": "error", "title": title, "error": str(e)})


@app.route("/api/movies/<int:movie_id>/sync-source", methods=["POST"])
def sync_single_source(movie_id):
    data = request.json or {}
    source = (data.get("source") or "").strip()
    fetch_posters = data.get("fetch_posters", True)
    allowed = {"omdb_imdb", "tmdb_title", "omdb_title", "fallback_title", "bluray_com", "bluray_disc_de"}
    if source not in allowed:
        return jsonify({"status": "error", "error": "Unknown source"}), 400

    conn = get_db()
    row = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"status": "skipped", "reason": "not_found"})
    if not _check_movie_owner(row):
        conn.close()
        return jsonify({"error": "Not your movie"}), 403

    movie = dict(row)
    title = movie.get("title", "")
    original_title = movie.get("original_title", "")
    search_title = original_title or title
    year = movie.get("year", "")
    imdb_id = movie.get("imdb_id", "")
    barcode = movie.get("barcode", "")

    attempts = []
    info = None
    source_label = source

    try:
        if source == "omdb_imdb":
            if imdb_id and OMDB_API_KEY:
                info = lookup_movie_omdb(imdb_id=imdb_id)
                _trace_add(attempts, "OMDb", "hit" if info else "miss", f"imdb_id={imdb_id}")
            elif imdb_id and not OMDB_API_KEY:
                _trace_add(attempts, "OMDb", "skipped", f"imdb_id={imdb_id}", "OMDB_API_KEY ontbreekt")
            else:
                _trace_add(attempts, "OMDb", "skipped", "geen imdb_id")
            source_label = "OMDb (imdb_id)"

        elif source == "tmdb_title":
            info = lookup_movie_tmdb(search_title, year)
            _trace_add(attempts, "TMDb", "hit" if info else "miss", f"title={search_title}, year={year}")
            source_label = "TMDb (titel+jaar)"

        elif source == "omdb_title":
            info = lookup_movie_omdb(title=search_title)
            _trace_add(attempts, "OMDb", "hit" if info else "miss", f"title={search_title}")
            source_label = "OMDb (titel)"

        elif source == "fallback_title":
            info = lookup_movie_tmdb(title, year)
            _trace_add(attempts, "TMDb", "hit" if info else "miss", f"fallback title={title}, year={year}")
            if not info:
                info = lookup_movie_omdb(title=title)
                _trace_add(attempts, "OMDb", "hit" if info else "miss", f"fallback title={title}")
            source_label = "Fallback titel"

        elif source == "bluray_com":
            if _is_bluray_scrape_enabled():
                specs, b_attempts = lookup_movie_bluray_specs_traced(search_title, year, barcode)
                for a in b_attempts:
                    _trace_add(attempts, "Blu-ray.com", a.get("result", "miss"), a.get("query", ""), a.get("extra", ""))
                info = _merge_disc_specs({}, specs)
            else:
                _trace_add(attempts, "Blu-ray.com", "skipped", "source toggle uit")
            source_label = "Blu-ray.com"

        elif source == "bluray_disc_de":
            if _is_bluraydiscde_scrape_enabled():
                specs, d_attempts = lookup_movie_bluraydiscde_specs_traced(search_title, year, barcode)
                for a in d_attempts:
                    _trace_add(attempts, "bluray-disc.de", a.get("result", "miss"), a.get("query", ""), a.get("extra", ""))
                info = _merge_disc_specs({}, specs)
                if specs and specs.get("poster"):
                    info["poster"] = specs.get("poster")
            else:
                _trace_add(attempts, "bluray-disc.de", "skipped", "source toggle uit")
            source_label = "bluray-disc.de"

        if not info:
            conn.close()
            add_log("refresh", f"Sync bron geen resultaat: \"{title}\"", f"Bron: {source_label}. Backends: {_trace_summary(attempts)}", "warn")
            return jsonify({"status": "skipped", "reason": "not_found_in_source", "title": title})

        refresh_fields = [
            "plot", "rating", "imdb_id", "tmdb_id", "release_date",
            "actor", "producer", "studios", "original_title",
            "language", "country", "runtime", "genre",
            "hdr", "audio_tracks", "subtitles",
        ]
        updates = {f: info[f] for f in refresh_fields if info.get(f)}

        new_poster_url = info.get("poster", "")
        if new_poster_url and new_poster_url != movie.get("poster"):
            if movie.get("poster_file"):
                try:
                    os.remove(os.path.join(POSTER_DIR, movie["poster_file"]))
                except OSError:
                    pass
            updates["poster"] = new_poster_url
            if fetch_posters:
                nf = download_poster(new_poster_url)
                updates["poster_file"] = nf or ""

        if updates:
            sc = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE movies SET {sc} WHERE id = ?", list(updates.values()) + [movie_id])
        conn.commit()
        conn.close()

        fields_updated = list(updates.keys())
        has_poster = "poster_file" in updates and updates.get("poster_file")
        add_log("refresh", f"Sync bron: \"{title}\"", f"Bron: {source_label}. Velden: {', '.join(fields_updated)}. Backends: {_trace_summary(attempts)}", "success")
        return jsonify({"status": "updated", "title": title, "source": source_label, "fields": fields_updated, "has_poster": bool(has_poster)})
    except Exception as e:
        conn.close()
        add_log("refresh", f'Error syncing source "{title}"', f"Source: {source_label}. {str(e)}", "error")
        return jsonify({"status": "error", "title": title, "error": str(e)}), 500


@app.route("/api/movies/bulk-refresh", methods=["POST"])
def bulk_refresh():
    """Re-fetch metadata from TMDb/OMDb for a list of movie IDs.
    With ?stream=1 streams NDJSON progress events (one per movie)."""
    ids           = (request.json or {}).get("ids", [])
    fetch_posters = (request.json or {}).get("fetch_posters", True)
    if not ids:
        return jsonify({"error": "No ids provided"}), 400

    def _process_movie(conn, movie_id, fetch_posters):
        """Process a single movie and return (status, title, error_detail)."""
        row = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
        if not row or not _check_movie_owner(row):
            add_log("refresh", f"Film ID {movie_id} niet gevonden — overgeslagen", level="warn")
            return "skipped", f"ID {movie_id}", None
        movie = dict(row)
        title          = movie.get("title", "")
        original_title = movie.get("original_title", "")
        search_title   = original_title or title
        year           = movie.get("year", "")
        imdb_id        = movie.get("imdb_id", "")
        try:
            info = None
            source = ""
            attempts = []
            if imdb_id and OMDB_API_KEY:
                try:
                    info = lookup_movie_omdb(imdb_id=imdb_id)
                    _trace_add(attempts, "OMDb", "hit" if info else "miss", f"imdb_id={imdb_id}")
                except Exception as ex:
                    _trace_add(attempts, "OMDb", "error", f"imdb_id={imdb_id}", str(ex))
                if info:
                    source = f"OMDb (imdb_id={imdb_id})"
            elif imdb_id and not OMDB_API_KEY:
                _trace_add(attempts, "OMDb", "skipped", f"imdb_id={imdb_id}", "OMDB_API_KEY ontbreekt")
            if not info:
                try:
                    info = lookup_movie_tmdb(search_title, year)
                    _trace_add(attempts, "TMDb", "hit" if info else "miss", f"title={search_title}, year={year}")
                except Exception as ex:
                    _trace_add(attempts, "TMDb", "error", f"title={search_title}, year={year}", str(ex))
                if info:
                    source = f"TMDb (titel=\"{search_title}\", jaar={year})"
            if not info:
                try:
                    info = lookup_movie_omdb(title=search_title)
                    _trace_add(attempts, "OMDb", "hit" if info else "miss", f"title={search_title}")
                except Exception as ex:
                    _trace_add(attempts, "OMDb", "error", f"title={search_title}", str(ex))
                if info:
                    source = f"OMDb (titel=\"{search_title}\")"
            if not info and original_title and original_title != title:
                try:
                    info = lookup_movie_tmdb(title, year)
                    _trace_add(attempts, "TMDb", "hit" if info else "miss", f"fallback title={title}, year={year}")
                except Exception as ex:
                    _trace_add(attempts, "TMDb", "error", f"fallback title={title}, year={year}", str(ex))
                if not info:
                    try:
                        info = lookup_movie_omdb(title=title)
                        _trace_add(attempts, "OMDb", "hit" if info else "miss", f"fallback title={title}")
                    except Exception as ex:
                        _trace_add(attempts, "OMDb", "error", f"fallback title={title}", str(ex))
                if info:
                    source = f"TMDb/OMDb fallback (titel=\"{title}\")"
            if info and _is_bluray_scrape_enabled():
                specs, bluray_attempts = lookup_movie_bluray_specs_traced(
                    info.get("title") or search_title,
                    info.get("year") or year,
                    movie.get("barcode") or ""
                )
                for a in bluray_attempts:
                    _trace_add(attempts, "Blu-ray.com", a.get("result", "miss"), a.get("query", ""), a.get("extra", ""))
                before = dict(info)
                info = _merge_disc_specs(info, specs)
                if specs and info != before:
                    source = f"{source} + Blu-ray.com"
            elif info:
                _trace_add(attempts, "Blu-ray.com", "skipped", "source toggle uit")
            if not info:
                add_log("refresh", f"Geen resultaat voor \"{search_title}\" ({year})",
                        f"Backends: {_trace_summary(attempts)}", "warn")
                return "skipped", title, None

            refresh_fields = [
                "plot", "rating", "imdb_id", "tmdb_id", "release_date",
                "actor", "producer", "studios", "original_title",
                "language", "country", "runtime", "genre",
                "hdr", "audio_tracks", "subtitles",
                "audience_rating", "content_ratings",
            ]
            # Convert _content_ratings dict to JSON string for storage
            if isinstance(info.get("_content_ratings"), dict) and info["_content_ratings"]:
                cr = info["_content_ratings"]
                info["content_ratings"] = json.dumps(cr, ensure_ascii=False)
                if not info.get("audience_rating") and cr.get("US"):
                    info["audience_rating"] = cr["US"]
            updates = {f: info[f] for f in refresh_fields if info.get(f)}

            new_poster_url = info.get("poster", "")
            if new_poster_url and new_poster_url != movie.get("poster"):
                if movie.get("poster_file"):
                    try:
                        os.remove(os.path.join(POSTER_DIR, movie["poster_file"]))
                    except OSError:
                        pass
                updates["poster"] = new_poster_url
                if fetch_posters:
                    new_file = download_poster(new_poster_url)
                    if new_file:
                        updates["poster_file"] = new_file
                        add_log("refresh", f"Poster gedownload voor \"{title}\"",
                                f"Bron: {source}, Bestand: {new_file}", "success")
                    else:
                        updates["poster_file"] = ""
                        add_log("refresh", f"Poster download mislukt voor \"{title}\"",
                                f"URL: {new_poster_url}", "warn")
            elif not movie.get("poster_file") and movie.get("poster") and fetch_posters:
                new_file = download_poster(movie["poster"])
                if new_file:
                    updates["poster_file"] = new_file

            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(
                    f"UPDATE movies SET {set_clause} WHERE id = ?",
                    list(updates.values()) + [movie_id]
                )

            cast_crew = info.get("_cast_crew")
            if not cast_crew and TMDB_API_KEY:
                tid = info.get("tmdb_id") or movie.get("tmdb_id") or ""
                if tid:
                    cast_crew = _fetch_tmdb_cast_crew(tid)
            if cast_crew:
                _sync_movie_cast_crew(conn, movie_id, cast_crew, download_photos=fetch_posters)

            fields_str = ", ".join(updates.keys()) if updates else "(geen wijzigingen)"
            add_log("refresh", f"Bijgewerkt: \"{title}\"",
                    f"Bron: {source}. Velden: {fields_str}. Backends: {_trace_summary(attempts)}", "success")
            return "updated", title, None
        except Exception as e:
            add_log("refresh", f"Fout bij \"{title}\"", f"Exception: {str(e)}", "error")
            return "error", title, f"[{movie_id}] {title}: {str(e)}"

    # ── Streaming mode ────────────────────────────────────────────────────────
    if request.args.get("stream") == "1":
        def generate():
            conn = get_db()
            updated = skipped = errors = 0
            error_details = []
            total = len(ids)
            for i, movie_id in enumerate(ids):
                status, title, err = _process_movie(conn, movie_id, fetch_posters)
                if status == "updated":  updated += 1
                elif status == "skipped": skipped += 1
                else:
                    errors += 1
                    if err: error_details.append(err)
                yield json.dumps({
                    "type":    "progress",
                    "current": i + 1,
                    "total":   total,
                    "title":   title,
                    "status":  status,
                }) + "\n"
            conn.commit()
            conn.close()
            yield json.dumps({
                "type":         "done",
                "updated":      updated,
                "skipped":      skipped,
                "errors":       errors,
                "error_details": error_details[:20],
            }) + "\n"
        return Response(stream_with_context(generate()), mimetype="application/x-ndjson")

    # ── Batch mode (legacy) ───────────────────────────────────────────────────
    conn = get_db()
    updated = skipped = errors = 0
    error_details = []
    for movie_id in ids:
        status, title, err = _process_movie(conn, movie_id, fetch_posters)
        if status == "updated":  updated += 1
        elif status == "skipped": skipped += 1
        else:
            errors += 1
            if err: error_details.append(err)
    conn.commit()
    conn.close()
    return jsonify({
        "status":        "done",
        "updated":       updated,
        "skipped":       skipped,
        "errors":        errors,
        "error_details": error_details[:20],
    })

# ---------------------------------------------------------------------------
# Routes: delete movie
# ---------------------------------------------------------------------------

@app.route("/api/movies/<int:movie_id>", methods=["DELETE"])
def delete_movie(movie_id):
    conn = get_db()
    row  = conn.execute(
        "SELECT * FROM movies WHERE id = ?", (movie_id,)
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Not found"}), 404
    if not _check_movie_owner(row):
        conn.close()
        return jsonify({"error": "Not your movie"}), 403
    title = row["title"]
    if row["poster_file"]:
        try:
            os.remove(os.path.join(POSTER_DIR, row["poster_file"]))
        except OSError:
            pass
    conn.execute("DELETE FROM movie_people WHERE movie_id = ?", (movie_id,))
    conn.execute("DELETE FROM movies WHERE id = ?", (movie_id,))
    conn.commit()
    conn.close()
    add_log("delete", f"Film verwijderd: {title} (ID {movie_id})")
    return jsonify({"status": "deleted"})


# ---------------------------------------------------------------------------
# Routes: lookup
# ---------------------------------------------------------------------------

@app.route("/api/lookup/<barcode>")
def lookup(barcode):
    stream = request.args.get("stream", "").lower() == "1"

    if not stream:
        return _lookup_sync(barcode)

    def stream_lookup():
        def emit(step, source, status, detail=""):
            return json.dumps({"type": "step", "step": step, "source": source, "status": status, "detail": detail}) + "\n"

        attempts = []
        tmdb_candidates = []
        # 1. Local DB
        yield emit(1, "Local DB", "searching")
        conn = get_db()
        existing = conn.execute("SELECT * FROM movies WHERE barcode = ?", (barcode,)).fetchone()
        conn.close()
        if existing:
            _trace_add(attempts, "Local DB", "hit", f"barcode={barcode}")
            add_log("lookup", f"Barcode {barcode} al in collectie", f"Backends: {_trace_summary(attempts)}", "info")
            yield emit(1, "Local DB", "hit")
            yield json.dumps({"type": "done", "status": "exists", "movie": dict(existing)}) + "\n"
            return
        yield emit(1, "Local DB", "miss")
        _trace_add(attempts, "Local DB", "miss", f"barcode={barcode}")

        # 2. UPCItemDB
        raw_title = None
        yield emit(2, "UPCItemDB", "searching")
        try:
            raw_title = lookup_by_barcode_upcitemdb(barcode)
            _trace_add(attempts, "UPCItemDB", "hit" if raw_title else "miss", f"barcode={barcode}")
            yield emit(2, "UPCItemDB", "hit" if raw_title else "miss", raw_title or "")
        except Exception:
            _trace_add(attempts, "UPCItemDB", "error", f"barcode={barcode}")
            yield emit(2, "UPCItemDB", "error")

        # 3. TMDb / OMDb
        movie_info = None
        if raw_title:
            for candidate in _title_candidates_from_upc(raw_title):
                if not movie_info:
                    yield emit(3, "TMDb", "searching", candidate)
                    try:
                        _cands = _tmdb_search_top5(candidate)
                        if _cands:
                            # Only accept result if TMDb title has real word overlap with
                            # the candidate to avoid distributor names matching wrong films.
                            valid_cands = [c for c in _cands if _titles_overlap(candidate, c['title'])]
                            if valid_cands:
                                movie_info = lookup_movie_tmdb_by_id(valid_cands[0]['tmdb_id'])
                                if movie_info and len(valid_cands) > 1:
                                    tmdb_candidates = valid_cands
                        _trace_add(attempts, "TMDb", "hit" if movie_info else "miss", f"title={candidate}")
                        yield emit(3, "TMDb", "hit" if movie_info else "miss", candidate)
                    except Exception as ex:
                        _trace_add(attempts, "TMDb", "error", f"title={candidate}", str(ex))
                        yield emit(3, "TMDb", "error", candidate)
                if not movie_info:
                    yield emit(4, "OMDb", "searching", candidate)
                    try:
                        movie_info = lookup_movie_omdb(title=candidate)
                        _trace_add(attempts, "OMDb", "hit" if movie_info else "miss", f"title={candidate}")
                        yield emit(4, "OMDb", "hit" if movie_info else "miss", candidate)
                    except Exception as ex:
                        _trace_add(attempts, "OMDb", "error", f"title={candidate}", str(ex))
                        yield emit(4, "OMDb", "error", candidate)
                if movie_info:
                    break

        if not movie_info and raw_title:
            movie_info = {f: "" for f in ALL_FIELDS}
            movie_info["title"] = raw_title
            _trace_add(attempts, "UPCItemDB", "partial", "title-only fallback")

        # 4. Blu-ray.com barcode fallback
        if not movie_info and _is_bluray_scrape_enabled():
            yield emit(5, "Blu-ray.com", "searching", f"barcode={barcode}")
            bluray_info = lookup_movie_bluray_by_barcode(barcode)
            _trace_add(attempts, "Blu-ray.com", "hit" if bluray_info else "miss", f"barcode={barcode}")
            yield emit(5, "Blu-ray.com", "hit" if bluray_info else "miss")
            if bluray_info:
                movie_info = {f: "" for f in ALL_FIELDS}
                movie_info["title"] = bluray_info.get("title", "") or f"Barcode {barcode}"
                movie_info["year"] = bluray_info.get("year", "")
                movie_info["poster"] = bluray_info.get("poster", "")
                movie_info["hdr"] = bluray_info.get("hdr", "")
                movie_info["audio_tracks"] = bluray_info.get("audio_tracks", "")
                movie_info["subtitles"] = bluray_info.get("subtitles", "")
        elif not movie_info:
            _trace_add(attempts, "Blu-ray.com", "skipped", "source toggle uit")

        # 5. Blu-ray.com spec enrichment
        if movie_info:
            if _is_bluray_scrape_enabled():
                yield emit(6, "Blu-ray.com specs", "searching")
                specs, bluray_attempts = lookup_movie_bluray_specs_traced(
                    movie_info.get("title") or raw_title,
                    movie_info.get("year") or "",
                    barcode
                )
                for a in bluray_attempts:
                    _trace_add(attempts, "Blu-ray.com", a.get("result", "miss"), a.get("query", ""), a.get("extra", ""))
                movie_info = _merge_disc_specs(movie_info, specs)
                yield emit(6, "Blu-ray.com specs", "hit" if specs else "miss")
            else:
                _trace_add(attempts, "Blu-ray.com", "skipped", "spec enrichment uit")

            add_log("lookup", f"Barcode {barcode} gevonden: \"{movie_info.get('title','?')}\"",
                    f"Backends: {_trace_summary(attempts)}", "success")
            yield json.dumps({"type": "done", "status": "found", "movie": movie_info, "barcode": barcode,
                              "tmdb_candidates": tmdb_candidates,
                              "raw_title": raw_title or "",
                              "detected_format": _detect_format_from_upc(raw_title or "")}) + "\n"
        else:
            add_log("lookup", f"Barcode {barcode} niet gevonden", f"Backends: {_trace_summary(attempts)}", "warn")
            yield json.dumps({"type": "done", "status": "not_found", "barcode": barcode, "raw_title": raw_title,
                              "detected_format": _detect_format_from_upc(raw_title or "")}) + "\n"

    return Response(stream_lookup(), mimetype="application/x-ndjson")


def _lookup_sync(barcode):
    try:
        attempts = []
        conn     = get_db()
        existing = conn.execute(
            "SELECT * FROM movies WHERE barcode = ?", (barcode,)
        ).fetchone()
        conn.close()
        if existing:
            _trace_add(attempts, "Local DB", "hit", f"barcode={barcode}")
            add_log("lookup", f"Barcode {barcode} al in collectie", f"Backends: {_trace_summary(attempts)}", "info")
            return jsonify({"status": "exists", "movie": dict(existing)})
        _trace_add(attempts, "Local DB", "miss", f"barcode={barcode}")
        raw_title = None
        try:
            raw_title = lookup_by_barcode_upcitemdb(barcode)
            _trace_add(attempts, "UPCItemDB", "hit" if raw_title else "miss", f"barcode={barcode}")
        except Exception:
            _trace_add(attempts, "UPCItemDB", "error", f"barcode={barcode}")
        movie_info = None
        if raw_title:
            for candidate in _title_candidates_from_upc(raw_title):
                if not movie_info:
                    try:
                        _cands = _tmdb_search_top5(candidate)
                        if _cands:
                            valid_cands = [c for c in _cands if _titles_overlap(candidate, c['title'])]
                            if valid_cands:
                                movie_info = lookup_movie_tmdb_by_id(valid_cands[0]['tmdb_id'])
                        _trace_add(attempts, "TMDb", "hit" if movie_info else "miss", f"title={candidate}")
                    except Exception as ex:
                        _trace_add(attempts, "TMDb", "error", f"title={candidate}", str(ex))
                if not movie_info:
                    try:
                        movie_info = lookup_movie_omdb(title=candidate)
                        _trace_add(attempts, "OMDb", "hit" if movie_info else "miss", f"title={candidate}")
                    except Exception as ex:
                        _trace_add(attempts, "OMDb", "error", f"title={candidate}", str(ex))
                if movie_info:
                    break
        if not movie_info and raw_title:
            movie_info = {f: "" for f in ALL_FIELDS}
            movie_info["title"] = raw_title
            _trace_add(attempts, "UPCItemDB", "partial", "title-only fallback")
        if not movie_info and _is_bluray_scrape_enabled():
            bluray_info = lookup_movie_bluray_by_barcode(barcode)
            _trace_add(attempts, "Blu-ray.com", "hit" if bluray_info else "miss", f"barcode={barcode}")
            if bluray_info:
                movie_info = {f: "" for f in ALL_FIELDS}
                movie_info["title"] = bluray_info.get("title", "") or f"Barcode {barcode}"
                movie_info["year"] = bluray_info.get("year", "")
                movie_info["poster"] = bluray_info.get("poster", "")
                movie_info["hdr"] = bluray_info.get("hdr", "")
                movie_info["audio_tracks"] = bluray_info.get("audio_tracks", "")
                movie_info["subtitles"] = bluray_info.get("subtitles", "")
        elif not movie_info:
            _trace_add(attempts, "Blu-ray.com", "skipped", "source toggle uit")
        if movie_info:
            if _is_bluray_scrape_enabled():
                specs, bluray_attempts = lookup_movie_bluray_specs_traced(
                    movie_info.get("title") or raw_title,
                    movie_info.get("year") or "",
                    barcode
                )
                for a in bluray_attempts:
                    _trace_add(attempts, "Blu-ray.com", a.get("result", "miss"), a.get("query", ""), a.get("extra", ""))
                movie_info = _merge_disc_specs(movie_info, specs)
            else:
                _trace_add(attempts, "Blu-ray.com", "skipped", "spec enrichment uit")
            # Finalise content ratings
            if isinstance(movie_info.get("_content_ratings"), dict):
                cr = movie_info.pop("_content_ratings")
                if cr:
                    movie_info["content_ratings"] = json.dumps(cr, ensure_ascii=False)
                    if not movie_info.get("audience_rating") and cr.get("US"):
                        movie_info["audience_rating"] = cr["US"]
            add_log(
                "lookup",
                f"Barcode {barcode} gevonden: \"{movie_info.get('title','?')}\"",
                f"Backends: {_trace_summary(attempts)}",
                "success"
            )
            return jsonify({"status": "found", "movie": movie_info, "barcode": barcode,
                            "detected_format": _detect_format_from_upc(raw_title or "")})
        add_log("lookup", f"Barcode {barcode} niet gevonden", f"Backends: {_trace_summary(attempts)}", "warn")
        return jsonify({"status": "not_found", "barcode": barcode, "raw_title": raw_title,
                        "detected_format": _detect_format_from_upc(raw_title or "")})
    except Exception as e:
        add_log("lookup", f"Fout bij opzoeken barcode {barcode}", str(e), "error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/search_title")
def search_title():
    title = request.args.get("q", "")
    year  = request.args.get("year", "")
    if not title:
        return jsonify({"error": "No title provided"}), 400
    try:
        attempts = []
        movie_info = lookup_movie_tmdb(title, year)
        _trace_add(attempts, "TMDb", "hit" if movie_info else "miss", f"title={title}, year={year}")
        if not movie_info:
            movie_info = lookup_movie_omdb(title=title)
            _trace_add(attempts, "OMDb", "hit" if movie_info else "miss", f"title={title}")
        if movie_info and _is_bluray_scrape_enabled():
            specs, bluray_attempts = lookup_movie_bluray_specs_traced(
                movie_info.get("title") or title,
                movie_info.get("year") or year,
                ""
            )
            for a in bluray_attempts:
                _trace_add(attempts, "Blu-ray.com", a.get("result", "miss"), a.get("query", ""), a.get("extra", ""))
            movie_info = _merge_disc_specs(movie_info, specs)
        elif movie_info:
            _trace_add(attempts, "Blu-ray.com", "skipped", "source toggle uit")
        if movie_info:
            add_log("lookup", f"Titel-zoekactie gevonden: \"{movie_info.get('title') or title}\"", f"Backends: {_trace_summary(attempts)}", "success")
            # Finalise content ratings
            if isinstance(movie_info.get("_content_ratings"), dict):
                cr = movie_info.pop("_content_ratings")
                if cr:
                    movie_info["content_ratings"] = json.dumps(cr, ensure_ascii=False)
                    if not movie_info.get("audience_rating") and cr.get("US"):
                        movie_info["audience_rating"] = cr["US"]
            return jsonify({"status": "found", "movie": movie_info})
        add_log("lookup", f"Titel-zoekactie geen resultaat: \"{title}\"", f"Backends: {_trace_summary(attempts)}", "warn")
        return jsonify({"status": "not_found"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/tmdb_candidates")
def api_tmdb_candidates():
    title = request.args.get("title", "").strip()
    year  = request.args.get("year", "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    return jsonify({"candidates": _tmdb_search_top5(title, year)})


@app.route("/api/tmdb_movie/<int:tmdb_id>")
def api_tmdb_movie(tmdb_id):
    try:
        movie_info = lookup_movie_tmdb_by_id(tmdb_id)
        if not movie_info:
            return jsonify({"error": "not found"}), 404
        if isinstance(movie_info.get("_content_ratings"), dict):
            cr = movie_info.pop("_content_ratings")
            if cr:
                movie_info["content_ratings"] = json.dumps(cr, ensure_ascii=False)
                if not movie_info.get("audience_rating") and cr.get("US"):
                    movie_info["audience_rating"] = cr["US"]
        return jsonify({"movie": movie_info})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Import: field mapping
# ---------------------------------------------------------------------------

KNOWN_FIELDS = {col for col, _ in SCHEMA_COLUMNS} - {"added_at"}

FIELD_ALIASES = {
    # title
    "movie title": "title", "film": "title", "name": "title",
    "film title": "title", "titel": "title",
    # sort_title
    "sort title": "sort_title", "sorteertitel": "sort_title",
    # original_title
    "original title": "original_title", "originele titel": "original_title",
    # year
    "release year": "year", "jaar": "year", "releasejaar": "year",
    # release_date
    "release date": "release_date", "released": "release_date",
    "datum": "release_date", "releasedatum": "release_date",
    # edition
    "editie": "edition",
    "edition release year": "edition_release_year",
    "edition release date": "edition_release_date",
    # country
    "land": "country",
    # language
    "taal": "language",
    # director
    "regisseur": "director", "directed by": "director",
    # actor / cast
    "actors": "actor", "cast": "actor", "acteur": "actor", "acteurs": "actor",
    # producer
    "producers": "producer",
    # studios
    "studio": "studios", "production company": "studios", "productiemaatschappij": "studios",
    # genre
    "genres": "genre",
    # audience_rating
    "audience rating": "audience_rating", "age rating": "audience_rating",
    "leeftijdsclassificatie": "audience_rating", "kijkwijzer": "audience_rating",
    # format
    "media type": "format", "type": "format", "formaat": "format", "disc type": "format",
    # runtime
    "duration": "runtime", "speelduur": "runtime", "length": "runtime",
    # hdr
    "hdr format": "hdr",
    "hdr info": "hdr", "hdr type": "hdr",
    "dolby vision": "hdr", "dv": "hdr", "hdr10": "hdr", "hdr10+": "hdr",
    # packaging
    "verpakking": "packaging",
    # screen_ratios
    "screen ratio": "screen_ratios", "screen ratios": "screen_ratios", "aspect ratio": "screen_ratios",
    "beeldverhouding": "screen_ratios",
    # audio_tracks
    "audio": "audio_tracks", "audio track": "audio_tracks",
    "geluidssporen": "audio_tracks", "audiosporen": "audio_tracks", "audio tracks": "audio_tracks",
    # subtitles
    "subtitle": "subtitles", "subtitle track": "subtitles", "subtitle tracks": "subtitles",
    "ondertitels": "subtitles", "ondertiteltracks": "subtitles",
    # regions
    "region": "regions", "regio": "regions",
    # plot
    "description": "plot", "synopsis": "plot", "omschrijving": "plot",
    # extras
    "special features": "extras", "bonusmateriaal": "extras",
    # box_set
    "box set": "box_set", "boxset": "box_set", "collectie": "box_set",
    "collection": "box_set",
    # imdb
    "imdb id": "imdb_id", "imdb": "imdb_id",
    "imdb url": "imdb_url", "imdb link": "imdb_url",
    # tmdb
    "tmdb id": "tmdb_id", "tmdb": "tmdb_id",
    # poster
    "cover": "poster", "image": "poster", "cover url": "poster",
    # distributor
    "publisher": "distributor", "label": "distributor", "uitgever": "distributor",
    # purchase
    "purchase date": "purchase_date", "aankoopdatum": "purchase_date",
    "purchase price": "purchase_price", "aankoopprijs": "purchase_price",
    # rating
    "imdb rating": "rating", "score": "rating", "beoordeling": "rating",
    # location
    "locatie": "location", "storage": "location", "shelf": "location",
    # notes
    "notities": "notes", "comment": "notes", "comments": "notes",
    "opmerkingen": "notes", "note": "notes",
    # skip fields
    "added date": "_skip", "date added": "_skip", "toegevoegd": "_skip",
}


def normalize_field(raw: str) -> str:
    clean = raw.strip().lower()
    if clean in KNOWN_FIELDS:
        return clean
    return FIELD_ALIASES.get(clean, clean)


def enrich_from_api(row: dict) -> dict:
    # Prefer original_title for API lookups (more accurate match)
    search_title = (row.get("original_title") or row.get("title") or "").strip()
    year     = _parse_year(row.get("year") or row.get("release_date") or "")
    imdb_id  = _extract_imdb_id(row.get("imdb_url") or row.get("imdb_id") or "")
    if not search_title:
        return row

    fillable_fields = [
        "plot", "poster", "rating", "imdb_id", "tmdb_id", "release_date",
        "actor", "producer", "studios", "original_title", "language", "country",
        "runtime", "genre", "director", "hdr", "audio_tracks", "subtitles",
    ]

    def is_missing(field_name: str) -> bool:
        value = row.get(field_name)
        if value is None:
            return True
        return str(value).strip() == ""

    def fill_missing_from(info: dict | None):
        if not info:
            return
        for field in fillable_fields:
            if is_missing(field) and info.get(field):
                row[field] = info[field]

    # Source 1: OMDb by imdb_id if available
    if imdb_id and OMDB_API_KEY:
        try:
            fill_missing_from(lookup_movie_omdb(imdb_id=imdb_id))
        except Exception:
            pass

    # Source 2: TMDb by title/year
    try:
        fill_missing_from(lookup_movie_tmdb(search_title, year))
    except Exception:
        pass

    # Source 3: OMDb by title
    try:
        fill_missing_from(lookup_movie_omdb(title=search_title))
    except Exception:
        pass

    # Source 4+: Disc-specific fallbacks, only for still-empty disc fields
    disc_fields = ["hdr", "audio_tracks", "subtitles", "poster"]
    needs_disc_fields = any(is_missing(f) for f in disc_fields)
    if needs_disc_fields and _is_bluray_scrape_enabled():
        try:
            specs, _ = lookup_movie_bluray_specs_traced(
                row.get("title") or search_title,
                row.get("year") or year,
                row.get("barcode") or ""
            )
            fill_missing_from(_merge_disc_specs({}, specs))
        except Exception:
            pass

    needs_disc_fields = any(is_missing(f) for f in disc_fields)
    if needs_disc_fields and _is_bluraydiscde_scrape_enabled():
        try:
            specs, _ = lookup_movie_bluraydiscde_specs_traced(
                row.get("title") or search_title,
                row.get("year") or year,
                row.get("barcode") or ""
            )
            fill_missing_from(_merge_disc_specs({}, specs))
        except Exception:
            pass

    # Fallback title variant when original_title was used and missing fields remain
    main_title = (row.get("title") or "").strip()
    if main_title and main_title != search_title and any(is_missing(f) for f in fillable_fields):
        try:
            fill_missing_from(lookup_movie_tmdb(main_title, year))
        except Exception:
            pass
        try:
            fill_missing_from(lookup_movie_omdb(title=main_title))
        except Exception:
            pass

    return row


def insert_row(conn, row: dict, mode: str, fetch_posters: bool = True, owner_id: str = None) -> str:
    title = (row.get("title") or "").strip()
    if not title:
        return "skipped"

    year = _parse_year(row.get("year") or row.get("release_date") or "")

    barcode = (row.get("barcode") or "").strip()
    if not barcode:
        safe = re.sub(r'[^A-Za-z0-9]', '_', title)[:40].upper()
        barcode = f"IMPORT-{safe}-{year}"

    existing = conn.execute(
        "SELECT id FROM movies WHERE barcode = ?", (barcode,)
    ).fetchone()

    # Derive imdb_id from imdb_url if not set separately
    imdb_id = _extract_imdb_id(row.get("imdb_url") or row.get("imdb_id") or "")

    poster_url  = (row.get("poster") or "").strip()
    poster_file = (row.get("poster_file") or "").strip()
    if fetch_posters and poster_url and not poster_file:
        poster_file = download_poster(poster_url) or ""

    def _s(key):
        return (row.get(key) or "").strip()

    fields = {
        "barcode":              barcode,
        "title":                title,
        "sort_title":           _s("sort_title"),
        "original_title":       _s("original_title"),
        "year":                 year,
        "release_date":         _s("release_date"),
        "edition":              _s("edition"),
        "edition_release_year": _s("edition_release_year"),
        "edition_release_date": _s("edition_release_date"),
        "country":              _s("country"),
        "language":             _s("language"),
        "director":             _s("director"),
        "actor":                _s("actor"),
        "producer":             _s("producer"),
        "studios":              _s("studios"),
        "genre":                _s("genre").replace(" | ", ", "),
        "audience_rating":      _s("audience_rating"),
        "format":               _s("format") or "4K UHD",
        "runtime":              re.sub(r'[^\d]', '', _s("runtime")),
        "hdr":                  _s("hdr"),
        "packaging":            _s("packaging"),
        "screen_ratios":        _s("screen_ratios"),
        "audio_tracks":         _s("audio_tracks"),
        "subtitles":            _s("subtitles"),
        "regions":              _s("regions"),
        "plot":                 _s("plot"),
        "extras":               _s("extras"),
        "box_set":              _s("box_set"),
        "imdb_id":              imdb_id,
        "imdb_url":             _s("imdb_url"),
        "tmdb_id":              _s("tmdb_id"),
        "poster":               poster_url,
        "poster_file":          poster_file,
        "distributor":          _s("distributor"),
        "purchase_date":        _s("purchase_date"),
        "purchase_price":       _s("purchase_price"),
        "rating":               _s("rating"),
        "location":             _s("location"),
        "notes":                _s("notes"),
        "added_at":             datetime.utcnow().isoformat(),
        "owner_id":             owner_id,
    }

    if existing:
        if mode == "skip":
            return "skipped"
        updates = {k: v for k, v in fields.items()
                   if k not in ("barcode", "added_at") and v}
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(
                f"UPDATE movies SET {set_clause} WHERE barcode = ?",
                list(updates.values()) + [barcode]
            )
        return "updated"
    else:
        cols   = list(fields.keys())
        places = ", ".join(f":{c}" for c in cols)
        colstr = ", ".join(cols)
        conn.execute(f"INSERT INTO movies ({colstr}) VALUES ({places})", fields)
        return "added"


def parse_csv(content: bytes) -> list:
    text = content.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows = []
    for raw_row in reader:
        row = {normalize_field(k): v for k, v in raw_row.items() if k}
        rows.append(row)
    return rows


def parse_xml(content: bytes) -> list:
    root = ET.fromstring(content)
    tag_counts = {}
    for child in root:
        tag_counts[child.tag] = tag_counts.get(child.tag, 0) + 1
    item_elements = []
    if tag_counts:
        most_common = max(tag_counts, key=lambda t: tag_counts[t])
        if tag_counts[most_common] > 1:
            item_elements = root.findall(most_common)
        else:
            for child in root:
                gc_tags = {}
                for gc in child:
                    gc_tags[gc.tag] = gc_tags.get(gc.tag, 0) + 1
                if gc_tags:
                    deepest = max(gc_tags, key=lambda t: gc_tags[t])
                    item_elements = child.findall(deepest)
                    break
    if not item_elements:
        item_elements = list(root)
    rows = []
    for item in item_elements:
        flat = {}
        for child in item:
            key = normalize_field(child.tag)
            val = (child.text or "").strip()
            if not val and len(child):
                val = ", ".join((gc.text or "").strip() for gc in child if gc.text)
            if val:
                flat[key] = val
        for attr_key, attr_val in item.attrib.items():
            key = normalize_field(attr_key)
            if key not in flat:
                flat[key] = attr_val.strip()
        if flat:
            rows.append(flat)
    return rows


# ---------------------------------------------------------------------------
# Routes: import
# ---------------------------------------------------------------------------

@app.route("/api/import/preview", methods=["POST"])
def import_preview():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f       = request.files["file"]
    content = f.read()
    fname   = (f.filename or "").lower()
    try:
        rows = parse_xml(content) if fname.endswith(".xml") else parse_csv(content)
    except Exception as e:
        return jsonify({"error": f"Parse error: {str(e)}"}), 400
    if not rows:
        return jsonify({"error": "No rows found in file"}), 400
    detected = list(rows[0].keys())
    mapped   = [c for c in detected if c in KNOWN_FIELDS]
    unknown  = [c for c in detected if c not in KNOWN_FIELDS and c != "_skip"]
    return jsonify({
        "total_rows":       len(rows),
        "preview":          rows[:10],
        "detected_columns": detected,
        "mapped_columns":   mapped,
        "unknown_columns":  unknown,
    })


@app.route("/api/import", methods=["POST"])
def import_movies():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f             = request.files["file"]
    content       = f.read()
    fname         = (f.filename or "").lower()
    mode          = request.form.get("mode", "skip")
    fetch_posters = request.form.get("fetch_posters", "true").lower() == "true"
    enrich        = request.form.get("enrich", "true").lower() == "true"
    import_id     = (request.form.get("import_id", "") or "").strip() or uuid.uuid4().hex

    try:
        rows = parse_xml(content) if fname.endswith(".xml") else parse_csv(content)
    except Exception as e:
        return jsonify({"error": f"Parse error: {str(e)}"}), 400
    if not rows:
        return jsonify({"error": "No rows found in file"}), 400

    _set_import_cancel(import_id, False)

    # Capture owner_id before entering generator (request context won't be available)
    import_owner_id = _get_current_user_id()

    add_log("import", f"Import gestart: {f.filename}", f"Rijen: {len(rows)}, Modus: {mode}, Enrich: {enrich}, Posters: {fetch_posters}")

    def stream_import():
        conn = get_db()
        added = updated = skipped = errors = 0
        error_details = []
        total = len(rows)
        cancelled = False

        try:
            for i, row in enumerate(rows):
                if _is_import_cancelled(import_id):
                    cancelled = True
                    add_log(
                        "import",
                        f"Import afgebroken door gebruiker: {f.filename}",
                        f"Na {added+updated+skipped+errors}/{total} rijen",
                        "warn"
                    )
                    break
                try:
                    if enrich:
                        row = enrich_from_api(row)
                    result = insert_row(conn, row, mode, fetch_posters=fetch_posters, owner_id=import_owner_id)
                    if result == "added":
                        added += 1
                    elif result == "updated":
                        updated += 1
                    else:
                        skipped += 1
                except Exception as e:
                    errors += 1
                    detail_msg = f"Row {i+1} ({row.get('title', '?')}): {str(e)}"
                    error_details.append(detail_msg)

                # Emit progress every row (or batch for large imports)
                if (i + 1) % max(1, total // 100) == 0 or i == total - 1:
                    pct = int(((i + 1) / total) * 100)
                    yield json.dumps({
                        "type": "progress",
                        "current": i + 1,
                        "total": total,
                        "percent": pct,
                        "added": added,
                        "updated": updated,
                        "skipped": skipped,
                        "errors": errors
                    }) + "\n"

                if (i + 1) % 10 == 0:
                    conn.commit()

        except Exception as e:
            add_log("import", f"Import afgebroken na {added+updated+skipped+errors}/{len(rows)} rijen",
                    str(e), "error")
            error_details.append(f"Import aborted: {str(e)}")
            errors += 1

        conn.commit()
        conn.close()

        # Final summary
        if cancelled:
            summary = f"Afgebroken: {added} toegevoegd, {updated} bijgewerkt, {skipped} overgeslagen, {errors} fouten"
            add_log("import", f"Import afgebroken: {f.filename}", summary, "warn")
        else:
            summary = f"Klaar: {added} toegevoegd, {updated} bijgewerkt, {skipped} overgeslagen, {errors} fouten"
            add_log("import", f"Import voltooid: {f.filename}", summary, "success" if errors == 0 else "warn")
            if import_owner_id:
                push_to_user(
                    import_owner_id,
                    "Import voltooid 🎬",
                    f"{added} toegevoegd, {updated} bijgewerkt — {f.filename}",
                    "/",
                )

        yield json.dumps({
            "type": "done",
            "total": total,
            "added": added,
            "updated": updated,
            "skipped": skipped,
            "errors": errors,
            "error_details": error_details[:20],
            "cancelled": cancelled,
        }) + "\n"

        _clear_import_cancel(import_id)

    return Response(stream_import(), mimetype="application/json")


@app.route("/api/import/cancel/<import_id>", methods=["POST"])
def cancel_import(import_id):
    _set_import_cancel(import_id, True)
    add_log("import", "Import stopverzoek ontvangen", f"Import-ID: {import_id}", "warn")
    return jsonify({"status": "cancelling", "import_id": import_id})


# ---------------------------------------------------------------------------
# Auth: JWT middleware
# ---------------------------------------------------------------------------

# In-DB challenge store (works across Gunicorn workers)
def _store_challenge(key: str, challenge: bytes):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS challenges (key TEXT PRIMARY KEY, challenge BLOB, created_at REAL)")
    # Clean up challenges older than 5 minutes
    conn.execute("DELETE FROM challenges WHERE created_at < ?", (time.time() - 300,))
    conn.execute("INSERT OR REPLACE INTO challenges (key, challenge, created_at) VALUES (?,?,?)",
                 (key, challenge, time.time()))
    conn.commit()
    conn.close()

def _pop_challenge(key: str) -> bytes | None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS challenges (key TEXT PRIMARY KEY, challenge BLOB, created_at REAL)")
    row = conn.execute("SELECT challenge FROM challenges WHERE key = ?", (key,)).fetchone()
    if row:
        conn.execute("DELETE FROM challenges WHERE key = ?", (key,))
        conn.commit()
        conn.close()
        return row[0]
    conn.close()
    return None

def _is_auth_enabled() -> bool:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT value FROM settings WHERE key='auth_enabled'").fetchone()
    conn.close()
    return row and row[0] == "true"


def _get_current_user_id() -> str | None:
    """Return current user id from g (set by check_auth), or None if auth disabled."""
    return getattr(g, "current_user_id", None)


def _get_current_user_role() -> str:
    """Return 'admin' or 'user' for the current authenticated user."""
    uid = _get_current_user_id()
    if not uid:
        return "admin"  # Auth disabled → treat as admin
    conn = get_db()
    row = conn.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return (row["role"] if row else "user")


def _movie_owner_filter() -> tuple[str, list]:
    """Return SQL WHERE clause + params to scope movies to current user + their groups."""
    uid = _get_current_user_id()
    if not uid:
        return "", []  # Auth disabled → show all
    conn = get_db()
    group_rows = conn.execute("SELECT group_id FROM user_groups WHERE user_id=?", (uid,)).fetchall()
    conn.close()
    group_ids = [r["group_id"] for r in group_rows]
    if group_ids:
        placeholders = ",".join("?" * len(group_ids))
        return (f" AND (movies.owner_id = ? OR movies.id IN (SELECT movie_id FROM movie_groups WHERE group_id IN ({placeholders})))",
                [uid] + group_ids)
    return " AND movies.owner_id = ?", [uid]


def _check_movie_owner(movie_row) -> bool:
    """Return True if current user may modify this movie (owner or admin)."""
    uid = _get_current_user_id()
    if not uid:
        return True  # Auth disabled
    if _get_current_user_role() == "admin":
        return True
    return movie_row["owner_id"] == uid


def _get_container_sibling_ids(conn, movie_id):
    """Return the set of movie ids that share a container (vault / box-set /
    collection) with the given movie. The returned set includes movie_id itself
    so callers can apply group changes uniformly across the whole container.

    A "container" here is any of:
      - vault       = edition_group (movies sharing the same edition_group_id)
      - box-set     = parent edition_group (parent_group_id of a vault, or
                      directly via movies.super_group_id)
      - collection  = a collection (linked via movies.collection_id, via
                      edition_groups.collection_id, or transitively via the
                      box-set's collection_id)
    """
    sibling_ids = {int(movie_id)}
    row = conn.execute(
        "SELECT edition_group_id, super_group_id, collection_id FROM movies WHERE id=?",
        (movie_id,)
    ).fetchone()
    if not row:
        return sibling_ids
    eg_id = row["edition_group_id"]
    sg_id = row["super_group_id"]
    col_id = row["collection_id"]

    parent_gid = None
    eg_col_id = None
    if eg_id:
        eg = conn.execute(
            "SELECT parent_group_id, collection_id FROM edition_groups WHERE id=?",
            (eg_id,)
        ).fetchone()
        if eg:
            parent_gid = eg["parent_group_id"]
            eg_col_id = eg["collection_id"]

    boxset_id = parent_gid or sg_id
    boxset_col_id = None
    if boxset_id:
        eg = conn.execute(
            "SELECT collection_id FROM edition_groups WHERE id=?",
            (boxset_id,)
        ).fetchone()
        if eg:
            boxset_col_id = eg["collection_id"]

    effective_col = col_id or eg_col_id or boxset_col_id

    # 1) Vault siblings
    if eg_id:
        for r in conn.execute(
            "SELECT id FROM movies WHERE edition_group_id=?", (eg_id,)
        ).fetchall():
            sibling_ids.add(r["id"])

    # 2) Box-set siblings (films in any child vault + films directly linked via super_group_id)
    if boxset_id:
        for r in conn.execute(
            "SELECT id FROM movies WHERE edition_group_id IN "
            "(SELECT id FROM edition_groups WHERE parent_group_id=?)",
            (boxset_id,)
        ).fetchall():
            sibling_ids.add(r["id"])
        for r in conn.execute(
            "SELECT id FROM movies WHERE super_group_id=?", (boxset_id,)
        ).fetchall():
            sibling_ids.add(r["id"])

    # 3) Collection siblings (direct, via vault, via box-set)
    if effective_col:
        for r in conn.execute(
            "SELECT id FROM movies WHERE collection_id=?", (effective_col,)
        ).fetchall():
            sibling_ids.add(r["id"])
        for r in conn.execute(
            "SELECT id FROM movies WHERE edition_group_id IN "
            "(SELECT id FROM edition_groups WHERE collection_id=?)",
            (effective_col,)
        ).fetchall():
            sibling_ids.add(r["id"])
        for r in conn.execute(
            "SELECT m.id FROM movies m "
            "JOIN edition_groups eg ON eg.id = m.edition_group_id "
            "WHERE eg.parent_group_id IN "
            "(SELECT id FROM edition_groups WHERE collection_id=?)",
            (effective_col,)
        ).fetchall():
            sibling_ids.add(r["id"])
        for r in conn.execute(
            "SELECT id FROM movies WHERE super_group_id IN "
            "(SELECT id FROM edition_groups WHERE collection_id=?)",
            (effective_col,)
        ).fetchall():
            sibling_ids.add(r["id"])

    return sibling_ids


def _apply_groups_to_movies(conn, movie_ids, group_ids, *, replace=True,
                            remove_only=False):
    """Apply a group assignment to a set of movies, respecting ownership.

    - replace=True (default): the given group_ids become the full group list
      for each movie (existing memberships are removed first).
    - replace=False, remove_only=False: add the given group_ids on top of
      existing memberships.
    - remove_only=True: remove the given group_ids from each movie.

    Returns the number of movies that were actually touched.
    """
    uid = _get_current_user_id()
    is_admin = _get_current_user_role() == "admin"
    touched = 0
    for mid in movie_ids:
        m = conn.execute("SELECT owner_id FROM movies WHERE id=?", (int(mid),)).fetchone()
        if not m:
            continue
        if uid and m["owner_id"] != uid and not is_admin:
            continue
        if remove_only:
            for gid in group_ids:
                conn.execute(
                    "DELETE FROM movie_groups WHERE movie_id=? AND group_id=?",
                    (int(mid), int(gid))
                )
        else:
            if replace:
                conn.execute("DELETE FROM movie_groups WHERE movie_id=?", (int(mid),))
            for gid in group_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO movie_groups (movie_id, group_id) VALUES (?,?)",
                    (int(mid), int(gid))
                )
        touched += 1
    return touched


def _inherit_groups_from_container_siblings(conn, movie_id):
    """If `movie_id` is part of a container, replace its group memberships with
    the union of the groups its container siblings already have. No-op when
    there are no siblings (i.e. the movie isn't in a container yet)."""
    siblings = _get_container_sibling_ids(conn, movie_id)
    siblings.discard(int(movie_id))
    if not siblings:
        return
    placeholders = ",".join(["?"] * len(siblings))
    rows = conn.execute(
        f"SELECT DISTINCT group_id FROM movie_groups WHERE movie_id IN ({placeholders})",
        tuple(siblings)
    ).fetchall()
    group_ids = [r["group_id"] for r in rows]
    if not group_ids:
        return
    _apply_groups_to_movies(conn, [int(movie_id)], group_ids, replace=True)


def _lock_edition_group_primary(conn, group_id):
    """Pin the current representative movie of an edition_group so that adding
    new members does not change which film's poster is shown for the group.

    Only acts when:
      - the group exists,
      - its `primary_movie_id` is currently NULL,
      - and it already has at least one member.
    The picked member matches the fallback used by the movies aggregator
    (best format first: 4K UHD > Blu-ray > DVD > other, then by sort_title)."""
    if not group_id:
        return
    eg = conn.execute(
        "SELECT primary_movie_id FROM edition_groups WHERE id=?", (group_id,)
    ).fetchone()
    if not eg or eg["primary_movie_id"]:
        return
    members = conn.execute(
        "SELECT id, format FROM movies WHERE edition_group_id=? "
        "ORDER BY COALESCE(NULLIF(sort_title,''), title) ASC",
        (group_id,)
    ).fetchall()
    if not members:
        return
    rank = {"4K UHD": 0, "Blu-ray": 1, "DVD": 2}
    members = sorted(members, key=lambda m: rank.get(m["format"] or "", 99))
    conn.execute(
        "UPDATE edition_groups SET primary_movie_id=? WHERE id=?",
        (members[0]["id"], group_id)
    )


def _is_source_enabled(key: str, default: bool) -> bool:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    if row is None:
        return default
    return str(row[0]).strip().lower() == "true"


def _is_omdb_enabled() -> bool:
    return _is_source_enabled("omdb_enabled", OMDB_ENABLED_DEFAULT)


def _is_tmdb_enabled() -> bool:
    return _is_source_enabled("tmdb_enabled", TMDB_ENABLED_DEFAULT)


def _is_bluray_scrape_enabled() -> bool:
    return _is_source_enabled("bluray_scrape_enabled", BLURAY_SCRAPE_ENABLED_DEFAULT)


def _is_bluraydiscde_scrape_enabled() -> bool:
    return _is_source_enabled("bluraydiscde_scrape_enabled", BLURAYDISCDE_SCRAPE_ENABLED_DEFAULT)


def _is_registration_enabled() -> bool:
    return _is_source_enabled("registration_enabled", True)

def _create_token(user_id: str, username: str) -> str:
    return jwt.encode(
        {"sub": user_id, "usr": username, "exp": datetime.utcnow() + timedelta(hours=24)},
        JWT_SECRET, algorithm="HS256"
    )

def _verify_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except Exception:
        return None

def auth_required(f):
    """Decorator: require valid JWT if auth is enabled."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _is_auth_enabled():
            return f(*args, **kwargs)
        token = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        if not token:
            return jsonify({"error": "Unauthorized", "auth_required": True}), 401
        payload = _verify_token(token)
        if not payload:
            return jsonify({"error": "Token expired or invalid", "auth_required": True}), 401
        return f(*args, **kwargs)
    return decorated

# Public routes that never need auth
PUBLIC_PREFIXES = ["/api/auth/", "/api/health", "/api/posters/", "/api/profiles/", "/api/avatars/", "/api/debug/"]

@app.before_request
def check_auth():
    """Global auth check: protect all /api/ routes except public ones."""
    if not request.path.startswith("/api/"):
        return
    if request.method == "OPTIONS":
        return

    # Always try to extract user info from token (even on public routes)
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]

    is_mcp = token and MCP_API_KEY and token == MCP_API_KEY

    if token and not is_mcp:
        # Try JWT auth first
        payload = _verify_token(token)
        if payload:
            g.current_user_id = payload.get("sub")
            g.current_username = payload.get("usr")
        else:
            # Try personal API key auth (SHA-256 lookup)
            key_hash = hashlib.sha256(token.encode()).hexdigest()
            try:
                conn = get_db()
                row = conn.execute(
                    "SELECT user_id FROM api_keys WHERE key_hash=?", (key_hash,)
                ).fetchone()
                conn.close()
                if row:
                    g.current_user_id = row["user_id"]
                    g.api_key_auth = True
            except Exception:
                pass

    # Public routes: allow without auth
    for prefix in PUBLIC_PREFIXES:
        if request.path.startswith(prefix):
            return

    if not _is_auth_enabled():
        return

    # MCP API key auth (legacy server-wide key)
    if is_mcp:
        if _is_source_enabled("mcp_enabled", True):
            return
        return jsonify({"error": "MCP server is disabled"}), 403

    if not token:
        return jsonify({"error": "Unauthorized", "auth_required": True}), 401
    if not getattr(g, "current_user_id", None):
        return jsonify({"error": "Token expired or invalid", "auth_required": True}), 401


# ---------------------------------------------------------------------------
# Auth: WebAuthn helpers (no external webauthn lib — using cbor2 + cryptography)
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def _b64url_decode(s: str) -> bytes:
    s += "=" * (4 - len(s) % 4)
    return base64.urlsafe_b64decode(s)

def _make_challenge() -> bytes:
    return secrets.token_bytes(32)

def _parse_cose_key(cose_map: dict) -> EllipticCurvePublicKey:
    """Parse COSE ES256 public key (kty=2, alg=-7) to cryptography key."""
    # -2 = x coordinate, -3 = y coordinate
    x = cose_map[-2]
    y = cose_map[-3]
    numbers = EllipticCurvePublicNumbers(
        x=int.from_bytes(x, "big"),
        y=int.from_bytes(y, "big"),
        curve=SECP256R1()
    )
    return numbers.public_key()

def _parse_attestation_object(att_obj_b64: str) -> tuple:
    """Parse attestationObject, return (credential_id, cose_public_key_bytes, sign_count)."""
    raw = _b64url_decode(att_obj_b64)
    att = cbor2.loads(raw)
    auth_data = att["authData"]
    # Parse authenticator data:
    # rp_id_hash(32) | flags(1) | sign_count(4) | [attested_cred_data] | [extensions]
    flags = auth_data[32]
    sign_count = struct.unpack(">I", auth_data[33:37])[0]
    # Attested credential data starts at byte 37
    # aaguid(16) | cred_id_len(2) | cred_id(L) | cose_key(...)
    aaguid = auth_data[37:53]
    cred_id_len = struct.unpack(">H", auth_data[53:55])[0]
    cred_id = auth_data[55:55 + cred_id_len]
    cose_key_bytes = auth_data[55 + cred_id_len:]
    cose_key = cbor2.loads(cose_key_bytes)
    return cred_id, cose_key_bytes, cose_key, sign_count

def _parse_auth_data(auth_data: bytes) -> tuple:
    """Parse authenticator data, return (rp_id_hash, flags, sign_count)."""
    rp_id_hash = auth_data[:32]
    flags = auth_data[32]
    sign_count = struct.unpack(">I", auth_data[33:37])[0]
    return rp_id_hash, flags, sign_count

def _verify_signature(public_key_bytes: bytes, auth_data: bytes, client_data_hash: bytes, signature: bytes):
    """Verify ES256 signature over auth_data + client_data_hash."""
    cose_key = cbor2.loads(public_key_bytes)
    pub_key = _parse_cose_key(cose_key)
    signed_data = auth_data + client_data_hash
    pub_key.verify(signature, signed_data, ECDSA(SHA256()))


# ---------------------------------------------------------------------------
# Auth: Passkey (WebAuthn) endpoints
# ---------------------------------------------------------------------------

@app.route("/.well-known/webauthn")
def webauthn_well_known():
    """Related Origins file (WebAuthn Level 3) — allows passkeys to be shared across all configured origins."""
    response = make_response(json.dumps({"origins": RP_ORIGINS}))
    response.headers["Content-Type"] = "application/json"
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


@app.route("/api/auth/status")
def auth_status():
    conn = get_db()
    enabled = _is_auth_enabled()
    user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    cred_count = conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
    group_count = conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
    conn.close()
    uid = _get_current_user_id()
    role = _get_current_user_role() if uid else None
    return jsonify({
        "auth_enabled": enabled,
        "has_users": user_count > 0,
        "has_credentials": cred_count > 0,
        "rp_id": RP_ID,
        "user_count": user_count,
        "group_count": group_count,
        "role": role,
        "registration_enabled": _is_registration_enabled(),
    })


@app.route("/api/auth/register/options", methods=["POST"])
def register_options():
    data = request.json or {}
    username = (data.get("username") or "admin").strip()
    display_name = data.get("display_name") or username

    conn = get_db()
    user = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()

    # Block registration when disabled (allow first user setup + existing authenticated users)
    has_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0
    caller_uid = _get_current_user_id()
    if has_users and not caller_uid and not _is_registration_enabled():
        invite_code = (data.get("invite_code") or "").replace("-", "").strip().upper()
        if not invite_code:
            conn.close()
            return jsonify({"error": "Registration is disabled"}), 403
        code_hash = hashlib.sha256(invite_code.encode()).hexdigest()
        now = datetime.utcnow().isoformat()
        invite = conn.execute(
            "SELECT id FROM invite_codes WHERE code_hash=? AND username=? AND used_at IS NULL AND expires_at > ?",
            (code_hash, username, now)
        ).fetchone()
        if not invite:
            conn.close()
            return jsonify({"error": "Invalid or expired invite code"}), 403
    if user:
        user_id = user["id"]
    else:
        user_id = uuid.uuid4().hex

    existing = conn.execute("SELECT id FROM credentials WHERE user_id = ?", (user_id,)).fetchall()
    conn.close()

    challenge = _make_challenge()
    _store_challenge(user_id, challenge)

    exclude_creds = [{"type": "public-key", "id": row["id"]} for row in existing]

    options = {
        "rp": {"name": RP_NAME, "id": RP_ID},
        "user": {
            "id": _b64url_encode(user_id.encode()),
            "name": username,
            "displayName": display_name,
        },
        "challenge": _b64url_encode(challenge),
        "pubKeyCredParams": [{"type": "public-key", "alg": -7}],  # ES256
        "timeout": 60000,
        "authenticatorSelection": {
            "residentKey": "preferred",
            "userVerification": "preferred",
        },
        "excludeCredentials": exclude_creds,
        "attestation": "none",
    }

    return jsonify({"user_id": user_id, "username": username, "options": options})


@app.route("/api/auth/register/verify", methods=["POST"])
def register_verify():
    data = request.json or {}
    user_id = data.get("user_id", "")
    username = data.get("username", "admin")
    display_name = data.get("display_name", username)
    credential_name = data.get("credential_name", "Passkey")
    credential = data.get("credential", {})

    # Block registration when disabled (allow first user setup + existing authenticated users)
    conn_check = get_db()
    has_users = conn_check.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0
    existing_user = conn_check.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    caller_uid = _get_current_user_id()
    if has_users and not existing_user and not caller_uid and not _is_registration_enabled():
        invite_code = (data.get("invite_code") or "").replace("-", "").strip().upper()
        if not invite_code:
            conn_check.close()
            return jsonify({"error": "Registration is disabled"}), 403
        code_hash = hashlib.sha256(invite_code.encode()).hexdigest()
        now = datetime.utcnow().isoformat()
        invite = conn_check.execute(
            "SELECT id FROM invite_codes WHERE code_hash=? AND username=? AND used_at IS NULL AND expires_at > ?",
            (code_hash, username, now)
        ).fetchone()
        if not invite:
            conn_check.close()
            return jsonify({"error": "Invalid or expired invite code"}), 403
    conn_check.close()

    challenge = _pop_challenge(user_id)
    if not challenge:
        return jsonify({"error": "No pending challenge"}), 400

    try:
        # Verify clientDataJSON
        client_data_raw = _b64url_decode(credential["response"]["clientDataJSON"])
        client_data = json.loads(client_data_raw)
        if client_data.get("type") != "webauthn.create":
            raise ValueError("Wrong type in clientDataJSON")
        received_challenge = _b64url_decode(client_data["challenge"])
        if received_challenge != challenge:
            raise ValueError("Challenge mismatch")
        incoming_origin = client_data.get("origin", "").rstrip("/")
        if incoming_origin not in RP_ORIGINS:
            raise ValueError(f"Origin not allowed: '{incoming_origin}' not in {RP_ORIGINS}")

        # Parse attestationObject
        cred_id, cose_key_bytes, cose_key, sign_count = _parse_attestation_object(
            credential["response"]["attestationObject"]
        )
        cred_id_b64 = _b64url_encode(cred_id)

    except Exception as e:
        add_log("auth", f"Passkey registratie mislukt voor {username}", str(e), "error")
        return jsonify({"error": f"Verification failed: {str(e)}"}), 400

    conn = get_db()
    existing_user = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    is_first_user = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0 and not existing_user
    recovery_code = None
    if not existing_user:
        role = "admin" if is_first_user else "user"
        # Generate recovery code (8 alphanumeric chars)
        recovery_code = secrets.token_hex(4).upper()  # 8 hex chars
        recovery_hash = hashlib.sha256(recovery_code.encode()).hexdigest()
        conn.execute(
            "INSERT INTO users (id, username, display_name, role, recovery_hash, created_at) VALUES (?,?,?,?,?,?)",
            (user_id, username, display_name, role, recovery_hash, datetime.utcnow().isoformat())
        )
        # Consume invite code if one was used
        invite_code_raw = (data.get("invite_code") or "").replace("-", "").strip().upper()
        if invite_code_raw:
            ic_hash = hashlib.sha256(invite_code_raw.encode()).hexdigest()
            conn.execute(
                "UPDATE invite_codes SET used_at=?, used_by=? WHERE code_hash=? AND username=? AND used_at IS NULL",
                (datetime.utcnow().isoformat(), user_id, ic_hash, username)
            )
    conn.execute(
        "INSERT INTO credentials (id, user_id, public_key, sign_count, credential_name, created_at) VALUES (?,?,?,?,?,?)",
        (cred_id_b64, user_id, cose_key_bytes, sign_count, credential_name,
         datetime.utcnow().isoformat())
    )
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('auth_enabled', 'true')")
    conn.commit()
    conn.close()

    token = _create_token(user_id, username)
    add_log("auth", f"Passkey geregistreerd voor {username}", f"Credential: {credential_name}", "success")
    result = {"status": "ok", "token": token}
    if recovery_code:
        result["recovery_code"] = recovery_code
    return jsonify(result)


@app.route("/api/auth/login/options", methods=["POST"])
def login_options():
    conn = get_db()
    creds = conn.execute("SELECT id FROM credentials").fetchall()
    conn.close()

    challenge = _make_challenge()
    _store_challenge("_login", challenge)

    allow_credentials = [{"type": "public-key", "id": row["id"]} for row in creds]

    options = {
        "challenge": _b64url_encode(challenge),
        "timeout": 60000,
        "rpId": RP_ID,
        "allowCredentials": allow_credentials,
        "userVerification": "preferred",
    }
    return jsonify({"options": options})


@app.route("/api/auth/login/verify", methods=["POST"])
def login_verify():
    data = request.json or {}
    credential = data.get("credential", {})
    cred_id_b64 = credential.get("id", "")

    challenge = _pop_challenge("_login")
    if not challenge:
        return jsonify({"error": "No pending challenge"}), 400

    conn = get_db()
    stored = conn.execute("SELECT * FROM credentials WHERE id = ?", (cred_id_b64,)).fetchone()
    if not stored:
        conn.close()
        return jsonify({"error": "Unknown credential"}), 400

    try:
        # Verify clientDataJSON
        client_data_raw = _b64url_decode(credential["response"]["clientDataJSON"])
        client_data = json.loads(client_data_raw)
        if client_data.get("type") != "webauthn.get":
            raise ValueError("Wrong type")
        received_challenge = _b64url_decode(client_data["challenge"])
        if received_challenge != challenge:
            raise ValueError("Challenge mismatch")
        incoming_origin = client_data.get("origin", "").rstrip("/")
        if incoming_origin not in RP_ORIGINS:
            raise ValueError(f"Origin not allowed: '{incoming_origin}' not in {RP_ORIGINS}")

        auth_data = _b64url_decode(credential["response"]["authenticatorData"])
        client_data_hash = hashlib.sha256(client_data_raw).digest()
        signature = _b64url_decode(credential["response"]["signature"])

        # Verify RP ID hash
        expected_rp_hash = hashlib.sha256(RP_ID.encode()).digest()
        if auth_data[:32] != expected_rp_hash:
            raise ValueError("RP ID hash mismatch")

        # Verify signature
        _verify_signature(stored["public_key"], auth_data, client_data_hash, signature)

        _, _, new_sign_count = _parse_auth_data(auth_data)

    except Exception as e:
        conn.close()
        add_log("auth", "Login mislukt", str(e), "error")
        return jsonify({"error": f"Verification failed: {str(e)}"}), 400

    conn.execute("UPDATE credentials SET sign_count = ? WHERE id = ?",
                 (new_sign_count, cred_id_b64))
    conn.commit()

    user = conn.execute("SELECT * FROM users WHERE id = ?", (stored["user_id"],)).fetchone()
    conn.close()

    token = _create_token(user["id"], user["username"])
    add_log("auth", f"Login: {user['username']}", level="success")
    return jsonify({"status": "ok", "token": token, "username": user["username"]})


@app.route("/api/auth/credentials", methods=["GET"])
def list_credentials():
    uid = _get_current_user_id()
    conn = get_db()
    if uid:
        rows = conn.execute("""
            SELECT c.id, c.credential_name, c.created_at, c.sign_count, u.username
            FROM credentials c JOIN users u ON c.user_id = u.id
            WHERE c.user_id = ?
            ORDER BY c.created_at DESC
        """, (uid,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT c.id, c.credential_name, c.created_at, c.sign_count, u.username
            FROM credentials c JOIN users u ON c.user_id = u.id
            ORDER BY c.created_at DESC
        """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/auth/credentials/<cred_id>", methods=["DELETE"])
def delete_credential(cred_id):
    uid = _get_current_user_id()
    conn = get_db()
    if uid and _get_current_user_role() != "admin":
        # Non-admin: only delete own credentials
        cred = conn.execute("SELECT user_id FROM credentials WHERE id = ?", (cred_id,)).fetchone()
        if not cred or cred["user_id"] != uid:
            conn.close()
            return jsonify({"error": "Not authorized"}), 403
    conn.execute("DELETE FROM credentials WHERE id = ?", (cred_id,))
    remaining = conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
    if remaining == 0:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('auth_enabled', 'false')")
    conn.commit()
    conn.close()
    add_log("auth", f"Passkey verwijderd", f"ID: {cred_id[:20]}...", "warn")
    return jsonify({"status": "deleted", "remaining": remaining})


@app.route("/api/auth/toggle", methods=["POST"])
def toggle_auth():
    err = _require_admin()
    if err:
        return err
    data = request.json or {}
    enabled = data.get("enabled", False)
    if enabled:
        conn = get_db()
        creds = conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
        conn.close()
        if creds == 0:
            return jsonify({"error": "Registreer eerst een passkey"}), 400
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('auth_enabled', ?)",
                 ("true" if enabled else "false",))
    conn.commit()
    conn.close()
    add_log("auth", f"Authenticatie {'ingeschakeld' if enabled else 'uitgeschakeld'}", level="info")
    return jsonify({"auth_enabled": enabled})


# ---------------------------------------------------------------------------
# Auth: Admin user management
# ---------------------------------------------------------------------------

def _require_admin():
    """Check if current user is admin. Returns error response or None."""
    if not _is_auth_enabled():
        return None  # Auth disabled → allow
    uid = _get_current_user_id()
    if not uid:
        return jsonify({"error": "Unauthorized", "auth_required": True}), 401
    if _get_current_user_role() != "admin":
        return jsonify({"error": "Admin access required"}), 403
    return None


def _require_group_manager():
    """Check if current user can manage groups (admin or MemberGroups role). Returns error or None."""
    if not _is_auth_enabled():
        return None
    uid = _get_current_user_id()
    if not uid:
        return jsonify({"error": "Unauthorized", "auth_required": True}), 401
    role = _get_current_user_role()
    if role not in ("admin", "MemberGroups"):
        return jsonify({"error": "Group management permission required"}), 403
    return None


@app.route("/api/auth/users", methods=["GET"])
def list_users():
    err = _require_admin()
    if err:
        return err
    conn = get_db()
    rows = conn.execute("""
        SELECT u.id, u.username, u.display_name, u.role, u.created_at,
               COUNT(c.id) as credential_count
        FROM users u LEFT JOIN credentials c ON c.user_id = u.id
        GROUP BY u.id ORDER BY u.created_at ASC
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/auth/users/<user_id>", methods=["DELETE"])
def delete_user(user_id):
    err = _require_admin()
    if err:
        return err
    admin_id = _get_current_user_id()
    if user_id == admin_id:
        return jsonify({"error": "Cannot delete yourself"}), 400
    conn = get_db()
    user = conn.execute("SELECT username, role FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        conn.close()
        return jsonify({"error": "User not found"}), 404
    # Delete user's movies or reassign to admin
    conn.execute("DELETE FROM movies WHERE owner_id=?", (user_id,))
    conn.execute("DELETE FROM credentials WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM user_groups WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    remaining_creds = conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
    if remaining_creds == 0:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('auth_enabled', 'false')")
    conn.commit()
    conn.close()
    add_log("auth", f"Gebruiker {user['username']} verwijderd door admin", level="warn")
    return jsonify({"status": "deleted"})


@app.route("/api/auth/users/<user_id>/role", methods=["PUT"])
def set_user_role(user_id):
    err = _require_admin()
    if err:
        return err
    data = request.json or {}
    new_role = data.get("role", "user")
    if new_role not in ("admin", "user", "MemberGroups"):
        return jsonify({"error": "Invalid role"}), 400
    conn = get_db()
    conn.execute("UPDATE users SET role=? WHERE id=?", (new_role, user_id))
    conn.commit()
    user = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    add_log("auth", f"Rol van {user['username']} gewijzigd naar {new_role}", level="info")
    return jsonify({"status": "ok", "role": new_role})


@app.route("/api/auth/users/<user_id>/reset-passkey", methods=["POST"])
def reset_user_passkey(user_id):
    """Admin: remove all credentials for a user so they can re-register."""
    err = _require_admin()
    if err:
        return err
    conn = get_db()
    user = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        conn.close()
        return jsonify({"error": "User not found"}), 404
    conn.execute("DELETE FROM credentials WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    add_log("auth", f"Passkey reset voor {user['username']} door admin", level="warn")
    return jsonify({"status": "ok"})


@app.route("/api/auth/invite", methods=["POST"])
def create_auth_invite():
    """Admin: create a temporary invite code for a new user."""
    err = _require_admin()
    if err:
        return err
    data = request.json or {}
    username = (data.get("username") or "").strip()
    if not username:
        return jsonify({"error": "Username required"}), 400
    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if existing:
        conn.close()
        return jsonify({"error": "User already exists"}), 409
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw_code = "".join(secrets.choice(alphabet) for _ in range(12))
    code_display = f"{raw_code[:4]}-{raw_code[4:8]}-{raw_code[8:12]}"
    code_hash = hashlib.sha256(raw_code.encode()).hexdigest()
    now = datetime.utcnow()
    expires_at = (now + timedelta(hours=48)).isoformat()
    admin_id = _get_current_user_id() or "system"
    conn.execute(
        "INSERT INTO invite_codes (code_hash, username, created_by, created_at, expires_at) VALUES (?,?,?,?,?)",
        (code_hash, username, admin_id, now.isoformat(), expires_at)
    )
    conn.commit()
    invite_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return jsonify({"id": invite_id, "code": code_display, "username": username, "expires_at": expires_at})


@app.route("/api/auth/invite", methods=["GET"])
def list_auth_invites():
    """Admin: list all invite codes."""
    err = _require_admin()
    if err:
        return err
    conn = get_db()
    rows = conn.execute(
        "SELECT id, username, created_at, expires_at, used_at FROM invite_codes ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return jsonify([
        {"id": r[0], "username": r[1], "created_at": r[2], "expires_at": r[3], "used_at": r[4]}
        for r in rows
    ])


@app.route("/api/auth/invite/<int:invite_id>", methods=["DELETE"])
def delete_auth_invite(invite_id):
    """Admin: revoke an invite code."""
    err = _require_admin()
    if err:
        return err
    conn = get_db()
    conn.execute("DELETE FROM invite_codes WHERE id=?", (invite_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted"})


@app.route("/api/auth/me", methods=["GET"])
def get_current_user():
    uid = _get_current_user_id()
    if not uid:
        return jsonify({"authenticated": False})
    conn = get_db()
    user = conn.execute("SELECT id, username, display_name, role, first_name, last_name, avatar, created_at FROM users WHERE id=?", (uid,)).fetchone()
    if not user:
        conn.close()
        return jsonify({"authenticated": False})
    data = {**dict(user), "authenticated": True}
    if data.get("avatar"):
        data["avatar_url"] = f"/api/avatars/{data['avatar']}"
    # Check if user's group restricts digital features (non-admins only)
    group_hide_digital = False
    if data.get("role") != "admin":
        row = conn.execute("""
            SELECT 1 FROM user_groups ug
            JOIN groups g ON g.id = ug.group_id
            WHERE ug.user_id = ? AND g.hide_digital = 1
            LIMIT 1
        """, (uid,)).fetchone()
        group_hide_digital = row is not None
    data["group_hide_digital"] = group_hide_digital
    # Custom RBAC roles and effective permissions
    custom_role_rows = conn.execute("""
        SELECT cr.name FROM custom_roles cr
        JOIN user_roles ur ON ur.role_id = cr.id
        WHERE ur.user_id = ?
    """, (uid,)).fetchall()
    perm_rows = conn.execute("""
        SELECT DISTINCT rp.permission FROM role_permissions rp
        JOIN user_roles ur ON ur.role_id = rp.role_id
        WHERE ur.user_id = ?
    """, (uid,)).fetchall()
    data["custom_roles"] = [r["name"] for r in custom_role_rows]
    data["permissions"]  = [r["permission"] for r in perm_rows]
    conn.close()
    return jsonify(data)


# ---------------------------------------------------------------------------
# Auth: User profile management
# ---------------------------------------------------------------------------

@app.route("/api/auth/profile", methods=["PUT"])
def update_profile():
    uid = _get_current_user_id()
    if not uid:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not user:
        conn.close()
        return jsonify({"error": "User not found"}), 404

    new_username = (data.get("username") or "").strip()
    first_name = (data.get("first_name") or "").strip() or None
    last_name = (data.get("last_name") or "").strip() or None

    if not new_username:
        conn.close()
        return jsonify({"error": "Username is required"}), 400

    if len(new_username) > 50:
        conn.close()
        return jsonify({"error": "Username too long"}), 400

    # Check uniqueness if username changed
    if new_username != user["username"]:
        existing = conn.execute("SELECT id FROM users WHERE username=? AND id!=?", (new_username, uid)).fetchone()
        if existing:
            conn.close()
            return jsonify({"error": "Username already taken", "field": "username"}), 409

    conn.execute(
        "UPDATE users SET username=?, first_name=?, last_name=? WHERE id=?",
        (new_username, first_name, last_name, uid)
    )
    conn.commit()

    # Re-issue token with new username
    token = _create_token(uid, new_username)
    updated = conn.execute("SELECT id, username, display_name, role, first_name, last_name, avatar, created_at FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    udata = {**dict(updated)}
    if udata.get("avatar"):
        udata["avatar_url"] = f"/api/avatars/{udata['avatar']}"
    add_log("auth", f"Profile updated: {new_username}", level="info")
    return jsonify({"status": "ok", "token": token, **udata})


@app.route("/api/auth/profile/avatar", methods=["POST"])
def upload_avatar():
    uid = _get_current_user_id()
    if not uid:
        return jsonify({"error": "Unauthorized"}), 401

    if "avatar" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files["avatar"]
    if not f.filename:
        return jsonify({"error": "No file selected"}), 400

    # Validate file type
    allowed = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in allowed:
        return jsonify({"error": "File type not allowed"}), 400

    # Limit file size (2MB)
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    if size > 2 * 1024 * 1024:
        return jsonify({"error": "File too large (max 2MB)"}), 400

    # Delete old avatar if exists
    conn = get_db()
    old = conn.execute("SELECT avatar FROM users WHERE id=?", (uid,)).fetchone()
    if old and old["avatar"]:
        old_path = os.path.join(AVATAR_DIR, os.path.basename(old["avatar"]))
        if os.path.isfile(old_path):
            os.remove(old_path)

    filename = f"{uid}_{uuid.uuid4().hex[:8]}{ext}"
    f.save(os.path.join(AVATAR_DIR, filename))

    conn.execute("UPDATE users SET avatar=? WHERE id=?", (filename, uid))
    conn.commit()
    conn.close()

    return jsonify({"status": "ok", "avatar_url": f"/api/avatars/{filename}"})


@app.route("/api/auth/profile/avatar", methods=["DELETE"])
def delete_avatar():
    uid = _get_current_user_id()
    if not uid:
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    old = conn.execute("SELECT avatar FROM users WHERE id=?", (uid,)).fetchone()
    if old and old["avatar"]:
        old_path = os.path.join(AVATAR_DIR, os.path.basename(old["avatar"]))
        if os.path.isfile(old_path):
            os.remove(old_path)
    conn.execute("UPDATE users SET avatar=NULL WHERE id=?", (uid,))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Auth: User preferences (per-user key-value, synced across devices)
# ---------------------------------------------------------------------------

ALLOWED_PREF_KEYS = {
    "lang", "rating_country", "collectors_mode", "group_editions",
    "digital_badges", "digital_badge_filter",
}

@app.route("/api/auth/preferences", methods=["GET"])
def get_user_preferences():
    uid = _get_current_user_id()
    if not uid:
        return jsonify({})
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM user_preferences WHERE user_id=?", (uid,)).fetchall()
    conn.close()
    return jsonify({r["key"]: r["value"] for r in rows})


@app.route("/api/auth/preferences", methods=["PUT"])
def set_user_preferences():
    uid = _get_current_user_id()
    if not uid:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    conn = get_db()
    for key, value in data.items():
        if key not in ALLOWED_PREF_KEYS:
            continue
        val = str(value) if value is not None else None
        conn.execute(
            "INSERT INTO user_preferences (user_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
            (uid, key, val)
        )
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/avatars/<path:filename>")
def serve_avatar(filename):
    safe_name = os.path.basename((filename or "").strip().replace("\\", "/"))
    if not safe_name:
        return jsonify({"error": "Not found"}), 404
    if os.path.isfile(os.path.join(AVATAR_DIR, safe_name)):
        resp = send_from_directory(AVATAR_DIR, safe_name)
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp
    return jsonify({"error": "Not found"}), 404


# ---------------------------------------------------------------------------
# Auth: Recovery code login
# ---------------------------------------------------------------------------

@app.route("/api/auth/recovery", methods=["POST"])
def recovery_login():
    data = request.json or {}
    username = data.get("username", "").strip()
    code = data.get("recovery_code", "").strip().upper()
    if not username or not code:
        return jsonify({"error": "Username and recovery code required"}), 400
    conn = get_db()
    user = conn.execute("SELECT id, username, recovery_hash FROM users WHERE username=?", (username,)).fetchone()
    if not user or not user["recovery_hash"]:
        conn.close()
        add_log("auth", f"Mislukte recovery login voor {username}", level="warn")
        return jsonify({"error": "Invalid username or recovery code"}), 401
    code_hash = hashlib.sha256(code.encode()).hexdigest()
    if code_hash != user["recovery_hash"]:
        conn.close()
        add_log("auth", f"Mislukte recovery login voor {username}", level="warn")
        return jsonify({"error": "Invalid username or recovery code"}), 401
    # Recovery code is one-time use: clear it and generate new one
    new_code = secrets.token_hex(4).upper()
    new_hash = hashlib.sha256(new_code.encode()).hexdigest()
    conn.execute("UPDATE users SET recovery_hash=? WHERE id=?", (new_hash, user["id"]))
    # Delete existing credentials so user must re-register a passkey
    conn.execute("DELETE FROM credentials WHERE user_id=?", (user["id"],))
    conn.commit()
    conn.close()
    token = _create_token(user["id"], user["username"])
    add_log("auth", f"Recovery login voor {username}", level="warn")
    return jsonify({"status": "ok", "token": token, "new_recovery_code": new_code,
                    "message": "Passkeys removed. Please register a new passkey."})


# ---------------------------------------------------------------------------
# Watchlist & Watch History
# ---------------------------------------------------------------------------

@app.route("/api/watchlist", methods=["GET"])
def get_watchlist():
    uid = _get_current_user_id() or "__global__"
    conn = get_db()
    rows = conn.execute("""
        SELECT m.*, 1 as on_watchlist, w.added_at as watchlist_added_at,
               (SELECT MAX(watched_at) FROM watch_history WHERE movie_id=m.id AND user_id=w.user_id) as last_watched
        FROM watchlist w
        JOIN movies m ON m.id = w.movie_id
        WHERE w.user_id = ?
        ORDER BY w.added_at DESC
    """, (uid,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/watchlist/<int:movie_id>", methods=["POST"])
def add_to_watchlist(movie_id):
    uid = _get_current_user_id() or "__global__"
    if _is_auth_enabled() and not uid:
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (user_id, movie_id, added_at) VALUES (?,?,?)",
            (uid, movie_id, datetime.utcnow().isoformat())
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "added"})


@app.route("/api/watchlist/<int:movie_id>", methods=["DELETE"])
def remove_from_watchlist(movie_id):
    uid = _get_current_user_id() or "__global__"
    conn = get_db()
    try:
        conn.execute("DELETE FROM watchlist WHERE user_id=? AND movie_id=?", (uid, movie_id))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "removed"})


@app.route("/api/watchlist/bulk", methods=["POST"])
def bulk_add_watchlist():
    uid = _get_current_user_id() or "__global__"
    data = request.get_json() or {}
    ids = data.get("movie_ids", [])
    now = datetime.utcnow().isoformat()
    conn = get_db()
    try:
        for mid in ids:
            conn.execute(
                "INSERT OR IGNORE INTO watchlist (user_id, movie_id, added_at) VALUES (?,?,?)",
                (uid, mid, now)
            )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "added", "count": len(ids)})


@app.route("/api/watched/<int:movie_id>", methods=["GET"])
def get_watched_entries(movie_id):
    """Return all watch history entries for a movie (newest first)."""
    uid = _get_current_user_id() or "__global__"
    conn = get_db()
    rows = conn.execute(
        "SELECT id, watched_at FROM watch_history WHERE user_id=? AND movie_id=? ORDER BY watched_at DESC, id DESC",
        (uid, movie_id)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/watched/<int:movie_id>", methods=["POST"])
def mark_watched(movie_id):
    uid = _get_current_user_id() or "__global__"
    if _is_auth_enabled() and not uid:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json() or {}
    watched_at = data.get("watched_at", datetime.utcnow().date().isoformat())
    now = datetime.utcnow().isoformat()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO watch_history (user_id, movie_id, watched_at, created_at) VALUES (?,?,?,?)",
            (uid, movie_id, watched_at, now)
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "watched", "watched_at": watched_at})


@app.route("/api/watched/entry/<int:entry_id>", methods=["DELETE"])
def delete_watched_entry(entry_id):
    """Delete a single watch history entry by its ID."""
    uid = _get_current_user_id() or "__global__"
    conn = get_db()
    try:
        conn.execute("DELETE FROM watch_history WHERE id=? AND user_id=?", (entry_id, uid))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "removed"})


@app.route("/api/watch-history", methods=["GET"])
def get_watch_history_page():
    """Return full chronological watch history for current user with movie details."""
    uid = _get_current_user_id() or "__global__"
    conn = get_db()
    rows = conn.execute("""
        SELECT h.id, h.watched_at, h.movie_id,
               m.title, m.year, m.format, m.poster, m.poster_file
        FROM watch_history h
        JOIN movies m ON m.id = h.movie_id
        WHERE h.user_id = ?
        ORDER BY h.watched_at DESC, h.id DESC
    """, (uid,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Groups: shared collections
# ---------------------------------------------------------------------------

@app.route("/api/groups", methods=["GET"])
def list_groups():
    uid = _get_current_user_id()
    conn = get_db()
    if not uid or _get_current_user_role() == "admin":
        # Admin and unauthenticated see all groups
        rows = conn.execute("""
            SELECT g.*, u.username as created_by_username,
                   (SELECT COUNT(*) FROM user_groups WHERE group_id=g.id) as member_count,
                   (SELECT COUNT(*) FROM movie_groups WHERE group_id=g.id) as movie_count,
                   ug.role as my_role
            FROM groups g
            LEFT JOIN users u ON g.created_by=u.id
            LEFT JOIN user_groups ug ON ug.group_id=g.id AND ug.user_id=?
            ORDER BY g.name
        """, (uid or "",)).fetchall()
    else:
        rows = conn.execute("""
            SELECT g.*, u.username as created_by_username,
                   (SELECT COUNT(*) FROM user_groups WHERE group_id=g.id) as member_count,
                   (SELECT COUNT(*) FROM movie_groups WHERE group_id=g.id) as movie_count,
                   ug.role as my_role
            FROM groups g
            JOIN user_groups ug ON ug.group_id=g.id AND ug.user_id=?
            LEFT JOIN users u ON g.created_by=u.id
            ORDER BY g.name
        """, (uid,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/groups", methods=["POST"])
def create_group():
    err = _require_group_manager()
    if err:
        return err
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Group name required"}), 400
    uid = _get_current_user_id()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO groups (name, created_by, created_at) VALUES (?,?,?)",
            (name, uid, datetime.utcnow().isoformat())
        )
        group_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Auto-add creator to group as owner
        if uid:
            conn.execute("INSERT INTO user_groups (user_id, group_id, role) VALUES (?,?,'owner')", (uid, group_id))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "Group name already exists"}), 409
    conn.close()
    add_log("groups", f"Groep '{name}' aangemaakt", level="info")
    return jsonify({"status": "ok", "id": group_id}), 201


@app.route("/api/groups/<int:group_id>", methods=["PUT"])
def update_group(group_id):
    uid = _get_current_user_id()
    if _is_auth_enabled():
        if not uid:
            return jsonify({"error": "Unauthorized"}), 401
        role = _get_current_user_role()
        if role != "admin":
            # Check if user is owner of this group
            conn = get_db()
            ug = conn.execute("SELECT role FROM user_groups WHERE user_id=? AND group_id=?", (uid, group_id)).fetchone()
            conn.close()
            if not ug or ug["role"] != "owner":
                return jsonify({"error": "Not the group owner"}), 403
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Group name required"}), 400
    is_admin = (_get_current_user_role() == "admin") if _is_auth_enabled() else True
    hide_digital = data.get("hide_digital")
    conn = get_db()
    try:
        if hide_digital is not None and is_admin:
            conn.execute("UPDATE groups SET name=?, hide_digital=? WHERE id=?",
                         (name, 1 if hide_digital else 0, group_id))
        else:
            conn.execute("UPDATE groups SET name=? WHERE id=?", (name, group_id))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "Group name already exists"}), 409
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/groups/<int:group_id>", methods=["DELETE"])
def delete_group(group_id):
    uid = _get_current_user_id()
    if _is_auth_enabled():
        if not uid:
            return jsonify({"error": "Unauthorized"}), 401
        role = _get_current_user_role()
        if role != "admin":
            conn = get_db()
            ug = conn.execute("SELECT role FROM user_groups WHERE user_id=? AND group_id=?", (uid, group_id)).fetchone()
            conn.close()
            if not ug or ug["role"] != "owner":
                return jsonify({"error": "Not the group owner"}), 403
    conn = get_db()
    group = conn.execute("SELECT name FROM groups WHERE id=?", (group_id,)).fetchone()
    if not group:
        conn.close()
        return jsonify({"error": "Group not found"}), 404
    # Unlink movies from this group
    conn.execute("DELETE FROM movie_groups WHERE group_id=?", (group_id,))
    conn.execute("DELETE FROM user_groups WHERE group_id=?", (group_id,))
    conn.execute("DELETE FROM group_invites WHERE group_id=?", (group_id,))
    conn.execute("DELETE FROM groups WHERE id=?", (group_id,))
    conn.commit()
    conn.close()
    add_log("groups", f"Group '{group['name']}' deleted", level="warn")
    return jsonify({"status": "deleted"})


@app.route("/api/groups/<int:group_id>/members", methods=["GET"])
def list_group_members(group_id):
    conn = get_db()
    rows = conn.execute("""
        SELECT u.id, u.username, u.display_name, u.role, ug.role as group_role
        FROM users u JOIN user_groups ug ON ug.user_id=u.id
        WHERE ug.group_id=?
        ORDER BY ug.role DESC, u.username
    """, (group_id,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/groups/<int:group_id>/members", methods=["POST"])
def add_group_member(group_id):
    err = _require_admin()
    if err:
        return err
    data = request.json or {}
    user_id = data.get("user_id", "")
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    conn = get_db()
    try:
        conn.execute("INSERT INTO user_groups (user_id, group_id, role) VALUES (?,?,'member')", (user_id, group_id))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "Already a member"}), 409
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/groups/<int:group_id>/members/<member_id>", methods=["DELETE"])
def remove_group_member(group_id, member_id):
    uid = _get_current_user_id()
    if _is_auth_enabled():
        if not uid:
            return jsonify({"error": "Unauthorized"}), 401
        role = _get_current_user_role()
        if role != "admin":
            # Only group owner can remove others; anyone can remove themselves
            if member_id != uid:
                conn = get_db()
                ug = conn.execute("SELECT role FROM user_groups WHERE user_id=? AND group_id=?", (uid, group_id)).fetchone()
                conn.close()
                if not ug or ug["role"] != "owner":
                    return jsonify({"error": "Not the group owner"}), 403
    conn = get_db()
    conn.execute("DELETE FROM user_groups WHERE user_id=? AND group_id=?", (member_id, group_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Group invites (user-driven collaboration)
# ---------------------------------------------------------------------------

@app.route("/api/groups/<int:group_id>/invite", methods=["POST"])
def invite_to_group(group_id):
    """Invite a user by username to join a group. Requires group owner or admin."""
    uid = _get_current_user_id()
    if _is_auth_enabled() and not uid:
        return jsonify({"error": "Unauthorized"}), 401
    # Check caller is owner or admin
    if _is_auth_enabled() and _get_current_user_role() != "admin":
        conn = get_db()
        ug = conn.execute("SELECT role FROM user_groups WHERE user_id=? AND group_id=?", (uid, group_id)).fetchone()
        conn.close()
        if not ug or ug["role"] != "owner":
            return jsonify({"error": "Not the group owner"}), 403
    data = request.json or {}
    username = data.get("username", "").strip()
    if not username:
        return jsonify({"error": "username required"}), 400
    conn = get_db()
    group = conn.execute("SELECT name FROM groups WHERE id=?", (group_id,)).fetchone()
    if not group:
        conn.close()
        return jsonify({"error": "Group not found"}), 404
    invitee = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if not invitee:
        conn.close()
        return jsonify({"error": "User not found"}), 404
    if invitee["id"] == uid:
        conn.close()
        return jsonify({"error": "Cannot invite yourself"}), 400
    # Check if already member
    already = conn.execute("SELECT 1 FROM user_groups WHERE user_id=? AND group_id=?", (invitee["id"], group_id)).fetchone()
    if already:
        conn.close()
        return jsonify({"error": "User is already a member"}), 409
    try:
        conn.execute(
            "INSERT INTO group_invites (group_id, inviter_id, invitee_id, status, created_at) VALUES (?,?,?,'pending',?)",
            (group_id, uid, invitee["id"], datetime.utcnow().isoformat())
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "Invite already sent"}), 409
    conn.close()
    # Push notification to the invitee
    inviter_name = ""
    try:
        c2 = get_db()
        u = c2.execute("SELECT display_name, username FROM users WHERE id=?", (uid,)).fetchone()
        c2.close()
        inviter_name = (u["display_name"] or u["username"]) if u else ""
    except Exception:
        pass
    push_to_user_if_pref(
        invitee["id"],
        "group_invite",
        "Uitnodiging voor groep",
        f"{inviter_name} heeft je uitgenodigd voor '{group['name']}'",
        "/#invites",
    )
    return jsonify({"status": "ok"}), 201


@app.route("/api/invites", methods=["GET"])
def list_invites():
    """Get pending invites for the current user."""
    uid = _get_current_user_id()
    if not uid:
        return jsonify([])
    conn = get_db()
    rows = conn.execute("""
        SELECT gi.id, gi.group_id, gi.status, gi.created_at,
               g.name as group_name,
               u.username as inviter_username, u.display_name as inviter_display_name
        FROM group_invites gi
        JOIN groups g ON g.id=gi.group_id
        JOIN users u ON u.id=gi.inviter_id
        WHERE gi.invitee_id=? AND gi.status='pending'
        ORDER BY gi.created_at DESC
    """, (uid,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/invites/<int:invite_id>/accept", methods=["POST"])
def accept_invite(invite_id):
    uid = _get_current_user_id()
    if not uid:
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    invite = conn.execute("SELECT * FROM group_invites WHERE id=? AND invitee_id=? AND status='pending'", (invite_id, uid)).fetchone()
    if not invite:
        conn.close()
        return jsonify({"error": "Invite not found"}), 404
    try:
        conn.execute("INSERT INTO user_groups (user_id, group_id, role) VALUES (?,?,'member')", (uid, invite["group_id"]))
    except sqlite3.IntegrityError:
        pass  # Already a member
    conn.execute("UPDATE group_invites SET status='accepted' WHERE id=?", (invite_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "accepted"})


@app.route("/api/invites/<int:invite_id>/decline", methods=["POST"])
def decline_invite(invite_id):
    uid = _get_current_user_id()
    if not uid:
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    invite = conn.execute("SELECT id FROM group_invites WHERE id=? AND invitee_id=? AND status='pending'", (invite_id, uid)).fetchone()
    if not invite:
        conn.close()
        return jsonify({"error": "Invite not found"}), 404
    conn.execute("UPDATE group_invites SET status='declined' WHERE id=?", (invite_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "declined"})


# ---------------------------------------------------------------------------
# RBAC: Custom Roles
# ---------------------------------------------------------------------------

ALL_PERMISSIONS = [
    "collection.view", "collection.add", "collection.edit_own",
    "collection.edit_all", "collection.delete_own", "collection.delete_all",
    "collection.import", "groups.create", "groups.manage", "groups.invite",
    "digital.view", "watchlist.manage", "admin.users", "admin.settings",
]


@app.route("/api/roles", methods=["GET"])
def list_roles():
    err = _require_admin()
    if err:
        return err
    conn = get_db()
    roles = conn.execute("SELECT id, name, description, created_at FROM custom_roles ORDER BY name").fetchall()
    result = []
    for r in roles:
        perms = conn.execute("SELECT permission FROM role_permissions WHERE role_id=?", (r["id"],)).fetchall()
        user_count = conn.execute("SELECT COUNT(*) FROM user_roles WHERE role_id=?", (r["id"],)).fetchone()[0]
        result.append({**dict(r), "permissions": [p["permission"] for p in perms], "user_count": user_count})
    conn.close()
    return jsonify(result)


@app.route("/api/roles", methods=["POST"])
def create_role():
    err = _require_admin()
    if err:
        return err
    data = request.json or {}
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip() or None
    permissions = [p for p in (data.get("permissions") or []) if p in ALL_PERMISSIONS]
    if not name:
        return jsonify({"error": "name required"}), 400
    if len(name) > 64:
        return jsonify({"error": "name too long"}), 400
    uid = _get_current_user_id()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO custom_roles (name, description, created_by, created_at) VALUES (?,?,?,?)",
            (name, description, uid, datetime.utcnow().isoformat())
        )
        role_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for p in permissions:
            conn.execute("INSERT INTO role_permissions (role_id, permission) VALUES (?,?)", (role_id, p))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "Role name already exists"}), 409
    conn.close()
    return jsonify({"id": role_id, "status": "created"}), 201


@app.route("/api/roles/<int:role_id>", methods=["PUT"])
def update_role(role_id):
    err = _require_admin()
    if err:
        return err
    data = request.json or {}
    conn = get_db()
    role = conn.execute("SELECT id FROM custom_roles WHERE id=?", (role_id,)).fetchone()
    if not role:
        conn.close()
        return jsonify({"error": "Role not found"}), 404
    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            conn.close()
            return jsonify({"error": "name required"}), 400
        if len(name) > 64:
            conn.close()
            return jsonify({"error": "name too long"}), 400
        try:
            conn.execute("UPDATE custom_roles SET name=? WHERE id=?", (name, role_id))
        except sqlite3.IntegrityError:
            conn.close()
            return jsonify({"error": "Role name already exists"}), 409
    if "description" in data:
        conn.execute("UPDATE custom_roles SET description=? WHERE id=?",
                     ((data["description"] or "").strip() or None, role_id))
    if "permissions" in data:
        permissions = [p for p in (data["permissions"] or []) if p in ALL_PERMISSIONS]
        conn.execute("DELETE FROM role_permissions WHERE role_id=?", (role_id,))
        for p in permissions:
            conn.execute("INSERT INTO role_permissions (role_id, permission) VALUES (?,?)", (role_id, p))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/roles/<int:role_id>", methods=["DELETE"])
def delete_role(role_id):
    err = _require_admin()
    if err:
        return err
    conn = get_db()
    role = conn.execute("SELECT name FROM custom_roles WHERE id=?", (role_id,)).fetchone()
    if not role:
        conn.close()
        return jsonify({"error": "Role not found"}), 404
    conn.execute("DELETE FROM custom_roles WHERE id=?", (role_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted"})


@app.route("/api/roles/<int:role_id>/users", methods=["GET"])
def list_role_users(role_id):
    err = _require_admin()
    if err:
        return err
    conn = get_db()
    rows = conn.execute("""
        SELECT u.id, u.username, u.display_name, u.role
        FROM users u JOIN user_roles ur ON ur.user_id=u.id
        WHERE ur.role_id=?
        ORDER BY u.username
    """, (role_id,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/roles/<int:role_id>/users", methods=["POST"])
def assign_user_role(role_id):
    err = _require_admin()
    if err:
        return err
    data = request.json or {}
    user_id = (data.get("user_id") or "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    conn = get_db()
    if not conn.execute("SELECT 1 FROM custom_roles WHERE id=?", (role_id,)).fetchone():
        conn.close()
        return jsonify({"error": "Role not found"}), 404
    if not conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
        conn.close()
        return jsonify({"error": "User not found"}), 404
    try:
        conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?,?)", (user_id, role_id))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "User already has this role"}), 409
    conn.close()
    return jsonify({"status": "ok"}), 201


@app.route("/api/roles/<int:role_id>/users/<user_id>", methods=["DELETE"])
def revoke_user_role(role_id, user_id):
    err = _require_admin()
    if err:
        return err
    conn = get_db()
    conn.execute("DELETE FROM user_roles WHERE user_id=? AND role_id=?", (user_id, role_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/movies/<int:movie_id>/groups", methods=["GET"])
def get_movie_groups(movie_id):
    conn = get_db()
    rows = conn.execute("""
        SELECT g.id, g.name FROM groups g
        JOIN movie_groups mg ON mg.group_id=g.id
        WHERE mg.movie_id=?
        ORDER BY g.name
    """, (movie_id,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/movies/<int:movie_id>/groups", methods=["PUT"])
def set_movie_groups(movie_id):
    """Set the full list of group_ids for a movie (replaces existing).

    Newly added groups are also added to every other movie that shares a
    container (vault / box-set / collection) with this movie, so adding a
    container to a group propagates to its members. Removals are *not*
    propagated here: an empty/stale list coming from a newly added film must
    not wipe the whole set. Use the bulk DELETE endpoint to remove a group
    from an entire container."""
    data = request.json or {}
    group_ids = [int(g) for g in (data.get("group_ids") or [])]
    conn = get_db()
    movie = conn.execute("SELECT owner_id FROM movies WHERE id=?", (movie_id,)).fetchone()
    if not movie:
        conn.close()
        return jsonify({"error": "Movie not found"}), 404
    uid = _get_current_user_id()
    if uid and movie["owner_id"] != uid and _get_current_user_role() != "admin":
        conn.close()
        return jsonify({"error": "Not your movie"}), 403

    # Capture the diff against the current state of THIS movie so we can
    # propagate only the additions to siblings.
    prev = {r["group_id"] for r in conn.execute(
        "SELECT group_id FROM movie_groups WHERE movie_id=?", (movie_id,)
    ).fetchall()}
    new = set(group_ids)
    added = list(new - prev)

    # Apply the full new list to this movie.
    _apply_groups_to_movies(conn, [movie_id], group_ids, replace=True)

    # Propagate only additions to the container.
    if added:
        siblings = _get_container_sibling_ids(conn, movie_id)
        siblings.discard(int(movie_id))
        if siblings:
            _apply_groups_to_movies(conn, siblings, added, replace=False)

    # Safety net: if siblings already share groups this movie now lacks, pull
    # them in (handles the "new film just joined the container" case where the
    # client posts a stale empty list right after the container linkage was
    # set).
    _inherit_groups_from_container_siblings(conn, movie_id)

    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/movies/bulk/groups", methods=["PUT"])
def bulk_set_movie_groups():
    """Add group(s) to multiple movies at once.

    For every supplied movie the addition is also propagated to its container
    siblings (vault / box-set / collection)."""
    data = request.json or {}
    movie_ids = data.get("movie_ids", [])
    group_ids = data.get("group_ids", [])
    if not movie_ids or not group_ids:
        return jsonify({"error": "movie_ids and group_ids required"}), 400
    conn = get_db()
    expanded = set()
    for mid in movie_ids:
        expanded.update(_get_container_sibling_ids(conn, int(mid)))
    added = _apply_groups_to_movies(conn, expanded, group_ids, replace=False)
    conn.commit()
    conn.close()
    return jsonify({"status": "ok", "updated": added})


@app.route("/api/movies/bulk/groups", methods=["DELETE"])
def bulk_remove_movie_groups():
    """Remove group(s) from multiple movies at once.

    The removal is propagated to every container sibling so the whole vault /
    box-set / collection leaves the group together."""
    data = request.json or {}
    movie_ids = data.get("movie_ids", [])
    group_ids = data.get("group_ids", [])
    if not movie_ids or not group_ids:
        return jsonify({"error": "movie_ids and group_ids required"}), 400
    conn = get_db()
    expanded = set()
    for mid in movie_ids:
        expanded.update(_get_container_sibling_ids(conn, int(mid)))
    removed = _apply_groups_to_movies(conn, expanded, group_ids, remove_only=True)
    conn.commit()
    conn.close()
    return jsonify({"status": "ok", "updated": removed})


@app.route("/api/movies/bulk/collection", methods=["PUT"])
def bulk_assign_collection():
    """Assign multiple movies directly to a collection."""
    data = request.json or {}
    movie_ids = data.get("movie_ids", [])
    col_id = data.get("collection_id")
    if not movie_ids or not col_id:
        return jsonify({"error": "movie_ids and collection_id required"}), 400
    conn = get_db()
    if not conn.execute("SELECT 1 FROM collections WHERE id=?", (col_id,)).fetchone():
        conn.close()
        return jsonify({"error": "Collection not found"}), 404
    for mid in movie_ids:
        conn.execute("UPDATE movies SET collection_id=? WHERE id=?", (col_id, int(mid)))
    conn.commit()
    for mid in movie_ids:
        _inherit_groups_from_container_siblings(conn, int(mid))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/movies/bulk/boxset", methods=["PUT"])
def bulk_assign_boxset():
    """Assign multiple movies as loose members of a Box-Set (via super_group_id)."""
    data = request.json or {}
    movie_ids = data.get("movie_ids", [])
    sg_id = data.get("super_group_id")
    if not movie_ids or not sg_id:
        return jsonify({"error": "movie_ids and super_group_id required"}), 400
    conn = get_db()
    if not conn.execute("SELECT 1 FROM edition_groups WHERE id=?", (sg_id,)).fetchone():
        conn.close()
        return jsonify({"error": "Box-Set not found"}), 404
    for mid in movie_ids:
        conn.execute("UPDATE movies SET super_group_id=? WHERE id=?", (int(sg_id), int(mid)))
    conn.commit()
    for mid in movie_ids:
        _inherit_groups_from_container_siblings(conn, int(mid))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/settings/sources", methods=["GET"])
def get_source_settings():
    return jsonify({
        "omdb_enabled": _is_omdb_enabled(),
        "tmdb_enabled": _is_tmdb_enabled(),
        "bluray_scrape_enabled": _is_bluray_scrape_enabled(),
        "bluraydiscde_scrape_enabled": _is_bluraydiscde_scrape_enabled(),
    })


@app.route("/api/settings/sources", methods=["POST"])
def set_source_settings():
    data = request.json or {}
    source_keys = [
        ("omdb_enabled", "OMDb"),
        ("tmdb_enabled", "TMDb"),
        ("bluray_scrape_enabled", "Blu-ray.com"),
        ("bluraydiscde_scrape_enabled", "bluray-disc.de"),
    ]
    result = {}
    conn = get_db()
    for key, label in source_keys:
        val = bool(data.get(key, False))
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, "true" if val else "false")
        )
        result[key] = val
        add_log("settings", f"{label} bron {'ingeschakeld' if val else 'uitgeschakeld'}", level="info")
    conn.commit()
    conn.close()
    return jsonify(result)


@app.route("/api/settings/mcp", methods=["GET"])
def get_mcp_settings():
    err = _require_admin()
    if err:
        return err
    return jsonify({"mcp_enabled": _is_source_enabled("mcp_enabled", True)})


@app.route("/api/settings/mcp", methods=["POST"])
def set_mcp_settings():
    err = _require_admin()
    if err:
        return err
    data = request.json or {}
    val = bool(data.get("mcp_enabled", True))
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("mcp_enabled", "true" if val else "false")
    )
    conn.commit()
    conn.close()
    add_log("settings", f"MCP server {'enabled' if val else 'disabled'}", level="info")
    return jsonify({"mcp_enabled": val})


@app.route("/api/settings/debug", methods=["GET"])
def get_debug_settings():
    err = _require_admin()
    if err:
        return err
    return jsonify({"debug_enabled": _is_source_enabled("debug_enabled", False)})


@app.route("/api/settings/debug", methods=["POST"])
def set_debug_settings():
    err = _require_admin()
    if err:
        return err
    data = request.json or {}
    val = bool(data.get("debug_enabled", False))
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("debug_enabled", "true" if val else "false")
    )
    conn.commit()
    conn.close()
    add_log("settings", f"Debug modus {'ingeschakeld' if val else 'uitgeschakeld'}", level="info")
    return jsonify({"debug_enabled": val})


@app.route("/api/settings/display", methods=["GET"])
def get_display_settings():
    err = _require_admin()
    if err:
        return err
    return jsonify({
        "show_local_title": _is_source_enabled("show_local_title", True),
        "show_search_button": _is_source_enabled("show_search_button", True),
        "show_auto_videos": _is_source_enabled("show_auto_videos", True),
    })


@app.route("/api/settings/display", methods=["POST"])
def set_display_settings():
    err = _require_admin()
    if err:
        return err
    data = request.json or {}
    val = bool(data.get("show_local_title", True))
    val2 = bool(data.get("show_search_button", True))
    val3 = bool(data.get("show_auto_videos", True))
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("show_local_title", "true" if val else "false")
    )
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("show_search_button", "true" if val2 else "false")
    )
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("show_auto_videos", "true" if val3 else "false")
    )
    conn.commit()
    conn.close()
    return jsonify({"show_local_title": val, "show_search_button": val2, "show_auto_videos": val3})


@app.route("/api/settings/registration", methods=["GET"])
def get_registration_settings():
    err = _require_admin()
    if err:
        return err
    return jsonify({"registration_enabled": _is_registration_enabled()})


@app.route("/api/settings/registration", methods=["POST"])
def set_registration_settings():
    err = _require_admin()
    if err:
        return err
    data = request.json or {}
    val = bool(data.get("registration_enabled", True))
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("registration_enabled", "true" if val else "false")
    )
    conn.commit()
    conn.close()
    add_log("settings", f"Gebruikersregistratie {'ingeschakeld' if val else 'uitgeschakeld'}", level="info")
    return jsonify({"registration_enabled": val})


# ---------------------------------------------------------------------------
# Settings: backup / restore / reset
# ---------------------------------------------------------------------------

@app.route("/api/settings/backup", methods=["POST"])
def create_backup():
    if _is_auth_enabled() and _get_current_user_role() != "admin":
        return jsonify({"error": "Alleen admins kunnen backups maken"}), 403
    ts = local_now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"discvault_backup_{ts}"
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    os.makedirs(backup_path, exist_ok=True)
    try:
        conn = get_db()
        # Export movies
        movies = [dict(r) for r in conn.execute("SELECT * FROM movies").fetchall()]
        # Export movie_groups with group names
        mg_rows = conn.execute("""
            SELECT mg.movie_id, g.name AS group_name
            FROM movie_groups mg JOIN groups g ON mg.group_id = g.id
        """).fetchall()
        movie_groups = [dict(r) for r in mg_rows]
        # Export people
        people = [dict(r) for r in conn.execute("SELECT * FROM people").fetchall()]
        # Export movie_people
        movie_people = [dict(r) for r in conn.execute("SELECT * FROM movie_people").fetchall()]
        conn.close()

        backup_data = {
            "version": 2,
            "created_at": local_now().isoformat(),
            "movies": movies,
            "movie_groups": movie_groups,
            "people": people,
            "movie_people": movie_people,
        }
        with open(os.path.join(backup_path, "backup.json"), "w", encoding="utf-8") as f:
            json.dump(backup_data, f, ensure_ascii=False, default=str)

        if os.path.isdir(POSTER_DIR):
            shutil.copytree(POSTER_DIR, os.path.join(backup_path, "posters"), dirs_exist_ok=True)
        size = sum(
            os.path.getsize(os.path.join(dp, f))
            for dp, _, fn in os.walk(backup_path) for f in fn
        )
        add_log("settings", f"Backup created: {backup_name}",
                f"Grootte: {size // 1024} KB", "success")
        return jsonify({"status": "ok", "name": backup_name, "size": size})
    except Exception as e:
        add_log("settings", "Backup mislukt", str(e), "error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/settings/backups", methods=["GET"])
def list_backups():
    if _is_auth_enabled() and _get_current_user_role() != "admin":
        return jsonify({"error": "Alleen admins kunnen backups bekijken"}), 403
    if not os.path.isdir(BACKUP_DIR):
        return jsonify([])
    backups = []
    for name in sorted(os.listdir(BACKUP_DIR), reverse=True):
        path = os.path.join(BACKUP_DIR, name)
        if os.path.isdir(path):
            size = sum(
                os.path.getsize(os.path.join(dp, f))
                for dp, _, fn in os.walk(path) for f in fn
            )
            has_json = os.path.exists(os.path.join(path, "backup.json"))
            has_db = os.path.exists(os.path.join(path, "discvault.db"))
            poster_count = 0
            poster_dir = os.path.join(path, "posters")
            if os.path.isdir(poster_dir):
                poster_count = len(os.listdir(poster_dir))
            movie_count = 0
            if has_json:
                try:
                    with open(os.path.join(path, "backup.json"), "r", encoding="utf-8") as f:
                        bd = json.load(f)
                    movie_count = len(bd.get("movies", []))
                except Exception:
                    pass
            backups.append({
                "name": name, "size": size,
                "has_db": has_db, "has_json": has_json,
                "poster_count": poster_count, "movie_count": movie_count,
                "format": "v2" if has_json else "v1",
                "created": name.replace("discvault_backup_", "").replace("_", " ")
            })
    return jsonify(backups)


@app.route("/api/settings/backup/<name>/download")
def download_backup(name):
    if _is_auth_enabled() and _get_current_user_role() != "admin":
        return jsonify({"error": "Alleen admins kunnen backups downloaden"}), 403
    safe = re.sub(r'[^a-zA-Z0-9_\-]', '', name)
    path = os.path.join(BACKUP_DIR, safe)
    if not os.path.isdir(path):
        return jsonify({"error": "Backup not found"}), 404
    tar_path = path + ".tar.gz"
    shutil.make_archive(path, "gztar", path)
    return send_file(tar_path, as_attachment=True, download_name=f"{safe}.tar.gz")


@app.route("/api/settings/backup/upload", methods=["POST"])
def upload_and_restore_backup():
    """Upload a .tar.gz backup file, extract, and store for restore."""
    if _is_auth_enabled() and _get_current_user_role() != "admin":
        return jsonify({"error": "Alleen admins kunnen backups uploaden"}), 403
    if "file" not in request.files:
        return jsonify({"error": "Geen bestand ontvangen"}), 400
    f = request.files["file"]
    if not f.filename or not re.search(r'\.(tar\.gz|tgz)$', f.filename, re.IGNORECASE):
        return jsonify({"error": "Alleen .tar.gz bestanden worden geaccepteerd"}), 400
    import tempfile, tarfile
    tmp = None
    try:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz")
        f.save(tmp)
        tmp.close()
        with tarfile.open(tmp.name, "r:gz") as tf:
            members = tf.getnames()
            for m in members:
                if m.startswith("/") or ".." in m:
                    return jsonify({"error": "Ongeldig archief (pad-traversal)"}), 400
            has_json = any(m.endswith("backup.json") for m in members)
            has_db = any(m.endswith("discvault.db") for m in members)
            if not has_json and not has_db:
                return jsonify({"error": "Geen backup.json of discvault.db gevonden in archief"}), 400
            extract_dir = tempfile.mkdtemp()
            tf.extractall(extract_dir)

        # Find backup root (backup.json or discvault.db may be nested)
        backup_root = None
        for root, dirs, files in os.walk(extract_dir):
            if "backup.json" in files or "discvault.db" in files:
                backup_root = root
                break
        if not backup_root:
            shutil.rmtree(extract_dir, ignore_errors=True)
            return jsonify({"error": "Backup data niet gevonden in archief"}), 400

        # Store as a named backup for restore
        ts = local_now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"discvault_upload_{ts}"
        dest = os.path.join(BACKUP_DIR, backup_name)
        shutil.copytree(backup_root, dest)
        shutil.rmtree(extract_dir, ignore_errors=True)

        add_log("settings", f"Backup geüpload: {f.filename} → {backup_name}", level="info")
        return jsonify({"status": "ok", "name": backup_name})
    except tarfile.TarError:
        return jsonify({"error": "Ongeldig of corrupt .tar.gz bestand"}), 400
    except Exception as e:
        add_log("settings", f"Upload mislukt", str(e), "error")
        return jsonify({"error": str(e)}), 500
    finally:
        if tmp and os.path.exists(tmp.name):
            os.unlink(tmp.name)


def _restore_from_json(backup_path, group_mapping=None):
    """Restore movies, posters, people from a v2 JSON backup.

    group_mapping: dict mapping backup group names to actions:
      {"GroupName": {"action": "create"}} or
      {"GroupName": {"action": "assign", "group_id": 5}} or
      {"GroupName": {"action": "skip"}}

    Returns (success_bool, response_data).
    """
    json_path = os.path.join(backup_path, "backup.json")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    movies = data.get("movies", [])
    backup_groups = data.get("movie_groups", [])
    people = data.get("people", [])
    movie_people = data.get("movie_people", [])

    # Collect unique group names from backup
    backup_group_names = list({mg["group_name"] for mg in backup_groups})

    conn = get_db()
    # Check which groups exist
    existing_groups = {}
    for row in conn.execute("SELECT id, name FROM groups").fetchall():
        existing_groups[row["name"]] = row["id"]

    missing_groups = [g for g in backup_group_names if g not in existing_groups]

    # If there are missing groups and no mapping provided, return conflict
    if missing_groups and not group_mapping:
        conn.close()
        return False, {
            "status": "groups_conflict",
            "missing_groups": missing_groups,
            "existing_groups": [{"id": v, "name": k} for k, v in existing_groups.items()],
            "movie_count": len(movies),
        }

    # Build group name → target group_id mapping
    group_name_to_id = dict(existing_groups)
    uid = _get_current_user_id()

    if group_mapping:
        for gname, action_data in group_mapping.items():
            action = action_data.get("action", "skip")
            if action == "create":
                now_str = local_now().isoformat()
                conn.execute(
                    "INSERT OR IGNORE INTO groups (name, created_by, created_at) VALUES (?, ?, ?)",
                    (gname, uid, now_str)
                )
                row = conn.execute("SELECT id FROM groups WHERE name=?", (gname,)).fetchone()
                if row:
                    group_name_to_id[gname] = row["id"]
            elif action == "assign":
                target_id = action_data.get("group_id")
                if target_id:
                    group_name_to_id[gname] = int(target_id)
            # action == "skip": don't add to mapping

    # Clear existing movies and people (full restore)
    conn.execute("DELETE FROM movie_people")
    conn.execute("DELETE FROM movie_groups")
    conn.execute("DELETE FROM movies")
    conn.execute("DELETE FROM people")

    # Current columns in movies and people tables (to safely ignore old/removed columns)
    current_movie_cols = {row[1] for row in conn.execute("PRAGMA table_info(movies)").fetchall()}
    current_people_cols = {row[1] for row in conn.execute("PRAGMA table_info(people)").fetchall()}

    # Build old movie id → new movie id mapping
    old_to_new_movie = {}
    old_to_new_person = {}

    # Restore people
    for p in people:
        old_id = p.pop("id", None)
        # Strip columns not in current schema (e.g. removed columns from old backups)
        p = {k: v for k, v in p.items() if k in current_people_cols}
        cols = [k for k in p.keys()]
        placeholders = ",".join(["?"] * len(cols))
        col_names = ",".join(cols)
        conn.execute(f"INSERT INTO people ({col_names}) VALUES ({placeholders})", list(p.values()))
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        if old_id is not None:
            old_to_new_person[old_id] = new_id

    # Restore movies
    for m in movies:
        old_id = m.pop("id", None)
        # Strip columns not in current schema (e.g. old group_id column moved to movie_groups)
        m = {k: v for k, v in m.items() if k in current_movie_cols}
        # Assign to current user
        m["owner_id"] = uid
        cols = [k for k in m.keys()]
        placeholders = ",".join(["?"] * len(cols))
        col_names = ",".join(cols)
        conn.execute(f"INSERT INTO movies ({col_names}) VALUES ({placeholders})", list(m.values()))
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        if old_id is not None:
            old_to_new_movie[old_id] = new_id

    # Restore movie_groups
    for mg in backup_groups:
        new_movie_id = old_to_new_movie.get(mg["movie_id"])
        target_gid = group_name_to_id.get(mg["group_name"])
        if new_movie_id and target_gid:
            conn.execute(
                "INSERT OR IGNORE INTO movie_groups (movie_id, group_id) VALUES (?, ?)",
                (new_movie_id, target_gid)
            )

    # Restore movie_people
    for mp in movie_people:
        new_movie_id = old_to_new_movie.get(mp.get("movie_id"))
        new_person_id = old_to_new_person.get(mp.get("person_id"))
        if new_movie_id and new_person_id:
            conn.execute(
                "INSERT OR IGNORE INTO movie_people (movie_id, person_id, role, character, job, sort_order) VALUES (?, ?, ?, ?, ?, ?)",
                (new_movie_id, new_person_id, mp.get("role"), mp.get("character"), mp.get("job"), mp.get("sort_order", 0))
            )

    conn.commit()

    # Restore posters
    poster_backup = os.path.join(backup_path, "posters")
    if os.path.isdir(poster_backup):
        if os.path.isdir(POSTER_DIR):
            shutil.rmtree(POSTER_DIR)
        shutil.copytree(poster_backup, POSTER_DIR)

    conn.close()
    return True, {"status": "ok", "movie_count": len(movies)}


@app.route("/api/settings/restore/<name>", methods=["POST"])
def restore_backup(name):
    if _is_auth_enabled() and _get_current_user_role() != "admin":
        return jsonify({"error": "Alleen admins kunnen backups terugzetten"}), 403
    name = re.sub(r'[^a-zA-Z0-9_\-]', '', name)
    backup_path = os.path.join(BACKUP_DIR, name)
    if not os.path.isdir(backup_path):
        return jsonify({"error": "Backup not found"}), 404

    json_path = os.path.join(backup_path, "backup.json")
    db_path = os.path.join(backup_path, "discvault.db")

    # V2 format (JSON)
    if os.path.exists(json_path):
        try:
            body = request.get_json(silent=True) or {}
            group_mapping = body.get("group_mapping")
            ok, result = _restore_from_json(backup_path, group_mapping)
            if not ok:
                return jsonify(result), 409
            add_log("settings", f"Backup hersteld: {name}", level="success")
            return jsonify(result)
        except Exception as e:
            add_log("settings", f"Restore mislukt: {name}", str(e), "error")
            return jsonify({"error": str(e)}), 500

    # V1 legacy format (full DB copy) — keep for backward compatibility
    if os.path.exists(db_path):
        try:
            shutil.copy2(db_path, DB_PATH)
            poster_backup = os.path.join(backup_path, "posters")
            if os.path.isdir(poster_backup):
                if os.path.isdir(POSTER_DIR):
                    shutil.rmtree(POSTER_DIR)
                shutil.copytree(poster_backup, POSTER_DIR)
            init_db()
            add_log("settings", f"Backup hersteld (v1): {name}", level="success")
            return jsonify({"status": "ok"})
        except Exception as e:
            add_log("settings", f"Restore mislukt: {name}", str(e), "error")
            return jsonify({"error": str(e)}), 500

    return jsonify({"error": "No backup data found"}), 404


@app.route("/api/settings/backup/<name>", methods=["DELETE"])
def delete_backup(name):
    if _is_auth_enabled() and _get_current_user_role() != "admin":
        return jsonify({"error": "Alleen admins kunnen backups verwijderen"}), 403
    name = re.sub(r'[^a-zA-Z0-9_\-]', '', name)
    path = os.path.join(BACKUP_DIR, name)
    if os.path.isdir(path):
        shutil.rmtree(path)
    tar = path + ".tar.gz"
    if os.path.exists(tar):
        os.remove(tar)
    return jsonify({"status": "deleted"})


@app.route("/api/settings/reset", methods=["POST"])
def reset_database():
    data = request.json or {}
    confirm = data.get("confirm", "")
    if confirm != "RESET":
        return jsonify({"error": "Bevestig met {\"confirm\": \"RESET\"}"}), 400
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM movies")
        conn.execute("DELETE FROM movie_people")
        conn.execute("DELETE FROM people")
        conn.execute("DELETE FROM logs")
        conn.commit()
        conn.close()
        if os.path.isdir(POSTER_DIR):
            shutil.rmtree(POSTER_DIR)
            os.makedirs(POSTER_DIR, exist_ok=True)
        if os.path.isdir(PROFILE_DIR):
            shutil.rmtree(PROFILE_DIR)
            os.makedirs(PROFILE_DIR, exist_ok=True)
        add_log("settings", "Database gereset: alle films en logs gewist", level="warn")
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/settings/db-stats")
def db_stats():
    size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    poster_count = len(os.listdir(POSTER_DIR)) if os.path.isdir(POSTER_DIR) else 0
    poster_size = sum(
        os.path.getsize(os.path.join(POSTER_DIR, f))
        for f in os.listdir(POSTER_DIR) if os.path.isfile(os.path.join(POSTER_DIR, f))
    ) if os.path.isdir(POSTER_DIR) else 0
    conn = get_db()
    uid = _get_current_user_id()
    if uid:
        movie_count = conn.execute("SELECT COUNT(*) FROM movies WHERE owner_id = ?", (uid,)).fetchone()[0]
    else:
        movie_count = conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
    log_count   = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    conn.close()
    return jsonify({
        "db_size": size, "poster_count": poster_count,
        "poster_size": poster_size, "movie_count": movie_count,
        "log_count": log_count,
    })


# ── Push notifications ────────────────────────────────────────────────────────

def _get_or_create_vapid_keys():
    """Return (public_key_base64url, private_key_raw_b64url) from settings, generating on first call.

    The private key is stored as a base64url-encoded raw 32-byte EC scalar (the 'd' value).
    This is the format py_vapid / pywebpush expect when vapid_private_key is passed as a string.
    """
    from cryptography.hazmat.primitives.asymmetric import ec as _ec
    from cryptography.hazmat.backends import default_backend as _cb
    from cryptography.hazmat.primitives.serialization import Encoding as _Enc, PublicFormat as _PubFmt
    conn = get_db()
    row_pub  = conn.execute("SELECT value FROM settings WHERE key='vapid_public_key'").fetchone()
    row_priv = conn.execute("SELECT value FROM settings WHERE key='vapid_private_key'").fetchone()
    if row_pub and row_priv:
        conn.close()
        return row_pub["value"], row_priv["value"]
    # Generate fresh P-256 VAPID key pair
    priv     = _ec.generate_private_key(_ec.SECP256R1(), _cb())
    pub_bytes = priv.public_key().public_bytes(_Enc.X962, _PubFmt.UncompressedPoint)
    pub_b64   = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()
    # Store private key as raw 32-byte d-value, base64url-encoded (no PEM/DER headers)
    d_bytes  = priv.private_numbers().private_value.to_bytes(32, 'big')
    priv_raw = base64.urlsafe_b64encode(d_bytes).rstrip(b"=").decode()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('vapid_public_key',?)",  (pub_b64,))
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('vapid_private_key',?)", (priv_raw,))
    conn.commit()
    conn.close()
    return pub_b64, priv_raw


def _send_push(endpoint, p256dh, auth, vapid_priv_raw, title, body, url="/"):
    """Send a single Web Push message. Returns None on success, error string on failure."""
    try:
        from pywebpush import webpush, WebPushException

        # Build optional extra headers for Microsoft WNS endpoints.
        # WNS (used by Edge on Windows) requires X-WNS-Type for raw encrypted payloads.
        extra_headers = {}
        if "notify.windows.com" in endpoint or "wns.windows.com" in endpoint:
            extra_headers["X-WNS-Type"] = "wns/raw"
            extra_headers["Content-Type"] = "application/octet-stream"

        webpush(
            subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
            data=json.dumps({"title": title, "body": body, "url": url}),
            vapid_private_key=vapid_priv_raw,  # raw base64url 32-byte d-value
            vapid_claims={"sub": "mailto:no-reply@discvault.app"},
            ttl=86400,        # 24 h — required to be > 0 by WNS and recommended for all services
            headers=extra_headers if extra_headers else None,
        )
        return None
    except Exception as ex:
        status = getattr(getattr(ex, "response", None), "status_code", None)
        err_body = ""
        err_headers = ""
        try:
            resp = getattr(ex, "response", None)
            if resp is not None:
                err_body    = (resp.text or "")[:400]
                err_headers = str(dict(resp.headers))[:200]
        except Exception:
            pass
        ep_hint = endpoint[:60] + "..." if len(endpoint) > 60 else endpoint
        err_msg = f"Push failed (HTTP {status}) [{ep_hint}]: {ex} | body: {err_body} | resp-headers: {err_headers}".strip()
        add_log("push", "Push delivery error", err_msg, level="error")
        if status == 410:
            # Subscription expired/revoked → purge it
            try:
                c = get_db()
                c.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
                c.commit()
                c.close()
            except Exception:
                pass
        return err_msg


def push_to_user(user_id, title, body, url="/"):
    """Deliver a push notification to all subscribed devices for *user_id*. Returns list of errors."""
    errors = []
    try:
        _, priv_raw = _get_or_create_vapid_keys()
        conn = get_db()
        subs = conn.execute(
            "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id=?", (user_id,)
        ).fetchall()
        conn.close()
        if not subs:
            errors.append("No subscriptions found for this user")
        for s in subs:
            err = _send_push(s["endpoint"], s["p256dh"], s["auth"], priv_raw, title, body, url)
            if err:
                errors.append(err)
    except Exception as ex:
        errors.append(str(ex))
        add_log("push", "push_to_user error", str(ex), level="error")
    return errors


@app.route("/api/push/vapid-public-key", methods=["GET"])
def push_vapid_public_key():
    pub, _ = _get_or_create_vapid_keys()
    return jsonify({"publicKey": pub})


@app.route("/api/push/subscribe", methods=["POST"])
def push_subscribe():
    data   = request.get_json() or {}
    endpoint = data.get("endpoint", "")
    keys     = data.get("keys", {})
    p256dh   = keys.get("p256dh", "")
    auth     = keys.get("auth", "")
    if not endpoint or not p256dh or not auth:
        return jsonify({"error": "Missing fields"}), 400
    user_id = _get_current_user_id() or "__global__"
    now     = __import__("datetime").datetime.utcnow().isoformat()
    conn = get_db()
    conn.execute("""
        INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET
            user_id=excluded.user_id, p256dh=excluded.p256dh,
            auth=excluded.auth, created_at=excluded.created_at
    """, (user_id, endpoint, p256dh, auth, now))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/push/subscribe", methods=["DELETE"])
def push_unsubscribe():
    data     = request.get_json() or {}
    endpoint = data.get("endpoint", "")
    if not endpoint:
        return jsonify({"error": "Missing endpoint"}), 400
    conn = get_db()
    conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/push/test", methods=["POST"])
def push_test():
    user_id = _get_current_user_id() or "__global__"
    errors = push_to_user(user_id, "DiscVault", "Push-meldingen werken! 🎬", "/")
    if errors:
        # If every error is a 410 (subscription expired), signal the frontend to re-subscribe.
        all_stale = errors and all("410" in str(e) for e in errors)
        return jsonify({"ok": False, "errors": errors, "stale": all_stale}), 500
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Notification preferences
# ---------------------------------------------------------------------------

# All recognised preference keys with their default value (True = on by default).
_NOTIF_PREF_DEFAULTS = {
    "group_invite": True,
}


def _user_notif_pref_enabled(user_id, pref_key):
    """Return True if the user has the given notification pref enabled (defaults to True)."""
    if pref_key not in _NOTIF_PREF_DEFAULTS:
        return False
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT enabled FROM notification_prefs WHERE user_id=? AND pref_key=?",
            (user_id, pref_key)
        ).fetchone()
        conn.close()
        if row is None:
            return _NOTIF_PREF_DEFAULTS[pref_key]
        return bool(row["enabled"])
    except Exception:
        return _NOTIF_PREF_DEFAULTS.get(pref_key, True)


def push_to_user_if_pref(user_id, pref_key, title, body, url="/"):
    """Send push only if user has the pref enabled (default on). Returns list of errors."""
    if not _user_notif_pref_enabled(user_id, pref_key):
        return []
    return push_to_user(user_id, title, body, url)


@app.route("/api/push/prefs", methods=["GET"])
def push_prefs_get():
    uid = _get_current_user_id()
    if not uid:
        return jsonify({k: v for k, v in _NOTIF_PREF_DEFAULTS.items()})
    conn = get_db()
    rows = conn.execute(
        "SELECT pref_key, enabled FROM notification_prefs WHERE user_id=?", (uid,)
    ).fetchall()
    conn.close()
    prefs = dict(_NOTIF_PREF_DEFAULTS)  # start with defaults
    for r in rows:
        if r["pref_key"] in prefs:
            prefs[r["pref_key"]] = bool(r["enabled"])
    return jsonify(prefs)


@app.route("/api/push/prefs", methods=["PUT"])
def push_prefs_put():
    uid = _get_current_user_id()
    if not uid:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json() or {}
    conn = get_db()
    for key, default in _NOTIF_PREF_DEFAULTS.items():
        if key in data:
            enabled = 1 if data[key] else 0
            conn.execute(
                "INSERT INTO notification_prefs (user_id, pref_key, enabled) VALUES (?,?,?)"
                " ON CONFLICT(user_id, pref_key) DO UPDATE SET enabled=excluded.enabled",
                (uid, key, enabled)
            )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Personal API keys (for MCP / external integrations)
# ---------------------------------------------------------------------------

@app.route("/api/user/api-keys", methods=["GET"])
def list_api_keys():
    uid = _get_current_user_id()
    if not uid:
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    rows = conn.execute(
        "SELECT id, label, created_at FROM api_keys WHERE user_id=? ORDER BY created_at DESC",
        (uid,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/user/api-keys", methods=["POST"])
def create_api_key():
    uid = _get_current_user_id()
    if not uid:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json() or {}
    label = (data.get("label") or "").strip()[:80] or None
    plaintext = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    conn = get_db()
    conn.execute(
        "INSERT INTO api_keys (user_id, key_hash, label, created_at) VALUES (?,?,?,?)",
        (uid, key_hash, label, datetime.utcnow().isoformat())
    )
    conn.commit()
    row = conn.execute(
        "SELECT id, label, created_at FROM api_keys WHERE key_hash=?", (key_hash,)
    ).fetchone()
    conn.close()
    return jsonify({"id": row["id"], "label": row["label"],
                    "created_at": row["created_at"], "key": plaintext}), 201


@app.route("/api/user/api-keys/<int:key_id>", methods=["DELETE"])
def delete_api_key(key_id):
    uid = _get_current_user_id()
    if not uid:
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    result = conn.execute(
        "DELETE FROM api_keys WHERE id=? AND user_id=?", (key_id, uid)
    )
    conn.commit()
    conn.close()
    if result.rowcount == 0:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/mcp/log", methods=["POST"])
def mcp_log():
    """Called by the MCP server after each tool invocation to write a user-scoped log entry."""
    # uid may be None when using the legacy MCP_API_KEY or when auth is disabled.
    # check_auth() has already validated the token, so just log with whatever uid we have.
    uid = _get_current_user_id()
    data = request.get_json() or {}
    tool    = (data.get("tool") or "unknown")[:80]
    detail  = (data.get("detail") or "")[:500]
    level   = data.get("level", "info")
    if level not in ("info", "warn", "error", "success"):
        level = "info"
    add_log("mcp", f"Tool: {tool}", detail, level=level, user_id=uid)
    return jsonify({"ok": True})


@app.route("/api/user/mcp-logs", methods=["GET"])
def user_mcp_logs():
    """Return the current user's MCP activity log (category='mcp', scoped to user_id)."""
    uid = _get_current_user_id()
    if not uid:
        return jsonify({"error": "Unauthorized"}), 401
    limit = min(int(request.args.get("limit", "100")), 500)
    conn = get_db()
    rows = conn.execute(
        "SELECT id, timestamp, level, message, detail FROM logs"
        " WHERE category='mcp' AND user_id=? ORDER BY id DESC LIMIT ?",
        (uid, limit)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Routes: Collections
# ---------------------------------------------------------------------------

@app.route("/api/collections", methods=["GET"])
def list_collections():
    q = (request.args.get("q") or "").strip()
    conn = get_db()
    col_sql = """
            SELECT c.*,
                   (SELECT COUNT(*) FROM edition_groups eg WHERE eg.collection_id = c.id) AS group_count,
                   (SELECT COUNT(*) FROM movies m WHERE m.collection_id = c.id) AS loose_movie_count,
                   (SELECT COUNT(*) FROM movies m WHERE m.edition_group_id IN (
                       SELECT id FROM edition_groups WHERE collection_id = c.id
                       UNION ALL
                       SELECT eg2.id FROM edition_groups eg2
                       WHERE eg2.parent_group_id IN (SELECT id FROM edition_groups WHERE collection_id = c.id)
                   )) AS eg_movie_count,
                   (SELECT COUNT(*) FROM movies m WHERE m.super_group_id IN (
                       SELECT id FROM edition_groups WHERE collection_id = c.id
                   )) AS boxset_loose_count
            FROM collections c
    """
    if q:
        rows = conn.execute(col_sql + " WHERE c.title LIKE ? ORDER BY c.title ASC", (f"%{q}%",)).fetchall()
    else:
        rows = conn.execute(col_sql + " ORDER BY c.title ASC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/collections", methods=["POST"])
def create_collection():
    data = request.json or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400
    conn = get_db()
    conn.execute(
        "INSERT INTO collections (title, badge_label, created_at) VALUES (?,?,?)",
        (title, data.get("badge_label"), datetime.utcnow().isoformat())
    )
    conn.commit()
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    row = conn.execute("SELECT * FROM collections WHERE id=?", (cid,)).fetchone()
    conn.close()
    return jsonify(dict(row)), 201


@app.route("/api/collections/<int:col_id>", methods=["GET"])
def get_collection(col_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM collections WHERE id=?", (col_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Not found"}), 404
    # Member edition_groups
    egs = conn.execute(
        "SELECT id, title, badge_label, parent_group_id FROM edition_groups WHERE collection_id=? ORDER BY title ASC",
        (col_id,)
    ).fetchall()
    # Loose movies directly linked to this collection
    loose = conn.execute(
        "SELECT id, title, year, poster_file, poster, backdrop, backdrops, trailer_url, videos FROM movies WHERE collection_id=? ORDER BY title ASC",
        (col_id,)
    ).fetchall()
    # Also gather backdrops from all movies linked via edition_groups
    eg_ids = [eg["id"] for eg in egs]
    eg_movies = []
    if eg_ids:
        placeholders = ",".join("?" * len(eg_ids))
        # Include movies in:
        #  - direct edition_groups of this collection
        #  - child vaults whose parent_group_id is one of those edition_groups
        #  - loose box-set movies linked via super_group_id
        eg_movies = conn.execute(
            f"SELECT DISTINCT id, title, year, poster_file, poster, backdrop, backdrops, trailer_url, videos"
            f" FROM movies"
            f" WHERE edition_group_id IN ({placeholders})"
            f"    OR edition_group_id IN ("
            f"        SELECT id FROM edition_groups WHERE parent_group_id IN ({placeholders})"
            f"    )"
            f"    OR super_group_id IN ({placeholders})"
            f" ORDER BY title ASC",
            eg_ids + eg_ids + eg_ids
        ).fetchall()
    conn.close()
    result = dict(row)
    result["edition_groups"] = [dict(eg) for eg in egs]
    result["loose_movies"] = [dict(m) for m in loose]
    result["eg_movies"] = [dict(m) for m in eg_movies]
    return jsonify(result)


@app.route("/api/collections/<int:col_id>", methods=["PUT"])
def update_collection(col_id):
    data = request.json or {}
    conn = get_db()
    row = conn.execute("SELECT * FROM collections WHERE id=?", (col_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Not found"}), 404
    fields = {}
    if "title" in data:
        fields["title"] = (data["title"] or "").strip()
    if "badge_label" in data:
        fields["badge_label"] = data.get("badge_label")
    if "backdrop" in data:
        fields["backdrop"] = data.get("backdrop")
    if "description" in data:
        fields["description"] = data.get("description")
    if fields:
        set_clause = ", ".join(f"{k}=?" for k in fields)
        conn.execute(
            f"UPDATE collections SET {set_clause} WHERE id=?",
            list(fields.values()) + [col_id]
        )
        conn.commit()
    updated = conn.execute("SELECT * FROM collections WHERE id=?", (col_id,)).fetchone()
    conn.close()
    return jsonify(dict(updated))


@app.route("/api/collections/<int:col_id>", methods=["DELETE"])
def delete_collection(col_id):
    conn = get_db()
    # Unlink all members
    conn.execute("UPDATE edition_groups SET collection_id=NULL WHERE collection_id=?", (col_id,))
    conn.execute("UPDATE movies SET collection_id=NULL WHERE collection_id=?", (col_id,))
    conn.execute("DELETE FROM collections WHERE id=?", (col_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Routes: Edition Groups
# ---------------------------------------------------------------------------

@app.route("/api/edition-groups", methods=["GET"])
def list_edition_groups():
    q = (request.args.get("q") or "").strip()
    conn = get_db()
    if q:
        rows = conn.execute("""
            SELECT eg.*,
                   COUNT(DISTINCT m.id) AS member_count,
                   (SELECT COUNT(*) FROM edition_groups eg2 WHERE eg2.parent_group_id = eg.id) AS child_group_count,
                   (SELECT COUNT(*) FROM movies m2 WHERE m2.super_group_id = eg.id) AS loose_movie_count,
                   (SELECT COUNT(*) FROM movies m3 JOIN edition_groups eg3 ON m3.edition_group_id = eg3.id WHERE eg3.parent_group_id = eg.id) AS child_member_count
            FROM edition_groups eg
            LEFT JOIN movies m ON m.edition_group_id = eg.id
            WHERE eg.title LIKE ?
            GROUP BY eg.id
            ORDER BY eg.title ASC
        """, (f"%{q}%",)).fetchall()
    else:
        rows = conn.execute("""
            SELECT eg.*,
                   COUNT(DISTINCT m.id) AS member_count,
                   (SELECT COUNT(*) FROM edition_groups eg2 WHERE eg2.parent_group_id = eg.id) AS child_group_count,
                   (SELECT COUNT(*) FROM movies m2 WHERE m2.super_group_id = eg.id) AS loose_movie_count,
                   (SELECT COUNT(*) FROM movies m3 JOIN edition_groups eg3 ON m3.edition_group_id = eg3.id WHERE eg3.parent_group_id = eg.id) AS child_member_count
            FROM edition_groups eg
            LEFT JOIN movies m ON m.edition_group_id = eg.id
            GROUP BY eg.id
            ORDER BY eg.title ASC
        """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/edition-groups", methods=["POST"])
def create_edition_group():
    data = request.json or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400
    conn = get_db()
    group_type = data.get("group_type") or "vault"
    if group_type not in ("vault", "boxset"):
        group_type = "vault"
    conn.execute(
        "INSERT INTO edition_groups (title, tmdb_id, imdb_id, year, created_at, group_type) VALUES (?,?,?,?,?,?)",
        (title, data.get("tmdb_id") or "", data.get("imdb_id") or "",
         data.get("year") or "", datetime.utcnow().isoformat(), group_type)
    )
    conn.commit()
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    row = conn.execute("SELECT * FROM edition_groups WHERE id=?", (gid,)).fetchone()
    conn.close()
    return jsonify(dict(row)), 201


@app.route("/api/edition-groups/<int:group_id>", methods=["GET"])
def get_edition_group(group_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM edition_groups WHERE id=?", (group_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Not found"}), 404
    members = conn.execute(
        "SELECT id, title, edition_type, format, year, poster_file, poster, backdrop, backdrops, trailer_url, videos FROM movies"
        " WHERE edition_group_id=? ORDER BY format ASC",
        (group_id,)
    ).fetchall()
    loose = conn.execute(
        "SELECT id, title, edition_type, format, year, poster_file, poster, backdrop, backdrops, trailer_url, videos FROM movies"
        " WHERE super_group_id=? ORDER BY title ASC",
        (group_id,)
    ).fetchall()
    child_groups = conn.execute(
        "SELECT id, title, badge_label FROM edition_groups WHERE parent_group_id=? ORDER BY title ASC",
        (group_id,)
    ).fetchall()
    conn.close()
    result = dict(row)
    result["members"] = [dict(m) for m in members]
    result["loose_movies"] = [dict(m) for m in loose]
    result["child_groups"] = [dict(g) for g in child_groups]
    return jsonify(result)


@app.route("/api/edition-groups/<int:group_id>", methods=["PUT"])
def update_edition_group(group_id):
    data = request.json or {}
    conn = get_db()
    row = conn.execute("SELECT * FROM edition_groups WHERE id=?", (group_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Not found"}), 404
    fields = {}
    if "title" in data:
        fields["title"] = (data["title"] or "").strip()
    if "primary_movie_id" in data:
        fields["primary_movie_id"] = data["primary_movie_id"]
    if "tmdb_id" in data:
        fields["tmdb_id"] = data["tmdb_id"]
    if "imdb_id" in data:
        fields["imdb_id"] = data["imdb_id"]
    if "year" in data:
        fields["year"] = data["year"]
    if "badge_label" in data:
        fields["badge_label"] = data.get("badge_label")
    if "parent_group_id" in data:
        fields["parent_group_id"] = data.get("parent_group_id")
    if "collection_id" in data:
        fields["collection_id"] = data.get("collection_id")
    if "backdrop" in data:
        fields["backdrop"] = data.get("backdrop")
    if "description" in data:
        fields["description"] = data.get("description")
    if fields:
        set_clause = ", ".join(f"{k}=?" for k in fields)
        conn.execute(
            f"UPDATE edition_groups SET {set_clause} WHERE id=?",
            list(fields.values()) + [group_id]
        )
        conn.commit()
    updated = conn.execute("SELECT * FROM edition_groups WHERE id=?", (group_id,)).fetchone()
    conn.close()
    return jsonify(dict(updated))


@app.route("/api/edition-groups/<int:group_id>", methods=["DELETE"])
def delete_edition_group(group_id):
    conn = get_db()
    conn.execute("UPDATE movies SET edition_group_id=NULL WHERE edition_group_id=?", (group_id,))
    conn.execute("DELETE FROM edition_groups WHERE id=?", (group_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


def _container_image_replace(conn, table, row_id, column, new_filename):
    """Update a poster/backdrop column on a container, removing the previous
    local file from disk if it was a local upload."""
    row = conn.execute(f"SELECT {column} FROM {table} WHERE id=?", (row_id,)).fetchone()
    if not row:
        return False
    old = (row[column] or "").strip() if row[column] else ""
    if old and not old.startswith(("http://", "https://")):
        try:
            os.remove(os.path.join(POSTER_DIR, os.path.basename(old)))
        except OSError:
            pass
    conn.execute(f"UPDATE {table} SET {column}=? WHERE id=?", (new_filename, row_id))
    return True


@app.route("/api/edition-groups/<int:group_id>/poster", methods=["POST"])
def upload_edition_group_poster(group_id):
    if "poster" not in request.files:
        return jsonify({"error": "Geen posterbestand meegegeven"}), 400
    conn = get_db()
    if not conn.execute("SELECT 1 FROM edition_groups WHERE id=?", (group_id,)).fetchone():
        conn.close()
        return jsonify({"error": "Group not found"}), 404
    new_file, err = save_uploaded_poster(request.files["poster"])
    if err:
        conn.close()
        return jsonify({"error": err}), 400
    _container_image_replace(conn, "edition_groups", group_id, "poster_file", new_file)
    conn.commit()
    conn.close()
    return jsonify({"status": "updated", "poster_file": new_file})


@app.route("/api/edition-groups/<int:group_id>/poster", methods=["DELETE"])
def clear_edition_group_poster(group_id):
    conn = get_db()
    if not conn.execute("SELECT 1 FROM edition_groups WHERE id=?", (group_id,)).fetchone():
        conn.close()
        return jsonify({"error": "Group not found"}), 404
    _container_image_replace(conn, "edition_groups", group_id, "poster_file", None)
    conn.commit()
    conn.close()
    return jsonify({"status": "cleared"})


@app.route("/api/edition-groups/<int:group_id>/backdrop", methods=["POST"])
def upload_edition_group_backdrop(group_id):
    if "backdrop" not in request.files:
        return jsonify({"error": "Geen backdropbestand meegegeven"}), 400
    conn = get_db()
    if not conn.execute("SELECT 1 FROM edition_groups WHERE id=?", (group_id,)).fetchone():
        conn.close()
        return jsonify({"error": "Group not found"}), 404
    new_file, err = save_uploaded_backdrop(request.files["backdrop"])
    if err:
        conn.close()
        return jsonify({"error": err}), 400
    url = f"/api/posters/{new_file}"
    _container_image_replace(conn, "edition_groups", group_id, "backdrop", url)
    conn.commit()
    conn.close()
    return jsonify({"status": "updated", "backdrop": url})


@app.route("/api/collections/<int:col_id>/poster", methods=["POST"])
def upload_collection_poster(col_id):
    if "poster" not in request.files:
        return jsonify({"error": "Geen posterbestand meegegeven"}), 400
    conn = get_db()
    if not conn.execute("SELECT 1 FROM collections WHERE id=?", (col_id,)).fetchone():
        conn.close()
        return jsonify({"error": "Collection not found"}), 404
    new_file, err = save_uploaded_poster(request.files["poster"])
    if err:
        conn.close()
        return jsonify({"error": err}), 400
    _container_image_replace(conn, "collections", col_id, "poster_file", new_file)
    conn.commit()
    conn.close()
    return jsonify({"status": "updated", "poster_file": new_file})


@app.route("/api/collections/<int:col_id>/poster", methods=["DELETE"])
def clear_collection_poster(col_id):
    conn = get_db()
    if not conn.execute("SELECT 1 FROM collections WHERE id=?", (col_id,)).fetchone():
        conn.close()
        return jsonify({"error": "Collection not found"}), 404
    _container_image_replace(conn, "collections", col_id, "poster_file", None)
    conn.commit()
    conn.close()
    return jsonify({"status": "cleared"})


@app.route("/api/collections/<int:col_id>/backdrop", methods=["POST"])
def upload_collection_backdrop(col_id):
    if "backdrop" not in request.files:
        return jsonify({"error": "Geen backdropbestand meegegeven"}), 400
    conn = get_db()
    if not conn.execute("SELECT 1 FROM collections WHERE id=?", (col_id,)).fetchone():
        conn.close()
        return jsonify({"error": "Collection not found"}), 404
    new_file, err = save_uploaded_backdrop(request.files["backdrop"])
    if err:
        conn.close()
        return jsonify({"error": err}), 400
    url = f"/api/posters/{new_file}"
    _container_image_replace(conn, "collections", col_id, "backdrop", url)
    conn.commit()
    conn.close()
    return jsonify({"status": "updated", "backdrop": url})


@app.route("/api/edition-groups/<int:group_id>/members", methods=["POST"])
def add_edition_group_member(group_id):
    data = request.json or {}
    movie_ids = data.get("movie_ids", [])
    if isinstance(movie_ids, int):
        movie_ids = [movie_ids]
    if not movie_ids:
        return jsonify({"error": "movie_ids required"}), 400
    conn = get_db()
    if not conn.execute("SELECT 1 FROM edition_groups WHERE id=?", (group_id,)).fetchone():
        conn.close()
        return jsonify({"error": "Group not found"}), 404
    # Pin the current representative before adding new members so the
    # vault keeps showing its existing poster.
    _lock_edition_group_primary(conn, group_id)
    for mid in movie_ids:
        conn.execute("UPDATE movies SET edition_group_id=? WHERE id=?", (group_id, mid))
    conn.commit()
    # New members inherit the vault's existing group memberships.
    for mid in movie_ids:
        _inherit_groups_from_container_siblings(conn, int(mid))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/edition-groups/<int:group_id>/members/<int:movie_id>", methods=["DELETE"])
def remove_edition_group_member(group_id, movie_id):
    conn = get_db()
    conn.execute(
        "UPDATE movies SET edition_group_id=NULL WHERE id=? AND edition_group_id=?",
        (movie_id, group_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Routes: Digital Library Sources (Plex / Jellyfin)
# ---------------------------------------------------------------------------

@app.route("/api/digital-sources", methods=["GET"])
def list_digital_sources():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, type, base_url, library_ids, last_synced, item_count, enabled, owner_id"
        " FROM digital_library_sources ORDER BY id ASC"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/digital-sources", methods=["POST"])
def create_digital_source():
    data = request.json or {}
    name     = (data.get("name") or "").strip()
    src_type = (data.get("type") or "").lower()
    base_url = (data.get("base_url") or "").strip().rstrip("/")
    token    = (data.get("token") or "").strip()
    if not name or not src_type or not base_url:
        return jsonify({"error": "name, type, and base_url are required"}), 400
    if src_type not in ("plex", "jellyfin"):
        return jsonify({"error": "type must be 'plex' or 'jellyfin'"}), 400
    token_enc = _encrypt_token(token)
    owner_id  = _get_current_user_id()
    conn = get_db()
    conn.execute(
        "INSERT INTO digital_library_sources (name, type, base_url, token_enc, library_ids, enabled, owner_id)"
        " VALUES (?,?,?,?,?,1,?)",
        (name, src_type, base_url, token_enc, json.dumps(data.get("library_ids") or []), owner_id)
    )
    conn.commit()
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    row = conn.execute(
        "SELECT id, name, type, base_url, library_ids, last_synced, item_count, enabled, owner_id"
        " FROM digital_library_sources WHERE id=?", (sid,)
    ).fetchone()
    conn.close()
    return jsonify(dict(row)), 201


@app.route("/api/digital-sources/<int:source_id>", methods=["PUT"])
def update_digital_source(source_id):
    data = request.json or {}
    conn = get_db()
    row = conn.execute("SELECT * FROM digital_library_sources WHERE id=?", (source_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Not found"}), 404
    fields = {}
    if "name" in data:
        fields["name"] = (data["name"] or "").strip()
    if "base_url" in data:
        fields["base_url"] = (data["base_url"] or "").strip().rstrip("/")
    if "token" in data and data["token"]:
        fields["token_enc"] = _encrypt_token(data["token"])
    if "library_ids" in data:
        fields["library_ids"] = json.dumps(data["library_ids"] or [])
    if "enabled" in data:
        fields["enabled"] = 1 if data["enabled"] else 0
    if fields:
        set_clause = ", ".join(f"{k}=?" for k in fields)
        conn.execute(
            f"UPDATE digital_library_sources SET {set_clause} WHERE id=?",
            list(fields.values()) + [source_id]
        )
        conn.commit()
    updated = conn.execute(
        "SELECT id, name, type, base_url, library_ids, last_synced, item_count, enabled, owner_id"
        " FROM digital_library_sources WHERE id=?", (source_id,)
    ).fetchone()
    conn.close()
    return jsonify(dict(updated))


@app.route("/api/digital-sources/<int:source_id>", methods=["DELETE"])
def delete_digital_source(source_id):
    conn = get_db()
    conn.execute("DELETE FROM digital_library_items WHERE source_id=?", (source_id,))
    conn.execute("DELETE FROM digital_library_sources WHERE id=?", (source_id,))
    conn.commit()
    conn.close()
    _sync_jobs.pop(source_id, None)
    return jsonify({"ok": True})


@app.route("/api/digital-sources/<int:source_id>/test", methods=["POST"])
def test_digital_source(source_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM digital_library_sources WHERE id=?", (source_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Not found"}), 404
    src = dict(row)
    token = _decrypt_token(src.get("token_enc") or "")
    base_url = src["base_url"]
    try:
        if src["type"] == "plex":
            r = requests.get(f"{base_url}/", params={"X-Plex-Token": token}, timeout=8)
            ok = r.status_code in (200, 401)  # 401 = wrong token but server reachable
            msg = "Plex server reachable" if r.status_code == 200 else f"HTTP {r.status_code}"
        else:  # jellyfin
            r = requests.get(f"{base_url}/System/Info/Public", timeout=8)
            ok = r.status_code == 200
            info = r.json() if ok else {}
            msg = f"Jellyfin {info.get('Version', 'server reachable')}" if ok else f"HTTP {r.status_code}"
        return jsonify({"ok": ok, "message": msg})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)})


def _run_plex_sync(source_id: int):
    """Background thread: fetch all movie items from Plex and upsert into digital_library_items."""
    _sync_jobs[source_id] = {"status": "running", "progress": 0, "total": 0, "error": ""}
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM digital_library_sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            conn.close()
            _sync_jobs[source_id]["status"] = "error"
            _sync_jobs[source_id]["error"] = "Source not found"
            return
        src = dict(row)
        token = _decrypt_token(src.get("token_enc") or "")
        base_url = src["base_url"]
        lib_ids_raw = src.get("library_ids") or "[]"
        try:
            configured_lib_ids = [str(x) for x in json.loads(lib_ids_raw)]
        except Exception:
            configured_lib_ids = []

        # Get library sections
        r = requests.get(f"{base_url}/library/sections",
                         params={"X-Plex-Token": token}, timeout=10)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        sections = [
            {"key": d.get("key"), "title": d.get("title"), "type": d.get("type")}
            for d in root.findall(".//Directory")
            if d.get("type") == "movie"
        ]
        if configured_lib_ids:
            sections = [s for s in sections if s["key"] in configured_lib_ids]

        all_items = []
        for section in sections:
            r2 = requests.get(
                f"{base_url}/library/sections/{section['key']}/all",
                params={"X-Plex-Token": token, "type": "1", "includeGuids": "1"},
                timeout=30
            )
            r2.raise_for_status()
            root2 = ET.fromstring(r2.text)
            for video in root2.findall(".//Video"):
                tmdb_id = ""
                imdb_id = ""
                guid = video.get("guid", "")
                # Parse primary guid
                if "themoviedb" in guid or "tmdb" in guid:
                    m = re.search(r'themoviedb[:/]+(\d+)', guid)
                    if m:
                        tmdb_id = m.group(1)
                elif "imdb" in guid:
                    m = re.search(r'tt\d+', guid)
                    if m:
                        imdb_id = m.group(0)
                # Also check Guid children (modern Plex)
                for g in video.findall("Guid"):
                    gid = g.get("id", "")
                    if gid.startswith("tmdb://"):
                        tmdb_id = gid[7:]
                    elif gid.startswith("imdb://"):
                        imdb_id = gid[7:]
                all_items.append({
                    "external_id": video.get("ratingKey", ""),
                    "title": video.get("title", ""),
                    "year": str(video.get("year", "")) if video.get("year") else "",
                    "tmdb_id": tmdb_id,
                    "imdb_id": imdb_id,
                })

        _sync_jobs[source_id]["total"] = len(all_items)
        synced_at = datetime.utcnow().isoformat()
        # Delete old items for this source and reinsert
        conn.execute("DELETE FROM digital_library_items WHERE source_id=?", (source_id,))
        for i, item in enumerate(all_items):
            conn.execute(
                "INSERT OR REPLACE INTO digital_library_items"
                " (source_id, external_id, title, year, tmdb_id, imdb_id, media_type, synced_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (source_id, item["external_id"], item["title"], item["year"],
                 item["tmdb_id"], item["imdb_id"], "movie", synced_at)
            )
            _sync_jobs[source_id]["progress"] = i + 1
        conn.execute(
            "UPDATE digital_library_sources SET last_synced=?, item_count=? WHERE id=?",
            (synced_at, len(all_items), source_id)
        )
        conn.commit()
        conn.close()
        _sync_jobs[source_id]["status"] = "done"
    except Exception as e:
        _sync_jobs[source_id]["status"] = "error"
        _sync_jobs[source_id]["error"] = str(e)


def _run_jellyfin_sync(source_id: int):
    """Background thread: fetch all movie items from Jellyfin and upsert into digital_library_items."""
    _sync_jobs[source_id] = {"status": "running", "progress": 0, "total": 0, "error": ""}
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM digital_library_sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            conn.close()
            _sync_jobs[source_id]["status"] = "error"
            _sync_jobs[source_id]["error"] = "Source not found"
            return
        src = dict(row)
        token = _decrypt_token(src.get("token_enc") or "")
        base_url = src["base_url"]

        params = {
            "IncludeItemTypes": "Movie",
            "Recursive": "true",
            "Fields": "ProviderIds",
            "Limit": "5000",
            "api_key": token,
        }
        r = requests.get(f"{base_url}/Items", params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        items_raw = data.get("Items", [])

        all_items = []
        for item in items_raw:
            pids = item.get("ProviderIds", {})
            tmdb_id = str(pids.get("Tmdb", "") or pids.get("tmdb", "") or "")
            imdb_id = str(pids.get("Imdb", "") or pids.get("imdb", "") or "")
            all_items.append({
                "external_id": str(item.get("Id", "")),
                "title": item.get("Name", ""),
                "year": str(item.get("ProductionYear", "")) if item.get("ProductionYear") else "",
                "tmdb_id": tmdb_id,
                "imdb_id": imdb_id,
            })

        _sync_jobs[source_id]["total"] = len(all_items)
        synced_at = datetime.utcnow().isoformat()
        conn.execute("DELETE FROM digital_library_items WHERE source_id=?", (source_id,))
        for i, item in enumerate(all_items):
            conn.execute(
                "INSERT OR REPLACE INTO digital_library_items"
                " (source_id, external_id, title, year, tmdb_id, imdb_id, media_type, synced_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (source_id, item["external_id"], item["title"], item["year"],
                 item["tmdb_id"], item["imdb_id"], "movie", synced_at)
            )
            _sync_jobs[source_id]["progress"] = i + 1
        conn.execute(
            "UPDATE digital_library_sources SET last_synced=?, item_count=? WHERE id=?",
            (synced_at, len(all_items), source_id)
        )
        conn.commit()
        conn.close()
        _sync_jobs[source_id]["status"] = "done"
    except Exception as e:
        _sync_jobs[source_id]["status"] = "error"
        _sync_jobs[source_id]["error"] = str(e)


@app.route("/api/digital-sources/<int:source_id>/sync", methods=["POST"])
def sync_digital_source(source_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM digital_library_sources WHERE id=?", (source_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Not found"}), 404
    job = _sync_jobs.get(source_id, {})
    if job.get("status") == "running":
        return jsonify({"status": "running", "progress": job.get("progress", 0), "total": job.get("total", 0)})
    src_type = row["type"]
    target = _run_plex_sync if src_type == "plex" else _run_jellyfin_sync
    t = threading.Thread(target=target, args=(source_id,), daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/api/digital-sources/<int:source_id>/sync-status", methods=["GET"])
def digital_source_sync_status(source_id):
    job = _sync_jobs.get(source_id)
    if not job:
        # No job in memory — check last_synced from DB
        conn = get_db()
        row = conn.execute(
            "SELECT last_synced, item_count FROM digital_library_sources WHERE id=?", (source_id,)
        ).fetchone()
        conn.close()
        if row and row["last_synced"]:
            return jsonify({"status": "done", "last_synced": row["last_synced"], "item_count": row["item_count"]})
        return jsonify({"status": "idle"})
    return jsonify(job)


# ---------------------------------------------------------------------------
# Routes: Collection Compare (Physical vs Digital)
# ---------------------------------------------------------------------------

@app.route("/api/collection/compare", methods=["GET"])
def collection_compare():
    conn = get_db()
    owner_clause, owner_params = _movie_owner_filter()
    movies = conn.execute(
        "SELECT id, title, original_title, year, tmdb_id, imdb_id, format, poster_file, poster"
        " FROM movies WHERE 1=1" + owner_clause,
        list(owner_params)
    ).fetchall()
    digital_items = conn.execute(
        "SELECT dli.*, dls.name AS source_name, dls.type AS source_type"
        " FROM digital_library_items dli"
        " JOIN digital_library_sources dls ON dls.id = dli.source_id"
        " WHERE dls.enabled=1"
    ).fetchall()
    conn.close()

    # Index digital items
    digital_by_tmdb: dict = {}
    digital_by_imdb: dict = {}
    digital_by_title_year: dict = {}
    for d in digital_items:
        dd = dict(d)
        # Normalize ids to string keys so lookup matches regardless of how
        # the source stored them (Plex may store as INTEGER, Jellyfin as TEXT).
        if dd.get("tmdb_id"):
            digital_by_tmdb.setdefault(str(dd["tmdb_id"]), []).append(dd)
        if dd.get("imdb_id"):
            digital_by_imdb.setdefault(str(dd["imdb_id"]), []).append(dd)
        key = f"{(dd.get('title') or '').lower().strip()}|{str(dd.get('year') or '').strip()}"
        digital_by_title_year.setdefault(key, []).append(dd)

    physical_and_digital = []
    physical_only = []
    matched_digital_ids: set = set()

    for m in movies:
        md = dict(m)
        matches = []
        # Match by tmdb_id first
        if md.get("tmdb_id"):
            matches = digital_by_tmdb.get(str(md["tmdb_id"]), [])
        # Fallback: imdb_id
        if not matches and md.get("imdb_id"):
            matches = digital_by_imdb.get(str(md["imdb_id"]), [])
        # Fallback: title+year
        if not matches:
            key = f"{(md.get('original_title') or md.get('title') or '').lower().strip()}|{str(md.get('year') or '').strip()}"
            matches = digital_by_title_year.get(key, [])
        if matches:
            for match in matches:
                matched_digital_ids.add(match["id"])
            # Deduplicate by source — one badge per source, not per library item
            seen_sources: set = set()
            deduped = []
            for x in matches:
                src_key = x.get("source_id") or x.get("source_name")
                if src_key not in seen_sources:
                    seen_sources.add(src_key)
                    deduped.append(x)
            physical_and_digital.append({
                "movie": md,
                "digital_matches": [
                    {"source_name": x["source_name"], "source_type": x["source_type"],
                     "title": x["title"], "year": x["year"]}
                    for x in deduped
                ]
            })
        else:
            physical_only.append({"movie": md})

    # Digital only: items not matched to any physical disc
    digital_only = []
    seen_digital_only: set = set()
    for d in digital_items:
        dd = dict(d)
        if dd["id"] not in matched_digital_ids:
            key = f"{dd['source_id']}:{dd['external_id']}"
            if key not in seen_digital_only:
                seen_digital_only.add(key)
                digital_only.append({
                    "title": dd["title"], "year": dd["year"],
                    "tmdb_id": dd.get("tmdb_id"), "imdb_id": dd.get("imdb_id"),
                    "source_name": dd["source_name"], "source_type": dd["source_type"],
                })

    return jsonify({
        "physical_and_digital": physical_and_digital,
        "physical_only": physical_only,
        "digital_only": digital_only,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
