"""Which release-level fields stop being authored once a release has discs.

There are three copies of that answer, and they have to agree:

1. **The server's**, `next_discs.UNION_LIST_COLUMNS` plus the scalar resolution.
   This is the only one that decides anything — it is what the derivation
   actually overwrites on save.
2. **The browser's declared set**, `MOVIE_DERIVED_RELEASE_FIELDS`.
3. **The fields actually tagged in the markup**, `data-derived-from-discs`.

The third is the one that failed, and it is worth saying why a two-way test
would have stayed green through it. `regions` was in the server's set from the
day the union shipped. What went wrong is that the editor hid a *section* rather
than a set of fields, and `regions` lives in the Collectors section — so it was
offered as an editable release-level value while every save overwrote it with
the union of the discs. Nothing disagreed; the field simply was not covered.

Hence the attribute rather than a list of element ids in the JS: it travels with
the field if somebody moves it between sections again, and this test fails the
moment a derived field has no tag.
"""

from __future__ import annotations

import os
import re
import sys
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
repo_root = os.path.abspath(os.path.join(BACKEND_DIR, "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_discs, next_views_ui

SOURCE = next_views_ui.__file__

#: The scalar the union derives beside the list columns. Named here rather than
#: imported because it is not in `UNION_LIST_COLUMNS` -- it is ranked, not
#: unioned -- and a test that silently inherited that distinction would stop
#: noticing if the ranking were dropped.
RESOLUTION = "video_resolution"


def _source() -> str:
    with open(SOURCE, encoding="utf-8") as handle:
        return handle.read()


class DerivedReleaseFieldParityTests(unittest.TestCase):
    def server_set(self) -> set[str]:
        return set(next_discs.UNION_LIST_COLUMNS) | {RESOLUTION}

    def browser_set(self) -> set[str]:
        match = re.search(
            r"const MOVIE_DERIVED_RELEASE_FIELDS = \[(.*?)\];", _source(), re.S
        )
        self.assertIsNotNone(match, "MOVIE_DERIVED_RELEASE_FIELDS not found")
        return set(re.findall(r'"([^"]+)"', match.group(1)))

    def tagged_set(self) -> set[str]:
        return set(re.findall(r'data-derived-from-discs="([^"]+)"', _source()))

    def test_the_browser_declares_exactly_what_the_server_derives(self):
        self.assertEqual(self.browser_set(), self.server_set())

    def test_every_derived_field_is_tagged_in_the_markup(self):
        """The leg that failed. A field can be in both sets above and still sit
        on screen as an editable input if nobody tagged it -- which is what
        `regions` did, in the Collectors section, for the whole life of the
        union rule."""
        self.assertEqual(self.tagged_set(), self.server_set())

    def test_regions_is_tagged_although_it_lives_elsewhere(self):
        """Named on its own because it is the one that is easy to lose: it is
        the only derived field outside the Audio & Video block, and §4.7 of the
        sync contract already flags it as the field whose read key and lock name
        disagree. It attracts this class of mistake."""
        self.assertIn("regions", self.tagged_set())

    def test_the_authored_fields_in_that_block_are_not_tagged(self):
        """`disc_count` and `runtime_minutes` sit in the same grid and are *not*
        derived. Hiding the grid took them with it, which removed the disc-count
        input at exactly the moment the mismatch warning starts asking the user
        to reconcile it -- a warning naming a field they could no longer reach.
        """
        source = _source()
        for element in ('id="movieEditDiscCount"', 'id="movieEditRuntime"'):
            with self.subTest(element=element):
                self.assertIn(element, source)
        self.assertNotIn("disc_count", self.tagged_set())
        self.assertNotIn("runtime_minutes", self.tagged_set())

    def test_the_box_fields_are_never_derived(self):
        """Packaging, finishes, the carrier and the content ratings describe the
        box rather than what is pressed onto a platter. Deriving them from discs
        would invent an answer."""
        for column in ("packaging", "outer_packaging", "finishes", "carrier_type", "content_ratings"):
            with self.subTest(column=column):
                self.assertNotIn(column, self.server_set())
                self.assertNotIn(column, self.tagged_set())

    def test_the_detail_view_filters_on_the_same_constant(self):
        """Not a second list. The read-only renderer tags its rows with the same
        field names and filters them through `MOVIE_DERIVED_RELEASE_FIELDS`, so
        the two screens cannot come to disagree about what is derived."""
        source = _source()
        self.assertIn(
            "MOVIE_DERIVED_RELEASE_FIELDS.includes(field)",
            source,
            "the detail view should filter on the shared constant",
        )

    def test_the_note_starts_hidden(self):
        """It reads "These values now come from the discs below", which is false
        for the majority of releases -- the ones with no discs at all. Without
        `hidden` in the static markup every movie showed it until the first
        render."""
        self.assertIn(
            '<p class="hint hidden" id="movieEditReleaseTechnicalDerived"', _source()
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
