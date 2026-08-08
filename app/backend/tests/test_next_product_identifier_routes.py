"""The typed-identifier routes.

Their own pair rather than a slice of the movie PATCH: the patch runs the
receiver-proposal machinery over public fields, and a side table with its own
uniqueness rule has neither a lock nor a merge to run through it.

What is asserted here is mostly what the endpoints refuse, and that a rejection
is reported rather than silently dropped -- `set_movie_identifiers` drops bad
entries by design, which is right for a background caller and wrong for someone
who just typed a value in.
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_app import create_app
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name != "flask":
        raise
    create_app = None

MOVIE_ID = "00000000-0000-0000-0000-0000000000a1"
EAN13 = "4006381333931"
UPCA = "012569828827"


@unittest.skipIf(create_app is None, "Flask is not installed in this test environment")
class ProductIdentifierRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()
        self.actor = {"id": "00000000-0000-0000-0000-0000000000c1", "permissions": ["collection.edit_all"]}
        self.conn = MagicMock()
        self.connect_context = MagicMock()
        self.connect_context.__enter__.return_value = self.conn
        self.connect_context.__exit__.return_value = False

    def _patches(self, *, stored=None, side_effect=None):
        setter = patch("app.backend.next_app.set_movie_identifiers", side_effect=side_effect) if side_effect else patch(
            "app.backend.next_app.set_movie_identifiers", return_value=stored or []
        )
        return (
            patch("app.backend.next_app.connect", return_value=self.connect_context),
            patch("app.backend.next_app.next_auth_effective_enabled", return_value=False),
            patch("app.backend.next_app.require_next_permission", return_value=self.actor),
            patch("app.backend.next_app.require_any_next_permission", return_value=self.actor),
            patch("app.backend.next_app.movie_entity", return_value={"id": MOVIE_ID, "metadata": {}}),
            patch("app.backend.next_app.actor_can_edit_visible_movie", return_value=True),
            patch("app.backend.next_app.actor_can_view_movie", return_value=True),
            # `autospec` rather than a bare mock, and that is the point of this
            # line. A plain MagicMock accepts any keyword, so a call written
            # with the wrong argument names passes every test here and fails
            # the first time a user presses Save. That is exactly how
            # `entity_type=` reached beta.
            patch("app.backend.next_app.audit_event", autospec=True),
            setter,
        )

    def _run(self, patches, call):
        if not patches:
            return call()
        with patches[0]:
            return self._run(patches[1:], call)

    def _put(self, body, **kwargs):
        return self._run(
            self._patches(**kwargs),
            lambda: self.client.put(
                f"/api/next/movies/{MOVIE_ID}/identifiers",
                data=json.dumps(body),
                content_type="application/json",
            ),
        )

    def test_the_vocabulary_is_served_rather_than_copied_into_each_client(self):
        """A client that keeps its own list drifts out of step with what the
        column will accept, and finds out as a 500."""
        with patch("app.backend.next_app.movie_identifiers_by_type", return_value=[]):
            response = self._run(
                self._patches(),
                lambda: self.client.get(f"/api/next/movies/{MOVIE_ID}/identifiers"),
            )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(
            payload["types"], ["ean", "upc", "isbn", "asin", "catalog_number"]
        )
        self.assertEqual(payload["scannableTypes"], ["ean", "isbn", "upc"])

    def test_a_valid_set_is_stored(self):
        stored = [{"type": "ean", "value": EAN13}, {"type": "upc", "value": UPCA}]
        response = self._put({"identifiers": stored}, stored=stored)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["identifiers"], stored)

    def test_a_value_that_does_not_match_its_stated_type_is_reported(self):
        """Not dropped. Someone typing an EAN into the ASIN box has made a
        mistake they can fix, and a silent drop reads as the save having
        worked."""
        response = self._put({"identifiers": [{"type": "asin", "value": EAN13}]})
        self.assertEqual(response.status_code, 400)

    def test_a_failed_check_digit_is_reported(self):
        """The checksum is the only evidence the read was correct."""
        response = self._put({"identifiers": [{"type": "ean", "value": "4006381333932"}]})
        self.assertEqual(response.status_code, 400)

    def test_a_body_without_a_list_is_refused(self):
        response = self._put({"identifiers": "4006381333931"})
        self.assertEqual(response.status_code, 400)

    def test_the_list_is_bounded_the_same_way_upstream_bounds_it(self):
        response = self._put({"identifiers": [{"type": "ean", "value": EAN13}] * 26})
        self.assertEqual(response.status_code, 400)

    def test_a_code_another_movie_already_holds_is_a_conflict_and_not_a_fault(self):
        """One scan resolves to one film. The partial unique index enforcing
        that is a real answer to the caller, not a server error."""

        def _boom(*args, **kwargs):
            raise RuntimeError(
                'duplicate key value violates unique constraint "uq_movie_product_identifiers_scannable"'
            )

        response = self._put({"identifiers": [{"type": "ean", "value": EAN13}]}, side_effect=_boom)
        self.assertEqual(response.status_code, 409)

    def test_an_unrelated_database_failure_is_not_disguised_as_a_conflict(self):
        def _boom(*args, **kwargs):
            raise RuntimeError("connection is dead")

        response = self._put({"identifiers": [{"type": "ean", "value": EAN13}]}, side_effect=_boom)
        # A fault, reported as one. Matching on the constraint name rather than
        # on "something went wrong" is what keeps the two apart.
        self.assertNotEqual(response.status_code, 409)
        self.assertGreaterEqual(response.status_code, 500)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
