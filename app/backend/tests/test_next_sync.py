import os
import sys
import unittest
import uuid
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
        self.assertIn("/api/next/sync/reconcile", rules)

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


@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class NextBatchCreateHealTests(unittest.TestCase):
    """resolve_new_movie_identity must keep batched brand-new creates distinct.

    Regression guard for the data-loss bug where a single mutations batch of N
    fresh movie.upsert creates (e.g. an 8-film box set) collapsed into one row.
    """

    def _resolve(self, *, client_entity_id, barcode, batch_ctx, mapping=None, owner=None):
        return next_app.resolve_new_movie_identity(
            entity_id=None,
            client_entity_id=client_entity_id,
            barcode=barcode,
            batch_ctx=batch_ctx,
            lookup_mapping=lambda: mapping,
            barcode_owner_lookup=lambda code: owner,
        )

    def test_duplicate_client_entity_id_yields_distinct_movies(self):
        # Client (buggily) reuses one clientEntityId for every member of a box set.
        ctx = next_app.SyncBatchContext()
        ids = []
        for _ in range(8):
            entity_id, _barcode = self._resolve(
                client_entity_id="tmp-box", barcode=None, batch_ctx=ctx, mapping=None
            )
            ids.append(entity_id)
        self.assertEqual(len(set(ids)), 8, "each create must become a distinct movie")

    def test_missing_client_entity_id_yields_distinct_movies(self):
        ctx = next_app.SyncBatchContext()
        ids = [self._resolve(client_entity_id=None, barcode=None, batch_ctx=ctx)[0] for _ in range(8)]
        self.assertEqual(len(set(ids)), 8)

    def test_shared_barcode_is_healed_after_the_first_member(self):
        # All 8 members carry the box's shared barcode; only the first keeps it,
        # the rest are inserted with a null barcode instead of colliding.
        ctx = next_app.SyncBatchContext()
        barcodes = []
        for _ in range(8):
            _entity_id, healed = self._resolve(
                client_entity_id=None, barcode="5051890000000", batch_ctx=ctx, owner=None
            )
            barcodes.append(healed)
        self.assertEqual(barcodes[0], "5051890000000")
        self.assertTrue(all(b is None for b in barcodes[1:]))

    def test_barcode_owned_by_existing_movie_is_dropped(self):
        ctx = next_app.SyncBatchContext()
        _entity_id, healed = self._resolve(
            client_entity_id=None,
            barcode="5051890000000",
            batch_ctx=ctx,
            owner=uuid.uuid4(),  # a different, pre-existing movie already owns it
        )
        self.assertIsNone(healed)

    def test_update_path_is_passthrough(self):
        existing = uuid.uuid4()
        entity_id, healed = next_app.resolve_new_movie_identity(
            entity_id=existing,
            client_entity_id="tmp-box",
            barcode="5051890000000",
            batch_ctx=next_app.SyncBatchContext(),
            lookup_mapping=lambda: uuid.uuid4(),
            barcode_owner_lookup=lambda code: uuid.uuid4(),
        )
        self.assertEqual(entity_id, existing)
        self.assertEqual(healed, "5051890000000")

    def test_first_occurrence_still_resolves_cross_batch_mapping(self):
        # A genuine temp-id created in an earlier committed batch must still map
        # to its server UUID (offline create -> later reference).
        mapped = uuid.uuid4()
        ctx = next_app.SyncBatchContext()
        entity_id, _barcode = self._resolve(
            client_entity_id="tmp-earlier", barcode=None, batch_ctx=ctx, mapping=mapped
        )
        self.assertEqual(entity_id, mapped)

    def test_container_sync_upsert_receives_authenticated_actor(self):
        actor = {"id": "creator-1", "role": "media_editor"}
        mutation = {
            "clientMutationId": "mutation-1",
            "entityType": "container",
            "operation": "upsert",
            "payload": {"title": "Owned vault", "containerType": "vault"},
        }
        applied = {
            "clientMutationId": "mutation-1",
            "status": "applied",
            "entityType": "container",
            "operation": "upsert",
            "entityId": uuid.uuid4(),
        }

        with (
            patch.object(next_app, "stored_idempotency_response", return_value=None),
            patch.object(next_app, "apply_container_upsert", return_value=applied) as upsert,
            patch.object(next_app, "store_idempotency_response"),
        ):
            result = next_app.apply_sync_mutation(
                object(),
                client_id="client-1",
                mutation=mutation,
                actor=actor,
            )

        self.assertEqual(result, applied)
        upsert.assert_called_once()
        self.assertIs(upsert.call_args.kwargs["actor"], actor)


@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class NextSyncDedupMigrationContractTests(unittest.TestCase):
    """Migration 045 must declare the dedup/tombstone schema (Deel 1 + 2)."""

    def setUp(self):
        self.migrations = {m.version: m for m in discover_migrations()}

    def test_dedup_migration_is_present(self):
        self.assertIn("045", self.migrations)

    def test_dedup_migration_declares_client_id_and_unique_index(self):
        sql = self.migrations["045"].sql
        self.assertIn("client_id", sql)
        # Single shared catalogue -> partial unique on client_id (not per-owner).
        self.assertIn("uq_movies_client_id", sql)

    def test_dedup_migration_declares_tombstones(self):
        sql = self.migrations["045"].sql
        # Soft-delete columns on movies AND containers (box sets).
        self.assertIn("deleted_at", sql)
        self.assertIn("ALTER TABLE movies", sql)
        self.assertIn("ALTER TABLE containers", sql)


@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class NextIdentityLadderTests(unittest.TestCase):
    """Create-path identity ladder: clientId -> barcode -> tmdbEdition (DoD Deel 1)."""

    def _match(self, **overrides):
        kwargs = dict(
            persistent_client_id=None,
            batch_claimed_client_id=None,
            barcode_normalized=None,
            barcode_claimed_in_batch=False,
            tmdb_id=None,
            fmt=None,
            duplicate_copy=False,
            find_by_client_id=lambda: None,
            find_by_barcode=lambda: None,
            find_by_tmdb_edition=lambda: None,
        )
        kwargs.update(overrides)
        return next_app.match_existing_movie(**kwargs)

    def test_same_client_id_in_batch_matches_the_first_create(self):
        # A retry (same persistent clientId) resolves to the id claimed earlier
        # in the batch -> one record, second call is "matched".
        first = uuid.uuid4()
        entity_id, matched_by = self._match(
            persistent_client_id="rec-uuid", batch_claimed_client_id=first
        )
        self.assertEqual(entity_id, first)
        self.assertEqual(matched_by, "clientId")

    def test_client_id_matches_existing_live_movie(self):
        # Retry after a committed batch (timeout replay): clientId already on a row.
        existing = uuid.uuid4()
        entity_id, matched_by = self._match(
            persistent_client_id="rec-uuid", find_by_client_id=lambda: existing
        )
        self.assertEqual(entity_id, existing)
        self.assertEqual(matched_by, "clientId")

    def test_existing_barcode_matches_by_barcode(self):
        existing = uuid.uuid4()
        entity_id, matched_by = self._match(
            barcode_normalized="5051890000000", find_by_barcode=lambda: existing
        )
        self.assertEqual(entity_id, existing)
        self.assertEqual(matched_by, "barcode")

    def test_duplicate_copy_skips_barcode_tier(self):
        # An explicit second physical copy must NOT match -> create a new record.
        entity_id, matched_by = self._match(
            barcode_normalized="5051890000000",
            duplicate_copy=True,
            find_by_barcode=lambda: uuid.uuid4(),
        )
        self.assertIsNone(entity_id)
        self.assertIsNone(matched_by)

    def test_batch_claimed_barcode_skips_barcode_tier(self):
        # Box-set members sharing the container EAN must stay distinct rows.
        entity_id, _ = self._match(
            barcode_normalized="5051890000000",
            barcode_claimed_in_batch=True,
            find_by_barcode=lambda: uuid.uuid4(),
        )
        self.assertIsNone(entity_id)

    def test_tmdb_edition_matches_when_format_present(self):
        existing = uuid.uuid4()
        entity_id, matched_by = self._match(
            tmdb_id="603", fmt="4K UHD", find_by_tmdb_edition=lambda: existing
        )
        self.assertEqual(entity_id, existing)
        self.assertEqual(matched_by, "tmdbEdition")

    def test_dvd_and_4k_of_same_film_stay_two_records(self):
        # Anti-over-merge: the tmdb+format+edition lookup only returns a match for
        # the *same* format. A different format resolves to no match -> create.
        # Model: the DVD already exists; the incoming 4K's format-scoped lookup
        # finds nothing, so the ladder returns None and the caller inserts a 2nd row.
        entity_id, matched_by = self._match(
            tmdb_id="603", fmt="4K UHD", find_by_tmdb_edition=lambda: None
        )
        self.assertIsNone(entity_id)
        self.assertIsNone(matched_by)

    def test_tmdb_tier_requires_a_format(self):
        # Missing format -> tmdb tier is skipped entirely (over-merge protection).
        entity_id, _ = self._match(
            tmdb_id="603", fmt=None, find_by_tmdb_edition=lambda: uuid.uuid4()
        )
        self.assertIsNone(entity_id)


@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class NextTitleNormalizationTests(unittest.TestCase):
    """normalize_title + find_movie_by_title_year (trede 4, adoption-only)."""

    def test_diacritics_and_article_are_folded(self):
        self.assertEqual(next_app.normalize_title("Amélie"), "amelie")
        self.assertEqual(
            next_app.normalize_title("The Matrix"), next_app.normalize_title("matrix")
        )

    def test_punctuation_and_case_collapse(self):
        self.assertEqual(
            next_app.normalize_title("WALL·E"), next_app.normalize_title("wall e")
        )

    def test_empty_title_normalizes_to_none(self):
        self.assertIsNone(next_app.normalize_title(None))
        self.assertIsNone(next_app.normalize_title("   "))

    def _title_year_conn(self, rows):
        class _Cur:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def execute(self_inner, sql, params=()):
                self_inner._rows = list(rows)

            def fetchall(self_inner):
                return list(self_inner._rows)

        class _Conn:
            def cursor(self_inner):
                return _Cur()

        return _Conn()

    def test_missing_format_refuses_to_match(self):
        # Over-merge protection: no incoming format -> no match, always create.
        conn = self._title_year_conn([{"id": uuid.uuid4(), "title": "The Matrix", "year": "1999"}])
        self.assertIsNone(
            next_app.find_movie_by_title_year(conn, title="The Matrix", year="1999", fmt=None)
        )

    def test_normalized_title_and_year_match(self):
        target = uuid.uuid4()
        conn = self._title_year_conn([{"id": target, "title": "Amélie", "year": "2001"}])
        found = next_app.find_movie_by_title_year(
            conn, title="amelie", year="2001", fmt="Blu-ray"
        )
        self.assertEqual(found, target)

    def test_no_row_matches_returns_none(self):
        conn = self._title_year_conn([{"id": uuid.uuid4(), "title": "Different", "year": "2001"}])
        self.assertIsNone(
            next_app.find_movie_by_title_year(conn, title="Amelie", year="2001", fmt="Blu-ray")
        )


@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class NextReconcileLadderTests(unittest.TestCase):
    """First-connect adoption ladder adds the title+year tier (Deel 1.4)."""

    def _match(self, **overrides):
        kwargs = dict(
            persistent_client_id=None,
            barcode_normalized=None,
            tmdb_id=None,
            fmt=None,
            title=None,
            year=None,
            find_by_client_id=lambda: None,
            find_by_barcode=lambda: None,
            find_by_tmdb_edition=lambda: None,
            find_by_title_year=lambda: None,
        )
        kwargs.update(overrides)
        return next_app.match_reconcile_item(**kwargs)

    def test_title_year_tier_is_active_in_reconcile(self):
        existing = uuid.uuid4()
        entity_id, matched_by = self._match(
            title="The Matrix",
            year="1999",
            fmt="4K UHD",
            find_by_title_year=lambda: existing,
        )
        self.assertEqual(entity_id, existing)
        self.assertEqual(matched_by, "titleYear")

    def test_client_id_takes_precedence_over_title_year(self):
        by_client = uuid.uuid4()
        entity_id, matched_by = self._match(
            persistent_client_id="rec-uuid",
            title="The Matrix",
            year="1999",
            fmt="4K UHD",
            find_by_client_id=lambda: by_client,
            find_by_title_year=lambda: uuid.uuid4(),
        )
        self.assertEqual(entity_id, by_client)
        self.assertEqual(matched_by, "clientId")

    def test_unknown_item_returns_no_match(self):
        entity_id, matched_by = self._match(title="Ghost", year="1990", fmt="DVD")
        self.assertIsNone(entity_id)
        self.assertIsNone(matched_by)


if __name__ == "__main__":
    unittest.main()
