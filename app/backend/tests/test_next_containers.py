import os
import sys
import unittest
from unittest.mock import ANY, MagicMock, patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_app import NextApiError
    from app.backend.next_app import actor_can_convert_container
    from app.backend.next_app import create_app
    from app.backend.next_app import container_payload
    from app.backend.next_app import container_detail_image
    from app.backend.next_app import container_type_change
    from app.backend.next_app import container_types_for_ids
    from app.backend.next_app import legacy_media_public_url
    from app.backend.next_app import merge_receiver_members
    from app.backend.next_app import receiver_member_payload_from_raw
    from app.backend.next_app import normalize_container_type
    from app.backend.next_app import should_reuse_box_set_container_for_import
    from app.backend.next_app import visible_container_where_sql
    from app.backend.next_app import with_preview_media_urls
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name != "flask":
        raise
    NextApiError = None
    actor_can_convert_container = None
    create_app = None
    container_payload = None
    container_detail_image = None
    container_type_change = None
    container_types_for_ids = None
    legacy_media_public_url = None
    merge_receiver_members = None
    receiver_member_payload_from_raw = None
    normalize_container_type = None
    should_reuse_box_set_container_for_import = None
    visible_container_where_sql = None
    with_preview_media_urls = None


@unittest.skipIf(container_payload is None, "Flask is not installed in this test environment")
class NextContainerPolicyTests(unittest.TestCase):
    def test_normalizes_supported_container_types(self):
        self.assertEqual(normalize_container_type("box-set"), "box_set")
        self.assertEqual(normalize_container_type("collection"), "collection")
        self.assertEqual(normalize_container_type("Vault"), "vault")

    def test_rejects_unknown_container_types(self):
        with self.assertRaises(NextApiError):
            normalize_container_type("playlist")

    def test_allows_only_box_set_and_vault_type_changes(self):
        self.assertEqual(container_type_change("box_set", "vault"), ("vault", True))
        self.assertEqual(container_type_change("vault", "box_set"), ("box_set", True))
        self.assertEqual(container_type_change("vault", "vault"), ("vault", False))
        with self.assertRaisesRegex(NextApiError, "Only box-sets and vaults"):
            container_type_change("collection", "vault")
        with self.assertRaisesRegex(NextApiError, "Only box-sets and vaults"):
            container_type_change("box_set", "collection")

    def test_container_conversion_requires_owner_or_admin_override(self):
        container = {
            "container_type": "box_set",
            "owner_id": "creator-1",
        }
        creator = {
            "id": "creator-1",
            "role": "media_editor",
            "permissions": ["containers.edit"],
        }
        other_editor = {
            "id": "editor-2",
            "role": "media_editor",
            "permissions": ["containers.edit"],
        }
        admin = {
            "id": "admin-1",
            "role": "admin",
            "permissions": ["containers.edit"],
        }
        system_owner = {
            "id": None,
            "role": "owner",
            "permissions": ["*"],
        }

        self.assertTrue(actor_can_convert_container(container, creator))
        self.assertFalse(actor_can_convert_container(container, other_editor))
        self.assertTrue(actor_can_convert_container(container, admin))
        self.assertTrue(actor_can_convert_container(container, system_owner))
        self.assertFalse(
            actor_can_convert_container(
                container,
                {"id": "creator-1", "role": "media_editor", "permissions": ["containers.view"]},
            )
        )
        self.assertFalse(actor_can_convert_container({**container, "container_type": "collection"}, admin))

    def test_collection_item_type_lookup_can_lock_containers_for_conversion(self):
        container_id = "00000000-0000-0000-0000-000000000020"
        cursor = MagicMock()
        cursor.fetchall.return_value = [{"id": container_id, "container_type": "vault"}]
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        cursor_context.__exit__.return_value = False
        conn = MagicMock()
        conn.cursor.return_value = cursor_context

        with patch("app.backend.next_app.table_exists", return_value=True):
            result = container_types_for_ids(conn, [container_id], lock=True)

        self.assertEqual(result, {container_id: "vault"})
        self.assertIn("ORDER BY id FOR UPDATE", cursor.execute.call_args.args[0])

    def test_container_payload_allows_optional_fields_to_be_cleared(self):
        payload = container_payload(
            {
                "title": "Updated",
                "description": "",
                "year": "",
                "barcode": "",
                "badgeLabel": "",
            },
            existing={
                "title": "Existing",
                "description": "Old",
                "year": "2026",
                "barcode": "123",
                "badge_label": "Old badge",
                "metadata": {"sync_revision": 1},
            },
        )

        self.assertEqual(payload["title"], "Updated")
        self.assertIsNone(payload["description"])
        self.assertIsNone(payload["year"])
        self.assertIsNone(payload["barcode"])
        self.assertIsNone(payload["badge_label"])
        self.assertEqual(payload["metadata"], {"sync_revision": 1})

    def test_preview_media_urls_falls_back_to_container_metadata_artwork(self):
        row = {
            "id": "container-1",
            "metadata": {
                "poster_url": "https://image.example/poster.jpg",
                "backdrop_url": "https://image.example/backdrop.jpg",
            },
            "poster_asset_id": None,
            "poster_asset_storage_backend": None,
            "poster_asset_storage_key": None,
            "poster_asset_source_url": None,
            "backdrop_asset_id": None,
            "backdrop_asset_storage_backend": None,
            "backdrop_asset_storage_key": None,
            "backdrop_asset_source_url": None,
        }

        preview = with_preview_media_urls(row)

        self.assertEqual(preview["poster_url"], "https://image.example/poster.jpg")
        self.assertEqual(preview["backdrop_url"], "https://image.example/backdrop.jpg")

    def test_preview_media_urls_falls_back_to_legacy_container_poster_file(self):
        import tempfile
        from pathlib import Path

        old_data_dir = os.environ.get("DISCVAULT_LEGACY_DATA_DIR")
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            poster_dir = data_dir / "posters"
            poster_dir.mkdir()
            (poster_dir / "box.jpg").write_bytes(b"poster")
            os.environ["DISCVAULT_LEGACY_DATA_DIR"] = str(data_dir)
            try:
                row = {
                    "id": "container-1",
                    "metadata": {"poster_file": "box.jpg"},
                    "poster_asset_id": None,
                    "poster_asset_storage_backend": None,
                    "poster_asset_storage_key": None,
                    "poster_asset_source_url": None,
                    "backdrop_asset_id": None,
                    "backdrop_asset_storage_backend": None,
                    "backdrop_asset_storage_key": None,
                    "backdrop_asset_source_url": None,
                }

                preview = with_preview_media_urls(row)

                self.assertEqual(preview["poster_url"], "/api/next/media/legacy/poster/posters/box.jpg")
                self.assertEqual(
                    container_detail_image([], {"poster_file": "box.jpg"}, "poster"),
                    "/api/next/media/legacy/poster/posters/box.jpg",
                )
                self.assertEqual(
                    legacy_media_public_url("poster", "box.jpg"),
                    "/api/next/media/legacy/poster/posters/box.jpg",
                )
            finally:
                if old_data_dir is None:
                    os.environ.pop("DISCVAULT_LEGACY_DATA_DIR", None)
                else:
                    os.environ["DISCVAULT_LEGACY_DATA_DIR"] = old_data_dir

    def test_receiver_members_merge_preview_overrides(self):
        base = [
            {"title": "Movie One", "year": "2001", "sortOrder": 1},
            {"title": "Movie Two", "year": "2002", "sortOrder": 2},
        ]
        manual = receiver_member_payload_from_raw(
            {
                "title": "Manual Preview Member",
                "year": "2003",
                "format": "Ultra HD Blu-ray",
                "posterUrl": "https://image.example/manual.jpg",
                "tmdbId": "12345",
            },
            sort_order=3,
        )

        merged = merge_receiver_members(base, [manual])

        self.assertEqual([item["title"] for item in merged], ["Movie One", "Movie Two", "Manual Preview Member"])
        self.assertEqual(merged[2]["tmdbId"], "12345")
        self.assertEqual(merged[2]["posterUrl"], "https://image.example/manual.jpg")

    def test_box_set_import_with_barcode_does_not_reuse_title_only_container(self):
        existing_4k = {
            "barcode": "5053083220914",
            "metadata": {"format": "Ultra HD Blu-ray"},
        }

        self.assertFalse(
            should_reuse_box_set_container_for_import(
                existing_4k,
                incoming_barcode="5050582369601",
                incoming_format="DVD",
            )
        )

    def test_box_set_import_without_barcode_requires_compatible_unbarcoded_format(self):
        existing_dvd = {"barcode": "", "metadata": {"format": "DVD"}}
        existing_4k = {"barcode": "", "metadata": {"format": "Ultra HD Blu-ray"}}
        existing_barcoded = {"barcode": "5053083220914", "metadata": {"format": "Ultra HD Blu-ray"}}

        self.assertTrue(
            should_reuse_box_set_container_for_import(existing_dvd, incoming_format="DVD")
        )
        self.assertFalse(
            should_reuse_box_set_container_for_import(existing_4k, incoming_format="DVD")
        )
        self.assertFalse(
            should_reuse_box_set_container_for_import(existing_barcoded, incoming_format="DVD")
        )

    def test_group_shared_movies_make_their_containers_visible(self):
        actor = {
            "id": "member-1",
            "role": "member_groups",
            "permissions": ["collection.view_group"],
        }

        with patch("app.backend.next_app.table_exists", return_value=True):
            where, params = visible_container_where_sql(object(), actor, "c")

        self.assertIn("FROM container_movies vis_cm", where)
        self.assertIn("FROM media_group_movies vis_mgm", where)
        self.assertIn("vis_cm.container_id = c.id", where)
        self.assertEqual(params, ["member-1", "member-1", "member-1"])


@unittest.skipIf(create_app is None, "Flask is not installed in this test environment")
class NextContainerConversionRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()
        self.container_id = "00000000-0000-0000-0000-000000000010"
        self.owner_id = "00000000-0000-0000-0000-000000000011"
        self.actor = {
            "id": self.owner_id,
            "role": "media_editor",
            "permissions": ["containers.edit"],
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

    def container(self, container_type, *, barcode=None):
        return {
            "id": self.container_id,
            "public_id": "container-public-id",
            "container_type": container_type,
            "title": "Container title",
            "barcode": barcode,
            "badge_label": "Owned",
            "year": "2026",
            "description": "Description",
            "location_id": "00000000-0000-0000-0000-000000000012",
            "primary_movie_id": "00000000-0000-0000-0000-000000000013",
            "owner_id": self.owner_id,
            "metadata": {"poster_url": "https://example.test/poster.jpg"},
        }

    def request_conversion(self, existing, target_type, *, actor=None, fetch_rows=None):
        detail = {**existing, "container_type": target_type}
        self.cursor.fetchone.side_effect = fetch_rows or [existing]
        with (
            patch("app.backend.next_app.connect", return_value=self.connect_context),
            patch("app.backend.next_app.next_auth_effective_enabled", return_value=False),
            patch("app.backend.next_app.require_next_permission", return_value=actor or self.actor),
            patch("app.backend.next_app.container_entity", return_value=existing),
            patch("app.backend.next_app.table_exists", return_value=True),
            patch("app.backend.next_app.audit_event") as audit,
            patch("app.backend.next_app.emit_container_change") as emit,
            patch("app.backend.next_app.container_detail_entity", return_value=detail),
        ):
            response = self.client.patch(
                f"/api/next/containers/{self.container_id}",
                json={"containerType": target_type},
            )
        return response, audit, emit

    def test_owner_can_convert_in_both_directions_and_collection_links_follow(self):
        for source_type, target_type in (("box_set", "vault"), ("vault", "box_set")):
            with self.subTest(source_type=source_type, target_type=target_type):
                self.cursor.reset_mock()
                existing = self.container(source_type)

                response, audit, emit = self.request_conversion(existing, target_type)

                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.get_json()["converted"])
                queries = [" ".join(call.args[0].split()) for call in self.cursor.execute.call_args_list]
                self.assertIn("FOR UPDATE", queries[0])
                self.assertTrue(any("SET item_type=%s" in query for query in queries))
                update_query = next(query for query in queries if query.startswith("UPDATE containers SET title="))
                self.assertNotIn("public_id=", update_query)
                self.assertNotIn("owner_id=", update_query)
                self.assertNotIn("primary_movie_id=", update_query)
                metadata = audit.call_args.kwargs["metadata"]
                self.assertEqual(metadata["previousContainerType"], source_type)
                self.assertEqual(metadata["containerType"], target_type)
                self.assertEqual(metadata["collectionReferencesUpdated"], 2)
                emit.assert_called_once_with(self.conn, ANY, operation="upsert")

    def test_non_owner_cannot_convert_container(self):
        existing = self.container("box_set")
        actor = {
            "id": "00000000-0000-0000-0000-000000000099",
            "role": "media_editor",
            "permissions": ["containers.edit"],
        }

        response, audit, emit = self.request_conversion(existing, "vault", actor=actor)

        self.assertEqual(response.status_code, 403)
        queries = [" ".join(call.args[0].split()) for call in self.cursor.execute.call_args_list]
        self.assertEqual(len(queries), 1)
        self.assertIn("FOR UPDATE", queries[0])
        audit.assert_not_called()
        emit.assert_not_called()

    def test_conversion_to_box_set_rejects_duplicate_barcode(self):
        existing = self.container("vault", barcode="123456789")
        response, audit, emit = self.request_conversion(
            existing,
            "box_set",
            fetch_rows=[existing, {"id": "00000000-0000-0000-0000-000000000098"}],
        )

        self.assertEqual(response.status_code, 409)
        queries = [" ".join(call.args[0].split()) for call in self.cursor.execute.call_args_list]
        self.assertTrue(any("pg_advisory_xact_lock" in query for query in queries))
        self.assertTrue(any("container_type='box_set'" in query for query in queries))
        self.assertFalse(any(query.startswith("UPDATE containers SET title=") for query in queries))
        audit.assert_not_called()
        emit.assert_not_called()

    def test_collections_cannot_be_converted(self):
        existing = self.container("collection")

        response, audit, emit = self.request_conversion(existing, "vault")

        self.assertEqual(response.status_code, 400)
        self.cursor.execute.assert_not_called()
        audit.assert_not_called()
        emit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
