"""One MCP tool list, six places that have to agree.

`app/mcp-server/server.py` decides what a client is *offered*; the backend
catalogue decides what a call is *allowed* to do, and every call is checked
against it. Nothing forces the two together, so a tool can be advertised and
permanently unusable, and neither side is wrong on its own.

That shipped. #654 added five statistics tools to the MCP server without a
catalogue entry or a permission, so all five answered "Permission denied" for
every role -- owner included -- and no setting could grant them, because the
permission keys did not exist. `tools/list` kept advertising them.

The properties below are the ones that failure violated: a tool is only real
when the server offers it, the catalogue knows it, a migration grants it, and
the profile screen can name it. The bundled plugin reports the same set,
because its health check is what an operator reads to decide the MCP server is
fine.
"""

import ast
import json
import pathlib
import re
import sys
import unittest


BACKEND = pathlib.Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND.parent
REPO_ROOT = APP_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.backend.next_mcp_activity import (  # noqa: E402
    MCP_STATISTICS_TOOL_NAMES,
    MCP_TOOL_NAMES,
)

MCP_SERVER_SOURCE = APP_ROOT / "mcp-server" / "server.py"
MIGRATIONS_DIR = BACKEND / "migrations_next"
PLUGIN_DIR = BACKEND / "next_plugins" / "discvault_mcp"
UI_SOURCE = BACKEND / "next_views_ui.py"
EN_US = APP_ROOT / "frontend" / "i18n" / "next" / "en-US.json"


def advertised_tool_names() -> list[str]:
    """The `name` of every entry in server.py's module-level TOOLS list.

    Parsed rather than imported: server.py is a separate deployment unit with
    its own requirements, and this test must not depend on installing them.
    """
    tree = ast.parse(MCP_SERVER_SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "TOOLS"
            for target in node.targets
        ):
            continue
        names = []
        for element in node.value.elts:
            entry = ast.literal_eval(element)
            names.append(entry["name"])
        return names
    raise AssertionError("server.py no longer defines a module-level TOOLS list")


class McpToolCatalogTest(unittest.TestCase):
    def test_the_server_advertises_exactly_the_catalogued_tools(self):
        advertised = advertised_tool_names()
        self.assertEqual(
            sorted(advertised),
            sorted(MCP_TOOL_NAMES),
            "app/mcp-server/server.py and MCP_TOOL_NAMES disagree: a tool in "
            "only one of them is either offered and always refused, or "
            "grantable and never offered",
        )
        self.assertEqual(len(advertised), len(set(advertised)))

    def test_every_tool_has_a_permission_a_migration_grants(self):
        granted = set()
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            granted.update(re.findall(r"'mcp\.tool\.([a-z_]+)'", path.read_text(encoding="utf-8")))
        missing = sorted(set(MCP_TOOL_NAMES) - granted)
        self.assertEqual(
            missing,
            [],
            "no migration inserts a permissions row for these tools, so "
            "/api/next/mcp/catalog can never report them allowed",
        )

    def test_the_statistics_tools_are_part_of_the_catalogue(self):
        for tool in MCP_STATISTICS_TOOL_NAMES:
            self.assertIn(tool, MCP_TOOL_NAMES)

    def test_the_statistics_endpoint_accepts_the_statistics_tools(self):
        # The five statistics tools read /api/next/stats/personal and nothing
        # else. Gated on watchlist.manage alone, a token scoped to those tools
        # is refused by the route after the catalogue has allowed the call.
        source = (BACKEND / "next_app.py").read_text(encoding="utf-8")
        marker = source.index('@flask_app.get("/api/next/stats/personal")')
        body = source[marker : marker + 4000]
        self.assertIn("MCP_STATISTICS_TOOL_PERMISSIONS", body)

    def test_the_profile_screen_can_name_every_tool(self):
        ui_source = UI_SOURCE.read_text(encoding="utf-8")
        catalog = json.loads(EN_US.read_text(encoding="utf-8"))
        for tool in MCP_TOOL_NAMES:
            with self.subTest(tool=tool):
                self.assertIn(f'"mcp.tool.{tool}": "', ui_source)
                self.assertIn(f"profile.perm.mcp.tool.{tool}", catalog)

    def test_the_bundled_plugin_reports_the_same_tools(self):
        plugin_source = (PLUGIN_DIR / "plugin.py").read_text(encoding="utf-8")
        tree = ast.parse(plugin_source)
        tools = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "MCP_TOOLS"
                for target in node.targets
            ):
                tools = ast.literal_eval(node.value)
        self.assertIsNotNone(tools, "discvault_mcp/plugin.py defines no MCP_TOOLS")
        self.assertEqual(sorted(tools), sorted(MCP_TOOL_NAMES))

        manifest = json.loads((PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))
        capabilities = set(manifest["capabilities"]) - {"mcp"}
        self.assertEqual(sorted(capabilities), sorted(MCP_TOOL_NAMES))


if __name__ == "__main__":
    unittest.main()
