import json
import os
import sys
import tempfile
import unittest
import uuid
import zipfile


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_backup import BACKUP_FORMAT
from app.backend.next_backup import BACKUP_FORMAT_VERSION
from app.backend.next_backup import BACKUP_TABLES
from app.backend.next_backup import validate_backup_zip


def empty_tables():
    return {table: [] for table in BACKUP_TABLES}


def write_backup(path, tables, *, media_files=None, omit_tables=None):
    media_files = media_files or {}
    omitted = set(omit_tables or [])
    manifest = {
        "format": BACKUP_FORMAT,
        "formatVersion": BACKUP_FORMAT_VERSION,
        "scope": "functional_collection",
        "createdAt": "2026-05-29T00:00:00Z",
        "generator": {"service": "unit-test"},
        "excludedScopes": ["auth", "plugin_secrets"],
        "tables": {name: {"count": len(rows)} for name, rows in tables.items()},
        "media": {"mode": "embedded_local_files"},
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for name in BACKUP_TABLES:
            if name in omitted:
                continue
            zf.writestr(f"data/{name}.json", json.dumps(tables[name]))
        for name, data in media_files.items():
            zf.writestr(name, data)


class NextBackupValidationTests(unittest.TestCase):
    def test_validate_accepts_collection_graph_with_embedded_media(self):
        movie_id = str(uuid.uuid4())
        person_id = str(uuid.uuid4())
        media_id = str(uuid.uuid4())
        collection_id = str(uuid.uuid4())
        tables = empty_tables()
        tables["media_assets"].append(
            {
                "id": media_id,
                "kind": "poster",
                "variant": "original",
                "storage_backend": "local",
                "storage_key": "posters/poster.jpg",
                "source_url": None,
                "provider_id": "unit",
                "content_type": "image/jpeg",
                "width": None,
                "height": None,
                "size_bytes": 3,
                "sha256": "abc",
                "metadata": {},
                "created_at": "2026-05-29T00:00:00Z",
            }
        )
        tables["movies"].append(
            {
                "id": movie_id,
                "public_id": "unit-movie-1",
                "barcode": "UNIT-1",
                "title": "Unit Movie",
                "sort_title": "Unit Movie",
                "original_title": "Unit Movie",
                "year": "2026",
                "release_date": None,
                "format": "4K UHD",
                "edition": None,
                "edition_type": None,
                "country": None,
                "language": None,
                "runtime_minutes": None,
                "overview": None,
                "notes": None,
                "rating": None,
                "purchase_date": None,
                "purchase_price": None,
                "location": None,
                "owner_id": None,
                "metadata": {},
                "created_at": "2026-05-29T00:00:00Z",
                "updated_at": "2026-05-29T00:00:00Z",
            }
        )
        tables["people"].append(
            {
                "id": person_id,
                "public_id": "unit-person-1",
                "name": "Unit Person",
                "birth_date": None,
                "death_date": None,
                "place_of_birth": None,
                "known_for": "Acting",
                "profile_asset_id": None,
                "metadata": {},
                "created_at": "2026-05-29T00:00:00Z",
                "updated_at": "2026-05-29T00:00:00Z",
            }
        )
        tables["movie_credits"].append(
            {
                "id": str(uuid.uuid4()),
                "movie_id": movie_id,
                "person_id": person_id,
                "credit_type": "actor",
                "character": "Self",
                "job": None,
                "sort_order": 0,
                "created_at": "2026-05-29T00:00:00Z",
            }
        )
        tables["containers"].append(
            {
                "id": collection_id,
                "public_id": "unit-collection-1",
                "container_type": "collection",
                "title": "Unit Collection",
                "barcode": None,
                "badge_label": None,
                "year": None,
                "description": None,
                "primary_movie_id": None,
                "metadata": {},
                "created_at": "2026-05-29T00:00:00Z",
                "updated_at": "2026-05-29T00:00:00Z",
            }
        )
        tables["collection_items"].append(
            {
                "collection_id": collection_id,
                "item_type": "movie",
                "item_id": movie_id,
                "sort_order": 1,
                "created_at": "2026-05-29T00:00:00Z",
            }
        )
        tables["entity_media"].append(
            {
                "entity_type": "movie",
                "entity_id": movie_id,
                "media_id": media_id,
                "role": "poster",
                "is_primary": True,
                "sort_order": 0,
                "created_at": "2026-05-29T00:00:00Z",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "backup.zip")
            write_backup(path, tables, media_files={"media/posters/poster.jpg": b"jpg"})
            report = validate_backup_zip(path)

        self.assertTrue(report["valid"], report)
        self.assertEqual(report["tables"]["movies"]["count"], 1)
        self.assertEqual(report["media"]["embedded"], 1)
        self.assertEqual(report["media"]["missing"], 0)

    def test_validate_rejects_broken_collection_item_reference(self):
        collection_id = str(uuid.uuid4())
        tables = empty_tables()
        tables["containers"].append(
            {
                "id": collection_id,
                "public_id": "unit-collection-1",
                "container_type": "collection",
                "title": "Unit Collection",
                "barcode": None,
                "badge_label": None,
                "year": None,
                "description": None,
                "primary_movie_id": None,
                "metadata": {},
                "created_at": "2026-05-29T00:00:00Z",
                "updated_at": "2026-05-29T00:00:00Z",
            }
        )
        tables["collection_items"].append(
            {
                "collection_id": collection_id,
                "item_type": "movie",
                "item_id": str(uuid.uuid4()),
                "sort_order": 1,
                "created_at": "2026-05-29T00:00:00Z",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "backup.zip")
            write_backup(path, tables)
            report = validate_backup_zip(path)

        self.assertFalse(report["valid"], report)
        self.assertTrue(any("collection_items" in error for error in report["errors"]))

    def test_validate_accepts_media_group_movie_links(self):
        movie_id = str(uuid.uuid4())
        group_id = str(uuid.uuid4())
        tables = empty_tables()
        tables["movies"].append(
            {
                "id": movie_id,
                "public_id": "unit-movie-1",
                "barcode": None,
                "title": "Unit Movie",
                "sort_title": "Unit Movie",
                "original_title": None,
                "year": None,
                "release_date": None,
                "format": None,
                "edition": None,
                "edition_type": None,
                "country": None,
                "language": None,
                "runtime_minutes": None,
                "overview": None,
                "notes": None,
                "rating": None,
                "purchase_date": None,
                "purchase_price": None,
                "location": None,
                "owner_id": None,
                "metadata": {},
                "created_at": "2026-05-29T00:00:00Z",
                "updated_at": "2026-05-29T00:00:00Z",
            }
        )
        tables["media_groups"].append(
            {
                "id": group_id,
                "public_id": "unit-group",
                "name": "Unit Group",
                "created_by": None,
                "hide_digital": False,
                "metadata": {},
                "created_at": "2026-05-29T00:00:00Z",
                "updated_at": "2026-05-29T00:00:00Z",
            }
        )
        tables["media_group_movies"].append(
            {
                "group_id": group_id,
                "movie_id": movie_id,
                "metadata": {},
                "created_at": "2026-05-29T00:00:00Z",
                "updated_at": "2026-05-29T00:00:00Z",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "backup.zip")
            write_backup(path, tables)
            report = validate_backup_zip(path)

        self.assertTrue(report["valid"], report)
        self.assertEqual(report["tables"]["media_group_movies"]["count"], 1)

    def test_validate_accepts_old_backup_without_optional_tables(self):
        tables = empty_tables()
        optional_tables = {"media_groups", "media_group_movies", "watchlist_items", "watch_history"}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "backup.zip")
            write_backup(path, tables, omit_tables=optional_tables)
            report = validate_backup_zip(path)

        self.assertTrue(report["valid"], report)
        for name in optional_tables:
            self.assertEqual(report["tables"][name]["count"], 0)

    def test_validate_accepts_collection_only_backup_without_people_or_media(self):
        movie_id = str(uuid.uuid4())
        tables = empty_tables()
        tables["movies"].append(
            {
                "id": movie_id,
                "public_id": "unit-movie-1",
                "barcode": None,
                "title": "Unit Movie",
                "sort_title": "Unit Movie",
                "original_title": None,
                "year": None,
                "release_date": None,
                "format": None,
                "edition": None,
                "edition_type": None,
                "country": None,
                "language": None,
                "runtime_minutes": None,
                "overview": None,
                "notes": None,
                "rating": None,
                "purchase_date": None,
                "purchase_price": None,
                "location": None,
                "owner_id": None,
                "metadata": {},
                "created_at": "2026-05-29T00:00:00Z",
                "updated_at": "2026-05-29T00:00:00Z",
            }
        )
        omit = {
            "media_assets",
            "entity_media",
            "people",
            "person_identifiers",
            "person_localizations",
            "movie_credits",
            "media_groups",
            "media_group_movies",
            "media_group_members",
            "users",
            "roles",
            "role_permissions",
            "user_roles",
            "recovery_codes",
            "api_access_tokens",
            "watchlist_items",
            "watch_history",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "backup.zip")
            write_backup(path, tables, omit_tables=omit)
            report = validate_backup_zip(path)

        self.assertTrue(report["valid"], report)
        self.assertEqual(report["tables"]["movies"]["count"], 1)
        self.assertEqual(report["tables"]["people"]["count"], 0)

    def test_validate_accepts_legacy_format_version_one(self):
        tables = empty_tables()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "backup.zip")
            manifest = {
                "format": BACKUP_FORMAT,
                "formatVersion": 1,
                "scope": "functional_collection",
                "createdAt": "2026-05-29T00:00:00Z",
                "tables": {name: {"count": 0} for name in tables},
                "media": {"mode": "embedded_local_files"},
            }
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("manifest.json", json.dumps(manifest))
                for name in BACKUP_TABLES:
                    zf.writestr(f"data/{name}.json", json.dumps(tables[name]))
            report = validate_backup_zip(path)

        self.assertTrue(report["valid"], report)


if __name__ == "__main__":
    unittest.main()
