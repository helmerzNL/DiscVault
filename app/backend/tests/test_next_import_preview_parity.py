"""The import preview must classify exactly what the writer will do.

`import_source_conflicts` (the preview, in next_app.py) and
`import_movie_existing_id` (the writer, in next_worker.py) used to carry two
hand-written copies of "is this the same film". They drifted: the preview took
whichever row Postgres happened to return first, applied no format check, and
matched against any provider identifier a plugin returned - not just the two
(`tmdb`/`imdb`) the writer ever persists or queries. Worse, the preview never
saw its own earlier items: a batch importing "Alien" twice previewed two
creates, because the writer's real 447-vs-460 discrepancy (#791) traces to
the second "Alien" landing as an update against the first the writer had
already inserted, one connection, sequentially - a fact the preview's
per-item DB-only query could never observe.

Both sides now call the shared `resolve_import_identity_match` in
next_common.py. These tests reproduce the batch shapes that used to diverge:
duplicate rows within one import (the direct #791 reproduction), an existing
DB row a batch item should attach to instead of creating, and a plain unique
row that must stay a "new" verdict either way.
"""

from __future__ import annotations

import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_app


class FakeCursor:
    """Answers the handful of SELECTs `import_source_conflicts` issues.

    Routes by a substring of the query text rather than modelling a real
    database, since the preview's SQL shapes are the thing under test (they
    must match the writer's) - not a SQL engine.
    """

    def __init__(self, movies: list[dict], identifiers: list[tuple[str, str, str]]):
        self.movies = movies
        self.identifiers = identifiers
        self._pending: list[dict] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, query: str, params: tuple = ()) -> None:
        flat = " ".join(query.split())
        if "to_regclass" in flat:
            self._pending = [{"table_name": "public.movies"}]
            return
        if "FROM movies WHERE barcode" in flat:
            (barcode,) = params
            self._pending = [m for m in self.movies if m.get("barcode") == barcode][:1]
            return
        if "FROM movie_identifiers" in flat:
            provider, identifier = params
            movie_ids = {mid for (p, ident, mid) in self.identifiers if p == provider and ident == identifier}
            rows = [m for m in self.movies if m["id"] in movie_ids]
            rows.sort(key=lambda m: m.get("_order", 0), reverse=True)
            self._pending = rows
            return
        if "lower(title)=lower" in flat:
            match_title, match_year = params
            rows = [
                m
                for m in self.movies
                if m["title"].casefold() == match_title.casefold() and m["year"] == match_year
            ]
            rows.sort(key=lambda m: m.get("_order", 0), reverse=True)
            self._pending = rows
            return
        self._pending = []

    def fetchall(self) -> list[dict]:
        return self._pending

    def fetchone(self):
        return self._pending[0] if self._pending else None


class FakeConn:
    def __init__(self, movies: list[dict], identifiers: list[tuple[str, str, str]] | None = None):
        self.movies = movies
        self.identifiers = identifiers or []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.movies, self.identifiers)


def _movie(id_, title, year, barcode="", format_="", order=0) -> dict:
    return {"id": id_, "title": title, "year": year, "barcode": barcode, "format": format_, "_order": order}


class ImportPreviewMatchesWriterTests(unittest.TestCase):
    def test_duplicate_rows_in_one_batch_are_not_both_previewed_as_creates(self):
        """The direct #791 reproduction: importing the same film twice in one
        batch must preview one create and one existing-match, matching what
        the writer does when it inserts the first and then finds it for the
        second - not two creates, which is where 460-vs-447 came from."""
        conn = FakeConn(movies=[])
        items = [
            {"title": "Alien", "year": "1979", "barcode": ""},
            {"title": "Alien", "year": "1979", "barcode": ""},
        ]

        conflicts = next_app.import_source_conflicts(conn, items)

        self.assertEqual([c["state"] for c in conflicts], ["new", "existing"])
        self.assertEqual(conflicts[1]["match"]["reason"], "title_year")
        self.assertTrue(str(conflicts[1]["match"]["id"]).startswith("pending-"))

    def test_in_batch_duplicates_with_compatible_formats_still_merge(self):
        """A blank/compatible format on the second item must not block the
        in-batch match - only a genuinely different, recognised format does."""
        conn = FakeConn(movies=[])
        items = [
            {"title": "Aliens", "year": "1986", "format": "Blu-ray"},
            {"title": "Aliens", "year": "1986", "format": ""},
        ]

        conflicts = next_app.import_source_conflicts(conn, items)

        self.assertEqual([c["state"] for c in conflicts], ["new", "existing"])

    def test_in_batch_duplicates_with_incompatible_formats_stay_separate(self):
        """A DVD copy of a film already claimed as Blu-ray in this same batch
        must not attach to it - same rule the writer applies to real rows."""
        conn = FakeConn(movies=[])
        items = [
            {"title": "Aliens", "year": "1986", "format": "Blu-ray"},
            {"title": "Aliens", "year": "1986", "format": "DVD"},
        ]

        conflicts = next_app.import_source_conflicts(conn, items)

        self.assertEqual([c["state"] for c in conflicts], ["new", "new"])

    def test_an_existing_database_row_is_previewed_as_an_update(self):
        """A batch item matching a row already in the database (not another
        batch item) must preview as existing, via barcode."""
        conn = FakeConn(movies=[_movie("11111111-1111-1111-1111-111111111111", "Predator", "1987", barcode="123456")])
        items = [{"title": "Predator", "year": "1987", "barcode": "123456"}]

        conflicts = next_app.import_source_conflicts(conn, items)

        self.assertEqual(conflicts[0]["state"], "existing")
        self.assertEqual(conflicts[0]["match"]["reason"], "barcode")
        self.assertEqual(conflicts[0]["match"]["id"], "11111111-1111-1111-1111-111111111111")

    def test_an_existing_database_row_matches_by_writer_scoped_identifier(self):
        """Only the two providers the writer itself persists (`tmdb`/`imdb`)
        may drive an existing-row match - a broader identifier set here would
        report a conflict the writer can never actually find."""
        conn = FakeConn(
            movies=[_movie("22222222-2222-2222-2222-222222222222", "The Thing", "1982")],
            identifiers=[("tmdb", "1091", "22222222-2222-2222-2222-222222222222")],
        )
        items = [{"title": "The Thing", "year": "1982", "tmdbId": "1091"}]

        conflicts = next_app.import_source_conflicts(conn, items)

        self.assertEqual(conflicts[0]["state"], "existing")
        self.assertEqual(conflicts[0]["match"]["reason"], "tmdb")

    def test_a_plugin_only_identifier_never_drives_a_match(self):
        """A plugin's own identifier namespace (anything other than
        tmdb/imdb) is not one the writer ever queries or stores, so it must
        never manufacture a conflict here either."""
        conn = FakeConn(
            movies=[_movie("33333333-3333-3333-3333-333333333333", "Some Other Film", "1990")],
            identifiers=[("movievault", "px-1", "33333333-3333-3333-3333-333333333333")],
        )
        items = [{"title": "Predator 2", "year": "1990", "identifiers": {"movievault": "px-1"}}]

        conflicts = next_app.import_source_conflicts(conn, items)

        self.assertEqual(conflicts[0]["state"], "new")

    def test_an_unrelated_unique_row_previews_as_a_plain_create(self):
        """A row sharing nothing with any existing movie or any other batch
        item must preview as a plain create, unaffected by the batch pool."""
        conn = FakeConn(movies=[_movie("44444444-4444-4444-4444-444444444444", "Predator", "1987")])
        items = [
            {"title": "Predator", "year": "1987"},
            {"title": "The Terminator", "year": "1984"},
        ]

        conflicts = next_app.import_source_conflicts(conn, items)

        self.assertEqual(conflicts[0]["state"], "existing")
        self.assertEqual(conflicts[1]["state"], "new")
        self.assertIsNone(conflicts[1]["match"])

    def test_a_later_row_prefers_the_batchs_own_row_over_a_format_incompatible_database_row(self):
        """A database row with an incompatible format must not block item 1
        from also being previewed against item 2's own (compatible) pending
        entry - the format filter applies per-candidate, not per-title+year,
        exactly as `best_format_compatible_match` does for the writer."""
        conn = FakeConn(movies=[_movie("55555555-5555-5555-5555-555555555555", "Predator", "1987", format_="DVD")])
        items = [
            {"title": "Predator", "year": "1987", "format": "Blu-ray"},
            {"title": "Predator", "year": "1987", "format": "Blu-ray"},
        ]

        conflicts = next_app.import_source_conflicts(conn, items)

        # Item 1's Blu-ray does not fit the existing DVD row, so it previews
        # as its own create - the same "different edition" outcome the
        # writer reaches for a real Blu-ray import against a DVD-only row.
        self.assertEqual(conflicts[0]["state"], "new")
        # Item 2 then attaches to item 1's own pending Blu-ray entry instead.
        self.assertEqual(conflicts[1]["state"], "existing")
        self.assertTrue(str(conflicts[1]["match"]["id"]).startswith("pending-"))

    def test_a_shared_series_provider_id_does_not_match_the_wrong_season(self):
        """A shared TMDb/IMDb id can identify a whole TV series, so a
        provider-identifier row that disagrees with the import on *both*
        title and year is the wrong season and must preview as a create, not
        an update against that row (#793) - the preview must reject it the
        same way the writer does."""
        conn = FakeConn(
            movies=[
                _movie(
                    "66666666-6666-6666-6666-666666666666",
                    "Star Trek: Lower Decks: Season 1",
                    "2020",
                    format_="Blu-ray",
                )
            ],
            identifiers=[("imdb", "tt9184820", "66666666-6666-6666-6666-666666666666")],
        )
        items = [
            {
                "title": "Star Trek: Lower Decks: Season 2",
                "year": "2021",
                "format": "Blu-ray",
                "imdbId": "tt9184820",
            }
        ]

        conflicts = next_app.import_source_conflicts(conn, items)

        self.assertEqual(conflicts[0]["state"], "new")
        self.assertIsNone(conflicts[0]["match"])


if __name__ == "__main__":
    unittest.main()
