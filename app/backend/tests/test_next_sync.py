import os
import sys
import types
import unittest
import uuid
from datetime import datetime, timezone
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
@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class NextContainerSyncArtworkTests(unittest.TestCase):
    """A box set pushed from another client carries the cover its source
    resolved. Dropping it leaves the container falling back to a member film's
    poster, which is a different film's cover."""

    def test_reads_the_pushed_cover_from_the_payload_or_metadata(self):
        cover = "https://movies2.vaultstack.eu/v2/assets/abc/display"
        for label, payload, metadata in (
            ("camelCase root", {"posterUrl": cover}, None),
            ("snake_case root", {"poster_url": cover}, None),
            ("bare key", {"poster": cover}, None),
            ("metadata fallback", {}, {"poster_url": cover}),
        ):
            with self.subTest(case=label):
                self.assertEqual(
                    next_app.container_sync_artwork_urls(payload, metadata),
                    {"poster": cover},
                )

    def test_payload_root_wins_over_metadata(self):
        cover = "https://movies2.vaultstack.eu/v2/assets/current/display"
        urls = next_app.container_sync_artwork_urls(
            {"posterUrl": cover},
            {"poster_url": "https://movies2.vaultstack.eu/v2/assets/stale/display"},
        )

        self.assertEqual(urls["poster"], cover)

    def test_backdrop_is_read_alongside_the_poster(self):
        urls = next_app.container_sync_artwork_urls(
            {
                "posterUrl": "https://movies2.vaultstack.eu/v2/assets/a/display",
                "backdropUrl": "https://movies2.vaultstack.eu/v2/assets/b/display",
            }
        )

        self.assertEqual(sorted(urls), ["backdrop", "poster"])

    def test_ignores_values_that_are_not_fetchable_urls(self):
        for label, payload in (
            ("relative path", {"posterUrl": "/v2/assets/abc/display"}),
            ("non-string", {"posterUrl": 42}),
            ("blank", {"posterUrl": "   "}),
            ("nothing", {}),
        ):
            with self.subTest(case=label):
                self.assertEqual(next_app.container_sync_artwork_urls(payload), {})

    def test_attach_requests_primary_without_overwriting_a_chosen_cover(self):
        """`link_container_media_option` only honours `primary` when no primary
        artwork of that kind exists, so a cover the user chose is preserved."""
        cover = "https://movies2.vaultstack.eu/v2/assets/abc/display"
        container_id = uuid.uuid4()
        calls = []

        class _Savepoint:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        conn = types.SimpleNamespace(transaction=lambda: _Savepoint())
        with patch.object(
            next_app,
            "link_container_media_option",
            side_effect=lambda _conn, **kwargs: calls.append(kwargs),
        ):
            next_app.attach_container_sync_artwork(
                conn,
                container_id=container_id,
                payload={"posterUrl": cover},
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["container_id"], container_id)
        self.assertEqual(calls[0]["kind"], "poster")
        self.assertEqual(calls[0]["source_url"], cover)
        self.assertTrue(calls[0]["primary"])

    def test_attach_failure_never_fails_the_sync_mutation(self):
        """Artwork is supplementary: a container must still sync when its cover
        cannot be registered."""

        class _Savepoint:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        conn = types.SimpleNamespace(transaction=lambda: _Savepoint())
        with patch.object(
            next_app,
            "link_container_media_option",
            side_effect=RuntimeError("media backend unavailable"),
        ):
            next_app.attach_container_sync_artwork(
                conn,
                container_id=uuid.uuid4(),
                payload={"posterUrl": "https://movies2.vaultstack.eu/v2/assets/a/display"},
            )


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

    def test_duplicate_copy_skips_tmdb_edition_tier(self):
        # The case the flag exists for: two copies of ONE edition share their tmdb
        # id, format and edition by definition. Suppressing only tier 2 left tier 3
        # to match them straight back together, so the client's deliberate "add
        # anyway" undid itself on the next sync.
        entity_id, matched_by = self._match(
            barcode_normalized="5051890000000",
            tmdb_id="157336",
            fmt="4K UHD",
            duplicate_copy=True,
            find_by_barcode=lambda: uuid.uuid4(),
            find_by_tmdb_edition=lambda: uuid.uuid4(),
        )
        self.assertIsNone(entity_id)
        self.assertIsNone(matched_by)

    def test_duplicate_copy_skips_client_id_tier(self):
        # Tier 1 cannot legitimately fire for a second copy either: tokens are never
        # copied (contract §6), so one always arrives with a fresh token. Pinned so
        # the short-circuit can't be narrowed back to "all tiers except the first".
        entity_id, matched_by = self._match(
            persistent_client_id="rec-uuid",
            duplicate_copy=True,
            find_by_client_id=lambda: uuid.uuid4(),
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
class NextTmdbEditionSafetyTests(unittest.TestCase):
    def _tmdb_conn(self, rows):
        class _Cur:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def execute(self_inner, sql, params=()):
                if "to_regclass" in sql:
                    self_inner._one = {"table_name": "public.movie_identifiers"}
                    self_inner._rows = []
                    return
                self_inner._rows = list(rows)
                self_inner._one = None

            def fetchall(self_inner):
                return list(self_inner._rows)

            def fetchone(self_inner):
                return self_inner._one

        class _Conn:
            def cursor(self_inner):
                return _Cur()

        return _Conn()

    def test_tmdb_tier_blocks_conflicting_barcodes(self):
        target = uuid.uuid4()
        conn = self._tmdb_conn(
            [
                {
                    "id": target,
                    "title": "The Matrix",
                    "year": "1999",
                    "barcode": "2222222222222",
                    "format": "4K UHD",
                }
            ]
        )
        found = next_app.find_movie_by_tmdb_edition(
            conn,
            tmdb_id="603",
            fmt="4K UHD",
            edition="",
            incoming_title="The Matrix",
            incoming_year="1999",
            incoming_barcode="1111111111111",
        )
        self.assertIsNone(found)

    def test_tmdb_tier_blocks_materially_different_title_or_year(self):
        target = uuid.uuid4()
        conn = self._tmdb_conn(
            [
                {
                    "id": target,
                    "title": "Harry Potter and the Deathly Hallows: Part 1",
                    "year": "2010",
                    "barcode": None,
                    "format": "4K UHD",
                }
            ]
        )
        found = next_app.find_movie_by_tmdb_edition(
            conn,
            tmdb_id="12444",
            fmt="4K UHD",
            edition="",
            incoming_title="Harry Potter and the Deathly Hallows: Part 2",
            incoming_year="2011",
            incoming_barcode=None,
        )
        self.assertIsNone(found)

    def test_tmdb_tier_normalizes_format_variants(self):
        target = uuid.uuid4()
        conn = self._tmdb_conn(
            [
                {
                    "id": target,
                    "title": "The Matrix",
                    "year": "1999",
                    "barcode": "1111111111111",
                    "format": "4K_UHD",
                }
            ]
        )
        found = next_app.find_movie_by_tmdb_edition(
            conn,
            tmdb_id="603",
            fmt="4K UHD",
            edition="",
            incoming_title="The Matrix",
            incoming_year="1999",
            incoming_barcode="1111111111111",
        )
        self.assertEqual(found, target)


@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class NextTitleNormalizationTests(unittest.TestCase):
    """normalize_title + find_movie_by_title_year (trede 4, adoption-only)."""

    def test_diacritics_and_article_are_folded(self):
        self.assertEqual(next_app.normalize_title("Amélie"), "amelie")
        self.assertEqual(
            next_app.normalize_title("The Matrix"), next_app.normalize_title("matrix")
        )
        self.assertEqual(next_app.normalize_title("Die Hard"), "die hard")
        self.assertEqual(next_app.normalize_title("De Aanslag"), "de aanslag")

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
        conn = self._title_year_conn(
            [{"id": uuid.uuid4(), "title": "The Matrix", "year": "1999", "format": "Blu-ray"}]
        )
        self.assertIsNone(
            next_app.find_movie_by_title_year(conn, title="The Matrix", year="1999", fmt=None)
        )

    def test_normalized_title_and_year_match(self):
        target = uuid.uuid4()
        conn = self._title_year_conn(
            [{"id": target, "title": "Amélie", "year": "2001", "format": "Blu_ray", "barcode": None}]
        )
        found = next_app.find_movie_by_title_year(
            conn, title="amelie", year="2001", fmt="Blu-ray"
        )
        self.assertEqual(found, target)

    def test_title_year_tier_blocks_conflicting_barcodes(self):
        target = uuid.uuid4()
        conn = self._title_year_conn(
            [
                {
                    "id": target,
                    "title": "The Matrix",
                    "year": "1999",
                    "format": "4K UHD",
                    "barcode": "2222222222222",
                }
            ]
        )
        self.assertIsNone(
            next_app.find_movie_by_title_year(
                conn,
                title="The Matrix",
                year="1999",
                fmt="4K_UHD",
                incoming_barcode="1111111111111",
            )
        )

    def test_title_year_tier_blocks_box_set_members_from_standalone_copies(self):
        cases = (
            ("Jurassic Park", "1993"),
            ("Jurassic Park III", "2001"),
            ("The Dark Knight Rises", "2012"),
            ("The Lost World: Jurassic Park", "1997"),
        )
        for title, year in cases:
            with self.subTest(title=title):
                conn = self._title_year_conn(
                    [
                        {
                            "id": uuid.uuid4(),
                            "title": title,
                            "year": year,
                            "format": "4K_UHD",
                            "barcode": None,
                            "edition": "Box-set member",
                            "container_ids": [str(uuid.uuid4())],
                        }
                    ]
                )
                self.assertIsNone(
                    next_app.find_movie_by_title_year(
                        conn,
                        title=title,
                        year=year,
                        fmt="4K UHD",
                        incoming_edition=None,
                        incoming_container_ids=[],
                    )
                )

    def test_title_year_tier_blocks_different_container_memberships(self):
        first_container = str(uuid.uuid4())
        second_container = str(uuid.uuid4())
        conn = self._title_year_conn(
            [
                {
                    "id": uuid.uuid4(),
                    "title": "Jurassic Park",
                    "year": "1993",
                    "format": "4K UHD",
                    "barcode": None,
                    "edition": None,
                    "container_ids": [first_container],
                }
            ]
        )
        self.assertIsNone(
            next_app.find_movie_by_title_year(
                conn,
                title="Jurassic Park",
                year="1993",
                fmt="4K UHD",
                incoming_container_ids=[second_container],
            )
        )

    def test_title_year_tier_allows_same_container_duplicate_stub(self):
        target = uuid.uuid4()
        container_id = str(uuid.uuid4())
        conn = self._title_year_conn(
            [
                {
                    "id": target,
                    "title": "Jurassic Park",
                    "year": "1993",
                    "format": "4K_UHD",
                    "barcode": None,
                    "edition": None,
                    "container_ids": [container_id],
                }
            ]
        )
        self.assertEqual(
            next_app.find_movie_by_title_year(
                conn,
                title="Jurassic Park",
                year="1993",
                fmt="4K UHD",
                incoming_container_ids=[container_id],
            ),
            target,
        )

    def test_title_year_tier_blocks_partial_edition_inside_same_container(self):
        container_id = str(uuid.uuid4())
        conn = self._title_year_conn(
            [
                {
                    "id": uuid.uuid4(),
                    "title": "Jurassic Park",
                    "year": "1993",
                    "format": "4K UHD",
                    "barcode": None,
                    "edition": "Box-set member",
                    "container_ids": [container_id],
                }
            ]
        )
        self.assertIsNone(
            next_app.find_movie_by_title_year(
                conn,
                title="Jurassic Park",
                year="1993",
                fmt="4K UHD",
                incoming_edition=None,
                incoming_container_ids=[container_id],
            )
        )

    def test_title_year_tier_blocks_conflicting_explicit_editions(self):
        conn = self._title_year_conn(
            [
                {
                    "id": uuid.uuid4(),
                    "title": "Blade Runner",
                    "year": "1982",
                    "format": "4K UHD",
                    "barcode": None,
                    "edition": "Final Cut",
                    "container_ids": [],
                }
            ]
        )
        self.assertIsNone(
            next_app.find_movie_by_title_year(
                conn,
                title="Blade Runner",
                year="1982",
                fmt="4K UHD",
                incoming_edition="Theatrical Cut",
            )
        )

    def test_title_year_tier_preserves_bourne_missing_edition_adoption(self):
        target = uuid.uuid4()
        conn = self._title_year_conn(
            [
                {
                    "id": target,
                    "title": "The Bourne Identity",
                    "year": "2002",
                    "format": "4K UHD",
                    "barcode": None,
                    "edition": "The Bourne Identity",
                    "container_ids": [],
                }
            ]
        )
        self.assertEqual(
            next_app.find_movie_by_title_year(
                conn,
                title="The Bourne Identity",
                year="2002",
                fmt="4K_UHD",
                incoming_edition=None,
            ),
            target,
        )

    def test_title_year_tier_preserves_greatest_showman_stub_adoption(self):
        targets = [uuid.uuid4(), uuid.uuid4()]
        rows = [
            {
                "id": target,
                "title": "The Greatest Showman",
                "year": "2017",
                "format": "4K UHD",
                "barcode": None,
                "edition": None,
                "container_ids": [],
            }
            for target in targets
        ]
        conn = self._title_year_conn(rows)
        self.assertIn(
            next_app.find_movie_by_title_year(
                conn,
                title="The Greatest Showman",
                year="2017",
                fmt="4K_UHD",
                incoming_edition="Standalone release",
            ),
            targets,
        )

    def test_no_row_matches_returns_none(self):
        conn = self._title_year_conn(
            [{"id": uuid.uuid4(), "title": "Different", "year": "2001", "format": "Blu-ray"}]
        )
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


class NextContainerReconcileLadderTests(unittest.TestCase):
    """First-connect adoption ladder for containers (sync-contract.md §5b)."""

    def _match(self, **overrides):
        kwargs = dict(
            persistent_client_id=None,
            barcode_normalized=None,
            identifier_type=None,
            identifier=None,
            container_type=None,
            title=None,
            find_by_client_id=lambda: None,
            find_by_barcode=lambda: None,
            find_by_identifier=lambda: None,
            find_by_title_type=lambda: None,
        )
        kwargs.update(overrides)
        return next_app.match_reconcile_container(**kwargs)

    def test_title_type_tier_is_active_in_reconcile(self):
        existing = uuid.uuid4()
        entity_id, matched_by = self._match(
            title="Kids",
            container_type="vault",
            find_by_title_type=lambda: existing,
        )
        self.assertEqual(entity_id, existing)
        self.assertEqual(matched_by, "titleType")

    def test_client_id_takes_precedence_over_title_type(self):
        by_client = uuid.uuid4()
        entity_id, matched_by = self._match(
            persistent_client_id="rec-uuid",
            title="Kids",
            container_type="vault",
            find_by_client_id=lambda: by_client,
            find_by_title_type=lambda: uuid.uuid4(),
        )
        self.assertEqual(entity_id, by_client)
        self.assertEqual(matched_by, "clientId")

    def test_barcode_takes_precedence_over_identifier_and_title_type(self):
        by_barcode = uuid.uuid4()
        entity_id, matched_by = self._match(
            barcode_normalized="5051888240816",
            identifier_type="external_id",
            identifier="tmdb-collection-1",
            title="Kids",
            container_type="vault",
            find_by_barcode=lambda: by_barcode,
            find_by_identifier=lambda: uuid.uuid4(),
            find_by_title_type=lambda: uuid.uuid4(),
        )
        self.assertEqual(entity_id, by_barcode)
        self.assertEqual(matched_by, "barcode")

    def test_unknown_item_returns_no_match(self):
        entity_id, matched_by = self._match(title="Ghost Vault", container_type="vault")
        self.assertIsNone(entity_id)
        self.assertIsNone(matched_by)


@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class NextMovieUpsertBarcodeConflictTests(unittest.TestCase):
    class _RecordingCursor:
        def __init__(self, calls):
            self._calls = calls

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=()):
            self._calls.append((" ".join(str(sql).split()), tuple(params or ())))

        def fetchone(self):
            return None

    class _RecordingConn:
        def __init__(self):
            self.calls = []

        def cursor(self):
            return NextMovieUpsertBarcodeConflictTests._RecordingCursor(self.calls)

    def _fields(self, barcode):
        return {
            "metadata": {},
            "barcode": barcode,
            "title": "The Matrix",
            "sort_title": "the matrix",
            "original_title": None,
            "release_title": None,
            "year": "1999",
            "release_date": None,
            "format": "4K UHD",
            "media_type": "MOVIE",
            "edition": "",
            "edition_type": None,
            "country": None,
            "language": None,
            "runtime_minutes": None,
            "overview": None,
            "notes": None,
            "rating": None,
            "purchase_date": None,
            "purchase_price": None,
            "estimated_value": None,
            "estimated_value_currency": None,
            "location": None,
        }

    def _mutation(self, *, barcode, duplicate_copy=False):
        return {
            "clientMutationId": "mutation-1",
            "clientEntityId": "tmp-1",
            "payload": {
                "barcode": barcode,
                "title": "The Matrix",
                "format": "4K UHD",
                "metadata": {},
                "updated_at": "2026-07-30T12:00:00Z",
                "duplicate_copy": duplicate_copy,
            },
        }

    def _insert_barcodes(self, conn):
        insert_calls = [params for sql, params in conn.calls if "INSERT INTO movies" in sql]
        self.assertEqual(len(insert_calls), 1)
        return insert_calls[0][2]

    def test_create_conflicting_barcode_adopts_live_owner(self):
        conn = self._RecordingConn()
        ladder_id = uuid.uuid4()
        barcode_owner = uuid.uuid4()
        owner_entity = {"id": barcode_owner, "title": "The Matrix", "deleted_at": None}

        with (
            patch.object(next_app, "movie_payload_fields", return_value=self._fields("3701432061177")),
            patch.object(next_app, "client_entity_mapping", return_value=None),
            patch.object(next_app, "match_existing_movie", return_value=(ladder_id, "clientId")),
            patch.object(next_app, "find_movie_by_barcode_match", return_value=barcode_owner),
            patch.object(next_app, "movie_entity", side_effect=[owner_entity, owner_entity]),
            patch.object(next_app, "store_client_entity_mapping"),
            patch.object(next_app, "sync_change"),
            patch.object(next_app, "next_revision", return_value=99),
            patch.object(next_app, "table_exists", return_value=False),
        ):
            result = next_app.apply_movie_upsert(
                conn,
                client_id="device-a",
                idem_key="idem-1",
                mutation=self._mutation(barcode="3701432061177"),
                batch_ctx=next_app.SyncBatchContext(),
            )

        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["entityId"], barcode_owner)
        self.assertEqual(result["matchedBy"], "barcode")
        self.assertFalse(result["created"])
        self.assertEqual(self._insert_barcodes(conn), "3701432061177")

    def test_duplicate_copy_keeps_target_and_drops_conflicting_barcode(self):
        conn = self._RecordingConn()
        ladder_id = uuid.uuid4()
        barcode_owner = uuid.uuid4()
        ladder_entity = {"id": ladder_id, "title": "The Matrix", "deleted_at": None}

        with (
            patch.object(next_app, "movie_payload_fields", return_value=self._fields("5051889647010")),
            patch.object(next_app, "client_entity_mapping", return_value=None),
            patch.object(next_app, "match_existing_movie", return_value=(ladder_id, "tmdbEdition")),
            patch.object(next_app, "find_movie_by_barcode_match", return_value=barcode_owner),
            patch.object(next_app, "movie_entity", side_effect=[ladder_entity, ladder_entity]),
            patch.object(next_app, "store_client_entity_mapping"),
            patch.object(next_app, "sync_change"),
            patch.object(next_app, "next_revision", return_value=100),
            patch.object(next_app, "table_exists", return_value=False),
        ):
            result = next_app.apply_movie_upsert(
                conn,
                client_id="device-a",
                idem_key="idem-2",
                mutation=self._mutation(
                    barcode="5051889647010",
                    duplicate_copy=True,
                ),
                batch_ctx=next_app.SyncBatchContext(),
            )

        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["entityId"], ladder_id)
        self.assertEqual(result["matchedBy"], "tmdbEdition")
        self.assertFalse(result["created"])
        self.assertIsNone(self._insert_barcodes(conn))

    def test_non_batch_conflicting_barcode_adopts_live_owner(self):
        # Import Center path: apply_movie_upsert() is called with batch_ctx=None,
        # so the dedup ladder never runs. resolve_new_movie_identity() detects
        # the exact barcode collision and null-heals the barcode; without the
        # belt-and-braces adoption this minted a NULL-barcode duplicate disc.
        conn = self._RecordingConn()
        barcode_owner = uuid.uuid4()
        owner_entity = {"id": barcode_owner, "title": "The Matrix", "deleted_at": None}

        def _fail_ladder(*args, **kwargs):  # ladder must not run without a batch
            raise AssertionError("match_existing_movie must not run for non-batch upserts")

        def _fail_normalized(*args, **kwargs):  # digits-only match must not be used here
            raise AssertionError("non-batch adoption must use exact barcode matching")

        with (
            patch.object(next_app, "movie_payload_fields", return_value=self._fields("3701432061177")),
            patch.object(next_app, "client_entity_mapping", return_value=None),
            patch.object(next_app, "match_existing_movie", side_effect=_fail_ladder),
            patch.object(next_app, "movie_id_for_barcode", return_value=barcode_owner),
            patch.object(next_app, "find_movie_by_barcode_match", side_effect=_fail_normalized),
            patch.object(next_app, "movie_entity", side_effect=[owner_entity, owner_entity]),
            patch.object(next_app, "store_client_entity_mapping"),
            patch.object(next_app, "sync_change"),
            patch.object(next_app, "next_revision", return_value=101),
            patch.object(next_app, "table_exists", return_value=False),
        ):
            result = next_app.apply_movie_upsert(
                conn,
                client_id="next-import-center",
                idem_key="idem-import-1",
                mutation=self._mutation(barcode="3701432061177"),
                batch_ctx=None,
            )

        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["entityId"], barcode_owner)
        self.assertEqual(result["matchedBy"], "barcode")
        self.assertFalse(result["created"])
        self.assertEqual(self._insert_barcodes(conn), "3701432061177")

    def test_non_batch_duplicate_copy_keeps_distinct_row(self):
        # An explicit duplicate copy imported outside a batch still gets its own
        # row, with the colliding barcode dropped so the UNIQUE index holds.
        conn = self._RecordingConn()
        barcode_owner = uuid.uuid4()

        with (
            patch.object(next_app, "movie_payload_fields", return_value=self._fields("5051889647010")),
            patch.object(next_app, "client_entity_mapping", return_value=None),
            patch.object(next_app, "movie_id_for_barcode", return_value=barcode_owner),
            patch.object(next_app, "movie_entity", side_effect=[None, None]),
            patch.object(next_app, "store_client_entity_mapping"),
            patch.object(next_app, "sync_change"),
            patch.object(next_app, "next_revision", return_value=102),
            patch.object(next_app, "table_exists", return_value=False),
        ):
            result = next_app.apply_movie_upsert(
                conn,
                client_id="next-import-center",
                idem_key="idem-import-2",
                mutation=self._mutation(barcode="5051889647010", duplicate_copy=True),
                batch_ctx=None,
            )

        self.assertEqual(result["status"], "applied")
        self.assertNotEqual(result["entityId"], barcode_owner)
        self.assertTrue(result["created"])
        self.assertIsNone(result["matchedBy"])
        self.assertIsNone(self._insert_barcodes(conn))


@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class NextContainerUpsertBarcodeDedupTests(unittest.TestCase):
    class _RecordingCursor:
        def __init__(self, calls):
            self._calls = calls

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=()):
            self._calls.append((" ".join(str(sql).split()), tuple(params or ())))

        def fetchone(self):
            return None

    class _RecordingConn:
        def __init__(self):
            self.calls = []

        def cursor(self):
            return NextContainerUpsertBarcodeDedupTests._RecordingCursor(self.calls)

    def _mutation(self, *, barcode, entity_id=None, client_entity_id="tmp-container-1"):
        mutation: dict = {
            "clientMutationId": "container-mutation-1",
            "clientEntityId": client_entity_id,
            "payload": {
                "title": "Alien Anthology",
                "containerType": "box_set",
                "barcode": barcode,
                "metadata": {},
            },
        }
        if entity_id is not None:
            mutation["entityId"] = str(entity_id)
        return mutation

    def _insert_container_id(self, conn):
        insert_calls = [params for sql, params in conn.calls if "INSERT INTO containers" in sql]
        self.assertEqual(len(insert_calls), 1)
        return insert_calls[0][0]  # id is the first bound parameter

    def _run(self, mutation, **patches):
        conn = self._RecordingConn()
        owner_id = uuid.uuid4()
        defaults = dict(
            table_exists=True,
            client_entity_mapping=None,
            container_entity=None,
            single_container_sync_entity={},
            next_revision=200,
            actor_or_instance_owner_id=owner_id,
        )
        defaults.update(patches)
        with (
            patch.object(next_app, "table_exists", return_value=defaults["table_exists"]),
            patch.object(next_app, "client_entity_mapping", return_value=defaults["client_entity_mapping"]),
            patch.object(next_app, "container_entity", return_value=defaults["container_entity"]),
            patch.object(next_app, "single_container_sync_entity", return_value=defaults["single_container_sync_entity"]),
            patch.object(next_app, "store_client_entity_mapping"),
            patch.object(next_app, "sync_change"),
            patch.object(next_app, "next_revision", return_value=defaults["next_revision"]),
            patch.object(next_app, "actor_or_instance_owner_id", return_value=defaults["actor_or_instance_owner_id"]),
            patch.object(next_app, "find_container_by_barcode", **defaults["find_container_by_barcode_kw"]),
        ):
            result = next_app.apply_container_upsert(
                conn,
                client_id="next-import-center",
                idem_key="idem-container-1",
                mutation=mutation,
            )
        return conn, result

    def test_new_barcode_container_adopts_existing_owner(self):
        # No entityId, no clientEntityId mapping: a fresh box-set push whose EAN
        # already belongs to a live container must adopt it, not duplicate.
        existing_container = uuid.uuid4()
        conn, result = self._run(
            self._mutation(barcode="5051892023047"),
            find_container_by_barcode_kw={"return_value": existing_container},
        )
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["entityId"], existing_container)
        self.assertEqual(self._insert_container_id(conn), existing_container)

    def test_new_barcode_container_without_match_creates_fresh(self):
        conn, result = self._run(
            self._mutation(barcode="5051892023047"),
            find_container_by_barcode_kw={"return_value": None},
        )
        self.assertEqual(result["status"], "applied")
        # A brand-new uuid was minted and used for the insert.
        self.assertEqual(self._insert_container_id(conn), result["entityId"])

    def test_provided_entity_id_skips_barcode_dedup(self):
        # An explicit entityId is an update; barcode adoption must not run and
        # cannot silently retarget the row to a different container.
        provided = uuid.uuid4()

        def _fail(*args, **kwargs):
            raise AssertionError("barcode dedup must not run when entityId is provided")

        conn, result = self._run(
            self._mutation(barcode="5051892023047", entity_id=provided),
            find_container_by_barcode_kw={"side_effect": _fail},
        )
        self.assertEqual(result["entityId"], provided)
        self.assertEqual(self._insert_container_id(conn), provided)

    def test_mapped_client_entity_skips_barcode_dedup(self):
        mapped = uuid.uuid4()

        def _fail(*args, **kwargs):
            raise AssertionError("barcode dedup must not run when a clientEntity mapping exists")

        conn, result = self._run(
            self._mutation(barcode="5051892023047"),
            client_entity_mapping=mapped,
            find_container_by_barcode_kw={"side_effect": _fail},
        )
        self.assertEqual(result["entityId"], mapped)
        self.assertEqual(self._insert_container_id(conn), mapped)


@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class NextTombstoneResponseContractTests(unittest.TestCase):
    def _fields(self):
        return {
            "metadata": {},
            "barcode": "5051890000000",
            "format": "4K UHD",
            "media_type": "MOVIE",
            "edition": "",
            "title": "The Matrix",
            "year": "1999",
        }

    def _mutation(self, *, record_client_id="record-client-1", entity_id=None):
        mutation = {
            "clientMutationId": "mutation-1",
            "clientEntityId": "local-1",
            "payload": {
                "client_id": record_client_id,
                "barcode": "5051890000000",
                "updated_at": "2020-01-01T00:00:00Z",
            },
        }
        if entity_id is not None:
            mutation["entityId"] = str(entity_id)
        return mutation

    def _assert_deleted_shape(self, result, *, entity_id, matched_by):
        self.assertEqual(result["entityId"], entity_id)
        self.assertFalse(result["created"])
        self.assertEqual(result["matchedBy"], matched_by)
        self.assertIs(result["deleted"], True)
        self.assertIs(result["tombstoned"], True)
        self.assertEqual(
            result["deletedAt"],
            datetime(2026, 7, 24, 18, 0, tzinfo=timezone.utc),
        )

    def test_same_record_client_id_returns_the_existing_tombstone(self):
        entity_id = uuid.uuid4()
        deleted_at = datetime(2026, 7, 24, 18, 0, tzinfo=timezone.utc)
        tombstone = {
            "id": entity_id,
            "deleted_at": deleted_at,
            "client_id": "record-client-1",
            "matched_by": "clientId",
        }
        with (
            patch.object(next_app, "movie_payload_fields", return_value=self._fields()),
            patch.object(next_app, "client_entity_mapping", return_value=None),
            patch.object(next_app, "match_existing_movie", return_value=(None, None)),
            patch.object(
                next_app,
                "find_tombstoned_movie_by_identity",
                return_value=tombstone,
            ),
            patch.object(
                next_app,
                "movie_entity",
                return_value={
                    "id": entity_id,
                    "deleted_at": deleted_at,
                    "client_id": "record-client-1",
                },
            ),
            patch.object(next_app, "store_client_entity_mapping") as store_mapping,
            patch.object(next_app, "next_revision", return_value=42),
        ):
            result = next_app.apply_movie_upsert(
                object(),
                client_id="device-a",
                idem_key="idem-1",
                mutation=self._mutation(),
                batch_ctx=next_app.SyncBatchContext(),
            )

        self._assert_deleted_shape(
            result,
            entity_id=entity_id,
            matched_by="clientId",
        )
        self.assertEqual(result["recordClientId"], "record-client-1")
        store_mapping.assert_called_once()

    def test_new_device_token_matching_by_barcode_returns_canonical_tombstone(self):
        entity_id = uuid.uuid4()
        deleted_at = datetime(2026, 7, 24, 18, 0, tzinfo=timezone.utc)
        tombstone = {
            "id": entity_id,
            "deleted_at": deleted_at,
            "client_id": "canonical-record-client",
            "matched_by": "barcode",
        }
        with (
            patch.object(next_app, "movie_payload_fields", return_value=self._fields()),
            patch.object(next_app, "client_entity_mapping", return_value=None),
            patch.object(next_app, "match_existing_movie", return_value=(None, None)),
            patch.object(
                next_app,
                "find_tombstoned_movie_by_identity",
                return_value=tombstone,
            ),
            patch.object(
                next_app,
                "movie_entity",
                return_value={
                    "id": entity_id,
                    "deleted_at": deleted_at,
                    "client_id": "canonical-record-client",
                },
            ),
            patch.object(next_app, "store_client_entity_mapping") as store_mapping,
            patch.object(next_app, "next_revision", return_value=43),
        ):
            result = next_app.apply_movie_upsert(
                object(),
                client_id="new-device-token",
                idem_key="idem-2",
                mutation=self._mutation(record_client_id="new-record-client"),
                batch_ctx=next_app.SyncBatchContext(),
            )

        self._assert_deleted_shape(
            result,
            entity_id=entity_id,
            matched_by="barcode",
        )
        self.assertEqual(result["recordClientId"], "canonical-record-client")
        self.assertEqual(
            store_mapping.call_args.kwargs["client_id"],
            "new-device-token",
        )

    def test_update_intent_for_tombstoned_row_is_delete_wins(self):
        entity_id = uuid.uuid4()
        deleted_at = datetime(2026, 7, 24, 18, 0, tzinfo=timezone.utc)
        mutation = self._mutation(entity_id=entity_id)
        mutation["payload"]["updated_at"] = "2026-07-25T00:00:00Z"
        with (
            patch.object(next_app, "movie_payload_fields", return_value=self._fields()),
            patch.object(
                next_app,
                "movie_entity",
                return_value={
                    "id": entity_id,
                    "deleted_at": deleted_at,
                    "client_id": "record-client-1",
                },
            ),
            patch.object(
                next_app,
                "find_tombstoned_movie_by_identity",
            ) as find_tombstone,
            patch.object(next_app, "store_client_entity_mapping"),
            patch.object(next_app, "next_revision", return_value=44),
        ):
            result = next_app.apply_movie_upsert(
                object(),
                client_id="device-a",
                idem_key="idem-3",
                mutation=mutation,
                batch_ctx=next_app.SyncBatchContext(),
            )

        self._assert_deleted_shape(
            result,
            entity_id=entity_id,
            matched_by=None,
        )
        find_tombstone.assert_not_called()


@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class NextCollectionMembershipSyncTests(unittest.TestCase):
    """Collection membership over sync (contract §4b.6/§4b.7).

    A collection keeps its members in ``collection_items``; ``containerMovie``
    writes ``container_movies``, which is never read back for a collection. The
    old behaviour returned ``applied`` for that write, and since ``isApplied`` is
    the only confirmation a client has, the client booked a membership that does
    not exist and stopped offering it. These tests pin the refusal and the route
    that replaces it."""

    def _mutation(self, entity_type, operation, payload):
        return {
            "clientMutationId": "mutation-1",
            "entityType": entity_type,
            "operation": operation,
            "payload": payload,
        }

    # --- change 1: containerMovie refuses a collection -------------------

    def test_container_movie_upsert_refuses_a_collection(self):
        container_id, movie_id = uuid.uuid4(), uuid.uuid4()
        with (
            patch.object(next_app, "table_exists", return_value=True),
            patch.object(next_app, "resolve_sync_entity_id", side_effect=[container_id, movie_id]),
            patch.object(next_app, "container_entity", return_value={"id": container_id}),
            patch.object(next_app, "movie_entity", return_value={"id": movie_id}),
            patch.object(next_app, "container_type_for_id", return_value="collection"),
        ):
            with self.assertRaises(next_app.NextApiError) as raised:
                next_app.apply_container_movie_upsert(
                    object(),
                    client_id="device-a",
                    idem_key="idem-1",
                    mutation=self._mutation(
                        "containerMovie",
                        "upsert",
                        {"containerId": str(container_id), "movieId": str(movie_id)},
                    ),
                )

        self.assertEqual(raised.exception.status_code, 400)
        # The message is the only place a client author will look for the route
        # that does work, so it has to name it.
        self.assertIn("collectionItem", str(raised.exception))

    def test_container_movie_delete_refuses_a_collection(self):
        container_id, movie_id = uuid.uuid4(), uuid.uuid4()
        with (
            patch.object(next_app, "table_exists", return_value=True),
            patch.object(next_app, "resolve_sync_entity_id", side_effect=[container_id, movie_id]),
            patch.object(next_app, "optional_container_type", return_value="collection"),
        ):
            with self.assertRaises(next_app.NextApiError) as raised:
                next_app.apply_container_movie_delete(
                    object(),
                    client_id="device-a",
                    idem_key="idem-1",
                    mutation=self._mutation(
                        "containerMovie",
                        "delete",
                        {"containerId": str(container_id), "movieId": str(movie_id)},
                    ),
                )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("collectionItem", str(raised.exception))

    # --- change 2: the collectionItem route ------------------------------

    def test_dispatcher_routes_collection_item_mutations(self):
        for operation, target in (
            ("upsert", "apply_collection_item_upsert"),
            ("delete", "apply_collection_item_delete"),
        ):
            with self.subTest(operation=operation):
                applied = {"status": "applied", "entityId": uuid.uuid4()}
                with (
                    patch.object(next_app, "stored_idempotency_response", return_value=None),
                    patch.object(next_app, target, return_value=applied) as handler,
                    patch.object(next_app, "store_idempotency_response") as store,
                ):
                    result = next_app.apply_sync_mutation(
                        object(),
                        client_id="device-a",
                        mutation=self._mutation("collectionItem", operation, {}),
                    )

                self.assertIs(result, applied)
                handler.assert_called_once()
                # Idempotency handling is generic; the new branches inherit it.
                store.assert_called_once()

    def test_snake_case_entity_type_is_still_unsupported(self):
        with (
            patch.object(next_app, "stored_idempotency_response", return_value=None),
            patch.object(next_app, "store_idempotency_response"),
        ):
            with self.assertRaises(next_app.NextApiError) as raised:
                next_app.apply_sync_mutation(
                    object(),
                    client_id="device-a",
                    mutation=self._mutation("collection_item", "upsert", {}),
                )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Unsupported mutation", str(raised.exception))

    def test_sync_state_advertises_the_collection_item_mutations(self):
        client = next_app.app.test_client()
        with (
            patch.object(next_app, "connect") as connect,
            patch.object(next_app, "table_exists", return_value=False),
            patch.object(next_app, "current_revision", return_value=7),
        ):
            connect.return_value.__enter__.return_value = object()
            payload = client.get("/api/next/sync/state").get_json()

        self.assertIn("collectionItem.upsert", payload["supportedMutations"])
        self.assertIn("collectionItem.delete", payload["supportedMutations"])

    def test_reference_accepts_snake_case_aliases(self):
        collection_id, item_id = uuid.uuid4(), uuid.uuid4()
        with patch.object(next_app, "resolve_sync_entity_id", side_effect=[collection_id, item_id]):
            resolved = next_app.collection_item_reference(
                object(),
                client_id="device-a",
                payload={
                    "collection_id": str(collection_id),
                    "item_type": "box_set",
                    "item_id": str(item_id),
                },
                label="collectionItem upsert",
            )

        self.assertEqual(resolved, (collection_id, "box_set", item_id))

    def test_reference_rejects_an_unknown_item_type(self):
        with patch.object(next_app, "resolve_sync_entity_id", return_value=uuid.uuid4()):
            with self.assertRaises(next_app.NextApiError) as raised:
                next_app.collection_item_reference(
                    object(),
                    client_id="device-a",
                    payload={
                        "collectionId": str(uuid.uuid4()),
                        "itemType": "series",
                        "itemId": str(uuid.uuid4()),
                    },
                    label="collectionItem upsert",
                )

        self.assertEqual(raised.exception.status_code, 400)

    def test_upsert_refuses_a_collection_id_that_is_a_vault(self):
        collection_id, item_id = uuid.uuid4(), uuid.uuid4()
        with (
            patch.object(next_app, "table_exists", return_value=True),
            patch.object(next_app, "resolve_sync_entity_id", side_effect=[collection_id, item_id]),
            patch.object(next_app, "container_type_for_id", return_value="vault"),
        ):
            with self.assertRaises(next_app.NextApiError) as raised:
                next_app.apply_collection_item_upsert(
                    object(),
                    client_id="device-a",
                    idem_key="idem-1",
                    mutation=self._mutation(
                        "collectionItem",
                        "upsert",
                        {
                            "collectionId": str(collection_id),
                            "itemType": "movie",
                            "itemId": str(item_id),
                        },
                    ),
                )

        self.assertEqual(raised.exception.status_code, 400)

    def test_upsert_refuses_an_item_type_that_lies_about_the_container(self):
        collection_id, item_id = uuid.uuid4(), uuid.uuid4()
        types = {collection_id: "collection", item_id: "vault"}
        with (
            patch.object(next_app, "table_exists", return_value=True),
            patch.object(next_app, "resolve_sync_entity_id", side_effect=[collection_id, item_id]),
            patch.object(next_app, "container_type_for_id", side_effect=lambda _conn, cid: types[cid]),
        ):
            with self.assertRaises(next_app.NextApiError) as raised:
                next_app.apply_collection_item_upsert(
                    object(),
                    client_id="device-a",
                    idem_key="idem-1",
                    mutation=self._mutation(
                        "collectionItem",
                        "upsert",
                        {
                            "collectionId": str(collection_id),
                            "itemType": "box_set",  # actually a vault
                            "itemId": str(item_id),
                        },
                    ),
                )

        self.assertEqual(raised.exception.status_code, 400)

    # --- the cycle rule (§4b.7) ------------------------------------------

    def test_cycle_guard_ignores_movies(self):
        collection_id = uuid.uuid4()
        with patch.object(next_app, "collection_ancestor_container_ids") as ancestors:
            next_app.guard_collection_item_cycle(
                object(), collection_id=collection_id, item_type="movie", item_id=uuid.uuid4()
            )
        # A movie holds no members, so it can never close a loop -- and asking
        # would be a query per membership write.
        ancestors.assert_not_called()

    def test_cycle_guard_refuses_a_direct_self_reference(self):
        collection_id = uuid.uuid4()
        with self.assertRaises(next_app.NextApiError) as raised:
            next_app.guard_collection_item_cycle(
                object(),
                collection_id=collection_id,
                item_type="collection",
                item_id=collection_id,
            )

        self.assertEqual(raised.exception.status_code, 400)

    def test_cycle_guard_refuses_an_indirect_cycle(self):
        # B is already inside A; adding A into B closes the loop.
        collection_a, collection_b = uuid.uuid4(), uuid.uuid4()
        with patch.object(
            next_app, "collection_ancestor_container_ids", return_value={collection_a}
        ):
            with self.assertRaises(next_app.NextApiError) as raised:
                next_app.guard_collection_item_cycle(
                    object(),
                    collection_id=collection_b,
                    item_type="collection",
                    item_id=collection_a,
                )

        self.assertEqual(raised.exception.status_code, 400)

    def test_cycle_guard_allows_an_unrelated_collection(self):
        with patch.object(next_app, "collection_ancestor_container_ids", return_value=set()):
            next_app.guard_collection_item_cycle(
                object(),
                collection_id=uuid.uuid4(),
                item_type="collection",
                item_id=uuid.uuid4(),
            )


if __name__ == "__main__":
    unittest.main()
