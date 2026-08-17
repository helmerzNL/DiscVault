"""The reported effective permission set must equal the enforced one.

`auth-hardening-decisions.md` rule 12 and its "mirror case" addendum: every
surface that *reports* an actor's permissions must report the same set the gates
*enforce*. The gates (`require_next_permission` / `require_any_next_permission`)
apply an owner/`*` bypass when `next_auth_effective_enabled` is false -- on a
single-owner / RBAC-unconfigured instance they accept every permissioned route.

Before this fix the three `effectivePermissionKeys` emitters in `next_auth.py`
(native login response, App-Store-review login, passkey/native exchange) computed
`sorted(user_permissions(conn, user_id))` with no matching bypass, and
`user_permissions` returns `[]` when the RBAC tables are absent. A legitimate
owner was therefore handed an empty set, and an iOS client that gates the
contribution button on `collection.edit_all` drew nothing even though the server
would have accepted the contribution.

The fix is a single shared reporter, `effective_permission_keys_for`, that
mirrors the gate's bypass: report the owner-equivalent full permission catalogue
(concrete keys, including `collection.edit_all`) when auth is not
effective-enabled, and the user's own permissions when it is. These tests pin
its two behaviours and that all three emitters call it.
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
    import flask

    from app.backend import next_auth
    from app.backend import next_common
except ModuleNotFoundError as exc:  # pragma: no cover - minimal environments
    if exc.name not in {"flask", "cbor2", "psycopg", "argon2", "jwt", "segno", "PIL"}:
        raise
    flask = None
    next_auth = None
    next_common = None


# The full permission catalogue an owner is granted by migration
# 005_seed_system_catalog.sql (owner_role CROSS JOIN permissions). The reporter's
# effective-disabled branch must hand back exactly this concrete set.
PERMISSION_CATALOG = (
    "collection.view",
    "collection.add",
    "collection.edit_all",
    "collection.delete_all",
    "collection.import",
    "collection.bulk_edit",
    "metadata.search",
    "metadata.refresh_one",
    "admin.view_settings",
    "users.view",
    "security.toggle_auth",
)


class FakeCursor:
    def __init__(self, connection):
        self._connection = connection
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, query, params=None):
        text = " ".join(str(query).split())
        self._rows = self._connection.answer(text, params)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    """Answers just enough for the reporter and its dependencies.

    ``effective_enabled`` drives ``next_auth_effective_enabled``: when False the
    reporter must take the bypass branch (report the catalogue); when True it must
    fall through to the user's own permissions.
    """

    def __init__(self, *, effective_enabled, rbac_present=True, user_permissions=()):
        self.effective_enabled = effective_enabled
        self.rbac_present = rbac_present
        self._user_permissions = list(user_permissions)
        # Tables the reporter's dependencies probe for.
        self.present = {"app_settings", "users", "passkey_credentials", "permissions"}
        if rbac_present:
            self.present |= {"role_permissions", "user_roles"}

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def answer(self, text, params):
        if "to_regclass" in text:
            name = str((params or ("",))[0]).replace("public.", "")
            return [{"table_name": name if name in self.present else None}]
        # next_auth_configured_enabled reads the auth_enabled setting.
        if "FROM app_settings" in text:
            return [{"value": bool(self.effective_enabled)}]
        # next_auth_ready EXISTS(... ) AS ready
        if "AS ready" in text:
            return [{"ready": bool(self.effective_enabled)}]
        # permission_keys_catalog: SELECT key FROM permissions
        if "FROM permissions" in text and "SELECT key" in text:
            return [{"key": k} for k in PERMISSION_CATALOG]
        # user_permissions: DISTINCT rp.permission_key FROM user_roles JOIN role_permissions
        if "permission_key" in text and "role_permissions" in text:
            return [{"permission_key": k} for k in self._user_permissions]
        return []


def _load_reporter():
    """Register the auth routes on a bare app and return the shared reporter."""

    app = flask.Flask(__name__)

    def connect():  # pragma: no cover - not exercised by these tests
        raise AssertionError("connect must not be called")

    def response(payload, status=200):  # pragma: no cover - not exercised
        return payload, status

    class _Err(Exception):
        def __init__(self, message, status=400):
            super().__init__(message)
            self.status = status

    next_auth.register_next_auth_routes(
        app,
        connect=connect,
        table_exists=next_common.table_exists,
        response=response,
        next_api_error=_Err,
    )
    return app, app.extensions["next_auth"]["effective_permission_keys_for"]


@unittest.skipIf(next_auth is None, "backend dependencies are required")
class EffectivePermissionKeysReporterTests(unittest.TestCase):
    def setUp(self):
        self.app, self.reporter = _load_reporter()

    def _run(self, conn, user_id="00000000-0000-0000-0000-000000000000"):
        with self.app.test_request_context("/api/next/auth/mobile/exchange"):
            return self.reporter(conn, user_id)

    def test_not_effective_enabled_reports_the_full_owner_set(self):
        # (a) On an auth-not-effective-enabled instance the exchange reports a set
        # containing collection.edit_all -- the gates bypass to owner and accept
        # the contribution, so the report must too. Even with the RBAC tables
        # absent (user_permissions would return []), the reporter mirrors the
        # bypass and returns the catalogue.
        conn = FakeConnection(effective_enabled=False, rbac_present=False)
        keys = self._run(conn)
        self.assertIn("collection.edit_all", keys)
        # It is the full catalogue, not the empty role-derived set.
        self.assertEqual(set(keys), set(PERMISSION_CATALOG))
        # Sorted, like the other emitters produced.
        self.assertEqual(keys, sorted(keys))

    def test_effective_enabled_does_not_over_report(self):
        # (b) On an effective-enabled instance a user WITHOUT collection.edit_all
        # still gets a set without it. The full-set path is gated on exactly the
        # same next_auth_effective_enabled == False condition as the gate's
        # bypass, so a narrowly-scoped multi-user actor is reported narrowly.
        conn = FakeConnection(
            effective_enabled=True,
            rbac_present=True,
            user_permissions=("collection.view", "collection.add"),
        )
        keys = self._run(conn)
        self.assertNotIn("collection.edit_all", keys)
        self.assertEqual(keys, ["collection.add", "collection.view"])

    def test_effective_enabled_reports_edit_all_only_when_the_user_has_it(self):
        # The complement of (b): when the effective-enabled user genuinely holds
        # collection.edit_all, it is reported -- reported == enforced, from the
        # other direction.
        conn = FakeConnection(
            effective_enabled=True,
            rbac_present=True,
            user_permissions=("collection.view", "collection.edit_all"),
        )
        keys = self._run(conn)
        self.assertIn("collection.edit_all", keys)


@unittest.skipIf(next_auth is None, "backend dependencies are required")
class EmittersShareTheReporterTests(unittest.TestCase):
    """(c) The shared helper is what all three emitters call -- pinned against
    source so the three sites cannot silently drift back to sorted(user_permissions).
    """

    def setUp(self):
        self.source = (BACKEND / "next_auth.py").read_text(encoding="utf-8")

    def test_every_emitter_assigns_from_the_shared_helper(self):
        assignments = re.findall(
            r"effective_permission_keys = (.+)", self.source
        )
        # Three emitters, plus none from the helper's own name (its def line does
        # not match this pattern).
        self.assertEqual(len(assignments), 3, assignments)
        for rhs in assignments:
            self.assertTrue(
                rhs.startswith("effective_permission_keys_for("),
                f"an emitter no longer routes through the shared reporter: {rhs!r}",
            )

    def test_no_emitter_computes_the_set_inline_from_user_permissions(self):
        # The only sorted(user_permissions(...)) left must be inside the reporter.
        inline = re.findall(
            r"effective_permission_keys = sorted\(user_permissions", self.source
        )
        self.assertEqual(inline, [], "an emitter still computes the set inline")


if __name__ == "__main__":
    unittest.main()
