"""Regression coverage for the writer's identity match after #791.

`import_movie_existing_id` (next_worker.py) used to carry its own inline
create/update decision. It now delegates to the shared
`resolve_import_identity_match` in next_common.py - the same function the
import preview (`import_source_conflicts` in next_app.py) uses, so the two
can no longer drift the way they did in #791. These tests pin the writer's
own precedence (public_id, then barcode, then format-filtered identifiers,
then format-filtered title+year) so a future change to the shared resolver
that breaks the writer is caught here, not just on the preview side.
"""

from __future__ import annotations

import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_worker


class FakeCursor:
    """Answers the handful of SELECTs `import_movie_existing_id` issues.

    Routes by a substring of the query text, mirroring the writer's exact
    shapes (e.g. `SELECT id FROM movies WHERE barcode=...` returns only
    `id` - no `format` column, unlike the identifier/title_year lookups).
    """

    def __init__(self, movies: list[dict], identifiers: list[tuple[str, str, str]], public_ids: dict[str, str]):
        self.movies = movies
        self.identifiers = identifiers
        self.public_ids = public_ids
        self._pending: list[dict] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, query: str, params: tuple = ()) -> None:
        flat = " ".join(query.split())
        if "to_regclass" in flat:
            self._pending = [{"table_name": "public.movie_identifiers"}]
            return
        if "FROM movies WHERE public_id" in flat:
            (public_id,) = params
            movie_id = self.public_ids.get(public_id)
            self._pending = [{"id": movie_id}] if movie_id else []
            return
        if "FROM movies WHERE barcode" in flat:
            (barcode,) = params
            self._pending = [{"id": m["id"]} for m in self.movies if m.get("barcode") == barcode][:1]
            return
        if "FROM movie_identifiers" in flat:
            provider, identifier = params
            movie_ids = {mid for (p, ident, mid) in self.identifiers if p == provider and ident == identifier}
            rows = [{"id": m["id"], "format": m.get("format", "")} for m in self.movies if m["id"] in movie_ids]
            rows.sort(key=lambda m: next(x for x in self.movies if x["id"] == m["id"]).get("_order", 0), reverse=True)
            self._pending = rows
            return
        if "lower(title)=lower" in flat:
            match_title, match_year = params
            rows = [
                {"id": m["id"], "format": m.get("format", "")}
                for m in self.movies
                if m["title"].casefold() == match_title.casefold() and m["year"] == match_year
            ]
            rows.sort(key=lambda m: next(x for x in self.movies if x["id"] == m["id"]).get("_order", 0), reverse=True)
            self._pending = rows
            return
        self._pending = []

    def fetchall(self) -> list[dict]:
        return self._pending

    def fetchone(self):
        return self._pending[0] if self._pending else None


class FakeConn:
    def __init__(
        self,
        movies: list[dict],
        identifiers: list[tuple[str, str, str]] | None = None,
        public_ids: dict[str, str] | None = None,
    ):
        self.movies = movies
        self.identifiers = identifiers or []
        self.public_ids = public_ids or {}

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.movies, self.identifiers, self.public_ids)


def _movie(id_, title, year, barcode="", format_="", order=0) -> dict:
    return {"id": id_, "title": title, "year": year, "barcode": barcode, "format": format_, "_order": order}


class WriterIdentityMatchTests(unittest.TestCase):
    def test_public_id_pre_check_wins_before_any_shared_matching(self):
        """The writer-only `public_id` pre-check is not part of the shared
        resolver - it must still take priority over everything else."""
        conn = FakeConn(
            movies=[_movie("movie-1", "Alien", "1979", barcode="000111")],
            public_ids={"pub-1": "movie-1"},
        )
        item = {"title": "Something Else", "year": "2000", "barcode": "999999"}

        result = next_worker.import_movie_existing_id(conn, item, public_id="pub-1")

        self.assertEqual(result, "movie-1")

    def test_barcode_match_wins_over_a_title_year_match_that_would_also_fit(self):
        conn = FakeConn(
            movies=[
                _movie("by-barcode", "Alien", "1979", barcode="000111"),
                _movie("by-title-year", "Alien", "1979", barcode="222222"),
            ],
        )
        item = {"title": "Alien", "year": "1979", "barcode": "000111"}

        result = next_worker.import_movie_existing_id(conn, item)

        self.assertEqual(result, "by-barcode")

    def test_identifier_match_skips_a_format_incompatible_row_for_a_compatible_one(self):
        conn = FakeConn(
            movies=[
                _movie("wrong-format", "Alien", "1979", format_="DVD", order=2),
                _movie("right-format", "Alien: Director's Cut", "2003", format_="Blu-ray", order=1),
            ],
            identifiers=[("tmdb", "348", "wrong-format"), ("tmdb", "348", "right-format")],
        )
        item = {"title": "Alien", "year": "1979", "format": "Blu-ray", "tmdbId": "348"}

        result = next_worker.import_movie_existing_id(conn, item)

        self.assertEqual(result, "right-format")

    def test_title_year_match_is_the_last_resort_and_still_format_filtered(self):
        conn = FakeConn(
            movies=[
                _movie("wrong-format", "Aliens", "1986", format_="DVD"),
                _movie("right-format", "Aliens", "1986", format_="Blu-ray"),
            ],
        )
        item = {"title": "Aliens", "year": "1986", "format": "Blu-ray"}

        result = next_worker.import_movie_existing_id(conn, item)

        self.assertEqual(result, "right-format")

    def test_a_plugin_only_identifier_never_drives_a_writer_match(self):
        """The writer only ever persists/queries tmdb+imdb identifiers - a
        plugin-specific provider key must be ignored, same as the preview."""
        conn = FakeConn(
            movies=[_movie("existing", "Some Other Film", "2001")],
            identifiers=[("myplugin", "abc123", "existing")],
        )
        item = {"title": "Predator 2", "year": "1990", "identifiers": {"myplugin": "abc123"}}

        result = next_worker.import_movie_existing_id(conn, item)

        self.assertIsNone(result)

    def test_an_unrelated_row_stays_unmatched(self):
        conn = FakeConn(movies=[_movie("unrelated", "Predator", "1987")])
        item = {"title": "The Terminator", "year": "1984"}

        result = next_worker.import_movie_existing_id(conn, item)

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
