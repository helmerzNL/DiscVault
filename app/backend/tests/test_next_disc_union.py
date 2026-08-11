"""The release row becomes the union of its discs, losing nothing on the way.

Approved as the shape of the feature: once a release has discs, its
release-level technical fields stop being authored and become a summary. A
reader asking "what is on this release" means across all of it, and the disc is
the more specific of the two places the same fact could live — two authored
copies is how they drift.

The hard part is not the union. It is that a release may already hold a value
no disc does, recorded before discs existed or written by a sync client that
predates them, and deriving straight over it would delete a fact nobody
retracted. So a leftover is pushed onto disc 1 first and only then unioned:
after one save the release row is a pure derivation, and before it nothing is
lost.
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
PREFIX = "disc-union-test"


class UnionRuleTests(unittest.TestCase):
    """What the derivation says, before any database is involved."""

    def test_lists_are_unioned_in_the_order_a_reader_scans_them(self):
        """Disc 1's English track before disc 2's commentary. Order is the
        discs' own, which is what somebody reading the release expects."""
        discs = [
            {"audio_tracks": [{"languageCode": "en"}], "hdr": ["dolby_vision"]},
            {"audio_tracks": [{"languageCode": "en"}, {"languageCode": "nl"}], "hdr": ["hdr10"]},
        ]
        derived = next_discs.union_release_technical(discs)
        self.assertEqual(
            derived["audio_tracks"], [{"languageCode": "en"}, {"languageCode": "nl"}]
        )
        self.assertEqual(derived["hdr"], ["dolby_vision", "hdr10"])

    def test_the_resolution_is_the_best_a_disc_in_the_box_offers(self):
        """A 4K disc packaged with a Blu-ray is a 2160p release — the reading
        the format string "4K UHD + Blu-ray" already takes."""
        derived = next_discs.union_release_technical(
            [{"video_resolution": "1080p"}, {"video_resolution": "2160p"}]
        )
        self.assertEqual(derived["video_resolution"], "2160p")

    def test_an_unrecognised_resolution_is_left_rather_than_blanked(self):
        derived = next_discs.union_release_technical([{"video_resolution": "8K"}])
        self.assertNotIn("video_resolution", derived)

    def test_no_discs_derives_nothing(self):
        """A release with no discs still authors its own values — the state
        every record was in before discs existed."""
        self.assertEqual(next_discs.union_release_technical([]), {})

    def test_only_the_columns_a_disc_actually_has(self):
        """Packaging, finishes, the carrier and the content ratings describe
        the box rather than what is pressed onto a platter. Deriving them from
        discs would invent an answer."""
        derived = next_discs.union_release_technical([{"hdr": ["hdr10"]}])
        for column in ("packaging", "finishes", "carrier_type", "content_ratings"):
            self.assertNotIn(column, derived)


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class DerivationOnSaveTests(unittest.TestCase):
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
                cur.execute(
                    "DELETE FROM movie_technical_specs WHERE movie_id = %s", (self.movie_id,)
                )
                cur.execute("DELETE FROM movie_discs WHERE movie_id = %s", (self.movie_id,))
                cur.execute("DELETE FROM movies WHERE id = %s", (self.movie_id,))
            conn.commit()

    def _save(self, conn, discs, technical=None):
        with conn.cursor() as cur:
            if technical:
                next_app.upsert_movie_technical_edits(cur, self.movie_id, technical)
            next_app.apply_movie_discs(
                cur,
                self.movie_id,
                next_discs.discs_payload({"discs": discs}),
                media_type="MOVIE",
            )
        conn.commit()

    def _release_row(self, conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT hdr, regions, audio_tracks, video_resolution "
                "FROM movie_technical_specs WHERE movie_id = %s",
                (self.movie_id,),
            )
            return dict(cur.fetchone() or {})

    def test_saving_discs_makes_the_release_row_their_union(self):
        with self.connect() as conn:
            self._save(
                conn,
                [
                    {"discType": "uhd_bluray", "hdr": ["dolby_vision"], "videoResolution": "2160p"},
                    {"discType": "bluray", "hdr": ["hdr10"], "videoResolution": "1080p"},
                ],
            )
            row = self._release_row(conn)
        self.assertEqual(list(row["hdr"]), ["dolby_vision", "hdr10"])
        self.assertEqual(row["video_resolution"], "2160p")

    def test_a_release_level_fact_no_disc_has_is_pushed_onto_disc_one(self):
        """The constraint the whole design turns on: deriving straight over a
        leftover would delete something nobody retracted. It becomes an
        authored value on disc 1 instead, and the union then includes it."""
        with self.connect() as conn:
            self._save(
                conn,
                [{"discType": "uhd_bluray", "hdr": ["dolby_vision"]}],
                technical={"hdr": ["hdr10_plus"], "regions": ["FREE"]},
            )
            row = self._release_row(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT hdr, regions FROM movie_discs WHERE movie_id = %s "
                    "ORDER BY sort_order LIMIT 1",
                    (self.movie_id,),
                )
                disc = dict(cur.fetchone())
        # Nothing lost at release level...
        self.assertEqual(set(row["hdr"]), {"dolby_vision", "hdr10_plus"})
        self.assertEqual(list(row["regions"]), ["FREE"])
        # ...and the leftover is now authored on the disc, so the next save
        # derives it rather than dropping it.
        self.assertEqual(set(disc["hdr"]), {"dolby_vision", "hdr10_plus"})
        self.assertEqual(list(disc["regions"]), ["FREE"])

    def test_a_second_save_is_stable(self):
        """Once pushed down, the derivation is a fixed point — a save that
        changes nothing must not keep growing the lists."""
        with self.connect() as conn:
            self._save(
                conn,
                [{"discType": "uhd_bluray", "hdr": ["dolby_vision"]}],
                technical={"hdr": ["hdr10_plus"]},
            )
            first = self._release_row(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM movie_discs WHERE movie_id = %s ORDER BY sort_order",
                    (self.movie_id,),
                )
                disc_id = str(cur.fetchone()["id"])
            self._save(
                conn,
                [{"id": disc_id, "discType": "uhd_bluray", "hdr": ["dolby_vision", "hdr10_plus"]}],
            )
            second = self._release_row(conn)
        self.assertEqual(list(first["hdr"]), list(second["hdr"]))

    def test_clearing_the_discs_leaves_the_release_authoring_its_own_values(self):
        """An explicit empty disc list is a statement, and it hands authorship
        back rather than blanking what the union had derived."""
        with self.connect() as conn:
            self._save(conn, [{"discType": "uhd_bluray", "hdr": ["dolby_vision"]}])
            self._save(conn, [])
            row = self._release_row(conn)
        self.assertEqual(list(row["hdr"]), ["dolby_vision"])

    def test_saying_nothing_about_discs_derives_nothing(self):
        with self.connect() as conn:
            self._save(conn, [{"discType": "bluray", "hdr": ["hdr10"]}])
            with conn.cursor() as cur:
                next_app.apply_movie_discs(cur, self.movie_id, None, media_type="MOVIE")
            conn.commit()
            row = self._release_row(conn)
        self.assertEqual(list(row["hdr"]), ["hdr10"])


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class ClientEditsToDerivedFieldsTests(unittest.TestCase):
    """A client writing a field the discs own, through the real edit path.

    The class above drives `upsert_movie_technical_edits` directly, which is
    the derivation's own writer and deliberately ungated. These go through
    `write_movie_edit_record` -- the function both the v1 PATCH route and the
    PWA edit route call -- because what is being tested is the wiring, not the
    helper: a guard that exists but is not called is exactly the bug it guards
    against.

    The PWA hides these fields once a release has discs, but hiding is a client
    courtesy. A client that does not know the rule -- the iOS app today -- was
    having its write accepted, and neither outcome was a plain overwrite.
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
                    (self.movie_id, f"{PREFIX}-{self.movie_id}", f"{PREFIX} film"),
                )
            conn.commit()

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM movie_technical_specs WHERE movie_id = %s", (self.movie_id,)
                )
                cur.execute("DELETE FROM movie_discs WHERE movie_id = %s", (self.movie_id,))
                cur.execute("DELETE FROM movies WHERE id = %s", (self.movie_id,))
            conn.commit()

    def _edit(self, conn, body):
        """One save through the route a client actually reaches."""
        payload = next_app.movie_update_payload(
            {"title": f"{PREFIX} film", **body},
            existing={"title": f"{PREFIX} film", "media_type": "MOVIE"},
        )
        with conn.cursor() as cur:
            next_app.write_movie_edit_record(cur, self.movie_id, payload)
        conn.commit()

    def _release_hdr(self, conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT hdr FROM movie_technical_specs WHERE movie_id = %s", (self.movie_id,)
            )
            row = cur.fetchone()
        return list((row or {}).get("hdr") or [])

    def _disc_one_hdr(self, conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT hdr FROM movie_discs WHERE movie_id = %s ORDER BY sort_order LIMIT 1",
                (self.movie_id,),
            )
            row = cur.fetchone()
        return list((row or {}).get("hdr") or [])

    def test_a_derived_field_sent_without_a_disc_list_is_ignored(self):
        """The likely shape from a client that knows nothing about discs. It
        used to be stored verbatim, and because nothing derives without a disc
        list in the same body, the release then contradicted its own discs
        until some later save happened to carry them."""
        with self.connect() as conn:
            self._edit(conn, {"discs": [{"discType": "uhd_bluray", "hdr": ["dolby_vision"]}]})
            self._edit(conn, {"hdr": ["hdr10_plus"]})
            self.assertEqual(self._release_hdr(conn), ["dolby_vision"])
            self.assertEqual(self._disc_one_hdr(conn), ["dolby_vision"])

    def test_a_derived_field_sent_with_a_disc_list_does_not_land_on_disc_one(self):
        """The regression worth having a test for. The leftover push-down
        cannot tell a fresh client edit from a value stranded on the release
        from before the discs existed -- both read as "held here, on no disc"
        -- so it wrote the client's value onto a disc the user never edited,
        where it then looked like an authored disc fact."""
        with self.connect() as conn:
            self._edit(conn, {"discs": [{"discType": "uhd_bluray", "hdr": ["dolby_vision"]}]})
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM movie_discs WHERE movie_id = %s ORDER BY sort_order",
                    (self.movie_id,),
                )
                disc_id = str(cur.fetchone()["id"])
            self._edit(
                conn,
                {
                    "hdr": ["hdr10_plus"],
                    "discs": [{"id": disc_id, "discType": "uhd_bluray", "hdr": ["dolby_vision"]}],
                },
            )
            self.assertNotIn("hdr10_plus", self._disc_one_hdr(conn))
            self.assertEqual(self._release_hdr(conn), ["dolby_vision"])

    def test_a_release_with_no_discs_still_authors_its_own_values(self):
        """The guard reads the database, not the payload. Most releases have no
        discs at all and must keep editing exactly as before."""
        with self.connect() as conn:
            self._edit(conn, {"hdr": ["hdr10_plus"]})
            self.assertEqual(self._release_hdr(conn), ["hdr10_plus"])

    def test_the_first_discs_are_still_seeded_from_the_release(self):
        """Reading the database rather than the payload is what keeps this
        working: a body creating a release's *first* discs still legitimately
        holds its release-level values, and at guard time the movie has none,
        so nothing is dropped and the push-down seeds disc 1 as before."""
        with self.connect() as conn:
            self._edit(
                conn,
                {
                    "hdr": ["hdr10_plus"],
                    "discs": [{"discType": "uhd_bluray", "hdr": ["dolby_vision"]}],
                },
            )
            self.assertEqual(set(self._disc_one_hdr(conn)), {"dolby_vision", "hdr10_plus"})
            self.assertEqual(set(self._release_hdr(conn)), {"dolby_vision", "hdr10_plus"})

    def test_a_field_the_discs_do_not_own_is_untouched(self):
        """`runtime_minutes` and the box fields sit beside the derived ones and
        are not derived. The guard drops seven columns, not a section."""
        with self.connect() as conn:
            self._edit(conn, {"discs": [{"discType": "uhd_bluray", "hdr": ["dolby_vision"]}]})
            self._edit(conn, {"finishes": ["embossed"], "runtimeMinutes": 128})
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT finishes FROM movie_technical_specs WHERE movie_id = %s",
                    (self.movie_id,),
                )
                finishes = list(cur.fetchone()["finishes"] or [])
                cur.execute("SELECT runtime_minutes FROM movies WHERE id = %s", (self.movie_id,))
                runtime = cur.fetchone()["runtime_minutes"]
        self.assertEqual(finishes, ["embossed"])
        self.assertEqual(runtime, 128)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
