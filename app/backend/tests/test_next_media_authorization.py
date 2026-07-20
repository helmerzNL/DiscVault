import os
import tempfile
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import ANY, patch

from app.backend import next_app


class _ReferenceCursor:
    def __init__(self, rows, queries):
        self._rows = rows
        self._queries = queries
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split())
        self._queries.append((normalized, params))
        if "FROM entity_media" in normalized:
            self._result = self._rows["entity_media"]
        elif "FROM people" in normalized:
            self._result = self._rows["people"]
        elif "FROM users" in normalized:
            self._result = self._rows["users"]
        elif "FROM wishlist_items" in normalized:
            self._result = self._rows["wishlist"]
        else:
            raise AssertionError(f"Unexpected query: {normalized}")

    def fetchall(self):
        return self._result


class _ReferenceConnection:
    def __init__(self, **rows):
        self.rows = {
            "entity_media": rows.get("entity_media", []),
            "people": rows.get("people", []),
            "users": rows.get("users", []),
            "wishlist": rows.get("wishlist", []),
        }
        self.queries = []

    def cursor(self):
        return _ReferenceCursor(self.rows, self.queries)


def _references(**updates):
    result = {
        "movie": set(),
        "container": set(),
        "person": set(),
        "user_avatar": set(),
        "wishlist": set(),
    }
    result.update(updates)
    return result


@contextmanager
def _connection():
    yield object()


class MediaAssetAuthorizationTests(unittest.TestCase):
    def test_collects_only_supported_live_reference_types_and_direct_owners(self):
        media_id = uuid.uuid4()
        movie_id = uuid.uuid4()
        container_id = uuid.uuid4()
        person_id = uuid.uuid4()
        avatar_user_id = uuid.uuid4()
        wishlist_user_id = uuid.uuid4()
        conn = _ReferenceConnection(
            entity_media=[
                {"entity_type": "movie", "entity_id": movie_id},
                {"entity_type": "container", "entity_id": container_id},
                {"entity_type": "person", "entity_id": person_id},
            ],
            people=[{"id": person_id}],
            users=[{"id": avatar_user_id}],
            wishlist=[{"user_id": wishlist_user_id}],
        )

        with patch.object(next_app, "table_exists", return_value=True):
            result = next_app.media_asset_authorization_references(conn, media_id)

        self.assertEqual(result["movie"], {movie_id})
        self.assertEqual(result["container"], {container_id})
        self.assertEqual(result["person"], {person_id})
        self.assertEqual(result["user_avatar"], {avatar_user_id})
        self.assertEqual(result["wishlist"], {wishlist_user_id})
        entity_query = next(query for query, _params in conn.queries if "FROM entity_media" in query)
        self.assertIn("deleted_at IS NULL", entity_query)
        self.assertIn("hidden_at IS NULL", entity_query)
        wishlist_query, wishlist_params = next(
            (query, params) for query, params in conn.queries if "FROM wishlist_items" in query
        )
        self.assertIn("JOIN media_assets", wishlist_query)
        self.assertIn("wi.snapshot->>%s = %s", wishlist_query)
        self.assertIn("ma.metadata->>'source' = 'wishlist_upload'", wishlist_query)
        self.assertIn("ma.metadata->'uploadedBy'->>'id' = wi.user_id::text", wishlist_query)
        self.assertEqual(
            wishlist_params,
            (
                media_id,
                f"/api/next/media/assets/{media_id}",
                next_app.WISHLIST_POSTER_ASSET_SNAPSHOT_KEY,
                str(media_id),
            ),
        )

    def test_wishlist_local_poster_marker_is_server_managed_and_not_exposed(self):
        media_id = uuid.uuid4()
        trusted = next_app.wishlist_snapshot_with_local_poster_asset(
            {"title": "Heat"},
            media_id,
        )
        preserved = next_app.wishlist_snapshot_preserving_local_poster_asset(
            {"title": "Heat (1995)"},
            trusted,
            poster_url_supplied=False,
        )
        cleared = next_app.wishlist_snapshot_preserving_local_poster_asset(
            {"title": "Heat (1995)"},
            trusted,
            poster_url_supplied=True,
        )

        self.assertEqual(
            preserved[next_app.WISHLIST_POSTER_ASSET_SNAPSHOT_KEY],
            str(media_id),
        )
        self.assertNotIn(next_app.WISHLIST_POSTER_ASSET_SNAPSHOT_KEY, cleared)
        entity = next_app._wishlist_row_entity(
            {
                "id": uuid.uuid4(),
                "snapshot": trusted,
            }
        )
        self.assertEqual(entity["snapshot"], {"title": "Heat"})

    def test_visible_movie_container_or_person_reference_grants_access(self):
        actor = {"id": "user-1", "permissions": ["collection.view_own"]}
        media_id = uuid.uuid4()
        refs = _references(
            movie={uuid.uuid4()},
            container={uuid.uuid4()},
            person={uuid.uuid4()},
        )
        with (
            patch.object(next_app, "media_asset_authorization_references", return_value=refs),
            patch.object(next_app, "actor_can_view_movie", return_value=False),
            patch.object(next_app, "actor_can_view_container", return_value=True),
            patch.object(next_app, "actor_can_view_person", return_value=False),
        ):
            self.assertTrue(next_app.actor_can_view_media_asset(object(), actor, media_id))

    def test_unrelated_or_unlinked_asset_is_denied(self):
        actor = {"id": "user-1", "permissions": ["collection.view_own"]}
        media_id = uuid.uuid4()
        refs = _references(
            movie={uuid.uuid4()},
            container={uuid.uuid4()},
            person={uuid.uuid4()},
            user_avatar={"user-2"},
            wishlist={"user-2"},
        )
        with (
            patch.object(next_app, "media_asset_authorization_references", return_value=refs),
            patch.object(next_app, "actor_can_view_movie", return_value=False),
            patch.object(next_app, "actor_can_view_container", return_value=False),
            patch.object(next_app, "actor_can_view_person", return_value=False),
        ):
            self.assertFalse(next_app.actor_can_view_media_asset(object(), actor, media_id))

        with patch.object(
            next_app,
            "media_asset_authorization_references",
            return_value=_references(),
        ):
            self.assertFalse(next_app.actor_can_view_media_asset(object(), actor, media_id))

    def test_avatar_owner_and_user_viewer_are_authorized(self):
        media_id = uuid.uuid4()
        refs = _references(user_avatar={"user-1"})
        with patch.object(next_app, "media_asset_authorization_references", return_value=refs):
            self.assertTrue(
                next_app.actor_can_view_media_asset(
                    object(),
                    {"id": "user-1", "permissions": []},
                    media_id,
                )
            )
            self.assertTrue(
                next_app.actor_can_view_media_asset(
                    object(),
                    {"id": "admin-1", "permissions": ["users.view"]},
                    media_id,
                )
            )
            self.assertFalse(
                next_app.actor_can_view_media_asset(
                    object(),
                    {"id": "user-2", "permissions": []},
                    media_id,
                )
            )

    def test_wishlist_poster_is_visible_only_to_owning_user(self):
        media_id = uuid.uuid4()
        refs = _references(wishlist={"user-1"})
        with patch.object(next_app, "media_asset_authorization_references", return_value=refs):
            self.assertTrue(
                next_app.actor_can_view_media_asset(
                    object(),
                    {"id": "user-1", "permissions": ["watchlist.manage"]},
                    media_id,
                )
            )
            self.assertFalse(
                next_app.actor_can_view_media_asset(
                    object(),
                    {"id": "user-2", "permissions": ["watchlist.manage"]},
                    media_id,
                )
            )


class MediaAssetRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = next_app.create_app()
        cls.app.testing = True

    def test_local_media_routes_are_not_public_authentication_exceptions(self):
        self.assertFalse(
            next_app.is_public_next_path(
                "/api/next/media/assets/00000000-0000-0000-0000-000000000001"
            )
        )
        self.assertFalse(
            next_app.is_public_next_path(
                "/api/next/media/legacy/poster/posters/example.jpg"
            )
        )

    def test_anonymous_media_request_is_rejected_before_file_lookup(self):
        media_id = uuid.uuid4()
        with (
            patch.object(next_app, "connect", _connection),
            patch.object(next_app, "next_auth_effective_enabled", return_value=True),
            patch.object(next_app, "next_auth_current_user", return_value=None),
            patch.object(next_app, "media_asset_entity") as asset_lookup,
        ):
            response = self.app.test_client().get(f"/api/next/media/assets/{media_id}")

        self.assertEqual(response.status_code, 401)
        self.assertTrue(response.get_json()["auth_required"])
        asset_lookup.assert_not_called()

    def test_authorized_media_keeps_bytes_type_etag_and_private_revalidation(self):
        media_id = uuid.uuid4()
        actor = {"id": "user-1", "permissions": ["collection.view_own"]}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "poster.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\nprotected-poster")
            asset = {
                "id": media_id,
                "kind": "poster",
                "provider_id": "upload",
                "storage_key": "media/poster.png",
                "content_type": "image/png",
            }
            with (
                patch.object(next_app, "connect", _connection),
                patch.object(next_app, "next_auth_effective_enabled", return_value=True),
                patch.object(next_app, "next_auth_current_user", return_value=actor),
                patch.object(next_app, "require_next_authenticated_user", return_value=actor),
                patch.object(next_app, "media_asset_entity", return_value=asset),
                patch.object(next_app, "actor_can_view_media_asset", return_value=True),
                patch.object(next_app, "local_media_asset_path", return_value=path),
            ):
                client = self.app.test_client()
                response = client.get(f"/api/next/media/assets/{media_id}")
                conditional = client.get(
                    f"/api/next/media/assets/{media_id}",
                    headers={"If-None-Match": response.headers["ETag"]},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data, b"\x89PNG\r\n\x1a\nprotected-poster")
                self.assertEqual(response.mimetype, "image/png")
                self.assertIn("private", response.headers["Cache-Control"])
                self.assertIn("no-cache", response.headers["Cache-Control"])
                self.assertNotIn("public", response.headers["Cache-Control"])
                self.assertEqual(conditional.status_code, 304)
                response.close()
                conditional.close()

    def test_unauthorized_and_unlinked_media_share_generic_not_found(self):
        media_id = uuid.uuid4()
        actor = {"id": "user-2", "permissions": ["collection.view_own"]}
        asset = {
            "id": media_id,
            "kind": "poster",
            "provider_id": "upload",
            "storage_key": "media/private.png",
        }
        with (
            patch.object(next_app, "connect", _connection),
            patch.object(next_app, "next_auth_effective_enabled", return_value=True),
            patch.object(next_app, "next_auth_current_user", return_value=actor),
            patch.object(next_app, "require_next_authenticated_user", return_value=actor),
            patch.object(next_app, "media_asset_entity", return_value=asset),
            patch.object(next_app, "actor_can_view_media_asset", return_value=False),
            patch.object(next_app, "local_media_asset_path") as path_lookup,
        ):
            denied = self.app.test_client().get(f"/api/next/media/assets/{media_id}")

        with (
            patch.object(next_app, "connect", _connection),
            patch.object(next_app, "next_auth_effective_enabled", return_value=True),
            patch.object(next_app, "next_auth_current_user", return_value=actor),
            patch.object(next_app, "require_next_authenticated_user", return_value=actor),
            patch.object(next_app, "media_asset_entity", return_value=None),
        ):
            missing = self.app.test_client().get(f"/api/next/media/assets/{uuid.uuid4()}")

        self.assertEqual(denied.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(denied.get_json()["error"], "Media asset not found")
        self.assertEqual(missing.get_json()["error"], "Media asset not found")
        path_lookup.assert_not_called()

    def test_legacy_media_requires_normalized_authorized_asset_reference(self):
        media_id = uuid.uuid4()
        actor = {"id": "user-1", "permissions": ["collection.view_own"]}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "posters" / "legacy.jpg"
            path.parent.mkdir()
            path.write_bytes(b"legacy-poster")
            asset = {
                "id": media_id,
                "kind": "poster",
                "provider_id": "legacy",
                "storage_key": "posters/legacy.jpg",
            }
            with (
                patch.dict(
                    os.environ,
                    {"DISCVAULT_LEGACY_DATA_DIR": temp_dir},
                ),
                patch.object(next_app, "connect", _connection),
                patch.object(next_app, "next_auth_effective_enabled", return_value=True),
                patch.object(next_app, "next_auth_current_user", return_value=actor),
                patch.object(next_app, "require_next_authenticated_user", return_value=actor),
                patch.object(
                    next_app,
                    "media_asset_entity_by_storage_key",
                    return_value=asset,
                ) as asset_lookup,
                patch.object(next_app, "actor_can_view_media_asset", return_value=True),
            ):
                response = self.app.test_client().get(
                    "/api/next/media/legacy/poster/posters/legacy.jpg"
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data, b"legacy-poster")
                self.assertIn("private", response.headers["Cache-Control"])
                response.close()
        asset_lookup.assert_called_once_with(
            ANY,
            "posters/legacy.jpg",
        )


class ServiceWorkerProtectedMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (
            Path(__file__).resolve().parents[2] / "frontend" / "service-worker.js"
        ).read_text(encoding="utf-8")

    def test_new_cache_version_evicts_pre_hardening_media(self):
        self.assertIn('const SW_VERSION = "discvault-sw-v153";', self.source)
        self.assertIn("oldKeys.map(key => caches.delete(key))", self.source)

    def test_protected_media_is_network_only_and_uses_offline_placeholder(self):
        self.assertIn('pathname.startsWith("/api/next/media/")', self.source)
        self.assertIn(
            'pathname.startsWith("/api/next/movievault-v2/posters/")',
            self.source,
        )
        branch_start = self.source.index("if (isProtectedMediaPath(url.pathname))")
        branch_end = self.source.index(
            'if (isLegacyAuthRoute && url.pathname !== "/api/auth/status")',
            branch_start,
        )
        branch = self.source[branch_start:branch_end]
        self.assertIn('cache: "no-store"', branch)
        self.assertIn("return imageFallbackResponse()", branch)
        self.assertNotIn("cachePut", branch)
        self.assertNotIn("cacheMatch", branch)

    def test_protected_media_cannot_enter_generic_or_prefetch_cache_writes(self):
        cache_put_start = self.source.index("async function cachePut")
        cache_put_end = self.source.index("async function readCachedJson", cache_put_start)
        self.assertIn(
            "if (isProtectedMediaRequest(request)) return;",
            self.source[cache_put_start:cache_put_end],
        )
        prefetch_start = self.source.index("async function prefetchSnapshotAssets")
        prefetch_end = self.source.index("async function detailFromSnapshot", prefetch_start)
        prefetch = self.source[prefetch_start:prefetch_end]
        self.assertIn("await cachePut(request, resp, RUNTIME_CACHE)", prefetch)
        self.assertNotIn("cache.put(", prefetch)


if __name__ == "__main__":
    unittest.main()
