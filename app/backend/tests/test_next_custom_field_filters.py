"""Filtering, sorting and exporting on instance-defined fields.

The claim that carries the most weight is the one about an **archived field
inside a saved filter**. Saved smart filters live only in each browser's
localStorage, so the server can never repair them, and `normalizeAdvancedSearch`
rebuilds its object on every read: a clause that gets dropped there does not
error, it becomes "any", and the user is shown their whole library as though all
of it matched. Archiving a field must therefore not drop the clause — the field
loses its input row, and keeps filtering.

The export half is a second cross-platform contract. `DEFAULT_EXPORT_COLUMN_KEYS`
must not move: a custom column exists on one instance and not another, so a
default selection that included one could never produce the same file twice.
"""

import os
import sys
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEXT_VIEWS_UI_PATH = os.path.join(BACKEND_DIR, "next_views_ui.py")

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import next_export_columns  # noqa: E402
from next_common import NextApiError  # noqa: E402


def _source() -> str:
    with open(NEXT_VIEWS_UI_PATH, "r", encoding="utf-8") as handle:
        return handle.read()


# A fixed-width window after the function's name is a proxy for "inside this
# function", and it silently stops being one as soon as the function grows: the
# assertion then fails for a reason that has nothing to do with what it claims.
# Adding the score filter to the same panel pushed three of these windows past
# the function they meant. Brace matching reads the real one, so it cannot go
# stale.
def _function_source(source: str, name: str) -> str:
    start = source.index("function %s(" % name)
    depth = 0
    for position in range(source.index("{", start), len(source)):
        char = source[position]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : position + 1]
    raise AssertionError("unbalanced braces reading %s" % name)


FIELDS = [
    {"key": "shelf", "name": "Shelf", "fieldType": "text"},
    {"key": "rip", "name": "Rip status", "fieldType": "select"},
]


class ExportCatalogueTests(unittest.TestCase):
    def test_custom_columns_are_appended_and_default_off(self):
        catalogue = next_export_columns.export_column_catalogue(FIELDS)
        keys = [column["key"] for column in catalogue]
        self.assertEqual(keys[-2:], ["custom:shelf", "custom:rip"])
        for column in catalogue:
            if column["key"].startswith("custom:"):
                with self.subTest(column=column["key"]):
                    self.assertFalse(column["default"])

    def test_the_default_selection_is_untouched_by_custom_fields(self):
        # Two installations legitimately define different fields, so a default
        # selection containing one could never produce the same file twice.
        before = list(next_export_columns.DEFAULT_EXPORT_COLUMN_KEYS)
        next_export_columns.export_column_catalogue(FIELDS)
        self.assertEqual(list(next_export_columns.DEFAULT_EXPORT_COLUMN_KEYS), before)
        self.assertFalse([key for key in before if key.startswith("custom:")])

    def test_a_custom_column_carries_the_owners_label_and_no_i18n_key(self):
        catalogue = next_export_columns.export_column_catalogue(FIELDS)
        shelf = next(column for column in catalogue if column["key"] == "custom:shelf")
        self.assertEqual(shelf["label"], "Shelf")
        self.assertEqual(shelf["i18nKey"], "")

    def test_a_known_custom_column_is_accepted(self):
        self.assertEqual(
            next_export_columns.normalize_columns(["title", "custom:rip"], FIELDS),
            ["title", "custom:rip"],
        )

    def test_an_undefined_custom_column_is_refused_like_a_typo(self):
        with self.assertRaises(NextApiError):
            next_export_columns.normalize_columns(["title", "custom:nope"], FIELDS)

    def test_custom_columns_come_out_in_definition_order_not_request_order(self):
        # A stored column selection must keep producing the same column order.
        self.assertEqual(
            next_export_columns.normalize_columns(["custom:rip", "custom:shelf"], FIELDS),
            ["custom:shelf", "custom:rip"],
        )

    def test_a_request_naming_only_custom_columns_is_valid(self):
        self.assertEqual(
            next_export_columns.normalize_columns(["custom:shelf"], FIELDS), ["custom:shelf"]
        )

    def test_without_definitions_a_custom_column_is_refused(self):
        with self.assertRaises(NextApiError):
            next_export_columns.normalize_columns(["custom:shelf"], [])


class FilterSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_the_schema_carries_a_custom_object(self):
        start = self.source.index("function advancedSearchDefaults")
        block = self.source[start : start + 900]
        self.assertIn("custom: {}", block)

    def test_a_stored_constraint_is_kept_rather_than_validated_against_data(self):
        # The whole point: an archived field keeps filtering. Dropping the clause
        # would make the filter mean "any" and show everything.
        start = self.source.index("function normalizeCustomFilters")
        block = self.source[start : start + 1400]
        self.assertIn("CUSTOM_FILTER_OPS.includes(source.op)", block)
        self.assertNotIn("customFieldDefinitions()", block)

    def test_a_malformed_field_key_is_dropped_from_a_stored_filter(self):
        start = self.source.index("function normalizeCustomFilters")
        block = self.source[start : start + 1400]
        self.assertIn("/^[a-z][a-z0-9_]{0,47}$/.test(String(key))", block)

    def test_each_constrained_field_counts_towards_the_badge(self):
        block = _function_source(self.source, "advancedSearchActiveCount")
        self.assertIn("Object.keys(normalized.custom || {}).length", block)

    def test_the_controls_are_read_back_by_scanning_the_dom(self):
        # The rows are generated from the definitions, so a literal list of ids
        # like every other filter has cannot work here.
        start = self.source.index("function readCustomFilterControls")
        block = self.source[start : start + 900]
        self.assertIn('querySelectorAll("[data-custom-filter-op]")', block)


class MatchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_every_operator_is_handled(self):
        start = self.source.index("function customValueMatches")
        block = self.source[start : start + 2000]
        for op in ("set", "unset", "contains", "gte", "lte"):
            with self.subTest(op=op):
                self.assertIn(f'"{op}"', block)

    def test_a_missing_value_fails_every_operator_except_unset(self):
        start = self.source.index("function customValueMatches")
        block = self.source[start : start + 2000]
        self.assertIn('if (op === "unset") return entry === undefined;', block)
        self.assertIn("if (entry === undefined) return false;", block)

    def test_numbers_compare_numerically(self):
        start = self.source.index("function customValueMatches")
        block = self.source[start : start + 2000]
        self.assertIn('if (entry.type === "number")', block)

    def test_every_constraint_must_hold(self):
        start = self.source.index("function movieMatchesCustomFilters")
        block = self.source[start : start + 800]
        self.assertIn("constraints.every(", block)

    def test_the_predicate_is_in_the_advanced_search_chain(self):
        start = self.source.index("function movieMatchesAdvancedSearch")
        block = self.source[start : start + 4000]
        self.assertIn("movieMatchesCustomFilters(movie, filters.custom)", block)

    def test_a_container_delegates_custom_filters_to_its_members(self):
        # Without this a box set of ripped discs vanishes under a rip filter,
        # because a container carries no custom values of its own.
        start = self.source.index("function containerMatchesAdvancedSearch")
        block = self.source[start : start + 2500]
        self.assertIn("Object.keys(filters.custom || {}).length", block)


class SortingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_a_custom_sort_key_is_checked_against_the_live_definitions(self):
        start = self.source.index("function normalizeLibraryDetailSort")
        block = self.source[start : start + 1600]
        self.assertIn('raw.startsWith("custom:")', block)
        self.assertIn("customFieldDefinitions().some(", block)

    def test_a_custom_sort_is_desktop_only(self):
        start = self.source.index("function normalizeLibraryDetailSort")
        block = self.source[start : start + 1600]
        self.assertIn("if (!compact && typeof raw === \"string\"", block)

    def test_empty_sorts_last_in_both_directions(self):
        start = self.source.index("function compareCustomFieldValue")
        block = self.source[start : start + 1400]
        self.assertIn("if (!leftHas && !rightHas) return 0;", block)
        self.assertIn("if (!leftHas) return 1;", block)
        self.assertIn("if (!rightHas) return -1;", block)
        # The nulls are decided before the direction is applied, or "descending"
        # would put the blanks first.
        self.assertLess(block.index("if (!leftHas) return 1;"), block.index('direction === "desc"'))

    def test_the_comparator_is_reached_from_the_sorter(self):
        start = self.source.index("function sortLibraryListItems")
        block = self.source[start : start + 1600]
        self.assertIn('state.key.startsWith("custom:")', block)


class ExportClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_the_row_builder_emits_a_cell_per_field(self):
        start = self.source.index("function libraryExportRow")
        block = self.source[start : start + 1600]
        self.assertIn("...customExportCells(movie)", block)

    def test_a_cell_exports_the_displayed_value_not_the_stored_key(self):
        start = self.source.index("function customValueDisplay")
        block = self.source[start : start + 1200]
        self.assertIn('tNext("common.yes", "Yes")', block)
        self.assertIn("option.label || option.key", block)

    def test_a_removed_option_still_exports_its_stored_value(self):
        start = self.source.index("function customValueDisplay")
        block = self.source[start : start + 1200]
        self.assertIn("return option ? (option.label || option.key) : String(entry.value);", block)

    def test_column_labels_are_the_owners_names(self):
        start = self.source.index("getExportColumnLabels: () => ({")
        block = self.source[start : start + 1400]
        self.assertIn("field.name || field.key", block)
        self.assertNotIn("tNext(field.name", block)


if __name__ == "__main__":
    unittest.main()
