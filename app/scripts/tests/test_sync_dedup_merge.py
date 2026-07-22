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

    def test_format_normalization_collapses_underscore_and_space(self):
        self.assertEqual(merge.normalize_format("4K_UHD"), merge.normalize_format("4K UHD"))


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


class GroupDetectionSafetyTests(unittest.TestCase):
    def _detect_with(self, movies, tmdb_map):
        original_movies = merge._fetch_live_movies
        original_tmdb = merge._fetch_tmdb_ids
        merge._fetch_live_movies = lambda _conn: movies
        merge._fetch_tmdb_ids = lambda _conn: tmdb_map
        try:
            groups, _by_id, _tmdb = merge.detect_groups(conn=None)
        finally:
            merge._fetch_live_movies = original_movies
            merge._fetch_tmdb_ids = original_tmdb
        return groups

    def test_tmdb_tier_blocks_materially_different_title_or_year(self):
        a = {
            "id": "a",
            "barcode": None,
            "title": "Harry Potter and the Deathly Hallows: Part 1",
            "year": "2010",
            "format": "4K UHD",
            "edition": "",
        }
        b = {
            "id": "b",
            "barcode": None,
            "title": "Harry Potter and the Deathly Hallows: Part 2",
            "year": "2011",
            "format": "4K_UHD",
            "edition": "",
        }
        groups = self._detect_with([a, b], {"a": "12444", "b": "12444"})
        self.assertEqual(groups["tmdbEdition"], {})

    def test_titleyear_tier_blocks_conflicting_barcodes(self):
        a = {
            "id": "a",
            "barcode": "1111111111111",
            "title": "The Matrix",
            "year": "1999",
            "format": "Blu-ray",
            "edition": "",
        }
        b = {
            "id": "b",
            "barcode": "2222222222222",
            "title": "Matrix",
            "year": "1999",
            "format": "Blu ray",
            "edition": "",
        }
        groups = self._detect_with([a, b], {})
        self.assertEqual(groups["titleYear"], {})


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
        winner, losers, _scores, _reasons, _decision = merge._choose_winner(
            conn, ["poor", "rich"], by_id
        )
        self.assertEqual(winner, "rich")
        self.assertEqual(losers, ["poor"])

    def test_tie_breaks_on_oldest_created_at(self):
        by_id = {
            "old": {"id": "old", "created_at": 1},
            "new": {"id": "new", "created_at": 2},
        }
        conn = _FakeConn({"old": 0, "new": 0})
        winner, losers, _scores, _reasons, _decision = merge._choose_winner(
            conn, ["new", "old"], by_id
        )
        self.assertEqual(winner, "old")
        self.assertEqual(losers, ["new"])

    def test_watch_history_outweighs_created_at_tie_breaker(self):
        by_id = {
            "old": {"id": "old", "created_at": 1, "_signals": {"watch_history_count": 0}},
            "new": {"id": "new", "created_at": 2, "_signals": {"watch_history_count": 2}},
        }
        conn = _FakeConn({"old": 0, "new": 0})
        winner, losers, _scores, _reasons, _decision = merge._choose_winner(
            conn, ["old", "new"], by_id
        )
        self.assertEqual(winner, "new")
        self.assertEqual(losers, ["old"])


if __name__ == "__main__":
    unittest.main()
