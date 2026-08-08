"""The four typed operations a client contributes through.

`docs/contracts/contribution-v2.md` puts every client - the PWA and both native
apps - behind its own DiscVault instance, with the credentials and the judgement
staying server-side. These tests hold that line: what is asserted here is mostly
what the endpoints refuse to take from the caller.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_app import create_app
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name != "flask":
        raise
    create_app = None

MOVIE_ID = "00000000-0000-0000-0000-0000000000a1"
CONTAINER_ID = "00000000-0000-0000-0000-0000000000b1"

PREVIEW = {
    "mode": "correction",
    "target": {"entityType": "release", "entityId": "c0000000-0000-0000-0000-000000000001", "baseRevision": 12},
    "changes": [
        {"field": "edition", "expected": "Theatrical", "proposed": "Director's Cut"},
        {"field": "format", "expected": "Blu-ray", "proposed": "4K UHD"},
    ],
    "withheld": {"region": "different_field_upstream"},
}


@unittest.skipIf(create_app is None, "Flask is not installed in this test environment")
class CorrectionRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()
        self.actor = {"id": "00000000-0000-0000-0000-0000000000c1", "permissions": ["collection.edit_all"]}
        self.conn = MagicMock()
        self.connect_context = MagicMock()
        self.connect_context.__enter__.return_value = self.conn
        self.connect_context.__exit__.return_value = False

    def _patches(self, *, enabled=True, owner_enabled=True, preview=None, movie=None, container=None, job=None):
        return (
            patch("app.backend.next_app.connect", return_value=self.connect_context),
            patch("app.backend.next_app.next_auth_effective_enabled", return_value=False),
            patch("app.backend.next_app.require_next_permission", return_value=self.actor),
            patch("app.backend.next_app.movie_entity", return_value=movie if movie is not None else {"id": MOVIE_ID, "metadata": {}}),
            patch("app.backend.next_app.container_entity", return_value=container),
            patch("app.backend.next_app.actor_can_edit_visible_movie", return_value=True),
            patch("app.backend.next_app.field_correction_enabled", return_value=enabled),
            patch(
                "app.backend.next_app.field_correction_gate",
                return_value={"owner": owner_enabled, "user": enabled},
            ),
            patch("app.backend.next_app.correction_preview", return_value=preview if preview is not None else PREVIEW),
            patch("app.backend.next_app.queue_field_correction_job", return_value=job),
        )

    def _run(self, call, **kwargs):
        patches = self._patches(**kwargs)
        for item in patches:
            item.start()
        try:
            return call()
        finally:
            for item in patches:
                item.stop()

    # ---- eligibility ---------------------------------------------------

    def test_eligibility_names_the_changed_fields_without_the_diff(self):
        """It runs on every detail render, and a button only needs to know
        whether there is anything to send."""
        response = self._run(
            lambda: self.client.get(f"/api/next/movievault/contributions/eligibility?entity=movie&id={MOVIE_ID}")
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["changedFields"], ["edition", "format"])
        self.assertNotIn("changes", payload)

    def test_the_two_gate_halves_are_reported_apart(self):
        """A client that only learns "not enabled" cannot tell the half it can
        act on from the half it cannot. It offered "Agree and send" while the
        owner flag was off, and the send was then refused.

        The halves are reported so the client can *explain* which one is off,
        not so it can hide itself: hiding the button when the owner half was
        off removed the only clue on an instance where the person looking at
        the screen is usually the owner who can flip it.
        """
        response = self._run(
            lambda: self.client.get(f"/api/next/movievault/contributions/eligibility?entity=movie&id={MOVIE_ID}"),
            enabled=False,
            owner_enabled=False,
        )
        payload = response.get_json()
        self.assertFalse(payload["ownerEnabled"])
        self.assertFalse(payload["userEnabled"])
        self.assertFalse(payload["enabled"])
        # Still reported as correctable, so the client has something to draw
        # and something to explain.
        self.assertEqual(payload["mode"], "correction")
        self.assertEqual(payload["changedFields"], ["edition", "format"])

    def test_eligibility_reports_the_preference_without_enforcing_it(self):
        """The button is drawn before the user has ever agreed, because
        agreeing is what the first-run sheet is for. Hiding it until the
        preference was on would leave no way to reach that sheet."""
        response = self._run(
            lambda: self.client.get(f"/api/next/movievault/contributions/eligibility?entity=movie&id={MOVIE_ID}"),
            enabled=False,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["enabled"])
        self.assertEqual(response.get_json()["changedFields"], ["edition", "format"])

    def test_a_record_with_no_upstream_target_is_not_correctable(self):
        response = self._run(
            lambda: self.client.get(f"/api/next/movievault/contributions/eligibility?entity=movie&id={MOVIE_ID}"),
            preview={"mode": "proposal", "target": None, "changes": [], "withheld": {}},
        )
        self.assertEqual(response.get_json()["mode"], "proposal")
        self.assertEqual(response.get_json()["changedFields"], [])

    def test_an_unknown_entity_is_refused(self):
        response = self._run(
            lambda: self.client.get(f"/api/next/movievault/contributions/eligibility?entity=person&id={MOVIE_ID}")
        )
        self.assertEqual(response.status_code, 400)

    # ---- the container gate -------------------------------------------

    def test_only_a_box_set_can_be_corrected(self):
        """A vault and a collection are personal organisation, not catalogue
        facts. There is nothing upstream for them to correct."""
        for container_type in ("vault", "collection"):
            with self.subTest(container_type=container_type):
                response = self._run(
                    lambda: self.client.get(
                        f"/api/next/movievault/contributions/eligibility?entity=container&id={CONTAINER_ID}"
                    ),
                    container={"id": CONTAINER_ID, "container_type": container_type, "metadata": {}},
                )
                self.assertEqual(response.status_code, 409)

    def test_a_box_set_is_allowed_through(self):
        response = self._run(
            lambda: self.client.get(
                f"/api/next/movievault/contributions/eligibility?entity=container&id={CONTAINER_ID}"
            ),
            container={"id": CONTAINER_ID, "container_type": "box_set", "metadata": {}},
        )
        self.assertEqual(response.status_code, 200)

    def test_a_missing_container_is_a_404_rather_than_a_409(self):
        response = self._run(
            lambda: self.client.get(
                f"/api/next/movievault/contributions/eligibility?entity=container&id={CONTAINER_ID}"
            ),
            container=None,
        )
        self.assertEqual(response.status_code, 404)

    # ---- submit --------------------------------------------------------

    def test_submit_recomputes_the_diff_instead_of_trusting_the_client(self):
        """`expected` is the entire conflict check on MovieVault's side. A
        client permitted to state its own would be permitted to state one it
        never saw, and the check would pass against a value nobody read.
        """
        captured = {}

        def _preview(conn, *, entity, record, metadata=None, fields=None):
            captured["fields"] = fields
            return PREVIEW

        with patch("app.backend.next_app.connect", return_value=self.connect_context), patch(
            "app.backend.next_app.next_auth_effective_enabled", return_value=False
        ), patch("app.backend.next_app.require_next_permission", return_value=self.actor), patch(
            "app.backend.next_app.movie_entity", return_value={"id": MOVIE_ID, "metadata": {}}
        ), patch(
            "app.backend.next_app.actor_can_edit_visible_movie", return_value=True
        ), patch(
            "app.backend.next_app.field_correction_enabled", return_value=True
        ), patch(
            "app.backend.next_app.correction_preview", _preview
        ), patch(
            "app.backend.next_app.queue_field_correction_job", return_value={"id": "job-1"}
        ) as queue:
            response = self.client.post(
                "/api/next/movievault/contributions/submit",
                json={
                    "entity": "movie",
                    "id": MOVIE_ID,
                    "fields": ["edition"],
                    # All of this is ignored. It is here because a client could
                    # send it, not because one should.
                    "changes": [{"field": "runtimeMinutes", "expected": "anything", "proposed": "999"}],
                    "target": {"entityId": "somebody-elses-record", "baseRevision": 1},
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["fields"], ["edition"])
        queued = queue.call_args[0][1]
        self.assertEqual(queued["changes"], PREVIEW["changes"])
        self.assertEqual(queued["target"], PREVIEW["target"])

    def test_submit_refuses_when_the_user_has_not_agreed(self):
        response = self._run(
            lambda: self.client.post(
                "/api/next/movievault/contributions/submit", json={"entity": "movie", "id": MOVIE_ID}
            ),
            enabled=False,
        )
        self.assertEqual(response.status_code, 403)

    def test_submit_refuses_when_there_is_nothing_left_to_correct(self):
        """The record may have changed between the sheet being drawn and the
        button being pressed."""
        response = self._run(
            lambda: self.client.post(
                "/api/next/movievault/contributions/submit", json={"entity": "movie", "id": MOVIE_ID}
            ),
            preview={**PREVIEW, "changes": []},
        )
        self.assertEqual(response.status_code, 409)

    def test_submit_refuses_a_record_with_no_upstream_target(self):
        response = self._run(
            lambda: self.client.post(
                "/api/next/movievault/contributions/submit", json={"entity": "movie", "id": MOVIE_ID}
            ),
            preview={"mode": "proposal", "target": None, "changes": [], "withheld": {}},
        )
        self.assertEqual(response.status_code, 409)

    def test_submit_returns_the_job_handle_the_status_route_reads(self):
        response = self._run(
            lambda: self.client.post(
                "/api/next/movievault/contributions/submit", json={"entity": "movie", "id": MOVIE_ID}
            ),
            job={"id": "job-1"},
        )
        self.assertEqual(response.get_json()["jobId"], "job-1")
        self.assertTrue(response.get_json()["queued"])

    def test_a_non_list_field_selection_is_refused(self):
        response = self._run(
            lambda: self.client.post(
                "/api/next/movievault/contributions/submit",
                json={"entity": "movie", "id": MOVIE_ID, "fields": "edition"},
            )
        )
        self.assertEqual(response.status_code, 400)

    # ---- status --------------------------------------------------------

    def test_status_reports_the_job_and_the_moderation_outcome(self):
        job = {
            "id": "job-1",
            "status": "completed",
            "attempts": 2,
            "result": {
                "contributionId": "c-1",
                "status": "partially_accepted",
                "canonicalTargetId": "t-1",
                "duplicateOf": None,
            },
        }
        with patch("app.backend.next_app.connect", return_value=self.connect_context), patch(
            "app.backend.next_app.next_auth_effective_enabled", return_value=False
        ), patch("app.backend.next_app.require_next_permission", return_value=self.actor), patch(
            "app.backend.next_app.background_job_entity", return_value=job
        ):
            response = self.client.get(
                "/api/next/movievault/contributions/status?jobId=00000000-0000-0000-0000-0000000000d1"
            )
        payload = response.get_json()
        self.assertEqual(payload["jobStatus"], "completed")
        self.assertEqual(payload["contributionStatus"], "partially_accepted")
        self.assertEqual(payload["contributionId"], "c-1")

    def test_status_requires_a_job_id(self):
        with patch("app.backend.next_app.connect", return_value=self.connect_context), patch(
            "app.backend.next_app.next_auth_effective_enabled", return_value=False
        ), patch("app.backend.next_app.require_next_permission", return_value=self.actor):
            response = self.client.get("/api/next/movievault/contributions/status")
        self.assertEqual(response.status_code, 400)

    def test_an_unknown_job_is_a_404(self):
        with patch("app.backend.next_app.connect", return_value=self.connect_context), patch(
            "app.backend.next_app.next_auth_effective_enabled", return_value=False
        ), patch("app.backend.next_app.require_next_permission", return_value=self.actor), patch(
            "app.backend.next_app.background_job_entity", return_value=None
        ):
            response = self.client.get(
                "/api/next/movievault/contributions/status?jobId=00000000-0000-0000-0000-0000000000d1"
            )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
