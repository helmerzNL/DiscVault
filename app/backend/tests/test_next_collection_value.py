"""What a collection-value total is allowed to claim.

The charts these functions feed are about money, and a money chart earns trust
by being explicit about what it could not count. Two rules carry most of that
weight and both are easy to "simplify" away later:

- an amount whose currency was never recorded is **not** EUR and **not** the
  display preference — assuming either invents a fact the user never entered;
- an amount that cannot be converted is left out of the total *and counted*,
  because a total that silently swallows it looks complete and is wrong.

The tests below fail if either becomes convenient.
"""

import os
import sys
import unittest
from datetime import date
from decimal import Decimal


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_collection_value as cv


RATES = {"EUR": 1.0, "USD": 1.10, "GBP": 0.85, "JPY": 160.0}


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return {"reg": "public.movies"}


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)


class ConvertAmountTests(unittest.TestCase):
    def test_same_currency_is_returned_untouched(self):
        self.assertEqual(
            Decimal("12.50"),
            cv.convert_amount(Decimal("12.50"), from_currency="EUR", to_currency="EUR", rates=RATES),
        )

    def test_cross_conversion_goes_through_the_base(self):
        converted = cv.convert_amount(Decimal("110"), from_currency="USD", to_currency="EUR", rates=RATES)
        self.assertAlmostEqual(100.0, float(converted), places=2)

    def test_an_unknown_currency_cannot_be_converted(self):
        # Returning the raw amount here would quietly add dollars to a euro
        # total. None forces the caller to count it as uncounted instead.
        self.assertIsNone(
            cv.convert_amount(Decimal("10"), from_currency="XYZ", to_currency="EUR", rates=RATES)
        )

    def test_a_missing_currency_cannot_be_converted(self):
        self.assertIsNone(
            cv.convert_amount(Decimal("10"), from_currency="", to_currency="EUR", rates=RATES)
        )


class ComputeCollectionValueTests(unittest.TestCase):
    def _compute(self, rows, currency="EUR"):
        return cv.compute_collection_value(_FakeConn(rows), "user-1", currency=currency, rates=RATES)

    def test_sums_only_what_it_can_convert(self):
        totals = self._compute(
            [
                {"estimated_value": Decimal("10.00"), "estimated_value_currency": "EUR"},
                {"estimated_value": Decimal("11.00"), "estimated_value_currency": "USD"},  # -> 10 EUR
            ]
        )
        self.assertEqual(Decimal("20.00"), totals["totalValue"])
        self.assertEqual(2, totals["valuedCount"])
        self.assertEqual(0, totals["unconvertibleCount"])

    def test_an_amount_without_a_currency_is_counted_not_assumed(self):
        """The rule App-Guidance `estimated-value-currency.md` §1 exists for.

        NULL means "not recorded". Treating it as EUR would put a number in the
        total that the user never agreed to.
        """
        totals = self._compute(
            [
                {"estimated_value": Decimal("10.00"), "estimated_value_currency": "EUR"},
                {"estimated_value": Decimal("999.00"), "estimated_value_currency": None},
            ]
        )
        self.assertEqual(Decimal("10.00"), totals["totalValue"])
        self.assertEqual(1, totals["unconvertibleCount"])
        self.assertEqual(1, totals["valuedCount"])

    def test_discs_without_a_value_are_counted_separately_from_unconvertible_ones(self):
        # Two different states with two different fixes: one needs a value, the
        # other needs a currency. Collapsing them would tell the user the wrong
        # thing to do.
        totals = self._compute(
            [
                {"estimated_value": None, "estimated_value_currency": None},
                {"estimated_value": Decimal("5.00"), "estimated_value_currency": None},
            ]
        )
        self.assertEqual(1, totals["unpricedCount"])
        self.assertEqual(1, totals["unconvertibleCount"])
        self.assertEqual(0, totals["valuedCount"])

    def test_movie_count_covers_everything_seen(self):
        totals = self._compute(
            [
                {"estimated_value": Decimal("1.00"), "estimated_value_currency": "EUR"},
                {"estimated_value": None, "estimated_value_currency": None},
                {"estimated_value": Decimal("2.00"), "estimated_value_currency": None},
            ]
        )
        self.assertEqual(3, totals["movieCount"])
        self.assertEqual(
            totals["movieCount"],
            totals["valuedCount"] + totals["unpricedCount"] + totals["unconvertibleCount"],
            "every disc must land in exactly one bucket",
        )

    def test_an_empty_collection_totals_zero_rather_than_failing(self):
        totals = self._compute([])
        self.assertEqual(Decimal("0.00"), totals["totalValue"])
        self.assertEqual(0, totals["movieCount"])

    def test_a_malformed_amount_is_treated_as_unpriced(self):
        totals = self._compute([{"estimated_value": "not-a-number", "estimated_value_currency": "EUR"}])
        self.assertEqual(1, totals["unpricedCount"])
        self.assertEqual(Decimal("0.00"), totals["totalValue"])


class SnapshotIntervalTests(unittest.TestCase):
    def setUp(self):
        self._original = os.environ.get("DISCVAULT_VALUE_SNAPSHOT_INTERVAL_HOURS")
        self.addCleanup(self._restore)

    def _restore(self):
        if self._original is None:
            os.environ.pop("DISCVAULT_VALUE_SNAPSHOT_INTERVAL_HOURS", None)
        else:
            os.environ["DISCVAULT_VALUE_SNAPSHOT_INTERVAL_HOURS"] = self._original

    def test_defaults_to_daily(self):
        os.environ.pop("DISCVAULT_VALUE_SNAPSHOT_INTERVAL_HOURS", None)
        self.assertEqual(24.0, cv.snapshot_interval_hours())

    def test_a_nonsense_value_falls_back_rather_than_crashing_the_worker(self):
        os.environ["DISCVAULT_VALUE_SNAPSHOT_INTERVAL_HOURS"] = "soon"
        self.assertEqual(24.0, cv.snapshot_interval_hours())

    def test_the_interval_is_clamped(self):
        os.environ["DISCVAULT_VALUE_SNAPSHOT_INTERVAL_HOURS"] = "0"
        self.assertEqual(1.0, cv.snapshot_interval_hours())


@unittest.skipUnless(os.environ.get("DATABASE_URL"), "PostgreSQL test database is not configured")
class CollectionValuePostgresTests(unittest.TestCase):
    """The capture and the two trends against a real database."""

    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row

        self._psycopg = psycopg
        self._dict_row = dict_row
        self.user_id = self._create_user()

    def connect(self):
        return self._psycopg.connect(
            os.environ["DATABASE_URL"], row_factory=self._dict_row, autocommit=False
        )

    def _create_user(self):
        import uuid

        user_id = uuid.uuid4()
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (id, username) VALUES (%s, %s)",
                    (user_id, f"value-test-{user_id}"),
                )
            conn.commit()
        return user_id

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM movies WHERE public_id LIKE 'value-test-%'")
                cur.execute("DELETE FROM collection_value_snapshots WHERE user_id=%s", (self.user_id,))
                cur.execute("DELETE FROM users WHERE id=%s", (self.user_id,))
            conn.commit()

    def _movie(self, conn, *, value=None, currency=None, purchase_price=None, purchase_date=None):
        import uuid

        movie_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movies (
                    id, public_id, title, sort_title, owner_id,
                    estimated_value, estimated_value_currency, purchase_price, purchase_date
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    movie_id,
                    f"value-test-{movie_id}",
                    "Valued",
                    "Valued",
                    self.user_id,
                    value,
                    currency,
                    purchase_price,
                    purchase_date,
                ),
            )
        conn.commit()
        return movie_id

    def test_capture_is_idempotent_within_a_day(self):
        """A restarted worker must not put two points on one date."""
        with self.connect() as conn:
            self._movie(conn, value=Decimal("20.00"), currency="EUR")
            cv.capture_collection_value_snapshots(conn, captured_on=date(2026, 8, 5))
            conn.commit()
            cv.capture_collection_value_snapshots(conn, captured_on=date(2026, 8, 5))
            conn.commit()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*)::int AS n FROM collection_value_snapshots WHERE user_id=%s",
                    (self.user_id,),
                )
                self.assertEqual(1, cur.fetchone()["n"])

    def test_a_second_day_adds_a_point_and_the_trend_reports_the_change(self):
        with self.connect() as conn:
            self._movie(conn, value=Decimal("20.00"), currency="EUR")
            cv.capture_collection_value_snapshots(conn, captured_on=date(2026, 8, 4))
            conn.commit()
            self._movie(conn, value=Decimal("30.00"), currency="EUR")
            cv.capture_collection_value_snapshots(conn, captured_on=date(2026, 8, 5))
            conn.commit()

            trend = cv.collection_value_trend(conn, self.user_id, days=3650)
            self.assertEqual(2, len(trend["points"]))
            self.assertTrue(trend["hasHistory"])
            self.assertEqual(50.0, trend["currentValue"])
            self.assertEqual(30.0, trend["changeFromStart"])

    def test_no_snapshots_yet_is_an_empty_series_not_an_error(self):
        """The normal state on the day this ships."""
        with self.connect() as conn:
            trend = cv.collection_value_trend(conn, self.user_id)
        self.assertEqual([], trend["points"])
        self.assertFalse(trend["hasHistory"])

    def test_the_purchase_trend_accumulates_and_needs_no_capture(self):
        with self.connect() as conn:
            self._movie(conn, currency="EUR", purchase_price=Decimal("10.00"), purchase_date=date(2024, 1, 15))
            self._movie(conn, currency="EUR", purchase_price=Decimal("5.00"), purchase_date=date(2024, 1, 20))
            self._movie(conn, currency="EUR", purchase_price=Decimal("7.00"), purchase_date=date(2024, 3, 2))

            trend = cv.purchase_trend(conn, self.user_id, currency="EUR", rates=RATES)

        self.assertEqual(2, len(trend["points"]), "two months with purchases")
        self.assertEqual(15.0, trend["points"][0]["cumulativeSpend"])
        self.assertEqual(22.0, trend["points"][1]["cumulativeSpend"])
        self.assertEqual(3, trend["totalCount"])
        self.assertEqual(22.0, trend["totalSpend"])

    def test_a_dated_disc_without_a_price_counts_but_does_not_add(self):
        with self.connect() as conn:
            self._movie(conn, currency="EUR", purchase_price=Decimal("10.00"), purchase_date=date(2024, 1, 15))
            self._movie(conn, currency="EUR", purchase_price=None, purchase_date=date(2024, 2, 15))

            trend = cv.purchase_trend(conn, self.user_id, currency="EUR", rates=RATES)

        self.assertEqual(2, trend["totalCount"])
        self.assertEqual(10.0, trend["totalSpend"])
        self.assertEqual(1, trend["unpricedCount"])
