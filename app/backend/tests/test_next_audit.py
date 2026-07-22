import os
import sys
import unittest
from unittest import mock


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from flask import Flask

    from app.backend import next_app
    from app.backend import next_audit
except ModuleNotFoundError as exc:  # Local minimal test environments may omit optional backend deps.
    if exc.name not in {"flask", "psycopg"}:
        raise
    Flask = None
    next_app = None
    next_audit = None


class _AuditCountCursor:
    def __init__(self, rows):
        self.rows = rows
        self.query = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query):
        self.query = query

    def fetchall(self):
        return self.rows


class _AuditCountConnection:
    def __init__(self, rows):
        self.cursor_instance = _AuditCountCursor(rows)

    def cursor(self):
        return self.cursor_instance


@unittest.skipIf(next_audit is None, "Flask is not installed in this test environment")
class NextAuditHelperTests(unittest.TestCase):
    def test_audit_helpers_are_reexported_from_next_app(self):
        names = [
            "audit_event",
            "audit_event_counts",
            "audit_event_row",
            "audit_api_interaction",
            "api_audit_metadata",
            "api_request_query_payload",
            "normalize_profile_api_audit_category",
            "normalize_request_ip_candidate",
            "profile_api_audit_category_condition",
            "profile_api_audit_search_term",
            "profile_api_audit_token_match_condition",
            "public_request_ip",
            "redact_sensitive_payload",
            "request_ip_audit_metadata",
            "request_ip_details",
            "register_next_audit_routes",
        ]
        for name in names:
            self.assertIs(getattr(next_app, name), getattr(next_audit, name), name)

    def test_audit_event_counts_are_not_limited_to_recent_events(self):
        connection = _AuditCountConnection(
            [
                {"category": "security", "count": 14},
                {"category": "admin", "count": 7},
                {"category": "backup", "count": 3},
            ]
        )

        with mock.patch("app.backend.next_audit.table_exists", return_value=True):
            counts = next_audit.audit_event_counts(connection)

        self.assertEqual(counts["total"], 24)
        self.assertEqual(counts["byCategory"], {"security": 14, "admin": 7, "backup": 3})
        self.assertIn("GROUP BY category", connection.cursor_instance.query)
        self.assertNotIn("LIMIT", connection.cursor_instance.query)

    def test_redact_sensitive_payload_masks_secret_like_keys(self):
        redacted = next_audit.redact_sensitive_payload(
            {"token": "abc", "name": "ok", "nested": {"password": "p"}}
        )
        self.assertEqual(redacted, {"token": "***", "name": "ok", "nested": {"password": "***"}})

    def test_profile_api_audit_category_filter_is_normalized(self):
        self.assertEqual(next_audit.normalize_profile_api_audit_category(None), "all")
        self.assertEqual(next_audit.normalize_profile_api_audit_category("MCP"), "mcp")
        self.assertEqual(next_audit.normalize_profile_api_audit_category("security"), "security")
        self.assertEqual(next_audit.normalize_profile_api_audit_category("not-a-filter"), "all")

    def test_profile_api_audit_category_conditions_scope_events(self):
        api_sql, api_params = next_audit.profile_api_audit_category_condition("api")
        mcp_sql, mcp_params = next_audit.profile_api_audit_category_condition("mcp")
        security_sql, security_params = next_audit.profile_api_audit_category_condition("security")
        all_sql, all_params = next_audit.profile_api_audit_category_condition("all")

        self.assertIn("category = %s", api_sql)
        self.assertIn("api.mcp_", api_sql)
        self.assertEqual(api_params, ["api"])
        self.assertIn("mcp.%", mcp_sql)
        self.assertEqual(mcp_params, ["mcp"])
        self.assertIn("api_token.created", security_sql)
        self.assertEqual(security_params, [])
        self.assertIn("category IN ('api', 'mcp')", all_sql)
        self.assertEqual(all_params, [])

    def test_profile_api_audit_search_term_is_trimmed_and_capped(self):
        self.assertEqual(next_audit.profile_api_audit_search_term(None), "")
        self.assertEqual(next_audit.profile_api_audit_search_term("  /mcp  "), "/mcp")
        self.assertEqual(len(next_audit.profile_api_audit_search_term("x" * 200)), 120)

    def test_profile_api_audit_token_match_condition_includes_token_names(self):
        sql, params = next_audit.profile_api_audit_token_match_condition(["token-1"], ["DiscVault MCP"])

        self.assertIn("metadata->>'apiTokenId'", sql)
        self.assertIn("target_type = 'api_access_token'", sql)
        self.assertIn("metadata->>'apiTokenName'", sql)
        self.assertEqual(params, ["token-1", "token-1", "token-1", "token-1", "token-1", "DiscVault MCP"])

    def test_profile_api_audit_token_match_condition_handles_empty_tokens(self):
        sql, params = next_audit.profile_api_audit_token_match_condition([], ["Ignored"])

        self.assertEqual(sql, "(false)")
        self.assertEqual(params, [])

    def test_public_request_ip_prefers_forwarded_public_client_ip(self):
        app = Flask(__name__)

        with app.test_request_context(
            "/api/next/mcp/catalog",
            headers={"X-Forwarded-For": "8.8.8.8, 172.26.0.5"},
            environ_base={"REMOTE_ADDR": "172.26.0.5"},
        ):
            details = next_audit.request_ip_details()
            selected = next_audit.public_request_ip()

        self.assertEqual(selected, "8.8.8.8")
        self.assertEqual(details["ip"], "8.8.8.8")
        self.assertEqual(details["source"], "X-Forwarded-For[0]")

    def test_public_request_ip_prefers_discvault_forwarded_client_ip(self):
        app = Flask(__name__)

        with app.test_request_context(
            "/api/next/mcp/catalog",
            headers={"X-DiscVault-Client-IP": "8.8.4.4", "X-Forwarded-For": "8.8.8.8"},
            environ_base={"REMOTE_ADDR": "172.26.0.5"},
        ):
            details = next_audit.request_ip_details()
            selected = next_audit.public_request_ip()

        self.assertEqual(selected, "8.8.4.4")
        self.assertEqual(details["ip"], "8.8.4.4")
        self.assertEqual(details["source"], "X-DiscVault-Client-IP")

    def test_api_audit_metadata_includes_command_and_request_ip(self):
        app = Flask(__name__)
        actor = {"apiToken": {"id": "tok-1", "name": "MCP", "scopes": ["mcp"], "permissionKeys": ["mcp.use"]}}

        with app.test_request_context(
            "/api/next/mcp/catalog?q=batman",
            environ_base={"REMOTE_ADDR": "8.8.8.8"},
        ):
            metadata = next_audit.api_audit_metadata(actor, command="search_collection")

        self.assertEqual(metadata["command"], "search_collection")
        self.assertEqual(metadata["endpoint"], "/api/next/mcp/catalog")
        self.assertEqual(metadata["apiTokenId"], "tok-1")
        self.assertEqual(metadata["apiTokenPermissions"], ["mcp.use"])
        self.assertEqual(metadata["query"], {"q": "batman"})
        self.assertEqual(metadata["requestIp"], "8.8.8.8")
        self.assertEqual(metadata["requestIpSource"], "remote_addr")

    def test_public_request_ip_does_not_fall_back_to_docker_private_ip(self):
        app = Flask(__name__)

        with app.test_request_context(
            "/api/next/mcp/catalog",
            environ_base={"REMOTE_ADDR": "172.26.0.5"},
        ):
            details = next_audit.request_ip_details()
            selected = next_audit.public_request_ip()

        self.assertEqual(selected, "")
        self.assertEqual(details["ip"], "")
        self.assertEqual(details["source"], "")
        self.assertEqual(details["candidates"][0]["ip"], "172.26.0.5")


if __name__ == "__main__":
    unittest.main()
