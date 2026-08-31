"""Migration 087 and the origin write path, against a real PostgreSQL.

The constraints are the whole protection here. There is no catalogue table and
no foreign key -- deliberately, because countries and languages are open sets
whose names come from Intl.DisplayNames rather than from a table DiscVault
maintains (see the migration's header). What stands in for that is the shape
check on each column, and a shape check is exactly the kind of claim that cannot
be tested without a database: a stub would assert the SQL text was written, not
that PostgreSQL refuses 'jp' in a column that must hold 'JP'.

The other half is replace-not-append. `replace_movie_film_origin` is called on
every metadata refresh, so an implementation that inserted without deleting
would grow a film's origin list forever and only show up as a country the user
cannot remove.
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

import next_metadata  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class MovieOriginStorageTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        self.movie_id = self._insert_movie()

    def tearDown(self):
        try:
            with self.conn.cursor() as cur:
                cur.execute("DELETE FROM movies WHERE id=%s", (self.movie_id,))
            self.conn.commit()
        finally:
            self.conn.close()

    def _insert_movie(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO movies (public_id, title) VALUES (%s, %s) RETURNING id",
                (f"origin-{uuid.uuid4().hex[:10]}", "Origin probe"),
            )
            movie_id = cur.fetchone()["id"]
        self.conn.commit()
        return movie_id

    def test_the_language_column_refuses_a_shape_it_cannot_display(self):
        # Upper case would filter as a second language beside its lower-case
        # twin; the rest are not language codes at all.
        for value in ("JA", "english!", "j", "ja_JP"):
            with self.subTest(value=value):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with self.conn.cursor() as cur:
                        cur.execute(
                            "UPDATE movies SET original_language=%s WHERE id=%s",
                            (value, self.movie_id),
                        )
                self.conn.rollback()

    def test_the_language_column_accepts_the_forms_tmdb_actually_returns(self):
        for value in ("ja", "fr", "cmn", "cmn-Hans", None):
            with self.subTest(value=value):
                with self.conn.cursor() as cur:
                    cur.execute(
                        "UPDATE movies SET original_language=%s WHERE id=%s",
                        (value, self.movie_id),
                    )
                self.conn.commit()

    def test_the_country_column_refuses_anything_but_alpha_2_upper_case(self):
        for value in ("jp", "JPN", "1P", ""):
            with self.subTest(value=value):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with self.conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO movie_origin_countries (movie_id, country_code)"
                            " VALUES (%s, %s)",
                            (self.movie_id, value),
                        )
                self.conn.rollback()

    def test_tmdbs_country_order_survives_a_round_trip(self):
        # The lead producer of a co-production is listed first, and re-sorting
        # would state something TMDB never said.
        next_metadata.replace_movie_film_origin(
            self.conn, self.movie_id, {"originalLanguage": "ja", "originCountries": ["JP", "FR"]}
        )
        self.conn.commit()
        self.assertEqual(
            next_metadata.movie_film_origin(self.conn, self.movie_id),
            {"originalLanguage": "ja", "originCountries": ["JP", "FR"]},
        )

    def test_writing_again_replaces_rather_than_appends(self):
        for origin in (
            {"originalLanguage": "ja", "originCountries": ["JP", "FR"]},
            {"originalLanguage": "fr", "originCountries": ["FR"]},
        ):
            next_metadata.replace_movie_film_origin(self.conn, self.movie_id, origin)
            self.conn.commit()
        self.assertEqual(
            next_metadata.movie_film_origin(self.conn, self.movie_id),
            {"originalLanguage": "fr", "originCountries": ["FR"]},
        )

    def test_an_empty_answer_clears_the_stored_origin(self):
        # An empty dict is TMDB saying it does not know, which is authoritative.
        # "Nobody asked" is a None and never reaches this function.
        next_metadata.replace_movie_film_origin(
            self.conn, self.movie_id, {"originalLanguage": "ja", "originCountries": ["JP"]}
        )
        self.conn.commit()
        next_metadata.replace_movie_film_origin(self.conn, self.movie_id, {})
        self.conn.commit()
        self.assertEqual(
            next_metadata.movie_film_origin(self.conn, self.movie_id),
            {"originalLanguage": "", "originCountries": []},
        )

    def test_deleting_the_movie_takes_its_origin_countries_with_it(self):
        next_metadata.replace_movie_film_origin(
            self.conn, self.movie_id, {"originalLanguage": "ja", "originCountries": ["JP"]}
        )
        self.conn.commit()
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM movies WHERE id=%s", (self.movie_id,))
            cur.execute(
                "SELECT count(*) AS n FROM movie_origin_countries WHERE movie_id=%s",
                (self.movie_id,),
            )
            self.assertEqual(cur.fetchone()["n"], 0)
        self.conn.commit()

    def test_the_release_country_is_left_alone_by_an_origin_write(self):
        # The failure this guards is silent and permanent: deriving one from the
        # other records the Netherlands as the origin of every Japanese film on a
        # European pressing.
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE movies SET country=%s, language=%s WHERE id=%s",
                ("NL", "Dutch", self.movie_id),
            )
        self.conn.commit()
        next_metadata.replace_movie_film_origin(
            self.conn, self.movie_id, {"originalLanguage": "ja", "originCountries": ["JP"]}
        )
        self.conn.commit()
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT country, language, original_language FROM movies WHERE id=%s",
                (self.movie_id,),
            )
            row = cur.fetchone()
        self.assertEqual(row["country"], "NL")
        self.assertEqual(row["language"], "Dutch")
        self.assertEqual(row["original_language"], "ja")


if __name__ == "__main__":
    unittest.main()
