import os
import json
import sys
import types
import unittest
from pathlib import Path


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

sys.modules.setdefault(
    "requests",
    types.SimpleNamespace(
        get=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("requests is stubbed in tests"))
    ),
)

from app.backend.next_plugins.movievault import plugin as movievault_plugin
from app.backend.next_plugins.movievault_26 import plugin as movievault26_plugin


class MovieVaultPluginBoxSetTests(unittest.TestCase):
    def test_manifest_has_no_manual_url_configuration_fields(self):
        manifest_path = Path(__file__).resolve().parents[1] / "next_plugins" / "movievault" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        schema = manifest.get("settingsSchema") or {}
        fields = schema.get("settings") or []

        self.assertEqual(fields, [])

    def test_box_set_members_are_normalized_and_identified_by_metadata_bridge(self):
        lookups = []

        def metadata_lookup(payload, **_kwargs):
            lookups.append(payload)
            title = payload["title"]
            return {
                "sourceOrder": ["tmdb", "omdb", "bluray_com"],
                "sourceSummary": [{"pluginId": "tmdb", "state": "applied"}],
                "proposalStats": {"updateFields": 4},
                "proposal": {
                    "movieUpdates": {
                        "title": title,
                        "year": "1985",
                        "overview": f"{title} overview",
                    },
                    "metadataUpdates": {
                        "poster_url": f"https://image.example/{title.lower().replace(' ', '-')}.jpg",
                    },
                    "identifiers": {"tmdb": "105", "imdb": "tt0088763"},
                    "mediaUpdates": {},
                },
            }

        proposal = movievault_plugin._normalize_box_set_proposal(
            {
                "items": [
                    {
                        "boxSetTitle": "Back to the Future Trilogy",
                        "barcode": "5051892000000",
                        "movies": [
                            {"title": "Back to the Future", "year": "1985"},
                            {"title": "Back to the Future Part II"},
                        ],
                    }
                ]
            },
            {"metadataLookup": metadata_lookup},
        )

        self.assertEqual(proposal["title"], "Back to the Future Trilogy")
        self.assertEqual(proposal["member_count"], 2)
        self.assertEqual(proposal["member_source"], "MovieVault + metadata plugins")
        self.assertEqual(len(lookups), 2)
        self.assertEqual(proposal["movies"][0]["tmdbId"], "105")
        self.assertEqual(proposal["movies"][0]["imdbId"], "tt0088763")
        self.assertEqual(proposal["movies"][0]["memberConfidence"], "identified_by_metadata_plugins")
        self.assertIn("poster_url", proposal["movies"][0])
        self.assertEqual(proposal["metadata_plugin_fallbacks"][0]["sourceOrder"], ["tmdb", "omdb", "bluray_com"])

    def test_box_set_member_format_keeps_selected_parent_edition(self):
        def metadata_lookup(payload, **_kwargs):
            return {
                "sourceOrder": ["tmdb", "bluray_com"],
                "sourceSummary": [{"pluginId": "bluray_com", "state": "hit"}],
                "proposal": {
                    "movieUpdates": {
                        "title": payload["title"],
                        "format": "4K UHD",
                    },
                    "identifiers": {"tmdb": "603"},
                },
            }

        proposal = movievault_plugin._normalize_box_set_proposal(
            {
                "items": [
                    {
                        "boxSetTitle": "DVD Trilogy",
                        "format": "DVD",
                        "movies": [
                            {"title": "Movie One"},
                            {"title": "Movie Two"},
                        ],
                    }
                ]
            },
            {"metadataLookup": metadata_lookup, "format": "DVD"},
        )

        self.assertEqual(proposal["format"], "DVD")
        self.assertEqual([movie["format"] for movie in proposal["movies"]], ["DVD", "DVD"])
        self.assertEqual(proposal["movies"][0]["tmdbId"], "603")

    def test_box_set_members_without_provider_match_still_remain_importable(self):
        def metadata_lookup(_payload, **_kwargs):
            return {"sourceSummary": [], "proposal": {}}

        proposal = movievault_plugin._normalize_box_set_proposal(
            {
                "items": [
                    {
                        "boxSetTitle": "Manual Only Box",
                        "format": "Blu-ray",
                        "movies": ["First Feature", "Second Feature"],
                    }
                ]
            },
            {"metadataLookup": metadata_lookup},
        )

        self.assertEqual(proposal["member_count"], 2)
        self.assertEqual(proposal["member_confidence"], "candidate")
        self.assertEqual([movie["title"] for movie in proposal["movies"]], ["First Feature", "Second Feature"])
        self.assertEqual([movie["format"] for movie in proposal["movies"]], ["Blu-ray", "Blu-ray"])

    def test_movievault26_box_set_members_are_deduped_across_release_and_movie_rows(self):
        proposal = movievault26_plugin._normalize_box_set_proposal(
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

    def test_box_set_candidates_returns_miss_when_movievault_has_no_members(self):
        original_get = movievault_plugin._get
        try:
            movievault_plugin._get = lambda *_args, **_kwargs: {"items": [{"title": "Empty Box Set", "movies": []}]}
            result = movievault_plugin.box_set_candidates({"title": "Empty Box Set"}, {})
        finally:
            movievault_plugin._get = original_get

        self.assertEqual(result["status"], "miss")
        self.assertEqual(result["boxSetProposal"], {})


if __name__ == "__main__":
    unittest.main()
