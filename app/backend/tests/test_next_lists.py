import os
import sys
import unittest
from unittest.mock import MagicMock, patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_database import discover_migrations

try:
    from app.backend import next_app
except ModuleNotFoundError as exc:  # Local minimal test environments may omit optional backend deps.
    if exc.name not in {"flask", "psycopg"}:
        raise
    next_app = None


class ListsExpansionMigrationContractTests(unittest.TestCase):
    """Migration 026 introduces the wishlist / tags / loans tables that the
    per-user sync builders and REST routes depend on."""

    def setUp(self):
        self.migrations = {m.version: m for m in discover_migrations()}

    def test_lists_expansion_migration_is_present(self):
        self.assertIn("026", self.migrations)
        self.assertEqual(self.migrations["026"].name, "lists_expansion")

    def test_migration_declares_expected_tables(self):
        sql = self.migrations["026"].sql
        self.assertIn("CREATE TABLE IF NOT EXISTS wishlist_items", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS tags", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS movie_tags", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS loans", sql)

    def test_readiness_table_names_are_honoured(self):
        # The readiness report auto-flips lending_center / tags_smart_rules on
        # exactly these table names, so they must not drift.
        sql = self.migrations["026"].sql
        self.assertIn("tags", sql)
        self.assertIn("movie_tags", sql)
        self.assertIn("loans", sql)

    def test_tag_and_assignment_uniqueness_and_loan_check(self):
        sql = self.migrations["026"].sql
        # A tag slug is unique per user; an assignment is unique per (user, tag, movie).
        self.assertIn("UNIQUE (user_id, slug)", sql)
        self.assertIn("UNIQUE (user_id, tag_id, movie_id)", sql)
        # A loan must always carry a borrower (freeform name or a linked account).
        self.assertIn("borrower_name IS NOT NULL OR borrower_user_id IS NOT NULL", sql)


class ListsProvenanceMigrationContractTests(unittest.TestCase):
    """Migration 027 records who created each loan / wishlist entry and speeds up
    the inbound "borrowed by me" lookup."""

    def setUp(self):
        self.migrations = {m.version: m for m in discover_migrations()}

    def test_provenance_migration_is_present(self):
        self.assertIn("027", self.migrations)
        self.assertEqual(self.migrations["027"].name, "lists_created_by")

    def test_created_by_columns_are_added_and_backfilled(self):
        sql = self.migrations["027"].sql
        self.assertIn("ALTER TABLE loans", sql)
        self.assertIn("ALTER TABLE wishlist_items", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS created_by_user_id uuid", sql)
        # Pre-existing rows are attributed to the owner/list-owner.
        self.assertIn("SET created_by_user_id = user_id", sql)

    def test_borrower_lookup_index_is_declared(self):
        sql = self.migrations["027"].sql
        self.assertIn("idx_loans_borrower_user", sql)
        self.assertIn("ON loans(borrower_user_id", sql)


class WishlistShopPricesMigrationContractTests(unittest.TestCase):
    """Migration 032 introduces per-wishlist-item shop tracking and price history."""

    def setUp(self):
        self.migrations = {m.version: m for m in discover_migrations()}

    def test_wishlist_shop_prices_migration_is_present(self):
        self.assertIn("032", self.migrations)
        self.assertEqual(self.migrations["032"].name, "wishlist_item_shops")

    def test_migration_declares_shop_and_price_history_tables(self):
        sql = self.migrations["032"].sql
        self.assertIn("CREATE TABLE IF NOT EXISTS wishlist_item_shops", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS wishlist_item_shop_prices", sql)
        self.assertIn("wishlist_item_id", sql)
        self.assertIn("wishlist_item_shop_id", sql)


class WishlistShopSelectorsMigrationContractTests(unittest.TestCase):
    """Migration 033 extends shop sources with deterministic selector metadata."""

    def setUp(self):
        self.migrations = {m.version: m for m in discover_migrations()}

    def test_wishlist_shop_selectors_migration_is_present(self):
        self.assertIn("033", self.migrations)
        self.assertEqual(self.migrations["033"].name, "wishlist_shop_selectors")

    def test_migration_adds_selector_and_source_columns(self):
        sql = self.migrations["033"].sql
        self.assertIn("ALTER TABLE wishlist_item_shops", sql)
        self.assertIn("selector_type", sql)
        self.assertIn("selector_value", sql)
        self.assertIn("selector_options", sql)
        self.assertIn("ALTER TABLE wishlist_item_shop_prices", sql)
        self.assertIn("extraction_source", sql)


class WishlistShopProvidersMigrationContractTests(unittest.TestCase):
    """Migration 034 adds provider metadata for plugin-backed shop pricing."""

    def setUp(self):
        self.migrations = {m.version: m for m in discover_migrations()}

    def test_wishlist_shop_providers_migration_is_present(self):
        self.assertIn("034", self.migrations)
        self.assertEqual(self.migrations["034"].name, "wishlist_shop_providers")

    def test_migration_adds_provider_columns(self):
        sql = self.migrations["034"].sql
        self.assertIn("ALTER TABLE wishlist_item_shops", sql)
        self.assertIn("provider_plugin_id", sql)
        self.assertIn("provider_product_ref", sql)
        self.assertIn("provider_last_status", sql)
        self.assertIn("provider_last_error", sql)
        self.assertIn("ALTER TABLE wishlist_item_shop_prices", sql)
        self.assertIn("provider_id", sql)
        self.assertIn("source_detail", sql)
        self.assertIn("confidence", sql)


class WishlistIdentityDedupMigrationContractTests(unittest.TestCase):
    """Migration 036 consolidates stable catalog identities before enforcing them."""

    def setUp(self):
        self.migrations = {m.version: m for m in discover_migrations()}

    def test_wishlist_identity_dedup_migration_is_present(self):
        self.assertIn("036", self.migrations)
        self.assertEqual(self.migrations["036"].name, "wishlist_identity_dedup")

    def test_migration_merges_dependencies_before_deleting_duplicates(self):
        sql = self.migrations["036"].sql
        self.assertIn("merge_wishlist_duplicate", sql)
        self.assertIn("UPDATE wishlist_item_shop_prices", sql)
        self.assertIn("UPDATE wishlist_item_shops", sql)
        self.assertIn("DELETE FROM wishlist_items WHERE id = duplicate_id", sql)

    def test_migration_enforces_movievault_and_tmdb_identity(self):
        sql = self.migrations["036"].sql
        self.assertIn("uq_wishlist_items_user_movievault", sql)
        self.assertIn("ON wishlist_items (user_id, movievault_id)", sql)
        self.assertIn("uq_wishlist_items_user_tmdb", sql)
        self.assertIn("snapshot->>'tmdb_id'", sql)
        self.assertIn("snapshot->>'tmdb_media_type'", sql)
        self.assertIn("NULLIF(btrim(movievault_id), '') IS NULL", sql)


@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class ListsRouteRegistrationTests(unittest.TestCase):
    """The personal-list write/read surface must be wired into the Flask app."""

    def _rules(self):
        return {rule.rule for rule in next_app.app.url_map.iter_rules()}

    def _methods(self, rule_str):
        methods = set()
        for rule in next_app.app.url_map.iter_rules():
            if rule.rule == rule_str:
                methods |= set(rule.methods)
        return methods

    def test_wishlist_routes_are_registered(self):
        rules = self._rules()
        self.assertIn("/api/next/lists/wishlist", rules)
        self.assertIn("/api/next/lists/wishlist/<item_id>", rules)
        self.assertIn("/api/next/lists/wishlist/<item_id>/acquire", rules)
        self.assertIn("/api/next/lists/wishlist/<item_id>/poster", rules)
        self.assertIn("/api/next/lists/wishlist/<item_id>/shops", rules)
        self.assertIn("/api/next/lists/wishlist/<item_id>/shops/<shop_id>", rules)
        self.assertIn("/api/next/price-providers", rules)
        self.assertIn("/api/next/admin/price-providers/<provider_id>/enabled", rules)
        self.assertIn("/api/next/admin/price-providers/<provider_id>/config", rules)
        self.assertIn("GET", self._methods("/api/next/lists/wishlist"))
        self.assertIn("POST", self._methods("/api/next/lists/wishlist"))
        self.assertIn("PATCH", self._methods("/api/next/lists/wishlist/<item_id>"))
        self.assertIn("DELETE", self._methods("/api/next/lists/wishlist/<item_id>"))
        self.assertIn("POST", self._methods("/api/next/lists/wishlist/<item_id>/poster"))
        self.assertIn("POST", self._methods("/api/next/lists/wishlist/<item_id>/shops"))
        self.assertIn("PATCH", self._methods("/api/next/lists/wishlist/<item_id>/shops/<shop_id>"))
        self.assertIn("GET", self._methods("/api/next/price-providers"))
        self.assertIn("PATCH", self._methods("/api/next/admin/price-providers/<provider_id>/enabled"))
        self.assertIn("GET", self._methods("/api/next/admin/price-providers/<provider_id>/config"))
        self.assertIn("PATCH", self._methods("/api/next/admin/price-providers/<provider_id>/config"))

    def test_tag_routes_are_registered(self):
        rules = self._rules()
        self.assertIn("/api/next/tags", rules)
        self.assertIn("/api/next/movies/<movie_id>/tags", rules)
        self.assertIn("/api/next/movies/<movie_id>/tags/<tag_id>", rules)
        self.assertIn("POST", self._methods("/api/next/movies/<movie_id>/tags"))
        self.assertIn("DELETE", self._methods("/api/next/movies/<movie_id>/tags/<tag_id>"))

    def test_loan_routes_are_registered(self):
        rules = self._rules()
        self.assertIn("/api/next/loans", rules)
        self.assertIn("/api/next/movies/<movie_id>/loans", rules)
        self.assertIn("/api/next/loans/<loan_id>/return", rules)
        self.assertIn("/api/next/loans/<loan_id>", rules)
        self.assertIn("/api/next/loans/borrowers/search", rules)
        self.assertIn("/api/next/loans/borrowed", rules)
        self.assertIn("POST", self._methods("/api/next/movies/<movie_id>/loans"))
        self.assertIn("POST", self._methods("/api/next/loans/<loan_id>/return"))
        self.assertIn("PATCH", self._methods("/api/next/loans/<loan_id>"))
        self.assertIn("DELETE", self._methods("/api/next/loans/<loan_id>"))
        self.assertIn("GET", self._methods("/api/next/loans/borrowers/search"))
        self.assertIn("GET", self._methods("/api/next/loans/borrowed"))

    def test_statistics_route_is_registered(self):
        self.assertIn("/api/next/stats/personal", self._rules())

    def test_shop_add_rejects_non_public_url_after_authorization_before_database_query(self):
        item_id = "00000000-0000-0000-0000-000000000001"
        conn = MagicMock()
        with (
            next_app.app.test_request_context(
                f"/api/next/lists/wishlist/{item_id}/shops",
                method="POST",
                json={
                    "shopName": "Internal",
                    "priceUrl": "http://127.0.0.1:5000/api/next/health",
                },
            ),
            patch(
                "app.backend.next_app.validate_public_url",
                side_effect=next_app.PublicHttpError("url_blocked"),
            ),
            patch("app.backend.next_app.connect") as connect,
            patch(
                "app.backend.next_app.require_next_permission",
                return_value={"id": "user-1"},
            ),
        ):
            connect.return_value.__enter__.return_value = conn
            with self.assertRaises(next_app.NextApiError) as raised:
                next_app.app.view_functions["add_wishlist_item_shop"](item_id)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.code, "wishlist_price_url_not_public")
        connect.assert_called_once()
        conn.cursor.assert_not_called()

    def test_shop_update_rejects_url_credentials_after_authorization_before_database_query(self):
        item_id = "00000000-0000-0000-0000-000000000001"
        shop_id = "00000000-0000-0000-0000-000000000002"
        conn = MagicMock()
        with (
            next_app.app.test_request_context(
                f"/api/next/lists/wishlist/{item_id}/shops/{shop_id}",
                method="PATCH",
                json={
                    "shopName": "Unsafe",
                    "priceUrl": "https://username@shop.example/product",
                },
            ),
            patch(
                "app.backend.next_app.validate_public_url",
                side_effect=next_app.PublicHttpError("url_invalid"),
            ),
            patch("app.backend.next_app.connect") as connect,
            patch(
                "app.backend.next_app.require_next_permission",
                return_value={"id": "user-1"},
            ),
        ):
            connect.return_value.__enter__.return_value = conn
            with self.assertRaises(next_app.NextApiError) as raised:
                next_app.app.view_functions["update_wishlist_item_shop"](item_id, shop_id)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.code, "wishlist_price_url_invalid")
        connect.assert_called_once()
        conn.cursor.assert_not_called()

    def test_wishlist_update_rejects_legacy_private_url_after_authorization_before_database_query(self):
        item_id = "00000000-0000-0000-0000-000000000001"
        conn = MagicMock()
        with (
            next_app.app.test_request_context(
                f"/api/next/lists/wishlist/{item_id}",
                method="PATCH",
                json={"priceUrl": "http://192.168.1.10/admin"},
            ),
            patch(
                "app.backend.next_app.validate_public_url",
                side_effect=next_app.PublicHttpError("url_blocked"),
            ),
            patch("app.backend.next_app.connect") as connect,
            patch(
                "app.backend.next_app.require_next_permission",
                return_value={"id": "user-1"},
            ),
        ):
            connect.return_value.__enter__.return_value = conn
            with self.assertRaises(next_app.NextApiError) as raised:
                next_app.app.view_functions["update_wishlist_item"](item_id)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.code, "wishlist_price_url_not_public")
        connect.assert_called_once()
        conn.cursor.assert_not_called()

    def test_unauthorized_wishlist_writes_do_not_resolve_price_url(self):
        item_id = "00000000-0000-0000-0000-000000000001"
        shop_id = "00000000-0000-0000-0000-000000000002"
        cases = (
            (
                f"/api/next/lists/wishlist/{item_id}",
                "update_wishlist_item",
                (item_id,),
            ),
            (
                f"/api/next/lists/wishlist/{item_id}/shops",
                "add_wishlist_item_shop",
                (item_id,),
            ),
            (
                f"/api/next/lists/wishlist/{item_id}/shops/{shop_id}",
                "update_wishlist_item_shop",
                (item_id, shop_id),
            ),
        )
        for path, endpoint, args in cases:
            with self.subTest(endpoint=endpoint):
                conn = MagicMock()
                with (
                    next_app.app.test_request_context(
                        path,
                        method="PATCH",
                        json={
                            "shopName": "Blocked",
                            "priceUrl": "http://internal.example/admin",
                        },
                    ),
                    patch("app.backend.next_app.connect") as connect,
                    patch(
                        "app.backend.next_app.require_next_permission",
                        side_effect=next_app.NextApiError("Permission required", 403),
                    ),
                    patch("app.backend.next_app.validate_public_url") as validate_url,
                ):
                    connect.return_value.__enter__.return_value = conn
                    with self.assertRaises(next_app.NextApiError) as raised:
                        next_app.app.view_functions[endpoint](*args)
                self.assertEqual(raised.exception.status_code, 403)
                validate_url.assert_not_called()

    def test_api_error_handler_serializes_stable_error_code(self):
        error = next_app.NextApiError(
            "Wishlist price URL must resolve to a public network address",
            400,
            "wishlist_price_url_not_public",
        )
        handler = next_app.app.error_handler_spec[None][None][next_app.NextApiError]

        with next_app.app.test_request_context("/api/next/lists/wishlist/test"):
            response, status = handler(error)

        self.assertEqual(status, 400)
        self.assertEqual(
            response.get_json(),
            {
                "status": "error",
                "error": "Wishlist price URL must resolve to a public network address",
                "errorCode": "wishlist_price_url_not_public",
            },
        )


class _FakeCursor:
    """Matches only the SQL fragments the lists sync builders/emit helpers run."""

    def __init__(self, store):
        self.store = store
        self._rows = []
        self._row = None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=()):
        q = " ".join(str(sql).split())
        p = tuple(params or ())
        store = self.store
        self._rows = []
        self._row = None

        if "INSERT INTO user_sync_state" in q:
            store["user_state"].setdefault(p[0], 0)
        elif "SELECT revision FROM user_sync_state" in q:
            self._row = {"revision": store["user_state"].get(p[0], 0)}
        elif "UPDATE user_sync_state" in q and "revision + 1" in q:
            uid = p[0]
            if uid in store["user_state"]:
                store["user_state"][uid] += 1
                self._row = {"revision": store["user_state"][uid]}
        elif "INSERT INTO user_sync_changes" in q:
            uid, rev, entity_type, entity_id, operation, payload = p
            store["user_changes"].append(
                {
                    "user_id": uid,
                    "revision": rev,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "operation": operation,
                    "payload": payload,
                }
            )
        elif "FROM wishlist_items" in q and "AND id=%s" in q:
            self._row = self._one(store["wishlist"], p)
        elif "FROM wishlist_items" in q:
            self._rows = self._all(store["wishlist"], p)
        elif "FROM tags" in q and "AND id=%s" in q:
            self._row = self._one(store["tags"], p)
        elif "FROM tags" in q:
            self._rows = self._all(store["tags"], p)
        elif "FROM movie_tags" in q and "AND id=%s" in q:
            self._row = self._one(store["movie_tags"], p)
        elif "FROM movie_tags" in q:
            self._rows = self._all(store["movie_tags"], p)
        elif "FROM loans" in q and ("AND id=%s" in q or "AND l.id=%s" in q):
            self._row = self._one(store["loans"], p)
        elif "FROM loans" in q:
            self._rows = self._all(store["loans"], p)

    @staticmethod
    def _one(table, params):
        row = table.get((params[0], params[1]))
        return dict(row) if row else None

    @staticmethod
    def _all(table, params):
        uid = params[0]
        return [dict(v) for (u, _i), v in table.items() if u == uid]

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return _FakeCursor(self.store)


class _WishlistCreateCursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=()):
        self.queries.append((" ".join(query.split()), params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class _WishlistCreateConn:
    def __init__(self, rows):
        self.cursor_instance = _WishlistCreateCursor(rows)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self.cursor_instance

    def transaction(self):
        return self


@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class WishlistCreateIdentityTests(unittest.TestCase):
    def _fields(self, **overrides):
        fields = {
            "title": "Dune: Part Two",
            "year": 2024,
            "barcode": None,
            "format": None,
            "movievault_id": None,
            "poster_url": None,
            "note": None,
            "tmdb_id": None,
            "tmdb_media_type": "movie",
            "source": None,
        }
        fields.update(overrides)
        return fields

    def test_new_item_emits_sync_and_audit_once(self):
        conn = _WishlistCreateConn([{"id": "new-id"}])
        fields = self._fields(movievault_id="mv-42")
        with patch.object(next_app, "emit_wishlist_change") as emit, patch.object(next_app, "audit_event") as audit:
            item_id, created = next_app._create_wishlist_item_record(
                conn,
                actor={"id": "user-a"},
                fields=fields,
                snapshot={"movievault_id": "mv-42"},
            )

        self.assertEqual(item_id, "new-id")
        self.assertTrue(created)
        emit.assert_called_once_with(conn, "user-a", "new-id", operation="upsert")
        audit.assert_called_once()
        self.assertIn("ON CONFLICT DO NOTHING", conn.cursor_instance.queries[0][0])

    def test_duplicate_movievault_id_returns_oldest_without_side_effects(self):
        conn = _WishlistCreateConn([None, {"id": "existing-id"}])
        fields = self._fields(movievault_id="mv-42")
        with patch.object(next_app, "emit_wishlist_change") as emit, patch.object(next_app, "audit_event") as audit:
            item_id, created = next_app._create_wishlist_item_record(
                conn,
                actor={"id": "user-a"},
                fields=fields,
                snapshot={"movievault_id": "mv-42"},
            )

        self.assertEqual(item_id, "existing-id")
        self.assertFalse(created)
        emit.assert_not_called()
        audit.assert_not_called()
        self.assertIn("movievault_id=%s", conn.cursor_instance.queries[1][0])
        self.assertEqual(conn.cursor_instance.queries[1][1], ("user-a", "mv-42"))

    def test_duplicate_tmdb_identity_includes_media_type(self):
        conn = _WishlistCreateConn([None, {"id": "existing-id"}])
        fields = self._fields(tmdb_id="693134", tmdb_media_type="tv", source="discover")
        with patch.object(next_app, "emit_wishlist_change") as emit, patch.object(next_app, "audit_event") as audit:
            item_id, created = next_app._create_wishlist_item_record(
                conn,
                actor={"id": "user-a"},
                fields=fields,
                snapshot={"tmdb_id": "693134", "tmdb_media_type": "tv"},
            )

        self.assertEqual(item_id, "existing-id")
        self.assertFalse(created)
        emit.assert_not_called()
        audit.assert_not_called()
        self.assertIn("snapshot->>'tmdb_id' = %s", conn.cursor_instance.queries[1][0])
        self.assertEqual(conn.cursor_instance.queries[1][1], ("user-a", "693134", "tv"))

    def test_identityless_same_title_items_remain_distinct(self):
        fields = self._fields()
        first = _WishlistCreateConn([{"id": "first-id"}])
        second = _WishlistCreateConn([{"id": "second-id"}])
        with patch.object(next_app, "emit_wishlist_change"), patch.object(next_app, "audit_event"):
            first_result = next_app._create_wishlist_item_record(
                first,
                actor={"id": "user-a"},
                fields=fields,
                snapshot={},
            )
            second_result = next_app._create_wishlist_item_record(
                second,
                actor={"id": "user-a"},
                fields=fields,
                snapshot={},
            )

        self.assertEqual(first_result, ("first-id", True))
        self.assertEqual(second_result, ("second-id", True))
        self.assertEqual(len(first.cursor_instance.queries), 1)
        self.assertEqual(len(second.cursor_instance.queries), 1)


@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class ListsSyncEntityTests(unittest.TestCase):
    """Sync-entity builders and emit helpers for the four personal entity types."""

    def _store(self):
        return {
            "user_state": {},
            "user_changes": [],
            "wishlist": {},
            "tags": {},
            "movie_tags": {},
            "loans": {},
        }

    def _patches(self, *, tables_present=True):
        return (
            patch.object(next_app, "table_exists", lambda conn, name: tables_present),
            patch.object(next_app, "Jsonb", lambda value: value),
        )

    # -- absence guards -----------------------------------------------------

    def test_builders_return_empty_when_tables_absent(self):
        conn = _FakeConn(self._store())
        p1, p2 = self._patches(tables_present=False)
        with p1, p2:
            self.assertEqual(next_app.all_wishlist_sync_entities(conn, "user-a"), [])
            self.assertEqual(next_app.all_tag_sync_entities(conn, "user-a"), [])
            self.assertEqual(next_app.all_movie_tag_sync_entities(conn, "user-a"), [])
            self.assertEqual(next_app.all_loan_sync_entities(conn, "user-a"), [])

    def test_builders_return_empty_without_user(self):
        conn = _FakeConn(self._store())
        p1, p2 = self._patches()
        with p1, p2:
            self.assertEqual(next_app.all_wishlist_sync_entities(conn, None), [])
            self.assertEqual(next_app.all_loan_sync_entities(conn, ""), [])

    # -- wishlist -----------------------------------------------------------

    def test_emit_wishlist_upsert_carries_metadata_snapshot(self):
        store = self._store()
        store["wishlist"][("user-a", "wish-1")] = {
            "id": "wish-1",
            "title": "Dune: Part Two",
            "year": 2024,
            "barcode": "5051890000000",
            "format": "4K UHD",
            "movievault_id": "mv_movie_42",
            "poster_url": None,
            "note": None,
            "snapshot": {"title": "Dune: Part Two"},
            "added_at": "2026-01-02T00:00:00Z",
            "acquired_at": None,
            "acquired_movie_id": None,
        }
        conn = _FakeConn(store)
        p1, p2 = self._patches()
        with p1, p2:
            revision = next_app.emit_wishlist_change(conn, "user-a", "wish-1", operation="upsert")
        self.assertEqual(revision, 1)
        change = store["user_changes"][-1]
        self.assertEqual(change["entity_type"], "wishlist")
        self.assertEqual(change["operation"], "upsert")
        self.assertEqual(change["entity_id"], "wish-1")
        self.assertEqual(change["payload"]["entity"]["title"], "Dune: Part Two")
        self.assertEqual(change["payload"]["entity"]["movievaultId"], "mv_movie_42")

    def test_emit_wishlist_delete_omits_entity(self):
        store = self._store()
        conn = _FakeConn(store)
        p1, p2 = self._patches()
        with p1, p2:
            next_app.emit_wishlist_change(conn, "user-a", "wish-1", operation="delete")
        change = store["user_changes"][-1]
        self.assertEqual(change["operation"], "delete")
        self.assertNotIn("entity", change["payload"])
        self.assertEqual(change["payload"]["id"], "wish-1")

    def test_wishlist_builder_scopes_to_the_caller(self):
        store = self._store()
        base = {
            "title": "T",
            "year": None,
            "barcode": None,
            "format": None,
            "movievault_id": None,
            "poster_url": None,
            "note": None,
            "snapshot": {},
            "added_at": "2026-01-01T00:00:00Z",
            "acquired_at": None,
            "acquired_movie_id": None,
        }
        store["wishlist"][("user-a", "w1")] = {**base, "id": "w1"}
        store["wishlist"][("user-b", "w2")] = {**base, "id": "w2"}
        conn = _FakeConn(store)
        p1, p2 = self._patches()
        with p1, p2:
            items = next_app.all_wishlist_sync_entities(conn, "user-a")
        self.assertEqual([i["id"] for i in items], ["w1"])

    # -- tags ---------------------------------------------------------------

    def test_emit_tag_upsert_carries_entity(self):
        store = self._store()
        store["tags"][("user-a", "tag-1")] = {
            "id": "tag-1",
            "name": "Noir",
            "slug": "noir",
            "color": "#222",
            "created_at": "2026-01-01T00:00:00Z",
        }
        conn = _FakeConn(store)
        p1, p2 = self._patches()
        with p1, p2:
            next_app.emit_tag_change(conn, "user-a", "tag-1", operation="upsert")
        change = store["user_changes"][-1]
        self.assertEqual(change["entity_type"], "tag")
        self.assertEqual(change["payload"]["entity"]["slug"], "noir")

    def test_emit_movie_tag_upsert_carries_tag_and_movie_ids(self):
        store = self._store()
        store["movie_tags"][("user-a", "asg-1")] = {
            "id": "asg-1",
            "tag_id": "tag-1",
            "movie_id": "movie-9",
            "created_at": "2026-01-01T00:00:00Z",
        }
        conn = _FakeConn(store)
        p1, p2 = self._patches()
        with p1, p2:
            next_app.emit_movie_tag_change(
                conn, "user-a", "asg-1", operation="upsert", tag_id="tag-1", movie_id="movie-9"
            )
        change = store["user_changes"][-1]
        self.assertEqual(change["entity_type"], "movieTag")
        self.assertEqual(change["payload"]["tagId"], "tag-1")
        self.assertEqual(change["payload"]["movieId"], "movie-9")
        self.assertEqual(change["payload"]["entity"]["movieId"], "movie-9")

    def test_emit_movie_tag_delete_keeps_hints_without_entity(self):
        store = self._store()
        conn = _FakeConn(store)
        p1, p2 = self._patches()
        with p1, p2:
            next_app.emit_movie_tag_change(
                conn, "user-a", "asg-1", operation="delete", tag_id="tag-1", movie_id="movie-9"
            )
        change = store["user_changes"][-1]
        self.assertEqual(change["operation"], "delete")
        self.assertNotIn("entity", change["payload"])
        self.assertEqual(change["payload"]["movieId"], "movie-9")

    # -- loans --------------------------------------------------------------

    def test_emit_loan_upsert_carries_provenance_and_borrower(self):
        store = self._store()
        store["loans"][("user-a", "loan-1")] = {
            "id": "loan-1",
            "movie_id": "movie-9",
            "snapshot": {"title": "Heat"},
            "borrower_name": "Neil",
            "borrower_user_id": "user-b",
            "loaned_at": "2026-01-01T00:00:00Z",
            "due_at": None,
            "returned_at": None,
            "note": None,
            "created_at": "2026-01-01T00:00:00Z",
            "created_by_user_id": "user-a",
        }
        conn = _FakeConn(store)
        p1, p2 = self._patches()
        with p1, p2:
            revision = next_app.emit_loan_change(
                conn, "user-a", "loan-1", operation="upsert", movie_id="movie-9"
            )
        self.assertEqual(revision, 1)
        change = store["user_changes"][-1]
        self.assertEqual(change["entity_type"], "loan")
        self.assertEqual(change["payload"]["movieId"], "movie-9")
        entity = change["payload"]["entity"]
        self.assertEqual(entity["borrowerName"], "Neil")
        self.assertEqual(entity["borrowerUserId"], "user-b")
        self.assertEqual(entity["createdByUserId"], "user-a")

    def test_loan_row_entity_marks_returned_state(self):
        active = next_app._loan_row_entity({"id": "l1", "returned_at": None})
        self.assertFalse(active["returned"])
        self.assertIsNone(active["returnedAt"])
        done = next_app._loan_row_entity(
            {"id": "l2", "returned_at": "2026-02-02T00:00:00Z"}
        )
        self.assertTrue(done["returned"])

    def test_borrowed_loan_row_entity_adds_lender_context(self):
        entity = next_app._borrowed_loan_row_entity(
            {
                "id": "loan-1",
                "movie_id": "movie-9",
                "user_id": "user-a",
                "borrower_user_id": "user-b",
                "lender_username": "alice",
                "lender_display_name": "Alice A.",
                "returned_at": None,
            }
        )
        self.assertEqual(entity["lenderUserId"], "user-a")
        self.assertEqual(entity["lenderName"], "Alice A.")
        self.assertEqual(entity["lenderUsername"], "alice")
        self.assertEqual(entity["borrowerUserId"], "user-b")

    def test_all_borrowed_loan_entities_empty_without_user_or_table(self):
        conn = _FakeConn(self._store())
        p1, p2 = self._patches(tables_present=False)
        with p1, p2:
            self.assertEqual(next_app.all_borrowed_loan_entities(conn, "user-b"), [])
        p1, p2 = self._patches()
        with p1, p2:
            self.assertEqual(next_app.all_borrowed_loan_entities(conn, None), [])

    # -- loans --------------------------------------------------------------

    def test_emit_loan_upsert_marks_active_vs_returned(self):
        store = self._store()
        store["loans"][("user-a", "loan-1")] = {
            "id": "loan-1",
            "movie_id": "movie-3",
            "snapshot": {"title": "Heat"},
            "borrower_name": "Alex",
            "borrower_user_id": None,
            "loaned_at": "2026-01-01T00:00:00Z",
            "due_at": "2026-02-01T00:00:00Z",
            "returned_at": None,
            "note": None,
            "created_at": "2026-01-01T00:00:00Z",
        }
        conn = _FakeConn(store)
        p1, p2 = self._patches()
        with p1, p2:
            next_app.emit_loan_change(conn, "user-a", "loan-1", operation="upsert", movie_id="movie-3")
        change = store["user_changes"][-1]
        self.assertEqual(change["entity_type"], "loan")
        self.assertEqual(change["payload"]["movieId"], "movie-3")
        entity = change["payload"]["entity"]
        self.assertEqual(entity["borrowerName"], "Alex")
        self.assertFalse(entity["returned"])

    def test_loan_entity_reports_returned_flag(self):
        store = self._store()
        store["loans"][("user-a", "loan-2")] = {
            "id": "loan-2",
            "movie_id": None,
            "snapshot": {},
            "borrower_name": None,
            "borrower_user_id": "user-b",
            "loaned_at": "2026-01-01T00:00:00Z",
            "due_at": None,
            "returned_at": "2026-01-10T00:00:00Z",
            "note": None,
            "created_at": "2026-01-01T00:00:00Z",
        }
        conn = _FakeConn(store)
        p1, p2 = self._patches()
        with p1, p2:
            entity = next_app.loan_sync_entity(conn, "user-a", "loan-2")
        self.assertIsNotNone(entity)
        self.assertTrue(entity["returned"])
        self.assertEqual(entity["borrowerUserId"], "user-b")
        self.assertIsNone(entity["movieId"])


if __name__ == "__main__":
    unittest.main()
