import os
import sys
import unittest
import uuid
from unittest.mock import patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend import next_app
except ModuleNotFoundError as exc:  # Local minimal test environments may omit optional backend deps.
    if exc.name not in {"flask", "psycopg"}:
        raise
    next_app = None


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *args, **kwargs):
        return None

    def fetchall(self):
        return self._rows


class _FakeConn:
    """Minimal connection whose cursor yields pre-baked rows for resolver queries."""

    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)


@unittest.skipIf(next_app is None, "backend optional dependencies not installed")
class MovieBorrowablePredicateTests(unittest.TestCase):
    """Authoritative borrow-eligibility used to pick the 'Ask to borrow' vs 'Lend' UI.

    The frontend cannot rely on a bare owner_id comparison because movies created via
    the sync/import path carry a NULL owner_id, which would make every viewer look like
    the owner. ``movie_borrowable_from_other`` is the backend source of truth and now
    resolves ownership authoritatively via ``resolve_movie_owner_id``.
    """

    def setUp(self):
        self.conn = object()  # never touched; helpers are patched
        self.actor_id = uuid.uuid4()
        self.owner_id = uuid.uuid4()
        self.movie_id = uuid.uuid4()

    def _run(self, *, owner, shared):
        with patch.object(next_app, "resolve_movie_owner_id", return_value=owner), patch.object(
            next_app, "movie_is_shared_with_actor", return_value=shared
        ):
            return next_app.movie_borrowable_from_other(
                self.conn, {"id": self.actor_id}, self.movie_id
            )

    def test_owner_viewing_own_shared_movie_cannot_borrow(self):
        # You cannot raise a borrow request for your own disc, even inside a group.
        self.assertFalse(self._run(owner=self.actor_id, shared=True))

    def test_other_members_movie_is_borrowable(self):
        self.assertTrue(self._run(owner=self.owner_id, shared=True))

    def test_unresolved_owner_shared_movie_is_borrowable(self):
        # Owner could not be resolved (no owner_id, no group creator) but the disc is
        # shared with the actor -> still offer the "Ask to borrow" flow.
        self.assertTrue(self._run(owner=None, shared=True))

    def test_unshared_movie_is_not_borrowable(self):
        # Not shared with the actor via any group -> no borrow control.
        self.assertFalse(self._run(owner=None, shared=False))
        self.assertFalse(self._run(owner=self.owner_id, shared=False))

    def test_anonymous_actor_cannot_borrow(self):
        # Anonymous callers short-circuit before any ownership resolution.
        with patch.object(next_app, "resolve_movie_owner_id") as resolver, patch.object(
            next_app, "movie_is_shared_with_actor", return_value=True
        ):
            self.assertFalse(next_app.movie_borrowable_from_other(self.conn, None, self.movie_id))
            self.assertFalse(next_app.movie_borrowable_from_other(self.conn, {"id": None}, self.movie_id))
            resolver.assert_not_called()


@unittest.skipIf(next_app is None, "backend optional dependencies not installed")
class ResolveMovieOwnerTests(unittest.TestCase):
    """Owner attribution for lending/borrowing, incl. NULL-owner sync/import discs."""

    def setUp(self):
        self.movie_id = uuid.uuid4()
        self.viewer_id = uuid.uuid4()

    def test_explicit_owner_is_returned_directly(self):
        owner = uuid.uuid4()
        with patch.object(next_app, "movie_owner_id", return_value=owner):
            # conn is never touched when owner_id is already set.
            result = next_app.resolve_movie_owner_id(object(), self.movie_id, viewer_id=self.viewer_id)
        self.assertEqual(result, owner)

    def test_null_owner_falls_back_to_group_creator(self):
        creator = uuid.uuid4()
        rows = [{"added_by": None, "created_by": creator}]
        with patch.object(next_app, "movie_owner_id", return_value=None), patch.object(
            next_app, "table_exists", return_value=True
        ):
            result = next_app.resolve_movie_owner_id(_FakeConn(rows), self.movie_id, viewer_id=self.viewer_id)
        self.assertEqual(result, creator)

    def test_added_by_metadata_is_preferred_over_creator(self):
        sharer = uuid.uuid4()
        creator = uuid.uuid4()
        rows = [{"added_by": str(sharer), "created_by": creator}]
        with patch.object(next_app, "movie_owner_id", return_value=None), patch.object(
            next_app, "table_exists", return_value=True
        ):
            result = next_app.resolve_movie_owner_id(_FakeConn(rows), self.movie_id, viewer_id=self.viewer_id)
        self.assertEqual(result, sharer)

    def test_null_owner_and_no_groups_returns_none(self):
        with patch.object(next_app, "movie_owner_id", return_value=None), patch.object(
            next_app, "table_exists", return_value=True
        ):
            result = next_app.resolve_movie_owner_id(_FakeConn([]), self.movie_id, viewer_id=self.viewer_id)
        self.assertIsNone(result)

    def test_invalid_added_by_is_ignored_and_creator_used(self):
        creator = uuid.uuid4()
        rows = [{"added_by": "not-a-uuid", "created_by": creator}]
        with patch.object(next_app, "movie_owner_id", return_value=None), patch.object(
            next_app, "table_exists", return_value=True
        ):
            result = next_app.resolve_movie_owner_id(_FakeConn(rows), self.movie_id, viewer_id=self.viewer_id)
        self.assertEqual(result, creator)

    def test_missing_group_tables_returns_none(self):
        with patch.object(next_app, "movie_owner_id", return_value=None), patch.object(
            next_app, "table_exists", return_value=False
        ):
            result = next_app.resolve_movie_owner_id(_FakeConn([]), self.movie_id, viewer_id=self.viewer_id)
        self.assertIsNone(result)


@unittest.skipIf(next_app is None, "backend optional dependencies not installed")
class PersonalMovieStateDefaultTests(unittest.TestCase):
    """The anonymous default state must advertise the borrow flags so the UI can gate."""

    def test_default_state_includes_borrow_flags(self):
        state = next_app.personal_movie_state(None, uuid.uuid4(), None)
        self.assertIn("isOwnMovie", state)
        self.assertIn("canRequestBorrow", state)
        self.assertIn("ownerUserId", state)
        self.assertFalse(state["isOwnMovie"])
        self.assertFalse(state["canRequestBorrow"])
        self.assertIsNone(state["ownerUserId"])


if __name__ == "__main__":
    unittest.main()
