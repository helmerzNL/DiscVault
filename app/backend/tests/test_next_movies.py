import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_app import NextApiError
    from app.backend.next_app import movie_payload_fields
    from app.backend.next_app import movie_update_payload
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name != "flask":
        raise
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


if __name__ == "__main__":
    unittest.main()
