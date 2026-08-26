"""The index and the tiebreaker that make library paging correct and cheap.

The Library is not one query. It is a first-paint page of 200 movies followed by
background pages of 500 (`library-paging.js`), every one of them served by
`collection_movie_preview_entities()` in next_app.py with LIMIT/OFFSET over:

    ORDER BY lower(COALESCE(m.sort_title, m.title)), m.year NULLS LAST, m.id

Two things about that line are load-bearing, and both are asserted here.

**`m.id`.** Title and year do not identify a row -- a 4K and a Blu-ray of one film
are two `movies` rows sharing both -- so without a unique tiebreaker the sort is
not a total order. Two executions may place tied rows differently, and OFFSET
slices whatever order it got: a row is served on two consecutive pages while
another is served on neither. The client cannot repair that. It pages by offset
and de-duplicates by id, so the repeat is dropped and the skipped row is simply
never loaded (#715).

**The index.** `idx_movies_title` and `idx_movies_sort_title`
(002_core_domain.sql) look like they cover this ordering and do not: an
expression index serves only the exact expression it was built on, and
`lower(COALESCE(sort_title, title))` is neither `lower(title)` nor
`lower(sort_title)`. Without a matching index every page sorts the whole live
library and discards `offset` rows -- O(total) per page, paid once per page.

The index has a crossover, and the tests below are written around it rather than
pretending it does not exist. Measured on PostgreSQL 16 against this schema, the
planner keeps choosing Seq Scan + Sort at a few thousand rows and switches to the
ordered index scan somewhere around ten thousand. Below that it is right to sort:
sorting a few thousand rows costs less than an ordered scan with a random heap
fetch per row. So `INDEX_ROWS` seeds past the crossover deliberately -- a test that
seeded a realistic 2,500-movie library would assert something false.

Only a real PostgreSQL can test either claim. A stubbed database could assert the
index was created and learn nothing about whether the planner uses it, and nothing
at all about whether two pages overlap -- which is the half that silently breaks
when either the index or the ORDER BY is edited.
"""

import os
import pathlib
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

INDEX_NAME = "idx_movies_library_sort_live"
MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "migrations_next"
    / "086_movies_library_sort_index.sql"
)

# The page query, reduced to the part that decides the plan: the artwork LATERALs
# and the enrichment round-trips do not participate in the ordering.
PAGE_QUERY = """
SELECT m.id
FROM movies m
WHERE m.deleted_at IS NULL
ORDER BY lower(COALESCE(m.sort_title, m.title)), m.year NULLS LAST, m.id
LIMIT %s OFFSET %s
"""


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class LibrarySortIndexTests(unittest.TestCase):
    PROBE_PREFIX = "sort-index-probe-"
    # Past the measured crossover (see the module docstring): below roughly ten
    # thousand rows the planner sorts and is right to, so a smaller table would make
    # test_the_planner_orders_from_the_index_instead_of_sorting assert something that
    # is not true and should not be.
    ROWS = 20_000
    # Large enough that a 500-row page boundary lands inside a tie group. That is what
    # an unstable sort needs in order to actually disagree between two executions: tie
    # groups that each fit inside one page are ordered arbitrarily but never split.
    TIE_GROUP = 800

    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)

    def setUp(self):
        self.tag = f"{self.PROBE_PREFIX}{uuid.uuid4()}"
        self.addCleanup(self._remove_probe_rows)
        with self.connect() as conn, conn.cursor() as cur:
            # Deliberately collide. A real shelf collides in fours -- a DVD, a Blu-ray,
            # a 4K and a re-issue of one film are four rows sharing a title and a year
            # -- but a four-row group only splits across a page boundary sometimes, so
            # the seed uses TIE_GROUP to make the split certain rather than likely.
            # The defect is the same one either way: a tie group the sort may order
            # differently on two executions, straddling the boundary between pages.
            cur.execute(
                """
                INSERT INTO movies (id, public_id, title, sort_title, year)
                SELECT
                    gen_random_uuid(),
                    gen_random_uuid()::text,
                    %s || lpad(((i - 1) / %s)::text, 7, '0'),
                    %s || lpad(((i - 1) / %s)::text, 7, '0'),
                    '1999'
                FROM generate_series(1, %s) AS i
                """,
                (self.tag, self.TIE_GROUP, self.tag, self.TIE_GROUP, self.ROWS),
            )
            cur.execute("ANALYZE movies")

    def _remove_probe_rows(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM movies WHERE title LIKE %s", (f"{self.PROBE_PREFIX}%",))
            cur.execute("ANALYZE movies")

    def _plan(self, sql, params):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params)
            return cur.fetchone()["QUERY PLAN"][0]

    @staticmethod
    def _nodes(plan):
        stack = [plan["Plan"]]
        seen = []
        while stack:
            node = stack.pop()
            seen.append(node)
            stack.extend(node.get("Plans") or [])
        return seen

    def _page(self, limit, offset, sql=PAGE_QUERY, force_sort=False):
        """One page, optionally planned the other way.

        `force_sort` is not a contrivance. Two consecutive page requests are two
        independent queries, and nothing guarantees the planner treats them alike:
        an autovacuum between them, a different actor's visibility filter, or a
        parallel plan is enough to flip one and not the other. Forcing the flip is
        how a test reproduces in a second what production reaches on its own.
        """
        with self.connect() as conn, conn.cursor() as cur:
            if force_sort:
                cur.execute("SET enable_indexscan=off")
                cur.execute("SET enable_bitmapscan=off")
            cur.execute(sql, (limit, offset))
            return [str(row["id"]) for row in cur.fetchall()]

    def test_the_index_exists_after_migrations(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT indexdef FROM pg_indexes WHERE tablename='movies' AND indexname=%s",
                (INDEX_NAME,),
            )
            row = cur.fetchone()
        self.assertIsNotNone(row, "migration 086 did not create the index")
        self.assertIn("COALESCE", row["indexdef"])
        # Partial, so tombstones stay out of it and the predicate matches the query's
        # own `AND m.deleted_at IS NULL`.
        self.assertIn("deleted_at IS NULL", row["indexdef"])

    def test_the_planner_orders_from_the_index_instead_of_sorting(self):
        # The property that matters, and the one an edit to either the ORDER BY or the
        # index expression would silently lose -- leaving a query that still returns
        # the right rows while sorting the entire library to do it.
        plan = self._plan(PAGE_QUERY, (500, 700))
        node_types = [node["Node Type"] for node in self._nodes(plan)]
        self.assertNotIn("Sort", node_types, f"planner chose {node_types}")
        self.assertTrue(
            any("Index" in name for name in node_types),
            f"expected an index scan, planner chose {node_types}",
        )

    def test_two_pages_neither_overlap_nor_skip(self):
        # The whole point of the tiebreaker, and the reason it is not cosmetic. The
        # two pages are planned differently on purpose (see _page), which is what any
        # two independent requests are free to be.
        first = self._page(500, 0)
        second = self._page(500, 500, force_sort=True)
        combined = first + second
        duplicated = len(combined) - len(set(combined))
        self.assertEqual(
            duplicated,
            0,
            f"{duplicated} rows were served on both of two consecutive pages",
        )
        self.assertEqual(len(combined), 1_000)

    def test_the_tiebreaker_is_what_prevents_that(self):
        # The negative control. Without it the assertion above could pass for reasons
        # that have nothing to do with `m.id` -- a seed too small to split a tie group,
        # say -- and quietly stop testing anything. This proves the fixture really does
        # reproduce the defect, so the test above really does prove the fix.
        unstable = PAGE_QUERY.replace(", m.id\n", "\n")
        self.assertNotIn(", m.id", unstable)
        first = self._page(500, 0, sql=unstable)
        second = self._page(500, 500, sql=unstable, force_sort=True)
        duplicated = len(first + second) - len(set(first + second))
        self.assertGreater(
            duplicated,
            0,
            "the fixture no longer reproduces unstable paging, so the test above "
            "proves nothing -- check TIE_GROUP still straddles the page boundary",
        )

    def test_paging_is_repeatable(self):
        # A stable order is not the same as a lucky one. Re-running each page must give
        # the identical slice, or the client's offset means something different on
        # every request.
        self.assertEqual(self._page(500, 0), self._page(500, 0))
        self.assertEqual(self._page(500, 700), self._page(500, 700))

    def test_walking_the_library_by_offset_visits_every_row_once(self):
        # What library-paging.js actually does, at its real chunk size: request by
        # offset until the server says there is no more. Every row exactly once, or
        # the client silently holds a library that is not the one on the server.
        walked = []
        offset = 0
        while True:
            page = self._page(500, offset, force_sort=bool(offset % 1000))
            if not page:
                break
            walked.extend(page)
            offset += len(page)
        self.assertEqual(len(set(walked)), len(walked), "a row was visited twice")
        # Against the live count rather than ROWS: a developer database is not empty,
        # and the property under test is "the walk sees the library", not "the fixture
        # is the only thing in it".
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM movies WHERE deleted_at IS NULL")
            live = cur.fetchone()["n"]
            cur.execute(
                "SELECT id FROM movies WHERE title LIKE %s AND deleted_at IS NULL",
                (f"{self.tag}%",),
            )
            seeded = {str(row["id"]) for row in cur.fetchall()}
        self.assertEqual(len(walked), live, "rows were skipped by the walk")
        self.assertTrue(seeded.issubset(set(walked)), "seeded rows were skipped")


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class TheMigrationIsSafeToReplayTests(unittest.TestCase):
    def test_running_it_again_changes_nothing(self):
        # A restored database replays every migration. IF NOT EXISTS is what makes that
        # a no-op rather than an error that stops the container.
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn("IF NOT EXISTS", sql)
        with psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)  # must not raise
                cur.execute(
                    "SELECT count(*) AS n FROM pg_indexes WHERE indexname=%s", (INDEX_NAME,)
                )
                self.assertEqual(cur.fetchone()["n"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
