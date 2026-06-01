import os
import importlib
import sqlite3
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

from app.backend.next_plugin_runtime import discover_plugins
from app.backend.next_plugin_runtime import run_plugin_entrypoint
from app.backend.next_plugins.trakt import plugin as trakt_plugin


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
    def test_legacy_import_plugin_is_discoverable(self):
        discovery = discover_plugins()
        plugins = {plugin.plugin_id: plugin for plugin in discovery["plugins"]}

        self.assertIn("discvault_legacy_import", plugins)
        plugin = plugins["discvault_legacy_import"]
        self.assertIn("import_source", plugin.manifest["categories"])
        self.assertIn("inspect_source", plugin.runtime["entrypoints"])
        self.assertIn("plan_import", plugin.runtime["entrypoints"])

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

    def test_legacy_import_plugin_inspects_sqlite_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            sqlite_db = data_dir / "discvault.db"
            conn = sqlite3.connect(sqlite_db)
            try:
                conn.execute("CREATE TABLE movies (id INTEGER PRIMARY KEY)")
                conn.execute("CREATE TABLE groups (id INTEGER PRIMARY KEY)")
                conn.executemany("INSERT INTO movies (id) VALUES (?)", [(1,), (2,)])
                conn.execute("INSERT INTO groups (id) VALUES (1)")
                conn.commit()
            finally:
                conn.close()
            posters_dir = data_dir / "posters"
            posters_dir.mkdir()
            (posters_dir / "poster.jpg").write_bytes(b"poster")

            execution = run_plugin_entrypoint(
                "discvault_legacy_import",
                "inspect_source",
                {"dataDir": str(data_dir)},
                {},
            )

        result = execution["result"]
        self.assertEqual(execution["status"], "ok")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["found"])
        self.assertTrue(result["readable"])
        self.assertEqual(result["sourceCounts"]["movies"], 2)
        self.assertEqual(result["sourceCounts"]["groups"], 1)
        self.assertEqual(result["mediaExtensions"][".jpg"], 1)
        self.assertIsNotNone(result["sourceDatabaseHash"])

    def test_legacy_import_plugin_plans_sqlite_import_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            sqlite_db = data_dir / "discvault.db"
            conn = sqlite3.connect(sqlite_db)
            try:
                conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
                conn.commit()
            finally:
                conn.close()

            execution = run_plugin_entrypoint(
                "discvault_legacy_import",
                "plan_import",
                {
                    "dataDir": str(data_dir),
                    "includeSecurity": False,
                    "includePersonal": False,
                    "importMediaReferences": True,
                    "ownerUsername": "admin",
                },
                {},
            )

        plan = execution["result"]
        self.assertEqual(execution["status"], "ok")
        self.assertTrue(plan["canStart"])
        self.assertEqual(plan["jobType"], "migration.import_sqlite")
        self.assertEqual(plan["jobPayload"]["includeSecurity"], False)
        self.assertEqual(plan["jobPayload"]["ownerUsername"], "admin")
        self.assertEqual(plan["jobPayload"]["importSource"]["pluginId"], "discvault_legacy_import")


if __name__ == "__main__":
    unittest.main()
