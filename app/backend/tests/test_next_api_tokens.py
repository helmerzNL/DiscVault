import os
import sys
import unittest
from unittest import mock


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from flask import Flask

    from app.backend.next_app import NextApiError
    from app.backend.next_app import api_token_scopes_for_permissions
    from app.backend.next_app import actor_effective_has_any_permission
    from app.backend.next_app import actor_effective_has_permission
    from app.backend.next_app import normalize_api_token_permissions
    from app.backend.next_app import normalize_profile_api_audit_category
    from app.backend.next_app import profile_api_audit_category_condition
    from app.backend.next_app import profile_api_audit_search_term
    from app.backend.next_app import profile_api_audit_token_match_condition
    from app.backend.next_app import profile_api_access_payload
    from app.backend.next_app import public_request_ip
    from app.backend.next_app import request_ip_details
except ModuleNotFoundError as exc:  # Local minimal test environments may omit optional backend deps.
    if exc.name not in {"flask", "psycopg"}:
        raise
    Flask = None
    NextApiError = None
    api_token_scopes_for_permissions = None
    actor_effective_has_any_permission = None
    actor_effective_has_permission = None
    normalize_api_token_permissions = None
    normalize_profile_api_audit_category = None
    profile_api_audit_category_condition = None
    profile_api_audit_search_term = None
    profile_api_audit_token_match_condition = None
    profile_api_access_payload = None
    public_request_ip = None
    request_ip_details = None


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

    def test_profile_api_audit_category_filter_is_normalized(self):
        self.assertEqual(normalize_profile_api_audit_category(None), "all")
        self.assertEqual(normalize_profile_api_audit_category(""), "all")
        self.assertEqual(normalize_profile_api_audit_category("MCP"), "mcp")
        self.assertEqual(normalize_profile_api_audit_category("security"), "security")
        self.assertEqual(normalize_profile_api_audit_category("not-a-filter"), "all")

    def test_profile_api_audit_category_conditions_scope_events(self):
        api_sql, api_params = profile_api_audit_category_condition("api")
        mcp_sql, mcp_params = profile_api_audit_category_condition("mcp")
        security_sql, security_params = profile_api_audit_category_condition("security")
        all_sql, all_params = profile_api_audit_category_condition("all")

        self.assertIn("category = %s", api_sql)
        self.assertIn("api.%", api_sql)
        self.assertIn("api.mcp_", api_sql)
        self.assertEqual(api_params, ["api"])
        self.assertIn("mcp.%", mcp_sql)
        self.assertIn("api.mcp_", mcp_sql)
        self.assertEqual(mcp_params, ["mcp"])
        self.assertIn("api_token.created", security_sql)
        self.assertEqual(security_params, [])
        self.assertIn("category IN ('api', 'mcp')", all_sql)
        self.assertEqual(all_params, [])

    def test_profile_api_audit_search_term_is_trimmed_and_capped(self):
        self.assertEqual(profile_api_audit_search_term(None), "")
        self.assertEqual(profile_api_audit_search_term("  /mcp  "), "/mcp")
        self.assertEqual(len(profile_api_audit_search_term("x" * 200)), 120)

    def test_profile_api_audit_token_match_condition_includes_token_names(self):
        sql, params = profile_api_audit_token_match_condition(["token-1"], ["DiscVault MCP"])

        self.assertIn("metadata->>'apiTokenId'", sql)
        self.assertIn("target_type = 'api_access_token'", sql)
        self.assertIn("metadata->>'apiTokenName'", sql)
        self.assertEqual(params, ["token-1", "token-1", "token-1", "token-1", "token-1", "DiscVault MCP"])

    def test_profile_api_audit_token_match_condition_handles_empty_tokens(self):
        sql, params = profile_api_audit_token_match_condition([], ["Ignored"])

        self.assertEqual(sql, "(false)")
        self.assertEqual(params, [])

    def test_public_request_ip_prefers_forwarded_public_client_ip(self):
        app = Flask(__name__)

        with app.test_request_context(
            "/api/next/mcp/catalog",
            headers={"X-Forwarded-For": "8.8.8.8, 172.26.0.5"},
            environ_base={"REMOTE_ADDR": "172.26.0.5"},
        ):
            details = request_ip_details()
            selected = public_request_ip()

        self.assertEqual(selected, "8.8.8.8")
        self.assertEqual(details["ip"], "8.8.8.8")
        self.assertEqual(details["source"], "X-Forwarded-For[0]")

    def test_public_request_ip_ignores_private_remote_addr_when_no_proxy_header_exists(self):
        app = Flask(__name__)

        with app.test_request_context(
            "/api/next/mcp/catalog",
            environ_base={"REMOTE_ADDR": "172.26.0.5"},
        ):
            details = request_ip_details()
            selected = public_request_ip()

        self.assertEqual(selected, "")
        self.assertEqual(details["ip"], "")
        self.assertEqual(details["source"], "")
        self.assertEqual(details["candidates"], [{"ip": "172.26.0.5", "source": "remote_addr", "scope": "private"}])


if __name__ == "__main__":
    unittest.main()
