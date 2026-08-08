import os
import sys
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_metadata


class RefreshMovieMetadataTransactionBoundaryTests(unittest.TestCase):
    """Applying a metadata proposal locks the movie rows and the single global
    sync_state row (record_sync_change). Those locks must be committed away
    BEFORE the receiver-plugin push runs its network I/O: holding them across
    a slow plugin call blocks every other writer until the API lock_timeout
    fires, which used to surface as a bogus "DiscVault is temporarily
    offline" error during metadata refreshes."""

    def _run_refresh(self, *, changed: bool):
        calls = []
        conn = MagicMock()
        conn.commit.side_effect = lambda: calls.append("commit")
        preview = {"movie": {"title": "Example", "barcode": "", "format": ""}, "proposal": {}}
        applied = {"changed": changed, "revision": 7 if changed else 0, "applied": {}}

        def record(name, value=None):
            def _inner(*args, **kwargs):
                calls.append(name)
                return value

            return _inner

        with (
            patch.object(next_metadata, "preview_movie_metadata", record("preview", preview)),
            patch.object(next_metadata, "apply_metadata_proposal", record("apply", applied)),
            patch.object(next_metadata, "insert_metadata_audit_event", record("audit")),
            patch.object(next_metadata, "metadata_fetch_audit_payload", record("payload", {})),
            patch.object(
                next_metadata,
                "push_metadata_to_receivers",
                record("push", {"receiverCount": 1, "receivers": []}),
            ),
        ):
            result = next_metadata.refresh_movie_metadata(conn, uuid4(), dry_run=False, actor={})
        return calls, result

    def test_apply_is_committed_before_the_receiver_push_runs(self):
        calls, result = self._run_refresh(changed=True)
        self.assertIn("push", calls)
        self.assertLess(
            calls.index("commit"),
            calls.index("push"),
            "the applied proposal (and its sync_state lock) must be committed before receiver network I/O",
        )
        self.assertTrue(result["applied"]["changed"])

    def test_apply_is_committed_even_when_nothing_changed(self):
        calls, result = self._run_refresh(changed=False)
        self.assertNotIn("push", calls)
        self.assertIn("commit", calls)
        self.assertTrue(result["receivers"]["skipped"])

    def test_dry_run_does_not_commit(self):
        conn = MagicMock()
        preview = {"movie": {"title": "Example"}, "proposal": {}}
        with (
            patch.object(next_metadata, "preview_movie_metadata", return_value=preview),
            patch.object(next_metadata, "insert_metadata_audit_event"),
            patch.object(next_metadata, "metadata_fetch_audit_payload", return_value={}),
        ):
            result = next_metadata.refresh_movie_metadata(conn, uuid4(), dry_run=True, actor={})
        conn.commit.assert_not_called()
        self.assertTrue(result["dryRun"])


if __name__ == "__main__":
    unittest.main()
