import unittest
import uuid
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.backend.next_import import NextImporter
from app.backend.next_ownership import actor_or_instance_owner_id
from app.backend.next_ownership import instance_owner_id


class _OwnerCursor:
    def __init__(self, row):
        self.row = row
        self.query = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query):
        self.query = " ".join(str(query).split())

    def fetchone(self):
        return self.row


class _OwnerConnection:
    def __init__(self, row):
        self.cursor_instance = _OwnerCursor(row)

    def cursor(self):
        return self.cursor_instance


class ContainerOwnershipTests(unittest.TestCase):
    def test_uses_authenticated_creator_without_owner_lookup(self):
        conn = _OwnerConnection({"id": "instance-owner"})

        self.assertEqual(
            actor_or_instance_owner_id(conn, {"id": "creator-1"}),
            "creator-1",
        )
        self.assertEqual(conn.cursor_instance.query, "")

    def test_system_creation_falls_back_to_instance_owner(self):
        conn = _OwnerConnection({"id": "instance-owner"})

        self.assertEqual(actor_or_instance_owner_id(conn, {"id": None}), "instance-owner")
        self.assertIn("WHERE r.key = 'owner'", conn.cursor_instance.query)

    def test_missing_instance_owner_leaves_owner_nullable(self):
        self.assertIsNone(instance_owner_id(_OwnerConnection(None)))

    def test_legacy_container_import_uses_target_connection_for_owner_fallback(self):
        importer = NextImporter.__new__(NextImporter)
        importer.sqlite = object()
        importer.movie_ids = {}
        importer.container_ids = {}
        importer.Jsonb = lambda value: value
        importer.summary = SimpleNamespace(counters=defaultdict(int))
        importer.record_mapping = MagicMock()
        importer.import_media = False
        conn = MagicMock()
        cursor = MagicMock()
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        cursor_context.__exit__.return_value = False
        conn.cursor.return_value = cursor_context
        row = {"id": 1, "title": "Legacy vault"}

        with (
            patch("app.backend.next_import.rows", return_value=[row]),
            patch(
                "app.backend.next_import.actor_or_instance_owner_id",
                return_value="instance-owner",
            ) as resolve_owner,
        ):
            importer.import_container_table(conn, uuid.uuid4(), "vaults", "vault")

        resolve_owner.assert_called_once_with(conn)
        insert_params = cursor.execute.call_args_list[0].args[1]
        self.assertEqual(insert_params[9], "instance-owner")


if __name__ == "__main__":
    unittest.main()
