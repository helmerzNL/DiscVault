"""Signing in twice with the same provider account, against a real database.

``oidc_identities`` permitted exactly one successful login, ever (#799). The
callback's lookup selected ``oi.id`` as ``id`` and then used that row as the
user, so ``user["id"]`` was the identity's primary key rather than the
account's. ``_upsert_oidc_identity`` guards its ``ON CONFLICT`` with
``WHERE oidc_identities.user_id=EXCLUDED.user_id``, which a value that is not
a user id cannot match: ``RETURNING`` came back empty and the caller raised
``identity_already_linked`` -- "That identity is already linked to another
DiscVault account", about the user's own account.

Both columns are ``uuid``, so nothing type-errored and nothing in the logs
named the real cause. Nor could the existing ``test_next_oidc`` see it: that
module asserts on the module's *source text*, which cannot observe what a
query returns. This one needs a database, because the defect lives entirely in
the disagreement between two ``uuid`` columns.

What is pinned here:

* the lookup returns ``users.id`` as ``id``, and offers the identity's own key
  under a name that cannot be mistaken for it;
* an identity that already exists can log in again, and ``last_login_at``
  moves when it does -- the second login is the one that used to fail;
* an identity *linked* from Profile (``last_login_at`` still NULL) can log in
  at all, which is the shape that never worked even once.
"""

import os
import sys
import unittest
import uuid


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
ISSUER = "https://id.oidc-login-identity-test.invalid"
PREFIX = "oidc-login-identity-test"


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and next_oidc is not None,
    "PostgreSQL test database is not configured",
)
class OidcLoginIdentityTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                # oidc_identities cascades off users, so the user is enough.
                cur.execute("DELETE FROM users WHERE username LIKE %s", (f"{PREFIX}-%",))
            conn.commit()

    # -- fixtures ----------------------------------------------------------

    def _config(self) -> "next_oidc.OidcConfig":
        return next_oidc.OidcConfig(
            issuer=ISSUER,
            client_id="discvault",
            client_secret="unit-test-secret",
            provider_name="Pocket ID",
            insecure_backchannel_origins=frozenset(),
        )

    def _account(self, conn) -> uuid.UUID:
        user_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (id, username, display_name, status, created_at, updated_at)
                VALUES (%s, %s, %s, 'active', now(), now())
                """,
                (user_id, f"{PREFIX}-{user_id.hex[:12]}", "OIDC Login Test"),
            )
        return user_id

    def _identity(self, conn, user_id: uuid.UUID, subject: str) -> uuid.UUID:
        """An identity as Profile -> Link leaves it: no login recorded yet."""
        identity_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO oidc_identities (
                    id, user_id, issuer, subject, provider_name,
                    created_at, updated_at, last_login_at
                )
                VALUES (%s, %s, %s, %s, 'Pocket ID', now(), now(), NULL)
                """,
                (identity_id, user_id, ISSUER, subject),
            )
        return identity_id

    @staticmethod
    def _claims(subject: str) -> dict[str, str]:
        return {"sub": subject, "email": "someone@example.test", "name": "Someone"}

    # -- the lookup ---------------------------------------------------------

    def test_the_lookup_returns_the_account_id_not_the_identity_id(self):
        subject = f"{PREFIX}-subject-lookup"
        with self.connect() as conn:
            user_id = self._account(conn)
            identity_id = self._identity(conn, user_id, subject)
            row = next_oidc._lookup_oidc_identity(conn, issuer=ISSUER, subject=subject)
            conn.rollback()

        self.assertIsNotNone(row)
        # The whole defect in one assertion: `id` used to be `identity_id`.
        self.assertEqual(str(row["id"]), str(user_id))
        self.assertEqual(str(row["user_id"]), str(user_id))
        self.assertEqual(str(row["identity_id"]), str(identity_id))
        self.assertNotEqual(str(row["id"]), str(identity_id))

    def test_the_lookup_finds_nothing_for_an_unknown_subject(self):
        with self.connect() as conn:
            row = next_oidc._lookup_oidc_identity(
                conn, issuer=ISSUER, subject=f"{PREFIX}-never-seen"
            )
            conn.rollback()
        self.assertIsNone(row)

    # -- the login that used to fail ---------------------------------------

    def test_a_linked_identity_can_log_in_and_records_the_login(self):
        subject = f"{PREFIX}-subject-linked"
        with self.connect() as conn:
            user_id = self._account(conn)
            self._identity(conn, user_id, subject)

            identity = next_oidc._lookup_oidc_identity(
                conn, issuer=ISSUER, subject=subject
            )
            # Exactly what the callback does in its `elif identity:` branch.
            user = identity
            row = next_oidc._upsert_oidc_identity(
                conn,
                config=self._config(),
                user_id=user["id"],
                claims=self._claims(subject),
                login=True,
            )
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT last_login_at FROM oidc_identities WHERE issuer=%s AND subject=%s",
                    (ISSUER, subject),
                )
                stored = cur.fetchone()
            conn.rollback()

        self.assertEqual(str(row["user_id"]), str(user_id))
        self.assertIsNotNone(stored["last_login_at"])

    def test_the_second_login_with_the_same_account_succeeds(self):
        subject = f"{PREFIX}-subject-twice"
        with self.connect() as conn:
            user_id = self._account(conn)
            self._identity(conn, user_id, subject)
            config = self._config()

            first = next_oidc._lookup_oidc_identity(conn, issuer=ISSUER, subject=subject)
            next_oidc._upsert_oidc_identity(
                conn,
                config=config,
                user_id=first["id"],
                claims=self._claims(subject),
                login=True,
            )
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT last_login_at FROM oidc_identities WHERE issuer=%s AND subject=%s",
                    (ISSUER, subject),
                )
                after_first = cur.fetchone()["last_login_at"]

            # The login that raised identity_already_linked for every user.
            second = next_oidc._lookup_oidc_identity(conn, issuer=ISSUER, subject=subject)
            next_oidc._upsert_oidc_identity(
                conn,
                config=config,
                user_id=second["id"],
                claims=self._claims(subject),
                login=True,
            )
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT last_login_at FROM oidc_identities WHERE issuer=%s AND subject=%s",
                    (ISSUER, subject),
                )
                after_second = cur.fetchone()["last_login_at"]
            conn.rollback()

        self.assertEqual(str(second["id"]), str(user_id))
        self.assertIsNotNone(after_first)
        self.assertIsNotNone(after_second)
        self.assertGreaterEqual(after_second, after_first)

    def test_an_identity_owned_by_another_account_is_still_refused(self):
        """The guard the upsert exists for has to keep refusing."""
        subject = f"{PREFIX}-subject-foreign"
        with self.connect() as conn:
            owner_id = self._account(conn)
            self._identity(conn, owner_id, subject)
            stranger_id = self._account(conn)

            with self.assertRaises(next_oidc.OidcFlowError) as caught:
                next_oidc._upsert_oidc_identity(
                    conn,
                    config=self._config(),
                    user_id=stranger_id,
                    claims=self._claims(subject),
                    login=True,
                )
            conn.rollback()

        self.assertEqual(caught.exception.code, "identity_already_linked")


if __name__ == "__main__":
    unittest.main()
