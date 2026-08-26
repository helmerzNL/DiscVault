import os
import sys
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_app import create_app
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name != "flask":
        raise
    create_app = None


CONTAINER_ID = "00000000-0000-0000-0000-000000000010"
MOVIE_A = UUID("00000000-0000-0000-0000-000000000001")
MOVIE_B = UUID("00000000-0000-0000-0000-000000000002")
MOVIE_TOMBSTONED = UUID("00000000-0000-0000-0000-000000000003")
BOX_SET_C = UUID("00000000-0000-0000-0000-000000000004")


@unittest.skipIf(create_app is None, "Flask is not installed in this test environment")
class NextContainerMemberRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()
        self.actor = {
            "id": "00000000-0000-0000-0000-000000000011",
            "role": "owner",
            "permissions": ["*"],
        }
        self.conn = MagicMock()
        self.connect_context = MagicMock()
        self.connect_context.__enter__.return_value = self.conn
        self.connect_context.__exit__.return_value = False
        self.transaction_context = MagicMock()
        self.transaction_context.__enter__.return_value = None
        self.transaction_context.__exit__.return_value = False
        self.conn.transaction.return_value = self.transaction_context
        self.cursor = MagicMock()
        self.cursor.rowcount = 1
        self.cursor_context = MagicMock()
        self.cursor_context.__enter__.return_value = self.cursor
        self.cursor_context.__exit__.return_value = False
        self.conn.cursor.return_value = self.cursor_context

    def container_movie_updates(self):
        return [
            call.args[1]
            for call in self.cursor.execute.call_args_list
            if call.args[0].strip().startswith("UPDATE container_movies")
        ]


class NextContainerReorderRouteTests(NextContainerMemberRouteTestCase):
    def reorder(self, movie_ids, *, linked, visible):
        # The route reads the raw link set first (for the "is it linked at
        # all" check) and the visibility-filtered member set second.
        self.cursor.fetchall.side_effect = [
            [{"movie_id": movie_id} for movie_id in linked],
            [{"movie_id": movie_id} for movie_id in visible],
        ]
        with (
            patch("app.backend.next_app.connect", return_value=self.connect_context),
            patch("app.backend.next_app.next_auth_effective_enabled", return_value=False),
            patch("app.backend.next_app.require_next_permission", return_value=self.actor),
            patch("app.backend.next_app.table_exists", return_value=True),
            patch("app.backend.next_app.container_type_for_id", return_value="box_set"),
            patch("app.backend.next_app.emit_container_membership_change"),
            patch("app.backend.next_app.container_detail_entity", return_value={"container": {"id": CONTAINER_ID}}) as detail,
        ):
            response = self.client.patch(
                f"/api/next/containers/{CONTAINER_ID}/movies/order",
                json={"movieIds": [str(movie_id) for movie_id in movie_ids]},
            )
        return response, detail

    def test_reorder_succeeds_when_a_tombstoned_link_is_omitted(self):
        # Soft-deleting a movie keeps its container_movies row, but the UI
        # never sees that member, so the posted order cannot include it.
        response, detail = self.reorder(
            [MOVIE_B, MOVIE_A],
            linked=[MOVIE_A, MOVIE_B, MOVIE_TOMBSTONED],
            visible=[MOVIE_A, MOVIE_B],
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")
        container_uuid = UUID(CONTAINER_ID)
        self.assertEqual(
            self.container_movie_updates(),
            [(1, container_uuid, MOVIE_B), (2, container_uuid, MOVIE_A)],
        )
        detail.assert_called_once_with(self.conn, container_uuid, actor=self.actor)

    def test_reorder_still_requires_every_visible_member(self):
        response, _ = self.reorder(
            [MOVIE_A],
            linked=[MOVIE_A, MOVIE_B, MOVIE_TOMBSTONED],
            visible=[MOVIE_A, MOVIE_B],
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("must include every linked movie", response.get_json()["error"])
        self.assertEqual(self.container_movie_updates(), [])

    def test_reorder_rejects_a_movie_that_is_not_linked(self):
        response, _ = self.reorder(
            [MOVIE_A, MOVIE_B, MOVIE_TOMBSTONED],
            linked=[MOVIE_A, MOVIE_B],
            visible=[MOVIE_A, MOVIE_B],
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("not linked to this container", response.get_json()["error"])
        self.assertEqual(self.container_movie_updates(), [])


class NextContainerRemoveMovieRouteTests(NextContainerMemberRouteTestCase):
    def test_remove_container_movie_reports_success(self):
        # Regression: the route used to reference an unassigned `actor` after
        # the delete committed, turning every successful removal into a 500.
        with (
            patch("app.backend.next_app.connect", return_value=self.connect_context),
            patch("app.backend.next_app.next_auth_effective_enabled", return_value=False),
            patch("app.backend.next_app.require_next_permission", return_value=self.actor),
            patch("app.backend.next_app.table_exists", return_value=True),
            patch("app.backend.next_app.container_type_for_id", return_value="box_set"),
            patch("app.backend.next_app.emit_container_membership_change"),
            patch("app.backend.next_app.capture_collection_value_snapshot") as snapshot,
            patch("app.backend.next_app.container_detail_entity", return_value={"container": {"id": CONTAINER_ID}}) as detail,
        ):
            response = self.client.delete(f"/api/next/containers/{CONTAINER_ID}/movies/{MOVIE_A}")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["changed"], 1)
        snapshot.assert_called_once_with(self.conn, self.actor)
        detail.assert_called_once_with(self.conn, UUID(CONTAINER_ID), actor=self.actor)


class NextBulkContainerMovieRouteTests(NextContainerMemberRouteTestCase):
    def test_add_movies_to_box_set_reports_success(self):
        self.cursor.fetchone.return_value = {"max_sort": 0}
        self.cursor.fetchall.return_value = [{"id": MOVIE_A}]
        with (
            patch("app.backend.next_app.connect", return_value=self.connect_context),
            patch("app.backend.next_app.next_auth_effective_enabled", return_value=False),
            patch("app.backend.next_app.require_next_permission", return_value=self.actor),
            patch("app.backend.next_app.table_exists", return_value=True),
            patch("app.backend.next_app.container_type_for_id", return_value="box_set"),
            patch("app.backend.next_app.emit_container_membership_change"),
            patch("app.backend.next_app.capture_collection_value_snapshot") as snapshot,
        ):
            response = self.client.post(
                f"/api/next/bulk/containers/{CONTAINER_ID}/movies",
                json={"movieIds": [str(MOVIE_A)], "targetType": "box_set"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["changed"], 1)
        snapshot.assert_called_once_with(self.conn, self.actor)


class NextCollectionReorderRouteTests(NextContainerMemberRouteTestCase):
    def reorder(self, items, *, linked, visible_movies, visible_containers):
        # Raw link rows first, then the visible movie items, then the visible
        # container items.
        self.cursor.fetchall.side_effect = [
            [{"item_type": item_type, "item_id": item_id} for item_type, item_id in linked],
            [{"item_type": item_type, "item_id": item_id} for item_type, item_id in visible_movies],
            [{"item_type": item_type, "item_id": item_id} for item_type, item_id in visible_containers],
        ]
        with (
            patch("app.backend.next_app.connect", return_value=self.connect_context),
            patch("app.backend.next_app.next_auth_effective_enabled", return_value=False),
            patch("app.backend.next_app.require_any_next_permission", return_value=self.actor),
            patch("app.backend.next_app.table_exists", return_value=True),
            patch("app.backend.next_app.container_type_for_id", return_value="collection"),
            patch("app.backend.next_app.container_detail_entity", return_value={"container": {"id": CONTAINER_ID}}),
        ):
            response = self.client.patch(
                f"/api/next/collections/{CONTAINER_ID}/items/order",
                json={
                    "items": [
                        {"itemType": item_type, "itemId": str(item_id)}
                        for item_type, item_id in items
                    ]
                },
            )
        return response

    def test_reorder_succeeds_when_a_hidden_item_is_omitted(self):
        response = self.reorder(
            [("box_set", BOX_SET_C), ("movie", MOVIE_A)],
            linked=[("movie", MOVIE_A), ("movie", MOVIE_TOMBSTONED), ("box_set", BOX_SET_C)],
            visible_movies=[("movie", MOVIE_A)],
            visible_containers=[("box_set", BOX_SET_C)],
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_reorder_still_requires_every_visible_item(self):
        response = self.reorder(
            [("movie", MOVIE_A)],
            linked=[("movie", MOVIE_A), ("box_set", BOX_SET_C)],
            visible_movies=[("movie", MOVIE_A)],
            visible_containers=[("box_set", BOX_SET_C)],
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("must include every linked item", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
