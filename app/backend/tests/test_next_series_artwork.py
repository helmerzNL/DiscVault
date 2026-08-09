"""How several sources' artwork becomes one answer.

`merge_series_details` used to combine text only, and text is forgiving: a
missing overview leaves a blank field somebody notices. Artwork is not — a
poster that quietly did not arrive looks exactly like a series whose source has
no poster, and the borrowed disc artwork underneath it hides the difference.

These are pure merge tests: no network, no database.
"""

import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_metadata import ENTITY_ARTWORK_TABLES, merge_series_details


def _result(**overrides):
    data = {"series": {}, "seasons": []}
    data.update(overrides)
    return data


class MergeSeriesArtworkTests(unittest.TestCase):
    def test_the_first_source_in_the_users_order_supplies_the_poster(self):
        """Precedence is the plugin order the user themselves ranked, not a rule
        buried in the merge."""
        merged = merge_series_details(
            [
                ("tmdb", _result(series={"posterUrl": "https://a.test/p.jpg"})),
                ("tvdb", _result(series={"posterUrl": "https://b.test/p.jpg"})),
            ]
        )
        self.assertEqual(merged["artwork"]["poster"]["sourceUrl"], "https://a.test/p.jpg")
        self.assertEqual(merged["artwork"]["poster"]["source"], "tmdb")

    def test_each_field_competes_on_its_own(self):
        """A source with a poster but no synopsis still supplies the poster.
        Letting the first source that answers anything win everything would make
        a second source pointless the moment the first replied at all."""
        merged = merge_series_details(
            [
                ("tmdb", _result(series={"overview": "Text."})),
                ("fanart", _result(series={"posterUrl": "https://b.test/p.jpg"})),
            ]
        )
        self.assertEqual(merged["overviewSource"], "tmdb")
        self.assertEqual(merged["artwork"]["poster"]["source"], "fanart")

    def test_runner_up_urls_survive_as_options(self):
        """First-source-wins picks the default, not the only choice: the Posters
        tab shows the rest so a person can disagree."""
        merged = merge_series_details(
            [
                (
                    "tmdb",
                    _result(series={"posterUrl": "https://a.test/1.jpg", "posters": [
                        "https://a.test/1.jpg",
                        "https://a.test/2.jpg",
                    ]}),
                )
            ]
        )
        self.assertIn("https://a.test/2.jpg", merged["artwork"]["poster"]["options"])

    def test_a_season_with_only_a_poster_is_still_recorded(self):
        """Seasons used to be collected only when they carried an overview,
        because text was all there was. Keeping that would have silently dropped
        every poster for a season nobody wrote a synopsis for."""
        merged = merge_series_details(
            [("tmdb", _result(seasons=[{"seasonNumber": 2, "posterUrl": "https://a.test/s2.jpg"}]))]
        )
        self.assertEqual(merged["seasons"][2]["posterUrl"], "https://a.test/s2.jpg")
        self.assertNotIn("overview", merged["seasons"][2])

    def test_one_source_may_complete_what_another_left_empty_on_the_same_season(self):
        merged = merge_series_details(
            [
                ("tmdb", _result(seasons=[{"seasonNumber": 1, "overview": "Text."}])),
                ("fanart", _result(seasons=[{"seasonNumber": 1, "posterUrl": "https://b.test/s1.jpg"}])),
            ]
        )
        season = merged["seasons"][1]
        self.assertEqual(season["source"], "tmdb")
        self.assertEqual(season["posterSource"], "fanart")

    def test_a_season_the_source_mentions_but_says_nothing_about_is_reported(self):
        """Carried, but deliberately not acted on by the writer.

        `refresh_series_metadata` writes an overview only where there is one and
        creates no season at all -- `test_a_season_the_feed_never_recorded_is_not_created`
        pins that rule: the plugin knows every season TMDB has, the collection
        knows the ones a disc covers, and this job describes what is there.

        Reporting the season here anyway keeps the merge honest about what the
        source said, and leaves what to do with it to the caller. That separation
        is what let the rule above be re-read rather than re-derived when it was
        questioned.
        """
        merged = merge_series_details([("tmdb", _result(seasons=[{"seasonNumber": 3}]))])
        self.assertIn(3, merged["seasons"])
        self.assertEqual(merged["seasons"][3], {})

    def test_a_source_that_answers_nothing_changes_nothing(self):
        merged = merge_series_details([("tmdb", _result())])
        self.assertEqual(merged["artwork"], {})
        self.assertEqual(merged["overview"], "")


class PluginContextTests(unittest.TestCase):
    """A series-side plugin context is built in exactly one place.

    `plugin_config_from_db` returns `settings` and `secretsRef` -- the *names* of
    the secrets. `plugin_execution_context` is what turns those names into
    `secrets`, which is where a plugin reads its API key.

    Passing the config straight through as the context therefore handed TMDB an
    empty key on an installation whose key was correctly configured: every series
    lookup raised "TMDb API key is not configured", the error was collected into a
    list nothing displayed, and the page reported that no source had anything to
    add. Movies enriched perfectly throughout, because that path used the context
    builder from the start -- which is exactly what made it look like a series
    problem rather than a wiring one.

    This test used to assert the builder appeared in each of three function
    bodies, which is a weaker thing to ask: it holds three copies to the same
    rule instead of removing the second and third. A comment repeated above four
    hand-written loops did not stop one of them being wrong, and would not have
    stopped a fifth. Counting is the assertion that does.
    """

    def _source(self):
        import inspect

        from app.backend import next_metadata

        return inspect.getsource(next_metadata)

    def _body(self, name):
        import inspect

        from app.backend import next_metadata

        return inspect.getsource(getattr(next_metadata, name))

    def test_the_context_is_built_in_exactly_one_place(self):
        self.assertEqual(
            self._source().count("plugin_execution_context(conn, plugin, plugin_config_from_db"),
            1,
        )
        self.assertIn("plugin_execution_context(", self._body("consult_plugins"))

    def test_every_series_source_call_goes_through_it(self):
        for name in (
            "consult_series_sources",
            "search_series_candidates",
            "refresh_season_episodes",
        ):
            with self.subTest(function=name):
                body = self._body(name)
                self.assertIn("consult_plugins(", body)
                # The raw config must never be the context itself.
                self.assertNotIn("context = plugin_config_from_db(conn, plugin_id)", body)

    def test_the_refresh_and_the_offer_ask_the_same_question(self):
        """`available_series_seasons` shows what `refresh_series_metadata` would
        write. If they built their own payloads the picker could offer a season
        the refresh then never describes."""
        for name in ("refresh_series_metadata", "available_series_seasons"):
            with self.subTest(function=name):
                self.assertIn("consult_series_sources(", self._body(name))


class ArtworkEntityMapTests(unittest.TestCase):
    def test_a_season_is_storable_and_spelled_one_way(self):
        """`entity_media.entity_type` is unconstrained text (migration 003), which
        is why a season needed no migration to own a poster — and why nothing but
        this assertion notices if the value is ever spelled differently in one of
        the two places that write it."""
        self.assertEqual(ENTITY_ARTWORK_TABLES["series_season"], "series_seasons")
        self.assertEqual(ENTITY_ARTWORK_TABLES["series"], "series")

    def test_the_map_is_the_only_source_of_table_names(self):
        """The owner table reaches the SQL as text. A map with known keys is what
        keeps that from ever being caller-controlled."""
        import inspect

        from app.backend import next_metadata

        body = inspect.getsource(next_metadata.apply_primary_media_update)
        self.assertIn("ENTITY_ARTWORK_TABLES.get(entity_type)", body)
        self.assertIn("UPDATE {owner_table} SET updated_at", body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
