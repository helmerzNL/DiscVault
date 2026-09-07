"""The origin-backfill endpoints, called as HTTP routes.

The GET is what the admin card reads and the POST is what its button presses,
and between them they are the whole of what an operator can learn about this
feature. Both used to answer with two numbers and nothing else: no queue, no
outcome of the last run, no word about a TMDb plugin too old to return the
field at all -- so a backfill that had crashed on every job looked exactly like
one that had not started (#719).

The claim worth the most is `test_queueing_pins_the_ids_each_job_owns`. A job
carrying only a batch size re-runs "the next N that still need it", so a film
TMDB cannot answer stays at the head of that ordering and is retried by every
job in the batch. With an unfillable first hundred, the remaining thousands are
never touched -- and every job still reports success.
"""

import os
import sys
import unittest
import uuid
from unittest.mock import patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:
    psycopg = None
    dict_row = None

from app.backend import next_app


DATABASE_URL = os.environ.get("DATABASE_URL")
PREFIX = "origin-backfill-route-test"


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class OriginBackfillRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = next_app.app.test_client()
        self.actor = {
            "id": "00000000-0000-0000-0000-0000000000c7",
            "role": "owner",
            "permissions": ["*"],
        }
        self.permission = patch(
            "app.backend.next_app.require_next_permission", return_value=self.actor
        )
        self.permission.start()
        self.addCleanup(self.permission.stop)
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (id, username) VALUES (%s,%s) ON CONFLICT (id) DO NOTHING",
                    (self.actor["id"], f"{PREFIX}-actor"),
                )
            conn.commit()

    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM background_jobs WHERE job_type=%s", ("metadata.backfill_origin",))
                cur.execute("DELETE FROM movies WHERE title LIKE %s", (f"{PREFIX}%",))
                cur.execute("DELETE FROM audit_events WHERE actor_user_id=%s", (self.actor["id"],))
                cur.execute("DELETE FROM users WHERE username LIKE %s", (f"{PREFIX}%",))
            conn.commit()

    def _movie_needing_origin(self, tmdb_id):
        movie_id = uuid.uuid4()
        title = f"{PREFIX}-{movie_id}"
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO movies (id, public_id, title, sort_title) VALUES (%s,%s,%s,%s)",
                    (movie_id, f"{PREFIX}-{movie_id}", title, title),
                )
                cur.execute(
                    """
                    INSERT INTO movie_identifiers (movie_id, provider_id, identifier_type, identifier)
                    VALUES (%s,'tmdb','movie_id',%s)
                    """,
                    (movie_id, tmdb_id),
                )
            conn.commit()
        return str(movie_id)

    def _status(self):
        response = self.client.get("/api/next/admin/metadata/origin-backfill")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def test_the_status_carries_the_queue_and_the_plugin_alongside_the_counters(self):
        payload = self._status()
        self.assertIn("pending", payload)
        self.assertIn("unresolvable", payload)
        self.assertIn("outstanding", payload["jobs"])
        self.assertIn("lastRun", payload["jobs"])
        # Below 1.8.0 the plugin never emits `filmOrigin`, so the job succeeds
        # and writes nothing. The card is the only place that can say so.
        self.assertIn("originCapable", payload["tmdbPlugin"])
        self.assertEqual(payload["tmdbPlugin"]["requiredVersion"], "1.8.0")

    def test_queueing_pins_the_ids_each_job_owns(self):
        mine = {self._movie_needing_origin(str(4000 + index)) for index in range(3)}
        response = self.client.post(
            "/api/next/admin/metadata/origin-backfill", json={"batchSize": 1}
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertGreaterEqual(payload["queued"], 3)

        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT payload FROM background_jobs WHERE job_type=%s",
                    ("metadata.backfill_origin",),
                )
                payloads = [row["payload"] for row in cur.fetchall()]
        claimed = [str(item) for job in payloads for item in (job.get("movieIds") or [])]
        self.assertTrue(all(job.get("movieIds") for job in payloads), "a job was queued without its ids")
        # Disjoint: no film is handed to two jobs, so a batch cannot spin on the
        # same head of the queue.
        self.assertEqual(len(claimed), len(set(claimed)))
        self.assertTrue(mine.issubset(set(claimed)))

    def test_the_queue_response_is_read_after_queueing_not_before(self):
        self._movie_needing_origin("4100")
        payload = self.client.post(
            "/api/next/admin/metadata/origin-backfill", json={"batchSize": 100}
        ).get_json()
        # The counters used to be computed before the jobs were created, so the
        # card re-rendered the state from before the press and looked frozen.
        self.assertGreater(payload["jobs"]["outstanding"], 0)

    def test_nothing_to_fill_queues_nothing_and_still_answers_with_the_full_shape(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*)::int AS n FROM movies WHERE deleted_at IS NULL")
                if cur.fetchone()["n"]:
                    self.skipTest("the test database already holds films that need origin data")
        payload = self.client.post(
            "/api/next/admin/metadata/origin-backfill", json={}
        ).get_json()
        self.assertEqual(payload["queued"], 0)
        self.assertIn("tmdbPlugin", payload)
        self.assertIn("jobs", payload)


if __name__ == "__main__":
    unittest.main()
