"""Which `movie_credits` rows the statistics page counts as directors and actors.

The "Top 10 Directors" and "Top 10 Actors" charts read empty on a collection
of eighty films. Nothing was wrong with the films, the credits, or the charts:
the query asked the wrong column.

A credit is stored across two columns, and which one carries the answer depends
on where the row came from.

    written by                credit_type   job
    ------------------------  ------------  -----------
    metadata refresh, cast    'actor'       NULL
    metadata refresh, crew    'crew'        'Director'
    legacy import             'director'    'Director'
                              'actor'       NULL

`normalize_credit_role` in next_metadata.py folds every crew role into 'crew'
and keeps the provider's own wording in `job`, where TMDB writes 'Director'
with a capital D. `import_movie_credits` in next_import.py copies the old
database's role straight into `credit_type` without normalising it at all.

So `WHERE mc.job = 'director'` missed the refreshed rows on case and the
imported ones on column, and `WHERE mc.job = 'actor'` could never match
anything, because no cast row has ever carried that job.

These are database-shaped claims, so they are tested against a real
PostgreSQL: the bug was not in any Python branch a stub could exercise, it was
in what the SQL selected. The tests below run the endpoint's own predicates -
imported from `STATS_TOP_CREDIT_FILTERS` rather than copied - against rows in
both storage shapes.

The two negative cases are the ones worth keeping. 'Director of Photography'
and 'Assistant Director' both contain the word "director" and neither directed
the film, so a substring match would quietly repopulate these charts with the
wrong people - a failure that looks like success.
"""

import os
import sys
import unittest
import uuid


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_root not in sys.path:
    sys.path.insert(0, backend_root)

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:  # pragma: no cover - minimal environments
    psycopg = None
    dict_row = None

DATABASE_URL = os.environ.get("DATABASE_URL")


def _credit_filters():
    """The predicates the statistics endpoint actually uses."""
    from next_app import STATS_TOP_CREDIT_FILTERS

    return STATS_TOP_CREDIT_FILTERS


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class StatsTopCreditsTests(unittest.TestCase):
    PROBE_PREFIX = "stats-credits-probe-"

    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)

    def setUp(self):
        self.tag = f"{self.PROBE_PREFIX}{uuid.uuid4()}"
        self.addCleanup(self._remove_probe_rows)
        self.filters = _credit_filters()
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movies (id, public_id, title)
                VALUES (gen_random_uuid(), %s, %s)
                RETURNING id
                """,
                (f"{self.tag}-movie", f"{self.tag} film"),
            )
            self.movie_id = cur.fetchone()["id"]

    def _remove_probe_rows(self):
        with self.connect() as conn, conn.cursor() as cur:
            # movie_credits cascades from both sides.
            cur.execute("DELETE FROM movies WHERE public_id LIKE %s", (f"{self.PROBE_PREFIX}%",))
            cur.execute("DELETE FROM people WHERE name LIKE %s", (f"{self.PROBE_PREFIX}%",))

    def _add_credit(self, name, *, credit_type, job=None):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO people (id, public_id, name)
                VALUES (gen_random_uuid(), %s, %s)
                RETURNING id
                """,
                (f"{self.tag}-{uuid.uuid4()}", f"{self.PROBE_PREFIX}{name}"),
            )
            person_id = cur.fetchone()["id"]
            cur.execute(
                """
                INSERT INTO movie_credits (id, movie_id, person_id, credit_type, job, sort_order)
                VALUES (gen_random_uuid(), %s, %s, %s, %s, 0)
                """,
                (self.movie_id, person_id, credit_type, job),
            )
        return f"{self.PROBE_PREFIX}{name}"

    def _matches(self, kind):
        """Names the endpoint's predicate selects for this movie."""
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT p.name
                FROM movies m
                JOIN movie_credits mc ON mc.movie_id = m.id
                JOIN people p ON p.id = mc.person_id
                WHERE m.id = %s AND m.deleted_at IS NULL AND {self.filters[kind]}
                ORDER BY p.name
                """,
                (self.movie_id,),
            )
            return [row["name"] for row in cur.fetchall()]

    # -- the shape a metadata refresh writes ---------------------------------

    def test_a_refreshed_director_is_found_despite_the_capital_d(self):
        name = self._add_credit("Refreshed Director", credit_type="crew", job="Director")
        self.assertIn(name, self._matches("director"))

    def test_a_refreshed_cast_row_is_an_actor_even_with_no_job(self):
        # This is the case that could never have worked: a cast row carries no
        # job at all, so the old `job = 'actor'` matched nothing, ever.
        name = self._add_credit("Refreshed Actor", credit_type="actor", job=None)
        self.assertIn(name, self._matches("actor"))

    # -- the shape the legacy importer writes --------------------------------

    def test_an_imported_director_is_found_in_the_credit_type_column(self):
        name = self._add_credit("Imported Director", credit_type="director", job="Director")
        self.assertIn(name, self._matches("director"))

    def test_an_imported_cast_row_is_an_actor(self):
        name = self._add_credit("Imported Actor", credit_type="actor", job=None)
        self.assertIn(name, self._matches("actor"))

    def test_a_cast_row_stored_as_cast_still_counts(self):
        name = self._add_credit("Cast Row", credit_type="cast", job=None)
        self.assertIn(name, self._matches("actor"))

    # -- the negatives, which are the point ----------------------------------

    def test_a_cinematographer_is_not_a_director(self):
        name = self._add_credit("Photography Person", credit_type="crew", job="Director of Photography")
        self.assertNotIn(name, self._matches("director"))

    def test_an_assistant_director_is_not_a_director(self):
        name = self._add_credit("Assistant Person", credit_type="crew", job="Assistant Director")
        self.assertNotIn(name, self._matches("director"))

    def test_a_writer_is_neither(self):
        name = self._add_credit("Writer Person", credit_type="crew", job="Screenplay")
        self.assertNotIn(name, self._matches("director"))
        self.assertNotIn(name, self._matches("actor"))

    def test_a_director_is_not_also_counted_as_an_actor(self):
        name = self._add_credit("Only Directed", credit_type="crew", job="Director")
        self.assertNotIn(name, self._matches("actor"))

    # -- the regression itself -----------------------------------------------

    def test_a_full_collection_does_not_report_empty_charts(self):
        """The reported symptom: credits present, both charts blank."""
        director = self._add_credit("Real Director", credit_type="crew", job="Director")
        actor = self._add_credit("Real Actor", credit_type="actor", job=None)
        directors = self._matches("director")
        actors = self._matches("actor")
        self.assertTrue(directors, "the directors chart would render empty")
        self.assertTrue(actors, "the actors chart would render empty")
        self.assertIn(director, directors)
        self.assertIn(actor, actors)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
