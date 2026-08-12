"""Token scopes bind: the role and the token must agree, everywhere.

Three properties, and the third is the one that is easy to ship without:

1. A permission gate grants only what both the role and the token allow.
2. A gate that consults no permission at all -- the owner/admin gates behind
   role management, user deletion, invites, the auth toggle, RBAC mode,
   ownership transfer -- refuses a bearer token outright. Without this, "token
   scopes are binding" is untrue for precisely the most dangerous routes, and
   the intersection makes the asymmetry sharper rather than smaller.
3. What a client is *told* it may do matches what it may actually do. A feature
   flag switched on for a route that answers 403 is a broken screen rather than
   a hidden button.
"""

import pathlib
import re
import sys
import unittest


BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from app.backend.next_app import (
        actor_effective_has_any_permission,
        actor_effective_has_permission,
        actor_has_effective_permission,
        actor_token_allows_permission,
    )
    from app.backend.next_auth import (
        MOBILE_AUTH_TOKEN_PERMISSIONS,
        _reconciled_native_token_permissions,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - minimal environments
    if exc.name not in {"flask", "cbor2", "psycopg", "argon2", "jwt", "segno", "PIL"}:
        raise
    actor_effective_has_permission = None


AUTH_SOURCE = (BACKEND / "next_auth.py").read_text(encoding="utf-8")
APP_SOURCE = (BACKEND / "next_app.py").read_text(encoding="utf-8")


@unittest.skipIf(actor_effective_has_permission is None, "backend dependencies are required")
class IntersectionTests(unittest.TestCase):
    @staticmethod
    def _actor(role_keys, token_keys):
        actor = {"role": "media_editor", "permissions": list(role_keys)}
        if token_keys is not None:
            actor["apiToken"] = {"permissionKeys": list(token_keys)}
        return actor

    def test_a_key_both_halves_allow_is_granted(self):
        actor = self._actor(["collection.view"], ["collection.view"])
        self.assertTrue(actor_effective_has_permission(actor, "collection.view"))

    def test_a_key_only_the_role_allows_is_refused(self):
        actor = self._actor(["collection.view"], ["api.read"])
        self.assertFalse(actor_effective_has_permission(actor, "collection.view"))

    def test_a_key_only_the_token_allows_is_refused(self):
        actor = self._actor(["api.read"], ["collection.view"])
        self.assertFalse(actor_effective_has_permission(actor, "collection.view"))

    def test_a_wildcard_role_still_defers_to_the_token(self):
        actor = self._actor(["*"], ["api.read"])
        self.assertTrue(actor_effective_has_permission(actor, "api.read"))
        self.assertFalse(actor_effective_has_permission(actor, "collection.delete_all"))

    def test_a_wildcard_token_defers_to_the_role(self):
        actor = self._actor(["collection.view"], ["*"])
        self.assertTrue(actor_effective_has_permission(actor, "collection.view"))
        self.assertFalse(actor_effective_has_permission(actor, "collection.delete_all"))

    def test_no_actor_is_refused(self):
        self.assertFalse(actor_effective_has_permission(None, "collection.view"))
        self.assertFalse(actor_effective_has_any_permission(None, ("collection.view",)))

    def test_the_two_near_identical_helpers_now_answer_alike(self):
        # actor_has_effective_permission and actor_effective_has_permission
        # differed by one word's position and, until now, by their meaning.
        for role_keys, token_keys, key in (
            (["collection.view"], ["collection.view"], "collection.view"),
            (["collection.view"], ["api.read"], "collection.view"),
            (["api.read"], ["collection.view"], "collection.view"),
            ([], ["collection.view"], "collection.view"),
        ):
            actor = self._actor(role_keys, token_keys)
            self.assertEqual(
                actor_has_effective_permission(actor, key),
                actor_effective_has_permission(actor, key),
                f"the two helpers disagree for role={role_keys} token={token_keys}",
            )

    def test_an_owner_whose_token_is_narrow_is_not_waved_through(self):
        # The old actor_has_effective_permission short-circuited on
        # role == "owner" before consulting the token. The owner role is granted
        # every permission by migration, so the shortcut answered a question the
        # role already answers -- while quietly exempting the owner's tokens.
        owner = {
            "role": "owner",
            "permissions": ["collection.view", "collection.delete_all"],
            "apiToken": {"permissionKeys": ["collection.view"]},
        }
        self.assertTrue(actor_has_effective_permission(owner, "collection.view"))
        self.assertFalse(actor_has_effective_permission(owner, "collection.delete_all"))


@unittest.skipIf(actor_effective_has_permission is None, "backend dependencies are required")
class NonTokenActorsAreUntouchedTests(unittest.TestCase):
    def test_a_session_actor_has_no_token_half(self):
        self.assertTrue(actor_token_allows_permission({"role": "admin"}, "anything"))

    def test_the_synthetic_owner_used_when_auth_is_off_has_no_token_half(self):
        actor = {"id": None, "username": "system", "role": "owner", "permissions": ["*"]}
        self.assertTrue(actor_effective_has_permission(actor, "collection.delete_all"))

    def test_an_empty_token_key_list_is_treated_as_unscoped(self):
        self.assertTrue(actor_token_allows_permission({"apiToken": {"permissionKeys": []}}, "x"))


@unittest.skipIf(actor_effective_has_permission is None, "backend dependencies are required")
class NativeTokenReconciliationTests(unittest.TestCase):
    def test_a_native_row_gains_the_keys_login_would_issue_today(self):
        stored = ["api.read", "collection.add"]
        result = _reconciled_native_token_permissions("ios", stored)
        self.assertIsNotNone(result)
        for key in MOBILE_AUTH_TOKEN_PERMISSIONS:
            self.assertIn(key, result)

    def test_reconciliation_never_drops_a_key_the_row_already_had(self):
        # A native row could carry a key retired from the tuple. Removing it
        # here would be a revocation applied silently on the next request.
        stored = list(MOBILE_AUTH_TOKEN_PERMISSIONS) + ["mcp.tool.search_collection"]
        self.assertIsNone(_reconciled_native_token_permissions("android", stored))

    def test_an_already_current_row_is_left_alone(self):
        # Returning None is what keeps this from writing on every single request
        # once the row is correct.
        self.assertIsNone(
            _reconciled_native_token_permissions("ios", list(MOBILE_AUTH_TOKEN_PERMISSIONS))
        )

    def test_a_user_created_token_is_never_rewritten(self):
        # Its scope is a choice its owner made. Widening it would be the exact
        # opposite of taking the scope seriously.
        self.assertIsNone(_reconciled_native_token_permissions(None, ["api.read"]))
        self.assertIsNone(_reconciled_native_token_permissions("", ["api.read"]))
        self.assertIsNone(_reconciled_native_token_permissions("web", ["api.read"]))

    def test_an_unscoped_row_is_grandfathered_rather_than_narrowed(self):
        # [] currently means full role authority. Writing the native tuple over
        # it would take authority away, which is a revocation, not a repair.
        self.assertIsNone(_reconciled_native_token_permissions("ios", []))


class RoleOnlyGatesRefuseTokensTests(unittest.TestCase):
    """Source-level: the gates that consult no permission must still bar tokens."""

    def test_every_role_only_gate_calls_the_token_bar(self):
        for source, gate in (
            (AUTH_SOURCE, "def require_admin(conn)"),
            (AUTH_SOURCE, "def require_owner(conn)"),
            (APP_SOURCE, "def require_next_admin_user(conn)"),
        ):
            body = source[source.index(gate) : source.index(gate) + 900]
            self.assertIn(
                "deny_api_token_actor(",
                body,
                f"{gate} still answers 'is this an admin' without asking how they arrived",
            )

    def test_the_bar_is_reached_after_the_role_check_not_before(self):
        # Ordering matters for what the caller learns: a non-admin must still be
        # told they are not an admin, rather than being told the route is
        # closed to tokens and thereby learning nothing about their role.
        for source, gate in (
            (AUTH_SOURCE, "def require_admin(conn)"),
            (AUTH_SOURCE, "def require_owner(conn)"),
            (APP_SOURCE, "def require_next_admin_user(conn)"),
        ):
            body = source[source.index(gate) : source.index(gate) + 900]
            self.assertLess(body.index("access required"), body.index("deny_api_token_actor("))

    def test_a_refusal_is_audited_and_committed_before_it_raises(self):
        # Authorisation runs before the route mutates anything, so the raise
        # would otherwise roll the denial event back and leave no trace of it.
        # The two copies write the event differently -- next_app delegates to
        # audit_permission_denied, next_auth has only its local audit_event --
        # so each is checked for the writer it actually reaches.
        auth_body = AUTH_SOURCE[AUTH_SOURCE.index("def deny_api_token_actor(") :][:2600]
        self.assertIn("security.permission_denied", auth_body)

        app_body = APP_SOURCE[APP_SOURCE.index("def deny_api_token_actor(") :][:2600]
        self.assertIn("audit_permission_denied(", app_body)

        for body in (auth_body, app_body):
            self.assertIn("api_token_barred_from_role_only_gate", body)
            self.assertLess(
                body.index("conn.commit()"),
                body.index(", 403)"),
                "the denial event must be committed before the 403 rolls it back",
            )

    def test_an_ordinary_permission_denial_is_also_committed(self):
        # Every caller of audit_permission_denied writes the event and then
        # raises, and the raise leaves `with connect() as conn` by exception --
        # which rolls it back. The event was written and discarded, so no
        # permission refusal reached the audit trail at all. That is the one
        # place that says which key was wanted and which the token carried, and
        # this change produces more refusals, not fewer.
        body = APP_SOURCE[APP_SOURCE.index("def audit_permission_denied(") :][:3000]
        self.assertIn("conn.commit()", body)
        self.assertLess(
            body.index("audit_event("),
            body.index("conn.commit()"),
            "the event has to be written before it is committed",
        )

    def test_the_delegated_writer_emits_the_same_event_type(self):
        # next_app's bar reaches audit_permission_denied rather than naming the
        # event itself; without this the check above would pass on a helper that
        # wrote nothing.
        body = APP_SOURCE[APP_SOURCE.index("def audit_permission_denied(") :][:1200]
        self.assertIn('event_type="security.permission_denied"', body)

    def test_require_authenticated_admin_inherits_the_bar(self):
        body = AUTH_SOURCE[
            AUTH_SOURCE.index("def require_authenticated_admin(conn)") :
        ][:400]
        self.assertIn("require_admin(conn)", body)


class ReportedPermissionsMatchEnforcedOnesTests(unittest.TestCase):
    def test_the_effective_key_set_intersects_rather_than_unions(self):
        body = APP_SOURCE[APP_SOURCE.index("def actor_effective_permission_keys(") :][:1600]
        self.assertIn("permissions & {", body)
        self.assertNotIn("permissions.update(token_permissions)", body)

    def test_the_mcp_catalogue_reports_the_effective_set(self):
        body = APP_SOURCE[APP_SOURCE.index("def mcp_catalog():") :][:1400]
        self.assertIn("actor_effective_permission_keys(conn, actor)", body)

    def test_the_native_bootstrap_reports_the_effective_set(self):
        preferences = (BACKEND / "next_preferences.py").read_text(encoding="utf-8")
        self.assertIn("actor_effective_permission_keys(conn, actor)", preferences)

    def test_mobile_feature_flags_follow_the_same_set(self):
        preferences = (BACKEND / "next_preferences.py").read_text(encoding="utf-8")
        body = preferences[preferences.index("def mobile_feature_capabilities(") :][:400]
        self.assertIn("actor_effective_permission_keys", body)


class AuditAddressTests(unittest.TestCase):
    def test_the_auth_audit_writer_records_the_resolved_address(self):
        # This writer kept its own inline copy of the old "first forwarded hop"
        # rule and so was missed when forwarded headers stopped being believed
        # on their own -- leaving the auth events, the ones that most need a
        # truthful address, choosable by the caller.
        body = AUTH_SOURCE[AUTH_SOURCE.index("    def audit_event(") :][:2600]
        self.assertIn("trusted_client_ip()", body)
        self.assertNotIn('request.headers.get("X-Forwarded-For"', body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
