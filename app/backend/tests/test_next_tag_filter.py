"""Execute Core's tag filtering and saved-filter round trip under Node."""
import pathlib
import shutil
import subprocess
import unittest


class TagFilterTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is not available")
    def test_tag_filter_behaviour(self):
        harness = pathlib.Path(__file__).parent / "fixtures" / "tag-filter-replay.mjs"
        result = subprocess.run(
            [shutil.which("node"), str(harness)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
