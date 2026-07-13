import importlib.machinery
import os
import sys
import types
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

_requests_stub = types.ModuleType("requests")
_requests_stub.__spec__ = importlib.machinery.ModuleSpec("requests", None)
_requests_stub.get = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("requests is stubbed in tests"))
_requests_stub.post = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("requests is stubbed in tests"))
_requests_stub.request = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("requests is stubbed in tests"))
sys.modules.setdefault("requests", _requests_stub)

from app.backend.next_plugins.movievault_26 import plugin as movievault_26


class FakeResponse:
    def __init__(self, payload=None):
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class MovieVaultV3AdapterTests(unittest.TestCase):
    def test_v3_barcode_match_adapts_release_specs(self):
        result = movievault_26._v3_barcode_match(
            {
                "matched": True,
                "match": {
                    "type": "release",
                    "movie": {"title": "Alien"},
                    "release": {"title": "Alien Blu-ray"},
                    "technicalSpecs": {
                        "format": "Blu-ray",
                        "hdr": "HDR10",
                        "audioTracks": ["DTS-HD MA 5.1"],
                        "subtitles": ["English"],
                        "regions": ["B"],
                    },
                },
            }
        )

        self.assertEqual(result["type"], "release")
        self.assertEqual(result["release"]["technicalSpecs"]["format"], "Blu-ray")
        self.assertEqual(result["release"]["format"], "Blu-ray")
        self.assertEqual(result["release"]["hdr"], "HDR10")
        self.assertEqual(result["release"]["audioTracks"], ["DTS-HD MA 5.1"])

    def test_v3_barcode_match_handles_box_set_miss_and_legacy(self):
        box_set = {"title": "Alien Anthology", "members": [{"title": "Alien"}, {"title": "Aliens"}]}
        self.assertEqual(
            movievault_26._v3_barcode_match({"matched": True, "match": {"type": "box_set", "boxSet": box_set}}),
            {"type": "box_set", "boxSet": box_set},
        )
        self.assertEqual(movievault_26._v3_barcode_match({"matched": False, "match": None}), {})

        legacy = {"status": "ok", "data": {"title": "Legacy"}}
        self.assertIs(movievault_26._v3_barcode_match(legacy), legacy)

    def test_v3_search_films_prefers_catalog(self):
        result = movievault_26._v3_search_films(
            {
                "catalog": [{"movieVaultId": "mv_1", "title": "Alien", "year": 1979}],
                "editions": [{"title": "Alien 4K UHD", "year": 1979}],
            }
        )

        self.assertEqual(result, [{"movieVaultId": "mv_1", "title": "Alien", "year": 1979}])

    def test_v3_search_films_falls_back_to_deduped_editions(self):
        result = movievault_26._v3_search_films(
            {
                "catalog": [],
                "editions": [
                    {"title": "Alien", "year": 1979, "format": "4K UHD"},
                    {"title": "alien", "year": "1979-05-25", "format": "Blu-ray"},
                    {"title": "Aliens", "year": 1986, "format": "Blu-ray"},
                    "bad",
                ],
            }
        )

        self.assertEqual(
            result,
            [
                {"title": "Alien", "year": 1979, "format": "4K UHD"},
                {"title": "Aliens", "year": 1986, "format": "Blu-ray"},
            ],
        )

    def test_v3_movie_item_maps_gallery_and_director(self):
        result = movievault_26._v3_movie_item(
            {
                "matched": True,
                "match": {
                    "type": "movie",
                    "movie": {
                        "id": "mv_alien",
                        "title": "Alien",
                        "posterUrls": ["https://img.example/poster.jpg"],
                        "backdropUrls": ["https://img.example/backdrop.jpg"],
                        "crew": [
                            {"name": "Ridley Scott", "job": "Director"},
                            {"name": "Dan O'Bannon", "job": "Writer"},
                        ],
                    },
                },
            }
        )

        self.assertEqual(result["posterUrl"], "https://img.example/poster.jpg")
        self.assertEqual(result["backdropUrl"], "https://img.example/backdrop.jpg")
        self.assertEqual(result["director"], ["Ridley Scott"])

    def test_v3_person_item_maps_known_for_and_profiles(self):
        result = movievault_26._v3_person_item(
            {
                "matched": True,
                "match": {
                    "type": "person",
                    "person": {
                        "id": "pp_1",
                        "name": "Sigourney Weaver",
                        "knownForDepartment": "Acting",
                        "knownFor": [{"title": "Alien"}],
                        "profiles": ["https://img.example/existing.jpg"],
                        "profileUrls": [
                            "https://img.example/existing.jpg",
                            "https://img.example/gallery.jpg",
                            "http://img.example/insecure.jpg",
                        ],
                    },
                },
            }
        )

        self.assertEqual(result["knownFor"], "Acting")
        self.assertEqual(
            result["profiles"],
            ["https://img.example/existing.jpg", "https://img.example/gallery.jpg"],
        )

    def test_get_routes_v3_through_signed_transport(self):
        calls = []

        def signed(method, path, body_obj=None):
            calls.append((method, path, body_obj))
            return {"status": 200, "data": {"catalog": [{"title": "Alien"}]}}

        result = movievault_26._get(
            {"movievaultSignedRequestV3": signed},
            "/api/v3/search",
            q="Alien Resurrection",
            year=1997,
            type="movie",
        )

        self.assertEqual(result, {"catalog": [{"title": "Alien"}]})
        self.assertEqual(calls, [("GET", "/api/v3/search?q=Alien+Resurrection&year=1997&type=movie", None)])

    def test_get_v1_does_not_call_signed_transport(self):
        calls = []

        def signed(_method, _path, _body_obj=None):
            calls.append("called")
            raise AssertionError("v1 request should not use v3 signer")

        original_request = movievault_26._request
        try:
            movievault_26._request = lambda method, url, **kwargs: FakeResponse({"items": [{"title": "Alien"}]})
            result = movievault_26._get({"movievaultSignedRequestV3": signed}, "/api/v1/movies", q="Alien")
        finally:
            movievault_26._request = original_request

        self.assertEqual(result, {"items": [{"title": "Alien"}]})
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
