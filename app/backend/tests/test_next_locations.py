import os
import sys
import io
import re
import shutil
import subprocess
import tempfile
import unittest
from PIL import Image


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_app import NextApiError
    from app.backend.next_app import LOCATION_MAX_DEPTH
    from app.backend.next_app import LOCATION_NAME_LIMIT
    from app.backend.next_app import LOCATION_DESCRIPTION_LIMIT
    from app.backend.next_app import location_payload
    from app.backend.next_app import location_public_id
    from app.backend.next_app import location_qr_token
    from app.backend.next_app import location_qr_svg
    from app.backend.next_app import location_qr_png
    from app.backend.next_app import location_deep_link
    from app.backend.next_app import PUBLIC_NEXT_PREFIXES
    from app.backend.next_app import location_summary_object
    from app.backend.next_app import collection_dashboard_html
    from app.backend.next_app import _location_path_names
    from app.backend.next_app import _location_children_map
    from app.backend.next_app import _location_depth_of
    from app.backend.next_app import _location_subtree_height
    from app.backend.next_app import _location_is_descendant
    from app.backend.next_views_ui import ui_preview_html
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name != "flask":
        raise
    NextApiError = None
    LOCATION_MAX_DEPTH = None
    LOCATION_NAME_LIMIT = None
    LOCATION_DESCRIPTION_LIMIT = None
    location_payload = None
    location_public_id = None
    location_qr_token = None
    location_qr_svg = None
    location_qr_png = None
    location_deep_link = None
    PUBLIC_NEXT_PREFIXES = ()
    location_summary_object = None
    collection_dashboard_html = None
    _location_path_names = None
    _location_children_map = None
    _location_depth_of = None
    _location_subtree_height = None
    _location_is_descendant = None
    ui_preview_html = None


def _build_index():
    """A 4-level chain (k1 > l1 > v1 > d1) plus a sibling root k2."""
    return {
        "k1": {"id": "k1", "public_id": "next-location-k1", "parent_id": None, "name": "Kast 01"},
        "l1": {"id": "l1", "public_id": "next-location-l1", "parent_id": "k1", "name": "Lade 01"},
        "v1": {"id": "v1", "public_id": "next-location-v1", "parent_id": "l1", "name": "Vak 01"},
        "d1": {"id": "d1", "public_id": "next-location-d1", "parent_id": "v1", "name": "Doos 01"},
        "k2": {"id": "k2", "public_id": "next-location-k2", "parent_id": None, "name": "Kast 02"},
    }


@unittest.skipIf(location_payload is None, "Flask is not installed in this test environment")
class LocationPayloadTests(unittest.TestCase):
    def test_requires_name_on_create(self):
        with self.assertRaises(NextApiError):
            location_payload({"description": "no name"})

    def test_trims_and_keeps_name_and_description(self):
        payload = location_payload({"name": "  Kast 01  ", "description": "  Top shelf  "})
        self.assertEqual(payload["name"], "Kast 01")
        self.assertEqual(payload["description"], "Top shelf")

    def test_description_can_be_cleared(self):
        payload = location_payload({"description": ""}, existing={"name": "Kast 01", "description": "old"})
        self.assertEqual(payload["name"], "Kast 01")
        self.assertIsNone(payload["description"])

    def test_name_kept_from_existing_when_absent(self):
        payload = location_payload({"description": "new"}, existing={"name": "Kast 01"})
        self.assertEqual(payload["name"], "Kast 01")
        self.assertEqual(payload["description"], "new")

    def test_rejects_overlong_name(self):
        with self.assertRaises(NextApiError):
            location_payload({"name": "x" * (LOCATION_NAME_LIMIT + 1)})

    def test_rejects_overlong_description(self):
        with self.assertRaises(NextApiError):
            location_payload({"name": "Kast", "description": "y" * (LOCATION_DESCRIPTION_LIMIT + 1)})


@unittest.skipIf(location_payload is None, "Flask is not installed in this test environment")
class LocationHierarchyTests(unittest.TestCase):
    def setUp(self):
        self.index = _build_index()
        self.children = _location_children_map(self.index)

    def test_depth_of_each_level(self):
        self.assertEqual(_location_depth_of(self.index, "k1"), 1)
        self.assertEqual(_location_depth_of(self.index, "l1"), 2)
        self.assertEqual(_location_depth_of(self.index, "v1"), 3)
        self.assertEqual(_location_depth_of(self.index, "d1"), 4)

    def test_subtree_height(self):
        self.assertEqual(_location_subtree_height(self.children, "d1"), 1)
        self.assertEqual(_location_subtree_height(self.children, "v1"), 2)
        self.assertEqual(_location_subtree_height(self.children, "l1"), 3)
        self.assertEqual(_location_subtree_height(self.children, "k1"), 4)

    def test_is_descendant(self):
        self.assertTrue(_location_is_descendant(self.children, "k1", "d1"))
        self.assertTrue(_location_is_descendant(self.children, "l1", "v1"))
        self.assertFalse(_location_is_descendant(self.children, "d1", "k1"))
        self.assertFalse(_location_is_descendant(self.children, "k1", "k2"))

    def test_depth_guard_math_rejects_deep_move(self):
        # Moving k1 (subtree height 4) under k2 (depth 1) => 1 + 4 = 5 > 4 => illegal.
        parent_depth = _location_depth_of(self.index, "k2")
        height = _location_subtree_height(self.children, "k1")
        self.assertGreater(parent_depth + height, LOCATION_MAX_DEPTH)

    def test_depth_guard_math_allows_shallow_move(self):
        # Moving d1 (height 1) under k2 (depth 1) => 1 + 1 = 2 <= 4 => allowed.
        parent_depth = _location_depth_of(self.index, "k2")
        height = _location_subtree_height(self.children, "d1")
        self.assertLessEqual(parent_depth + height, LOCATION_MAX_DEPTH)

    def test_path_names_and_summary(self):
        self.assertEqual(_location_path_names(self.index, "v1"), ["Kast 01", "Lade 01", "Vak 01"])
        summary = location_summary_object(self.index, "v1")
        self.assertEqual(summary["name"], "Vak 01")
        self.assertEqual(summary["parentId"], "l1")
        self.assertEqual(summary["pathLabel"], "Kast 01 / Lade 01 / Vak 01")

    def test_summary_none_for_missing(self):
        self.assertIsNone(location_summary_object(self.index, None))
        self.assertIsNone(location_summary_object(self.index, "does-not-exist"))


@unittest.skipIf(location_payload is None, "Flask is not installed in this test environment")
class LocationQrTests(unittest.TestCase):
    def test_public_id_and_token_are_unique(self):
        self.assertNotEqual(location_public_id(), location_public_id())
        self.assertTrue(location_public_id().startswith("next-location-"))
        self.assertNotEqual(location_qr_token(), location_qr_token())

    def test_deep_link_without_request_context(self):
        self.assertEqual(location_deep_link("next-location-abc"), "/open/locations/next-location-abc")

    def test_public_prefixes_allow_app_location_routes(self):
        self.assertIn("/app/locations/", PUBLIC_NEXT_PREFIXES)

    def test_qr_svg_encodes_link(self):
        svg = location_qr_svg("https://discvault.example/app/locations/next-location-abc")
        self.assertIn("<svg", svg)
        self.assertIn("</svg>", svg)

    def test_qr_png_is_square(self):
        png = location_qr_png("https://discvault.example/app/locations/next-location-abc")
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(io.BytesIO(png)) as qr_image:
            self.assertEqual(qr_image.width, qr_image.height)


@unittest.skipIf(collection_dashboard_html is None, "Flask is not installed in this test environment")
class UiShellScriptSyntaxTests(unittest.TestCase):
    def assert_executable_scripts_are_valid_javascript(self, html):
        node_binary = shutil.which("node")
        if not node_binary:
            self.skipTest("node is not installed in this test environment")

        script_pattern = re.compile(r"<script([^>]*)>([\s\S]*?)</script>", re.IGNORECASE)
        script_blocks = list(script_pattern.finditer(html))
        self.assertGreater(len(script_blocks), 0)

        executable_scripts = 0
        with tempfile.TemporaryDirectory() as temp_dir:
            for index, match in enumerate(script_blocks, start=1):
                attrs = match.group(1) or ""
                if re.search(r'type\s*=\s*["\']application/json["\']', attrs, re.IGNORECASE):
                    continue
                executable_scripts += 1
                script_body = match.group(2) or ""
                script_path = os.path.join(temp_dir, f"script_{index}.js")
                with open(script_path, "w", encoding="utf-8") as handle:
                    handle.write(script_body)
                result = subprocess.run(
                    [node_binary, "--check", script_path],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    msg=f"Inline executable script block {index} is invalid JavaScript:\n{result.stderr}",
                )

        self.assertGreater(executable_scripts, 0)

    def test_collection_dashboard_executable_scripts_are_valid_javascript(self):
        self.assert_executable_scripts_are_valid_javascript(collection_dashboard_html({}))

    def test_primary_ui_executable_scripts_are_valid_javascript(self):
        self.assert_executable_scripts_are_valid_javascript(ui_preview_html({}, app_mode=True))


if __name__ == "__main__":
    unittest.main()
