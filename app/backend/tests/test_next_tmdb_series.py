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

    def test_no_artwork_is_mapped(self):
        """Artwork is deliberately absent rather than half-done: a series is a
        third entity type in the media-asset path, which is its own piece of
        work. A partial mapping here would look like support and store nothing."""
        result = tmdb._normalize_series(
            _payload(poster_path="/poster.jpg", backdrop_path="/backdrop.jpg")
        )
        flattened = repr(result)
        self.assertNotIn("poster", flattened)
        self.assertNotIn("backdrop", flattened)

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

    def test_a_non_numeric_id_is_a_miss(self):
        """A slug or a title arriving where an id belongs must not be sent to
        TMDB as if it were one."""
        for value in ("game-of-thrones", "tv/1399", "1399a", "-1"):
            with self.subTest(value=value):
                self.assertEqual(
                    tmdb.series_details({"tmdbTvId": value}, {}),
                    {"status": "miss", "provider": "tmdb"},
                )


if __name__ == "__main__":
    unittest.main()
