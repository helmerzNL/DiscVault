import os
import sys
import unittest
import uuid
from decimal import Decimal


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:
    psycopg = None
    dict_row = None

from app.backend import next_app
from app.backend import next_metadata


DATABASE_URL = os.environ.get("DATABASE_URL")


class MovieEstimatedValueFieldMappingTests(unittest.TestCase):
    """Pure unit coverage for the payload plumbing, no database needed."""

    def test_movie_payload_fields_accepts_camel_and_snake_case(self):
        camel = next_app.movie_payload_fields({"title": "T", "estimatedValue": "42.50"})
        snake = next_app.movie_payload_fields({"title": "T", "estimated_value": "42.50"})
        self.assertEqual(camel["estimated_value"], "42.50")
        self.assertEqual(snake["estimated_value"], "42.50")

    def test_movie_estimated_value_accepts_a_comma_decimal_separator(self):
        value = next_app.movie_estimated_value({"estimatedValue": "19,99"}, existing={})
        self.assertEqual(value, Decimal("19.99"))

    def test_movie_estimated_value_rounds_to_two_decimals(self):
        value = next_app.movie_estimated_value({"estimatedValue": "19.999"}, existing={})
        self.assertEqual(value, Decimal("20.00"))

    def test_movie_estimated_value_falls_back_to_existing_when_absent_from_body(self):
        value = next_app.movie_estimated_value({}, existing={"estimated_value": Decimal("10.00")})
        self.assertEqual(value, Decimal("10.00"))

    def test_movie_estimated_value_clears_on_explicit_empty_string(self):
        value = next_app.movie_estimated_value(
            {"estimatedValue": ""}, existing={"estimated_value": Decimal("10.00")}
        )
        self.assertIsNone(value)

    def test_movie_estimated_value_rejects_non_numeric_input(self):
        with self.assertRaises(next_app.NextApiError):
            next_app.movie_estimated_value({"estimatedValue": "not-a-number"}, existing={})

    def test_estimated_value_is_excluded_from_the_metadata_merge_pipeline(self):
        # A metadata plugin (MovieVault, TMDB, ...) must never be able to
        # overwrite a manually-entered value, exactly like purchase_price.
        self.assertIn("estimated_value", next_metadata.METADATA_LOCAL_ONLY_FIELDS)


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class MovieEstimatedValuePostgresTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def _insert_bare_movie(self, conn, *, title="Estimated Value Test Movie") -> uuid.UUID:
        movie_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movies (id, public_id, title, sort_title)
                VALUES (%s, %s, %s, %s)
                """,
                (movie_id, f"estimated-value-test-{movie_id}", title, title),
            )
        conn.commit()
        return movie_id

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM movies WHERE public_id LIKE 'estimated-value-test-%'")
            conn.commit()

    def test_write_movie_edit_record_persists_estimated_value_and_movie_entity_reads_it_back(self):
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            existing = next_app.movie_entity(conn, movie_id)
            self.assertIsNone(existing["estimated_value"])

            payload = next_app.movie_update_payload(
                {"title": existing["title"], "estimatedValue": "129.95"}, existing=existing
            )
            with conn.cursor() as cur:
                next_app.write_movie_edit_record(cur, movie_id, payload)
            conn.commit()

            updated = next_app.movie_entity(conn, movie_id)
        self.assertEqual(updated["estimated_value"], Decimal("129.95"))

    def test_all_movie_entities_includes_estimated_value_for_bootstrap_sync(self):
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn, title="Bootstrap Value Movie")
            existing = next_app.movie_entity(conn, movie_id)
            payload = next_app.movie_update_payload(
                {"title": existing["title"], "estimatedValue": "5.00"}, existing=existing
            )
            with conn.cursor() as cur:
                next_app.write_movie_edit_record(cur, movie_id, payload)
            conn.commit()

            entities = next_app.all_movie_entities(conn, limit=5000)
        match = next((row for row in entities if row["id"] == movie_id), None)
        self.assertIsNotNone(match)
        self.assertEqual(match["estimated_value"], Decimal("5.00"))

    def test_editing_unrelated_field_does_not_clear_a_previously_set_estimated_value(self):
        """Sync mutations from other clients (and the api/v1 upsert path) must
        not blank out a value the user already entered elsewhere - mirrors
        the COALESCE behaviour purchase_price already relies on."""
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            existing = next_app.movie_entity(conn, movie_id)
            payload = next_app.movie_update_payload(
                {"title": existing["title"], "estimatedValue": "75.00"}, existing=existing
            )
            with conn.cursor() as cur:
                next_app.write_movie_edit_record(cur, movie_id, payload)
            conn.commit()

            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE movies SET estimated_value = COALESCE(%s, estimated_value) WHERE id = %s",
                    (None, movie_id),
                )
            conn.commit()

            unchanged = next_app.movie_entity(conn, movie_id)
        self.assertEqual(unchanged["estimated_value"], Decimal("75.00"))


class MovieEstimatedValueCurrencyTests(unittest.TestCase):
    """An amount without its unit is not money. The currency is stored next to
    the value, and is never guessed."""

    def test_accepts_and_uppercases_a_three_letter_code(self):
        self.assertEqual(
            next_app.movie_estimated_value_currency({"estimatedValueCurrency": " usd "}, {}), "USD"
        )
        self.assertEqual(
            next_app.movie_estimated_value_currency({"estimated_value_currency": "eur"}, {}), "EUR"
        )

    def test_an_empty_string_clears_the_currency(self):
        existing = {"estimated_value_currency": "USD"}
        self.assertIsNone(
            next_app.movie_estimated_value_currency({"estimatedValueCurrency": ""}, existing)
        )

    def test_an_absent_key_keeps_the_stored_currency(self):
        existing = {"estimated_value_currency": "GBP"}
        self.assertEqual(next_app.movie_estimated_value_currency({}, existing), "GBP")

    def test_a_malformed_code_is_rejected(self):
        for bad in ("EU", "EUROS", "E1R", "€"):
            with self.subTest(bad=bad):
                with self.assertRaises(next_app.NextApiError):
                    next_app.movie_estimated_value_currency({"estimatedValueCurrency": bad}, {})

    def test_a_code_outside_the_picker_list_is_still_accepted(self):
        """The picker offers what the converter can fetch rates for, but a stored
        row must never become unsaveable because that list changed."""
        self.assertEqual(
            next_app.movie_estimated_value_currency({"estimatedValueCurrency": "SEK"}, {}), "SEK"
        )

    def test_no_currency_is_invented_for_a_value_that_has_none(self):
        """Defaulting to EUR, or to the display preference, would attach a unit to
        money nobody stated - and the preference can change later."""
        payload = next_app.movie_update_payload({"title": "X", "estimatedValue": "25"}, existing={})
        self.assertIsNone(payload["estimated_value_currency"])

    def test_it_is_local_only_like_the_amount(self):
        self.assertIn("estimated_value_currency", next_metadata.METADATA_LOCAL_ONLY_FIELDS)


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class MovieEstimatedValueCurrencyPostgresTests(MovieEstimatedValuePostgresTests):
    def test_currency_round_trips_and_reaches_the_sync_bootstrap(self):
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            existing = next_app.movie_entity(conn, movie_id)
            payload = next_app.movie_update_payload(
                {
                    "title": existing["title"],
                    "estimatedValue": "129,95",
                    "estimatedValueCurrency": "gbp",
                },
                existing=existing,
            )
            with conn.cursor() as cur:
                next_app.write_movie_edit_record(cur, movie_id, payload)
            conn.commit()

            stored = next_app.movie_entity(conn, movie_id)
            self.assertEqual(stored["estimated_value"], Decimal("129.95"))
            self.assertEqual(stored["estimated_value_currency"], "GBP")

            # The bootstrap is what iOS and Android read on a fresh sync.
            rows = next_app.all_movie_entities(conn, limit=500)
            row = next(r for r in rows if str(r["id"]) == str(movie_id))
            self.assertEqual(row["estimated_value_currency"], "GBP")

    def test_a_sync_upsert_without_the_key_does_not_clear_the_currency(self):
        """encodeDefaults=false on Android and encodeIfPresent on iOS both omit an
        untouched field, so the upsert must COALESCE rather than overwrite."""
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            existing = next_app.movie_entity(conn, movie_id)
            payload = next_app.movie_update_payload(
                {"title": existing["title"], "estimatedValue": "40", "estimatedValueCurrency": "CHF"},
                existing=existing,
            )
            with conn.cursor() as cur:
                next_app.write_movie_edit_record(cur, movie_id, payload)
            conn.commit()

            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE movies SET estimated_value_currency ="
                    " COALESCE(%s, estimated_value_currency) WHERE id = %s",
                    (None, movie_id),
                )
            conn.commit()

            self.assertEqual(
                next_app.movie_entity(conn, movie_id)["estimated_value_currency"], "CHF"
            )


if __name__ == "__main__":
    unittest.main()
