"""Who changed this film, and when.

The question this answers came from a user watching a value revert and having
no way to find out what had rewritten it. Three kinds of writer touch a movie —
a person through the edit form, a device through the sync route, and a plugin
through enrichment — and each recorded itself somewhere different, or in the
sync route's case nowhere at all.

What is worth pinning is not the merge itself but the two judgements inside it:
which events count as a change *to the record*, and that the timestamp leaves
here unformatted.
"""

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
except ModuleNotFoundError:  # pragma: no cover - depends on environment
    psycopg = None

from app.backend import next_app


DATABASE_URL = os.environ.get("DATABASE_URL")

#: Names this module's synthetic clients so a row read out of the database by
#: hand says where it came from. Cleanup goes by the ids the pushes returned,
#: not by this prefix -- a name is a label, not an index.
PREFIX = "history-test"


class HistorySourceTests(unittest.TestCase):
    """`source` says which surface a change came through."""

    def test_a_sync_push_is_attributed_by_its_user_agent(self):
        self.assertEqual(
            next_app.movie_history_source("movie.updated", {"source": "sync"}, "DiscVault-iOS/26.8"),
            "ios",
        )
        self.assertEqual(
            next_app.movie_history_source("movie.updated", {"source": "sync"}, "okhttp/4.12.0"),
            "android",
        )

    def test_an_unrecognised_client_is_sync_rather_than_a_guess(self):
        """A user agent is a claim by the client, not a fact. An unknown one
        still came through the sync route, and saying so is the honest answer —
        inventing a platform from a string nobody recognises is not."""
        self.assertEqual(
            next_app.movie_history_source("movie.updated", {"source": "sync"}, "curl/8.4.0"),
            "sync",
        )

    def test_anything_not_from_sync_is_the_web_surface(self):
        self.assertEqual(next_app.movie_history_source("movie.updated", {}, "Mozilla/5.0"), "web")


class ChangedFieldsTests(unittest.TestCase):
    """What an edit moved, answered on the entity rather than on the request.

    The web PATCH recorded `movie.updated` with the title and the barcode and
    nothing else, so a History row named a change and never which field. That
    matters most for the edit it describes worst: a disc-only edit touches no
    column on `movies` at all, so a diff of the movie's own fields reports
    nothing and the row reads as no change.
    """

    def test_a_disc_only_edit_is_still_a_change(self):
        before = {"id": "m1", "title": "Aladdin", "discs": [{"audioTracks": []}]}
        after = {"id": "m1", "title": "Aladdin", "discs": [{"audioTracks": [{"languageCode": "nl"}]}]}
        self.assertEqual(next_app.changed_movie_fields(before, after), ["discs"])

    def test_bookkeeping_that_moves_on_every_write_is_not_a_change(self):
        """`updated_at` and `revision` move whether or not a field did. Reporting
        them would make every row claim a change to everything, which is the
        same as reporting nothing."""
        before = {"id": "m1", "title": "Aladdin", "updated_at": "1", "revision": 1}
        after = {"id": "m1", "title": "Aladdin", "updated_at": "2", "revision": 2}
        self.assertEqual(next_app.changed_movie_fields(before, after), [])

    def test_a_field_that_appears_or_disappears_counts(self):
        self.assertEqual(next_app.changed_movie_fields({}, {"format": "4k_uhd"}), ["format"])
        self.assertEqual(next_app.changed_movie_fields({"format": "4k_uhd"}, {}), ["format"])

    def test_the_technical_profile_is_covered_because_it_is_flattened_in(self):
        """`movie_entity` merges the technical spec into the movie dict, which
        is why comparing entities covers it and comparing the request body
        would not."""
        before = {"id": "m1", "audio_tracks": [{"languageCode": "en", "codec": "dts_hd_ma"}]}
        after = {"id": "m1", "audio_tracks": [{"languageCode": "nl", "codec": "dts_hd_ma"}]}
        self.assertEqual(next_app.changed_movie_fields(before, after), ["audio_tracks"])


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class HistoryReaderTests(unittest.TestCase):
    def setUp(self):
        self.movie_id = str(uuid.uuid4())

    def _connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)

    def test_both_writers_appear_in_one_list_newest_first(self):
        """A plugin write and a person's edit are the same question asked twice.
        Merging them here rather than in the client is what makes the PWA, iOS
        and Android give the same answer in the same order."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_events (
                        event_type, category, actor_username, target_type, target_id,
                        summary, metadata, created_at
                    )
                    VALUES ('movie.updated', 'sync', 'helmer', 'movie', %s, 'Synced',
                            %s, now() - interval '1 minute')
                    """,
                    (self.movie_id, '{"source": "sync", "changedFields": ["format"]}'),
                )
                cur.execute(
                    "INSERT INTO metadata_plugins (id, name, version, manifest)"
                    " VALUES ('tmdb_test', 'TMDB test', '1.0.0', '{}')"
                    " ON CONFLICT (id) DO NOTHING"
                )
                cur.execute(
                    """
                    INSERT INTO metadata_field_provenance (
                        entity_type, entity_id, field_name, plugin_id, captured_at
                    )
                    VALUES ('movie', %s, 'format', 'tmdb_test', now())
                    """,
                    (self.movie_id,),
                )
            conn.commit()
            entries = next_app.movie_change_history(conn, self.movie_id)
            with conn.cursor() as cur:
                cur.execute("DELETE FROM audit_events WHERE target_id = %s", (self.movie_id,))
                cur.execute(
                    "DELETE FROM metadata_field_provenance WHERE entity_id = %s", (self.movie_id,)
                )
            conn.commit()

        self.assertEqual([entry["source"] for entry in entries], ["plugin", "sync"])
        self.assertEqual(entries[0]["plugin"], "tmdb_test")
        self.assertEqual(entries[1]["actor"], "helmer")
        self.assertEqual(entries[1]["fields"], ["format"])

    def test_the_timestamp_leaves_unformatted_and_with_its_offset(self):
        """The device knows its timezone and the server does not. Formatting
        here would bake the server's zone into a value the reader then
        mis-reads by however many hours they are away from it."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_events (
                        event_type, category, target_type, target_id, metadata
                    )
                    VALUES ('movie.updated', 'admin', 'movie', %s, '{}')
                    """,
                    (self.movie_id,),
                )
            conn.commit()
            entries = next_app.movie_change_history(conn, self.movie_id)
            with conn.cursor() as cur:
                cur.execute("DELETE FROM audit_events WHERE target_id = %s", (self.movie_id,))
            conn.commit()

        at = entries[0]["at"]
        self.assertRegex(at, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        self.assertTrue(at.endswith("+00:00") or at.endswith("Z") or "+" in at[10:])

    def test_activity_about_the_film_is_not_a_change_to_the_record(self):
        """A poster upload is about the film; it is not an edit of its fields.
        Ten entries that mix the two stop answering the question they exist
        for."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_events (event_type, category, target_type, target_id, metadata)
                    VALUES ('movie.media_uploaded', 'admin', 'movie', %s, '{}')
                    """,
                    (self.movie_id,),
                )
            conn.commit()
            entries = next_app.movie_change_history(conn, self.movie_id)
            with conn.cursor() as cur:
                cur.execute("DELETE FROM audit_events WHERE target_id = %s", (self.movie_id,))
            conn.commit()
        self.assertEqual(entries, [])


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class SyncLeavesATraceTests(unittest.TestCase):
    """A push over the sync route records who pushed it.

    This is the gap the whole feature exists to close. The sync path wrote the
    same columns the edit form writes and left nothing behind, so a value that
    changed underneath somebody was attributable to a plugin (provenance names
    those) or to a person editing in the browser (the audit named those) or to
    nothing at all -- and "nothing at all" was every write from the iOS and
    Android apps.
    """

    def setUp(self):
        # Every run gets its own ids. A fixed client entity id would be replayed
        # by the idempotency record rather than re-applied -- the second run
        # would then read two events on one movie and fail for a reason that has
        # nothing to do with what is being tested.
        self.run_id = uuid.uuid4().hex[:12]
        self.created: list[str] = []

    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def tearDown(self):
        if not self.created:
            return
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM audit_events WHERE target_id = ANY(%s)", (self.created,)
                )
                cur.execute(
                    "DELETE FROM sync_changes WHERE entity_id = ANY(%s)", (self.created,)
                )
                cur.execute(
                    "DELETE FROM client_id_mappings WHERE entity_id::text = ANY(%s)",
                    (self.created,),
                )
                cur.execute("DELETE FROM movies WHERE id::text = ANY(%s)", (self.created,))
            conn.commit()

    def _push(self, conn, payload, *, name, batch_ctx=None):
        result = next_app.apply_movie_upsert(
            conn,
            client_id=f"{PREFIX}-{name}",
            idem_key=f"{PREFIX}-{self.run_id}-{name}",
            mutation={
                "clientMutationId": f"{self.run_id}-{name}",
                "clientEntityId": f"{PREFIX}-{self.run_id}-{name}",
                "payload": payload,
            },
            batch_ctx=batch_ctx,
        )
        conn.commit()
        self.created.append(str(result["entityId"]))
        return result

    def test_a_synced_edit_names_the_device_the_person_and_the_fields(self):
        batch = next_app.SyncBatchContext()
        batch.actor = {"username": "helmer", "role": "owner"}
        with self.connect() as conn:
            result = self._push(
                conn,
                {"title": "Traced", "format": "4k_uhd"},
                name="iphone",
                batch_ctx=batch,
            )
            entries = next_app.movie_change_history(conn, result["entityId"])

        self.assertEqual(len(entries), 1)
        entry = entries[0]
        # The three facts that make the row answer "who changed this": which
        # installation pushed it, which account it was signed in as, and which
        # fields the presence-keyed body actually carried.
        self.assertEqual(entry["clientId"], f"{PREFIX}-iphone")
        self.assertEqual(entry["actor"], "helmer")
        self.assertEqual(entry["fields"], ["format", "title"])
        self.assertEqual(entry["source"], "sync")

    def test_a_push_outside_a_request_still_records(self):
        """`apply_movie_upsert` runs in a worker and in this test with no Flask
        request in scope. An audit trail that raises there is one that stops
        existing exactly when something unusual is happening, so the
        request-derived columns are simply left empty."""
        with self.connect() as conn:
            result = self._push(conn, {"title": "Headless"}, name="headless")
            entries = next_app.movie_change_history(conn, result["entityId"])
        self.assertEqual(len(entries), 1)
        self.assertIsNone(entries[0]["actor"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
