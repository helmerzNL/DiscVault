import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.scripts.build_bluray_com_plugin import build


class BuildBlurayComPluginTests(unittest.TestCase):
    def test_build_is_reproducible_and_contains_only_plugin_files(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            archive_one, digest_one = build(Path(first))
            archive_two, digest_two = build(Path(second))

            self.assertEqual(digest_one, digest_two)
            self.assertEqual(archive_one.read_bytes(), archive_two.read_bytes())
            with zipfile.ZipFile(archive_one) as package:
                self.assertEqual(
                    package.namelist(),
                    ["bluray_com/manifest.json", "bluray_com/plugin.py"],
                )
                manifest = json.loads(package.read("bluray_com/manifest.json"))
                self.assertEqual(manifest["version"], "1.0.6")


if __name__ == "__main__":
    unittest.main()
