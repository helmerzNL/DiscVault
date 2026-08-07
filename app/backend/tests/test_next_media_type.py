"""Media type: normalization, the ladder veto, and the local-only ownership rule.

The vocabulary is fixed by shipped clients (sync-contract.md §3b), so the tests
that matter here are about exactness rather than plumbing: an unrecognised value
must not become MOVIE, and a stored SHOW must survive a client that says nothing.
"""

from __future__ import annotations

import json
import os
import unittest

try:
    from ..dedup_identity import (
        MEDIA_TYPE_MOVIE,
        MEDIA_TYPE_SHOW,
        infer_media_type_from_title,
        media_type_conflicts,
        normalize_media_type,
    )
    from .. import next_metadata
    from .. import next_app
except ImportError:  # pragma: no cover - backend working-directory CI imports
    from dedup_identity import (
        MEDIA_TYPE_MOVIE,
        MEDIA_TYPE_SHOW,
        infer_media_type_from_title,
        media_type_conflicts,
        normalize_media_type,
    )
    import next_metadata
    import next_app


class NormalizeMediaTypeTests(unittest.TestCase):
    def test_movie_spellings_normalize_to_the_exact_wire_value(self):
        for value in ("MOVIE", "movie", "Movie", " movie ", "film", "FILM"):
            with self.subTest(value=value):
                self.assertEqual(normalize_media_type(value), MEDIA_TYPE_MOVIE)

    def test_show_spellings_normalize_to_the_exact_wire_value(self):
        for value in ("SHOW", "show", "tv", "TV", "tv_series", "tvseries",
                      "series", "tv show", "TV_SHOW"):
            with self.subTest(value=value):
                self.assertEqual(normalize_media_type(value), MEDIA_TYPE_SHOW)

    def test_absent_values_are_absent(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                self.assertIsNone(normalize_media_type(value))

    def test_an_unrecognised_value_is_absent_and_never_movie(self):
        """The server must not copy the clients' silent fallback.

        Android's fromStorage() falls back to MOVIE because a client has to
        render something. A server doing the same turns "I do not recognise
        this" into "this is a film", which is the exact mislabelling this field
        exists to prevent -- and it would then be able to veto merges with it.
        """
        for value in ("anime", "documentary", "SHOWS", "movies", "tv-series-2"):
            with self.subTest(value=value):
                self.assertIsNone(normalize_media_type(value))

    def test_output_is_only_ever_one_of_the_two_exact_strings(self):
        seen = {
            normalize_media_type(value)
            for value in ("movie", "film", "tv", "show", "series", "junk", None)
        }
        self.assertEqual(seen, {MEDIA_TYPE_MOVIE, MEDIA_TYPE_SHOW, None})


class InferMediaTypeFromTitleTests(unittest.TestCase):
    """Reading a series off the printed title, for sources that cannot say.

    Every source except MovieVault is blind to the distinction, so a barcode
    scan that resolves elsewhere arrives with no type at all. The disc itself
    often says what it is; this reads that and nothing more.
    """

    def test_a_season_or_series_marker_is_recognised(self):
        for title in (
            "Yellowstone: Season 1",
            "Fargo Season Two",
            "Twin Peaks - Seasons 1-2",
            "Band of Brothers: The Complete Series",
            "Het Bureau Seizoen 3",
            "Friends: De Complete Serie",
            "Volume 2",
        ):
            with self.subTest(title=title):
                self.assertEqual(infer_media_type_from_title(title), MEDIA_TYPE_SHOW)

    def test_it_never_answers_movie(self):
        """The half that keeps the contract's rule intact.

        A marker is evidence; its absence is not. Plenty of series boxes carry
        only the show's name, so answering MOVIE here would turn "no marker" into
        a confident wrong type -- and this field vetoes the identity ladder, so a
        wrong value silently makes a record unmergeable with its own duplicate.
        """
        for title in ("Dune: Part Two", "Heat", "Yellowstone", "", None, "   "):
            with self.subTest(title=title):
                self.assertIsNone(infer_media_type_from_title(title))

    def test_the_output_is_only_ever_show_or_nothing(self):
        seen = {
            infer_media_type_from_title(title)
            for title in ("Season 1", "The Complete Series", "Heat", None)
        }
        self.assertEqual(seen, {MEDIA_TYPE_SHOW, None})


class MediaTypeConflictTests(unittest.TestCase):
    def test_both_present_and_different_conflicts(self):
        self.assertTrue(media_type_conflicts("MOVIE", "SHOW"))
        self.assertTrue(media_type_conflicts("film", "tv"))

    def test_both_present_and_equal_does_not_conflict(self):
        self.assertFalse(media_type_conflicts("SHOW", "SHOW"))
        self.assertFalse(media_type_conflicts("tv", "SHOW"))

    def test_one_sided_absence_blocks_nothing(self):
        """A client from before this contract version omits the key entirely.

        That must stay inconclusive rather than becoming a veto, or upgrading
        the server would stop every pre-existing client from ever merging.
        """
        for left, right in ((None, "SHOW"), ("SHOW", None), ("", "MOVIE"), ("MOVIE", "  ")):
            with self.subTest(left=left, right=right):
                self.assertFalse(media_type_conflicts(left, right))

    def test_an_unrecognised_value_never_vetoes(self):
        self.assertFalse(media_type_conflicts("anime", "SHOW"))


class MediaTypeErrorMessageTests(unittest.TestCase):
    """Both handlers accept two spellings; the rejection must name both.

    A caller who sent `media_type` and is told only about `mediaType` has no way
    to tell whether the value or the key was wrong.
    """

    def test_the_edit_path_names_both_spellings(self):
        with self.assertRaises(next_app.NextApiError) as caught:
            next_app.movie_update_payload(
                {"title": "X", "media_type": "miniseries"}, existing={"title": "X"}
            )
        message = str(caught.exception)
        self.assertIn("mediaType", message)
        self.assertIn("media_type", message)

    def test_both_accepted_spellings_reach_the_same_rejection(self):
        for key in ("mediaType", "media_type"):
            with self.subTest(key=key):
                with self.assertRaises(next_app.NextApiError):
                    next_app.movie_update_payload(
                        {"title": "X", key: "anime"}, existing={"title": "X"}
                    )


class BarcodeFinderQueryShapeTests(unittest.TestCase):
    """The veto decides the query shape, so the shape is worth pinning.

    Scanning every barcode holder is only justified when a conflict is possible.
    With no incoming type nothing can be vetoed, so the finder must not page in
    candidates just to accept the first one.
    """

    class _RecordingConn:
        def __init__(self, rows):
            self.rows = rows
            self.sql = []

        def cursor(self):
            outer = self

            class _Cur:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *_):
                    return False

                def execute(self_inner, sql, params=None):
                    outer.sql.append(sql)

                def fetchall(self_inner):
                    return outer.rows

            return _Cur()

    def test_no_incoming_type_uses_the_single_row_query(self):
        conn = self._RecordingConn([{"id": "movie-1", "media_type": "MOVIE"}])
        next_app.find_movie_by_barcode_match(conn, "5051890000000")
        self.assertIn("LIMIT 1", conn.sql[0])

    def test_an_incoming_type_scans_so_a_conflict_cannot_hide_a_match(self):
        conn = self._RecordingConn([{"id": "movie-1", "media_type": "MOVIE"}])
        next_app.find_movie_by_barcode_match(
            conn, "5051890000000", incoming_media_type="SHOW"
        )
        self.assertNotIn("LIMIT 1", conn.sql[0])

    def test_an_unrecognised_incoming_type_cannot_veto_so_it_does_not_scan(self):
        conn = self._RecordingConn([{"id": "movie-1", "media_type": "MOVIE"}])
        next_app.find_movie_by_barcode_match(
            conn, "5051890000000", incoming_media_type="miniseries"
        )
        self.assertIn("LIMIT 1", conn.sql[0])

    def test_a_conflicting_first_row_still_does_not_hide_a_later_match(self):
        conn = self._RecordingConn(
            [
                {"id": "the-film", "media_type": "MOVIE"},
                {"id": "the-series", "media_type": "SHOW"},
            ]
        )
        found = next_app.find_movie_by_barcode_match(
            conn, "5051890000000", incoming_media_type="SHOW"
        )
        self.assertEqual(found, "the-series")


class MediaTypeOwnershipTests(unittest.TestCase):
    def test_media_type_is_provider_writable(self):
        """MovieVault may state the type, because a human states it there.

        This was local-only while nothing could tell a series from a film. It
        moved once MovieVault gained content.films.work_type, which an operator
        sets by hand -- a stated value rather than a guess. Nothing infers it on
        either side.
        """
        self.assertIn("media_type", next_metadata.METADATA_MAIN_FIELDS)

    def test_media_type_is_named_in_exactly_one_field_set(self):
        """Being in neither is the dangerous state, whichever way it is owned.

        apply_metadata_proposal skips any field missing from
        METADATA_MAIN_FIELDS silently, so a proposal would be accepted and the
        write would disappear with no error at all.
        """
        self.assertNotIn("media_type", next_metadata.METADATA_LOCAL_ONLY_FIELDS)

    def test_both_spellings_resolve_to_the_column_name(self):
        self.assertEqual(next_metadata.MOVIE_FIELD_ALIASES["mediaType"], "media_type")
        self.assertEqual(next_metadata.MOVIE_FIELD_ALIASES["media_type"], "media_type")


class MediaTypeProposalNormalisationTests(unittest.TestCase):
    """Whatever a plugin calls it, only MOVIE or SHOW may reach the column.

    ``movies.media_type`` carries a CHECK constraint, and the apply path builds
    one UPDATE out of every proposed field. So an unnormalised spelling does not
    merely drop its own value -- it fails the statement and loses the whole
    refresh, exactly like the `year` int-vs-text case documented beside it.
    """

    @staticmethod
    def _proposed(raw):
        result = next_metadata.canonicalize_plugin_result(
            "movievault_v2", "release.lookup", {"movie": {"title": "X", "mediaType": raw}}
        )
        return result.get("movieUpdates", {}).get("media_type")

    def test_every_dialect_converges_on_the_column_vocabulary(self):
        for raw, expected in (
            ("tv", MEDIA_TYPE_SHOW),
            ("SHOW", MEDIA_TYPE_SHOW),
            ("tv_series", MEDIA_TYPE_SHOW),
            ("movie", MEDIA_TYPE_MOVIE),
            ("MOVIE", MEDIA_TYPE_MOVIE),
            ("film", MEDIA_TYPE_MOVIE),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(self._proposed(raw), expected)

    def test_an_unknown_spelling_is_dropped_rather_than_guessed(self):
        for raw in ("miniseries", "anime", "documentary"):
            with self.subTest(raw=raw):
                self.assertIsNone(self._proposed(raw))

    def test_nothing_a_plugin_can_say_reaches_the_column_unnormalised(self):
        proposed = {
            self._proposed(raw)
            for raw in ("tv", "movie", "Show", "FILM", "miniseries", "", None)
        }
        self.assertEqual(proposed, {MEDIA_TYPE_MOVIE, MEDIA_TYPE_SHOW, None})


V4_FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "distribution-v4-full.ndjson"
)

NEXT_VIEWS_UI_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "next_views_ui.py")
)


class MediaTypeFilterUiTests(unittest.TestCase):
    """The TV filter shipped disabled for a long time. Keep it from going back."""

    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()

    def test_neither_tv_filter_button_is_disabled(self):
        for attribute in ('data-type-filter="tv"', 'data-location-type-filter="tv"'):
            with self.subTest(attribute=attribute):
                index = self.source.index(attribute)
                button = self.source[index : self.source.index("</button>", index)]
                self.assertNotIn("disabled", button)
                self.assertNotIn("is-disabled", button)

    def test_the_type_matcher_is_not_stubbed_out(self):
        self.assertNotIn('if (selected === "tv") return false;', self.source)

    def test_a_box_set_of_a_series_survives_the_tv_filter(self):
        """Containers carry no type of their own; their members do."""
        self.assertIn("function containerMatchesType(container)", self.source)
        self.assertIn("containerMatchesType(container)", self.source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


try:
    from .. import next_movievault_v2
    from ..next_plugins.movievault_v2 import plugin as movievault_v2_plugin
except ImportError:  # pragma: no cover - backend working-directory CI imports
    import next_movievault_v2
    from next_plugins.movievault_v2 import plugin as movievault_v2_plugin


class WorkTypeFromMovieVaultTests(unittest.TestCase):
    """MovieVault's `movie`/`tv` becoming DiscVault's `MOVIE`/`SHOW`.

    Two dialects meet in exactly one place, the plugin's release mapper, and
    these tests pin both the translation and the cases where it must stay quiet.
    """

    @staticmethod
    def _release(**overrides):
        record = {
            "releaseId": "10000000-0000-0000-0000-000000000001",
            "filmId": "20000000-0000-0000-0000-000000000001",
            "canonicalTitle": "Fargo",
            "releaseTitle": "Fargo",
            "releaseYear": 1996,
            "format": "Blu-ray",
        }
        record.update(overrides)
        return movievault_v2_plugin._release(record)

    def test_tv_becomes_show(self):
        self.assertEqual(self._release(workType="tv")["movie"]["mediaType"], "SHOW")

    def test_movie_becomes_movie(self):
        self.assertEqual(self._release(workType="movie")["movie"]["mediaType"], "MOVIE")

    def test_an_absent_work_type_proposes_nothing(self):
        """Silence must not be a proposal.

        A record projected before MovieVault added the field carries no value,
        and a merge policy that saw "MOVIE" here would let an old record
        overwrite a series the user had typed by hand.
        """
        self.assertNotIn("mediaType", self._release()["movie"])

    def test_an_unrecognised_work_type_proposes_nothing(self):
        self.assertNotIn("mediaType", self._release(workType="miniseries")["movie"])

    @staticmethod
    def _v4_feed_record(**overrides):
        with open(V4_FIXTURE_PATH, "rb") as handle:
            for line in handle.read().splitlines():
                candidate = json.loads(line)
                if candidate.get("recordType") == "release":
                    candidate.update(overrides)
                    return candidate
        raise AssertionError("no release record in the v4 fixture")

    def test_the_parser_only_keeps_a_value_the_vocabulary_knows(self):
        """The index caches what the feed said, not a guess at what it meant."""
        cases = (("tv", "tv"), ("movie", "movie"), ("miniseries", None))
        for value, expected in cases:
            with self.subTest(work_type=value):
                parsed = next_movievault_v2.validate_record(
                    self._v4_feed_record(workType=value),
                    contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT,
                )
                self.assertEqual(parsed["workType"], expected)

    def test_the_parser_reports_no_work_type_when_the_feed_omits_it(self):
        parsed = next_movievault_v2.validate_record(
            self._v4_feed_record(),
            contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT,
        )
        self.assertIsNone(parsed["workType"])
