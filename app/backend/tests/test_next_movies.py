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


if __name__ == "__main__":
    unittest.main()
