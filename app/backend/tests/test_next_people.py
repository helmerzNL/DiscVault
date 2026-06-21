import os
import sys
import types
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_app import group_person_credits_by_job
    from app.backend.next_app import merge_person_filmography_entries
    from app.backend.next_app import native_person_detail_payload
    from app.backend.next_app import native_person_filmography_payload
    from app.backend.next_app import person_biography_value
    from app.backend.next_app import person_filmography_entries_from_metadata
    from app.backend.next_app import person_local_filmography_entries
    from app.backend.next_app import select_movie_metadata_person_refresh_credits
    from app.backend.next_plugins.tmdb import plugin as tmdb_plugin
except ModuleNotFoundError as exc:  # Local minimal test environments may omit web/database deps.
    if exc.name not in {"flask", "requests", "psycopg"}:
        raise
    group_person_credits_by_job = None
    merge_person_filmography_entries = None
    native_person_detail_payload = None
    native_person_filmography_payload = None
    person_biography_value = None
    person_filmography_entries_from_metadata = None
    person_local_filmography_entries = None
    select_movie_metadata_person_refresh_credits = None
    tmdb_plugin = None


@unittest.skipIf(person_biography_value is None, "Flask is not installed in this test environment")
class NextPeoplePolicyTests(unittest.TestCase):
    def test_person_biography_prefers_dutch_then_english(self):
        biography = person_biography_value(
            [
                {"lang": "en", "biography": "English biography"},
                {"lang": "nl", "biography": "Nederlandse biografie"},
            ],
            {},
        )

        self.assertEqual(biography, "Nederlandse biografie")

    def test_person_biography_uses_metadata_fallback(self):
        biography = person_biography_value([], {"biography": "Metadata biography"})

        self.assertEqual(biography, "Metadata biography")

    def test_person_crew_credits_group_by_job(self):
        groups = group_person_credits_by_job(
            [
                {"job": "Director", "title": "Movie A"},
                {"job": "Producer", "title": "Movie B"},
                {"job": "Director", "title": "Movie C"},
            ]
        )

        by_job = {group["job"]: group for group in groups}
        self.assertEqual(by_job["Director"]["count"], 2)
        self.assertEqual(by_job["Producer"]["count"], 1)

    def test_movie_person_refresh_selection_prioritizes_key_crew_then_cast(self):
        credits = [
            {"person_id": "actor-1", "credit_type": "actor", "name": "Actor One"},
            {"person_id": "crew-1", "credit_type": "crew", "job": "Director", "name": "Director One"},
            {"person_id": "crew-2", "credit_type": "crew", "job": "Stunts", "name": "Stunt One"},
        ]

        selected = select_movie_metadata_person_refresh_credits(credits, limit=3)

        self.assertEqual([credit["person_id"] for credit in selected], ["crew-1", "actor-1", "crew-2"])

    def test_movie_person_refresh_selection_can_target_crew_only(self):
        credits = [
            {"person_id": "actor-1", "credit_type": "actor", "name": "Actor One"},
            {"person_id": "crew-1", "credit_type": "crew", "job": "Director", "name": "Director One"},
            {"person_id": "crew-2", "credit_type": "crew", "job": "Stunts", "name": "Stunt One"},
        ]

        selected = select_movie_metadata_person_refresh_credits(credits, limit=3, scope="crew")

        self.assertEqual([credit["person_id"] for credit in selected], ["crew-1", "crew-2"])

    def test_person_filmography_metadata_normalizes_tmdb_entries(self):
        entries = person_filmography_entries_from_metadata(
            {
                "combined_credits": {
                    "cast": [
                        {
                            "id": 11,
                            "media_type": "movie",
                            "title": "Example",
                            "release_date": "2020-02-03",
                            "character": "Lead",
                            "poster_path": "/poster.jpg",
                        }
                    ],
                    "crew": [
                        {
                            "id": 12,
                            "media_type": "movie",
                            "title": "Directed",
                            "job": "Director",
                        }
                    ],
                }
            }
        )

        self.assertEqual(entries[0]["tmdb_id"], "11")
        self.assertEqual(entries[0]["year"], "2020")
        self.assertEqual(entries[0]["credit_type"], "actor")
        self.assertEqual(entries[0]["poster_url"], "https://image.tmdb.org/t/p/w342/poster.jpg")
        self.assertEqual(entries[1]["job"], "Director")

    def test_tmdb_person_filmography_entrypoint_normalizes_combined_credits(self):
        original_request = tmdb_plugin._request

        def fake_request(context, path, **params):
            self.assertEqual(path, "/person/123/combined_credits")
            self.assertEqual(params["language"], "nl-NL")
            return {
                "cast": [
                    {
                        "id": 42,
                        "media_type": "movie",
                        "title": "Actor Movie",
                        "release_date": "2021-01-02",
                        "character": "Lead",
                        "poster_path": "/actor.jpg",
                    }
                ],
                "crew": [
                    {
                        "id": 43,
                        "media_type": "movie",
                        "title": "Crew Movie",
                        "job": "Director",
                    }
                ],
            }

        try:
            tmdb_plugin._request = fake_request
            result = tmdb_plugin.person_filmography({"tmdbId": "123"}, {"settings": {"language": "nl-NL"}})
        finally:
            tmdb_plugin._request = original_request

        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["counts"]["total"], 2)
        self.assertEqual(result["combinedCredits"]["cast"][0]["tmdbId"], 42)
        self.assertEqual(result["combinedCredits"]["cast"][0]["posterUrl"], "https://image.tmdb.org/t/p/original/actor.jpg")
        self.assertEqual(result["combinedCredits"]["crew"][0]["job"], "Director")

    def test_tmdb_person_details_entrypoint_enriches_aliases_imdb_and_profiles(self):
        original_request = tmdb_plugin._request

        def fake_request(context, path, **params):
            self.assertEqual(path, "/person/585")
            self.assertEqual(params["append_to_response"], "images,external_ids")
            return {
                "name": "Rutger Hauer",
                "biography": "Dutch actor",
                "birthday": "1944-01-23",
                "deathday": "2019-07-19",
                "place_of_birth": "Breukelen, Netherlands",
                "known_for_department": "Acting",
                "profile_path": "/primary.jpg",
                "also_known_as": ["Rutger Oelsen Hauer", ""],
                "external_ids": {"imdb_id": "nm0000442"},
                "images": {
                    "profiles": [
                        {"file_path": "/secondary.jpg", "vote_average": 5.1},
                        {"file_path": "/tertiary.jpg", "vote_average": 9.4},
                    ]
                },
            }

        try:
            tmdb_plugin._request = fake_request
            result = tmdb_plugin.person_details({"tmdbId": "585"}, {"settings": {"language": "nl-NL"}})
        finally:
            tmdb_plugin._request = original_request

        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["imdbId"], "nm0000442")
        self.assertEqual(result["alsoKnownAs"], ["Rutger Oelsen Hauer"])
        self.assertEqual(result["profileUrl"], "https://image.tmdb.org/t/p/original/primary.jpg")
        self.assertEqual(result["profiles"][0], "https://image.tmdb.org/t/p/original/primary.jpg")
        self.assertIn("https://image.tmdb.org/t/p/original/tertiary.jpg", result["profiles"])
        self.assertLess(
            result["profiles"].index("https://image.tmdb.org/t/p/original/tertiary.jpg"),
            result["profiles"].index("https://image.tmdb.org/t/p/original/secondary.jpg"),
        )

    def test_tmdb_person_awards_entrypoint_groups_wikidata_results(self):
        original_import = tmdb_plugin._import_wikidata_awards
        awards = [
            {"award": "Saturn Award", "category": "Best Actor", "year": 1983, "result": "won"},
            {"award": "Saturn Award", "category": "Best Actor", "year": 1986, "result": "nominated"},
        ]
        grouped = [{"award": "Saturn Award", "wins": 1, "nominations": 1, "items": awards}]
        captured = {}

        def fake_fetch(tmdb_id=None, imdb_id=None, wikidata_id=None, language=None):
            captured["args"] = (tmdb_id, imdb_id, wikidata_id, language)
            return {"wikidataId": "Q44519", "awards": awards}

        fake_module = types.SimpleNamespace(
            fetch_person_awards=fake_fetch,
            group_awards=lambda value: grouped,
        )

        try:
            tmdb_plugin._import_wikidata_awards = lambda: fake_module
            result = tmdb_plugin.person_awards(
                {"tmdbId": "585", "imdbId": "nm0000442"},
                {"settings": {"language": "nl-NL"}},
            )
        finally:
            tmdb_plugin._import_wikidata_awards = original_import

        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["provider"], "wikidata")
        self.assertEqual(result["wikidataId"], "Q44519")
        self.assertEqual(result["awardGroups"], grouped)
        self.assertEqual(result["counts"]["awards"], 2)
        self.assertEqual(captured["args"], ("585", "nm0000442", None, "nl"))

    def test_tmdb_person_awards_entrypoint_requires_identifier(self):
        result = tmdb_plugin.person_awards({}, {"settings": {}})

        self.assertEqual(result["status"], "miss")
        self.assertEqual(result["provider"], "wikidata")

    def test_native_person_detail_payload_flattens_ios_contract(self):
        detail = {
            "person": {
                "id": "person-uuid",
                "public_id": "person-public",
                "name": "Rutger Hauer",
                "profile_url": "https://example.test/profile.jpg",
                "birth_date": "1944-01-23",
                "death_date": "2019-07-19",
                "place_of_birth": "Breukelen, Utrecht, Netherlands",
                "known_for": "Acting",
                "metadata": {"biography_en": "English metadata biography"},
            },
            "identifiers": [{"provider_id": "tmdb", "identifier": "585"}],
            "tmdbId": "585",
            "localizations": [
                {"lang": "nl", "biography": "Nederlandse biografie"},
                {"lang": "en", "biography": "English biography"},
            ],
        }

        payload = native_person_detail_payload(detail, language="nl")

        self.assertEqual(payload["id"], "person-uuid")
        self.assertEqual(payload["tmdbId"], "585")
        self.assertEqual(payload["profileUrl"], "https://example.test/profile.jpg")
        self.assertEqual(payload["birthday"], "1944-01-23")
        self.assertEqual(payload["deathday"], "2019-07-19")
        self.assertEqual(payload["biography"], "Nederlandse biografie")
        self.assertEqual(payload["biography_nl"], "Nederlandse biografie")
        self.assertEqual(payload["biography_en"], "English biography")

    def test_native_person_detail_payload_accepts_empty_language(self):
        detail = {
            "person": {
                "id": "person-uuid",
                "name": "Example Person",
                "metadata": {"biography": "Fallback biography"},
            },
            "localizations": [],
        }

        payload = native_person_detail_payload(detail)

        self.assertEqual(payload["id"], "person-uuid")
        self.assertEqual(payload["biography"], "Fallback biography")

    def test_native_person_detail_payload_exposes_awards_aliases_mentions_media(self):
        detail = {
            "person": {
                "id": "person-uuid",
                "name": "Rutger Hauer",
                "metadata": {
                    "imdbId": "nm0000442",
                    "alsoKnownAs": ["Rutger Oelsen Hauer", "  "],
                    "awards": [{"award": "Saturn Award", "result": "won", "year": 1983}],
                    "awardGroups": [
                        {
                            "award": "Saturn Award",
                            "wins": 1,
                            "nominations": 0,
                            "items": [{"result": "won", "year": 1983, "category": "Best Actor"}],
                        }
                    ],
                    "profiles": ["https://image.tmdb.org/t/p/original/fallback.jpg"],
                },
            },
            "localizations": [],
            "counts": {"filmography": 12, "collection": 4},
            "personMedia": [
                {
                    "id": "media-1",
                    "url": "https://image.tmdb.org/t/p/original/primary.jpg",
                    "kind": "profile",
                    "is_primary": True,
                    "sort_order": 0,
                    "provider_id": "tmdb",
                },
                {
                    "id": "media-2",
                    "source_url": "https://image.tmdb.org/t/p/original/secondary.jpg",
                    "is_primary": False,
                    "sort_order": 1,
                },
            ],
        }

        payload = native_person_detail_payload(detail)

        self.assertEqual(payload["imdbId"], "nm0000442")
        self.assertEqual(payload["alsoKnownAs"], ["Rutger Oelsen Hauer"])
        self.assertEqual(payload["mentions"], 12)
        self.assertEqual(payload["awards"][0]["award"], "Saturn Award")
        self.assertEqual(payload["awardGroups"][0]["items"][0]["result"], "won")
        self.assertEqual(len(payload["media"]), 2)
        self.assertTrue(payload["media"][0]["isPrimary"])
        self.assertEqual(payload["media"][0]["id"], "media-1")
        self.assertEqual(
            payload["profiles"],
            [
                "https://image.tmdb.org/t/p/original/primary.jpg",
                "https://image.tmdb.org/t/p/original/secondary.jpg",
            ],
        )

    def test_native_person_detail_payload_uses_metadata_profiles_without_media(self):
        detail = {
            "person": {
                "id": "person-uuid",
                "name": "Example Person",
                "metadata": {"profiles": ["https://image.tmdb.org/t/p/original/only.jpg"]},
            },
            "localizations": [],
        }

        payload = native_person_detail_payload(detail)

        self.assertEqual(payload["media"], [])
        self.assertEqual(payload["profiles"], ["https://image.tmdb.org/t/p/original/only.jpg"])
        self.assertEqual(payload["mentions"], 0)

    def test_local_person_filmography_entries_expose_collection_and_digital_state(self):
        entries = person_local_filmography_entries(
            [
                {
                    "movie_id": "movie-uuid",
                    "movie_public_id": "movie-public",
                    "tmdb_id": "987654",
                    "title": "Collected Movie",
                    "year": "2026",
                    "poster_url": "https://example.test/poster.jpg",
                    "character": "Lead",
                    "credit_type": "actor",
                    "format": "4K UHD",
                }
            ],
            [
                {
                    "movie_id": "movie-uuid",
                    "digital_item_id": "digital-uuid",
                    "source_name": "Plex",
                    "source_type": "plex",
                    "plugin_id": "plex",
                    "playback_url": "https://plex.example.test/movie",
                }
            ],
        )

        self.assertEqual(entries[0]["tmdb_id"], "987654")
        self.assertEqual(entries[0]["movie_id"], "movie-uuid")
        self.assertTrue(entries[0]["in_collection"])
        self.assertTrue(entries[0]["in_digital"])
        self.assertEqual(entries[0]["digital_items"][0]["source_name"], "Plex")

    def test_person_filmography_merge_preserves_tmdb_and_local_fallback_entries(self):
        entries = merge_person_filmography_entries(
            [
                {
                    "tmdb_id": "42",
                    "title": "External Movie",
                    "character": "Lead",
                    "credit_type": "actor",
                }
            ],
            [
                {
                    "tmdb_id": "99",
                    "movie_id": "local-movie",
                    "title": "Local Movie",
                    "job": "Director",
                    "credit_type": "crew",
                    "in_collection": True,
                }
            ],
        )

        self.assertEqual([entry["tmdb_id"] for entry in entries], ["42", "99"])
        self.assertTrue(entries[1]["in_collection"])

    def test_native_person_filmography_payload_splits_cast_crew_and_digital_links(self):
        detail = {
            "person": {"id": "person-uuid", "name": "Example Person", "metadata": {}},
            "identifiers": [{"provider_id": "tmdb", "identifier": "123"}],
            "tmdbId": "123",
            "localizations": [],
            "filmography": [
                {
                    "tmdb_id": "42",
                    "movie_id": "movie-uuid",
                    "title": "Actor Movie",
                    "year": "2022",
                    "poster_url": "https://example.test/poster.jpg",
                    "character": "Lead",
                    "credit_type": "actor",
                    "in_collection": True,
                    "digital_items": [
                        {
                            "digital_item_id": "digital-1",
                            "source_name": "Plex",
                            "source_type": "plex",
                            "plugin_id": "plex",
                            "playback_url": "https://plex.example.test/movie",
                        }
                    ],
                },
                {
                    "tmdb_id": "43",
                    "title": "Crew Movie",
                    "job": "Director",
                    "credit_type": "crew",
                },
            ],
        }

        payload = native_person_filmography_payload(detail, language="nl")

        self.assertEqual(payload["personId"], "person-uuid")
        self.assertEqual(payload["counts"], {"cast": 1, "crew": 1, "total": 2})
        self.assertEqual(payload["cast"][0]["movieId"], "movie-uuid")
        self.assertTrue(payload["cast"][0]["inCollection"])
        self.assertTrue(payload["cast"][0]["inDigital"])
        self.assertEqual(payload["cast"][0]["digitalPlatformUrls"][0]["platform"], "Plex")
        self.assertEqual(payload["crew"][0]["job"], "Director")


if __name__ == "__main__":
    unittest.main()
