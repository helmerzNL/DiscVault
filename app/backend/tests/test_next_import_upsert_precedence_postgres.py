"""An import fills blank fields; it never overwrites filled ones.

Only a real PostgreSQL proves this. The rule lives entirely in an
`ON CONFLICT ... DO UPDATE` clause — the argument order inside each COALESCE,
`NULLIF` treating '' as blank, and the side of `||` that wins for the metadata
blob. A fake cursor executes none of that, so the whole rule would be invisible
to a test that stubs the database out.

Each test imports a row once (creating the film), curates it the way a user
would, then re-imports the same row with different values. That is the exact
path a repeated import takes, identity matching included.
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

from app.backend.next_worker import upsert_import_movie


DATABASE_URL = os.environ.get("DATABASE_URL")

PLUGIN_ID = "import_precedence_test"


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class ImportUpsertPrecedenceTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def setUp(self):
        self.external_id = f"precedence-{uuid.uuid4()}"

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM movies WHERE metadata->>'import_source' = %s", (PLUGIN_ID,))
            conn.commit()

    def _import(self, conn, **item):
        movie_id, was_created = upsert_import_movie(conn, PLUGIN_ID, {"externalId": self.external_id, **item})
        conn.commit()
        return movie_id, was_created

    def _curate(self, conn, movie_id, **columns):
        """Whatever the user (or a metadata refresh) put on the film."""
        assignments = ", ".join(f"{name}=%s" for name in columns)
        with conn.cursor() as cur:
            cur.execute(f"UPDATE movies SET {assignments} WHERE id=%s", (*columns.values(), movie_id))
        conn.commit()

    def _row(self, conn, movie_id):
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM movies WHERE id=%s", (movie_id,))
            return cur.fetchone()

    def test_a_new_film_is_written_in_full(self):
        with self.connect() as conn:
            movie_id, was_created = self._import(
                conn,
                title="Basic Instinct",
                year="1992",
                format="4K UHD",
                barcode="5053083230142",
            )
            row = self._row(conn, movie_id)
        self.assertTrue(was_created)
        self.assertEqual(row["title"], "Basic Instinct")
        self.assertEqual(row["year"], "1992")
        self.assertEqual(row["format"], "4K UHD")
        self.assertEqual(row["barcode"], "5053083230142")

    def test_a_filled_field_survives_an_import_that_disagrees(self):
        with self.connect() as conn:
            movie_id, _ = self._import(conn, title="Bohemian Rhapsody", year="2018", format="Blu-ray")
            self._curate(conn, movie_id, title="Bohemian Rhapsody", year="2018")
            # The pre-26.8.5 shape of a Blu-ray.com row: packaging title, disc year.
            self._import(conn, title="Bohemian Rhapsody 4K", year="2019", format="4K UHD")
            row = self._row(conn, movie_id)
        self.assertEqual(row["title"], "Bohemian Rhapsody")
        self.assertEqual(row["year"], "2018")
        self.assertEqual(row["format"], "Blu-ray")

    def test_a_blank_field_is_filled_from_the_import(self):
        with self.connect() as conn:
            movie_id, _ = self._import(conn, title="Bohemian Rhapsody")
            self._curate(conn, movie_id, year=None, barcode=None, overview=None, country="")
            self._import(
                conn,
                title="Bohemian Rhapsody",
                year="2018",
                barcode="8712626064312",
                overview="A Queen biopic.",
                country="NL",
            )
            row = self._row(conn, movie_id)
        self.assertEqual(row["year"], "2018")
        self.assertEqual(row["barcode"], "8712626064312")
        self.assertEqual(row["overview"], "A Queen biopic.")
        # '' is blank, not a value worth protecting.
        self.assertEqual(row["country"], "NL")

    def test_a_second_import_updates_rather_than_duplicates(self):
        with self.connect() as conn:
            first_id, first_created = self._import(conn, title="Bohemian Rhapsody", year="2018")
            second_id, second_created = self._import(conn, title="Bohemian Rhapsody 4K", year="2019")
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) AS n FROM movies WHERE metadata->>'import_source' = %s", (PLUGIN_ID,))
                count = cur.fetchone()["n"]
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first_id, second_id)
        self.assertEqual(count, 1)

    def test_a_field_confirmed_in_the_review_does_overwrite(self):
        # Fill-don't-overwrite protects the film from the file, not from its
        # owner. A reviewer who picks a match is editing this film deliberately,
        # and declining that write would report "applied" while changing nothing.
        with self.connect() as conn:
            movie_id, _ = self._import(conn, title="Bohemian Rapsody", year="2018")
            self._import(
                conn,
                title="Bohemian Rhapsody",
                year="2018",
                barcode="8712626064312",
                importReviewMatch={"title": "Bohemian Rhapsody", "year": "2018"},
            )
            row = self._row(conn, movie_id)
        self.assertEqual(row["title"], "Bohemian Rhapsody")
        self.assertEqual(row["sort_title"], "Bohemian Rhapsody")

    def test_review_confirmation_does_not_license_the_other_fields(self):
        # The reviewer confirmed a title. That says nothing about the format the
        # file happens to carry, so the stored format still wins.
        with self.connect() as conn:
            movie_id, _ = self._import(conn, title="Bohemian Rapsody", format="Blu-ray")
            self._import(
                conn,
                title="Bohemian Rhapsody",
                format="4K UHD",
                importReviewMatch={"title": "Bohemian Rhapsody"},
            )
            row = self._row(conn, movie_id)
        self.assertEqual(row["title"], "Bohemian Rhapsody")
        self.assertEqual(row["format"], "Blu-ray")

    def test_a_blank_field_in_the_review_match_licenses_nothing(self):
        with self.connect() as conn:
            movie_id, _ = self._import(conn, title="Bohemian Rhapsody", year="2018")
            self._import(
                conn,
                title="Bohemian Rhapsody 4K",
                year="2019",
                importReviewMatch={"title": "", "overview": "Something"},
            )
            row = self._row(conn, movie_id)
        self.assertEqual(row["title"], "Bohemian Rhapsody")
        self.assertEqual(row["year"], "2018")

    def test_metadata_keeps_film_data_and_refreshes_provenance(self):
        with self.connect() as conn:
            movie_id, _ = self._import(
                conn,
                title="Bohemian Rhapsody",
                director="Bryan Singer",
                sourceFile="/data/import/first.csv",
            )
            self._import(
                conn,
                title="Bohemian Rhapsody",
                director="Someone Else",
                actor="Rami Malek",
                sourceFile="/data/import/second.csv",
            )
            row = self._row(conn, movie_id)
        metadata = row["metadata"]
        # Film data already present is kept…
        self.assertEqual(metadata["director"], "Bryan Singer")
        # …a key the film did not have is filled…
        self.assertEqual(metadata["actor"], "Rami Malek")
        # …and provenance describes the import that ran last.
        self.assertEqual(metadata["source_file"], "/data/import/second.csv")
        self.assertEqual(metadata["import_source"], PLUGIN_ID)


if __name__ == "__main__":
    unittest.main()
