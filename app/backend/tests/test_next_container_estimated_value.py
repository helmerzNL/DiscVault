import os
import sys
import unittest
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:
    psycopg = None
    dict_row = None

from app.backend import next_app


DATABASE_URL = os.environ.get("DATABASE_URL")


class ContainerEstimatedValuePayloadTests(unittest.TestCase):
    """A box set may carry a value; a vault and a collection may not."""

    def test_box_set_accepts_a_value_and_currency(self):
        payload = next_app.container_payload(
            {"title": "Alien Anthology", "estimatedValue": "89,95", "estimatedValueCurrency": "eur"},
            container_type="box_set",
        )
        self.assertEqual(payload["estimated_value"], Decimal("89.95"))
        self.assertEqual(payload["estimated_value_currency"], "EUR")

    def test_box_set_value_rounds_to_two_decimals(self):
        payload = next_app.container_payload(
            {"title": "Rounding", "estimatedValue": "19.999"}, container_type="box_set"
        )
        self.assertEqual(payload["estimated_value"], Decimal("20.00"))

    def test_box_set_value_clears_on_explicit_empty_string(self):
        payload = next_app.container_payload(
            {"title": "Cleared", "estimatedValue": ""},
            existing={"container_type": "box_set", "estimated_value": Decimal("10.00")},
            container_type="box_set",
        )
        self.assertIsNone(payload["estimated_value"])

    def test_box_set_value_is_kept_when_the_body_does_not_mention_it(self):
        payload = next_app.container_payload(
            {"title": "Untouched"},
            existing={"container_type": "box_set", "estimated_value": Decimal("42.00"), "estimated_value_currency": "USD"},
            container_type="box_set",
        )
        self.assertEqual(payload["estimated_value"], Decimal("42.00"))
        self.assertEqual(payload["estimated_value_currency"], "USD")

    def test_box_set_rejects_a_non_numeric_value(self):
        with self.assertRaises(next_app.NextApiError):
            next_app.container_payload(
                {"title": "Bad", "estimatedValue": "priceless"}, container_type="box_set"
            )

    def test_box_set_rejects_a_currency_that_is_not_an_iso_code(self):
        with self.assertRaises(next_app.NextApiError):
            next_app.container_payload(
                {"title": "Bad", "estimatedValueCurrency": "euros"}, container_type="box_set"
            )

    def test_vault_rejects_a_value(self):
        # A vault is a way of arranging what you already own. Its worth is the sum
        # of its contents; a second number there would double-count the shelf.
        with self.assertRaises(next_app.NextApiError) as ctx:
            next_app.container_payload(
                {"title": "Shelf A", "estimatedValue": "10.00"}, container_type="vault"
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_collection_rejects_a_value(self):
        with self.assertRaises(next_app.NextApiError):
            next_app.container_payload(
                {"title": "Favourites", "estimatedValue": "10.00"}, container_type="collection"
            )

    def test_collection_rejects_a_currency_on_its_own(self):
        with self.assertRaises(next_app.NextApiError):
            next_app.container_payload(
                {"title": "Favourites", "estimatedValueCurrency": "EUR"}, container_type="collection"
            )

    def test_a_vault_edit_that_never_mentions_the_value_still_works(self):
        payload = next_app.container_payload({"title": "Shelf A"}, container_type="vault")
        self.assertIsNone(payload["estimated_value"])
        self.assertEqual(payload["title"], "Shelf A")

    def test_type_falls_back_to_the_existing_row_when_not_supplied(self):
        with self.assertRaises(next_app.NextApiError):
            next_app.container_payload(
                {"title": "Shelf A", "estimatedValue": "10.00"},
                existing={"container_type": "vault"},
            )


class EstimatedValueLockRouteTests(unittest.TestCase):
    """The API refuses a value write the UI would never make."""

    def setUp(self):
        self.app = next_app.create_app()
        self.client = self.app.test_client()
        self.movie_id = "00000000-0000-0000-0000-0000000000a1"
        self.container_id = "00000000-0000-0000-0000-0000000000a2"
        self.actor = {
            "id": "00000000-0000-0000-0000-0000000000a3",
            "role": "media_editor",
            "permissions": ["collection.edit_all", "containers.edit"],
        }
        self.conn = MagicMock()
        self.connect_context = MagicMock()
        self.connect_context.__enter__.return_value = self.conn
        self.connect_context.__exit__.return_value = False

    def test_setting_a_value_on_a_box_set_member_is_rejected(self):
        with (
            patch("app.backend.next_app.connect", return_value=self.connect_context),
            patch("app.backend.next_app.next_auth_effective_enabled", return_value=False),
            patch("app.backend.next_app.require_next_permission", return_value=self.actor),
            patch("app.backend.next_app.table_exists", return_value=True),
            patch("app.backend.next_app.movie_entity", return_value={"id": self.movie_id, "title": "Aliens"}),
            patch("app.backend.next_app.actor_can_edit_visible_movie", return_value=True),
            patch(
                "app.backend.next_app.movie_value_lock",
                return_value={"id": self.container_id, "title": "Alien Anthology"},
            ),
            patch("app.backend.next_app.write_movie_edit_record") as write,
        ):
            response = self.client.patch(
                f"/api/next/movies/{self.movie_id}",
                json={"title": "Aliens", "estimatedValue": "12.00"},
            )
        self.assertEqual(response.status_code, 409)
        # The stored amount is untouched: it comes back when the film leaves the set.
        write.assert_not_called()

    def test_an_edit_that_does_not_mention_the_value_still_saves(self):
        """A box-set member is not frozen - only its price is."""
        with (
            patch("app.backend.next_app.connect", return_value=self.connect_context),
            patch("app.backend.next_app.next_auth_effective_enabled", return_value=False),
            patch("app.backend.next_app.require_next_permission", return_value=self.actor),
            patch("app.backend.next_app.table_exists", return_value=True),
            patch("app.backend.next_app.movie_entity", return_value={"id": self.movie_id, "title": "Aliens"}),
            patch("app.backend.next_app.actor_can_edit_visible_movie", return_value=True),
            patch(
                "app.backend.next_app.movie_value_lock",
                return_value={"id": self.container_id, "title": "Alien Anthology"},
            ),
            patch("app.backend.next_app.write_movie_edit_record") as write,
            patch("app.backend.next_app.movie_detail_entity", return_value={}),
            patch("app.backend.next_app.capture_collection_value_snapshot"),
            patch("app.backend.next_app.audit_event"),
            patch("app.backend.next_app.movie_edit_receiver_proposal", return_value=None),
            patch("app.backend.next_app.movie_technical_spec_entity", return_value={}),
        ):
            response = self.client.patch(
                f"/api/next/movies/{self.movie_id}", json={"title": "Aliens 2"}
            )
        self.assertEqual(response.status_code, 200)
        write.assert_called_once()

    def test_a_free_movie_may_still_set_its_value(self):
        with (
            patch("app.backend.next_app.connect", return_value=self.connect_context),
            patch("app.backend.next_app.next_auth_effective_enabled", return_value=False),
            patch("app.backend.next_app.require_next_permission", return_value=self.actor),
            patch("app.backend.next_app.table_exists", return_value=True),
            patch("app.backend.next_app.movie_entity", return_value={"id": self.movie_id, "title": "Aliens"}),
            patch("app.backend.next_app.actor_can_edit_visible_movie", return_value=True),
            patch("app.backend.next_app.movie_value_lock", return_value=None),
            patch("app.backend.next_app.write_movie_edit_record") as write,
            patch("app.backend.next_app.movie_detail_entity", return_value={}),
            patch("app.backend.next_app.capture_collection_value_snapshot"),
            patch("app.backend.next_app.audit_event"),
            patch("app.backend.next_app.movie_edit_receiver_proposal", return_value=None),
            patch("app.backend.next_app.movie_technical_spec_entity", return_value={}),
        ):
            response = self.client.patch(
                f"/api/next/movies/{self.movie_id}",
                json={"title": "Aliens", "estimatedValue": "12.00"},
            )
        self.assertEqual(response.status_code, 200)
        write.assert_called_once()


NEXT_VIEWS_UI_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "next_views_ui.py")
)


class ContainerEstimatedValueUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()

    def test_the_box_set_form_has_a_value_and_a_currency_input(self):
        self.assertIn('<input id="containerEditEstimatedValue" name="estimated_value"', self.source)
        self.assertIn('<select id="containerEditEstimatedValueCurrency"', self.source)

    def test_the_value_fields_are_shown_only_for_a_box_set(self):
        self.assertIn("function syncContainerValueFieldVisibility(containerType)", self.source)
        self.assertIn('String(containerType || "") === "box_set"', self.source)

    def test_switching_the_type_retoggles_the_value_fields(self):
        self.assertIn(
            'document.getElementById("containerEditType")?.addEventListener("change"',
            self.source,
        )

    def test_the_value_is_only_submitted_for_a_box_set(self):
        # A vault or a collection would be a 400 from the API, even for "".
        self.assertIn('if (requestedType === "box_set") {', self.source)
        self.assertIn('body.estimatedValue = formTextValue("containerEditEstimatedValue");', self.source)

    def test_the_currency_picker_is_reused_rather_than_duplicated(self):
        self.assertIn(
            'fillMovieEditEstimatedValueCurrency(container.estimated_value_currency, "containerEditEstimatedValueCurrency")',
            self.source,
        )

    def test_a_box_set_member_has_its_price_field_disabled_and_blanked(self):
        self.assertIn("function applyMovieEstimatedValueLock(detail)", self.source)
        self.assertIn("element.disabled = locked;", self.source)
        self.assertIn('if (locked) element.value = "";', self.source)

    def test_a_locked_movie_does_not_submit_the_value_at_all(self):
        # Sending the blanked field would be a 409, and would ask the API to erase
        # an amount the user gets back when the film leaves the set.
        self.assertIn('if (document.getElementById("movieEditEstimatedValue")?.disabled) {', self.source)
        self.assertIn("delete body.estimatedValue;", self.source)

    def test_the_statistics_view_shows_a_collection_value_card(self):
        self.assertIn("function collectionValueCard(summary)", self.source)
        self.assertIn('tNext("stats.cardCollectionValue", "Collection value")', self.source)


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class ContainerEstimatedValuePostgresTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def _insert_container(self, conn, *, container_type="box_set", title="Container Value Test"):
        container_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO containers (id, public_id, container_type, title)
                VALUES (%s, %s, %s, %s)
                """,
                (container_id, f"container-value-test-{container_id}", container_type, title),
            )
        conn.commit()
        return container_id

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM containers WHERE public_id LIKE 'container-value-test-%'")
            conn.commit()

    def test_container_entity_reads_the_value_back(self):
        with self.connect() as conn:
            container_id = self._insert_container(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE containers SET estimated_value=%s, estimated_value_currency=%s WHERE id=%s",
                    (Decimal("129.95"), "EUR", container_id),
                )
            conn.commit()
            entity = next_app.container_entity(conn, container_id)
        self.assertEqual(entity["estimated_value"], Decimal("129.95"))
        self.assertEqual(entity["estimated_value_currency"], "EUR")

    def test_the_sync_entity_carries_the_value_to_mobile_clients(self):
        with self.connect() as conn:
            container_id = self._insert_container(conn, title="Sync Value Box Set")
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE containers SET estimated_value=%s WHERE id=%s",
                    (Decimal("7.50"), container_id),
                )
            conn.commit()
            entity = next_app.single_container_sync_entity(conn, container_id)
        self.assertEqual(entity["estimated_value"], Decimal("7.50"))

    def test_a_currency_may_be_absent(self):
        # Nullable on purpose (migration 061, mirroring 057): NULL means "not
        # recorded", and defaulting to EUR would misstate money.
        with self.connect() as conn:
            container_id = self._insert_container(conn, title="No Currency Box Set")
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE containers SET estimated_value=%s WHERE id=%s",
                    (Decimal("3.00"), container_id),
                )
            conn.commit()
            entity = next_app.container_entity(conn, container_id)
        self.assertEqual(entity["estimated_value"], Decimal("3.00"))
        self.assertIsNone(entity["estimated_value_currency"])


if __name__ == "__main__":
    unittest.main()
