"""The personal rating: its scale, its absence, and who may see whose.

Two claims here are the ones that would fail silently in production.

**Absence is not zero.** An unrated film has no row. If any read path ever
returns 0 for one, it ranks below every rated film while telling the user
somebody scored it that way, and there is nothing on screen to reveal the
substitution.

**The owner test lives on the server.** `attach_movie_ratings` publishes the
owner's score to anyone who may see the film and nobody else's to anyone. A
client deciding that for itself by comparing owner_id to its own id reads a NULL
owner_id as "I am the owner" -- the shape of bug renderMovieLoan already carries
a comment about. Both the NULL case and the viewer-is-owner case are asserted
below because neither is reachable from the happy path.
"""

import os
import sys
import unittest
import uuid


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:  # pragma: no cover - minimal environments
    psycopg = None
    dict_row = None

import next_app  # noqa: E402
from next_common import NextApiError  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")


class ScoreValidationTests(unittest.TestCase):
    """Refused, never rounded.

    A slider that sends 7.34 and is quietly stored as 7.3 has recorded something
    the person did not choose, and they have no way to notice.
    """

    def test_whole_and_half_numbers_in_range_are_accepted(self):
        for value in ("0.5", 0.5, 7, "7.5", 10, "10.0"):
            with self.subTest(value=value):
                next_app.normalize_movie_rating_score(value)

    def test_a_value_between_the_steps_is_refused(self):
        for value in ("7.3", 7.34, "0.7"):
            with self.subTest(value=value):
                with self.assertRaises(NextApiError):
                    next_app.normalize_movie_rating_score(value)

    def test_out_of_range_is_refused_including_zero(self):
        # 0 is refused deliberately: "no rating" is the absence of a row, so a
        # stored zero could only ever mean a score somebody chose.
        for value in (0, "0", -1, 10.5, 11):
            with self.subTest(value=value):
                with self.assertRaises(NextApiError):
                    next_app.normalize_movie_rating_score(value)

    def test_missing_and_unparseable_values_are_refused(self):
        for value in (None, "", "  ", "eight", {}):
            with self.subTest(value=value):
                with self.assertRaises(NextApiError):
                    next_app.normalize_movie_rating_score(value)


class RatingValueTests(unittest.TestCase):
    def test_no_rating_is_none_and_never_zero(self):
        self.assertIsNone(next_app.movie_rating_value(None))

    def test_a_stored_score_becomes_a_float(self):
        from decimal import Decimal

        self.assertEqual(next_app.movie_rating_value(Decimal("8.5")), 8.5)


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class MovieRatingStorageTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        self.owner_id = self._insert_user("owner")
        self.viewer_id = self._insert_user("viewer")
        self.movie_id = self._insert_movie(owner_id=self.owner_id)

    def tearDown(self):
        try:
            with self.conn.cursor() as cur:
                cur.execute("DELETE FROM movies WHERE id=%s", (self.movie_id,))
                cur.execute(
                    "DELETE FROM users WHERE id = ANY(%s)", ([self.owner_id, self.viewer_id],)
                )
            self.conn.commit()
        finally:
            self.conn.close()

    def _insert_user(self, label):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, display_name) VALUES (%s, %s) RETURNING id",
                (f"{label}-{uuid.uuid4().hex[:8]}", label.title()),
            )
            user_id = cur.fetchone()["id"]
        self.conn.commit()
        return user_id

    def _insert_movie(self, owner_id=None):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO movies (public_id, title, owner_id) VALUES (%s, %s, %s) RETURNING id",
                (f"rating-{uuid.uuid4().hex[:9]}", "Rating probe", owner_id),
            )
            movie_id = cur.fetchone()["id"]
        self.conn.commit()
        return movie_id

    def _rate(self, user_id, score, movie_id=None):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movie_user_ratings (user_id, movie_id, score)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id, movie_id)
                DO UPDATE SET score=EXCLUDED.score, updated_at=now()
                """,
                (user_id, movie_id or self.movie_id, score),
            )
        self.conn.commit()

    def _rows(self):
        return [{"id": self.movie_id}]

    def test_the_database_refuses_a_score_off_the_half_step(self):
        with self.assertRaises(psycopg.errors.CheckViolation):
            self._rate(self.viewer_id, "7.3")
        self.conn.rollback()

    def test_the_database_refuses_a_score_out_of_range(self):
        for value in ("0", "10.5"):
            with self.subTest(value=value):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    self._rate(self.viewer_id, value)
                self.conn.rollback()

    def test_an_unrated_movie_reports_none_not_zero(self):
        rows = next_app.attach_movie_ratings(self.conn, self._rows(), self.viewer_id)
        self.assertIsNone(rows[0]["personal_rating"])
        self.assertIsNone(rows[0]["owner_rating"])

    def test_a_viewer_sees_their_own_score_and_the_owners(self):
        self._rate(self.owner_id, "9.0")
        self._rate(self.viewer_id, "6.5")
        rows = next_app.attach_movie_ratings(self.conn, self._rows(), self.viewer_id)
        self.assertEqual(rows[0]["personal_rating"], 6.5)
        self.assertEqual(rows[0]["owner_rating"], 9.0)
        self.assertEqual(rows[0]["owner_rating_by"], "Owner")

    def test_the_owner_is_not_shown_their_own_score_twice(self):
        self._rate(self.owner_id, "9.0")
        rows = next_app.attach_movie_ratings(self.conn, self._rows(), self.owner_id)
        self.assertEqual(rows[0]["personal_rating"], 9.0)
        self.assertIsNone(rows[0]["owner_rating"])

    def test_a_movie_with_no_owner_never_publishes_anyones_score(self):
        # The NULL owner_id case. A client-side owner check would show the
        # viewer's own score back to them as "the owner's".
        orphan_id = self._insert_movie(owner_id=None)
        try:
            self._rate(self.viewer_id, "8.0", movie_id=orphan_id)
            rows = next_app.attach_movie_ratings(self.conn, [{"id": orphan_id}], self.viewer_id)
            self.assertEqual(rows[0]["personal_rating"], 8.0)
            self.assertIsNone(rows[0]["owner_rating"])
        finally:
            with self.conn.cursor() as cur:
                cur.execute("DELETE FROM movies WHERE id=%s", (orphan_id,))
            self.conn.commit()

    def test_a_third_partys_score_is_visible_to_nobody_else(self):
        # The asymmetry that makes this safe to publish: only the owner's score
        # leaves its author.
        third_party = self._insert_user("stranger")
        try:
            self._rate(third_party, "1.0")
            rows = next_app.attach_movie_ratings(self.conn, self._rows(), self.viewer_id)
            self.assertIsNone(rows[0]["personal_rating"])
            self.assertIsNone(rows[0]["owner_rating"])
        finally:
            with self.conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE id=%s", (third_party,))
            self.conn.commit()

    def test_rating_again_replaces_rather_than_duplicating(self):
        self._rate(self.viewer_id, "6.5")
        self._rate(self.viewer_id, "8.0")
        rows = next_app.attach_movie_ratings(self.conn, self._rows(), self.viewer_id)
        self.assertEqual(rows[0]["personal_rating"], 8.0)

    def test_personal_movie_state_carries_the_same_answer(self):
        self._rate(self.owner_id, "9.0")
        self._rate(self.viewer_id, "6.5")
        state = next_app.personal_movie_state(self.conn, self.movie_id, self.viewer_id)
        self.assertEqual(state["rating"], 6.5)
        self.assertEqual(state["ownerRating"], 9.0)
        self.assertEqual(state["ownerRatingBy"], "Owner")

    def test_an_unauthenticated_read_sees_no_ratings_at_all(self):
        self._rate(self.owner_id, "9.0")
        rows = next_app.attach_movie_ratings(self.conn, self._rows(), None)
        self.assertIsNone(rows[0]["personal_rating"])
        self.assertIsNone(rows[0]["owner_rating"])

    def test_deleting_the_movie_takes_the_ratings_with_it(self):
        self._rate(self.viewer_id, "6.5")
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM movies WHERE id=%s", (self.movie_id,))
            cur.execute(
                "SELECT count(*) AS n FROM movie_user_ratings WHERE movie_id=%s", (self.movie_id,)
            )
            self.assertEqual(cur.fetchone()["n"], 0)
        self.conn.commit()

    def test_deleting_the_user_takes_their_ratings_with_them(self):
        self._rate(self.viewer_id, "6.5")
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id=%s", (self.viewer_id,))
            cur.execute(
                "SELECT count(*) AS n FROM movie_user_ratings WHERE user_id=%s", (self.viewer_id,)
            )
            self.assertEqual(cur.fetchone()["n"], 0)
        self.conn.commit()

    def test_the_sync_entity_reports_the_score_as_a_number(self):
        self._rate(self.viewer_id, "8.5")
        entity = next_app.movie_rating_sync_entity(self.conn, self.viewer_id, self.movie_id)
        self.assertEqual(entity["score"], 8.5)
        self.assertEqual(entity["movieId"], str(self.movie_id))
        everything = next_app.all_movie_rating_sync_entities(self.conn, self.viewer_id)
        self.assertIn(str(self.movie_id), [item["movieId"] for item in everything])


if __name__ == "__main__":
    unittest.main()
