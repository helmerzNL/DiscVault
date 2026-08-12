"""The database ids a record is known by, and who may fill one in.

Two different stores, and the difference is the whole subject. `tmdb`/`imdb`
name the *film* and live in `movie_identifiers`; a television id names the
*show*, is shared by every release of it, and lives on the series. An edit
surface that blurred the two would write a television id onto one pressing and
leave every other release of the same series without it.

They are editable at all because a source can only supply an id for a title it
recognised, and the title it did not recognise is exactly the one somebody has
to identify by hand -- for a series, the difference between a page with a
synopsis and artwork and a page that can never have either.

The bug guarded at the bottom is the one that made them look absent in the
first place: picking a candidate overwrote the ids the sources had found with
the blanks the browser sends for the services that candidate says nothing
about.
"""

import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_app


UI_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "next_views_ui.py")
)


class TheEditScreenOffersEveryIdTests(unittest.TestCase):
    """The panel is a Python string, so nothing but a read of the source
    notices when a field stops being rendered or stops being saved."""

    @classmethod
    def setUpClass(cls):
        with open(UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()

    def _function(self, name):
        start = self.source.index(f"function {name}(")
        return self.source[start:self.source.index("\n    function ", start + 1)]

    def test_the_film_ids_are_on_the_release_tab(self):
        self.assertIn('id="movieEditTmdbId"', self.source)
        self.assertIn('id="movieEditImdbId"', self.source)

    def test_the_television_ids_are_on_the_series_tab(self):
        """Not beside the film's own ids. A television id belongs to the show,
        so putting it in the Release block would invite the reading that this
        pressing has one of its own."""
        series_section = self.source[
            self.source.index('id="movieEditSectionSeries"'):
            self.source.index('id="movieEditReleaseTechnicalSection"')
        ]
        self.assertIn('id="movieEditSeriesTvdbId"', series_section)
        self.assertIn('id="movieEditSeriesTmdbId"', series_section)

    def test_the_film_ids_are_written_to_the_movie_route(self):
        body = self._function("saveMovieSourceIds")
        self.assertIn("/api/next/movies/", body)
        self.assertIn('"POST"', body)
        # An emptied field is a removal, and the POST route has no way to say
        # "this record has none".
        self.assertIn('"DELETE"', body)

    def test_the_television_ids_are_written_to_the_series_route(self):
        body = self._function("saveMovieSeriesIds")
        self.assertIn("/api/next/series/", body)
        self.assertIn("movieEditSeriesIdTarget", body)
        self.assertNotIn("/api/next/movies/", body)

    def test_an_unchanged_field_is_not_resent(self):
        """The baseline is what separates "left alone" from "cleared". Without
        it every save would re-send both ids, and an emptied field would be
        indistinguishable from one nobody touched -- which on the series route
        means a removal nobody asked for."""
        body = self._function("collectMovieEditIdChanges")
        self.assertIn("baseline[field[key]]", body)
        self.assertIn("continue", body)

    def test_a_typed_id_is_validated_before_it_is_stored(self):
        """The routes accept any string, so a typo would be stored and only
        ever surface as a link that opens the wrong film."""
        body = self._function("collectMovieEditIdChanges")
        self.assertIn("field.valid.test(value)", body)
        self.assertIn("error.focusInput", body)

    def test_a_series_with_no_link_yet_cannot_be_given_an_id(self):
        """An id needs a series to hang off. Disabled rather than hidden, with
        the reason on screen beside the link that would fix it."""
        body = self._function("fillMovieEditSeriesIds")
        self.assertIn("input.disabled = !movieEditSeriesIdTarget", body)
        self.assertIn("movieEditSeriesIdsLinkFirst", body)

    def test_the_page_is_reread_after_an_id_is_written(self):
        """The PATCH response was built before the identifier writes ran, so
        rendering it would leave the film's IMDb and TMDb chips a reload behind
        and make a save that worked look ignored."""
        start = self.source.index("async function saveMovieDetails(")
        body = self.source[start:self.source.index("\n    function previousMovieEditFieldValue", start)]
        self.assertIn("identifiersWritten", body)
        self.assertIn("await saveMovieSourceIds()", body)
        self.assertIn("await saveMovieSeriesIds()", body)


class TheIdsAreDescribedInEveryLocaleTests(unittest.TestCase):
    """`test_next_i18n_completeness` already holds every locale to en-US's key
    set. What it cannot see is a *label* that was never given a key at all, so
    the one thing worth pinning here is that none of the new fields is
    hard-coded English in the markup."""

    @classmethod
    def setUpClass(cls):
        with open(UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()

    def test_every_new_field_carries_an_i18n_key(self):
        for key in (
            "movieDetail.sourceIds",
            "movieDetail.sourceIdsHint",
            "movieDetail.tmdbId",
            "movieDetail.imdbId",
            "movieDetail.seriesIds",
            "movieDetail.seriesIdsHint",
            "movieDetail.seriesIdsLinkFirst",
            "movieDetail.tvdbId",
            "movieDetail.tmdbTvId",
        ):
            self.assertIn(key, self.source, key)

    def test_the_refusals_are_translated_too(self):
        """A validation message is the one piece of text a user only ever sees
        when something went wrong, which is the worst moment to switch
        language on them."""
        self.assertIn("movieDetail.idDigitsOnly", self.source)
        self.assertIn("movieDetail.idImdbFormat", self.source)


class APickedCandidateStatesOnlyTheIdsItHasTests(unittest.TestCase):
    """Why the ids looked absent after adding a title.

    The browser builds the candidate's identifier map with a key per service it
    knows about and an empty string where it found nothing, and the pick is
    merged *over* what the sources answered. A TMDB search result names no IMDb
    id, so picking one wrote an empty string over the IMDb id the barcode
    lookup had already found, and the film arrived with a link nothing would
    fill in again until the next refresh.
    """

    def _proposal(self, candidate):
        return next_app.selected_import_movie_candidate_proposal(candidate)

    def test_an_empty_id_is_not_a_statement(self):
        proposal = self._proposal(
            {"title": "The Matrix", "provider": "tmdb", "identifiers": {"tmdb": "603", "imdb": ""}}
        )
        self.assertEqual(proposal["identifiers"], {"tmdb": "603"})

    def test_the_ids_it_does_have_still_travel(self):
        proposal = self._proposal(
            {"title": "The Matrix", "provider": "tmdb", "identifiers": {"tmdb": "603", "imdb": "tt0133093"}}
        )
        self.assertEqual(proposal["identifiers"], {"tmdb": "603", "imdb": "tt0133093"})

    def test_a_pick_no_longer_blanks_an_id_another_source_found(self):
        """The assertion that matters, against the merge that does the
        overwriting rather than against the shape above it."""
        merged = next_app.merge_selected_import_movie_candidate(
            {"movieUpdates": {}, "identifiers": {"imdb": "tt0133093"}},
            next_app.selected_import_movie_candidate_from_body(
                {
                    "selectedMovieCandidate": {
                        "title": "The Matrix",
                        "provider": "tmdb",
                        "identifiers": {"tmdb": "603", "imdb": ""},
                    }
                }
            ),
        )
        self.assertEqual(merged["identifiers"], {"imdb": "tt0133093", "tmdb": "603"})

    def test_the_pick_still_outranks_the_sources_on_an_id_it_does_state(self):
        """Dropping the blanks must not turn into dropping the corrections: a
        person choosing a different film is choosing its ids too."""
        merged = next_app.merge_selected_import_movie_candidate(
            {"movieUpdates": {}, "identifiers": {"tmdb": "999"}},
            next_app.selected_import_movie_candidate_from_body(
                {
                    "selectedMovieCandidate": {
                        "title": "The Matrix",
                        "provider": "tmdb",
                        "identifiers": {"tmdb": "603", "imdb": ""},
                    }
                }
            ),
        )
        self.assertEqual(merged["identifiers"]["tmdb"], "603")


class TheSeriesCarriesItsIdentifiersToTheEditScreenTests(unittest.TestCase):
    """`movie_series_payload` is what the disc's edit screen reads. Without the
    series' identifiers on it the television fields could only ever render
    empty, which reads as "this series has no id" on a series that has one."""

    def test_the_payload_reads_the_series_identifier_table(self):
        import inspect

        body = inspect.getsource(next_app.movie_series_payload)
        self.assertIn("series_identifier_entities", body)
        self.assertIn('series["identifiers"]', body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
