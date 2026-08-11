"""The one-time backfill converges the releases that predate the union rule.

The union derivation (#609) runs on save, so a release broken down into discs
before it existed keeps its historical release-level row until somebody edits
it. This is the sweep that converges the rest, and everything interesting about
it is a safety property rather than a feature:

* it must not lose a fact -- the same constraint the derivation itself is built
  around, checked here from the outside rather than trusted;
* it must be safe to re-run, because a backfill over a whole library is exactly
  the kind of thing that gets interrupted;
* and it must not touch a release that is already converged, or every rerun
  would churn the sync feed for no change.

The report is tested as its own surface. A dry run nobody can read is not a dry
run, and the number that matters -- how many facts survive only because of the
push-down -- is the one a reader would use to decide whether to proceed.
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
from app.backend.scripts import backfill_disc_union

DATABASE_URL = os.environ.get("DATABASE_URL")
PREFIX = "disc-union-backfill-test"


class PlanTests(unittest.TestCase):
    """What the report says, before any database is involved."""

    def test_a_converged_release_plans_nothing(self):
        """The fixed-point property, read from the planner: if the row already
        equals the union, there is nothing to do and the release must not
        appear in the report at all."""
        discs = [{"hdr": ["dolby_vision"], "video_resolution": "2160p"}]
        stored = {"hdr": ["dolby_vision"], "video_resolution": "2160p"}
        self.assertIsNone(backfill_disc_union.plan_release(stored, discs))

    def test_a_leftover_is_counted_as_pushed_rather_than_added(self):
        """The number a reader decides on. `hdr10_plus` is on the release and
        on no disc, so it survives only because the push-down puts it on disc 1
        -- and the report has to say so rather than reporting a net gain of
        one."""
        plan = backfill_disc_union.plan_release(
            {"hdr": ["hdr10_plus"]}, [{"hdr": ["dolby_vision"]}]
        )
        self.assertEqual(
            plan["columns"]["hdr"],
            {"before": 1, "after": 2, "pushed_to_disc_one": 1},
        )

    def test_a_push_down_is_reported_even_when_the_release_row_is_unchanged(self):
        """The case the first version of this report missed. No disc states a
        region, so the union puts the release's own value straight back and the
        release row does not move — but disc 1 gains an authored value it did
        not have, and a report a reader decides on cannot hide a write."""
        plan = backfill_disc_union.plan_release(
            {"regions": ["FREE"]}, [{"hdr": ["dolby_vision"]}]
        )
        self.assertEqual(
            plan["columns"]["regions"],
            {"before": 1, "after": 1, "pushed_to_disc_one": 1},
        )

    def test_a_release_with_no_discs_plans_nothing(self):
        """It still authors its own values. The candidate query cannot produce
        one, but the planner is what would have to be wrong for a future caller
        to blank a release by asking it the wrong question."""
        self.assertIsNone(backfill_disc_union.plan_release({"hdr": ["hdr10"]}, []))

    def test_a_raised_resolution_is_reported_as_a_change(self):
        plan = backfill_disc_union.plan_release(
            {"video_resolution": "1080p"},
            [{"video_resolution": "1080p"}, {"video_resolution": "2160p"}],
        )
        self.assertEqual(
            plan["columns"]["video_resolution"], {"before": "1080p", "after": "2160p"}
        )

    def test_the_loss_check_names_the_column_that_lost_an_entry(self):
        self.assertEqual(
            backfill_disc_union._kept_everything(
                {"hdr": ["dolby_vision", "hdr10"]}, {"hdr": ["dolby_vision"]}
            ),
            ["hdr"],
        )
        self.assertEqual(
            backfill_disc_union._kept_everything(
                {"hdr": ["dolby_vision"]}, {"hdr": ["dolby_vision", "hdr10"]}
            ),
            [],
        )

    def test_a_blanked_resolution_counts_as_a_loss_and_a_raised_one_does_not(self):
        self.assertEqual(
            backfill_disc_union._kept_everything(
                {"video_resolution": "1080p"}, {"video_resolution": None}
            ),
            ["video_resolution"],
        )
        self.assertEqual(
            backfill_disc_union._kept_everything(
                {"video_resolution": "1080p"}, {"video_resolution": "2160p"}
            ),
            [],
        )


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class BackfillTests(unittest.TestCase):
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
                cur.execute(
                    "DELETE FROM sync_changes WHERE entity_id = %s", (str(self.movie_id),)
                )
                cur.execute("DELETE FROM movies WHERE id = %s", (self.movie_id,))
            conn.commit()

    def _make_historical_release(self, conn, *, discs, technical):
        """A release in the state this backfill exists for.

        The discs are written *without* the derivation, which is what a release
        broken down before 26.8.76 looks like: a disc list and a release row
        that was never folded together.
        """
        with conn.cursor() as cur:
            next_app.upsert_movie_technical_edits(cur, self.movie_id, technical)
            for index, disc in enumerate(next_discs.discs_payload({"discs": discs})):
                next_app._upsert_movie_disc(cur, self.movie_id, disc, sort_order=index)
        conn.commit()

    def _row(self, conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT hdr, regions, video_resolution FROM movie_technical_specs "
                "WHERE movie_id = %s",
                (self.movie_id,),
            )
            return dict(cur.fetchone() or {})

    def _mine(self, report):
        return [
            item for item in report["releases"] if item["movie_id"] == str(self.movie_id)
        ]

    def test_the_dry_run_names_the_release_and_writes_nothing(self):
        with self.connect() as conn:
            self._make_historical_release(
                conn,
                discs=[{"discType": "uhd_bluray", "hdr": ["dolby_vision"]}],
                technical={"hdr": ["hdr10_plus"]},
            )
            report = backfill_disc_union.build_report(conn)
            mine = self._mine(report)
            self.assertEqual(len(mine), 1)
            self.assertEqual(
                mine[0]["columns"]["hdr"],
                {"before": 1, "after": 2, "pushed_to_disc_one": 1},
            )
            # Nothing moved: the report is a read.
            self.assertEqual(list(self._row(conn)["hdr"]), ["hdr10_plus"])

    def test_executing_converges_the_release_without_losing_the_leftover(self):
        with self.connect() as conn:
            self._make_historical_release(
                conn,
                discs=[{"discType": "uhd_bluray", "hdr": ["dolby_vision"]}],
                technical={"hdr": ["hdr10_plus"], "regions": ["FREE"]},
            )
            report = backfill_disc_union.build_report(conn)
            outcome = backfill_disc_union.execute_backfill(
                conn, {"releases": self._mine(report)}
            )
            self.assertEqual(outcome, {"converged": 1, "published": 1, "refused": []})
            row = self._row(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT hdr, regions FROM movie_discs WHERE movie_id = %s "
                    "ORDER BY sort_order LIMIT 1",
                    (self.movie_id,),
                )
                disc = dict(cur.fetchone())
        self.assertEqual(set(row["hdr"]), {"dolby_vision", "hdr10_plus"})
        self.assertEqual(list(row["regions"]), ["FREE"])
        # Authored on the disc now, so the next ordinary save derives it.
        self.assertEqual(set(disc["hdr"]), {"dolby_vision", "hdr10_plus"})
        self.assertEqual(list(disc["regions"]), ["FREE"])

    def test_a_second_run_finds_nothing_to_do(self):
        """Safe to re-run, which is what makes an interrupted sweep recoverable
        by simply starting it again."""
        with self.connect() as conn:
            self._make_historical_release(
                conn,
                discs=[{"discType": "uhd_bluray", "hdr": ["dolby_vision"]}],
                technical={"hdr": ["hdr10_plus"]},
            )
            first = backfill_disc_union.build_report(conn)
            backfill_disc_union.execute_backfill(conn, {"releases": self._mine(first)})
            second = backfill_disc_union.build_report(conn)
        self.assertEqual(self._mine(second), [])

    def test_a_release_already_converged_is_never_in_the_report(self):
        """Saved through the ordinary path, so the derivation already ran. A
        backfill that re-reported it would churn the sync feed for no change."""
        with self.connect() as conn:
            with conn.cursor() as cur:
                next_app.apply_movie_discs(
                    cur,
                    self.movie_id,
                    next_discs.discs_payload(
                        {"discs": [{"discType": "bluray", "hdr": ["hdr10"]}]}
                    ),
                    media_type="MOVIE",
                )
            conn.commit()
            report = backfill_disc_union.build_report(conn)
        self.assertEqual(self._mine(report), [])

    def test_converging_publishes_the_change_to_connected_clients(self):
        """A client holding the pre-union row would otherwise keep it -- and
        could push it back on its next mutation, recreating exactly the
        divergence this sweep removes."""
        with self.connect() as conn:
            self._make_historical_release(
                conn,
                discs=[{"discType": "uhd_bluray", "hdr": ["dolby_vision"]}],
                technical={"hdr": ["hdr10_plus"]},
            )
            report = backfill_disc_union.build_report(conn)
            backfill_disc_union.execute_backfill(conn, {"releases": self._mine(report)})
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT operation FROM sync_changes WHERE entity_type='movie' "
                    "AND entity_id=%s",
                    (str(self.movie_id),),
                )
                rows = cur.fetchall()
        self.assertEqual([row["operation"] for row in rows], ["upsert"])

    def test_a_release_that_would_lose_a_fact_is_rolled_back_and_reported(self):
        """The safety net, exercised by making the derivation lose something.

        The property holds today, so this cannot be provoked with real data --
        which is the point: the check exists for the case nobody has modelled,
        and a check that has never been seen to fire is a check nobody knows
        works.
        """
        with self.connect() as conn:
            self._make_historical_release(
                conn,
                discs=[{"discType": "uhd_bluray", "hdr": ["dolby_vision"]}],
                technical={"hdr": ["hdr10_plus"]},
            )
            report = backfill_disc_union.build_report(conn)

            def losing_derivation(cur, movie_id):
                next_app.upsert_movie_technical_edits(cur, movie_id, {"hdr": []})

            original = backfill_disc_union.derive_release_technical_from_discs
            backfill_disc_union.derive_release_technical_from_discs = losing_derivation
            try:
                outcome = backfill_disc_union.execute_backfill(
                    conn, {"releases": self._mine(report)}
                )
            finally:
                backfill_disc_union.derive_release_technical_from_discs = original

            self.assertEqual(outcome["converged"], 0)
            self.assertEqual(
                outcome["refused"],
                [{"movie_id": str(self.movie_id), "lost_columns": ["hdr"]}],
            )
            # Rolled back: the release still holds what it held.
            self.assertEqual(list(self._row(conn)["hdr"]), ["hdr10_plus"])

    def test_a_deleted_release_is_not_swept(self):
        with self.connect() as conn:
            self._make_historical_release(
                conn,
                discs=[{"discType": "bluray", "hdr": ["hdr10"]}],
                technical={"hdr": ["hdr10_plus"]},
            )
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE movies SET deleted_at = now() WHERE id = %s", (self.movie_id,)
                )
            conn.commit()
            report = backfill_disc_union.build_report(conn)
        self.assertEqual(self._mine(report), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
