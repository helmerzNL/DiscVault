import os
import sys
import unittest
from unittest.mock import MagicMock, patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_worker


class ImportMovieIdentityTests(unittest.TestCase):
    def _lookup(
        self,
        item,
        *,
        public_id="",
        public_rows=(),
        barcode_rows=(),
        provider_rows=None,
        title_year_rows=(),
    ):
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        current_rows = []
        provider_rows = provider_rows or {}

        def execute(query, params):
            normalized = " ".join(query.split())
            if "WHERE public_id=%s" in normalized:
                rows = public_rows
            elif "WHERE barcode=%s" in normalized:
                rows = barcode_rows
            elif "WHERE mi.provider_id=%s" in normalized:
                rows = provider_rows.get(params[0], ())
            elif "WHERE lower(title)=lower(%s) AND year=%s" in normalized:
                rows = title_year_rows
            else:
                self.fail(f"Unexpected identity query: {normalized}")
            current_rows[:] = rows

        cursor.execute.side_effect = execute
        cursor.fetchone.side_effect = lambda: current_rows[0] if current_rows else None
        cursor.fetchall.side_effect = lambda: list(current_rows)
        connection = MagicMock()
        connection.cursor.return_value = cursor

        with patch.object(next_worker, "table_exists", return_value=True):
            result = next_worker.import_movie_existing_id(connection, item, public_id=public_id)
        return result, cursor

    def test_distinct_tv_seasons_do_not_match_on_a_shared_series_provider_id(self):
        for provider, item_key, identifier in (
            ("imdb", "imdbId", "tt9184820"),
            ("tmdb", "tmdbId", "60574"),
        ):
            with self.subTest(provider=provider):
                result, cursor = self._lookup(
                    {
                        "title": "Star Trek: Lower Decks: Season 2",
                        "year": "2021",
                        "format": "Blu-ray",
                        item_key: identifier,
                    },
                    provider_rows={
                        provider: (
                            {
                                "id": f"{provider}-season-1",
                                "title": "Star Trek: Lower Decks: Season 1",
                                "year": "2020",
                                "format": "Blu-ray",
                            },
                        )
                    },
                )

                self.assertIsNone(result)
                provider_query = next(
                    " ".join(call.args[0].split())
                    for call in cursor.execute.call_args_list
                    if "movie_identifiers" in call.args[0]
                )
                self.assertIn("m.title", provider_query)
                self.assertIn("m.year", provider_query)

    def test_shared_series_provider_id_selects_the_matching_season(self):
        result, _ = self._lookup(
            {
                "title": "Star Trek: Lower Decks: Season 2",
                "year": "2021",
                "format": "Blu-ray",
                "imdbId": "tt9184820",
            },
            provider_rows={
                "imdb": (
                    {
                        "id": "season-1",
                        "title": "Star Trek: Lower Decks: Season 1",
                        "year": "2020",
                        "format": "Blu-ray",
                    },
                    {
                        "id": "season-2",
                        "title": "Star Trek: Lower Decks: Season 2",
                        "year": "2021",
                        "format": "Blu-ray",
                    },
                )
            },
        )

        self.assertEqual(result, "season-2")

    def test_provider_id_still_matches_when_at_most_one_release_field_differs(self):
        cases = (
            ("same release", "Basic Instinct", "1992"),
            ("edition in imported title", "Basic Instinct 4K", "1992"),
            ("disc year in import", "Basic Instinct", "2022"),
            ("missing imported year", "Basic Instinct", ""),
        )
        for label, title, year in cases:
            with self.subTest(label=label):
                result, _ = self._lookup(
                    {
                        "title": title,
                        "year": year,
                        "format": "Blu-ray",
                        "imdbId": "tt0103772",
                    },
                    provider_rows={
                        "imdb": (
                            {
                                "id": "basic-instinct",
                                "title": "Basic Instinct",
                                "year": "1992",
                                "format": "Blu-ray",
                            },
                        )
                    },
                )

                self.assertEqual(result, "basic-instinct")

    def test_provider_id_matching_keeps_physical_format_compatibility(self):
        stored = {
            "id": "basic-instinct",
            "title": "Basic Instinct",
            "year": "1992",
            "format": "Blu-ray",
        }
        compatible, _ = self._lookup(
            {"title": "Basic Instinct", "year": "1992", "format": "BD", "imdbId": "tt0103772"},
            provider_rows={"imdb": (stored,)},
        )
        incompatible, _ = self._lookup(
            {"title": "Basic Instinct", "year": "1992", "format": "DVD", "imdbId": "tt0103772"},
            provider_rows={"imdb": (stored,)},
            title_year_rows=(stored,),
        )

        self.assertEqual(compatible, "basic-instinct")
        self.assertIsNone(incompatible)

    def test_public_id_remains_the_highest_precedence_identity(self):
        result, cursor = self._lookup(
            {
                "title": "Star Trek: Lower Decks: Season 2",
                "year": "2021",
                "format": "DVD",
                "barcode": "barcode-season-2",
                "imdbId": "tt9184820",
            },
            public_id="import-source-season-2",
            public_rows=({"id": "public-season-2"},),
            barcode_rows=({"id": "barcode-season-2"},),
        )

        self.assertEqual(result, "public-season-2")
        self.assertEqual(cursor.execute.call_count, 1)

    def test_barcode_remains_ahead_of_provider_identity(self):
        result, cursor = self._lookup(
            {
                "title": "Star Trek: Lower Decks: Season 2",
                "year": "2021",
                "format": "DVD",
                "barcode": "barcode-season-2",
                "imdbId": "tt9184820",
            },
            public_id="import-source-season-2",
            barcode_rows=({"id": "barcode-season-2"},),
            provider_rows={
                "imdb": (
                    {
                        "id": "provider-season-1",
                        "title": "Star Trek: Lower Decks: Season 1",
                        "year": "2020",
                        "format": "DVD",
                    },
                )
            },
        )

        self.assertEqual(result, "barcode-season-2")
        self.assertEqual(cursor.execute.call_count, 2)


if __name__ == "__main__":
    unittest.main()
