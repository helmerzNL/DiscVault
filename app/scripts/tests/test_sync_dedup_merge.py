"""Tests for app/scripts/sync_dedup_merge.py (pure/plan logic, no live DB)."""

from __future__ import annotations

import importlib.util
import os
import unittest


_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "sync_dedup_merge.py")
_spec = importlib.util.spec_from_file_location("sync_dedup_merge", _SCRIPT)
merge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(merge)


class NormalizationParityTests(unittest.TestCase):
    """The merge script must group with the same rules the live ladder matches."""

    def test_title_normalization_matches_expectations(self):
        self.assertEqual(merge.normalize_title("The Matrix"), "matrix")
        self.assertEqual(merge.normalize_title("Amélie"), "amelie")
        self.assertEqual(
            merge.normalize_title("WALL·E"), merge.normalize_title("wall e")
        )
        self.assertIsNone(merge.normalize_title("   "))

    def test_barcode_normalization_keeps_digits(self):
        self.assertEqual(merge.normalize_barcode("0-051-89"), "005189")
        self.assertIsNone(merge.normalize_barcode(""))


class DedupPlanTests(unittest.TestCase):
    def test_first_tier_claims_members_so_losers_tombstone_once(self):
        groups = {
            "barcode": {"5051890000000": ["a", "b"]},
            "tmdbEdition": {("603", "4k uhd", ""): ["a", "b"]},
            "titleYear": {("matrix", "1999", "4k uhd"): ["a", "b"]},
        }
        plans = merge._dedup_group_members(groups)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["tier"], "barcode")
        self.assertEqual(sorted(plans[0]["members"]), ["a", "b"])

    def test_distinct_groups_across_tiers_are_all_planned(self):
        groups = {
            "barcode": {"5051890000000": ["a", "b"]},
            "tmdbEdition": {("603", "4k uhd", ""): ["c", "d"]},
            "titleYear": {},
        }
        plans = merge._dedup_group_members(groups)
        self.assertEqual(len(plans), 2)

    def test_singletons_are_not_merged(self):
        groups = {"barcode": {"x": ["a"]}, "tmdbEdition": {}, "titleYear": {}}
        self.assertEqual(merge._dedup_group_members(groups), [])


class _FakeCursor:
    def __init__(self, counts):
        self._counts = counts
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        # Every scoring query is a count for a given movie id.
        movie_id = params[0]
        self._row = {"n": self._counts.get(movie_id, 0)}

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, counts):
        self._counts = counts

    def cursor(self):
        return _FakeCursor(self._counts)


class WinnerSelectionTests(unittest.TestCase):
    def test_movie_with_more_user_data_wins(self):
        by_id = {
            "poor": {"id": "poor", "notes": None, "created_at": 1},
            "rich": {"id": "rich", "notes": "great disc", "created_at": 2},
        }
        # 'rich' has more related rows in every relation table.
        counts = {"rich": 5, "poor": 0}
        conn = _FakeConn(counts)
        winner, losers, _scores = merge._choose_winner(conn, ["poor", "rich"], by_id)
        self.assertEqual(winner, "rich")
        self.assertEqual(losers, ["poor"])

    def test_tie_breaks_on_oldest_created_at(self):
        by_id = {
            "old": {"id": "old", "created_at": 1},
            "new": {"id": "new", "created_at": 2},
        }
        conn = _FakeConn({"old": 0, "new": 0})
        winner, losers, _scores = merge._choose_winner(conn, ["new", "old"], by_id)
        self.assertEqual(winner, "old")
        self.assertEqual(losers, ["new"])


if __name__ == "__main__":
    unittest.main()
