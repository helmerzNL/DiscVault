"""The link wizard's safety snapshot (sync-contract §5f): `POST /sync/snapshot`.

Before the server's library may be replaced from a phone, the server takes a copy
and refuses to go on without it. These tests assert the properties that make that
copy trustworthy, against a real database, because a fake cursor cannot tell a
row that exists from one that merely was inserted:

* the id exists only once an archive has been written, read back and validated by
  the backup engine's own `validate_backup_zip`;
* an id belongs to whoever took it -- another user's id answers like an unknown one;
* it expires, its file must still be on disk, and a failure at any point leaves
  neither a row nor a file behind;
* it is rate limited, space checked and pruned, and pruning never touches a backup
  an administrator made by hand in the same directory.

Who may call it is `test_next_sync_library_replace_permission`. Point
`DATABASE_URL` at a database with every migration applied; without it these skip.
"""

import os
import shutil
import sys
import unittest
import uuid
from unittest.mock import patch

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
    from app.backend import next_app as na
    from app.backend import next_backup
    from app.backend import next_preferences
    from app.backend import next_sync_snapshot as snap
    from app.backend.next_common import NextApiError
except ModuleNotFoundError as exc:  # pragma: no cover - minimal environments
    if exc.name not in {"flask", "psycopg", "cbor2", "argon2", "jwt", "segno", "PIL"}:
        raise
    na = None

from .sync_wizard_support import DATABASE_URL, KEY, NATIVE_LIST, WizardCase, needs_database


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and na is not None,
    "PostgreSQL test database is not configured",
)
class SnapshotTests(WizardCase):
    # -- creation ---------------------------------------------------------

    def test_an_owner_gets_a_snapshot_whose_archive_the_engine_accepts(self):
        resp = self.post()
        self.assertEqual(resp.status_code, 201, resp.get_json())
        body = resp.get_json()
        self.assertEqual(body["status"], "ok")
        uuid.UUID(body["snapshotId"])
        self.assertEqual(body["validForSeconds"], snap.SNAPSHOT_VALID_FOR_SECONDS)
        self.assertGreater(body["sizeBytes"], 0)
        self.assertEqual(len(body["sha256"]), 64)

        # The archive exists, is where the Backup page would list it, and passes
        # the engine's own validation -- not just "a file was written".
        path = self.backup_dir / body["fileName"]
        self.assertTrue(path.is_file())
        report = next_backup.validate_backup_zip(path)
        self.assertTrue(report["valid"], report)
        self.assertEqual(next_backup.sha256_file(path), body["sha256"])

        # The counts are the live counts sync/state?counts=1 reports.
        with self.db.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM movies WHERE deleted_at IS NULL")
            movies = cur.fetchone()["n"]
        self.assertEqual(body["counts"]["movies"], movies)
        self.assertIn("containers", body["counts"])

        rows = self.snapshot_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0]["id"]), body["snapshotId"])
        self.assertEqual(str(rows[0]["created_by"]), self.owner_id)

    def test_the_snapshot_covers_the_user_own_images_and_personal_lists(self):
        # The wizard may replace the acting user's personal lists, and own
        # images exist nowhere else once pushed; the copy has to carry both.
        body = self.post().get_json()
        report = next_backup.validate_backup_zip(self.backup_dir / body["fileName"])
        scopes = set(report.get("scopes") or [])
        self.assertTrue({"collection", "film_artwork", "personal_lists"} <= scopes, scopes)
        self.assertNotIn("user_accounts", scopes)

    def test_it_is_audited(self):
        body = self.post().get_json()
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT target_id, actor_user_id FROM audit_events WHERE event_type=%s AND target_id=%s",
                ("sync.library_snapshot_created", body["snapshotId"]),
            )
            row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(str(row["actor_user_id"]), self.owner_id)

    # -- refusals ---------------------------------------------------------

    def test_an_ordinary_users_native_token_is_refused_and_leaves_nothing(self):
        resp = self.post(self.other_id, role="media_editor", permissions=("collection.view",), token_keys=NATIVE_LIST)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.get_json()["errorCode"], "permission_missing")
        self.assertEqual(self.snapshot_rows(), [])
        self.assertEqual(self.archives(), [])

    def test_with_authentication_off_it_is_refused(self):
        resp = self.post(auth_enabled=False)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.get_json()["errorCode"], "auth_not_enabled")
        self.assertEqual(self.snapshot_rows(), [])
        self.assertEqual(self.archives(), [])

    def test_a_second_snapshot_within_the_interval_is_rate_limited(self):
        self.assertEqual(self.post().status_code, 201)
        resp = self.post()
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.get_json()["errorCode"], "snapshot_rate_limited")
        self.assertEqual(len(self.snapshot_rows()), 1)
        self.assertEqual(len(self.archives()), 1)
        self.backdate(snap.SNAPSHOT_MIN_INTERVAL_SECONDS + 1)
        self.assertEqual(self.post().status_code, 201)

    def test_too_little_free_space_is_refused_before_anything_is_written(self):
        usage = shutil.disk_usage(self.tmp)._replace(free=1024)
        with patch.object(snap.shutil, "disk_usage", return_value=usage):
            resp = self.post()
        self.assertEqual(resp.status_code, 507)
        self.assertEqual(resp.get_json()["errorCode"], "snapshot_no_space")
        self.assertEqual(self.snapshot_rows(), [])
        self.assertEqual(self.archives(), [])

    def test_a_failing_export_leaves_no_row_and_no_file(self):
        real = snap.export_functional_backup

        def writes_then_fails(conn, output_path, **kwargs):
            real(conn, output_path, **kwargs)
            raise RuntimeError("boom after the archive was written")

        with patch.object(snap, "export_functional_backup", side_effect=writes_then_fails):
            resp = self.post()
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.get_json()["errorCode"], "snapshot_failed")
        self.assertNotIn("boom", resp.get_data(as_text=True))
        self.assertEqual(self.snapshot_rows(), [])
        self.assertEqual(self.archives(), [])

    def test_an_archive_the_engine_rejects_is_never_recorded(self):
        with patch.object(snap, "validate_backup_zip", return_value={"valid": False, "errors": ["nope"]}):
            resp = self.post()
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.get_json()["errorCode"], "snapshot_failed")
        self.assertEqual(self.snapshot_rows(), [])
        self.assertEqual(self.archives(), [])

    # -- retention --------------------------------------------------------

    def test_only_the_newest_snapshots_are_kept_and_a_manual_backup_survives(self):
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        manual = self.backup_dir / "discvault-next-movies-groups-20250101-000000.zip"
        manual.write_bytes(b"an administrator's own backup")
        ids = []
        for _ in range(snap.SNAPSHOT_KEEP + 2):
            self.backdate(snap.SNAPSHOT_MIN_INTERVAL_SECONDS + 1)
            resp = self.post()
            self.assertEqual(resp.status_code, 201, resp.get_json())
            ids.append(resp.get_json()["snapshotId"])
        kept = [str(row["id"]) for row in self.snapshot_rows()]
        self.assertEqual(len(kept), snap.SNAPSHOT_KEEP)
        self.assertEqual(kept, ids[-snap.SNAPSHOT_KEEP :])
        snapshot_files = [name for name in self.archives() if name.startswith(snap.SNAPSHOT_FILE_PREFIX)]
        self.assertEqual(len(snapshot_files), snap.SNAPSHOT_KEEP)
        self.assertTrue(manual.is_file(), "pruning must never touch a backup it did not make")


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and na is not None,
    "PostgreSQL test database is not configured",
)
class SnapshotValidationTests(WizardCase):
    """`require_valid_snapshot`: the one check behind retain and supersede."""

    def validate(self, snapshot_id, user_id=None):
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            return snap.require_valid_snapshot(
                conn, snapshot_id, {"id": user_id or self.owner_id}, backup_dir=self.backup_dir
            )

    def make(self):
        resp = self.post()
        self.assertEqual(resp.status_code, 201, resp.get_json())
        return resp.get_json()["snapshotId"]

    def refused(self, snapshot_id, code, user_id=None):
        with self.assertRaises(NextApiError) as ctx:
            self.validate(snapshot_id, user_id)
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception

    def test_a_fresh_snapshot_of_the_same_user_is_valid(self):
        sid = self.make()
        self.assertEqual(self.validate(sid)["snapshotId"], sid)
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            self.assertTrue(snap.snapshot_is_valid(conn, sid, {"id": self.owner_id}, backup_dir=self.backup_dir))
            self.assertFalse(snap.snapshot_is_valid(conn, sid, {"id": self.other_id}, backup_dir=self.backup_dir))
            self.assertFalse(snap.snapshot_is_valid(conn, None, {"id": self.owner_id}, backup_dir=self.backup_dir))

    def test_missing_or_malformed_ids_are_snapshot_required(self):
        for value in (None, "", "   ", "not-a-uuid", "12345"):
            with self.subTest(value=value):
                err = self.refused(value, "snapshot_required")
                self.assertEqual(err.status_code, 400)

    def test_an_unknown_id_is_snapshot_unknown(self):
        err = self.refused(str(uuid.uuid4()), "snapshot_unknown")
        self.assertEqual(err.status_code, 404)

    def test_another_users_id_answers_exactly_like_an_unknown_one(self):
        sid = self.make()
        mine = self.refused(str(uuid.uuid4()), "snapshot_unknown")
        theirs = self.refused(sid, "snapshot_unknown", user_id=self.other_id)
        self.assertEqual((theirs.status_code, str(theirs)), (mine.status_code, str(mine)))

    def test_an_old_snapshot_is_expired(self):
        sid = self.make()
        self.backdate(snap.SNAPSHOT_VALID_FOR_SECONDS + 60)
        err = self.refused(sid, "snapshot_expired")
        self.assertEqual(err.status_code, 410)

    def test_a_snapshot_just_inside_the_window_is_still_valid(self):
        sid = self.make()
        self.backdate(snap.SNAPSHOT_VALID_FOR_SECONDS - 60)
        self.assertEqual(self.validate(sid)["snapshotId"], sid)

    def test_a_deleted_archive_is_snapshot_file_missing(self):
        sid = self.make()
        for path in self.backup_dir.glob(f"{snap.SNAPSHOT_FILE_PREFIX}*.zip"):
            path.unlink()
        err = self.refused(sid, "snapshot_file_missing")
        self.assertEqual(err.status_code, 409)

    def test_an_archive_that_changed_size_is_not_trusted(self):
        sid = self.make()
        for path in self.backup_dir.glob(f"{snap.SNAPSHOT_FILE_PREFIX}*.zip"):
            path.write_bytes(b"truncated")
        self.refused(sid, "snapshot_file_missing")

    def test_a_pruned_snapshot_is_unknown(self):
        first = self.make()
        for _ in range(snap.SNAPSHOT_KEEP):
            self.backdate(snap.SNAPSHOT_MIN_INTERVAL_SECONDS + 1)
            self.make()
        self.refused(first, "snapshot_unknown")


class AdvertisedToTheNativeClientTests(unittest.TestCase):
    """The native contract says where the route is and who may use it."""

    @unittest.skipIf(na is None, "backend dependencies are required")
    def test_the_contract_advertises_the_snapshot_and_retain_routes(self):
        sync = next_preferences.mobile_endpoint_contract_payload()["sync"]
        self.assertEqual(sync["snapshot"], "/api/next/sync/snapshot")
        self.assertEqual(sync["retain"], "/api/next/sync/retain")

    @unittest.skipIf(na is None, "backend dependencies are required")
    def test_the_native_token_carries_the_key_and_a_profile_token_cannot(self):
        from app.backend import next_api_token, next_auth

        self.assertIn(KEY, next_auth.MOBILE_AUTH_TOKEN_PERMISSIONS)
        self.assertNotIn(KEY, next_api_token.API_TOKEN_GRANTABLE_PERMISSION_KEYS)

    @unittest.skipIf(na is None, "backend dependencies are required")
    def test_the_capability_flag_follows_the_route_not_just_the_key(self):
        def flag(role, keys):
            with patch.object(na, "actor_effective_permission_keys", return_value=set(keys)):
                caps = next_preferences.mobile_feature_capabilities(None, {"role": role})
            return caps["sync"]["replaceLibrary"]

        self.assertTrue(flag("owner", {KEY}))
        self.assertTrue(flag("admin", {KEY}))
        self.assertFalse(flag("media_editor", {"collection.view"}))
        # A custom role an admin granted the key: the route refuses it, so the
        # client must not be told it may.
        self.assertFalse(flag("custom_curator", {KEY}))
        # An owner whose old token lacks the key.
        self.assertFalse(flag("owner", {"collection.view"}))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
