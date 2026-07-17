import os
import sys
import types
import unittest
import json
import importlib.machinery
from pathlib import Path


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


def _v3_barcode_release(movie, release=None, **extra):
    match = {"type": "release", "movie": movie, "release": release or {}}
    match.update(extra)
    return {"matched": True, "match": match}


def _v3_barcode_box_set(box_set):
    return {"matched": True, "match": {"type": "box_set", "boxSet": box_set}}


def _v3_barcode_match(match):
    return {"matched": True, "match": match}


def _v3_signed_from_request(fake_request):
    """Serve a signed /api/v3 read from the same fixture a test wired for its v1 twin.

    Wave 2 catalogue reads (people search, box-sets, contribution-template) go through the
    signed transport hook instead of the raw ``_request`` layer, but their response bodies are
    byte-identical to the v1 endpoints. This adapter lets the existing ``fake_request`` fixtures
    keep serving those bodies by mapping the ``/api/v3`` path back onto its ``/api/v1`` twin.
    """

    def _hook(method, path, body_obj=None):
        assert path.startswith("/api/v3/"), f"expected a signed /api/v3 path, got {path}"
        clean = path.split("?", 1)[0].replace("/api/v3/", "/api/v1/")
        response = fake_request(method, "https://mv.example" + clean)
        return {"status": getattr(response, "status_code", 200), "data": response.json()}

    return _hook


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

        # The bundled manifest version gates in-place plugin upgrades in
        # production: upgrade_seeded_default_plugins() only replaces the
        # installed copy when the bundled version is strictly newer. Assert a
        # minimum (not an exact pin) so shipping a plugin.py change without
        # bumping this version — which silently strands the fix in the image —
        # is caught, while future bumps don't require editing this test.
        version_tuple = tuple(int(part) for part in manifest["version"].split(".")[:3])
        self.assertGreaterEqual(version_tuple, (1, 6, 0))
        self.assertIn("connection_request", manifest["capabilities"])
        self.assertIn("connection_recovery_action", manifest["capabilities"])
        self.assertIn("describe_payload", manifest["capabilities"])
        self.assertIn("activity_summary", manifest["capabilities"])
        self.assertIn("prepare_container_update", manifest["capabilities"])
        self.assertIn("prepare_barcode_update", manifest["capabilities"])
        self.assertIn("member_intelligence", manifest["capabilities"])
        self.assertIn("person_details", manifest["capabilities"])

    def test_movievault_26_runtime_exposes_receiver_observability_hooks(self):
        discovery = discover_plugins()
        plugin = next(item for item in discovery["plugins"] if item.plugin_id == "movievault_26")

        self.assertIn("describe_payload", plugin.runtime["entrypoints"])
        self.assertIn("activity_summary", plugin.runtime["entrypoints"])
        self.assertIn("connection_request", plugin.runtime["entrypoints"])
        self.assertIn("connection_recovery_action", plugin.runtime["entrypoints"])
        self.assertIn("prepare_container_update", plugin.runtime["entrypoints"])
        self.assertIn("prepare_barcode_update", plugin.runtime["entrypoints"])
        self.assertIn("member_intelligence", plugin.runtime["entrypoints"])

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

    def test_connection_request_builds_bootstrap_and_signed_recovery_contracts(self):
        bootstrap = movievault_26.connection_request(
            {
                "phase": "bootstrap",
                "body": {"instanceId": "dv_test", "requestedScopes": ["search:read"]},
                "publicKey": "public_key_test",
            },
            {},
        )
        recovery = movievault_26.connection_request(
            {
                "phase": "recovery",
                "body": {"instanceId": "dv_test"},
                "timestamp": "2026-06-09T12:00:00Z",
                "nonce": "nonce-test",
                "publicKeyId": "dvpk_test",
                "signature": "signature-test",
            },
            {},
        )

        self.assertEqual(bootstrap["path"], "/api/v1/internal/discvault/bootstrap")
        self.assertEqual(bootstrap["body"]["publicKey"], "public_key_test")
        self.assertIn("contributions:write", bootstrap["requestedScopes"])
        self.assertEqual(recovery["path"], "/api/v1/internal/discvault/handshake")
        self.assertEqual(recovery["headers"]["X-DiscVault-Key-Id"], "dvpk_test")
        self.assertEqual(recovery["headers"]["X-DiscVault-Signature"], "key-v1=signature-test")

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

    def test_movievault_26_box_set_members_are_deduped_across_release_and_movie_rows(self):
        proposal = movievault_26._normalize_box_set_proposal(
            {
                "title": "Back to the Future Trilogy 4K",
                "format": "4K UHD",
                "members": [
                    {"title": "Back to the Future", "year": "1985", "tmdbId": "105"},
                    {"title": "Back to the Future Part II", "year": "1989", "tmdbId": "165"},
                    {"title": "Back to the Future Part III", "year": "1990", "tmdbId": "196"},
                    {"title": "Back to the Future", "year": "1985"},
                    {"title": "Back to the Future Part II", "year": "1989"},
                    {"title": "Back to the Future Part III", "year": "1990"},
                ],
            },
            {"format": "4K UHD"},
        )

        self.assertEqual(proposal["member_count"], 3)
        self.assertEqual(
            [movie["title"] for movie in proposal["movies"]],
            ["Back to the Future", "Back to the Future Part II", "Back to the Future Part III"],
        )

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
                self.assertEqual(path, "/api/v3/barcodes/8712626068546")
                return _v3_barcode_release(
                    {
                        "id": "mv_movie_1",
                        "title": "Bohemian Rhapsody",
                        "year": "2018",
                        "format": "4K UHD",
                        "posterUrl": "https://img.example/bohemian.jpg",
                        "tmdbId": 424694,
                    },
                    {"format": "4K UHD", "barcode": "8712626068546"},
                )

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

    def test_barcode_hit_surfaces_persistable_movievault_identifier(self):
        # The movievault_26 catalog id must be surfaced as an identifiers[] entry
        # so the enrichment write path persists the movievault_26 link in
        # movie_identifiers (and emits the matching sync delta). Regression for
        # the movie_identifiers table staying empty for this provider.
        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **_params):
                return _v3_barcode_release(
                    {
                        "id": "mv_movie_1",
                        "title": "Bohemian Rhapsody",
                        "year": "2018",
                        "format": "4K UHD",
                        "tmdbId": 424694,
                    },
                    {"format": "4K UHD", "barcode": "8712626068546"},
                )

            movievault_26._get = fake_get
            result = movievault_26.search_barcode(
                {"barcode": "8712626068546"}, {"movievault": {"enabled": True}}
            )
        finally:
            movievault_26._get = original_get

        self.assertEqual(result["status"], "hit")
        self.assertIn("identifiers", result)
        self.assertIn(
            {
                "provider_id": "movievault_26",
                "identifier_type": "movie_id",
                "identifier": "mv_movie_1",
            },
            result["identifiers"],
        )

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

    def test_single_film_with_release_list_does_not_become_box_set_proposal(self):
        # Regression for the iOS HTTP 422 on regular films (barcode 3344428071189).
        # MovieVault returns a single movie carrying its own physical structure
        # (releases/discs/contents). Those are NOT box-set members, so the lookup
        # must surface a normal movie candidate and never a 1-member box-set.
        original_get = movievault_26._get
        shapes = {
            "releases": [{"format": "4K UHD", "barcode": "3344428071189"}],
            "releases_two_editions": [
                {"title": "The Greatest Showman", "format": "4K UHD"},
                {"title": "The Greatest Showman", "format": "Blu-ray"},
            ],
            "discs": [{"discNumber": 1, "format": "4K UHD"}],
            "contents": [{"title": "The Greatest Showman"}],
        }
        try:
            for key, value in shapes.items():
                def fake_get(_context, path, **_params):
                    self.assertEqual(path, "/api/v3/barcodes/3344428071189")
                    return _v3_barcode_release(
                        {
                            "id": "mv_tgs",
                            "title": "The Greatest Showman",
                            "year": "2017",
                            "format": "4K UHD",
                            "posterUrl": "https://img.example/tgs.jpg",
                            "tmdbId": 316029,
                            ("discItems" if key == "discs" else key): value,
                        },
                        {"format": "4K UHD", "barcode": "3344428071189"},
                    )

                movievault_26._get = fake_get
                result = movievault_26.search_barcode(
                    {"barcode": "3344428071189"}, {"movievault": {"enabled": True}}
                )
                self.assertEqual(result["status"], "hit", key)
                self.assertNotIn("boxSetProposal", result, key)
                self.assertNotIn("boxSetProposals", result, key)
                self.assertEqual(result["movie"]["title"], "The Greatest Showman", key)
                self.assertEqual(len(result["candidates"]), 1, key)
        finally:
            movievault_26._get = original_get

    def test_barcode_release_match_resolves_single_disc_with_release_format(self):
        # Regression for barcode 3344428072513 (French StudioCanal Blu-ray of
        # Fight Club). MovieVault's contract response is a *release* match: the
        # scanned Blu-ray sits at the top level while the movie carries ALL its
        # physical editions under ``releases`` plus a movie-level format copied
        # from the first edition (4K UHD). The lookup must surface exactly ONE
        # candidate — the scanned Blu-ray — and never a box-set, never the 4K UHD.
        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **_params):
                self.assertEqual(path, "/api/v3/barcodes/3344428072513")
                return _v3_barcode_match(
                    {
                        "type": "release",
                        "lookup": {
                            "barcode": "3344428072513",
                            "normalizedBarcode": "3344428072513",
                            "matchedType": "release",
                        },
                        "release": {
                            "id": "mv_rel_fc_bd_fr",
                            "movieId": "mv_movie_fc",
                            "barcode": "3344428072513",
                            "title": "Fight Club",
                            "format": "Blu-ray",
                            "country": "France",
                            "edition": "StudioCanal",
                        },
                        "movie": {
                            "id": "mv_movie_fc",
                            "movieVaultId": "mv_movie_fc",
                            "title": "Fight Club",
                            "year": "1999",
                            "tmdbId": 550,
                            "posterUrl": "https://img.example/fightclub.jpg",
                            "format": "4K UHD",
                            "release": {"barcode": "5051890315526", "format": "4K UHD"},
                            "releases": [
                                {"title": "Fight Club", "barcode": "5051890315526", "format": "4K UHD"},
                                {"title": "Fight Club - Remastered Blu-ray (Germany)", "format": "Blu-ray", "country": "Germany"},
                                {"title": "Fight Club Blu-ray (Sweden)", "format": "Blu-ray", "country": "Sweden"},
                                {"title": "Fight Club (Édition SteelBook Limitée)", "format": "Blu-ray", "country": "France"},
                                {"title": "Fight Club", "barcode": "3344428072513", "format": "Blu-ray", "country": "France", "edition": "StudioCanal"},
                                {"title": "Fight Club DVD", "format": "DVD"},
                                {"title": "Fight Club 4K UHD (UK)", "format": "4K UHD", "country": "United Kingdom"},
                            ],
                        },
                    }
                )

            movievault_26._get = fake_get
            result = movievault_26.search_barcode(
                {"barcode": "3344428072513"}, {"movievault": {"enabled": True}}
            )
        finally:
            movievault_26._get = original_get

        self.assertEqual(result["status"], "hit")
        self.assertNotIn("boxSetProposal", result)
        self.assertNotIn("boxSetProposals", result)
        self.assertEqual(len(result["candidates"]), 1)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["title"], "Fight Club")
        self.assertEqual(candidate["format"], "Blu-ray")
        self.assertEqual(candidate["barcode"], "3344428072513")
        self.assertEqual(result["movie"]["format"], "Blu-ray")

    def test_single_film_payload_with_single_member_is_dropped(self):
        proposal = movievault_26._normalize_box_set_proposal(
            {
                "data": {
                    "id": "mv_tgs",
                    "title": "The Greatest Showman",
                    "year": "2017",
                    "format": "4K UHD",
                    "contents": [{"title": "The Greatest Showman"}],
                }
            },
            {},
        )

        self.assertEqual(proposal, {})

        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **_params):
                self.assertEqual(path, "/api/v3/barcodes/5051892237710")
                return _v3_barcode_box_set(
                    {
                        "type": "box_set",
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
                )

            movievault_26._get = fake_get
            result = movievault_26.search_barcode({"barcode": "5051892237710"}, {"movievault": {"enabled": True}})
        finally:
            movievault_26._get = original_get

        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["boxSetProposal"]["title"], "Harry Potter Complete Collection")
        self.assertEqual(result["boxSetProposal"]["member_count"], 2)
        self.assertEqual(result["boxSetProposal"]["members"][0]["title"], "Harry Potter and the Philosopher's Stone")
        self.assertTrue(result["boxSetProposal"]["boxSetEvidence"]["barcodeMatch"])
        self.assertTrue(result["boxSetProposal"]["boxSetEvidence"]["membersAreExplicit"])
        self.assertEqual(result["items"], [])
        self.assertEqual(result["candidates"], [])

    def test_barcode_lookup_unwraps_movievault_data_box_set_and_fetches_members(self):
        calls = []
        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **_params):
                calls.append(path)
                if path == "/api/v3/barcodes/5051890315526":
                    return _v3_barcode_box_set(
                        {
                            "type": "box_set",
                            "entityType": "box_set",
                            "id": 42,
                            "title": "Harry Potter Complete Collection",
                            "barcode": "5051890315526",
                            "format": "4K UHD",
                            "posterUrl": "https://img.example/hp-box.jpg",
                        },
                    )
                if path == "/api/v3/box-sets/42/members":
                    return {
                        "members": [
                            {"title": "Harry Potter and the Philosopher's Stone", "year": 2001},
                            {"title": "Harry Potter and the Chamber of Secrets", "year": 2002},
                        ]
                    }
                self.fail(f"unexpected MovieVault path {path}")

            movievault_26._get = fake_get
            result = movievault_26.search_barcode({"barcode": "5051890315526"}, {"movievault": {"enabled": True}})
        finally:
            movievault_26._get = original_get

        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["boxSetProposal"]["title"], "Harry Potter Complete Collection")
        self.assertEqual(result["boxSetProposal"]["member_count"], 2)
        self.assertTrue(result["boxSetProposal"]["boxSetEvidence"]["barcodeMatch"])
        self.assertTrue(result["boxSetProposal"]["boxSetEvidence"]["membersAreExplicit"])
        self.assertEqual(result["items"], [])
        self.assertEqual(result["candidates"], [])
        self.assertEqual(calls, ["/api/v3/barcodes/5051890315526", "/api/v3/box-sets/42/members"])

    def test_barcode_lookup_can_return_movie_candidates_and_box_set_proposals(self):
        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **_params):
                self.assertEqual(path, "/api/v3/barcodes/8712626068546")
                return _v3_barcode_match(
                    {
                        "type": "movie",
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
                )

            movievault_26._get = fake_get
            result = movievault_26.search_barcode({"barcode": "8712626068546"}, {"movievault": {"enabled": True}})
        finally:
            movievault_26._get = original_get

        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["items"][0]["title"], "Bohemian Rhapsody")
        self.assertEqual(result["boxSetProposal"]["title"], "Queen Music Films")
        self.assertEqual(result["boxSetProposal"]["member_count"], 2)
        self.assertEqual(len(result["boxSetProposals"]), 1)

    def test_box_set_proposal_members_win_over_generic_items(self):
        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **_params):
                self.assertEqual(path, "/api/v3/barcodes/5050582369601")
                return _v3_barcode_match(
                    {
                        "type": "box_set",
                        "items": [
                            {"title": "Back to the Future", "year": 1985},
                            {"title": "Back to the Future Part II", "year": 1989},
                            {"title": "Back to the Future Part III", "year": 1990},
                            {"title": "Looking Back to the Future", "year": 2009},
                        ],
                        "boxSetProposal": {
                            "entityType": "box_set",
                            "id": "mv_bttf_dvd",
                            "title": "Back to the Future Trilogy DVD",
                            "barcode": "5050582369601",
                            "format": "DVD",
                            "members": [
                                {"title": "Back to the Future", "year": 1985, "format": "DVD"},
                                {"title": "Back to the Future Part II", "year": 1989, "format": "DVD"},
                                {"title": "Back to the Future Part III", "year": 1990, "format": "DVD"},
                            ],
                        },
                    }
                )

            movievault_26._get = fake_get
            result = movievault_26.search_barcode({"barcode": "5050582369601"}, {"movievault": {"enabled": True}})
        finally:
            movievault_26._get = original_get

        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["boxSetProposal"]["member_count"], 3)
        self.assertTrue(result["boxSetProposal"]["boxSetEvidence"]["barcodeMatch"])
        self.assertTrue(result["boxSetProposal"]["boxSetEvidence"]["membersAreExplicit"])
        self.assertEqual(result["boxSetProposal"]["memberConfidence"], "needs_member_confirmation")
        self.assertEqual(
            [member["title"] for member in result["boxSetProposal"]["members"]],
            ["Back to the Future", "Back to the Future Part II", "Back to the Future Part III"],
        )
        self.assertEqual(result["items"], [])
        self.assertEqual(result["candidates"], [])

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
                if path == "/api/v3/box-sets":
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
                if path == "/api/v3/box-sets/42/members":
                    return {
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
        self.assertEqual([path for path, _params in calls], ["/api/v3/box-sets", "/api/v3/box-sets/42/members"])

    def test_barcode_lookup_fetches_members_when_movievault_found_box_set_is_sparse(self):
        calls = []
        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **_params):
                calls.append(path)
                if path == "/api/v3/barcodes/5051892237710":
                    return _v3_barcode_box_set(
                        {
                            "status": "found",
                            "type": "box_set",
                            "id": 42,
                            "title": "Harry Potter Complete Collection",
                            "barcode": "5051892237710",
                        }
                    )
                if path == "/api/v3/box-sets/42/members":
                    return {
                        "members": [
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
        self.assertEqual(calls, ["/api/v3/barcodes/5051892237710", "/api/v3/box-sets/42/members"])

    def test_search_title_uses_v3_search_catalog(self):
        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **params):
                self.assertEqual(path, "/api/v3/search")
                self.assertEqual(params, {"q": "Alien", "year": "1979", "type": "movie"})
                return {
                    "catalog": [
                        {
                            "movieVaultId": "mv_alien",
                            "title": "Alien",
                            "year": 1979,
                            "posterUrl": "https://img.example/alien.jpg",
                            "tmdbId": 348,
                        }
                    ],
                    "editions": [],
                }

            movievault_26._get = fake_get
            result = movievault_26.search_title(
                {"title": "Alien", "year": "1979"},
                {"movievault": {"enabled": True}},
            )
        finally:
            movievault_26._get = original_get

        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["items"][0]["id"], "mv_alien")
        self.assertEqual(result["items"][0]["title"], "Alien")

    def test_movie_details_uses_v3_search_then_movie_detail(self):
        calls = []
        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **params):
                calls.append((path, params))
                if path == "/api/v3/search":
                    return {"catalog": [{"movieVaultId": "mv_alien", "title": "Alien", "year": 1979}], "editions": []}
                if path == "/api/v3/movies/mv_alien":
                    return {
                        "matched": True,
                        "match": {
                            "type": "movie",
                            "movie": {
                                "id": "mv_alien",
                                "title": "Alien",
                                "year": 1979,
                                "posterUrls": ["https://img.example/alien.jpg"],
                                "crew": [{"name": "Ridley Scott", "job": "Director"}],
                            },
                        },
                    }
                self.fail(f"unexpected MovieVault path {path}")

            movievault_26._get = fake_get
            result = movievault_26.movie_details(
                {"title": "Alien", "year": "1979"},
                {"movievault": {"enabled": True}},
            )
        finally:
            movievault_26._get = original_get

        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["movie"]["title"], "Alien")
        self.assertEqual(result["movie"]["director"], "Ridley Scott")
        self.assertEqual([path for path, _params in calls], ["/api/v3/search", "/api/v3/movies/mv_alien"])

    def test_movie_details_uses_known_movievault_id_and_decodes_v3_contract(self):
        calls = []
        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **params):
                calls.append((path, params))
                self.assertEqual(path, "/api/v3/movies/mv_matrix")
                return {
                    "matched": True,
                    "match": {
                        "type": "movie",
                        "lookup": {
                            "movieVaultId": "mv_matrix",
                            "matchedType": "movie",
                            "resolvedVia": "movie",
                            "moviePublicId": "mv_matrix",
                        },
                        "movie": {
                            "id": "mv_matrix",
                            "movieVaultId": "mv_matrix",
                            "title": "The Matrix",
                            "year": 1999,
                            "runtime": 136,
                            "genre": "Action, Science Fiction",
                            "posterUrls": ["https://img.example/matrix.jpg"],
                            "backdropUrls": ["https://img.example/matrix-bg.jpg"],
                            "cast": [
                                {
                                    "name": "Keanu Reeves",
                                    "tmdbId": 6384,
                                    "character": "Neo",
                                    "order": 0,
                                }
                            ],
                            "crew": [
                                {
                                    "name": "Lana Wachowski",
                                    "tmdbId": 9339,
                                    "job": "Director",
                                    "department": "Directing",
                                    "order": 0,
                                }
                            ],
                            "release": {
                                "id": "rel_matrix_4k",
                                "movieVaultId": "rel_matrix_4k",
                                "movieId": "mv_matrix",
                                "barcode": "5051888238400",
                                "title": "The Matrix 4K Ultra HD",
                                "format": "4K UHD",
                                "edition": "Steelbook",
                                "distributor": "Warner Bros.",
                                "technicalSpecs": {
                                    "hdr": "HDR10, Dolby Vision",
                                    "audioTracks": ["English Dolby Atmos"],
                                },
                            },
                            "releases": [
                                {
                                    "id": "rel_matrix_4k",
                                    "title": "The Matrix 4K Ultra HD",
                                    "format": "4K UHD",
                                    "barcode": "5051888238400",
                                }
                            ],
                            "contentRatings": [{"country": "NL", "rating": "16"}],
                        },
                    },
                    "enrichment": [],
                    "sources": [],
                    "cacheStatus": "live",
                    "degraded": False,
                }

            movievault_26._get = fake_get
            result = movievault_26.movie_details(
                {"movieVaultId": "mv_matrix", "title": "Ignored fallback"},
                {"movievault": {"enabled": True}},
            )
        finally:
            movievault_26._get = original_get

        self.assertEqual(calls, [("/api/v3/movies/mv_matrix", {})])
        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["movie"]["title"], "The Matrix")
        self.assertEqual(result["movie"]["runtimeMinutes"], 136)
        self.assertNotIn("genre", result["movie"])
        self.assertEqual(result["movie"]["posterUrl"], "https://img.example/matrix.jpg")
        self.assertEqual(result["movie"]["director"], "Lana Wachowski")
        self.assertEqual(result["release"]["id"], "rel_matrix_4k")
        self.assertEqual(result["releases"][0]["barcode"], "5051888238400")
        self.assertEqual(result["technicalSpecs"]["format"], "4K UHD")
        self.assertEqual(result["technicalSpecs"]["hdr"], "HDR10, Dolby Vision")
        self.assertEqual(result["technicalSpecs"]["audioTracks"], ["English Dolby Atmos"])
        self.assertEqual(
            result["identifiers"],
            [
                {
                    "provider_id": "movievault_26",
                    "identifier_type": "movie_id",
                    "identifier": "mv_matrix",
                }
            ],
        )

    def test_movie_details_accepts_release_id_and_returns_miss_for_unknown_id(self):
        calls = []
        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **params):
                calls.append((path, params))
                return {"query": {"type": "movie", "movieId": "rel_missing"}, "match": None, "matched": False}

            movievault_26._get = fake_get
            result = movievault_26.movie_details(
                {"releaseId": "rel_missing"},
                {"movievault": {"enabled": True}},
            )
        finally:
            movievault_26._get = original_get

        self.assertEqual(calls, [("/api/v3/movies/rel_missing", {})])
        self.assertEqual(result, {"status": "miss", "provider": "movievault_26"})

    def test_movie_details_barcode_enriches_credits_from_movie_detail(self):
        # Regression for barcode discs (e.g. Avengers: Endgame, 8717418549893):
        # /api/v3/barcodes/{barcode} matches the precise release but carries no
        # cast/crew, so movie_details must reach /api/v3/movies/{id} for credits
        # while preserving the barcode-matched release/technical fields.
        calls = []
        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **params):
                calls.append(path)
                if path == "/api/v3/barcodes/8717418549893":
                    return _v3_barcode_release(
                        {
                            "id": "mv_endgame",
                            "title": "Avengers: Endgame",
                            "year": "2019",
                            "format": "Blu-ray",
                            "tmdbId": 299534,
                        },
                        {"format": "4K UHD", "barcode": "8717418549893", "edition": "SteelBook"},
                    )
                if path == "/api/v3/movies/mv_endgame":
                    return {
                        "matched": True,
                        "match": {
                            "type": "movie",
                            "movie": {
                                "id": "mv_endgame",
                                "title": "Avengers: Endgame",
                                "cast": [
                                    {"name": "Robert Downey Jr.", "tmdbId": 3223, "character": "Tony Stark"},
                                ],
                                "crew": [
                                    {"name": "Anthony Russo", "job": "Director", "tmdbId": 19271},
                                ],
                            },
                        },
                    }
                self.fail(f"unexpected MovieVault path {path}")

            movievault_26._get = fake_get
            result = movievault_26.movie_details(
                {"barcode": "8717418549893"}, {"movievault": {"enabled": True}}
            )
        finally:
            movievault_26._get = original_get

        self.assertEqual(result["status"], "hit")
        # Barcode-matched release/technical fields are preserved.
        self.assertEqual(result["movie"]["title"], "Avengers: Endgame")
        self.assertEqual(result["candidates"][0]["format"], "4K UHD")
        self.assertEqual(result["movie"]["edition"], "SteelBook")
        # Credits are now sourced from the movie-detail endpoint.
        credits = result.get("credits") or []
        by_name = {entry["name"]: entry for entry in credits}
        self.assertEqual(by_name["Robert Downey Jr."]["tmdbId"], "3223")
        self.assertEqual(by_name["Robert Downey Jr."]["role"], "actor")
        self.assertEqual(by_name["Anthony Russo"]["tmdbId"], "19271")
        self.assertEqual(by_name["Anthony Russo"]["job"], "Director")
        self.assertIn("/api/v3/movies/mv_endgame", calls)

    def test_movie_details_barcode_without_detail_credits_is_unchanged(self):
        # No regression: when /api/v3/movies/{id} yields no credits, the barcode
        # hit is returned untouched (still a hit, credits absent).
        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **params):
                if path == "/api/v3/barcodes/8717418549893":
                    return _v3_barcode_release(
                        {
                            "id": "mv_endgame",
                            "title": "Avengers: Endgame",
                            "year": "2019",
                            "format": "Blu-ray",
                        },
                        {"format": "4K UHD", "barcode": "8717418549893"},
                    )
                if path == "/api/v3/movies/mv_endgame":
                    return {"matched": True, "match": {"type": "movie", "movie": {"id": "mv_endgame", "title": "Avengers: Endgame"}}}
                self.fail(f"unexpected MovieVault path {path}")

            movievault_26._get = fake_get
            result = movievault_26.movie_details(
                {"barcode": "8717418549893"}, {"movievault": {"enabled": True}}
            )
        finally:
            movievault_26._get = original_get

        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["candidates"][0]["format"], "4K UHD")
        self.assertFalse(result.get("credits"))

    def test_movie_details_barcode_credits_falls_back_to_id_resolution(self):
        # When the barcode match omits a resolvable movieVaultId, credit
        # enrichment falls back to the tmdbId/imdbId /api/v3/search resolution.
        calls = []
        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **params):
                calls.append(path)
                if path == "/api/v3/barcodes/8717418549893":
                    return _v3_barcode_release(
                        {"title": "Avengers: Endgame", "year": "2019", "format": "Blu-ray"},
                        {"format": "4K UHD", "barcode": "8717418549893"},
                    )
                if path == "/api/v3/search":
                    return {"catalog": [{"movieVaultId": "mv_endgame", "title": "Avengers: Endgame"}], "editions": []}
                if path == "/api/v3/movies/mv_endgame":
                    return {
                        "matched": True,
                        "match": {
                            "type": "movie",
                            "movie": {
                                "id": "mv_endgame",
                                "title": "Avengers: Endgame",
                                "crew": [{"name": "Anthony Russo", "job": "Director", "tmdbId": 19271}],
                            },
                        },
                    }
                self.fail(f"unexpected MovieVault path {path}")

            movievault_26._get = fake_get
            result = movievault_26.movie_details(
                {"barcode": "8717418549893", "tmdbId": 299534}, {"movievault": {"enabled": True}}
            )
        finally:
            movievault_26._get = original_get

        self.assertEqual(result["status"], "hit")
        credits = result.get("credits") or []
        self.assertEqual({entry["name"] for entry in credits}, {"Anthony Russo"})
        self.assertIn("/api/v3/search", calls)

    def test_person_details_by_movievault_id_uses_v3_people_detail(self):
        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **_params):
                self.assertEqual(path, "/api/v3/people/pp_sigourney")
                return {
                    "matched": True,
                    "match": {
                        "type": "person",
                        "person": {
                            "id": "pp_sigourney",
                            "name": "Sigourney Weaver",
                            "knownForDepartment": "Acting",
                            "profileUrls": ["https://img.example/sigourney.jpg"],
                        },
                    },
                }

            movievault_26._get = fake_get
            result = movievault_26.person_details(
                {"movieVaultId": "pp_sigourney"},
                {"movievault": {"enabled": True}},
            )
        finally:
            movievault_26._get = original_get

        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["movieVaultId"], "pp_sigourney")
        self.assertEqual(result["knownFor"], "Acting")
        self.assertEqual(result["profiles"], ["https://img.example/sigourney.jpg"])

    def test_person_details_search_uses_signed_v3_people(self):
        captured = {}
        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **params):
                captured["path"] = path
                captured["params"] = params
                return {
                    "results": [
                        {
                            "id": "pp_sigourney",
                            "name": "Sigourney Weaver",
                            "tmdbId": "10205",
                            "knownFor": "Acting",
                        }
                    ]
                }

            movievault_26._get = fake_get
            result = movievault_26.person_details(
                {"tmdbId": "10205"},
                {"movievault": {"enabled": True}},
            )
        finally:
            movievault_26._get = original_get

        self.assertEqual(captured["path"], "/api/v3/people")
        self.assertEqual(captured["params"].get("tmdbId"), "10205")
        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["name"], "Sigourney Weaver")

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
            require_explicit=False,
        )

        self.assertEqual(proposal["movies"][0]["discNumber"], "1")
        self.assertEqual(proposal["movies"][1]["disc_number"], "2")

    def test_box_set_proposal_exposes_camel_case_poster_url(self):
        proposal = movievault_26._normalize_box_set_proposal(
            {
                "items": [
                    {
                        "title": "Example Trilogy",
                        "posterUrl": "https://img.example/box.jpg",
                        "movies": [
                            {"title": "Example One", "posterUrl": "https://img.example/one.jpg"},
                            {"title": "Example Two", "posterUrl": "https://img.example/two.jpg"},
                        ],
                    }
                ]
            },
            {"format": "Blu-ray"},
            require_explicit=False,
        )

        self.assertEqual(proposal["posterUrl"], "https://img.example/box.jpg")
        self.assertEqual(proposal["poster_url"], "https://img.example/box.jpg")

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
                    "movievaultSignedRequestV3": _v3_signed_from_request(fake_request),
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
                    "movievaultSignedRequestV3": _v3_signed_from_request(fake_request),
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
                    "movievaultSignedRequestV3": _v3_signed_from_request(fake_request),
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
                    "movievaultSignedRequestV3": _v3_signed_from_request(fake_request),
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

    def test_receive_metadata_skips_gracefully_when_rate_limited(self):
        attempts = []

        class RateLimitedResponse(FakeResponse):
            def __init__(self):
                super().__init__(429, {"error": {"code": "rate_limited"}})
                self.headers = {"Retry-After": "0"}

        def fake_request(method, url, **kwargs):
            if method == "GET" and url.endswith("/api/v1/contribution-template"):
                return FakeResponse(200, {"version": "tpl-1", "allowedFields": ["title"]})
            if method == "POST" and url.endswith("/api/v1/contributions"):
                attempts.append(url)
                return RateLimitedResponse()
            return FakeResponse(404, {})

        original_requests = movievault_26.requests
        original_cache = dict(movievault_26._TEMPLATE_CACHE)
        original_sleep = movievault_26.time.sleep
        try:
            movievault_26._TEMPLATE_CACHE.clear()
            movievault_26.requests = types.SimpleNamespace(request=fake_request)
            movievault_26.time.sleep = lambda *_args, **_kwargs: None
            result = movievault_26.receive_metadata(
                {
                    "entityType": "movie",
                    "identity": "tt0078748",
                    "payload": {"title": "Alien"},
                },
                {
                    "secrets": {"token": "mv_live_test"},
                    "movievault": {"contributionEnabled": True, "sharingMode": "opt_in"},
                    "movievaultSignedRequestV3": _v3_signed_from_request(fake_request),
                },
            )
        finally:
            movievault_26.requests = original_requests
            movievault_26.time.sleep = original_sleep
            movievault_26._TEMPLATE_CACHE.clear()
            movievault_26._TEMPLATE_CACHE.update(original_cache)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "rate_limited")
        self.assertEqual(len(attempts), movievault_26.RATE_LIMIT_MAX_RETRIES + 1)

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

    def test_prepare_barcode_update_filters_private_identifiers(self):
        skipped = movievault_26.prepare_barcode_update({"barcode": "MANUAL-123", "entityType": "release"}, {})
        result = movievault_26.prepare_barcode_update(
            {
                "barcode": "8712626068546",
                "entityType": "release",
                "identity": "release-1",
                "sourceReference": {"type": "release", "barcode": "8712626068546", "owner_id": "user-1"},
            },
            {},
        )

        self.assertEqual(skipped["status"], "skipped")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["payload"], {"barcode": "8712626068546"})
        self.assertEqual(result["contribution"]["metadata"]["changedFields"], ["barcode"])
        self.assertNotIn("owner_id", str(result))

    def test_prepare_container_update_keeps_members_and_public_payload_in_plugin(self):
        result = movievault_26.prepare_container_update(
            {
                "identity": "box-1",
                "container": {
                    "containerType": "box_set",
                    "title": "Back to the Future Trilogy",
                    "barcode": "5050582369601",
                    "format": "DVD",
                    "owner_id": "private-owner",
                    "members": [
                        {"title": "Back to the Future", "year": "1985", "tmdbId": "105"},
                        {"title": "Back to the Future Part II", "year": "1989"},
                    ],
                },
            },
            {},
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["entityType"], "box_set")
        self.assertEqual(result["payload"]["barcode"], "5050582369601")
        self.assertEqual(result["payload"]["memberCount"], 2)
        self.assertEqual(result["memberIntelligence"]["membersIdentified"], 1)
        self.assertEqual(result["memberIntelligence"]["membersNeedingConfirmation"], 1)
        self.assertEqual(result["contribution"]["entityType"], "box_set")
        self.assertNotIn("private-owner", str(result))

    def test_member_intelligence_normalizes_and_dedupes_members(self):
        result = movievault_26.member_intelligence(
            {
                "title": "Example Box",
                "members": [
                    {"title": "Example One", "year": "2001", "tmdbId": "1"},
                    {"title": "Example One", "year": "2001", "tmdbId": "1"},
                    {"title": "Example Two", "year": "2002"},
                ],
            },
            {},
        )

        self.assertEqual(result["memberCount"], 2)
        self.assertEqual(result["membersIdentified"], 1)
        self.assertEqual(result["membersNeedingConfirmation"], 1)

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


class MovieVault26SignedContributionTests(unittest.TestCase):
    def _run_contribution(self, context, fake_request):
        context = {**context, "movievaultSignedRequestV3": _v3_signed_from_request(fake_request)}
        original_requests = movievault_26.requests
        original_cache = dict(movievault_26._TEMPLATE_CACHE)
        try:
            movievault_26._TEMPLATE_CACHE.clear()
            movievault_26.requests = types.SimpleNamespace(request=fake_request)
            return movievault_26.receive_metadata(
                {
                    "entityType": "movie",
                    "identity": "tt0078748",
                    "payload": {"title": "Alien", "overview": "A public synopsis."},
                },
                context,
            )
        finally:
            movievault_26.requests = original_requests
            movievault_26._TEMPLATE_CACHE.clear()
            movievault_26._TEMPLATE_CACHE.update(original_cache)

    def test_contribution_is_signed_and_transmits_exact_signed_bytes(self):
        signer_inputs = []
        posts = []

        def signer(raw_body):
            signer_inputs.append(raw_body)
            return {
                "keyId": "dvpk_registered",
                "timestamp": "2026-06-21T12:00:00Z",
                "nonce": f"nonce-{len(signer_inputs)}",
                "signature": "key-v1=sig-value",
            }

        def fake_request(method, url, **kwargs):
            if method == "GET" and url.endswith("/api/v1/contribution-template"):
                return FakeResponse(200, {"version": "tpl-1", "allowedFields": ["title", "overview"]})
            if method == "POST" and url.endswith("/api/v1/contributions"):
                posts.append(kwargs)
                return FakeResponse(200, {"id": "contrib_signed"})
            return FakeResponse(404, {})

        context = {
            "secrets": {"token": "mv_live_test"},
            "movievault": {"contributionEnabled": True, "sharingMode": "opt_in"},
            "movievaultSignRequest": signer,
        }
        result = self._run_contribution(context, fake_request)

        self.assertEqual(result["status"], "submitted")
        self.assertEqual(len(posts), 1)
        post = posts[0]
        # Signed requests must use data= (raw bytes), never json= (which would re-serialize).
        self.assertIsNone(post.get("json"))
        sent_bytes = post["data"]
        self.assertIsInstance(sent_bytes, bytes)
        # The bytes signed must be exactly the bytes transmitted.
        self.assertEqual(sent_bytes.decode("utf-8"), signer_inputs[0])
        headers = post["headers"]
        self.assertEqual(headers["X-DiscVault-Key-Id"], "dvpk_registered")
        self.assertEqual(headers["X-DiscVault-Timestamp"], "2026-06-21T12:00:00Z")
        self.assertEqual(headers["X-DiscVault-Nonce"], "nonce-1")
        self.assertEqual(headers["X-DiscVault-Signature"], "key-v1=sig-value")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Authorization"], "Bearer mv_live_test")

    def test_unsigned_contribution_uses_json_when_no_signer(self):
        posts = []

        def fake_request(method, url, **kwargs):
            if method == "GET" and url.endswith("/api/v1/contribution-template"):
                return FakeResponse(200, {"version": "tpl-1", "allowedFields": ["title", "overview"]})
            if method == "POST" and url.endswith("/api/v1/contributions"):
                posts.append(kwargs)
                return FakeResponse(200, {"id": "contrib_plain"})
            return FakeResponse(404, {})

        context = {
            "secrets": {"token": "mv_live_test"},
            "movievault": {"contributionEnabled": True, "sharingMode": "opt_in"},
        }
        result = self._run_contribution(context, fake_request)

        self.assertEqual(result["status"], "submitted")
        self.assertIsNone(posts[0].get("data"))
        self.assertIsNotNone(posts[0].get("json"))
        self.assertNotIn("X-DiscVault-Signature", posts[0]["headers"])

    def test_signed_contribution_resigns_with_fresh_nonce_on_token_recovery_retry(self):
        nonces = []
        posts = []

        def signer(raw_body):
            nonces.append(f"nonce-{len(nonces) + 1}")
            return {
                "keyId": "dvpk_registered",
                "timestamp": "2026-06-21T12:00:00Z",
                "nonce": nonces[-1],
                "signature": f"key-v1=sig-{len(nonces)}",
            }

        def fake_request(method, url, **kwargs):
            if method == "GET" and url.endswith("/api/v1/contribution-template"):
                return FakeResponse(200, {"version": "tpl-1", "allowedFields": ["title", "overview"]})
            if method == "POST" and url.endswith("/api/v1/contributions"):
                posts.append(kwargs)
                if len(posts) == 1:
                    return FakeResponse(401, {"error": "unauthorized"})
                return FakeResponse(200, {"id": "contrib_retry"})
            return FakeResponse(404, {})

        context = {
            "secrets": {"token": "mv_old"},
            "movievault": {"contributionEnabled": True, "sharingMode": "opt_in"},
            "movievaultRecoverToken": lambda: "mv_new",
            "movievaultSignRequest": signer,
        }
        result = self._run_contribution(context, fake_request)

        self.assertEqual(result["status"], "submitted")
        self.assertEqual(len(posts), 2)
        # Each attempt re-signs with a unique nonce so the server never sees a replay.
        first_nonce = posts[0]["headers"]["X-DiscVault-Nonce"]
        second_nonce = posts[1]["headers"]["X-DiscVault-Nonce"]
        self.assertNotEqual(first_nonce, second_nonce)
        self.assertEqual(posts[0]["headers"]["Authorization"], "Bearer mv_old")
        self.assertEqual(posts[1]["headers"]["Authorization"], "Bearer mv_new")
        # Both attempts sign-then-send identical bytes.
        self.assertEqual(posts[0]["data"], posts[1]["data"])


class MovieVault26ClientVersionGateTests(unittest.TestCase):
    def test_bootstrap_request_body_advertises_client_version(self):
        result = movievault_26.connection_request(
            {
                "phase": "bootstrap",
                "body": {
                    "instanceId": "dv_test",
                    "instanceVersion": "26.2.9",
                    "software": {"name": "DiscVault", "version": "26.2.9", "backendVersion": "26.2.9"},
                },
                "publicKey": "public_key_test",
            },
            {},
        )

        self.assertEqual(result["body"]["clientVersion"], "26.2.9")

    def test_handshake_request_body_advertises_client_version(self):
        result = movievault_26.connection_request(
            {
                "phase": "recovery",
                "body": {
                    "instanceId": "dv_test",
                    "instanceVersion": "26.2.9",
                    "software": {"name": "DiscVault", "version": "26.2.9"},
                },
                "timestamp": "2026-06-09T12:00:00Z",
                "nonce": "nonce-test",
                "publicKeyId": "dvpk_test",
                "signature": "signature-test",
            },
            {},
        )

        self.assertEqual(result["path"], "/api/v1/internal/discvault/handshake")
        self.assertEqual(result["body"]["clientVersion"], "26.2.9")

    def test_client_version_falls_back_to_context_source_version(self):
        result = movievault_26.connection_request(
            {"phase": "bootstrap", "body": {"instanceId": "dv_test"}, "publicKey": "pk"},
            {"movievault": {"sourceVersion": "26.3.0"}},
        )

        self.assertEqual(result["body"]["clientVersion"], "26.3.0")

    def test_connection_recovery_action_treats_426_as_terminal(self):
        for payload in (
            {
                "phase": "bootstrap",
                "statusCode": 426,
                "response": {
                    "error": {
                        "code": "client_version_unsupported",
                        "message": "Upgrade required",
                        "minVersion": "26.1.0",
                        "detectedVersion": "25.9.9",
                    }
                },
            },
            {
                "phase": "recovery",
                "statusCode": 400,
                "response": {"error": {"code": "client_version_unsupported", "minVersion": "26.1.0"}},
            },
        ):
            action = movievault_26.connection_recovery_action(payload, {})
            self.assertEqual(action["action"], "")
            self.assertEqual(action["reason"], "client_version_unsupported")
            self.assertTrue(action["terminal"])
            self.assertIn("26.1.0", action["message"])

    def test_request_raises_terminal_error_on_426_without_retry_loop(self):
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url))
            return FakeResponse(
                426,
                {
                    "error": {
                        "code": "client_version_unsupported",
                        "message": "Upgrade required",
                        "minVersion": "26.1.0",
                        "detectedVersion": "25.9.9",
                    }
                },
            )

        original_requests = movievault_26.requests
        recover_calls = []
        try:
            movievault_26.requests = types.SimpleNamespace(request=fake_request)
            context = {
                "secrets": {"token": "mv_old"},
                "movievaultRecoverToken": lambda: recover_calls.append(1) or "mv_new",
            }
            with self.assertRaises(movievault_26.MovieVaultClientVersionUnsupported) as ctx:
                movievault_26._get(context, "/api/v1/movies", q="Alien")
        finally:
            movievault_26.requests = original_requests

        self.assertEqual(len(calls), 1)
        self.assertEqual(recover_calls, [])
        message = str(ctx.exception)
        self.assertIn("26.1.0", message)
        self.assertIn("25.9.9", message)
        self.assertEqual(ctx.exception.min_version, "26.1.0")
        self.assertEqual(ctx.exception.detected_version, "25.9.9")

    def test_describe_payload_surfaces_cached_min_client_version(self):
        original_cache = dict(movievault_26._TEMPLATE_CACHE)
        try:
            movievault_26._TEMPLATE_CACHE.clear()
            context = {"movievault": {"sourceVersion": "26.2.9"}}
            key = movievault_26._template_cache_key(context)
            movievault_26._TEMPLATE_CACHE[key] = {
                "fetchedAt": 9_999_999_999,
                "template": {"minClientVersion": "26.1.0"},
            }
            result = movievault_26.describe_payload(
                {"entityType": "movie", "payload": {"title": "Alien"}},
                context,
            )
        finally:
            movievault_26._TEMPLATE_CACHE.clear()
            movievault_26._TEMPLATE_CACHE.update(original_cache)

        self.assertEqual(result["destination"]["minClientVersion"], "26.1.0")
        self.assertEqual(result["destination"]["clientVersion"], "26.2.9")


class ContributionFieldDiagnosticsTest(unittest.TestCase):
    def test_reports_dropped_fields_not_in_template(self):
        payload = {
            "entityType": "movie",
            "payload": {
                "title": "The Matrix",
                "overview": "A hacker learns the truth.",
                "audio_tracks": "DTS-HD MA 5.1",
                "runtime": 136,
                "empty_field": "",
            },
        }
        template = {"allowedFields": ["title", "overview"]}
        _, contribution_payload = movievault_26._contribution_payload(payload, template)
        accepted, dropped = movievault_26._contribution_field_diagnostics(
            payload, template, contribution_payload
        )
        self.assertEqual(accepted, ["overview", "title"])
        dropped_map = {item["field"]: item["reason"] for item in dropped}
        self.assertEqual(dropped_map.get("audio_tracks"), "not_in_template")
        self.assertEqual(dropped_map.get("runtime"), "not_in_template")
        self.assertNotIn("empty_field", dropped_map)

    def test_no_template_keeps_all_fields(self):
        payload = {"entityType": "movie", "payload": {"title": "The Matrix", "audio_tracks": "DTS"}}
        _, contribution_payload = movievault_26._contribution_payload(payload, {})
        accepted, dropped = movievault_26._contribution_field_diagnostics(
            payload, {}, contribution_payload
        )
        self.assertIn("title", accepted)
        self.assertEqual(dropped, [])


class ContributionReleaseTitleTest(unittest.TestCase):
    def test_release_entity_carries_physical_title_and_canonical_movie_title(self):
        payload = {
            "entityType": "release",
            "payload": {
                "title": "John Wick",
                "release_title": "John Wick (4K Ultra HD + Blu-ray) (UK Import)",
                "barcode": "5051888255889",
                "format": "4K UHD",
                "country": "United Kingdom",
                "regions": ["Region B"],
            },
        }
        _, contribution_payload = movievault_26._contribution_payload(payload, {})
        # The release entity carries the full physical/packaging title...
        self.assertEqual(
            contribution_payload["title"],
            "John Wick (4K Ultra HD + Blu-ray) (UK Import)",
        )
        # ...while the canonical film title travels as movieTitle/tmdbTitle.
        self.assertEqual(contribution_payload["movieTitle"], "John Wick")
        self.assertEqual(contribution_payload["tmdbTitle"], "John Wick")
        self.assertEqual(contribution_payload["country"], "United Kingdom")
        self.assertEqual(contribution_payload["regions"], ["Region B"])
        # The raw DiscVault-only key is not forwarded as its own field.
        self.assertNotIn("release_title", contribution_payload)

    def test_release_title_falls_back_to_clean_title(self):
        payload = {"entityType": "release", "payload": {"title": "Heat", "format": "Blu-ray"}}
        _, contribution_payload = movievault_26._contribution_payload(payload, {})
        self.assertEqual(contribution_payload["title"], "Heat")
        self.assertEqual(contribution_payload["movieTitle"], "Heat")
        self.assertEqual(contribution_payload["tmdbTitle"], "Heat")

    def test_movie_entity_keeps_only_clean_title(self):
        payload = {
            "entityType": "movie",
            "payload": {
                "title": "John Wick",
                "release_title": "John Wick (4K Ultra HD + Blu-ray) (UK Import)",
            },
        }
        _, contribution_payload = movievault_26._contribution_payload(payload, {})
        self.assertEqual(contribution_payload["title"], "John Wick")
        self.assertNotIn("movieTitle", contribution_payload)


class ContributionLocalizedFieldsTest(unittest.TestCase):
    def test_expands_localizations_into_localized_field_keys(self):
        payload = {
            "entityType": "movie",
            "payload": {
                "title": "Spirited Away",
                "localizations": [
                    {"lang": "fr", "title": "Le Voyage de Chihiro", "overview": "Une fille..."},
                    {"lang": "de-DE", "title": "Chihiros Reise"},
                ],
            },
        }
        template = {
            "allowedFields": ["title", "overview"],
            "localizedFieldPattern": "<field>_<iso-639-1-language>",
        }
        _, contribution_payload = movievault_26._contribution_payload(payload, template)
        self.assertEqual(contribution_payload["title"], "Spirited Away")
        self.assertEqual(contribution_payload["title_fr"], "Le Voyage de Chihiro")
        self.assertEqual(contribution_payload["overview_fr"], "Une fille...")
        self.assertEqual(contribution_payload["title_de"], "Chihiros Reise")
        self.assertNotIn("localizations", contribution_payload)

    def test_localized_fields_respect_allowed_base_fields(self):
        payload = {
            "entityType": "movie",
            "payload": {
                "title": "Heat",
                "localizations": [
                    {"lang": "fr", "title": "Heat FR", "edition": "Edition FR"},
                ],
            },
        }
        template = {"allowedFields": ["title"]}
        _, contribution_payload = movievault_26._contribution_payload(payload, template)
        self.assertEqual(contribution_payload["title_fr"], "Heat FR")
        self.assertNotIn("edition_fr", contribution_payload)

    def test_localizations_not_reported_as_dropped(self):
        payload = {
            "entityType": "movie",
            "payload": {
                "title": "Heat",
                "localizations": [{"lang": "fr", "title": "Heat FR"}],
            },
        }
        template = {"allowedFields": ["title"]}
        _, contribution_payload = movievault_26._contribution_payload(payload, template)
        _, dropped = movievault_26._contribution_field_diagnostics(
            payload, template, contribution_payload
        )
        dropped_fields = {item["field"] for item in dropped}
        self.assertNotIn("localizations", dropped_fields)


class ReadBackLocalizationsTest(unittest.TestCase):
    def test_ingest_localizations_normalizes_and_dedupes(self):
        rows = movievault_26._ingest_localizations(
            {
                "localizations": [
                    {"language": "fr", "title": "Le Voyage de Chihiro", "overview": "Une fille..."},
                    {"language": "de-DE", "title": "Chihiros Reise"},
                    {"language": "FR", "title": "Duplicate"},
                    {"language": "xx"},
                    {"language": "123", "title": "Bad lang"},
                ]
            }
        )
        by_lang = {row["lang"]: row for row in rows}
        self.assertEqual(by_lang["fr"]["title"], "Le Voyage de Chihiro")
        self.assertEqual(by_lang["fr"]["overview"], "Une fille...")
        self.assertEqual(by_lang["de"]["title"], "Chihiros Reise")
        self.assertEqual(by_lang["fr"]["source"], "movievault_26")
        self.assertNotIn("xx", by_lang)
        self.assertNotIn("123", by_lang)

    def test_barcode_lookup_exposes_localizations(self):
        original_get = movievault_26._get
        try:
            def fake_get(_context, path, **_params):
                return _v3_barcode_release(
                    {
                        "id": "mv_movie_1",
                        "title": "Spirited Away",
                        "year": "2001",
                        "format": "4K UHD",
                        "localizations": [
                            {"language": "fr", "title": "Le Voyage de Chihiro", "overview": "Une fille..."},
                            {"language": "ja", "title": "千と千尋の神隠し"},
                        ],
                    },
                    {"format": "4K UHD", "barcode": "8712626068546"},
                )

            movievault_26._get = fake_get
            result = movievault_26.search_barcode({"barcode": "8712626068546"}, {"movievault": {"enabled": True}})
        finally:
            movievault_26._get = original_get

        self.assertEqual(result["status"], "hit")
        localizations = result["localizations"]
        by_lang = {row["lang"]: row for row in localizations}
        self.assertEqual(by_lang["fr"]["title"], "Le Voyage de Chihiro")
        self.assertEqual(by_lang["ja"]["title"], "千と千尋の神隠し")

class MovieVaultCircuitBreakerTests(unittest.TestCase):
    def test_network_failure_short_circuits_remaining_requests(self):
        class FakeTimeout(Exception):
            pass

        calls = []

        def fake_request(method, url, **kwargs):
            calls.append(url)
            raise FakeTimeout("connection timed out")

        fake_requests = types.SimpleNamespace(
            request=fake_request,
            exceptions=types.SimpleNamespace(
                Timeout=FakeTimeout,
                ConnectTimeout=FakeTimeout,
                ReadTimeout=FakeTimeout,
                ConnectionError=FakeTimeout,
            ),
        )

        context = {"secrets": {"token": "mv_live_test"}}
        original_requests = movievault_26.requests
        try:
            movievault_26.requests = fake_requests
            with self.assertRaises(FakeTimeout):
                movievault_26._request(
                    "GET", "https://mv.example/api/v1/movies", context=context
                )
            self.assertTrue(movievault_26._movievault_unreachable(context))
            with self.assertRaises(movievault_26.MovieVaultUnavailable):
                movievault_26._request(
                    "GET", "https://mv.example/api/v1/box-sets", context=context
                )
        finally:
            movievault_26.requests = original_requests

        self.assertEqual(len(calls), 1)

    def test_request_uses_configurable_timeout(self):
        captured = {}

        def fake_request(method, url, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return FakeResponse(200, {"items": []})

        fake_requests = types.SimpleNamespace(
            request=fake_request, exceptions=types.SimpleNamespace()
        )
        context = {"secrets": {"token": "mv_live_test"}}
        original_requests = movievault_26.requests
        try:
            movievault_26.requests = fake_requests
            movievault_26._request(
                "GET", "https://mv.example/api/v1/movies", context=context
            )
        finally:
            movievault_26.requests = original_requests

        self.assertEqual(captured["timeout"], movievault_26.REQUEST_TIMEOUT_SECONDS)


class MovieVaultFormatReconciliationTests(unittest.TestCase):
    def test_blu_ray_title_overrides_contradictory_4k_format(self):
        self.assertEqual(
            movievault_26._reconcile_release_format(
                "4K UHD", "Fight Club", "Fight Club Blu-ray (SteelBook) (France)"
            ),
            "Blu-ray",
        )

    def test_genuine_4k_title_keeps_4k_format(self):
        self.assertEqual(
            movievault_26._reconcile_release_format("4K UHD", "Fight Club 4K UHD"),
            "4K UHD",
        )

    def test_no_format_token_keeps_4k_format(self):
        self.assertEqual(
            movievault_26._reconcile_release_format("4K UHD", "Fight Club"),
            "4K UHD",
        )

    def test_non_4k_format_is_untouched(self):
        self.assertEqual(
            movievault_26._reconcile_release_format("Blu-ray", "Fight Club Blu-ray"),
            "Blu-ray",
        )

    def test_movie_payload_corrects_contradictory_release_format(self):
        movie = movievault_26._movie_payload(
            {
                "title": "Fight Club",
                "format": "4K UHD",
                "releaseTitle": "Fight Club Blu-ray (SteelBook) (France)",
            }
        )
        self.assertEqual(movie["format"], "Blu-ray")

    def test_movie_payload_keeps_genuine_4k_release(self):
        movie = movievault_26._movie_payload(
            {
                "title": "Fight Club",
                "format": "4K UHD",
                "releaseTitle": "Fight Club 4K UHD Blu-ray",
            }
        )
        self.assertEqual(movie["format"], "4K UHD")


class MovieVaultStructuredCreditsTests(unittest.TestCase):
    def test_movie_credits_extracts_structured_cast_crew_with_tmdb(self):
        credits = movievault_26._movie_credits(
            {
                "title": "Heat",
                "cast": [
                    {"name": "Al Pacino", "tmdbId": 1158, "character": "Vincent Hanna"},
                    {"name": "Robert De Niro", "tmdb_id": "380", "character": "Neil McCauley"},
                ],
                "crew": [
                    {"name": "Michael Mann", "job": "Director", "tmdbId": 7715},
                ],
            }
        )
        by_name = {entry["name"]: entry for entry in credits}
        self.assertEqual(by_name["Al Pacino"]["role"], "actor")
        self.assertEqual(by_name["Al Pacino"]["tmdbId"], "1158")
        self.assertEqual(by_name["Al Pacino"]["character"], "Vincent Hanna")
        self.assertEqual(by_name["Robert De Niro"]["tmdbId"], "380")
        self.assertEqual(by_name["Michael Mann"]["role"], "crew")
        self.assertEqual(by_name["Michael Mann"]["job"], "Director")
        self.assertEqual(by_name["Michael Mann"]["tmdbId"], "7715")

    def test_movie_credits_reads_generic_people_array(self):
        credits = movievault_26._movie_credits(
            {
                "title": "Heat",
                "people": [
                    {"name": "Al Pacino", "role": "actor", "tmdbId": 1158, "character": "Hanna"},
                    {"name": "Michael Mann", "role": "crew", "job": "Director", "tmdbId": 7715},
                ],
            }
        )
        by_name = {entry["name"]: entry for entry in credits}
        self.assertEqual(by_name["Al Pacino"]["tmdbId"], "1158")
        self.assertEqual(by_name["Michael Mann"]["job"], "Director")

    def test_movie_credits_ignores_plain_name_strings(self):
        # Plain-name lists carry no structure; leave them to the backend's
        # flat-field fallback so people-dedup stays centralized.
        self.assertEqual(
            movievault_26._movie_credits(
                {"title": "Heat", "cast": ["Al Pacino", "Robert De Niro"], "director": "Michael Mann"}
            ),
            [],
        )

    def test_normalize_result_forwards_structured_credits(self):
        result = movievault_26._normalize_result(
            {
                "results": [
                    {
                        "title": "Heat",
                        "year": "1995",
                        "cast": [
                            {"name": "Al Pacino", "tmdbId": 1158, "character": "Hanna"},
                        ],
                        "crew": [
                            {"name": "Michael Mann", "job": "Director", "tmdbId": 7715},
                        ],
                    }
                ]
            },
            source_ref="title:Heat",
        )
        self.assertEqual(result["status"], "hit")
        credits = result.get("credits") or []
        by_name = {entry["name"]: entry for entry in credits}
        self.assertEqual(by_name["Al Pacino"]["tmdbId"], "1158")
        self.assertEqual(by_name["Michael Mann"]["tmdbId"], "7715")

    def test_names_text_flattens_person_dicts(self):
        self.assertEqual(
            movievault_26._names_text(
                [{"name": "Al Pacino"}, {"name": "Robert De Niro"}, "Val Kilmer"]
            ),
            "Al Pacino, Robert De Niro, Val Kilmer",
        )


if __name__ == "__main__":
    unittest.main()
