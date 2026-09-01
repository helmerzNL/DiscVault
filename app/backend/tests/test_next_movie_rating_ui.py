"""The personal rating in the SPA, read as source text.

Three of these guard mistakes that produce no error at all.

**The list column key is `personalRating`, not `rating`.** `rating` is already
the age-certificate column, in the sort whitelist, the value extractor and the
table head alike. Reusing it silently repurposes the content-rating column
instead of adding one.

**The sort key is allowed in the wide set only.** The column is desktop-only, and
a key allowed in the compact set renders a header whose click resets the sort
back to title with nothing failing.

**The tile badge preference must be registered twice.** `APP_PREFERENCE_DEFAULTS`
alone makes it a string preference, and the string "false" is truthy -- so
turning the badge off would leave it on.
"""

import os
import sys
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEXT_VIEWS_UI_PATH = os.path.join(BACKEND_DIR, "next_views_ui.py")

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import next_export_columns  # noqa: E402
import next_preferences  # noqa: E402


def _source() -> str:
    with open(NEXT_VIEWS_UI_PATH, "r", encoding="utf-8") as handle:
        return handle.read()


def _without_comments(block: str) -> str:
    """Drop `//` comment lines.

    Assertions below say what the code must NOT do, and the code carries
    comments saying the same thing in prose. Without this, a comment explaining
    why owner_id is not read would fail an assertion that owner_id is not read.
    """
    return "\n".join(
        line for line in block.splitlines() if not line.strip().startswith("//")
    )


class ColumnKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_the_sort_whitelist_keeps_both_keys_apart(self):
        start = self.source.index("function normalizeLibraryDetailSort")
        block = self.source[start : start + 900]
        self.assertIn('"rating", "personalRating"', block)

    def test_the_personal_rating_sort_is_desktop_only(self):
        start = self.source.index("function normalizeLibraryDetailSort")
        block = self.source[start : start + 900]
        compact = _without_comments(
            block[block.index("compact") : block.index(': new Set(["title", "director"')]
        )
        self.assertNotIn("personalRating", compact)

    def test_the_content_rating_column_still_renders_the_certificate(self):
        # If this ever renders a score, the key collision happened.
        self.assertIn(
            '<td class="library-list-rating-column library-list-desktop-column">'
            "${libraryListValueLinesHtml(itemRatingValues(item))}</td>",
            self.source,
        )

    def test_the_personal_rating_column_has_its_own_header_and_cell(self):
        self.assertIn('libraryListSortHeaderHtml("personalRating"', self.source)
        self.assertIn(
            '<td class="library-list-personal-rating-column library-list-desktop-column">'
            "${libraryListPersonalRatingHtml(item)}</td>",
            self.source,
        )


class SortingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_sorting_is_numeric_rather_than_a_string_compare(self):
        start = self.source.index("function sortLibraryListItems")
        block = self.source[start : start + 1200]
        self.assertIn('if (state.key === "personalRating")', block)
        self.assertIn("comparePersonalRating(a, b, state.direction)", block)

    def test_unrated_sorts_last_in_both_directions(self):
        # The direction is applied inside the comparator precisely so the null
        # branch is not flipped with it. Somebody sorting by rating wants their
        # best films or their worst, never 1,800 blanks.
        start = self.source.index("function comparePersonalRating")
        block = self.source[start : start + 700]
        self.assertIn("if (left === null && right === null) return 0;", block)
        self.assertIn("if (left === null) return 1;", block)
        self.assertIn("if (right === null) return -1;", block)
        self.assertIn('return direction === "desc" ? -diff : diff;', block)
        # The nulls must be decided before the direction is applied.
        self.assertLess(block.index("if (left === null) return 1;"), block.index('direction === "desc"'))


class TwoScoresStayDistinctTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_the_external_score_names_itself_as_a_score(self):
        start = self.source.index("function movieScoreLabel")
        block = self.source[start : start + 500]
        self.assertIn('tNext("movieDetail.externalScore", "Score")', block)

    def test_the_personal_pill_is_a_button_and_the_external_one_is_not(self):
        # The load-bearing distinction: one you can press, one you cannot. That
        # reads correctly with no colour and in every locale.
        start = self.source.index("function ratingStarHtml")
        block = self.source[start : start + 1400]
        self.assertIn("data-set-rating=", block)
        self.assertIn("<button type=", block)
        external = self.source[self.source.index("function movieScoreLabel") :][:500]
        self.assertNotIn("<button", external)

    def test_only_one_number_reaches_a_tile(self):
        start = self.source.index("function posterRatingBadgeHtml")
        block = self.source[start : start + 800]
        self.assertIn("movie?.personal_rating ?? movie?.owner_rating", block)
        # The external score must not join it: two numbers on a tile is the
        # ambiguity the detail page works to avoid.
        self.assertNotIn("movie?.rating", block)
        self.assertNotIn("movieScoreLabel", block)


class PreferenceRegistrationTests(unittest.TestCase):
    def test_the_tile_badge_preference_is_registered_in_both_sets(self):
        self.assertIn("show_rating_badge_on_tiles", next_preferences.APP_PREFERENCE_DEFAULTS)
        self.assertIn("show_rating_badge_on_tiles", next_preferences.APP_BOOLEAN_PREFERENCES)

    def test_turning_it_off_actually_reads_as_off(self):
        # The failure the second registration prevents: a string preference for
        # which "false" is truthy, so the badge stays on.
        self.assertIs(
            next_preferences.validate_app_preference("show_rating_badge_on_tiles", False), False
        )
        self.assertIs(
            next_preferences.validate_app_preference("show_rating_badge_on_tiles", "false"), False
        )


class OwnerAttributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_the_owner_flag_comes_from_the_server_not_from_an_id_comparison(self):
        # A client comparing owner_id to its own id reads a NULL owner_id as
        # "I am the owner" -- the bug shape renderMovieLoan carries a comment
        # about. renderMovieRating must only read what the server decided.
        start = self.source.index("function renderMovieRating")
        block = self.source[start : start + 1800]
        self.assertIn("state?.ownerRating", block)
        self.assertIn("state?.ownerRatingBy", block)
        code = _without_comments(block)
        self.assertNotIn("owner_id", code)
        self.assertNotIn("currentUserId()", code)

    def test_a_borrowed_score_is_labelled_with_whose_it_is(self):
        start = self.source.index("function libraryListPersonalRatingHtml")
        block = self.source[start : start + 1200]
        self.assertIn("owner_rating_by", block)
        self.assertIn('tNext("lists.ownerRating"', block)


class FilterAndExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_rated_and_unrated_join_the_personal_filter(self):
        start = self.source.index("function normalizeAdvancedSearch")
        block = self.source[start : start + 2200]
        self.assertIn('"tagged", "rated", "unrated"', block)

    def test_the_rated_filter_asks_about_the_viewers_own_score(self):
        # "films I have rated" must not be answered with films somebody else
        # rated, which owner_rating would do.
        start = self.source.index("function movieMatchesAdvancedSearch")
        block = self.source[start : start + 4000]
        self.assertIn('filters.personal === "rated" && !movie?.personal_rating', block)
        self.assertIn('filters.personal === "unrated" && movie?.personal_rating', block)

    def test_the_export_column_ships_default_off(self):
        by_key = {column["key"]: column for column in next_export_columns.EXPORT_COLUMNS}
        self.assertFalse(by_key["personalRating"]["default"])


if __name__ == "__main__":
    unittest.main()
