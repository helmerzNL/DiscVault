"""Security regression tests for the plugin-management authorization boundary.

Context: ``POST /api/next/plugins/import`` (and the sibling update/rollback/
auto-update endpoints that also write executable ``plugin.py`` files to disk)
were previously gated by ``PLUGIN_REGISTRY_MANAGE_PERMISSIONS``, a broad tuple
that includes low-privilege permissions such as ``watchlist.manage`` (held by
every built-in role down to ``media_viewer``) and ``api.tokens.manage`` (held
by the read-only ``api_reader`` API-token role). Because
``next_plugin_runtime.discover_plugins`` executes any ``plugin.py`` found on
disk unconditionally (independent of the plugin's "enabled" flag), any
authenticated user in one of those roles could upload and have arbitrary
Python executed inside the backend process -- a remote code execution bug.

These tests prove:
  * the permission-set data itself excludes every built-in non-admin role;
  * the real ``require_any_next_permission`` authorization function denies
    every built-in non-admin role and allows owner/admin;
  * a denied HTTP request never reaches installation, registry sync, or
    plugin execution;
  * an authorized owner/admin request with a harmless fixture still gets the
    original success response contract;
  * import attempts/outcomes are recorded via the existing audit-event
    mechanism without ever logging the uploaded plugin source.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_app as na
from app.backend.next_common import NextApiError


# ---------------------------------------------------------------------------
# Built-in role permission sets, mirrored from the migration seeds that grant
# them (kept literal/inline so a drift here fails loudly rather than
# reproducing whatever the seed currently contains):
#   - media_editor / media_fan / media_viewer: migrations_next/007, 009, 019
#   - member_groups: migrations_next/020
#   - api_reader / api_writer / mcp_user: migrations_next/016, 019
# None of these roles are ever granted 'plugins.delete'
# (migrations_next/013_plugin_delete_permission.sql grants it to owner/admin
# only).
# ---------------------------------------------------------------------------
ORDINARY_ROLE_PERMISSIONS = {
    "media_editor": {
        "collection.view",
        "collection.add",
        "collection.add_own",
        "collection.edit_own",
        "collection.edit_all",
        "collection.delete_own",
        "collection.delete_all",
        "collection.bulk_edit",
        "containers.view",
        "containers.create",
        "containers.edit",
        "containers.delete",
        "metadata.search",
        "metadata.refresh_one",
        "metadata.refresh_bulk",
        "watchlist.manage",
        "lending.request",
        "collection.view_own",
        "collection.view_group",
        "collection.edit_group",
        "collection.delete_group",
    },
    "media_fan": {
        "collection.view",
        "collection.add_own",
        "collection.edit_own",
        "containers.view",
        "groups.view",
        "watchlist.manage",
        "lending.request",
        "collection.view_own",
        "collection.view_group",
    },
    "media_viewer": {
        "collection.view",
        "containers.view",
        "groups.view",
        "watchlist.manage",
        "lending.request",
        "collection.view_own",
        "collection.view_group",
    },
    "member_groups": {
        "groups.view",
        "groups.create",
        "groups.invite",
        "collection.view_own",
        "collection.view_group",
    },
    "api_reader": {
        "api.read",
        "api.tokens.manage",
        "collection.view",
        "containers.view",
        "groups.view",
        "collection.view_own",
        "collection.view_group",
    },
    "api_writer": {
        "api.read",
        "api.write",
        "api.tokens.manage",
        "collection.view",
        "collection.add",
        "collection.add_own",
        "collection.edit_all",
        "collection.delete_all",
        "collection.bulk_edit",
        "containers.view",
        "containers.create",
        "containers.edit",
        "containers.delete",
        "groups.view",
        "collection.view_own",
        "collection.view_group",
        "collection.edit_group",
        "collection.delete_group",
    },
    "mcp_user": {
        "api.tokens.manage",
        "mcp.use",
        "mcp.tool.search_collection",
        "mcp.tool.get_collection_stats",
        "mcp.tool.get_movie_details",
        "mcp.tool.add_movie",
        "mcp.tool.delete_movie",
        "mcp.tool.lookup_barcode",
        "mcp.tool.list_all_movies",
        "mcp.tool.get_watchlist",
        "mcp.tool.get_watch_history",
        "mcp.tool.get_groups",
    },
    # Legacy/unused roles declared in migrations_next/005 that were never
    # granted any permissions by a later migration.
    "collection_manager": set(),
    "metadata_manager": set(),
    "user_manager": set(),
    "auditor": set(),
    "viewer": set(),
    "member": set(),
}

# admin gets every permission except a short owner-only exclusion list
# (migrations_next/007) plus 'plugins.delete' explicitly (migrations_next/013).
ADMIN_PERMISSIONS = {"plugins.delete"}
OWNER_PERMISSIONS = {"*"}


class PluginInstallPermissionDataTests(unittest.TestCase):
    """Pure data-level assertions about the authorization boundary itself."""

    def test_install_permission_is_reused_dedicated_owner_admin_permission(self):
        # No new DB permission/migration was introduced; the fix reuses the
        # existing owner/admin-only 'plugins.delete' permission.
        self.assertEqual(na.PLUGIN_REGISTRY_INSTALL_PERMISSIONS, ("plugins.delete",))

    def test_ordinary_roles_never_hold_the_install_permission(self):
        for role, permissions in ORDINARY_ROLE_PERMISSIONS.items():
            with self.subTest(role=role):
                self.assertFalse(
                    permissions.intersection(na.PLUGIN_REGISTRY_INSTALL_PERMISSIONS),
                    f"{role} must not hold any of {na.PLUGIN_REGISTRY_INSTALL_PERMISSIONS}",
                )

    def test_old_broad_manage_permissions_would_have_leaked_to_ordinary_roles(self):
        # Regression guard: documents *why* the fix was necessary. If this
        # ever starts failing it means PLUGIN_REGISTRY_MANAGE_PERMISSIONS was
        # narrowed -- which is fine -- but it must never be reintroduced as
        # the gate for install/update/rollback/import.
        vulnerable_roles = {
            role
            for role, permissions in ORDINARY_ROLE_PERMISSIONS.items()
            if permissions.intersection(na.PLUGIN_REGISTRY_MANAGE_PERMISSIONS)
        }
        self.assertIn("media_viewer", vulnerable_roles)
        self.assertIn("api_reader", vulnerable_roles)

    def test_actor_effective_has_any_permission_denies_ordinary_roles(self):
        for role, permissions in ORDINARY_ROLE_PERMISSIONS.items():
            with self.subTest(role=role):
                actor = {"id": "u1", "role": role, "permissions": sorted(permissions)}
                self.assertFalse(
                    na.actor_effective_has_any_permission(actor, na.PLUGIN_REGISTRY_INSTALL_PERMISSIONS)
                )

    def test_actor_effective_has_any_permission_allows_owner_and_admin(self):
        owner_actor = {"id": "owner-1", "role": "owner", "permissions": sorted(OWNER_PERMISSIONS)}
        admin_actor = {"id": "admin-1", "role": "admin", "permissions": sorted(ADMIN_PERMISSIONS)}
        self.assertTrue(na.actor_effective_has_any_permission(owner_actor, na.PLUGIN_REGISTRY_INSTALL_PERMISSIONS))
        self.assertTrue(na.actor_effective_has_any_permission(admin_actor, na.PLUGIN_REGISTRY_INSTALL_PERMISSIONS))


class _FakeCursor:
    """Minimal cursor stub: records executed statements, returns a canned row."""

    def __init__(self, conn: "_FakeConnection", fetchone_result=None):
        self._conn = conn
        self._fetchone_result = fetchone_result

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self._conn.executed.append((sql, params))

    def fetchone(self):
        return self._fetchone_result

    def fetchall(self):
        return []


class _FakeConnection:
    """Minimal ``with connect() as conn`` stand-in with no real database.

    Every ``table_exists`` check (in both ``next_app`` and ``next_audit``)
    resolves truthy, and the single SELECT the import endpoint issues
    ("SELECT categories FROM plugins WHERE id=%s") resolves to
    ``fetchone_categories``.
    """

    def __init__(self, fetchone_categories: list[str] | None = None):
        self.executed: list[tuple[str, Any]] = []
        self.commit_called = False
        self._fetchone_categories = fetchone_categories

    def cursor(self, *args, **kwargs):
        if self._fetchone_categories is not None:
            row = {"categories": self._fetchone_categories, "table_name": "public.plugins"}
        else:
            row = {"table_name": "public.plugins"}
        return _FakeCursor(self, row)

    def transaction(self):
        return contextlib.nullcontext()

    def commit(self):
        self.commit_called = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _auth_patches(
    role: str,
    permissions: set[str],
    *,
    user_id: str = "user-1",
    api_token_permissions: set[str] | None = None,
):
    """Return the patch context managers needed to simulate an authenticated
    actor with a given role/permission set, without touching a real database.
    """

    actor = {"id": user_id, "username": f"{role}-user", "role": role}
    if api_token_permissions is not None:
        actor["apiToken"] = {
            "id": "token-1",
            "name": "scoped-automation",
            "permissionKeys": sorted(api_token_permissions),
            "scopes": [],
        }
    return [
        patch.object(na, "next_auth_effective_enabled", return_value=True),
        patch.object(na, "next_auth_current_user", return_value=actor),
        patch.object(na, "next_user_primary_role", return_value=role),
        patch.object(na, "next_user_permission_keys", return_value=set(permissions)),
        patch.object(na, "table_exists", return_value=True),
    ]


class RequireAnyNextPermissionBoundaryTests(unittest.TestCase):
    """Exercise the real authorization function (not just the data)."""

    def _call_with_role(self, role: str, permissions: set[str]):
        fake_conn = _FakeConnection()
        with contextlib.ExitStack() as stack:
            for ctx in _auth_patches(role, permissions):
                stack.enter_context(ctx)
            with na.app.test_request_context("/api/next/plugins/import"):
                return na.require_any_next_permission(fake_conn, na.PLUGIN_REGISTRY_INSTALL_PERMISSIONS)

    def test_every_ordinary_role_is_denied(self):
        for role, permissions in ORDINARY_ROLE_PERMISSIONS.items():
            with self.subTest(role=role):
                with self.assertRaises(NextApiError) as ctx:
                    self._call_with_role(role, permissions)
                self.assertEqual(ctx.exception.status_code, 403)

    def test_owner_and_admin_are_allowed(self):
        owner_actor = self._call_with_role("owner", OWNER_PERMISSIONS)
        self.assertEqual(owner_actor["role"], "owner")
        admin_actor = self._call_with_role("admin", ADMIN_PERMISSIONS)
        self.assertEqual(admin_actor["role"], "admin")


class PluginRegistryInstallPermissionBoundaryTests(unittest.TestCase):
    """The executable-write boundary must enforce both role and token scope."""

    def test_owner_session_is_allowed(self):
        fake_conn = _FakeConnection()
        with contextlib.ExitStack() as stack:
            for ctx in _auth_patches("owner", OWNER_PERMISSIONS):
                stack.enter_context(ctx)
            actor = na.require_plugin_registry_install_permission(fake_conn)

        self.assertEqual(actor["role"], "owner")
        self.assertFalse(fake_conn.commit_called)

    def test_scoped_owner_api_token_is_denied_and_audit_is_committed(self):
        fake_conn = _FakeConnection()
        with contextlib.ExitStack() as stack:
            for ctx in _auth_patches(
                "owner",
                OWNER_PERMISSIONS,
                api_token_permissions={"api.read"},
            ):
                stack.enter_context(ctx)
            with na.app.test_request_context("/api/next/plugins/import"):
                with self.assertRaises(NextApiError) as ctx:
                    na.require_plugin_registry_install_permission(fake_conn)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertTrue(fake_conn.commit_called)
        audit_events = [
            params
            for sql, params in fake_conn.executed
            if sql.strip().startswith("INSERT INTO audit_events")
        ]
        self.assertEqual(len(audit_events), 1)
        self.assertEqual(audit_events[0][0], "security.permission_denied")

    def test_owner_api_token_with_legacy_wildcard_is_still_denied(self):
        fake_conn = _FakeConnection()
        with contextlib.ExitStack() as stack:
            for ctx in _auth_patches(
                "owner",
                OWNER_PERMISSIONS,
                api_token_permissions={"*"},
            ):
                stack.enter_context(ctx)
            with na.app.test_request_context("/api/next/plugins/import"):
                with self.assertRaises(NextApiError) as ctx:
                    na.require_plugin_registry_install_permission(fake_conn)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertTrue(fake_conn.commit_called)


def _harmless_plugin_zip(plugin_id: str = "harmless-fixture-plugin") -> bytes:
    manifest = {
        "id": plugin_id,
        "name": "Harmless Fixture Plugin",
        "version": "1.0.0",
        "discVaultPluginApi": "next-1",
        "categories": ["price_provider"],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(
            "plugin.py",
            "def health_check(context):\n    return {'status': 'available'}\n",
        )
    return buf.getvalue()


class PluginImportRouteAuthorizationTests(unittest.TestCase):
    """Full Flask-route regression coverage for POST /api/next/plugins/import."""

    def setUp(self):
        self.client = na.app.test_client()

    def _post_import(self, *, file_bytes: bytes | None = None, filename: str = "plugin.zip"):
        data = None
        content_type = None
        if file_bytes is not None:
            data = {"file": (io.BytesIO(file_bytes), filename)}
            content_type = "multipart/form-data"
        return self.client.post("/api/next/plugins/import", data=data, content_type=content_type)

    def test_ordinary_roles_are_denied_and_never_reach_install_or_sync(self):
        for role, permissions in ORDINARY_ROLE_PERMISSIONS.items():
            with self.subTest(role=role):
                fake_conn = _FakeConnection()
                with contextlib.ExitStack() as stack:
                    stack.enter_context(patch.object(na, "connect", side_effect=lambda: fake_conn))
                    for ctx in _auth_patches(role, permissions):
                        stack.enter_context(ctx)
                    install_mock = stack.enter_context(patch.object(na, "install_plugin_from_root"))
                    sync_mock = stack.enter_context(patch.object(na, "sync_plugin_registry"))
                    sync_meta_mock = stack.enter_context(patch.object(na, "sync_metadata_plugin_registry"))
                    validate_mock = stack.enter_context(patch.object(na, "validate_import_plugin_root"))

                    resp = self._post_import(file_bytes=_harmless_plugin_zip())

                self.assertEqual(resp.status_code, 403, resp.get_json())
                self.assertTrue(fake_conn.commit_called)
                install_mock.assert_not_called()
                sync_mock.assert_not_called()
                sync_meta_mock.assert_not_called()
                validate_mock.assert_not_called()

    def test_scoped_owner_api_token_is_denied_before_import_side_effects(self):
        fake_conn = _FakeConnection()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(na, "connect", side_effect=lambda: fake_conn))
            for ctx in _auth_patches(
                "owner",
                OWNER_PERMISSIONS,
                api_token_permissions={"api.read"},
            ):
                stack.enter_context(ctx)
            install_mock = stack.enter_context(patch.object(na, "install_plugin_from_root"))
            sync_mock = stack.enter_context(patch.object(na, "sync_plugin_registry"))
            sync_meta_mock = stack.enter_context(patch.object(na, "sync_metadata_plugin_registry"))

            resp = self._post_import(file_bytes=_harmless_plugin_zip())

        self.assertEqual(resp.status_code, 403, resp.get_json())
        self.assertTrue(fake_conn.commit_called)
        install_mock.assert_not_called()
        sync_mock.assert_not_called()
        sync_meta_mock.assert_not_called()

    def _authorized_success_response(self, role: str, permissions: set[str], plugin_id: str):
        fake_conn = _FakeConnection(fetchone_categories=["price_provider"])
        install_dir = tempfile.mkdtemp(prefix="discvault-plugin-install-test-")
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(na, "connect", side_effect=lambda: fake_conn))
            for ctx in _auth_patches(role, permissions):
                stack.enter_context(ctx)
            stack.enter_context(patch.object(na, "sync_plugin_registry", return_value={}))
            stack.enter_context(patch.object(na, "sync_metadata_plugin_registry", return_value=None))
            stack.enter_context(
                patch.object(
                    na,
                    "plugin_registry_snapshot",
                    return_value={"plugins": [{"id": plugin_id, "name": "Harmless Fixture Plugin"}]},
                )
            )
            stack.enter_context(patch.dict(os.environ, {"DISCVAULT_PLUGIN_INSTALL_DIR": install_dir}))
            resp = self._post_import(file_bytes=_harmless_plugin_zip(plugin_id))
        return resp, fake_conn, install_dir

    def test_owner_import_preserves_success_contract_with_harmless_fixture(self):
        resp, _fake_conn, install_dir = self._authorized_success_response(
            "owner", OWNER_PERMISSIONS, "harmless-fixture-plugin-owner"
        )
        body = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(body["status"], "ok")
        self.assertIn("plugin", body)
        self.assertIn("registry", body)
        self.assertEqual(body["plugin"]["id"], "harmless-fixture-plugin-owner")
        self.assertTrue(
            os.path.isdir(os.path.join(install_dir, "harmless-fixture-plugin-owner")),
            "the harmless fixture must actually reach install_plugin_from_root",
        )

    def test_admin_import_preserves_success_contract_with_harmless_fixture(self):
        resp, _fake_conn, install_dir = self._authorized_success_response(
            "admin", ADMIN_PERMISSIONS, "harmless-fixture-plugin-admin"
        )
        body = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["plugin"]["id"], "harmless-fixture-plugin-admin")
        self.assertTrue(os.path.isdir(os.path.join(install_dir, "harmless-fixture-plugin-admin")))


class PluginImportAuditTests(unittest.TestCase):
    """Audit-trail coverage for plugin import attempts/outcomes."""

    def setUp(self):
        self.client = na.app.test_client()

    @staticmethod
    def _jsonb_payload(params):
        for value in params:
            if hasattr(value, "obj"):
                return value.obj
        return None

    def test_invalid_zip_upload_is_audited_with_digest_only_and_committed(self):
        fake_conn = _FakeConnection()
        bogus_bytes = b"not a real zip file"
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(na, "connect", side_effect=lambda: fake_conn))
            for ctx in _auth_patches("owner", OWNER_PERMISSIONS):
                stack.enter_context(ctx)
            resp = self.client.post(
                "/api/next/plugins/import",
                data={"file": (io.BytesIO(bogus_bytes), "plugin.zip")},
                content_type="multipart/form-data",
            )

        self.assertEqual(resp.status_code, 400)
        audit_inserts = [
            params
            for sql, params in fake_conn.executed
            if sql.strip().startswith("INSERT INTO audit_events")
        ]
        self.assertEqual(len(audit_inserts), 1)
        params = audit_inserts[0]
        self.assertEqual(params[0], "plugin.import_failed")
        metadata = self._jsonb_payload(params)
        self.assertIsNotNone(metadata)
        self.assertIn("packageSha256", metadata)
        self.assertIsInstance(metadata["packageSha256"], str)
        self.assertEqual(len(metadata["packageSha256"]), 64)
        self.assertEqual(metadata["uploadSizeBytes"], len(bogus_bytes))
        # Never log the uploaded plugin source/content.
        serialized_metadata = json.dumps(metadata)
        self.assertNotIn("not a real zip file", serialized_metadata)
        # The failure audit event must be durable even though the request
        # ultimately raises and fails.
        self.assertTrue(fake_conn.commit_called)

    def test_successful_import_audit_metadata_includes_digest_and_size_not_source(self):
        fake_conn = _FakeConnection(fetchone_categories=["price_provider"])
        install_dir = tempfile.mkdtemp(prefix="discvault-plugin-install-audit-test-")
        zip_bytes = _harmless_plugin_zip("harmless-audit-plugin")
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(na, "connect", side_effect=lambda: fake_conn))
            for ctx in _auth_patches("owner", OWNER_PERMISSIONS):
                stack.enter_context(ctx)
            stack.enter_context(patch.object(na, "sync_plugin_registry", return_value={}))
            stack.enter_context(patch.object(na, "sync_metadata_plugin_registry", return_value=None))
            stack.enter_context(
                patch.object(
                    na,
                    "plugin_registry_snapshot",
                    return_value={"plugins": [{"id": "harmless-audit-plugin"}]},
                )
            )
            stack.enter_context(patch.dict(os.environ, {"DISCVAULT_PLUGIN_INSTALL_DIR": install_dir}))
            resp = self.client.post(
                "/api/next/plugins/import",
                data={"file": (io.BytesIO(zip_bytes), "plugin.zip")},
                content_type="multipart/form-data",
            )

        self.assertEqual(resp.status_code, 200)
        audit_inserts = [
            params
            for sql, params in fake_conn.executed
            if sql.strip().startswith("INSERT INTO audit_events")
        ]
        imported_events = [params for params in audit_inserts if params[0] == "plugin.imported"]
        self.assertEqual(len(imported_events), 1)
        metadata = self._jsonb_payload(imported_events[0])
        self.assertEqual(metadata["uploadSizeBytes"], len(zip_bytes))
        self.assertIsInstance(metadata["packageSha256"], str)
        self.assertEqual(len(metadata["packageSha256"]), 64)
        serialized_metadata = json.dumps(metadata)
        self.assertNotIn("health_check", serialized_metadata)

    def test_install_os_error_is_audited_without_exposing_error_details(self):
        fake_conn = _FakeConnection()
        zip_bytes = _harmless_plugin_zip("failed-install-plugin")
        with tempfile.TemporaryDirectory(prefix="discvault-plugin-install-error-test-") as install_dir:
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(na, "connect", side_effect=lambda: fake_conn))
                for ctx in _auth_patches("owner", OWNER_PERMISSIONS):
                    stack.enter_context(ctx)
                stack.enter_context(
                    patch.object(
                        na,
                        "install_plugin_from_root",
                        side_effect=OSError("disk failure with sensitive path"),
                    )
                )
                stack.enter_context(patch.object(na.app.logger, "exception"))
                stack.enter_context(patch.dict(os.environ, {"DISCVAULT_PLUGIN_INSTALL_DIR": install_dir}))
                resp = self.client.post(
                    "/api/next/plugins/import",
                    data={"file": (io.BytesIO(zip_bytes), "plugin.zip")},
                    content_type="multipart/form-data",
                )

        self.assertEqual(resp.status_code, 500)
        self.assertTrue(fake_conn.commit_called)
        audit_inserts = [
            params
            for sql, params in fake_conn.executed
            if sql.strip().startswith("INSERT INTO audit_events")
        ]
        failed_events = [params for params in audit_inserts if params[0] == "plugin.import_failed"]
        self.assertEqual(len(failed_events), 1)
        metadata = self._jsonb_payload(failed_events[0])
        self.assertEqual(metadata["stage"], "install")
        self.assertEqual(metadata["errorType"], "OSError")
        self.assertEqual(metadata["statusCode"], 500)
        self.assertNotIn("sensitive path", json.dumps(metadata))

    def test_registry_sync_failure_restores_previous_plugin_and_is_audited(self):
        fake_conn = _FakeConnection()
        plugin_id = "restore-on-sync-failure"
        zip_bytes = _harmless_plugin_zip(plugin_id)
        with tempfile.TemporaryDirectory(prefix="discvault-plugin-restore-test-") as install_dir:
            existing_plugin_dir = os.path.join(install_dir, plugin_id)
            os.makedirs(existing_plugin_dir)
            old_runtime = "OLD_PLUGIN_RUNTIME = True\n"
            with open(os.path.join(existing_plugin_dir, "manifest.json"), "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "id": plugin_id,
                        "name": "Existing Plugin",
                        "version": "0.9.0",
                        "categories": ["price_provider"],
                    },
                    handle,
                )
            with open(os.path.join(existing_plugin_dir, "plugin.py"), "w", encoding="utf-8") as handle:
                handle.write(old_runtime)

            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(na, "connect", side_effect=lambda: fake_conn))
                for ctx in _auth_patches("owner", OWNER_PERMISSIONS):
                    stack.enter_context(ctx)
                stack.enter_context(
                    patch.object(
                        na,
                        "sync_metadata_plugin_registry",
                        side_effect=RuntimeError("registry sync failed"),
                    )
                )
                stack.enter_context(patch.object(na.app.logger, "exception"))
                stack.enter_context(patch.dict(os.environ, {"DISCVAULT_PLUGIN_INSTALL_DIR": install_dir}))
                resp = self.client.post(
                    "/api/next/plugins/import",
                    data={"file": (io.BytesIO(zip_bytes), "plugin.zip")},
                    content_type="multipart/form-data",
                )

            with open(os.path.join(existing_plugin_dir, "plugin.py"), encoding="utf-8") as handle:
                restored_runtime = handle.read()

        self.assertEqual(resp.status_code, 500)
        self.assertEqual(restored_runtime, old_runtime)
        self.assertTrue(fake_conn.commit_called)
        audit_inserts = [
            params
            for sql, params in fake_conn.executed
            if sql.strip().startswith("INSERT INTO audit_events")
        ]
        failed_events = [params for params in audit_inserts if params[0] == "plugin.import_failed"]
        self.assertEqual(len(failed_events), 1)
        metadata = self._jsonb_payload(failed_events[0])
        self.assertEqual(metadata["stage"], "registry_sync")
        self.assertEqual(metadata["errorType"], "RuntimeError")
        self.assertNotIn("registry sync failed", json.dumps(metadata))

    def test_cleanup_failure_reports_skipped_restore_and_preserves_backup(self):
        fake_conn = _FakeConnection()
        plugin_id = "preserve-backup-on-cleanup-failure"
        zip_bytes = _harmless_plugin_zip(plugin_id)
        captured_backup: dict[str, Path | None] = {}
        real_move_to_trash = na.move_plugin_source_to_trash
        real_rmtree = na.shutil.rmtree

        def capture_backup(plugin):
            source_path, trash_path, trash_root = real_move_to_trash(plugin)
            captured_backup.update(
                source_path=source_path,
                trash_path=trash_path,
                trash_root=trash_root,
            )
            return source_path, trash_path, trash_root

        with tempfile.TemporaryDirectory(prefix="discvault-plugin-cleanup-error-test-") as install_dir:
            existing_plugin_dir = Path(install_dir) / plugin_id
            existing_plugin_dir.mkdir()
            old_runtime = "OLD_PLUGIN_RUNTIME = True\n"
            (existing_plugin_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "id": plugin_id,
                        "name": "Existing Plugin",
                        "version": "0.9.0",
                        "categories": ["price_provider"],
                    }
                ),
                encoding="utf-8",
            )
            (existing_plugin_dir / "plugin.py").write_text(old_runtime, encoding="utf-8")

            def fail_only_for_install_target(path, *args, **kwargs):
                if Path(path).resolve() == existing_plugin_dir.resolve():
                    raise OSError("target is locked")
                return real_rmtree(path, *args, **kwargs)

            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(na, "connect", side_effect=lambda: fake_conn))
                for ctx in _auth_patches("owner", OWNER_PERMISSIONS):
                    stack.enter_context(ctx)
                stack.enter_context(
                    patch.object(
                        na,
                        "sync_metadata_plugin_registry",
                        side_effect=RuntimeError("registry sync failed"),
                    )
                )
                stack.enter_context(patch.object(na, "move_plugin_source_to_trash", side_effect=capture_backup))
                stack.enter_context(patch.object(na.shutil, "rmtree", side_effect=fail_only_for_install_target))
                logger_error = stack.enter_context(patch.object(na.app.logger, "error"))
                stack.enter_context(patch.object(na.app.logger, "exception"))
                stack.enter_context(patch.dict(os.environ, {"DISCVAULT_PLUGIN_INSTALL_DIR": install_dir}))
                resp = self.client.post(
                    "/api/next/plugins/import",
                    data={"file": (io.BytesIO(zip_bytes), "plugin.zip")},
                    content_type="multipart/form-data",
                )

            trash_path = captured_backup["trash_path"]
            trash_root = captured_backup["trash_root"]
            self.assertIsNotNone(trash_path)
            self.assertTrue(trash_path.exists())
            self.assertEqual((trash_path / "plugin.py").read_text(encoding="utf-8"), old_runtime)
            logger_error.assert_any_call(
                "Previous plugin restore skipped because the target path is still occupied"
            )
            logger_error.assert_any_call("Preserved a previous plugin backup after failed import recovery")
            if trash_root:
                real_rmtree(trash_root, ignore_errors=True)

        self.assertEqual(resp.status_code, 500)


class OtherExecutableWriteEndpointAuthorizationTests(unittest.TestCase):
    """Denial coverage for the other endpoints tightened alongside import."""

    def setUp(self):
        self.client = na.app.test_client()

    def test_scoped_owner_api_token_is_denied_by_every_executable_write_endpoint(self):
        cases = (
            ("post", "/api/next/plugins/some-plugin/update", "install_bundled_plugin_update"),
            ("post", "/api/next/plugins/some-plugin/rollback", "rollback_plugin_update"),
            ("put", "/api/next/plugins/auto-update", "upgrade_seeded_default_plugins"),
        )
        for method, path, side_effect_name in cases:
            with self.subTest(path=path):
                fake_conn = _FakeConnection()
                with contextlib.ExitStack() as stack:
                    stack.enter_context(patch.object(na, "connect", side_effect=lambda: fake_conn))
                    for ctx in _auth_patches(
                        "owner",
                        OWNER_PERMISSIONS,
                        api_token_permissions={"api.read"},
                    ):
                        stack.enter_context(ctx)
                    side_effect_mock = stack.enter_context(patch.object(na, side_effect_name))
                    request_method = getattr(self.client, method)
                    kwargs = {"json": {"enabled": True}} if method == "put" else {}
                    resp = request_method(path, **kwargs)

                self.assertEqual(resp.status_code, 403, resp.get_json())
                self.assertTrue(fake_conn.commit_called)
                side_effect_mock.assert_not_called()

    def test_update_to_bundled_denies_media_viewer_and_never_installs(self):
        fake_conn = _FakeConnection()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(na, "connect", side_effect=lambda: fake_conn))
            for ctx in _auth_patches("media_viewer", ORDINARY_ROLE_PERMISSIONS["media_viewer"]):
                stack.enter_context(ctx)
            install_update_mock = stack.enter_context(patch.object(na, "install_bundled_plugin_update"))
            resp = self.client.post("/api/next/plugins/some-plugin/update")

        self.assertEqual(resp.status_code, 403)
        install_update_mock.assert_not_called()

    def test_rollback_denies_media_viewer_and_never_rolls_back(self):
        fake_conn = _FakeConnection()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(na, "connect", side_effect=lambda: fake_conn))
            for ctx in _auth_patches("media_viewer", ORDINARY_ROLE_PERMISSIONS["media_viewer"]):
                stack.enter_context(ctx)
            rollback_mock = stack.enter_context(patch.object(na, "rollback_plugin_update"))
            resp = self.client.post("/api/next/plugins/some-plugin/rollback")

        self.assertEqual(resp.status_code, 403)
        rollback_mock.assert_not_called()

    def test_auto_update_toggle_denies_media_viewer_and_never_upgrades(self):
        fake_conn = _FakeConnection()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(na, "connect", side_effect=lambda: fake_conn))
            for ctx in _auth_patches("media_viewer", ORDINARY_ROLE_PERMISSIONS["media_viewer"]):
                stack.enter_context(ctx)
            upgrade_mock = stack.enter_context(patch.object(na, "upgrade_seeded_default_plugins"))
            resp = self.client.put("/api/next/plugins/auto-update", json={"enabled": True})

        self.assertEqual(resp.status_code, 403)
        upgrade_mock.assert_not_called()

    def test_owner_can_still_reach_rollback_authorization_boundary(self):
        # Positive control: owner must pass the permission gate (the endpoint
        # then runs, with rollback_plugin_update patched to avoid touching a
        # real plugin directory) -- proving the 403 above is specifically
        # about role, not a broken route.
        fake_conn = _FakeConnection()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(na, "connect", side_effect=lambda: fake_conn))
            for ctx in _auth_patches("owner", OWNER_PERMISSIONS):
                stack.enter_context(ctx)
            rollback_mock = stack.enter_context(
                patch.object(na, "rollback_plugin_update", return_value={"pluginId": "x", "from": "1", "to": "2"})
            )
            stack.enter_context(patch.object(na, "sync_plugin_registry", return_value={}))
            stack.enter_context(patch.object(na, "sync_metadata_plugin_registry", return_value=None))
            stack.enter_context(patch.object(na, "plugin_registry_snapshot", return_value={"plugins": []}))
            stack.enter_context(patch.object(na, "plugin_auto_update_setting", return_value=False))
            resp = self.client.post("/api/next/plugins/some-plugin/rollback")

        self.assertEqual(resp.status_code, 200)
        rollback_mock.assert_called_once_with("some-plugin")


if __name__ == "__main__":
    unittest.main()
