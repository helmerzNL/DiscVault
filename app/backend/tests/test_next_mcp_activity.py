import os
import sys
import unittest
from datetime import datetime, timezone
from uuid import uuid4


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from flask import Flask

    from app.backend import next_app
    from app.backend import next_mcp_activity
except ModuleNotFoundError as exc:  # Local minimal test environments may omit optional backend deps.
    if exc.name not in {"flask", "psycopg"}:
        raise
    Flask = None
    next_app = None
    next_mcp_activity = None


@unittest.skipIf(next_mcp_activity is None, "Flask is not installed in this test environment")
class NextMcpActivityHelperTests(unittest.TestCase):
    def test_helpers_are_reexported_from_next_app(self):
        self.assertIs(next_app.MCP_TOOL_NAMES, next_mcp_activity.MCP_TOOL_NAMES)
        self.assertIs(next_app.mcp_request_api_token_value, next_mcp_activity.mcp_request_api_token_value)
        self.assertIs(next_app.register_next_mcp_routes, next_mcp_activity.register_next_mcp_routes)

    def test_mcp_tool_names_include_core_tools(self):
        self.assertIn("search_collection", next_mcp_activity.MCP_TOOL_NAMES)
        self.assertIn("lookup_barcode", next_mcp_activity.MCP_TOOL_NAMES)

    def test_token_value_prefers_bearer_authorization_header(self):
        app = Flask(__name__)
        with app.test_request_context("/mcp", headers={"Authorization": "Bearer dvapi_example"}):
            self.assertEqual(next_mcp_activity.mcp_request_api_token_value(), "dvapi_example")

    def test_token_value_falls_back_to_custom_headers(self):
        app = Flask(__name__)
        with app.test_request_context("/mcp", headers={"X-DiscVault-Api-Token": "dvapi_custom"}):
            self.assertEqual(next_mcp_activity.mcp_request_api_token_value(), "dvapi_custom")

    def test_token_value_is_empty_without_credentials(self):
        app = Flask(__name__)
        with app.test_request_context("/mcp"):
            self.assertEqual(next_mcp_activity.mcp_request_api_token_value(), "")

    def test_activity_log_entry_maps_audit_metadata(self):
        created_at = datetime(2026, 6, 11, 9, 12, 33, tzinfo=timezone.utc)
        entry = next_mcp_activity.mcp_activity_log_entry(
            {
                "id": uuid4(),
                "event_type": "mcp.request",
                "category": "mcp",
                "summary": "MCP search_collection -> 200",
                "request_ip": "203.0.113.42",
                "metadata": {
                    "apiTokenName": "Hermes MCP",
                    "apiTokenScopes": ["read", "mcp"],
                    "agent": "python-requests/2.34.2",
                    "mcpTools": ["search_collection"],
                    "method": "POST",
                    "mcpPath": "/mcp",
                    "mcpSessionId": "session-secret",
                    "responseStatus": 200,
                    "request": {
                        "params": {"name": "search_collection", "arguments": {"query": "Indiana Jones"}},
                        "headers": {"authorization": "Bearer secret"},
                    },
                },
                "user_agent": "python-requests/2.34.2",
                "created_at": created_at,
            }
        )
        self.assertEqual(entry["timestamp"], "2026-06-11T09:12:33.000Z")
        self.assertEqual(entry["level"], "info")
        self.assertEqual(entry["event"], "tool_call.completed")
        self.assertEqual(entry["source"], "mcp")
        self.assertEqual(entry["client"], "Hermes MCP")
        self.assertEqual(entry["agent"], "Hermes MCP")
        self.assertEqual(entry["user_agent"], "python-requests/2.34.2")
        self.assertEqual(entry["ip_address"], "203.0.113.42")
        self.assertEqual(entry["tool_name"], "search_collection")
        self.assertEqual(entry["method"], "POST")
        self.assertEqual(entry["path"], "/mcp")
        self.assertEqual(entry["metadata"]["status_code"], 200)
        self.assertEqual(entry["metadata"]["transport"], "http")
        self.assertEqual(entry["metadata"]["session_id"], "[REDACTED]")
        self.assertEqual(entry["metadata"]["input"]["headers"]["authorization"], "[REDACTED]")
        self.assertEqual(entry["metadata"]["input"]["params"]["arguments"]["query"], "Indiana Jones")
        self.assertEqual(entry["metadata"]["tools"], ["search_collection"])
        self.assertEqual(entry["metadata"]["api_token_name"], "Hermes MCP")

    def test_activity_log_entry_uses_ip_candidates_and_empty_metadata_object(self):
        entry = next_mcp_activity.mcp_activity_log_entry(
            {
                "id": "log_1",
                "event_type": "mcp.request",
                "category": "mcp",
                "summary": "MCP request",
                "metadata": {
                    "requestIpCandidates": [
                        {"ip": "172.26.0.5", "scope": "private", "source": "remote_addr"},
                        {"ip": "203.0.113.42", "scope": "public", "source": "X-Forwarded-For"},
                    ]
                },
                "user_agent": "",
                "created_at": None,
            }
        )
        self.assertEqual(entry["timestamp"], "")
        self.assertEqual(entry["agent"], "Custom MCP Client")
        self.assertEqual(entry["ip_address"], "203.0.113.42")
        self.assertIsInstance(entry["metadata"], dict)

        empty_entry = next_mcp_activity.mcp_activity_log_entry(
            {
                "id": "log_2",
                "event_type": "mcp.request",
                "category": "mcp",
                "summary": "MCP request",
                "metadata": {},
                "user_agent": "",
                "created_at": None,
            }
        )
        self.assertEqual(empty_entry["metadata"], {})

    def test_activity_log_filter_recognizes_ios_clients(self):
        self.assertTrue(
            next_mcp_activity.mcp_activity_log_is_ios(
                {
                    "source": "mcp",
                    "client": "DiscVault iOS",
                    "user_agent": "DiscVault/iOS 1.0",
                }
            )
        )
        self.assertFalse(
            next_mcp_activity.mcp_activity_log_is_ios(
                {
                    "source": "mcp",
                    "client": "Hermes MCP",
                    "user_agent": "python-requests/2.34.2",
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
