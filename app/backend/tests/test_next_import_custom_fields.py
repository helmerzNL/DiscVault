"""An import file can fill the fields the owner defined (#719 follow-up).

Custom fields (migration 089) are defined at runtime, so the import mapping had
no way to name one: its target list was a fixed allowlist of built-in fields,
and a column holding "Streamer" or "Shelf" could only be dropped. A mapping
target of the form `custom:<key>` now names a definition, and the value lands on
the film.

Three properties carry the feature, and each is tested where it actually lives:

  the convention   two modules spell `custom:` and the key shape, and cannot
                   import each other -- a plugin loads through three different
                   import paths and may run outside the backend package
  the reading      the plugin pulls the mapped column out of the row as raw
                   text, because only the backend knows the field's type
  the writing      the worker fills a value the film does not already have, and
                   never raises: an import is thousands of rows and one bad cell
                   must not end the run

The last one is why the skipped reasons are asserted rather than just the happy
path. A silently dropped column is indistinguishable from an empty one, which is
the failure this feature exists to remove.
"""

import os
import sys
import unittest
import uuid
from pathlib import Path


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEXT_VIEWS_UI_PATH = os.path.join(BACKEND_DIR, "next_views_ui.py")

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ModuleNotFoundError:  # pragma: no cover - minimal environments
    psycopg = None
    dict_row = None
    Jsonb = None

from app.backend.next_custom_fields import (
    IMPORT_MAPPING_PREFIX,
    fill_movie_custom_values,
    import_mapping_field_key,
)
from app.backend.next_plugins import _collection_import_base as import_base
from app.backend.next_plugins._collection_import_base import CollectionImportPlugin

try:
    from app.backend.next_app import normalize_import_column_mapping
except ModuleNotFoundError as exc:  # pragma: no cover - minimal environments
    if exc.name not in {"flask", "psycopg"}:
        raise
    normalize_import_column_mapping = None

DATABASE_URL = os.environ.get("DATABASE_URL")

SOURCE = {
    "id": "import_custom_field_test",
    "name": "Test Source",
    "sourceKind": "test_export",
    "defaultPath": "/data/import/test",
    "aliases": {"title": ("Title",)},
}
SOURCE_FILE = Path("collection.csv")


class MappingConventionTests(unittest.TestCase):
    """Two modules spell the same convention and must keep agreeing."""

    def test_the_plugin_and_the_backend_spell_the_prefix_the_same(self):
        # The plugin cannot import next_custom_fields -- it is loaded through
        # three import paths and may run outside the backend package -- so the
        # constant is duplicated there deliberately. This is the seam.
        self.assertEqual(import_base.CUSTOM_FIELD_MAPPING_PREFIX, IMPORT_MAPPING_PREFIX)

    def test_the_two_key_patterns_accept_the_same_keys(self):
        for candidate in ("streamer", "a", "shelf_2", "a" * 48):
            with self.subTest(candidate=candidate, accepted=True):
                self.assertTrue(import_base.CUSTOM_FIELD_KEY_RE.match(candidate))
                self.assertEqual(import_mapping_field_key(f"custom:{candidate}"), candidate)
        for candidate in ("", "9lives", "with space", "a" * 49, "has:colon"):
            with self.subTest(candidate=candidate, accepted=False):
                self.assertEqual(import_mapping_field_key(f"custom:{candidate}"), "")

    def test_a_target_is_matched_case_insensitively_by_both(self):
        # A definition key is always lower case, so folding the target is a
        # normalisation rather than a guess -- and both sides must fold, or a
        # mapping the UI accepts silently reads as a built-in in the plugin.
        self.assertEqual(import_mapping_field_key("custom:Streamer"), "streamer")
        self.assertEqual(import_base.custom_field_mapping_key("custom:Streamer"), "streamer")
        self.assertEqual(
            import_mapping_field_key("  custom:Streamer  "),
            import_base.custom_field_mapping_key("  custom:Streamer  "),
        )

    def test_a_built_in_target_is_never_read_as_a_custom_field(self):
        for built_in in ("title", "barcode", "watchedAt", ""):
            self.assertEqual(import_mapping_field_key(built_in), "")
        self.assertEqual(import_base.custom_field_mapping_key("title"), "")


@unittest.skipIf(normalize_import_column_mapping is None, "Flask/psycopg are not installed")
class MappingAllowlistTests(unittest.TestCase):
    """The request normaliser lets a custom target through, in shape only."""

    def test_a_custom_target_survives_beside_the_built_ins(self):
        mapping = normalize_import_column_mapping(
            {"title": "Title", "custom:streamer": "Streaming service"}
        )
        self.assertEqual(mapping, {"title": "Title", "custom:streamer": "Streaming service"})

    def test_a_malformed_custom_target_is_dropped(self):
        mapping = normalize_import_column_mapping(
            {"custom:": "A", "custom:Bad Key": "B", "nonsense": "C", "custom:ok": "D"}
        )
        self.assertEqual(mapping, {"custom:ok": "D"})

    def test_a_target_without_a_column_is_dropped(self):
        # Unchanged from the built-in behaviour: a mapping entry naming no
        # column says nothing, and "Auto" is expressed by the absence of one.
        self.assertEqual(normalize_import_column_mapping({"custom:streamer": ""}), {})


class PluginReadingTests(unittest.TestCase):
    """The plugin reads the mapped column, as raw text."""

    def setUp(self):
        self.plugin = CollectionImportPlugin(SOURCE)

    def normalize(self, row, mapping):
        return self.plugin.normalize_row(row, SOURCE_FILE, 1, mapping)

    def test_a_mapped_column_lands_under_its_field_key(self):
        movie = self.normalize(
            {"Title": "Heat", "Streaming service": "Netflix"},
            {"custom:streamer": "Streaming service"},
        )
        self.assertEqual(movie["customFields"], {"streamer": "Netflix"})

    def test_the_value_is_carried_as_written_not_interpreted(self):
        # The field's type lives in the database; typing it here would mean
        # guessing. "12" may be a number field or a text one.
        movie = self.normalize(
            {"Title": "Heat", "Discs": "12", "Owned": "yes"},
            {"custom:disc_count": "Discs", "custom:owned": "Owned"},
        )
        self.assertEqual(movie["customFields"], {"disc_count": "12", "owned": "yes"})

    def test_an_unmapped_or_empty_column_produces_no_entry(self):
        self.assertNotIn("customFields", self.normalize({"Title": "Heat"}, {}))
        self.assertNotIn(
            "customFields",
            self.normalize({"Title": "Heat", "Streaming service": ""}, {"custom:streamer": "Streaming service"}),
        )

    def test_a_custom_field_is_never_auto_detected_from_a_column_name(self):
        # Every built-in field has aliases; a custom field cannot, because the
        # owner invents it at runtime. Mapping it is the only way in, and a
        # column that happens to share its name must not be picked up silently.
        movie = self.normalize({"Title": "Heat", "streamer": "Netflix"}, {})
        self.assertNotIn("customFields", movie)

    def test_the_built_in_fields_are_unaffected_by_a_custom_mapping(self):
        movie = self.normalize(
            {"Title": "Heat", "Streaming service": "Netflix"},
            {"custom:streamer": "Streaming service"},
        )
        self.assertEqual(movie["title"], "Heat")


class MappingUiTests(unittest.TestCase):
    """The Import Center offers the defined fields as targets."""

    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.ui_source = handle.read()

    def _helper_body(self):
        start = self.ui_source.index("function importCustomFieldMappingFields()")
        return self.ui_source[start:self.ui_source.index("function renderImportMapping()", start)]

    def test_the_mapping_card_offers_the_defined_fields(self):
        self.assertIn("importCustomFieldMappingFields()", self.ui_source)
        self.assertIn("`custom:${field.key}`", self._helper_body())

    def test_archived_definitions_are_not_offered(self):
        # Archiving means "no new input", and an import is input.
        self.assertIn("!field.archivedAt", self._helper_body())

    def test_the_heading_reuses_an_existing_translated_key(self):
        # The field labels are the owner's own words and are never translated.
        # The heading above them is, and reusing this key keeps the change from
        # needing a thirtieth translation of "Custom fields".
        self.assertIn('tNext("movieDetail.customFields", "Custom fields")', self.ui_source)

    def test_the_heading_spans_the_grid(self):
        # In an auto-fit grid a heading that does not span reads as a cell
        # sitting beside the first field rather than introducing the group.
        start = self.ui_source.index(".import-mapping-grid .import-mapping-group {")
        self.assertIn("grid-column: 1 / -1;", self.ui_source[start:start + 400])


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class ValueWritingTests(unittest.TestCase):
    """The worker fills what the film does not have, and reports what it skipped."""

    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)

    def setUp(self):
        self.tag = f"cfimport-{uuid.uuid4().hex[:8]}"
        self.movie_ids = []
        self.field_ids = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        with self.connect() as conn, conn.cursor() as cur:
            for movie_id in self.movie_ids:
                cur.execute("DELETE FROM movie_custom_field_values WHERE movie_id=%s", (movie_id,))
                cur.execute("DELETE FROM movies WHERE id=%s", (movie_id,))
            for field_id in self.field_ids:
                cur.execute("DELETE FROM movie_custom_field_values WHERE field_id=%s", (field_id,))
                cur.execute("DELETE FROM custom_field_definitions WHERE id=%s", (field_id,))

    def _movie(self, conn):
        movie_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO movies (id, public_id, title) VALUES (%s, %s, %s)",
                (movie_id, str(uuid.uuid4()), f"{self.tag} film"),
            )
        self.movie_ids.append(movie_id)
        return movie_id

    def _field(self, conn, key, field_type="text", *, archived=False, options=None):
        field_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO custom_field_definitions (id, key, name, field_type, options, archived_at)
                VALUES (%s, %s, %s, %s, %s, CASE WHEN %s THEN now() ELSE NULL END)
                """,
                (field_id, key, key.title(), field_type, Jsonb(options or []), archived),
            )
        self.field_ids.append(field_id)
        return field_id

    def _definitions(self, conn):
        from app.backend.next_custom_fields import custom_field_definition_rows

        return custom_field_definition_rows(conn)

    def _stored(self, conn, movie_id, field_id, column="value_text"):
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {column} AS value FROM movie_custom_field_values WHERE movie_id=%s AND field_id=%s",
                (movie_id, field_id),
            )
            row = cur.fetchone()
        return row["value"] if row else None

    def test_a_mapped_value_lands_on_the_film(self):
        with self.connect() as conn:
            movie_id = self._movie(conn)
            field_id = self._field(conn, f"{self.tag}_streamer".replace("-", "_"))
            key = next(
                row["key"] for row in self._definitions(conn) if str(row["id"]) == str(field_id)
            )
            filled, reasons = fill_movie_custom_values(
                conn, movie_id, {key: "Netflix"}, fields=self._definitions(conn)
            )
        self.assertEqual((filled, reasons), (1, []))
        with self.connect() as conn:
            self.assertEqual(self._stored(conn, movie_id, field_id), "Netflix")

    def test_an_import_fills_a_field_but_never_overwrites_one(self):
        # The same rule the movie columns follow: a file is not the authority on
        # a film the user already curated.
        with self.connect() as conn:
            movie_id = self._movie(conn)
            key = f"{self.tag}_shelf".replace("-", "_")
            field_id = self._field(conn, key)
            fields = self._definitions(conn)
            fill_movie_custom_values(conn, movie_id, {key: "Curated"}, fields=fields)
            filled, reasons = fill_movie_custom_values(conn, movie_id, {key: "From file"}, fields=fields)
            self.assertEqual((filled, reasons), (0, []))
            self.assertEqual(self._stored(conn, movie_id, field_id), "Curated")

    def test_each_declared_type_lands_in_its_own_column(self):
        with self.connect() as conn:
            movie_id = self._movie(conn)
            cases = {
                "number": ("value_number", "12", 12),
                "date": ("value_date", "2026-09-04", "2026-09-04"),
                "boolean": ("value_boolean", "yes", True),
            }
            for field_type, (column, raw, expected) in cases.items():
                key = f"{self.tag}_{field_type}".replace("-", "_")
                field_id = self._field(conn, key, field_type)
                filled, reasons = fill_movie_custom_values(
                    conn, movie_id, {key: raw}, fields=self._definitions(conn)
                )
                with self.subTest(field_type=field_type):
                    self.assertEqual((filled, reasons), (1, []))
                    stored = self._stored(conn, movie_id, field_id, column)
                    self.assertEqual(str(stored)[:10] if column == "value_date" else stored, expected)

    def test_a_cell_the_type_refuses_is_reported_not_raised(self):
        # One bad cell must not end a run of thousands of rows.
        with self.connect() as conn:
            movie_id = self._movie(conn)
            key = f"{self.tag}_count".replace("-", "_")
            self._field(conn, key, "number")
            filled, reasons = fill_movie_custom_values(
                conn, movie_id, {key: "not a number"}, fields=self._definitions(conn)
            )
        self.assertEqual(filled, 0)
        self.assertEqual(len(reasons), 1)
        self.assertIn("number", reasons[0])

    def test_an_archived_or_unknown_field_is_reported_by_name(self):
        with self.connect() as conn:
            movie_id = self._movie(conn)
            archived_key = f"{self.tag}_old".replace("-", "_")
            self._field(conn, archived_key, archived=True)
            filled, reasons = fill_movie_custom_values(
                conn,
                movie_id,
                {archived_key: "x", "no_such_field_here": "y"},
                fields=self._definitions(conn),
            )
        self.assertEqual(filled, 0)
        self.assertEqual(len(reasons), 2)
        self.assertTrue(any("archived" in reason for reason in reasons))
        self.assertTrue(any("no such custom field" in reason for reason in reasons))

    def test_an_empty_cell_is_neither_written_nor_reported(self):
        # Most rows of a real file leave most columns blank; that is not an error.
        with self.connect() as conn:
            movie_id = self._movie(conn)
            key = f"{self.tag}_blank".replace("-", "_")
            self._field(conn, key)
            filled, reasons = fill_movie_custom_values(
                conn, movie_id, {key: "   "}, fields=self._definitions(conn)
            )
        self.assertEqual((filled, reasons), (0, []))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
