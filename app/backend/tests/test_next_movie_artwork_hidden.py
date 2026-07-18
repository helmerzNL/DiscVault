import os
import inspect
import sys
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_app


class _ArtworkCursor:
    def __init__(self, *, media, replacement=None, movie_metadata=None, trash_row=None):
        self.media = dict(media)
        self.replacement = dict(replacement) if replacement else None
        self.movie_metadata = dict(movie_metadata or {})
        self.trash_row = trash_row
        self.calls = []
        self.current = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        sql = " ".join(query.split())
        self.calls.append((sql, params))
        self.current = None
        if sql.startswith("SELECT id, metadata FROM movies"):
            self.current = {"id": params[0], "metadata": self.movie_metadata}
        elif sql.startswith("SELECT id FROM movies"):
            self.current = {"id": params[0]}
        elif "FROM entity_media em" in sql and "em.media_id=%s" in sql:
            self.current = dict(self.media)
        elif "SET hidden_at=now()" in sql:
            self.current = {"hidden_at": datetime(2026, 7, 18, tzinfo=timezone.utc)}
        elif "ORDER BY em.is_primary DESC" in sql:
            self.current = dict(self.replacement) if self.replacement else None
        elif "SET deleted_at=now()" in sql:
            self.current = self.trash_row or {
                "deleted_at": datetime(2026, 7, 18, tzinfo=timezone.utc),
                "purge_after": None,
            }

    def fetchone(self):
        return self.current


class _ArtworkConnection:
    def __init__(self, cursor):
        self.test_cursor = cursor

    def cursor(self):
        return self.test_cursor


def _media(media_id, *, hidden_at=None, primary=False, source_url="https://example.test/poster.jpg"):
    return {
        "id": media_id,
        "kind": "poster",
        "variant": "original",
        "storage_backend": "remote",
        "storage_key": f"remote/{media_id}",
        "source_url": source_url,
        "provider_id": "unit",
        "content_type": "image/jpeg",
        "width": 1000,
        "height": 1500,
        "size_bytes": 10,
        "sha256": "abc",
        "metadata": {},
        "role": "poster",
        "is_primary": primary,
        "sort_order": 0 if primary else 1,
        "hidden_at": hidden_at,
    }


class NextMovieArtworkHiddenTests(unittest.TestCase):
    def test_reuploading_hidden_artwork_unhides_the_reused_link(self):
        source = inspect.getsource(next_app.create_uploaded_movie_media_asset)

        self.assertIn("hidden_at=NULL", source)
        self.assertIn("is_primary=EXCLUDED.is_primary", source)

    def test_hide_sets_hidden_without_trashing_and_replaces_primary(self):
        movie_id = uuid.uuid4()
        media_id = uuid.uuid4()
        replacement_id = uuid.uuid4()
        cursor = _ArtworkCursor(
            media=_media(media_id, primary=True),
            replacement=_media(
                replacement_id,
                source_url="https://example.test/replacement.jpg",
            ),
            movie_metadata={"poster_url": "https://example.test/poster.jpg"},
        )
        conn = _ArtworkConnection(cursor)
        sync_payloads = []
        with (
            patch.object(next_app, "table_exists", return_value=True),
            patch.object(
                next_app,
                "record_sync_change",
                side_effect=lambda _conn, _movie_id, payload: sync_payloads.append(payload) or 7,
            ),
        ):
            result = next_app.set_movie_artwork_hidden_state(
                conn,
                movie_id=movie_id,
                media_id=media_id,
                kind="poster",
                hidden=True,
            )

        sql = "\n".join(call[0] for call in cursor.calls)
        self.assertIn("SET hidden_at=now(), is_primary=false", sql)
        self.assertNotIn("SET deleted_at=now()", sql)
        self.assertTrue(result["hidden"])
        self.assertEqual(result["replacement"]["id"], replacement_id)
        self.assertEqual(result["revision"], 7)
        self.assertEqual(sync_payloads[0]["operation"], "movie.media_hidden")

    def test_unhide_restores_visibility_without_making_primary(self):
        movie_id = uuid.uuid4()
        media_id = uuid.uuid4()
        cursor = _ArtworkCursor(
            media=_media(
                media_id,
                hidden_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
            )
        )
        conn = _ArtworkConnection(cursor)
        sync_payloads = []
        with (
            patch.object(next_app, "table_exists", return_value=True),
            patch.object(
                next_app,
                "record_sync_change",
                side_effect=lambda _conn, _movie_id, payload: sync_payloads.append(payload) or 8,
            ),
        ):
            result = next_app.set_movie_artwork_hidden_state(
                conn,
                movie_id=movie_id,
                media_id=media_id,
                kind="poster",
                hidden=False,
            )

        sql = "\n".join(call[0] for call in cursor.calls)
        self.assertIn("SET hidden_at=NULL, is_primary=false", sql)
        self.assertFalse(result["hidden"])
        self.assertFalse(result["media"]["is_primary"])
        self.assertEqual(sync_payloads[0]["operation"], "movie.media_unhidden")

    def test_deleting_hidden_artwork_clears_hidden_state_and_uses_trash(self):
        movie_id = uuid.uuid4()
        media_id = uuid.uuid4()
        cursor = _ArtworkCursor(
            media=_media(
                media_id,
                hidden_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
            )
        )
        conn = _ArtworkConnection(cursor)
        sync_payloads = []
        with (
            patch.object(next_app, "table_exists", return_value=True),
            patch.object(
                next_app,
                "artwork_trash_settings",
                return_value={
                    "purgeEnabled": False,
                    "retentionInterval": "7 days",
                },
            ),
            patch.object(
                next_app,
                "record_sync_change",
                side_effect=lambda _conn, _movie_id, payload: sync_payloads.append(payload) or 9,
            ),
        ):
            result = next_app.delete_entity_artwork_media_asset(
                conn,
                entity_type="movie",
                entity_id=movie_id,
                media_id=media_id,
                kind="poster",
            )

        trash_sql = next(
            sql for sql, _params in cursor.calls if "SET deleted_at=now()" in sql
        )
        self.assertIn("hidden_at=NULL", trash_sql)
        self.assertTrue(result["trashed"])
        self.assertTrue(sync_payloads[0]["wasHidden"])
        self.assertEqual(sync_payloads[0]["operation"], "movie.media_trashed")


if __name__ == "__main__":
    unittest.main()
