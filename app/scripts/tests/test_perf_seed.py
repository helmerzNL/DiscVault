"""Tests for app/scripts/perf_seed.py and the pure parts of perf_measure.py.

A measurement is only worth comparing with the previous one if both ran on the
same data. So the properties pinned here are the ones a before/after number
leans on: the seed produces what it says, the same arguments produce the same
rows, the tie groups that make OFFSET paging hard are really there, and the
script cannot be pointed at a database that already holds a library.

The seed tests need a real PostgreSQL and build their own scratch database (the
seed refuses anything but an empty one, and the shared test database is not
guaranteed empty). Without DATABASE_URL they skip.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
import uuid

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def _load(name: str):
    path = os.path.join(os.path.dirname(__file__), "..", f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, os.path.abspath(path))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


perf_seed = _load("perf_seed")
perf_measure = _load("perf_measure")

try:
    import psycopg
    from psycopg import sql
except ModuleNotFoundError:  # pragma: no cover - minimal environments
    psycopg = None
    sql = None

DATABASE_URL = os.environ.get("DATABASE_URL")
MOVIES = 400


class MeasureHelpersTest(unittest.TestCase):
    def test_percentile_interpolates_and_survives_one_sample(self):
        self.assertEqual(perf_measure._percentile([7.0], 0.95), 7.0)
        self.assertEqual(perf_measure._percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5), 3.0)
        self.assertAlmostEqual(perf_measure._percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.95), 4.8)

    def test_sequential_scans_are_found_at_any_depth(self):
        plan = {
            "Node Type": "Limit",
            "Plans": [
                {"Node Type": "Index Scan", "Relation Name": "movies"},
                {
                    "Node Type": "Hash Join",
                    "Plans": [
                        {"Node Type": "Seq Scan", "Relation Name": "people", "Actual Rows": 80000, "Actual Loops": 1}
                    ],
                },
            ],
        }
        found: list[dict] = []
        perf_measure._seq_scans(plan, found)
        self.assertEqual([scan["table"] for scan in found], ["people"])

    def test_every_endpoint_is_a_read_with_known_placeholders(self):
        values = {"deep": 0, "last": 0, "movie": "m", "asset": "a", "revision": 0}
        labels = [label for label, _ in perf_measure.ENDPOINTS]
        self.assertEqual(len(labels), len(set(labels)), "endpoint labels must be unique: they key the report")
        for label, template in perf_measure.ENDPOINTS:
            self.assertTrue(template.format(**values).startswith("/"), label)

    def test_the_hydration_chunk_matches_the_pager(self):
        # The harness walks the library the way the PWA does; if the pager's page
        # size changes and this does not, "full hydration" measures something else.
        with open(os.path.join(REPO_ROOT, "app", "frontend", "js", "library-paging.js"), encoding="utf-8") as handle:
            self.assertIn(f"CHUNK_SIZE = {perf_measure.HYDRATION_CHUNK}", handle.read())


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class PerfSeedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.backend import next_database

        cls.scratch = f"perf_seed_test_{uuid.uuid4().hex[:12]}"
        with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.scratch)))
        cls.addClassCleanup(cls._drop_scratch)
        info = psycopg.conninfo.conninfo_to_dict(DATABASE_URL)
        info["dbname"] = cls.scratch
        cls.scratch_url = psycopg.conninfo.make_conninfo(**info)
        with psycopg.connect(cls.scratch_url) as conn:
            next_database.apply_migrations(conn, next_database.discover_migrations())
            conn.commit()

    @classmethod
    def _drop_scratch(cls):
        with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(cls.scratch)))

    def _seeded(self, **kwargs):
        """Seed inside a transaction that is rolled back, so every test starts empty."""
        conn = psycopg.connect(self.scratch_url)
        self.addCleanup(conn.close)
        self.addCleanup(conn.rollback)
        counts = perf_seed.seed(conn, movies=MOVIES, **kwargs)
        return conn, counts

    @staticmethod
    def _fingerprint(conn) -> str:
        return conn.execute(
            """
            SELECT md5(string_agg(row_text, '|' ORDER BY row_text)) FROM (
                SELECT id::text || title || year || format || metadata::text AS row_text FROM movies
                UNION ALL SELECT movie_id::text || person_id::text || sort_order FROM movie_credits
                UNION ALL SELECT container_id::text || movie_id::text FROM container_movies
            ) rows
            """
        ).fetchone()[0]

    def test_it_writes_what_it_says(self):
        conn, counts = self._seeded()
        self.assertEqual(counts["movies"], MOVIES)
        self.assertEqual(counts["people"], int(MOVIES * 1.6))
        self.assertEqual(counts["locations"], 45)
        self.assertGreater(counts["movie_credits"], MOVIES * 12)
        self.assertEqual(counts["media_assets"], counts["entity_media"])
        self.assertGreater(counts["containers"], 0)
        self.assertGreater(counts["container_movies"], counts["containers"])
        # The library reads director and actor from metadata; they must be there.
        missing = conn.execute(
            "SELECT COUNT(*) FROM movies WHERE metadata->>'director' IS NULL OR metadata->>'actor' IS NULL"
        ).fetchone()[0]
        self.assertEqual(missing, 0)

    def test_the_same_arguments_give_the_same_rows(self):
        first_conn, _ = self._seeded()
        first = self._fingerprint(first_conn)
        first_conn.rollback()
        second_conn, _ = self._seeded()
        self.assertEqual(first, self._fingerprint(second_conn))

    def test_another_seed_gives_another_library(self):
        first_conn, _ = self._seeded()
        first = self._fingerprint(first_conn)
        first_conn.rollback()
        second_conn, _ = self._seeded(seed_text="another-seed")
        self.assertNotEqual(first, self._fingerprint(second_conn))

    def test_tie_groups_exist(self):
        # Two discs of one film share title and year. Without them a paging or
        # sort measurement would be taken on a library easier than a real one.
        conn, _ = self._seeded()
        groups = conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT 1 FROM movies GROUP BY lower(COALESCE(sort_title, title)), year HAVING COUNT(*) > 1
            ) tied
            """
        ).fetchone()[0]
        self.assertGreater(groups, 0)

    def test_it_refuses_a_database_that_holds_movies(self):
        with psycopg.connect(self.scratch_url) as conn:
            conn.execute(
                "INSERT INTO movies (public_id, title) VALUES ('perf-seed-refusal-probe', 'A real library')"
            )
            with self.assertRaises(perf_seed.SeedRefused):
                perf_seed.seed(conn, movies=MOVIES)
            # Nothing was added next to the row that was already there.
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0], 1)
            conn.rollback()

    def test_nothing_points_at_a_real_host(self):
        conn, _ = self._seeded()
        hosts = conn.execute(
            "SELECT DISTINCT split_part(source_url, '/', 3) FROM media_assets WHERE source_url IS NOT NULL"
        ).fetchall()
        self.assertEqual([row[0] for row in hosts], ["posters.invalid"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
