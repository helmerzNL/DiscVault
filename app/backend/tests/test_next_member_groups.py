import os
import sys
import unittest
from unittest.mock import MagicMock, patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_app import create_app
    from app.backend.next_app import media_group_owner_can_delete
    from app.backend.next_app import media_group_owner_state
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name != "flask":
        raise
    create_app = None
    media_group_owner_can_delete = None
    media_group_owner_state = None


@unittest.skipIf(create_app is None, "Flask is not installed in this test environment")
class NextMemberGroupRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()
        self.conn = MagicMock()
        self.connect_context = MagicMock()
        self.connect_context.__enter__.return_value = self.conn
        self.connect_context.__exit__.return_value = False
        self.transaction_context = MagicMock()
        self.transaction_context.__enter__.return_value = None
        self.transaction_context.__exit__.return_value = False
        self.conn.transaction.return_value = self.transaction_context
        self.cursor = MagicMock()
        self.cursor_context = MagicMock()
        self.cursor_context.__enter__.return_value = self.cursor
        self.cursor_context.__exit__.return_value = False
        self.conn.cursor.return_value = self.cursor_context
        self.actor = {"id": "00000000-0000-0000-0000-000000000001"}
        self.group_id = "00000000-0000-0000-0000-000000000002"

    def route_patches(self, owner_state):
        return (
            patch("app.backend.next_app.connect", return_value=self.connect_context),
            patch("app.backend.next_app.require_next_permission", return_value=self.actor),
            patch("app.backend.next_app.table_exists", return_value=True),
            patch("app.backend.next_app.media_group_owner_state", return_value=owner_state),
            patch("app.backend.next_app.audit_event"),
            patch("app.backend.next_app.next_auth_effective_enabled", return_value=False),
        )

    def test_owner_can_rename_group(self):
        owner_state = {
            "name": "Old name",
            "actor_role": "owner",
            "member_count": 1,
        }
        connect_patch, permission_patch, table_patch, state_patch, audit_patch, auth_patch = (
            self.route_patches(owner_state)
        )
        with (
            connect_patch,
            permission_patch as permission_mock,
            table_patch,
            state_patch,
            audit_patch,
            auth_patch,
            patch(
                "app.backend.next_app.media_group_detail_entity",
                return_value={"id": self.group_id, "name": "New name"},
            ),
        ):
            response = self.client.patch(
                f"/api/next/media-groups/{self.group_id}",
                json={"name": "New name"},
            )

        self.assertEqual(response.status_code, 200)
        permission_mock.assert_called_with(self.conn, "groups.view")
        self.cursor.execute.assert_called_once()
        self.assertIn("UPDATE media_groups", self.cursor.execute.call_args.args[0])

    def test_non_owner_cannot_rename_group(self):
        owner_state = {
            "name": "Group",
            "actor_role": "manager",
            "member_count": 1,
        }
        patches = self.route_patches(owner_state)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            response = self.client.patch(
                f"/api/next/media-groups/{self.group_id}",
                json={"name": "New name"},
            )

        self.assertEqual(response.status_code, 403)
        self.cursor.execute.assert_not_called()

    def test_owner_can_delete_group_when_sole_member(self):
        owner_state = {
            "name": "Group",
            "actor_role": "owner",
            "member_count": 1,
        }
        patches = self.route_patches(owner_state)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            response = self.client.delete(f"/api/next/media-groups/{self.group_id}")

        self.assertEqual(response.status_code, 200)
        self.cursor.execute.assert_called_once()
        self.assertIn("DELETE FROM media_groups", self.cursor.execute.call_args.args[0])

    def test_owner_cannot_delete_group_while_other_members_remain(self):
        owner_state = {
            "name": "Group",
            "actor_role": "owner",
            "member_count": 2,
        }
        patches = self.route_patches(owner_state)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            response = self.client.delete(f"/api/next/media-groups/{self.group_id}")

        self.assertEqual(response.status_code, 409)
        self.cursor.execute.assert_not_called()

    def test_owner_state_locks_before_counting_and_supports_no_auth_owner(self):
        self.cursor.fetchone.side_effect = [
            {"id": self.group_id},
            {
                "id": self.group_id,
                "name": "Group",
                "actor_role": None,
                "member_count": 1,
            },
        ]

        state = media_group_owner_state(
            self.conn,
            self.group_id,
            {"id": None, "role": "owner"},
            lock=True,
        )

        self.assertEqual(state["actor_role"], "owner")
        self.assertEqual(self.cursor.execute.call_count, 2)
        self.assertIn("FOR UPDATE", self.cursor.execute.call_args_list[0].args[0])
        self.assertIn("COUNT(*)::int", self.cursor.execute.call_args_list[1].args[0])

    def test_no_auth_owner_can_only_delete_groups_without_real_members(self):
        actor = {"id": None, "role": "owner"}

        self.assertTrue(media_group_owner_can_delete(actor, {"member_count": 0}))
        self.assertFalse(media_group_owner_can_delete(actor, {"member_count": 1}))


if __name__ == "__main__":
    unittest.main()
