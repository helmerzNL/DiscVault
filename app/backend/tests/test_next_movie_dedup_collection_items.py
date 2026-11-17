"""Movie dedup keeps a merged film in the collections that listed it.

The admin "Scan & merge" re-hangs a loser movie's relations onto the winner
through ``MOVIE_RELATIONS``. A collection lists its films in
``collection_items`` (``item_type = 'movie'``, ``item_id = <movie id>``), and
``item_id`` has no foreign key -- so that table was never re-hung, and after a
merge the collection still pointed at the tombstoned loser: the film silently
dropped out of it.

Three claims, all from one merge:

1. a collection that listed only the loser now lists the winner;
2. a collection that listed both keeps exactly one row -- the winner's own,
   with its sort order -- instead of colliding on the primary key;
3. each of those collections gets a ``container_membership`` upsert naming the
   winner, and a collection that only ever listed the winner gets none.

PostgreSQL only: it needs ``DATABASE_URL`` and runs in the smoke workflow below
the migrations. It executes only its own group, never a full report, because
the shared smoke database holds other tests' rows.
"""

import os
import sys
import unittest
import uuid


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.scripts import sync_dedup_merge as merge  # noqa: E402

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:  # pragma: no cover - minimal environments
    psycopg = None
    dict_row = None

DATABASE_URL = os.environ.get("DATABASE_URL")


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class MovieDedupCollectionItemsPostgresTests(unittest.TestCase):
    def connect(self, **kwargs):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, **kwargs)

    def setUp(self):
        self.tag = f"movie-dedup-collection-{uuid.uuid4().hex[:12]}"
        self.movie_ids = []
        self.container_ids = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        with self.connect(autocommit=True) as conn, conn.cursor() as cur:
            ids = self.container_ids + self.movie_ids
            if ids:
                cur.execute("DELETE FROM sync_changes WHERE entity_id = ANY(%s)", (ids,))
            if self.container_ids:
                cur.execute("DELETE FROM containers WHERE id = ANY(%s::uuid[])", (self.container_ids,))
            if self.movie_ids:
                cur.execute("DELETE FROM movies WHERE id = ANY(%s::uuid[])", (self.movie_ids,))

    def _movie(self, cur, *, notes=None):
        # Same title and year for both, so the title/year tier groups them; the
        # tag keeps the pair apart from every other row in the shared database.
        cur.execute(
            """
            INSERT INTO movies (public_id, title, year, notes)
            VALUES (%s, %s, '2001', %s)
            RETURNING id
            """,
            (f"{self.tag}-{uuid.uuid4().hex[:8]}", f"{self.tag} Duplicate film", notes),
        )
        movie_id = str(cur.fetchone()["id"])
        self.movie_ids.append(movie_id)
        return movie_id

    def _collection(self, cur, title, movies):
        cur.execute(
            """
            INSERT INTO containers (public_id, container_type, title)
            VALUES (%s, 'collection', %s)
            RETURNING id
            """,
            (f"{self.tag}-{uuid.uuid4().hex[:8]}", f"{self.tag} {title}"),
        )
        collection_id = str(cur.fetchone()["id"])
        self.container_ids.append(collection_id)
        for order, movie_id in movies:
            cur.execute(
                """
                INSERT INTO collection_items (collection_id, item_type, item_id, sort_order)
                VALUES (%s, 'movie', %s, %s)
                """,
                (collection_id, movie_id, order),
            )
        return collection_id

    def _items(self, cur, collection_id):
        cur.execute(
            """
            SELECT item_id::text AS item_id, sort_order
            FROM collection_items
            WHERE collection_id = %s AND item_type = 'movie'
            ORDER BY sort_order
            """,
            (collection_id,),
        )
        return [(row["item_id"], row["sort_order"]) for row in cur.fetchall()]

    def test_merge_repoints_collections_and_publishes_their_membership(self):
        with self.connect(autocommit=True) as conn, conn.cursor() as cur:
            # `notes` is user data, so the richer copy wins the merge.
            winner = self._movie(cur, notes="kept")
            loser = self._movie(cur)
            only_loser = self._collection(cur, "Shelf", [(4, loser)])
            both = self._collection(cur, "Favourites", [(1, winner), (2, loser)])
            only_winner = self._collection(cur, "Untouched", [(7, winner)])

        with self.connect() as conn:
            report = merge.build_report(conn)
            conn.rollback()
        mine = [
            group
            for group in report["groups"]
            if {member["id"] for member in group["members"]} == {winner, loser}
        ]
        self.assertEqual(len(mine), 1, [g["members"] for g in report["groups"]])
        group = mine[0]
        self.assertEqual(group["winner"], winner)
        self.assertEqual(group["losers"], [loser])

        scoped = {"groups": [group], "container_groups": []}
        with self.connect() as conn:
            movies_done, containers_done = merge.execute_merge(conn, scoped)
        self.assertEqual((movies_done, containers_done), (1, 0))

        with self.connect(autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("SELECT deleted_at FROM movies WHERE id = %s", (loser,))
            self.assertIsNotNone(cur.fetchone()["deleted_at"])

            # 1. The film stays in the collection, as the winner, in its place.
            self.assertEqual(self._items(cur, only_loser), [(winner, 4)])
            # 2. One row, the winner's own: the loser's would have collided.
            self.assertEqual(self._items(cur, both), [(winner, 1)])
            self.assertEqual(self._items(cur, only_winner), [(winner, 7)])
            cur.execute(
                "SELECT count(*)::int AS n FROM collection_items WHERE item_type = 'movie' AND item_id = %s",
                (loser,),
            )
            self.assertEqual(cur.fetchone()["n"], 0)

            cur.execute(
                """
                SELECT entity_id, operation, payload
                FROM sync_changes
                WHERE entity_type = 'container_membership'
                  AND entity_id = ANY(%s)
                ORDER BY revision
                """,
                ([only_loser, both, only_winner],),
            )
            changes = cur.fetchall()

        # 3. One upsert per changed collection, none for the untouched one.
        self.assertEqual(sorted(c["entity_id"] for c in changes), sorted([only_loser, both]))
        for change in changes:
            self.assertEqual(change["operation"], "upsert")
            payload = change["payload"]
            self.assertEqual(payload["containerId"], change["entity_id"])
            self.assertEqual(
                [(m["movie_id"], m["relationship"]) for m in payload["members"]],
                [(winner, "collection_movie")],
            )


if __name__ == "__main__":
    unittest.main()
