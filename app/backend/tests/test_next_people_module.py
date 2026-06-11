import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend import next_app
    from app.backend import next_people
except ModuleNotFoundError as exc:  # Local minimal test environments may omit optional backend deps.
    if exc.name not in {"flask", "psycopg", "requests"}:
        raise
    next_app = None
    next_people = None


@unittest.skipIf(next_people is None, "Flask is not installed in this test environment")
class NextPeopleModuleTests(unittest.TestCase):
    def test_people_helpers_are_reexported_from_next_app(self):
        names = [
            "person_biography_value",
            "group_person_credits_by_job",
            "select_movie_metadata_person_refresh_credits",
            "person_filmography_entries_from_metadata",
            "person_local_filmography_entries",
            "merge_person_filmography_entries",
            "native_person_detail_payload",
            "native_person_filmography_payload",
            "register_next_people_routes",
        ]
        for name in names:
            self.assertIs(getattr(next_app, name), getattr(next_people, name), name)

    def test_language_specific_biography_falls_back_to_base_locale(self):
        value = next_people.person_biography_value_for_language(
            [{"lang": "nl", "biography": "Nederlandse bio"}],
            {"biography_en": "English bio"},
            "nl-NL",
        )
        self.assertEqual(value, "Nederlandse bio")

    def test_refresh_credit_selection_prioritizes_key_crew(self):
        selected = next_people.select_movie_metadata_person_refresh_credits(
            [
                {"person_id": "actor-1", "credit_type": "actor", "name": "Actor"},
                {"person_id": "crew-1", "credit_type": "crew", "job": "Director", "name": "Director"},
                {"person_id": "crew-2", "credit_type": "crew", "job": "Stunts", "name": "Stunts"},
            ],
            limit=3,
        )
        self.assertEqual([row["person_id"] for row in selected], ["crew-1", "actor-1", "crew-2"])


if __name__ == "__main__":
    unittest.main()
