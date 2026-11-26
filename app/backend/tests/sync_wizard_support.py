"""Shared fixtures for the link wizard's server tests (sync-contract §5f).

Not a test module: snapshot, retain and supersede all need the same things -- a
real database, a backup directory of their own, real users to own snapshots, a way
to speak as an owner or as somebody else, and a few films and containers whose fate
the test controls. Keeping them here means each module states only what it asserts.

The one rule worth knowing before writing a retain test: `POST /sync/retain`
tombstones every live film and container outside the lists, and the database it
runs against is shared with every other module. A test therefore keeps everything
that is live except the rows it means to remove (`keep_all_but`), so it never
takes another module's fixtures with it.
"""

import contextlib
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:  # pragma: no cover - minimal environments
    psycopg = None
    dict_row = None

try:
    from app.backend import next_app as na
    from app.backend import next_sync_snapshot as snap
except ModuleNotFoundError as exc:  # pragma: no cover - minimal environments
    if exc.name not in {"flask", "psycopg", "cbor2", "argon2", "jwt", "segno", "PIL"}:
        raise
    na = None
    snap = None

DATABASE_URL = os.environ.get("DATABASE_URL")
KEY = "sync.replace_library"
#: What a native token carries for an owner or an admin.
NATIVE_LIST = ["api.read", "collection.view", "collection.add", KEY]

HAVE_DATABASE = bool(DATABASE_URL and psycopg is not None and na is not None)
needs_database = unittest.skipUnless(HAVE_DATABASE, "PostgreSQL test database is not configured")


class WizardCase(unittest.TestCase):
    """A database, a backup directory, two users and helpers; no tests of its own."""

    def setUp(self):
        self.client = na.app.test_client()
        self.tmp = Path(tempfile.mkdtemp(prefix="wizard-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.backup_dir = self.tmp / "backups"
        self.data_dir = self.tmp / "data"
        self.data_dir.mkdir()
        env = patch.dict(
            os.environ,
            {
                "DISCVAULT_NEXT_BACKUP_DIR": str(self.backup_dir),
                "DISCVAULT_LEGACY_DATA_DIR": str(self.data_dir),
            },
        )
        env.start()
        self.addCleanup(env.stop)

        self.db = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)
        self.addCleanup(self.db.close)
        self.run_id = uuid.uuid4().hex[:8]
        self.prefix = f"wizard-test-{self.run_id}"
        self.device = f"{self.prefix}-device"
        self.owner_id = self._make_user("owner")
        self.other_id = self._make_user("other")
        self._mutation_counter = 0
        self.addCleanup(self._cleanup_rows)
        with self.db.cursor() as cur:
            cur.execute("DELETE FROM sync_library_snapshots")

    # -- users, auth -------------------------------------------------------

    def _make_user(self, label):
        with self.db.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username) VALUES (%s) RETURNING id",
                (f"{self.prefix}-{label}",),
            )
            return str(cur.fetchone()["id"])

    @contextlib.contextmanager
    def actor(self, user_id=None, role="owner", permissions=(KEY,), token_keys=None, auth_enabled=True):
        """Make requests inside this block come from this role (and token)."""
        who = {"id": user_id or self.owner_id, "username": f"{self.prefix}-{role}"}
        if token_keys is not None:
            who["apiToken"] = {"id": "t", "name": "DiscVault iOS", "permissionKeys": list(token_keys), "scopes": []}
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(na, "next_auth_effective_enabled", return_value=auth_enabled))
            stack.enter_context(patch.object(na, "next_auth_current_user", return_value=who))
            stack.enter_context(patch.object(na, "next_user_primary_role", return_value=role))
            stack.enter_context(patch.object(na, "next_user_permission_keys", return_value=set(permissions)))
            yield

    # -- snapshots ---------------------------------------------------------

    def post_snapshot(self, **actor_kwargs):
        with self.actor(**actor_kwargs):
            return self.client.post("/api/next/sync/snapshot")

    def post(self, user_id=None, **actor_kwargs):
        return self.post_snapshot(user_id=user_id, **actor_kwargs)

    def take_snapshot(self, **actor_kwargs):
        """A valid snapshot id, past the rate limit however many were taken before."""
        self.backdate(snap.SNAPSHOT_MIN_INTERVAL_SECONDS + 1)
        resp = self.post_snapshot(**actor_kwargs)
        self.assertEqual(resp.status_code, 201, resp.get_json())
        return resp.get_json()["snapshotId"]

    def snapshot_rows(self):
        with self.db.cursor() as cur:
            cur.execute("SELECT * FROM sync_library_snapshots ORDER BY created_at")
            return cur.fetchall()

    def backdate(self, seconds):
        with self.db.cursor() as cur:
            cur.execute(
                "UPDATE sync_library_snapshots SET created_at = created_at - make_interval(secs => %s)",
                (seconds,),
            )

    def archives(self):
        return sorted(p.name for p in self.backup_dir.glob("*.zip")) if self.backup_dir.exists() else []

    # -- films and containers ----------------------------------------------

    def make_movie(self, label="film", *, token=None):
        with self.db.cursor() as cur:
            cur.execute(
                "INSERT INTO movies (public_id, title, client_id) VALUES (%s, %s, %s) RETURNING id",
                (f"{self.prefix}-{label}-{uuid.uuid4().hex[:6]}", f"{self.prefix} {label}", token),
            )
            return str(cur.fetchone()["id"])

    def make_container(self, label="box", *, token=None):
        with self.db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO containers (public_id, title, container_type, client_id)
                VALUES (%s, %s, 'box_set', %s) RETURNING id
                """,
                (f"{self.prefix}-{label}-{uuid.uuid4().hex[:6]}", f"{self.prefix} {label}", token),
            )
            return str(cur.fetchone()["id"])

    def tombstone(self, table, row_id):
        with self.db.cursor() as cur:
            cur.execute(f"UPDATE {table} SET deleted_at=now() WHERE id=%s", (row_id,))  # table: caller literal

    def deleted_at(self, table, row_id):
        with self.db.cursor() as cur:
            cur.execute(f"SELECT deleted_at FROM {table} WHERE id=%s", (row_id,))  # table: caller literal
            row = cur.fetchone()
        return row["deleted_at"] if row else "absent"

    def is_live(self, table, row_id):
        return self.deleted_at(table, row_id) is None

    def live_ids(self, table):
        with self.db.cursor() as cur:
            cur.execute(f"SELECT id FROM {table} WHERE deleted_at IS NULL")  # table: caller literal
            return {str(row["id"]) for row in cur.fetchall()}

    def keep_all_but(self, *, movies=(), containers=()):
        """A retain body that removes exactly these rows and keeps everything else live."""
        return {
            "movies": sorted(self.live_ids("movies") - {str(i) for i in movies}),
            "containers": sorted(self.live_ids("containers") - {str(i) for i in containers}),
        }

    # -- retain ------------------------------------------------------------

    def post_retain(self, body, **actor_kwargs):
        with self.actor(**actor_kwargs):
            return self.client.post("/api/next/sync/retain", json=body)

    def current_revision(self):
        with self.db.cursor() as cur:
            cur.execute("SELECT revision FROM sync_state WHERE id='global'")
            return int(cur.fetchone()["revision"])

    def change_count(self):
        with self.db.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM sync_changes")
            return cur.fetchone()["n"]

    def delta(self, since):
        resp = self.client.get(f"/api/next/sync/delta?since={since}&limit=1000")
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        return resp.get_json()["changes"]

    # -- cleanup -----------------------------------------------------------

    def _cleanup_rows(self):
        users = [self.owner_id, self.other_id]
        with self.db.cursor() as cur:
            cur.execute("DELETE FROM sync_library_snapshots")
            for table in ("watchlist_items", "wishlist_items", "tags"):
                cur.execute(f"DELETE FROM {table} WHERE user_id = ANY(%s::uuid[])", (users,))  # fixed names
            cur.execute("DELETE FROM client_id_mappings WHERE client_id LIKE %s", (f"{self.prefix}%",))
            cur.execute("DELETE FROM idempotency_records WHERE key LIKE %s", (f"%{self.prefix}%",))
            cur.execute("DELETE FROM locations WHERE name LIKE %s", (f"{self.prefix}%",))
            cur.execute("DELETE FROM series WHERE title LIKE %s", (f"{self.prefix}%",))
            cur.execute("DELETE FROM containers WHERE title LIKE %s", (f"{self.prefix}%",))
            cur.execute("DELETE FROM movies WHERE title LIKE %s", (f"{self.prefix}%",))
            cur.execute("DELETE FROM audit_events WHERE actor_user_id = ANY(%s::uuid[])", (users,))
            cur.execute("DELETE FROM users WHERE id = ANY(%s::uuid[])", (users,))
