"""The couple-and-merge trigger: POST .../contributions/merge.

The route mediates a two-sided merge (contribution-v3 §5.7; change spec
2026-08-16-ios-contributions.md §2b.1): the server half is signed server-side by
`merge_instance`, the device half rides in the body as a proof DiscVault cannot
forge. What the route itself owns -- and what these tests pin -- is the shape of
the request it accepts, that it fails closed on the permission gate before any
MovieVault call, and how a failure is reported: `errorCode` is the contract
surface a client branches on, and the status says whether repeating can help.

`merge_unsupported` (#699) is the one terminal code that means "not here" rather
than "refused": the configured MovieVault has no merge operation, nothing was
changed, and the client should stop offering the merge instead of inviting a
retry.
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
    from app.backend.next_common import NextApiError
    from app.backend import next_movievault_v2_contributions as contributions
    from app.backend.next_movievault_v2_contributions import MovieVaultContributionError
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name != "flask":
        raise
    create_app = None
    NextApiError = None
    contributions = None
    MovieVaultContributionError = None

_I_SRV = "11111111-1111-4111-8111-111111111111"
_I_IOS = "22222222-2222-4222-8222-222222222222"
_PROOF = "device-proof"
_PATH = "/api/next/movievault/contributions/merge"
_CONTRIBUTIONS = "app.backend.next_movievault_v2_contributions"
_MERGED = {"status": "completed", "reassociatedCount": 3, "retiredInPlaceCount": 1}


@unittest.skipIf(create_app is None, "Flask is not installed in this test environment")
class ContributionMergeRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()
        self.actor = {
            "id": "00000000-0000-0000-0000-0000000000c1",
            "permissions": ["collection.edit_all"],
        }
        self.conn = MagicMock()
        self.connect_context = MagicMock()
        self.connect_context.__enter__.return_value = self.conn
        self.connect_context.__exit__.return_value = False

    def _post(self, body=None, *, merge=None, permission=None, raw=None):
        """Drive one POST with `merge_instance` and the permission gate mocked.

        `merge` is the canned result, or an exception for the failure paths.
        `merge_instance` is imported lazily inside the route, so it is patched
        on its defining module. `raw` sends a non-JSON-object body verbatim.
        """
        with patch("app.backend.next_app.connect", return_value=self.connect_context), \
                patch(
                    "app.backend.next_app.next_auth_effective_enabled",
                    return_value=False,
                ), \
                patch(f"{_CONTRIBUTIONS}.merge_instance") as merge_instance, \
                patch("app.backend.next_app.require_next_permission") as require_perm:
            if isinstance(merge, BaseException):
                merge_instance.side_effect = merge
            else:
                merge_instance.return_value = _MERGED if merge is None else merge
            if isinstance(permission, BaseException):
                require_perm.side_effect = permission
            else:
                require_perm.return_value = permission or self.actor
            if raw is not None:
                resp = self.client.post(_PATH, data=raw, content_type="application/json")
            else:
                resp = self.client.post(
                    _PATH,
                    json=body
                    if body is not None
                    else {"retiredInstanceId": _I_IOS, "retiredInstanceProof": _PROOF},
                )
            return resp, merge_instance, require_perm

    # --- the request shape ---------------------------------------------------

    def test_a_non_object_body_is_refused(self):
        resp, merge_instance, _ = self._post(raw='["not", "an", "object"]')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "Request body must be an object")
        merge_instance.assert_not_called()

    def test_the_retired_instance_id_is_required(self):
        resp, merge_instance, _ = self._post({"retiredInstanceProof": _PROOF})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "retiredInstanceId is required")
        merge_instance.assert_not_called()

    def test_the_device_proof_is_required(self):
        """The device half missing never reaches the server half, let alone
        MovieVault: 'neither side can merge unilaterally' at the route."""
        resp, merge_instance, _ = self._post({"retiredInstanceId": _I_IOS})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "retiredInstanceProof is required")
        merge_instance.assert_not_called()

    # --- the permission gate -------------------------------------------------

    def test_the_merge_is_gated_on_collection_edit_all(self):
        _, _, require_perm = self._post()
        require_perm.assert_called_once()
        self.assertEqual(require_perm.call_args.args[1], "collection.edit_all")

    def test_the_route_fails_closed_when_the_permission_gate_refuses(self):
        """Adversarial: an unauthorised caller gets no MovieVault call at all,
        so it cannot spend the server's signing half on its own proof."""
        resp, merge_instance, _ = self._post(
            permission=NextApiError("Forbidden", 403, "forbidden"),
        )
        self.assertEqual(resp.status_code, 403)
        merge_instance.assert_not_called()

    # --- success -------------------------------------------------------------

    def test_a_successful_merge_passes_both_fields_through(self):
        resp, merge_instance, _ = self._post()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"status": "ok", "merge": _MERGED})
        merge_instance.assert_called_once()
        self.assertIs(merge_instance.call_args.args[0], self.conn)
        self.assertEqual(merge_instance.call_args.kwargs["retired_instance_id"], _I_IOS)
        self.assertEqual(merge_instance.call_args.kwargs["retired_instance_proof"], _PROOF)

    def test_snake_case_field_names_are_accepted(self):
        resp, merge_instance, _ = self._post(
            {"retired_instance_id": _I_IOS, "retired_instance_proof": _PROOF}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(merge_instance.call_args.kwargs["retired_instance_id"], _I_IOS)
        self.assertEqual(merge_instance.call_args.kwargs["retired_instance_proof"], _PROOF)

    # --- failures ------------------------------------------------------------

    def test_a_retryable_failure_is_a_503_with_its_code(self):
        """A timeout is safe to repeat (the merge is idempotent upstream), so it
        answers 503. An unmapped code gets the generic line, never a raw code
        in `error`."""
        resp, _, _ = self._post(
            merge=MovieVaultContributionError("contribution_network_error", retryable=True)
        )
        self.assertEqual(resp.status_code, 503)
        payload = resp.get_json()
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["errorCode"], "contribution_network_error")
        self.assertEqual(payload["error"], "The identity merge could not be completed")

    def test_a_rejected_proof_is_a_terminal_400_with_its_code(self):
        resp, _, _ = self._post(merge=MovieVaultContributionError("signature_invalid"))
        self.assertEqual(resp.status_code, 400)
        payload = resp.get_json()
        self.assertEqual(payload["errorCode"], "signature_invalid")
        self.assertEqual(payload["error"], "The device merge proof did not verify")

    def test_merge_unsupported_is_a_terminal_400_the_client_can_tell_apart(self):
        """#699: a MovieVault without the merge operation must read as its own
        case, not as the generic refusal a client would offer to retry."""
        resp, _, _ = self._post(
            merge=MovieVaultContributionError("merge_unsupported", status=405)
        )
        self.assertEqual(resp.status_code, 400)
        payload = resp.get_json()
        self.assertEqual(payload["errorCode"], "merge_unsupported")
        self.assertEqual(
            payload["error"],
            "This server's MovieVault cannot merge contributor identities yet;"
            " nothing was changed",
        )

    def test_an_older_movievault_reaches_the_client_as_merge_unsupported(self):
        """End to end through the real `merge_instance`: an older MovieVault's
        405 for the unknown POST becomes `merge_unsupported`, not `http_405`."""
        settings = {
            contributions.INSTANCE_ID_KEY: _I_SRV,
            contributions.TOKEN_KEY: "srv-token",
            contributions.KEY_ID_KEY: "srv-key-id",
            contributions.PRIVATE_KEY_KEY: "unused",
        }
        sent = {}

        def fake_http(url, *, body, headers, timeout_seconds):
            sent["url"] = url
            return 405, b'{"detail":"Method Not Allowed"}'

        with patch("app.backend.next_app.connect", return_value=self.connect_context), \
                patch(
                    "app.backend.next_app.next_auth_effective_enabled",
                    return_value=False,
                ), \
                patch("app.backend.next_app.require_next_permission", return_value=self.actor), \
                patch(
                    f"{_CONTRIBUTIONS}.registration_state",
                    return_value={"registered": True, "instanceId": _I_SRV},
                ), \
                patch(
                    f"{_CONTRIBUTIONS}._setting_value",
                    side_effect=lambda conn, key, default="", **_: settings.get(key, default),
                ), \
                patch(f"{_CONTRIBUTIONS}._decrypt_secret_value", side_effect=lambda value, **_: value), \
                patch(f"{_CONTRIBUTIONS}._signed_headers", return_value={}), \
                patch(f"{_CONTRIBUTIONS}._http", side_effect=fake_http):
            resp = self.client.post(
                _PATH,
                json={"retiredInstanceId": _I_IOS, "retiredInstanceProof": _PROOF},
            )

        self.assertTrue(sent["url"].endswith("/v2/contributions/merge"))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["errorCode"], "merge_unsupported")


if __name__ == "__main__":
    unittest.main()
