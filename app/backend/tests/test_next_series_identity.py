"""Giving a series the identifier that makes it enrichable.

A series is only ever consulted about when it carries an identifier, and until
now the single place that wrote one was `ensure_series`, on the distribution
feed. A series created any other way was un-enrichable for good: no overview and
no artwork, from any source, ever, while the page reported the same "nothing to
add" it shows for a title nobody has heard of.

What is worth pinning here is not that the route stores a row. It is the two
rules around the storing — that searching is a person's act and never a source's,
and that one identifier may not name two series.
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
from app.backend.next_plugins.tmdb import plugin as tmdb


DATABASE_URL = os.environ.get("DATABASE_URL")
PREFIX = "series-identity-test"


class SearchIsAPersonsActTests(unittest.TestCase):
    """`series_details` refuses to search; `search_series` is built to.

    That is not a contradiction, and the distinction is the whole reason this
    feature is allowed to exist: a *source* matching on title is a guess wearing
    an answer's clothes, while a person choosing from a list is identity being
    asserted by someone who can be wrong on purpose.
    """

    def test_details_still_refuses_to_search(self):
        self.assertEqual(
            tmdb.series_details({"title": "Fargo"}, {}),
            {"status": "miss", "provider": "tmdb"},
        )

    def test_search_asks_the_television_namespace(self):
        calls = []

        def fake_request(context, path, **params):
            calls.append((path, params))
            return {"results": []}

        original = tmdb._request
        tmdb._request = fake_request
        try:
            tmdb.search_series({"title": "Fargo"}, {})
        finally:
            tmdb._request = original

        self.assertEqual(calls[0][0], "/search/tv")
        self.assertEqual(calls[0][1]["query"], "Fargo")

    def test_a_candidate_carries_its_namespace_rather_than_a_bare_number(self):
        """Otherwise whoever stores it has to reconstruct which namespace the id
        belongs to — knowledge that lives in the source, not in the caller."""

        def fake_request(context, path, **params):
            return {
                "results": [
                    {
                        "id": 1399,
                        "name": "Example Show",
                        "first_air_date": "2011-04-17",
                        "overview": "Examples.",
                        "poster_path": "/p.jpg",
                    }
                ]
            }

        original = tmdb._request
        tmdb._request = fake_request
        try:
            result = tmdb.search_series({"title": "Example"}, {})
        finally:
            tmdb._request = original

        item = result["items"][0]
        self.assertEqual(item["identifierType"], "tmdb_tv")
        self.assertEqual(item["identifier"], "1399")
        self.assertEqual(item["year"], "2011")
        # A poster and a year are what make choosing informed rather than a coin
        # flip on the first row.
        self.assertTrue(item["posterUrl"])

    def test_an_empty_query_is_skipped_rather_than_sent(self):
        for payload in ({}, {"title": "   "}):
            with self.subTest(payload=payload):
                self.assertEqual(tmdb.search_series(payload, {})["status"], "skipped")


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class SeriesIdentityRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = next_app.app.test_client()

    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM series_identifiers WHERE series_id IN (SELECT id FROM series WHERE public_id LIKE %s)",
                    (f"{PREFIX}-%",),
                )
                cur.execute("DELETE FROM series WHERE public_id LIKE %s", (f"{PREFIX}-%",))
            conn.commit()

    def _series(self, conn, title="Example Show"):
        series_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO series (id, public_id, title, sort_title) VALUES (%s,%s,%s,%s)",
                (series_id, f"{PREFIX}-{series_id}", title, title),
            )
        conn.commit()
        return series_id

    def test_setting_an_identifier_makes_the_series_enrichable(self):
        """Before: `refresh_series_metadata` skips with 'no series identifier'.
        After: it is at least asked. Which is the entire point — the refresh may
        still miss, but it is no longer a dead end."""
        from app.backend import next_metadata

        with self.connect() as conn:
            series_id = self._series(conn)
            self.assertEqual(
                next_metadata.refresh_series_metadata(conn, series_id)["reason"],
                "no series identifier",
            )

        response = self.client.put(
            f"/api/next/series/{series_id}/identifiers",
            json={"identifierType": "tmdb_tv", "identifier": "1399", "providerId": "tmdb"},
        )
        self.assertEqual(response.status_code, 200, response.data[:300])

        with self.connect() as conn:
            self.assertEqual(
                next_metadata.series_identifier_map(conn, series_id), {"tmdb_tv": "1399"}
            )
            self.assertNotEqual(
                next_metadata.refresh_series_metadata(conn, series_id).get("reason"),
                "no series identifier",
            )

    def test_the_same_identifier_may_not_name_two_series(self):
        """Two series answering to one id would make `series_id_for_identifier`
        return whichever the planner reached first, so the feed would start
        attaching discs at random. Merging them is a real operation; it is not
        this one, so this refuses rather than guessing."""
        with self.connect() as conn:
            first = self._series(conn, "First")
            second = self._series(conn, "Second")

        self.assertEqual(
            self.client.put(
                f"/api/next/series/{first}/identifiers",
                json={"identifierType": "tmdb_tv", "identifier": "1399"},
            ).status_code,
            200,
        )
        clash = self.client.put(
            f"/api/next/series/{second}/identifiers",
            json={"identifierType": "tmdb_tv", "identifier": "1399"},
        )
        self.assertEqual(clash.status_code, 409)

    def test_correcting_an_identifier_replaces_rather_than_accumulates(self):
        """`series_identifier_map` keeps the first row it sees, so leaving the old
        one behind would make the effective identity depend on insertion order —
        a correction that silently does nothing."""
        with self.connect() as conn:
            series_id = self._series(conn)

        for value in ("1399", "1400"):
            self.assertEqual(
                self.client.put(
                    f"/api/next/series/{series_id}/identifiers",
                    json={"identifierType": "tmdb_tv", "identifier": value},
                ).status_code,
                200,
            )

        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT identifier FROM series_identifiers WHERE series_id = %s", (series_id,)
                )
                self.assertEqual([row["identifier"] for row in cur.fetchall()], ["1400"])

    def test_an_incomplete_identifier_is_refused(self):
        with self.connect() as conn:
            series_id = self._series(conn)
        for body in ({}, {"identifier": "1399"}, {"identifierType": "tmdb_tv"}, {"identifierType": "tmdb_tv", "identifier": "  "}):
            with self.subTest(body=body):
                self.assertEqual(
                    self.client.put(f"/api/next/series/{series_id}/identifiers", json=body).status_code,
                    400,
                )

    def test_searching_writes_nothing(self):
        """A search is an offer. If it stored anything, a typo in the box would
        become the series' identity."""
        with self.connect() as conn:
            series_id = self._series(conn)

        response = self.client.get(f"/api/next/series/{series_id}/identity/search?q=Example")
        self.assertEqual(response.status_code, 200)
        self.assertIn("candidates", response.get_json()["result"])

        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) AS n FROM series_identifiers WHERE series_id = %s", (series_id,))
                self.assertEqual(cur.fetchone()["n"], 0)

    def test_a_missing_series_is_a_404_on_both_routes(self):
        missing = uuid.uuid4()
        self.assertEqual(self.client.get(f"/api/next/series/{missing}/identity/search").status_code, 404)
        self.assertEqual(
            self.client.put(
                f"/api/next/series/{missing}/identifiers",
                json={"identifierType": "tmdb_tv", "identifier": "1"},
            ).status_code,
            404,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
