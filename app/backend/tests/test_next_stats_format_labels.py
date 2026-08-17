"""One spelling per physical format, everywhere it is shown.

`movies.format` is an unconstrained free-text column. Providers and sync
clients write raw codes into it (`4K_UHD`, `BLURAY`), a person can type their
own, and nothing has ever constrained it - so one shelf holds several spellings
of one format. Everything that shows a format to a person therefore routes
through a single spelling function, which is what
`test_next_movie_detail_ui.test_movie_format_is_never_displayed_raw` already
pins for the detail screens.

The statistics endpoint did not. It carried its own `format_label()` with a
hand-written replacement table, which produced a *third* spelling -
"4K UHD + Blu-Ray", with a capital R - while the rest of the app produced
"4K UHD + Blu-ray". The chart listed both, as two formats, from one shelf.

Two things are pinned here, because fixing the spelling alone would not have
fixed the chart:

1. the shared function is the only normaliser, and the endpoint uses it;
2. the counts are folded **after** normalising. Grouping in SQL happens on the
   raw column, so several spellings arrive as several rows; if they are not
   summed afterwards the chart still shows one format more than once, now with
   the same label twice - which looks even more like a bug.

The ambiguous case is deliberate and is asserted as such. A combo pack is
recognised only when the value marks it: an explicit `+`, `/`, `&`, or the
*underscore* the clients' own canonical code carries (`4K_UHD_BLURAY`). A bare
space is not such a mark, because "4K Blu-ray" / "Ultra HD Blu-ray" is the
ordinary name for a *single* UHD disc. Reading "4K UHD BLURAY" as a two-disc
pack would guess, and guess wrong for every shelf that spells one disc that way
-- while reading the underscore-joined code as one is reading the record, since
that string is only ever written for a two-disc pack.
"""

import os
import re
import sys
import unittest


backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_root not in sys.path:
    sys.path.insert(0, backend_root)

from next_common import physical_format_label  # noqa: E402


class PhysicalFormatLabelTests(unittest.TestCase):
    def test_the_spellings_one_shelf_actually_holds_fold_together(self):
        # Every one of these is the same product on a shelf.
        for raw in ("4K UHD + Blu-ray", "4K UHD + Blu-Ray", "4k uhd + bluray",
                    "4K UHD/Blu-ray", "UHD + BD", "4K UHD & Blu-Ray"):
            with self.subTest(raw=raw):
                self.assertEqual(physical_format_label(raw), "4K UHD + Blu-ray")

    def test_the_raw_codes_providers_write(self):
        self.assertEqual(physical_format_label("4K_UHD"), "4K UHD")
        self.assertEqual(physical_format_label("BLURAY"), "Blu-ray")
        self.assertEqual(physical_format_label("blu ray"), "Blu-ray")
        self.assertEqual(physical_format_label("BD"), "Blu-ray")
        self.assertEqual(physical_format_label("dvd"), "DVD")

    def test_a_combo_needs_an_explicit_separator(self):
        # "4K Blu-ray" is what a single UHD disc is called, so a pack is only
        # a pack when the value says so. A bare *space* is not that mark, and
        # guessing here would mislabel every shelf that spells one disc this way.
        self.assertEqual(physical_format_label("4K UHD BLURAY"), "4K UHD")
        self.assertEqual(physical_format_label("4K Blu-ray"), "4K UHD")
        self.assertEqual(physical_format_label("Ultra HD Blu-ray"), "4K UHD")

    def test_the_clients_underscore_combo_code_is_a_pack(self):
        # `4K_UHD_BLURAY` is the single token iOS `DiscFormat.uhd4kBluray` and
        # the PWA filter store for a two-disc pack, and the sync push writes it
        # raw into `movies.format`. The underscore joining the Blu-ray token to
        # the 4K name is the pack's own separator, so both halves survive -- the
        # correction sheet and this chart were dropping the Blu-ray leg and
        # folding the pack in with single-4K discs.
        for raw in ("4K_UHD_BLURAY", "4k_uhd_bluray", "4K_UHD_BD"):
            with self.subTest(raw=raw):
                self.assertEqual(physical_format_label(raw), "4K UHD + Blu-ray")

    def test_hd_dvd_is_not_dvd(self):
        self.assertEqual(physical_format_label("HD DVD"), "HD DVD")

    def test_an_unknown_value_is_returned_as_typed(self):
        # Inventing a spelling for something nobody recognises would merge
        # things that are not the same, and that mistake is invisible.
        self.assertEqual(physical_format_label("LaserDisc"), "LaserDisc")
        self.assertEqual(physical_format_label("VHS"), "VHS")
        self.assertEqual(physical_format_label("  spaced   out  "), "spaced out")

    def test_blank_is_blank(self):
        for value in ("", "   ", None):
            with self.subTest(value=value):
                self.assertEqual(physical_format_label(value), "")


class StatisticsUseTheSharedLabelTests(unittest.TestCase):
    """Source-level, because the defect was a second implementation existing."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(backend_root, "next_app.py"), encoding="utf-8") as handle:
            cls.app_source = handle.read()
        path = os.path.join(backend_root, "next_movievault_v2_field_corrections.py")
        with open(path, encoding="utf-8") as handle:
            cls.corrections_source = handle.read()

    def test_the_endpoint_has_no_normaliser_of_its_own(self):
        self.assertNotIn(
            "def format_label(",
            self.app_source,
            "the statistics endpoint grew a second format normaliser again",
        )

    def test_the_endpoint_routes_through_the_shared_one(self):
        self.assertIn("physical_format_label(row.get(\"label\")", self.app_source)

    def test_the_contribution_sheet_delegates_rather_than_copying(self):
        self.assertIn("return physical_format_label(text)", self.corrections_source)
        self.assertNotIn("_BLURAY_PATTERN", self.corrections_source)

    def test_the_counts_are_folded_after_normalising(self):
        # Grouping happens in SQL on the raw column, so the fold has to happen
        # in Python or the chart shows the same label twice.
        self.assertIn("format_counts[label] = format_counts.get(label, 0) + count", self.app_source)

    def test_the_total_is_summed_from_the_same_buckets(self):
        # A total counted separately from the buckets can disagree with them.
        window = self.app_source[self.app_source.index("format_counts: dict[str, int] = {}"):]
        window = window[: window.index("by_format = [")]
        self.assertIn("total += count", window)


class FoldingTests(unittest.TestCase):
    """The fold itself, on the shape the SQL hands over."""

    @staticmethod
    def _fold(rows):
        counts: dict[str, int] = {}
        total = 0
        for label, count in rows:
            key = physical_format_label(label) or "Unknown"
            counts[key] = counts.get(key, 0) + count
            total += count
        by_format = [
            {"label": label, "count": count}
            for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))
        ]
        return by_format, total

    def test_two_spellings_become_one_entry(self):
        by_format, total = self._fold([("4K UHD + Blu-Ray", 7), ("4K UHD + Blu-ray", 5)])
        self.assertEqual(by_format, [{"label": "4K UHD + Blu-ray", "count": 12}])
        self.assertEqual(total, 12)

    def test_the_total_matches_the_sum_of_the_folded_buckets(self):
        by_format, total = self._fold(
            [("BLURAY", 10), ("Blu-ray", 3), ("4K_UHD", 6), ("dvd", 2), ("", 1)]
        )
        self.assertEqual(sum(entry["count"] for entry in by_format), total)
        labels = {entry["label"]: entry["count"] for entry in by_format}
        self.assertEqual(labels["Blu-ray"], 13)
        self.assertEqual(labels["4K UHD"], 6)
        self.assertEqual(labels["DVD"], 2)
        self.assertEqual(labels["Unknown"], 1)

    def test_the_biggest_bucket_leads(self):
        by_format, _ = self._fold([("dvd", 2), ("BLURAY", 9)])
        self.assertEqual([entry["label"] for entry in by_format], ["Blu-ray", "DVD"])

    def test_ties_break_on_the_label_so_the_order_is_stable(self):
        first, _ = self._fold([("dvd", 4), ("BLURAY", 4)])
        second, _ = self._fold([("BLURAY", 4), ("dvd", 4)])
        self.assertEqual(first, second)


class NormaliserAgreementTests(unittest.TestCase):
    """The PWA has two format normalisers; they must not disagree.

    `physicalFormatLabel` decides how a format is *shown*, and
    `normalizedMovieFormatValue` decides which of the seven filter options a
    format *is*. Two functions answering "how is this spelled" drift, and they
    had: an HD DVD's badge read "DVD" while the library filter listed the same
    disc under "HD DVD". Neither was wrong on its own terms, which is why
    nothing failed.

    Pinned at source level rather than by running the JavaScript, so the check
    needs nothing but Python and runs everywhere the rest of the suite does.
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(backend_root, "next_views_ui.py"), encoding="utf-8") as handle:
            cls.source = handle.read()

    @staticmethod
    def _function(source, name):
        start = source.index("function %s(" % name)
        depth = 0
        for index in range(source.index("{", start), len(source)):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    return source[start:index + 1]
        raise AssertionError("unbalanced %s" % name)

    def test_the_display_label_does_not_call_an_hd_dvd_a_dvd(self):
        body = self._function(self.source, "physicalFormatLabel")
        self.assertIn('!lower.includes("hd dvd")', body)

    def test_the_filter_value_agrees(self):
        body = self._function(self.source, "normalizedMovieFormatValue")
        self.assertIn('lower.includes("hd dvd")', body)

    def test_python_agrees_with_both(self):
        self.assertEqual(physical_format_label("HD DVD"), "HD DVD")

    def test_the_two_javascript_normalisers_are_documented_as_a_pair(self):
        # A display label and a filter value may legitimately differ - the
        # filter maps onto a fixed vocabulary. That is a decision, so it has to
        # be written down where both are defined, or the next reader reads the
        # difference as the bug it looks like.
        body = self._function(self.source, "physicalFormatLabel")
        self.assertIn("library filter", body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
