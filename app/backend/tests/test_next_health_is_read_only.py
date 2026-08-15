"""A liveness probe may not delete things.

`GET /api/next/health` is the container health check, fired every ten seconds
per container (`app/deploy/next/docker-compose.yml`). It also ran
`purge_expired_artwork_trash` on every call: deleting `entity_media` rows,
deleting the `media_assets` rows nothing pointed at any more, and unlinking the
files underneath them.

Under I/O load that work can outlast the probe's own timeout, and a probe that
times out marks a healthy container unhealthy and restarts it. So the failure
mode is not "the health check is slow" -- it is a container being restarted
because of cleanup that was never urgent (App-Guidance PERF-02, #153).

The purge itself was never wrong; only its schedule was. Retention is measured
in days (7 by default, `artwork_trash_retention`), and it was running 8,640
times a day from a probe that is supposed to answer one question: can the API
reach a migrated database?

It now runs as `artwork.trash_purge` on the worker, enqueued at most once an
hour. **What gets purged is unchanged** -- the same function, moved to
`next_artwork_trash` so the worker can import it without importing the Flask
app. These tests pin both halves: that the probe no longer deletes, and that
the purge still deletes exactly what it did.
"""

import os
import sys
import unittest
import uuid
from pathlib import Path


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:  # pragma: no cover - minimal environments
    psycopg = None
    dict_row = None

DATABASE_URL = os.environ.get("DATABASE_URL")

try:
    from app.backend import next_app
    from app.backend import next_artwork_trash
except ModuleNotFoundError as exc:  # pragma: no cover - minimal environments
    if exc.name not in {"flask", "psycopg", "cbor2", "argon2", "jwt", "segno", "PIL"}:
        raise
    next_app = None
    next_artwork_trash = None


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and next_app is not None,
    "PostgreSQL test database is not configured",
)
class HealthProbeIsReadOnlyTests(unittest.TestCase):
    PREFIX = "trash-probe-"

    @classmethod
    def setUpClass(cls):
        cls.app = next_app.create_app()

    def setUp(self):
        self.tag = f"{self.PREFIX}{uuid.uuid4().hex[:8]}"
        self.client = self.app.test_client()
        self.addCleanup(self._cleanup)

    def _connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)

    def _cleanup(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM entity_media WHERE media_id IN "
                    "(SELECT id FROM media_assets WHERE storage_key LIKE %s)",
                    (f"%{self.PREFIX}%",),
                )
                cur.execute("DELETE FROM media_assets WHERE storage_key LIKE %s", (f"%{self.PREFIX}%",))
                cur.execute("DELETE FROM movies WHERE title LIKE %s", (f"{self.PREFIX}%",))
            conn.commit()

    def _expired_trash_row(self, *, expired: bool = True, kind: str = "poster"):
        """One hidden poster whose retention window has (or has not) run out."""

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO movies (public_id, title, sort_title, year) "
                    "VALUES (gen_random_uuid()::text, %s, %s, 2001) RETURNING id",
                    (f"{self.tag}-movie", f"{self.tag}-movie"),
                )
                movie_id = cur.fetchone()["id"]
                cur.execute(
                    """
                    INSERT INTO media_assets (kind, variant, storage_backend, storage_key, sha256)
                    VALUES (%s, 'original', 'local', %s, %s)
                    RETURNING id
                    """,
                    (kind, f"media/{self.tag}.jpg", uuid.uuid4().hex),
                )
                media_id = cur.fetchone()["id"]
                purge_after = "-1 day" if expired else "6 days"
                cur.execute(
                    """
                    INSERT INTO entity_media (entity_type, entity_id, media_id, role, deleted_at, purge_after)
                    VALUES ('movie', %s, %s, 'primary', now() - interval '8 days', now() + %s::interval)
                    """,
                    (movie_id, media_id, purge_after),
                )
            conn.commit()
        return movie_id, media_id

    def _link_exists(self, media_id) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM entity_media WHERE media_id=%s", (media_id,))
            return cur.fetchone() is not None

    def _asset_exists(self, media_id) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM media_assets WHERE id=%s", (media_id,))
            return cur.fetchone() is not None

    # -- the probe -------------------------------------------------------

    def test_the_probe_does_not_purge_expired_artwork(self):
        # The defect, stated directly: this row used to disappear because
        # something asked whether the container was alive.
        _, media_id = self._expired_trash_row()
        self.assertEqual(self.client.get("/api/next/health").status_code, 200)
        self.assertTrue(self._link_exists(media_id), "the health probe deleted an entity_media row")
        self.assertTrue(self._asset_exists(media_id), "the health probe deleted a media_assets row")

    def test_the_probe_still_answers_what_it_is_for(self):
        payload = self.client.get("/api/next/health").get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["service"], "discvault-next-api")
        self.assertEqual(payload["migrations"]["state"], "ready")
        self.assertTrue(payload["database"]["database"])

    def test_the_probe_no_longer_reports_maintenance_it_does_not_do(self):
        # The field carried the purge counts. Keeping it at zero forever would
        # assert the probe still does maintenance, which is the thing removed.
        self.assertNotIn("maintenance", self.client.get("/api/next/health").get_json())

    # -- the purge, unchanged --------------------------------------------

    def test_the_purge_still_removes_what_the_probe_used_to_remove(self):
        _, media_id = self._expired_trash_row()
        with self._connect() as conn:
            summary = next_artwork_trash.purge_expired_artwork_trash(conn)
            conn.commit()
        self.assertGreaterEqual(summary["purgedLinks"], 1)
        self.assertFalse(self._link_exists(media_id))
        self.assertFalse(self._asset_exists(media_id))

    def test_artwork_inside_its_retention_window_is_left_alone(self):
        _, media_id = self._expired_trash_row(expired=False)
        with self._connect() as conn:
            next_artwork_trash.purge_expired_artwork_trash(conn)
            conn.commit()
        self.assertTrue(self._link_exists(media_id))
        self.assertTrue(self._asset_exists(media_id))

    def test_an_asset_another_entity_still_uses_survives_its_purged_link(self):
        # The reference check is the reason the purge is row-by-row rather than
        # one DELETE: an asset two entities share must outlive the first link.
        movie_id, media_id = self._expired_trash_row()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO movies (public_id, title, sort_title, year) "
                    "VALUES (gen_random_uuid()::text, %s, %s, 2002) RETURNING id",
                    (f"{self.tag}-second", f"{self.tag}-second"),
                )
                other_movie = cur.fetchone()["id"]
                cur.execute(
                    "INSERT INTO entity_media (entity_type, entity_id, media_id, role) "
                    "VALUES ('movie', %s, %s, 'primary')",
                    (other_movie, media_id),
                )
            conn.commit()
        with self._connect() as conn:
            summary = next_artwork_trash.purge_expired_artwork_trash(conn)
            conn.commit()
        self.assertEqual(summary["purgedAssets"], 0)
        self.assertTrue(self._asset_exists(media_id))
        self.assertTrue(self._link_exists(media_id))


class ArtworkTrashFileRemovalTests(unittest.TestCase):
    """`delete_local_media_asset_file` decides what may be unlinked."""

    @unittest.skipIf(next_artwork_trash is None, "backend dependencies are unavailable")
    def setUp(self):
        self.data_dir = Path(os.environ.get("DISCVAULT_LEGACY_DATA_DIR") or "/data")

    def test_a_remote_asset_is_never_unlinked(self):
        self.assertFalse(
            next_artwork_trash.delete_local_media_asset_file(
                {"storage_backend": "s3", "storage_key": "media/anything.jpg"}
            )
        )

    def test_a_key_escaping_the_data_directory_is_refused(self):
        # The guard that keeps a crafted storage_key from reaching /etc.
        self.assertFalse(
            next_artwork_trash.delete_local_media_asset_file(
                {"storage_backend": "local", "storage_key": "../../etc/hostname"}
            )
        )

    def test_a_missing_file_is_not_an_error(self):
        self.assertFalse(
            next_artwork_trash.delete_local_media_asset_file(
                {"storage_backend": "local", "storage_key": f"media/{uuid.uuid4().hex}.jpg"}
            )
        )

    def test_a_local_file_inside_the_data_directory_is_unlinked(self):
        target = self.data_dir / "media" / f"purge-probe-{uuid.uuid4().hex}.jpg"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"poster")
        except OSError:  # pragma: no cover - read-only data dir in this environment
            self.skipTest("the data directory is not writable here")
        self.addCleanup(lambda: target.unlink(missing_ok=True))
        self.assertTrue(
            next_artwork_trash.delete_local_media_asset_file(
                {
                    "storage_backend": "local",
                    "storage_key": str(target.relative_to(self.data_dir)),
                }
            )
        )
        self.assertFalse(target.exists())


class PurgeIntervalTests(unittest.TestCase):
    """How often the worker enqueues the purge, and how that is configured."""

    @unittest.skipIf(next_artwork_trash is None, "backend dependencies are unavailable")
    def setUp(self):
        self._previous = os.environ.get("DISCVAULT_ARTWORK_TRASH_PURGE_INTERVAL_HOURS")
        self.addCleanup(self._restore)

    def _restore(self):
        if self._previous is None:
            os.environ.pop("DISCVAULT_ARTWORK_TRASH_PURGE_INTERVAL_HOURS", None)
        else:
            os.environ["DISCVAULT_ARTWORK_TRASH_PURGE_INTERVAL_HOURS"] = self._previous

    def _interval(self, value: str | None) -> float:
        if value is None:
            os.environ.pop("DISCVAULT_ARTWORK_TRASH_PURGE_INTERVAL_HOURS", None)
        else:
            os.environ["DISCVAULT_ARTWORK_TRASH_PURGE_INTERVAL_HOURS"] = value
        return next_artwork_trash.purge_interval_hours()

    def test_the_default_is_hourly(self):
        self.assertEqual(self._interval(None), 1.0)

    def test_an_operator_can_shorten_it(self):
        self.assertEqual(self._interval("0.25"), 0.25)

    def test_a_malformed_interval_falls_back_rather_than_crashing_the_loop(self):
        # This is read on every poll. A typo must not stop the worker.
        self.assertEqual(self._interval("hourly"), 1.0)

    def test_a_zero_or_negative_interval_does_not_become_a_busy_loop(self):
        self.assertEqual(self._interval("0"), 1.0)
        self.assertEqual(self._interval("-4"), 1.0)


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and next_app is not None,
    "PostgreSQL test database is not configured",
)
class ArtworkTrashSchedulerTests(unittest.TestCase):
    """The scheduler that replaced the ten-second probe."""

    def setUp(self):
        sys.path.insert(0, os.path.join(repo_root, "app", "backend"))
        import next_worker

        self.worker = next_worker
        self.addCleanup(self._cleanup)
        self._cleanup()

    def _connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)

    def _cleanup(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM background_jobs WHERE job_type=%s",
                    (next_artwork_trash.ARTWORK_TRASH_PURGE_JOB_TYPE,),
                )
            conn.commit()

    def _queued(self) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*)::int AS total FROM background_jobs WHERE job_type=%s",
                (next_artwork_trash.ARTWORK_TRASH_PURGE_JOB_TYPE,),
            )
            return cur.fetchone()["total"]

    def test_the_first_poll_queues_a_purge(self):
        self.worker._maybe_enqueue_artwork_trash_purge("test-worker")
        self.assertEqual(self._queued(), 1)

    def test_the_next_poll_two_seconds_later_does_not_queue_a_second(self):
        # The whole point of the interval: the loop runs every two seconds and
        # must not turn that into a job every two seconds.
        for _ in range(5):
            self.worker._maybe_enqueue_artwork_trash_purge("test-worker")
        self.assertEqual(self._queued(), 1)

    def test_a_purge_that_already_ran_is_not_repeated_within_the_interval(self):
        self.worker._maybe_enqueue_artwork_trash_purge("test-worker")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE background_jobs SET status='succeeded' WHERE job_type=%s",
                    (next_artwork_trash.ARTWORK_TRASH_PURGE_JOB_TYPE,),
                )
            conn.commit()
        self.worker._maybe_enqueue_artwork_trash_purge("test-worker")
        self.assertEqual(self._queued(), 1)

    def test_the_worker_recognises_the_job_type(self):
        # A job nothing dispatches would sit pending forever, which looks
        # exactly like a purge that has nothing to do.
        result = self.worker.process_job(
            {"id": uuid.uuid4(), "job_type": next_artwork_trash.ARTWORK_TRASH_PURGE_JOB_TYPE, "payload": {}},
            "test-worker",
        )
        self.assertTrue(result["handled"])
        self.assertEqual(result["jobType"], next_artwork_trash.ARTWORK_TRASH_PURGE_JOB_TYPE)
        for key in ("purgedLinks", "purgedAssets", "purgedFiles"):
            self.assertIn(key, result)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
