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


if __name__ == "__main__":
    unittest.main()
