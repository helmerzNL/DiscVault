"""A plugin that proposes a box set must not crash the import preview.

`import_source_metadata_suggestions` de-duplicates the box-set proposals a
metadata plugin returns by hashing an identity key for each one. That hash
was written as `json.dumps`, but `next_app` imports the module as
`json_lib` — so the line compiled fine and raised `NameError: name 'json'
is not defined` the first time a provider actually answered with a
`boxSetProposal`. Nothing else in the preview path reaches it, so no
existing test noticed.

These tests walk that branch: one proposal comes back annotated, and two
identical proposals collapse to one.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_app


TRILOGY_MEMBERS = [
    {"title": "The Godfather", "year": "1972"},
    {"title": "The Godfather: Part II", "year": "1974"},
]


def _proposal(title="The Godfather Trilogy"):
    return {"title": title, "barcode": "4020628667276", "members": list(TRILOGY_MEMBERS)}


class ImportBoxSetProposalSuggestionTests(unittest.TestCase):
    def suggestions_for(self, results):
        with patch.object(
            next_app,
            "lookup_metadata_sources",
            return_value={"results": results, "sourceSummary": []},
        ):
            return next_app.import_source_metadata_suggestions(
                MagicMock(),
                item={"title": "The Godfather Trilogy", "barcode": "4020628667276"},
                actor={"id": "00000000-0000-0000-0000-000000000011", "permissions": ["*"]},
            )

    def test_a_returned_box_set_proposal_is_carried_into_the_preview(self):
        suggestions = self.suggestions_for(
            [{"pluginId": "movievault", "sourceLabel": "MovieVault", "boxSetProposal": _proposal()}]
        )

        self.assertEqual(suggestions["status"], "ok")
        proposals = suggestions["boxSetProposals"]
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["title"], "The Godfather Trilogy")
        self.assertEqual(proposals[0]["provider"], "movievault")

    def test_the_same_proposal_from_one_provider_is_only_offered_once(self):
        suggestions = self.suggestions_for(
            [
                {
                    "pluginId": "movievault",
                    "sourceLabel": "MovieVault",
                    "boxSetProposals": [_proposal(), _proposal()],
                }
            ]
        )

        self.assertEqual(len(suggestions["boxSetProposals"]), 1)

    def test_distinct_proposals_are_both_offered(self):
        suggestions = self.suggestions_for(
            [
                {
                    "pluginId": "movievault",
                    "sourceLabel": "MovieVault",
                    "boxSetProposals": [_proposal(), _proposal("The Godfather Collection")],
                }
            ]
        )

        self.assertEqual(len(suggestions["boxSetProposals"]), 2)

    def test_candidate_without_overview_is_offered_with_empty_overview(self):
        suggestions = self.suggestions_for(
            [
                {
                    "pluginId": "omdb",
                    "sourceLabel": "OMDb",
                    "candidates": [{"title": "The Godfather", "year": "1972", "imdbId": "tt0068646"}],
                }
            ]
        )

        self.assertEqual(suggestions["items"][0]["overview"], "")

    def test_movie_updates_fallback_without_overview_is_offered(self):
        suggestions = self.suggestions_for(
            [
                {
                    "pluginId": "movievault_v2",
                    "sourceLabel": "MovieVault",
                    "movieUpdates": {"title": "The Godfather", "year": "1972"},
                }
            ]
        )

        self.assertEqual(suggestions["items"][0]["overview"], "")

    def test_proposal_without_title_is_carried_into_the_preview(self):
        suggestions = self.suggestions_for(
            [{"pluginId": "movievault", "sourceLabel": "MovieVault", "boxSetProposal": _proposal(title=None)}]
        )

        self.assertEqual(len(suggestions["boxSetProposals"]), 1)
        self.assertIsNone(suggestions["boxSetProposals"][0]["audit"]["title"])

    def test_proposal_without_title_can_become_a_container_review(self):
        reviews = next_app.import_source_box_set_reviews(
            [{"containerType": "box_set", "boxSetProposal": _proposal(title=None)}],
            [],
        )

        self.assertEqual(len(reviews), 1)
        self.assertIsNone(reviews[0]["title"])

    def test_proposal_without_title_can_become_a_queue_review(self):
        reviews = next_app.import_source_box_set_reviews(
            [],
            [{"detectedBoxSetProposal": _proposal(title=None)}],
        )

        self.assertEqual(len(reviews), 1)
        self.assertIsNone(reviews[0]["title"])

    def test_titleless_container_without_a_proposal_stays_reviewable(self):
        reviews = next_app.import_source_box_set_reviews(
            [{"containerType": "box_set", "members": []}],
            [],
        )

        self.assertEqual(len(reviews), 1)
        self.assertIsNone(reviews[0]["title"])


class ImportUploadCandidateTests(unittest.TestCase):
    def test_unexpected_metadata_suggestion_failure_is_not_silenced(self):
        with (
            patch.object(
                next_app,
                "inspect_import_source_plugin",
                return_value=({"found": True, "readable": True}, {}),
            ),
            patch.object(next_app, "import_source_summary", return_value={"pluginId": "import_clz_movies"}),
            patch.object(
                next_app,
                "inspect_import_source_selection",
                side_effect=[
                    {"source": {"pluginId": "import_clz_movies"}},
                    RuntimeError("metadata suggestions failed"),
                ],
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "metadata suggestions failed"):
                next_app.import_upload_candidates(
                    MagicMock(),
                    source_path=Path("collection.csv"),
                    plugins=[{"id": "import_clz_movies", "name": "CLZ Movies"}],
                    actor={"id": "00000000-0000-0000-0000-000000000011", "permissions": ["*"]},
                )


if __name__ == "__main__":
    unittest.main()
