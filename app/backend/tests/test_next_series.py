"""Series and season payload shaping.

The database tests cover what the schema enforces; these cover what it cannot —
which spellings a caller may use, and the difference between "unstated" and
"explicitly empty", which is the distinction the whole edit path is keyed on.
"""

from __future__ import annotations

import unittest
import uuid

try:
    from .. import next_app
    from ..next_common import NextApiError
    from ..next_series import (
        normalize_season_ids,
        season_payload,
        series_payload,
    )
except ImportError:  # pragma: no cover - backend working-directory CI imports
    import next_app
    from next_common import NextApiError
    from next_series import normalize_season_ids, season_payload, series_payload


class SeriesPayloadTests(unittest.TestCase):
    def test_a_title_is_required(self):
        for body in ({}, {"title": ""}, {"title": "   "}):
            with self.subTest(body=body):
                with self.assertRaises(NextApiError):
                    series_payload(body)

    def test_both_spellings_are_accepted(self):
        camel = series_payload({"title": "Fargo", "sortTitle": "Fargo, the"})
        snake = series_payload({"title": "Fargo", "sort_title": "Fargo, the"})
        self.assertEqual(camel, snake)

    def test_the_sort_title_falls_back_to_the_title(self):
        """Every caller today sends no sort title, and list order still matters."""
        self.assertEqual(series_payload({"title": "Fargo"})["sort_title"], "Fargo")

    def test_an_over_long_field_is_rejected_rather_than_truncated(self):
        with self.assertRaises(NextApiError):
            series_payload({"title": "x" * 301})

    def test_a_patch_may_omit_the_title(self):
        self.assertIsNone(series_payload({"overview": "x"}, require_title=False)["title"])


class SeasonPayloadTests(unittest.TestCase):
    def test_season_zero_is_valid(self):
        """Specials are season 0 on TMDB and on plenty of printed spines."""
        self.assertEqual(season_payload({"seasonNumber": 0})["season_number"], 0)

    def test_a_number_is_required(self):
        with self.assertRaises(NextApiError):
            season_payload({"title": "Season One"})

    def test_a_negative_or_non_numeric_number_is_rejected(self):
        for value in (-1, "two", "", None, 1.5 and "1.5"):
            with self.subTest(value=value):
                with self.assertRaises(NextApiError):
                    season_payload({"seasonNumber": value})

    def test_a_boolean_is_not_a_number(self):
        """int(True) is 1, which would silently become season one."""
        with self.assertRaises(NextApiError):
            season_payload({"seasonNumber": True})

    def test_an_absent_episode_count_stays_absent(self):
        self.assertIsNone(season_payload({"seasonNumber": 1})["episode_count"])

    def test_an_empty_episode_count_is_absent_rather_than_zero(self):
        """Zero episodes is a claim; a blank field is not."""
        self.assertIsNone(season_payload({"seasonNumber": 1, "episodeCount": ""})["episode_count"])


class SeasonIdListTests(unittest.TestCase):
    def test_an_empty_list_survives_as_an_empty_list(self):
        """It means 'the complete series', so it must not collapse to None."""
        self.assertEqual(normalize_season_ids([]), [])

    def test_duplicates_are_dropped_and_order_is_kept(self):
        first, second = uuid.uuid4(), uuid.uuid4()
        self.assertEqual(
            normalize_season_ids([str(second), str(first), str(second)]),
            [second, first],
        )

    def test_a_bare_string_is_not_a_list(self):
        with self.assertRaises(NextApiError):
            normalize_season_ids(str(uuid.uuid4()))

    def test_an_invalid_id_names_itself(self):
        with self.assertRaises(NextApiError) as caught:
            normalize_season_ids(["not-a-uuid"])
        self.assertIn("not-a-uuid", str(caught.exception))


class MovieSeriesAssignmentTests(unittest.TestCase):
    """Absent, explicitly null, and set are three different instructions."""

    def test_a_body_naming_neither_key_states_nothing(self):
        self.assertIsNone(next_app.movie_series_assignment({"title": "X"}))

    def test_an_explicit_null_clears_the_link(self):
        assignment = next_app.movie_series_assignment({"seriesId": None})
        self.assertIsNotNone(assignment)
        self.assertIsNone(assignment["series_id"])
        self.assertEqual(assignment["season_ids"], [])

    def test_a_series_id_is_read_in_either_spelling(self):
        series_id = uuid.uuid4()
        for key in ("seriesId", "series_id"):
            with self.subTest(key=key):
                self.assertEqual(
                    next_app.movie_series_assignment({key: str(series_id)})["series_id"],
                    series_id,
                )

    def test_seasons_alone_still_produce_an_assignment(self):
        """So that 'seasons but no series' reaches the 400 instead of vanishing."""
        season_id = uuid.uuid4()
        assignment = next_app.movie_series_assignment({"seasonIds": [str(season_id)]})
        self.assertIsNotNone(assignment)
        self.assertEqual(assignment["season_ids"], [season_id])

    def test_a_malformed_series_id_is_rejected(self):
        with self.assertRaises(NextApiError):
            next_app.movie_series_assignment({"seriesId": "not-a-uuid"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
