"""What the movie sync publishes, and that it keeps publishing it.

Two bugs of the same family motivate this file, and neither announced itself.

**The bootstrap and the delta had drifted.** `all_movie_entities` and
`movie_entity` were two hand-maintained SELECT lists with no test between them.
`release_title` had been added to the delta alone, so the field synced when a
movie was edited and was missing on a fresh install. The delta is the path
anyone testing by hand exercises, so it looked fine for as long as nobody
bootstrapped.

**`content_ratings` was accepted and never published.** The push side parsed it
and wrote it; the read side never mentioned it. A client could set it and could
never read it back — write-only, by construction, with nothing failing.

So the two guards below are deliberately structural rather than per-field: they
assert *relationships* between the payload builders, which is what neither bug
violated visibly and both violated in fact. A new column added to one builder
alone, or accepted on push but never published, fails here.

Sync-contract §4.8.
"""

import json
import os
import sys
import unittest
import uuid


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:
    psycopg = None
    dict_row = None

from app.backend import next_app, next_metadata


DATABASE_URL = os.environ.get("DATABASE_URL")


class MovieSyncFieldOwnershipTests(unittest.TestCase):
    """Ownership and lock membership. No database."""

    def test_notes_and_location_are_local_only(self):
        # No metadata provider may write these, which is why a client applies
        # them on pull without a lock check (§4.8).
        for field in ("notes", "location"):
            self.assertIn(field, next_metadata.METADATA_LOCAL_ONLY_FIELDS, field)

    def test_the_provider_writable_user_fields_are_not_local_only(self):
        # The mirror image: these four a provider *may* write, so a pull has to
        # honour the field lock. Asserting the negative keeps someone from
        # "tidying" them into the local-only set and silently disabling
        # metadata refresh for them.
        for field in ("release_title", "sort_title", "distributor", "studios"):
            self.assertNotIn(field, next_metadata.METADATA_LOCAL_ONLY_FIELDS, field)

    def test_every_provider_writable_user_field_is_lockable(self):
        # A provider-writable field with no lock is a field the user cannot
        # defend. `release_title` was exactly that until contract 1.6.
        for field in ("release_title", "sort_title", "distributor", "studios"):
            self.assertIn(field, next_metadata.MOVIE_LOCKABLE_FIELDS, field)

    def test_every_lockable_field_can_be_stripped_from_a_receiver_payload(self):
        # A lock that does not reach `MOVIE_LOCK_RECEIVER_KEYS` still forwards
        # the locked value upstream, which is the half of the lock nobody sees.
        # Artwork locks are handled separately and carry no receiver payload.
        missing = sorted(
            field
            for field in next_metadata.MOVIE_LOCKABLE_FIELDS - {"poster", "backdrop"}
            if field not in next_metadata.MOVIE_LOCK_RECEIVER_KEYS
        )
        self.assertEqual([], missing)


class TechnicalSyncKeyTests(unittest.TestCase):
    """`MOVIE_TECHNICAL_SYNC_KEYS` is load-bearing. No database."""

    def test_the_column_tuple_is_derived_from_the_key_map(self):
        self.assertEqual(
            tuple(next_app.MOVIE_TECHNICAL_SYNC_KEYS),
            next_app._TECHNICAL_SYNC_COLUMNS,
        )

    def test_the_empty_profile_covers_every_wire_key(self):
        empty = next_app._empty_technical_profile()
        expected = {wire for wire, _ in next_app.MOVIE_TECHNICAL_SYNC_KEYS.values()}
        self.assertEqual(expected, set(empty))

    def test_the_content_rating_empty_is_a_map(self):
        # `[]` here would read as "clear my rating" on every client that has one.
        self.assertEqual({}, next_app._empty_technical_profile()["content_ratings"])

    def test_the_empties_are_not_shared_between_calls(self):
        # One `dict` reused across a 1000-movie bootstrap would hand every movie
        # the same list object.
        first = next_app._empty_technical_profile()
        second = next_app._empty_technical_profile()
        first["packaging"].append("steelbook")
        self.assertEqual([], second["packaging"])


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class MovieSyncPayloadParityPostgresTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def _insert_bare_movie(self, conn, *, title="Payload Parity Test Movie") -> uuid.UUID:
        movie_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO movies (id, public_id, title, sort_title) VALUES (%s, %s, %s, %s)",
                (movie_id, f"payload-parity-test-{movie_id}", title, title),
            )
        conn.commit()
        return movie_id

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM movies WHERE public_id LIKE 'payload-parity-test-%'")
            conn.commit()

    def _write(self, conn, movie_id: uuid.UUID, body: dict) -> None:
        """The sync write path, as `apply_movie_upsert` performs it.

        `movie_payload_fields` returns every column, `None` for the ones the
        body did not mention, so the COALESCE is not a shortcut - it *is* the
        absent-preserves rule. Assigning the raw values would blank the whole
        row on a payload that named one field.
        """
        fields = next_app.movie_payload_fields(body)
        columns = [
            column
            for column in fields
            if column not in ("metadata", "technical_edits", "location_assignment", "discs")
        ]
        with conn.cursor() as cur:
            if columns:
                assignments = ", ".join(f"{column}=COALESCE(%s, {column})" for column in columns)
                params = [fields[column] for column in columns]
                cur.execute(f"UPDATE movies SET {assignments} WHERE id=%s", (*params, movie_id))
            if fields.get("metadata"):
                cur.execute(
                    "UPDATE movies SET metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb WHERE id=%s",
                    (json.dumps(fields["metadata"]), movie_id),
                )
            next_app.upsert_movie_technical_edits(cur, movie_id, fields["technical_edits"])
            # `location_id` is a uuid column, so the COALESCE above cannot carry
            # it: an absent key and an explicitly emptied one both arrive as
            # NULL. It is keyed on presence instead and written separately, and
            # this helper mirrors `apply_movie_upsert`, so it does the same.
            next_app.apply_movie_location_assignment(cur, movie_id, fields["location_assignment"])
            # `discs` is a table, not a column, and presence-keyed like the two
            # above; the mirror applies it the way `apply_movie_upsert` does.
            if fields.get("discs") is not None:
                next_app.apply_movie_discs(
                    cur, movie_id, fields["discs"], media_type="MOVIE"
                )
        conn.commit()

    # ---- The two structural guards ----

    def test_the_bootstrap_and_the_delta_publish_the_same_movie_keys(self):
        """The guard for the bug that started this.

        Not "the two are equal" - the delta legitimately carries routing and
        tombstone state a bootstrap has no use for. The difference has to be
        exactly the declared allow-list, so widening it is a deliberate edit in
        one named place rather than a silent divergence.
        """
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            delta = next_app.movie_entity(conn, movie_id)
            bootstrap = next(
                movie
                for movie in next_app.all_movie_entities(conn, limit=1000)
                if str(movie["id"]) == str(movie_id)
            )

        self.assertEqual(
            set(next_app._MOVIE_ENTITY_ONLY_COLUMNS),
            set(delta) - set(bootstrap),
            "the delta gained a column the bootstrap does not publish",
        )
        self.assertEqual(
            set(),
            set(bootstrap) - set(delta) - {"genre_search"},
            "the bootstrap gained a column the delta does not publish",
        )

    def test_every_field_the_push_accepts_is_readable_back(self):
        """The guard for the `content_ratings` class of bug.

        A field the server accepts but never publishes is write-only: a client
        can set it and can never confirm it, so the two sides drift with no
        symptom. Anything `movie_payload_fields` maps to a column has to appear
        in the payload a client reads.
        """
        # `metadata`, `technical_edits` and `location_assignment` are not plain
        # column mappings — they are sub-objects the caller applies separately,
        # so their key names are not what a client reads back. The columns
        # behind them are covered elsewhere: the technical wire keys below, and
        # `location_id` in `test_next_location_sync.py`.
        accepted = {
            column
            for column in next_app.movie_payload_fields({})
            if column not in ("metadata", "technical_edits", "location_assignment")
        }
        technical_wire_keys = {wire for wire, _ in next_app.MOVIE_TECHNICAL_SYNC_KEYS.values()}

        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            published = set(next_app.movie_entity(conn, movie_id)) | technical_wire_keys

        self.assertEqual(
            set(),
            accepted - published,
            "these are accepted on push but never published back to a client",
        )

    # ---- Round trip, per field ----

    def test_the_user_fields_round_trip(self):
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            self._write(
                conn,
                movie_id,
                {
                    "releaseTitle": "Dune: Part Two (4K Ultra HD + Blu-ray)",
                    "sortTitle": "Dune 02",
                    "notes": "Sleeve has a crease",
                    "location": "Shelf B, row 3",
                },
            )
            delta = next_app.movie_entity(conn, movie_id)
            bootstrap = next(
                movie
                for movie in next_app.all_movie_entities(conn, limit=1000)
                if str(movie["id"]) == str(movie_id)
            )

        for payload, label in ((delta, "delta"), (bootstrap, "bootstrap")):
            self.assertEqual("Dune: Part Two (4K Ultra HD + Blu-ray)", payload["release_title"], label)
            self.assertEqual("Dune 02", payload["sort_title"], label)
            self.assertEqual("Sleeve has a crease", payload["notes"], label)
            self.assertEqual("Shelf B, row 3", payload["location"], label)

    def test_an_omitted_key_leaves_the_stored_value_alone(self):
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            self._write(conn, movie_id, {"notes": "Original note"})
            self._write(conn, movie_id, {"title": "Payload Parity Test Movie"})
            self.assertEqual("Original note", next_app.movie_entity(conn, movie_id)["notes"])

    # ---- The content rating ----

    def test_the_content_rating_is_published_as_a_map(self):
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            self._write(conn, movie_id, {"contentRating": "16", "ratingCountry": "NL"})
            delta = next_app.movie_entity(conn, movie_id)
            bootstrap = next(
                movie
                for movie in next_app.all_movie_entities(conn, limit=1000)
                if str(movie["id"]) == str(movie_id)
            )

        self.assertEqual({"NL": "16"}, delta["content_ratings"])
        self.assertEqual({"NL": "16"}, bootstrap["content_ratings"])

    def test_a_movie_with_no_rating_reports_an_empty_map(self):
        # Empty, not an absent key: a client reads absence as "no opinion, keep
        # what you have", and "this film is rated nowhere" has to be sayable.
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            payload = next_app.movie_entity(conn, movie_id)

        self.assertIn("content_ratings", payload)
        self.assertEqual({}, payload["content_ratings"])

    def test_a_rating_from_one_country_does_not_disturb_another(self):
        # The `||` merge is what lets a phone set on NL leave the US rating
        # alone. Without it, every sync from a differently-configured device
        # would fight over one value.
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            self._write(conn, movie_id, {"contentRating": "16", "ratingCountry": "NL"})
            self._write(conn, movie_id, {"contentRating": "R", "ratingCountry": "US"})
            payload = next_app.movie_entity(conn, movie_id)

        self.assertEqual({"NL": "16", "US": "R"}, payload["content_ratings"])


if __name__ == "__main__":
    unittest.main()
