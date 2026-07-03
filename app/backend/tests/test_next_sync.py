import os
import sys
import unittest
from unittest.mock import patch


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


class NextSyncMigrationContractTests(unittest.TestCase):
    """The per-user sync stream migration (025) must be discoverable and well-formed."""

    def setUp(self):
        self.migrations = {m.version: m for m in discover_migrations()}

    def test_user_sync_stream_migration_is_present(self):
        self.assertIn("025", self.migrations)
        migration = self.migrations["025"]
        self.assertEqual(migration.name, "user_sync_stream")

    def test_user_sync_stream_migration_declares_expected_objects(self):
        sql = self.migrations["025"].sql
        self.assertIn("CREATE TABLE IF NOT EXISTS user_sync_state", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS user_sync_changes", sql)
        self.assertIn("idx_user_sync_changes_user_revision", sql)
        # The per-user stream must be keyed and indexed by user_id + revision.
        self.assertIn("user_sync_changes(user_id, revision)", sql)

    def test_migration_versions_are_unique_and_monotonic(self):
        versions = [m.version for m in discover_migrations()]
        self.assertEqual(len(versions), len(set(versions)))
        self.assertEqual(versions, sorted(versions))


@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class NextSyncRouteRegistrationTests(unittest.TestCase):
    """The new sync surface must be wired into the Flask app."""

    def test_user_sync_routes_are_registered(self):
        rules = {rule.rule for rule in next_app.app.url_map.iter_rules()}
        self.assertIn("/api/next/sync/user/bootstrap", rules)
        self.assertIn("/api/next/sync/user/delta", rules)

    def test_catalog_sync_routes_are_registered(self):
        rules = {rule.rule for rule in next_app.app.url_map.iter_rules()}
        self.assertIn("/api/next/sync/state", rules)
        self.assertIn("/api/next/sync/bootstrap", rules)
        self.assertIn("/api/next/sync/mutations", rules)

    def test_movie_identifier_write_routes_are_registered(self):
        rules = {rule.rule for rule in next_app.app.url_map.iter_rules()}
        self.assertIn("/api/next/movies/<movie_id>/identifiers", rules)
        methods = set()
        for rule in next_app.app.url_map.iter_rules():
            if rule.rule == "/api/next/movies/<movie_id>/identifiers":
                methods |= set(rule.methods)
        self.assertIn("POST", methods)
        self.assertIn("DELETE", methods)


class _FakeCursor:
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
        self.rowcount = 0

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
                    "id": len(store["user_changes"]) + 1,
                    "user_id": uid,
                    "revision": rev,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "operation": operation,
                    "payload": payload,
                }
            )
        elif "FROM watchlist_items" in q and "AND movie_id=%s" in q:
            row = store["watchlist"].get((p[0], p[1]))
            self._row = dict(row) if row else None
        elif "FROM watchlist_items" in q:
            uid = p[0]
            self._rows = [dict(v) for (u, _m), v in store["watchlist"].items() if u == uid]
        elif "FROM watch_history" in q and "AND id=%s" in q:
            row = store["watch_history"].get((p[0], p[1]))
            self._row = dict(row) if row else None
        elif "FROM watch_history" in q:
            uid = p[0]
            self._rows = [dict(v) for (u, _i), v in store["watch_history"].items() if u == uid]
        elif "UPDATE sync_state" in q and "user_sync_state" not in q and "revision + 1" in q:
            store["global_state"] = store.get("global_state", 0) + 1
            self._row = {"revision": store["global_state"]}
        elif "INSERT INTO sync_changes" in q and "user_sync_changes" not in q:
            revision, entity_type, entity_id, operation, payload = p
            store.setdefault("global_changes", []).append(
                {
                    "revision": revision,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "operation": operation,
                    "payload": payload,
                }
            )
        elif "FROM movie_identifiers" in q:
            self._rows = [dict(r) for r in store.get("movie_identifiers", {}).get(p[0], [])]

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return _FakeCursor(self.store)


@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class NextUserSyncStreamTests(unittest.TestCase):
    """Per-user sync stream helpers: revisions, payload shape, isolation."""

    def _store(self):
        return {
            "user_state": {},
            "user_changes": [],
            "watchlist": {},
            "watch_history": {},
            "global_state": 0,
            "global_changes": [],
            "movie_identifiers": {},
        }

    def _patches(self):
        # Treat every table as present and keep JSON payloads as plain dicts.
        return (
            patch.object(next_app, "table_exists", lambda conn, name: True),
            patch.object(next_app, "Jsonb", lambda value: value),
        )

    def test_user_sync_change_increments_revision_per_user(self):
        store = self._store()
        conn = _FakeConn(store)
        p1, p2 = self._patches()
        with p1, p2:
            r1 = next_app.user_sync_change(
                conn, "user-a", entity_type="watchlist", entity_id="m1", operation="upsert", payload={}
            )
            r2 = next_app.user_sync_change(
                conn, "user-a", entity_type="watchlist", entity_id="m2", operation="upsert", payload={}
            )
            r3 = next_app.user_sync_change(
                conn, "user-b", entity_type="watchlist", entity_id="m1", operation="upsert", payload={}
            )
        self.assertEqual((r1, r2), (1, 2))
        self.assertEqual(r3, 1)  # user-b has an independent revision counter
        self.assertEqual(store["user_state"], {"user-a": 2, "user-b": 1})

    def test_emit_watchlist_upsert_carries_entity_snapshot(self):
        store = self._store()
        store["watchlist"][("user-a", "movie-1")] = {
            "movie_id": "movie-1",
            "added_at": "2026-01-02T00:00:00Z",
            "snapshot": {"title": "Dune"},
        }
        conn = _FakeConn(store)
        p1, p2 = self._patches()
        with p1, p2:
            revision = next_app.emit_watchlist_change(conn, "user-a", "movie-1", operation="upsert")
        self.assertEqual(revision, 1)
        change = store["user_changes"][-1]
        self.assertEqual(change["entity_type"], "watchlist")
        self.assertEqual(change["operation"], "upsert")
        self.assertEqual(change["entity_id"], "movie-1")
        self.assertEqual(change["payload"]["movieId"], "movie-1")
        self.assertEqual(change["payload"]["entity"]["snapshot"], {"title": "Dune"})

    def test_emit_watchlist_delete_omits_entity(self):
        store = self._store()
        conn = _FakeConn(store)
        p1, p2 = self._patches()
        with p1, p2:
            next_app.emit_watchlist_change(conn, "user-a", "movie-1", operation="delete")
        change = store["user_changes"][-1]
        self.assertEqual(change["operation"], "delete")
        self.assertNotIn("entity", change["payload"])
        self.assertEqual(change["payload"]["movieId"], "movie-1")

    def test_emit_watch_history_delete_includes_movie_hint(self):
        store = self._store()
        conn = _FakeConn(store)
        p1, p2 = self._patches()
        with p1, p2:
            next_app.emit_watch_history_change(
                conn, "user-a", "entry-9", operation="delete", movie_id="movie-1"
            )
        change = store["user_changes"][-1]
        self.assertEqual(change["entity_type"], "watch_history")
        self.assertEqual(change["operation"], "delete")
        self.assertEqual(change["entity_id"], "entry-9")
        self.assertEqual(change["payload"]["movieId"], "movie-1")

    def test_emit_movie_identifiers_change_carries_full_set(self):
        # The movie_identifier delta must carry the movie's full identifier set
        # (including the movievault_26 link) so an offline client replaces its
        # local copy for that movie in one change.
        store = self._store()
        store["movie_identifiers"]["movie-1"] = [
            {
                "provider_id": "movievault_26",
                "identifier_type": "movie_id",
                "identifier": "mv_movie_1",
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "provider_id": "tmdb",
                "identifier_type": "movie_id",
                "identifier": "603",
                "created_at": "2026-01-01T00:00:00Z",
            },
        ]
        conn = _FakeConn(store)
        p1, p2 = self._patches()
        with p1, p2:
            revision = next_app.emit_movie_identifiers_change(conn, "movie-1", operation="upsert")
        self.assertEqual(revision, 1)
        change = store["global_changes"][-1]
        self.assertEqual(change["entity_type"], "movie_identifier")
        self.assertEqual(change["operation"], "upsert")
        self.assertEqual(change["entity_id"], "movie-1")
        self.assertEqual(change["payload"]["movieId"], "movie-1")
        providers = {i["provider_id"] for i in change["payload"]["identifiers"]}
        self.assertIn("movievault_26", providers)
        self.assertIn("tmdb", providers)

    def test_bootstrap_collectors_scope_to_the_caller(self):
        store = self._store()
        store["watchlist"][("user-a", "movie-1")] = {
            "movie_id": "movie-1",
            "added_at": "2026-01-01T00:00:00Z",
            "snapshot": {},
        }
        store["watchlist"][("user-b", "movie-2")] = {
            "movie_id": "movie-2",
            "added_at": "2026-01-01T00:00:00Z",
            "snapshot": {},
        }
        store["watch_history"][("user-a", "entry-1")] = {
            "id": "entry-1",
            "movie_id": "movie-1",
            "watched_at": "2026-01-03T00:00:00Z",
            "created_at": "2026-01-03T00:00:00Z",
            "snapshot": {},
        }
        conn = _FakeConn(store)
        with patch.object(next_app, "table_exists", lambda conn, name: True):
            watchlist = next_app.all_watchlist_sync_entities(conn, "user-a")
            history = next_app.all_watch_history_sync_entities(conn, "user-a")
        self.assertEqual([w["movieId"] for w in watchlist], ["movie-1"])
        self.assertEqual([h["id"] for h in history], ["entry-1"])


if __name__ == "__main__":
    unittest.main()
