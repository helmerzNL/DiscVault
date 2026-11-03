"""Season links as a MovieVault correction, per release and for a whole series.

A person who files three boxes under a series and links each to its season
knows something the shared catalogue may not: which seasons each box covers.
`seasons` carries that upstream as a `contribution-2` field (MovieVault 0.44.0),
by season number, and the series screen sends it for every release at once.

What is asserted here is about what the database holds, so it cannot be faked
with a cursor: which local rows count as a release's seasons, which mirror
value is honest to state as `expected`, and when the field must wait.

Point `DATABASE_URL` at a database with every migration applied; without it
these skip.
"""

import os
import sys
import unittest
import uuid
from unittest import mock


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ModuleNotFoundError:
    psycopg = None
    dict_row = None
    Jsonb = None

from app.backend import next_movievault_v2_field_corrections as corrections


DATABASE_URL = os.environ.get("DATABASE_URL")

PREFIX = "season-correction-test"
#: Check-digit valid, so the lookup hash names a real code.
BARCODES = ("4006381333931", "5051890013279", "0717951008572")


def _feed_seasons(*numbers):
    """Seasons in the shape the feed publishes and the mirror stores."""
    return [
        {"seasonNumber": number, "title": None, "releaseYear": None, "episodeCount": None}
        for number in numbers
    ]


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class SeasonCorrectionPostgresTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)

    def setUp(self):
        self.generation = uuid.uuid4()
        self.film_id = uuid.uuid4()
        self.series_id = uuid.uuid4()
        self.conn = self.connect()
        self.addCleanup(self.conn.close)
        self.addCleanup(self._cleanup)
        self.season_ids = {}
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movievault_v2_sync_state (plugin_id, origin, active_generation)
                VALUES ('movievault_v2', 'https://example.test', %s)
                ON CONFLICT (plugin_id) DO UPDATE SET active_generation = EXCLUDED.active_generation
                """,
                (self.generation,),
            )
            cur.execute(
                "INSERT INTO series (id, public_id, title) VALUES (%s, %s, %s)",
                (self.series_id, f"{PREFIX}-{self.series_id}", f"{PREFIX} series"),
            )
            for number in (0, 1, 2, 3):
                season_id = uuid.uuid4()
                self.season_ids[number] = season_id
                cur.execute(
                    """
                    INSERT INTO series_seasons (id, public_id, series_id, season_number)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (season_id, f"{PREFIX}-{season_id}", self.series_id, number),
                )

    def _cleanup(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM movievault_v2_contributions WHERE movie_id IN "
                "(SELECT id FROM movies WHERE title LIKE %s)",
                (f"{PREFIX}%",),
            )
            cur.execute(
                "DELETE FROM movie_seasons WHERE movie_id IN (SELECT id FROM movies WHERE title LIKE %s)",
                (f"{PREFIX}%",),
            )
            cur.execute("DELETE FROM movies WHERE title LIKE %s", (f"{PREFIX}%",))
            cur.execute("DELETE FROM series WHERE id = %s", (self.series_id,))
            cur.execute("DELETE FROM movievault_v2_lookup_hashes WHERE generation = %s", (self.generation,))
            cur.execute("DELETE FROM movievault_v2_releases WHERE generation = %s", (self.generation,))
            cur.execute("DELETE FROM movievault_v2_sync_state WHERE active_generation = %s", (self.generation,))

    # ---- fixtures ----------------------------------------------------------

    def _mirror_release(self, barcode, *, seasons=(), work_type="tv", unknown_seasons=False):
        """A catalogue release in the mirror, findable by its barcode."""
        release_id = uuid.uuid4()
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movievault_v2_releases (
                    generation, release_id, film_id, canonical_title, release_title,
                    format, revision, work_type, seasons
                )
                VALUES (%s, %s, %s, 'Mirror Series', 'Mirror Series Box', 'Blu-ray', 7, %s, %s)
                """,
                (
                    self.generation,
                    release_id,
                    self.film_id,
                    work_type,
                    None if unknown_seasons else Jsonb(_feed_seasons(*seasons)),
                ),
            )
            cur.execute(
                """
                INSERT INTO movievault_v2_lookup_hashes (
                    generation, lookup_hash, entity_type, entity_id, source_type, member_position
                )
                VALUES (%s, %s, 'release', %s, 'release_ean', 0)
                """,
                (self.generation, corrections.barcode_lookup_hash(barcode), release_id),
            )
        return release_id

    def _release(self, barcode, *seasons, sort_orders=None):
        """A local TV release under the series, linked to these season numbers."""
        movie_id = uuid.uuid4()
        record = {
            "id": movie_id,
            "public_id": f"{PREFIX}-{movie_id}",
            "title": f"{PREFIX} release {barcode}",
            "release_title": None,
            "barcode": barcode,
            "format": "Blu-ray",
            "media_type": "SHOW",
            "series_id": self.series_id,
        }
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movies (id, public_id, title, release_title, barcode, format,
                                    media_type, series_id)
                VALUES (%(id)s, %(public_id)s, %(title)s, %(release_title)s, %(barcode)s,
                        %(format)s, %(media_type)s, %(series_id)s)
                """,
                record,
            )
            for index, number in enumerate(seasons):
                cur.execute(
                    """
                    INSERT INTO movie_seasons (movie_id, season_id, series_id, sort_order)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        movie_id,
                        self.season_ids[number],
                        self.series_id,
                        sort_orders[index] if sort_orders else index,
                    ),
                )
        return record

    def _preview(self, record, **kwargs):
        kwargs.setdefault("preflight", False)
        return corrections.correction_preview(conn=self.conn, entity="movie", record=record, metadata={}, **kwargs)

    @staticmethod
    def _seasons_change(preview):
        return next((item for item in preview["changes"] if item["field"] == "seasons"), None)

    # ---- one release -------------------------------------------------------

    def test_a_release_proposes_the_seasons_it_is_linked_to(self):
        """A box linked to seasons 1-3 here, which the catalogue holds as season
        1 only: the correction says so, by number."""
        self._mirror_release(BARCODES[0], seasons=(1,))
        record = self._release(BARCODES[0], 1, 2, 3)

        change = self._seasons_change(self._preview(record))

        self.assertEqual(change, {"field": "seasons", "expected": [1], "proposed": [1, 2, 3]})

    def test_specials_travel_as_season_zero(self):
        self._mirror_release(BARCODES[0], seasons=())
        record = self._release(BARCODES[0], 0)

        change = self._seasons_change(self._preview(record))

        self.assertEqual(change["expected"], [])
        self.assertEqual(change["proposed"], [0])

    def test_a_release_linked_to_no_season_proposes_nothing(self):
        """Upstream reads `[]` as a replacement list -- "the complete series, or
        unspecified" -- so sending it would unlink every season the catalogue
        holds for the box. A local record with nothing recorded says nothing."""
        self._mirror_release(BARCODES[0], seasons=(1, 2))
        record = self._release(BARCODES[0])

        preview = self._preview(record)

        self.assertIsNone(self._seasons_change(preview))
        self.assertNotIn("seasons", preview["withheld"])

    def test_the_curators_order_is_not_a_disagreement(self):
        """Both sides compare sorted numbers: a box listing season 2 first is
        the same box."""
        self._mirror_release(BARCODES[0], seasons=(1, 2))
        record = self._release(BARCODES[0], 2, 1)

        self.assertIsNone(self._seasons_change(self._preview(record)))

    def test_a_deleted_local_season_is_not_one_the_release_covers(self):
        self._mirror_release(BARCODES[0], seasons=(1,))
        record = self._release(BARCODES[0], 1, 2)
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE series_seasons SET deleted_at = now() WHERE id = %s",
                (self.season_ids[2],),
            )

        self.assertIsNone(self._seasons_change(self._preview(record)))

    def test_a_generation_that_never_carried_seasons_waits_for_a_live_read(self):
        """NULL in the mirror is "the feed has not said" (072). Stating `[]` as
        `expected` there would be a claim about the catalogue nobody observed."""
        self._mirror_release(BARCODES[0], unknown_seasons=True)
        record = self._release(BARCODES[0], 1)

        preview = self._preview(record)

        self.assertIsNone(self._seasons_change(preview))
        self.assertEqual(preview["withheld"]["seasons"], "needs_live_catalogue")

    def test_a_live_read_settles_what_the_mirror_could_not(self):
        """A canonical summary omits `seasons` when none is linked, so absent
        there is a real `[]` -- unlike the mirror's NULL."""
        self._mirror_release(BARCODES[0], unknown_seasons=True)
        record = self._release(BARCODES[0], 2)
        live = {"format": "Blu-ray", "seasons": [], "_workType": "tv"}

        with mock.patch.object(corrections, "live_release_values", lambda *a, **k: live):
            preview = self._preview(record, preflight=True)

        self.assertEqual(preview["comparedAgainst"], "catalogue")
        self.assertEqual(
            self._seasons_change(preview), {"field": "seasons", "expected": [], "proposed": [2]}
        )

    def test_a_work_the_catalogue_holds_as_a_film_is_refused_out_loud(self):
        """MovieVault refuses to link seasons to a film (`release_not_a_series`)
        because approving one field may not change the work type. Sending it
        anyway would cost a moderator a decision that cannot succeed."""
        self._mirror_release(BARCODES[0], seasons=(), work_type="movie")
        record = self._release(BARCODES[0], 1)

        preview = self._preview(record)

        self.assertIsNone(self._seasons_change(preview))
        self.assertEqual(preview["withheld"]["seasons"], "catalogue_records_a_film")

    def test_the_live_summary_shape_is_read_by_number(self):
        payload = {
            "film": {"workType": "tv"},
            "releases": [
                {"releaseRef": "r1", "seasons": _feed_seasons(3, 1)},
                {"releaseRef": "r2"},
            ],
        }
        with mock.patch.object(corrections, "_film_releases_payload", return_value=payload):
            first = corrections.live_release_values("f1", "r1")
            second = corrections.live_release_values("f1", "r2")

        self.assertEqual(first["seasons"], [1, 3])
        self.assertEqual(first["_workType"], "tv")
        self.assertEqual(second["seasons"], [])

    # ---- the whole series --------------------------------------------------

    def test_the_series_screen_states_each_release(self):
        """Three boxes, three answers: one to send, one the catalogue already
        has, and one with no season linked here."""
        self._mirror_release(BARCODES[0], seasons=())
        self._mirror_release(BARCODES[1], seasons=(2,))
        self._mirror_release(BARCODES[2], seasons=(3,))
        changed = self._release(BARCODES[0], 1)
        in_sync = self._release(BARCODES[1], 2)
        unlinked = self._release(BARCODES[2])

        states = {
            str(record["id"]): corrections.series_season_item(self.conn, record, {}, preflight=False)
            for record in (changed, in_sync, unlinked)
        }

        first = states[str(changed["id"])]
        self.assertEqual(first["state"], "changed")
        self.assertEqual(first["localSeasons"], [1])
        self.assertEqual(first["catalogueSeasons"], [])
        self.assertEqual(first["change"]["proposed"], [1])
        self.assertEqual(states[str(in_sync["id"])]["state"], "in_sync")
        self.assertEqual(states[str(in_sync["id"])]["catalogueSeasons"], [2])
        self.assertEqual(states[str(unlinked["id"])]["state"], "no_seasons")

    def test_a_release_the_catalogue_does_not_hold_is_said_to_be_missing(self):
        record = self._release("0012569828827", 1)

        item = corrections.series_season_item(self.conn, record, {}, preflight=False)

        self.assertEqual(item["state"], "not_in_catalogue")
        self.assertIsNone(item["change"])

    def test_a_refused_release_says_why(self):
        self._mirror_release(BARCODES[0], unknown_seasons=True)
        record = self._release(BARCODES[0], 1)

        item = corrections.series_season_item(self.conn, record, {}, preflight=False)

        self.assertEqual(item["state"], "withheld")
        self.assertEqual(item["withheld"], "needs_live_catalogue")

    def test_one_live_read_serves_every_release_of_the_film(self):
        """The route answers for every release of a film; a twelve-box series
        must not make twelve identical round trips, nor wait out twelve
        timeouts when MovieVault is unreachable."""
        first_release = self._mirror_release(BARCODES[0], seasons=())
        second_release = self._mirror_release(BARCODES[1], seasons=())
        first = self._release(BARCODES[0], 1)
        second = self._release(BARCODES[1], 2)
        payload = {
            "film": {"workType": "tv"},
            "releases": [
                {"releaseRef": str(first_release)},
                {"releaseRef": str(second_release), "seasons": _feed_seasons(2)},
            ],
        }
        cache = {}

        with mock.patch.object(corrections, "_film_releases_payload", return_value=payload) as fetch:
            items = [
                corrections.series_season_item(self.conn, record, {}, live_cache=cache)
                for record in (first, second)
            ]

        self.assertEqual(fetch.call_count, 1)
        self.assertEqual([item["state"] for item in items], ["changed", "in_sync"])
        self.assertEqual(items[0]["comparedAgainst"], "catalogue")

    def test_an_unreachable_catalogue_is_asked_once_and_falls_back(self):
        self._mirror_release(BARCODES[0], seasons=())
        self._mirror_release(BARCODES[1], seasons=())
        records = (self._release(BARCODES[0], 1), self._release(BARCODES[1], 2))
        cache = {}

        with mock.patch.object(corrections, "_film_releases_payload", return_value=None) as fetch:
            items = [
                corrections.series_season_item(self.conn, record, {}, live_cache=cache)
                for record in records
            ]

        self.assertEqual(fetch.call_count, 1)
        self.assertEqual([item["comparedAgainst"] for item in items], ["mirror", "mirror"])
        self.assertEqual([item["state"] for item in items], ["changed", "changed"])


if __name__ == "__main__":
    unittest.main()
