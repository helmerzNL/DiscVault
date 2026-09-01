"""Where /mcp forwards to, and why it has to be settable.

`/mcp` on the API port is a proxy. Its target was hardcoded to
`http://127.0.0.1:6090`, which is right for the all-in-one image -- Supervisor
runs the API and the MCP server in one container -- and wrong for every split
deployment, where the MCP server is its own container and loopback is the API's
own. There `/mcp` answered "MCP server is not reachable" however healthy the MCP
server was, and nothing could move the address.

The port is the trap inside the trap: `DISCVAULT_NEXT_MCP_PORT` republishes the
*host* port and leaves the container on 6090, so an operator who changed it
reasonably concluded the proxy had followed.
"""

import os
import pathlib
import sys
import unittest
from unittest import mock

BACKEND = pathlib.Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND.parent
REPO_ROOT = APP_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.backend.next_mcp_activity import (  # noqa: E402
    MCP_PROXY_DEFAULT_URL,
    mcp_proxy_base_url,
)

COMPOSE = APP_ROOT / "deploy" / "next" / "docker-compose.yml"


class McpProxyTargetTest(unittest.TestCase):
    def test_the_default_is_the_all_in_one_image(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DISCVAULT_MCP_URL", None)
            self.assertEqual(mcp_proxy_base_url(), "http://127.0.0.1:6090")
        self.assertEqual(MCP_PROXY_DEFAULT_URL, "http://127.0.0.1:6090")

    def test_an_empty_value_falls_back_rather_than_building_a_bare_path(self):
        for value in ("", "   "):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"DISCVAULT_MCP_URL": value}):
                    self.assertEqual(mcp_proxy_base_url(), MCP_PROXY_DEFAULT_URL)

    def test_a_service_address_is_honoured(self):
        with mock.patch.dict(os.environ, {"DISCVAULT_MCP_URL": "http://next-mcp:6090"}):
            self.assertEqual(mcp_proxy_base_url(), "http://next-mcp:6090")

    def test_a_trailing_slash_does_not_double_up_on_the_path(self):
        with mock.patch.dict(os.environ, {"DISCVAULT_MCP_URL": "http://next-mcp:6090/"}):
            self.assertEqual(mcp_proxy_base_url() + "/mcp", "http://next-mcp:6090/mcp")

    def test_a_missing_scheme_is_assumed_to_be_http(self):
        # `next-mcp:6090` is the obvious thing to write, and requests rejects it
        # with an error naming neither the variable nor the omission.
        with mock.patch.dict(os.environ, {"DISCVAULT_MCP_URL": "next-mcp:6090"}):
            self.assertEqual(mcp_proxy_base_url(), "http://next-mcp:6090")

    def test_the_split_deployment_sets_it(self):
        # Without this line the compose file ships a topology its own /mcp route
        # cannot serve, which is the defect this test exists for.
        compose = COMPOSE.read_text(encoding="utf-8")
        self.assertIn("DISCVAULT_MCP_URL", compose)
        self.assertIn("http://next-mcp:6090", compose)


if __name__ == "__main__":
    unittest.main()
