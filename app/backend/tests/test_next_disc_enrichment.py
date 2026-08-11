"""The catalogue's disc breakdown fills emptiness and nothing else.

C7b, the last leg of the disc programme: MovieVault publishes a per-disc
breakdown on distribution-6, DiscVault mirrors it verbatim, and until now
nothing carried it onto the release. A user whose shelf had no discs saw none,
even though the catalogue knew all four.

The rule the whole leg turns on is a precedence one, and it only has teeth in
one direction: **a user's own breakdown outranks the catalogue's, always**.
They are holding the box. So this fills only a release with no discs at all --
not "no matching disc", not "fewer discs than the feed says". One disc entered
by hand is an answer.

The mapping half is tested against the plugin's real output rather than a
fixture, because the last disc converter to guess at key names dropped every
field whose spellings differed and looked populated doing it.
"""

from __future__ import annotations

import importlib.util
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

from app.backend import next_app, next_discs, next_metadata

_PLUGIN_PATH = os.path.join(BACKEND_DIR, "next_plugins", "movievault_v2", "plugin.py")
_spec = importlib.util.spec_from_file_location("movievault_v2_plugin_c7b", _PLUGIN_PATH)
plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plugin)

DATABASE_URL = os.environ.get("DATABASE_URL")
PREFIX = "disc-enrichment-test"


class FeedToDiscShapeTests(unittest.TestCase):
    """The feed's disc keys are not DiscVault's, and three of them differ."""

    RECORD = {
        "discs": [
            {
                "position": 2,
                "discType": "bluray",
                "discRole": "bonus",
                "label": "Bonus",
                "hdrFormats": ["hdr10"],
                "aspectRatios": ["1.85:1"],
                "discRegions": ["B"],
                "subtitles": [{"languageCode": "nl", "subtitleType": "full"}],
            },
            {
                "position": 1,
                "discType": "uhd_bluray",
                "discRole": "feature",
                "videoResolution": "2160p",
                "videoCodecs": ["hevc"],
                "audioTracks": [{"languageCode": "en", "codec": "dolby_truehd"}],
            },
        ]
    }

    def test_the_three_renamed_keys_survive(self):
        """`hdrFormats`, `aspectRatios` and `discRegions` upstream are `hdr`,
        `screenRatios` and `regions` here. Each side named its own field for a
        reason; the mapping is where they meet."""
        bonus = plugin._discs(self.RECORD)[1]
        self.assertEqual(bonus["hdr"], ["hdr10"])
        self.assertEqual(bonus["screenRatios"], ["1.85:1"])
        self.assertEqual(bonus["regions"], ["B"])

    def test_every_key_it_emits_is_one_the_writer_reads(self):
        """Checked against `MOVIE_DISC_WIRE_KEYS` rather than a list written
        here — a hand-written expectation agrees with whoever wrote it."""
        known = set(next_app.MOVIE_DISC_WIRE_KEYS.values())
        for disc in plugin._discs(self.RECORD):
            self.assertTrue(set(disc).issubset(known), f"unknown keys: {set(disc) - known}")

    def test_order_comes_from_position_and_position_itself_does_not_travel(self):
        """DiscVault orders by list order and the feed orders by `position`.
        Carrying both would state one thing twice and let them disagree."""
        discs = plugin._discs(self.RECORD)
        self.assertEqual([disc["discType"] for disc in discs], ["uhd_bluray", "bluray"])
        self.assertNotIn("position", discs[0])

    def test_a_feed_with_no_discs_says_nothing(self):
        for record in ({}, {"discs": None}, {"discs": []}, {"discs": "four"}):
            with self.subTest(record=record):
                self.assertEqual(plugin._discs(record), [])


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class FillOnlyEmptinessTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)

    def setUp(self):
        self.movie_id = uuid.uuid4()
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO movies (id, public_id, title, media_type) "
                    "VALUES (%s, %s, %s, 'MOVIE')",
                    (self.movie_id, f"{PREFIX}-{self.movie_id}", f"{PREFIX} film"),
                )
            conn.commit()

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM movie_discs WHERE movie_id = %s", (self.movie_id,))
                cur.execute("DELETE FROM movies WHERE id = %s", (self.movie_id,))
            conn.commit()

    def _discs(self, conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT disc_type, label FROM movie_discs WHERE movie_id = %s "
                "ORDER BY sort_order",
                (self.movie_id,),
            )
            return [(str(row["disc_type"]), row["label"]) for row in cur.fetchall()]

    def test_an_empty_release_is_filled_from_the_catalogue(self):
        stated = [{"discType": "uhd_bluray", "label": "Feature"}, {"discType": "bluray"}]
        with self.connect() as conn:
            applied = next_metadata.apply_movie_disc_enrichment(conn, self.movie_id, stated)
            conn.commit()
            self.assertEqual(applied, {"created": 2, "source": "catalogue"})
            self.assertEqual(
                self._discs(conn), [("uhd_bluray", "Feature"), ("bluray", None)]
            )

    def test_a_release_with_one_disc_of_its_own_is_left_alone(self):
        """The precedence rule, and the reason it is "any disc" rather than
        "every disc": a person who entered one disc has answered the question,
        and a refresh that adds three more has reorganised their answer."""
        with self.connect() as conn:
            with conn.cursor() as cur:
                # Through `discs_payload`, like every real caller: the writer
                # is entitled to a normalised entry, and a fixture that hands
                # it a partial dict is testing a shape the application never
                # produces.
                next_app.apply_movie_discs(
                    cur,
                    self.movie_id,
                    next_discs.discs_payload({"discs": [{"discType": "bluray", "label": "Mine"}]}),
                    media_type="MOVIE",
                )
            conn.commit()
            applied = next_metadata.apply_movie_disc_enrichment(
                conn,
                self.movie_id,
                [{"discType": "uhd_bluray", "label": "Feature"}, {"discType": "bluray"}],
            )
            conn.commit()
            self.assertIsNone(applied)
            self.assertEqual(self._discs(conn), [("bluray", "Mine")])

    def test_a_catalogue_with_nothing_to_say_cannot_empty_a_shelf(self):
        """Silence is not disagreement — the same posture the series link takes,
        and the reason a feed without discs cannot delete a breakdown."""
        with self.connect() as conn:
            with conn.cursor() as cur:
                # Through `discs_payload`, like every real caller: the writer
                # is entitled to a normalised entry, and a fixture that hands
                # it a partial dict is testing a shape the application never
                # produces.
                next_app.apply_movie_discs(
                    cur,
                    self.movie_id,
                    next_discs.discs_payload({"discs": [{"discType": "bluray", "label": "Mine"}]}),
                    media_type="MOVIE",
                )
            conn.commit()
            for stated in (None, [], "four discs"):
                with self.subTest(stated=stated):
                    self.assertIsNone(
                        next_metadata.apply_movie_disc_enrichment(conn, self.movie_id, stated)
                    )
            self.assertEqual(self._discs(conn), [("bluray", "Mine")])

    def test_the_whole_chain_from_a_feed_record_to_stored_rows(self):
        """The mapping and the write together, because each was correct alone
        the last time a disc breakdown failed to arrive."""
        record = {
            "discs": [
                {"position": 1, "discType": "uhd_bluray", "label": "Feature",
                 "hdrFormats": ["dolby_vision"], "discRegions": ["FREE"]},
                {"position": 2, "discType": "bluray", "label": "Extras"},
            ]
        }
        with self.connect() as conn:
            next_metadata.apply_movie_disc_enrichment(
                conn, self.movie_id, plugin._discs(record)
            )
            conn.commit()
            self.assertEqual(
                self._discs(conn), [("uhd_bluray", "Feature"), ("bluray", "Extras")]
            )
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT hdr, regions FROM movie_discs WHERE movie_id = %s "
                    "ORDER BY sort_order LIMIT 1",
                    (self.movie_id,),
                )
                row = cur.fetchone()
            self.assertEqual(list(row["hdr"]), ["dolby_vision"])
            self.assertEqual(list(row["regions"]), ["FREE"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
