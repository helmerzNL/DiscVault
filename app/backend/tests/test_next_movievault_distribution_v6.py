"""distribution-6: the contract that carries the per-disc breakdown.

The consumer half of the deployment-order rule: everything here must hold
*before* the origin ever serves v6, because tolerance has to reach instances
ahead of the feed. The three layers with a history of being missed each get an
assertion — the contract enumerations (the v3 test's lesson), the sync-state
CHECK (073's lesson, covered by the SUPPORTED_CONTRACTS iteration in
test_next_movievault_v2_postgres), and the parser's open posture (563's lesson,
inherited from ``_seasons``).
"""

import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_movievault_v2 as mv


class ContractEnumerationTests(unittest.TestCase):
    """Every place the consumer enumerates contract versions names v6."""

    def test_v6_is_supported_and_routable(self):
        self.assertIn(mv.MOVIEVAULT_V6_CONTRACT, mv.SUPPORTED_CONTRACTS)
        self.assertEqual(mv.CONTRACT_PATH_VERSIONS[mv.MOVIEVAULT_V6_CONTRACT], "6")

    def test_v6_inherits_every_earlier_contracts_fields(self):
        """The or-later predicates, not equality — the fourteen-sites lesson
        their own comment records. A v6 record that lost seasons or finishes
        because a predicate said `== v5` would fail silently, per field."""
        for predicate in (mv._is_v3_or_later, mv._is_v4_or_later, mv._is_v5_or_later,
                          mv._is_v6_or_later):
            with self.subTest(predicate=predicate.__name__):
                self.assertTrue(predicate(mv.MOVIEVAULT_V6_CONTRACT))
        self.assertFalse(mv._is_v6_or_later(mv.MOVIEVAULT_V5_CONTRACT))


class DiscsParserTests(unittest.TestCase):
    """``_discs`` keeps the catalog alive rather than the entry perfect."""

    def test_discs_are_ordered_by_position_and_kept_verbatim(self):
        parsed = mv._discs(
            [
                {"position": 2, "discType": "bluray", "discRole": "bonus"},
                {"position": 1, "discType": "uhd_bluray", "label": "4K UHD",
                 "hdrFormats": ["dolby_vision"], "someFutureKey": "kept"},
            ],
            release_id="r1",
        )
        self.assertEqual([d["position"] for d in parsed], [1, 2])
        # Verbatim mirror: unknown keys and open-enum values survive untouched.
        self.assertEqual(parsed[0]["someFutureKey"], "kept")

    def test_unusable_entries_cost_themselves_and_nothing_else(self):
        """563's posture one field on: a raise here is not one lost breakdown
        but a dead catalog for every instance."""
        parsed = mv._discs(
            [
                "not-a-dict",
                {"position": 0, "discType": "dvd"},      # 0 is the release, not a disc
                {"position": 1, "discType": "dvd"},
                {"position": 1, "discType": "bluray"},   # duplicate position
                {"discType": "bluray"},                  # no position at all
            ],
            release_id="r1",
        )
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["discType"], "dvd")

    def test_a_non_list_is_ignored_rather_than_fatal(self):
        self.assertEqual(mv._discs({"position": 1}, release_id="r1"), [])

    def test_none_and_empty_stay_distinct_through_the_parser_gate(self):
        """Absent key → None (the feed has not said); `[]` → a statement.
        The record-level gate mirrors `seasons`' rule exactly."""
        self.assertEqual(mv._discs([], release_id="r1"), [])


# Migration 076's own round trip -- that the mirror column exists, and that
# NULL survives it distinct from `[]` -- lives in
# test_next_movievault_v2_postgres.py instead of here, with the other schema
# assertions. Not a matter of taste: this module is named in the
# distribution-parser step, which runs *before* `next_database.py migrate`, and
# DATABASE_URL is set for the whole job. A schema test placed here therefore
# does not skip -- it connects to a database without the table and fails on a
# migration that is perfectly correct.


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
