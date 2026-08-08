"""The typed-identifier vocabulary, and what a scan can honestly say about it.

Every barcode below is a real, check-digit-valid code: a fabricated one would
pass the length test, fail the checksum, and quietly make half these cases
assert nothing.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import next_product_identifiers as identifiers  # noqa: E402

EAN13 = "4006381333931"
EAN13_OTHER = "5051890013279"
UPCA = "012569828827"
UPCA_PADDED = "0012569828827"
ISBN13 = "9780306406157"
EAN8 = "96385074"
GTIN14 = "14006381333938"


class ClassifyScannedIdentifierTests(unittest.TestCase):
    def test_the_digit_count_names_the_symbology(self):
        """The question "is this a UPC or an EAN" has a deterministic answer, so
        nobody should be asked it."""
        self.assertEqual(identifiers.classify_scanned_identifier(UPCA), ("upc", UPCA))
        self.assertEqual(identifiers.classify_scanned_identifier(EAN13), ("ean", EAN13))
        self.assertEqual(identifiers.classify_scanned_identifier(EAN8), ("ean", EAN8))
        self.assertEqual(identifiers.classify_scanned_identifier(GTIN14), ("ean", GTIN14))

    def test_a_bookland_prefix_is_an_isbn_and_not_an_ean(self):
        """978/979 is a real EAN-13 range that GS1 gave to books. Recording it
        as an EAN is not wrong about the digits and is wrong about the fact."""
        self.assertEqual(identifiers.classify_scanned_identifier(ISBN13), ("isbn", ISBN13))

    def test_a_zero_padded_upc_is_still_a_upc(self):
        """GS1 made UPC-A a subset of EAN-13 by prefixing a zero, and most
        scanners return the padded form. Reading that as an EAN would file a
        North American pressing under the European scheme."""
        self.assertEqual(identifiers.classify_scanned_identifier(UPCA_PADDED), ("upc", UPCA_PADDED))

    def test_the_value_is_never_rewritten(self):
        """The padded and unpadded forms are the same product and two different
        identifiers upstream, which matches on the literal value. Normalising
        one into the other would silently retarget the match."""
        _, padded = identifiers.classify_scanned_identifier(UPCA_PADDED)
        _, plain = identifiers.classify_scanned_identifier(UPCA)
        self.assertNotEqual(padded, plain)
        self.assertEqual(len(padded), 13)
        self.assertEqual(len(plain), 12)

    def test_spaces_and_dashes_are_separators_and_letters_are_not(self):
        self.assertEqual(identifiers.classify_scanned_identifier("4006381-333931"), ("ean", EAN13))
        self.assertEqual(identifiers.classify_scanned_identifier("4006381 333931"), ("ean", EAN13))
        self.assertIsNone(identifiers.classify_scanned_identifier("400638133393X"))

    def test_a_failed_check_digit_is_not_a_barcode(self):
        """The checksum is the only evidence the read was correct. Accepting a
        value that fails it records a misread as a fact."""
        self.assertIsNone(identifiers.classify_scanned_identifier("4006381333932"))

    def test_a_length_outside_the_scheme_is_refused(self):
        for value in ("", "1", "400638133393", "400638133393100"):
            with self.subTest(value=value):
                self.assertIsNone(identifiers.classify_scanned_identifier(value))


class NormalizeIdentifierTests(unittest.TestCase):
    def test_a_stated_type_is_checked_rather_than_trusted(self):
        """Someone typing an EAN into the ASIN box should be told, not silently
        recorded -- the two are different namespaces upstream."""
        self.assertIsNone(identifiers.normalize_identifier("asin", EAN13))
        self.assertIsNone(identifiers.normalize_identifier("ean", UPCA))
        self.assertEqual(identifiers.normalize_identifier("upc", UPCA), UPCA)

    def test_an_asin_is_upper_cased_and_bounded(self):
        self.assertEqual(identifiers.normalize_identifier("asin", "b07x1kqz3n"), "B07X1KQZ3N")
        self.assertIsNone(identifiers.normalize_identifier("asin", "B07X1KQZ3"))
        self.assertIsNone(identifiers.normalize_identifier("asin", "B07X1KQZ3NN"))

    def test_an_all_digit_ten_is_an_isbn_10_rather_than_an_asin(self):
        """Amazon uses the ISBN-10 as the ASIN for books, so the same value
        would end up in two rows claiming to be two identifiers."""
        self.assertIsNone(identifiers.normalize_identifier("asin", "0306406152"))

    def test_a_catalogue_number_is_free_text_because_it_is(self):
        self.assertEqual(identifiers.normalize_identifier("catalog_number", " SPHE  1234  "), "SPHE 1234")
        self.assertIsNone(identifiers.normalize_identifier("catalog_number", "  "))
        self.assertIsNone(identifiers.normalize_identifier("catalog_number", "x" * 121))

    def test_an_unknown_type_is_refused(self):
        """The five types are MovieVault's. A sixth would be one DiscVault can
        record and never contribute."""
        self.assertIsNone(identifiers.normalize_identifier("gtin", EAN13))


class ContributableEansTests(unittest.TestCase):
    """`eans` upstream is narrower than the table it writes into.

    `_replace_release_eans` deletes and inserts under `identifier_type = 'ean'`
    only, and its conflict check queries that one type. So a UPC in this list
    would be written as an EAN, and a UPC the release already holds as `upc`
    would be invisible to the check.
    """

    def test_only_ean_typed_values_travel(self):
        result = identifiers.contributable_eans(
            [
                {"type": "ean", "value": EAN13},
                {"type": "upc", "value": UPCA},
                {"type": "isbn", "value": ISBN13},
                {"type": "asin", "value": "B07X1KQZ3N"},
                {"type": "catalog_number", "value": "SPHE 1234"},
            ]
        )
        self.assertEqual(result, [EAN13])

    def test_the_scanned_barcode_is_folded_in_and_deduplicated(self):
        """It is the one value this instance is certain about, so it belongs in
        the list whether or not somebody also typed it in."""
        self.assertEqual(
            identifiers.contributable_eans([{"type": "ean", "value": EAN13}], barcode=EAN13),
            [EAN13],
        )
        self.assertEqual(
            identifiers.contributable_eans([{"type": "ean", "value": EAN13_OTHER}], barcode=EAN13),
            sorted([EAN13, EAN13_OTHER]),
        )

    def test_a_scanned_upc_does_not_become_an_ean(self):
        self.assertEqual(identifiers.contributable_eans([], barcode=UPCA), [])

    def test_the_order_matches_the_order_upstream_reads_in(self):
        """Upstream reads its side `order by identifier_value`, and this list is
        compared against that one. An unsorted list is a spurious conflict."""
        result = identifiers.contributable_eans(
            [{"type": "ean", "value": EAN13}, {"type": "ean", "value": EAN13_OTHER}]
        )
        self.assertEqual(result, sorted(result))


class CatalogueEansTests(unittest.TestCase):
    def test_the_wire_vocabulary_is_per_symbology_and_the_stored_one_is_not(self):
        """`ean8`/`ean13`/`gtin14` all land in `identifier_type = 'ean'`; only
        `upca` lands in `upc`. So everything that is not a `upca` is comparable
        against what this side would send."""
        self.assertEqual(
            identifiers.catalogue_eans(
                [
                    {"type": "ean13", "value": EAN13, "scope": "package"},
                    {"type": "upca", "value": UPCA, "scope": "package"},
                    {"type": "ean8", "value": EAN8, "scope": "disc"},
                ]
            ),
            sorted([EAN13, EAN8]),
        )

    def test_an_absent_list_is_not_an_empty_one(self):
        """None means "the catalogue did not say", which is not the same as
        "the release has no barcodes" -- one is a missing answer and the other
        would propose deleting every identifier the release has."""
        self.assertIsNone(identifiers.catalogue_eans(None))
        self.assertEqual(identifiers.catalogue_eans([]), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
