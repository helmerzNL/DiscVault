import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend import next_app
    from app.backend import next_notifications
except ModuleNotFoundError as exc:
    if exc.name not in {"flask", "psycopg"}:
        raise
    next_app = None
    next_notifications = None


@unittest.skipIf(next_notifications is None, "Flask is not installed in this test environment")
class NextNotificationsModuleTests(unittest.TestCase):
    def test_notification_helpers_are_reexported_from_next_app(self):
        names = [
            "NOTIFICATION_PREF_DEFAULTS",
            "notification_counts",
            "notification_preference_map",
            "create_user_notification",
            "register_next_notifications_routes",
        ]
        for name in names:
            self.assertIs(getattr(next_app, name), getattr(next_notifications, name), name)

    def test_notification_counts_without_user_defaults_to_zero(self):
        self.assertEqual(next_notifications.notification_counts(None, None), {"total": 0, "unread": 0})


if __name__ == "__main__":
    unittest.main()
