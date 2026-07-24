"""Tests for the canonical backend dedup implementation (no live DB)."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from app.backend import versioning
from app.backend.scripts import sync_dedup_merge as merge


class NormalizationParityTests(unittest.TestCase):
    """The merge script must group with the same rules the live ladder matches."""

    def test_title_normalization_matches_expectations(self):
        self.assertEqual(merge.normalize_title("The Matrix"), "matrix")
        self.assertEqual(merge.normalize_title("Amélie"), "amelie")
        self.assertEqual(
            merge.normalize_title("WALL·E"), merge.normalize_title("wall e")
        )
        self.assertEqual(merge.normalize_title("Die Hard"), "die hard")
        self.assertEqual(merge.normalize_title("De Aanslag"), "de aanslag")
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

    def test_titleyear_tier_allows_missing_barcode_on_one_side(self):
        a = {
            "id": "a",
            "barcode": "8713045201470",
            "title": "What Women Want",
            "year": None,
            "format": "DVD",
            "edition": "",
        }
        b = {
            "id": "b",
            "barcode": None,
            "title": "What Women Want",
            "year": None,
            "format": "DVD",
            "edition": "",
        }
        groups = self._detect_with([a, b], {})
        self.assertEqual(groups["titleYear"], {("what women want", "", "dvd"): ["a", "b"]})

    def test_titleyear_tier_blocks_box_set_members_from_standalone_copies(self):
        cases = (
            ("Jurassic Park", "1993"),
            ("Jurassic Park III", "2001"),
            ("The Dark Knight Rises", "2012"),
            ("The Lost World: Jurassic Park", "1997"),
        )
        for index, (title, year) in enumerate(cases):
            with self.subTest(title=title):
                member = {
                    "id": f"member-{index}",
                    "barcode": None,
                    "title": title,
                    "year": year,
                    "format": "4K_UHD",
                    "edition": "Box-set member",
                    "container_ids": [f"box-{index}"],
                }
                standalone = {
                    "id": f"standalone-{index}",
                    "barcode": None,
                    "title": title,
                    "year": year,
                    "format": "4K UHD",
                    "edition": None,
                    "container_ids": [],
                }
                groups = self._detect_with([member, standalone], {})
                self.assertEqual(groups["titleYear"], {})

    def test_titleyear_tier_blocks_members_of_different_containers(self):
        first = {
            "id": "first",
            "barcode": None,
            "title": "Jurassic Park",
            "year": "1993",
            "format": "4K UHD",
            "edition": None,
            "container_ids": ["box-a"],
        }
        second = {**first, "id": "second", "container_ids": ["box-b"]}
        groups = self._detect_with([first, second], {})
        self.assertEqual(groups["titleYear"], {})

    def test_titleyear_tier_allows_same_container_duplicate_stubs(self):
        first = {
            "id": "first",
            "barcode": None,
            "title": "Jurassic Park",
            "year": "1993",
            "format": "4K UHD",
            "edition": None,
            "container_ids": ["box-a"],
        }
        second = {**first, "id": "second", "format": "4K_UHD"}
        groups = self._detect_with([first, second], {})
        self.assertEqual(
            groups["titleYear"],
            {("jurassic park", "1993", "ultra_hd_blu_ray"): ["first", "second"]},
        )

    def test_titleyear_tier_blocks_partial_edition_inside_same_container(self):
        first = {
            "id": "first",
            "barcode": None,
            "title": "Jurassic Park",
            "year": "1993",
            "format": "4K UHD",
            "edition": "Box-set member",
            "container_ids": ["box-a"],
        }
        second = {**first, "id": "second", "edition": None}
        groups = self._detect_with([first, second], {})
        self.assertEqual(groups["titleYear"], {})

    def test_titleyear_tier_blocks_conflicting_explicit_editions(self):
        first = {
            "id": "first",
            "barcode": None,
            "title": "Blade Runner",
            "year": "1982",
            "format": "4K UHD",
            "edition": "Final Cut",
        }
        second = {**first, "id": "second", "edition": "Theatrical Cut"}
        groups = self._detect_with([first, second], {})
        self.assertEqual(groups["titleYear"], {})

    def test_titleyear_tier_preserves_bourne_missing_edition_adoption(self):
        complete = {
            "id": "complete",
            "barcode": None,
            "title": "The Bourne Identity",
            "year": "2002",
            "format": "4K UHD",
            "edition": "The Bourne Identity",
        }
        stub = {**complete, "id": "stub", "edition": None}
        groups = self._detect_with([complete, stub], {})
        self.assertEqual(
            groups["titleYear"],
            {("bourne identity", "2002", "ultra_hd_blu_ray"): ["complete", "stub"]},
        )

    def test_titleyear_tier_preserves_greatest_showman_stub_adoption(self):
        complete = {
            "id": "complete",
            "barcode": None,
            "title": "The Greatest Showman",
            "year": "2017",
            "format": "4K UHD",
            "edition": "Standalone release",
        }
        first_stub = {**complete, "id": "stub-a", "edition": None}
        second_stub = {**complete, "id": "stub-b", "edition": ""}
        groups = self._detect_with([complete, first_stub, second_stub], {})
        self.assertEqual(
            groups["titleYear"],
            {
                ("greatest showman", "2017", "ultra_hd_blu_ray"): [
                    "complete",
                    "stub-a",
                    "stub-b",
                ]
            },
        )


class ReportProvenanceTests(unittest.TestCase):
    def test_immutable_image_provenance_overrides_mutable_runtime_values(self):
        with mock.patch.dict(
            "os.environ",
            {
                "DISCVAULT_IMAGE_VERSION": "26.6.33",
                "DISCVAULT_IMAGE_SHA": "immutable-sha",
                "DISCVAULT_BACKEND_VERSION": "next-dev",
                "BUILD_VERSION": "next-dev",
                "DISCVAULT_BUILD_SHA": "runtime-sha",
                "BUILD_SHA": "runtime-sha",
            },
            clear=False,
        ):
            metadata = merge._report_metadata()

        self.assertEqual(metadata["backend_version"], "26.6.33")
        self.assertEqual(metadata["script_commit"], "immutable-sha")

    def test_shallow_backend_image_path_has_safe_version_fallbacks(self):
        with mock.patch.object(versioning, "__file__", "/app/versioning.py"):
            candidates = versioning._version_file_candidates()

        self.assertTrue(candidates)
        self.assertTrue(all(candidate.name == "VERSION" for candidate in candidates))

    def test_gunicorn_top_level_package_import(self):
        backend_dir = Path(__file__).resolve().parents[2] / "backend"
        subprocess.run(
            [sys.executable, "-c", "import scripts.sync_dedup_merge"],
            cwd=backend_dir,
            check=True,
            capture_output=True,
            text=True,
        )


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
    def test_candidate_state_hash_changes_for_nonempty_user_data_edits(self):
        first = {"id": "movie-1", "notes": "first"}
        second = {"id": "movie-1", "notes": "second"}
        self.assertNotEqual(
            merge._movie_state_hash(first),
            merge._movie_state_hash(second),
        )

    def test_candidate_state_hash_changes_for_container_membership_edits(self):
        canonical_state = {"id": "movie-1", "notes": None}
        first = {
            "canonical_state": canonical_state,
            "container_ids": ["container-a"],
        }
        second = {
            "canonical_state": canonical_state,
            "container_ids": ["container-b"],
        }
        self.assertNotEqual(
            merge._movie_state_hash(first),
            merge._movie_state_hash(second),
        )

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
