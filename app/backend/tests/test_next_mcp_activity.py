import os
import sys
import unittest


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


if __name__ == "__main__":
    unittest.main()
