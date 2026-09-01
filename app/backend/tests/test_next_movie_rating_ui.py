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


class StarGeometryTests(unittest.TestCase):
    """The row has to *look* like stars, which is the one thing nothing checked.

    The picker shipped drawing each star as two 13px buttons, each clipped to
    half its own box with `clip-path`, rejoined with a negative margin. None of
    that produces a half star: a 50% inset on a 13px box exposes a middle band
    of a glyph that spans -4.5px to 17.5px, the picker's flex `gap` landed
    between the two halves and cancelled the negative margin, and `flex-wrap`
    could break the line between them. Every existing test stayed green while
    the row rendered as slivers, because a source-text assertion is the only
    thing here that can see geometry at all.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = _source()
        start = cls.source.index("function ratingStarHtml")
        cls.star = cls.source[start : cls.source.index("function movieRatingPickerHtml")]

    def test_the_drawing_is_never_clipped(self):
        # The regression itself. The fill state is drawn; only the hit target is
        # divided, and it is divided by layout, not by cutting up the glyph.
        self.assertNotIn("clip-path", self.star)
        self.assertNotIn("margin-right:-13px", self.star)
        self.assertNotIn("margin-left:-13px", self.star)

    def test_one_star_is_one_svg_and_one_flex_item(self):
        self.assertEqual(self.star.count("<svg"), 1)
        self.assertIn('<span class="${cls}" data-rating-star=', self.star)

    def test_both_half_steps_stay_reachable(self):
        self.assertEqual(self.star.count("<button type="), 2)
        self.assertIn('data-set-rating="${index - 0.5}"', self.star)
        self.assertIn('data-set-rating="${index}"', self.star)

    def test_the_hit_targets_are_overlays_over_the_star(self):
        start = self.source.index(".movie-rating-hit {")
        block = self.source[start : start + 300]
        self.assertIn("position: absolute;", block)
        self.assertIn("width: 50%;", block)

    def test_the_star_row_cannot_wrap_through_a_star(self):
        # Twenty flex items in the picker let a narrow viewport put a line break
        # between a star's two halves. Ten stars in a nowrap row cannot.
        start = self.source.index(".movie-rating-stars {")
        block = self.source[start : start + 300]
        self.assertIn("flex-wrap: nowrap;", block)

    def test_a_label_says_what_pressing_the_star_does(self):
        # "7.5" alone announced a number with no verb and no scale.
        self.assertIn('tNext("lists.ratingStarLabel", "Rate {score} out of 10")', self.source)


class DragToRateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_every_pointer_gesture_commits_on_pointerup(self):
        # A tap has to commit here too, not through the click handler. While the
        # picker holds the pointer capture the browser retargets `click` to the
        # picker instead of the button under the finger, so `closest(
        # "[data-set-rating]")` finds nothing and a tap writes no score at all.
        start = self.source.index('movieRatingPickerNode?.addEventListener("pointerup"')
        block = _without_comments(self.source[start : start + 900])
        self.assertIn("setActiveMovieRating(score)", block)
        self.assertNotIn("dragged", block)

    def test_the_click_handler_serves_the_keyboard_only(self):
        # Enter and Space report detail 0; a pointer click reports 1 or more and
        # was already spent on pointerup. Without the guard one mouse press
        # writes the score twice.
        start = self.source.index('movieRatingPickerNode?.addEventListener("click"')
        block = self.source[start : start + 400]
        self.assertIn("if (event.detail !== 0) return;", block)
        self.assertIn("setActiveMovieRating(star.dataset.setRating)", block)

    def test_a_preview_never_rebuilds_the_row_it_is_dragging_on(self):
        # innerHTML here would destroy the node holding the pointer capture, and
        # the drag would stop dead halfway along the row.
        start = self.source.index("function applyMovieRatingPreview")
        block = _without_comments(
            self.source[start : self.source.index("function movieRatingScoreFromPointer")]
        )
        self.assertNotIn("innerHTML", block)
        self.assertIn('classList.toggle("filled"', block)

    def test_capture_is_taken_on_the_static_node(self):
        # #movieRatingPicker is markup; its children are replaced on every
        # render. Capture on a child would not survive the first preview.
        start = self.source.index('movieRatingPickerNode?.addEventListener("pointerdown"')
        block = self.source[start : start + 900]
        self.assertIn("movieRatingPickerNode.setPointerCapture(event.pointerId)", block)


if __name__ == "__main__":
    unittest.main()
