"""Who may use the link wizard's two destructive routes (sync-contract §5f, D2).

`sync.replace_library` is the one permission behind `POST /sync/snapshot` and
`POST /sync/retain`. The requirement is "owner or admin only, never on an
instance without authentication, and a token must never exceed its user". The
interesting part is how a token and a role combine, so these tests drive the
real `require_next_library_replace_actor` and `actor_effective_has_permission`
with the role and token halves set independently:

* a token's key list AND the holder's role must both allow a permission, so a
  native token of an ordinary user -- whose list does carry the key -- is still
  refused, because the role lacks it;
* an owner's token works only if its list carries the key (or `*`), so a token
  minted before the key existed is refused until the token reconciler adds it;
* a token whose list is empty is an unscoped legacy row and the role decides
  alone -- stated here because it is the one case where the token half says
  nothing;
* a custom role that an admin granted the key is still refused: the role check
  is explicit and does not depend on the role matrix.

The migration half needs a database with every migration applied (point
`DATABASE_URL` at one); without it that class skips.
"""

import contextlib
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:  # pragma: no cover - minimal environments
    psycopg = None
    dict_row = None

from app.backend import next_app as na
from app.backend.next_common import NextApiError

KEY = "sync.replace_library"
DATABASE_URL = os.environ.get("DATABASE_URL")

# What a native token carries today, plus the new key, as issue_mobile_api_token
# would hand it to an owner or an admin.
NATIVE_LIST = {"api.read", "collection.view", "collection.add", KEY}


def _actor_patches(role, permissions, *, token_keys=None):
    """Patches that make the next request come from this role (and token)."""
    actor = {"id": "user-1", "username": f"{role}-user"}
    if token_keys is not None:
        actor["apiToken"] = {
            "id": "token-1",
            "name": "DiscVault iOS",
            "permissionKeys": sorted(token_keys),
            "scopes": [],
        }
    return [
        patch.object(na, "next_auth_effective_enabled", return_value=True),
        patch.object(na, "next_auth_current_user", return_value=actor),
        patch.object(na, "next_user_primary_role", return_value=role),
        patch.object(na, "next_user_permission_keys", return_value=set(permissions)),
        patch.object(na, "table_exists", return_value=True),
        patch.object(na, "audit_event"),
    ]


def _call(role, permissions, *, token_keys=None, auth_enabled=True):
    conn = MagicMock()
    with contextlib.ExitStack() as stack:
        for ctx in _actor_patches(role, permissions, token_keys=token_keys):
            stack.enter_context(ctx)
        if not auth_enabled:
            stack.enter_context(patch.object(na, "next_auth_effective_enabled", return_value=False))
        with na.app.test_request_context("/api/next/sync/snapshot"):
            return na.require_next_library_replace_actor(conn)


class SessionActorTests(unittest.TestCase):
    def test_an_owner_session_may(self):
        actor = _call("owner", {KEY})
        self.assertEqual(actor["role"], "owner")

    def test_an_admin_session_may(self):
        actor = _call("admin", {KEY})
        self.assertEqual(actor["role"], "admin")

    def test_an_ordinary_role_is_refused_for_the_missing_permission(self):
        for role in ("media_editor", "media_fan", "media_viewer", "member_groups"):
            with self.subTest(role=role):
                with self.assertRaises(NextApiError) as ctx:
                    _call(role, {"collection.view", "collection.add"})
                self.assertEqual(ctx.exception.status_code, 403)
                self.assertEqual(ctx.exception.code, "permission_missing")

    def test_a_custom_role_holding_the_key_is_still_refused(self):
        # An admin may grant any catalogued permission to a custom role. D2 says
        # owner or admin only, so the role itself is checked too.
        with self.assertRaises(NextApiError) as ctx:
            _call("custom_curator", {KEY, "collection.view"})
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.code, "role_not_allowed")

    def test_authentication_switched_off_is_refused(self):
        # With auth off, require_next_permission would return an owner-level
        # system actor to anybody who reaches the server.
        with self.assertRaises(NextApiError) as ctx:
            _call("owner", {KEY}, auth_enabled=False)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.code, "auth_not_enabled")

    def test_nobody_signed_in_is_unauthorized(self):
        conn = MagicMock()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(na, "next_auth_effective_enabled", return_value=True))
            stack.enter_context(patch.object(na, "next_auth_current_user", return_value=None))
            stack.enter_context(patch.object(na, "table_exists", return_value=True))
            with na.app.test_request_context("/api/next/sync/snapshot"):
                with self.assertRaises(NextApiError) as ctx:
                    na.require_next_library_replace_actor(conn)
        self.assertEqual(ctx.exception.status_code, 401)


class TokenActorTests(unittest.TestCase):
    """A token's list and the holder's role must both allow the key."""

    def test_an_owners_native_token_with_the_key_may(self):
        actor = _call("owner", {KEY}, token_keys=NATIVE_LIST)
        self.assertEqual(actor["role"], "owner")

    def test_an_admins_native_token_with_the_key_may(self):
        self.assertEqual(_call("admin", {KEY}, token_keys=NATIVE_LIST)["role"], "admin")

    def test_an_ordinary_users_native_token_is_refused_although_its_list_carries_the_key(self):
        # The token list is the same fixed tuple for everyone; the role half is
        # what refuses. This is "a token never exceeds its user".
        with self.assertRaises(NextApiError) as ctx:
            _call("media_editor", {"collection.view", "collection.add"}, token_keys=NATIVE_LIST)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.code, "permission_missing")

    def test_an_owners_token_without_the_key_is_refused(self):
        # A token minted before the key existed. The reconciler adds the key on
        # the token's next use; until then the token half says no.
        old_list = NATIVE_LIST - {KEY}
        with self.assertRaises(NextApiError) as ctx:
            _call("owner", {KEY}, token_keys=old_list)
        self.assertEqual(ctx.exception.code, "permission_missing")

    def test_an_owners_wildcard_token_may(self):
        self.assertEqual(_call("owner", {KEY}, token_keys={"*"})["role"], "owner")

    def test_an_unscoped_legacy_token_leaves_the_decision_to_the_role(self):
        # permission_keys is NOT NULL DEFAULT '[]', so every token minted before
        # scopes existed has an empty list and is treated as unscoped. The token
        # half says nothing; the role still binds, in both directions.
        self.assertEqual(_call("owner", {KEY}, token_keys=set())["role"], "owner")
        with self.assertRaises(NextApiError) as ctx:
            _call("media_viewer", {"collection.view"}, token_keys=set())
        self.assertEqual(ctx.exception.code, "permission_missing")

    def test_a_scoped_token_of_a_custom_role_holding_the_key_is_refused(self):
        with self.assertRaises(NextApiError) as ctx:
            _call("custom_curator", {KEY}, token_keys=NATIVE_LIST)
        self.assertEqual(ctx.exception.code, "role_not_allowed")


class ProfileTokenTests(unittest.TestCase):
    """The profile page cannot mint a token that carries the key."""

    def test_even_an_owner_cannot_grant_it_to_a_token_they_create(self):
        from app.backend import next_api_token

        owner = {"id": "user-1", "role": "owner", "permissions": [KEY]}
        with patch.object(na, "permission_key_catalog", return_value={KEY, "api.read"}):
            with self.assertRaises(NextApiError) as ctx:
                next_api_token.normalize_api_token_permissions(MagicMock(), owner, [KEY])
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("not valid for API tokens", str(ctx.exception))

    def test_a_wildcard_is_not_a_way_around_it(self):
        from app.backend import next_api_token

        owner = {"id": "user-1", "role": "owner", "permissions": [KEY]}
        with patch.object(na, "permission_key_catalog", return_value={KEY, "api.read"}):
            with self.assertRaises(NextApiError):
                next_api_token.normalize_api_token_permissions(MagicMock(), owner, ["*"])


class ReconcilerTests(unittest.TestCase):
    """Tokens minted before the key existed pick it up on their next use."""

    def test_an_older_native_token_gains_the_key(self):
        from app.backend import next_auth

        old_list = ["api.read", "collection.view", "collection.add"]
        reconciled = next_auth._reconciled_native_token_permissions("ios", old_list)
        self.assertIn(KEY, reconciled)
        # The reconciler is the same for everybody, which is why the role half
        # matters: an ordinary user's token gains the key too and stays refused.
        self.assertEqual(
            next_auth._reconciled_native_token_permissions("android", old_list), reconciled
        )

    def test_an_unscoped_legacy_token_is_left_alone(self):
        from app.backend import next_auth

        self.assertIsNone(next_auth._reconciled_native_token_permissions("ios", []))

    def test_a_token_that_is_not_native_is_left_alone(self):
        from app.backend import next_auth

        self.assertIsNone(next_auth._reconciled_native_token_permissions(None, ["api.read"]))


class AuditTests(unittest.TestCase):
    def test_a_role_refusal_is_audited(self):
        conn = MagicMock()
        with contextlib.ExitStack() as stack:
            for ctx in _actor_patches("custom_curator", {KEY}):
                stack.enter_context(ctx)
            audit = stack.enter_context(patch.object(na, "audit_permission_denied"))
            with na.app.test_request_context("/api/next/sync/retain"):
                with self.assertRaises(NextApiError):
                    na.require_next_library_replace_actor(conn)
        audit.assert_called_once()
        self.assertEqual(audit.call_args.kwargs["reason"], "library_replace_needs_owner_or_admin")


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class MigrationSeedTests(unittest.TestCase):
    """Migration 096 is the only thing that decides which roles hold the key."""

    def setUp(self):
        self.conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        self.addCleanup(self.conn.close)

    def test_the_permission_is_in_the_catalogue(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT domain FROM permissions WHERE key=%s", (KEY,))
            row = cur.fetchone()
        self.assertIsNotNone(row, "migration 096 did not register the permission")
        self.assertEqual(row["domain"], "sync")

    def test_only_owner_and_admin_hold_it(self):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.key
                FROM role_permissions rp
                JOIN roles r ON r.id = rp.role_id
                WHERE rp.permission_key = %s
                ORDER BY r.key
                """,
                (KEY,),
            )
            holders = [row["key"] for row in cur.fetchall()]
        self.assertEqual(holders, ["admin", "owner"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
