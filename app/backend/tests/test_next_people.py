import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_app import person_biography_value
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name != "flask":
        raise
    person_biography_value = None


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


if __name__ == "__main__":
    unittest.main()
