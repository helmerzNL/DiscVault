"""The media_type column against a real database, on the sync write path.

`movies.media_type` is NOT NULL DEFAULT 'MOVIE' (migration 063), and the sync
upsert names the column explicitly. That combination is what these tests exist
for: a fake connection cannot tell a working default from a broken one, so the
whole class of bug here is invisible to the rest of the suite and only a real
PostgreSQL sees it.
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
except ModuleNotFoundError:
    psycopg = None
    dict_row = None

from app.backend import next_app


DATABASE_URL = os.environ.get("DATABASE_URL")

PUBLIC_ID_PREFIX = "media-type-sync-test"


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class MediaTypeSyncUpsertPostgresTests(unittest.TestCase):
    def setUp(self):
        self.client = next_app.app.test_client()
        self.client_entity_id = f"{PUBLIC_ID_PREFIX}-{uuid.uuid4()}"

    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM movies WHERE public_id LIKE %s", (f"{PUBLIC_ID_PREFIX}-%",)
                )
            conn.commit()

    def _mutate(self, **payload_overrides):
        """Push one movie upsert through the real sync endpoint."""
        payload = {
            "publicId": self.client_entity_id,
            "title": "Media Type Sync Test",
            "format": "Blu-ray",
        }
        payload.update(payload_overrides)
        response = self.client.post(
            "/api/next/sync/mutations",
            json={
                "clientId": f"{PUBLIC_ID_PREFIX}-client",
                "baseRevision": 0,
                "mutations": [
                    {
                        "clientMutationId": f"{PUBLIC_ID_PREFIX}-{uuid.uuid4()}",
                        "entityType": "movie",
                        "operation": "upsert",
                        "clientEntityId": self.client_entity_id,
                        "payload": payload,
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 200, response.data[:500])
        result = response.get_json()["results"][0]
        self.assertEqual(result["status"], "applied", result)
        return result

    def _stored_media_type(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT media_type FROM movies WHERE public_id = %s",
                    (self.client_entity_id,),
                )
                row = cur.fetchone()
        self.assertIsNotNone(row, "the mutation did not create a row")
        return row["media_type"]

    def test_a_client_that_states_no_type_can_still_create_a_movie(self):
        """Every shipped client is such a client -- none of them send mediaType.

        Naming media_type in the INSERT column list and binding NULL defeats the
        column DEFAULT and trips the NOT NULL instead, so the mutation comes back
        as an error and the client can never sync that movie at all.
        """
        self._mutate()
        self.assertEqual(self._stored_media_type(), "MOVIE")

    def test_a_client_may_state_the_type_on_creation(self):
        self._mutate(mediaType="SHOW")
        self.assertEqual(self._stored_media_type(), "SHOW")

    def test_a_later_silent_update_does_not_reset_a_stored_show(self):
        """The half of the fix that the constraint error does not force.

        Coalescing to MOVIE inside VALUES alone would make EXCLUDED.media_type
        always non-null, and the next update from a client that says nothing --
        a rename, a format correction, anything -- would quietly turn the user's
        series back into a film.
        """
        self._mutate(mediaType="SHOW")
        self._mutate(title="Media Type Sync Test Renamed")
        self.assertEqual(self._stored_media_type(), "SHOW")

    def test_an_explicit_type_still_wins_on_update(self):
        self._mutate(mediaType="SHOW")
        self._mutate(mediaType="MOVIE")
        self.assertEqual(self._stored_media_type(), "MOVIE")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
