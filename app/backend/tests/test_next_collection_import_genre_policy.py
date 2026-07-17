import os
import sys
import unittest
from pathlib import Path


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_plugins._collection_import_base import CollectionImportPlugin
from app.backend.next_worker import import_movie_metadata


class CollectionImportGenreExclusionTests(unittest.TestCase):
    """Genres are TMDB-only: collection imports must never write genre data,
    even when the source file has an explicit Genre column."""

    def setUp(self):
        self.plugin = CollectionImportPlugin(
            {
                "id": "import_test_source",
                "name": "Test Source",
                "sourceKind": "test_export",
                "defaultPath": "/data/import/test",
                "aliases": {},
            }
        )

    def test_normalize_row_never_carries_a_genre_field(self):
        movie = self.plugin.normalize_row(
            {"Title": "Heat", "Genre": "Action, Crime, Thriller", "Year": "1995"},
            Path("collection.csv"),
            0,
        )
        self.assertTrue(movie)
        self.assertNotIn("genre", movie)

    def test_normalize_row_ignores_explicit_genre_column_mapping(self):
        movie = self.plugin.normalize_row(
            {"Title": "Heat", "MyGenreColumn": "Action"},
            Path("collection.csv"),
            0,
            column_mapping={"genre": "MyGenreColumn"},
        )
        self.assertTrue(movie)
        self.assertNotIn("genre", movie)

    def test_import_movie_metadata_drops_genre_even_if_item_carries_it(self):
        # Defensive: even if an older/foreign import item dict still has a
        # "genre" key, the worker must not persist it into movie metadata.
        metadata = import_movie_metadata(
            {"genre": "Action", "director": "Michael Mann", "sourceProvider": "import_test_source"},
            "import_test_source",
        )
        self.assertNotIn("genre", metadata)
        self.assertEqual(metadata.get("director"), "Michael Mann")


if __name__ == "__main__":
    unittest.main()
