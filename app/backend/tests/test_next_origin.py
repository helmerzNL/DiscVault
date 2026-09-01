"""The film's origin: normalizers, the TMDB boundary, and what may not lock it."""

import os
import sys
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import next_metadata  # noqa: E402
import next_origin  # noqa: E402


def _load_tmdb_plugin():
    import importlib.util

    path = os.path.join(BACKEND_DIR, "next_plugins", "tmdb", "plugin.py")
    spec = importlib.util.spec_from_file_location("tmdb_plugin_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RegionCodeTests(unittest.TestCase):
    def test_a_two_letter_code_is_upper_cased(self):
        self.assertEqual(next_origin.normalize_region_code("jp"), "JP")
        self.assertEqual(next_origin.normalize_region_code(" US "), "US")

    def test_anything_that_is_not_alpha_2_is_refused(self):
        for value in ("USA", "U", "", None, "1S", "japan"):
            with self.subTest(value=value):
                self.assertEqual(next_origin.normalize_region_code(value), "")

    def test_the_order_given_is_preserved_and_duplicates_collapse(self):
        # TMDB lists the lead producer of a co-production first, so the order is
        # information and re-sorting it would state something it never said.
        self.assertEqual(
            next_origin.normalize_region_codes(["FR", "it", "FR", "bad"]),
            ["FR", "IT"],
        )

    def test_a_bare_string_is_treated_as_one_code(self):
        self.assertEqual(next_origin.normalize_region_codes("jp"), ["JP"])


class LanguageCodeTests(unittest.TestCase):
    def test_the_primary_subtag_is_lower_cased_and_subtags_keep_their_case(self):
        self.assertEqual(next_origin.normalize_language_code("JA"), "ja")
        self.assertEqual(next_origin.normalize_language_code("cmn-Hans"), "cmn-Hans")

    def test_tmdbs_placeholders_for_no_language_are_not_languages(self):
        # 'xx' is what TMDB uses for a silent film. Stored as a language it would
        # appear in the filter under whatever Intl.DisplayNames makes of it.
        for value in ("xx", "zxx", "und", "mul"):
            with self.subTest(value=value):
                self.assertEqual(next_origin.normalize_language_code(value), "")

    def test_malformed_values_are_refused(self):
        for value in ("", None, "e", "english!", "12"):
            with self.subTest(value=value):
                self.assertEqual(next_origin.normalize_language_code(value), "")


class TmdbExtractionTests(unittest.TestCase):
    def test_origin_country_wins_over_production_countries(self):
        # They answer different questions: origin_country is where the film is
        # from, production_countries is everyone who financed it.
        origin = next_origin.origin_from_tmdb_details(
            {
                "original_language": "ja",
                "origin_country": ["JP"],
                "production_countries": [{"iso_3166_1": "US"}, {"iso_3166_1": "JP"}],
            }
        )
        self.assertEqual(origin, {"originalLanguage": "ja", "originCountries": ["JP"]})

    def test_production_countries_are_the_fallback(self):
        origin = next_origin.origin_from_tmdb_details(
            {"original_language": "fr", "production_countries": [{"iso_3166_1": "FR"}]}
        )
        self.assertEqual(origin["originCountries"], ["FR"])

    def test_an_empty_payload_answers_with_empty_values_not_an_error(self):
        self.assertEqual(
            next_origin.origin_from_tmdb_details({}),
            {"originalLanguage": "", "originCountries": []},
        )


class PluginBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.plugin = _load_tmdb_plugin()

    def test_film_origin_is_emitted_outside_the_movie_sub_dict(self):
        # `movie` keys flow through the generic free-text merge policy; this is a
        # relational always-replace-on-hit association, like genreIds beside it.
        result = self.plugin._normalize_details(
            {"id": 1, "title": "T", "original_language": "ja", "origin_country": ["JP"]}
        )
        self.assertIn("filmOrigin", result)
        self.assertNotIn("filmOrigin", result["movie"])
        self.assertEqual(result["filmOrigin"]["originCountries"], ["JP"])

    def test_the_key_is_always_present_so_an_empty_answer_can_clear(self):
        result = self.plugin._normalize_details({"id": 1, "title": "T"})
        self.assertEqual(
            result["filmOrigin"], {"originalLanguage": "", "originCountries": []}
        )


class ProvidedVersusAbsentTests(unittest.TestCase):
    def test_no_answer_is_none_and_an_empty_answer_is_a_dict(self):
        # The whole distinction: None leaves stored origin alone, {} clears it.
        # Collapsing the two would let any provider that knows nothing about
        # origin wipe it on every refresh.
        self.assertIsNone(next_metadata.normalize_film_origin(None))
        self.assertIsNone(next_metadata.normalize_film_origin("nonsense"))
        self.assertEqual(
            next_metadata.normalize_film_origin({}),
            {"originalLanguage": "", "originCountries": []},
        )

    def test_a_plugin_answer_is_validated_not_trusted(self):
        self.assertEqual(
            next_metadata.normalize_film_origin(
                {"originalLanguage": "JA", "originCountries": ["jp", "nope", "FR"]}
            ),
            {"originalLanguage": "ja", "originCountries": ["JP", "FR"]},
        )


class OriginIsNotLockableTests(unittest.TestCase):
    def test_neither_origin_field_is_lockable(self):
        # Same reason genres are not: a lock only arbitrates a merge, and these
        # are always-replace-on-hit associations with no edit-form input.
        self.assertNotIn("original_language", next_metadata.MOVIE_LOCKABLE_FIELDS)
        self.assertNotIn("origin_country", next_metadata.MOVIE_LOCKABLE_FIELDS)
        self.assertNotIn("origin_countries", next_metadata.MOVIE_LOCKABLE_FIELDS)

    def test_the_disc_fields_beside_them_stay_lockable(self):
        # The contrast is deliberate: `country` and `language` describe the
        # pressing and a collector corrects them by hand.
        self.assertIn("country", next_metadata.MOVIE_LOCKABLE_FIELDS)
        self.assertIn("language", next_metadata.MOVIE_LOCKABLE_FIELDS)

    def test_a_client_sending_an_origin_lock_has_it_dropped(self):
        # 037 had to strip a stale 'genre' lock retroactively with two jsonb_agg
        # UPDATEs. Migration 087 adds no such block, which is only safe if an
        # origin lock can never be stored in the first place.
        stored = next_metadata.normalize_movie_field_locks(
            ["original_language", "origin_country", "country"]
        )
        self.assertEqual(stored, ["country"])


class PublishedNotAcceptedTests(unittest.TestCase):
    """The origin travels to clients; it does not come back from them.

    TMDB owns this fact. A client that could set it would be asserting something
    about the film rather than about its own copy, and the next metadata refresh
    would overwrite the assertion anyway -- an edit that appears to work and then
    silently reverts. The reverse shape, accepted-on-push and never published, is
    what test_next_movie_sync_payload_parity forbids outright.
    """

    def setUp(self):
        import next_app

        self.next_app = next_app

    def test_original_language_is_published_in_every_sync_payload(self):
        self.assertIn("original_language", self.next_app._MOVIE_SYNC_COLUMNS)

    def test_original_language_is_not_accepted_from_a_client(self):
        # movie_payload_fields is the sync/push mapping; movie_update_payload is
        # the REST edit path. Neither may take it.
        payload = self.next_app.movie_payload_fields({"originalLanguage": "en", "title": "T"})
        self.assertNotIn("original_language", payload)
        update = self.next_app.movie_update_payload(
            {"originalLanguage": "en", "title": "T"}, existing={}
        )
        self.assertNotIn("original_language", update)


if __name__ == "__main__":
    unittest.main()
