import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_app import import_source_review_summary
    from app.backend.next_app import import_source_recommended_match
    from app.backend.next_app import import_source_match_candidate_score
    from app.backend.next_app import import_source_match_title_key
    from app.backend.next_app import NextApiError
    from app.backend.next_app import movie_payload_fields
    from app.backend.next_app import movie_update_payload
    from app.backend.next_app import merge_selected_import_movie_candidate
    from app.backend.next_app import box_set_proposal_audit_summary
    from app.backend.next_app import box_set_proposal_member_list
    from app.backend.next_app import box_set_proposal_sort_key
    from app.backend.next_app import selected_import_movie_candidate_from_body
    from app.backend.next_app import selected_import_movie_candidate_proposal
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name != "flask":
        raise
    import_source_review_summary = None
    import_source_recommended_match = None
    import_source_match_candidate_score = None
    import_source_match_title_key = None
    NextApiError = None
    movie_payload_fields = None
    movie_update_payload = None
    merge_selected_import_movie_candidate = None
    box_set_proposal_audit_summary = None
    box_set_proposal_member_list = None
    box_set_proposal_sort_key = None
    selected_import_movie_candidate_from_body = None
    selected_import_movie_candidate_proposal = None


@unittest.skipIf(movie_update_payload is None, "Flask is not installed in this test environment")
class NextMovieEditPolicyTests(unittest.TestCase):
    def test_movie_update_payload_allows_optional_fields_to_be_cleared(self):
        payload = movie_update_payload(
            {
                "title": "Updated",
                "originalTitle": "",
                "barcode": "",
                "releaseDate": "",
                "overview": "",
            },
            existing={
                "title": "Existing",
                "original_title": "Original",
                "barcode": "123",
                "release_date": "2026-05-31",
                "overview": "Old overview",
            },
        )

        self.assertEqual(payload["title"], "Updated")
        self.assertIsNone(payload["original_title"])
        self.assertIsNone(payload["barcode"])
        self.assertIsNone(payload["release_date"])
        self.assertIsNone(payload["overview"])

    def test_movie_update_payload_rejects_invalid_release_date(self):
        with self.assertRaises(NextApiError):
            movie_update_payload(
                {"title": "Updated", "releaseDate": "31-05-2026"},
                existing={"title": "Existing"},
            )

    def test_movie_payload_fields_drops_year_only_release_date(self):
        payload = movie_payload_fields(
            {
                "title": "RoboCop",
                "year": "1987",
                "releaseDate": "1987",
                "purchaseDate": "2026-06-02",
            }
        )

        self.assertIsNone(payload["release_date"])
        self.assertEqual(payload["purchase_date"].isoformat(), "2026-06-02")

    def test_import_source_review_summary_flags_review_risks(self):
        summary = import_source_review_summary(
            [
                {
                    "action": "create",
                    "title": "A Minecraft Movie 4K Blu-ray (SteelBook) (France)",
                    "confidence": {"label": "low", "evidence": ["title"]},
                    "metadataSuggestions": {"items": [{"title": "A Minecraft Movie"}]},
                },
                {
                    "action": "update",
                    "title": "RoboCop",
                    "matchState": "existing",
                    "confidence": {"label": "high", "evidence": ["exact_identity"]},
                },
            ],
            actions=[],
            containers=[{"containerType": "box_set"}, {"containerType": "collection"}],
            provider_summary=[{"provider": "import_clz_movies"}],
        )

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["actions"]["create"], 1)
        self.assertEqual(summary["actions"]["update"], 1)
        self.assertEqual(summary["confidence"]["low"], 1)
        self.assertEqual(summary["releaseTitleRisks"], 1)
        self.assertEqual(summary["metadataSuggestionRows"], 1)
        self.assertEqual(summary["recommendedAction"], "review")

    def test_import_source_recommended_match_prefers_movie_identity(self):
        match = import_source_recommended_match(
            {
                "title": "A Minecraft Movie 4K Blu-ray (SteelBook) (France)",
                "year": "2025",
            },
            {
                "items": [
                    {"provider": "tmdb", "title": "Minecraft: The Story of Mojang", "year": "2012"},
                    {"provider": "tmdb", "title": "A Minecraft Movie", "year": "2025", "identifiers": {"tmdb": "950387"}},
                ]
            },
        )

        self.assertIsNotNone(match)
        self.assertEqual(match["title"], "A Minecraft Movie")
        self.assertGreaterEqual(match["resolution"]["score"], 72)
        self.assertIn("same_year", match["resolution"]["evidence"])

    def test_import_source_recommended_match_ignores_weak_candidate(self):
        match = import_source_recommended_match(
            {"title": "RoboCop", "year": "1987"},
            {"items": [{"provider": "tmdb", "title": "Saving Private Ryan", "year": "1998"}]},
        )

        self.assertIsNone(match)
        score = import_source_match_candidate_score(
            {"title": "RoboCop", "year": "1987"},
            {"title": "Saving Private Ryan", "year": "1998"},
        )
        self.assertEqual(score["label"], "low")

    def test_import_source_match_title_key_tolerates_empty_values(self):
        self.assertEqual(import_source_match_title_key(None), "")
        self.assertEqual(import_source_match_title_key("Harry Potter 4K Blu-ray SteelBook"), "harry potter")

    def test_selected_import_movie_candidate_from_body_normalizes_plugin_fields(self):
        candidate = selected_import_movie_candidate_from_body(
            {
                "selectedMovieCandidate": {
                    "provider": "movievault_26",
                    "sourceLabel": "MovieVault",
                    "sourceRef": "mv:release:123",
                    "title": "Bohemian Rhapsody",
                    "year": "2018",
                    "format": "4K UHD",
                    "posterUrl": "https://image.example/poster.jpg",
                    "identifiers": {"tmdb": "424694"},
                }
            }
        )

        self.assertEqual(candidate["provider"], "movievault_26")
        self.assertEqual(candidate["title"], "Bohemian Rhapsody")
        self.assertEqual(candidate["format"], "4K UHD")
        self.assertEqual(candidate["poster_url"], "https://image.example/poster.jpg")
        self.assertEqual(candidate["identifiers"]["tmdb"], "424694")

    def test_selected_import_movie_candidate_proposal_marks_media_and_identifiers(self):
        proposal = selected_import_movie_candidate_proposal(
            {
                "provider": "tmdb",
                "sourceLabel": "TMDb",
                "sourceRef": "tmdb:123",
                "title": "RoboCop",
                "year": "1987",
                "poster_url": "https://image.example/robocop.jpg",
                "identifiers": {"tmdb": "5548", "imdb": "tt0093870"},
            }
        )

        self.assertEqual(proposal["movieUpdates"]["title"], "RoboCop")
        self.assertEqual(proposal["movieUpdates"]["year"], "1987")
        self.assertEqual(proposal["metadataUpdates"]["poster_url"], "https://image.example/robocop.jpg")
        self.assertEqual(proposal["mediaUpdates"]["poster"]["providerId"], "tmdb")
        self.assertEqual(proposal["identifiers"]["imdb"], "tt0093870")
        self.assertGreaterEqual(len(proposal["provenance"]), 3)

    def test_merge_selected_import_movie_candidate_overrides_selected_identity_only(self):
        merged = merge_selected_import_movie_candidate(
            {
                "movieUpdates": {"title": "Release title", "runtime_minutes": 110},
                "technicalUpdates": {"hdr": "HDR10"},
                "identifiers": {"imdb": "tt-existing"},
            },
            {
                "provider": "tmdb",
                "title": "A Minecraft Movie",
                "year": "2025",
                "identifiers": {"tmdb": "950387"},
            },
        )

        self.assertEqual(merged["movieUpdates"]["title"], "A Minecraft Movie")
        self.assertEqual(merged["movieUpdates"]["runtime_minutes"], 110)
        self.assertEqual(merged["technicalUpdates"]["hdr"], "HDR10")
        self.assertEqual(merged["identifiers"]["imdb"], "tt-existing")
        self.assertEqual(merged["identifiers"]["tmdb"], "950387")

    def test_box_set_proposals_prefer_movievault_members_over_candidate_hints(self):
        movievault = {
            "provider": "movievault_26",
            "title": "Back to the Future Trilogy",
            "members": [
                {"title": "Back to the Future", "year": "1985"},
                {"title": "Back to the Future Part II", "year": "1989"},
                {"title": "Back to the Future Part III", "year": "1990"},
            ],
            "items": [
                {"title": "Back to the Future"},
                {"title": "Back to the Future Part II"},
                {"title": "Back to the Future Part III"},
                {"title": "Back to the Future Bonus Disc"},
                {"title": "Back to the Future Documentary"},
            ],
            "memberConfidence": "identified",
        }
        bluray_candidate = {
            "provider": "bluray_com",
            "title": "Back to the Future Trilogy",
            "members": movievault["items"],
            "detectedWithoutMembers": True,
            "memberConfidence": "candidate",
            "memberSource": "metadata_candidates",
        }
        proposals = sorted([bluray_candidate, movievault], key=box_set_proposal_sort_key)

        self.assertIs(proposals[0], movievault)
        self.assertEqual(len(box_set_proposal_member_list(proposals[0])), 3)

    def test_box_set_proposal_audit_summary_shows_provider_and_members(self):
        summary = box_set_proposal_audit_summary(
            {
                "provider": "movievault_26",
                "title": "Back to the Future Trilogy DVD",
                "barcode": "5050582369601",
                "format": "DVD",
                "memberConfidence": "identified",
                "members": [
                    {"title": "Back to the Future", "year": "1985", "format": "DVD"},
                    {"title": "Back to the Future Part II", "year": "1989", "format": "DVD"},
                    {"title": "Back to the Future Part III", "year": "1990", "format": "DVD"},
                ],
            }
        )

        self.assertEqual(summary["provider"], "movievault_26")
        self.assertEqual(summary["memberCount"], 3)
        self.assertFalse(summary["candidateOnly"])
        self.assertEqual(summary["members"][0]["title"], "Back to the Future")


if __name__ == "__main__":
    unittest.main()
