"""The couple-and-merge merge target: GET .../contributions/identity.

A freshly-coupled standalone device knows only a DiscVault URL, token and role;
it does not know this server's surviving MovieVault instance id `I_srv`. But the
device half of the merge proof (contribution-v3 §5.7; change spec
2026-08-16-ios-contributions.md §2b.1) is a signature over
`merge_proof_message(surviving=I_srv, retired=I_ios)`, so it cannot be built
without `I_srv`. This endpoint hands over that one public identifier -- and
nothing else -- gated on the coupling user's `collection.edit_all`.

These are route-level tests: what the endpoint exposes, what it withholds, and
that it fails closed on the permission gate.
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
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name != "flask":
        raise
    create_app = None
    NextApiError = None

_I_SRV = "11111111-1111-4111-8111-111111111111"
_PATH = "/api/next/movievault/contributions/identity"


@unittest.skipIf(create_app is None, "Flask is not installed in this test environment")
class ContributionIdentityRouteTests(unittest.TestCase):
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

    def _run(self, *, state=None, permission=None):
        """Drive one GET with `registration_state` and the permission gate mocked.

        `permission` may be a canned actor (default) or a callable/exception to
        drive the gate's refusal path. `registration_state` is imported lazily
        inside the route, so it is patched on its defining module.
        """
        perm = patch("app.backend.next_app.require_next_permission")
        with patch("app.backend.next_app.connect", return_value=self.connect_context), \
                patch(
                    "app.backend.next_app.next_auth_effective_enabled",
                    return_value=False,
                ), \
                patch(
                    "app.backend.next_movievault_v2_contributions.registration_state",
                    return_value=state if state is not None else {},
                ), perm as require_perm:
            if isinstance(permission, BaseException):
                require_perm.side_effect = permission
            else:
                require_perm.return_value = permission or self.actor
            resp = self.client.get(_PATH)
            return resp, require_perm

    def test_a_registered_server_returns_its_surviving_instance_id(self):
        """The happy path: the coupling client learns `I_srv` so it can bind its
        merge proof to this server."""
        resp, require_perm = self._run(
            state={"registered": True, "instanceId": _I_SRV}
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["registered"])
        self.assertEqual(payload["instanceId"], _I_SRV)

    def test_the_merge_target_is_gated_on_collection_edit_all(self):
        """The same DiscVault-level evidence the merge itself requires: only a
        user who may edit the whole collection sees the target."""
        _, require_perm = self._run(
            state={"registered": True, "instanceId": _I_SRV}
        )
        require_perm.assert_called_once()
        self.assertEqual(require_perm.call_args.args[1], "collection.edit_all")

    def test_an_unregistered_server_offers_no_target(self):
        """`registered: false` means there is nothing to fold into. The id is
        withheld (null), not faked, and this is a state -- not an error."""
        resp, _ = self._run(state={"registered": False, "instanceId": ""})
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertFalse(payload["registered"])
        self.assertIsNone(payload["instanceId"])

    def test_a_registered_flag_never_leaks_a_stale_id(self):
        """Belt and braces: even if state carried an id while `registered` were
        false, the route reports null -- `registered` is the single gate."""
        resp, _ = self._run(state={"registered": False, "instanceId": _I_SRV})
        self.assertIsNone(resp.get_json()["instanceId"])

    def test_the_route_fails_closed_when_the_permission_gate_refuses(self):
        """Adversarial: an unauthorised caller learns nothing, not even whether
        the server is registered."""
        resp, _ = self._run(
            state={"registered": True, "instanceId": _I_SRV},
            permission=NextApiError("Forbidden", 403, "forbidden"),
        )
        self.assertEqual(resp.status_code, 403)
        body = resp.get_data(as_text=True)
        self.assertNotIn(_I_SRV, body)


if __name__ == "__main__":
    unittest.main()
