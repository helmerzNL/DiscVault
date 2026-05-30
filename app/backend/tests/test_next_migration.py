import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_import import legacy_group_member_role
from app.backend.next_import import legacy_role_key
try:
    from app.backend.next_auth import next_generate_recovery_codes
    from app.backend.next_auth import next_normalize_recovery_code
    from app.backend.next_auth import next_recovery_code_hash
except ModuleNotFoundError as exc:
    if exc.name != "cbor2":
        raise
    next_generate_recovery_codes = None
    next_normalize_recovery_code = None
    next_recovery_code_hash = None


class NextMigrationContractTests(unittest.TestCase):
    def test_legacy_membergroups_maps_to_basic_viewer_role(self):
        self.assertEqual(legacy_role_key("MemberGroups"), "media_viewer")
        self.assertEqual(legacy_role_key("admin"), "admin")
        self.assertEqual(legacy_role_key("anything-custom"), "media_viewer")
        self.assertEqual(legacy_role_key("MemberGroups", is_owner=True), "owner")

    def test_legacy_group_member_role_is_conservative(self):
        self.assertEqual(legacy_group_member_role("manager"), "manager")
        self.assertEqual(legacy_group_member_role("unknown"), "member")

    @unittest.skipIf(next_generate_recovery_codes is None, "Passkey dependencies are not installed")
    def test_recovery_codes_are_normalized_and_one_time_hashable(self):
        codes = next_generate_recovery_codes(4)

        self.assertEqual(len(codes), 4)
        self.assertEqual(len(set(codes)), 4)
        for code in codes:
            self.assertRegex(code, r"^[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$")

        self.assertEqual(next_normalize_recovery_code("ab12-cd34 ef56"), "AB12CD34EF56")
        self.assertEqual(
            next_recovery_code_hash("ab12-cd34"),
            next_recovery_code_hash("AB12CD34"),
        )


if __name__ == "__main__":
    unittest.main()
