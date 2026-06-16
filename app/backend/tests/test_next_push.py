import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend import next_app
    from app.backend import next_push
except ModuleNotFoundError as exc:
    if exc.name not in {"flask", "psycopg", "jwt"}:
        raise
    next_app = None
    next_push = None


@unittest.skipIf(next_push is None, "Flask is not installed in this test environment")
class NextPushModuleTests(unittest.TestCase):
    def test_push_helpers_are_reexported_from_next_app(self):
        names = [
            "pwa_manifest_payload",
            "get_or_create_push_vapid_keys",
            "send_push_to_user",
            "send_native_push_to_user",
            "register_next_push_routes",
        ]
        for name in names:
            self.assertIs(getattr(next_app, name), getattr(next_push, name), name)

    def test_pwa_manifest_uses_asset_prefix(self):
        manifest = next_push.pwa_manifest_payload(asset_prefix="/assets")
        self.assertEqual(manifest["icons"][0]["src"], "/assets/pwa-icon-192.png")


if __name__ == "__main__":
    unittest.main()
