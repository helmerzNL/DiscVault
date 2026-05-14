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
import requests
import jwt
import cbor2
import xml.etree.ElementTree as ET
from functools import wraps
from urllib.parse import quote_plus, quote
from flask import Flask, request, jsonify, send_from_directory, send_file, Response, g
from flask_cors import CORS
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from PIL import Image, ImageOps, UnidentifiedImageError
from bs4 import BeautifulSoup
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
MCP_API_KEY   = os.environ.get("MCP_API_KEY", "")
JWT_SECRET    = os.environ.get("JWT_SECRET", secrets.token_hex(32))
RP_ID         = os.environ.get("RP_ID", "localhost")
RP_NAME       = os.environ.get("RP_NAME", "DiscVault")
RP_ORIGIN     = os.environ.get("RP_ORIGIN", "http://localhost:6080")
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
    ("extras",               "TEXT"),
    ("box_set",              "TEXT"),
    # External IDs & links
    ("imdb_id",              "TEXT"),
    ("imdb_url",             "TEXT"),
    ("tmdb_id",              "TEXT"),
    # Media
    ("poster",               "TEXT"),
    ("poster_file",          "TEXT"),
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
            detail     TEXT
        )
    """)

    # Import control flags (cross-worker safe cancel state)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS import_controls (
            import_id         TEXT PRIMARY KEY,
            cancel_requested  INTEGER NOT NULL DEFAULT 0,
            updated_at        REAL NOT NULL
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

    # Auth: settings
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
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

    # Auto-assign existing movies (no owner) to first admin user
    first_user = conn.execute("SELECT id FROM users ORDER BY created_at ASC LIMIT 1").fetchone()
    if first_user:
        conn.execute("UPDATE users SET role='admin' WHERE id=? AND role='user'", (first_user[0],))
        orphan_count = conn.execute("SELECT COUNT(*) FROM movies WHERE owner_id IS NULL").fetchone()[0]
        if orphan_count > 0:
            conn.execute("UPDATE movies SET owner_id=? WHERE owner_id IS NULL", (first_user[0],))

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

def add_log(category: str, message: str, detail: str = "", level: str = "info"):
    """Write a structured log entry to the database.
    Levels: info, warn, error, success
    Categories: import, refresh, lookup, add, delete, scan, api, general
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO logs (timestamp, level, category, message, detail) VALUES (?,?,?,?,?)",
            (local_now_iso(), level, category, message, detail or "")
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

    return out


# ---------------------------------------------------------------------------
# External API lookups
# ---------------------------------------------------------------------------

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
                    "country":      d.get("Country", ""),
                    "language":     d.get("Language", ""),
                    "tmdb_id":      "",
                }
    except Exception:
        pass
    return None


def lookup_movie_tmdb(title, year=""):
    if not TMDB_API_KEY or not _is_tmdb_enabled():
        return None
    try:
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
        rd = requests.get(
            f"https://api.themoviedb.org/3/movie/{movie_id}"
            f"?api_key={TMDB_API_KEY}&append_to_response=credits",
            timeout=6
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
        poster_url = ""
        if d.get("poster_path"):
            poster_url = f"https://image.tmdb.org/t/p/w500{d['poster_path']}"
        release = d.get("release_date", "") or ""

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

        return {
            "title":        d.get("title", ""),
            "original_title": d.get("original_title", ""),
            "year":         release[:4],
            "release_date": release,
            "director":     ", ".join(directors),
            "actor":        ", ".join(actors),
            "producer":     ", ".join(producers[:3]),
            "studios":      ", ".join(studios),
            "genre":        ", ".join(genres),
            "plot":         d.get("overview", ""),
            "poster":       poster_url,
            "runtime":      str(d.get("runtime", "")),
            "rating":       str(round(d.get("vote_average", 0), 1)),
            "imdb_id":      d.get("imdb_id", "") or "",
            "tmdb_id":      str(movie_id),
            "language":     d.get("original_language", ""),
            "_cast_crew":   cast_crew,
        }
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
    owner_clause, owner_params = _movie_owner_filter()
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
    owner_clause, owner_params = _movie_owner_filter()
    sql = "SELECT * FROM movies WHERE 1=1" + owner_clause
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
    if not data.get("barcode") or not data.get("title"):
        return jsonify({"error": "barcode and title are required"}), 400
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
        return jsonify({"status": "added", "movie": dict(movie)}), 201
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
                        f"Blu-ray.com aanvulling bij handmatige update: \"{merged.get('title') or lookup_title}\"",
                        f"Aangevuld: {', '.join(filled)}",
                        "info"
                    )
        elif lookup_title:
            _trace_add(attempts, "Blu-ray.com", "skipped", f"title={lookup_title}", "geen missende hdr/audio/subtitles")
    else:
        _trace_add(attempts, "Blu-ray.com", "skipped", "source toggle uit")

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE movies SET {set_clause} WHERE id = ?",
        list(updates.values()) + [movie_id]
    )
    conn.commit()
    movie = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
    conn.close()
    add_log("refresh", f"Handmatige update: \"{(movie['title'] if movie else existing.get('title','?'))}\"", f"Backends: {_trace_summary(attempts)}", "info")
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
        source = ""
        attempts = []
        if imdb_id and OMDB_API_KEY:
            try:
                info = lookup_movie_omdb(imdb_id=imdb_id)
                _trace_add(attempts, "OMDb", "hit" if info else "miss", f"imdb_id={imdb_id}")
            except Exception as ex:
                _trace_add(attempts, "OMDb", "error", f"imdb_id={imdb_id}", str(ex))
            if info:
                source = f"OMDb (imdb_id)"
        elif imdb_id and not OMDB_API_KEY:
            _trace_add(attempts, "OMDb", "skipped", f"imdb_id={imdb_id}", "OMDB_API_KEY ontbreekt")
        if not info:
            try:
                info = lookup_movie_tmdb(search_title, year)
                _trace_add(attempts, "TMDb", "hit" if info else "miss", f"title={search_title}, year={year}")
            except Exception as ex:
                _trace_add(attempts, "TMDb", "error", f"title={search_title}, year={year}", str(ex))
            if info:
                source = f"TMDb"
        if not info:
            try:
                info = lookup_movie_omdb(title=search_title)
                _trace_add(attempts, "OMDb", "hit" if info else "miss", f"title={search_title}")
            except Exception as ex:
                _trace_add(attempts, "OMDb", "error", f"title={search_title}", str(ex))
            if info:
                source = f"OMDb"
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
                source = "fallback"
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
            tmdb = lookup_movie_tmdb(search_title, year)
            _trace_add(attempts, "TMDb", "hit" if tmdb else "miss", f"title={search_title}, year={year}")
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
            add_log("refresh", f"Sync alle bronnen: geen resultaat voor \"{search_title}\"", f"Backends: {_trace_summary(attempts)}", "warn")
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
            f"Sync alle bronnen: \"{title}\"",
            f"Bronnen: {source_label}. Velden: {', '.join(fields_updated)}. Backends: {_trace_summary(attempts)}",
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
        add_log("refresh", f"Fout bij sync alle bronnen \"{title}\"", str(e), "error")
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
        add_log("refresh", f"Fout bij sync bron \"{title}\"", f"Bron: {source_label}. {str(e)}", "error")
        return jsonify({"status": "error", "title": title, "error": str(e)}), 500


@app.route("/api/movies/bulk-refresh", methods=["POST"])
def bulk_refresh():
    """Re-fetch metadata (poster, plot, rating, etc.) from TMDb/OMDb for a list of movie IDs."""
    ids            = (request.json or {}).get("ids", [])
    fetch_posters  = (request.json or {}).get("fetch_posters", True)
    if not ids:
        return jsonify({"error": "No ids provided"}), 400

    conn = get_db()
    updated = skipped = errors = 0
    error_details = []

    for movie_id in ids:
        row = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
        if not row or not _check_movie_owner(row):
            skipped += 1
            add_log("refresh", f"Film ID {movie_id} niet gevonden — overgeslagen", level="warn")
            continue
        movie = dict(row)
        title          = movie.get("title", "")
        original_title = movie.get("original_title", "")
        search_title   = original_title or title  # prefer original_title
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
            # Fallback: try regular title if original_title didn't match
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
                skipped += 1
                add_log("refresh", f"Geen resultaat voor \"{search_title}\" ({year})",
                        f"Backends: {_trace_summary(attempts)}", "warn")
                continue

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

            # Sync cast/crew — use tmdb_id directly for reliable credits fetch
            cast_crew = info.get("_cast_crew")
            if not cast_crew and TMDB_API_KEY:
                tid = info.get("tmdb_id") or movie.get("tmdb_id") or ""
                if tid:
                    cast_crew = _fetch_tmdb_cast_crew(tid)
            if cast_crew:
                _sync_movie_cast_crew(conn, movie_id, cast_crew, download_photos=fetch_posters)

            updated += 1
            fields_str = ", ".join(updates.keys()) if updates else "(geen wijzigingen)"
            add_log("refresh", f"Bijgewerkt: \"{title}\"",
                    f"Bron: {source}. Velden: {fields_str}. Backends: {_trace_summary(attempts)}", "success")
        except Exception as e:
            errors += 1
            error_details.append(f"[{movie_id}] {title}: {str(e)}")
            add_log("refresh", f"Fout bij \"{title}\"",
                    f"Exception: {str(e)}", "error")

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

        # 3. OMDb / TMDb
        movie_info = None
        if raw_title:
            for candidate in _title_candidates_from_upc(raw_title):
                if not movie_info:
                    yield emit(3, "OMDb", "searching", candidate)
                    try:
                        movie_info = lookup_movie_omdb(title=candidate)
                        _trace_add(attempts, "OMDb", "hit" if movie_info else "miss", f"title={candidate}")
                        yield emit(3, "OMDb", "hit" if movie_info else "miss", candidate)
                    except Exception as ex:
                        _trace_add(attempts, "OMDb", "error", f"title={candidate}", str(ex))
                        yield emit(3, "OMDb", "error", candidate)
                if not movie_info:
                    yield emit(4, "TMDb", "searching", candidate)
                    try:
                        movie_info = lookup_movie_tmdb(candidate)
                        _trace_add(attempts, "TMDb", "hit" if movie_info else "miss", f"title={candidate}")
                        yield emit(4, "TMDb", "hit" if movie_info else "miss", candidate)
                    except Exception as ex:
                        _trace_add(attempts, "TMDb", "error", f"title={candidate}", str(ex))
                        yield emit(4, "TMDb", "error", candidate)
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
            yield json.dumps({"type": "done", "status": "found", "movie": movie_info, "barcode": barcode}) + "\n"
        else:
            add_log("lookup", f"Barcode {barcode} niet gevonden", f"Backends: {_trace_summary(attempts)}", "warn")
            yield json.dumps({"type": "done", "status": "not_found", "barcode": barcode, "raw_title": raw_title}) + "\n"

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
                        movie_info = lookup_movie_omdb(title=candidate)
                        _trace_add(attempts, "OMDb", "hit" if movie_info else "miss", f"title={candidate}")
                    except Exception as ex:
                        _trace_add(attempts, "OMDb", "error", f"title={candidate}", str(ex))
                if not movie_info:
                    try:
                        movie_info = lookup_movie_tmdb(candidate)
                        _trace_add(attempts, "TMDb", "hit" if movie_info else "miss", f"title={candidate}")
                    except Exception as ex:
                        _trace_add(attempts, "TMDb", "error", f"title={candidate}", str(ex))
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
            add_log(
                "lookup",
                f"Barcode {barcode} gevonden: \"{movie_info.get('title','?')}\"",
                f"Backends: {_trace_summary(attempts)}",
                "success"
            )
            return jsonify({"status": "found", "movie": movie_info, "barcode": barcode})
        add_log("lookup", f"Barcode {barcode} niet gevonden", f"Backends: {_trace_summary(attempts)}", "warn")
        return jsonify({"status": "not_found", "barcode": barcode, "raw_title": raw_title})
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
        movie_info = lookup_movie_omdb(title=title)
        _trace_add(attempts, "OMDb", "hit" if movie_info else "miss", f"title={title}")
        if not movie_info:
            movie_info = lookup_movie_tmdb(title, year)
            _trace_add(attempts, "TMDb", "hit" if movie_info else "miss", f"title={title}, year={year}")
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
            return jsonify({"status": "found", "movie": movie_info})
        add_log("lookup", f"Titel-zoekactie geen resultaat: \"{title}\"", f"Backends: {_trace_summary(attempts)}", "warn")
        return jsonify({"status": "not_found"})
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
    "screen ratio": "screen_ratios", "aspect ratio": "screen_ratios",
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
    if _get_current_user_role() == "admin":
        return "", []  # Admin sees everything
    conn = get_db()
    group_rows = conn.execute("SELECT group_id FROM user_groups WHERE user_id=?", (uid,)).fetchall()
    conn.close()
    group_ids = [r["group_id"] for r in group_rows]
    if group_ids:
        placeholders = ",".join("?" * len(group_ids))
        return (f" AND (owner_id = ? OR id IN (SELECT movie_id FROM movie_groups WHERE group_id IN ({placeholders})))",
                [uid] + group_ids)
    return " AND owner_id = ?", [uid]


def _check_movie_owner(movie_row) -> bool:
    """Return True if current user may modify this movie (owner or admin)."""
    uid = _get_current_user_id()
    if not uid:
        return True  # Auth disabled
    if _get_current_user_role() == "admin":
        return True
    return movie_row["owner_id"] == uid


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
        payload = _verify_token(token)
        if payload:
            g.current_user_id = payload.get("sub")
            g.current_username = payload.get("usr")

    # Public routes: allow without auth
    for prefix in PUBLIC_PREFIXES:
        if request.path.startswith(prefix):
            return

    if not _is_auth_enabled():
        return

    # MCP API key auth
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
        conn.close()
        return jsonify({"error": "Registration is disabled"}), 403
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
    conn_check.close()
    caller_uid = _get_current_user_id()
    if has_users and not existing_user and not caller_uid and not _is_registration_enabled():
        return jsonify({"error": "Registration is disabled"}), 403

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
        if client_data.get("origin") != RP_ORIGIN:
            raise ValueError(f"Origin mismatch: {client_data.get('origin')} != {RP_ORIGIN}")

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
        if client_data.get("origin") != RP_ORIGIN:
            raise ValueError(f"Origin mismatch: {client_data.get('origin')} != {RP_ORIGIN}")

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
    if new_role not in ("admin", "user"):
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


@app.route("/api/auth/me", methods=["GET"])
def get_current_user():
    uid = _get_current_user_id()
    if not uid:
        return jsonify({"authenticated": False})
    conn = get_db()
    user = conn.execute("SELECT id, username, display_name, role, first_name, last_name, avatar, created_at FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    if not user:
        return jsonify({"authenticated": False})
    data = {**dict(user), "authenticated": True}
    if data.get("avatar"):
        data["avatar_url"] = f"/api/avatars/{data['avatar']}"
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
    add_log("auth", f"Profiel bijgewerkt: {new_username}", level="info")
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
# Groups: shared collections
# ---------------------------------------------------------------------------

@app.route("/api/groups", methods=["GET"])
def list_groups():
    uid = _get_current_user_id()
    conn = get_db()
    if not uid or _get_current_user_role() == "admin":
        rows = conn.execute("""
            SELECT g.*, u.username as created_by_username,
                   (SELECT COUNT(*) FROM user_groups WHERE group_id=g.id) as member_count,
                   (SELECT COUNT(*) FROM movie_groups WHERE group_id=g.id) as movie_count
            FROM groups g LEFT JOIN users u ON g.created_by=u.id
            ORDER BY g.name
        """).fetchall()
    else:
        rows = conn.execute("""
            SELECT g.*, u.username as created_by_username,
                   (SELECT COUNT(*) FROM user_groups WHERE group_id=g.id) as member_count,
                   (SELECT COUNT(*) FROM movie_groups WHERE group_id=g.id) as movie_count
            FROM groups g
            JOIN user_groups ug ON ug.group_id=g.id AND ug.user_id=?
            LEFT JOIN users u ON g.created_by=u.id
            ORDER BY g.name
        """, (uid,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/groups", methods=["POST"])
def create_group():
    err = _require_admin()
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
        # Auto-add creator to group
        if uid:
            conn.execute("INSERT INTO user_groups (user_id, group_id) VALUES (?,?)", (uid, group_id))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "Group name already exists"}), 409
    conn.close()
    add_log("groups", f"Groep '{name}' aangemaakt", level="info")
    return jsonify({"status": "ok", "id": group_id}), 201


@app.route("/api/groups/<int:group_id>", methods=["PUT"])
def update_group(group_id):
    err = _require_admin()
    if err:
        return err
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Group name required"}), 400
    conn = get_db()
    try:
        conn.execute("UPDATE groups SET name=? WHERE id=?", (name, group_id))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "Group name already exists"}), 409
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/groups/<int:group_id>", methods=["DELETE"])
def delete_group(group_id):
    err = _require_admin()
    if err:
        return err
    conn = get_db()
    group = conn.execute("SELECT name FROM groups WHERE id=?", (group_id,)).fetchone()
    if not group:
        conn.close()
        return jsonify({"error": "Group not found"}), 404
    # Unlink movies from this group
    conn.execute("DELETE FROM movie_groups WHERE group_id=?", (group_id,))
    conn.execute("DELETE FROM user_groups WHERE group_id=?", (group_id,))
    conn.execute("DELETE FROM groups WHERE id=?", (group_id,))
    conn.commit()
    conn.close()
    add_log("groups", f"Groep '{group['name']}' verwijderd", level="warn")
    return jsonify({"status": "deleted"})


@app.route("/api/groups/<int:group_id>/members", methods=["GET"])
def list_group_members(group_id):
    conn = get_db()
    rows = conn.execute("""
        SELECT u.id, u.username, u.display_name, u.role
        FROM users u JOIN user_groups ug ON ug.user_id=u.id
        WHERE ug.group_id=?
        ORDER BY u.username
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
        conn.execute("INSERT INTO user_groups (user_id, group_id) VALUES (?,?)", (user_id, group_id))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "Already a member"}), 409
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/groups/<int:group_id>/members/<member_id>", methods=["DELETE"])
def remove_group_member(group_id, member_id):
    err = _require_admin()
    if err:
        return err
    conn = get_db()
    conn.execute("DELETE FROM user_groups WHERE user_id=? AND group_id=?", (member_id, group_id))
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
    """Set the full list of group_ids for a movie (replaces existing)."""
    data = request.json or {}
    group_ids = data.get("group_ids", [])
    conn = get_db()
    movie = conn.execute("SELECT owner_id FROM movies WHERE id=?", (movie_id,)).fetchone()
    if not movie:
        conn.close()
        return jsonify({"error": "Movie not found"}), 404
    uid = _get_current_user_id()
    if uid and movie["owner_id"] != uid and _get_current_user_role() != "admin":
        conn.close()
        return jsonify({"error": "Not your movie"}), 403
    conn.execute("DELETE FROM movie_groups WHERE movie_id=?", (movie_id,))
    for gid in group_ids:
        conn.execute("INSERT OR IGNORE INTO movie_groups (movie_id, group_id) VALUES (?,?)",
                     (movie_id, int(gid)))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/movies/bulk/groups", methods=["PUT"])
def bulk_set_movie_groups():
    """Add group(s) to multiple movies at once."""
    data = request.json or {}
    movie_ids = data.get("movie_ids", [])
    group_ids = data.get("group_ids", [])
    if not movie_ids or not group_ids:
        return jsonify({"error": "movie_ids and group_ids required"}), 400
    conn = get_db()
    uid = _get_current_user_id()
    added = 0
    for mid in movie_ids:
        movie = conn.execute("SELECT owner_id FROM movies WHERE id=?", (mid,)).fetchone()
        if not movie:
            continue
        if uid and movie["owner_id"] != uid and _get_current_user_role() != "admin":
            continue
        for gid in group_ids:
            conn.execute("INSERT OR IGNORE INTO movie_groups (movie_id, group_id) VALUES (?,?)",
                         (int(mid), int(gid)))
        added += 1
    conn.commit()
    conn.close()
    return jsonify({"status": "ok", "updated": added})


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
    add_log("settings", f"MCP server {'ingeschakeld' if val else 'uitgeschakeld'}", level="info")
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
        add_log("settings", f"Backup aangemaakt: {backup_name}",
                f"Grootte: {size // 1024} KB", "success")
        return jsonify({"status": "ok", "name": backup_name, "size": size})
    except Exception as e:
        add_log("settings", "Backup mislukt", str(e), "error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/settings/backups", methods=["GET"])
def list_backups():
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

    # Build old movie id → new movie id mapping
    old_to_new_movie = {}
    old_to_new_person = {}

    # Restore people
    for p in people:
        old_id = p.pop("id", None)
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
    movie_count = conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
    log_count   = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    conn.close()
    return jsonify({
        "db_size": size, "poster_count": poster_count,
        "poster_size": poster_size, "movie_count": movie_count,
        "log_count": log_count,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
