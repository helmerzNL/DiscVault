"""Instance-defined custom fields: the vocabulary, value typing, and the wire.

The claims here that would otherwise fail quietly:

**A value lands in the column matching its type.** That pairing cannot be a
database CHECK — it needs a join — so it lives in Python, and nothing but a test
stands behind it. A number written into `value_text` would sort as a string and
nobody would see why "10" came before "9".

**Zero and false are values.** The clearing path keys on emptiness, and a falsy
test would swallow both, deleting a rating of 0 and a "no" as though the user had
never answered.

**Changing a value clears the other three columns.** Otherwise a field edited
from a number to a date leaves the old number behind, and the CHECK that says
exactly one column is filled starts failing on rows nobody touched.
"""

import os
import sys
import unittest
import uuid


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:  # pragma: no cover - minimal environments
    psycopg = None
    dict_row = None

import next_custom_fields as cf  # noqa: E402
from next_common import NextApiError  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")


class VocabularyTests(unittest.TestCase):
    def test_the_five_types_are_accepted_and_nothing_else(self):
        for field_type in ("text", "number", "date", "boolean", "select"):
            self.assertEqual(cf.normalize_field_type(field_type), field_type)
        for bad in ("rating", "", None, "TEXT ", "json"):
            with self.subTest(value=bad):
                if bad == "TEXT ":
                    self.assertEqual(cf.normalize_field_type(bad), "text")
                    continue
                with self.assertRaises(NextApiError):
                    cf.normalize_field_type(bad)

    def test_select_shares_the_text_column_because_it_stores_a_key(self):
        self.assertEqual(cf.value_column_for_type("select"), "value_text")
        self.assertEqual(cf.value_column_for_type("text"), "value_text")
        self.assertEqual(cf.value_column_for_type("number"), "value_number")
        self.assertEqual(cf.value_column_for_type("date"), "value_date")
        self.assertEqual(cf.value_column_for_type("boolean"), "value_boolean")

    def test_a_key_is_derived_from_the_name_when_none_is_given(self):
        # An owner typing "Rip status" should not also have to invent an id.
        self.assertEqual(cf.normalize_field_key("", name="Rip status"), "rip_status")
        self.assertEqual(cf.normalize_field_key("", name="Shelf #3!"), "shelf_3")

    def test_a_malformed_key_is_refused(self):
        for bad in ("", "3shelf", "shelf-3", "shelf 3", "x" * 60):
            with self.subTest(value=bad):
                with self.assertRaises(NextApiError):
                    cf.normalize_field_key(bad)

    def test_an_explicit_key_is_refused_rather_than_trimmed(self):
        # Truncating a key the owner typed would store something they did not
        # choose, under a name saved filters and stored values then depend on.
        with self.assertRaises(NextApiError):
            cf.normalize_field_key("x" * 60)
        # Derived from a name it is trimmed instead: nobody saw that string.
        self.assertEqual(len(cf.normalize_field_key("", name="y" * 60)), 48)

    def test_case_is_folded_rather_than_refused(self):
        # A slug is lower case by definition, and an owner typing "Shelf" means
        # the same field as one typing "shelf".
        self.assertEqual(cf.normalize_field_key("Shelf"), "shelf")

    def test_options_only_mean_something_for_select(self):
        # Not an error for the others: a field that stops being a select keeps
        # its rows valid, and unread options are harmless where a hard failure
        # mid-edit is not.
        self.assertEqual(cf.normalize_options(["a"], field_type="text"), [])
        self.assertEqual(
            cf.normalize_options(["ripped"], field_type="select"),
            [{"key": "ripped", "label": "ripped"}],
        )

    def test_a_select_with_no_options_is_refused(self):
        with self.assertRaises(NextApiError):
            cf.normalize_options([], field_type="select")


class ValueTypingTests(unittest.TestCase):
    def _field(self, field_type, **extra):
        return {"key": "probe", "field_type": field_type, **extra}

    def test_each_type_lands_in_its_own_column(self):
        cases = [
            ("text", "hello", "value_text"),
            ("number", "8.5", "value_number"),
            ("date", "2024-05-01", "value_date"),
            ("boolean", True, "value_boolean"),
        ]
        for field_type, value, column in cases:
            with self.subTest(field_type=field_type):
                resolved = cf.normalize_field_value(value, field=self._field(field_type))
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved[0], column)

    def test_zero_and_false_are_values_and_not_emptiness(self):
        self.assertEqual(
            cf.normalize_field_value(0, field=self._field("number"))[1], 0
        )
        self.assertIs(
            cf.normalize_field_value(False, field=self._field("boolean"))[1], False
        )

    def test_null_and_blank_clear_the_value(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                self.assertIsNone(cf.normalize_field_value(value, field=self._field("text")))

    def test_a_number_that_is_not_a_number_is_refused(self):
        for value in ("eight", "1,5", "nan", "inf"):
            with self.subTest(value=value):
                with self.assertRaises(NextApiError):
                    cf.normalize_field_value(value, field=self._field("number"))

    def test_a_date_must_be_iso(self):
        with self.assertRaises(NextApiError):
            cf.normalize_field_value("01/05/2024", field=self._field("date"))

    def test_a_select_refuses_a_value_outside_its_options(self):
        field = self._field("select", options=[{"key": "ripped", "label": "Ripped"}])
        self.assertEqual(cf.normalize_field_value("ripped", field=field)[1], "ripped")
        with self.assertRaises(NextApiError):
            cf.normalize_field_value("pending", field=field)

    def test_booleans_accept_the_spellings_a_wire_actually_carries(self):
        for value, expected in (("true", True), ("YES", True), ("0", False), ("no", False)):
            with self.subTest(value=value):
                self.assertIs(
                    cf.normalize_field_value(value, field=self._field("boolean"))[1], expected
                )


class WireShapeTests(unittest.TestCase):
    def test_a_value_carries_its_type_beside_it(self):
        # So a client applying the two changes in the other order, or holding no
        # definition yet, can still render what it has instead of dropping it.
        entity = cf.value_entity(
            {
                "field_id": "11111111-1111-1111-1111-111111111111",
                "key": "shelf",
                "field_type": "text",
                "value_text": "A3",
                "updated_at": None,
            }
        )
        self.assertEqual(entity["type"], "text")
        self.assertEqual(entity["value"], "A3")
        self.assertEqual(entity["key"], "shelf")

    def test_a_definition_publishes_its_archived_flag(self):
        entity = cf.field_definition_entity(
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "key": "shelf",
                "name": "Shelf",
                "field_type": "text",
                "options": [],
                "sort_order": 0,
                "archived_at": None,
                "created_at": None,
                "updated_at": None,
            }
        )
        self.assertIn("archivedAt", entity)
        self.assertEqual(entity["fieldType"], "text")


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class CustomFieldStorageTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        self.field_ids = []
        self.movie_id = self._insert_movie()

    def tearDown(self):
        try:
            with self.conn.cursor() as cur:
                cur.execute("DELETE FROM movie_custom_field_values WHERE movie_id=%s", (self.movie_id,))
                cur.execute("DELETE FROM movies WHERE id=%s", (self.movie_id,))
                if self.field_ids:
                    cur.execute(
                        "DELETE FROM custom_field_definitions WHERE id = ANY(%s)", (self.field_ids,)
                    )
            self.conn.commit()
        finally:
            self.conn.close()

    def _insert_movie(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO movies (public_id, title) VALUES (%s, %s) RETURNING id",
                (f"cf-{uuid.uuid4().hex[:10]}", "Custom field probe"),
            )
            movie_id = cur.fetchone()["id"]
        self.conn.commit()
        return movie_id

    def _field(self, key, field_type, options=None):
        import json

        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO custom_field_definitions (key, name, field_type, options)
                VALUES (%s, %s, %s, %s::jsonb) RETURNING id
                """,
                (key, key.title(), field_type, json.dumps(options or [])),
            )
            field_id = cur.fetchone()["id"]
        self.conn.commit()
        self.field_ids.append(field_id)
        return field_id

    def _row(self, field_id):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM movie_custom_field_values WHERE movie_id=%s AND field_id=%s",
                (self.movie_id, field_id),
            )
            return cur.fetchone()

    def _write(self, field_id, column, value):
        with self.conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO movie_custom_field_values (movie_id, field_id, {column})"
                " VALUES (%s, %s, %s)",
                (self.movie_id, field_id, value),
            )
        self.conn.commit()

    def test_the_key_shape_is_enforced_by_the_database(self):
        for bad in ("Shelf", "3shelf", "shelf-3", ""):
            with self.subTest(value=bad):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with self.conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO custom_field_definitions (key, name, field_type)"
                            " VALUES (%s, 'X', 'text')",
                            (bad,),
                        )
                self.conn.rollback()

    def test_an_unknown_field_type_is_refused_by_the_database(self):
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO custom_field_definitions (key, name, field_type)"
                    " VALUES ('x', 'X', 'rating')"
                )
        self.conn.rollback()

    def test_a_row_must_carry_exactly_one_value(self):
        field_id = self._field("shelf", "text")
        # None at all.
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO movie_custom_field_values (movie_id, field_id) VALUES (%s, %s)",
                    (self.movie_id, field_id),
                )
        self.conn.rollback()
        # Two at once.
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO movie_custom_field_values"
                    " (movie_id, field_id, value_text, value_number) VALUES (%s, %s, 'a', 1)",
                    (self.movie_id, field_id),
                )
        self.conn.rollback()

    def test_a_definition_in_use_cannot_be_hard_deleted(self):
        # The tooth behind "archive, never delete": the database refuses even if
        # a future route asks.
        field_id = self._field("shelf", "text")
        self._write(field_id, "value_text", "A3")
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            with self.conn.cursor() as cur:
                cur.execute("DELETE FROM custom_field_definitions WHERE id=%s", (field_id,))
        self.conn.rollback()

    def test_deleting_the_movie_takes_its_values_with_it(self):
        field_id = self._field("shelf", "text")
        self._write(field_id, "value_text", "A3")
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM movies WHERE id=%s", (self.movie_id,))
            cur.execute(
                "SELECT count(*) AS n FROM movie_custom_field_values WHERE movie_id=%s",
                (self.movie_id,),
            )
            self.assertEqual(cur.fetchone()["n"], 0)
        self.conn.commit()

    def test_writing_a_value_clears_the_other_columns(self):
        import next_app

        field_id = self._field("bought", "date")
        next_app.replace_movie_custom_values(
            self.conn, self.movie_id, {"bought": "2024-05-01"}
        )
        self.conn.commit()
        row = self._row(field_id)
        self.assertIsNotNone(row["value_date"])
        self.assertIsNone(row["value_text"])
        self.assertIsNone(row["value_number"])
        self.assertIsNone(row["value_boolean"])

    def test_clearing_a_value_removes_the_row(self):
        import next_app

        field_id = self._field("shelf", "text")
        next_app.replace_movie_custom_values(self.conn, self.movie_id, {"shelf": "A3"})
        self.conn.commit()
        self.assertIsNotNone(self._row(field_id))
        next_app.replace_movie_custom_values(self.conn, self.movie_id, {"shelf": None})
        self.conn.commit()
        self.assertIsNone(self._row(field_id))

    def test_an_omitted_field_keeps_its_value(self):
        # The body is sparse: it states only what it means to change.
        import next_app

        shelf = self._field("shelf", "text")
        self._field("bought", "date")
        next_app.replace_movie_custom_values(
            self.conn, self.movie_id, {"shelf": "A3", "bought": "2024-05-01"}
        )
        self.conn.commit()
        next_app.replace_movie_custom_values(self.conn, self.movie_id, {"bought": "2025-01-02"})
        self.conn.commit()
        self.assertEqual(self._row(shelf)["value_text"], "A3")

    def test_writing_to_an_archived_field_is_refused_by_name(self):
        import next_app

        field_id = self._field("shelf", "text")
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE custom_field_definitions SET archived_at=now() WHERE id=%s", (field_id,)
            )
        self.conn.commit()
        with self.assertRaises(NextApiError) as caught:
            next_app.replace_movie_custom_values(self.conn, self.movie_id, {"shelf": "A3"})
        self.assertIn("shelf", str(caught.exception))
        self.conn.rollback()

    def test_an_unknown_field_is_refused_by_name(self):
        import next_app

        with self.assertRaises(NextApiError) as caught:
            next_app.replace_movie_custom_values(self.conn, self.movie_id, {"nope": "x"})
        self.assertIn("nope", str(caught.exception))
        self.conn.rollback()

    def test_the_value_set_travels_whole_and_in_definition_order(self):
        import next_app

        self._field("shelf", "text")
        self._field("bought", "date")
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE custom_field_definitions SET sort_order=1 WHERE key='bought'"
            )
            cur.execute("UPDATE custom_field_definitions SET sort_order=0 WHERE key='shelf'")
        self.conn.commit()
        next_app.replace_movie_custom_values(
            self.conn, self.movie_id, {"shelf": "A3", "bought": "2024-05-01"}
        )
        self.conn.commit()
        entity = next_app.movie_custom_values_sync_entity(self.conn, self.movie_id)
        self.assertEqual([item["key"] for item in entity["values"]], ["shelf", "bought"])
        self.assertEqual(entity["movieId"], str(self.movie_id))

    def test_an_empty_set_is_still_an_entity(self):
        # A receiver replaces the set wholesale, so an empty set is how the last
        # value is removed. A None here would read as "no change".
        import next_app

        entity = next_app.movie_custom_values_sync_entity(self.conn, self.movie_id)
        self.assertEqual(entity["values"], [])
        self.assertIn("movieId", entity)

    def test_archived_definitions_still_reach_the_bootstrap(self):
        # A client holding a value for an archived field needs its name and type
        # to render it (contract 4e.5).
        import next_app

        field_id = self._field("shelf", "text")
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE custom_field_definitions SET archived_at=now() WHERE id=%s", (field_id,)
            )
        self.conn.commit()
        keys = [item["key"] for item in next_app.all_custom_field_entities(self.conn)]
        self.assertIn("shelf", keys)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class UpwardMutationShapeTests(unittest.TestCase):
    """Values go up per field, and that shape is the protection.

    A set-shaped upward path would mean a phone modelling three of five fields
    sends its three back and deletes the other two -- no error, no log, which is
    what §4d.2 already says about own images. The alternative is an echo
    obligation nothing on the wire enforces. Per-field operations make the loss
    impossible rather than agreed, so the test that matters is that a mutation
    naming one field leaves every other field alone.
    """

    def setUp(self):
        self.conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        self.field_ids = []
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO movies (public_id, title) VALUES (%s, %s) RETURNING id",
                (f"cfm-{uuid.uuid4().hex[:9]}", "Mutation probe"),
            )
            self.movie_id = cur.fetchone()["id"]
        self.conn.commit()

    def tearDown(self):
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM movie_custom_field_values WHERE movie_id=%s", (self.movie_id,)
                )
                cur.execute("DELETE FROM movies WHERE id=%s", (self.movie_id,))
                if self.field_ids:
                    cur.execute(
                        "DELETE FROM custom_field_definitions WHERE id = ANY(%s)", (self.field_ids,)
                    )
            self.conn.commit()
        finally:
            self.conn.close()

    def _field(self, key, field_type="text"):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO custom_field_definitions (key, name, field_type)"
                " VALUES (%s, %s, %s) RETURNING id",
                (key, key.title(), field_type),
            )
            field_id = cur.fetchone()["id"]
        self.conn.commit()
        self.field_ids.append(field_id)
        return field_id

    def _mutation(self, operation, payload):
        return {
            "clientMutationId": uuid.uuid4().hex,
            "entityType": "movieCustomValue",
            "operation": operation,
            "payload": payload,
        }

    def _apply(self, operation, payload):
        import next_app

        applier = (
            next_app.apply_custom_value_upsert
            if operation == "upsert"
            else next_app.apply_custom_value_delete
        )
        return applier(
            self.conn,
            client_id="probe-device",
            idem_key=uuid.uuid4().hex,
            mutation=self._mutation(operation, payload),
        )

    def test_a_mutation_names_one_field_and_leaves_the_rest_alone(self):
        import next_app

        self._field("shelf")
        self._field("bought", "date")
        next_app.replace_movie_custom_values(
            self.conn, self.movie_id, {"shelf": "A3", "bought": "2024-05-01"}
        )
        self.conn.commit()
        self._apply("upsert", {"movieId": str(self.movie_id), "key": "shelf", "value": "B7"})
        self.conn.commit()
        values = {
            item["key"]: item["value"]
            for item in next_app.movie_custom_values(self.conn, self.movie_id)
        }
        self.assertEqual(values["shelf"], "B7")
        # The whole point: a field the mutation never mentioned is untouched.
        self.assertEqual(values["bought"], "2024-05-01")

    def test_the_applier_returns_the_whole_set_so_one_round_trip_converges(self):
        self._field("shelf")
        result = self._apply(
            "upsert", {"movieId": str(self.movie_id), "key": "shelf", "value": "A3"}
        )
        self.conn.commit()
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["entityType"], "movieCustomValue")
        self.assertEqual(result["entityId"], str(self.movie_id))
        self.assertEqual([item["key"] for item in result["customValues"]], ["shelf"])

    def test_delete_is_its_own_operation_and_is_safe_to_repeat(self):
        import next_app

        self._field("shelf")
        next_app.replace_movie_custom_values(self.conn, self.movie_id, {"shelf": "A3"})
        self.conn.commit()
        first = self._apply("delete", {"movieId": str(self.movie_id), "key": "shelf"})
        self.conn.commit()
        second = self._apply("delete", {"movieId": str(self.movie_id), "key": "shelf"})
        self.conn.commit()
        self.assertEqual(first["changed"], 1)
        # A queued delete may be offered again after a dropped connection; the
        # state the caller asked for is the state that holds.
        self.assertEqual(second["status"], "applied")
        self.assertEqual(second["changed"], 0)

    def test_an_unknown_field_is_rejected_by_name_not_ignored(self):
        with self.assertRaises(NextApiError) as caught:
            self._apply("upsert", {"movieId": str(self.movie_id), "key": "nope", "value": "x"})
        self.assertIn("nope", str(caught.exception))
        self.conn.rollback()

    def test_a_value_for_an_archived_field_is_refused(self):
        # A phone that filled one in offline while the owner archived the field
        # should hear about it. An ignored mutation cannot be told apart from a
        # successful one.
        field_id = self._field("shelf")
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE custom_field_definitions SET archived_at=now() WHERE id=%s", (field_id,)
            )
        self.conn.commit()
        with self.assertRaises(NextApiError):
            self._apply("upsert", {"movieId": str(self.movie_id), "key": "shelf", "value": "A3"})
        self.conn.rollback()

    def test_a_field_can_be_named_by_id_as_well_as_by_key(self):
        import next_app

        field_id = self._field("shelf")
        self._apply(
            "upsert", {"movieId": str(self.movie_id), "fieldId": str(field_id), "value": "A3"}
        )
        self.conn.commit()
        values = next_app.movie_custom_values(self.conn, self.movie_id)
        self.assertEqual(values[0]["value"], "A3")


class MutationsAreAdvertisedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BACKEND_DIR, "next_app.py"), encoding="utf-8") as handle:
            cls.source = handle.read()

    def test_the_two_operations_are_dispatched_and_advertised(self):
        for operation in ("upsert", "delete"):
            with self.subTest(operation=operation):
                self.assertIn(
                    f'entity_type == "movieCustomValue" and operation == "{operation}"', self.source
                )
                self.assertIn(f'"movieCustomValue.{operation}"', self.source)

    def test_a_definition_has_no_mutation_arm(self):
        # Definitions are created against the server (§4e.3a), not from an
        # offline queue: a create path with no tier 1 produces exactly the
        # duplicates this contract exists to prevent — 1.11's reason for denying
        # locations one.
        self.assertNotIn('entity_type == "customField"', self.source)
        self.assertNotIn('"customField.upsert"', self.source)
