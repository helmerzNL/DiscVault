"""Import a copied DiscVault SQLite data directory into DiscVault Next PostgreSQL.

This tool is intentionally standalone. It reads the legacy SQLite database and
media files directly, and writes into the PostgreSQL schema created by
``next_database.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import sqlite3
import sys
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    from .next_genres import map_legacy_genre_text
    from .next_genres import normalize_genre_keys
except ImportError:  # pragma: no cover - supports direct script execution
    from next_genres import map_legacy_genre_text
    from next_genres import normalize_genre_keys


IMPORT_NAMESPACE = uuid.UUID("7c76309b-063d-4c63-b925-2f49fdad332c")
LOCALIZED_LANGS = ("nl", "fr", "de", "es", "pt", "it", "sv", "da", "no", "fi")
SECRET_SETTING_HINTS = ("secret", "token", "private_key", "password", "_enc")
LEGACY_METADATA_PLUGIN_FLAGS = {
    "tmdb": ("tmdb_enabled",),
    "omdb": ("omdb_enabled",),
    "movievault": ("movievault_enabled",),
    "bluray_com": ("bluray_scrape_enabled", "bluray_com_enabled"),
    "upcitemdb": ("upcitemdb_enabled",),
}
LEGACY_METADATA_SOURCE_ALIASES = {
    "tmdb": "tmdb",
    "themoviedb": "tmdb",
    "omdb": "omdb",
    "movievault": "movievault",
    "movie_vault": "movievault",
    "movievault_26": "movievault",
    "movievault26": "movievault",
    "bluray_com": "bluray_com",
    "bluray.com": "bluray_com",
    "blu-ray.com": "bluray_com",
    "bluray": "bluray_com",
    "upcitemdb": "upcitemdb",
    "upc_item_db": "upcitemdb",
    "upc": "upcitemdb",
    "bluray_disc_de": "bluray_disc_de",
    "bluraydiscde": "bluray_disc_de",
    "bluraydisc.de": "bluray_disc_de",
}
CLIENT_SYNC_SETTING_KEYS = {
    "auth_enabled",
    "registration_enabled",
    "rating_country",
    "show_auto_videos",
    "show_local_title",
    "show_search_button",
}
LEGACY_ROLE_ALIASES = {
    "administrator": "admin",
    "admins": "admin",
    "admin": "admin",
    "owner": "owner",
    "collection_manager": "media_editor",
    "collectionmanager": "media_editor",
    "media_editor": "media_editor",
    "mediaeditor": "media_editor",
    "editor": "media_editor",
    "media_fan": "media_fan",
    "mediafan": "media_fan",
    "fan": "media_fan",
    "membergroups": "media_viewer",
    "member_groups": "media_viewer",
    "group_member": "media_viewer",
    "viewer": "media_viewer",
    "member": "media_viewer",
    "user": "media_viewer",
}
LEGACY_GROUP_MEMBER_ROLES = {
    "admin": "admin",
    "administrator": "admin",
    "manager": "manager",
    "owner": "owner",
    "editor": "editor",
    "member": "member",
    "viewer": "viewer",
}


class ImportError(RuntimeError):
    """Raised when the DiscVault Next import cannot proceed safely."""


@dataclass
class ImportSummary:
    counters: Counter[str] = field(default_factory=Counter)
    skipped: Counter[str] = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "counters": dict(sorted(self.counters.items())),
            "skipped": dict(sorted(self.skipped.items())),
            "warnings": self.warnings,
        }


def _load_psycopg():
    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ImportError as exc:  # pragma: no cover - depends on runtime image
        raise ImportError(
            "psycopg is required for DiscVault Next imports. "
            "Install backend requirements first."
        ) from exc
    return psycopg, Jsonb


def database_url() -> str:
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise ImportError("DATABASE_URL is required for DiscVault Next imports.")
    return value


def connect_postgres():
    psycopg, _ = _load_psycopg()
    return psycopg.connect(database_url(), autocommit=False)


def connect_sqlite(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_uuid(source_table: str, source_id: Any) -> uuid.UUID:
    return uuid.uuid5(IMPORT_NAMESPACE, f"{source_table}:{source_id}")


def legacy_user_uuid(value: Any) -> uuid.UUID | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        if re.fullmatch(r"[0-9a-fA-F]{32}", text):
            return uuid.UUID(hex=text)
        return uuid.UUID(text)
    except ValueError:
        return stable_uuid("users", text)


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def clean_int(value: Any) -> int | None:
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None


def clean_date(value: Any) -> str | None:
    """Normalize loose legacy date values before writing PostgreSQL date fields."""
    text = clean_text(value)
    if not text:
        return None
    text = text.replace("\\", "/")
    if text.lower() in {"n/a", "na", "none", "null", "unknown", "0000", "0000-00-00"}:
        return None
    if re.fullmatch(r"\d{4}", text):
        year = int(text)
        if 1 <= year <= 9999:
            return f"{year:04d}-01-01"
        return None
    if re.fullmatch(r"\d{4}[-/]\d{1,2}", text):
        year_text, month_text = re.split(r"[-/]", text)
        try:
            return date(int(year_text), int(month_text), 1).isoformat()
        except ValueError:
            return None
    iso_match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    if iso_match:
        try:
            return date.fromisoformat(iso_match.group(1)).isoformat()
        except ValueError:
            return None
    for fmt in ("%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%Y.%m.%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_price(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    text = text.replace(",", ".")
    match = re.search(r"\d+(?:\.\d{1,2})?", text)
    return match.group(0) if match else None


def parse_json(value: Any) -> Any:
    text = clean_text(value)
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def json_array(value: Any) -> list[Any]:
    parsed = parse_json(value)
    if isinstance(parsed, list):
        return parsed
    if parsed is not None:
        return [parsed]
    text = clean_text(value)
    if not text:
        return []
    if "," in text:
        return [part.strip() for part in text.split(",") if part.strip()]
    return [text]


def json_object(value: Any) -> dict[str, Any]:
    parsed = parse_json(value)
    return parsed if isinstance(parsed, dict) else {}


def json_value(value: Any) -> Any:
    parsed = parse_json(value)
    return parsed if parsed is not None else clean_text(value)


def bool_value(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled", "active"}:
        return True
    if text in {"0", "false", "no", "off", "disabled", "inactive"}:
        return False
    return default


def normalized_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def legacy_role_key(value: Any, *, is_owner: bool = False) -> str:
    if is_owner:
        return "owner"
    return LEGACY_ROLE_ALIASES.get(normalized_key(value), "media_viewer")


def legacy_group_member_role(value: Any) -> str:
    return LEGACY_GROUP_MEMBER_ROLES.get(normalized_key(value), "member")


def normalize_metadata_source(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    key = text.strip().lower().replace("-", "_").replace(" ", "_")
    return LEGACY_METADATA_SOURCE_ALIASES.get(key)


def legacy_metadata_source_order(value: Any) -> list[str]:
    parsed = parse_json(value)
    raw_items: list[Any]
    if isinstance(parsed, list):
        raw_items = parsed
    else:
        text = clean_text(value)
        raw_items = re.split(r"[,>]", text) if text else []

    order: list[str] = []
    for item in raw_items:
        plugin_id = normalize_metadata_source(item)
        if plugin_id and plugin_id not in order:
            order.append(plugin_id)
    return order


def legacy_metadata_plugin_plan(settings: dict[str, Any]) -> dict[str, Any]:
    enabled: dict[str, bool] = {}
    for plugin_id, keys in LEGACY_METADATA_PLUGIN_FLAGS.items():
        for key in keys:
            if key in settings:
                enabled[plugin_id] = bool_value(settings.get(key), default=False)
                break

    # UPCItemDB was used implicitly by the legacy barcode flow. Keep that
    # behavior unless a future legacy export explicitly disables it.
    enabled.setdefault("upcitemdb", True)

    order = legacy_metadata_source_order(settings.get("metadata_source_order"))
    if enabled.get("upcitemdb") and "upcitemdb" not in order:
        order.insert(0, "upcitemdb")
    for plugin_id in LEGACY_METADATA_PLUGIN_FLAGS:
        if plugin_id not in order:
            order.append(plugin_id)

    unsupported = [plugin_id for plugin_id in order if plugin_id not in LEGACY_METADATA_PLUGIN_FLAGS]
    order = [plugin_id for plugin_id in order if plugin_id in LEGACY_METADATA_PLUGIN_FLAGS]

    return {
        "enabled": enabled,
        "order": order,
        "unsupported": unsupported,
    }


def apply_legacy_metadata_plugin_plan(conn, settings: dict[str, Any], Jsonb, summary: ImportSummary | None = None) -> dict[str, Any]:
    plan = legacy_metadata_plugin_plan(settings)
    enabled = plan["enabled"]
    order = plan["order"]
    applied: dict[str, dict[str, Any]] = {}
    with conn.cursor() as cur:
        for index, plugin_id in enumerate(order, start=1):
            plugin_enabled = bool(enabled.get(plugin_id, False))
            order_index = index * 10
            cur.execute(
                """
                UPDATE metadata_plugins
                SET enabled=%s,
                    order_index=%s,
                    updated_at=now()
                WHERE id=%s
                """,
                (plugin_enabled, order_index, plugin_id),
            )
            if cur.rowcount:
                applied[plugin_id] = {
                    "enabled": plugin_enabled,
                    "orderIndex": order_index,
                }
                if summary is not None:
                    summary.counters["metadata_plugins_mapped"] += 1
        cur.execute(
            """
            INSERT INTO app_settings (key, value, is_secret)
            VALUES ('legacy_metadata_plugins_reconciled', %s, false)
            ON CONFLICT (key) DO UPDATE SET
                value=EXCLUDED.value,
                updated_at=now()
            """,
            (
                Jsonb(
                    {
                        "applied": applied,
                        "unsupported": plan["unsupported"],
                    }
                ),
            ),
        )
    if summary is not None:
        for plugin_id in plan["unsupported"]:
            summary.skipped[f"metadata_plugin_unsupported_{plugin_id}"] += 1
    return {"applied": applied, "unsupported": plan["unsupported"]}


def row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return bool(row)


def rows(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    if not table_exists(conn, table):
        return []
    return conn.execute(f'SELECT * FROM "{table}"').fetchall()


def first_existing_file(data_dir: Path, candidates: list[str | None]) -> Path | None:
    for candidate in candidates:
        text = clean_text(candidate)
        if not text:
            continue
        path = data_dir / text.replace("/", os.sep)
        if path.exists() and path.is_file():
            return path
    return None


def storage_key_for(data_dir: Path, path: Path) -> str:
    return path.relative_to(data_dir).as_posix()


def url_fingerprint(kind: str, variant: str, source_url: str) -> str:
    return hashlib.sha256(f"{kind}:{variant}:{source_url}".encode("utf-8")).hexdigest()


def metadata_subset(row: sqlite3.Row, keys: tuple[str, ...]) -> dict[str, Any]:
    source = row_dict(row)
    data: dict[str, Any] = {}
    for key in keys:
        if key not in source:
            continue
        value = json_value(source.get(key))
        if value not in (None, "", [], {}):
            data[key] = value
    return data


class NextImporter:
    def __init__(
        self,
        sqlite_db: Path,
        data_dir: Path,
        *,
        include_security: bool,
        include_personal: bool,
        import_media: bool,
        owner_username: str | None,
    ) -> None:
        self.sqlite_db = sqlite_db
        self.data_dir = data_dir
        self.include_security = include_security
        self.include_personal = include_personal
        self.import_media = import_media
        self.owner_username = owner_username
        self.sqlite = connect_sqlite(sqlite_db)
        self.summary = ImportSummary()
        self.movie_ids: dict[str, uuid.UUID] = {}
        self.person_ids: dict[str, uuid.UUID] = {}
        self.container_ids: dict[tuple[str, str], uuid.UUID] = {}
        self.user_ids: dict[str, uuid.UUID] = {}
        self.group_ids: dict[str, uuid.UUID] = {}
        self.Jsonb = lambda value: value

    def source_counts(self) -> dict[str, int]:
        tracked = (
            "settings",
            "users",
            "credentials",
            "custom_roles",
            "user_roles",
            "role_permissions",
            "movies",
            "people",
            "movie_people",
            "box_sets",
            "box_set_movies",
            "collections",
            "collection_items",
            "vaults",
            "vault_movies",
            "groups",
            "user_groups",
            "group_invites",
            "movie_groups",
            "user_digital_group_access",
            "watchlist",
            "watch_history",
        )
        counts = {}
        for table in tracked:
            if not table_exists(self.sqlite, table):
                counts[table] = 0
                continue
            counts[table] = int(self.sqlite.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        return counts

    def dry_run(self) -> dict[str, Any]:
        counts = self.source_counts()
        media_counts = Counter()
        if self.import_media and self.data_dir.exists():
            for media_dir in ("posters", "profiles", "avatars"):
                root = self.data_dir / media_dir
                if not root.exists():
                    continue
                for item in root.rglob("*"):
                    if item.is_file():
                        media_counts[item.suffix.lower() or "<none>"] += 1
        return {
            "source_database": str(self.sqlite_db),
            "source_database_sha256": sha256_file(self.sqlite_db),
            "data_dir": str(self.data_dir),
            "source_counts": counts,
            "options": {
                "include_security": self.include_security,
                "include_personal": self.include_personal,
                "import_media": self.import_media,
                "owner_username": self.owner_username,
            },
            "media_extensions": dict(sorted(media_counts.items())),
        }

    def run(self) -> dict[str, Any]:
        _, self.Jsonb = _load_psycopg()
        with connect_postgres() as conn:
            run_id = self.create_migration_run(conn)
            conn.commit()
            try:
                with conn.transaction():
                    if self.include_security:
                        self.import_users(conn, run_id)
                    self.import_settings(conn)
                    self.import_people(conn, run_id)
                    self.import_movies(conn, run_id)
                    self.import_movie_credits(conn)
                    self.import_containers(conn, run_id)
                    self.import_container_memberships(conn)
                    if self.include_security:
                        self.import_media_groups(conn, run_id)
                    if self.include_personal:
                        self.import_personal_data(conn)
                self.finish_migration_run(conn, run_id, "completed", self.summary.as_dict(), None)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                self.finish_migration_run(conn, run_id, "failed", self.summary.as_dict(), str(exc))
                conn.commit()
                raise
        return self.summary.as_dict()

    def create_migration_run(self, conn) -> uuid.UUID:
        manifest = {
            "source_database": str(self.sqlite_db),
            "data_dir": str(self.data_dir),
            "source_counts": self.source_counts(),
            "options": {
                "include_security": self.include_security,
                "include_personal": self.include_personal,
                "import_media": self.import_media,
                "owner_username": self.owner_username,
            },
        }
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO migration_runs (
                    source_kind,
                    source_version,
                    source_database_hash,
                    export_manifest,
                    status
                )
                VALUES ('sqlite_data_dir', 'legacy-discvault', %s, %s, 'running')
                RETURNING id
                """,
                (sha256_file(self.sqlite_db), self.Jsonb(manifest)),
            )
            return cur.fetchone()[0]

    def finish_migration_run(
        self,
        conn,
        run_id: uuid.UUID,
        status: str,
        result: dict[str, Any],
        error: str | None,
    ) -> None:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE migration_runs
                SET status=%s, result=%s, error=%s, completed_at=now()
                WHERE id=%s
                """,
                (status, self.Jsonb(result), error, run_id),
            )

    def record_mapping(
        self,
        conn,
        run_id: uuid.UUID,
        source_table: str,
        source_id: Any,
        target_table: str,
        target_id: uuid.UUID,
    ) -> None:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO import_id_mappings (
                    migration_run_id,
                    source_table,
                    source_id,
                    target_table,
                    target_id
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (run_id, source_table, str(source_id), target_table, target_id),
            )

    def existing_user_id_by_username(self, conn, username: str) -> uuid.UUID | None:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE lower(username)=lower(%s)", (username,))
            row = cur.fetchone()
        if not row:
            return None
        return row["id"] if isinstance(row, dict) else row[0]

    def import_settings(self, conn) -> None:
        imported_settings: dict[str, Any] = {}
        for row in rows(self.sqlite, "settings"):
            key = clean_text(row["key"])
            if not key:
                continue
            if any(hint in key.lower() for hint in SECRET_SETTING_HINTS):
                self.summary.skipped["settings_secret"] += 1
                continue
            value = json_value(row["value"])
            imported_settings[key] = value
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app_settings (key, value, is_secret)
                    VALUES (%s, %s, false)
                    ON CONFLICT (key) DO UPDATE SET
                        value=EXCLUDED.value,
                        updated_at=now()
                    """,
                    (key, self.Jsonb(value),),
                )
            self.summary.counters["settings"] += 1
        apply_legacy_metadata_plugin_plan(conn, imported_settings, self.Jsonb, self.summary)

    def import_users(self, conn, run_id: uuid.UUID) -> None:
        user_rows = rows(self.sqlite, "users")
        owner_username = self.owner_username
        if not owner_username:
            for row in user_rows:
                if clean_text(row["role"]) == "admin":
                    owner_username = clean_text(row["username"])
                    break
        for row in user_rows:
            source_id = clean_text(row["id"])
            username = clean_text(row["username"])
            if not source_id or not username:
                self.summary.skipped["users_missing_id_or_username"] += 1
                continue
            user_id = self.existing_user_id_by_username(conn, username) or legacy_user_uuid(source_id)
            avatar_asset_id = None
            if self.import_media:
                avatar_asset_id = self.ensure_media_asset(
                    conn,
                    kind="profile",
                    variant="original",
                    candidates=[f"avatars/{clean_text(row['avatar'])}" if clean_text(row["avatar"]) else None],
                    source_url=None,
                )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (
                        id,
                        username,
                        display_name,
                        first_name,
                        last_name,
                        avatar_asset_id,
                        status,
                        created_at,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, COALESCE(%s, now()))
                    ON CONFLICT (id) DO UPDATE SET
                        username=EXCLUDED.username,
                        display_name=EXCLUDED.display_name,
                        first_name=EXCLUDED.first_name,
                        last_name=EXCLUDED.last_name,
                        avatar_asset_id=COALESCE(EXCLUDED.avatar_asset_id, users.avatar_asset_id),
                        updated_at=now()
                    """,
                    (
                        user_id,
                        username,
                        clean_text(row["display_name"]),
                        clean_text(row["first_name"]),
                        clean_text(row["last_name"]),
                        avatar_asset_id,
                        clean_text(row["created_at"]),
                        clean_text(row["created_at"]),
                    ),
                )
                role_key = legacy_role_key(row["role"], is_owner=username == owner_username)
                cur.execute("SELECT 1 FROM roles WHERE key=%s", (role_key,))
                if cur.fetchone() is None:
                    role_key = "member"
                cur.execute(
                    """
                    INSERT INTO user_roles (user_id, role_id, scope_type, scope_id)
                    SELECT %s, roles.id, 'global', ''
                    FROM roles
                    WHERE roles.key=%s
                    ON CONFLICT DO NOTHING
                    """,
                    (user_id, role_key),
                )
            self.user_ids[source_id] = user_id
            self.record_mapping(conn, run_id, "users", source_id, "users", user_id)
            self.summary.counters["users"] += 1

        for row in rows(self.sqlite, "credentials"):
            user_id = self.user_ids.get(clean_text(row["user_id"]) or "")
            if not user_id:
                self.summary.skipped["credentials_missing_user"] += 1
                continue
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO passkey_credentials (
                        id,
                        user_id,
                        public_key,
                        sign_count,
                        credential_name,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()))
                    ON CONFLICT (id) DO UPDATE SET
                        user_id=EXCLUDED.user_id,
                        sign_count=EXCLUDED.sign_count,
                        credential_name=EXCLUDED.credential_name
                    """,
                    (
                        clean_text(row["id"]),
                        user_id,
                        row["public_key"],
                        int(row["sign_count"] or 0),
                        clean_text(row["credential_name"]),
                        clean_text(row["created_at"]),
                    ),
                )
            self.summary.counters["passkey_credentials"] += 1

    def import_people(self, conn, run_id: uuid.UUID) -> None:
        for row in rows(self.sqlite, "people"):
            source_id = str(row["id"])
            person_id = stable_uuid("people", source_id)
            profile_asset_id = None
            if self.import_media:
                profile_asset_id = self.ensure_media_asset(
                    conn,
                    kind="profile",
                    variant="original",
                    candidates=[
                        f"profiles/{clean_text(row['photo_file'])}" if clean_text(row["photo_file"]) else None,
                    ],
                    source_url=None,
                )
            metadata = metadata_subset(
                row,
                (
                    "photo_file",
                    "sync_revision",
                ),
            )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO people (
                        id,
                        public_id,
                        name,
                        birth_date,
                        death_date,
                        place_of_birth,
                        known_for,
                        profile_asset_id,
                        metadata,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()))
                    ON CONFLICT (public_id) DO UPDATE SET
                        name=EXCLUDED.name,
                        birth_date=EXCLUDED.birth_date,
                        death_date=EXCLUDED.death_date,
                        place_of_birth=EXCLUDED.place_of_birth,
                        known_for=EXCLUDED.known_for,
                        profile_asset_id=COALESCE(EXCLUDED.profile_asset_id, people.profile_asset_id),
                        metadata=EXCLUDED.metadata,
                        updated_at=now()
                    """,
                    (
                        person_id,
                        f"legacy-person-{source_id}",
                        clean_text(row["name"]) or f"Unknown person {source_id}",
                        clean_date(row["birthday"]),
                        clean_date(row["deathday"]),
                        clean_text(row["place_of_birth"]),
                        clean_text(row["known_for"]),
                        profile_asset_id,
                        self.Jsonb(metadata),
                        clean_text(row["updated_at"]),
                    ),
                )
                if clean_text(row["tmdb_id"]):
                    cur.execute(
                        """
                        INSERT INTO person_identifiers (
                            person_id,
                            provider_id,
                            identifier_type,
                            identifier
                        )
                        VALUES (%s, 'tmdb', 'person_id', %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (person_id, str(row["tmdb_id"])),
                    )
                for lang in LOCALIZED_LANGS:
                    biography = clean_text(row[f"biography_{lang}"]) if f"biography_{lang}" in row.keys() else None
                    if biography:
                        cur.execute(
                            """
                            INSERT INTO person_localizations (person_id, lang, biography)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (person_id, lang) DO UPDATE SET
                                biography=EXCLUDED.biography,
                                updated_at=now()
                            """,
                            (person_id, lang, biography),
                        )
                if clean_text(row["biography"]):
                    cur.execute(
                        """
                        INSERT INTO person_localizations (person_id, lang, biography)
                        VALUES (%s, 'en', %s)
                        ON CONFLICT (person_id, lang) DO UPDATE SET
                            biography=EXCLUDED.biography,
                            updated_at=now()
                        """,
                        (person_id, clean_text(row["biography"])),
                    )
            self.person_ids[source_id] = person_id
            self.record_mapping(conn, run_id, "people", source_id, "people", person_id)
            self.summary.counters["people"] += 1

    def import_movies(self, conn, run_id: uuid.UUID) -> None:
        for row in rows(self.sqlite, "movies"):
            source_id = str(row["id"])
            movie_id = stable_uuid("movies", source_id)
            owner_id = self.user_ids.get(clean_text(row["owner_id"]) or "")
            metadata = metadata_subset(
                row,
                (
                    "director",
                    "actor",
                    "producer",
                    "studios",
                    "audience_rating",
                    "extras",
                    "box_set",
                    "imdb_url",
                    "poster",
                    "poster_file",
                    "backdrop",
                    "poster_url",
                    "backdrop_url",
                    "backdrop_urls",
                    "posters",
                    "distributor",
                    "trailer_url",
                    "videos",
                    "edition_release_year",
                    "edition_release_date",
                    "custom_edition_label",
                    "edition_group_id",
                    "super_group_id",
                    "collection_id",
                    "sync_revision",
                ),
            )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO movies (
                        id,
                        public_id,
                        barcode,
                        title,
                        sort_title,
                        original_title,
                        year,
                        release_date,
                        format,
                        edition,
                        edition_type,
                        country,
                        language,
                        runtime_minutes,
                        overview,
                        notes,
                        rating,
                        purchase_date,
                        purchase_price,
                        location,
                        owner_id,
                        metadata,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        COALESCE(%s, now()), COALESCE(%s, now())
                    )
                    ON CONFLICT (public_id) DO UPDATE SET
                        barcode=EXCLUDED.barcode,
                        title=EXCLUDED.title,
                        sort_title=EXCLUDED.sort_title,
                        original_title=EXCLUDED.original_title,
                        year=EXCLUDED.year,
                        release_date=EXCLUDED.release_date,
                        format=EXCLUDED.format,
                        edition=EXCLUDED.edition,
                        edition_type=EXCLUDED.edition_type,
                        country=EXCLUDED.country,
                        language=EXCLUDED.language,
                        runtime_minutes=EXCLUDED.runtime_minutes,
                        overview=EXCLUDED.overview,
                        notes=EXCLUDED.notes,
                        rating=EXCLUDED.rating,
                        purchase_date=EXCLUDED.purchase_date,
                        purchase_price=EXCLUDED.purchase_price,
                        location=EXCLUDED.location,
                        owner_id=COALESCE(EXCLUDED.owner_id, movies.owner_id),
                        metadata=EXCLUDED.metadata,
                        updated_at=now()
                    """,
                    (
                        movie_id,
                        f"legacy-movie-{source_id}",
                        clean_text(row["barcode"]),
                        clean_text(row["title"]) or f"Unknown movie {source_id}",
                        clean_text(row["sort_title"]),
                        clean_text(row["original_title"]),
                        clean_text(row["year"]),
                        clean_date(row["release_date"]),
                        clean_text(row["format"]),
                        clean_text(row["edition"]),
                        clean_text(row["edition_type"]),
                        clean_text(row["country"]),
                        clean_text(row["language"]),
                        clean_int(row["runtime"]),
                        clean_text(row["plot"]),
                        clean_text(row["notes"]),
                        clean_text(row["rating"]),
                        clean_date(row["purchase_date"]),
                        parse_price(row["purchase_price"]),
                        clean_text(row["location"]),
                        owner_id,
                        self.Jsonb(metadata),
                        clean_text(row["added_at"]),
                        clean_text(row["updated_at"]),
                    ),
                )
                self.insert_movie_identifiers(cur, movie_id, row)
                self.insert_movie_localizations(cur, movie_id, row)
                self.import_legacy_genres(cur, movie_id, row)
                cur.execute(
                    """
                    INSERT INTO movie_technical_specs (
                        movie_id,
                        hdr,
                        packaging,
                        screen_ratios,
                        audio_tracks,
                        subtitles,
                        regions,
                        content_ratings
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (movie_id) DO UPDATE SET
                        hdr=EXCLUDED.hdr,
                        packaging=EXCLUDED.packaging,
                        screen_ratios=EXCLUDED.screen_ratios,
                        audio_tracks=EXCLUDED.audio_tracks,
                        subtitles=EXCLUDED.subtitles,
                        regions=EXCLUDED.regions,
                        content_ratings=EXCLUDED.content_ratings,
                        updated_at=now()
                    """,
                    (
                        movie_id,
                        clean_text(row["hdr"]),
                        clean_text(row["packaging"]),
                        clean_text(row["screen_ratios"]),
                        self.Jsonb(json_array(row["audio_tracks"])),
                        self.Jsonb(json_array(row["subtitles"])),
                        self.Jsonb(json_array(row["regions"])),
                        self.Jsonb(json_object(row["content_ratings"])),
                    ),
                )
            self.movie_ids[source_id] = movie_id
            self.record_mapping(conn, run_id, "movies", source_id, "movies", movie_id)
            if self.import_media:
                self.link_movie_media(conn, movie_id, row)
            self.summary.counters["movies"] += 1

    def import_legacy_genres(self, cur, movie_id: uuid.UUID, row: sqlite3.Row) -> None:
        """Migrate the legacy scalar genre column into movie_genres.

        Mirrors migration 037's alias-mapping behavior for movies that are
        migrated straight from a v25 SQLite database rather than an
        already-upgraded v26 Postgres one: split, alias-map, dedupe, and
        keep the raw text plus unmapped tokens in the audit table.
        """
        raw_genre = clean_text(row["genre"]) if "genre" in row.keys() else ""
        if not raw_genre:
            return
        cur.execute(
            """
            SELECT
                EXISTS (
                    SELECT 1
                    FROM movie_genres
                    WHERE movie_id = %s
                )
                OR EXISTS (
                    SELECT 1
                    FROM movie_genre_migration_audit
                    WHERE movie_id = %s
                )
            """,
            (movie_id, movie_id),
        )
        if bool(cur.fetchone()[0]):
            return
        matched_keys, unmatched_parts = map_legacy_genre_text(raw_genre)
        matched_keys = normalize_genre_keys(matched_keys)
        for genre_key in matched_keys:
            cur.execute(
                """
                INSERT INTO movie_genres (movie_id, genre_key)
                VALUES (%s, %s)
                ON CONFLICT (movie_id, genre_key) DO NOTHING
                """,
                (movie_id, genre_key),
            )
        cur.execute(
            """
            INSERT INTO movie_genre_migration_audit (
                movie_id, raw_value, matched_keys, unmatched_parts
            )
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (movie_id) DO NOTHING
            """,
            (
                movie_id,
                raw_genre,
                self.Jsonb(matched_keys),
                self.Jsonb(unmatched_parts),
            ),
        )

    def insert_movie_identifiers(self, cur, movie_id: uuid.UUID, row: sqlite3.Row) -> None:
        identifiers = (
            ("tmdb", "movie_id", clean_text(row["tmdb_id"])),
            ("imdb", "movie_id", clean_text(row["imdb_id"])),
        )
        for provider_id, identifier_type, identifier in identifiers:
            if not identifier:
                continue
            cur.execute(
                """
                INSERT INTO movie_identifiers (
                    movie_id,
                    provider_id,
                    identifier_type,
                    identifier
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (movie_id, provider_id, identifier_type, identifier),
            )

    def insert_movie_localizations(self, cur, movie_id: uuid.UUID, row: sqlite3.Row) -> None:
        if clean_text(row["plot"]):
            cur.execute(
                """
                INSERT INTO movie_localizations (movie_id, lang, title, overview)
                VALUES (%s, 'en', %s, %s)
                ON CONFLICT (movie_id, lang) DO UPDATE SET
                    title=EXCLUDED.title,
                    overview=EXCLUDED.overview,
                    updated_at=now()
                """,
                (movie_id, clean_text(row["title"]), clean_text(row["plot"])),
            )
        for lang in LOCALIZED_LANGS:
            title_key = f"title_{lang}"
            plot_key = f"plot_{lang}"
            if title_key not in row.keys() and plot_key not in row.keys():
                continue
            title = clean_text(row[title_key]) if title_key in row.keys() else None
            overview = clean_text(row[plot_key]) if plot_key in row.keys() else None
            if not title and not overview:
                continue
            cur.execute(
                """
                INSERT INTO movie_localizations (movie_id, lang, title, overview)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (movie_id, lang) DO UPDATE SET
                    title=EXCLUDED.title,
                    overview=EXCLUDED.overview,
                    updated_at=now()
                """,
                (movie_id, lang, title, overview),
            )

    def import_movie_credits(self, conn) -> None:
        for row in rows(self.sqlite, "movie_people"):
            movie_id = self.movie_ids.get(str(row["movie_id"]))
            person_id = self.person_ids.get(str(row["person_id"]))
            if not movie_id or not person_id:
                self.summary.skipped["movie_credits_missing_reference"] += 1
                continue
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO movie_credits (
                        id,
                        movie_id,
                        person_id,
                        credit_type,
                        character,
                        job,
                        sort_order,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()))
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        stable_uuid("movie_people", row["id"]),
                        movie_id,
                        person_id,
                        clean_text(row["role"]) or "credit",
                        clean_text(row["character"]),
                        clean_text(row["job"]),
                        int(row["sort_order"] or 0),
                        clean_text(row["updated_at"]),
                    ),
                )
            self.summary.counters["movie_credits"] += 1

    def import_containers(self, conn, run_id: uuid.UUID) -> None:
        self.import_container_table(conn, run_id, "box_sets", "box_set")
        self.import_container_table(conn, run_id, "collections", "collection")
        self.import_container_table(conn, run_id, "vaults", "vault")

    def import_container_table(
        self,
        conn,
        run_id: uuid.UUID,
        source_table: str,
        container_type: str,
    ) -> None:
        for row in rows(self.sqlite, source_table):
            source_id = str(row["id"])
            container_id = stable_uuid(source_table, source_id)
            primary_movie_id = None
            if "primary_movie_id" in row.keys() and row["primary_movie_id"] is not None:
                primary_movie_id = self.movie_ids.get(str(row["primary_movie_id"]))
            metadata = metadata_subset(
                row,
                (
                    "tmdb_id",
                    "imdb_id",
                    "poster_file",
                    "backdrop",
                    "poster_url",
                    "backdrop_url",
                    "backdrop_urls",
                    "legacy_edition_group_id",
                    "sync_revision",
                ),
            )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO containers (
                        id,
                        public_id,
                        container_type,
                        title,
                        barcode,
                        badge_label,
                        year,
                        description,
                        primary_movie_id,
                        metadata,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        COALESCE(%s, now()), COALESCE(%s, now())
                    )
                    ON CONFLICT (public_id) DO UPDATE SET
                        title=EXCLUDED.title,
                        barcode=EXCLUDED.barcode,
                        badge_label=EXCLUDED.badge_label,
                        year=EXCLUDED.year,
                        description=EXCLUDED.description,
                        primary_movie_id=EXCLUDED.primary_movie_id,
                        metadata=EXCLUDED.metadata,
                        updated_at=now()
                    """,
                    (
                        container_id,
                        f"legacy-{container_type.replace('_', '-')}-{source_id}",
                        container_type,
                        clean_text(row["title"]) or f"Untitled {container_type} {source_id}",
                        clean_text(row["barcode"]) if "barcode" in row.keys() else None,
                        clean_text(row["badge_label"]) if "badge_label" in row.keys() else None,
                        clean_text(row["year"]) if "year" in row.keys() else None,
                        clean_text(row["description"]) if "description" in row.keys() else None,
                        primary_movie_id,
                        self.Jsonb(metadata),
                        clean_text(row["created_at"]) if "created_at" in row.keys() else None,
                        clean_text(row["updated_at"]) if "updated_at" in row.keys() else None,
                    ),
                )
                if clean_text(row["tmdb_id"]) if "tmdb_id" in row.keys() else None:
                    cur.execute(
                        """
                        INSERT INTO container_identifiers (
                            container_id,
                            provider_id,
                            identifier_type,
                            identifier
                        )
                        VALUES (%s, 'tmdb', 'collection_id', %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (container_id, clean_text(row["tmdb_id"])),
                    )
                if clean_text(row["imdb_id"]) if "imdb_id" in row.keys() else None:
                    cur.execute(
                        """
                        INSERT INTO container_identifiers (
                            container_id,
                            provider_id,
                            identifier_type,
                            identifier
                        )
                        VALUES (%s, 'imdb', 'title_id', %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (container_id, clean_text(row["imdb_id"])),
                    )
            self.container_ids[(container_type, source_id)] = container_id
            self.record_mapping(conn, run_id, source_table, source_id, "containers", container_id)
            if self.import_media:
                self.link_container_media(conn, container_id, row)
            self.summary.counters[f"containers_{container_type}"] += 1

    def import_container_memberships(self, conn) -> None:
        for row in rows(self.sqlite, "box_set_movies"):
            container_id = self.container_ids.get(("box_set", str(row["box_set_id"])))
            movie_id = self.movie_ids.get(str(row["movie_id"]))
            if not container_id or not movie_id:
                self.summary.skipped["box_set_movies_missing_reference"] += 1
                continue
            self.insert_container_movie(conn, container_id, movie_id, row["sort_order"], row["updated_at"])
            self.summary.counters["box_set_movies"] += 1

        for row in rows(self.sqlite, "vault_movies"):
            container_id = self.container_ids.get(("vault", str(row["vault_id"])))
            movie_id = self.movie_ids.get(str(row["movie_id"]))
            if not container_id or not movie_id:
                self.summary.skipped["vault_movies_missing_reference"] += 1
                continue
            self.insert_container_movie(conn, container_id, movie_id, row["sort_order"], row["updated_at"])
            self.summary.counters["vault_movies"] += 1

        for row in rows(self.sqlite, "collection_items"):
            collection_id = self.container_ids.get(("collection", str(row["collection_id"])))
            item_type = clean_text(row["item_type"]) or "movie"
            target_id = None
            if item_type == "movie":
                target_id = self.movie_ids.get(str(row["item_id"]))
            elif item_type in {"box_set", "collection", "vault"}:
                target_id = self.container_ids.get((item_type, str(row["item_id"])))
            if not collection_id or not target_id:
                self.summary.skipped["collection_items_missing_reference"] += 1
                continue
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO collection_items (
                        collection_id,
                        item_type,
                        item_id,
                        sort_order,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, COALESCE(%s, now()))
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        collection_id,
                        item_type,
                        target_id,
                        int(row["sort_order"] or 0),
                        clean_text(row["updated_at"]),
                    ),
                )
            self.summary.counters["collection_items"] += 1

    def import_media_groups(self, conn, run_id: uuid.UUID) -> None:
        for row in rows(self.sqlite, "groups"):
            source_id = str(row["id"])
            group_id = stable_uuid("groups", source_id)
            created_by = self.user_ids.get(clean_text(row["created_by"]) or "")
            metadata = metadata_subset(row, ("sync_revision",))
            metadata["legacy_group_id"] = source_id
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO media_groups (
                        id,
                        public_id,
                        name,
                        created_by,
                        hide_digital,
                        metadata,
                        created_at,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, now()), COALESCE(%s, now()))
                    ON CONFLICT (public_id) DO UPDATE SET
                        name=EXCLUDED.name,
                        created_by=COALESCE(EXCLUDED.created_by, media_groups.created_by),
                        hide_digital=EXCLUDED.hide_digital,
                        metadata=EXCLUDED.metadata,
                        updated_at=now()
                    """,
                    (
                        group_id,
                        f"legacy-group-{source_id}",
                        clean_text(row["name"]) or f"Legacy group {source_id}",
                        created_by,
                        bool_value(row["hide_digital"], default=False) if "hide_digital" in row.keys() else False,
                        self.Jsonb(metadata),
                        clean_text(row["created_at"]) if "created_at" in row.keys() else None,
                        clean_text(row["updated_at"]) if "updated_at" in row.keys() else None,
                    ),
                )
            self.group_ids[source_id] = group_id
            self.record_mapping(conn, run_id, "groups", source_id, "media_groups", group_id)
            self.summary.counters["media_groups"] += 1

        for row in rows(self.sqlite, "user_groups"):
            group_id = self.group_ids.get(str(row["group_id"]))
            user_id = self.user_ids.get(clean_text(row["user_id"]) or "")
            if not group_id or not user_id:
                self.summary.skipped["media_group_members_missing_reference"] += 1
                continue
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO media_group_members (group_id, user_id, role)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (group_id, user_id) DO UPDATE SET
                        role=EXCLUDED.role
                    """,
                    (group_id, user_id, legacy_group_member_role(row["role"] if "role" in row.keys() else None)),
                )
            self.summary.counters["media_group_members"] += 1

        for row in rows(self.sqlite, "movie_groups"):
            group_id = self.group_ids.get(str(row["group_id"]))
            movie_id = self.movie_ids.get(str(row["movie_id"]))
            if not group_id or not movie_id:
                self.summary.skipped["media_group_movies_missing_reference"] += 1
                continue
            metadata = metadata_subset(row, ("sync_revision",))
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO media_group_movies (
                        group_id,
                        movie_id,
                        metadata,
                        updated_at
                    )
                    VALUES (%s, %s, %s, COALESCE(%s, now()))
                    ON CONFLICT (group_id, movie_id) DO UPDATE SET
                        metadata=EXCLUDED.metadata,
                        updated_at=EXCLUDED.updated_at
                    """,
                    (
                        group_id,
                        movie_id,
                        self.Jsonb(metadata),
                        clean_text(row["updated_at"]) if "updated_at" in row.keys() else None,
                    ),
                )
            self.summary.counters["media_group_movies"] += 1

        for row in rows(self.sqlite, "group_invites"):
            source_id = str(row["id"])
            group_id = self.group_ids.get(str(row["group_id"]))
            inviter_id = self.user_ids.get(clean_text(row["inviter_id"]) or "")
            invitee_id = self.user_ids.get(clean_text(row["invitee_id"]) or "")
            if not group_id:
                self.summary.skipped["media_group_invites_missing_group"] += 1
                continue
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO media_group_invites (
                        id,
                        group_id,
                        inviter_id,
                        invitee_id,
                        status,
                        metadata,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, now()))
                    ON CONFLICT (id) DO UPDATE SET
                        group_id=EXCLUDED.group_id,
                        inviter_id=EXCLUDED.inviter_id,
                        invitee_id=EXCLUDED.invitee_id,
                        status=EXCLUDED.status,
                        metadata=EXCLUDED.metadata
                    """,
                    (
                        stable_uuid("group_invites", source_id),
                        group_id,
                        inviter_id,
                        invitee_id,
                        clean_text(row["status"]) or "pending",
                        self.Jsonb({"legacy_invite_id": source_id}),
                        clean_text(row["created_at"]) if "created_at" in row.keys() else None,
                    ),
                )
            self.summary.counters["media_group_invites"] += 1

        for row in rows(self.sqlite, "user_digital_group_access"):
            group_id = self.group_ids.get(str(row["group_id"]))
            user_id = self.user_ids.get(clean_text(row["user_id"]) or "")
            if not group_id or not user_id:
                self.summary.skipped["media_group_digital_access_missing_reference"] += 1
                continue
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO media_group_digital_access (group_id, user_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (group_id, user_id),
                )
            self.summary.counters["media_group_digital_access"] += 1

    def insert_container_movie(
        self,
        conn,
        container_id: uuid.UUID,
        movie_id: uuid.UUID,
        sort_order: Any,
        created_at: Any,
    ) -> None:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO container_movies (
                    container_id,
                    movie_id,
                    sort_order,
                    created_at
                )
                VALUES (%s, %s, %s, COALESCE(%s, now()))
                ON CONFLICT DO NOTHING
                """,
                (container_id, movie_id, int(sort_order or 0), clean_text(created_at)),
            )

    def import_personal_data(self, conn) -> None:
        for row in rows(self.sqlite, "watchlist"):
            user_id = self.user_ids.get(clean_text(row["user_id"]) or "")
            movie_id = self.movie_ids.get(str(row["movie_id"]))
            if not user_id or not movie_id:
                self.summary.skipped["watchlist_missing_reference"] += 1
                continue
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO watchlist_items (user_id, movie_id, added_at)
                    VALUES (%s, %s, COALESCE(%s, now()))
                    ON CONFLICT DO NOTHING
                    """,
                    (user_id, movie_id, clean_text(row["added_at"])),
                )
            self.summary.counters["watchlist_items"] += 1

        for row in rows(self.sqlite, "watch_history"):
            user_id = self.user_ids.get(clean_text(row["user_id"]) or "")
            movie_id = self.movie_ids.get(str(row["movie_id"]))
            snapshot = metadata_subset(
                row,
                ("movie_title", "movie_year", "movie_format", "tmdb_id", "imdb_id", "poster", "poster_file"),
            )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO watch_history (
                        id,
                        user_id,
                        movie_id,
                        watched_at,
                        created_at,
                        snapshot
                    )
                    VALUES (%s, %s, %s, COALESCE(%s, now()), COALESCE(%s, now()), %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        stable_uuid("watch_history", row["id"]),
                        user_id,
                        movie_id,
                        clean_text(row["watched_at"]),
                        clean_text(row["created_at"]),
                        self.Jsonb(snapshot),
                    ),
                )
            self.summary.counters["watch_history"] += 1

    def ensure_media_asset(
        self,
        conn,
        *,
        kind: str,
        variant: str,
        candidates: list[str | None],
        source_url: str | None,
        provider_id: str | None = None,
    ) -> uuid.UUID | None:
        path = first_existing_file(self.data_dir, candidates)
        if path:
            storage_key = storage_key_for(self.data_dir, path)
            digest = sha256_file(path)
            size_bytes = path.stat().st_size
            content_type = mimetypes.guess_type(path.name)[0]
        elif source_url:
            storage_key = f"remote/{kind}/{url_fingerprint(kind, variant, source_url)}"
            digest = url_fingerprint(kind, variant, source_url)
            size_bytes = None
            content_type = None
        else:
            return None

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM media_assets
                WHERE storage_backend='local' AND storage_key=%s
                """,
                (storage_key,),
            )
            row = cur.fetchone()
            if row:
                return row[0]
            cur.execute(
                """
                SELECT id FROM media_assets
                WHERE kind=%s AND variant=%s AND sha256=%s
                """,
                (kind, variant, digest),
            )
            row = cur.fetchone()
            if row:
                return row[0]
            media_id = stable_uuid("media_assets", storage_key)
            cur.execute(
                """
                INSERT INTO media_assets (
                    id,
                    kind,
                    variant,
                    storage_backend,
                    storage_key,
                    source_url,
                    provider_id,
                    content_type,
                    size_bytes,
                    sha256
                )
                VALUES (%s, %s, %s, 'local', %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    media_id,
                    kind,
                    variant,
                    storage_key,
                    source_url,
                    provider_id,
                    content_type,
                    size_bytes,
                    digest,
                ),
            )
            self.summary.counters["media_assets"] += 1
            return media_id

    def link_media(
        self,
        conn,
        *,
        entity_type: str,
        entity_id: uuid.UUID,
        media_id: uuid.UUID | None,
        role: str,
        sort_order: int = 0,
    ) -> None:
        if not media_id:
            return
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO entity_media (
                    entity_type,
                    entity_id,
                    media_id,
                    role,
                    is_primary,
                    sort_order
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (entity_type, entity_id, media_id, role, sort_order == 0, sort_order),
            )
        self.summary.counters["entity_media"] += 1

    def link_movie_media(self, conn, movie_id: uuid.UUID, row: sqlite3.Row) -> None:
        poster = self.ensure_media_asset(
            conn,
            kind="poster",
            variant="original",
            candidates=[
                f"posters/{clean_text(row['poster_file'])}" if clean_text(row["poster_file"]) else None,
                clean_text(row["poster"]),
            ],
            source_url=clean_text(row["poster_url"]) or clean_text(row["poster"]),
            provider_id="legacy",
        )
        self.link_media(conn, entity_type="movie", entity_id=movie_id, media_id=poster, role="poster")
        backdrop = self.ensure_media_asset(
            conn,
            kind="backdrop",
            variant="original",
            candidates=[
                f"posters/_variants/backdrop/{clean_text(row['poster_file'])}"
                if clean_text(row["poster_file"])
                else None,
            ],
            source_url=clean_text(row["backdrop_url"]) or clean_text(row["backdrop"]),
            provider_id="legacy",
        )
        self.link_media(conn, entity_type="movie", entity_id=movie_id, media_id=backdrop, role="backdrop")

    def link_container_media(self, conn, container_id: uuid.UUID, row: sqlite3.Row) -> None:
        poster_file = clean_text(row["poster_file"]) if "poster_file" in row.keys() else None
        poster_url = clean_text(row["poster_url"]) if "poster_url" in row.keys() else None
        backdrop = clean_text(row["backdrop"]) if "backdrop" in row.keys() else None
        backdrop_url = clean_text(row["backdrop_url"]) if "backdrop_url" in row.keys() else None
        poster = self.ensure_media_asset(
            conn,
            kind="poster",
            variant="original",
            candidates=[f"posters/{poster_file}" if poster_file else None],
            source_url=poster_url,
            provider_id="legacy",
        )
        self.link_media(conn, entity_type="container", entity_id=container_id, media_id=poster, role="poster")
        backdrop_asset = self.ensure_media_asset(
            conn,
            kind="backdrop",
            variant="original",
            candidates=[f"posters/_variants/backdrop/{poster_file}" if poster_file else None],
            source_url=backdrop_url or backdrop,
            provider_id="legacy",
        )
        self.link_media(
            conn,
            entity_type="container",
            entity_id=container_id,
            media_id=backdrop_asset,
            role="backdrop",
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import DiscVault SQLite data into DiscVault Next PostgreSQL.")
    parser.add_argument("--db", required=True, help="Path to the legacy discvault.db SQLite database.")
    parser.add_argument(
        "--data-dir",
        default="",
        help="Legacy data directory. Defaults to the SQLite database parent directory.",
    )
    parser.add_argument(
        "--include-security",
        action="store_true",
        help="Import users and passkey credentials. Off by default.",
    )
    parser.add_argument(
        "--include-personal",
        action="store_true",
        help="Import watchlist/watch history. Requires --include-security for user mapping.",
    )
    parser.add_argument(
        "--owner-username",
        default="",
        help="Username that should receive the owner role when importing security data.",
    )
    parser.add_argument(
        "--skip-media",
        action="store_true",
        help="Skip media asset import/linking.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect source counts and options without connecting to PostgreSQL.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    sqlite_db = Path(args.db).expanduser().resolve()
    if not sqlite_db.exists():
        raise SystemExit(f"SQLite database not found: {sqlite_db}")
    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else sqlite_db.parent
    if args.include_personal and not args.include_security:
        raise SystemExit("--include-personal requires --include-security so user ownership can be mapped.")

    importer = NextImporter(
        sqlite_db,
        data_dir,
        include_security=bool(args.include_security),
        include_personal=bool(args.include_personal),
        import_media=not bool(args.skip_media),
        owner_username=clean_text(args.owner_username),
    )
    try:
        result = importer.dry_run() if args.dry_run else importer.run()
    except ImportError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
