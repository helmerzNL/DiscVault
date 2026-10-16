import os
import sys
import unittest
import uuid


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend import next_common
    from app.backend import next_app
except ModuleNotFoundError as exc:  # Local minimal test environments may omit optional backend deps.
    if exc.name not in {"flask", "psycopg"}:
        raise
    next_common = None
    next_app = None


@unittest.skipIf(next_common is None, "Flask is not installed in this test environment")
class NextCommonHelperTests(unittest.TestCase):
    def test_shared_helpers_are_reexported_from_next_app(self):
        names = [
            "NextApiError",
            "json_ready",
            "response",
            "parse_int_arg",
            "parse_uuid",
            "parse_bool_value",
            "parse_uuid_list",
            "table_exists",
            "count_table",
        ]
        for name in names:
            self.assertIs(getattr(next_app, name), getattr(next_common, name), name)

    def test_json_ready_normalizes_nested_values(self):
        identifier = uuid.uuid4()
        result = next_common.json_ready({"id": identifier, "items": (1, 2)})
        self.assertEqual(result, {"id": str(identifier), "items": [1, 2]})

    def test_parse_bool_value_accepts_common_truthy_tokens(self):
        self.assertTrue(next_common.parse_bool_value("yes"))
        self.assertFalse(next_common.parse_bool_value("off"))
        self.assertTrue(next_common.parse_bool_value(None, default=True))

    def test_parse_uuid_rejects_invalid_value(self):
        with self.assertRaises(next_common.NextApiError) as ctx:
            next_common.parse_uuid("not-a-uuid", "movieId")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_parse_uuid_list_deduplicates_and_validates(self):
        identifier = uuid.uuid4()
        parsed = next_common.parse_uuid_list([str(identifier), str(identifier)], "movieIds")
        self.assertEqual(parsed, [identifier])

    def test_resolve_import_identity_match_prefers_barcode_over_everything(self):
        """Barcode wins outright, with no format check - both existing
        callers (writer and preview) already relied on that."""
        result = next_common.resolve_import_identity_match(
            barcode="123",
            identifiers={"tmdb": "999"},
            title="A",
            year="2000",
            item_format="DVD",
            barcode_candidates=lambda: [{"id": "by-barcode", "format": "Blu-ray"}],
            identifier_candidates=lambda provider, identifier: [{"id": "by-identifier"}],
            title_year_candidates=lambda title, year: [{"id": "by-title-year"}],
        )
        self.assertEqual(result, ("barcode", {"id": "by-barcode", "format": "Blu-ray"}))

    def test_resolve_import_identity_match_falls_through_an_incompatible_identifier_row(self):
        """A format-incompatible identifier row must be skipped in favour of
        a later, compatible one - not simply the first row returned."""
        result = next_common.resolve_import_identity_match(
            barcode="",
            identifiers={"tmdb": "999"},
            title="",
            year="",
            item_format="Blu-ray",
            barcode_candidates=lambda: [],
            identifier_candidates=lambda provider, identifier: [
                {"id": "wrong-format", "format": "DVD"},
                {"id": "right-format", "format": "Blu-ray"},
            ],
            title_year_candidates=lambda title, year: [],
        )
        self.assertEqual(result, ("tmdb", {"id": "right-format", "format": "Blu-ray"}))

    def test_resolve_import_identity_match_falls_back_to_title_year(self):
        result = next_common.resolve_import_identity_match(
            barcode="",
            identifiers={"tmdb": "", "imdb": ""},
            title="Predator",
            year="1987",
            item_format="",
            barcode_candidates=lambda: [],
            identifier_candidates=lambda provider, identifier: [],
            title_year_candidates=lambda title, year: [{"id": "by-title-year", "format": ""}],
        )
        self.assertEqual(result, ("title_year", {"id": "by-title-year", "format": ""}))

    def test_resolve_import_identity_match_rejects_identifier_row_conflicting_on_both_fields(self):
        """A shared TMDb/IMDb id can identify a whole TV series, so a
        provider-identifier row that disagrees with the import on *both*
        title and year is the wrong season/entry and must be skipped (#793) -
        even when it is format-compatible and no other candidate exists."""
        result = next_common.resolve_import_identity_match(
            barcode="",
            identifiers={"imdb": "tt9184820"},
            title="Star Trek: Lower Decks: Season 2",
            year="2021",
            item_format="Blu-ray",
            barcode_candidates=lambda: [],
            identifier_candidates=lambda provider, identifier: [
                {"id": "season-1", "title": "Star Trek: Lower Decks: Season 1", "year": "2020", "format": "Blu-ray"},
            ],
            title_year_candidates=lambda title, year: [],
        )
        self.assertIsNone(result)

    def test_resolve_import_identity_match_keeps_identifier_row_conflicting_on_one_field(self):
        """One disagreeing release field (an edition suffix, a re-release
        year) is not enough to reject a provider-identifier match - only
        both fields disagreeing at once is (#793)."""
        result = next_common.resolve_import_identity_match(
            barcode="",
            identifiers={"imdb": "tt0103772"},
            title="Basic Instinct 4K",
            year="1992",
            item_format="Blu-ray",
            barcode_candidates=lambda: [],
            identifier_candidates=lambda provider, identifier: [
                {"id": "basic-instinct", "title": "Basic Instinct", "year": "1992", "format": "Blu-ray"},
            ],
            title_year_candidates=lambda title, year: [],
        )
        self.assertEqual(result, ("imdb", {"id": "basic-instinct", "title": "Basic Instinct", "year": "1992", "format": "Blu-ray"}))

    def test_resolve_import_identity_match_returns_none_when_nothing_fits(self):
        result = next_common.resolve_import_identity_match(
            barcode="",
            identifiers={},
            title="",
            year="",
            item_format="",
            barcode_candidates=lambda: [],
            identifier_candidates=lambda provider, identifier: [],
            title_year_candidates=lambda title, year: [],
        )
        self.assertIsNone(result)

    def test_count_table_returns_zero_for_missing_table(self):
        class _FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, *args):
                self._row = {"table_name": None}

            def fetchone(self):
                return self._row

        class _FakeConn:
            def cursor(self):
                return _FakeCursor()

        self.assertEqual(next_common.count_table(_FakeConn(), "missing_table"), 0)


if __name__ == "__main__":
    unittest.main()
