"""Route-level cover for the bulk box-set / list membership endpoints.

`python -m py_compile` — the only whole-file check CI ran over
`next_app.py` — proves a module parses, not that a route runs. A route
that references a name it never bound compiles cleanly and raises
`NameError` the first time a request reaches that line, which is how
"name 'actor' is not defined" reached a released build (issue #720):
`/api/next/bulk/containers/<id>/movies` passed `actor` to the collection
value snapshot without ever assigning it.

These tests exercise the two bulk membership routes through the Flask
test client, so any unbound name on their happy path fails here instead
of in front of a user.
"""

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
COLLECTION_ID = "00000000-0000-0000-0000-000000000020"
MOVIE_A = UUID("00000000-0000-0000-0000-000000000001")
MOVIE_B = UUID("00000000-0000-0000-0000-000000000002")


@unittest.skipIf(create_app is None, "Flask is not installed in this test environment")
class NextBulkMembershipRouteTestCase(unittest.TestCase):
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
        self.cursor.fetchone.return_value = {"max_sort": 0}
        self.cursor_context = MagicMock()
        self.cursor_context.__enter__.return_value = self.cursor
        self.cursor_context.__exit__.return_value = False
        self.conn.cursor.return_value = self.cursor_context


class NextBulkContainerMoviesRouteTests(NextBulkMembershipRouteTestCase):
    def post_bulk(self, body, *, container_type="box_set"):
        with (
            patch("app.backend.next_app.connect", return_value=self.connect_context),
            patch("app.backend.next_app.next_auth_effective_enabled", return_value=False),
            patch("app.backend.next_app.require_next_permission", return_value=self.actor),
            patch("app.backend.next_app.table_exists", return_value=True),
            patch("app.backend.next_app.container_type_for_id", return_value=container_type),
            patch("app.backend.next_app.require_existing_movie_ids"),
            patch("app.backend.next_app.emit_container_membership_change"),
            patch("app.backend.next_app.capture_collection_value_snapshot") as snapshot,
        ):
            response = self.client.post(
                f"/api/next/bulk/containers/{CONTAINER_ID}/movies",
                json=body,
            )
        return response, snapshot

    def test_bulk_add_links_the_selected_movies_to_the_box_set(self):
        response, snapshot = self.post_bulk(
            {"movieIds": [str(MOVIE_A), str(MOVIE_B)], "targetType": "box_set"}
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["operation"], "add")
        self.assertEqual(payload["requested"], 2)
        self.assertEqual(payload["containerType"], "box_set")

    def test_bulk_add_snapshots_the_collection_value_for_the_authenticated_actor(self):
        # The regression itself: the snapshot argument has to be the actor
        # the permission check returned, not a name the route never bound.
        response, snapshot = self.post_bulk({"movieIds": [str(MOVIE_A)]})

        self.assertEqual(response.status_code, 200)
        snapshot.assert_called_once_with(self.conn, self.actor)

    def test_bulk_add_to_a_vault_works_the_same_way(self):
        response, snapshot = self.post_bulk(
            {"movieIds": [str(MOVIE_A)], "targetType": "vault"},
            container_type="vault",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["containerType"], "vault")
        snapshot.assert_called_once_with(self.conn, self.actor)

    def test_bulk_remove_unlinks_the_selected_movies(self):
        response, snapshot = self.post_bulk(
            {"movieIds": [str(MOVIE_A)], "operation": "remove"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["operation"], "remove")
        snapshot.assert_called_once_with(self.conn, self.actor)


class NextBulkCollectionItemsRouteTests(NextBulkMembershipRouteTestCase):
    def post_bulk(self, body):
        with (
            patch("app.backend.next_app.connect", return_value=self.connect_context),
            patch("app.backend.next_app.next_auth_effective_enabled", return_value=False),
            patch("app.backend.next_app.require_any_next_permission", return_value=self.actor),
            patch("app.backend.next_app.table_exists", return_value=True),
            patch("app.backend.next_app.container_type_for_id", return_value="collection"),
            patch("app.backend.next_app.container_types_for_ids", return_value={}),
            patch("app.backend.next_app.collection_ancestor_container_ids", return_value=set()),
            patch("app.backend.next_app.require_existing_movie_ids"),
        ):
            response = self.client.post(
                f"/api/next/bulk/collections/{COLLECTION_ID}/items",
                json=body,
            )
        return response

    def test_bulk_add_puts_the_selected_movies_on_the_list(self):
        response = self.post_bulk({"movieIds": [str(MOVIE_A), str(MOVIE_B)]})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["operation"], "add")
        self.assertEqual(payload["requested"], 2)

    def test_bulk_remove_takes_the_selected_movies_off_the_list(self):
        response = self.post_bulk({"movieIds": [str(MOVIE_A)], "operation": "remove"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["operation"], "remove")


if __name__ == "__main__":
    unittest.main()
