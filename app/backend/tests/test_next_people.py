import os
import sys
import types
import unittest
import unittest.mock


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_app import group_person_credits_by_job
    from app.backend.next_app import group_person_awards
    from app.backend.next_app import merge_person_awards
    from app.backend.next_app import merge_person_filmography_entries
    from app.backend.next_app import native_person_detail_payload
    from app.backend.next_app import native_person_filmography_payload
    from app.backend.next_app import person_biography_value
    from app.backend.next_app import person_filmography_entries_from_metadata
    from app.backend.next_app import person_local_filmography_entries
    from app.backend.next_app import person_receiver_contribution_payload
    from app.backend.next_app import sanitize_profile_urls
    from app.backend.next_app import select_movie_metadata_person_refresh_credits
    from app.backend.next_plugins.tmdb import plugin as tmdb_plugin
    from app.backend.next_plugins.movievault_26 import plugin as movievault_plugin
except ModuleNotFoundError as exc:  # Local minimal test environments may omit web/database deps.
    if exc.name not in {"flask", "requests", "psycopg"}:
        raise
    group_person_credits_by_job = None
    group_person_awards = None
    merge_person_awards = None
    merge_person_filmography_entries = None
    native_person_detail_payload = None
    native_person_filmography_payload = None
    person_biography_value = None
    person_filmography_entries_from_metadata = None
    person_local_filmography_entries = None
    person_receiver_contribution_payload = None
    sanitize_profile_urls = None
    select_movie_metadata_person_refresh_credits = None
    tmdb_plugin = None
    movievault_plugin = None


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
            self.assertEqual(params["append_to_response"], "images,external_ids,translations")
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

    def test_tmdb_person_details_collects_multilanguage_biography(self):
        original_request = tmdb_plugin._request

        def fake_request(context, path, **params):
            self.assertEqual(path, "/person/585")
            self.assertEqual(params.get("append_to_response"), "images,external_ids,translations")
            return {
                "name": "Rutger Hauer",
                "biography": "",
                "birthday": "1944-01-23",
                "place_of_birth": "Breukelen",
                "known_for_department": "Acting",
                "profile_path": "/profile.jpg",
                "translations": {
                    "translations": [
                        {"iso_639_1": "nl", "iso_3166_1": "NL", "data": {"biography": "Nederlandse biografie"}},
                        {"iso_639_1": "en", "iso_3166_1": "US", "data": {"biography": "English biography"}},
                        {"iso_639_1": "fr", "iso_3166_1": "FR", "data": {"biography": ""}},
                    ]
                },
            }

        try:
            tmdb_plugin._request = fake_request
            result = tmdb_plugin.person_details({"tmdbId": "585"}, {"settings": {"language": "nl-NL"}})
        finally:
            tmdb_plugin._request = original_request

        self.assertEqual(result["status"], "hit")
        langs = {row["lang"]: row["biography"] for row in result["localizations"]}
        self.assertEqual(langs["nl-NL"], "Nederlandse biografie")
        self.assertEqual(langs["en-US"], "English biography")
        self.assertNotIn("fr-FR", langs)
        self.assertEqual(result["biography"], "Nederlandse biografie")

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

    def test_tmdb_person_awards_entrypoint_miss_returns_empty_awards(self):
        original_import = tmdb_plugin._import_wikidata_awards
        fake_module = types.SimpleNamespace(
            fetch_person_awards=lambda **_kwargs: {"wikidataId": "", "awards": []},
            group_awards=lambda value: [],
        )

        try:
            tmdb_plugin._import_wikidata_awards = lambda: fake_module
            result = tmdb_plugin.person_awards({"tmdbId": "585"}, {"settings": {}})
        finally:
            tmdb_plugin._import_wikidata_awards = original_import

        self.assertEqual(result["status"], "miss")
        self.assertEqual(result["awards"], [])
        self.assertEqual(result["awardGroups"], [])
        self.assertEqual(result["counts"]["awards"], 0)

    def test_sanitize_profile_urls_https_only_dedup_and_capped(self):
        urls = sanitize_profile_urls(
            [
                "https://image.tmdb.org/t/p/original/a.jpg",
                "  https://image.tmdb.org/t/p/original/a.jpg  ",
                "http://image.tmdb.org/t/p/original/insecure.jpg",
                "/relative/local.jpg",
                "",
                None,
                "https://image.tmdb.org/t/p/original/b.jpg",
            ]
        )

        self.assertEqual(
            urls,
            [
                "https://image.tmdb.org/t/p/original/a.jpg",
                "https://image.tmdb.org/t/p/original/b.jpg",
            ],
        )

    def test_sanitize_profile_urls_respects_cap(self):
        candidates = [f"https://cdn.example.test/p/{index}.jpg" for index in range(20)]

        capped = sanitize_profile_urls(candidates, cap=12)

        self.assertEqual(len(capped), 12)
        self.assertEqual(capped[0], "https://cdn.example.test/p/0.jpg")
        self.assertEqual(capped[-1], "https://cdn.example.test/p/11.jpg")

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
            "counts": {"filmography": 12, "collection": 4, "digital": 3},
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
        self.assertEqual(payload["mentionsInVault"], 7)
        self.assertEqual(payload["mentionsTotal"], 12)
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
        self.assertEqual(payload["mentionsInVault"], 0)
        self.assertEqual(payload["mentionsTotal"], 0)

    def test_native_person_detail_payload_splits_mentions_in_vault_and_total(self):
        detail = {
            "person": {"id": "person-uuid", "name": "Example Person", "metadata": {}},
            "localizations": [],
            "counts": {"collection": 3, "digital": 2, "filmography": 142},
        }

        payload = native_person_detail_payload(detail)

        self.assertEqual(payload["mentionsInVault"], 5)
        self.assertEqual(payload["mentionsTotal"], 142)

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

    def test_person_receiver_contribution_payload_includes_localized_biographies(self):
        payload = person_receiver_contribution_payload(
            person_id="00000000-0000-0000-0000-000000000001",
            person={"public_id": "person-public", "name": "Rutger Hauer"},
            tmdb_id="585",
            updates={
                "name": "Rutger Hauer",
                "biography": "Nederlandse biografie",
                "birth_date": "1944-01-23",
                "death_date": "2019-07-19",
                "place_of_birth": "Breukelen",
                "known_for": "Acting",
                "profile_url": "https://example.test/profile.jpg",
            },
            localizations=[
                {"lang": "nl-NL", "biography": "Nederlandse biografie"},
                {"lang": "en-US", "biography": "English biography"},
            ],
            source_providers=["tmdb", "tmdb"],
        )

        self.assertEqual(payload["entityType"], "person")
        self.assertEqual(payload["identity"], "person-public")
        self.assertEqual(payload["sourceRef"], "person-public")
        self.assertEqual(payload["sourceReference"]["type"], "discvault_person")
        self.assertEqual(payload["sourceReference"]["tmdbId"], "585")
        self.assertEqual(payload["payload"]["name"], "Rutger Hauer")
        self.assertEqual(payload["payload"]["tmdbId"], "585")
        self.assertEqual(payload["payload"]["biography"], "Nederlandse biografie")
        self.assertEqual(payload["payload"]["photoUrl"], "https://example.test/profile.jpg")
        self.assertEqual(payload["payload"]["biography_nl-nl"], "Nederlandse biografie")
        self.assertEqual(payload["payload"]["biography_nl"], "Nederlandse biografie")
        self.assertEqual(payload["payload"]["biography_en-us"], "English biography")
        self.assertEqual(payload["payload"]["biography_en"], "English biography")
        self.assertEqual(payload["metadata"]["sourceProviders"], ["tmdb"])
        self.assertEqual(payload["metadata"]["personId"], "00000000-0000-0000-0000-000000000001")

    def test_person_receiver_contribution_payload_drops_empty_fields(self):
        payload = person_receiver_contribution_payload(
            person_id="person-uuid",
            person={"name": "Example Person"},
            tmdb_id="",
            updates={"name": "Example Person", "biography": "", "profile_url": ""},
            localizations=[],
            source_providers=[],
        )

        self.assertEqual(payload["identity"], "person-uuid")
        self.assertNotIn("biography", payload["payload"])
        self.assertNotIn("tmdbId", payload["payload"])
        self.assertNotIn("photoUrl", payload["payload"])
        self.assertEqual(payload["payload"]["name"], "Example Person")
        self.assertEqual(payload["metadata"]["sourceProviders"], [])

    def test_refresh_person_metadata_dry_run_preview_includes_localizations_profiles_awards(self):
        from app.backend import next_app

        detail = {
            "tmdbId": "585",
            "person": {"id": "person-uuid", "name": "Rutger Hauer", "metadata": {}},
            "localizations": [],
            "personMedia": [],
            "counts": {},
        }
        person_result = {
            "status": "hit",
            "provider": "tmdb",
            "sourceRef": "tmdb:person:585",
            "name": "Rutger Hauer",
            "biography": "Nederlandse biografie",
            "birthday": "1944-01-23",
            "language": "nl-NL",
            "alsoKnownAs": ["Rutger Oelsen Hauer"],
            "imdbId": "nm0000442",
            "profileUrl": "https://image.tmdb.org/t/p/original/primary.jpg",
            "profiles": [
                "https://image.tmdb.org/t/p/original/primary.jpg",
                "https://image.tmdb.org/t/p/original/secondary.jpg",
            ],
            "localizations": [
                {"lang": "nl-NL", "biography": "Nederlandse biografie"},
                {"lang": "en-US", "biography": "English biography"},
            ],
        }
        awards = [{"award": "Saturn Award", "result": "won", "year": 1983}]
        award_groups = [{"award": "Saturn Award", "wins": 1, "nominations": 0, "items": awards}]
        awards_result = {
            "status": "hit",
            "provider": "wikidata",
            "sourceRef": "wikidata:Q44519",
            "awards": awards,
            "awardGroups": award_groups,
        }

        def fake_run(plugin_id, entrypoint, payload, context):
            if entrypoint == "person_details":
                return {"status": "ok", "state": "ok", "result": person_result}
            if entrypoint == "person_awards":
                return {"status": "ok", "state": "ok", "result": awards_result}
            return {"status": "error"}

        plugin = {"id": "tmdb", "name": "TMDb", "enabled": True}
        with unittest.mock.patch.multiple(
            next_app,
            person_detail_entity=unittest.mock.DEFAULT,
            person_metadata_source_plugins=unittest.mock.DEFAULT,
            plugin_config_from_db=unittest.mock.DEFAULT,
            plugin_requires_config_for_entrypoint=unittest.mock.DEFAULT,
            plugin_execution_context=unittest.mock.DEFAULT,
            run_plugin_entrypoint=unittest.mock.DEFAULT,
        ) as mocks:
            mocks["person_detail_entity"].return_value = detail
            mocks["person_metadata_source_plugins"].return_value = [plugin]
            mocks["plugin_config_from_db"].return_value = {}
            mocks["plugin_requires_config_for_entrypoint"].return_value = False
            mocks["plugin_execution_context"].return_value = {}
            mocks["run_plugin_entrypoint"].side_effect = fake_run
            result = next_app.refresh_person_metadata(object(), "person-uuid", dry_run=True)

        self.assertTrue(result["dryRun"])
        self.assertEqual(result["sourceProviders"], ["tmdb"])
        self.assertTrue(result["awards"]["applied"])
        preview = result["detail"]
        # Multi-language localizations land in the preview, alongside profiles/awards.
        langs = {row["lang"] for row in preview["localizations"]}
        self.assertEqual(langs, {"nl-NL", "en-US"})
        media_urls = [item["url"] for item in preview["personMedia"]]
        self.assertIn("https://image.tmdb.org/t/p/original/primary.jpg", media_urls)
        self.assertTrue(preview["personMedia"][0]["is_primary"])
        self.assertTrue(all(item["preview"] for item in preview["personMedia"]))
        meta = preview["person"]["metadata"]
        self.assertEqual(meta["alsoKnownAs"], ["Rutger Oelsen Hauer"])
        self.assertEqual(meta["imdbId"], "nm0000442")
        self.assertEqual(meta["profiles"][0], "https://image.tmdb.org/t/p/original/primary.jpg")
        self.assertEqual(meta["awards"], awards)
        self.assertEqual(meta["awardGroups"], award_groups)
        self.assertEqual(meta["person_metadata_sources"], ["tmdb"])

    def test_movievault_person_details_maps_people_read_api(self):
        mv_person = {
            "id": "mv_person_31",
            "movieVaultId": "mv_person_31",
            "name": "Tom Hanks",
            "tmdbId": "31",
            "imdbId": "nm0000158",
            "biography": "An American actor and filmmaker.",
            "birthday": "1956-07-09",
            "placeOfBirth": "Concord, California, USA",
            "knownFor": "Acting",
            "profileUrl": "https://image.tmdb.org/t/p/original/a.jpg",
            "biography_nl": "Een Amerikaanse acteur en filmmaker.",
            "profiles": [
                "https://image.tmdb.org/t/p/original/a.jpg",
                "https://image.tmdb.org/t/p/original/b.jpg",
                "/relative/should-be-dropped.jpg",
            ],
            "alsoKnownAs": ["Thomas Jeffrey Hanks", "Tom Hanks"],
            "awards": [
                {
                    "award": "Academy Award for Best Actor",
                    "awardWikidataId": "Q103916",
                    "category": None,
                    "year": 1995,
                    "work": "Forrest Gump",
                    "workWikidataId": "Q134773",
                    "workTmdbId": 13,
                    "result": "won",
                    "source": "wikidata",
                    "sourceRef": "wikidata:Q2263",
                }
            ],
        }
        captured = {}

        def fake_get(context, path, **params):
            captured["path"] = path
            captured["params"] = params
            return {"results": [mv_person]}

        original = movievault_plugin._get
        try:
            movievault_plugin._get = fake_get
            result = movievault_plugin.person_details(
                {"tmdbId": "31"},
                {"settings": {"language": "nl-NL"}, "movievault": {"enabled": True}},
            )
        finally:
            movievault_plugin._get = original

        self.assertEqual(captured["path"], "/api/v1/people")
        self.assertEqual(captured["params"].get("tmdbId"), "31")
        self.assertNotIn("q", {k: v for k, v in captured["params"].items() if v})
        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["provider"], "movievault_26")
        self.assertEqual(result["tmdbId"], "31")
        self.assertIsInstance(result["tmdbId"], str)
        self.assertEqual(result["imdbId"], "nm0000158")
        self.assertEqual(result["name"], "Tom Hanks")
        self.assertEqual(result["alsoKnownAs"], ["Thomas Jeffrey Hanks", "Tom Hanks"])
        # Configured-language (nl) localized biography is preferred for the top-level bio.
        self.assertEqual(result["biography"], "Een Amerikaanse acteur en filmmaker.")
        langs = {row["lang"]: row["biography"] for row in result["localizations"]}
        self.assertEqual(langs["nl"], "Een Amerikaanse acteur en filmmaker.")
        # Relative/non-https profile entries are dropped; https ones kept and deduped.
        self.assertEqual(
            result["profiles"],
            [
                "https://image.tmdb.org/t/p/original/a.jpg",
                "https://image.tmdb.org/t/p/original/b.jpg",
            ],
        )
        award = result["awards"][0]
        self.assertEqual(award["award"], "Academy Award for Best Actor")
        self.assertEqual(award["awardWikidataId"], "Q103916")
        self.assertEqual(award["year"], 1995)
        self.assertEqual(award["workTmdbId"], 13)
        self.assertEqual(award["result"], "won")
        self.assertEqual(award["source"], "wikidata")

    def test_movievault_person_details_fetches_by_movievault_id(self):
        mv_person = {"id": "mv_person_7", "movieVaultId": "mv_person_7", "name": "Jane Doe", "tmdbId": "7"}
        captured = {}

        def fake_get(context, path, **params):
            captured["path"] = path
            return mv_person

        original = movievault_plugin._get
        try:
            movievault_plugin._get = fake_get
            result = movievault_plugin.person_details(
                {"movieVaultId": "mv_person_7"},
                {"movievault": {"enabled": True}},
            )
        finally:
            movievault_plugin._get = original

        self.assertEqual(captured["path"], "/api/v1/people/mv_person_7")
        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["name"], "Jane Doe")
        self.assertEqual(result["sourceRef"], "movievault:person:mv_person_7")

    def test_merge_person_awards_dedupes_and_collapses_won_over_nominated(self):
        wikidata = [
            {
                "award": "Academy Award for Best Actor",
                "awardWikidataId": "Q103916",
                "year": 1995,
                "work": "Forrest Gump",
                "workWikidataId": "Q134773",
                "result": "nominated",
                "source": "wikidata",
            }
        ]
        movievault = [
            {
                "award": "Academy Award for Best Actor",
                "awardWikidataId": "Q103916",
                "year": 1995,
                "work": "Forrest Gump",
                "workWikidataId": "Q134773",
                "result": "won",
                "source": "movievault_26",
            },
            {
                "award": "Saturn Award",
                "awardWikidataId": "",
                "year": 1989,
                "work": "Big",
                "workWikidataId": "",
                "result": "nominated",
                "source": "movievault_26",
            },
        ]
        merged = merge_person_awards(wikidata, movievault)
        self.assertEqual(len(merged), 2)
        forrest = [a for a in merged if a["award"] == "Academy Award for Best Actor"]
        self.assertEqual(len(forrest), 1)
        self.assertEqual(forrest[0]["result"], "won")

    def test_group_person_awards_groups_and_counts(self):
        awards = [
            {"award": "Academy Award", "awardWikidataId": "Q103916", "year": 1995, "result": "won"},
            {"award": "Academy Award", "awardWikidataId": "Q103916", "year": 2001, "result": "nominated"},
        ]
        groups = group_person_awards(awards)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["award"], "Academy Award")
        self.assertEqual(groups[0]["wins"], 1)
        self.assertEqual(groups[0]["nominations"], 1)
        self.assertEqual([item["year"] for item in groups[0]["items"]], [2001, 1995])

    def test_refresh_person_metadata_merges_movievault_inline_awards(self):
        from app.backend import next_app

        detail = {
            "tmdbId": "31",
            "person": {"id": "person-uuid", "name": "Tom Hanks", "metadata": {}},
            "localizations": [],
            "personMedia": [],
            "counts": {},
        }
        tmdb_result = {
            "status": "hit",
            "provider": "tmdb",
            "sourceRef": "tmdb:person:31",
            "name": "Tom Hanks",
            "biography": "An American actor.",
            "birthday": "1956-07-09",
            "language": "en-US",
            "profileUrl": "https://image.tmdb.org/t/p/original/a.jpg",
            "profiles": ["https://image.tmdb.org/t/p/original/a.jpg"],
            "localizations": [],
        }
        mv_awards = [
            {
                "award": "Academy Award for Best Actor",
                "awardWikidataId": "Q103916",
                "year": 1995,
                "work": "Forrest Gump",
                "workWikidataId": "Q134773",
                "result": "won",
                "source": "movievault_26",
            }
        ]
        mv_result = {
            "status": "hit",
            "provider": "movievault_26",
            "sourceRef": "movievault:person:tmdb:31",
            "name": "Tom Hanks",
            "biography": "",
            "language": "en-US",
            "awards": mv_awards,
            "localizations": [],
        }

        def fake_run(plugin_id, entrypoint, payload, context):
            if entrypoint == "person_details":
                if plugin_id == "tmdb":
                    return {"status": "ok", "state": "ok", "result": tmdb_result}
                return {"status": "ok", "state": "ok", "result": mv_result}
            if entrypoint == "person_awards":
                # Wikidata returns nothing; the MovieVault inline awards must still win.
                return {"status": "ok", "state": "ok", "result": {"awards": [], "awardGroups": []}}
            return {"status": "error"}

        plugins = [
            {"id": "tmdb", "name": "TMDb", "enabled": True},
            {"id": "movievault_26", "name": "MovieVault 26", "enabled": True},
        ]
        with unittest.mock.patch.multiple(
            next_app,
            person_detail_entity=unittest.mock.DEFAULT,
            person_metadata_source_plugins=unittest.mock.DEFAULT,
            plugin_config_from_db=unittest.mock.DEFAULT,
            plugin_requires_config_for_entrypoint=unittest.mock.DEFAULT,
            plugin_execution_context=unittest.mock.DEFAULT,
            run_plugin_entrypoint=unittest.mock.DEFAULT,
        ) as mocks:
            mocks["person_detail_entity"].return_value = detail
            mocks["person_metadata_source_plugins"].return_value = plugins
            mocks["plugin_config_from_db"].return_value = {}
            mocks["plugin_requires_config_for_entrypoint"].return_value = False
            mocks["plugin_execution_context"].return_value = {}
            mocks["run_plugin_entrypoint"].side_effect = fake_run
            result = next_app.refresh_person_metadata(object(), "person-uuid", dry_run=True)

        self.assertTrue(result["awards"]["applied"])
        self.assertEqual(result["sourceProviders"], ["movievault_26", "tmdb"])
        meta = result["detail"]["person"]["metadata"]
        self.assertEqual(len(meta["awards"]), 1)
        self.assertEqual(meta["awards"][0]["award"], "Academy Award for Best Actor")
        self.assertEqual(meta["awards"][0]["result"], "won")
        self.assertEqual(meta["awards"][0]["source"], "movievault_26")
        self.assertEqual(meta["awards_source"], "movievault_26")
        self.assertEqual(meta["awardGroups"][0]["wins"], 1)
        self.assertEqual(meta["person_metadata_sources"], ["movievault_26", "tmdb"])


if __name__ == "__main__":
    unittest.main()
