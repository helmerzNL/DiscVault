"""The admin UI shows box-set merge groups and the box-set/release barcode check.

Before this, the Scan & merge report already carried ``container_groups`` and
the execute step already merged them, but the wizard rendered only movie
groups -- box-sets were merged without ever being shown -- and the "done" line
printed the ``[movies, containers]`` pair as text. These run the shipped
functions out of ``next_views_ui.py`` in Node against a minimal DOM stub.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

try:
    from .test_next_score_filter import _function_source
except ImportError:  # pragma: no cover - direct module execution
    from test_next_score_filter import _function_source


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "backend/next_views_ui.py"

PRELUDE = r"""
const assert = require('node:assert/strict');
const nodes = {};
const document = { getElementById: (id) => (nodes[id] = nodes[id] || { innerHTML: '', textContent: '', disabled: false }) };
function escapeHtml(value) { return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
function tNext(_key, fallback) { return fallback; }
function formatNumber(value) { return String(value ?? 0); }
let _dedupExecuteEnabled = true;
let _dedupReport = null;
let _dedupReportId = null;
"""


@unittest.skipUnless(shutil.which("node"), "node is unavailable")
class BoxSetBarcodeUiTests(unittest.TestCase):
    def run_js(self, body):
        source = SOURCE.read_text(encoding="utf-8")
        names = [
            "renderAppAdminDedupWizard",
            "appAdminOperationsRow",
            "appAdminBoxSetReleaseKindLabel",
            "appAdminBoxSetReleaseOverlapRow",
        ]
        script = PRELUDE + "\n".join(_function_source(source, name) for name in names)
        result = subprocess.run(
            [shutil.which("node"), "-e", script + "\n" + body],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return result.stdout

    def test_wizard_renders_box_set_groups_even_without_movie_groups(self):
        report = {
            "groups": [],
            "container_groups": [
                {
                    "tier": "barcode",
                    "match": "approximate",
                    "winner": "c1",
                    "winner_reason": "highest user-data score won",
                    "losers": ["c2"],
                    "members": [
                        {"id": "c1", "title": "Saga Box", "barcode": "012345678905", "movie_count": 3},
                        {"id": "c2", "title": "Saga Box (rescan)", "barcode": "0012345678905", "movie_count": 1},
                    ],
                }
            ],
        }
        self.run_js(
            "renderAppAdminDedupWizard('results', %s);\n" % json.dumps(report)
            + r"""
const html = nodes.appAdminDedupWizardArea.innerHTML;
assert.ok(html.includes('appAdminDedupBoxSetGroups'), html);
assert.ok(html.includes('Saga Box (rescan)'));
assert.ok(html.includes('3 movie(s)'));
assert.ok(html.includes('Approximately the same barcode'));
assert.ok(html.includes('Duplicate box-set groups found: 1'));
assert.ok(html.includes('appAdminDedupExecuteBtn'));
assert.ok(!html.includes('No duplicates found'));
"""
        )

    def test_empty_report_is_still_clean(self):
        self.run_js(
            r"""
renderAppAdminDedupWizard('results', { groups: [], container_groups: [] });
assert.ok(nodes.appAdminDedupWizardArea.innerHTML.includes('No duplicates found'));
"""
        )

    def test_done_message_counts_movies_and_box_sets(self):
        self.run_js(
            r"""
renderAppAdminDedupWizard('done', { tombstoned: 3, moviesTombstoned: 2, containersTombstoned: 1 });
const html = nodes.appAdminDedupWizardArea.innerHTML;
assert.ok(html.includes('Done! 3 duplicate(s) removed.'), html);
assert.ok(html.includes('2 movie(s), 1 box-set(s).'), html);
assert.ok(!html.includes('2,1'));
"""
        )

    def test_release_overlap_row_names_the_failure_mode(self):
        self.run_js(
            r"""
const row = appAdminBoxSetReleaseOverlapRow({
  containerId: 'c1', title: 'Trilogy', barcode: '012345678905', match: 'approximate',
  releases: [
    { movieId: 'm1', title: 'Part One', year: '2001', barcode: '0012345678905', match: 'approximate', kind: 'member_carries_box_barcode' },
    { movieId: 'm2', title: 'Trilogy', barcode: '012345678905', match: 'exact', kind: 'box_set_imported_as_release' }
  ]
});
assert.ok(row.includes('Trilogy'));
assert.ok(row.includes('member film carries the box-set barcode'));
assert.ok(row.includes('box-set also added as a separate release'));
assert.ok(row.includes('Barcode ≈ release'));
"""
        )


if __name__ == "__main__":
    unittest.main()
