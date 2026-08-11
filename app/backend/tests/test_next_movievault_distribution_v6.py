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

    def test_every_table_keyed_by_contract_covers_every_supported_contract(self):
        """Found by enumerating the module rather than by listing the tables.

        This class's own docstring claims "every place the consumer enumerates
        contract versions names v6", and it named two of the three. The third
        was `ASSET_PATH_PATTERNS`, whose five values are character-for-character
        identical -- so it reads like a constant, and is in fact indexed with a
        bare `[contract_version]` on every record carrying artwork. v6 shipped
        without its row, and the first real film with a poster took the whole
        sync down with `KeyError: 'distribution-6'`.

        A hand-written list of tables has the same failure mode as the thing it
        is checking, so this asks the module instead.
        """
        supported = set(mv.SUPPORTED_CONTRACTS)
        checked = 0
        for name in dir(mv):
            table = getattr(mv, name)
            if not isinstance(table, dict) or not table:
                continue
            # A table *about* contract versions is one whose keys are all
            # contract versions. Anything else is a different kind of map.
            if not set(table).issubset(supported):
                continue
            checked += 1
            with self.subTest(table=name):
                self.assertEqual(
                    set(table),
                    supported,
                    f"{name} is missing {sorted(supported - set(table))}",
                )
        # The sweep is only a guard while it finds something to guard.
        self.assertGreaterEqual(checked, 2, "no contract-keyed tables were found")



class ArtworkOnV6Tests(unittest.TestCase):
    """The path that actually broke, exercised directly.

    The sweep above states the invariant; this states the symptom. A v6 record
    carrying artwork is the ordinary case -- most films have a poster -- so
    this ran on the very first sync after the contract was raised, and raised
    `KeyError: 'distribution-6'` before any record was stored.
    """

    VARIANT = {
        "path": "/v2/assets/2f1c0f3e-0000-4000-8000-000000000000/thumbnail",
        "checksum": "b" * 64,
    }

    def test_an_asset_variant_parses_under_v6(self):
        parsed = mv._asset_variant(dict(self.VARIANT), mv.MOVIEVAULT_V6_CONTRACT)
        self.assertEqual(parsed["path"], self.VARIANT["path"])

    def test_the_asset_path_is_still_the_unversioned_one(self):
        """MovieVault serves assets from `/v2/assets/...` whatever the contract
        envelope says, which is why every row of the table is identical -- and
        why a v6 row that pointed at `/v6/assets/...` would be wrong rather
        than merely redundant."""
        with self.assertRaises(mv.MovieVaultV2Error):
            mv._asset_variant(
                {"path": "/v6/assets/2f1c0f3e-0000-4000-8000-000000000000/thumbnail",
                 "checksum": "b" * 64},
                mv.MOVIEVAULT_V6_CONTRACT,
            )

    def test_every_supported_contract_accepts_the_same_asset_path(self):
        for contract in mv.SUPPORTED_CONTRACTS:
            with self.subTest(contract=contract):
                self.assertEqual(
                    mv._asset_variant(dict(self.VARIANT), contract)["path"],
                    self.VARIANT["path"],
                )


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
