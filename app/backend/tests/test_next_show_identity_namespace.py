"""A confirmed television release is never described by the film namespace.

#826. Two series in a collection -- *Band of Brothers* and *Planet Earth* --
were classified `SHOW`, carried the right television identity, and were then
shown the synopsis, runtime, rating, cast, genres, studios and artwork of
films they have nothing to do with. Not as a suggestion: the values arrived
under **Current** in the provider comparison, because the refresh had merged
them onto the record.

Both cases are the same mechanism, reached by its two doors:

* **By id.** An id is issued *by* a namespace and a bare number does not say
  which. TMDB's 4613 is the series *Band of Brothers* and the film *Scorched*;
  `lookup_external_id` is `/movie/{id}`, so the number on the row resolved to
  the film.
* **By title.** With no film id to use, `movie_details` falls back to a film
  title search. *Planet Earth* (2006, `tv/1044`) matched *Planet Earth* (1974,
  `movie/54805`), and the unrelated film's id was written onto the record
  beside the right one -- `tmdb:1044, tmdb:54805` in the diagnostics.

The demotion guard (`should_apply_media_type`) could not catch either: it
defends the *classification*, so the record stayed a `SHOW` the whole time and
only its description was somebody else's. Provenance needed its own rule.

What is pinned here is that rule and its limits. The stored `media_type` has to
reach the query at all -- it was read for the merge and never travelled -- and a
`SHOW` then withholds the film namespace and nothing else: a barcode lookup,
technical specs and box-set detection are not keyed to a namespace and must
still run, and a preview, where a person is choosing between a film and a show,
must still offer both.
"""

import os
import sys
import unittest
from unittest import mock


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_metadata import movie_lookup_context
from app.backend.next_metadata import plugin_execution_plan
from app.backend.next_metadata import query_from_payload
from app.backend.next_metadata import query_is_confirmed_series
from app.backend.next_metadata import run_metadata_source_pipeline


# TMDB's own shape: a film detail namespace, a film id namespace, and the
# television siblings of both.
TMDB_CAPABILITIES = [
    "search_title",
    "search_series",
    "movie_details",
    "lookup_external_id",
    "lookup_external_series_id",
    "season_episodes",
    "series_details",
]


def _plan(query, capabilities=TMDB_CAPABILITIES):
    return [
        step["entrypoint"]
        for step in plugin_execution_plan({"capabilities": list(capabilities)}, query)
    ]


class TheStoredMediaTypeReachesTheQueryTests(unittest.TestCase):
    """`movie_lookup_context` read `media_type` and kept it to itself.

    It was selected for the merge's demotion rule and landed on the `current`
    record; the *query*, which is what the sources are asked, described a
    confirmed show in exactly the same words as a film. Nothing downstream
    could have known, which is why this is the first thing pinned.
    """

    MOVIE_ROW = {
        "id": "11111111-1111-5111-8111-111111111111",
        "title": "Band of Brothers",
        "original_title": "Band of Brothers",
        "year": "2001",
        "barcode": "5051890110312",
        "format": "Blu-ray",
        "media_type": "SHOW",
        "series_id": "22222222-2222-5222-8222-222222222222",
        "metadata": {},
    }

    def _query(self, row, series_identifiers=None):
        cursor = mock.MagicMock()
        cursor.__enter__ = lambda _self: cursor
        cursor.__exit__ = lambda *_args: False
        cursor.fetchone.return_value = dict(row)
        conn = mock.MagicMock()
        conn.cursor.return_value = cursor
        series_lookup = mock.MagicMock(return_value=dict(series_identifiers or {}))
        with mock.patch("app.backend.next_metadata.movie_identifiers", return_value={"tmdbId": "4613"}), \
             mock.patch("app.backend.next_metadata.movie_technical_specs", return_value={}), \
             mock.patch("app.backend.next_metadata.movie_container_context", return_value=[]), \
             mock.patch("app.backend.next_metadata.movie_genre_keys", return_value=[]), \
             mock.patch("app.backend.next_metadata.series_identifier_map", series_lookup):
            context = movie_lookup_context(conn, self.MOVIE_ROW["id"])
        self.series_lookup = series_lookup
        return context["query"]

    def test_the_series_television_id_travels_beside_the_film_id(self):
        """The show's id lives on its series and names the show; `tmdbId` is
        the film namespace. Both reach the query, under keys that say which
        space each number came from."""
        query = self._query(self.MOVIE_ROW, {"tmdb_tv": "4613", "tvdb": "74205"})
        self.assertEqual(query["tmdbTvId"], "4613")
        self.assertEqual(query["tmdbId"], "4613")
        self.series_lookup.assert_called_once()

    def test_a_disc_with_no_series_states_no_television_id(self):
        query = self._query({**self.MOVIE_ROW, "series_id": None})
        self.assertEqual(query["tmdbTvId"], "")
        self.series_lookup.assert_not_called()

    def test_a_show_says_so(self):
        self.assertEqual(self._query(self.MOVIE_ROW)["mediaType"], "SHOW")

    def test_a_film_says_so(self):
        self.assertEqual(self._query({**self.MOVIE_ROW, "media_type": "MOVIE"})["mediaType"], "MOVIE")

    def test_an_unclassified_record_states_nothing(self):
        """Absent, never `MOVIE`. An unrecognised value becoming a confident
        `MOVIE` here would hand the film namespace a record nobody classified
        -- `normalize_media_type`'s own rule, and the reason this reads it."""
        self.assertEqual(self._query({**self.MOVIE_ROW, "media_type": None})["mediaType"], "")
        self.assertEqual(self._query({**self.MOVIE_ROW, "media_type": "cartoon"})["mediaType"], "")

    def test_a_typed_payload_may_state_one_too(self):
        self.assertEqual(query_from_payload({"mediaType": "SHOW"})["mediaType"], "SHOW")
        self.assertEqual(query_from_payload({"workType": "tv"})["mediaType"], "SHOW")
        self.assertEqual(query_from_payload({"title": "Fargo"})["mediaType"], "")


class WhatAConfirmedShowIsTests(unittest.TestCase):
    def test_the_wire_vocabulary_and_its_aliases(self):
        self.assertTrue(query_is_confirmed_series({"mediaType": "SHOW"}))
        self.assertTrue(query_is_confirmed_series({"mediaType": "series"}))

    def test_everything_else_is_not_a_show(self):
        self.assertFalse(query_is_confirmed_series({"mediaType": "MOVIE"}))
        self.assertFalse(query_is_confirmed_series({"mediaType": ""}))
        self.assertFalse(query_is_confirmed_series({}))


class AShowIsNotLookedUpInTheFilmNamespaceTests(unittest.TestCase):
    """The planner half of the fix, for both doors of #826."""

    def test_band_of_brothers_is_not_resolved_as_a_film_id(self):
        """`tv/4613` is the series; `movie/4613` is *Scorched* (2003)."""
        self.assertEqual(
            _plan({"mediaType": "SHOW", "title": "Band of Brothers", "year": "2001", "tmdbId": "4613"}),
            [],
        )

    def test_planet_earth_is_not_resolved_by_a_film_title_search(self):
        """No film id to misread, so `movie_details` fell back to the film
        title search and found *Planet Earth* (1974)."""
        self.assertEqual(
            _plan({"mediaType": "SHOW", "title": "Planet Earth", "year": "2006"}),
            [],
        )

    def test_an_imdb_id_is_not_offered_to_it_either(self):
        """`/find/{imdb}` is read for `movie_results` and nothing else, so a
        series' IMDb id can only ever name a film here."""
        self.assertEqual(
            _plan({"mediaType": "SHOW", "title": "Band of Brothers", "imdbId": "tt0185906"}),
            [],
        )

    def test_a_film_is_unaffected(self):
        self.assertEqual(
            _plan({"mediaType": "MOVIE", "title": "Scorched", "year": "2003", "tmdbId": "4613"}),
            ["lookup_external_id"],
        )

    def test_an_unclassified_record_is_unaffected(self):
        """The overwhelming majority of rows. A record nobody has classified is
        still enriched exactly as before -- the guard needs a stated `SHOW`,
        which is the only value that was ever stated on purpose."""
        self.assertEqual(
            _plan({"title": "Scorched", "year": "2003", "tmdbId": "4613"}),
            ["lookup_external_id"],
        )
        self.assertEqual(_plan({"title": "Scorched", "year": "2003"}), ["movie_details"])


class WhatAShowStillGetsTests(unittest.TestCase):
    """The limit of the rule. Withholding one namespace must not quietly
    withhold everything else a television disc is entitled to."""

    def test_the_barcode_lookup_still_runs(self):
        """A barcode names a pressing, not a namespace: it is how MovieVault
        recognises the disc in the first place, and it is what told DiscVault
        this was a series."""
        self.assertIn(
            "search_barcode",
            _plan(
                {"mediaType": "SHOW", "title": "Planet Earth", "externalBarcode": "4006448360016"},
                capabilities=["search_barcode", "movie_details", "lookup_external_id"],
            ),
        )

    def test_technical_specs_still_run(self):
        """Disc properties -- codecs, audio tracks, aspect ratio -- belong to
        the pressing and are the same question whatever is printed on it."""
        self.assertIn(
            "technical_specs",
            _plan(
                {"mediaType": "SHOW", "title": "Planet Earth", "tmdbId": "1044"},
                capabilities=["technical_specs", "movie_details", "lookup_external_id"],
            ),
        )

    def test_box_set_detection_still_runs(self):
        """A season pack is a box set, so this is a question shows ask more
        often than films do."""
        self.assertIn(
            "box_set_candidates",
            _plan(
                {
                    "mediaType": "SHOW",
                    "title": "Band of Brothers",
                    "detectBoxSets": True,
                    "tmdbId": "4613",
                },
                capabilities=["box_set_candidates", "movie_details", "lookup_external_id"],
            ),
        )

    def test_a_person_choosing_is_still_offered_both_namespaces(self):
        """A preview is somebody searching, and the ambiguity is the point: a
        number is asked of both namespaces so the person can pick. Narrowing
        that would re-break the search this repository deliberately widened."""
        self.assertEqual(
            _plan({"previewMode": True, "mediaType": "SHOW", "tmdbId": "4613"}),
            ["lookup_external_id", "lookup_external_series_id"],
        )


class TheRefreshDoesNotMergeAFilmOntoAShowTests(unittest.TestCase):
    """End to end, which is the claim the issue actually makes.

    Not planning a step is one thing; what reached the record is another -- and
    for TMDB in a refresh they are not even the same code. TMDB is lifted out
    of the planner's pass and asked `movie_details` by name, so a planner-only
    fix would have left the whole of #826 in place. `movie_details` is also
    where both doors meet: it tries `lookup_external_id` (`/movie/{id}`, the
    *Scorched* half) and falls back to a film title search (the *Planet Earth*
    half), which is why one call site accounts for both reported cases.
    """

    PLUGINS = [
        {
            "id": "tmdb",
            "name": "TMDb",
            "categories": ["metadata_source"],
            "capabilities": TMDB_CAPABILITIES,
            "manifest": {"capabilities": TMDB_CAPABILITIES},
            "order_index": 10,
        }
    ]

    # What `/movie/4613` answers: *Scorched* (2003), which is what landed on
    # *Band of Brothers* as its current metadata.
    SCORCHED = {
        "status": "hit",
        "provider": "tmdb",
        "movie": {
            "title": "Scorched",
            "year": "2003",
            "overview": "Bank robbery at Desert Savings Bank",
            "runtimeMinutes": 90,
            "rating": 5.5,
            "posterUrl": "https://image.tmdb.org/t/p/original/scorched.jpg",
        },
        "credits": [{"name": "Gavin Grazer", "role": "director"}],
        "tmdbId": "4613",
    }

    def _run(self, query):
        calls = []

        def execute(plugin_id, entrypoint, payload, _context):
            calls.append((plugin_id, entrypoint))
            return {
                "status": "ok",
                "state": "available",
                "elapsedMs": 1,
                "result": dict(self.SCORCHED),
            }

        with mock.patch("app.backend.next_metadata.metadata_source_plugins", return_value=self.PLUGINS), \
             mock.patch("app.backend.next_metadata.plugin_config_from_db", return_value={"apiKey": "configured"}), \
             mock.patch("app.backend.next_metadata.plugin_execution_context", return_value={}), \
             mock.patch("app.backend.next_metadata.plugin_requires_config", return_value=False), \
             mock.patch("app.backend.next_metadata.preferred_provider_overwrite", return_value=False), \
             mock.patch("app.backend.next_metadata.run_plugin_entrypoint", side_effect=execute):
            result = run_metadata_source_pipeline(
                object(),
                query=query,
                current={"metadata": {}, "media_type": query.get("mediaType") or ""},
                technical_current={},
            )
        return result, calls

    SHOW_QUERY = {
        "mediaType": "SHOW",
        "title": "Band of Brothers",
        "fallbackTitle": "Band of Brothers",
        "year": "2001",
        "tmdbId": "4613",
    }

    def test_tmdb_is_not_asked_about_the_show_as_a_film(self):
        _result, calls = self._run(dict(self.SHOW_QUERY))
        self.assertEqual([entrypoint for _plugin, entrypoint in calls], [])

    def test_no_film_metadata_is_proposed_for_it(self):
        result, _calls = self._run(dict(self.SHOW_QUERY))
        movie_updates = result["proposal"]["movieUpdates"]
        self.assertNotIn("overview", movie_updates)
        self.assertNotIn("runtime_minutes", movie_updates)
        self.assertNotIn("rating", movie_updates)
        self.assertEqual(result["proposal"]["credits"], [])
        self.assertNotIn("Scorched", str(result["proposal"]))

    def test_the_comparison_says_why_rather_than_going_quiet(self):
        """A screen that simply shows nothing reads as a broken provider. The
        state names television as the reason, so the next reader of a report
        like #826 is not left guessing."""
        result, _calls = self._run(dict(self.SHOW_QUERY))
        self.assertEqual(result["enrichment"]["tmdb"]["state"], "series_identity")
        self.assertTrue(result["enrichment"]["tmdb"]["configured"])

    def test_the_same_record_as_a_film_is_still_enriched(self):
        """The control. If this stops passing, the fix has stopped being about
        television and started being about TMDB."""
        result, calls = self._run({**self.SHOW_QUERY, "mediaType": "MOVIE", "title": "Scorched"})
        self.assertIn(("tmdb", "movie_details"), calls)
        self.assertEqual(
            result["proposal"]["movieUpdates"]["overview"],
            "Bank robbery at Desert Savings Bank",
        )
        self.assertEqual(result["enrichment"]["tmdb"]["state"], "enriched")



SHOW_CAPABILITIES = [*TMDB_CAPABILITIES, "show_details"]


class TheShowIsDescribedFromTheTelevisionNamespaceTests(TheRefreshDoesNotMergeAFilmOntoAShowTests):
    """The other half of #826: withholding the film namespace left a confirmed
    show's title record with no source at all for its overview, rating, cast,
    genres, studios or artwork. `show_details` is that source.

    Inherits the film-namespace tests above on purpose: with the television
    capability installed, every one of them must still hold.
    """

    PLUGINS = [
        {
            **TheRefreshDoesNotMergeAFilmOntoAShowTests.PLUGINS[0],
            "capabilities": SHOW_CAPABILITIES,
            "manifest": {"capabilities": SHOW_CAPABILITIES},
        }
    ]

    BAND_OF_BROTHERS = {
        "status": "hit",
        "provider": "tmdb",
        "sourceRef": "tmdb:tv:4613",
        "movie": {
            "title": "Band of Brothers",
            "year": "2001",
            "overview": "Easy Company, from Toccoa to Berchtesgaden.",
            "rating": "8.6",
            "studios": "DreamWorks Television, Playtone",
        },
        "credits": [{"name": "Damian Lewis", "role": "actor", "character": "Richard Winters"}],
        "genreIds": [18, 10752],
        "tmdbTvId": 4613,
        "imdbId": "tt0185906",
    }

    def _run(self, query):
        calls = []
        payloads = []

        def execute(plugin_id, entrypoint, payload, _context):
            calls.append((plugin_id, entrypoint))
            payloads.append(dict(payload))
            answer = self.BAND_OF_BROTHERS if entrypoint == "show_details" else self.SCORCHED
            return {"status": "ok", "state": "available", "elapsedMs": 1, "result": dict(answer)}

        with mock.patch("app.backend.next_metadata.metadata_source_plugins", return_value=self.PLUGINS), \
             mock.patch("app.backend.next_metadata.plugin_config_from_db", return_value={"apiKey": "configured"}), \
             mock.patch("app.backend.next_metadata.plugin_execution_context", return_value={}), \
             mock.patch("app.backend.next_metadata.plugin_requires_config", return_value=False), \
             mock.patch("app.backend.next_metadata.preferred_provider_overwrite", return_value=False), \
             mock.patch("app.backend.next_metadata.run_plugin_entrypoint", side_effect=execute):
            result = run_metadata_source_pipeline(
                object(),
                query=query,
                current={"metadata": {}, "media_type": query.get("mediaType") or ""},
                technical_current={},
            )
        self.payloads = payloads
        return result, calls

    SERIES_QUERY = {**TheRefreshDoesNotMergeAFilmOntoAShowTests.SHOW_QUERY, "tmdbTvId": "4613"}

    def test_the_show_is_asked_by_its_television_id_and_nothing_else(self):
        result, calls = self._run(dict(self.SERIES_QUERY))
        self.assertEqual(calls, [("tmdb", "show_details")])
        self.assertEqual(self.payloads, [{"tmdbTvId": "4613"}])
        self.assertEqual(result["enrichment"]["tmdb"]["state"], "enriched")
        self.assertTrue(result["enrichment"]["tmdb"]["enabled"])

    def test_the_film_number_and_the_title_never_reach_the_television_call(self):
        """`tmdbId` 4613 is *Scorched*. Neither it nor a title may be what the
        show is identified by -- those were the two doors #826 came through."""
        self._run(dict(self.SERIES_QUERY))
        for payload in self.payloads:
            self.assertNotIn("tmdbId", payload)
            self.assertNotIn("title", payload)

    def test_the_show_describes_its_own_title_record(self):
        result, _calls = self._run(dict(self.SERIES_QUERY))
        movie_updates = result["proposal"]["movieUpdates"]
        self.assertEqual(movie_updates["overview"], "Easy Company, from Toccoa to Berchtesgaden.")
        self.assertEqual(movie_updates["rating"], "8.6")
        self.assertNotIn("Scorched", str(result["proposal"]))

    def test_its_number_is_never_filed_as_a_film_id(self):
        result, _calls = self._run(dict(self.SERIES_QUERY))
        self.assertNotIn("tmdb", result["proposal"].get("identifiers") or {})

    def test_an_imdb_id_alone_is_enough_to_ask(self):
        query = {key: value for key, value in self.SHOW_QUERY.items() if key != "tmdbId"}
        result, calls = self._run({**query, "imdbId": "tt0185906"})
        self.assertEqual(calls, [("tmdb", "show_details")])
        self.assertEqual(self.payloads, [{"imdbId": "tt0185906"}])
        self.assertEqual(result["enrichment"]["tmdb"]["state"], "enriched")

    def test_with_neither_id_nothing_is_asked_and_the_state_says_why(self):
        result, calls = self._run(dict(self.SHOW_QUERY))
        self.assertEqual(calls, [])
        self.assertEqual(result["enrichment"]["tmdb"]["state"], "series_identity")

    def test_an_older_tmdb_plugin_without_the_capability_asks_nothing(self):
        """An installed copy is replaced only by a newer bundled one, so an
        instance can run the old plugin for a while. It must get the #827
        behaviour, not a call its plugin cannot answer."""
        self.PLUGINS = TheRefreshDoesNotMergeAFilmOntoAShowTests.PLUGINS
        result, calls = self._run(dict(self.SERIES_QUERY))
        self.assertEqual(calls, [])
        self.assertEqual(result["enrichment"]["tmdb"]["state"], "series_identity")

    def test_a_film_is_never_sent_to_the_television_call(self):
        _result, calls = self._run({**self.SERIES_QUERY, "mediaType": "MOVIE", "title": "Scorched"})
        self.assertIn(("tmdb", "movie_details"), calls)
        self.assertNotIn(("tmdb", "show_details"), calls)

    def test_a_preview_still_offers_both_namespaces_and_not_this_call(self):
        """Preview is a person choosing between a film and a show; the
        confirmed-show rule is about refreshing a stored record."""
        _result, calls = self._run({**self.SERIES_QUERY, "previewMode": True})
        self.assertNotIn(("tmdb", "show_details"), calls)

    # The inherited tests are restated for the television plugin where their
    # expectation is different, and kept where it is the same.
    def test_tmdb_is_not_asked_about_the_show_as_a_film(self):
        _result, calls = self._run(dict(self.SERIES_QUERY))
        self.assertNotIn(("tmdb", "movie_details"), calls)
        self.assertNotIn(("tmdb", "lookup_external_id"), calls)
        self.assertNotIn(("tmdb", "search_title"), calls)

    def test_the_comparison_says_why_rather_than_going_quiet(self):
        result, _calls = self._run(dict(self.SHOW_QUERY))
        self.assertEqual(result["enrichment"]["tmdb"]["state"], "series_identity")
        self.assertTrue(result["enrichment"]["tmdb"]["configured"])


if __name__ == "__main__":
    unittest.main()
