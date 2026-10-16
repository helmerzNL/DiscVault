"""CSV parsing coverage shared by all collection import plugins."""

import os
import sys
import tempfile
import unittest
from pathlib import Path


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_plugins._collection_import_base import CollectionImportPlugin


SOURCE = {
    "id": "import_csv_test",
    "name": "CSV Test",
    "sourceKind": "csv_test",
    "defaultPath": "/data/import/test",
    "aliases": {},
}


class CollectionImportCsvTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.plugin = CollectionImportPlugin(SOURCE)

    def write_bytes(self, name: str, content: str, *, bom: bool = False) -> Path:
        path = self.root / name
        encoding = "utf-8-sig" if bom else "utf-8"
        path.write_bytes(content.encode(encoding))
        return path

    def test_rfc4180_doubled_quotes_are_decoded_after_the_sniffer_sample(self):
        lines = ["Title,Plot,Barcode"]
        lines.extend(
            f"Filler {index},Plain plot {index},{index:013d}"
            for index in range(300)
        )
        lines.append(
            '"Wayne\'s ""World""","A movie with ""excellent"" dialogue",0123456789012'
        )
        path = self.write_bytes("collection.csv", "\r\n".join(lines) + "\r\n")

        rows = self.plugin.read_rows(path)

        self.assertEqual(len(rows), 301)
        self.assertEqual(rows[-1]["Title"], 'Wayne\'s "World"')
        self.assertEqual(rows[-1]["Plot"], 'A movie with "excellent" dialogue')
        self.assertEqual(rows[-1]["Barcode"], "0123456789012")

    def test_multiline_quoted_fields_preserve_their_embedded_newline(self):
        path = self.write_bytes(
            "collection.csv",
            'Title,Plot,Year\r\nHeat,"First line\r\nSecond line",1995\r\n',
        )

        rows = self.plugin.read_rows(path)

        self.assertEqual(
            rows,
            [{"Title": "Heat", "Plot": "First line\r\nSecond line", "Year": "1995"}],
        )

    def test_supported_delimiters_keep_quoted_delimiter_text(self):
        cases = (
            ("comma.csv", ",", "Crime, drama"),
            ("semicolon.csv", ";", "Crime; drama"),
            ("pipe.csv", "|", "Crime | drama"),
            ("tab.tsv", "\t", "Crime\t drama"),
        )
        for filename, delimiter, plot in cases:
            with self.subTest(filename=filename):
                path = self.write_bytes(
                    filename,
                    f'Title{delimiter}Plot\r\nHeat{delimiter}"{plot}"\r\n',
                )
                self.assertEqual(
                    self.plugin.read_rows(path),
                    [{"Title": "Heat", "Plot": plot}],
                )

    def test_utf8_bom_and_non_ascii_text_are_preserved(self):
        path = self.write_bytes(
            "collection.csv",
            "Title;Plot\r\nAmélie;Crème brûlée\r\n",
            bom=True,
        )

        self.assertEqual(
            self.plugin.read_rows(path),
            [{"Title": "Amélie", "Plot": "Crème brûlée"}],
        )

    def test_unidentifiable_dialect_keeps_existing_warning_behavior(self):
        path = self.write_bytes("collection.csv", "Title\r\nHeat\r\nAmélie\r\n")

        items, warnings, files, columns = self.plugin.load_items(path)

        self.assertEqual(items, [])
        self.assertEqual(files, [path])
        self.assertEqual(columns, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("Could not determine delimiter", warnings[0])

    def test_load_items_keeps_existing_normalization_behavior(self):
        path = self.write_bytes(
            "collection.csv",
            "Title,Year,Barcode\r\nHeat,1995,0123456789012\r\n",
        )

        items, warnings, files, columns = self.plugin.load_items(path)

        self.assertEqual(warnings, [])
        self.assertEqual(files, [path])
        self.assertEqual(columns, ["Title", "Year", "Barcode"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Heat")
        self.assertEqual(items[0]["year"], "1995")
        self.assertEqual(items[0]["barcode"], "0123456789012")


if __name__ == "__main__":
    unittest.main()
