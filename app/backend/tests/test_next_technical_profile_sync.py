"""The technical profile on the sync wire (sync-contract 1.5, §4.7).

Before this, a track edited in the PWA reached no client. The mobile sync
surface was the `movies` table in both directions while this data lives in
`movie_technical_specs`, so there was neither a producer nor a consumer.

These tests pin both halves of the round trip and, more importantly, the three
rules that fail silently when they are wrong: an absent key preserves, an
explicit empty list clears, and a pre-contract client's plain language strings
must not overwrite structured tracks.
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
except ModuleNotFoundError:
    psycopg = None
    dict_row = None

from app.backend import next_app


DATABASE_URL = os.environ.get("DATABASE_URL")


class TechnicalSyncEditFilterTests(unittest.TestCase):
    """`movie_technical_sync_edits` — the wire-only restriction, no database."""

    def test_structured_tracks_pass_through(self):
        edits = next_app.movie_technical_sync_edits(
            {
                "audioTracks": [
                    {"languageCode": "en", "codec": "dolby_truehd", "channels": "7.1"}
                ],
                "subtitles": [{"languageCode": "en", "subtitleType": "sdh"}],
            }
        )
        self.assertEqual(edits["audio_tracks"][0]["codec"], "dolby_truehd")
        self.assertEqual(edits["subtitles"][0]["subtitleType"], "sdh")

    def test_a_plain_language_list_is_ignored_rather_than_stored_as_legacy_text(self):
        """An Android build from before §4.7 pushes `audioTracks: ["en", "nl"]`.

        Accepting that would replace the structured tracks the PWA just wrote
        with two bare strings — a downgrade performed by the device that knows
        least. Treated as "no opinion" instead.
        """
        edits = next_app.movie_technical_sync_edits({"audioTracks": ["en", "nl"]})
        self.assertNotIn("audio_tracks", edits)

    def test_an_explicitly_empty_list_still_clears(self):
        """The only way a user removes the last track."""
        edits = next_app.movie_technical_sync_edits({"audioTracks": [], "subtitles": []})
        self.assertEqual(edits["audio_tracks"], [])
        self.assertEqual(edits["subtitles"], [])

    def test_an_absent_key_produces_no_edit(self):
        self.assertEqual(next_app.movie_technical_sync_edits({"title": "Dune"}), {})

    def test_a_mixed_list_keeps_the_structured_entries(self):
        edits = next_app.movie_technical_sync_edits(
            {"subtitles": ["English", {"languageCode": "nl", "subtitleType": "forced"}]}
        )
        self.assertEqual(len(edits["subtitles"]), 1)
        self.assertEqual(edits["subtitles"][0]["languageCode"], "nl")

    def test_the_video_fields_are_carried_unchanged(self):
        edits = next_app.movie_technical_sync_edits(
            {
                "hdrFormats": ["hdr10", "dolby_vision"],
                "aspectRatios": ["2.39:1"],
                "discRegions": ["A", "B"],
                "packaging": ["steelbook"],
                "carrierType": "steelbook",
                "videoResolution": "2160p",
                "videoCodecs": ["hevc"],
            }
        )
        self.assertEqual(edits["hdr"], ["hdr10", "dolby_vision"])
        self.assertEqual(edits["screen_ratios"], ["2.39:1"])
        self.assertEqual(edits["regions"], ["A", "B"])
        self.assertEqual(edits["video_resolution"], "2160p")
        self.assertEqual(edits["video_codecs"], ["hevc"])
        # `packaging` is the one field that is *not* carried through: it is a
        # derived mirror the server recomputes from the axes, and a client may
        # not write it (sync-contract §4.7a). The carrier in the same body is
        # what drives that recomputation.
        self.assertNotIn("packaging", edits)
        self.assertEqual(edits["carrier_type"], "steelbook")
        self.assertTrue(edits["_derive_packaging"])

    def test_an_unknown_codec_survives(self):
        """MovieVault keeps an unrecognised member raw rather than dropping the
        record; the sync path must not be stricter than the feed."""
        edits = next_app.movie_technical_sync_edits(
            {"audioTracks": [{"languageCode": "en", "codec": "dolby_something_new"}]}
        )
        self.assertEqual(edits["audio_tracks"][0]["codec"], "dolby_something_new")


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class TechnicalProfileSyncPostgresTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def _insert_bare_movie(self, conn, *, title="Track Sync Test Movie") -> uuid.UUID:
        movie_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO movies (id, public_id, title, sort_title) VALUES (%s, %s, %s, %s)",
                (movie_id, f"track-sync-test-{movie_id}", title, title),
            )
        conn.commit()
        return movie_id

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM movies WHERE public_id LIKE 'track-sync-test-%'")
            conn.commit()

    def _write(self, conn, movie_id: uuid.UUID, body: dict) -> None:
        """The sync write path, as `apply_movie_upsert` performs it."""
        with conn.cursor() as cur:
            next_app.upsert_movie_technical_edits(
                cur, movie_id, next_app.movie_payload_fields(body)["technical_edits"]
            )
        conn.commit()

    # ---- Reading: bootstrap and delta ----

    def test_the_sync_entity_carries_the_technical_profile(self):
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            self._write(
                conn,
                movie_id,
                {
                    "audioTracks": [
                        {
                            "languageCode": "en",
                            "codec": "dolby_truehd",
                            "channels": "7.1",
                            "immersiveFormat": "dolby_atmos",
                        }
                    ],
                    "subtitles": [{"languageCode": "en", "subtitleType": "sdh"}],
                    "hdrFormats": ["hdr10", "dolby_vision"],
                    "discRegions": ["B"],
                    "videoResolution": "2160p",
                    "videoCodecs": ["hevc"],
                },
            )
            entity = next_app.movie_entity(conn, movie_id)

        self.assertEqual(entity["audio_tracks"][0]["immersiveFormat"], "dolby_atmos")
        self.assertEqual(entity["subtitles"][0]["subtitleType"], "sdh")
        # Wire names, not column names: `hdr` and `regions` are renamed to the
        # distribution-4 vocabulary the clients already speak.
        self.assertEqual(entity["hdr_formats"], ["hdr10", "dolby_vision"])
        self.assertEqual(entity["disc_regions"], ["B"])
        self.assertEqual(entity["video_resolution"], "2160p")
        self.assertEqual(entity["video_codecs"], ["hevc"])

    def test_a_movie_without_a_specs_row_reports_empty_rather_than_omitting_the_keys(self):
        """An absent key means "no opinion" to a client. This payload *is* the
        server's opinion, so "no technical data" has to be expressible."""
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            entity = next_app.movie_entity(conn, movie_id)
        for key in ("audio_tracks", "subtitles", "hdr_formats", "disc_regions", "video_codecs"):
            with self.subTest(key=key):
                self.assertEqual(entity[key], [])
        self.assertIsNone(entity["video_resolution"])

    def test_the_bootstrap_listing_carries_it_too(self):
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            self._write(conn, movie_id, {"audioTracks": [{"languageCode": "fr", "codec": "dts"}]})
            rows = next_app.all_movie_entities(conn, limit=1000)
        match = next(row for row in rows if row["id"] == movie_id)
        self.assertEqual(match["audio_tracks"][0]["languageCode"], "fr")

    def test_the_batch_read_is_one_query_for_the_whole_page(self):
        """A bootstrap carries up to 1000 movies; a per-movie lookup would be
        1000 round trips. Same shape as attach_movie_genres."""
        with self.connect() as conn:
            first = self._insert_bare_movie(conn, title="Track Sync A")
            second = self._insert_bare_movie(conn, title="Track Sync B")
            self._write(conn, first, {"audioTracks": [{"languageCode": "en"}]})
            rows = [{"id": first}, {"id": second}]
            next_app.attach_movie_technical_specs(conn, rows)
        self.assertEqual(rows[0]["audio_tracks"][0]["languageCode"], "en")
        self.assertEqual(rows[1]["audio_tracks"], [])

    # ---- Writing: absent, empty, and the downgrade guard ----

    def test_an_omitted_key_does_not_clear_a_stored_track(self):
        """kotlinx omits defaults and Swift uses encodeIfPresent, so a client
        that never touched the field sends no key at all."""
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            self._write(conn, movie_id, {"audioTracks": [{"languageCode": "en", "codec": "dts"}]})
            self._write(conn, movie_id, {"title": "Renamed, nothing else"})
            entity = next_app.movie_entity(conn, movie_id)
        self.assertEqual(entity["audio_tracks"][0]["codec"], "dts")

    def test_an_explicitly_empty_list_clears_the_stored_track(self):
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            self._write(conn, movie_id, {"audioTracks": [{"languageCode": "en"}]})
            self._write(conn, movie_id, {"audioTracks": []})
            entity = next_app.movie_entity(conn, movie_id)
        self.assertEqual(entity["audio_tracks"], [])

    def test_a_pre_contract_client_cannot_downgrade_structured_tracks(self):
        """The rollout case: the server ships before the clients, so an older
        Android build keeps pushing language strings for a while."""
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            self._write(
                conn,
                movie_id,
                {"audioTracks": [{"languageCode": "en", "codec": "dolby_truehd"}]},
            )
            self._write(conn, movie_id, {"audioTracks": ["en", "nl"]})
            entity = next_app.movie_entity(conn, movie_id)
        self.assertEqual(len(entity["audio_tracks"]), 1)
        self.assertEqual(entity["audio_tracks"][0]["codec"], "dolby_truehd")


if __name__ == "__main__":
    unittest.main()
