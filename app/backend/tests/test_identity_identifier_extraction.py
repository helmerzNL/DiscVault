"""Production-backed tests for server identity identifier extraction."""

from __future__ import annotations

import unittest
from unittest import mock

try:
    from .. import next_metadata
    from ..dedup_identity import (
        extract_identity_identifiers,
        select_movievault_identifier,
        select_tmdb_identifier,
    )
    from ..scripts import sync_dedup_merge as merge
except ImportError:  # pragma: no cover - backend working-directory CI imports
    import next_metadata
    from dedup_identity import (
        extract_identity_identifiers,
        select_movievault_identifier,
        select_tmdb_identifier,
    )
    from scripts import sync_dedup_merge as merge


IDENTIFIER_EXPECTATIONS = (
    {
        "id": "ident-tmdb-movie-id",
        "identifiers": [
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
            {
                "provider_id": "imdb",
                "identifier_type": "title_id",
                "identifier": "tt0816692",
            },
        ],
        "expected": {"tmdb": "157336", "movievault": "mv-9"},
    },
    {
        "id": "ident-case-insensitive-provider",
        "identifiers": [
            {
                "provider_id": "TMDB",
                "identifier_type": "MOVIE_ID",
                "identifier": "603",
            }
        ],
        "expected": {"tmdb": "603", "movievault": None},
    },
    {
        "id": "ident-prefers-movie-id",
        "identifiers": [
            {
                "provider_id": "tmdb",
                "identifier_type": "collection_id",
                "identifier": "99999",
            },
            {
                "provider_id": "tmdb",
                "identifier_type": "movie_id",
                "identifier": "157336",
            },
        ],
        "expected": {"tmdb": "157336", "movievault": None},
    },
    {
        "id": "ident-blank-is-absent",
        "identifiers": [
            {
                "provider_id": "tmdb",
                "identifier_type": "movie_id",
                "identifier": " ",
            }
        ],
        "expected": {"tmdb": None, "movievault": None},
    },
    {
        "id": "ident-no-tmdb-provider",
        "identifiers": [
            {
                "provider_id": "wikidata",
                "identifier_type": "item",
                "identifier": "Q42",
            }
        ],
        "expected": {"tmdb": None, "movievault": None},
    },
)


class ProductionIdentifierExtractionTests(unittest.TestCase):
    def test_all_five_authoritative_expectations_execute(self):
        executed = []
        for case in IDENTIFIER_EXPECTATIONS:
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    extract_identity_identifiers(case["identifiers"]),
                    case["expected"],
                )
            executed.append(case["id"])

        self.assertEqual(
            executed,
            [
                "ident-tmdb-movie-id",
                "ident-case-insensitive-provider",
                "ident-prefers-movie-id",
                "ident-blank-is-absent",
                "ident-no-tmdb-provider",
            ],
        )
        self.assertEqual(len(executed), 5)

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

    def test_first_nonblank_tmdb_row_is_deterministic_fallback(self):
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

    def test_the_v2_generation_is_selected(self):
        self.assertEqual(
            select_movievault_identifier(
                [
                    {
                        "provider_id": "movievault_v2",
                        "identifier_type": "movie_id",
                        "identifier": " 4f1c7d2e-0000-4000-8000-000000000001 ",
                    }
                ]
            ),
            "4f1c7d2e-0000-4000-8000-000000000001",
        )

    def test_v2_wins_over_an_older_generation_regardless_of_row_order(self):
        """The generations are separate namespaces, so the newest must win.

        Callers read `movie_identifiers` ordered by `provider_id`, which sorts
        "movievault_26" before "movievault_v2". A first-match selector would
        therefore hand a v4 catalog lookup an id from a namespace it cannot
        resolve, and the movie would stay unreachable.
        """
        older_first = [
            {
                "provider_id": "movievault_26",
                "identifier_type": "movie_id",
                "identifier": "mv-9",
            },
            {
                "provider_id": "movievault_v2",
                "identifier_type": "movie_id",
                "identifier": "4f1c7d2e-0000-4000-8000-000000000001",
            },
        ]
        self.assertEqual(
            select_movievault_identifier(older_first),
            "4f1c7d2e-0000-4000-8000-000000000001",
        )
        self.assertEqual(
            select_movievault_identifier(list(reversed(older_first))),
            "4f1c7d2e-0000-4000-8000-000000000001",
        )

    def test_an_older_generation_still_wins_when_v2_is_blank(self):
        self.assertEqual(
            select_movievault_identifier(
                [
                    {
                        "provider_id": "movievault_v2",
                        "identifier_type": "movie_id",
                        "identifier": "   ",
                    },
                    {
                        "provider_id": "movievault_26",
                        "identifier_type": "movie_id",
                        "identifier": "mv-9",
                    },
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
