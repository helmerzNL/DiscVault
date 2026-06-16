import os
import sys
import unittest
from unittest import mock


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_auth import _ios_webcredential_app_ids
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name not in {"flask", "cbor2", "psycopg"}:
        raise
    _ios_webcredential_app_ids = None


@unittest.skipIf(_ios_webcredential_app_ids is None, "Flask is not installed in this test environment")
class NextAuthIosContractTests(unittest.TestCase):
    def test_aasa_apps_can_be_overridden_explicitly(self):
        with mock.patch.dict(
            os.environ,
            {
                "DISCVAULT_IOS_WEBCREDENTIAL_APPS": "ABCDE12345.HelmerNL.DiscVault,FGHIJ67890.HelmerNL.DiscVault",
                "APPLE_TEAM_ID": "IGNORED123",
            },
            clear=False,
        ):
            self.assertEqual(
                _ios_webcredential_app_ids(),
                [
                    "ABCDE12345.HelmerNL.DiscVault",
                    "FGHIJ67890.HelmerNL.DiscVault",
                ],
            )

    def test_aasa_apps_use_apple_team_id_by_default(self):
        with mock.patch.dict(
            os.environ,
            {
                "DISCVAULT_IOS_WEBCREDENTIAL_APPS": "",
                "APPLE_TEAM_ID": "ABCDE12345",
            },
            clear=False,
        ):
            self.assertEqual(_ios_webcredential_app_ids(), ["ABCDE12345.HelmerNL.DiscVault"])

    def test_aasa_apps_empty_when_team_id_is_missing(self):
        with mock.patch.dict(
            os.environ,
            {"DISCVAULT_IOS_WEBCREDENTIAL_APPS": "", "APPLE_TEAM_ID": ""},
            clear=False,
        ):
            self.assertEqual(_ios_webcredential_app_ids(), [])


if __name__ == "__main__":
    unittest.main()
