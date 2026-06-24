import importlib.util
import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SERVER_PATH = os.path.join(repo_root, "app", "mcp-server", "server.py")


def _load_server_module():
    spec = importlib.util.spec_from_file_location("discvault_mcp_server", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


server = _load_server_module()


class McpProtocolNegotiationTests(unittest.TestCase):
    def test_supported_version_is_echoed_back(self):
        for version in server.SUPPORTED_PROTOCOL_VERSIONS:
            self.assertEqual(server.negotiate_protocol_version(version), version)

    def test_unknown_version_falls_back_to_default(self):
        self.assertEqual(
            server.negotiate_protocol_version("2025-11-05"),
            server.DEFAULT_PROTOCOL_VERSION,
        )

    def test_missing_version_uses_default(self):
        self.assertEqual(
            server.negotiate_protocol_version(None),
            server.DEFAULT_PROTOCOL_VERSION,
        )
        self.assertEqual(
            server.negotiate_protocol_version(123),
            server.DEFAULT_PROTOCOL_VERSION,
        )

    def test_default_version_is_supported(self):
        self.assertIn(server.DEFAULT_PROTOCOL_VERSION, server.SUPPORTED_PROTOCOL_VERSIONS)

    def test_initialize_negotiates_client_version(self):
        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        }
        response = server.handle_message(msg)
        self.assertEqual(response["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(response["result"]["serverInfo"]["name"], "discvault")

    def test_initialize_falls_back_for_unsupported_client_version(self):
        msg = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-05"},
        }
        response = server.handle_message(msg)
        self.assertEqual(
            response["result"]["protocolVersion"],
            server.DEFAULT_PROTOCOL_VERSION,
        )


if __name__ == "__main__":
    unittest.main()
