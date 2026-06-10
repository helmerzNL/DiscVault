import os
import sys
import unittest
from unittest import mock


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend import next_app
    from app.backend import next_api_token
    from app.backend.next_api_token import NextApiError
except ModuleNotFoundError as exc:  # Local minimal test environments may omit optional backend deps.
    if exc.name not in {"flask", "psycopg"}:
        raise
    next_app = None
    next_api_token = None
    NextApiError = None


@unittest.skipIf(next_api_token is None, "Flask is not installed in this test environment")
class NextApiTokenHelperTests(unittest.TestCase):
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

    def test_token_helpers_are_reexported_from_next_app(self):
        names = [
            "API_TOKEN_DEFAULT_PERMISSION_KEYS",
            "API_TOKEN_GRANTABLE_PERMISSION_KEYS",
            "api_access_token_row",
            "api_token_permission_is_grantable",
            "api_token_scopes_for_permissions",
            "normalize_api_token_permissions",
            "profile_api_access_payload",
        ]
        for name in names:
            self.assertIs(getattr(next_app, name), getattr(next_api_token, name), name)

    def test_api_token_permission_is_grantable_allows_mcp_tools(self):
        self.assertTrue(next_api_token.api_token_permission_is_grantable("mcp.tool.add_movie"))
        self.assertTrue(next_api_token.api_token_permission_is_grantable("metadata.search"))
        self.assertFalse(next_api_token.api_token_permission_is_grantable("admin.view_audit"))

    def test_api_tokens_can_grant_metadata_search_and_collection_add(self):
        actor = {
            "role": "admin",
            "permissions": ["api.tokens.manage", "metadata.search", "collection.add", "collection.import"],
        }
        with mock.patch("app.backend.next_app.permission_key_catalog", return_value=self.known_permissions):
            permissions = next_api_token.normalize_api_token_permissions(
                None, actor, ["metadata.search", "collection.add", "collection.import"]
            )
        self.assertEqual(permissions, ["metadata.search", "collection.add", "collection.import"])

    def test_api_tokens_reject_non_grantable_permissions(self):
        actor = {"role": "owner", "permissions": []}
        with mock.patch("app.backend.next_app.permission_key_catalog", return_value=self.known_permissions):
            with self.assertRaises(NextApiError):
                next_api_token.normalize_api_token_permissions(None, actor, ["admin.view_audit"])

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
            payload = next_api_token.profile_api_access_payload(None, actor)

        self.assertIn("metadata.search", payload["allowedPermissions"])
        self.assertIn("collection.add", payload["allowedPermissions"])
        self.assertIn("collection.import", payload["allowedPermissions"])
        self.assertNotIn("admin.view_audit", payload["allowedPermissions"])

    def test_collection_and_metadata_permissions_map_to_read_write_scopes(self):
        scopes = next_api_token.api_token_scopes_for_permissions(["metadata.search", "collection.add"])
        self.assertEqual(scopes, ["read", "write"])

    def test_mcp_permissions_map_to_mcp_scope(self):
        self.assertEqual(next_api_token.api_token_scopes_for_permissions(["mcp.use"]), ["mcp"])
        self.assertEqual(
            next_api_token.api_token_scopes_for_permissions(["mcp.tool.search_collection"]), ["mcp"]
        )


if __name__ == "__main__":
    unittest.main()
