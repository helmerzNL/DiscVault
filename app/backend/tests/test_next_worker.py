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


if __name__ == "__main__":
    unittest.main()
