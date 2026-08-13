"""The index that stops a metadata refresh scanning a table once per credit.

`person_identifiers` answers two opposite questions and had an index for only
one. Its primary key is (person_id, provider_id, identifier_type, identifier),
which serves "what identifiers does this person have". The refresh path asks
"who is the person behind this TMDB id", where `person_id` is the answer rather
than the input -- so the primary key cannot serve it and PostgreSQL scanned the
table, once for every credit on the film.

Measured with EXPLAIN (ANALYZE, BUFFERS) on a scratch copy:

    rows       without      with        blocks touched
    2,000      0.172 ms     0.012 ms    10 -> 3
    50,000     2.179 ms     0.015 ms    234 -> 4
    400,000   22.630 ms     0.016 ms    3,739 -> 4

The millisecond at today's size is not the argument -- at roughly two thousand
rows this is a small part of a refresh, and saying otherwise would be dressing
up a preventive change as an urgent one. The argument is the shape: without the
index the cost grows with the table and is multiplied by the credit count, and
with it the cost stops depending on either.

Only a real PostgreSQL can test this, because the claim is about what the query
planner does. A test that stubbed the database out could assert the index was
created and learn nothing about whether the query uses it -- which is the half
that silently breaks when either the index or the query is edited.
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
except ModuleNotFoundError:  # pragma: no cover - minimal environments
    psycopg = None
    dict_row = None

DATABASE_URL = os.environ.get("DATABASE_URL")

INDEX_NAME = "idx_person_identifiers_provider_lookup"

# The query in next_metadata.py that runs once per credit.
HOT_QUERY = """
SELECT person_id
FROM person_identifiers
WHERE provider_id='tmdb'
  AND identifier_type='person_id'
  AND identifier=%s
LIMIT 1
"""


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class PersonIdentifierIndexTests(unittest.TestCase):
    PROBE_PREFIX = "index-probe-"
    ROWS = 2_000

    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)

    def setUp(self):
        self.tag = f"{self.PROBE_PREFIX}{uuid.uuid4()}"
        self.addCleanup(self._remove_probe_rows)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO people (id, public_id, name)
                SELECT gen_random_uuid(), gen_random_uuid()::text, %s || i::text
                FROM generate_series(1, %s) AS i
                """,
                (self.tag, self.ROWS),
            )
            cur.execute(
                """
                INSERT INTO person_identifiers (person_id, provider_id, identifier_type, identifier)
                SELECT id, 'tmdb', 'person_id', %s || '-' || id::text
                FROM people
                WHERE name LIKE %s
                """,
                (self.tag, f"{self.tag}%"),
            )
            cur.execute("ANALYZE person_identifiers")

    def _remove_probe_rows(self):
        with self.connect() as conn, conn.cursor() as cur:
            # person_identifiers is ON DELETE CASCADE from people.
            cur.execute("DELETE FROM people WHERE name LIKE %s", (f"{self.PROBE_PREFIX}%",))
            cur.execute("ANALYZE person_identifiers")

    def _plan(self, sql, params):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params)
            return cur.fetchone()["QUERY PLAN"][0]

    @staticmethod
    def _leaf(plan):
        node = plan["Plan"]
        while node.get("Plans"):
            node = node["Plans"][0]
        return node

    def test_the_index_exists_after_migrations(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT indexdef FROM pg_indexes WHERE tablename='person_identifiers' AND indexname=%s",
                (INDEX_NAME,),
            )
            row = cur.fetchone()
        self.assertIsNotNone(row, "migration 079 did not create the index")
        self.assertIn("provider_id", row["indexdef"])

    def test_the_columns_are_in_the_order_the_query_needs(self):
        # provider_id first. An index leading with `identifier` would look
        # right in pg_indexes and serve this query worse for every other
        # provider added later.
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.attname, k.ordinality
                FROM pg_index i
                JOIN pg_class c ON c.oid = i.indexrelid
                JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ordinality) ON TRUE
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
                WHERE c.relname = %s
                ORDER BY k.ordinality
                """,
                (INDEX_NAME,),
            )
            columns = [row["attname"] for row in cur.fetchall()]
        self.assertEqual(columns, ["provider_id", "identifier_type", "identifier"])

    def test_the_refresh_lookup_uses_it_instead_of_scanning(self):
        # The property that matters, and the one an edit to either side would
        # silently lose.
        plan = self._plan(HOT_QUERY, (f"{self.tag}-nonexistent",))
        leaf = self._leaf(plan)
        self.assertIn(
            "Index",
            leaf["Node Type"],
            f"expected an index scan, planner chose {leaf['Node Type']}",
        )

    def test_it_reads_a_handful_of_blocks_rather_than_the_table(self):
        plan = self._plan(HOT_QUERY, (f"{self.tag}-nonexistent",))
        leaf = self._leaf(plan)
        blocks = leaf.get("Shared Hit Blocks", 0) + leaf.get("Shared Read Blocks", 0)
        self.assertLessEqual(
            blocks,
            8,
            "a scan of this table touches tens to thousands of blocks; the index "
            "makes the cost independent of how large it has grown",
        )

    def test_the_answer_is_still_correct(self):
        # An index is only ever a speed change. Prove the query still resolves
        # the identifier it is given.
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT person_id, identifier FROM person_identifiers WHERE identifier LIKE %s LIMIT 1",
                (f"{self.tag}%",),
            )
            known = cur.fetchone()
            cur.execute(HOT_QUERY, (known["identifier"],))
            found = cur.fetchone()
        self.assertEqual(found["person_id"], known["person_id"])


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class TheMigrationIsSafeToReplayTests(unittest.TestCase):
    def test_running_it_again_changes_nothing(self):
        # A restored database replays every migration. IF NOT EXISTS is what
        # makes that a no-op rather than an error that stops the container.
        import pathlib

        path = (
            pathlib.Path(__file__).resolve().parents[1]
            / "migrations_next"
            / "079_person_identifiers_provider_lookup.sql"
        )
        sql = path.read_text(encoding="utf-8")
        self.assertIn("IF NOT EXISTS", sql)
        with psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)  # must not raise
                cur.execute(
                    "SELECT count(*) AS n FROM pg_indexes WHERE indexname=%s", (INDEX_NAME,)
                )
                self.assertEqual(cur.fetchone()["n"], 1)

    def test_it_does_not_use_concurrently(self):
        # next_database.py applies every migration inside conn.transaction(),
        # and PostgreSQL refuses CREATE INDEX CONCURRENTLY there. Adding it
        # would fail at container start, which is the worst place to find out.
        import pathlib

        path = (
            pathlib.Path(__file__).resolve().parents[1]
            / "migrations_next"
            / "079_person_identifiers_provider_lookup.sql"
        )
        statements = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("--")
        ]
        self.assertNotIn("CONCURRENTLY", "\n".join(statements).upper())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
