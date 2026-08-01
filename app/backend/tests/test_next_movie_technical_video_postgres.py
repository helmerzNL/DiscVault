import os
import pathlib
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
MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations_next"


class VideoListMigrationSourceTests(unittest.TestCase):
    """Data preservation cannot be tested after the fact - once 055 has run, the
    original scalars are gone. Assert the migration text itself instead."""

    def test_055_preserves_existing_scalars_as_one_element_arrays(self):
        sql = (MIGRATIONS / "055_movie_technical_specs_video_lists.sql").read_text()
        for column in ("hdr", "screen_ratios"):
            with self.subTest(column=column):
                self.assertIn(f"ALTER COLUMN {column} TYPE jsonb USING", sql)
        # Not a bare cast: a scalar must survive as ["HDR10"], and NULL/'' must
        # land on the empty-list convention every sibling column uses.
        self.assertEqual(sql.count("jsonb_build_array"), 2)
        self.assertEqual(sql.count("THEN '[]'::jsonb"), 2)
        self.assertEqual(sql.count("SET DEFAULT '[]'::jsonb"), 2)
        self.assertEqual(sql.count("SET NOT NULL"), 2)

    def test_055_does_not_try_to_split_a_legacy_comma_string(self):
        """Splitting "1.85:1, 2.39:1" here would be an irreversible rewrite done
        sight unseen. It happens later, in normalize_list_field, where the user
        can see and correct it."""
        sql = (MIGRATIONS / "055_movie_technical_specs_video_lists.sql").read_text()
        self.assertNotIn("string_to_array", sql)
        self.assertNotIn("regexp_split", sql)


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class MovieTechnicalVideoPostgresTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def _insert_bare_movie(self, conn, *, title="Video Fields Test Movie") -> uuid.UUID:
        movie_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movies (id, public_id, title, sort_title)
                VALUES (%s, %s, %s, %s)
                """,
                (movie_id, f"video-fields-test-{movie_id}", title, title),
            )
        conn.commit()
        return movie_id

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM movies WHERE public_id LIKE 'video-fields-test-%'")
            conn.commit()

    def _column_type(self, cur, column: str) -> str:
        cur.execute(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_name = 'movie_technical_specs' AND column_name = %s
            """,
            (column,),
        )
        row = cur.fetchone()
        self.assertIsNotNone(row, f"{column} column is missing")
        return row["data_type"]

    def _specs(self, conn, movie_id: uuid.UUID) -> dict:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT hdr, screen_ratios, regions, video_resolution, video_codecs"
                " FROM movie_technical_specs WHERE movie_id = %s",
                (movie_id,),
            )
            return cur.fetchone()

    def _apply(self, conn, movie_id: uuid.UUID, body: dict) -> None:
        with conn.cursor() as cur:
            next_app.upsert_movie_technical_edits(
                cur, movie_id, next_app.movie_technical_edits(body)
            )
        conn.commit()

    def test_the_widened_and_new_columns_have_the_expected_types(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                for column in ("hdr", "screen_ratios", "video_codecs"):
                    with self.subTest(column=column):
                        self.assertEqual(self._column_type(cur, column), "jsonb")
                # A disc has exactly one resolution, so this one stays scalar.
                self.assertEqual(self._column_type(cur, "video_resolution"), "text")

    def test_a_new_row_defaults_the_list_columns_to_an_empty_array(self):
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            self._apply(conn, movie_id, {"videoResolution": "2160p"})
            specs = self._specs(conn, movie_id)
        self.assertEqual(specs["hdr"], [])
        self.assertEqual(specs["screen_ratios"], [])
        self.assertEqual(specs["video_codecs"], [])
        self.assertEqual(specs["video_resolution"], "2160p")

    def test_hdr_round_trips_as_a_multi_value_list(self):
        """A 4K disc is routinely HDR10 *and* Dolby Vision - the case the scalar
        column could not represent."""
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            self._apply(conn, movie_id, {"hdr": ["HDR10", "Dolby Vision"]})
            self.assertEqual(self._specs(conn, movie_id)["hdr"], ["HDR10", "Dolby Vision"])

    def test_comma_separated_text_still_works_for_the_widened_columns(self):
        """The untouched edit form posts a plain text input, so this is what keeps
        the old UI round-tripping across the widening."""
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            self._apply(
                conn,
                movie_id,
                {"hdr": "HDR10, Dolby Vision", "screenRatio": "1.85:1, 2.39:1"},
            )
            specs = self._specs(conn, movie_id)
        self.assertEqual(specs["hdr"], ["HDR10", "Dolby Vision"])
        self.assertEqual(specs["screen_ratios"], ["1.85:1", "2.39:1"])

    def test_regions_and_video_codecs_round_trip(self):
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            self._apply(
                conn,
                movie_id,
                {"discRegions": ["A", "B"], "videoCodecs": ["hevc", "h264"]},
            )
            specs = self._specs(conn, movie_id)
        self.assertEqual(specs["regions"], ["A", "B"])
        self.assertEqual(specs["video_codecs"], ["hevc", "h264"])

    def test_an_untouched_column_is_left_alone(self):
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            self._apply(conn, movie_id, {"hdr": ["HDR10"]})
            self._apply(conn, movie_id, {"videoCodecs": ["av1"]})
            specs = self._specs(conn, movie_id)
        self.assertEqual(specs["hdr"], ["HDR10"])
        self.assertEqual(specs["video_codecs"], ["av1"])

    def test_an_explicit_empty_list_clears_the_column(self):
        """Distinct from the merge pipeline, where an empty list means "no opinion".
        A user emptying a field in the edit form does mean to empty it."""
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            self._apply(conn, movie_id, {"hdr": ["HDR10"]})
            self._apply(conn, movie_id, {"hdr": []})
            self.assertEqual(self._specs(conn, movie_id)["hdr"], [])


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class MovieTechnicalTrackPostgresTests(unittest.TestCase):
    """Audio tracks and subtitles hold a *union* at rest: canonical objects from
    MovieVault alongside free text entered by hand or imported years ago."""

    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def _insert_bare_movie(self, conn) -> uuid.UUID:
        movie_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO movies (id, public_id, title, sort_title) VALUES (%s, %s, %s, %s)",
                (movie_id, f"track-union-test-{movie_id}", "Track Union", "Track Union"),
            )
        conn.commit()
        return movie_id

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM movies WHERE public_id LIKE 'track-union-test-%'")
            conn.commit()

    def _apply(self, conn, movie_id: uuid.UUID, body: dict) -> None:
        with conn.cursor() as cur:
            next_app.upsert_movie_technical_edits(
                cur, movie_id, next_app.movie_technical_edits(body)
            )
        conn.commit()

    def _tracks(self, conn, movie_id: uuid.UUID) -> dict:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT audio_tracks, subtitles FROM movie_technical_specs WHERE movie_id = %s",
                (movie_id,),
            )
            return cur.fetchone()

    def test_a_mixed_array_survives_a_save_byte_for_byte(self):
        """The core promise: a user who never opens the audio editor loses nothing."""
        mixed = [
            "English (DTS-HD MA 5.1)",
            {
                "languageCode": "nl",
                "codec": "dolby_digital",
                "channels": "5.1",
                "immersiveFormat": None,
            },
        ]
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            self._apply(conn, movie_id, {"audioTracks": mixed})
            self.assertEqual(self._tracks(conn, movie_id)["audio_tracks"], mixed)

    def test_two_subtitle_variants_of_one_language_both_persist(self):
        subtitles = [
            {"languageCode": "en", "subtitleType": "full"},
            {"languageCode": "en", "subtitleType": "sdh"},
        ]
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            self._apply(conn, movie_id, {"subtitles": subtitles})
            self.assertEqual(self._tracks(conn, movie_id)["subtitles"], subtitles)

    def test_a_bare_language_code_is_stored_as_the_full_variant(self):
        with self.connect() as conn:
            movie_id = self._insert_bare_movie(conn)
            self._apply(conn, movie_id, {"subtitles": ["en", "nl"]})
            self.assertEqual(
                self._tracks(conn, movie_id)["subtitles"],
                [
                    {"languageCode": "en", "subtitleType": "full"},
                    {"languageCode": "nl", "subtitleType": "full"},
                ],
            )


class TrackNormalizerTests(unittest.TestCase):
    def setUp(self):
        from app.backend import next_metadata

        self.metadata = next_metadata

    def test_a_structured_audio_track_is_normalized_to_all_four_keys(self):
        self.assertEqual(
            self.metadata.normalize_audio_track_entry(
                {"language_code": " EN ", "codec": "dts_hd_ma", "channels": "5.1"}
            ),
            {
                "languageCode": "en",
                "codec": "dts_hd_ma",
                "channels": "5.1",
                "immersiveFormat": None,
            },
        )

    def test_legacy_free_text_passes_through_untouched(self):
        self.assertEqual(
            self.metadata.normalize_audio_track_entry("English (DTS-HD MA 5.1)"),
            "English (DTS-HD MA 5.1)",
        )

    def test_an_unknown_codec_is_accepted_rather_than_rejected(self):
        """The edit form is filled *from stored data*. Rejecting a forward-compat
        value the feed deliberately stored raw would make that movie permanently
        unsaveable through the UI."""
        entry = self.metadata.normalize_audio_track_entry(
            {"languageCode": "en", "codec": "some_future_codec"}
        )
        self.assertEqual(entry["codec"], "some_future_codec")

    def test_shape_errors_are_still_rejected(self):
        for bad in (
            {"languageCode": "en", "codec": "dts", "forced": True},
            {"languageCode": "en_US", "codec": "dts"},
            {"codec": "dts"},
            42,
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.metadata.normalize_audio_track_entry(bad)

    def test_a_track_may_name_a_language_and_no_codec(self):
        """Changed 2026-08-01. A codec used to be mandatory, which held while the
        only writers were the feed (always has one) and the PWA form (silently
        dropped a track without one). A title saved before the structured columns
        existed knows a language and nothing else, and every hand-entered track
        passes through that state - so a codec-less track now stores rather than
        422-ing the whole sync mutation."""
        for entry in ({"languageCode": "en"}, {"languageCode": "en", "codec": ""}):
            with self.subTest(entry=entry):
                self.assertEqual(
                    self.metadata.normalize_audio_track_entry(entry),
                    {"languageCode": "en", "codec": "", "channels": None, "immersiveFormat": None},
                )

    def test_subtitle_text_that_is_not_a_subtag_stays_legacy_text(self):
        self.assertEqual(
            self.metadata.normalize_subtitle_entry("English forced"), "English forced"
        )

    def test_duplicate_entries_are_collapsed(self):
        tracks = self.metadata.normalize_audio_tracks(
            [
                {"languageCode": "en", "codec": "dts"},
                {"languageCode": "en", "codec": "dts"},
                "English",
                "English",
            ]
        )
        self.assertEqual(len(tracks), 2)

    def test_more_than_fifty_entries_are_rejected(self):
        with self.assertRaises(ValueError):
            self.metadata.normalize_subtitles(["en"] * 51)

    def test_a_plain_string_still_routes_through_the_historical_splitter(self):
        """API and mobile clients post comma-separated text; that must keep working."""
        self.assertEqual(
            self.metadata.normalize_subtitles("en, nl"),
            [
                {"languageCode": "en", "subtitleType": "full"},
                {"languageCode": "nl", "subtitleType": "full"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
