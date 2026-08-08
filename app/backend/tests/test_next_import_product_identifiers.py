"""An import keeps every product code a row carries, not just the scanned one.

`movies.barcode` holds one value because a scan must resolve to one film. A
pressing routinely carries more: an EAN for Europe beside a UPC for North
America, an Amazon ASIN, a catalogue number. `movie_product_identifiers` records
those (migration 069), and these tests cover both halves — the plugin reading
them out of a row, and the worker writing them without deleting what is there.
"""

import os
import sys
import unittest
import uuid
from pathlib import Path


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:
    psycopg = None
    dict_row = None

from app.backend.next_plugins._collection_import_base import CollectionImportPlugin
from app.backend.next_plugins.import_bluray_com.plugin import SOURCE as BLURAY_SOURCE
from app.backend.next_product_identifiers import add_movie_identifiers, movie_identifiers_by_type
from app.backend.next_worker import import_product_identifiers, upsert_import_movie


DATABASE_URL = os.environ.get("DATABASE_URL")

PLUGIN_ID = "import_identifier_test"
SOURCE_FILE = Path("bluray_collection.csv")


def bluray_row(**overrides):
    row = {
        "Title": "The Bourne Complete Collection 4K",
        "Country code": "US",
        "UPC": "191329223109",
        "EAN": "0191329223109",
        "ASIN": "B09XY1F8QJ",
        "Release date": "June 7 2022",
        "Blu-ray discs": "6",
    }
    row.update(overrides)
    return row


def identifier_types(entries):
    return {(entry["type"], entry["value"]) for entry in entries}


class ProductIdentifierReadingTests(unittest.TestCase):
    def setUp(self):
        self.plugin = CollectionImportPlugin(BLURAY_SOURCE)

    def test_every_code_in_the_row_is_kept_under_its_own_type(self):
        movie = self.plugin.normalize_row(bluray_row(), SOURCE_FILE, 1)
        # The barcode still resolves the scan and holds exactly one value…
        self.assertEqual(movie["barcode"], "191329223109")
        # …and none of the other codes are lost any more.
        self.assertEqual(
            identifier_types(movie["productIdentifiers"]),
            {("upc", "191329223109"), ("upc", "0191329223109"), ("asin", "B09XY1F8QJ")},
        )

    def test_the_type_comes_from_the_digits_not_from_the_column_header(self):
        # Blu-ray.com files a zero-padded UPC-A under "EAN". DiscVault derives
        # the type from the symbology, so trusting the header would store a
        # value under a type its own validator rejects.
        movie = self.plugin.normalize_row(bluray_row(UPC="", EAN="0191329223109"), SOURCE_FILE, 1)
        self.assertIn(("upc", "0191329223109"), identifier_types(movie["productIdentifiers"]))
        self.assertNotIn(("ean", "0191329223109"), identifier_types(movie["productIdentifiers"]))

    def test_a_genuine_ean_stays_an_ean(self):
        movie = self.plugin.normalize_row(bluray_row(UPC="", EAN="5050582898897", ASIN=""), SOURCE_FILE, 1)
        self.assertEqual(identifier_types(movie["productIdentifiers"]), {("ean", "5050582898897")})

    def test_the_scanned_barcode_is_recorded_as_a_typed_row_too(self):
        # Otherwise the resolving column and the descriptive rows disagree about
        # a value the file stated once.
        movie = self.plugin.normalize_row(bluray_row(UPC="", EAN="", ASIN="", Barcode="5050582898897"), SOURCE_FILE, 1)
        self.assertEqual(movie["barcode"], "5050582898897")
        self.assertIn(("ean", "5050582898897"), identifier_types(movie["productIdentifiers"]))

    def test_a_row_without_codes_carries_no_identifiers(self):
        movie = self.plugin.normalize_row({"Title": "Heat"}, SOURCE_FILE, 1)
        self.assertNotIn("productIdentifiers", movie)

    def test_a_catalogue_number_is_read_from_a_generic_export(self):
        plugin = CollectionImportPlugin(
            {
                "id": "import_test_source",
                "name": "Test Source",
                "sourceKind": "test_export",
                "defaultPath": "/data/import/test",
                "aliases": {},
            }
        )
        movie = plugin.normalize_row({"Title": "Heat", "Catalog Number": "ARROW-042"}, SOURCE_FILE, 1)
        self.assertEqual(identifier_types(movie["productIdentifiers"]), {("catalog_number", "ARROW-042")})


class ImportItemIdentifierShapeTests(unittest.TestCase):
    def test_the_plugin_list_is_read(self):
        entries = import_product_identifiers({"productIdentifiers": [{"type": "asin", "value": "B09XY1F8QJ"}]})
        self.assertEqual(identifier_types(entries), {("asin", "B09XY1F8QJ")})

    def test_loose_per_type_keys_are_read_too(self):
        # A hand-built or older payload that never heard of productIdentifiers
        # still gets its ASIN stored rather than silently dropped.
        entries = import_product_identifiers({"asin": "B09XY1F8QJ", "catalogNumber": "ARROW-042"})
        self.assertEqual(identifier_types(entries), {("asin", "B09XY1F8QJ"), ("catalog_number", "ARROW-042")})


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class ProductIdentifierWritingTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def setUp(self):
        self.external_id = f"identifiers-{uuid.uuid4()}"

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM movies WHERE metadata->>'import_source' = %s", (PLUGIN_ID,))
            conn.commit()

    def _import(self, conn, **item):
        movie_id, _ = upsert_import_movie(conn, PLUGIN_ID, {"externalId": self.external_id, **item})
        conn.commit()
        return movie_id

    def test_an_import_stores_every_typed_identifier(self):
        with self.connect() as conn:
            movie_id = self._import(
                conn,
                title="The Bourne Complete Collection",
                barcode="191329223109",
                productIdentifiers=[
                    {"type": "upc", "value": "191329223109"},
                    {"type": "upc", "value": "0191329223109"},
                    {"type": "asin", "value": "B09XY1F8QJ"},
                ],
            )
            stored = movie_identifiers_by_type(conn, movie_id)
        self.assertEqual(
            identifier_types(stored),
            {("upc", "191329223109"), ("upc", "0191329223109"), ("asin", "B09XY1F8QJ")},
        )

    def test_a_second_import_adds_without_deleting(self):
        # The file describes the pressing as one source saw it. It knows nothing
        # of codes the film already carries, so replacing the set would delete
        # them — the same fill-don't-overwrite rule as every other field.
        with self.connect() as conn:
            movie_id = self._import(conn, title="Basic Instinct", productIdentifiers=[{"type": "asin", "value": "B0BQ91T536"}])
            add_movie_identifiers(conn, movie_id, [{"type": "catalog_number", "value": "SC-1992"}])
            conn.commit()
            self._import(conn, title="Basic Instinct", productIdentifiers=[{"type": "ean", "value": "5053083230142"}])
            stored = movie_identifiers_by_type(conn, movie_id)
        self.assertEqual(
            identifier_types(stored),
            {("asin", "B0BQ91T536"), ("catalog_number", "SC-1992"), ("ean", "5053083230142")},
        )

    def test_an_invalid_code_is_dropped_rather_than_stored_or_raised(self):
        # The writer validates with the same rules the manual edit surface uses:
        # a file must not be able to store what a person could not type.
        with self.connect() as conn:
            movie_id = self._import(
                conn,
                title="Basic Instinct",
                productIdentifiers=[
                    {"type": "ean", "value": "1234567890123"},  # check digit fails
                    {"type": "asin", "value": "not-an-asin"},
                    {"type": "asin", "value": "B0BQ91T536"},
                ],
            )
            stored = movie_identifiers_by_type(conn, movie_id)
        self.assertEqual(identifier_types(stored), {("asin", "B0BQ91T536")})

    def test_a_scannable_code_another_film_holds_does_not_fail_the_row(self):
        # uq_movie_product_identifiers_scannable promises one film per scannable
        # code. A batch import must not lose a whole row over a code that is
        # already accounted for elsewhere.
        with self.connect() as conn:
            first = self._import(conn, title="Basic Instinct", productIdentifiers=[{"type": "ean", "value": "5053083230142"}])
            self.external_id = f"identifiers-{uuid.uuid4()}"
            second = self._import(
                conn,
                title="Another Pressing",
                productIdentifiers=[
                    {"type": "ean", "value": "5053083230142"},
                    {"type": "asin", "value": "B0BQ91T536"},
                ],
            )
            first_stored = movie_identifiers_by_type(conn, first)
            second_stored = movie_identifiers_by_type(conn, second)
        self.assertNotEqual(first, second)
        self.assertEqual(identifier_types(first_stored), {("ean", "5053083230142")})
        self.assertEqual(identifier_types(second_stored), {("asin", "B0BQ91T536")})


if __name__ == "__main__":
    unittest.main()
