"""Existing provider ratings must not freeze under fill-only metadata policy."""

import sys
import unittest
from pathlib import Path

repo_root = str(Path(__file__).resolve().parents[3])
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_metadata import canonicalize_plugin_result, merge_metadata_results


class RatingRefreshTests(unittest.TestCase):
    def merge(self, current, *providers, overwrite=False):
        return merge_metadata_results(
            current=current,
            technical_current={},
            results=[
                canonicalize_plugin_result(provider, "movie_details", {"movie": movie})
                for provider, movie in providers
            ],
            overwrite_enabled=overwrite,
            target_format="Blu-ray",
        )

    def test_stored_zero_is_replaced_when_votes_are_filled(self):
        for zero in ("0", "0.0", 0):
            with self.subTest(zero=zero):
                proposal = self.merge(
                    {"rating": zero, "rating_votes": None},
                    ("tmdb", {"rating": "6.9", "ratingVotes": 7890}),
                )
                self.assertEqual(proposal["movieUpdates"], {"rating": "6.9", "rating_votes": 7890})

    def test_refresh_updates_already_populated_scores_and_votes_in_either_direction(self):
        for score, votes in (("7.1", 8000), ("6.8", 7800)):
            with self.subTest(score=score, votes=votes):
                proposal = self.merge(
                    {"rating": "6.9", "rating_votes": 7890},
                    ("tmdb", {"rating": score, "ratingVotes": votes}),
                )
                self.assertEqual(proposal["movieUpdates"], {"rating": score, "rating_votes": votes})

    def test_backfilled_votes_do_not_prevent_repairing_the_score(self):
        proposal = self.merge(
            {"rating": "0", "rating_votes": 7890},
            ("tmdb", {"rating": "6.9", "ratingVotes": 7890}),
        )
        self.assertEqual(proposal["movieUpdates"].get("rating"), "6.9")

    def test_higher_priority_provider_wins_even_when_its_values_are_unchanged(self):
        proposal = self.merge(
            {"rating": "6.9", "rating_votes": 7890},
            ("tmdb", {"rating": "6.9", "ratingVotes": 7890}),
            ("omdb", {"rating": "7.5", "ratingVotes": 12000}),
        )
        self.assertEqual(proposal["movieUpdates"], {"rating": "6.9", "rating_votes": 7890})
        for decision in proposal["fieldDecisions"]:
            self.assertEqual(decision["winner"]["pluginId"], "tmdb")
            self.assertFalse(decision["candidates"][1]["accepted"])

    def test_missing_score_and_invalid_votes_do_not_erase_stored_values(self):
        for score, votes in ((None, None), ("", ""), (None, -1), (None, "many")):
            with self.subTest(score=score, votes=votes):
                proposal = self.merge(
                    {"rating": "6.9", "rating_votes": 7890},
                    ("tmdb", {"rating": score, "ratingVotes": votes}),
                )
                self.assertEqual(proposal["movieUpdates"], {})

    def test_explicit_zero_votes_are_an_update(self):
        proposal = self.merge(
            {"rating_votes": 10},
            ("tmdb", {"ratingVotes": 0}),
        )
        self.assertEqual(proposal["movieUpdates"], {"rating_votes": 0})

    def test_refresh_does_not_enable_overwrite_for_other_fields(self):
        proposal = self.merge(
            {"title": "My title", "overview": "My overview", "rating": "0"},
            ("tmdb", {"title": "Provider title", "overview": "Provider overview", "rating": "6.9"}),
        )
        self.assertEqual(proposal["movieUpdates"], {"rating": "6.9"})

    def test_existing_field_locks_still_apply_with_overwrite_enabled(self):
        proposal = self.merge(
            {"overview": "My overview", "rating": "0", "metadata": {"field_locks": ["overview"]}},
            ("tmdb", {"overview": "Provider overview", "rating": "6.9"}),
            overwrite=True,
        )
        self.assertEqual(proposal["movieUpdates"], {"rating": "6.9"})


if __name__ == "__main__":
    unittest.main()
