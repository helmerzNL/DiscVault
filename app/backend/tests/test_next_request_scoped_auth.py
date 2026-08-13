"""One request, one answer: the fixed cost every request paid twice.

Measured against the beta instance, the endpoint that does the least work --
``/api/next/sync/state``, which authenticates and reads one integer -- cost
about as much as the ones that do the most. What an endpoint nominally does did
not explain its cost; the fixed overhead did.

Counting round trips on a warm, authenticated request found where it went. Three
questions were asked over and over for answers that cannot move while the
request is in flight:

* **"Does this table exist?"** ``to_regclass``, 103 times out of 151 queries on
  ``/api/next/app/snapshot`` and 20 out of 32 on the collection list. The schema
  does not change between the first call and the last.
* **"Is authentication on?"** Two queries -- the setting, then an ``EXISTS``
  over ``users`` joined against the credential tables -- run three times.
* **"Who is calling?"** Resolved once by the ``before_request`` gate to decide
  whether to answer 401, then again by the handler on a second connection, from
  the same unchanged header. On a bearer token that was not even a pure read:
  each resolution stamps ``last_used_at``, so a plain GET wrote to
  ``api_access_tokens`` twice.

After: 151 -> 76 queries on the snapshot, 32 -> 19 on the collection list, and
one token write per request instead of two.

The tests below are mostly about the *limits* of those memos, because that is
where a caching change turns into a correctness bug. A memo on the connection
must not outlive the connection; a memo in ``flask.g`` must not outlive the
request; a failed sign-in must not be remembered as "nobody is here"; and a
caller writing ``role`` onto the actor it was handed must not write it into
everyone else's copy.
"""

import pathlib
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
    next_common = None
    next_auth = None


class FakeCursor:
    def __init__(self, connection):
        self._connection = connection
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, query, params=None):
        text = " ".join(str(query).split())
        self._connection.queries.append(text)
        self._row = self._connection.answer(text, params)

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [self._row] if self._row else []


class FakeConnection:
    """A connection that records what was asked of it and answers plausibly."""

    def __init__(self, *, present=(), user_row=None):
        self.queries: list[str] = []
        self.present = set(present)
        self.user_row = user_row
        self.committed = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed += 1

    def answer(self, text, params):
        if "to_regclass" in text:
            name = str((params or ("",))[0]).replace("public.", "")
            return {"table_name": name if name in self.present else None}
        if "FROM api_access_tokens" in text:
            return dict(self.user_row) if self.user_row else None
        if "FROM users" in text and "SELECT id" in text:
            return dict(self.user_row) if self.user_row else None
        if "AS ready" in text:
            return {"ready": True}
        if "FROM app_settings" in text:
            return {"value": True}
        return None


class SlottedStandIn:
    """Refuses attributes, the way a stand-in connection object might."""

    __slots__ = ()

    def cursor(self):  # pragma: no cover - only the attribute refusal matters
        raise AssertionError("not called")


@unittest.skipIf(next_common is None, "backend dependencies are required")
class TableExistenceMemoTests(unittest.TestCase):
    def test_the_same_table_is_only_asked_about_once(self):
        conn = FakeConnection(present={"movies"})
        self.assertTrue(next_common.table_exists(conn, "movies"))
        self.assertTrue(next_common.table_exists(conn, "movies"))
        self.assertTrue(next_common.table_exists(conn, "movies"))
        self.assertEqual(len(conn.queries), 1, conn.queries)

    def test_an_absent_table_is_remembered_too(self):
        # The negative answer is the one most of the 103 calls got: a helper
        # asking whether an optional table is present before skipping it.
        conn = FakeConnection(present=set())
        self.assertFalse(next_common.table_exists(conn, "price_alerts"))
        self.assertFalse(next_common.table_exists(conn, "price_alerts"))
        self.assertEqual(len(conn.queries), 1, conn.queries)

    def test_different_tables_are_asked_about_separately(self):
        conn = FakeConnection(present={"movies"})
        self.assertTrue(next_common.table_exists(conn, "movies"))
        self.assertFalse(next_common.table_exists(conn, "containers"))
        self.assertEqual(len(conn.queries), 2)

    def test_the_memo_belongs_to_one_connection(self):
        # This is what keeps a schema change visible: connections are opened per
        # request, so the request after a migration asks again.
        first = FakeConnection(present=set())
        self.assertFalse(next_common.table_exists(first, "series"))
        second = FakeConnection(present={"series"})
        self.assertTrue(
            next_common.table_exists(second, "series"),
            "a fresh connection must not inherit another connection's answer",
        )

    def test_forgetting_makes_it_ask_again(self):
        conn = FakeConnection(present=set())
        self.assertFalse(next_common.table_exists(conn, "series"))
        conn.present.add("series")
        next_common.forget_table_existence(conn)
        self.assertTrue(next_common.table_exists(conn, "series"))
        self.assertEqual(len(conn.queries), 2)

    def test_an_object_that_refuses_attributes_still_gets_an_answer(self):
        # The metadata pipeline is deliberately callable with a connection-shaped
        # stand-in. A cache must never be the reason something fails, so an
        # object that cannot hold the memo simply goes without one.
        self.assertIsNone(next_common._table_exists_cache(SlottedStandIn()))
        next_common.forget_table_existence(SlottedStandIn())  # must not raise

    def test_the_auth_and_metadata_copies_share_this_memo(self):
        # Both modules carried their own identical implementation, so token
        # authentication and every metadata refresh ran beside the memo rather
        # than filling it.
        from app.backend import next_metadata

        conn = FakeConnection(present={"users"})
        self.assertTrue(next_auth._auth_table_exists(conn, "users"))
        self.assertTrue(next_metadata.table_exists(conn, "users"))
        self.assertTrue(next_common.table_exists(conn, "users"))
        self.assertEqual(len(conn.queries), 1, conn.queries)


TOKEN_ROW = {
    "id": "11111111-1111-1111-1111-111111111111",
    "username": "probe",
    "display_name": "Probe",
    "first_name": None,
    "last_name": None,
    "status": "active",
    "created_at": None,
    "updated_at": None,
    "api_token_id": "22222222-2222-2222-2222-222222222222",
    "api_token_name": "probe token",
    "api_token_scopes": ["read"],
    "api_token_client_kind": None,
    "api_token_permission_keys": ["api.read"],
}


@unittest.skipIf(next_common is None, "backend dependencies are required")
class RequestScopedActorTests(unittest.TestCase):
    def setUp(self):
        self.app = flask.Flask(__name__)

    def _token_request(self):
        return self.app.test_request_context(
            "/api/next/collection",
            headers={"Authorization": f"Bearer {next_auth.API_TOKEN_PREFIX}probe"},
        )

    def test_the_caller_is_resolved_once_per_request(self):
        conn = FakeConnection(present={"api_access_tokens", "users"}, user_row=TOKEN_ROW)
        with self._token_request():
            first = next_auth.next_auth_current_user(conn)
            second = next_auth.next_auth_current_user(conn)
        self.assertIsNotNone(first)
        self.assertEqual(first["username"], second["username"])
        selects = [q for q in conn.queries if "FROM api_access_tokens" in q]
        self.assertEqual(len(selects), 1, conn.queries)

    def test_the_token_is_stamped_once_rather_than_twice(self):
        # last_used_at and last_seen_ip are still recorded -- a read request just
        # stops writing them twice.
        conn = FakeConnection(present={"api_access_tokens", "users"}, user_row=TOKEN_ROW)
        with self._token_request():
            next_auth.next_auth_current_user(conn)
            next_auth.next_auth_current_user(conn)
        updates = [q for q in conn.queries if q.startswith("UPDATE api_access_tokens")]
        self.assertEqual(len(updates), 1, conn.queries)

    def test_the_next_request_resolves_again(self):
        row = dict(TOKEN_ROW)
        first_conn = FakeConnection(present={"api_access_tokens", "users"}, user_row=row)
        with self._token_request():
            next_auth.next_auth_current_user(first_conn)
        second_conn = FakeConnection(present={"api_access_tokens", "users"}, user_row=row)
        with self._token_request():
            next_auth.next_auth_current_user(second_conn)
        self.assertTrue(
            any("FROM api_access_tokens" in q for q in second_conn.queries),
            "a revoked token must stop working on the very next request",
        )

    def test_a_caller_writing_role_does_not_write_it_for_everyone(self):
        # require_admin does exactly this: user["role"] = role. A shared dict
        # would leak one gate's conclusion into the next gate's input.
        conn = FakeConnection(present={"api_access_tokens", "users"}, user_row=TOKEN_ROW)
        with self._token_request():
            first = next_auth.next_auth_current_user(conn)
            first["role"] = "owner"
            first["permissions"] = ["everything"]
            second = next_auth.next_auth_current_user(conn)
        self.assertNotIn("role", second)
        self.assertNotIn("permissions", second)

    def test_no_credential_resolves_to_nobody_without_querying(self):
        conn = FakeConnection(present={"api_access_tokens", "users"})
        with self.app.test_request_context("/api/next/collection"):
            self.assertIsNone(next_auth.next_auth_current_user(conn))
        self.assertEqual(conn.queries, [])

    def test_a_failed_resolution_is_not_remembered(self):
        # Only success is cached. Remembering "nobody" is the direction that
        # could outlive a sign-in happening inside the same request.
        conn = FakeConnection(present={"api_access_tokens", "users"}, user_row=None)
        with self._token_request():
            self.assertIsNone(next_auth.next_auth_current_user(conn))
            conn.user_row = TOKEN_ROW
            self.assertIsNotNone(next_auth.next_auth_current_user(conn))

    def test_forgetting_the_actor_makes_it_resolve_again(self):
        conn = FakeConnection(present={"api_access_tokens", "users"}, user_row=TOKEN_ROW)
        with self._token_request():
            next_auth.next_auth_current_user(conn)
            next_auth.forget_request_actor()
            next_auth.next_auth_current_user(conn)
        selects = [q for q in conn.queries if "FROM api_access_tokens" in q]
        self.assertEqual(len(selects), 2)

    def test_outside_a_request_nothing_is_cached_and_nothing_raises(self):
        # The background worker calls into this module with no request context.
        self.assertIsNone(next_auth._remembered_request_actor())
        next_auth._remember_request_actor({"id": "x"})  # must not raise
        next_auth.forget_request_actor()  # must not raise
        next_auth.forget_auth_enabled()  # must not raise


@unittest.skipIf(next_common is None, "backend dependencies are required")
class AuthEnabledMemoTests(unittest.TestCase):
    def setUp(self):
        self.app = flask.Flask(__name__)
        self.present = {"app_settings", "users", "passkey_credentials"}

    def test_it_is_answered_once_per_request(self):
        conn = FakeConnection(present=self.present)
        with self.app.test_request_context("/api/next/collection"):
            self.assertTrue(next_auth.next_auth_effective_enabled(conn, next_common.table_exists))
            self.assertTrue(next_auth.next_auth_effective_enabled(conn, next_common.table_exists))
        readiness = [q for q in conn.queries if "AS ready" in q]
        self.assertEqual(len(readiness), 1, conn.queries)

    def test_it_spans_the_two_connections_one_request_opens(self):
        # This is why it lives in flask.g rather than on the connection: the
        # before_request gate and the handler do not share one.
        gate_conn = FakeConnection(present=self.present)
        handler_conn = FakeConnection(present=self.present)
        with self.app.test_request_context("/api/next/collection"):
            next_auth.next_auth_effective_enabled(gate_conn, next_common.table_exists)
            next_auth.next_auth_effective_enabled(handler_conn, next_common.table_exists)
        self.assertEqual(handler_conn.queries, [], "the handler must reuse the gate's answer")

    def test_the_next_request_asks_again(self):
        first = FakeConnection(present=self.present)
        with self.app.test_request_context("/api/next/collection"):
            next_auth.next_auth_effective_enabled(first, next_common.table_exists)
        second = FakeConnection(present=self.present)
        with self.app.test_request_context("/api/next/collection"):
            next_auth.next_auth_effective_enabled(second, next_common.table_exists)
        self.assertTrue(any("AS ready" in q for q in second.queries))

    def test_forgetting_makes_it_ask_again_within_the_request(self):
        conn = FakeConnection(present=self.present)
        with self.app.test_request_context("/api/next/collection"):
            next_auth.next_auth_effective_enabled(conn, next_common.table_exists)
            next_auth.forget_auth_enabled()
            next_auth.next_auth_effective_enabled(conn, next_common.table_exists)
        readiness = [q for q in conn.queries if "AS ready" in q]
        self.assertEqual(len(readiness), 2)

    def test_writing_the_setting_invalidates_it(self):
        # set_setting() calls forget_auth_enabled() for this key, so the toggle
        # route, the first-owner bootstrap and the last-passkey-deleted path all
        # invalidate without each having to remember to.
        source = (BACKEND / "next_auth.py").read_text(encoding="utf-8")
        marker = source.index("def set_setting(")
        body = source[marker : marker + 1400]
        self.assertIn('if key == "auth_enabled":', body)
        self.assertIn("forget_auth_enabled()", body)


@unittest.skipIf(next_common is None, "backend dependencies are required")
class NoRemainingDuplicateImplementationsTests(unittest.TestCase):
    """A second copy of any of these is a memo that silently stops applying."""

    def test_only_next_common_issues_the_table_existence_query(self):
        for module in ("next_auth.py", "next_metadata.py"):
            source = (BACKEND / module).read_text(encoding="utf-8")
            self.assertNotIn(
                "SELECT to_regclass(%s) AS table_name",
                source,
                f"{module} must delegate to next_common.table_exists",
            )

    @staticmethod
    def _closure_body(source: str, signature: str) -> str:
        start = source.index(signature)
        end = source.index("\n    def ", start + len(signature))
        return source[start:end]

    def test_the_auth_closure_delegates_to_the_module_level_resolver(self):
        source = (BACKEND / "next_auth.py").read_text(encoding="utf-8")
        body = self._closure_body(source, "    def current_user(conn)")
        self.assertIn("return next_auth_current_user(conn)", body)
        self.assertNotIn("cur.execute", body, "the closure must not resolve on its own again")

    def test_the_auth_enabled_closure_delegates_too(self):
        source = (BACKEND / "next_auth.py").read_text(encoding="utf-8")
        body = self._closure_body(source, "    def auth_enabled(conn)")
        self.assertIn("next_auth_effective_enabled(conn, table_exists)", body)
        self.assertNotIn("configured_auth_enabled(conn) and", body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
