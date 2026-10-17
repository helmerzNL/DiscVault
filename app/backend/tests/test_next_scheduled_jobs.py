"""Built-in scheduler for the daily backup and the Jellyfin / Plex syncs (issue #812)."""

import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_scheduled_jobs as sj  # noqa: E402

NOW = datetime(2026, 10, 16, 3, 0, tzinfo=timezone.utc)


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, params=None):
        text = " ".join(str(query).split())
        self.conn.executed.append((text, params))
        self._rows = []
        if "FROM background_jobs" in text and "SELECT 1" in text:
            self._rows = [{"?column?": 1}] if self.conn.active_jobs else []
        elif "FROM plugins" in text:
            ready = params[0] in self.conn.ready_plugins if params else False
            self._rows = [{"?column?": 1}] if ready else []

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    def __init__(self, config=None, state=None, active_jobs=False, ready_plugins=("jellyfin", "plex")):
        self.config = config
        self.state = state or {}
        self.active_jobs = active_jobs
        self.ready_plugins = set(ready_plugins)
        self.executed = []

    def cursor(self):
        return FakeCursor(self)

    def inserted_jobs(self):
        return [params for text, params in self.executed if text.startswith("INSERT INTO background_jobs")]


def patched(conn):
    return (
        patch.object(sj, "table_exists", return_value=True),
        patch.object(sj, "load_config", side_effect=lambda c: sj.normalize_config(conn.config)),
        patch.object(sj, "load_state", side_effect=lambda c: conn.state),
        patch.object(sj, "_update_state", side_effect=lambda c, job, **v: conn.state.setdefault(job, {}).update(v)),
    )


class ConfigTests(unittest.TestCase):
    def test_defaults_are_off_and_complete(self):
        config = sj.normalize_config(None)
        self.assertFalse(config["backup"]["enabled"])
        self.assertFalse(config["jellyfin"]["enabled"])
        self.assertFalse(config["plex"]["enabled"])
        self.assertEqual(config["plex"]["intervalHours"], 24)
        self.assertEqual(config["backup"]["intervalHours"], 24)
        self.assertEqual(config["backup"]["keep"], 7)
        self.assertTrue(config["backup"]["onlyIfChanged"])
        self.assertIn("collection", config["backup"]["scopes"])

    def test_values_are_clamped_and_coerced(self):
        config = sj.normalize_config(
            {"backup": {"enabled": "true", "intervalHours": 0, "keep": 9999, "onlyIfChanged": "no"},
             "jellyfin": {"enabled": True, "intervalHours": "abc"}}
        )
        self.assertTrue(config["backup"]["enabled"])
        self.assertEqual(config["backup"]["intervalHours"], sj.MIN_INTERVAL_HOURS)
        self.assertEqual(config["backup"]["keep"], sj.MAX_KEEP)
        self.assertFalse(config["backup"]["onlyIfChanged"])
        self.assertEqual(config["jellyfin"]["intervalHours"], 24)

    def test_requested_by_needs_an_id(self):
        for job in ("jellyfin", "plex"):
            with self.subTest(job=job):
                self.assertIsNone(sj.normalize_config({job: {"requestedBy": {"username": "x"}}})[job]["requestedBy"])
                kept = sj.normalize_config({job: {"requestedBy": {"id": 5, "username": "x"}}})[job]["requestedBy"]
                self.assertEqual(kept["id"], "5")

    def test_a_section_saved_alone_leaves_the_others_as_stored(self):
        # The Backup tab and the Scheduled tasks tab each save only their own
        # jobs; saving one must not reset the other to its defaults.
        stored = {"backup": {"enabled": True, "keep": 3}, "plex": {"enabled": True, "intervalHours": 6}}
        saved = {}
        with patch.object(sj, "load_config", return_value=sj.normalize_config(stored)), \
                patch.object(sj, "_store_setting", side_effect=lambda c, key, value, actor_id=None: saved.update(value)):
            config = sj.save_config(None, {"jellyfin": {"enabled": True}})
        self.assertTrue(config["jellyfin"]["enabled"])
        self.assertTrue(config["plex"]["enabled"])
        self.assertEqual(config["plex"]["intervalHours"], 6)
        self.assertEqual(config["backup"]["keep"], 3)
        self.assertEqual(saved["plex"]["intervalHours"], 6)

    def test_a_partial_section_keeps_its_other_fields(self):
        stored = {"plex": {"enabled": True, "intervalHours": 6, "includeWatched": False}}
        with patch.object(sj, "load_config", return_value=sj.normalize_config(stored)), \
                patch.object(sj, "_store_setting"):
            config = sj.save_config(None, {"plex": {"intervalHours": 12}})
        self.assertEqual(config["plex"]["intervalHours"], 12)
        self.assertTrue(config["plex"]["enabled"])
        self.assertFalse(config["plex"]["includeWatched"])

    def test_is_due(self):
        self.assertTrue(sj.is_due(None, 24, now=NOW))
        self.assertTrue(sj.is_due("garbage", 24, now=NOW))
        self.assertFalse(sj.is_due((NOW - timedelta(hours=23)).isoformat(), 24, now=NOW))
        self.assertTrue(sj.is_due((NOW - timedelta(hours=24)).isoformat(), 24, now=NOW))


class PruneTests(unittest.TestCase):
    def test_only_scheduled_archives_beyond_keep_are_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            names = [f"discvault-scheduled-2026101{i}-030000.zip" for i in range(1, 6)]
            for name in names + ["discvault-next-movies-groups-20260101-000000.zip", "notes.txt"]:
                (d / name).write_bytes(b"x")
            removed = sj.prune_scheduled_backups(d, 2)
            self.assertEqual(sorted(removed), sorted(names[:3]))
            self.assertTrue((d / names[4]).exists() and (d / names[3]).exists())
            self.assertTrue((d / "discvault-next-movies-groups-20260101-000000.zip").exists())
            self.assertTrue((d / "notes.txt").exists())


class ScheduledBackupTests(unittest.TestCase):
    def run_backup(self, conn, tmp, fingerprint="fp1"):
        def fake_export(_conn, output_path, **kwargs):
            output_path.write_bytes(b"zip")
            return {"sizeBytes": 3, "sha256": "abc"}

        p = patched(conn)
        with p[0], p[1], p[2], p[3], \
                patch.object(sj, "data_fingerprint", return_value=fingerprint), \
                patch.object(sj, "export_functional_backup", side_effect=fake_export):
            return sj.run_scheduled_backup(conn, backup_dir=Path(tmp), data_dir=Path(tmp), now=NOW)

    def test_disabled_does_nothing(self):
        conn = FakeConn(config={})
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_backup(conn, tmp)["reason"], "disabled")
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_creates_then_skips_when_unchanged_then_recreates_on_change(self):
        conn = FakeConn(config={"backup": {"enabled": True}})
        with tempfile.TemporaryDirectory() as tmp:
            first = self.run_backup(conn, tmp, "fp1")
            self.assertEqual(first["status"], "created")
            self.assertTrue(first["fileName"].startswith(sj.SCHEDULED_BACKUP_PREFIX))
            self.assertEqual(self.run_backup(conn, tmp, "fp1")["reason"], "unchanged")
            self.assertEqual(len(list(Path(tmp).iterdir())), 1)
            later = NOW + timedelta(days=1)
            p = patched(conn)
            with p[0], p[1], p[2], p[3], \
                    patch.object(sj, "data_fingerprint", return_value="fp2"), \
                    patch.object(sj, "export_functional_backup",
                                 side_effect=lambda c, out, **k: (out.write_bytes(b"z"), {"sizeBytes": 1})[1]):
                second = sj.run_scheduled_backup(conn, backup_dir=Path(tmp), data_dir=Path(tmp), now=later)
            self.assertEqual(second["status"], "created")

    def test_always_backs_up_when_only_if_changed_is_off(self):
        conn = FakeConn(config={"backup": {"enabled": True, "onlyIfChanged": False}})
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_backup(conn, tmp)["status"], "created")
            conn.state["backup"]["fileName"] = None
            self.assertEqual(self.run_backup(conn, tmp)["status"], "created")

    def test_recreates_if_previous_archive_was_deleted(self):
        conn = FakeConn(config={"backup": {"enabled": True}})
        with tempfile.TemporaryDirectory() as tmp:
            first = self.run_backup(conn, tmp, "fp1")
            (Path(tmp) / first["fileName"]).unlink()
            self.assertEqual(self.run_backup(conn, tmp, "fp1")["status"], "created")


class EnqueueTests(unittest.TestCase):
    def enqueue(self, conn):
        p = patched(conn)
        with p[0], p[1], p[2], p[3]:
            return sj.enqueue_due_jobs(conn, "w1", now=NOW)

    def test_nothing_enabled_queues_nothing(self):
        conn = FakeConn(config={})
        self.assertEqual(self.enqueue(conn), [])
        self.assertEqual(conn.inserted_jobs(), [])

    def test_due_backup_and_jellyfin_are_queued_once(self):
        actor = {"id": "u1", "username": "admin", "role": "owner"}
        conn = FakeConn(config={"backup": {"enabled": True}, "jellyfin": {"enabled": True, "requestedBy": actor}})
        queued = self.enqueue(conn)
        self.assertEqual(queued, ["backup", "jellyfin.sync_library", "jellyfin.sync_personal_lists"])
        self.assertEqual(len(conn.inserted_jobs()), 3)
        # Not due again until the interval passes.
        self.assertEqual(self.enqueue(conn), [])

    def test_watched_sync_needs_a_user(self):
        conn = FakeConn(config={"jellyfin": {"enabled": True}})
        self.assertEqual(self.enqueue(conn), ["jellyfin.sync_library"])

    def test_active_job_blocks_duplicate_and_leaves_schedule_untouched(self):
        conn = FakeConn(config={"backup": {"enabled": True}}, active_jobs=True)
        self.assertEqual(self.enqueue(conn), [])
        self.assertNotIn("backup", conn.state)

    def test_jellyfin_not_installed_is_skipped(self):
        conn = FakeConn(config={"jellyfin": {"enabled": True}}, ready_plugins=("plex",))
        self.assertEqual(self.enqueue(conn), [])

    def test_plex_is_queued_against_the_plex_plugin(self):
        actor = {"id": "u1", "username": "admin", "role": "owner"}
        conn = FakeConn(config={"plex": {"enabled": True, "requestedBy": actor}})
        self.assertEqual(self.enqueue(conn), ["plex.sync_library", "plex.sync_personal_lists"])
        payloads = [params[0].obj for params in conn.inserted_jobs()]
        self.assertEqual({p["pluginId"] for p in payloads}, {"plex"})
        self.assertEqual(payloads[1]["requestedBy"], actor)
        self.assertNotIn("requestedBy", payloads[0])
        self.assertIn("lastEnqueuedAt", conn.state["plex"])
        self.assertNotIn("jellyfin", conn.state)

    def test_plex_not_installed_is_skipped_without_blocking_jellyfin(self):
        conn = FakeConn(config={"jellyfin": {"enabled": True}, "plex": {"enabled": True}}, ready_plugins=("jellyfin",))
        self.assertEqual(self.enqueue(conn), ["jellyfin.sync_library"])
        self.assertNotIn("plex", conn.state)

    def test_each_server_keeps_its_own_interval(self):
        conn = FakeConn(
            config={"jellyfin": {"enabled": True}, "plex": {"enabled": True, "intervalHours": 1}},
            state={"jellyfin": {"lastEnqueuedAt": (NOW - timedelta(hours=2)).isoformat()},
                   "plex": {"lastEnqueuedAt": (NOW - timedelta(hours=2)).isoformat()}},
        )
        self.assertEqual(self.enqueue(conn), ["plex.sync_library"])


if __name__ == "__main__":
    unittest.main()
