import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_app import group_person_credits_by_job
    from app.backend.next_app import person_biography_value
    from app.backend.next_app import person_filmography_entries_from_metadata
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name != "flask":
        raise
    group_person_credits_by_job = None
    person_biography_value = None
    person_filmography_entries_from_metadata = None


@unittest.skipIf(person_biography_value is None, "Flask is not installed in this test environment")
class NextPeoplePolicyTests(unittest.TestCase):
    def test_person_biography_prefers_dutch_then_english(self):
        biography = person_biography_value(
            [
                {"lang": "en", "biography": "English biography"},
                {"lang": "nl", "biography": "Nederlandse biografie"},
            ],
            {},
        )

        self.assertEqual(biography, "Nederlandse biografie")

    def test_person_biography_uses_metadata_fallback(self):
        biography = person_biography_value([], {"biography": "Metadata biography"})

        self.assertEqual(biography, "Metadata biography")

    def test_person_crew_credits_group_by_job(self):
        groups = group_person_credits_by_job(
            [
                {"job": "Director", "title": "Movie A"},
                {"job": "Producer", "title": "Movie B"},
                {"job": "Director", "title": "Movie C"},
            ]
        )

        by_job = {group["job"]: group for group in groups}
        self.assertEqual(by_job["Director"]["count"], 2)
        self.assertEqual(by_job["Producer"]["count"], 1)

    def test_person_filmography_metadata_normalizes_tmdb_entries(self):
        entries = person_filmography_entries_from_metadata(
            {
                "combined_credits": {
                    "cast": [
                        {
                            "id": 11,
                            "media_type": "movie",
                            "title": "Example",
                            "release_date": "2020-02-03",
                            "character": "Lead",
                            "poster_path": "/poster.jpg",
                        }
                    ],
                    "crew": [
                        {
                            "id": 12,
                            "media_type": "movie",
                            "title": "Directed",
                            "job": "Director",
                        }
                    ],
                }
            }
        )

        self.assertEqual(entries[0]["tmdb_id"], "11")
        self.assertEqual(entries[0]["year"], "2020")
        self.assertEqual(entries[0]["credit_type"], "actor")
        self.assertEqual(entries[0]["poster_url"], "https://image.tmdb.org/t/p/w342/poster.jpg")
        self.assertEqual(entries[1]["job"], "Director")


if __name__ == "__main__":
    unittest.main()
