import unittest
from unittest.mock import MagicMock, patch

from app.backend import next_app
from app.backend import next_worker


class PersonMetadataRefreshWorkerTests(unittest.TestCase):
    def test_process_job_dispatches_person_metadata_refresh(self):
        payload = {"personId": "00000000-0000-0000-0000-000000000031"}
        with patch.object(
            next_worker,
            "process_person_metadata_refresh",
            return_value={"handled": True},
        ) as process:
            result = next_worker.process_job(
                {"job_type": next_worker.PERSON_METADATA_REFRESH_JOB_TYPE, "payload": payload},
                "worker-1",
            )

        self.assertEqual(result, {"handled": True})
        process.assert_called_once_with(payload, "worker-1")

    def test_person_metadata_refresh_uses_tmdb_refresh_pipeline(self):
        person_id = "00000000-0000-0000-0000-000000000031"
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.__exit__.return_value = False
        with (
            patch.object(next_worker, "connect", return_value=connection),
            patch.object(next_app, "refresh_person_metadata", return_value={"updated": True}) as refresh,
        ):
            result = next_worker.process_person_metadata_refresh(
                {"personId": person_id, "requestedBy": {"id": "admin"}},
                "worker-1",
            )

        refresh.assert_called_once()
        self.assertEqual(str(refresh.call_args.args[1]), person_id)
        self.assertFalse(refresh.call_args.kwargs["dry_run"])
        self.assertEqual(refresh.call_args.kwargs["actor"], {"id": "admin"})
        self.assertEqual(result["personId"], person_id)
        self.assertEqual(result["result"], {"updated": True})

    def test_person_metadata_refresh_rejects_invalid_uuid(self):
        with self.assertRaisesRegex(RuntimeError, "personId must be a valid UUID"):
            next_worker.process_person_metadata_refresh(
                {"personId": "not-a-uuid"},
                "worker-1",
            )


class _Clock:
    """Monotonic clock that only advances when the code under test sleeps."""

    def __init__(self):
        self.now = 0.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class _FakeConn:
    def __init__(self, ready: bool):
        self.ready = ready
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def cursor(self):
        return self

    def __call__(self, *a, **k):
        return self

    def execute(self, *a, **k):
        pass

    def fetchone(self):
        return {"table_name": "background_jobs"} if self.ready else None


class WaitForBackgroundJobsTableTests(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.logged = []

    def _wait(self, connect_fn, **kwargs):
        next_worker.wait_for_background_jobs_table(
            connect_fn,
            sleep=self.clock.sleep,
            monotonic=self.clock.monotonic,
            log=self.logged.append,
            **kwargs,
        )

    def test_returns_immediately_when_the_table_already_exists(self):
        connect_fn = lambda: _FakeConn(ready=True)

        self._wait(connect_fn, timeout=30)

        self.assertEqual(self.clock.slept, [])
        self.assertEqual(self.logged, [])

    def test_polls_with_backoff_until_the_table_appears(self):
        conns = [_FakeConn(ready=False), _FakeConn(ready=False), _FakeConn(ready=True)]

        def connect_fn():
            return conns.pop(0)

        self._wait(connect_fn, timeout=30)

        self.assertEqual(self.clock.slept, [0.5, 1.0])
        self.assertEqual(len(self.logged), 1)
        self.assertIn("background_jobs", self.logged[0])

    def test_each_poll_closes_its_connection(self):
        opened = []

        def connect_fn():
            conn = _FakeConn(ready=len(opened) >= 2)
            opened.append(conn)
            return conn

        self._wait(connect_fn, timeout=30)

        self.assertTrue(all(c.closed for c in opened))

    def test_gives_up_after_the_timeout_without_raising(self):
        connect_fn = lambda: _FakeConn(ready=False)

        # A missing table is not a startup failure; work_loop() already
        # tolerates it via run_once()'s "waiting" status.
        self._wait(connect_fn, timeout=10)

        self.assertLessEqual(self.clock.now, 10)
        self.assertIn("Gave up waiting", self.logged[-1])


if __name__ == "__main__":
    unittest.main()
