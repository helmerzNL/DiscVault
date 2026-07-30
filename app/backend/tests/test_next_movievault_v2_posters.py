from __future__ import annotations

import hashlib
import io
import os
import sys
import tempfile
import unittest
from typing import Any
from unittest.mock import patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from PIL import Image

from app.backend import next_movievault_v2_posters as posters
from app.backend.next_movievault_v2 import MovieVaultV2Error

# The MovieVault v2 origin is now enforced at runtime (never read from stored
# settings). Inject the test origin via the MOVIEVAULT_V2_ORIGIN env override so
# existing URL assertions built from "https://movievault.example" stay valid.
_MOVIEVAULT_V2_ORIGIN_ENV_PATCH = patch.dict(
    os.environ, {"MOVIEVAULT_V2_ORIGIN": "https://movievault.example"}
)


def setUpModule():
    _MOVIEVAULT_V2_ORIGIN_ENV_PATCH.start()


def tearDownModule():
    _MOVIEVAULT_V2_ORIGIN_ENV_PATCH.stop()


def _png_bytes(size=(10, 10)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(1, 2, 3)).save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeCursor:
    """Minimal cursor fake recording every statement/params pair executed
    against it, backed by a scripted queue of fetchone/fetchall results."""

    def __init__(self, script: list[Any]):
        self._script = list(script)
        self.calls: list[tuple[str, tuple]] = []
        self._last_result: Any = None

    def execute(self, sql: str, params: tuple = ()):
        self.calls.append((" ".join(sql.split()), params))
        self._last_result = self._script.pop(0) if self._script else None
        return self

    def fetchone(self):
        return self._last_result

    def fetchall(self):
        return self._last_result or []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeTransaction:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def transaction(self):
        return _FakeTransaction()

    def cursor(self):
        return self._cursor


class PosterCacheJobTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env_patch = patch.dict(os.environ, {"DISCVAULT_LEGACY_DATA_DIR": self._tmp.name})
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
        self._tmp.cleanup()

    def _payload(self, **overrides):
        base = {
            "cacheId": "cache-1",
            "assetId": "asset-1",
            "variant": "thumbnail",
            "checksum": None,
            "origin": "https://movievault.example",
            "path": "/v2/assets/asset-1/thumbnail",
        }
        base.update(overrides)
        return base

    def test_anonymous_request_carries_no_auth_cookie_or_telemetry_kwargs(self):
        content = _png_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        cursor = _FakeCursor(
            [
                {"id": "cache-1", "media_asset_id": None},  # SELECT ... FOR UPDATE
                {"id": "media-1"},  # INSERT media_assets RETURNING id
                None,  # UPDATE cache row
            ]
        )
        conn = _FakeConn(cursor)
        with patch.object(
            posters,
            "_request",
            return_value=(200, content, {"content-type": "image/png"}),
        ) as request:
            result = posters.run_poster_cache_job(conn, self._payload(checksum=checksum))
        self.assertEqual(result["outcome"], "ready")
        request.assert_called_once()
        _, kwargs = request.call_args
        # Bounded transport controls only - no auth, cookie, identity or
        # telemetry kwargs are ever passed to the anonymous fetch.
        self.assertEqual(
            set(kwargs),
            {"accept", "timeout_seconds", "maximum_bytes", "passthrough_statuses"},
        )
        self.assertEqual(kwargs["passthrough_statuses"], frozenset({429}))

    def test_rate_limited_fetch_waits_for_retry_after_then_succeeds(self):
        content = _png_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        cursor = _FakeCursor(
            [
                {"id": "cache-1", "media_asset_id": None},  # SELECT ... FOR UPDATE
                {"id": "media-1"},  # INSERT media_assets RETURNING id
                None,  # UPDATE cache row
            ]
        )
        conn = _FakeConn(cursor)
        waits: list[float] = []
        with patch.object(
            posters,
            "_request",
            side_effect=[
                (429, b"", {"retry-after": "3"}),
                (200, content, {"content-type": "image/png"}),
            ],
        ) as request:
            result = posters.run_poster_cache_job(
                conn,
                self._payload(checksum=checksum),
                sleep=waits.append,
            )

        self.assertEqual(result["outcome"], "ready")
        self.assertEqual(waits, [3])
        self.assertEqual(request.call_count, 2)

    def test_rate_limit_wait_is_clamped_and_never_unbounded(self):
        cases = {
            "over_maximum": ("3600", posters.POSTER_RATE_LIMIT_MAX_WAIT_SECONDS),
            "zero": ("0", posters.POSTER_RATE_LIMIT_MIN_WAIT_SECONDS),
            "negative": ("-10", posters.POSTER_RATE_LIMIT_MIN_WAIT_SECONDS),
            "missing": (None, posters.POSTER_RATE_LIMIT_MIN_WAIT_SECONDS),
            "unparseable": ("soon", posters.POSTER_RATE_LIMIT_MIN_WAIT_SECONDS),
            "http_date_in_past": (
                "Wed, 21 Oct 2015 07:28:00 GMT",
                posters.POSTER_RATE_LIMIT_MIN_WAIT_SECONDS,
            ),
        }
        for name, (header, expected) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(posters._retry_after_seconds(header), expected)

    def test_rate_limited_beyond_retries_degrades_without_raising(self):
        cursor = _FakeCursor(
            [
                {"id": "cache-1", "media_asset_id": "media-prior"},  # SELECT FOR UPDATE
                None,  # UPDATE cache row
            ]
        )
        conn = _FakeConn(cursor)
        waits: list[float] = []
        with patch.object(
            posters,
            "_request",
            return_value=(429, b"", {"retry-after": "1"}),
        ) as request:
            result = posters.run_poster_cache_job(
                conn,
                self._payload(checksum="a" * 64),
                sleep=waits.append,
            )

        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(request.call_count, posters.POSTER_RATE_LIMIT_MAX_RETRIES + 1)
        self.assertEqual(len(waits), posters.POSTER_RATE_LIMIT_MAX_RETRIES)
        # The prior valid poster keeps serving: media_asset_id is untouched and
        # the row is only degraded.
        update = [call for call in cursor.calls if "UPDATE movievault_v2_poster_cache" in call[0]]
        self.assertEqual(update[-1][1][0], "degraded")
        self.assertEqual(update[-1][1][1], "rate_limited")
        self.assertNotIn("media_asset_id", update[-1][0])

    def test_success_activates_media_asset_and_marks_cache_ready(self):
        content = _png_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        cursor = _FakeCursor(
            [
                {"id": "cache-1", "media_asset_id": None},
                {"id": "media-42"},
                None,
            ]
        )
        conn = _FakeConn(cursor)
        with patch.object(
            posters,
            "_request",
            return_value=(200, content, {"content-type": "image/png"}),
        ):
            result = posters.run_poster_cache_job(conn, self._payload(checksum=checksum))

        self.assertEqual(result["outcome"], "ready")
        self.assertEqual(result["mediaAssetId"], "media-42")
        self.assertFalse(result.get("evicted"))
        insert_sql, insert_params = cursor.calls[1]
        self.assertIn("INSERT INTO media_assets", insert_sql)
        self.assertEqual(insert_params[-1], checksum)
        update_sql, update_params = cursor.calls[-1]
        self.assertIn("status = 'ready'", update_sql)
        self.assertIn("media_asset_id = %s", update_sql)
        self.assertEqual(update_params[0], "media-42")

    def test_checksum_mismatch_preserves_prior_poster_and_marks_degraded(self):
        content = _png_bytes()
        wrong_checksum = "f" * 64
        cursor = _FakeCursor(
            [
                {"id": "cache-1", "media_asset_id": "prior-media-9"},
                None,  # UPDATE cache row (failure path)
            ]
        )
        conn = _FakeConn(cursor)
        with patch.object(
            posters,
            "_request",
            return_value=(200, content, {"content-type": "image/png"}),
        ):
            result = posters.run_poster_cache_job(conn, self._payload(checksum=wrong_checksum))

        self.assertEqual(result["outcome"], "failed")
        # Only two statements ran: the row lock SELECT and the failure UPDATE.
        # No media_assets INSERT/UPDATE happened - no partial bytes activated.
        self.assertEqual(len(cursor.calls), 2)
        update_sql, update_params = cursor.calls[-1]
        self.assertIn("status = %s", update_sql)
        self.assertNotIn("media_asset_id = %s", update_sql)
        self.assertEqual(update_params[0], "degraded")
        self.assertEqual(update_params[1], "checksum_mismatch")

    def test_missing_prior_poster_marks_error_not_degraded(self):
        content = _png_bytes()
        wrong_checksum = "e" * 64
        cursor = _FakeCursor(
            [
                {"id": "cache-1", "media_asset_id": None},
                None,
            ]
        )
        conn = _FakeConn(cursor)
        with patch.object(
            posters,
            "_request",
            return_value=(200, content, {"content-type": "image/png"}),
        ):
            result = posters.run_poster_cache_job(conn, self._payload(checksum=wrong_checksum))
        self.assertEqual(result["outcome"], "failed")
        _, update_params = cursor.calls[-1]
        self.assertEqual(update_params[0], "error")

    def test_byte_limit_exceeded_rejected_before_decode(self):
        checksum = "a" * 64
        oversized = b"\x00" * (posters.POSTER_MAX_BYTES + 1)
        cursor = _FakeCursor([{"id": "cache-1", "media_asset_id": None}, None])
        conn = _FakeConn(cursor)
        with patch.object(
            posters,
            "_request",
            return_value=(200, oversized, {"content-type": "image/png"}),
        ):
            result = posters.run_poster_cache_job(conn, self._payload(checksum=checksum))
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(cursor.calls[-1][1][1], "byte_limit_invalid")

    def test_wrong_content_type_rejected(self):
        content = _png_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        cursor = _FakeCursor([{"id": "cache-1", "media_asset_id": None}, None])
        conn = _FakeConn(cursor)
        with patch.object(
            posters,
            "_request",
            return_value=(200, content, {"content-type": "text/html"}),
        ):
            result = posters.run_poster_cache_job(conn, self._payload(checksum=checksum))
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(cursor.calls[-1][1][1], "content_type_invalid")

    def test_undecodable_bytes_rejected_as_decode_invalid(self):
        content = b"not a real image but matches checksum"
        checksum = hashlib.sha256(content).hexdigest()
        cursor = _FakeCursor([{"id": "cache-1", "media_asset_id": None}, None])
        conn = _FakeConn(cursor)
        with patch.object(
            posters,
            "_request",
            return_value=(200, content, {"content-type": "image/png"}),
        ):
            result = posters.run_poster_cache_job(conn, self._payload(checksum=checksum))
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(cursor.calls[-1][1][1], "poster_decode_invalid")

    def test_oversized_dimensions_rejected(self):
        content = _png_bytes(size=(20, 20))
        checksum = hashlib.sha256(content).hexdigest()
        cursor = _FakeCursor([{"id": "cache-1", "media_asset_id": None}, None])
        conn = _FakeConn(cursor)
        with (
            patch.object(posters, "POSTER_MAX_DIMENSION", 10),
            patch.object(
                posters,
                "_request",
                return_value=(200, content, {"content-type": "image/png"}),
            ),
        ):
            result = posters.run_poster_cache_job(conn, self._payload(checksum=checksum))
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(cursor.calls[-1][1][1], "poster_dimensions_invalid")

    def test_remote_error_from_request_is_caught_not_raised(self):
        checksum = "b" * 64
        cursor = _FakeCursor([{"id": "cache-1", "media_asset_id": None}, None])
        conn = _FakeConn(cursor)
        with patch.object(
            posters,
            "_request",
            side_effect=MovieVaultV2Error("http_error"),
        ):
            result = posters.run_poster_cache_job(conn, self._payload(checksum=checksum))
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(cursor.calls[-1][1][1], "http_error")

    def test_missing_cache_row_is_skipped_not_an_error(self):
        cursor = _FakeCursor([None])
        conn = _FakeConn(cursor)
        result = posters.run_poster_cache_job(conn, self._payload(checksum="c" * 64))
        self.assertEqual(result["skipped"], "cache_row_missing")
        self.assertEqual(len(cursor.calls), 1)

    def test_invalid_payload_shape_raises_without_touching_database(self):
        cursor = _FakeCursor([])
        conn = _FakeConn(cursor)
        with self.assertRaisesRegex(MovieVaultV2Error, "^poster_job_payload_invalid$"):
            posters.run_poster_cache_job(conn, {"cacheId": "cache-1"})
        self.assertEqual(cursor.calls, [])

    def test_http_404_evicts_cache_row_and_soft_deletes_entity_media(self):
        """A 404 response marks status=evicted, NULLs media_asset_id, and
        soft-deletes entity_media rows that referenced the old asset."""
        cursor = _FakeCursor(
            [
                {"id": "cache-1", "media_asset_id": "prior-media-7"},  # SELECT FOR UPDATE
                None,  # UPDATE cache row (eviction)
                None,  # UPDATE entity_media (soft-delete)
            ]
        )
        conn = _FakeConn(cursor)
        with patch.object(
            posters,
            "_request",
            return_value=(404, b"", {"content-type": "text/html"}),
        ):
            result = posters.run_poster_cache_job(conn, self._payload(checksum="a" * 64))

        self.assertEqual(result["outcome"], "failed")
        self.assertTrue(result["evicted"])
        self.assertNotIn("mediaAssetId", result)

        evict_update = [c for c in cursor.calls if "status = 'evicted'" in c[0]]
        self.assertEqual(len(evict_update), 1)
        # media_asset_id should be set to NULL in the eviction UPDATE
        self.assertIn("media_asset_id = NULL", evict_update[0][0])

        em_update = [c for c in cursor.calls if "UPDATE entity_media" in c[0]]
        self.assertEqual(len(em_update), 1)
        self.assertIn("deleted_at", em_update[0][0])
        self.assertEqual(em_update[0][1][0], "prior-media-7")

    def test_http_404_without_prior_media_asset_only_evicts_cache(self):
        """A 404 with no prior media_asset_id evicts the cache row but skips
        the entity_media soft-delete (nothing to unlink)."""
        cursor = _FakeCursor(
            [
                {"id": "cache-1", "media_asset_id": None},  # SELECT FOR UPDATE
                None,  # UPDATE cache row (eviction)
            ]
        )
        conn = _FakeConn(cursor)
        with patch.object(
            posters,
            "_request",
            return_value=(404, b"", {}),
        ):
            result = posters.run_poster_cache_job(conn, self._payload(checksum="b" * 64))

        self.assertTrue(result["evicted"])
        em_update = [c for c in cursor.calls if "UPDATE entity_media" in c[0]]
        self.assertEqual(len(em_update), 0)


class LinkBoxSetPosterToContainersTests(unittest.TestCase):
    def _make_cursor_and_conn(self, container_rows):
        cursor = _FakeCursor([container_rows])
        conn = _FakeConn(cursor)
        return cursor, conn

    def test_links_matching_container(self):
        import types
        import uuid
        container_id = str(uuid.uuid4())
        media_id = str(uuid.uuid4())
        cursor, conn = self._make_cursor_and_conn([{"id": container_id}])
        linked_calls: list[dict] = []

        def fake_link(conn, *, container_id, kind, media_asset_id, provider_id, sort_order, primary):
            linked_calls.append({
                "container_id": container_id,
                "media_asset_id": media_asset_id,
                "primary": primary,
            })
            return True

        fake_app = types.SimpleNamespace(link_local_media_asset_to_container=fake_link)
        with patch.dict("sys.modules", {"app.backend.next_app": fake_app}):
            result = posters.link_box_set_poster_to_containers(
                conn, asset_id="asset-99", media_asset_id=media_id
            )

        self.assertEqual(result, 1)
        self.assertEqual(len(linked_calls), 1)
        self.assertTrue(linked_calls[0]["primary"])

    def test_no_matching_containers_returns_zero(self):
        cursor, conn = self._make_cursor_and_conn([])
        result = posters.link_box_set_poster_to_containers(
            conn, asset_id="asset-00", media_asset_id="not-a-uuid"
        )
        self.assertEqual(result, 0)

    def test_invalid_media_asset_id_returns_zero(self):
        """Invalid UUID for media_asset_id short-circuits before linking."""
        cursor, conn = self._make_cursor_and_conn([{"id": "some-container"}])
        result = posters.link_box_set_poster_to_containers(
            conn, asset_id="asset-01", media_asset_id="not-a-uuid"
        )
        self.assertEqual(result, 0)


class PosterCleanupTests(unittest.TestCase):
    def test_removes_only_unreferenced_stale_rows_and_deletes_orphan_media_asset(self):
        cursor = _FakeCursor(
            [
                [{"id": "cache-1", "media_asset_id": "media-1"}],  # candidate select
                {"storage_backend": "local", "storage_key": "media/x.png"},  # media_assets lookup
                None,  # still-referenced check -> no row
                None,  # DELETE cache row
                None,  # DELETE media_assets row
            ]
        )
        conn = _FakeConn(cursor)
        result = posters.run_poster_cleanup(conn, limit=50, retention_seconds=604800)
        self.assertEqual(result["removed"], 1)
        delete_calls = [sql for sql, _ in cursor.calls if sql.startswith("DELETE")]
        self.assertEqual(len(delete_calls), 2)

    def test_keeps_media_asset_when_still_referenced_by_another_cache_row(self):
        cursor = _FakeCursor(
            [
                [{"id": "cache-1", "media_asset_id": "media-1"}],
                {"storage_backend": "local", "storage_key": "media/x.png"},
                {"1": 1},  # still-referenced check -> a row exists
                None,  # DELETE cache row only
            ]
        )
        conn = _FakeConn(cursor)
        result = posters.run_poster_cleanup(conn)
        self.assertEqual(result["removed"], 1)
        delete_calls = [sql for sql, _ in cursor.calls if sql.startswith("DELETE")]
        self.assertEqual(len(delete_calls), 1)
        self.assertIn("movievault_v2_poster_cache", delete_calls[0])

    def test_no_candidates_removes_nothing(self):
        cursor = _FakeCursor([[]])
        conn = _FakeConn(cursor)
        result = posters.run_poster_cleanup(conn)
        self.assertEqual(result["removed"], 0)

    def test_default_retention_matches_seven_day_artwork_convention(self):
        self.assertEqual(posters.POSTER_CACHE_RETENTION_SECONDS, 7 * 24 * 60 * 60)


if __name__ == "__main__":
    unittest.main()
