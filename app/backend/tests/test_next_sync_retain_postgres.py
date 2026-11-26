"""`POST /sync/retain`: the wizard's one destructive step (sync-contract §5f).

The app has already pushed every record it holds. Retain names the ids it ended up
with, and the server tombstones every other live film and container in ONE
transaction, with a `delete` change per row so other devices follow through the
delta. What these tests pin down:

* **It cannot run without its safeguards.** No valid snapshot of the same user, no
  permission, no authentication, both lists empty without saying so, a malformed or
  oversized body: each refuses with its own `errorCode` and changes nothing.
* **It removes exactly the right rows**, through the same helpers `movie.delete`
  and `container.delete` use, one revision per change, visible in the delta a
  second device reads.
* **It touches nothing else.** Locations, series and every user's personal lists
  are rows it never names; a count of each, before and after, proves it.
* **It is atomic and idempotent.** A failure midway leaves nothing tombstoned;
  the same call again tombstones nothing.

A retain tombstones everything live outside its lists and the database is shared,
so every test keeps all other live rows (`keep_all_but`) and removes only its own.

Point `DATABASE_URL` at a database with every migration applied; without it these
skip.
"""

import os
import sys
import uuid
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend import next_app as na
    from app.backend import next_sync_snapshot as snap
except ModuleNotFoundError as exc:  # pragma: no cover - minimal environments
    if exc.name not in {"flask", "psycopg", "cbor2", "argon2", "jwt", "segno", "PIL"}:
        raise
    na = None
    snap = None

from .sync_wizard_support import KEY, NATIVE_LIST, WizardCase, needs_database

#: Tables retain must never write to.
UNTOUCHED = ("locations", "series", "wishlist_items", "tags", "watchlist_items")


@needs_database
class RetainRefusalTests(WizardCase):
    """Every refusal changes nothing, and says which refusal it is."""

    def setUp(self):
        super().setUp()
        self.victim_movie = self.make_movie("victim")
        self.victim_box = self.make_container("victim")
        self.body = self.keep_all_but(movies=[self.victim_movie], containers=[self.victim_box])
        self.revision_before = self.current_revision()
        self.changes_before = self.change_count()

    def refused(self, resp, status, code):
        self.assertEqual(resp.status_code, status, resp.get_data(as_text=True))
        self.assertEqual(resp.get_json()["errorCode"], code)
        # Nothing was tombstoned, no change was emitted, the revision did not move.
        self.assertTrue(self.is_live("movies", self.victim_movie))
        self.assertTrue(self.is_live("containers", self.victim_box))
        self.assertEqual(self.change_count(), self.changes_before)
        self.assertEqual(self.current_revision(), self.revision_before)

    # -- the snapshot -----------------------------------------------------

    def test_without_a_snapshot_id(self):
        self.refused(self.post_retain({"retain": self.body}), 400, "snapshot_required")

    def test_with_a_malformed_snapshot_id(self):
        self.refused(self.post_retain({"snapshotId": "nope", "retain": self.body}), 400, "snapshot_required")

    def test_with_an_unknown_snapshot_id(self):
        resp = self.post_retain({"snapshotId": str(uuid.uuid4()), "retain": self.body})
        self.refused(resp, 404, "snapshot_unknown")

    def test_with_another_users_snapshot(self):
        sid = self.take_snapshot()
        resp = self.post_retain(
            {"snapshotId": sid, "retain": self.body}, user_id=self.other_id, role="admin", permissions=(KEY,)
        )
        # Answers exactly like an unknown id, so ids cannot be probed.
        self.refused(resp, 404, "snapshot_unknown")

    def test_with_an_expired_snapshot(self):
        sid = self.take_snapshot()
        self.backdate(snap.SNAPSHOT_VALID_FOR_SECONDS + 60)
        self.refused(self.post_retain({"snapshotId": sid, "retain": self.body}), 410, "snapshot_expired")

    def test_with_a_snapshot_whose_archive_is_gone(self):
        sid = self.take_snapshot()
        for path in self.backup_dir.glob(f"{snap.SNAPSHOT_FILE_PREFIX}*.zip"):
            path.unlink()
        self.refused(self.post_retain({"snapshotId": sid, "retain": self.body}), 409, "snapshot_file_missing")

    def test_with_the_id_of_a_snapshot_that_failed(self):
        # A failed snapshot is never recorded, so there is no id to present. The
        # only "id" a client could hold is one it invented.
        with patch.object(snap, "validate_backup_zip", return_value={"valid": False, "errors": ["x"]}):
            self.assertEqual(self.post_snapshot().status_code, 500)
        resp = self.post_retain({"snapshotId": str(uuid.uuid4()), "retain": self.body})
        self.refused(resp, 404, "snapshot_unknown")

    # -- who ---------------------------------------------------------------

    def test_an_ordinary_users_native_token(self):
        sid = self.take_snapshot()
        resp = self.post_retain(
            {"snapshotId": sid, "retain": self.body},
            user_id=self.other_id,
            role="media_editor",
            permissions=("collection.view",),
            token_keys=NATIVE_LIST,
        )
        self.refused(resp, 403, "permission_missing")

    def test_a_custom_role_holding_the_key(self):
        sid = self.take_snapshot()
        resp = self.post_retain(
            {"snapshotId": sid, "retain": self.body}, role="custom_curator", permissions=(KEY,)
        )
        self.refused(resp, 403, "role_not_allowed")

    def test_with_authentication_switched_off(self):
        sid = self.take_snapshot()
        resp = self.post_retain({"snapshotId": sid, "retain": self.body}, auth_enabled=False)
        self.refused(resp, 403, "auth_not_enabled")

    # -- the body ----------------------------------------------------------

    def test_both_lists_empty_without_allow_empty(self):
        sid = self.take_snapshot()
        for body in ({"movies": [], "containers": []}, {}, {"movies": None}):
            with self.subTest(retain=body):
                self.refused(self.post_retain({"snapshotId": sid, "retain": body}), 400, "retain_empty")

    def test_allow_empty_must_be_exactly_true(self):
        sid = self.take_snapshot()
        for value in ("true", 1, "yes"):
            with self.subTest(allowEmpty=value):
                resp = self.post_retain(
                    {"snapshotId": sid, "retain": {"movies": [], "containers": []}, "allowEmpty": value}
                )
                self.refused(resp, 400, "retain_empty")

    def test_a_body_that_is_not_the_shape(self):
        sid = self.take_snapshot()
        for payload in (
            {"snapshotId": sid},
            {"snapshotId": sid, "retain": []},
            {"snapshotId": sid, "retain": "all"},
            {"snapshotId": sid, "retain": {"movies": "x"}},
            {"snapshotId": sid, "retain": {"containers": {"a": 1}}},
        ):
            with self.subTest(payload=payload):
                self.refused(self.post_retain(payload), 400, "retain_invalid_body")

    def test_a_body_that_is_not_an_object(self):
        sid = self.take_snapshot()
        with self.actor():
            resp = self.client.post("/api/next/sync/retain", json=[sid])
        # No snapshot id can be read from it, so that is the first thing missing.
        self.refused(resp, 400, "snapshot_required")

    def test_ids_that_are_not_ids(self):
        sid = self.take_snapshot()
        bad = ["not-a-uuid", 7, None, {"id": "x"}, ""]
        resp = self.post_retain(
            {"snapshotId": sid, "retain": {"movies": self.body["movies"] + bad, "containers": []}}
        )
        self.refused(resp, 400, "retain_invalid_ids")
        # The offending entries are counted, never echoed back.
        self.assertIn("5 entries", resp.get_json()["error"])
        self.assertNotIn("not-a-uuid", resp.get_data(as_text=True))

    def test_a_list_over_the_cap(self):
        sid = self.take_snapshot()
        too_many = [str(uuid.UUID(int=i)) for i in range(snap.RETAIN_MAX_IDS_PER_LIST + 1)]
        resp = self.post_retain({"snapshotId": sid, "retain": {"movies": too_many, "containers": []}})
        self.refused(resp, 413, "retain_too_large")

    def test_a_body_over_the_byte_cap(self):
        sid = self.take_snapshot()
        padding = "x" * (snap.RETAIN_MAX_BODY_BYTES + 1024)
        resp = self.post_retain({"snapshotId": sid, "retain": self.body, "padding": padding})
        self.refused(resp, 413, "retain_too_large")


@needs_database
class RetainBehaviourTests(WizardCase):
    def setUp(self):
        super().setUp()
        self.keep_movie = self.make_movie("keep")
        self.keep_movie_2 = self.make_movie("keep2")
        self.drop_movie = self.make_movie("drop")
        self.drop_movie_2 = self.make_movie("drop2")
        self.keep_box = self.make_container("keep")
        self.drop_box = self.make_container("drop")
        self.sid = self.take_snapshot()

    def retain(self, *, movies=None, containers=None, **kwargs):
        body = self.keep_all_but(
            movies=[self.drop_movie, self.drop_movie_2] if movies is None else movies,
            containers=[self.drop_box] if containers is None else containers,
        )
        return self.post_retain({"snapshotId": self.sid, "retain": body}, **kwargs)

    def test_exactly_the_rows_outside_the_lists_are_tombstoned(self):
        resp = self.retain()
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        body = resp.get_json()
        self.assertEqual(body["tombstoned"], {"movies": 2, "containers": 1})
        self.assertEqual(body["snapshotId"], self.sid)

        for gone in (self.drop_movie, self.drop_movie_2):
            self.assertIsNotNone(self.deleted_at("movies", gone))
        for kept in (self.keep_movie, self.keep_movie_2):
            self.assertTrue(self.is_live("movies", kept))
        self.assertIsNotNone(self.deleted_at("containers", self.drop_box))
        self.assertTrue(self.is_live("containers", self.keep_box))

    def test_the_answer_reports_what_is_left_and_where_the_revision_is(self):
        body = self.retain().get_json()
        self.assertEqual(body["counts"]["movies"], len(self.live_ids("movies")))
        self.assertEqual(body["counts"]["containers"], len(self.live_ids("containers")))
        self.assertEqual(body["revision"], self.current_revision())
        self.assertGreaterEqual(body["retained"]["movies"], 2)
        self.assertEqual(body["ignored"], {
            "movies": {"unknown": 0, "tombstoned": 0},
            "containers": {"unknown": 0, "tombstoned": 0},
        })

    def test_a_second_device_converges_through_the_delta(self):
        before = self.current_revision()
        self.retain()
        changes = self.delta(before)
        deleted = {(c["entity_type"], str(c["entity_id"])) for c in changes if c["operation"] == "delete"}
        self.assertEqual(
            deleted,
            {
                ("movie", self.drop_movie),
                ("movie", self.drop_movie_2),
                ("container", self.drop_box),
            },
        )
        # The rows it kept are not mentioned at all.
        mentioned = {str(c["entity_id"]) for c in changes}
        self.assertFalse(mentioned & {self.keep_movie, self.keep_movie_2, self.keep_box})

    def test_every_change_has_a_revision_of_its_own(self):
        # A delta page ends at the revision of its last change; two changes that
        # shared one could be split across a page and the second skipped.
        before = self.current_revision()
        self.retain()
        revisions = [c["revision"] for c in self.delta(before)]
        self.assertEqual(len(revisions), 3)
        self.assertEqual(len(set(revisions)), 3)
        self.assertEqual(revisions, sorted(revisions))

    def test_a_movie_delete_looks_the_same_as_one_made_by_movie_delete(self):
        # Same helper, so the same payload keys a client already reads.
        before = self.current_revision()
        self.retain()
        movie_change = next(
            c for c in self.delta(before) if c["entity_type"] == "movie" and str(c["entity_id"]) == self.drop_movie
        )
        self.assertEqual(str(movie_change["payload"]["entityId"]), self.drop_movie)
        self.assertEqual(movie_change["payload"]["clientMutationId"], f"retain:{self.sid}")

    def test_running_it_again_changes_nothing(self):
        self.assertEqual(self.retain().status_code, 200)
        revision, changes = self.current_revision(), self.change_count()
        again = self.retain()
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.get_json()["tombstoned"], {"movies": 0, "containers": 0})
        self.assertEqual(self.current_revision(), revision)
        self.assertEqual(self.change_count(), changes)

    def test_unknown_and_already_deleted_ids_are_counted_and_never_revived(self):
        already_gone = self.make_movie("already-gone")
        self.tombstone("movies", already_gone)
        body = self.keep_all_but(movies=[self.drop_movie, self.drop_movie_2], containers=[self.drop_box])
        body["movies"] += [already_gone, str(uuid.uuid4())]
        body["containers"] += [str(uuid.uuid4()), str(uuid.uuid4())]
        resp = self.post_retain({"snapshotId": self.sid, "retain": body})
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        ignored = resp.get_json()["ignored"]
        self.assertEqual(ignored["movies"], {"unknown": 1, "tombstoned": 1})
        self.assertEqual(ignored["containers"], {"unknown": 2, "tombstoned": 0})
        self.assertIsNotNone(self.deleted_at("movies", already_gone), "retain must never revive a deleted film")

    def test_duplicate_ids_and_id_spelling_do_not_matter(self):
        keep = self.keep_all_but(movies=[self.drop_movie, self.drop_movie_2], containers=[self.drop_box])
        shouting = [i.upper() for i in keep["movies"]] + keep["movies"]
        resp = self.post_retain({"snapshotId": self.sid, "retain": {**keep, "movies": shouting}})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["tombstoned"], {"movies": 2, "containers": 1})

    def test_it_does_not_touch_locations_series_or_any_users_personal_lists(self):
        with self.db.cursor() as cur:
            cur.execute(
                "INSERT INTO locations (name, public_id, qr_token) VALUES (%s, %s, %s)",
                (f"{self.prefix} shelf", f"{self.prefix}-loc", f"{self.prefix}-qr"),
            )
            cur.execute(
                "INSERT INTO series (public_id, title) VALUES (%s, %s)",
                (f"{self.prefix}-series", f"{self.prefix} series"),
            )
            for user in (self.owner_id, self.other_id):
                cur.execute(
                    "INSERT INTO wishlist_items (user_id, title) VALUES (%s, %s)", (user, f"{self.prefix} wish")
                )
                cur.execute(
                    "INSERT INTO tags (user_id, name, slug) VALUES (%s, %s, %s)",
                    (user, f"{self.prefix} tag", f"{self.prefix}-tag"),
                )
                # The person's watchlist points at a film retain is about to remove.
                cur.execute(
                    "INSERT INTO watchlist_items (user_id, movie_id) VALUES (%s, %s)", (user, self.drop_movie)
                )

        def counts():
            with self.db.cursor() as cur:
                out = {}
                for table in UNTOUCHED:
                    cur.execute(f"SELECT count(*) AS n FROM {table}")  # fixed table names
                    out[table] = cur.fetchone()["n"]
                return out

        before = counts()
        self.assertEqual(self.retain().status_code, 200)
        self.assertEqual(counts(), before)
        # ...including the row that points at the film that was removed.
        with self.db.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM watchlist_items WHERE movie_id=%s", (self.drop_movie,))
            self.assertEqual(cur.fetchone()["n"], 2)

    def test_it_leaves_the_memberships_of_a_removed_container_alone(self):
        # Same as container.delete: the links stay, the container is a tombstone.
        with self.db.cursor() as cur:
            cur.execute(
                "INSERT INTO container_movies (container_id, movie_id) VALUES (%s, %s)",
                (self.drop_box, self.keep_movie),
            )
        self.assertEqual(self.retain().status_code, 200)
        with self.db.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM container_movies WHERE container_id=%s", (self.drop_box,))
            self.assertEqual(cur.fetchone()["n"], 1)
        self.assertTrue(self.is_live("movies", self.keep_movie))

    def test_a_failure_midway_leaves_nothing_tombstoned(self):
        # The movies are tombstoned before the containers; a failure while the
        # containers are being removed must take the movies back with it.
        revision, changes = self.current_revision(), self.change_count()
        with patch.object(na, "emit_container_change", side_effect=RuntimeError("boom")):
            resp = self.retain()
        self.assertEqual(resp.status_code, 500)
        for movie in (self.drop_movie, self.drop_movie_2):
            self.assertTrue(self.is_live("movies", movie), "the movie tombstones were not rolled back")
        self.assertTrue(self.is_live("containers", self.drop_box))
        self.assertEqual(self.change_count(), changes)
        self.assertEqual(self.current_revision(), revision)
        # And the same request goes through once the fault is gone.
        self.assertEqual(self.retain().status_code, 200)
        self.assertIsNotNone(self.deleted_at("movies", self.drop_movie))

    def test_allow_empty_reaches_the_removal_with_empty_lists(self):
        # What allowEmpty does to a real database is "tombstone every live row",
        # which this shared database must not be asked to do; assert the route
        # hands the removal exactly what the request said.
        outcome = {
            "tombstoned": {"movies": 0, "containers": 0},
            "retained": {"movies": 0, "containers": 0},
            "ignored": {
                "movies": {"unknown": 0, "tombstoned": 0},
                "containers": {"unknown": 0, "tombstoned": 0},
            },
        }
        with patch.object(na, "retain_library_entities", return_value=outcome) as removal:
            resp = self.post_retain(
                {"snapshotId": self.sid, "retain": {"movies": [], "containers": []}, "allowEmpty": True}
            )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        removal.assert_called_once()
        self.assertEqual(removal.call_args.kwargs["movie_ids"], [])
        self.assertEqual(removal.call_args.kwargs["container_ids"], [])

    def test_one_empty_list_is_an_ordinary_library(self):
        # A phone with films and no box sets: containers [] is accepted, and every
        # live container outside it goes.
        keep = self.keep_all_but(movies=[self.drop_movie, self.drop_movie_2])
        with patch.object(na, "retain_library_entities") as removal:
            removal.return_value = {
                "tombstoned": {"movies": 0, "containers": 0},
                "retained": {"movies": 0, "containers": 0},
                "ignored": {
                    "movies": {"unknown": 0, "tombstoned": 0},
                    "containers": {"unknown": 0, "tombstoned": 0},
                },
            }
            resp = self.post_retain({"snapshotId": self.sid, "retain": {"movies": keep["movies"], "containers": []}})
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertEqual(removal.call_args.kwargs["container_ids"], [])

    def test_it_is_audited(self):
        self.assertEqual(self.retain().status_code, 200)
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT metadata, actor_user_id FROM audit_events WHERE event_type=%s AND target_id=%s",
                ("sync.library_retained", self.sid),
            )
            row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(str(row["actor_user_id"]), self.owner_id)
        self.assertEqual(row["metadata"]["tombstoned"], {"movies": 2, "containers": 1})

    def test_the_snapshot_can_be_used_again_inside_its_window(self):
        # Retain is idempotent and a wizard run may have to be retried, so the
        # id is not spent by one use.
        self.assertEqual(self.retain().status_code, 200)
        self.assertEqual(self.retain().status_code, 200)


@needs_database
class RetainHelperTests(WizardCase):
    """`parse_retain_request` on its own: no database, no request."""

    def test_lists_are_deduplicated_sorted_and_normalised(self):
        a, b = uuid.UUID(int=1), uuid.UUID(int=2)
        parsed = snap.parse_retain_request(
            {"retain": {"movies": [str(b), str(a).upper(), str(a)], "containers": [f" {b} "]}}
        )
        self.assertEqual(parsed["movies"], [a, b])
        self.assertEqual(parsed["containers"], [b])
        self.assertFalse(parsed["allowEmpty"])

    def test_missing_lists_count_as_empty(self):
        parsed = snap.parse_retain_request({"retain": {"movies": [str(uuid.UUID(int=1))]}})
        self.assertEqual(parsed["containers"], [])
