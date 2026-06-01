import os
import sys
import types
import unittest


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


if __name__ == "__main__":
    unittest.main()
