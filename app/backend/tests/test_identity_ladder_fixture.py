"""Fail-closed coverage for the shared identity-ladder fixture categories."""

from __future__ import annotations

import copy
from datetime import datetime
import json
from pathlib import Path
import unittest
from unittest import mock

try:
    from .. import next_app, next_metadata
    from ..dedup_identity import (
        extract_identity_identifiers,
        normalize_edition_identity,
        select_movievault_identifier,
        select_tmdb_identifier,
        title_year_identity_compatible,
    )
    from ..identity_fixture_contract import (
        IDENTITY_FIXTURE_METADATA_KEYS,
        IDENTITY_FIXTURE_RUNNERS,
    )
    from ..scripts import sync_dedup_merge as merge
except ImportError:  # pragma: no cover - backend working-directory CI imports
    import next_app
    import next_metadata
    from dedup_identity import (
        extract_identity_identifiers,
        normalize_edition_identity,
        select_movievault_identifier,
        select_tmdb_identifier,
        title_year_identity_compatible,
    )
    from identity_fixture_contract import (
        IDENTITY_FIXTURE_METADATA_KEYS,
        IDENTITY_FIXTURE_RUNNERS,
    )
    from scripts import sync_dedup_merge as merge


FIXTURE_PATH = (
    Path(__file__).resolve().parents[3] / "sync" / "fixtures" / "identity-ladder.json"
)


class _ScoreCursor:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, _sql, _params=()):
        self._row = {"n": 0}

    def fetchone(self):
        return self._row


class _ScoreConnection:
    def cursor(self):
        return _ScoreCursor()


class IdentityLadderFixtureRunner:
    """Dispatch every declared fixture category to its production-backed runner."""

    SUPPORTED_CATEGORIES = tuple(IDENTITY_FIXTURE_RUNNERS)
    ALLOWED_METADATA_KEYS = IDENTITY_FIXTURE_METADATA_KEYS
    DEFAULT_RUNNERS = dict(IDENTITY_FIXTURE_RUNNERS)

    def __init__(self, payload, *, runners=None):
        self.payload = payload
        self.runners = dict(self.DEFAULT_RUNNERS if runners is None else runners)

    @classmethod
    def load(cls, path=FIXTURE_PATH):
        with Path(path).open(encoding="utf-8") as fixture_file:
            return cls(json.load(fixture_file))

    def validate_schema_and_dispatch(self):
        if not isinstance(self.payload, dict):
            raise AssertionError("Identity fixture root must be a JSON object.")

        supported = set(self.SUPPORTED_CATEGORIES)
        allowed = supported | set(self.ALLOWED_METADATA_KEYS)
        unknown = set(self.payload) - allowed
        if unknown:
            raise AssertionError(
                "Unknown identity fixture top-level key(s): "
                f"{sorted(unknown)}. Add each test category to "
                "SUPPORTED_CATEGORIES with a runner, or explicitly document it "
                "in ALLOWED_METADATA_KEYS."
            )

        missing_categories = supported - set(self.payload)
        if missing_categories:
            raise AssertionError(
                "Missing required identity fixture category/categories: "
                f"{sorted(missing_categories)}. Restore the category and its cases."
            )

        missing_runners = supported - set(self.runners)
        extra_runners = set(self.runners) - supported
        if missing_runners or extra_runners:
            raise AssertionError(
                "Identity fixture runner registry does not exactly cover declared "
                f"categories; missing={sorted(missing_runners)}, "
                f"extra={sorted(extra_runners)}."
            )
        invalid_runners = sorted(
            category
            for category, runner_name in self.runners.items()
            if not isinstance(runner_name, str)
            or not callable(getattr(self, runner_name, None))
        )
        if invalid_runners:
            raise AssertionError(
                "Identity fixture categories have no callable runner: "
                f"{invalid_runners}. Register a runner method for every category."
            )

        for category in self.SUPPORTED_CATEGORIES:
            cases = self.payload[category]
            if not isinstance(cases, list) or not cases:
                raise AssertionError(
                    f"Identity fixture category '{category}' must be a non-empty list."
                )
            case_ids = [
                item.get("id") if isinstance(item, dict) else None for item in cases
            ]
            if any(not case_id for case_id in case_ids):
                raise AssertionError(
                    f"Every case in identity fixture category '{category}' needs an id."
                )
            if len(case_ids) != len(set(case_ids)):
                raise AssertionError(
                    f"Identity fixture category '{category}' contains duplicate ids."
                )

    def run_all(self):
        self.validate_schema_and_dispatch()
        executed = {}
        for category in self.SUPPORTED_CATEGORIES:
            runner = getattr(self, self.runners[category])
            executed_ids = runner(self.payload[category])
            expected_ids = [case["id"] for case in self.payload[category]]
            if executed_ids != expected_ids:
                raise AssertionError(
                    f"Runner for '{category}' did not dispatch every case in order; "
                    f"expected={expected_ids}, executed={executed_ids}."
                )
            executed[category] = executed_ids
        return executed

    @staticmethod
    def _record_barcode(record):
        if record.get("barcode_claimed_in_batch") is True:
            return None
        return record.get("barcode")

    @classmethod
    def _barcode_conflicts(cls, left, right):
        left_value = merge.normalize_barcode(cls._record_barcode(left))
        right_value = merge.normalize_barcode(cls._record_barcode(right))
        return bool(left_value and right_value and left_value != right_value)

    @classmethod
    def _tmdb_matches(cls, left, right):
        left_id = str(left.get("tmdb_id") or "").strip()
        right_id = str(right.get("tmdb_id") or "").strip()
        if not left_id or not right_id or left_id != right_id:
            return False
        left_format = merge.normalize_format(left.get("format"))
        right_format = merge.normalize_format(right.get("format"))
        if not left_format or left_format != right_format:
            return False
        if normalize_edition_identity(left.get("edition")) != normalize_edition_identity(
            right.get("edition")
        ):
            return False
        if cls._barcode_conflicts(left, right):
            return False
        return not merge._tmdb_sanity_conflicts(left, right)

    @classmethod
    def _lower_tiers_blocked(cls, left, right):
        if cls._barcode_conflicts(left, right):
            return True
        left_id = str(left.get("tmdb_id") or "").strip()
        right_id = str(right.get("tmdb_id") or "").strip()
        if not left_id or not right_id:
            return False
        return not cls._tmdb_matches(left, right)

    @classmethod
    def _title_year_matches(cls, left, right):
        if cls._lower_tiers_blocked(left, right):
            return False
        left_title = merge.normalize_title(left.get("title"))
        right_title = merge.normalize_title(right.get("title"))
        if not left_title or left_title != right_title:
            return False
        left_year = merge._year_key(left.get("year"))
        right_year = merge._year_key(right.get("year"))
        if not left_year or left_year != right_year:
            return False
        left_format = merge.normalize_format(left.get("format"))
        right_format = merge.normalize_format(right.get("format"))
        if not left_format or left_format != right_format:
            return False
        return title_year_identity_compatible(
            left_edition=left.get("edition"),
            right_edition=right.get("edition"),
        )

    @classmethod
    def _classify_ladder_case(cls, case):
        left = case["a"]
        right = case["b"]
        marker = "fixture-match"

        left_token = str(left.get("client_id") or "").strip()
        right_token = str(right.get("client_id") or "").strip()
        same_token = bool(left_token and left_token == right_token)

        left_barcode = merge.normalize_barcode(cls._record_barcode(left))
        right_barcode = merge.normalize_barcode(cls._record_barcode(right))
        same_barcode = bool(left_barcode and left_barcode == right_barcode)
        lower_blocked = cls._lower_tiers_blocked(left, right)

        callbacks = {
            "find_by_client_id": lambda: marker if same_token else None,
            "find_by_barcode": lambda: marker if same_barcode else None,
            "find_by_tmdb_edition": lambda: (
                marker if not lower_blocked and cls._tmdb_matches(left, right) else None
            ),
        }
        if case["context"] == "create":
            return next_app.match_existing_movie(
                persistent_client_id=left_token or None,
                batch_claimed_client_id=None,
                barcode_normalized=left_barcode,
                barcode_claimed_in_batch=left.get("barcode_claimed_in_batch") is True,
                tmdb_id=left.get("tmdb_id"),
                fmt=left.get("format"),
                duplicate_copy=False,
                **callbacks,
            )
        if case["context"] == "reconcile":
            return next_app.match_reconcile_item(
                persistent_client_id=left_token or None,
                barcode_normalized=left_barcode,
                tmdb_id=left.get("tmdb_id"),
                fmt=left.get("format"),
                title=left.get("title"),
                year=left.get("year"),
                find_by_title_year=lambda: (
                    marker if cls._title_year_matches(left, right) else None
                ),
                **callbacks,
            )
        raise AssertionError(
            f"[{case['id']}] unknown identity fixture context {case['context']!r}."
        )

    @classmethod
    def _run_ladder_cases(cls, cases):
        executed = []
        for case in cases:
            movie_id, matched_by = cls._classify_ladder_case(case)
            expected = case["expected"]
            if expected == "match":
                if movie_id is None or matched_by != case["match_tier"]:
                    raise AssertionError(
                        f"[{case['id']}] expected {case['match_tier']} match, "
                        f"got movie_id={movie_id!r}, matchedBy={matched_by!r}."
                    )
            elif expected in {"no_match", "no_match_or_candidate"}:
                if movie_id is not None:
                    raise AssertionError(
                        f"[{case['id']}] expected no firm match, got {matched_by!r}."
                    )
            else:
                raise AssertionError(
                    f"[{case['id']}] unknown expected value {expected!r}."
                )
            executed.append(case["id"])
        return executed

    @staticmethod
    def _run_merge_winner_cases(cases):
        executed = []
        for case in cases:
            by_id = {}
            for record in case["records"]:
                by_id[record["id"]] = {
                    "id": record["id"],
                    "created_at": datetime.fromisoformat(
                        record["created_at"].replace("Z", "+00:00")
                    ),
                    "_signals": {
                        "has_own_artwork": record["own_artwork"],
                        "has_locked_fields": record["locked_fields"],
                        "watch_history_count": record["watch_history"],
                    },
                }
            winner, _losers, _scores, _reasons, _decision = merge._choose_winner(
                _ScoreConnection(), list(by_id), by_id
            )
            if winner != case["expected_winner"]:
                raise AssertionError(
                    f"[{case['id']}] expected winner {case['expected_winner']!r}, "
                    f"got {winner!r}."
                )
            executed.append(case["id"])
        return executed

    @staticmethod
    def _run_identifier_cases(cases):
        executed = []
        for case in cases:
            actual = extract_identity_identifiers(case["identifiers"])
            expected = {
                "tmdb": case.get("expected_tmdb_id"),
                "movievault": case.get("expected_movievault_id"),
            }
            if actual != expected:
                raise AssertionError(
                    f"[{case['id']}] expected identifiers {expected!r}, "
                    f"got {actual!r}."
                )
            executed.append(case["id"])
        return executed


class FixtureCategoryCoverageTests(unittest.TestCase):
    def setUp(self):
        self.runner = IdentityLadderFixtureRunner.load()

    def test_all_categories_and_every_case_execute(self):
        executed = self.runner.run_all()
        self.assertEqual(
            {category: len(case_ids) for category, case_ids in executed.items()},
            {"cases": 19, "merge_winner_cases": 3, "identifier_cases": 5},
        )
        self.assertEqual(
            executed["identifier_cases"],
            [
                "ident-tmdb-movie-id",
                "ident-case-insensitive-provider",
                "ident-prefers-movie-id",
                "ident-blank-is-absent",
                "ident-no-tmdb-provider",
            ],
        )

    def test_unknown_top_level_category_is_rejected(self):
        payload = copy.deepcopy(self.runner.payload)
        payload["future_cases"] = [{"id": "not-dispatched"}]
        with self.assertRaisesRegex(
            AssertionError, "Unknown identity fixture top-level key"
        ):
            IdentityLadderFixtureRunner(payload).run_all()

    def test_missing_required_category_is_rejected(self):
        payload = copy.deepcopy(self.runner.payload)
        payload.pop("identifier_cases")
        with self.assertRaisesRegex(
            AssertionError, "Missing required identity fixture category"
        ):
            IdentityLadderFixtureRunner(payload).run_all()

    def test_declared_category_without_runner_is_rejected(self):
        runners = dict(IdentityLadderFixtureRunner.DEFAULT_RUNNERS)
        runners.pop("identifier_cases")
        with self.assertRaisesRegex(
            AssertionError, "runner registry does not exactly cover"
        ):
            IdentityLadderFixtureRunner(
                copy.deepcopy(self.runner.payload), runners=runners
            ).run_all()

    def test_declared_category_with_unknown_runner_is_rejected(self):
        runners = dict(IdentityLadderFixtureRunner.DEFAULT_RUNNERS)
        runners["identifier_cases"] = "_run_missing_category"
        with self.assertRaisesRegex(AssertionError, "no callable runner"):
            IdentityLadderFixtureRunner(
                copy.deepcopy(self.runner.payload), runners=runners
            ).run_all()

    def test_empty_category_is_rejected(self):
        payload = copy.deepcopy(self.runner.payload)
        payload["identifier_cases"] = []
        with self.assertRaisesRegex(AssertionError, "must be a non-empty list"):
            IdentityLadderFixtureRunner(payload).run_all()

    def test_incomplete_category_dispatch_is_rejected(self):
        class IncompleteIdentifierRunner(IdentityLadderFixtureRunner):
            @staticmethod
            def _run_identifier_cases(cases):
                return [case["id"] for case in cases[:-1]]

        with self.assertRaisesRegex(AssertionError, "did not dispatch every case"):
            IncompleteIdentifierRunner(copy.deepcopy(self.runner.payload)).run_all()


class TmdbIdentifierSelectionTests(unittest.TestCase):
    def test_extracts_tmdb_and_movievault_identifiers(self):
        self.assertEqual(
            extract_identity_identifiers(
                [
                    {
                        "provider_id": "movievault_26",
                        "identifier_type": "movie_id",
                        "identifier": "mv-9",
                    },
                    {
                        "provider_id": "tmdb",
                        "identifier_type": "movie_id",
                        "identifier": "157336",
                    },
                ]
            ),
            {"tmdb": "157336", "movievault": "mv-9"},
        )

    def test_provider_and_identifier_type_are_case_insensitive(self):
        self.assertEqual(
            select_tmdb_identifier(
                [
                    {
                        "provider_id": "TMDB",
                        "identifier_type": "MOVIE_ID",
                        "identifier": "603",
                    }
                ]
            ),
            "603",
        )

    def test_movie_id_is_preferred(self):
        self.assertEqual(
            select_tmdb_identifier(
                [
                    {
                        "provider_id": "tmdb",
                        "identifier_type": "collection_id",
                        "identifier": "999",
                    },
                    {
                        "provider_id": "tmdb",
                        "identifier_type": "movie_id",
                        "identifier": "27205",
                    },
                ]
            ),
            "27205",
        )

    def test_blank_identifier_is_absent(self):
        self.assertIsNone(
            select_tmdb_identifier(
                [
                    {
                        "provider_id": "tmdb",
                        "identifier_type": "movie_id",
                        "identifier": " \t ",
                    }
                ]
            )
        )

    def test_blank_identifier_does_not_hide_later_movie_id(self):
        self.assertEqual(
            select_tmdb_identifier(
                [
                    {
                        "provider_id": "tmdb",
                        "identifier_type": "movie_id",
                        "identifier": "   ",
                    },
                    {
                        "provider_id": "tmdb",
                        "identifier_type": "movie_id",
                        "identifier": "603",
                    },
                ]
            ),
            "603",
        )

    def test_first_tmdb_row_is_deterministic_fallback(self):
        self.assertEqual(
            select_tmdb_identifier(
                [
                    {
                        "provider_id": "tmdb",
                        "identifier_type": "collection_id",
                        "identifier": "420",
                    },
                    {
                        "provider_id": "tmdb",
                        "identifier_type": "person_id",
                        "identifier": "31",
                    },
                ]
            ),
            "420",
        )

    def test_provider_storage_key_is_not_whitespace_normalized(self):
        self.assertIsNone(
            select_tmdb_identifier(
                [
                    {
                        "provider_id": " TMDB ",
                        "identifier_type": "movie_id",
                        "identifier": "603",
                    }
                ]
            )
        )

    def test_movievault_provider_and_type_are_case_insensitive(self):
        self.assertEqual(
            select_movievault_identifier(
                [
                    {
                        "provider_id": "MOVIEVAULT_26",
                        "identifier_type": "MOVIE_ID",
                        "identifier": " mv-9 ",
                    }
                ]
            ),
            "mv-9",
        )


class _IdentifierCursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, _params=()):
        self.sql = " ".join(sql.split())

    def fetchall(self):
        return list(self.rows)


class _IdentifierConnection:
    def __init__(self, rows):
        self.cursor_instance = _IdentifierCursor(rows)

    def cursor(self):
        return self.cursor_instance


class MetadataIdentifierExtractionIntegrationTests(unittest.TestCase):
    def test_metadata_path_uses_shared_identity_extractor(self):
        connection = _IdentifierConnection(
            [
                {
                    "provider_id": "TMDB",
                    "identifier_type": "MOVIE_ID",
                    "identifier": "603",
                },
                {
                    "provider_id": "movievault_26",
                    "identifier_type": "movie_id",
                    "identifier": " mv-9 ",
                },
                {
                    "provider_id": "tmdb",
                    "identifier_type": "collection_id",
                    "identifier": "99999",
                },
            ]
        )

        with mock.patch.object(next_metadata, "table_exists", return_value=True):
            values = next_metadata.movie_identifiers(connection, "movie-a")

        self.assertEqual(values["tmdb_id"], "603")
        self.assertEqual(values["tmdbId"], "603")
        self.assertEqual(values["movievault_id"], "mv-9")
        self.assertEqual(values["movieVaultId"], "mv-9")
        self.assertIn(
            "ORDER BY provider_id, identifier_type, identifier",
            connection.cursor_instance.sql,
        )


class MergeTmdbExtractionIntegrationTests(unittest.TestCase):
    def test_merge_ladder_uses_shared_selector_for_stored_rows(self):
        connection = _IdentifierConnection(
            [
                {
                    "movie_id": "movie-a",
                    "provider_id": "TMDB",
                    "identifier_type": "collection_id",
                    "identifier": "999",
                },
                {
                    "movie_id": "movie-a",
                    "provider_id": "TMDB",
                    "identifier_type": "MOVIE_ID",
                    "identifier": "603",
                },
                {
                    "movie_id": "movie-b",
                    "provider_id": "tmdb",
                    "identifier_type": "movie_id",
                    "identifier": "   ",
                },
                {
                    "movie_id": "movie-c",
                    "provider_id": "tmdb",
                    "identifier_type": "movie_id",
                    "identifier": "   ",
                },
                {
                    "movie_id": "movie-c",
                    "provider_id": "tmdb",
                    "identifier_type": "movie_id",
                    "identifier": "27205",
                },
            ]
        )

        self.assertEqual(
            merge._fetch_tmdb_ids(connection),
            {"movie-a": "603", "movie-c": "27205"},
        )
        self.assertIn(
            "WHERE lower(provider_id) = 'tmdb'",
            connection.cursor_instance.sql,
        )
        self.assertIn(
            "ORDER BY movie_id, provider_id, identifier_type, identifier",
            connection.cursor_instance.sql,
        )


if __name__ == "__main__":
    unittest.main()
