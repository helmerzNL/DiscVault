"""Run library-paging.js and check what it does, not what it says.

Every other test of this module reads it as source text -- `assertIn` against the
file. That is how #715 shipped: all of those assertions passed while a user with
2,509 movies watched the library fill to 700 and stop for good. Source-text tests
pin the shape of a fix already understood; they cannot notice a case nobody thought
of, because there is no case, only a string.

So this one executes the module. `fixtures/library-hydration-replay.mjs` stubs the
browser (fetch, timers, the DOM lookups the module makes) and stands up a bridge
that mirrors `window.DiscVaultLibrary` in next_views_ui.py exactly, then plays the
reported scenario through the real file:

    clean            hydration with nothing interfering
    race-fixed       the SPA reloads its snapshot while a page is in flight
    race-unguarded   the same, against a bridge that ignores the offset guard

`race-fixed` is #715. Against the pre-fix module it reports 700 of 2,509 loaded and
the "Only part of the library could be loaded." warning -- the reporter's screenshot,
reproduced in about a second.

Skipped when node is unavailable. That is a real gap on a machine without it, and
the alternative -- a JavaScript engine vendored into a Python test suite -- costs
more than it is worth for one file. CI runs ubuntu-latest, which ships node.
"""

import json
import os
import shutil
import subprocess
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO_APP_DIR = os.path.abspath(os.path.join(BACKEND_DIR, ".."))
LIBRARY_PAGING_JS_PATH = os.path.join(REPO_APP_DIR, "frontend", "js", "library-paging.js")
HARNESS_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "library-hydration-replay.mjs"
)

NODE = shutil.which("node")

# The reporter's library, and the two page sizes that produce the number in the
# report: 200 from the first-paint snapshot, then chunks of 500.
TOTAL = 2_509
SNAPSHOT = 200
CHUNK = 500
STALLED_AT = SNAPSHOT + CHUNK  # 700


@unittest.skipUnless(NODE, "node is not available")
class LibraryHydrationReplayTests(unittest.TestCase):
    def replay(self, scenario):
        result = subprocess.run(
            [NODE, HARNESS_PATH, LIBRARY_PAGING_JS_PATH, scenario],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_the_harness_matches_the_page_sizes_the_report_describes(self):
        # If these drift apart, a passing run stops meaning what the docstring says.
        with open(HARNESS_PATH, encoding="utf-8") as handle:
            harness = handle.read()
        self.assertIn(f"SNAPSHOT = {SNAPSHOT}", harness)
        self.assertIn(f"TOTAL = {TOTAL}", harness)
        with open(LIBRARY_PAGING_JS_PATH, encoding="utf-8") as handle:
            self.assertIn(f"var CHUNK_SIZE = {CHUNK};", handle.read())

    def test_an_undisturbed_hydration_loads_the_whole_library(self):
        state = self.replay("clean")
        self.assertEqual(state["loaded"], TOTAL)
        self.assertEqual(state["warning"], "")
        # 200 + five chunks of 500 covers 2,509. A sixth would mean the cursor is
        # being walked back by de-duplication somewhere.
        self.assertEqual(state["pagesServed"], 5)

    def test_a_snapshot_reload_mid_flight_no_longer_strands_the_library(self):
        # #715 itself. The SPA resets `movies` to the first-paint page while a request
        # is in the air; the response that lands is for an array that no longer exists.
        state = self.replay("race-fixed")
        self.assertNotEqual(
            state["loaded"],
            STALLED_AT,
            "the library stalled at 200 + 500 again -- this is the reported bug",
        )
        self.assertEqual(state["loaded"], TOTAL)
        self.assertEqual(state["warning"], "")

    def test_recovering_from_the_reload_costs_pages_but_not_correctness(self):
        # The stale page is dropped and the cycle restarts, so the recovery is visible
        # as extra requests. Bounded, and bounded well below MAX_STALE_RESTARTS.
        state = self.replay("race-fixed")
        self.assertGreater(state["pagesServed"], 5)
        self.assertLessEqual(state["pagesServed"], 10)

    def test_the_offset_guard_is_what_does_the_work(self):
        # The negative control. Against a bridge that accepts any page regardless of
        # which array it was fetched against, the same run comes up short -- so the
        # test above is passing because of the guard and not around it.
        state = self.replay("race-unguarded")
        self.assertLess(state["loaded"], TOTAL)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
