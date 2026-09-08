"""Execute the shipped list sorting and export extraction under Node."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path
try:
    from .test_next_score_filter import _function_source
except ImportError:
    from test_next_score_filter import _function_source

SOURCE = Path(__file__).resolve().parents[1] / "next_views_ui.py"

@unittest.skipUnless(shutil.which("node"), "node is unavailable")
class LibraryRatingColumnsTests(unittest.TestCase):
    def run_js(self, body):
        source = SOURCE.read_text(encoding="utf-8")
        names = ["normalizeLibraryDetailSort", "sortLibraryListItems", "comparePersonalRating",
                 "itemPersonalRatingValue", "itemExternalScoreValue", "compareExternalScore",
                 "movieScoreNumber", "formatRatingScore", "libraryExportRow"]
        script = "\n".join(_function_source(source, name) for name in names)
        script += r"""
const localeState = {locale: "en-US"};
let compact = false;
const libraryListCompactMode = () => compact;
const customFieldDefinitions = () => [];
const itemSortTitleValue = item => item.movie?.title || item.title || "";
const itemTitleValue = itemSortTitleValue;
const itemYearLabel = () => "";
const itemFormatValues = () => [];
const itemDirectorCredits = () => [];
const itemActorCredits = () => [];
const creditText = () => "";
const itemStudioValues = () => [];
const itemRatingValues = () => [];
const itemTagValues = () => [];
const libraryExportWatchActivityText = () => "";
const movieOriginCountryValues = () => [];
const movieOriginLanguageValue = () => "any";
const regionLabel = x => x;
const customExportCells = () => ({});
"""
        result = subprocess.run([shutil.which("node"), "-e", script + body], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_both_scores_sort_numerically_in_both_layouts_with_blanks_last(self):
        result = self.run_js(r"""
const rows = [
 {kind:"movie", movie:{title:"Blank"}},
 {kind:"movie", movie:{title:"Ten", rating:"10", personal_rating:10}},
 {kind:"movie", movie:{title:"Nine", metadata:{rating:"9,5"}, personal_rating:9.5}},
 {kind:"movie", movie:{title:"Zero", rating:"0", personal_rating:0}},
 {kind:"container", container:{title:"Group", movies:[{rating:"10"}]}}
];
const results = [];
for (compact of [false, true]) for (const key of ["externalScore", "personalRating"]) {
 for (const direction of ["asc", "desc"]) {
  results.push(sortLibraryListItems(rows, {key, direction}).map(itemSortTitleValue));
 }
}
process.stdout.write(JSON.stringify(results));
""")
        for index, row in enumerate(result):
            self.assertEqual(row[:2], ["Nine", "Ten"] if index % 2 == 0 else ["Ten", "Nine"])
            self.assertEqual(set(row[2:]), {"", "Blank", "Zero"})

    def test_export_keeps_scores_separate_and_missing_cells_empty(self):
        result = self.run_js(r"""
process.stdout.write(JSON.stringify([
 {rating:"8.25", personal_rating:7.5},
 {metadata:{rating:"9,5"}, owner_rating:8},
 {}, {rating:"0"}, {rating:"invalid"}
].map(movie => { const row = libraryExportRow(movie); return [row.externalScore, row.personalRating]; })));
""")
        self.assertEqual(result, [["8.25", "7.5"], ["9.5", "8"], ["", ""], ["", ""], ["", ""]])
