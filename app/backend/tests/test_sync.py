import importlib
import json
import os
import sys
import tempfile
import time
import unittest
import uuid


class SyncIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        except TypeError:
            cls.tmp = tempfile.TemporaryDirectory()
        root = cls.tmp.name
        os.environ.update({
            "DB_PATH": os.path.join(root, "discvault.db"),
            "POSTER_DIR": os.path.join(root, "posters"),
            "PROFILE_DIR": os.path.join(root, "profiles"),
            "BACKUP_DIR": os.path.join(root, "backups"),
            "AVATAR_DIR": os.path.join(root, "avatars"),
            "BUILD_VERSION": "3.5-test",
            "BUILD_SHA": "abcdef1234567890",
        })
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        for name in (
            "app.backend.app",
            "app.backend.config",
            "app.backend.db",
            "app.backend.logging_utils",
            "app.backend.movievault_client",
            "app.backend.push.routes",
            "app.backend.settings.routes",
            "app.backend.versioning",
        ):
            sys.modules.pop(name, None)
        try:
            cls.backend = importlib.import_module("app.backend.app")
        except ModuleNotFoundError as exc:
            raise unittest.SkipTest(f"Backend dependency missing: {exc.name}") from exc
        cls.client = cls.backend.create_app().test_client()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "tmp"):
            cls.tmp.cleanup()

    def _json(self, response):
        self.assertLess(response.status_code, 500, response.get_data(as_text=True))
        return response.get_json()

    def _create_movie(self, title=None):
        title = title or f"Sync Test {uuid.uuid4().hex[:8]}"
        barcode = f"SYNC-{uuid.uuid4().hex[:12].upper()}"
        response = self.client.post("/api/movies", json={
            "title": title,
            "barcode": barcode,
            "format": "4K UHD",
        })
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()["movie"]

    def test_movie_source_image_urls_are_stored_for_movievault(self):
        original_download = self.backend.download_poster
        calls = []

        def fake_download(url, asset_kind="poster"):
            calls.append((url, asset_kind))
            return f"local-{asset_kind}.jpg"

        poster_url = "https://images.example.test/poster.jpg"
        backdrop_url = "https://images.example.test/backdrop-main.jpg"
        backdrop_urls = [
            backdrop_url,
            "https://images.example.test/backdrop-alt.jpg",
        ]
        try:
            self.backend.download_poster = fake_download
            response = self.client.post("/api/movies", json={
                "title": f"Source Images {uuid.uuid4().hex[:8]}",
                "barcode": f"SRCIMG-{uuid.uuid4().hex[:10].upper()}",
                "poster_url": poster_url,
                "backdrop": backdrop_url,
                "backdrops": json.dumps(backdrop_urls),
                "format": "4K UHD",
            })
        finally:
            self.backend.download_poster = original_download

        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        movie = response.get_json()["movie"]
        self.assertEqual(movie["poster"], poster_url)
        self.assertEqual(movie["poster_url"], poster_url)
        self.assertEqual(movie["backdrop"], "/api/images/local-backdrop.jpg")
        self.assertEqual(movie["backdrop_url"], backdrop_url)
        self.assertEqual(json.loads(movie["backdrop_urls"]), backdrop_urls)
        self.assertIn((poster_url, "poster"), calls)
        self.assertIn((backdrop_url, "backdrop"), calls)

        payload = self.backend._movievault_raw_payload(
            self.backend._movievault_public_source(movie),
            "movie",
        )
        self.assertEqual(payload["posterUrl"], poster_url)
        self.assertEqual(payload["backdropUrl"], backdrop_url)
        self.assertEqual(payload["backdropUrls"], json.loads(movie["backdrop_urls"]))
        self.assertEqual(
            self.backend._movievault_normalize_template_value(
                payload["backdropUrls"],
                {"type": "array"},
            ),
            backdrop_urls,
        )

    def test_movievault_movie_payload_excludes_release_fields(self):
        source = {
            "title": "Bohemian Rhapsody 4K Blu-ray",
            "barcode": "8712626045139",
            "format": "4K UHD",
            "edition": "4K Ultra HD + Blu-ray",
            "packaging": "Keep Case",
            "country": "Netherlands",
            "language": "nl",
            "hdr": "Dolby Vision",
            "audio_tracks": "English Dolby Atmos",
            "subtitles": "Dutch, English SDH",
            "regions": "B",
            "screen_ratios": "2.39:1",
            "distributor": "20th Century Fox",
            "poster_url": "https://release.example.test/poster.jpg",
            "_release_poster_url": "https://release.example.test/poster.jpg",
            "_movie_poster_url": "https://tmdb.example.test/poster.jpg",
            "backdrop_urls": json.dumps([
                "https://release.example.test/backdrop.jpg",
                "https://discvault.example.test/api/images/local-backdrop.jpg",
            ]),
            "_release_backdrop_urls": json.dumps(["https://release.example.test/backdrop.jpg"]),
            "_movie_backdrop_urls": json.dumps(["https://tmdb.example.test/backdrop.jpg"]),
            "tmdb_id": "424694",
            "imdb_id": "tt1727824",
            "runtime": "134",
            "plot": "Public overview",
        }

        movie_payload = self.backend._movievault_raw_payload(source, "movie")
        release_payload = self.backend._movievault_raw_payload(source, "release")

        self.assertNotIn("barcode", movie_payload)
        self.assertNotIn("format", movie_payload)
        self.assertNotIn("edition", movie_payload)
        self.assertNotIn("packaging", movie_payload)
        self.assertNotIn("country", movie_payload)
        self.assertNotIn("language", movie_payload)
        self.assertNotIn("hdr", movie_payload)
        self.assertNotIn("audioTracks", movie_payload)
        self.assertNotIn("subtitles", movie_payload)
        self.assertNotIn("regions", movie_payload)
        self.assertNotIn("screenRatios", movie_payload)
        self.assertEqual(movie_payload["posterUrl"], "https://tmdb.example.test/poster.jpg")
        self.assertEqual(movie_payload["backdropUrls"], ["https://tmdb.example.test/backdrop.jpg"])

        self.assertEqual(release_payload["barcode"], "8712626045139")
        self.assertEqual(release_payload["format"], "4K UHD")
        self.assertEqual(release_payload["packaging"], "Keep Case")
        self.assertEqual(release_payload["posterUrl"], "https://release.example.test/poster.jpg")
        self.assertEqual(release_payload["backdropUrls"], ["https://release.example.test/backdrop.jpg"])

    def test_movievault_box_set_member_source_reference_is_stable(self):
        source = {
            "title": "Mission: Impossible",
            "year": "1996",
            "tmdb_id": "954",
            "barcode": "032429316110-BOX-01",
            "box_set": "Mission: Impossible Collection",
        }
        first, first_stats = self.backend._movievault_contribution_payload(
            "movie",
            source,
            "Unit test",
            {"localEntityType": "movie", "localEntityId": 101},
        )
        second, second_stats = self.backend._movievault_contribution_payload(
            "movie",
            source,
            "Unit test",
            {"localEntityType": "movie", "localEntityId": 202},
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first["idempotencyKey"], second["idempotencyKey"])
        self.assertEqual(first_stats["source_reference"]["key"], "032429316110-BOX-01")
        self.assertEqual(first_stats["source_reference"]["memberSortOrder"], 1)
        self.assertEqual(first["sourceReference"], first_stats["source_reference"])
        self.assertNotIn("sourceReference", first["payload"])
        self.assertNotIn("barcode", first["payload"])
        self.assertEqual(first["sourceReference"]["parentBarcode"], "032429316110")

    def test_movievault_box_set_member_release_uses_source_reference_not_barcode(self):
        source = {
            "title": "Mission: Impossible 4K Blu-ray",
            "barcode": "032429316110-BOX-01",
            "format": "4K UHD",
            "edition": "Box-set member",
            "hdr": "Dolby Vision",
            "audio_tracks": "English Dolby Atmos",
        }
        first, first_stats = self.backend._movievault_contribution_payload(
            "release",
            source,
            "Unit test",
            {"localEntityType": "movie", "localEntityId": 101},
        )
        second, second_stats = self.backend._movievault_contribution_payload(
            "release",
            source,
            "Unit test",
            {"localEntityType": "movie", "localEntityId": 202},
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first["idempotencyKey"], second["idempotencyKey"])
        self.assertEqual(first_stats["source_reference"]["key"], "032429316110-BOX-01")
        self.assertEqual(first_stats["source_reference"]["memberSortOrder"], 1)
        self.assertEqual(first_stats["missing_required"], [])
        self.assertEqual(first["sourceReference"], first_stats["source_reference"])
        self.assertEqual(first["payload"]["title"], "Mission: Impossible 4K Blu-ray")
        self.assertEqual(first["payload"]["format"], "4K UHD")
        self.assertEqual(first["payload"]["hdr"], "Dolby Vision")
        self.assertNotIn("barcode", first["payload"])
        self.assertNotIn("sourceReference", first["payload"])
        self.assertEqual(first["sourceReference"]["parentBarcode"], "032429316110")

    def test_movievault_skips_unchanged_person_payload(self):
        original_enabled = self.backend._is_movievault_contribution_enabled
        original_url = self.backend._movievault_contribution_url
        original_request = self.backend.movievault_request
        calls = []

        class FakeResponse:
            status_code = 202
            text = '{"status":"accepted","contributionId":"mv_contrib_person_1"}'

            def json(self):
                return {"status": "accepted", "contributionId": "mv_contrib_person_1"}

        def fake_request(method, url, **kwargs):
            calls.append({"method": method, "url": url, "json": kwargs.get("json")})
            return FakeResponse()

        person = {
            "name": "Rami Malek",
            "tmdbId": 11856,
            "biography": "Public biography",
            "knownFor": "Acting",
        }
        context = {"localEntityType": "person", "localEntityId": "11856"}

        try:
            self.backend._is_movievault_contribution_enabled = lambda: True
            self.backend._movievault_contribution_url = lambda: "https://movies.example.test/api/v1/contributions"
            self.backend.movievault_request = fake_request
            first = self.backend._submit_movievault_entity_contribution_result(
                "person",
                person,
                "Unit test",
                context,
            )
            second = self.backend._submit_movievault_entity_contribution_result(
                "person",
                person,
                "Unit test",
                context,
            )
        finally:
            self.backend._is_movievault_contribution_enabled = original_enabled
            self.backend._movievault_contribution_url = original_url
            self.backend.movievault_request = original_request

        self.assertEqual(first["status"], "submitted")
        self.assertEqual(second["status"], "skipped")
        self.assertEqual(second["action"], "unchanged_payload")
        self.assertEqual(len([call for call in calls if call["method"] == "POST"]), 1)
        submitted = next(call["json"] for call in calls if call["method"] == "POST")
        self.assertEqual(submitted["sourceVersion"], "3.5-test")
        self.assertNotIn("sourceVersion", submitted["payload"])

    def test_movievault_resubmits_after_prior_unchanged_skip(self):
        original_enabled = self.backend._is_movievault_contribution_enabled
        original_url = self.backend._movievault_contribution_url
        original_request = self.backend.movievault_request
        calls = []

        class FakeResponse:
            status_code = 202
            text = '{"status":"accepted","contributionId":"mv_contrib_resubmit"}'

            def json(self):
                return {"status": "accepted", "contributionId": f"mv_contrib_resubmit_{len(calls)}"}

        def fake_request(method, url, **kwargs):
            calls.append({"method": method, "url": url, "json": kwargs.get("json")})
            return FakeResponse()

        person = {
            "name": f"Repeat Person {uuid.uuid4().hex[:8]}",
            "tmdbId": int(uuid.uuid4().int % 900000) + 100000,
            "knownFor": "Acting",
        }
        context = {"localEntityType": "person", "localEntityId": str(person["tmdbId"])}

        try:
            self.backend._is_movievault_contribution_enabled = lambda: True
            self.backend._movievault_contribution_url = lambda: "https://movies.example.test/api/v1/contributions"
            self.backend.movievault_request = fake_request
            first = self.backend._submit_movievault_entity_contribution_result("person", person, "Unit test", context)
            second = self.backend._submit_movievault_entity_contribution_result("person", person, "Unit test", context)
            third = self.backend._submit_movievault_entity_contribution_result("person", person, "Unit test", context)
        finally:
            self.backend._is_movievault_contribution_enabled = original_enabled
            self.backend._movievault_contribution_url = original_url
            self.backend.movievault_request = original_request

        self.assertEqual(first["status"], "submitted")
        self.assertEqual(second["action"], "unchanged_payload")
        self.assertEqual(third["status"], "submitted")
        self.assertEqual(len([call for call in calls if call["method"] == "POST"]), 2)

    def test_movievault_resubmits_when_successful_state_is_unproven(self):
        original_enabled = self.backend._is_movievault_contribution_enabled
        original_url = self.backend._movievault_contribution_url
        original_request = self.backend.movievault_request
        calls = []

        class FakeResponse:
            status_code = 202
            text = "{}"

            def json(self):
                return {}

        def fake_request(method, url, **kwargs):
            calls.append({"method": method, "url": url, "json": kwargs.get("json")})
            return FakeResponse()

        person = {
            "name": f"Pending Person {uuid.uuid4().hex[:8]}",
            "tmdbId": int(uuid.uuid4().int % 900000) + 100000,
            "knownFor": "Directing",
        }
        context = {"localEntityType": "person", "localEntityId": str(person["tmdbId"])}

        try:
            self.backend._is_movievault_contribution_enabled = lambda: True
            self.backend._movievault_contribution_url = lambda: "https://movies.example.test/api/v1/contributions"
            self.backend.movievault_request = fake_request
            first = self.backend._submit_movievault_entity_contribution_result("person", person, "Unit test", context)
            second = self.backend._submit_movievault_entity_contribution_result("person", person, "Unit test", context)
        finally:
            self.backend._is_movievault_contribution_enabled = original_enabled
            self.backend._movievault_contribution_url = original_url
            self.backend.movievault_request = original_request

        self.assertEqual(first["status"], "submitted")
        self.assertEqual(second["status"], "submitted")
        self.assertEqual(len([call for call in calls if call["method"] == "POST"]), 2)

    def test_movievault_force_contribution_posts_again_with_new_idempotency_key(self):
        original_enabled = self.backend._is_movievault_contribution_enabled
        original_url = self.backend._movievault_contribution_url
        original_request = self.backend.movievault_request
        calls = []

        class FakeResponse:
            status_code = 202
            text = '{"status":"accepted","contributionId":"mv_contrib_force"}'

            def json(self):
                return {"status": "accepted", "contributionId": f"mv_contrib_force_{len(calls)}"}

        def fake_request(method, url, **kwargs):
            calls.append({"method": method, "url": url, "json": kwargs.get("json")})
            return FakeResponse()

        person = {
            "name": f"Force Person {uuid.uuid4().hex[:8]}",
            "tmdbId": int(uuid.uuid4().int % 900000) + 100000,
            "knownFor": "Writing",
        }
        context = {"localEntityType": "person", "localEntityId": str(person["tmdbId"])}
        force_context = {**context, "forceMovieVaultContribution": True}

        try:
            self.backend._is_movievault_contribution_enabled = lambda: True
            self.backend._movievault_contribution_url = lambda: "https://movies.example.test/api/v1/contributions"
            self.backend.movievault_request = fake_request
            first = self.backend._submit_movievault_entity_contribution_result("person", person, "Unit test", context)
            second = self.backend._submit_movievault_entity_contribution_result("person", person, "Unit test", force_context)
        finally:
            self.backend._is_movievault_contribution_enabled = original_enabled
            self.backend._movievault_contribution_url = original_url
            self.backend.movievault_request = original_request

        self.assertEqual(first["status"], "submitted")
        self.assertEqual(second["status"], "submitted")
        self.assertEqual(len([call for call in calls if call["method"] == "POST"]), 2)
        keys = [call["json"]["idempotencyKey"] for call in calls if call["method"] == "POST"]
        self.assertNotEqual(keys[0], keys[1])
        self.assertIn(":attempt:", keys[1])

    def test_movievault_template_version_check_refreshes_fresh_cache_when_due(self):
        original_request = self.backend.movievault_request
        now = time.time()
        template_v1 = {
            "version": "v1",
            "templates": {
                "movie": {
                    "entityType": "movie",
                    "required": ["title"],
                    "fields": {"title": {"type": "string"}},
                }
            },
        }
        template_v2 = {
            "version": "v2",
            "templates": {
                "movie": {
                    "entityType": "movie",
                    "required": ["title"],
                    "fields": {
                        "title": {"type": "string"},
                        "posterUrl": {"type": "string"},
                    },
                }
            },
        }

        class FakeResponse:
            def __init__(self, data):
                self.status_code = 200
                self.text = json.dumps(data)
                self._data = data

            def json(self):
                return self._data

        calls = []

        def fake_request(method, url, include_auth=False, timeout=None, **kwargs):
            calls.append((method, url, include_auth, timeout))
            return FakeResponse(template_v2)

        try:
            with self.backend.connect_db() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (self.backend.MOVIEVAULT_TEMPLATE_CACHE_KEY, json.dumps(template_v1)),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (self.backend.MOVIEVAULT_TEMPLATE_VERSION_KEY, "v1"),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (self.backend.MOVIEVAULT_TEMPLATE_FETCHED_AT_KEY, str(now)),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (
                        self.backend.MOVIEVAULT_TEMPLATE_VERSION_CHECKED_AT_KEY,
                        str(now - self.backend.MOVIEVAULT_TEMPLATE_VERSION_CHECK_INTERVAL_SECONDS - 1),
                    ),
                )
                conn.commit()

            self.backend.movievault_request = fake_request
            template, version, source = self.backend._movievault_contribution_template("movie")

            self.assertEqual(version, "v2")
            self.assertEqual(source, "live-check")
            self.assertIn("posterUrl", template["fields"])
            self.assertEqual(len(calls), 1)

            calls.clear()
            template, version, source = self.backend._movievault_contribution_template("movie")

            self.assertEqual(version, "v2")
            self.assertEqual(source, "cache")
            self.assertIn("posterUrl", template["fields"])
            self.assertEqual(calls, [])
        finally:
            self.backend.movievault_request = original_request

    def _create_box_set(self, title=None):
        title = title or f"Box Set {uuid.uuid4().hex[:8]}"
        response = self.client.post("/api/edition-groups", json={
            "title": title,
            "group_type": "boxset",
            "barcode": f"BOX-{uuid.uuid4().hex[:12].upper()}",
        })
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()

    def _table_count(self, table, row_id):
        conn = self.backend.get_db()
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE id=?", (row_id,)).fetchone()[0]
        finally:
            conn.close()

    def _box_set_member_count(self, box_set_id):
        conn = self.backend.get_db()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM box_set_movies WHERE box_set_id=?",
                (box_set_id,),
            ).fetchone()[0]
        finally:
            conn.close()

    def _collection_item_count(self, item_type, item_id):
        conn = self.backend.get_db()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM collection_items WHERE item_type=? AND item_id=?",
                (item_type, item_id),
            ).fetchone()[0]
        finally:
            conn.close()

    def test_bootstrap_delta_and_movie_tombstone(self):
        bootstrap = self._json(self.client.get("/api/sync/bootstrap"))
        before = bootstrap["server_revision"]
        self.assertEqual(bootstrap["serverRevision"], before)
        self.assertIn("assetManifest", bootstrap)
        self.assertIn("containers", bootstrap)
        movie = self._create_movie()

        delta = self._json(self.client.get(f"/api/sync/delta?since_revision={before}"))
        self.assertGreater(delta["server_revision"], before)
        self.assertIn(movie["id"], {m["id"] for m in delta["movies"]})

        changes = self._json(self.client.get(f"/api/sync/changes?sinceRevision={before}"))
        self.assertEqual(changes["server_revision"], delta["server_revision"])
        self.assertEqual(changes["toRevision"], delta["server_revision"])
        self.assertIn("upserts", changes)
        self.assertIn("deletes", changes)
        self.assertIn("assetUpdates", changes)
        self.assertIn(movie["id"], {m["id"] for m in changes["movies"]})

        update = self.client.put(f"/api/movies/{movie['id']}", json={"title": movie["title"] + " Updated"})
        self.assertEqual(update.status_code, 200, update.get_data(as_text=True))
        after_update = self._json(self.client.get(f"/api/sync/delta?since_revision={delta['server_revision']}"))
        self.assertIn(movie["id"], {m["id"] for m in after_update["movies"]})

        delete = self.client.delete(f"/api/movies/{movie['id']}")
        self.assertEqual(delete.status_code, 200, delete.get_data(as_text=True))
        after_delete = self._json(self.client.get(f"/api/sync/delta?since_revision={after_update['server_revision']}"))
        self.assertIn(
            ("movie", str(movie["id"])),
            {(t["entity"], t["entity_id"]) for t in after_delete["tombstones"]},
        )

    def test_deleting_last_box_set_movie_removes_empty_container(self):
        box_set = self._create_box_set()
        movie = self._create_movie("Single Member Box Set")
        assign = self.client.put(f"/api/movies/{movie['id']}/containers", json={"box_set_ids": [box_set["id"]]})
        self.assertEqual(assign.status_code, 200, assign.get_data(as_text=True))

        deleted = self.client.delete(f"/api/movies/{movie['id']}")

        self.assertEqual(deleted.status_code, 200, deleted.get_data(as_text=True))
        self.assertEqual(deleted.get_json()["deleted_box_sets"], [box_set["id"]])
        self.assertEqual(self._table_count("box_sets", box_set["id"]), 0)
        self.assertEqual(self._table_count("edition_groups", box_set["id"]), 0)
        self.assertEqual(self._box_set_member_count(box_set["id"]), 0)
        self.assertEqual(self._collection_item_count("box_set", box_set["id"]), 0)

    def test_bulk_delete_removes_empty_box_set_after_last_member(self):
        box_set = self._create_box_set()
        first = self._create_movie("Bulk Box Set One")
        second = self._create_movie("Bulk Box Set Two")
        for movie in (first, second):
            assign = self.client.put(f"/api/movies/{movie['id']}/containers", json={"box_set_ids": [box_set["id"]]})
            self.assertEqual(assign.status_code, 200, assign.get_data(as_text=True))
        self.assertEqual(self._box_set_member_count(box_set["id"]), 2)

        first_delete = self.client.post("/api/movies/bulk-delete", json={"ids": [first["id"]]})
        self.assertEqual(first_delete.status_code, 200, first_delete.get_data(as_text=True))
        self.assertEqual(first_delete.get_json()["deleted_box_sets"], [])
        self.assertEqual(self._table_count("box_sets", box_set["id"]), 1)

        second_delete = self.client.post("/api/movies/bulk-delete", json={"ids": [second["id"]]})
        self.assertEqual(second_delete.status_code, 200, second_delete.get_data(as_text=True))
        self.assertEqual(second_delete.get_json()["deleted_box_sets"], [box_set["id"]])
        self.assertEqual(self._table_count("box_sets", box_set["id"]), 0)
        self.assertEqual(self._table_count("edition_groups", box_set["id"]), 0)
        self.assertEqual(self._box_set_member_count(box_set["id"]), 0)

    def test_operations_are_idempotent_and_detect_conflicts(self):
        key = f"op-{uuid.uuid4().hex}"
        payload = {
            "client_id": "ios-test",
            "operations": [{
                "idempotency_key": key,
                "type": "movie.create",
                "entity": "movie",
                "payload": {
                    "title": f"Created by Sync {uuid.uuid4().hex[:8]}",
                    "barcode": f"SYNCOP-{uuid.uuid4().hex[:10].upper()}",
                    "format": "4K UHD",
                },
            }],
        }
        first = self._json(self.client.post("/api/sync/operations", json=payload))
        self.assertEqual(first["results"][0]["status"], "applied")
        movie_id = first["results"][0]["entity_id"]

        duplicate = self._json(self.client.post("/api/sync/operations", json=payload))
        self.assertEqual(duplicate["results"][0]["status"], "duplicate")

        mutation_duplicate = self._json(self.client.post("/api/sync/mutations", json={
            "clientId": payload["client_id"],
            "mutations": [{
                "idempotencyKey": key,
                "type": "movie.create",
                "entity": "movie",
                "payload": payload["operations"][0]["payload"],
            }],
        }))
        self.assertEqual(mutation_duplicate["results"][0]["status"], "duplicate")

        current = self._json(self.client.get(f"/api/movies/{movie_id}"))
        conflict = self._json(self.client.post("/api/sync/operations", json={
            "client_id": "ios-test",
            "operations": [{
                "idempotency_key": f"op-{uuid.uuid4().hex}",
                "type": "movie.update",
                "entity": "movie",
                "entityId": movie_id,
                "baseRevision": max(0, int(current["sync_revision"]) - 1),
                "payload": {"title": "Should Conflict"},
            }],
        }))
        self.assertEqual(conflict["results"][0]["status"], "conflict")

    def test_normalized_container_memberships_sync(self):
        movie = self._create_movie()
        vault = self._json(self.client.post("/api/edition-groups", json={
            "title": f"Vault {uuid.uuid4().hex[:8]}",
            "group_type": "vault",
        }))
        box_set = self._json(self.client.post("/api/edition-groups", json={
            "title": f"Box Set {uuid.uuid4().hex[:8]}",
            "group_type": "boxset",
        }))
        collection = self._json(self.client.post("/api/collections", json={
            "title": f"Collection {uuid.uuid4().hex[:8]}",
        }))

        assign = self.client.put(f"/api/movies/{movie['id']}/containers", json={
            "vault_ids": [vault["id"]],
            "box_set_ids": [box_set["id"]],
            "collection_ids": [collection["id"]],
        })
        self.assertEqual(assign.status_code, 200, assign.get_data(as_text=True))

        bootstrap = self._json(self.client.get("/api/sync/bootstrap"))
        memberships = bootstrap["container_memberships"]
        self.assertIn(
            (vault["id"], movie["id"]),
            {(r["vault_id"], r["movie_id"]) for r in memberships["vault_movies"]},
        )
        self.assertIn(
            (box_set["id"], movie["id"]),
            {(r["box_set_id"], r["movie_id"]) for r in memberships["box_set_movies"]},
        )
        self.assertIn(
            (collection["id"], "movie", movie["id"]),
            {(r["collection_id"], r["item_type"], r["item_id"]) for r in memberships["collection_items"]},
        )

    def test_bootstrap_normalizes_legacy_image_routes_for_offline_manifest(self):
        from PIL import Image

        filename = f"legacy-{uuid.uuid4().hex}.jpg"
        image_path = os.path.join(os.environ["POSTER_DIR"], filename)
        Image.new("RGB", (1800, 1000), color=(20, 40, 80)).save(image_path, "JPEG")

        conn = self.backend.get_db()
        conn.execute(
            "INSERT INTO movies (barcode, title, format, backdrop, added_at) VALUES (?, ?, ?, ?, ?)",
            (
                f"SYNCIMG-{uuid.uuid4().hex[:10].upper()}",
                f"Legacy Image {uuid.uuid4().hex[:8]}",
                "4K UHD",
                f"/api/posters/{filename}",
                "2026-05-23T00:00:00",
            ),
        )
        movie_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()

        bootstrap = self._json(self.client.get("/api/sync/bootstrap"))
        conn = self.backend.get_db()
        row = conn.execute("SELECT backdrop FROM movies WHERE id=?", (movie_id,)).fetchone()
        conn.close()
        self.assertEqual(row["backdrop"], f"/api/images/{filename}")

        asset = next(
            a for a in bootstrap["assetManifest"]
            if a["entity"] == "movie" and a["entityId"] == movie_id and a["kind"] == "backdrop"
        )
        self.assertEqual(asset["url"], f"/api/images/{filename}")
        self.assertIn("/api/images/offline/backdrop/", asset["offlineUrl"])
        self.assertGreater(asset["width"], 0)
        self.assertGreater(asset["height"], 0)

    def test_person_filmography_includes_ios_movie_identifiers_and_posters(self):
        from PIL import Image

        filename = f"filmography-{uuid.uuid4().hex}.jpg"
        image_path = os.path.join(os.environ["POSTER_DIR"], filename)
        Image.new("RGB", (600, 900), color=(80, 30, 20)).save(image_path, "JPEG")

        tmdb_movie_id = 987654
        conn = self.backend.get_db()
        conn.execute(
            "INSERT INTO people (tmdb_id, name) VALUES (?, ?)",
            ("12345", "Filmography Person"),
        )
        person_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO movies
               (barcode, title, format, tmdb_id, collection_id, poster_file, added_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                f"SYNCFILM-{uuid.uuid4().hex[:10].upper()}",
                "Collected Filmography Movie",
                "4K UHD",
                str(tmdb_movie_id),
                321,
                filename,
                "2026-05-23T00:00:00",
            ),
        )
        movie_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO digital_library_sources (name, type, base_url, last_synced, machine_id) VALUES (?, ?, ?, ?, ?)",
            ("Plex Test", "plex", "http://plex.local", "2026-05-23T00:00:00", "plex-machine"),
        )
        source_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO digital_library_items
               (source_id, external_id, title, year, tmdb_id, media_type, synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (source_id, "plex-1", "Collected Filmography Movie", "2026", str(tmdb_movie_id), "movie", "2026-05-23T00:00:00"),
        )
        digital_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()

        original_key = self.backend.TMDB_API_KEY
        original_get = self.backend.requests.get

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "cast": [{
                        "id": tmdb_movie_id,
                        "media_type": "movie",
                        "title": "Collected Filmography Movie",
                        "release_date": "2026-05-23",
                        "poster_path": "/tmdb-poster.jpg",
                        "vote_average": 8.25,
                        "character": "Lead",
                    }],
                    "crew": [],
                }

        try:
            self.backend.TMDB_API_KEY = "test-key"
            self.backend.requests.get = lambda *args, **kwargs: FakeResponse()
            payload = self._json(self.client.get(f"/api/people/{person_id}/filmography"))
        finally:
            self.backend.TMDB_API_KEY = original_key
            self.backend.requests.get = original_get

        item = payload["cast"][0]
        self.assertEqual(item["tmdbId"], tmdb_movie_id)
        self.assertEqual(item["movieId"], movie_id)
        self.assertEqual(item["collectionId"], 321)
        self.assertEqual(item["digitalId"], digital_id)
        self.assertEqual(item["digitalSource"], "plex")
        self.assertEqual(item["digitalWebUrl"], "https://app.plex.tv/desktop/#!/server/plex-machine/details?key=%2Flibrary%2Fmetadata%2Fplex-1")
        self.assertEqual(item["digitalAppUrl"], item["digitalWebUrl"])
        self.assertEqual(item["digitalNativeUrl"], "plex://play/?metadataKey=%2Flibrary%2Fmetadata%2Fplex-1&server=plex-machine")
        self.assertEqual(item["digital"]["webUrl"], item["digitalWebUrl"])
        self.assertEqual(item["digital"]["nativeUrl"], item["digitalNativeUrl"])
        self.assertEqual(item["movie"]["digitalWebUrl"], item["digitalWebUrl"])
        self.assertEqual(item["movie"]["digitalNativeUrl"], item["digitalNativeUrl"])
        self.assertTrue(item["posterUrl"].startswith("http://localhost/api/images/"))
        self.assertEqual(item["poster_path"], "/tmdb-poster.jpg")
        self.assertEqual(item["movie"]["tmdbId"], tmdb_movie_id)
        self.assertEqual(item["movie"]["movieId"], movie_id)
        self.assertEqual(item["movie"]["posterUrl"], item["posterUrl"])

    def test_movie_endpoints_include_digital_availability_and_detail_links(self):
        tmdb_movie_id = 456789
        conn = self.backend.get_db()
        conn.execute(
            """INSERT INTO movies (barcode, title, original_title, year, format, tmdb_id, added_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                f"SYNCDIG-{uuid.uuid4().hex[:10].upper()}",
                "Digital Linked Movie",
                "Digital Linked Movie",
                "2026",
                "4K UHD",
                str(tmdb_movie_id),
                "2026-05-23T00:00:00",
            ),
        )
        movie_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO digital_library_sources (name, type, base_url, last_synced, machine_id) VALUES (?, ?, ?, ?, ?)",
            ("Plex Test", "plex", "http://plex.local", "2026-05-23T00:00:00", "plex-machine"),
        )
        source_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO digital_library_items
               (source_id, external_id, title, year, tmdb_id, media_type, synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (source_id, "plex-2", "Digital Linked Movie", "2026", str(tmdb_movie_id), "movie", "2026-05-23T00:00:00"),
        )
        digital_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()

        detail = self._json(self.client.get(f"/api/movies/{movie_id}"))
        self.assertTrue(detail["inDigital"])
        self.assertEqual(detail["digitalIds"], [digital_id])
        self.assertEqual(detail["digitalSources"], ["plex"])
        self.assertEqual(detail["digitalMatches"][0]["webUrl"], "https://app.plex.tv/desktop/#!/server/plex-machine/details?key=%2Flibrary%2Fmetadata%2Fplex-2")
        self.assertEqual(detail["digitalMatches"][0]["appUrl"], detail["digitalMatches"][0]["webUrl"])
        self.assertEqual(detail["digitalMatches"][0]["nativeUrl"], "plex://play/?metadataKey=%2Flibrary%2Fmetadata%2Fplex-2&server=plex-machine")

        movies = self._json(self.client.get("/api/movies"))
        listed = next(m for m in movies if m["id"] == movie_id)
        self.assertTrue(listed["inDigital"])
        self.assertEqual(listed["digitalIds"], [digital_id])
        self.assertEqual(listed["digitalMatchesCount"], 1)
        self.assertNotIn("digitalMatches", listed)

        bootstrap = self._json(self.client.get("/api/sync/bootstrap"))
        synced = next(m for m in bootstrap["movies"] if m["id"] == movie_id)
        self.assertTrue(synced["inDigital"])
        self.assertEqual(synced["digitalSources"], ["plex"])

    def test_sync_includes_movie_cast_and_alternative_backdrop_assets(self):
        from PIL import Image

        primary_backdrop = f"primary-{uuid.uuid4().hex}.jpg"
        alt_legacy = f"alt-legacy-{uuid.uuid4().hex}.jpg"
        alt_image = f"alt-image-{uuid.uuid4().hex}.jpg"
        profile_file = f"profile-{uuid.uuid4().hex}.jpg"
        for filename, size in (
            (primary_backdrop, (1800, 1000)),
            (alt_legacy, (1600, 900)),
            (alt_image, (1500, 900)),
            (profile_file, (400, 600)),
        ):
            directory = os.environ["PROFILE_DIR"] if filename == profile_file else os.environ["POSTER_DIR"]
            Image.new("RGB", size, color=(30, 70, 110)).save(os.path.join(directory, filename), "JPEG")

        conn = self.backend.get_db()
        conn.execute(
            """INSERT INTO movies (barcode, title, format, backdrop, backdrops, added_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                f"SYNCCAST-{uuid.uuid4().hex[:10].upper()}",
                "Offline Cast Movie",
                "4K UHD",
                f"/api/images/{primary_backdrop}",
                f"[\"/api/posters/{alt_legacy}\", \"/api/images/{alt_image}\"]",
                "2026-05-23T00:00:00",
            ),
        )
        movie_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO people (tmdb_id, name, photo_file) VALUES (?, ?, ?)",
            ("2888", "Will Smith", profile_file),
        )
        actor_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO people (tmdb_id, name) VALUES (?, ?)",
            ("1234", "David Ayer"),
        )
        crew_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO movie_people (movie_id, person_id, role, character, sort_order) VALUES (?, ?, ?, ?, ?)",
            (movie_id, actor_id, "actor", "Deadshot", 1),
        )
        conn.execute(
            "INSERT INTO movie_people (movie_id, person_id, role, job, sort_order) VALUES (?, ?, ?, ?, ?)",
            (movie_id, crew_id, "crew", "Director", 2),
        )
        conn.commit()
        before = self.backend._current_sync_revision(conn)
        conn.close()

        bootstrap = self._json(self.client.get("/api/sync/bootstrap"))
        movie_cast = next(item for item in bootstrap["movieCast"] if item["movieId"] == movie_id)
        self.assertEqual(movie_cast["movieID"], movie_id)
        self.assertEqual({member["name"] for member in movie_cast["cast"]}, {"Will Smith", "David Ayer"})
        actor = next(member for member in movie_cast["cast"] if member["name"] == "Will Smith")
        self.assertEqual(actor["personId"], actor_id)
        self.assertEqual(actor["role"], "actor")
        self.assertEqual(actor["character"], "Deadshot")
        self.assertEqual(actor["photoFile"], profile_file)
        self.assertTrue(actor["photoUrl"].endswith(f"/api/images/profiles/{profile_file}"))
        crew = next(member for member in movie_cast["cast"] if member["name"] == "David Ayer")
        self.assertEqual(crew["role"], "crew")
        self.assertEqual(crew["job"], "Director")

        conn = self.backend.get_db()
        movie_row = conn.execute("SELECT backdrops FROM movies WHERE id=?", (movie_id,)).fetchone()
        conn.close()
        self.assertIn("/api/images/", movie_row["backdrops"])
        self.assertNotIn("/api/posters/", movie_row["backdrops"])

        movie_backdrops = [
            asset for asset in bootstrap["assetManifest"]
            if asset["entity"] == "movie" and asset["entityId"] == movie_id and asset["kind"] == "backdrop"
        ]
        self.assertGreaterEqual(len(movie_backdrops), 3)
        self.assertTrue(all("/api/images/offline/backdrop/" in asset["offlineUrl"] for asset in movie_backdrops))
        profile_asset = next(
            asset for asset in bootstrap["assetManifest"]
            if asset["entity"] == "person" and asset["entityId"] == actor_id and asset["kind"] == "profile"
        )
        self.assertIn("/api/images/profiles/offline/profile/", profile_asset["offlineUrl"])

        delta = self._json(self.client.get(f"/api/sync/delta?since_revision={before - 1}"))
        self.assertIn(movie_id, {item["movieId"] for item in delta["movieCast"]})
        self.assertIn("movieCast", delta["upserts"])

        since_existing_profile = bootstrap["serverRevision"]
        conn = self.backend.get_db()
        conn.execute(
            "INSERT INTO movies (barcode, title, format, added_at) VALUES (?, ?, ?, ?)",
            (
                f"SYNCCAST2-{uuid.uuid4().hex[:10].upper()}",
                "Second Offline Cast Movie",
                "4K UHD",
                "2026-05-23T00:00:00",
            ),
        )
        second_movie_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO movie_people (movie_id, person_id, role, character, sort_order) VALUES (?, ?, ?, ?, ?)",
            (second_movie_id, actor_id, "actor", "Cameo", 1),
        )
        conn.commit()
        conn.close()

        delta_existing_profile = self._json(self.client.get(f"/api/sync/delta?since_revision={since_existing_profile}"))
        self.assertIn(second_movie_id, {item["movieId"] for item in delta_existing_profile["movieCast"]})
        self.assertTrue(any(
            asset["entity"] == "person" and asset["entityId"] == actor_id and asset["kind"] == "profile"
            for asset in delta_existing_profile["assetManifest"]
        ))

    def test_movie_search_matches_normalized_cast_and_crew(self):
        conn = self.backend.get_db()
        conn.execute(
            "INSERT INTO movies (barcode, title, format, added_at) VALUES (?, ?, ?, ?)",
            (
                f"SYNCSEARCH-{uuid.uuid4().hex[:10].upper()}",
                "Searchable Cast Movie",
                "4K UHD",
                "2026-05-23T00:00:00",
            ),
        )
        movie_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        actor_tmdb_id = str(800000000 + int(uuid.uuid4().hex[:6], 16))
        crew_tmdb_id = str(810000000 + int(uuid.uuid4().hex[:6], 16))
        conn.execute("INSERT INTO people (tmdb_id, name) VALUES (?, ?)", (actor_tmdb_id, "Will Smith"))
        actor_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO people (tmdb_id, name) VALUES (?, ?)", (crew_tmdb_id, "David Ayer"))
        crew_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO movie_people (movie_id, person_id, role, character, sort_order) VALUES (?, ?, ?, ?, ?)",
            (movie_id, actor_id, "actor", "Deadshot", 1),
        )
        conn.execute(
            "INSERT INTO movie_people (movie_id, person_id, role, job, sort_order) VALUES (?, ?, ?, ?, ?)",
            (movie_id, crew_id, "crew", "Director", 2),
        )
        conn.commit()
        conn.close()

        by_actor = self._json(self.client.get("/api/movies?q=Will%20Smith"))
        self.assertIn(movie_id, {movie["id"] for movie in by_actor})
        matched = next(movie for movie in by_actor if movie["id"] == movie_id)
        self.assertIn("Will Smith", matched["searchPeople"])
        self.assertIn("Deadshot", matched["searchPeople"])

        by_job = self._json(self.client.get("/api/movies?q=Director"))
        self.assertIn(movie_id, {movie["id"] for movie in by_job})

    def test_barcode_lookup_reports_existing_movie_status(self):
        movie = self._create_movie("Existing Barcode Movie")

        lookup = self._json(self.client.get(f"/api/lookup/{movie['barcode']}"))
        self.assertEqual(lookup["status"], "movie_exists")
        self.assertEqual(lookup["barcode"], movie["barcode"])
        self.assertEqual(lookup["movie"]["id"], movie["id"])

        stream_response = self.client.get(f"/api/lookup/{movie['barcode']}?stream=1")
        self.assertEqual(stream_response.status_code, 200, stream_response.get_data(as_text=True))
        done = None
        for line in stream_response.get_data(as_text=True).splitlines():
            if not line.strip():
                continue
            item = self.backend.json.loads(line)
            if item.get("type") == "done":
                done = item
        self.assertIsNotNone(done)
        self.assertEqual(done["status"], "movie_exists")
        self.assertEqual(done["barcode"], movie["barcode"])
        self.assertEqual(done["movie"]["id"], movie["id"])

    def test_bluray_quicksearch_uses_all_country_for_barcode_lookup(self):
        original_post = self.backend.requests.post

        class FakeResponse:
            status_code = 200
            text = "var urls = new Array('https://www.blu-ray.com/movies/Test-Movie/12345/');"

        captured = {}

        def fake_post(url, data=None, headers=None, timeout=None):
            captured["url"] = url
            captured["data"] = dict(data or {})
            return FakeResponse()

        try:
            self.backend.requests.post = fake_post
            url = self.backend._bluray_find_first_movie_url("7340112740717")
        finally:
            self.backend.requests.post = original_post

        self.assertEqual(url, "https://www.blu-ray.com/movies/Test-Movie/12345/")
        self.assertEqual(captured["url"], "https://www.blu-ray.com/search/quicksearch.php")
        self.assertEqual(captured["data"]["country"], "all")

    def test_bluray_barcode_lookup_prefers_dvd_section_for_ean(self):
        original_post = self.backend.requests.post
        calls = []

        class FakeResponse:
            status_code = 200

            def __init__(self, text):
                self.text = text

        def fake_post(url, data=None, headers=None, timeout=None):
            section = (data or {}).get("section")
            calls.append(section)
            if section == "dvdmovies":
                return FakeResponse("var urls = new Array('https://www.blu-ray.com/dvd/Back-to-the-Future-DVD/219226/');")
            return FakeResponse("var urls = new Array('https://www.blu-ray.com/movies/Back-to-the-Future-4K-Blu-ray/395515/');")

        try:
            self.backend.requests.post = fake_post
            url = self.backend._bluray_find_first_movie_url("5050582369601")
        finally:
            self.backend.requests.post = original_post

        self.assertEqual(url, "https://www.blu-ray.com/dvd/Back-to-the-Future-DVD/219226/")
        self.assertEqual(calls[0], "dvdmovies")

    def test_bluray_specs_lookup_uses_barcode_before_title(self):
        original_find = self.backend._bluray_find_first_movie_url
        original_parse = self.backend._bluray_parse_movie_page
        queries = []

        def fake_find(query):
            queries.append(query)
            if query == "5050582369601":
                return "https://www.blu-ray.com/dvd/Back-to-the-Future-DVD/219226/"
            return "https://www.blu-ray.com/movies/Back-to-the-Future-4K-Blu-ray/395515/"

        def fake_parse(url):
            return {
                "title": "Back to the Future DVD",
                "poster": "https://images.example.test/back-to-the-future-dvd.jpg",
                "poster_url": "https://images.example.test/back-to-the-future-dvd.jpg",
                "format": "DVD",
                "audio_tracks": "English: Dolby Digital 5.1",
            }

        try:
            self.backend._bluray_find_first_movie_url = fake_find
            self.backend._bluray_parse_movie_page = fake_parse
            specs, attempts = self.backend.lookup_movie_bluray_specs_traced(
                "Back to the Future",
                "1985",
                "5050582369601",
            )
        finally:
            self.backend._bluray_find_first_movie_url = original_find
            self.backend._bluray_parse_movie_page = original_parse

        self.assertEqual(queries, ["5050582369601"])
        self.assertEqual(attempts[0]["query"], "barcode=5050582369601")
        self.assertEqual(specs["format"], "DVD")
        self.assertEqual(specs["poster"], "https://images.example.test/back-to-the-future-dvd.jpg")

    def test_bluray_barcode_release_overwrites_tmdb_poster(self):
        originals = {
            "order": self.backend._metadata_source_order,
            "enabled": self.backend._metadata_source_enabled,
            "tmdb": self.backend.lookup_movie_tmdb,
            "bluray_barcode": self.backend.lookup_movie_bluray_by_barcode,
            "bluray_specs": self.backend.lookup_movie_bluray_specs_traced,
        }

        def fake_tmdb(title, year=""):
            return {
                "title": "Back to the Future",
                "year": "1985",
                "poster": "https://images.example.test/back-to-the-future-tmdb.jpg",
                "poster_url": "https://images.example.test/back-to-the-future-tmdb.jpg",
            }

        def fake_bluray_barcode(barcode):
            return {
                "title": "Back to the Future DVD",
                "poster": "https://images.example.test/back-to-the-future-dvd.jpg",
                "poster_url": "https://images.example.test/back-to-the-future-dvd.jpg",
                "format": "DVD",
            }

        try:
            self.backend._metadata_source_order = lambda: ["tmdb", "bluray_com"]
            self.backend._metadata_source_enabled = lambda source: True
            self.backend.lookup_movie_tmdb = fake_tmdb
            self.backend.lookup_movie_bluray_by_barcode = fake_bluray_barcode
            self.backend.lookup_movie_bluray_specs_traced = lambda *args, **kwargs: ({}, [])

            attempts = []
            info, source = self.backend._merge_metadata_by_order(
                "Back to the Future",
                "1985",
                barcode="5050582369601",
                attempts=attempts,
                contribute_to_movievault=False,
            )
        finally:
            self.backend._metadata_source_order = originals["order"]
            self.backend._metadata_source_enabled = originals["enabled"]
            self.backend.lookup_movie_tmdb = originals["tmdb"]
            self.backend.lookup_movie_bluray_by_barcode = originals["bluray_barcode"]
            self.backend.lookup_movie_bluray_specs_traced = originals["bluray_specs"]

        self.assertEqual(info["poster"], "https://images.example.test/back-to-the-future-dvd.jpg")
        self.assertEqual(info["poster_url"], "https://images.example.test/back-to-the-future-dvd.jpg")
        self.assertEqual(info["format"], "DVD")
        self.assertIn("TMDb", source)
        self.assertIn("Blu-ray.com", source)

    def test_bluray_box_set_without_members_uses_metadata_candidate_fallback(self):
        original_candidates = self.backend._metadata_candidates_by_order
        html = """
        <html><head>
          <meta property="og:title" content="Back to the Future: The Ultimate Trilogy DVD" />
          <meta property="og:image" content="https://images.example.test/bttf-dvd-box.jpg" />
        </head><body>
          <h1>Back to the Future: The Ultimate Trilogy DVD</h1>
          <div>4-disc collector's set</div>
          <div>Video: 4 DVD-9</div>
          <div>Four-disc set</div>
        </body></html>
        """

        def fake_candidates(title, year=""):
            self.assertEqual(title, "Back to the Future")
            return [
                {"provider": "tmdb", "tmdb_id": "105", "title": "Back to the Future", "year": "1985", "poster": "p1"},
                {"provider": "tmdb", "tmdb_id": "165", "title": "Back to the Future Part II", "year": "1989", "poster": "p2"},
                {"provider": "tmdb", "tmdb_id": "999", "title": "Back to the Future: Making the Trilogy", "year": "2002", "poster": "bonus"},
                {"provider": "tmdb", "tmdb_id": "196", "title": "Back to the Future Part III", "year": "1990", "poster": "p3"},
            ]

        class FakeResponse:
            status_code = 200
            text = html

        original_get = self.backend.requests.get

        try:
            self.backend._metadata_candidates_by_order = fake_candidates
            self.backend.requests.get = lambda *args, **kwargs: FakeResponse()
            parsed = self.backend._bluray_parse_movie_page(
                "https://www.blu-ray.com/dvd/Back-to-the-Future-The-Ultimate-Trilogy-DVD/219226/"
            )
        finally:
            self.backend._metadata_candidates_by_order = original_candidates
            self.backend.requests.get = original_get

        proposal = parsed.get("box_set_proposal")
        self.assertIsNotNone(proposal)
        self.assertTrue(proposal["detected_without_members"])
        self.assertEqual(proposal["member_confidence"], "candidate")
        self.assertEqual(len(proposal["movies"]), 3)
        self.assertEqual(proposal["movies"][1]["title"], "Back to the Future Part II")
        self.assertEqual(proposal["movies"][2]["title"], "Back to the Future Part III")

    def test_synthetic_box_set_member_barcode_is_not_used_for_external_lookup(self):
        originals = {
            "order": self.backend._metadata_source_order,
            "enabled": self.backend._metadata_source_enabled,
            "movievault_barcode": self.backend.lookup_movievault_by_barcode,
            "movievault_movie": self.backend.lookup_movievault_movie,
            "bluray_barcode": self.backend.lookup_movie_bluray_by_barcode,
            "bluray_specs": self.backend.lookup_movie_bluray_specs_traced,
        }
        calls = []

        def fake_movievault_barcode(barcode):
            calls.append(("movievault_barcode", barcode))
            return {"title": "Wrong release", "backdrop": "https://wrong.example/backdrop.jpg"}

        def fake_movievault_movie(title="", year="", barcode=""):
            calls.append(("movievault_movie", title, year, barcode))
            return None

        def fake_bluray_barcode(barcode):
            calls.append(("bluray_barcode", barcode))
            return {"title": "Dragonball - Box 01 Blu-ray", "backdrop": "https://wrong.example/dragonball.jpg"}

        def fake_bluray_specs(title="", year="", barcode=""):
            calls.append(("bluray_specs", title, year, barcode))
            return ({"hdr": "HDR10"}, [{"result": "hit", "query": f"title={title}, year={year}", "extra": ""}])

        try:
            self.backend._metadata_source_order = lambda: ["movievault", "bluray_com"]
            self.backend._metadata_source_enabled = lambda source: True
            self.backend.lookup_movievault_by_barcode = fake_movievault_barcode
            self.backend.lookup_movievault_movie = fake_movievault_movie
            self.backend.lookup_movie_bluray_by_barcode = fake_bluray_barcode
            self.backend.lookup_movie_bluray_specs_traced = fake_bluray_specs

            attempts = []
            info, source = self.backend._merge_metadata_by_order(
                "Mission: Impossible",
                "1996",
                barcode="032429316110-BOX-01",
                attempts=attempts,
                contribute_to_movievault=False,
            )
        finally:
            self.backend._metadata_source_order = originals["order"]
            self.backend._metadata_source_enabled = originals["enabled"]
            self.backend.lookup_movievault_by_barcode = originals["movievault_barcode"]
            self.backend.lookup_movievault_movie = originals["movievault_movie"]
            self.backend.lookup_movie_bluray_by_barcode = originals["bluray_barcode"]
            self.backend.lookup_movie_bluray_specs_traced = originals["bluray_specs"]

        self.assertEqual(info["hdr"], "HDR10")
        self.assertNotIn("backdrop", info)
        self.assertNotIn(("movievault_barcode", "032429316110-BOX-01"), calls)
        self.assertNotIn(("bluray_barcode", "032429316110-BOX-01"), calls)
        self.assertIn(("bluray_specs", "Mission: Impossible", "1996", ""), calls)
        self.assertTrue(any("local/synthetic identifier" in attempt for attempt in attempts))

    def test_bluray_candidate_search_uses_all_country(self):
        original_post = self.backend.requests.post
        original_enabled = self.backend._is_bluray_scrape_enabled

        class FakeResponse:
            status_code = 200
            text = "var urls = new Array('https://www.blu-ray.com/movies/Test-Title/67890/');"

        captured = {}

        def fake_post(url, data=None, headers=None, timeout=None):
            captured["url"] = url
            captured["data"] = dict(data or {})
            return FakeResponse()

        try:
            self.backend.requests.post = fake_post
            self.backend._is_bluray_scrape_enabled = lambda: True
            candidates = self.backend.search_movie_bluray_candidates("Test Title", "2026")
        finally:
            self.backend.requests.post = original_post
            self.backend._is_bluray_scrape_enabled = original_enabled

        self.assertEqual(captured["url"], "https://www.blu-ray.com/search/quicksearch.php")
        self.assertEqual(captured["data"]["country"], "all")
        self.assertTrue(candidates)
        self.assertEqual(candidates[0]["id"], "67890")

    def test_barcode_lookup_stream_returns_json_error_on_exception(self):
        original_lookup = self.backend._lookup_metadata_for_barcode

        def failing_lookup(*args, **kwargs):
            raise RuntimeError("lookup boom")

        try:
            self.backend._lookup_metadata_for_barcode = failing_lookup
            response = self.client.get("/api/lookup/STREAM-ERROR?stream=1")
            body = response.get_data(as_text=True)
        finally:
            self.backend._lookup_metadata_for_barcode = original_lookup

        self.assertEqual(response.status_code, 200, body)
        self.assertNotIn("<!doctype", body.lower())
        done = None
        for line in body.splitlines():
            if not line.strip():
                continue
            item = self.backend.json.loads(line)
            if item.get("type") == "done":
                done = item
        self.assertIsNotNone(done)
        self.assertEqual(done["status"], "error")
        self.assertIn("lookup boom", done["error"])

    def test_movie_search_matches_barcode(self):
        movie = self._create_movie("Search By Barcode Movie")

        result = self._json(self.client.get(f"/api/movies?q={movie['barcode']}"))
        self.assertIn(movie["id"], {item["id"] for item in result})

    def test_movie_update_allows_upc_ean_field(self):
        movie = self._create_movie("Editable Barcode Movie")
        other = self._create_movie("Duplicate Barcode Guard")
        new_barcode = f"EAN-{uuid.uuid4().hex[:10].upper()}"

        updated = self.client.put(
            f"/api/movies/{movie['id']}",
            json={"title": movie["title"], "barcode": new_barcode},
        )
        self.assertEqual(updated.status_code, 200, updated.get_data(as_text=True))
        self.assertEqual(updated.get_json()["barcode"], new_barcode)

        duplicate = self.client.put(
            f"/api/movies/{movie['id']}",
            json={"title": movie["title"], "barcode": other["barcode"]},
        )
        self.assertEqual(duplicate.status_code, 409, duplicate.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
