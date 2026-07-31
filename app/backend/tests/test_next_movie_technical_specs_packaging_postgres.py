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


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class MoviePackagingListPostgresTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def _insert_bare_movie(self, conn, *, title="Packaging List Test Movie") -> uuid.UUID:
        movie_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movies (id, public_id, title, sort_title)
                VALUES (%s, %s, %s, %s)
                """,
                (movie_id, f"packaging-list-test-{movie_id}", title, title),
            )
        conn.commit()
        return movie_id

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM movies WHERE public_id LIKE 'packaging-list-test-%'")
            conn.commit()

    def test_movie_technical_specs_packaging_column_is_jsonb(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT data_type FROM information_schema.columns
                    WHERE table_name = 'movie_technical_specs' AND column_name = 'packaging'
                    """
                )
                row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["data_type"], "jsonb")

    def test_upsert_movie_technical_edits_persists_a_multi_value_packaging_list(self):
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            edits = next_app.movie_technical_edits({"packaging": ["steelbook", "slipcover"]})
            with conn.cursor() as cur:
                next_app.upsert_movie_technical_edits(cur, movie_id, edits)
            conn.commit()
            spec = next_app.movie_technical_spec_entity(conn, movie_id)
        self.assertEqual(spec["packaging"], ["steelbook", "slipcover"])

    def test_upsert_movie_technical_edits_persists_comma_separated_packaging_text(self):
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            edits = next_app.movie_technical_edits({"packaging": "Steelbook, Slipcover"})
            with conn.cursor() as cur:
                next_app.upsert_movie_technical_edits(cur, movie_id, edits)
            conn.commit()
            spec = next_app.movie_technical_spec_entity(conn, movie_id)
        self.assertEqual(spec["packaging"], ["Steelbook", "Slipcover"])

    def test_empty_packaging_list_does_not_clear_a_previously_set_value(self):
        """The technical_updates merge path uses a CASE WHEN <> '[]'::jsonb
        guard (matching audio_tracks/subtitles) - an update carrying an
        empty packaging array must not blank out a value set earlier."""
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            with conn.cursor() as cur:
                next_app.upsert_movie_technical_edits(
                    cur, movie_id, next_app.movie_technical_edits({"packaging": ["steelbook"]})
                )
            conn.commit()

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE movie_technical_specs
                    SET packaging = CASE WHEN %s::jsonb <> '[]'::jsonb THEN %s::jsonb ELSE packaging END
                    WHERE movie_id = %s
                    """,
                    ("[]", "[]", movie_id),
                )
            conn.commit()

            spec = next_app.movie_technical_spec_entity(conn, movie_id)
        self.assertEqual(spec["packaging"], ["steelbook"])

    def test_new_movie_gets_an_empty_packaging_list_by_default(self):
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO movie_technical_specs (movie_id) VALUES (%s)",
                    (movie_id,),
                )
            conn.commit()
            spec = next_app.movie_technical_spec_entity(conn, movie_id)
        self.assertEqual(spec["packaging"], [])


if __name__ == "__main__":
    unittest.main()
