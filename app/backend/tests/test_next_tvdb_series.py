"""TheTVDB as a series source.

No network: `_request` is replaced, so what is pinned here is the shaping and the
rules, not TheTVDB's uptime. The URLs and response field names were taken from
TheTVDB's own OpenAPI document and official Python client rather than from
memory, because the API is unreachable from the environment this was written in
-- these tests are what keeps the reading and the code from drifting apart.
"""

import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_plugins.tvdb import plugin as tvdb
from app.backend.next_series import provider_series_payload  # noqa: F401  (import guard)


class _Recorder:
    """Replaces `_request` and remembers what would have been asked."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, context, path, **params):
        self.calls.append((path, params))
        return self.response


class SearchIsAPersonsActTests(unittest.TestCase):
    def test_details_refuses_to_search(self):
        """The same rule the TMDB plugin keeps: §7b allows only a source querying
        a series namespace to speak to series identity, so a title with no
        identifier is a miss rather than a guess."""
        self.assertEqual(
            tvdb.series_details({"title": "Fargo"}, {}),
            {"status": "miss", "provider": "tvdb"},
        )

    def test_search_asks_the_series_type(self):
        recorder = _Recorder([])
        original, tvdb._request = tvdb._request, recorder
        try:
            tvdb.search_series({"title": "Fargo"}, {})
        finally:
            tvdb._request = original
        path, params = recorder.calls[0]
        self.assertEqual(path, "/search")
        self.assertEqual(params["query"], "Fargo")
        # Without this the same query returns films, people and companies.
        self.assertEqual(params["type"], "series")

    def test_a_candidate_carries_the_id_other_endpoints_accept(self):
        """`id` on a search result is a prefixed string ("series-1234") that no
        other endpoint takes. Storing it would produce a series identifier that
        looks right and resolves to nothing."""
        recorder = _Recorder([
            {"id": "series-1234", "tvdb_id": "1234", "name": "Example", "year": "2011", "image_url": "https://a.test/p.jpg"}
        ])
        original, tvdb._request = tvdb._request, recorder
        try:
            item = tvdb.search_series({"title": "Example"}, {})["items"][0]
        finally:
            tvdb._request = original
        self.assertEqual(item["identifier"], "1234")
        self.assertEqual(item["identifierType"], "tvdb")

    def test_an_empty_query_is_skipped_rather_than_sent(self):
        for payload in ({}, {"title": "   "}):
            with self.subTest(payload=payload):
                self.assertEqual(tvdb.search_series(payload, {})["status"], "skipped")


class SeasonOrderingTests(unittest.TestCase):
    """TheTVDB holds several orderings of the same show in one list.

    Their own README: "All series can have multiple seasons associated with
    them... The series base record includes the id of the default season order."
    So a show that also has a DVD or Netflix order returns season 1 more than
    once, and taking the list as-is makes the season list disagree with itself --
    with which duplicate won decided by list order, which is not a decision.
    """

    def _record(self):
        return {
            "name": "Example Show",
            "overview": "Examples.",
            "firstAired": "2011-04-17",
            "lastAired": "2019-05-19",
            "defaultSeasonType": "1",
            "seasons": [
                {"number": 1, "name": "Season 1", "year": "2011", "image": "https://a.test/s1.jpg", "type": {"id": "1", "type": "official"}},
                {"number": 2, "name": "Season 2", "year": "2012", "image": "", "type": {"id": "1", "type": "official"}},
                {"number": 1, "name": "DVD 1", "year": "2011", "image": "", "type": {"id": "2", "type": "dvd"}},
            ],
            "artworks": [
                {"type": 2, "image": "https://a.test/poster.jpg"},
                {"type": 3, "image": "https://a.test/back.jpg"},
                {"type": 1, "image": "https://a.test/banner.jpg"},
            ],
        }

    def _details(self):
        recorder = _Recorder(self._record())
        original, tvdb._request = tvdb._request, recorder
        try:
            return tvdb.series_details({"seriesIdentifiers": {"tvdb": "1234"}}, {}), recorder
        finally:
            tvdb._request = original

    def test_only_the_default_ordering_is_returned(self):
        result, _ = self._details()
        numbers = [season["seasonNumber"] for season in result["seasons"]]
        self.assertEqual(numbers, [1, 2])

    def test_the_default_ordering_is_the_one_the_series_names(self):
        result, _ = self._details()
        by_number = {season["seasonNumber"]: season for season in result["seasons"]}
        self.assertEqual(by_number[1]["title"], "Season 1")

    def test_artwork_is_filtered_by_kind(self):
        """`artworks` is flat across posters, backgrounds and banners. Without the
        filter a banner lands in the poster slot, which looks like a broken image
        rather than a wrong one."""
        result, _ = self._details()
        series = result["series"]
        self.assertEqual(series["posters"], ["https://a.test/poster.jpg"])
        self.assertEqual(series["backdrops"], ["https://a.test/back.jpg"])
        self.assertNotIn("https://a.test/banner.jpg", series["posters"] + series["backdrops"])

    def test_the_identifier_comes_from_the_namespace_map(self):
        _, recorder = self._details()
        self.assertEqual(recorder.calls[0][0], "/series/1234/extended")


class SeasonEpisodesTests(unittest.TestCase):
    def _episodes(self, data, payload=None):
        recorder = _Recorder(data)
        original, tvdb._request = tvdb._request, recorder
        try:
            return tvdb.season_episodes(
                payload or {"seriesIdentifiers": {"tvdb": "1234"}, "seasonNumber": 2}, {}
            ), recorder
        finally:
            tvdb._request = original

    def test_the_source_filters_the_season_rather_than_this_code(self):
        """One call per season is what the Collectors switch pays for. Fetching
        every season and discarding all but one would spend the whole series'
        cost for one season's answer."""
        _, recorder = self._episodes({"episodes": []})
        path, params = recorder.calls[0]
        self.assertEqual(path, "/series/1234/episodes/default")
        self.assertEqual(params["season"], 2)

    def test_an_episode_from_another_season_is_dropped(self):
        """Trust the filter, verify the answer. An episode filed under the wrong
        season is something the schema forbids -- but only after the write, and
        the composite foreign keys would then reject the whole refresh."""
        result, _ = self._episodes(
            {
                "episodes": [
                    {"number": 1, "seasonNumber": 2, "name": "Right"},
                    {"number": 2, "seasonNumber": 3, "name": "Wrong"},
                ]
            }
        )
        self.assertEqual([episode["title"] for episode in result["episodes"]], ["Right"])

    def test_a_season_nobody_named_is_skipped_rather_than_guessed(self):
        for payload in (
            {"seriesIdentifiers": {"tvdb": "1234"}},
            {"seasonNumber": 2},
            {"seriesIdentifiers": {"tvdb": "1234"}, "seasonNumber": "2"},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(tvdb.season_episodes(payload, {})["status"], "skipped")


class TokenHandlingTests(unittest.TestCase):
    """A login per call would double every lookup against a source whose own
    guidance asks for restraint."""

    def setUp(self):
        tvdb._TOKENS.clear()

    def tearDown(self):
        tvdb._TOKENS.clear()

    def test_a_missing_key_is_named_rather_than_sent(self):
        with self.assertRaises(RuntimeError) as caught:
            tvdb._token({})
        self.assertIn("API key", str(caught.exception))

    def test_a_token_is_cached_per_key(self):
        """Per key rather than globally: a corrected key, or a second
        installation, must not be handed the previous one's token."""
        tvdb._TOKENS["one"] = "token-one"
        self.assertEqual(tvdb._token({"secrets": {"apiKey": "one"}}), "token-one")
        self.assertNotIn("two", tvdb._TOKENS)


class ManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import json

        path = os.path.join(os.path.dirname(__file__), "..", "next_plugins", "tvdb", "manifest.json")
        with open(os.path.abspath(path), encoding="utf-8") as handle:
            cls.manifest = json.load(handle)

    def test_it_declares_only_what_it_answers(self):
        """No film capabilities. A source with an opinion on everything wins
        fields nobody meant it to, and makes the plugin order unreadable."""
        self.assertEqual(
            sorted(self.manifest["capabilities"]),
            ["search_series", "season_episodes", "series_details"],
        )

    def test_the_credit_carries_a_direct_link(self):
        """The requirement that could be read is attribution *with a direct link
        to TheTVDB.com*. The statement is literal rather than a translation key,
        which is the documented safe default for a new source -- a machine
        translation of a required sentence is a different sentence."""
        attribution = self.manifest["attribution"]
        self.assertEqual(attribution["url"], "https://www.thetvdb.com/")
        self.assertTrue(attribution["statement"])
        self.assertNotIn("statementKey", attribution)

    def test_tmdb_still_ranks_first(self):
        self.assertGreater(self.manifest["orderIndex"], 10)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
