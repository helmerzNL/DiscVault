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


@unittest.skipIf(next_app is None, "backend optional dependencies not installed")
class MovieBorrowablePredicateTests(unittest.TestCase):
    """Authoritative borrow-eligibility used to pick the 'Ask to borrow' vs 'Lend' UI.

    The frontend cannot rely on a bare owner_id comparison because movies created via
    the sync/import path carry a NULL owner_id, which would make every viewer look like
    the owner. ``movie_borrowable_from_other`` is the backend source of truth.
    """

    def setUp(self):
        self.conn = object()  # never touched; helpers are patched
        self.actor_id = uuid.uuid4()
        self.owner_id = uuid.uuid4()
        self.movie_id = uuid.uuid4()

    def _run(self, *, owner, shared):
        with patch.object(next_app, "movie_owner_id", return_value=owner), patch.object(
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

    def test_null_owner_shared_movie_is_borrowable(self):
        # Regression: sync/import-created movies have NULL owner_id. A group member
        # viewing such a movie must still get the "Ask to borrow" flow.
        self.assertTrue(self._run(owner=None, shared=True))

    def test_unshared_movie_is_not_borrowable(self):
        # Not shared with the actor via any group -> no borrow control.
        self.assertFalse(self._run(owner=None, shared=False))
        self.assertFalse(self._run(owner=self.owner_id, shared=False))

    def test_anonymous_actor_cannot_borrow(self):
        with patch.object(next_app, "movie_owner_id", return_value=None), patch.object(
            next_app, "movie_is_shared_with_actor", return_value=True
        ):
            self.assertFalse(next_app.movie_borrowable_from_other(self.conn, None, self.movie_id))
            self.assertFalse(next_app.movie_borrowable_from_other(self.conn, {"id": None}, self.movie_id))


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
