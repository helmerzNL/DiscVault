"""`oidc_auth_transactions.used_at` must survive a failed callback (#804).

`_transaction_row()` marks a transaction used with a plain `UPDATE ...
SET used_at=now() ... RETURNING`, inside `with conn.transaction():`. Before
the fix, `oidc_callback` ran a SELECT on that same connection first
(`require_tables()`), which moved it to `INTRANS`; psycopg 3 then opens
`with conn.transaction():` as a SAVEPOINT rather than an outer transaction,
since it decides outer-vs-savepoint by whether the connection is IDLE at
entry. A later `OidcFlowError` raised anywhere else in the callback, on that
same connection, rolled the whole implicit transaction back -- taking the
`used_at` mark with it, on exactly the attempts that failed.

The fix moves the mark onto a connection of its own
(tests/test_next_oidc.py's `test_transaction_row_is_marked_on_a_connection_of_its_own`
pins that at the source level). This module proves the underlying claim
against a real database: a connection with a statement before
`with conn.transaction():` produces a mark that a later failure on a
*different* connection cannot touch, while the pre-fix shape -- a single
connection carrying a statement before the block and then a later
failure -- loses it.
"""

import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:  # pragma: no cover - depends on environment
    psycopg = None
    dict_row = None

try:
    from app.backend import next_oidc
except ModuleNotFoundError as exc:  # pragma: no cover - minimal test environments
    if exc.name not in {"flask", "psycopg", "authlib", "jwt", "cryptography"}:
        raise
    next_oidc = None


DATABASE_URL = os.environ.get("DATABASE_URL")
PREFIX = "oidc-used-at-test"


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and next_oidc is not None,
    "PostgreSQL test database is not configured",
)
class OidcUsedAtSurvivesFailureTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM oidc_auth_transactions WHERE return_path LIKE %s",
                    (f"/{PREFIX}-%",),
                )
            conn.commit()

    def _make_transaction(self, conn, *, raw_state: str) -> None:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO oidc_auth_transactions (
                    state_hash, nonce_hash, browser_binding_hash,
                    code_verifier, mode, return_path, redirect_uri, expires_at
                )
                VALUES (%s, 'nonce-hash', 'binding-hash', 'verifier', 'login', %s,
                        'https://vault.example.test/callback', now() + interval '5 minutes')
                """,
                (next_oidc.oidc_state_hash(raw_state), f"/{PREFIX}-{raw_state}"),
            )

    def _used_at(self, raw_state: str):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT used_at FROM oidc_auth_transactions WHERE state_hash=%s",
                    (next_oidc.oidc_state_hash(raw_state),),
                )
                row = cur.fetchone()
            conn.commit()
        return row["used_at"] if row else None

    def test_mark_on_a_dedicated_connection_survives_a_later_failure_elsewhere(self):
        """The fixed shape: `_transaction_row` runs on its own connection,
        which closes (and genuinely commits) before anything that could raise
        even exists."""
        raw_state = f"{PREFIX}-dedicated"
        with self.connect() as setup_conn:
            self._make_transaction(setup_conn, raw_state=raw_state)
            setup_conn.commit()

        with self.connect() as txn_conn:
            # Mirrors `require_tables()`: any statement before the block
            # moves this connection to INTRANS.
            with txn_conn.cursor() as cur:
                cur.execute("SELECT to_regclass('oidc_auth_transactions')")
                cur.fetchone()
            with txn_conn.transaction():
                row = next_oidc._transaction_row(txn_conn, raw_state)
        self.assertIsNotNone(row)

        # The rest of the callback now fails, on a *different* connection.
        try:
            with self.connect() as conn:
                with conn.transaction():
                    raise next_oidc.OidcFlowError("provider_denied")
        except next_oidc.OidcFlowError:
            pass

        self.assertIsNotNone(self._used_at(raw_state))

    def test_mark_sharing_the_failing_connection_is_lost(self):
        """The pre-fix shape, reproduced directly: one connection carries the
        pre-statement, the mark, and the later failure. This is what made the
        bug invisible to a check that opened a fresh connection for the
        `transaction()` block in isolation -- it never does, in the real
        callback."""
        raw_state = f"{PREFIX}-shared"
        with self.connect() as setup_conn:
            self._make_transaction(setup_conn, raw_state=raw_state)
            setup_conn.commit()

        try:
            with self.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('oidc_auth_transactions')")
                    cur.fetchone()
                with conn.transaction():
                    row = next_oidc._transaction_row(conn, raw_state)
                self.assertIsNotNone(row)
                with conn.transaction():
                    raise next_oidc.OidcFlowError("provider_denied")
        except next_oidc.OidcFlowError:
            pass

        self.assertIsNone(self._used_at(raw_state))


if __name__ == "__main__":
    unittest.main()
