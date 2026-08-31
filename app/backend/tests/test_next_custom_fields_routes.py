"""The custom-field and rating endpoints, called as HTTP routes.

Everything else written for this feature tests a helper directly or reads the
emitted source as text. Nothing called the routes, and the route is where the
permission check, the argument types and the body parsing live. Two
user-facing bugs came out of that one gap: a JSON body sent without a content
type, so the server parsed an empty dict and complained about a field the user
had filled in; and a movie id passed where a movie entity was expected, which
crashed every save with `'UUID' object is not subscriptable`.

Neither is subtle. Both were invisible to a suite of 3400 tests, because no
test ever issued the request.

**The permission helpers are deliberately not patched.** Supplying an actor by
patching `require_next_permission` is the house pattern and is fine -- it
stands in for the auth layer. Patching `actor_can_edit_visible_movie` is not:
some existing tests do, and a test written that way would have sailed past the
crash, because the broken call is the one that gets mocked away. The actor here
holds every permission so the real helper runs and is exercised against a real
row.

The pairing that caused it is worth naming, since the two functions sit next to
each other and read alike: `actor_can_view_movie` takes a movie **id**, while
`actor_can_edit_visible_movie` takes a movie **entity** and immediately does
`movie["id"]`.
"""

import os
import sys
import unittest
import uuid
from unittest.mock import patch


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
PREFIX = "custom-field-route-test"


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class CustomFieldRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = next_app.app.test_client()
        self.actor = {
            "id": "00000000-0000-0000-0000-0000000000c5",
            "role": "owner",
            "permissions": ["*"],
        }
        self.permission = patch(
            "app.backend.next_app.require_next_permission", return_value=self.actor
        )
        self.permission.start()
        self.addCleanup(self.permission.stop)
        # The audit trail carries a real foreign key to `users`, so the actor
        # standing in for the auth layer has to exist as a row.
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (id, username) VALUES (%s,%s) ON CONFLICT (id) DO NOTHING",
                    (self.actor["id"], f"{PREFIX}-actor"),
                )
            conn.commit()

    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM movie_custom_field_values WHERE field_id IN "
                    "(SELECT id FROM custom_field_definitions WHERE key LIKE %s)",
                    (PREFIX.replace("-", "_") + "%",),
                )
                # The key is a slug, so the hyphens in PREFIX are underscores here.
                cur.execute(
                    "DELETE FROM custom_field_definitions WHERE key LIKE %s",
                    (PREFIX.replace("-", "_") + "%",),
                )
                cur.execute(
                    "DELETE FROM movie_user_ratings WHERE movie_id IN "
                    "(SELECT id FROM movies WHERE title LIKE %s)",
                    (f"{PREFIX}%",),
                )
                cur.execute("DELETE FROM movies WHERE title LIKE %s", (f"{PREFIX}%",))
                cur.execute("DELETE FROM audit_events WHERE actor_user_id=%s", (self.actor["id"],))
                cur.execute("DELETE FROM users WHERE username LIKE %s", (f"{PREFIX}%",))
            conn.commit()

    def _movie(self):
        movie_id = uuid.uuid4()
        title = f"{PREFIX}-{movie_id}"
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO movies (id, public_id, title, sort_title) VALUES (%s,%s,%s,%s)",
                    (movie_id, f"{PREFIX}-{movie_id}", title, title),
                )
            conn.commit()
        return movie_id

    def _field(self, field_type="text", name=None):
        """A field whose name is unique to the calling test.

        The key is a slug of the name, and one test here archives its field.
        Sharing a name across tests would let that archive leak into whichever
        test ran next, which is a failure about ordering rather than about the
        route.
        """
        response = self.client.post(
            "/api/next/admin/custom-fields",
            json={"name": name or f"{PREFIX} {uuid.uuid4().hex[:8]}", "fieldType": field_type},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["field"]

    def test_creating_a_field_returns_it(self):
        field = self._field()
        self.assertTrue(field["id"])
        self.assertEqual(field["fieldType"], "text")

    def test_creating_the_same_field_twice_returns_the_existing_one(self):
        """Re-creating is not a conflict: the key is a natural key on the name.

        That is what makes a create safe to retry from a phone that lost its
        connection mid-request, so the same name has to be sent twice here on
        purpose rather than through the randomising helper.
        """
        name = f"{PREFIX} {uuid.uuid4().hex[:8]}"
        first = self._field(name=name)
        response = self.client.post(
            "/api/next/admin/custom-fields",
            json={"name": name, "fieldType": "text"},
        )
        payload = response.get_json()
        self.assertFalse(payload["created"])
        self.assertEqual(payload["field"]["id"], first["id"])

    def test_renaming_a_field_does_not_need_a_name_on_every_patch(self):
        field = self._field()
        rename = self.client.patch(
            f"/api/next/admin/custom-fields/{field['id']}", json={"name": f"{PREFIX} rack"}
        )
        self.assertEqual(rename.status_code, 200, rename.get_data(as_text=True))
        archive = self.client.patch(
            f"/api/next/admin/custom-fields/{field['id']}", json={"archived": True}
        )
        self.assertEqual(archive.status_code, 200, archive.get_data(as_text=True))

    def test_setting_a_value_stores_it(self):
        """The regression: this route passed a movie id where an entity was due.

        `actor_can_edit_visible_movie` dereferences `movie["id"]` on its way in,
        so the request died with a TypeError before reaching any of the value
        handling -- for every user, whatever their permissions.
        """
        field = self._field()
        movie_id = self._movie()
        response = self.client.put(
            f"/api/next/movies/{movie_id}/custom-values",
            json={"values": {field["key"]: "Shelf B"}},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        values = {row["key"]: row["value"] for row in response.get_json()["customValues"]}
        self.assertEqual(values.get(field["key"]), "Shelf B")

    def test_clearing_a_value_removes_the_row(self):
        field = self._field()
        movie_id = self._movie()
        self.client.put(
            f"/api/next/movies/{movie_id}/custom-values",
            json={"values": {field["key"]: "Shelf B"}},
        )
        response = self.client.put(
            f"/api/next/movies/{movie_id}/custom-values",
            json={"values": {field["key"]: ""}},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.get_json()["customValues"], [])

    def test_a_value_on_a_missing_movie_is_a_404(self):
        field = self._field()
        response = self.client.put(
            f"/api/next/movies/{uuid.uuid4()}/custom-values",
            json={"values": {field["key"]: "Shelf B"}},
        )
        self.assertEqual(response.status_code, 404, response.get_data(as_text=True))

    def test_usage_counts_the_films_holding_a_value(self):
        field = self._field()
        movie_id = self._movie()
        self.client.put(
            f"/api/next/movies/{movie_id}/custom-values",
            json={"values": {field["key"]: "Shelf B"}},
        )
        response = self.client.get(f"/api/next/admin/custom-fields/{field['id']}/usage")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.get_json()["movies"], 1)

    def test_rating_a_film_stores_the_score(self):
        movie_id = self._movie()
        response = self.client.put(f"/api/next/movies/{movie_id}/rating", json={"score": 8.5})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
