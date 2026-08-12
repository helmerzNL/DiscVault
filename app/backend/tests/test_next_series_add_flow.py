"""A series can be added from the Add screen, not only recognised afterwards.

Searching on title used to reach `/search/movie` and nothing else, so the result
list could not contain a series however hard you looked -- and a television disc
added there arrived as a `MOVIE` with no series behind it and no `tmdb_tv`
identifier. That last part is what made it more than a cosmetic gap: a series
without an identifier is un-enrichable, which is the exact dead end the identity
picker on the series page was built to escape. The Add screen was where those
series were being created.

What is pinned here is the route, not the wording: the television namespace is
planned only for a source that has one, and the candidate a person picks travels
to the import in the shape the existing linking code already accepts.

The route reached one source. Planning the television namespace is not the same
as asking anybody: a preview runs the identity sources and then TMDB by name,
and a source outside both -- TheTVDB, which is where a great many series are
described at all -- was never consulted however high the user had ranked it. So
`search_series` was planned for TMDB and for nothing else, and a title search
was TMDB's answer wearing the plural. `PreviewAsksEverySearchableSourceTests`
below is that half.
"""

import os
import sys
import unittest
from unittest import mock


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_app
from app.backend.next_metadata import plugin_execution_plan
from app.backend.next_metadata import preview_title_search_plan
from app.backend.next_metadata import query_from_payload
from app.backend.next_metadata import run_metadata_source_pipeline
from app.backend.next_plugins.tmdb import plugin as tmdb
from app.backend.next_plugins.tvdb import plugin as tvdb
from app.backend.next_series import provider_series_payload


def _plan(capabilities):
    return [
        step["entrypoint"]
        for step in plugin_execution_plan(
            {"capabilities": list(capabilities)},
            {"previewMode": True, "title": "Fargo"},
        )
    ]


class TitleSearchReachesTelevisionTests(unittest.TestCase):
    def test_a_source_with_a_television_namespace_is_asked(self):
        self.assertIn("search_series", _plan(["search_title", "search_series", "movie_details"]))

    def test_a_source_without_one_gains_no_empty_step(self):
        """`add` is a no-op for an undeclared capability, and this is what keeps
        it that way: a movie-only plugin must not acquire a step that can only
        fail."""
        self.assertEqual(_plan(["search_title", "movie_details"]), ["search_title", "movie_details"])

    def test_a_barcode_search_is_unchanged(self):
        """Television reaches a barcode lookup only when the source itself says
        so (`workType`). Planning a title search there would make the source
        identify a series by title, which is what §7b forbids."""
        plan = [
            step["entrypoint"]
            for step in plugin_execution_plan(
                {"capabilities": ["search_barcode", "search_series"]},
                {"previewMode": True, "externalBarcode": "5051892123457"},
            )
        ]
        self.assertEqual(plan, ["search_barcode"])


class PreviewAsksEverySearchableSourceTests(unittest.TestCase):
    """Which sources a title search reaches, and with which questions.

    Membership is by declared capability rather than by id, which is the whole
    point: installing a source that says it can search is enough, and no future
    one is a code change here.
    """

    def _search_plan(self, capabilities, **query):
        return [
            step["entrypoint"]
            for step in preview_title_search_plan(
                {"capabilities": list(capabilities)},
                {"previewMode": True, **query},
            )
        ]

    def test_a_television_only_source_is_asked(self):
        """TheTVDB's shape: a series namespace and no film one."""
        self.assertEqual(
            self._search_plan(["series_details", "search_series", "season_episodes"], title="Fargo"),
            ["search_series"],
        )

    def test_enrichment_is_not_part_of_the_question(self):
        """A preview has identified nothing yet, so a source is asked what the
        typed text might mean and nothing else. Details, specs and box-set
        detection belong to the movie that was picked."""
        self.assertEqual(
            self._search_plan(
                ["search_title", "movie_details", "technical_specs", "box_set_candidates"],
                title="Fargo",
                detectBoxSets=True,
            ),
            ["search_title"],
        )

    def test_a_barcode_reaches_no_supporting_source(self):
        """A source that never claimed to read this barcode must not acquire a
        call that can only fail -- and answering a barcode with a title match is
        the source identifying a release on its own initiative, which §7b
        forbids."""
        self.assertEqual(
            self._search_plan(["search_title", "search_barcode"], externalBarcode="5051892123457"),
            [],
        )

    def test_a_source_with_nothing_to_search_is_left_alone(self):
        self.assertEqual(self._search_plan(["movie_details", "images"], title="Fargo"), [])


class ATitleSearchReachesTheTelevisionSourceTests(unittest.TestCase):
    """The pipeline half. The plan above decides what to ask; this decides who
    is asked, and it is the half that was missing."""

    PLUGINS = [
        {
            "id": "tmdb",
            "name": "TMDb",
            "categories": ["metadata_source"],
            "capabilities": ["search_title", "search_series", "movie_details"],
            "manifest": {"capabilities": ["search_title", "search_series", "movie_details"]},
            "order_index": 10,
        },
        {
            "id": "tvdb",
            "name": "TheTVDB",
            "categories": ["metadata_source"],
            "capabilities": ["series_details", "search_series", "season_episodes"],
            "manifest": {"capabilities": ["series_details", "search_series", "season_episodes"]},
            "order_index": 20,
        },
    ]

    def _answer(self, plugin_id, entrypoint, _payload, _context):
        if plugin_id == "tvdb":
            return {
                "status": "ok",
                "state": "available",
                "elapsedMs": 1,
                "result": {
                    "status": "hit",
                    "provider": "tvdb",
                    "items": [
                        {
                            "provider": "tvdb",
                            "providerLabel": "TheTVDB",
                            "identifierType": "tvdb",
                            "identifier": "305288",
                            "title": "Stranger Things",
                            "year": "2016",
                            "mediaType": "SHOW",
                            "series": {
                                "providerId": "tvdb",
                                "identifier": "305288",
                                "identifierType": "tvdb",
                                "title": "Stranger Things",
                                "seasons": [],
                            },
                        }
                    ],
                },
            }
        return {
            "status": "ok",
            "state": "available",
            "elapsedMs": 1,
            "result": {
                "status": "hit",
                "provider": "tmdb",
                "items": [
                    {
                        "provider": "tmdb",
                        "providerLabel": "TMDb",
                        "title": "Stranger Things",
                        "year": "2016",
                        "tmdbId": "66732",
                    }
                ],
            },
        }

    def _run(self, payload):
        with mock.patch("app.backend.next_metadata.metadata_source_plugins", return_value=self.PLUGINS), \
             mock.patch("app.backend.next_metadata.plugin_config_from_db", return_value={}), \
             mock.patch("app.backend.next_metadata.plugin_execution_context", return_value={}), \
             mock.patch("app.backend.next_metadata.plugin_requires_config", return_value=False), \
             mock.patch("app.backend.next_metadata.preferred_provider_overwrite", return_value=False), \
             mock.patch("app.backend.next_metadata.run_plugin_entrypoint", side_effect=self._answer):
            return run_metadata_source_pipeline(
                object(),
                query=query_from_payload(payload),
                current={"metadata": {}},
                technical_current={},
            )

    def _executions(self, result):
        return {(item["pluginId"], item["entrypoint"]) for item in result["executions"]}

    def test_the_television_source_is_consulted(self):
        result = self._run({"title": "Stranger Things", "previewMode": True})
        self.assertIn(("tvdb", "search_series"), self._executions(result))

    def test_its_candidates_reach_the_picker(self):
        """Being called is not the same as being offered. The candidate list is
        what the Add screen draws, so a result the merge quietly dropped would
        look exactly like a source that was never asked."""
        result = self._run({"title": "Stranger Things", "previewMode": True})
        titles = [
            candidate.get("title")
            for item in result["results"]
            if item.get("pluginId") == "tvdb"
            for candidate in item.get("candidates") or []
        ]
        self.assertEqual(titles, ["Stranger Things"])

    def test_tmdb_still_answers_first(self):
        """The pre-selected candidate is whichever comes first, so the new
        source is appended rather than inserted: a film search must not start
        defaulting to a series because a second source was added."""
        result = self._run({"title": "Stranger Things", "previewMode": True})
        plugin_order = [item.get("pluginId") for item in result["results"]]
        self.assertLess(plugin_order.index("tmdb"), plugin_order.index("tvdb"))

    def test_a_barcode_scan_asks_it_nothing(self):
        result = self._run({"barcode": "5051892123457", "previewMode": True})
        self.assertNotIn("tvdb", {plugin_id for plugin_id, _ in self._executions(result)})


class ATvdbCandidateCarriesItsSeriesTests(unittest.TestCase):
    """The same contract `ACandidateCarriesItsSeriesTests` pins for TMDB, on the
    source that has no film namespace at all -- so every result it offers is a
    series, and a candidate that failed to say so would arrive as a film."""

    def _items(self):
        def fake_request(context, path, **params):
            return [
                {
                    "tvdb_id": "305288",
                    "name": "Stranger Things",
                    "year": "2016",
                    "overview": "Examples.",
                    "image_url": "https://artworks.thetvdb.com/p.jpg",
                }
            ]

        original = tvdb._request
        tvdb._request = fake_request
        try:
            return tvdb.search_series({"title": "Stranger Things"}, {})["items"]
        finally:
            tvdb._request = original

    def test_the_candidate_states_it_is_television(self):
        self.assertEqual(self._items()[0]["mediaType"], "SHOW")

    def test_the_series_block_fits_the_existing_linking_contract(self):
        payload = provider_series_payload(self._items()[0]["series"])
        self.assertIsNotNone(payload)
        self.assertEqual(payload["identifier"], "305288")
        self.assertEqual(payload["provider_id"], "tvdb")
        self.assertEqual(payload["title"], "Stranger Things")

    def test_the_identifier_stays_in_its_own_namespace(self):
        """The assertion that matters. Stored as `tmdb_tv`, a TheTVDB id would be
        an id in a namespace that never issued it: the series refresh hands
        `seriesIdentifiers` to each source and every one of them reads the entry
        it recognises, so the number would go to TMDB, name a different show or
        none, and the series would be described as something it is not."""
        self.assertEqual(provider_series_payload(self._items()[0]["series"])["identifier_type"], "tvdb")

    def test_a_source_that_names_no_namespace_still_means_tmdb(self):
        """The MovieVault feed has always sent a bare `tmdbTvId`. Reading that
        silence as anything else would re-file every series it ever placed."""
        payload = provider_series_payload(
            {"tmdbTvId": "1399", "providerId": "tmdb", "title": "Fargo", "seasons": []}
        )
        self.assertEqual(payload["identifier_type"], "tmdb_tv")


class ACandidateCarriesItsSeriesTests(unittest.TestCase):
    def _items(self):
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
            return tmdb.search_series({"title": "Example"}, {})["items"]
        finally:
            tmdb._request = original

    def test_the_candidate_states_it_is_television(self):
        self.assertEqual(self._items()[0]["mediaType"], "SHOW")

    def test_the_series_block_fits_the_existing_linking_contract(self):
        """The assertion that matters. A block that merely *looks* like the one
        the MovieVault feed sends would leave the disc unlinked with nothing
        raised -- so it is checked against the parser that will actually read
        it, not against a shape written down here."""
        payload = provider_series_payload(self._items()[0]["series"])
        self.assertIsNotNone(payload)
        self.assertEqual(payload["identifier"], "1399")
        self.assertEqual(payload["provider_id"], "tmdb")
        self.assertEqual(payload["title"], "Example Show")

    def test_no_seasons_are_claimed(self):
        """TMDB knows every season the show has; none of them is a statement
        about what is in the box. `test_a_season_the_feed_never_recorded_is_not_created`
        is the rule this keeps out of reach."""
        self.assertEqual(self._items()[0]["series"]["seasons"], [])


class TheImportKeepsWhatWasPickedTests(unittest.TestCase):
    """Between the picker and the import the candidate is rebuilt twice -- once
    in the browser, once from the request body. Both used to construct an
    explicit object and neither listed these two fields, so a correct card
    produced a film."""

    def _candidate(self, **overrides):
        candidate = {
            "title": "Example Show",
            "provider": "tmdb",
            "mediaType": "SHOW",
            "series": {"tmdbTvId": "1399", "providerId": "tmdb", "title": "Example Show", "seasons": []},
        }
        candidate.update(overrides)
        return candidate

    def test_the_request_body_keeps_both_fields(self):
        parsed = next_app.selected_import_movie_candidate_from_body(
            {"selectedMovieCandidate": self._candidate()}
        )
        self.assertEqual(parsed["mediaType"], "SHOW")
        self.assertEqual(parsed["series"]["tmdbTvId"], "1399")

    def test_the_disc_is_created_as_a_show(self):
        proposal = next_app.selected_import_movie_candidate_proposal(
            next_app.selected_import_movie_candidate_from_body(
                {"selectedMovieCandidate": self._candidate()}
            )
        )
        self.assertEqual(proposal["movieUpdates"]["media_type"], "SHOW")

    def test_a_film_states_no_type_at_all(self):
        """Not `MOVIE`. The import falls back to `infer_media_type_from_title`
        exactly when nothing was stated, and a written-in MOVIE reads as an
        answer and silences it."""
        proposal = next_app.selected_import_movie_candidate_proposal(
            next_app.selected_import_movie_candidate_from_body(
                {"selectedMovieCandidate": {"title": "Fargo", "provider": "tmdb"}}
            )
        )
        self.assertNotIn("media_type", proposal["movieUpdates"])
        self.assertIsNone(proposal["series"])

    def test_the_pick_outranks_the_sources_on_series_identity(self):
        merged = next_app.merge_selected_import_movie_candidate(
            {"movieUpdates": {}, "series": {"tmdbTvId": "9999"}},
            next_app.selected_import_movie_candidate_from_body(
                {"selectedMovieCandidate": self._candidate()}
            ),
        )
        self.assertEqual(merged["series"]["tmdbTvId"], "1399")

    def test_a_film_never_clears_a_series_somebody_else_established(self):
        """Absent means "nothing was stated". Reading it as "unlink" would let
        picking a film candidate detach a disc the feed had already placed."""
        merged = next_app.merge_selected_import_movie_candidate(
            {"movieUpdates": {}, "series": {"tmdbTvId": "9999"}},
            next_app.selected_import_movie_candidate_from_body(
                {"selectedMovieCandidate": {"title": "Fargo", "provider": "tmdb"}}
            ),
        )
        self.assertEqual(merged["series"]["tmdbTvId"], "9999")


class TheFrontendCarriesItTooTests(unittest.TestCase):
    """The browser half of the same journey. It is a Python string, so nothing
    but a read of the source notices when a field stops being copied."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(__file__), "..", "next_views_ui.py")
        with open(os.path.abspath(path), encoding="utf-8") as handle:
            cls.source = handle.read()

    def _normalizer(self):
        start = self.source.index("function normalizeLookupMovieCandidate(")
        return self.source[start:self.source.index("\n    function ", start + 1)]

    def test_the_normalized_candidate_carries_media_type_and_series(self):
        body = self._normalizer()
        self.assertIn("mediaType,", body)
        self.assertIn("series,", body)

    def test_the_key_separates_a_film_from_a_same_titled_series(self):
        """A film and a series share no `tmdb` id -- the series id lives in the
        television namespace -- so without the television id in the key, "Fargo
        (2014)" the series and "Fargo" the film collapse onto one entry and
        selecting either selects the other."""
        self.assertIn("seriesKey", self._normalizer())

    def test_the_key_carries_the_namespace_the_id_was_issued_in(self):
        """Two sources answer the same search now, and their ids are unrelated
        numbers: TheTVDB's 305288 and TMDB's 305288 are different shows. A key
        built from the number alone would collapse them onto one card, so the
        namespace is part of it -- and an id stated without one still reads as
        `tmdb_tv`, which is what the MovieVault feed has always sent."""
        body = self._normalizer()
        self.assertIn("${seriesRefType || \"tmdb_tv\"}:${seriesRef}", body)
        self.assertIn("series?.identifierType", body)

    def test_the_deduplication_uses_the_same_namespaced_reference(self):
        """`lookupMovieCandidates` drops a candidate whose identity it has
        already seen. Comparing only `tmdbTvId` there made every TheTVDB result
        identity-less, so two different series with one title deduplicated onto
        the first."""
        start = self.source.index("function lookupMovieCandidates(")
        body = self.source[start:self.source.index("\n    function ", start + 1)]
        self.assertIn("normalized.seriesKey", body)
        self.assertNotIn("normalized.series?.tmdbTvId", body)

    def test_the_card_says_which_it_is(self):
        self.assertIn("importCenter.candidateSeries", self.source)
        self.assertIn("importCenter.candidateFilm", self.source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
