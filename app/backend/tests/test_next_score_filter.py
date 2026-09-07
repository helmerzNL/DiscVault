"""Filtering the Library on the external score (TMDB's vote average).

Requested on #719: "A good filter would be on the TMDB ratings. This would be
great to search for movies to watch that universally has a good rating."

Two kinds of claim live here, and they fail differently.

The **behavioural** half runs the shipped code. The three helpers and the block
inside `movieMatchesAdvancedSearch` are lifted verbatim out of
``next_views_ui.py`` and executed under node, so what is asserted is the filter
itself rather than a second implementation of it that can drift. The claim that
earns this the most is the one about a stored **zero**: TMDB writes
``vote_average = 0.0`` for a film nobody has voted on, and DiscVault stores that
string like any other. Read as a number it is a perfectly good score below every
threshold -- so "Score to 5", the search for the duds, would return every
unscored film in the library first. Nothing about that reads as a bug; it reads
as a library full of bad films.

The **wiring** half is read as source text, the way the filters next to it are
asserted (see test_next_origin_ui.py). A filter needs seven separate edits --
markup, defaults, normalize, count, read, sync, predicate -- and skipping any one
of them is silent: the control renders, accepts a number, and filters nothing.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEXT_VIEWS_UI_PATH = os.path.join(BACKEND_DIR, "next_views_ui.py")
I18N_DIR = os.path.abspath(os.path.join(BACKEND_DIR, "..", "frontend", "i18n", "next"))

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

NODE = shutil.which("node")

SCORE_KEYS = (
    "collection.scoreFilter",
    "collection.scoreFrom",
    "collection.scoreTo",
    "collection.scoreFilterHelp",
    "collection.scoreDataMissing",
)


def _source() -> str:
    with open(NEXT_VIEWS_UI_PATH, "r", encoding="utf-8") as handle:
        return handle.read()


def _function_source(source: str, name: str) -> str:
    """The whole of ``function name(...) { ... }``, by brace matching."""
    start = source.index("function %s(" % name)
    depth = 0
    index = source.index("{", start)
    for position in range(index, len(source)):
        char = source[position]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : position + 1]
    raise AssertionError("unbalanced braces reading %s" % name)


def _extract_score_predicate(source: str) -> str:
    """The score block out of ``movieMatchesAdvancedSearch``, wrapped as a function.

    Only the wrapper is written here; every ``return false`` inside it is the
    shipped text. A rewrite of the block that changes what it decides changes
    what these tests decide too.
    """
    start = source.index("      const scoreFrom = scoreBoundNumber(filters.scoreFrom);")
    end = source.index("\n      }\n", start) + len("\n      }\n")
    block = source[start:end]
    assert "return false;" in block, block
    return "function movieMatchesScore(movie, filters) {\n%s      return true;\n}\n" % block


HARNESS_DRIVER = """
const cases = JSON.parse(process.argv[2]);
const has = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
const out = cases.map((item) => {
  if (item.fn === "normalizeScoreBound") return normalizeScoreBound(has(item, "value") ? item.value : undefined);
  if (item.fn === "scoreBoundNumber") return scoreBoundNumber(has(item, "value") ? item.value : undefined);
  if (item.fn === "movieScoreNumber") return movieScoreNumber(item.movie);
  if (item.fn === "match") return movieMatchesScore(item.movie, item.filters);
  throw new Error("unknown case: " + item.fn);
});
process.stdout.write(JSON.stringify(out));
"""


def _harness_source() -> str:
    source = _source()
    return "\n".join(
        [
            _function_source(source, "normalizeScoreBound"),
            _function_source(source, "scoreBoundNumber"),
            _function_source(source, "movieScoreNumber"),
            _extract_score_predicate(source),
            HARNESS_DRIVER,
        ]
    )


@unittest.skipUnless(NODE, "node is not available")
class ScoreFilterBehaviourTests(unittest.TestCase):
    """The shipped helpers and predicate, executed."""

    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.mkdtemp(prefix="dv-score-filter-")
        cls.harness = os.path.join(cls.tempdir, "score-filter-harness.mjs")
        with open(cls.harness, "w", encoding="utf-8") as handle:
            handle.write(_harness_source())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tempdir, ignore_errors=True)

    def run_cases(self, cases):
        result = subprocess.run(
            [NODE, self.harness, json.dumps(cases)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def normalize(self, *values):
        return self.run_cases([{"fn": "normalizeScoreBound", "value": v} for v in values])

    def score_of(self, *movies):
        return self.run_cases([{"fn": "movieScoreNumber", "movie": m} for m in movies])

    def matches(self, filters, *movies):
        return self.run_cases(
            [{"fn": "match", "movie": m, "filters": filters} for m in movies]
        )

    # --- the stored value ------------------------------------------------

    def test_a_stored_zero_is_no_score_rather_than_a_score_of_zero(self):
        # Half of the contract: "0" comes back as the same 0 an absent score
        # does. The other half -- that the predicate then drops it rather than
        # ranking it below every threshold -- is
        # test_an_unscored_film_is_out_of_every_bounded_search. Together they are
        # what keeps "Score to 5", the search for the ones to skip, from opening
        # with every film nobody has voted on.
        self.assertEqual(self.score_of({"rating": "0"}, {"rating": "0.0"}), [0, 0])

    def test_an_absent_score_reads_as_zero_in_every_shape(self):
        self.assertEqual(
            self.score_of({}, {"rating": ""}, {"rating": None}, {"rating": "  "}),
            [0, 0, 0, 0],
        )

    def test_a_score_is_read_from_the_column_and_from_older_metadata(self):
        # The library page carries `rating` as a column; a payload built before
        # that carries it inside `metadata`, which is the chain movieScoreLabel
        # already follows on the detail page.
        self.assertEqual(
            self.score_of({"rating": "7.4"}, {"metadata": {"rating": "6.1"}}),
            [7.4, 6.1],
        )

    def test_a_non_numeric_score_is_not_a_score(self):
        self.assertEqual(self.score_of({"rating": "n/a"}), [0])

    # --- the bound -------------------------------------------------------

    def test_a_bound_is_canonicalised_so_one_filter_is_one_string(self):
        # "7" and "7.0" must be the same saved smart filter, and a comma decimal
        # is what a European keyboard produces.
        self.assertEqual(
            self.normalize("7", "7.0", "7,4", " 6.5 "), ["7", "7", "7.4", "6.5"]
        )

    def test_an_empty_or_unparseable_bound_is_no_bound(self):
        self.assertEqual(self.normalize("", "   ", "abc", None), ["", "", "", ""])

    def test_a_bound_outside_the_scale_is_clamped_not_dropped(self):
        # A number input accepts more than its own max when typed. Dropping "12"
        # would leave the filter reading "any" while the box still shows 12.
        self.assertEqual(self.normalize("12", "-3", "10.4"), ["10", "0", "10"])

    def test_an_unset_bound_reads_as_null(self):
        self.assertEqual(
            self.run_cases(
                [
                    {"fn": "scoreBoundNumber", "value": ""},
                    {"fn": "scoreBoundNumber"},
                    {"fn": "scoreBoundNumber", "value": "0"},
                    {"fn": "scoreBoundNumber", "value": "7,4"},
                ]
            ),
            [None, None, 0, 7.4],
        )

    # --- the predicate ---------------------------------------------------

    def test_without_a_bound_every_film_passes_including_unscored_ones(self):
        self.assertEqual(
            self.matches({"scoreFrom": "", "scoreTo": ""}, {"rating": "7.4"}, {}),
            [True, True],
        )

    def test_a_lower_bound_keeps_the_good_ones_and_is_inclusive(self):
        self.assertEqual(
            self.matches(
                {"scoreFrom": "7", "scoreTo": ""},
                {"rating": "7.4"},
                {"rating": "7"},
                {"rating": "6.9"},
            ),
            [True, True, False],
        )

    def test_an_upper_bound_is_inclusive_too(self):
        self.assertEqual(
            self.matches(
                {"scoreFrom": "", "scoreTo": "5"},
                {"rating": "4.2"},
                {"rating": "5"},
                {"rating": "5.1"},
            ),
            [True, True, False],
        )

    def test_both_bounds_together_are_a_range(self):
        self.assertEqual(
            self.matches(
                {"scoreFrom": "6", "scoreTo": "8"},
                {"rating": "7.4"},
                {"rating": "5.9"},
                {"rating": "8.1"},
            ),
            [True, False, False],
        )

    def test_an_unscored_film_is_out_of_every_bounded_search(self):
        # Including the upper-bound one: "no score" is not "a low score", and a
        # library where nothing has been fetched yet must not read as a library
        # of bad films.
        self.assertEqual(
            self.matches({"scoreFrom": "", "scoreTo": "5"}, {}, {"rating": "0"}),
            [False, False],
        )

    def test_a_bound_of_zero_means_has_a_score_at_all(self):
        self.assertEqual(
            self.matches({"scoreFrom": "0", "scoreTo": ""}, {"rating": "1.2"}, {}),
            [True, False],
        )

    def test_a_filter_saved_before_this_feature_existed_bounds_nothing(self):
        # Smart filters live in each browser's localStorage and can predate any
        # field. A missing key must read as "no bound"; read as NaN it would
        # compare false against everything and quietly require a score.
        self.assertEqual(self.matches({}, {"rating": "7.4"}, {}), [True, True])


class ScoreFilterWiringTests(unittest.TestCase):
    """The seven edits, each silent on its own."""

    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_the_panel_carries_its_own_group_and_both_inputs(self):
        self.assertIn('data-library-advanced-group="score"', self.source)
        self.assertIn('id="advancedScoreFrom"', self.source)
        self.assertIn('id="advancedScoreTo"', self.source)

    def test_the_inputs_are_decimal_on_the_scale_the_score_uses(self):
        block = self.source[self.source.index('data-library-advanced-group="score"') :][:1600]
        self.assertIn('type="number" min="0" max="10" step="0.1"', block)
        self.assertIn('inputmode="decimal"', block)

    def test_the_defaults_carry_both_keys(self):
        block = self.source[self.source.index("function advancedSearchDefaults") :][:900]
        self.assertIn('scoreFrom: ""', block)
        self.assertIn('scoreTo: ""', block)

    def test_a_stored_filter_is_normalized_through_the_bound_helper(self):
        block = self.source[self.source.index("function normalizeAdvancedSearch") :][:1200]
        self.assertIn("scoreFrom: normalizeScoreBound(source.scoreFrom)", block)
        self.assertIn("scoreTo: normalizeScoreBound(source.scoreTo)", block)

    def test_both_bounds_count_towards_the_badge(self):
        block = self.source[self.source.index("function advancedSearchActiveCount") :][:1400]
        # Against "" and not for truthiness: a bound of "0" is a real filter.
        self.assertIn('normalized.scoreFrom !== ""', block)
        self.assertIn('normalized.scoreTo !== ""', block)

    def test_the_controls_are_read_back(self):
        block = self.source[self.source.index("function readAdvancedSearchControls") :][:1200]
        self.assertIn("advancedScoreFrom", block)
        self.assertIn("advancedScoreTo", block)

    def test_the_controls_are_written_back(self):
        block = self.source[self.source.index("function syncAdvancedSearchControls") :][:3000]
        self.assertIn('setAdvancedControlValue("advancedScoreFrom"', block)
        self.assertIn('setAdvancedControlValue("advancedScoreTo"', block)

    def test_the_predicate_is_in_the_advanced_search_chain(self):
        block = self.source[self.source.index("function movieMatchesAdvancedSearch") :][:4000]
        self.assertIn("scoreBoundNumber(filters.scoreFrom)", block)
        self.assertIn("movieScoreNumber(movie)", block)

    def test_a_container_delegates_the_score_to_its_members(self):
        # A box set carries no score of its own, so without this a box set of
        # well-reviewed films vanishes from a score search.
        block = self.source[self.source.index("function containerMatchesAdvancedSearch") :][:2500]
        self.assertIn("scoreBoundNumber(filters.scoreFrom) !== null", block)
        self.assertIn("scoreBoundNumber(filters.scoreTo) !== null", block)

    def test_the_panel_says_how_many_films_have_no_score(self):
        # "Score from 7" over a library nothing has scored returns nothing, and
        # an empty result reads as a broken filter rather than as missing data.
        self.assertIn('id="advancedScoreHint"', self.source)
        block = self.source[self.source.index("function scoreDataMissingCount") :][:400]
        self.assertIn("movieScoreNumber(movie)", block)


class ScoreFilterCopyTests(unittest.TestCase):
    """The strings, in every locale."""

    @classmethod
    def setUpClass(cls):
        cls.source = _source()
        with open(os.path.join(I18N_DIR, "en-US.json"), encoding="utf-8") as handle:
            cls.en = json.load(handle)

    def _catalogs(self):
        for name in sorted(os.listdir(I18N_DIR)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(I18N_DIR, name), encoding="utf-8") as handle:
                yield name, json.load(handle)

    def test_every_locale_carries_every_score_key(self):
        problems = [
            "%s: %s" % (name, key)
            for name, catalog in self._catalogs()
            for key in SCORE_KEYS
            if not str(catalog.get(key, "")).strip()
        ]
        self.assertEqual(problems, [], "Missing score filter copy:\n" + "\n".join(problems))

    def test_the_count_placeholder_survives_translation(self):
        problems = [
            name
            for name, catalog in self._catalogs()
            if "{count}" not in catalog.get("collection.scoreDataMissing", "")
        ]
        self.assertEqual(problems, [], "collection.scoreDataMissing lost {count} in: %s" % problems)

    def test_the_inline_fallback_matches_the_source_catalog(self):
        # tNext renders the fallback whenever the catalog has not loaded, so a
        # stale one shows old wording on a slow connection only.
        self.assertIn(
            'tNext("collection.scoreDataMissing", "%s")' % self.en["collection.scoreDataMissing"],
            self.source,
        )

    def test_the_markup_defaults_match_the_source_catalog(self):
        block = self.source[self.source.index('data-library-advanced-group="score"') :][:1600]
        for key in ("collection.scoreFilter", "collection.scoreFrom", "collection.scoreTo"):
            with self.subTest(key=key):
                self.assertIn('data-next-i18n="%s">%s<' % (key, self.en[key]), block)

    def test_the_label_matches_the_word_the_detail_page_uses(self):
        # "Score" on the detail page and "Rating" in the filter would read as two
        # different numbers. There are three on a film -- the external score, the
        # personal one, and the age rating -- so the wording is what separates them.
        self.assertEqual(self.en["collection.scoreFilter"], self.en["movieDetail.externalScore"])


if __name__ == "__main__":
    unittest.main()
