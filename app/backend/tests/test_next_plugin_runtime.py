import os
import importlib
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

sys.modules.setdefault(
    "requests",
    types.SimpleNamespace(
        get=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("requests is stubbed in tests")),
        HTTPError=Exception,
    ),
)
sys.modules.setdefault("bs4", types.SimpleNamespace(BeautifulSoup=lambda *_args, **_kwargs: None))
psycopg_module = types.ModuleType("psycopg")
psycopg_rows_module = types.ModuleType("psycopg.rows")
psycopg_rows_module.dict_row = object()
psycopg_types_module = types.ModuleType("psycopg.types")
psycopg_types_json_module = types.ModuleType("psycopg.types.json")
psycopg_types_json_module.Jsonb = lambda value: value
sys.modules.setdefault("psycopg", psycopg_module)
sys.modules.setdefault("psycopg.rows", psycopg_rows_module)
sys.modules.setdefault("psycopg.types", psycopg_types_module)
sys.modules.setdefault("psycopg.types.json", psycopg_types_json_module)

from app.backend.next_plugin_runtime import DEFAULT_PLUGIN_DIR
from app.backend.next_plugin_runtime import PLUGIN_INITIALIZED_MARKER
from app.backend.next_plugin_runtime import discover_plugins
from app.backend.next_plugin_runtime import plugin_install_dir
from app.backend.next_plugin_runtime import plugin_paths
from app.backend.next_plugin_runtime import run_plugin_entrypoint
from app.backend.next_plugin_runtime import seed_default_plugins_if_needed
from app.backend import next_worker
from app.backend.next_worker import apply_collection_import_review
from app.backend.next_worker import import_release_date
from app.backend.next_worker import import_year
from app.backend.next_plugins._collection_import_base import CollectionImportPlugin
from app.backend.next_plugins._collection_import_base import parse_release_date
from app.backend.next_plugins.bluray_com.plugin import _movie_title_from_release_title
from app.backend.next_plugins.bluray_com import plugin as bluray_com_plugin
from app.backend.next_plugins.trakt import plugin as trakt_plugin
from app.backend.next_plugin_runtime import (
    _plugin_is_active,
    unconfigured_integration_plugins,
)
from app.backend.next_plugin_runtime import _parse_plugin_version
from app.backend.next_plugin_runtime import install_bundled_plugin_update
from app.backend.next_plugin_runtime import installed_plugin_version
from app.backend.next_plugin_runtime import plugin_auto_update_enabled
from app.backend.next_plugin_runtime import plugin_backup_version
from app.backend.next_plugin_runtime import plugin_has_backup
from app.backend.next_plugin_runtime import plugin_update_state
from app.backend.next_plugin_runtime import rollback_plugin_update
from app.backend.next_plugin_runtime import set_plugin_auto_update_enabled
from app.backend.next_plugin_runtime import upgrade_seeded_default_plugins
from app.backend.next_plugin_runtime import write_plugin_auto_update_marker


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code < 400:
            return
        error = FakeHTTPError(f"{self.status_code} error")
        error.response = self
        raise error


class FakeHTTPError(Exception):
    pass


class NextPluginRuntimeTests(unittest.TestCase):
    def test_plugin_import_job_fails_when_persistence_writes_no_rows(self):
        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with (
            patch.object(next_worker, "connect", return_value=FakeConnection()),
            patch.object(next_worker, "plugin_execution_context_from_db", return_value={}),
            patch.object(
                next_worker,
                "run_plugin_entrypoint",
                return_value={"status": "ok", "result": {"status": "completed", "items": [{"title": "RoboCop"}]}},
            ),
            patch.object(
                next_worker,
                "persist_collection_import",
                return_value={
                    "failed": True,
                    "error": "Import source returned 1 item(s), but none could be written.",
                    "items": 1,
                    "imported": 0,
                    "errors": [{"index": 1, "title": "RoboCop", "error": "database error"}],
                },
            ),
        ):
            with self.assertRaises(next_worker.JobFailure) as raised:
                next_worker.process_plugin_execute(
                    {"pluginId": "import_clz_movies", "entrypoint": "import_source", "payload": {}},
                    "worker-1",
                )

        self.assertEqual(str(raised.exception), "Import source returned 1 item(s), but none could be written.")
        self.assertTrue(raised.exception.result["persistence"]["failed"])
        self.assertEqual(raised.exception.result["persistence"]["imported"], 0)

    def test_collection_import_year_only_release_date_is_not_emitted_as_date(self):
        plugin = CollectionImportPlugin(
            {
                "id": "import_test",
                "name": "Import Test",
                "defaultPath": "",
                "sourceKind": "test_csv",
                "aliases": {},
            }
        )
        item = plugin.normalize_row(
            {"Title": "RoboCop", "Release Date": "1987"},
            Path("movies.csv"),
            1,
        )

        self.assertEqual(item["year"], "1987")
        self.assertNotIn("releaseDate", item)

    def test_collection_import_keeps_full_release_dates(self):
        plugin = CollectionImportPlugin(
            {
                "id": "import_test",
                "name": "Import Test",
                "defaultPath": "",
                "sourceKind": "test_csv",
                "aliases": {},
            }
        )
        item = plugin.normalize_row(
            {"Title": "RoboCop", "Release Date": "17-07-1987"},
            Path("movies.csv"),
            1,
        )

        self.assertEqual(item["year"], "1987")
        self.assertEqual(item["releaseDate"], "1987-07-17")

    def test_collection_import_explicit_box_set_columns_create_proposal(self):
        plugin = CollectionImportPlugin(
            {
                "id": "import_test",
                "name": "Import Test",
                "defaultPath": "",
                "sourceKind": "test_csv",
                "aliases": {},
            }
        )
        item = plugin.normalize_row(
            {
                "Barcode": "883929704736",
                "Title": "The Lord of the Rings: The Motion Picture Trilogy",
                "Year": "2020",
                "Format": "4K UHD",
                "IsBoxSet": "Yes",
                "BoxSetMembers": "The Fellowship of the Ring|The Two Towers|The Return of the King",
                "Region": "US",
                "Verified": "Yes",
            },
            Path("top50_4kuhd_box-sets.csv"),
            1,
        )

        self.assertEqual(item["itemType"], "box_set")
        self.assertTrue(item["isBoxSet"])
        self.assertEqual(item["country"], "US")
        self.assertTrue(item["verified"])
        self.assertEqual(len(item["boxSetMembers"]), 3)
        self.assertEqual(item["boxSetMembers"][0]["format"], "4K UHD")
        self.assertEqual(item["boxSetProposal"]["memberCount"], 3)
        self.assertTrue(item["boxSetProposal"]["boxSetEvidence"]["membersAreExplicit"])
        self.assertEqual(item["containers"][0]["containerType"], "box_set")

    def test_import_worker_release_date_normalization_is_defensive(self):
        self.assertEqual(import_year("Released in 1998"), "1998")
        self.assertIsNone(import_release_date("1998"))
        self.assertIsNone(import_release_date("not a date"))
        self.assertEqual(import_release_date("1998/07/24"), "1998-07-24")
        self.assertEqual(parse_release_date("24-07-1998"), "1998-07-24")

    def test_import_metadata_box_set_proposals_accepts_confirmed_barcode_members(self):
        metadata = {
            "query": {"barcode": "883929704729"},
            "results": [
                {
                    "pluginId": "movievault_26",
                    "boxSetProposal": {
                        "title": "Example Trilogy",
                        "barcode": "883929704729",
                        "provider": "movievault_26",
                        "members": [
                            {"title": "Example One", "year": "2001", "format": "Blu-ray"},
                            {"title": "Example Two", "year": "2002", "format": "Blu-ray"},
                        ],
                        "boxSetEvidence": {
                            "barcodeMatch": True,
                            "membersAreExplicit": True,
                            "memberConfidence": "confirmed",
                            "memberSource": "MovieVault",
                        },
                    },
                }
            ],
        }

        proposals = next_worker.import_metadata_box_set_proposals(metadata)

        self.assertEqual(len(proposals), 1)
        self.assertTrue(next_worker.import_box_set_auto_importable(proposals[0]))
        self.assertEqual(next_worker.import_box_set_member_list(proposals[0])[0]["title"], "Example One")

    def test_import_metadata_box_set_proposals_rejects_candidate_only_sets(self):
        metadata = {
            "query": {"barcode": "883929704729"},
            "results": [
                {
                    "pluginId": "bluray_com",
                    "boxSetProposal": {
                        "title": "Maybe A Set",
                        "barcode": "883929704729",
                        "provider": "bluray_com",
                        "members": [
                            {"title": "Candidate One"},
                            {"title": "Candidate Two"},
                        ],
                        "boxSetEvidence": {
                            "barcodeMatch": True,
                            "membersAreExplicit": False,
                            "memberConfidence": "candidate",
                            "memberSource": "metadata_candidates",
                            "detectedWithoutMembers": True,
                        },
                    },
                }
            ],
        }

        proposals = next_worker.import_metadata_box_set_proposals(metadata)

        self.assertEqual(len(proposals), 1)
        self.assertFalse(next_worker.import_box_set_auto_importable(proposals[0]))

    def test_import_box_set_member_item_does_not_reuse_parent_barcode(self):
        member_item = next_worker.import_box_set_member_item(
            {"title": "Parent Set", "barcode": "883929704729", "format": "Blu-ray"},
            {"barcode": "883929704729", "provider": "movievault_26"},
            {"title": "Member Movie", "year": "1999"},
            1,
        )

        self.assertEqual(member_item["title"], "Member Movie")
        self.assertNotIn("barcode", member_item)
        self.assertEqual(member_item["format"], "Blu-ray")

    def test_import_review_applies_selected_metadata_match(self):
        result = {
            "items": [
                {"title": "A Minecraft Movie 4K Blu-ray (SteelBook) (France)", "year": "2025"},
                {"title": "Skip Me", "year": "2024"},
            ],
            "counts": {"movies": 2},
        }
        reviewed = apply_collection_import_review(
            result,
            {
                "decisions": [
                    {
                        "index": 1,
                        "action": "create",
                        "metadataMatch": {
                            "provider": "tmdb",
                            "sourceLabel": "TMDb",
                            "title": "A Minecraft Movie",
                            "year": "2025",
                            "posterUrl": "https://image.example/poster.jpg",
                            "identifiers": {"tmdb": "950387"},
                        },
                        "manualOverride": {"year": "2025", "imdbId": "tt3566834"},
                    },
                    {"index": 2, "action": "skip"},
                ]
            },
        )

        self.assertEqual(len(reviewed["items"]), 1)
        self.assertEqual(reviewed["items"][0]["title"], "A Minecraft Movie")
        self.assertEqual(reviewed["items"][0]["tmdbId"], "950387")
        self.assertEqual(reviewed["items"][0]["imdbId"], "tt3566834")
        self.assertEqual(reviewed["items"][0]["posterUrl"], "https://image.example/poster.jpg")
        self.assertEqual(reviewed["counts"]["reviewSkipped"], 1)
        self.assertEqual(reviewed["counts"]["reviewMatched"], 1)

    def test_import_review_applies_box_set_proposal(self):
        result = {
            "items": [
                {
                    "title": "Back to the Future Trilogy",
                    "barcode": "5050582369601",
                    "format": "DVD",
                }
            ],
            "counts": {"movies": 1},
        }
        proposal = {
            "provider": "movievault_26",
            "title": "Back to the Future Trilogy",
            "barcode": "5050582369601",
            "format": "DVD",
            "members": [
                {"title": "Back to the Future", "year": "1985", "format": "DVD"},
                {"title": "Back to the Future Part II", "year": "1989", "format": "DVD"},
                {"title": "Back to the Future Part III", "year": "1990", "format": "DVD"},
            ],
            "boxSetEvidence": {
                "barcodeMatch": True,
                "entityType": "box_set",
                "memberConfidence": "identified",
                "membersAreExplicit": True,
                "detectedWithoutMembers": False,
            },
        }

        reviewed = apply_collection_import_review(
            result,
            {"decisions": [{"index": 1, "action": "create", "boxSetProposal": proposal}]},
        )

        item = reviewed["items"][0]
        self.assertEqual(item["itemType"], "box_set")
        self.assertEqual(item["entityType"], "box_set")
        self.assertEqual(item["boxSetTitle"], "Back to the Future Trilogy")
        self.assertEqual(item["boxSetMembers"][1]["title"], "Back to the Future Part II")
        self.assertEqual(item["boxSetMembers"][1]["format"], "DVD")
        self.assertEqual(item["containers"][0]["containerType"], "box_set")
        self.assertEqual(item["containers"][0]["memberCount"], 3)
        self.assertEqual(reviewed["counts"]["reviewMatched"], 1)

    def test_import_metadata_box_set_proposals_tolerates_missing_barcode_fields(self):
        metadata_result = {
            "query": {"barcode": "883929704736"},
            "results": [
                {
                    "pluginId": "movievault_26",
                    "boxSetProposal": {
                        "title": "The Lord of the Rings: The Motion Picture Trilogy",
                        "members": [
                            {"title": "The Fellowship of the Ring"},
                            {"title": "The Two Towers"},
                            {"title": "The Return of the King"},
                        ],
                        "boxSetEvidence": {
                            "barcodeMatch": True,
                            "membersAreExplicit": True,
                            "memberConfidence": "identified",
                        },
                    },
                }
            ],
        }

        proposals = next_worker.import_metadata_box_set_proposals(metadata_result)

        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["provider"], "movievault_26")
        self.assertEqual(len(proposals[0]["members"]), 3)

    def test_bluray_release_title_is_cleaned_for_movie_identity(self):
        self.assertEqual(
            _movie_title_from_release_title("A Minecraft Movie 4K Blu-ray (SteelBook) (France)"),
            "A Minecraft Movie",
        )
        self.assertEqual(_movie_title_from_release_title("Back to the Future DVD"), "Back to the Future")

    def test_bluray_dvd_section_search_ignores_bluray_cross_links(self):
        captured_sections = []

        def fake_post(url, data=None, headers=None, timeout=None):
            captured_sections.append((data or {}).get("section"))
            text = (
                "var urls = new Array("
                "'/movies/Lethal-Weapon-2-Blu-ray/12345/',"
                "'/dvd/Lethal-Weapon-2-DVD/67890/'"
                ")"
            )
            return types.SimpleNamespace(status_code=200, text=text)

        original_requests = bluray_com_plugin.requests
        try:
            bluray_com_plugin.requests = types.SimpleNamespace(post=fake_post)
            urls = bluray_com_plugin._release_urls("Lethal Weapon 2", preferred_format="DVD", limit=8)
        finally:
            bluray_com_plugin.requests = original_requests

        self.assertEqual(captured_sections, ["dvdmovies"])
        self.assertEqual(urls, ["https://www.blu-ray.com/dvd/Lethal-Weapon-2-DVD/67890/"])

    def test_bluray_bluray_section_search_ignores_dvd_cross_links(self):
        def fake_post(url, data=None, headers=None, timeout=None):
            text = (
                "var urls = new Array("
                "'/dvd/Heat-DVD/111/',"
                "'/movies/Heat-Blu-ray/222/'"
                ")"
            )
            return types.SimpleNamespace(status_code=200, text=text)

        original_requests = bluray_com_plugin.requests
        try:
            bluray_com_plugin.requests = types.SimpleNamespace(post=fake_post)
            urls = bluray_com_plugin._release_urls("Heat", preferred_format="Blu-ray", limit=8)
        finally:
            bluray_com_plugin.requests = original_requests

        self.assertEqual(urls, ["https://www.blu-ray.com/movies/Heat-Blu-ray/222/"])

    def test_legacy_import_plugin_is_not_bundled(self):
        discovery = discover_plugins()
        plugins = {plugin.plugin_id: plugin for plugin in discovery["plugins"]}

        self.assertNotIn("discvault_legacy_import", plugins)
        self.assertNotIn("movievault", plugins)

    def test_plugin_install_dir_defaults_to_data_plugins(self):
        with tempfile.TemporaryDirectory() as data_dir:
            with patch.dict(
                os.environ,
                {
                    "DISCVAULT_DATA_DIR": data_dir,
                    "DISCVAULT_PLUGIN_INSTALL_DIR": "",
                    "DISCVAULT_PLUGIN_PATHS": "",
                },
            ):
                self.assertEqual(plugin_install_dir(), Path(data_dir) / "plugins")

    def test_data_plugins_override_bundled_plugin_ids(self):
        with tempfile.TemporaryDirectory() as data_dir:
            plugin_dir = Path(data_dir) / "plugins" / "tmdb"
            plugin_dir.mkdir(parents=True)
            (plugin_dir / "manifest.json").write_text(
                """
                {
                  "id": "tmdb",
                  "name": "TMDb Data Override",
                  "version": "9.9.9",
                  "manifestVersion": 1,
                  "discVaultPluginApi": "next-1",
                  "categories": ["metadata_source"],
                  "capabilities": ["search_title"],
                  "settingsSchemaVersion": 1,
                  "settingsSchema": {"settings": [], "secrets": []}
                }
                """,
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "DISCVAULT_DATA_DIR": data_dir,
                    "DISCVAULT_PLUGIN_INSTALL_DIR": "",
                    "DISCVAULT_PLUGIN_PATHS": "",
                },
            ):
                discovery = discover_plugins()
        plugins = {plugin.plugin_id: plugin for plugin in discovery["plugins"]}

        self.assertEqual(plugins["tmdb"].manifest["name"], "TMDb Data Override")
        self.assertEqual(plugins["tmdb"].manifest["version"], "9.9.9")
        self.assertEqual(plugins["tmdb"].path, plugin_dir.resolve())

    def test_seed_default_plugins_copies_bundled_into_install_dir(self):
        with tempfile.TemporaryDirectory() as data_dir:
            with patch.dict(
                os.environ,
                {
                    "DISCVAULT_DATA_DIR": data_dir,
                    "DISCVAULT_PLUGIN_INSTALL_DIR": "",
                    "DISCVAULT_PLUGIN_PATHS": "",
                    "DISCVAULT_BUNDLED_PLUGIN_DIR": "",
                },
            ):
                result = seed_default_plugins_if_needed()
                install_dir = Path(data_dir) / "plugins"

                self.assertTrue(result["initialized"])
                self.assertTrue((install_dir / PLUGIN_INITIALIZED_MARKER).exists())
                # A known bundled default now lives in the writable install dir.
                self.assertTrue((install_dir / "tmdb" / "manifest.json").exists())
                self.assertIn("tmdb", result["seeded"])

    def test_plugin_paths_uses_install_dir_only_after_seeding(self):
        with tempfile.TemporaryDirectory() as data_dir:
            with patch.dict(
                os.environ,
                {
                    "DISCVAULT_DATA_DIR": data_dir,
                    "DISCVAULT_PLUGIN_INSTALL_DIR": "",
                    "DISCVAULT_PLUGIN_PATHS": "",
                    "DISCVAULT_BUNDLED_PLUGIN_DIR": "",
                },
            ):
                paths = plugin_paths()

                install_dir = (Path(data_dir) / "plugins").resolve()
                self.assertIn(install_dir, paths)
                # Bundled dir is no longer a read path once seeding succeeded, so
                # a deleted default cannot reappear from the read-only image.
                self.assertNotIn(DEFAULT_PLUGIN_DIR.resolve(), paths)

    def test_deleted_default_plugin_is_not_reseeded(self):
        with tempfile.TemporaryDirectory() as data_dir:
            with patch.dict(
                os.environ,
                {
                    "DISCVAULT_DATA_DIR": data_dir,
                    "DISCVAULT_PLUGIN_INSTALL_DIR": "",
                    "DISCVAULT_PLUGIN_PATHS": "",
                    "DISCVAULT_BUNDLED_PLUGIN_DIR": "",
                },
            ):
                seed_default_plugins_if_needed()
                install_dir = Path(data_dir) / "plugins"
                tmdb_dir = install_dir / "tmdb"
                self.assertTrue(tmdb_dir.exists())

                # Simulate deleting a bundled default plugin.
                shutil.rmtree(tmdb_dir)

                # A second discovery must not bring it back (marker gates reseed).
                second = seed_default_plugins_if_needed()
                self.assertTrue(second["initialized"])
                self.assertFalse(tmdb_dir.exists())

                plugins = {plugin.plugin_id for plugin in discover_plugins()["plugins"]}
                self.assertNotIn("tmdb", plugins)
                # Other defaults remain available.
                self.assertIn("movievault_26", plugins)

    def test_plugin_paths_falls_back_to_bundled_when_data_dir_unavailable(self):
        with tempfile.TemporaryDirectory() as data_dir:
            # Point the install dir under a non-existent data root so seeding is
            # skipped (mimics a read-only/absent data directory).
            missing_root = Path(data_dir) / "does" / "not" / "exist"
            with patch.dict(
                os.environ,
                {
                    "DISCVAULT_DATA_DIR": str(missing_root),
                    "DISCVAULT_PLUGIN_INSTALL_DIR": "",
                    "DISCVAULT_PLUGIN_PATHS": "",
                    "DISCVAULT_BUNDLED_PLUGIN_DIR": "",
                },
            ):
                seed = seed_default_plugins_if_needed()
                self.assertFalse(seed["initialized"])

                paths = plugin_paths()
                self.assertIn(DEFAULT_PLUGIN_DIR.resolve(), paths)
                # Bundled defaults stay discoverable in fallback mode.
                plugins = {plugin.plugin_id for plugin in discover_plugins()["plugins"]}
                self.assertIn("tmdb", plugins)

    def test_known_collection_import_plugins_are_discoverable(self):
        discovery = discover_plugins()
        plugins = {plugin.plugin_id: plugin for plugin in discovery["plugins"]}

        for plugin_id in ("import_mymovies_dk", "import_letterboxd", "import_bluray_com", "import_clz_movies"):
            with self.subTest(plugin_id=plugin_id):
                plugin = plugins[plugin_id]
                self.assertIn("import_source", plugin.manifest["categories"])
                self.assertIn("inspect_source", plugin.runtime["entrypoints"])
                self.assertIn("plan_import", plugin.runtime["entrypoints"])
                self.assertIn("import_source", plugin.runtime["entrypoints"])

    def test_personal_list_source_plugins_are_discoverable(self):
        discovery = discover_plugins()
        plugins = {plugin.plugin_id: plugin for plugin in discovery["plugins"]}

        for plugin_id in ("trakt", "plex", "jellyfin"):
            with self.subTest(plugin_id=plugin_id):
                plugin = plugins[plugin_id]
                self.assertIn("personal_list_source", plugin.manifest["categories"])
                self.assertIn("sync_personal_lists", plugin.runtime["entrypoints"])

    def test_tmdb_exposes_person_awards_capability_and_entrypoint(self):
        discovery = discover_plugins()
        plugins = {plugin.plugin_id: plugin for plugin in discovery["plugins"]}

        plugin = plugins["tmdb"]
        self.assertIn("person_awards", plugin.manifest["capabilities"])
        self.assertIn("person_awards", plugin.runtime["entrypoints"])
        self.assertIn("person_details", plugin.runtime["entrypoints"])

    def test_movievault_exposes_person_details_capability_and_entrypoint(self):
        discovery = discover_plugins()
        plugins = {plugin.plugin_id: plugin for plugin in discovery["plugins"]}

        plugin = plugins["movievault_26"]
        self.assertIn("metadata_source", plugin.manifest["categories"])
        self.assertIn("person_details", plugin.manifest["capabilities"])
        self.assertIn("person_details", plugin.runtime["entrypoints"])

    def test_upcitemdb_is_tagged_as_bootstrap_metadata_source(self):
        discovery = discover_plugins()
        plugins = {plugin.plugin_id: plugin for plugin in discovery["plugins"]}

        plugin = plugins["upcitemdb"]
        self.assertIn("metadata_source", plugin.manifest["categories"])
        self.assertIn("metadata_bootstrap", plugin.manifest["categories"])
        self.assertIn("bootstrap_lookup", plugin.manifest["capabilities"])
        self.assertEqual(plugin.manifest["bootstrap"]["onlyWhen"], "public_barcode_lookup")
        self.assertEqual(plugin.manifest["bootstrap"]["role"], "barcode_title_hint")

    def test_plex_personal_lists_maps_viewed_at_history(self):
        def fake_get(url, params=None, timeout=None):
            if url.endswith("/status/sessions/history/all"):
                return FakeResponse(
                    text="""
                    <MediaContainer>
                      <Video ratingKey="42" title="Back to the Future" year="1985" viewedAt="1772337600">
                        <Guid id="imdb://tt0088763" />
                        <Guid id="tmdb://105" />
                      </Video>
                    </MediaContainer>
                    """
                )
            if url.endswith("/identity"):
                return FakeResponse(text='<MediaContainer machineIdentifier="plex-machine" friendlyName="Plex Home" />')
            raise AssertionError(url)

        fake_requests = types.SimpleNamespace(get=Mock(side_effect=fake_get), HTTPError=FakeHTTPError)
        with patch.dict(sys.modules, {"requests": fake_requests}):
            plex_plugin = importlib.import_module("app.backend.next_plugins.plex.plugin")
            result = plex_plugin.sync_personal_lists(
                {},
                {"settings": {"baseUrl": "https://plex.local"}, "secrets": {"token": "plex-token"}},
            )

        watched = result["personalLists"]["watched"]
        self.assertEqual(result["counts"]["watched"], 1)
        self.assertEqual(watched[0]["watchedAt"], "2026-03-01T04:00:00+00:00")
        self.assertEqual(watched[0]["imdbId"], "tt0088763")

    def test_jellyfin_personal_lists_maps_last_played_date(self):
        def fake_get(url, params=None, headers=None, timeout=None):
            if url.endswith("/Users/user-1/Items"):
                return FakeResponse(
                    {
                        "Items": [
                            {
                                "Id": "jf-42",
                                "Name": "Back to the Future",
                                "ProductionYear": 1985,
                                "ProviderIds": {"Imdb": "tt0088763", "Tmdb": "105"},
                                "UserData": {"Played": True, "PlayCount": 2, "LastPlayedDate": "2026-05-31T20:15:00.0000000Z"},
                            }
                        ]
                    }
                )
            raise AssertionError(url)

        fake_requests = types.SimpleNamespace(get=Mock(side_effect=fake_get), HTTPError=FakeHTTPError)
        with patch.dict(sys.modules, {"requests": fake_requests}):
            jellyfin_plugin = importlib.import_module("app.backend.next_plugins.jellyfin.plugin")
            result = jellyfin_plugin.sync_personal_lists(
                {},
                {"settings": {"baseUrl": "https://jellyfin.local", "userId": "user-1"}, "secrets": {"token": "jf-token"}},
            )

        watched = result["personalLists"]["watched"]
        self.assertEqual(result["counts"]["watched"], 1)
        self.assertEqual(watched[0]["watchedAt"], "2026-05-31T20:15:00.0000000Z")
        self.assertEqual(watched[0]["plays"], 2)

    def test_trakt_health_reports_token_status_separately(self):
        def fake_get(url, headers=None, params=None, timeout=None):
            if url.endswith("/movies/tron-legacy-2010"):
                self.assertNotIn("Authorization", headers or {})
                return FakeResponse({"title": "Tron: Legacy"})
            if url.endswith("/users/settings"):
                self.assertEqual(headers.get("Authorization"), "Bearer bad-token")
                return FakeResponse({"error": "unauthorized"}, 401)
            raise AssertionError(url)

        context = {
            "secrets": {"clientId": "client-id", "accessToken": "bad-token"},
            "settings": {"username": "me"},
        }
        fake_requests = types.SimpleNamespace(get=Mock(side_effect=fake_get), HTTPError=FakeHTTPError)
        with patch.dict(sys.modules, {"requests": fake_requests}):
            health = trakt_plugin.health_check(context)

        self.assertEqual(health["status"], "available")
        self.assertEqual(health["tokenStatus"], "invalid")
        self.assertEqual(health["tokenHttpStatus"], 401)

    def test_trakt_public_user_sync_does_not_send_bearer_token(self):
        calls = []

        def fake_get(url, headers=None, params=None, timeout=None):
            calls.append((url, headers or {}))
            self.assertNotIn("Authorization", headers or {})
            return FakeResponse([])

        context = {
            "secrets": {"client_id": "client-id", "access_token": "unused-token"},
            "settings": {"username": "public-user"},
        }
        fake_requests = types.SimpleNamespace(get=Mock(side_effect=fake_get), HTTPError=FakeHTTPError)
        with patch.dict(sys.modules, {"requests": fake_requests}):
            result = trakt_plugin.sync_personal_lists({}, context)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["counts"], {"watchlist": 0, "watched": 0})
        self.assertTrue(any("/users/public-user/watchlist/movies" in url for url, _ in calls))

    def test_trakt_private_sync_maps_history_dates_to_watched_at(self):
        def fake_get(url, headers=None, params=None, timeout=None):
            self.assertEqual(headers.get("Authorization"), "Bearer access-token")
            if url.endswith("/sync/watchlist/movies"):
                return FakeResponse([])
            if url.endswith("/sync/history/movies"):
                return FakeResponse(
                    [
                        {
                            "id": 991,
                            "watched_at": "2026-05-30T20:15:00.000Z",
                            "movie": {
                                "title": "Back to the Future",
                                "year": 1985,
                                "ids": {"trakt": 3088, "slug": "back-to-the-future-1985", "imdb": "tt0088763", "tmdb": 105},
                            },
                        },
                        {
                            "id": 992,
                            "watched_at": "2026-05-31T20:15:00.000Z",
                            "movie": {
                                "title": "Back to the Future",
                                "year": 1985,
                                "ids": {"trakt": 3088, "slug": "back-to-the-future-1985", "imdb": "tt0088763", "tmdb": 105},
                            },
                        },
                    ]
                )
            raise AssertionError(url)

        context = {
            "secrets": {"clientId": "client-id", "accessToken": "access-token"},
            "settings": {"username": "me"},
        }
        fake_requests = types.SimpleNamespace(get=Mock(side_effect=fake_get), HTTPError=FakeHTTPError)
        with patch.dict(sys.modules, {"requests": fake_requests}):
            result = trakt_plugin.sync_personal_lists({}, context)

        watched = result["personalLists"]["watched"]
        self.assertEqual(result["counts"]["watched"], 2)
        self.assertEqual(watched[0]["watchedAt"], "2026-05-30T20:15:00.000Z")
        self.assertEqual(watched[1]["watchedAt"], "2026-05-31T20:15:00.000Z")
        self.assertEqual(watched[0]["metadata"]["historyId"], 991)

    def test_letterboxd_import_plugin_parses_export_csv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_file = Path(temp_dir) / "watched.csv"
            export_file.write_text(
                "Date,Name,Year,Letterboxd URI\n"
                "2026-05-31,Back to the Future,1985,https://boxd.it/2b8e\n",
                encoding="utf-8",
            )

            execution = run_plugin_entrypoint(
                "import_letterboxd",
                "import_source",
                {"sourcePath": str(export_file)},
                {},
            )

        result = execution["result"]
        self.assertEqual(execution["status"], "ok")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["items"][0]["title"], "Back to the Future")
        self.assertEqual(result["items"][0]["year"], "1985")
        self.assertEqual(result["items"][0]["sourceUrl"], "https://boxd.it/2b8e")

    def test_collection_import_plugins_parse_container_columns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_file = Path(temp_dir) / "movies.csv"
            export_file.write_text(
                "Title,Year,Barcode,Collection,Box Set,Vault\n"
                "The Fellowship of the Ring,2001,123456789012,Fantasy Shelf,The Lord of the Rings,Middle-earth 4K\n",
                encoding="utf-8",
            )

            execution = run_plugin_entrypoint(
                "import_mymovies_dk",
                "import_source",
                {"sourcePath": str(export_file)},
                {},
            )

        result = execution["result"]
        self.assertEqual(execution["status"], "ok")
        self.assertEqual(result["status"], "completed")
        item = result["items"][0]
        self.assertEqual(item["collectionTitle"], "Fantasy Shelf")
        self.assertEqual(item["boxSetTitle"], "The Lord of the Rings")
        self.assertEqual(item["vaultTitle"], "Middle-earth 4K")

    def test_collection_import_plugin_parses_box_set_export_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_file = Path(temp_dir) / "top50_4kuhd_box_sets.csv"
            export_file.write_text(
                "Barcode,Title,Year,Format,IsBoxSet,BoxSetMembers,Region,Verified\n"
                "191329144657,Back to the Future: The Ultimate Trilogy,2020,4K UHD,Yes,"
                "Back to the Future|Back to the Future Part II|Back to the Future Part III,US,Yes\n",
                encoding="utf-8",
            )

            execution = run_plugin_entrypoint(
                "import_mymovies_dk",
                "import_source",
                {"sourcePath": str(export_file)},
                {},
            )

        result = execution["result"]
        self.assertEqual(execution["status"], "ok")
        item = result["items"][0]
        proposal = item["boxSetProposal"]
        self.assertEqual(item["itemType"], "box_set")
        self.assertEqual(item["boxSetTitle"], "Back to the Future: The Ultimate Trilogy")
        self.assertEqual(item["country"], "US")
        self.assertEqual(proposal["barcode"], "191329144657")
        self.assertEqual(proposal["boxSetEvidence"]["memberConfidence"], "file_explicit")
        self.assertTrue(proposal["boxSetEvidence"]["membersAreExplicit"])
        self.assertEqual([member["format"] for member in proposal["members"]], ["4K UHD", "4K UHD", "4K UHD"])
        self.assertEqual(
            [member["title"] for member in proposal["members"]],
            ["Back to the Future", "Back to the Future Part II", "Back to the Future Part III"],
        )
        self.assertEqual(result["counts"]["boxSets"], 1)

    def test_collection_import_plugin_detects_box_set_from_member_column_without_flag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_file = Path(temp_dir) / "box_sets.csv"
            export_file.write_text(
                "Barcode,Title,Year,Format,BoxSetMembers\n"
                "883929704736,The Lord of the Rings: The Motion Picture Trilogy,2020,4K UHD,"
                "The Fellowship of the Ring|The Two Towers|The Return of the King\n",
                encoding="utf-8",
            )

            execution = run_plugin_entrypoint(
                "import_mymovies_dk",
                "import_source",
                {"sourcePath": str(export_file)},
                {},
            )

        item = execution["result"]["items"][0]
        self.assertEqual(item["itemType"], "box_set")
        self.assertEqual(item["boxSetProposal"]["memberCount"], 3)
        self.assertTrue(item["boxSetProposal"]["boxSetEvidence"]["membersAreExplicit"])

    def test_import_worker_treats_box_set_export_row_as_container_spec(self):
        item = {
            "itemType": "box_set",
            "title": "Back to the Future: The Ultimate Trilogy",
            "boxSetTitle": "Back to the Future: The Ultimate Trilogy",
            "barcode": "191329144657",
            "format": "4K UHD",
            "boxSetMembers": [
                {"title": "Back to the Future", "format": "4K UHD"},
                {"title": "Back to the Future Part II", "format": "4K UHD"},
                {"title": "Back to the Future Part III", "format": "4K UHD"},
            ],
            "boxSetProposal": {"title": "Back to the Future: The Ultimate Trilogy"},
        }

        specs = next_worker.import_item_container_specs(item)

        self.assertTrue(next_worker.import_item_is_box_set_container(item))
        self.assertEqual(specs[0]["containerType"], "box_set")
        self.assertEqual(specs[0]["barcode"], "191329144657")
        self.assertEqual(specs[0]["format"], "4K UHD")
        self.assertEqual(specs[0]["memberCount"], 3)
        self.assertEqual(specs[0]["boxSetProposal"]["title"], "Back to the Future: The Ultimate Trilogy")

    def test_import_worker_keeps_same_title_box_set_formats_separate(self):
        item = {
            "containers": [
                {"containerType": "box_set", "title": "Back to the Future Trilogy", "format": "DVD"},
                {"containerType": "box_set", "title": "Back to the Future Trilogy", "format": "4K UHD"},
                {"containerType": "box_set", "title": "Back to the Future Trilogy", "format": "DVD"},
            ],
        }

        specs = next_worker.import_item_container_specs(item)

        self.assertEqual(len(specs), 2)
        self.assertEqual([spec["format"] for spec in specs], ["DVD", "4K UHD"])

    def test_import_review_applies_box_set_member_edits_to_worker_result(self):
        result = {
            "status": "completed",
            "items": [
                {
                    "title": "Back to the Future Trilogy",
                    "barcode": "5050582369601",
                }
            ],
            "counts": {},
        }
        reviewed = apply_collection_import_review(
            result,
            {
                "decisions": [
                    {
                        "index": 1,
                        "action": "create",
                        "boxSetProposal": {
                            "title": "Back to the Future Trilogy",
                            "barcode": "5050582369601",
                            "format": "DVD",
                            "members": [
                                {"title": "Back to the Future", "format": "4K UHD"},
                                {"title": "Back to the Future Part II"},
                            ],
                        },
                    }
                ]
            },
        )

        item = reviewed["items"][0]
        self.assertEqual(item["itemType"], "box_set")
        self.assertEqual(item["boxSetMembers"][0]["format"], "DVD")
        self.assertEqual(item["boxSetProposal"]["memberCount"], 2)
        self.assertEqual(item["containers"][0]["boxSetProposal"]["movies"][1]["title"], "Back to the Future Part II")

    def test_collection_import_plugin_supports_manual_column_mapping(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_file = Path(temp_dir) / "custom.csv"
            export_file.write_text(
                "Filmnaam,Jaarcode,Groep\n"
                "Dune,2021,Sci-Fi Favorites\n",
                encoding="utf-8",
            )

            execution = run_plugin_entrypoint(
                "import_mymovies_dk",
                "import_source",
                {
                    "sourcePath": str(export_file),
                    "columnMapping": {
                        "title": "Filmnaam",
                        "year": "Jaarcode",
                        "collectionTitle": "Groep",
                    },
                },
                {},
            )

        result = execution["result"]
        self.assertEqual(execution["status"], "ok")
        self.assertEqual(result["status"], "completed")
        item = result["items"][0]
        self.assertEqual(item["title"], "Dune")
        self.assertEqual(item["year"], "2021")
        self.assertEqual(item["collectionTitle"], "Sci-Fi Favorites")

    def test_clz_import_plugin_strongly_recognizes_flat_csv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_file = Path(temp_dir) / "clz_movies.csv"
            export_file.write_text(
                "CLZ ID,Title,Year,Barcode,IMDb URL,Format,Collection Status\n"
                "42,Back to the Future,1985,5050582805967,https://www.imdb.com/title/tt0088763/,Blu-ray,Owned\n",
                encoding="utf-8",
            )

            execution = run_plugin_entrypoint(
                "import_clz_movies",
                "inspect_source",
                {"sourcePath": str(export_file)},
                {},
            )

        result = execution["result"]
        self.assertEqual(execution["status"], "ok")
        self.assertEqual(result["recognition"]["label"], "strong")
        self.assertGreaterEqual(result["recognition"]["score"], 70)
        self.assertEqual(result["sample"][0]["imdbId"], "tt0088763")

    def test_collection_import_plugin_parses_common_dutch_flat_csv_headers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_file = Path(temp_dir) / "platte-csv.csv"
            export_file.write_text(
                "Titel,Jaar,Streepjescode,IMDb URL,Formaat,Regisseur,Acteurs\n"
                "Dune,2021,8712609644750,https://www.imdb.com/title/tt1160419/,Ultra HD Blu-ray,Denis Villeneuve,Timothee Chalamet\n",
                encoding="utf-8",
            )

            execution = run_plugin_entrypoint(
                "import_mymovies_dk",
                "import_source",
                {"sourcePath": str(export_file)},
                {},
            )

        result = execution["result"]
        self.assertEqual(execution["status"], "ok")
        self.assertEqual(result["status"], "completed")
        item = result["items"][0]
        self.assertEqual(item["title"], "Dune")
        self.assertEqual(item["year"], "2021")
        self.assertEqual(item["barcode"], "8712609644750")
        self.assertEqual(item["imdbId"], "tt1160419")
        self.assertEqual(item["format"], "Ultra HD Blu-ray")
        self.assertEqual(item["director"], "Denis Villeneuve")
        self.assertIn("Timothee", item["actor"])


class UnconfiguredIntegrationPluginsTests(unittest.TestCase):
    @staticmethod
    def _plugin(plugin_id, name, *, enabled=True, requires_secrets=False,
                secrets_configured=False, settings_schema=None, settings_configured=False):
        return {
            "id": plugin_id,
            "name": name,
            "enabled": enabled,
            "requiresSecrets": requires_secrets,
            "secretsConfigured": secrets_configured,
            "settingsConfigured": settings_configured,
            "settingsSchema": settings_schema or {},
        }

    def test_plugin_is_active_requires_enabled_and_secrets(self):
        self.assertFalse(_plugin_is_active(self._plugin("tmdb", "TMDb", enabled=False)))
        self.assertFalse(
            _plugin_is_active(
                self._plugin("tmdb", "TMDb", requires_secrets=True, secrets_configured=False)
            )
        )
        self.assertTrue(
            _plugin_is_active(
                self._plugin("tmdb", "TMDb", requires_secrets=True, secrets_configured=True)
            )
        )

    def test_plugin_is_active_requires_required_settings(self):
        schema = {"settings": [{"name": "baseUrl", "required": True}]}
        self.assertFalse(
            _plugin_is_active(
                self._plugin(
                    "plex", "Plex", requires_secrets=True, secrets_configured=True,
                    settings_schema=schema, settings_configured=False,
                )
            )
        )
        self.assertTrue(
            _plugin_is_active(
                self._plugin(
                    "plex", "Plex", requires_secrets=True, secrets_configured=True,
                    settings_schema=schema, settings_configured=True,
                )
            )
        )

    def test_unconfigured_lists_disabled_and_missing_plugins_in_order(self):
        snapshot = {
            "plugins": [
                # tmdb fully configured -> excluded
                self._plugin("tmdb", "TMDb", requires_secrets=True, secrets_configured=True),
                # bluray disabled -> included
                self._plugin("bluray_com", "Blu-ray.com", enabled=False),
                # plex enabled but missing secrets -> included
                self._plugin("plex", "Plex", requires_secrets=True, secrets_configured=False),
                # jellyfin fully configured -> excluded
                self._plugin(
                    "jellyfin", "Jellyfin", requires_secrets=True, secrets_configured=True,
                ),
                # trakt not present in snapshot -> included via fallback name
            ]
        }
        with patch(
            "app.backend.next_plugin_runtime.plugin_registry_snapshot",
            return_value=snapshot,
        ):
            result = unconfigured_integration_plugins(Mock(), Mock(), Mock())
        self.assertEqual(
            result,
            [
                {"id": "bluray_com", "name": "Blu-ray.com"},
                {"id": "plex", "name": "Plex"},
                {"id": "trakt", "name": "Trakt"},
            ],
        )

    def test_unconfigured_empty_when_all_active(self):
        snapshot = {
            "plugins": [
                self._plugin("tmdb", "TMDb", requires_secrets=True, secrets_configured=True),
                self._plugin("bluray_com", "Blu-ray.com"),
                self._plugin(
                    "plex", "Plex", requires_secrets=True, secrets_configured=True,
                ),
                self._plugin(
                    "jellyfin", "Jellyfin", requires_secrets=True, secrets_configured=True,
                ),
                self._plugin(
                    "trakt", "Trakt", requires_secrets=True, secrets_configured=True,
                ),
            ]
        }
        with patch(
            "app.backend.next_plugin_runtime.plugin_registry_snapshot",
            return_value=snapshot,
        ):
            result = unconfigured_integration_plugins(Mock(), Mock(), Mock())
        self.assertEqual(result, [])


class PluginAutoUpdateTests(unittest.TestCase):
    """Auto-update / manual update / rollback of bundled default plugins."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.bundled_dir = root / "bundled"
        self.install_dir = root / "install"
        self.backup_dir = root / "backups"
        self.bundled_dir.mkdir(parents=True, exist_ok=True)
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self._env = patch.dict(
            os.environ,
            {
                "DISCVAULT_PLUGIN_INSTALL_DIR": str(self.install_dir),
                "DISCVAULT_BUNDLED_PLUGIN_DIR": str(self.bundled_dir),
                "DISCVAULT_PLUGIN_BACKUP_DIR": str(self.backup_dir),
                "DISCVAULT_PLUGIN_AUTO_UPDATE": "",
                "DISCVAULT_DATA_DIR": str(root / "data"),
                "DISCVAULT_PLUGIN_PATHS": "",
            },
        )
        self._env.start()
        # Ensure no leftover process override leaks between tests.
        set_plugin_auto_update_enabled(None)

    def tearDown(self):
        set_plugin_auto_update_enabled(None)
        self._env.stop()
        self._tmp.cleanup()

    def _write_plugin(self, base: Path, plugin_id: str, version: str, body: str = "pass\n"):
        plugin_dir = base / plugin_id
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "manifest.json").write_text(
            f'{{"id": "{plugin_id}", "name": "{plugin_id}", "version": "{version}"}}',
            encoding="utf-8",
        )
        (plugin_dir / "plugin.py").write_text(body, encoding="utf-8")
        return plugin_dir

    def _mark_initialized(self):
        (self.install_dir / PLUGIN_INITIALIZED_MARKER).write_text("1", encoding="utf-8")

    def test_parse_plugin_version_orders_versions(self):
        self.assertEqual(_parse_plugin_version("1.5.1"), (1, 5, 1))
        self.assertEqual(_parse_plugin_version("1.5.1-beta"), (1, 5, 1))
        self.assertEqual(_parse_plugin_version(""), ())
        self.assertEqual(_parse_plugin_version("garbage"), ())
        self.assertGreater(_parse_plugin_version("1.5.1"), _parse_plugin_version("1.5.0"))
        self.assertGreater(_parse_plugin_version("2.0"), _parse_plugin_version("1.9.9"))
        self.assertGreater(_parse_plugin_version("1.0"), _parse_plugin_version(""))

    def test_upgrade_replaces_outdated_installed_plugin_and_snapshots_backup(self):
        self._write_plugin(self.bundled_dir, "movievault_26", "1.5.1", "VERSION = '1.5.1'\n")
        self._write_plugin(self.install_dir, "movievault_26", "1.5.0", "VERSION = '1.5.0'\n")
        self._mark_initialized()

        result = upgrade_seeded_default_plugins()

        upgraded = {entry["plugin"]: entry for entry in result["upgraded"]}
        self.assertIn("movievault_26", upgraded)
        self.assertEqual(upgraded["movievault_26"]["from"], "1.5.0")
        self.assertEqual(upgraded["movievault_26"]["to"], "1.5.1")
        self.assertEqual(installed_plugin_version("movievault_26"), "1.5.1")
        # New plugin body is live in the install dir.
        self.assertIn(
            "1.5.1",
            (self.install_dir / "movievault_26" / "plugin.py").read_text(encoding="utf-8"),
        )
        # The previous version is preserved for rollback.
        self.assertTrue(plugin_has_backup("movievault_26"))
        self.assertEqual(plugin_backup_version("movievault_26"), "1.5.0")

    def test_upgrade_skips_when_installed_is_equal_or_newer(self):
        self._write_plugin(self.bundled_dir, "movievault_26", "1.5.0")
        self._write_plugin(self.install_dir, "movievault_26", "1.5.0")
        self._mark_initialized()

        result = upgrade_seeded_default_plugins()

        self.assertIn("movievault_26", result["skipped"])
        self.assertEqual(result["upgraded"], [])
        self.assertFalse(plugin_has_backup("movievault_26"))

    def test_upgrade_respects_auto_update_disabled(self):
        self._write_plugin(self.bundled_dir, "movievault_26", "1.5.1")
        self._write_plugin(self.install_dir, "movievault_26", "1.5.0")
        self._mark_initialized()
        set_plugin_auto_update_enabled(False)

        result = upgrade_seeded_default_plugins()

        self.assertTrue(result.get("disabled"))
        self.assertEqual(result["upgraded"], [])
        # Installed copy untouched.
        self.assertEqual(installed_plugin_version("movievault_26"), "1.5.0")

    def test_upgrade_does_not_resurrect_deleted_default(self):
        # Bundled has the plugin, but the user deleted it from the install dir.
        self._write_plugin(self.bundled_dir, "movievault_26", "1.5.1")
        self._mark_initialized()

        result = upgrade_seeded_default_plugins()

        self.assertEqual(result["upgraded"], [])
        self.assertFalse((self.install_dir / "movievault_26").exists())

    def test_upgrade_noop_when_install_dir_not_initialized(self):
        self._write_plugin(self.bundled_dir, "movievault_26", "1.5.1")
        self._write_plugin(self.install_dir, "movievault_26", "1.5.0")
        # No .initialized marker written.

        result = upgrade_seeded_default_plugins()

        self.assertEqual(result["upgraded"], [])
        self.assertEqual(installed_plugin_version("movievault_26"), "1.5.0")

    def test_install_bundled_update_writes_new_version_with_backup(self):
        self._write_plugin(self.bundled_dir, "movievault_26", "1.5.1")
        self._write_plugin(self.install_dir, "movievault_26", "1.5.0")
        self._mark_initialized()

        info = install_bundled_plugin_update("movievault_26")

        self.assertEqual(info["from"], "1.5.0")
        self.assertEqual(info["to"], "1.5.1")
        self.assertEqual(installed_plugin_version("movievault_26"), "1.5.1")
        self.assertEqual(plugin_backup_version("movievault_26"), "1.5.0")

    def test_install_bundled_update_rejects_when_not_newer(self):
        self._write_plugin(self.bundled_dir, "movievault_26", "1.5.0")
        self._write_plugin(self.install_dir, "movievault_26", "1.5.0")
        self._mark_initialized()

        with self.assertRaises(ValueError):
            install_bundled_plugin_update("movievault_26")

    def test_install_bundled_update_rejects_unknown_plugin(self):
        self._mark_initialized()
        with self.assertRaises(ValueError):
            install_bundled_plugin_update("does_not_exist")

    def test_rollback_restores_previous_version_and_removes_backup(self):
        self._write_plugin(self.bundled_dir, "movievault_26", "1.5.1")
        self._write_plugin(self.install_dir, "movievault_26", "1.5.0", "VERSION = '1.5.0'\n")
        self._mark_initialized()

        install_bundled_plugin_update("movievault_26")
        self.assertEqual(installed_plugin_version("movievault_26"), "1.5.1")

        info = rollback_plugin_update("movievault_26")

        self.assertEqual(info["from"], "1.5.1")
        self.assertEqual(info["to"], "1.5.0")
        self.assertEqual(installed_plugin_version("movievault_26"), "1.5.0")
        self.assertIn(
            "1.5.0",
            (self.install_dir / "movievault_26" / "plugin.py").read_text(encoding="utf-8"),
        )
        # Backup consumed; after rollback bundled is newer again so an update
        # can be re-offered.
        self.assertFalse(plugin_has_backup("movievault_26"))

    def test_rollback_without_backup_raises(self):
        self._write_plugin(self.install_dir, "movievault_26", "1.5.0")
        self._mark_initialized()
        with self.assertRaises(ValueError):
            rollback_plugin_update("movievault_26")

    def test_update_state_reports_update_then_rollback_availability(self):
        self._write_plugin(self.bundled_dir, "movievault_26", "1.5.1")
        self._write_plugin(self.install_dir, "movievault_26", "1.5.0")
        self._mark_initialized()

        state = plugin_update_state("movievault_26")
        self.assertTrue(state["isBundledDefault"])
        self.assertTrue(state["updateAvailable"])
        self.assertEqual(state["bundledVersion"], "1.5.1")
        self.assertFalse(state["canRollback"])

        install_bundled_plugin_update("movievault_26")
        state_after = plugin_update_state("movievault_26")
        self.assertFalse(state_after["updateAvailable"])
        self.assertTrue(state_after["canRollback"])
        self.assertEqual(state_after["rollbackVersion"], "1.5.0")

    def test_auto_update_marker_disables_then_enables(self):
        set_plugin_auto_update_enabled(None)
        self.assertTrue(plugin_auto_update_enabled())
        write_plugin_auto_update_marker(False)
        self.assertFalse(plugin_auto_update_enabled())
        write_plugin_auto_update_marker(True)
        self.assertTrue(plugin_auto_update_enabled())

    def test_auto_update_override_takes_precedence(self):
        write_plugin_auto_update_marker(False)
        set_plugin_auto_update_enabled(True)
        self.assertTrue(plugin_auto_update_enabled())
        set_plugin_auto_update_enabled(None)
        self.assertFalse(plugin_auto_update_enabled())


if __name__ == "__main__":
    unittest.main()
