import importlib
import os
import sys
import tempfile
import unittest
import uuid


class SyncIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = cls.tmp.name
        os.environ.update({
            "DB_PATH": os.path.join(root, "discvault.db"),
            "POSTER_DIR": os.path.join(root, "posters"),
            "PROFILE_DIR": os.path.join(root, "profiles"),
            "BACKUP_DIR": os.path.join(root, "backups"),
            "AVATAR_DIR": os.path.join(root, "avatars"),
        })
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
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

    def test_bootstrap_delta_and_movie_tombstone(self):
        before = self._json(self.client.get("/api/sync/bootstrap"))["server_revision"]
        movie = self._create_movie()

        delta = self._json(self.client.get(f"/api/sync/delta?since_revision={before}"))
        self.assertGreater(delta["server_revision"], before)
        self.assertIn(movie["id"], {m["id"] for m in delta["movies"]})

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

        current = self._json(self.client.get(f"/api/movies/{movie_id}"))
        conflict = self._json(self.client.post("/api/sync/operations", json={
            "client_id": "ios-test",
            "operations": [{
                "idempotency_key": f"op-{uuid.uuid4().hex}",
                "type": "movie.update",
                "entity": "movie",
                "entity_id": movie_id,
                "base_revision": max(0, int(current["sync_revision"]) - 1),
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


if __name__ == "__main__":
    unittest.main()
