"""`supersedeTombstone`: a push that may bring back what the user chose to keep.

The tombstone rules (sync-contract §7) exist so that a stale device cannot undo a
deletion: a push that lands on a tombstone is answered "deleted" and the client
removes its own copy. The link wizard's "phone leads" would turn that against the
leader -- the phone's films may match records the server tombstoned long ago, and
the phone would delete its own library. So inside a wizard session, and only there,
a mutation may carry `supersedeTombstone: true` and lift the tombstone instead.

What these tests pin down:

* **Only inside a session.** The flag counts only when the same request carries a
  snapshotId that passes the validation `retain` uses, for someone who could take
  that snapshot right now. Without one -- missing, unknown, expired, somebody
  else's, a revoked role, authentication off -- every push is answered exactly as
  it always was, and the response says the flag was not honoured and why.
* **Per mutation.** A valid session does not turn the flag on for pushes that did
  not ask.
* **It lifts what it says it lifts**, on every route into a tombstone: an
  `entityId`, a token that matches (even with an `updated_at` older than the
  deletion), a barcode that matches, and for containers both the id and the token
  -- which the container guard otherwise refuses outright.
* **Nothing else changes.** Other tombstones stay deleted, and a request with no
  flag and no snapshot is byte-for-byte what it was.

Point `DATABASE_URL` at a database with every migration applied; without it these
skip.
"""

import os
import sys
import uuid
from datetime import datetime, timezone

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from .sync_wizard_support import KEY, WizardCase, needs_database

#: Older than any deletion the tests make, so the movie path's own timestamp
#: tiebreak would say "delete wins" and only the flag can change the outcome.
STALE = "2000-01-01T00:00:00Z"


def ean13(seed: str) -> str:
    """A check-digit-valid EAN-13 from twelve digits."""
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(seed))
    return seed + str((10 - total % 10) % 10)


@needs_database
class SupersedeTestCase(WizardCase):
    def setUp(self):
        super().setUp()
        self.device = f"{self.prefix}-phone"

    # -- pushing ----------------------------------------------------------

    def mid(self):
        self._mutation_counter += 1
        return f"{self.prefix}-m{self._mutation_counter}"

    def push(self, mutations, *, snapshot_id=None, mutation_ids=None, **actor_kwargs):
        stamped = []
        for index, mutation in enumerate(mutations):
            mid = mutation_ids[index] if mutation_ids else self.mid()
            stamped.append({"clientMutationId": mid, **mutation})
        body = {"clientId": self.device, "mutations": stamped}
        if snapshot_id is not None:
            body["snapshotId"] = snapshot_id
        with self.actor(**actor_kwargs):
            resp = self.client.post("/api/next/sync/mutations", json=body)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        return resp.get_json()

    def push_one(self, mutation, **kwargs):
        return self.push([mutation], **kwargs)["results"][0]

    # -- movies -----------------------------------------------------------

    def movie_upsert(self, *, token, barcode=None, entity_id=None, supersede=False, updated_at=None):
        payload = {"title": f"{self.prefix} film", "client_id": token}
        if barcode:
            payload["barcode"] = barcode
        if updated_at:
            payload["updated_at"] = updated_at
        mutation = {"entityType": "movie", "operation": "upsert", "payload": payload}
        if entity_id:
            mutation["entityId"] = str(entity_id)
        if supersede:
            mutation["supersedeTombstone"] = True
        return mutation

    def deleted_movie(self, *, token=None, barcode=None):
        """A movie that was created through sync and then deleted through sync."""
        token = token or str(uuid.uuid4())
        created = self.push_one(self.movie_upsert(token=token, barcode=barcode))
        self.assertEqual(created["status"], "applied", created)
        movie_id = str(created["entityId"])
        gone = self.push_one({"entityType": "movie", "operation": "delete", "entityId": movie_id})
        self.assertEqual(gone["status"], "applied", gone)
        self.assertIsNotNone(self.deleted_at("movies", movie_id))
        return movie_id, token

    def movie_rows_with_token(self, token):
        with self.db.cursor() as cur:
            cur.execute("SELECT id, deleted_at FROM movies WHERE client_id=%s", (token,))
            return cur.fetchall()

    def upsert_changes(self, table_type, entity_id):
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM sync_changes WHERE entity_type=%s AND entity_id=%s AND operation='upsert'",
                (table_type, str(entity_id)),
            )
            return cur.fetchone()["n"]

    def assert_deleted_signal(self, result, entity_id):
        self.assertEqual(result["status"], "applied", result)
        self.assertIs(result["deleted"], True, result)
        self.assertEqual(str(result["entityId"]), str(entity_id))
        self.assertNotIn("supersededTombstone", result)


class MovieSupersedeTests(SupersedeTestCase):
    # -- without a session: nothing changes -------------------------------

    def test_an_ordinary_push_to_a_tombstone_is_answered_as_it_always_was(self):
        movie_id, token = self.deleted_movie()
        body = self.push([self.movie_upsert(token=token, updated_at=STALE)])
        self.assert_deleted_signal(body["results"][0], movie_id)
        self.assertNotIn("snapshot", body, "a request without a snapshotId is unchanged")
        self.assertIsNotNone(self.deleted_at("movies", movie_id))

    def test_the_flag_without_a_snapshot_is_ignored(self):
        movie_id, token = self.deleted_movie()
        body = self.push([self.movie_upsert(token=token, updated_at=STALE, supersede=True)])
        self.assert_deleted_signal(body["results"][0], movie_id)
        self.assertNotIn("snapshot", body)
        self.assertIsNotNone(self.deleted_at("movies", movie_id))

    def test_the_flag_with_an_unknown_snapshot_is_ignored_and_says_why(self):
        movie_id, token = self.deleted_movie()
        sid = str(uuid.uuid4())
        body = self.push([self.movie_upsert(token=token, updated_at=STALE, supersede=True)], snapshot_id=sid)
        self.assert_deleted_signal(body["results"][0], movie_id)
        self.assertEqual(body["snapshot"], {"id": sid, "honoured": False, "errorCode": "snapshot_unknown"})
        self.assertIsNotNone(self.deleted_at("movies", movie_id))

    def test_the_flag_with_an_expired_snapshot_is_ignored(self):
        movie_id, token = self.deleted_movie()
        sid = self.take_snapshot()
        self.backdate(3600 + 60)
        body = self.push([self.movie_upsert(token=token, updated_at=STALE, supersede=True)], snapshot_id=sid)
        self.assert_deleted_signal(body["results"][0], movie_id)
        self.assertEqual(body["snapshot"]["errorCode"], "snapshot_expired")
        self.assertFalse(body["snapshot"]["honoured"])

    def test_the_flag_with_somebody_elses_snapshot_is_ignored(self):
        movie_id, token = self.deleted_movie()
        sid = self.take_snapshot()  # the owner's
        body = self.push(
            [self.movie_upsert(token=token, updated_at=STALE, supersede=True)],
            snapshot_id=sid,
            user_id=self.other_id,
            role="admin",
        )
        self.assert_deleted_signal(body["results"][0], movie_id)
        self.assertEqual(body["snapshot"]["errorCode"], "snapshot_unknown")

    def test_a_revoked_role_cannot_use_a_snapshot_it_took_earlier(self):
        movie_id, token = self.deleted_movie()
        sid = self.take_snapshot()
        body = self.push(
            [self.movie_upsert(token=token, updated_at=STALE, supersede=True)],
            snapshot_id=sid,
            role="media_editor",
            permissions=("collection.add",),
        )
        self.assert_deleted_signal(body["results"][0], movie_id)
        self.assertEqual(body["snapshot"]["errorCode"], "permission_missing")
        self.assertIsNotNone(self.deleted_at("movies", movie_id))

    def test_a_custom_role_holding_the_key_cannot_either(self):
        movie_id, token = self.deleted_movie()
        sid = self.take_snapshot()
        body = self.push(
            [self.movie_upsert(token=token, updated_at=STALE, supersede=True)],
            snapshot_id=sid,
            role="custom_curator",
            permissions=(KEY,),
        )
        self.assert_deleted_signal(body["results"][0], movie_id)
        self.assertEqual(body["snapshot"]["errorCode"], "role_not_allowed")

    def test_with_authentication_off_the_flag_never_counts(self):
        movie_id, token = self.deleted_movie()
        sid = self.take_snapshot()
        body = self.push(
            [self.movie_upsert(token=token, updated_at=STALE, supersede=True)],
            snapshot_id=sid,
            auth_enabled=False,
        )
        self.assert_deleted_signal(body["results"][0], movie_id)
        self.assertEqual(body["snapshot"]["errorCode"], "auth_not_enabled")

    def test_a_valid_session_does_not_turn_the_flag_on_for_pushes_that_did_not_ask(self):
        movie_id, token = self.deleted_movie()
        sid = self.take_snapshot()
        body = self.push([self.movie_upsert(token=token, updated_at=STALE)], snapshot_id=sid)
        self.assertEqual(body["snapshot"], {"id": sid, "honoured": True})
        self.assert_deleted_signal(body["results"][0], movie_id)
        self.assertIsNotNone(self.deleted_at("movies", movie_id))

    def test_the_flag_must_be_exactly_true(self):
        movie_id, token = self.deleted_movie()
        sid = self.take_snapshot()
        for value in ("true", 1, "yes", [True]):
            with self.subTest(flag=value):
                mutation = self.movie_upsert(token=token, updated_at=STALE)
                mutation["supersedeTombstone"] = value
                body = self.push([mutation], snapshot_id=sid)
                self.assert_deleted_signal(body["results"][0], movie_id)

    # -- inside a session: it lifts what it says -------------------------

    def test_by_entity_id(self):
        movie_id, token = self.deleted_movie()
        sid = self.take_snapshot()
        before = self.upsert_changes("movie", movie_id)
        body = self.push([self.movie_upsert(token=token, entity_id=movie_id, supersede=True)], snapshot_id=sid)
        result = body["results"][0]
        self.assertEqual(body["snapshot"], {"id": sid, "honoured": True})
        self.assertEqual(result["status"], "applied", result)
        self.assertNotIn("deleted", result)
        self.assertIs(result["supersededTombstone"], True)
        self.assertEqual(str(result["entityId"]), movie_id)
        self.assertTrue(self.is_live("movies", movie_id))
        # Other devices re-create it: an upsert is announced.
        self.assertEqual(self.upsert_changes("movie", movie_id), before + 1)

    def test_by_token_even_when_the_push_is_older_than_the_deletion(self):
        movie_id, token = self.deleted_movie()
        sid = self.take_snapshot()
        body = self.push([self.movie_upsert(token=token, supersede=True, updated_at=STALE)], snapshot_id=sid)
        result = body["results"][0]
        self.assertEqual(result["status"], "applied", result)
        self.assertIs(result["supersededTombstone"], True)
        self.assertEqual(str(result["entityId"]), movie_id, "the tombstone's id is reused, not a second row")
        self.assertTrue(self.is_live("movies", movie_id))
        self.assertEqual(len(self.movie_rows_with_token(token)), 1)

    def test_by_barcode_with_a_new_token(self):
        barcode = ean13(f"{uuid.uuid4().int % 10**12:012d}")
        movie_id, _ = self.deleted_movie(barcode=barcode)
        sid = self.take_snapshot()
        fresh = str(uuid.uuid4())
        body = self.push(
            [self.movie_upsert(token=fresh, barcode=barcode, supersede=True, updated_at=STALE)], snapshot_id=sid
        )
        result = body["results"][0]
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(str(result["entityId"]), movie_id)
        self.assertIs(result["supersededTombstone"], True)
        self.assertTrue(self.is_live("movies", movie_id))

    def test_it_lifts_only_the_tombstone_it_was_asked_about(self):
        movie_id, token = self.deleted_movie()
        other_id, _ = self.deleted_movie()
        sid = self.take_snapshot()
        self.push([self.movie_upsert(token=token, entity_id=movie_id, supersede=True)], snapshot_id=sid)
        self.assertTrue(self.is_live("movies", movie_id))
        self.assertIsNotNone(self.deleted_at("movies", other_id), "an unrelated tombstone must stay deleted")

    def test_a_live_movie_is_updated_as_usual_and_nothing_is_reported_lifted(self):
        created = self.push_one(self.movie_upsert(token=str(uuid.uuid4())))
        sid = self.take_snapshot()
        again = self.push(
            [self.movie_upsert(token=created["recordClientId"], entity_id=created["entityId"], supersede=True)],
            snapshot_id=sid,
        )["results"][0]
        self.assertEqual(again["status"], "applied")
        self.assertNotIn("supersededTombstone", again)

    def test_it_is_audited_on_the_movie(self):
        movie_id, token = self.deleted_movie()
        sid = self.take_snapshot()
        self.push([self.movie_upsert(token=token, entity_id=movie_id, supersede=True)], snapshot_id=sid)
        with self.db.cursor() as cur:
            cur.execute(
                """
                SELECT metadata FROM audit_events
                WHERE event_type='movie.updated' AND target_id=%s
                ORDER BY created_at DESC LIMIT 1
                """,
                (movie_id,),
            )
            row = cur.fetchone()
        self.assertIs(row["metadata"].get("supersededTombstone"), True)

    def test_a_replayed_mutation_id_replays_the_old_answer(self):
        # Idempotency comes first, so a retry under the same mutation id returns
        # what the first attempt got. A wizard session therefore pushes under new
        # mutation ids (sync-contract §5f), and this is why.
        movie_id, token = self.deleted_movie()
        first = self.push([self.movie_upsert(token=token, updated_at=STALE, supersede=True)], mutation_ids=["fixed-id"])
        self.assert_deleted_signal(first["results"][0], movie_id)
        sid = self.take_snapshot()
        second = self.push(
            [self.movie_upsert(token=token, updated_at=STALE, supersede=True)],
            snapshot_id=sid,
            mutation_ids=["fixed-id"],
        )
        self.assertIs(second["results"][0].get("replayed"), True)
        self.assertIs(second["results"][0]["deleted"], True)
        self.assertIsNotNone(self.deleted_at("movies", movie_id))


class ContainerSupersedeTests(SupersedeTestCase):
    def container_upsert(self, *, token, entity_id=None, supersede=False):
        mutation = {
            "entityType": "container",
            "operation": "upsert",
            "payload": {"title": f"{self.prefix} box", "containerType": "box_set", "client_id": token},
        }
        if entity_id:
            mutation["entityId"] = str(entity_id)
        if supersede:
            mutation["supersedeTombstone"] = True
        return mutation

    def deleted_container(self):
        token = str(uuid.uuid4())
        created = self.push_one(self.container_upsert(token=token))
        self.assertEqual(created["status"], "applied", created)
        container_id = str(created["entityId"])
        gone = self.push_one({"entityType": "container", "operation": "delete", "entityId": container_id})
        self.assertEqual(gone["status"], "applied", gone)
        return container_id, token

    def test_an_ordinary_push_is_still_the_deleted_signal(self):
        container_id, token = self.deleted_container()
        body = self.push([self.container_upsert(token=token)])
        self.assert_deleted_signal(body["results"][0], container_id)
        self.assertIsNotNone(self.deleted_at("containers", container_id))

    def test_the_flag_without_a_valid_snapshot_is_ignored(self):
        container_id, token = self.deleted_container()
        for sid in (None, str(uuid.uuid4())):
            with self.subTest(snapshot=sid):
                body = self.push([self.container_upsert(token=token, supersede=True)], snapshot_id=sid)
                self.assert_deleted_signal(body["results"][0], container_id)
                self.assertIsNotNone(self.deleted_at("containers", container_id))

    def test_by_entity_id(self):
        container_id, token = self.deleted_container()
        sid = self.take_snapshot()
        before = self.upsert_changes("container", container_id)
        result = self.push(
            [self.container_upsert(token=token, entity_id=container_id, supersede=True)], snapshot_id=sid
        )["results"][0]
        self.assertEqual(result["status"], "applied", result)
        self.assertNotIn("deleted", result)
        self.assertIs(result["supersededTombstone"], True)
        self.assertEqual(str(result["entityId"]), container_id)
        self.assertTrue(self.is_live("containers", container_id))
        self.assertEqual(self.upsert_changes("container", container_id), before + 1)
        # The announcement carries the container as it is now, not a deleted one.
        self.assertEqual(result["entity"]["id"], container_id)

    def test_by_token_which_the_container_guard_otherwise_refuses(self):
        container_id, token = self.deleted_container()
        sid = self.take_snapshot()
        result = self.push([self.container_upsert(token=token, supersede=True)], snapshot_id=sid)["results"][0]
        self.assertEqual(result["status"], "applied", result)
        self.assertIs(result["supersededTombstone"], True)
        self.assertEqual(str(result["entityId"]), container_id, "the tombstone's id is reused")
        self.assertIs(result["created"], False)
        self.assertEqual(result["matchedBy"], "clientId")
        self.assertTrue(self.is_live("containers", container_id))
        with self.db.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM containers WHERE client_id=%s", (token,))
            self.assertEqual(cur.fetchone()["n"], 1, "no unique violation, no second row")

    def test_it_lifts_only_the_container_it_was_asked_about(self):
        container_id, token = self.deleted_container()
        other_id, _ = self.deleted_container()
        sid = self.take_snapshot()
        self.push([self.container_upsert(token=token, supersede=True)], snapshot_id=sid)
        self.assertTrue(self.is_live("containers", container_id))
        self.assertIsNotNone(self.deleted_at("containers", other_id))

    def test_a_batch_may_mix_flagged_and_unflagged_pushes(self):
        container_id, token = self.deleted_container()
        other_id, other_token = self.deleted_container()
        sid = self.take_snapshot()
        body = self.push(
            [
                self.container_upsert(token=token, supersede=True),
                self.container_upsert(token=other_token),
            ],
            snapshot_id=sid,
        )
        flagged, plain = body["results"]
        self.assertIs(flagged["supersededTombstone"], True)
        self.assert_deleted_signal(plain, other_id)
        self.assertTrue(self.is_live("containers", container_id))
        self.assertIsNotNone(self.deleted_at("containers", other_id))
