"""A container upsert that reaches a tombstone must say "deleted", not fail or write.

Movies have answered this since the H4 guard: an upsert whose `entityId` points
at a tombstone, or whose token belongs to one, gets the deleted signal back
(sync-contract §7) and the row is left alone. Containers had no such guard, and
the two routes into a tombstoned container behaved differently from each other
and from movies:

* **Same token, new request.** The ladder only looks at live rows, so it found
  nothing and the write minted a fresh id -- and `uq_containers_client_id` does
  not look at `deleted_at`, so the INSERT hit a unique violation. The mutation
  came back as `status: "error"` with the database's message, which a client
  cannot tell from a transient failure and retries forever.
* **`entityId` (or a `clientEntityId` mapping) pointing at the tombstone.** The
  upsert's `ON CONFLICT (id) DO UPDATE` rewrote the deleted row and then emitted a
  full `upsert` change for it, with nothing in it saying the container is
  deleted. The row stayed deleted, so it vanished again on the next delta, but
  every other device was told in the meantime that it had been changed.

Neither can be asserted against a fake cursor: the question is what the row looks
like afterwards and what the stream carries.

The timestamp-resurrection rule movies have is deliberately **not** applied to
containers here; this only gives them the non-resurrection branch.

Point `DATABASE_URL` at a database with every migration applied; without it these
skip.
"""

import os
import sys
import unittest
import uuid


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:  # pragma: no cover - minimal environments
    psycopg = None
    dict_row = None

try:
    from app.backend import next_app
except ModuleNotFoundError as exc:  # pragma: no cover - minimal environments
    if exc.name not in {"flask", "psycopg", "cbor2", "argon2", "jwt", "segno", "PIL"}:
        raise
    next_app = None

DATABASE_URL = os.environ.get("DATABASE_URL")

PREFIX = "container-tombstone-test"
#: A real, check-digit-valid EAN-13.
EAN = "5027035025124"


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and next_app is not None,
    "PostgreSQL test database is not configured",
)
class ContainerTombstoneGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = next_app.create_app()

    def setUp(self):
        self.client = self.app.test_client()
        self.run_id = uuid.uuid4().hex[:10]
        self.device = f"{PREFIX}-{self.run_id}-a"
        self.other_device = f"{PREFIX}-{self.run_id}-b"
        self._mutation_counter = 0
        self.addCleanup(self._cleanup)

    # -- plumbing ---------------------------------------------------------

    def _connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)

    def _cleanup(self):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM containers WHERE title LIKE %s", (f"{PREFIX}%",))
            cur.execute(
                "DELETE FROM client_id_mappings WHERE client_id LIKE %s", (f"{PREFIX}%",)
            )
            cur.execute("DELETE FROM idempotency_records WHERE key LIKE %s", (f"%{PREFIX}%",))

    def _next_mutation_id(self):
        self._mutation_counter += 1
        return f"{PREFIX}-{self.run_id}-m{self._mutation_counter}"

    def _send(self, mutation, *, device=None):
        mutation = {"clientMutationId": self._next_mutation_id(), **mutation}
        response = self.client.post(
            "/api/next/sync/mutations",
            json={"clientId": device or self.device, "mutations": [mutation]},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        results = response.get_json()["results"]
        self.assertEqual(len(results), 1)
        return results[0]

    def _upsert(self, *, token=None, entity_id=None, client_entity_id=None, title=None,
                barcode=None, device=None):
        payload = {
            "title": title or f"{PREFIX} {self.run_id}",
            "containerType": "box_set",
        }
        if token is not None:
            payload["client_id"] = token
        if barcode is not None:
            payload["barcode"] = barcode
        mutation = {"entityType": "container", "operation": "upsert", "payload": payload}
        if entity_id is not None:
            mutation["entityId"] = str(entity_id)
        if client_entity_id is not None:
            mutation["clientEntityId"] = client_entity_id
        return self._send(mutation, device=device)

    def _delete(self, entity_id, *, device=None):
        return self._send(
            {"entityType": "container", "operation": "delete", "entityId": str(entity_id)},
            device=device,
        )

    def _make_tombstone(self, *, token, client_entity_id=None, barcode=None):
        created = self._upsert(
            token=token, client_entity_id=client_entity_id, barcode=barcode
        )
        self.assertEqual(created["status"], "applied", created)
        entity_id = created["entityId"]
        deleted = self._delete(entity_id)
        self.assertEqual(deleted["status"], "applied", deleted)
        return entity_id

    def _row(self, entity_id):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT title, deleted_at, updated_at, client_id FROM containers WHERE id=%s",
                (entity_id,),
            )
            return cur.fetchone()

    def _upsert_changes(self, entity_id):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) AS n FROM sync_changes
                WHERE entity_type='container' AND entity_id=%s AND operation='upsert'
                """,
                (str(entity_id),),
            )
            return cur.fetchone()["n"]

    def _assert_deleted_signal(self, result, entity_id):
        self.assertEqual(result["status"], "applied", result)
        self.assertIs(result["deleted"], True, result)
        self.assertIs(result["tombstoned"], True, result)
        self.assertTrue(result["deletedAt"], result)
        self.assertEqual(str(result["entityId"]), str(entity_id))
        self.assertIs(result["created"], False)

    # -- the guard --------------------------------------------------------

    def test_a_create_with_a_tombstoned_token_gets_the_deleted_signal(self):
        # The defect: this came back as status "error" (unique violation).
        token = str(uuid.uuid4())
        tombstone_id = self._make_tombstone(token=token)
        before = self._row(tombstone_id)

        result = self._upsert(
            token=token, client_entity_id="tmp-other-device", device=self.other_device
        )

        self._assert_deleted_signal(result, tombstone_id)
        self.assertEqual(result["recordClientId"], token)
        after = self._row(tombstone_id)
        self.assertEqual(after["deleted_at"], before["deleted_at"])
        self.assertEqual(after["updated_at"], before["updated_at"])

    def test_the_token_is_not_given_a_second_live_row(self):
        token = str(uuid.uuid4())
        self._make_tombstone(token=token)

        self._upsert(token=token, client_entity_id="tmp-x", device=self.other_device)

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT deleted_at FROM containers WHERE client_id=%s", (token,))
            rows = cur.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0]["deleted_at"])

    def test_an_update_by_entity_id_on_a_tombstone_gets_the_deleted_signal(self):
        # The defect: the deleted row was rewritten and a full upsert change
        # emitted for it.
        token = str(uuid.uuid4())
        tombstone_id = self._make_tombstone(token=token)
        before = self._row(tombstone_id)
        changes_before = self._upsert_changes(tombstone_id)

        result = self._upsert(
            token=token, entity_id=tombstone_id, title=f"{PREFIX} renamed {self.run_id}"
        )

        self._assert_deleted_signal(result, tombstone_id)
        after = self._row(tombstone_id)
        self.assertEqual(after["title"], before["title"])
        self.assertEqual(after["updated_at"], before["updated_at"])
        self.assertEqual(
            self._upsert_changes(tombstone_id),
            changes_before,
            "the stream must not announce a change to a deleted container",
        )

    def test_a_client_entity_mapping_onto_a_tombstone_gets_the_deleted_signal(self):
        # A device that created the container under a temporary id and kept
        # pushing by that id after it was deleted elsewhere.
        token = str(uuid.uuid4())
        tombstone_id = self._make_tombstone(token=token, client_entity_id="tmp-mapped")

        result = self._upsert(token=token, client_entity_id="tmp-mapped")

        self._assert_deleted_signal(result, tombstone_id)

    def test_a_replay_of_the_deleted_signal_is_answered_from_the_record(self):
        token = str(uuid.uuid4())
        tombstone_id = self._make_tombstone(token=token)
        mutation = {
            "clientMutationId": f"{PREFIX}-{self.run_id}-replay",
            "entityType": "container",
            "operation": "upsert",
            "payload": {"title": f"{PREFIX} {self.run_id}", "containerType": "box_set", "client_id": token},
        }
        body = {"clientId": self.other_device, "mutations": [mutation]}

        first = self.client.post("/api/next/sync/mutations", json=body).get_json()["results"][0]
        second = self.client.post("/api/next/sync/mutations", json=body).get_json()["results"][0]

        self._assert_deleted_signal(first, tombstone_id)
        self._assert_deleted_signal(second, tombstone_id)

    # -- what must not change --------------------------------------------

    def test_a_live_container_is_still_created_matched_and_updated(self):
        token = str(uuid.uuid4())

        created = self._upsert(token=token, client_entity_id="tmp-live")
        self.assertEqual(created["status"], "applied", created)
        self.assertIs(created["created"], True)
        self.assertNotIn("deleted", created)

        matched = self._upsert(token=token, client_entity_id="tmp-live-b", device=self.other_device)
        self.assertEqual(matched["status"], "applied", matched)
        self.assertIs(matched["created"], False)
        self.assertEqual(matched["matchedBy"], "clientId")
        self.assertEqual(matched["entityId"], created["entityId"])
        self.assertNotIn("deleted", matched)

        updated = self._upsert(
            token=token,
            entity_id=created["entityId"],
            title=f"{PREFIX} edited {self.run_id}",
        )
        self.assertEqual(updated["status"], "applied", updated)
        self.assertNotIn("deleted", updated)
        self.assertEqual(
            self._row(created["entityId"])["title"], f"{PREFIX} edited {self.run_id}"
        )

    def test_a_new_token_on_a_tombstoned_barcode_is_still_a_new_live_row(self):
        # Contract §7: a new token that only matches a tombstone by barcode is a
        # user adding it again -- a new live row, the tombstone left standing.
        # The container ladder never looked at tombstones and still does not.
        tombstone_id = self._make_tombstone(token=str(uuid.uuid4()), barcode=EAN)

        result = self._upsert(
            token=str(uuid.uuid4()),
            client_entity_id="tmp-readd",
            barcode=EAN,
            title=f"{PREFIX} re-added {self.run_id}",
            device=self.other_device,
        )

        self.assertEqual(result["status"], "applied", result)
        self.assertIs(result["created"], True)
        self.assertNotIn("deleted", result)
        self.assertNotEqual(str(result["entityId"]), str(tombstone_id))
        self.assertIsNone(self._row(result["entityId"])["deleted_at"])
        self.assertIsNotNone(self._row(tombstone_id)["deleted_at"])


if __name__ == "__main__":
    unittest.main()
