"""Why `get_top_actors` and `get_top_directors` returned nothing at all.

Both MCP tools read one endpoint, `/api/next/stats/personal`, and both failed
the same way:

    HTTP 500  'NoneType' object has no attribute 'lower'
    ... and then, on the calls after it,
    HTTPConnectionPool(host='next-api', port=5000): Read timed out

Those are two separate faults that looked like one, which is why the endpoint
was reported as flaky rather than broken.

**The 500 is not about credits at all.** It is the first line of the handler:

    period = clean_text(request.args.get("period")).lower() or STATS_PERIOD_ALL

`clean_text` returns `None` for an argument that was not sent, so the default
was being applied *after* the case fold instead of before it. The statistics
page in the browser always sends `period` and `mediaType`, so it never saw
this; the MCP server sends neither, so it never got past this line. The bug
reads as intermittent because whether it fires depends entirely on the caller.

**The timeout is about cost.** The handler asks for exchange rates over the
network, and then captured a value snapshot that recomputed the entire
collection valuation and asked for the rates a second time. A failed rate
lookup was not cached, so a deployment that cannot reach the rates provider
paid the full lookup timeout twice per request - past the MCP client's
fifteen-second read timeout. That is covered by
`test_next_price_display_rate_cooldown.py`; what is pinned here is the
per-request folding, including that it stays linear in the number of credits.

The remaining tests cover the shapes a real `people` table holds: a credit
whose person has no name, one whose name is whitespace, and a collection with
no credits at all. None of them may raise, and a nameless director still
directed the film - so the count belongs under "Unknown", not nowhere.
"""

import os
import sys
import time
import unittest


backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_root not in sys.path:
    sys.path.insert(0, backend_root)

os.environ.setdefault("JWT_SECRET", "test-secret-for-stats-credit-tests")

from next_app import (  # noqa: E402
    STATS_MEDIA_ALL,
    STATS_MEDIA_TYPES,
    STATS_PERIOD_ALL,
    STATS_PERIOD_WINDOW_DAYS,
    STATS_TOP_CREDIT_LIMIT,
    stats_ordered_genres,
    stats_query_choice,
    stats_top_credit_entries,
)
from next_common import NextApiError  # noqa: E402


class StatsQueryChoiceTests(unittest.TestCase):
    """The 500 the two MCP tools hit, at its source."""

    def test_an_absent_argument_falls_back_instead_of_raising(self):
        # This is the reported failure verbatim: the MCP server calls
        # /api/next/stats/personal with no query string at all, so
        # request.args.get(...) is None for both arguments.
        self.assertEqual(
            stats_query_choice(None, STATS_PERIOD_WINDOW_DAYS, STATS_PERIOD_ALL, "period"),
            STATS_PERIOD_ALL,
        )
        self.assertEqual(
            stats_query_choice(None, STATS_MEDIA_TYPES, STATS_MEDIA_ALL, "mediaType"),
            STATS_MEDIA_ALL,
        )

    def test_an_empty_or_blank_argument_falls_back_too(self):
        # `?period=` and `?period=%20` reach Flask as "" and " ". Both are the
        # caller declining to choose, not a value to validate.
        for raw in ("", "   ", "\t"):
            with self.subTest(raw=repr(raw)):
                self.assertEqual(
                    stats_query_choice(raw, STATS_PERIOD_WINDOW_DAYS, STATS_PERIOD_ALL, "period"),
                    STATS_PERIOD_ALL,
                )

    def test_a_real_value_is_case_folded_and_trimmed(self):
        self.assertEqual(
            stats_query_choice("  YEAR ", STATS_PERIOD_WINDOW_DAYS, STATS_PERIOD_ALL, "period"),
            "year",
        )
        self.assertEqual(
            stats_query_choice("Movie", STATS_MEDIA_TYPES, STATS_MEDIA_ALL, "mediaType"),
            "movie",
        )

    def test_an_unknown_value_is_still_a_400_and_not_a_500(self):
        # Rejecting a typo is the point of the check; it must survive the fix.
        with self.assertRaises(NextApiError) as caught:
            stats_query_choice("decade", STATS_PERIOD_WINDOW_DAYS, STATS_PERIOD_ALL, "period")
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("period must be one of", str(caught.exception))


class StatsTopCreditEntryTests(unittest.TestCase):
    """What the two charts do with the rows the query actually returns."""

    def test_normal_results_are_ranked_by_count(self):
        entries = stats_top_credit_entries(
            [
                {"name": "Ridley Scott", "count": 4},
                {"name": "Denis Villeneuve", "count": 9},
                {"name": "Akira Kurosawa", "count": 6},
            ]
        )
        self.assertEqual(
            entries,
            [
                {"label": "Denis Villeneuve", "count": 9},
                {"label": "Akira Kurosawa", "count": 6},
                {"label": "Ridley Scott", "count": 4},
            ],
        )

    def test_a_credit_with_a_null_name_is_grouped_under_unknown(self):
        # A credit imported before its person was resolved has people.name NULL.
        # The film was still directed by someone, so dropping the row would make
        # the chart disagree with the collection it describes.
        entries = stats_top_credit_entries(
            [{"name": None, "count": 3}, {"name": "Chantal Akerman", "count": 2}]
        )
        self.assertEqual(
            entries,
            [{"label": "Unknown", "count": 3}, {"label": "Chantal Akerman", "count": 2}],
        )

    def test_null_and_blank_names_fold_into_one_unknown_bucket(self):
        # Two rows, one nameless and one whitespace-named, are one bucket - not
        # "Unknown" listed twice with the counts split between them.
        entries = stats_top_credit_entries(
            [{"name": None, "count": 2}, {"name": "   ", "count": 5}]
        )
        self.assertEqual(entries, [{"label": "Unknown", "count": 7}])

    def test_missing_credit_data_yields_an_empty_chart_not_a_crash(self):
        # No `movie_credits` table, or a collection nobody has enriched: the
        # endpoint passes through whatever the query returned, including None.
        for rows in (None, [], (), [{}], [None], ["not a row"]):
            with self.subTest(rows=rows):
                self.assertEqual(stats_top_credit_entries(rows), [])

    def test_an_empty_collection_yields_an_empty_chart(self):
        self.assertEqual(stats_top_credit_entries([]), [])

    def test_a_row_without_a_usable_count_is_skipped(self):
        # A count that is not a number is a broken row. Rendering it as a zero
        # would put a person on the chart with a total nobody can explain.
        entries = stats_top_credit_entries(
            [
                {"name": "Agnes Varda", "count": None},
                {"name": "Wong Kar-wai", "count": "many"},
                {"name": "Kelly Reichardt", "count": 0},
                {"name": "Claire Denis", "count": 2},
            ]
        )
        self.assertEqual(entries, [{"label": "Claire Denis", "count": 2}])

    def test_ties_are_broken_by_name_without_lowering_a_none(self):
        # The sort key is the other place `.lower()` was called on a value that
        # could be None. Ties make the key actually run on every entry.
        entries = stats_top_credit_entries(
            [
                {"name": "bong joon-ho", "count": 3},
                {"name": None, "count": 3},
                {"name": "Ava DuVernay", "count": 3},
            ]
        )
        self.assertEqual(
            [entry["label"] for entry in entries],
            ["Ava DuVernay", "bong joon-ho", "Unknown"],
        )

    def test_the_chart_is_capped_at_ten(self):
        entries = stats_top_credit_entries(
            [{"name": f"Director {index:03d}", "count": 100 - index} for index in range(50)]
        )
        self.assertEqual(len(entries), STATS_TOP_CREDIT_LIMIT)
        self.assertEqual(entries[0]["label"], "Director 000")

    def test_folding_stays_linear_so_the_request_cannot_time_out_here(self):
        # Not a benchmark - a shape check. Folding is one pass plus a sort, so a
        # large collection costs milliseconds. A quadratic implementation (a
        # scan of the accumulated list per row, say) would blow far past this
        # budget and put the endpoint back over the MCP client's 15s read
        # timeout, which is the second half of the reported bug.
        rows = [{"name": f"Person {index % 5000}", "count": 1} for index in range(200_000)]
        started = time.monotonic()
        entries = stats_top_credit_entries(rows)
        elapsed = time.monotonic() - started
        self.assertEqual(len(entries), STATS_TOP_CREDIT_LIMIT)
        self.assertLess(elapsed, 5.0, f"folding {len(rows)} credits took {elapsed:.2f}s")


class StatsGenreOrderTests(unittest.TestCase):
    """The other `.lower()` on this endpoint, and the data it was losing.

    Auditing the crash turned up a second fault in the same expression. The
    genre chart appended an "Unknown" bucket, re-sorted `by_genre[:-1]` to strip
    it, and appended it again - which is correct only when that bucket exists.
    A collection where every film has a genre has no such bucket, so the slice
    removed a real genre instead, every time, silently.
    """

    def test_no_genre_is_lost_when_there_is_nothing_unknown(self):
        entries = [
            {"label": "Drama", "count": 12},
            {"label": "Comedy", "count": 8},
            {"label": "Western", "count": 3},
        ]
        ordered = stats_ordered_genres(entries, 0)
        self.assertEqual([entry["label"] for entry in ordered], ["Comedy", "Drama", "Western"])

    def test_unknown_is_pinned_last_rather_than_sorted_in(self):
        entries = [{"label": "Drama", "count": 12}, {"label": "Western", "count": 3}]
        ordered = stats_ordered_genres(entries, 5)
        self.assertEqual([entry["label"] for entry in ordered], ["Drama", "Western", "Unknown"])
        self.assertEqual(ordered[-1]["count"], 5)

    def test_an_empty_collection_orders_to_nothing(self):
        self.assertEqual(stats_ordered_genres([], 0), [])
        self.assertEqual(stats_ordered_genres(None, 0), [])

    def test_a_null_label_does_not_raise_on_the_sort_key(self):
        ordered = stats_ordered_genres([{"label": None, "count": 2}, {"label": "Anime", "count": 1}], 0)
        self.assertEqual([entry["label"] for entry in ordered], [None, "Anime"])


if __name__ == "__main__":
    unittest.main()
