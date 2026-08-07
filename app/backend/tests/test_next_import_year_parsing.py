"""`import_year` must survive a row that has neither a year nor a release date.

That row shape is normal, not exotic: a source whose date column describes the
disc rather than the film (`releaseDateIsEditionDate`) deliberately supplies no
year and no `releaseDate`, so this is exactly the path such an import takes for
every one of its rows.
"""

import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_import import clean_text
from app.backend.next_worker import import_year


class ImportYearTests(unittest.TestCase):
    def test_clean_text_returns_none_not_empty_string(self):
        # The premise of the bug: this `clean_text` yields None, so any caller
        # that measures or slices its result must coalesce first.
        self.assertIsNone(clean_text(None))
        self.assertIsNone(clean_text(""))

    def test_absent_value_yields_no_year_instead_of_raising(self):
        # Regression: this raised "object of type 'NoneType' has no len()",
        # which the import loop caught per row and recorded as an error, so the
        # row was never written and the whole import failed with a wall of
        # identical, contextless messages.
        self.assertEqual(import_year(None), "")
        self.assertEqual(import_year(""), "")

    def test_a_year_is_still_extracted_from_any_date_shape(self):
        self.assertEqual(import_year("2012-08-15"), "2012")
        self.assertEqual(import_year("August 15 2012"), "2012")
        self.assertEqual(import_year(1995), "1995")

    def test_values_carrying_no_plausible_year_yield_nothing(self):
        self.assertEqual(import_year("soon"), "")
        self.assertEqual(import_year("12-08"), "")


if __name__ == "__main__":
    unittest.main()
