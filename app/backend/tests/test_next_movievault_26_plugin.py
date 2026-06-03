import os
import sys
import types
import unittest
import json
from pathlib import Path


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

sys.modules.setdefault(
    "requests",
    types.SimpleNamespace(
        get=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("requests is stubbed in tests")),
        post=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("requests is stubbed in tests")),
        request=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("requests is stubbed in tests")),
    ),
)

from app.backend.next_plugins.movievault_26 import plugin as movievault_26
from app.backend.next_import import legacy_metadata_plugin_plan
from app.backend.next_plugin_runtime import discover_plugins
from app.backend.next_plugin_runtime import replacement_plugin_ids


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class MovieVault26PluginContractTests(unittest.TestCase):
    def test_movievault_26_manifest_declares_plugin_replacement(self):
        manifest = {
            "id": "movievault_26",
            "replacesPlugins": ["movievault"],
        }

        self.assertEqual(replacement_plugin_ids(manifest), ["movievault"])

    def test_movievault_26_manifest_declares_receiver_observability(self):
        manifest_path = Path(__file__).resolve().parents[1] / "next_plugins" / "movievault_26" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "1.3.5")
        self.assertIn("describe_payload", manifest["capabilities"])
        self.assertIn("activity_summary", manifest["capabilities"])

    def test_movievault_26_runtime_exposes_receiver_observability_hooks(self):
        discovery = discover_plugins()
        plugin = next(item for item in discovery["plugins"] if item.plugin_id == "movievault_26")

        self.assertIn("describe_payload", plugin.runtime["entrypoints"])
        self.assertIn("activity_summary", plugin.runtime["entrypoints"])

    def test_legacy_movievault_settings_keep_logical_provider_id(self):
        plan = legacy_metadata_plugin_plan(
            {
                "movievault_enabled": True,
                "tmdb_enabled": True,
                "metadata_source_order": "upcitemdb,movievault,tmdb",
            }
        )

        self.assertTrue(plan["enabled"]["movievault"])
        self.assertIn("movievault", plan["order"])

    def test_connection_recovery_action_maps_movievault_next_validation_errors(self):
        bootstrap = movievault_26.connection_recovery_action(
            {
                "phase": "recovery",
                "statusCode": 400,
                "response": {
                    "error": {
                        "code": "validation_error",
                        "message": "DiscVault instance is not linked; bootstrap is required",
                    }
                },
            },
            {},
        )
        recover = movievault_26.connection_recovery_action(
            {
                "phase": "bootstrap",
                "statusCode": 400,
                "response": {
                    "error": {
                        "code": "validation_error",
                        "message": "DiscVault instance is already linked; use signed recovery",
                    }
                },
            },
            {},
        )

        self.assertEqual(bootstrap["action"], "bootstrap")
        self.assertEqual(recover["action"], "recover")

    def test_default_base_url_uses_movievault_next(self):
        previous = {key: os.environ.get(key) for key in ("MOVIEVAULT_SEARCH_URL", "MOVIEVAULT_BASE_URL")}
        try:
            os.environ.pop("MOVIEVAULT_SEARCH_URL", None)
            os.environ.pop("MOVIEVAULT_BASE_URL", None)
            self.assertEqual(movievault_26._base_url({}), "https://movies.vaultstack.eu")
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_barcode_lookup_sends_only_public_barcodes(self):
        original_get = movievault_26._get
        try:
            movievault_26._get = lambda *_args, **_kwargs: self.fail("pseudo barcode must not be sent")
            self.assertEqual(movievault_26.search_barcode({"barcode": "MANUAL-123"}, {})["status"], "skipped")
            self.assertEqual(movievault_26.search_barcode({"barcode": "032429316110-BOX-01"}, {})["status"], "skipped")
        finally:
            movievault_26._get = original_get

    def test_barcode_lookup_exposes_movievault_hit_as_import_candidate(self):
        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **_params):
                self.assertEqual(path, "/api/v1/barcodes/8712626068546")
                return {
                    "status": "ok",
                    "data": {
                        "id": "mv_movie_1",
                        "title": "Bohemian Rhapsody",
                        "year": "2018",
                        "format": "4K UHD",
                        "posterUrl": "https://img.example/bohemian.jpg",
                        "tmdbId": 424694,
                    },
                }

            movievault_26._get = fake_get
            result = movievault_26.search_barcode({"barcode": "8712626068546"}, {"movievault": {"enabled": True}})
        finally:
            movievault_26._get = original_get

        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["movie"]["title"], "Bohemian Rhapsody")
        self.assertEqual(result["items"][0]["title"], "Bohemian Rhapsody")
        self.assertEqual(result["items"][0]["providerId"], "movievault_26")
        self.assertEqual(result["items"][0]["posterUrl"], "https://img.example/bohemian.jpg")
        self.assertEqual(result["candidates"][0]["tmdbId"], "424694")
        self.assertNotIn("boxSetProposal", result)

    def test_regular_movie_payload_does_not_become_box_set_proposal(self):
        proposal = movievault_26._normalize_box_set_proposal(
            {
                "status": "ok",
                "data": {
                    "id": "mv_movie_1",
                    "title": "Bohemian Rhapsody",
                    "year": "2018",
                    "format": "4K UHD",
                    "posterUrl": "https://img.example/bohemian.jpg",
                    "tmdbId": 424694,
                },
            },
            {},
        )

        self.assertEqual(proposal, {})

    def test_barcode_lookup_exposes_box_set_payload_without_movie_wrapper(self):
        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **_params):
                self.assertEqual(path, "/api/v1/barcodes/5051892237710")
                return {
                    "status": "ok",
                    "data": {
                        "entityType": "box_set",
                        "id": "mv_box_1",
                        "title": "Harry Potter Complete Collection",
                        "barcode": "5051892237710",
                        "format": "4K UHD",
                        "posterUrl": "https://img.example/hp-box.jpg",
                        "members": [
                            {"title": "Harry Potter and the Philosopher's Stone", "year": 2001},
                            {"title": "Harry Potter and the Chamber of Secrets", "year": 2002},
                        ],
                    },
                }

            movievault_26._get = fake_get
            result = movievault_26.search_barcode({"barcode": "5051892237710"}, {"movievault": {"enabled": True}})
        finally:
            movievault_26._get = original_get

        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["boxSetProposal"]["title"], "Harry Potter Complete Collection")
        self.assertEqual(result["boxSetProposal"]["member_count"], 2)
        self.assertEqual(result["boxSetProposal"]["members"][0]["title"], "Harry Potter and the Philosopher's Stone")

    def test_barcode_lookup_can_return_movie_candidates_and_box_set_proposals(self):
        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **_params):
                self.assertEqual(path, "/api/v1/barcodes/8712626068546")
                return {
                    "status": "ok",
                    "movie": {
                        "id": "mv_movie_1",
                        "title": "Bohemian Rhapsody",
                        "year": "2018",
                        "format": "4K UHD",
                    },
                    "boxSetProposal": {
                        "entityType": "box_set",
                        "id": "mv_box_1",
                        "title": "Queen Music Films",
                        "members": [
                            {"title": "Bohemian Rhapsody", "year": 2018},
                            {"title": "Queen: Rock Montreal", "year": 2007},
                        ],
                    },
                }

            movievault_26._get = fake_get
            result = movievault_26.search_barcode({"barcode": "8712626068546"}, {"movievault": {"enabled": True}})
        finally:
            movievault_26._get = original_get

        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["items"][0]["title"], "Bohemian Rhapsody")
        self.assertEqual(result["boxSetProposal"]["title"], "Queen Music Films")
        self.assertEqual(result["boxSetProposal"]["member_count"], 2)
        self.assertEqual(len(result["boxSetProposals"]), 1)

    def test_box_set_candidates_omits_invalid_barcode_parameter(self):
        captured = {}
        original_get = movievault_26._get
        try:
            def fake_get(_context, _path, **params):
                captured.update(params)
                return {"items": [{"title": "Box", "movies": ["One", "Two"]}]}

            movievault_26._get = fake_get
            result = movievault_26.box_set_candidates({"title": "Box", "barcode": "IMPORT-1"}, {})
        finally:
            movievault_26._get = original_get

        self.assertEqual(result["status"], "hit")
        self.assertEqual(captured["barcode"], "")

    def test_box_set_candidates_fetches_detail_members_for_movievault_list_hits(self):
        calls = []
        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **params):
                calls.append((path, params))
                if path == "/api/v1/box-sets":
                    self.assertEqual(params["barcode"], "5051892237710")
                    return {
                        "items": [
                            {
                                "id": 42,
                                "movieVaultId": "mv_box_set_42",
                                "type": "box_set",
                                "title": "Harry Potter Complete Collection",
                                "barcode": "5051892237710",
                                "posterUrl": "https://img.example/hp-box.jpg",
                            }
                        ]
                    }
                if path == "/api/v1/box-sets/42":
                    return {
                        "id": 42,
                        "type": "box_set",
                        "title": "Harry Potter Complete Collection",
                        "members": [
                            {"title": "Harry Potter and the Philosopher's Stone", "year": 2001},
                            {"title": "Harry Potter and the Chamber of Secrets", "year": 2002},
                        ],
                    }
                self.fail(f"unexpected MovieVault path {path}")

            movievault_26._get = fake_get
            result = movievault_26.box_set_candidates(
                {"barcode": "5051892237710", "title": "Harry Potter", "format": "4K UHD"},
                {"movievault": {"enabled": True}},
            )
        finally:
            movievault_26._get = original_get

        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["boxSetProposal"]["title"], "Harry Potter Complete Collection")
        self.assertEqual(result["boxSetProposal"]["member_count"], 2)
        self.assertEqual(result["boxSetProposal"]["members"][0]["format"], "4K UHD")
        self.assertEqual([path for path, _params in calls], ["/api/v1/box-sets", "/api/v1/box-sets/42"])

    def test_barcode_lookup_fetches_members_when_movievault_found_box_set_is_sparse(self):
        calls = []
        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **_params):
                calls.append(path)
                if path == "/api/v1/barcodes/5051892237710":
                    return {
                        "status": "found",
                        "type": "box_set",
                        "id": 42,
                        "title": "Harry Potter Complete Collection",
                        "barcode": "5051892237710",
                    }
                if path == "/api/v1/box-sets/42":
                    return {
                        "status": "found",
                        "type": "box_set",
                        "id": 42,
                        "title": "Harry Potter Complete Collection",
                    }
                if path == "/api/v1/box-sets/42/members":
                    return {
                        "items": [
                            {"title": "Harry Potter and the Philosopher's Stone", "year": 2001},
                            {"title": "Harry Potter and the Chamber of Secrets", "year": 2002},
                        ]
                    }
                self.fail(f"unexpected MovieVault path {path}")

            movievault_26._get = fake_get
            result = movievault_26.search_barcode({"barcode": "5051892237710"}, {"movievault": {"enabled": True}})
        finally:
            movievault_26._get = original_get

        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["boxSetProposal"]["member_count"], 2)
        self.assertEqual(result["boxSetProposal"]["members"][1]["title"], "Harry Potter and the Chamber of Secrets")
        self.assertEqual(calls, ["/api/v1/barcodes/5051892237710", "/api/v1/box-sets/42", "/api/v1/box-sets/42/members"])

    def test_box_set_members_keep_disc_number_for_preview(self):
        proposal = movievault_26._normalize_box_set_proposal(
            {
                "items": [
                    {
                        "title": "Example Trilogy",
                        "movies": [
                            {"title": "Example One", "discNumber": 1},
                            {"title": "Example Two", "disc_number": 2},
                        ],
                    }
                ]
            },
            {"format": "Blu-ray"},
        )

        self.assertEqual(proposal["movies"][0]["discNumber"], "1")
        self.assertEqual(proposal["movies"][1]["disc_number"], "2")

    def test_unauthorized_request_recovers_token_once_and_retries(self):
        seen_auth = []

        def fake_request(_method, _url, **kwargs):
            seen_auth.append((kwargs.get("headers") or {}).get("Authorization"))
            if len(seen_auth) == 1:
                return FakeResponse(401, {"error": "unauthorized"})
            return FakeResponse(200, {"items": [{"title": "Alien"}]})

        original_requests = movievault_26.requests
        try:
            movievault_26.requests = types.SimpleNamespace(request=fake_request)
            context = {
                "secrets": {"token": "mv_old"},
                "movievaultRecoverToken": lambda: "mv_new",
            }
            result = movievault_26._get(context, "/api/v1/movies", q="Alien")
        finally:
            movievault_26.requests = original_requests

        self.assertEqual(result["items"][0]["title"], "Alien")
        self.assertEqual(seen_auth, ["Bearer mv_old", "Bearer mv_new"])

    def test_receive_metadata_filters_payload_by_template(self):
        posted = []

        def fake_request(method, url, **kwargs):
            if method == "GET" and url.endswith("/api/v1/contribution-template"):
                return FakeResponse(200, {"version": "tpl-1", "allowedFields": ["title", "overview", "posterUrl"]})
            if method == "POST" and url.endswith("/api/v1/contributions"):
                posted.append(kwargs.get("json"))
                return FakeResponse(200, {"id": "contrib_1"})
            return FakeResponse(404, {})

        original_requests = movievault_26.requests
        original_cache = dict(movievault_26._TEMPLATE_CACHE)
        try:
            movievault_26._TEMPLATE_CACHE.clear()
            movievault_26.requests = types.SimpleNamespace(request=fake_request)
            result = movievault_26.receive_metadata(
                {
                    "entityType": "movie",
                    "identity": "tt0078748",
                    "payload": {
                        "title": "Alien",
                        "overview": "A public synopsis.",
                        "owner_id": "user-1",
                        "posterUrl": "/api/next/assets/private.jpg",
                        "watchlist": True,
                    },
                },
                {
                    "secrets": {"token": "mv_live_test"},
                    "movievault": {
                        "contributionEnabled": True,
                        "sharingMode": "opt_in",
                        "sourceVersion": "26-test",
                    },
                },
            )
        finally:
            movievault_26.requests = original_requests
            movievault_26._TEMPLATE_CACHE.clear()
            movievault_26._TEMPLATE_CACHE.update(original_cache)

        self.assertEqual(result["status"], "submitted")
        self.assertEqual(posted[0]["payload"], {"title": "Alien", "overview": "A public synopsis."})
        self.assertNotIn("mv_live_test", str(posted[0]))

    def test_receive_metadata_adds_tmdb_title_hint_when_template_allows_it(self):
        posted = []

        def fake_request(method, url, **kwargs):
            if method == "GET" and url.endswith("/api/v1/contribution-template"):
                return FakeResponse(
                    200,
                    {
                        "version": "tpl-2",
                        "allowedFields": ["title", "overview", "tmdbTitle", "providerTitleHints"],
                    },
                )
            if method == "POST" and url.endswith("/api/v1/contributions"):
                posted.append(kwargs.get("json"))
                return FakeResponse(200, {"id": "contrib_2"})
            return FakeResponse(404, {})

        original_requests = movievault_26.requests
        original_cache = dict(movievault_26._TEMPLATE_CACHE)
        try:
            movievault_26._TEMPLATE_CACHE.clear()
            movievault_26.requests = types.SimpleNamespace(request=fake_request)
            result = movievault_26.receive_metadata(
                {
                    "entityType": "movie",
                    "identity": "tt6139732",
                    "payload": {
                        "title": "Local DiscVault Title",
                        "overview": "A public synopsis.",
                    },
                    "metadata": {
                        "tmdbTitle": "TMDb Canonical Title",
                        "tmdbOriginalTitle": "TMDb Original Title",
                        "providerTitleHints": [
                            {
                                "pluginId": "tmdb",
                                "sourceLabel": "TMDb",
                                "title": "TMDb Canonical Title",
                                "originalTitle": "TMDb Original Title",
                            }
                        ],
                    },
                },
                {
                    "secrets": {"token": "mv_live_test"},
                    "movievault": {"contributionEnabled": True},
                },
            )
        finally:
            movievault_26.requests = original_requests
            movievault_26._TEMPLATE_CACHE.clear()
            movievault_26._TEMPLATE_CACHE.update(original_cache)

        self.assertEqual(result["status"], "submitted")
        self.assertEqual(posted[0]["payload"]["title"], "Local DiscVault Title")
        self.assertEqual(posted[0]["payload"]["tmdbTitle"], "TMDb Canonical Title")
        self.assertEqual(
            posted[0]["payload"]["providerTitleHints"],
            [
                {
                    "provider": "tmdb",
                    "pluginId": "tmdb",
                    "sourceLabel": "TMDb",
                    "title": "TMDb Canonical Title",
                    "originalTitle": "TMDb Original Title",
                }
            ],
        )

    def test_receive_metadata_keeps_tmdb_title_hint_out_when_template_disallows_it(self):
        posted = []

        def fake_request(method, url, **kwargs):
            if method == "GET" and url.endswith("/api/v1/contribution-template"):
                return FakeResponse(200, {"version": "tpl-3", "allowedFields": ["title", "overview"]})
            if method == "POST" and url.endswith("/api/v1/contributions"):
                posted.append(kwargs.get("json"))
                return FakeResponse(200, {"id": "contrib_3"})
            return FakeResponse(404, {})

        original_requests = movievault_26.requests
        original_cache = dict(movievault_26._TEMPLATE_CACHE)
        try:
            movievault_26._TEMPLATE_CACHE.clear()
            movievault_26.requests = types.SimpleNamespace(request=fake_request)
            result = movievault_26.receive_metadata(
                {
                    "entityType": "movie",
                    "identity": "tt6139732",
                    "payload": {
                        "title": "Local DiscVault Title",
                        "overview": "A public synopsis.",
                    },
                    "metadata": {"tmdbTitle": "TMDb Canonical Title"},
                },
                {
                    "secrets": {"token": "mv_live_test"},
                    "movievault": {"contributionEnabled": True},
                },
            )
        finally:
            movievault_26.requests = original_requests
            movievault_26._TEMPLATE_CACHE.clear()
            movievault_26._TEMPLATE_CACHE.update(original_cache)

        self.assertEqual(result["status"], "submitted")
        self.assertEqual(posted[0]["payload"], {"title": "Local DiscVault Title", "overview": "A public synopsis."})

    def test_receive_metadata_preserves_box_set_member_aliases_allowed_by_template(self):
        posted = []

        def fake_request(method, url, **kwargs):
            if method == "GET" and url.endswith("/api/v1/contribution-template"):
                return FakeResponse(
                    200,
                    {
                        "version": "tpl-box",
                        "entityTypes": {
                            "box_set": {
                                "fields": ["title", "barcode", "members", "boxSetMovies", "memberCount"]
                            }
                        },
                    },
                )
            if method == "POST" and url.endswith("/api/v1/contributions"):
                posted.append(kwargs.get("json"))
                return FakeResponse(200, {"id": "contrib_box_1"})
            return FakeResponse(404, {})

        original_requests = movievault_26.requests
        original_cache = dict(movievault_26._TEMPLATE_CACHE)
        try:
            movievault_26._TEMPLATE_CACHE.clear()
            movievault_26.requests = types.SimpleNamespace(request=fake_request)
            result = movievault_26.receive_metadata(
                {
                    "entityType": "box_set",
                    "identity": "box-set-1",
                    "sourceReference": {"type": "discvault_box_set", "remoteRef": "mv_box_1"},
                    "payload": {
                        "title": "Harry Potter Complete Collection",
                        "barcode": "5051892222222",
                        "members": [
                            {"title": "Harry Potter and the Philosopher's Stone", "year": "2001", "sortOrder": 1},
                            {"title": "Harry Potter and the Chamber of Secrets", "year": "2002", "sortOrder": 2},
                        ],
                    },
                },
                {
                    "secrets": {"token": "mv_live_test"},
                    "movievault": {"contributionEnabled": True},
                },
            )
        finally:
            movievault_26.requests = original_requests
            movievault_26._TEMPLATE_CACHE.clear()
            movievault_26._TEMPLATE_CACHE.update(original_cache)

        self.assertEqual(result["status"], "submitted")
        payload = posted[0]["payload"]
        self.assertEqual(payload["memberCount"], 2)
        self.assertEqual(payload["members"], payload["boxSetMovies"])
        self.assertEqual(payload["members"][0]["title"], "Harry Potter and the Philosopher's Stone")
        self.assertNotIn("mv_live_test", str(posted[0]))

    def test_describe_payload_summarizes_box_set_contribution(self):
        result = movievault_26.describe_payload(
            {
                "entityType": "box_set",
                "identity": "legacy-box-set-3",
                "sourceReference": {
                    "type": "container",
                    "key": "container-1",
                    "publicId": "legacy-box-set-3",
                    "barcode": "5051890315526",
                    "containerType": "box_set",
                },
                "payload": {
                    "title": "Jurassic Park Trilogie",
                    "barcode": "5051890315526",
                    "owner_id": "must-not-leak",
                },
                "metadata": {
                    "changedFields": ["barcode"],
                    "sourceProviders": ["discvault"],
                },
            },
            {
                "secrets": {"token": "mv_live_secret"},
                "movievault": {
                    "contributionEnabled": True,
                    "enabled": True,
                    "linkStatus": "active",
                    "sharingMode": "opt_in",
                    "tokenSet": True,
                },
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["entityType"], "box_set")
        self.assertEqual(result["identity"], "legacy-box-set-3")
        self.assertEqual(result["sourceReference"]["barcode"], "5051890315526")
        self.assertEqual(result["fields"], ["barcode", "title"])
        self.assertEqual(result["changedFields"], ["barcode"])
        self.assertNotIn("owner_id", result["fields"])
        self.assertNotIn("mv_live_secret", str(result))

    def test_activity_summary_summarizes_submission_without_secret_values(self):
        result = movievault_26.activity_summary(
            {
                "payload": {
                    "entityType": "movie",
                    "identity": "tt6139732",
                    "payload": {"title": "Aladdin", "overview": "A public synopsis."},
                },
                "execution": {
                    "status": "submitted",
                    "entityType": "movie",
                    "idempotencyPrefix": "movie:tt6139732:tpl-1",
                    "templateVersion": "tpl-1",
                    "response": {"id": "contrib_123", "token": "must-not-leak"},
                },
            },
            {"secrets": {"token": "mv_live_secret"}},
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["state"], "submitted")
        self.assertEqual(result["remoteId"], "contrib_123")
        self.assertEqual(result["fields"], ["overview", "title"])
        self.assertNotIn("mv_live_secret", str(result))
        self.assertNotIn("must-not-leak", str(result))


if __name__ == "__main__":
    unittest.main()
