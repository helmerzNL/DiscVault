"""Media type: normalization, the ladder veto, and the local-only ownership rule.

The vocabulary is fixed by shipped clients (sync-contract.md §3b), so the tests
that matter here are about exactness rather than plumbing: an unrecognised value
must not become MOVIE, and a stored SHOW must survive a client that says nothing.
"""

from __future__ import annotations

import os
import unittest

try:
    from ..dedup_identity import (
        MEDIA_TYPE_MOVIE,
        MEDIA_TYPE_SHOW,
        media_type_conflicts,
        normalize_media_type,
    )
    from .. import next_metadata
except ImportError:  # pragma: no cover - backend working-directory CI imports
    from dedup_identity import (
        MEDIA_TYPE_MOVIE,
        MEDIA_TYPE_SHOW,
        media_type_conflicts,
        normalize_media_type,
    )
    import next_metadata


class NormalizeMediaTypeTests(unittest.TestCase):
    def test_movie_spellings_normalize_to_the_exact_wire_value(self):
        for value in ("MOVIE", "movie", "Movie", " movie ", "film", "FILM"):
            with self.subTest(value=value):
                self.assertEqual(normalize_media_type(value), MEDIA_TYPE_MOVIE)

    def test_show_spellings_normalize_to_the_exact_wire_value(self):
        for value in ("SHOW", "show", "tv", "TV", "tv_series", "tvseries",
                      "series", "tv show", "TV_SHOW"):
            with self.subTest(value=value):
                self.assertEqual(normalize_media_type(value), MEDIA_TYPE_SHOW)

    def test_absent_values_are_absent(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                self.assertIsNone(normalize_media_type(value))

    def test_an_unrecognised_value_is_absent_and_never_movie(self):
        """The server must not copy the clients' silent fallback.

        Android's fromStorage() falls back to MOVIE because a client has to
        render something. A server doing the same turns "I do not recognise
        this" into "this is a film", which is the exact mislabelling this field
        exists to prevent -- and it would then be able to veto merges with it.
        """
        for value in ("anime", "documentary", "SHOWS", "movies", "tv-series-2"):
            with self.subTest(value=value):
                self.assertIsNone(normalize_media_type(value))

    def test_output_is_only_ever_one_of_the_two_exact_strings(self):
        seen = {
            normalize_media_type(value)
            for value in ("movie", "film", "tv", "show", "series", "junk", None)
        }
        self.assertEqual(seen, {MEDIA_TYPE_MOVIE, MEDIA_TYPE_SHOW, None})


class MediaTypeConflictTests(unittest.TestCase):
    def test_both_present_and_different_conflicts(self):
        self.assertTrue(media_type_conflicts("MOVIE", "SHOW"))
        self.assertTrue(media_type_conflicts("film", "tv"))

    def test_both_present_and_equal_does_not_conflict(self):
        self.assertFalse(media_type_conflicts("SHOW", "SHOW"))
        self.assertFalse(media_type_conflicts("tv", "SHOW"))

    def test_one_sided_absence_blocks_nothing(self):
        """A client from before this contract version omits the key entirely.

        That must stay inconclusive rather than becoming a veto, or upgrading
        the server would stop every pre-existing client from ever merging.
        """
        for left, right in ((None, "SHOW"), ("SHOW", None), ("", "MOVIE"), ("MOVIE", "  ")):
            with self.subTest(left=left, right=right):
                self.assertFalse(media_type_conflicts(left, right))

    def test_an_unrecognised_value_never_vetoes(self):
        self.assertFalse(media_type_conflicts("anime", "SHOW"))


class MediaTypeOwnershipTests(unittest.TestCase):
    def test_media_type_is_local_only(self):
        self.assertIn("media_type", next_metadata.METADATA_LOCAL_ONLY_FIELDS)

    def test_media_type_is_not_provider_writable(self):
        """Named in exactly one of the two sets, deliberately.

        Being in neither is the dangerous state: apply_metadata_proposal skips
        unknown fields silently, so a provider proposal would be accepted and
        the write would disappear with no error at all.
        """
        self.assertNotIn("media_type", next_metadata.METADATA_MAIN_FIELDS)

    def test_both_spellings_resolve_to_the_column_name(self):
        self.assertEqual(next_metadata.MOVIE_FIELD_ALIASES["mediaType"], "media_type")
        self.assertEqual(next_metadata.MOVIE_FIELD_ALIASES["media_type"], "media_type")


NEXT_VIEWS_UI_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "next_views_ui.py")
)


class MediaTypeFilterUiTests(unittest.TestCase):
    """The TV filter shipped disabled for a long time. Keep it from going back."""

    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()

    def test_neither_tv_filter_button_is_disabled(self):
        for attribute in ('data-type-filter="tv"', 'data-location-type-filter="tv"'):
            with self.subTest(attribute=attribute):
                index = self.source.index(attribute)
                button = self.source[index : self.source.index("</button>", index)]
                self.assertNotIn("disabled", button)
                self.assertNotIn("is-disabled", button)

    def test_the_type_matcher_is_not_stubbed_out(self):
        self.assertNotIn('if (selected === "tv") return false;', self.source)

    def test_a_box_set_of_a_series_survives_the_tv_filter(self):
        """Containers carry no type of their own; their members do."""
        self.assertIn("function containerMatchesType(container)", self.source)
        self.assertIn("containerMatchesType(container)", self.source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
