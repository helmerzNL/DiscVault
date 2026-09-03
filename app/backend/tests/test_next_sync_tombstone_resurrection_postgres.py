"""A sync upsert must never leave its own write in an invisible row.

The bug these pin: `deleted_at` was cleared only on the one route that sets
`resurrect_tombstone` (ladder miss -> tombstone found by identity -> client edit
post-dates the deletion). Three other routes reach the write with `entity_id`
pointing at a tombstoned row -- a `clientEntityId` mapping stored by an earlier
delete-wins response, the barcode-owner lookup in `resolve_new_movie_identity`,
and a re-push replaying either. The server answered `status: applied`, the client
showed the record, and the row stayed deleted, so it vanished again on the next
delta on every device with nothing reporting it.

None of this can be asserted against a fake cursor: the question is what the row
looks like afterwards, and whether a tombstone answers a lookup at all.

Point `DATABASE_URL` at a database with every migration applied; without it these
skip.
"""

import os
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone


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

PREFIX = "tombstone-resurrection-test"
#: Real, check-digit-valid EAN-13s. A fabricated code passes a length test,
#: fails the checksum, and would assert nothing about a real scan.
EAN = "5027035025124"
OTHER_EAN = "5027035027531"
#: What an import placeholder (`IMPORT-<title>-BOX-01`) collapses to once the
#: non-digits are stripped. Two unrelated box sets produce the same key.
SYNTHETIC_DIGITS = "01"


class _MoviesHarness(unittest.TestCase):
    """Shared fixture. Not collected on its own -- both concrete classes below
    carry the skip decorator, and this one holds no test methods."""

    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)

    def setUp(self):
        self.conn = self.connect()
        self.addCleanup(self.conn.close)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM movies WHERE title LIKE %s", (f"{PREFIX}%",))

    def _insert_movie(
        self,
        *,
        title,
        barcode=None,
        deleted_at=None,
        client_id=None,
        media_type="MOVIE",
    ):
        movie_id = uuid.uuid4()
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movies (
                    id, public_id, title, barcode, media_type, deleted_at, client_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    movie_id,
                    str(movie_id),
                    f"{PREFIX} {title}",
                    barcode,
                    media_type,
                    deleted_at,
                    client_id,
                ),
            )
        return movie_id

    def _deleted_at(self, movie_id):
        with self.conn.cursor() as cur:
            cur.execute("SELECT deleted_at FROM movies WHERE id=%s", (movie_id,))
            row = cur.fetchone()
        return row["deleted_at"] if row else None

@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class TombstoneLookupPostgresTests(_MoviesHarness):
    def test_a_tombstone_is_found_by_barcode(self):
        """The guard still works -- the vetoes below must not disable it."""
        deleted_at = datetime.now(timezone.utc) - timedelta(days=1)
        movie_id = self._insert_movie(title="deleted", barcode=EAN, deleted_at=deleted_at)

        found = next_app.find_tombstoned_movie_by_identity(
            self.conn,
            persistent_client_id=None,
            barcode=EAN,
            incoming_media_type="MOVIE",
        )

        self.assertIsNotNone(found)
        self.assertEqual(found["id"], movie_id)
        self.assertEqual(found["matched_by"], "barcode")

    def test_a_media_type_conflict_vetoes_the_tombstone(self):
        """A deleted show must not answer for an incoming film.

        Without the veto the caller is told "this record is deleted" about a
        record that was never deleted, and the film silently never appears.
        """
        deleted_at = datetime.now(timezone.utc) - timedelta(days=1)
        self._insert_movie(
            title="deleted show",
            barcode=EAN,
            deleted_at=deleted_at,
            media_type="SHOW",
        )

        found = next_app.find_tombstoned_movie_by_identity(
            self.conn,
            persistent_client_id=None,
            barcode=EAN,
            incoming_media_type="MOVIE",
        )

        self.assertIsNone(found)

    def test_a_conflicting_row_does_not_hide_a_legitimate_match_behind_it(self):
        """A veto blocks one candidate, not the query.

        `movies.barcode` is globally unique, so the two candidates cannot hold
        the same string -- they hold the same *digits* in different formatting,
        which is exactly what the digits-only key exists to collapse. The show
        was deleted more recently, so `ORDER BY deleted_at DESC` offers it first.
        """
        newer = datetime.now(timezone.utc) - timedelta(hours=1)
        older = datetime.now(timezone.utc) - timedelta(days=2)
        self._insert_movie(
            title="deleted show", barcode=EAN, deleted_at=newer, media_type="SHOW"
        )
        wanted = self._insert_movie(
            title="deleted film",
            barcode=f"{EAN[0]}-{EAN[1:7]}-{EAN[7:]}",
            deleted_at=older,
            media_type="MOVIE",
        )

        found = next_app.find_tombstoned_movie_by_identity(
            self.conn,
            persistent_client_id=None,
            barcode=EAN,
            incoming_media_type="MOVIE",
        )

        self.assertIsNotNone(found)
        self.assertEqual(found["id"], wanted)

    def test_a_synthetic_import_barcode_is_too_short_to_match(self):
        """`IMPORT-<title>-BOX-01` collapses to "01" for every unrelated set."""
        deleted_at = datetime.now(timezone.utc) - timedelta(days=1)
        self._insert_movie(
            title="deleted box member",
            barcode="IMPORT-SOMETHING-BOX-01",
            deleted_at=deleted_at,
        )

        found = next_app.find_tombstoned_movie_by_identity(
            self.conn,
            persistent_client_id=None,
            barcode="IMPORT-SOMETHING-ELSE-BOX-01",
            incoming_media_type="MOVIE",
        )

        self.assertIsNone(found)

    def test_client_id_still_matches_regardless_of_barcode(self):
        """Tier 1 is unchanged: the persistent record id is the strongest tie."""
        deleted_at = datetime.now(timezone.utc) - timedelta(days=1)
        record_client_id = f"{PREFIX}-{uuid.uuid4()}"
        movie_id = self._insert_movie(
            title="deleted by client id",
            barcode=OTHER_EAN,
            deleted_at=deleted_at,
            client_id=record_client_id,
        )

        found = next_app.find_tombstoned_movie_by_identity(
            self.conn,
            persistent_client_id=record_client_id,
            barcode=None,
        )

        self.assertIsNotNone(found)
        self.assertEqual(found["id"], movie_id)
        self.assertEqual(found["matched_by"], "clientId")


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class TombstoneResurrectionOnWritePostgresTests(_MoviesHarness):
    """The write itself: reaching the update means the row must become visible.

    Delete-wins returns before this point, so any write that lands on a
    tombstoned row is by definition one the server decided to apply.
    """

    def _upsert(self, *, client_id="device-a", client_entity_id):
        """A push carrying no `entityId`.

        That is the shape of the mapping route: the client knows only its own
        temporary id, the server resolves it through `client_entity_mapping`,
        and the ladder never runs. Passing an `entityId` instead would take the
        deliberate delete-wins return at the top of `apply_movie_upsert`, which
        is pinned by `NextTombstoneResponseContractTests` and unchanged here.
        """
        return next_app.apply_movie_upsert(
            self.conn,
            client_id=client_id,
            idem_key=f"idem-{uuid.uuid4()}",
            mutation={
                "clientMutationId": str(uuid.uuid4()),
                "clientEntityId": client_entity_id,
                "payload": {"title": f"{PREFIX} rewritten", "barcode": EAN},
            },
        )

    def test_a_write_landing_on_a_tombstoned_row_makes_it_visible_again(self):
        """The defect: `status: applied` into a row that stays deleted.

        The mapping route reaches the write with `entity_id` on a tombstone and
        never sets `resurrect_tombstone`, so before the fix the row kept its
        `deleted_at` and the record disappeared on the next delta.
        """
        deleted_at = datetime.now(timezone.utc) - timedelta(days=1)
        movie_id = self._insert_movie(title="deleted", barcode=EAN, deleted_at=deleted_at)
        client_entity_id = f"local-{uuid.uuid4()}"

        # Reproduce the stored mapping an earlier delete-wins response leaves
        # behind, which is what makes the next push skip the ladder.
        next_app.store_client_entity_mapping(
            self.conn,
            client_id="device-a",
            client_entity_id=client_entity_id,
            entity_type="movie",
            entity_id=movie_id,
            idem_key=f"idem-{uuid.uuid4()}",
        )

        self._upsert(client_entity_id=client_entity_id)

        self.assertIsNone(
            self._deleted_at(movie_id),
            "an applied upsert must not leave the row tombstoned",
        )

    def test_an_ordinary_update_of_a_live_row_is_untouched(self):
        """The predicate keeps the common path a no-op."""
        movie_id = self._insert_movie(title="live", barcode=EAN)
        client_entity_id = f"local-{uuid.uuid4()}"
        next_app.store_client_entity_mapping(
            self.conn,
            client_id="device-a",
            client_entity_id=client_entity_id,
            entity_type="movie",
            entity_id=movie_id,
            idem_key=f"idem-{uuid.uuid4()}",
        )

        self._upsert(client_entity_id=client_entity_id)

        self.assertIsNone(self._deleted_at(movie_id))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
