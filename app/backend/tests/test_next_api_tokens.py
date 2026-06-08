import os
import sys
import unittest
from unittest import mock


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_app import NextApiError
    from app.backend.next_app import api_token_scopes_for_permissions
    from app.backend.next_app import actor_effective_has_any_permission
    from app.backend.next_app import actor_effective_has_permission
    from app.backend.next_app import normalize_api_token_permissions
    from app.backend.next_app import profile_api_access_payload
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name != "flask":
        raise
    NextApiError = None
    api_token_scopes_for_permissions = None
    actor_effective_has_any_permission = None
    actor_effective_has_permission = None
    normalize_api_token_permissions = None
    profile_api_access_payload = None


@unittest.skipIf(normalize_api_token_permissions is None, "Flask is not installed in this test environment")
class NextApiTokenPermissionTests(unittest.TestCase):
    def setUp(self):
        self.known_permissions = {
            "api.read",
            "api.write",
            "api.tokens.manage",
            "mcp.use",
            "mcp.tool.search_collection",
            "mcp.tool.lookup_barcode",
            "metadata.search",
            "collection.view",
            "collection.add",
            "collection.import",
            "collection.delete_all",
            "admin.view_audit",
        }

    def test_api_tokens_can_grant_metadata_search_and_collection_add(self):
        actor = {
            "role": "admin",
            "permissions": [
                "api.tokens.manage",
                "metadata.search",
                "collection.add",
                "collection.import",
            ],
        }

        with mock.patch("app.backend.next_app.permission_key_catalog", return_value=self.known_permissions):
            permissions = normalize_api_token_permissions(
                None,
                actor,
                ["metadata.search", "collection.add", "collection.import"],
            )

        self.assertEqual(permissions, ["metadata.search", "collection.add", "collection.import"])

    def test_api_tokens_reject_non_grantable_permissions(self):
        actor = {
            "role": "owner",
            "permissions": [],
        }

        with mock.patch("app.backend.next_app.permission_key_catalog", return_value=self.known_permissions):
            with self.assertRaises(NextApiError):
                normalize_api_token_permissions(None, actor, ["admin.view_audit"])

    def test_profile_payload_exposes_import_token_permissions(self):
        actor = {
            "id": None,
            "role": "admin",
            "permissions": [
                "api.read",
                "api.tokens.manage",
                "metadata.search",
                "collection.add",
                "collection.import",
                "admin.view_audit",
            ],
        }

        with (
            mock.patch("app.backend.next_app.permission_key_catalog", return_value=self.known_permissions),
            mock.patch("app.backend.next_app.table_exists", return_value=False),
        ):
            payload = profile_api_access_payload(None, actor)

        self.assertIn("metadata.search", payload["allowedPermissions"])
        self.assertIn("collection.add", payload["allowedPermissions"])
        self.assertIn("collection.import", payload["allowedPermissions"])
        self.assertNotIn("admin.view_audit", payload["allowedPermissions"])

    def test_collection_and_metadata_permissions_map_to_read_write_scopes(self):
        scopes = api_token_scopes_for_permissions(["metadata.search", "collection.add"])

        self.assertEqual(scopes, ["read", "write"])

    def test_api_token_request_can_use_user_role_permission(self):
        actor = {
            "role": "owner",
            "permissions": ["metadata.refresh_one"],
            "apiToken": {"permissionKeys": ["api.read"]},
        }

        self.assertTrue(actor_effective_has_permission(actor, "metadata.refresh_one"))

    def test_custom_role_can_use_metadata_refresh_permission_with_mobile_token(self):
        actor = {
            "role": "media_editor",
            "permissions": ["metadata.refresh_one"],
            "apiToken": {"permissionKeys": ["api.read", "metadata.search"]},
        }

        self.assertTrue(actor_effective_has_permission(actor, "metadata.refresh_one"))

    def test_api_token_scope_can_grant_permission_when_role_lacks_it(self):
        actor = {
            "role": "media_viewer",
            "permissions": ["collection.view"],
            "apiToken": {"permissionKeys": ["metadata.refresh_one"]},
        }

        self.assertTrue(actor_effective_has_permission(actor, "metadata.refresh_one"))

    def test_request_without_role_or_token_permission_is_denied(self):
        actor = {
            "role": "media_viewer",
            "permissions": ["collection.view"],
            "apiToken": {"permissionKeys": ["api.read"]},
        }

        self.assertFalse(actor_effective_has_permission(actor, "metadata.refresh_one"))
        self.assertFalse(actor_effective_has_any_permission(actor, ("metadata.refresh_one", "metadata.refresh_bulk")))

    def test_owner_role_is_not_a_hardcoded_effective_bypass(self):
        actor = {
            "role": "owner",
            "permissions": [],
            "apiToken": {"permissionKeys": ["api.read"]},
        }

        self.assertFalse(actor_effective_has_permission(actor, "metadata.refresh_one"))


if __name__ == "__main__":
    unittest.main()
