"""Saying "I have none" without saying "I have not seen yours yet".

Presence-keying puts two rules on one key: absent keeps, empty clears. That
leaves no way to distinguish a client with nothing from a client that has not
bootstrapped the field yet — and the second one must not send `[]`, because push
runs before pull and it would wipe a list it never saw. So it sends absent, the
key is spent, and **removing the last disc, the last audio track or the last
note never propagates**.

`clears` names the field instead. Sync-contract §4.10.

Two of the cases below are about refusals rather than clearing, and they are the
reason the feature is worth having at all: a mechanism that exists to stop a
removal being silently lost must not itself be able to silently do nothing.

The second half of this file pins something adjacent that was unspecified until
contract 1.23: what a *missing key inside a disc entry* means. The answer is
"clear", which makes the contract's demand that a writer spell out every key a
necessity rather than a precaution — and it is worth a test precisely because
nothing in the code announces it.
"""

from __future__ import annotations

import os
import sys
import unittest
import uuid


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
repo_root = os.path.abspath(os.path.join(BACKEND_DIR, "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:  # pragma: no cover - depends on environment
    psycopg = None

from app.backend import next_app, next_discs

DATABASE_URL = os.environ.get("DATABASE_URL")
PREFIX = "sync-clears-test"


class ClearsPayloadTests(unittest.TestCase):
    """What `clears` does to a payload, before any database is involved."""

    def test_a_named_field_becomes_its_empty_value(self):
        payload = next_app.movie_clears(
            {"clears": ["discs", "notes"]}, {"title": "Alien"}
        )
        self.assertEqual(payload, {"title": "Alien", "discs": [], "notes": ""})

    def test_the_empty_value_matches_the_shape_of_the_field(self):
        payload = next_app.movie_clears(
            {"clears": ["audioTracks", "subtitles", "location", "releaseTitle", "sortTitle"]},
            {},
        )
        self.assertEqual(
            payload,
            {
                "audioTracks": [],
                "subtitles": [],
                "location": "",
                "releaseTitle": "",
                "sortTitle": "",
            },
        )

    def test_clears_is_itself_presence_keyed(self):
        """A client that does not know the key changes nothing, which is what
        lets this ship without every client learning it at once."""
        payload = {"title": "Alien"}
        self.assertEqual(next_app.movie_clears({}, payload), payload)
        self.assertEqual(next_app.movie_clears({"clears": None}, payload), payload)

    def test_an_empty_clears_list_clears_nothing(self):
        self.assertEqual(next_app.movie_clears({"clears": []}, {"title": "A"}), {"title": "A"})

    def test_an_unknown_field_is_refused_by_name(self):
        """The refusal that matters most. A typo that quietly cleared nothing
        would reproduce the exact failure this mechanism exists to fix, wearing
        a different hat."""
        with self.assertRaises(next_app.NextApiError) as caught:
            next_app.movie_clears({"clears": ["disks"]}, {})
        self.assertIn("disks", str(caught.exception))

    def test_content_rating_is_not_clearable(self):
        """Excluded deliberately: the `||` merge cannot remove a key, and a
        clear could not say which country's rating it meant."""
        with self.assertRaises(next_app.NextApiError):
            next_app.movie_clears({"clears": ["contentRating"]}, {})

    def test_clearing_and_stating_the_same_field_is_contradictory(self):
        with self.assertRaises(next_app.NextApiError) as caught:
            next_app.movie_clears({"clears": ["notes"]}, {"notes": "still here"})
        self.assertIn("notes", str(caught.exception))

    def test_the_conflict_is_caught_under_either_spelling(self):
        """`sortTitle` and `sort_title` are one field. Checking only the
        camelCase spelling would let the snake_case one through, and the payload
        would then both state and clear it."""
        for spelling in ("sortTitle", "sort_title"):
            with self.subTest(spelling=spelling):
                with self.assertRaises(next_app.NextApiError):
                    next_app.movie_clears({"clears": ["sortTitle"]}, {spelling: "Alien"})

    def test_a_clears_that_is_not_a_list_is_refused(self):
        for raw in ("discs", 3, {"discs": True}):
            with self.subTest(raw=raw):
                with self.assertRaises(next_app.NextApiError):
                    next_app.movie_clears({"clears": raw}, {})

    def test_the_original_payload_is_not_mutated(self):
        """The caller's dict is theirs. A mutation applied in place would reach
        the audit's `changedFields` and the idempotency key differently
        depending on call order."""
        payload = {"title": "Alien"}
        next_app.movie_clears({"clears": ["notes"]}, payload)
        self.assertEqual(payload, {"title": "Alien"})


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class ClearsOverSyncTests(unittest.TestCase):
    """The acceptance case: the last one of something can be removed."""

    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)

    def setUp(self):
        self.client_id = f"{PREFIX}-{uuid.uuid4()}"
        self.entity_id = None

    def tearDown(self):
        if self.entity_id is None:
            return
        with self.connect() as conn:
            with conn.cursor() as cur:
                for sql in (
                    "DELETE FROM movie_discs WHERE movie_id=%s",
                    "DELETE FROM movie_technical_specs WHERE movie_id=%s",
                    "DELETE FROM movies WHERE id=%s",
                ):
                    cur.execute(sql, (self.entity_id,))
                cur.execute("DELETE FROM sync_changes WHERE entity_id=%s", (str(self.entity_id),))
            conn.commit()

    def _push(self, conn, payload, clears=None):
        mutation = {
            "clientMutationId": str(uuid.uuid4()),
            "clientEntityId": str(uuid.uuid4()),
            "payload": payload,
        }
        if self.entity_id is not None:
            mutation["entityId"] = str(self.entity_id)
        if clears is not None:
            mutation["clears"] = clears
        result = next_app.apply_movie_upsert(
            conn, client_id=self.client_id, idem_key=str(uuid.uuid4()), mutation=mutation
        )
        conn.commit()
        self.entity_id = result["entityId"]
        return result

    def _stored(self, conn):
        entity = next_app.movie_entity(conn, self.entity_id) or {}
        with conn.cursor() as cur:
            cur.execute(
                "SELECT notes FROM movies WHERE id=%s", (self.entity_id,)
            )
            row = cur.fetchone() or {}
        return entity, row

    def test_the_last_disc_and_the_last_note_can_both_be_removed(self):
        with self.connect() as conn:
            self._push(
                conn,
                {
                    "title": f"{PREFIX} film",
                    "mediaType": "MOVIE",
                    "notes": "Bought in Utrecht",
                    "discs": [{"discType": "bluray", "label": "Feature"}],
                },
            )
            entity, row = self._stored(conn)
            self.assertEqual(len(entity["discs"]), 1)
            self.assertEqual(row["notes"], "Bought in Utrecht")

            # The push a client makes when the user deletes the only disc and
            # the note. Nothing about it says `[]`.
            self._push(conn, {"title": f"{PREFIX} film"}, clears=["discs", "notes"])
            entity, row = self._stored(conn)

        self.assertEqual(entity["discs"], [])
        # The empty string rather than NULL: §4.8's clear for a scalar *is* the
        # explicit empty string, and `clears` produces the same value a client
        # sending it by hand would. The edit form clears to NULL, so the column
        # holds two spellings of empty -- pre-existing, noted here rather than
        # papered over, and both read as empty everywhere that reads it.
        self.assertEqual(row["notes"], "")

    def test_a_push_that_says_nothing_still_keeps_everything(self):
        """The rule `clears` must not weaken. Absent is still absent."""
        with self.connect() as conn:
            self._push(
                conn,
                {
                    "title": f"{PREFIX} film",
                    "mediaType": "MOVIE",
                    "notes": "Kept",
                    "discs": [{"discType": "bluray", "label": "Feature"}],
                },
            )
            self._push(conn, {"title": f"{PREFIX} renamed"})
            entity, row = self._stored(conn)

        self.assertEqual(len(entity["discs"]), 1)
        self.assertEqual(row["notes"], "Kept")

    def test_an_unknown_name_fails_the_whole_mutation(self):
        """Not "cleared nothing and carried on". The rest of the payload does
        not land either, so the client sees the refusal rather than a partial
        success it has no way to notice."""
        with self.connect() as conn:
            self._push(conn, {"title": f"{PREFIX} film", "mediaType": "MOVIE", "notes": "Kept"})
            with self.assertRaises(next_app.NextApiError):
                self._push(conn, {"title": f"{PREFIX} renamed"}, clears=["disks"])
            _, row = self._stored(conn)
        self.assertEqual(row["notes"], "Kept")


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class DiscEntryPresenceTests(unittest.TestCase):
    """Inside a disc entry, a missing key means CLEAR. Contract 1.23, §4.9.

    Unspecified until now, and it is the dangerous of the two readings: an entry
    is a whole replacement, so a writer must spell out every key it wants to
    keep. Swift and kotlinx omit `nil` by default, which makes the default
    encoder destructive here — exactly the opposite of list level, where
    omitting is the safe choice.

    Pinned in the direction the code turned out to implement, so that changing
    it is a decision somebody makes rather than one that happens.
    """

    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)

    def setUp(self):
        self.movie_id = uuid.uuid4()
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO movies (id, public_id, title, media_type) "
                    "VALUES (%s, %s, %s, 'MOVIE')",
                    (self.movie_id, f"{PREFIX}-entry-{self.movie_id}", f"{PREFIX} film"),
                )
            conn.commit()

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM movie_discs WHERE movie_id=%s", (self.movie_id,))
                cur.execute(
                    "DELETE FROM movie_technical_specs WHERE movie_id=%s", (self.movie_id,)
                )
                cur.execute("DELETE FROM movies WHERE id=%s", (self.movie_id,))
            conn.commit()

    def _save(self, conn, discs):
        with conn.cursor() as cur:
            next_app.apply_movie_discs(
                cur, self.movie_id, next_discs.discs_payload({"discs": discs}), media_type="MOVIE"
            )
        conn.commit()

    def _disc(self, conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, label, notes, hdr, audio_tracks FROM movie_discs "
                "WHERE movie_id=%s ORDER BY sort_order LIMIT 1",
                (self.movie_id,),
            )
            return dict(cur.fetchone())

    def test_a_key_left_out_of_an_entry_is_cleared_not_kept(self):
        with self.connect() as conn:
            self._save(
                conn,
                [
                    {
                        "discType": "bluray",
                        "label": "Feature",
                        "notes": "Scratched",
                        "hdr": ["hdr10"],
                        "audioTracks": [{"languageCode": "en", "codec": "dts"}],
                    }
                ],
            )
            disc_id = str(self._disc(conn)["id"])
            # The same disc, re-sent by a writer that omitted everything it did
            # not change. Under "absent keeps" this is a no-op; under the rule
            # this file pins it empties four fields.
            self._save(conn, [{"id": disc_id, "discType": "bluray"}])
            disc = self._disc(conn)

        self.assertIsNone(disc["label"])
        self.assertIsNone(disc["notes"])
        self.assertEqual(list(disc["hdr"]), [])
        self.assertEqual(list(disc["audio_tracks"]), [])

    def test_writing_every_key_keeps_every_value(self):
        """The other half, and the one a client has to implement: spelling the
        keys out is what preserves them."""
        described = {
            "discType": "bluray",
            "label": "Feature",
            "notes": "Scratched",
            "hdr": ["hdr10"],
            "audioTracks": [{"languageCode": "en", "codec": "dts"}],
        }
        with self.connect() as conn:
            self._save(conn, [described])
            disc_id = str(self._disc(conn)["id"])
            self._save(conn, [{"id": disc_id, **described}])
            disc = self._disc(conn)

        self.assertEqual(disc["label"], "Feature")
        self.assertEqual(disc["notes"], "Scratched")
        self.assertEqual(list(disc["hdr"]), ["hdr10"])
        self.assertEqual(len(list(disc["audio_tracks"])), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
