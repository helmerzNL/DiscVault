"""What DiscVault's own TMDB plugin says about a television series.

These are pure shaping tests: no network, no database. The plugin's job here is
to turn a `/tv/{id}` payload into the little this feature stores, and the things
worth pinning are the deliberate omissions rather than the mapping.
"""

import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_plugins.tmdb import plugin as tmdb
from app.backend.next_series import provider_series_payload


def _payload(**overrides):
    data = {
        "id": 1399,
        "name": "Example Show",
        "original_name": "Example Show",
        "overview": "A show about examples.",
        "first_air_date": "2011-04-17",
        "last_air_date": "2019-05-19",
        "seasons": [
            {
                "season_number": 0,
                "name": "Specials",
                "overview": "Behind the scenes.",
                "air_date": "2010-12-05",
                "episode_count": 4,
            },
            {
                "season_number": 1,
                "name": "Season 1",
                "overview": "The first season.",
                "air_date": "2011-04-17",
                "episode_count": 10,
            },
        ],
    }
    data.update(overrides)
    return data


class NormalizeSeriesTests(unittest.TestCase):
    def test_the_series_and_its_seasons_are_shaped(self):
        result = tmdb._normalize_series(_payload())

        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["tmdbTvId"], 1399)
        self.assertEqual(result["series"]["overview"], "A show about examples.")
        self.assertEqual(result["series"]["startYear"], "2011")
        self.assertEqual(result["series"]["endYear"], "2019")
        self.assertEqual([s["seasonNumber"] for s in result["seasons"]], [0, 1])

    def test_season_zero_travels_like_any_other(self):
        """Specials on TMDB are specials on the disc, and a box set that includes
        them is a real thing to own. Filtering season 0 would silently drop it."""
        result = tmdb._normalize_series(_payload())
        specials = next(s for s in result["seasons"] if s["seasonNumber"] == 0)
        self.assertEqual(specials["overview"], "Behind the scenes.")

    def test_a_season_without_a_usable_number_is_skipped(self):
        result = tmdb._normalize_series(
            _payload(
                seasons=[
                    {"season_number": 1, "overview": "kept"},
                    {"season_number": "two", "overview": "dropped"},
                    {"season_number": True, "overview": "dropped"},
                    {"overview": "dropped"},
                    "not-an-object",
                ]
            )
        )
        self.assertEqual([s["seasonNumber"] for s in result["seasons"]], [1])

    def test_artwork_uses_the_same_key_names_as_a_movie(self):
        """Matching `movie`\'s names is what keeps the merge layer free of
        per-source vocabulary, so a second series source can answer in a shape
        that already works."""
        result = tmdb._normalize_series(
            _payload(poster_path="/poster.jpg", backdrop_path="/backdrop.jpg")
        )
        series = result["series"]
        self.assertEqual(series["posterUrl"], tmdb._image("/poster.jpg"))
        self.assertEqual(series["posters"], [tmdb._image("/poster.jpg")])
        self.assertEqual(series["backdropUrl"], tmdb._image("/backdrop.jpg"))
        self.assertEqual(series["backdropUrls"], [tmdb._image("/backdrop.jpg")])

    def test_the_images_list_outranks_the_default_path(self):
        """`poster_path` is what TMDB shows by default; the `images` list is
        ordered by what people actually voted for. Taking the default when a
        ranked list exists would quietly ignore the ranking."""
        result = tmdb._normalize_series(
            _payload(
                poster_path="/default.jpg",
                images={
                    "posters": [
                        {"file_path": "/meh.jpg", "vote_average": 1.0},
                        {"file_path": "/best.jpg", "vote_average": 9.0},
                    ]
                },
            )
        )
        self.assertEqual(result["series"]["posterUrl"], tmdb._image("/best.jpg"))
        # The runner-up survives as an option rather than being discarded -- it is
        # what fills the Posters tab with a choice.
        self.assertIn(tmdb._image("/meh.jpg"), result["series"]["posters"])

    def test_a_series_with_no_artwork_at_all_says_so_with_empty_values(self):
        """A miss must not read as `None` downstream: the caller skips empties."""
        result = tmdb._normalize_series(_payload())
        self.assertEqual(result["series"]["posterUrl"], "")
        self.assertEqual(result["series"]["posters"], [])
        self.assertEqual(result["series"]["backdropUrls"], [])

    def test_a_season_poster_rides_along_on_the_payload_already_fetched(self):
        """`/tv/{id}` carries `seasons[].poster_path`, so season artwork costs no
        request. `/tv/{id}/season/{n}` is richer and costs one call per season --
        a ten-season show turns one request into eleven."""
        result = tmdb._normalize_series(
            _payload(
                seasons=[
                    {"season_number": 1, "overview": "x", "poster_path": "/s1.jpg"},
                    {"season_number": 2, "overview": "y"},
                ]
            )
        )
        posters = {s["seasonNumber"]: s["posterUrl"] for s in result["seasons"]}
        self.assertEqual(posters[1], tmdb._image("/s1.jpg"))
        self.assertEqual(posters[2], "")

    def test_artwork_is_asked_for_on_the_request_that_was_happening_anyway(self):
        """The argument in this plugin is against extra *requests*, not extra
        fields. `append_to_response` costs neither a round trip nor a rate-limit
        slot -- so this must stay one call."""
        calls = []

        def fake_request(context, path, **params):
            calls.append((path, params))
            return {"id": 1399}

        original = tmdb._request
        tmdb._request = fake_request
        try:
            tmdb._series_details_request({}, "1399")
        finally:
            tmdb._request = original

        self.assertEqual(len(calls), 1)
        path, params = calls[0]
        self.assertEqual(path, "/tv/1399")
        self.assertIn("images", params["append_to_response"])

    def test_an_empty_payload_yields_empty_strings_not_none(self):
        """The caller writes these into text columns and skips empties, so `""`
        and `None` must not both have to be handled downstream."""
        result = tmdb._normalize_series({"id": 1})
        self.assertEqual(result["series"]["overview"], "")
        self.assertEqual(result["series"]["title"], "")
        self.assertEqual(result["seasons"], [])


class SeriesDetailsEntrypointTests(unittest.TestCase):
    """`series_details` never searches, unlike `movie_details`.

    The caller holds a TMDB television id that arrived on the distribution feed
    and was stored in `series_identifiers`, so an exact answer exists. Falling
    back to a title search would trade it for a guess — and §7b of the media-type
    document is explicit that only a source querying a series namespace may speak
    to series identity.
    """

    def test_a_missing_id_is_a_miss_rather_than_a_search(self):
        for payload in ({}, {"tmdbTvId": ""}, {"tmdbTvId": "   "}):
            with self.subTest(payload=payload):
                self.assertEqual(
                    tmdb.series_details(payload, {}),
                    {"status": "miss", "provider": "tmdb"},
                )

    def test_the_identifier_map_is_read_and_other_namespaces_ignored(self):
        """Every source is offered every identifier and takes the one it speaks.

        A source is the only thing that knows which namespaces it can use, so
        being handed a TVDB id alongside a TMDB one must be unremarkable rather
        than confusing.
        """
        self.assertEqual(
            tmdb.series_details(
                {"seriesIdentifiers": {"tvdb": "121361", "fanart": "abc"}}, {}
            ),
            {"status": "miss", "provider": "tmdb"},
        )

    def test_the_older_top_level_id_still_answers(self):
        """A DiscVault that has not been updated yet sends only `tmdbTvId`, and a
        newer plugin dropping it would break exactly the installations that were
        slowest to update."""
        captured = {}

        def fake_request(context, tmdb_tv_id):
            captured["id"] = tmdb_tv_id
            return {"id": int(tmdb_tv_id), "name": "Example"}

        original = tmdb._series_details_request
        tmdb._series_details_request = fake_request
        try:
            tmdb.series_details({"tmdbTvId": "1399"}, {})
            self.assertEqual(captured["id"], "1399")
            captured.clear()
            # The map wins when both are present -- it is the newer, richer form.
            tmdb.series_details(
                {"tmdbTvId": "1", "seriesIdentifiers": {"tmdb_tv": "1399"}}, {}
            )
            self.assertEqual(captured["id"], "1399")
        finally:
            tmdb._series_details_request = original

    def test_a_non_numeric_id_is_a_miss(self):
        """A slug or a title arriving where an id belongs must not be sent to
        TMDB as if it were one."""
        for value in ("game-of-thrones", "tv/1399", "1399a", "-1"):
            with self.subTest(value=value):
                self.assertEqual(
                    tmdb.series_details({"tmdbTvId": value}, {}),
                    {"status": "miss", "provider": "tmdb"},
                )


class LookupExternalSeriesIdTests(unittest.TestCase):
    """A number a person typed, asked of the television namespace.

    The film namespace has always been asked -- `lookup_external_id` is
    `/movie/{id}` -- and a bare number does not say which namespace issued it.
    TMDB's 1516 is a film and a television series with nothing to do with each
    other, so answering from the film namespace alone tells somebody searching
    for the show that no such thing exists.
    """

    def _lookup(self, payload, response=None, raises=None):
        captured = {}

        def fake_request(context, path, **params):
            captured["path"] = path
            if raises is not None:
                raise raises
            return response

        original = tmdb._request
        tmdb._request = fake_request
        try:
            return tmdb.lookup_external_series_id(payload, {}), captured
        finally:
            tmdb._request = original

    def _show(self):
        return {
            "id": 1516,
            "name": "The A-Team",
            "original_name": "The A-Team",
            "first_air_date": "1983-01-23",
            "overview": "Soldiers of fortune.",
            "poster_path": "/p.jpg",
        }

    def test_the_television_namespace_is_the_one_asked(self):
        _result, captured = self._lookup({"tmdbId": "1516"}, response=self._show())
        self.assertEqual(captured["path"], "/tv/1516")

    def test_the_answer_is_a_candidate_to_choose_from(self):
        """`items`, not a detail block: this runs beside `lookup_external_id`
        and both offers land in the one list the person picks from."""
        result, _ = self._lookup({"tmdbId": "1516"}, response=self._show())
        self.assertEqual(result["status"], "hit")
        item = result["items"][0]
        self.assertEqual(item["identifierType"], "tmdb_tv")
        self.assertEqual(item["identifier"], "1516")
        self.assertEqual(item["title"], "The A-Team")
        self.assertEqual(item["year"], "1983")

    def test_the_candidate_is_the_one_the_add_screen_already_understands(self):
        """Checked against the parser that will actually read it rather than
        against a shape written down twice -- the same assertion the title
        search carries, because both answers travel the same route."""
        result, _ = self._lookup({"tmdbId": "1516"}, response=self._show())
        item = result["items"][0]
        self.assertEqual(item["mediaType"], "SHOW")
        payload = provider_series_payload(item["series"])
        self.assertIsNotNone(payload)
        self.assertEqual(payload["identifier"], "1516")
        self.assertEqual(payload["provider_id"], "tmdb")

    def test_a_number_that_names_no_series_is_a_miss_not_an_error(self):
        """The ordinary case, not a fault: every id typed for a film reaches
        this too, and reporting each one as a failure would put a red message
        on a search that worked."""
        import requests

        response = requests.Response()
        response.status_code = 404
        result, _ = self._lookup(
            {"tmdbId": "10195"}, raises=requests.HTTPError(response=response)
        )
        self.assertEqual(result, {"status": "miss", "provider": "tmdb", "items": []})

    def test_a_real_failure_is_still_a_failure(self):
        """A 500 or a rejected key must not be reported as "no such series" --
        that is the distinction `sourceErrorText` exists to draw."""
        import requests

        response = requests.Response()
        response.status_code = 500
        with self.assertRaises(requests.HTTPError):
            self._lookup({"tmdbId": "1516"}, raises=requests.HTTPError(response=response))

    def test_an_id_from_another_namespace_is_never_sent(self):
        """An IMDb id reaches this whenever somebody searches by one. Pasting
        it into a TMDB path would be a request that can only fail, and a slug
        would be worse: it can succeed and name a different show."""
        for value in ("tt0084967", "the-a-team", "1516a", ""):
            with self.subTest(value=value):
                result, captured = self._lookup({"tmdbId": value}, response=self._show())
                self.assertEqual(result["items"], [])
                self.assertNotIn("path", captured)

    def test_the_series_identifier_map_wins_when_it_is_offered(self):
        """The richer form, same precedence `series_details` gives it."""
        _result, captured = self._lookup(
            {"tmdbId": "1", "seriesIdentifiers": {"tmdb_tv": "1516"}}, response=self._show()
        )
        self.assertEqual(captured["path"], "/tv/1516")

    def test_the_film_namespace_answers_a_miss_on_the_same_terms(self):
        """The other half of asking both. A television id reaching the film
        lookup used to raise, so a search that found the series still reported
        a failed request beside it -- and `movie_details`, which calls that
        first and falls back to a title search, raised instead of falling
        back."""
        import requests

        response = requests.Response()
        response.status_code = 404

        def fake_request(context, path, **params):
            raise requests.HTTPError(response=response)

        original = tmdb._request
        tmdb._request = fake_request
        try:
            self.assertEqual(
                tmdb.lookup_external_id({"tmdbId": "1516"}, {}),
                {"status": "miss", "provider": "tmdb"},
            )
        finally:
            tmdb._request = original


if __name__ == "__main__":
    unittest.main()
