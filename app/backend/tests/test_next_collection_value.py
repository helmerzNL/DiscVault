import os
import sys
import unittest
import uuid
from datetime import date
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

from app.backend import next_collection_value


DATABASE_URL = os.environ.get("DATABASE_URL")

RATES = {"EUR": 1.0, "USD": 2.0, "GBP": 0.5}


class CurrencyBucketTests(unittest.TestCase):
    def test_a_single_currency_converts_at_its_rate(self):
        total, unconvertible = next_collection_value.convert_bucket(
            {"USD": Decimal("20.00")}, base_currency="EUR", exchange_rates=RATES
        )
        self.assertEqual(total, Decimal("10.00"))
        self.assertEqual(unconvertible, Decimal("0.00"))

    def test_mixed_currencies_are_summed_in_the_base(self):
        total, _ = next_collection_value.convert_bucket(
            {"EUR": Decimal("5.00"), "USD": Decimal("20.00"), "GBP": Decimal("5.00")},
            base_currency="EUR",
            exchange_rates=RATES,
        )
        self.assertEqual(total, Decimal("25.00"))

    def test_amounts_without_a_currency_are_reported_not_converted(self):
        # movies.estimated_value_currency is nullable on purpose. Folding an
        # amount of unknown unit into the total would invent a fact.
        total, unconvertible = next_collection_value.convert_bucket(
            {"": Decimal("99.00"), "EUR": Decimal("1.00")},
            base_currency="EUR",
            exchange_rates=RATES,
        )
        self.assertEqual(total, Decimal("1.00"))
        self.assertEqual(unconvertible, Decimal("99.00"))

    def test_a_currency_the_rate_table_does_not_carry_is_reported_separately(self):
        total, unconvertible = next_collection_value.convert_bucket(
            {"SEK": Decimal("50.00")}, base_currency="EUR", exchange_rates=RATES
        )
        self.assertEqual(total, Decimal("0.00"))
        self.assertEqual(unconvertible, Decimal("50.00"))

    def test_an_empty_bucket_is_zero(self):
        total, unconvertible = next_collection_value.convert_bucket({}, exchange_rates=RATES)
        self.assertEqual(total, Decimal("0.00"))
        self.assertEqual(unconvertible, Decimal("0.00"))


class ValueLockPredicateTests(unittest.TestCase):
    def test_the_lock_predicate_only_looks_at_live_box_sets(self):
        sql = next_collection_value.movie_value_locked_sql("m")
        self.assertIn("container_type = 'box_set'", sql)
        self.assertIn("deleted_at IS NULL", sql)
        self.assertIn("val_cm.movie_id = m.id", sql)

    def test_the_predicate_honours_the_alias_it_is_given(self):
        self.assertIn("val_cm.movie_id = mv.id", next_collection_value.movie_value_locked_sql("mv"))


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class CollectionValuePostgresTests(unittest.TestCase):
    """The ownership rule, end to end against a real schema."""

    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def _movie(self, conn, title, value=None, currency=None):
        movie_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movies (id, public_id, title, sort_title, estimated_value, estimated_value_currency)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (movie_id, f"collection-value-test-{movie_id}", title, title, value, currency),
            )
        conn.commit()
        return movie_id

    def _container(self, conn, container_type, title, value=None, currency=None):
        container_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO containers (id, public_id, container_type, title, estimated_value, estimated_value_currency)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (container_id, f"collection-value-test-{container_id}", container_type, title, value, currency),
            )
        conn.commit()
        return container_id

    def _link(self, conn, container_id, movie_id):
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO container_movies (container_id, movie_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (container_id, movie_id),
            )
        conn.commit()

    def _unlink(self, conn, container_id, movie_id):
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM container_movies WHERE container_id=%s AND movie_id=%s",
                (container_id, movie_id),
            )
        conn.commit()

    def _collection_item(self, conn, collection_id, item_type, item_id):
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO collection_items (collection_id, item_type, item_id)
                VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                """,
                (collection_id, item_type, item_id),
            )
        conn.commit()

    def _value(self, conn, **overrides):
        kwargs = {
            "movie_where": "m.deleted_at IS NULL AND m.public_id LIKE 'collection-value-test-%%'",
            "movie_params": [],
            "container_where": "c.public_id LIKE 'collection-value-test-%%'",
            "container_params": [],
            "exchange_rates": RATES,
        }
        kwargs.update(overrides)
        return next_collection_value.compute_collection_value(conn, **kwargs)

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM container_movies
                    WHERE container_id IN (SELECT id FROM containers WHERE public_id LIKE 'collection-value-test-%')
                       OR movie_id IN (SELECT id FROM movies WHERE public_id LIKE 'collection-value-test-%')
                    """
                )
                cur.execute(
                    """
                    DELETE FROM collection_items
                    WHERE collection_id IN (SELECT id FROM containers WHERE public_id LIKE 'collection-value-test-%')
                    """
                )
                cur.execute("DELETE FROM containers WHERE public_id LIKE 'collection-value-test-%'")
                cur.execute("DELETE FROM movies WHERE public_id LIKE 'collection-value-test-%'")
            conn.commit()

    def test_a_free_movie_counts_toward_the_total(self):
        with self.connect() as conn:
            self._movie(conn, "Free Film", Decimal("25.00"), "EUR")
            summary = self._value(conn)
        self.assertEqual(Decimal(summary["total"]), Decimal("25.00"))
        self.assertEqual(summary["pricedCount"], 1)

    def test_a_box_set_member_stops_counting_but_keeps_its_amount(self):
        with self.connect() as conn:
            movie_id = self._movie(conn, "Member Film", Decimal("25.00"), "EUR")
            box_set_id = self._container(conn, "box_set", "The Set", Decimal("60.00"), "EUR")
            self._link(conn, box_set_id, movie_id)
            summary = self._value(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT estimated_value FROM movies WHERE id=%s", (movie_id,))
                stored = cur.fetchone()["estimated_value"]
        # Only the set's own price counts - the member's 25.00 is suppressed...
        self.assertEqual(Decimal(summary["total"]), Decimal("60.00"))
        # ...but not destroyed.
        self.assertEqual(stored, Decimal("25.00"))

    def test_removing_a_movie_from_a_box_set_restores_its_contribution(self):
        with self.connect() as conn:
            movie_id = self._movie(conn, "Returning Film", Decimal("25.00"), "EUR")
            box_set_id = self._container(conn, "box_set", "The Set", Decimal("60.00"), "EUR")
            self._link(conn, box_set_id, movie_id)
            self._unlink(conn, box_set_id, movie_id)
            summary = self._value(conn)
        self.assertEqual(Decimal(summary["total"]), Decimal("85.00"))

    def test_a_deleted_box_set_no_longer_locks_its_members(self):
        with self.connect() as conn:
            movie_id = self._movie(conn, "Orphaned Film", Decimal("12.00"), "EUR")
            box_set_id = self._container(conn, "box_set", "Gone Set", Decimal("99.00"), "EUR")
            self._link(conn, box_set_id, movie_id)
            with conn.cursor() as cur:
                cur.execute("UPDATE containers SET deleted_at=now() WHERE id=%s", (box_set_id,))
            conn.commit()
            summary = self._value(conn)
        self.assertEqual(Decimal(summary["total"]), Decimal("12.00"))

    def test_an_unpriced_movie_is_counted_as_unpriced_not_as_zero(self):
        with self.connect() as conn:
            self._movie(conn, "Priced", Decimal("10.00"), "EUR")
            self._movie(conn, "Unpriced")
            summary = self._value(conn)
        self.assertEqual(Decimal(summary["total"]), Decimal("10.00"))
        self.assertEqual(summary["unpricedCount"], 1)
        self.assertEqual(summary["pricedCount"], 1)

    def test_a_vault_scope_sums_its_own_members(self):
        with self.connect() as conn:
            in_vault = self._movie(conn, "Shelved", Decimal("30.00"), "EUR")
            self._movie(conn, "Elsewhere", Decimal("70.00"), "EUR")
            vault_id = self._container(conn, "vault", "Shelf A")
            self._link(conn, vault_id, in_vault)
            summary = self._value(conn)
        vaults = {scope["id"]: scope for scope in summary["scopes"]["vaults"]}
        self.assertEqual(Decimal(vaults[str(vault_id)]["total"]), Decimal("30.00"))
        self.assertEqual(Decimal(summary["total"]), Decimal("100.00"))

    def test_a_collection_sums_direct_movies_and_nested_containers(self):
        with self.connect() as conn:
            direct = self._movie(conn, "Direct", Decimal("10.00"), "EUR")
            in_set = self._movie(conn, "In Set", Decimal("99.00"), "EUR")
            box_set_id = self._container(conn, "box_set", "Nested Set", Decimal("40.00"), "EUR")
            self._link(conn, box_set_id, in_set)
            collection_id = self._container(conn, "collection", "Favourites")
            self._collection_item(conn, collection_id, "movie", direct)
            self._collection_item(conn, collection_id, "box_set", box_set_id)
            summary = self._value(conn)
        collections = {scope["id"]: scope for scope in summary["scopes"]["collections"]}
        # 10 for the direct film, 40 for the set - the set's member is suppressed.
        self.assertEqual(Decimal(collections[str(collection_id)]["total"]), Decimal("50.00"))

    def test_a_cycle_between_collections_terminates(self):
        with self.connect() as conn:
            movie_id = self._movie(conn, "Looped", Decimal("5.00"), "EUR")
            first = self._container(conn, "collection", "First")
            second = self._container(conn, "collection", "Second")
            self._collection_item(conn, first, "movie", movie_id)
            self._collection_item(conn, first, "collection", second)
            self._collection_item(conn, second, "collection", first)
            summary = self._value(conn)
        collections = {scope["id"]: scope for scope in summary["scopes"]["collections"]}
        # The film is counted once, and the walk returns rather than recursing.
        self.assertEqual(Decimal(collections[str(first)]["total"]), Decimal("5.00"))
        self.assertEqual(Decimal(collections[str(second)]["total"]), Decimal("5.00"))

    def test_currencies_are_kept_raw_alongside_the_converted_total(self):
        with self.connect() as conn:
            self._movie(conn, "Euro Film", Decimal("10.00"), "EUR")
            self._movie(conn, "Dollar Film", Decimal("20.00"), "USD")
            summary = self._value(conn)
        self.assertEqual(summary["byCurrency"], {"EUR": "10.00", "USD": "20.00"})
        self.assertEqual(Decimal(summary["total"]), Decimal("20.00"))


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class CollectionValueSnapshotPostgresTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def _owner_id(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users ORDER BY created_at LIMIT 1")
            row = cur.fetchone()
        return row["id"] if row else None

    def _snapshot(self, conn, user_id, captured_on):
        return next_collection_value.record_collection_value_snapshot(
            conn,
            user_id=user_id,
            movie_where="m.deleted_at IS NULL AND m.public_id LIKE 'snapshot-value-test-%%'",
            movie_params=[],
            container_where="c.public_id LIKE 'snapshot-value-test-%%'",
            container_params=[],
            captured_on=captured_on,
        )

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM movies WHERE public_id LIKE 'snapshot-value-test-%'")
                cur.execute(
                    """
                    DELETE FROM collection_value_snapshots
                    WHERE captured_on IN (%s, %s)
                    """,
                    (date(2001, 1, 1), date(2001, 1, 2)),
                )
            conn.commit()

    def _movie(self, conn, title, value):
        movie_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movies (id, public_id, title, sort_title, estimated_value, estimated_value_currency)
                VALUES (%s, %s, %s, %s, %s, 'EUR')
                """,
                (movie_id, f"snapshot-value-test-{movie_id}", title, title, value),
            )
        conn.commit()
        return movie_id

    def test_a_second_write_on_the_same_day_updates_rather_than_appends(self):
        with self.connect() as conn:
            user_id = self._owner_id(conn)
            if not user_id:
                self.skipTest("no user rows to attribute a snapshot to")
            self._movie(conn, "First", Decimal("10.00"))
            self._snapshot(conn, user_id, date(2001, 1, 1))
            conn.commit()
            self._movie(conn, "Second", Decimal("15.00"))
            self._snapshot(conn, user_id, date(2001, 1, 1))
            conn.commit()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT total_value FROM collection_value_snapshots
                    WHERE user_id=%s AND scope_id IS NULL AND captured_on=%s
                    """,
                    (user_id, date(2001, 1, 1)),
                )
                rows = cur.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["total_value"], Decimal("25.00"))

    def test_the_history_returns_one_point_per_day_oldest_first(self):
        with self.connect() as conn:
            user_id = self._owner_id(conn)
            if not user_id:
                self.skipTest("no user rows to attribute a snapshot to")
            self._movie(conn, "Day one", Decimal("10.00"))
            self._snapshot(conn, user_id, date(2001, 1, 1))
            self._movie(conn, "Day two", Decimal("5.00"))
            self._snapshot(conn, user_id, date(2001, 1, 2))
            conn.commit()
            points = next_collection_value.collection_value_history(
                conn, user_id=user_id, since=date(2001, 1, 1), until=date(2001, 1, 2)
            )
        self.assertEqual([point["capturedOn"] for point in points], ["2001-01-01", "2001-01-02"])
        self.assertEqual(Decimal(points[0]["total"]), Decimal("10.00"))
        self.assertEqual(Decimal(points[1]["total"]), Decimal("15.00"))


if __name__ == "__main__":
    unittest.main()
