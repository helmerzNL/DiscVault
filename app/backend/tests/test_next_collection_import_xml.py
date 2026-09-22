import os
import sys
import tempfile
import unittest
from pathlib import Path


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_plugins._collection_import_base import CollectionImportPlugin
from app.backend.next_plugins.import_clz_movies.plugin import SOURCE as CLZ_SOURCE


class ClzXmlImportTests(unittest.TestCase):
    def setUp(self):
        self.plugin = CollectionImportPlugin(CLZ_SOURCE)

    def read_movie(self, xml: str):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_file = Path(temp_dir) / "clz_movies.xml"
            source_file.write_text(xml, encoding="utf-8")
            rows = self.plugin.read_xml(source_file)
            self.assertEqual(len(rows), 1)
            return rows[0], self.plugin.normalize_row(rows[0], source_file, 1)

    def test_nested_clz_display_names_match_flat_csv_fields(self):
        row, movie = self.read_movie(
            """<?xml version="1.0" encoding="UTF-8"?>
<movieinfo>
  <movies>
    <movie>
      <title>The Shawshank Redemption</title>
      <releasedate>
        <year>
          <displayname>1994</displayname>
        </year>
      </releasedate>
      <format>
        <displayname>Blu-ray</displayname>
      </format>
      <edition>
        <displayname>Collector's Edition</displayname>
      </edition>
      <imdburl>https://www.imdb.com/title/tt0111161/?ref_=ref_ext_clz</imdburl>
      <barcode>5050582805967</barcode>
    </movie>
  </movies>
</movieinfo>
"""
        )

        self.assertEqual(row["title"], "The Shawshank Redemption")
        self.assertEqual(row["barcode"], "5050582805967")
        self.assertEqual(movie["title"], "The Shawshank Redemption")
        self.assertEqual(movie["year"], "1994")
        self.assertEqual(movie["format"], "Blu-ray")
        self.assertEqual(movie["edition"], "Collector's Edition")
        self.assertEqual(movie["imdbId"], "tt0111161")
        self.assertEqual(movie["barcode"], "5050582805967")

    def test_single_format_inside_formats_wrapper_is_unwrapped(self):
        _row, movie = self.read_movie(
            """<?xml version="1.0" encoding="UTF-8"?>
<movieinfo>
  <movies>
    <movie>
      <title>Dune</title>
      <formats>
        <format>
          <displayname>4K UHD</displayname>
        </format>
      </formats>
    </movie>
  </movies>
</movieinfo>
"""
        )

        self.assertEqual(movie["format"], "4K UHD")

    def test_single_text_bearing_descendant_is_used_without_display_name(self):
        _row, movie = self.read_movie(
            """<?xml version="1.0" encoding="UTF-8"?>
<movieinfo>
  <movies>
    <movie>
      <title>Back to the Future</title>
      <releasedate>
        <year>1985</year>
      </releasedate>
    </movie>
  </movies>
</movieinfo>
"""
        )

        self.assertEqual(movie["year"], "1985")

    def test_multi_valued_nested_structures_are_not_flattened(self):
        row, movie = self.read_movie(
            """<?xml version="1.0" encoding="UTF-8"?>
<movieinfo>
  <movies>
    <movie>
      <title>Dune</title>
      <formats>
        <format><displayname>4K UHD</displayname></format>
        <format><id>bluray</id></format>
      </formats>
      <languages>
        <language><displayname>English</displayname></language>
        <language><displayname>French</displayname></language>
      </languages>
    </movie>
  </movies>
</movieinfo>
"""
        )

        self.assertNotIn("formats", row)
        self.assertNotIn("format", row)
        self.assertNotIn("languages", row)
        self.assertNotIn("language", row)
        self.assertNotIn("format", movie)
        self.assertNotIn("language", movie)


if __name__ == "__main__":
    unittest.main()
