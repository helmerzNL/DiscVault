"""What the Library snapshot says about series, against a real database.

The three helpers here are the only reason the Library can group by series
without the sync wire knowing about it. Each carries a rule that a fake
connection would not enforce: a series is only listed when the actor can see a
disc under it, a disc that names no season is a complete-series set rather than
an empty one, and every read is a no-op on an instance that has not run
migration 063.
"""

import hashlib
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

from app.backend import next_app
from app.backend import next_metadata
from app.backend import next_library_data


DATABASE_URL = os.environ.get("DATABASE_URL")

PREFIX = "series-library-test"


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class SeriesLibrarySnapshotPostgresTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                # movie_seasons cites both sides, so it goes before either. The
                # production path documents the same ordering.
                cur.execute(
                    """
                    DELETE FROM movie_seasons WHERE movie_id IN (
                        SELECT id FROM movies WHERE public_id LIKE %s
                    )
                    """,
                    (f"{PREFIX}-%",),
                )
                # Artwork before the series it hangs off. `entity_media.media_id`
                # cascades from `media_assets`, but `entity_id` has no foreign
                # key to `series` at all -- migration 003 leaves `entity_type`
                # free text -- so a link deleted in the other order is an orphan
                # that outlives the run and is counted by the next one.
                cur.execute(
                    """
                    DELETE FROM entity_media WHERE entity_type='series' AND entity_id IN (
                        SELECT id FROM series WHERE public_id LIKE %s
                    )
                    """,
                    (f"{PREFIX}-%",),
                )
                cur.execute("DELETE FROM media_assets WHERE provider_id LIKE %s", (f"{PREFIX}-%",))
                cur.execute("DELETE FROM movies WHERE public_id LIKE %s", (f"{PREFIX}-%",))
                cur.execute("DELETE FROM series_seasons WHERE public_id LIKE %s", (f"{PREFIX}-%",))
                cur.execute("DELETE FROM series WHERE public_id LIKE %s", (f"{PREFIX}-%",))
            conn.commit()

    # --- fixtures -----------------------------------------------------------

    def _series(self, conn, title="Fargo", *, start_year=None, end_year=None):
        series_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO series (id, public_id, title, sort_title, start_year, end_year)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (series_id, f"{PREFIX}-{series_id}", title, title, start_year, end_year),
            )
        conn.commit()
        return series_id

    def _season(self, conn, series_id, number):
        season_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO series_seasons (id, public_id, series_id, season_number)
                VALUES (%s, %s, %s, %s)
                """,
                (season_id, f"{PREFIX}-{season_id}", series_id, number),
            )
        conn.commit()
        return season_id

    def _disc(self, conn, *, series_id=None, title="A Disc", owner_id=None):
        movie_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movies (id, public_id, title, sort_title, media_type, series_id, owner_id)
                VALUES (%s, %s, %s, %s, 'SHOW', %s, %s)
                """,
                (movie_id, f"{PREFIX}-{movie_id}", title, title, series_id, owner_id),
            )
        conn.commit()
        return movie_id

    def _cover(self, conn, movie_id, series_id, season_id):
        # `movie_seasons` names the series as well as the season: the composite
        # foreign key back to `movies(id, series_id)` is what stops a disc from
        # covering a season of a show it does not belong to.
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO movie_seasons (movie_id, series_id, season_id) VALUES (%s, %s, %s)",
                (movie_id, series_id, season_id),
            )
        conn.commit()

    def _poster(self, conn, series_id, *, is_primary=True, sort_order=0, local=True, source_url=None):
        """Give a series a poster the way both write paths do: an asset plus a
        link under `entity_type='series'`.

        `local=True` is the uploaded case, which is served from DiscVault's own
        media route; `local=False` is the fetched case, which keeps the source's
        URL. The two take different branches of `media_asset_public_url`, and a
        test using only one of them would not notice the other breaking.
        """
        media_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO media_assets (id, kind, variant, storage_backend, storage_key,
                                          source_url, provider_id, sha256)
                VALUES (%s, 'poster', 'display', %s, %s, %s, %s, %s)
                """,
                (
                    media_id,
                    "local" if local else "remote",
                    # Both keys are unique per asset: `media_assets` constrains
                    # (storage_backend, storage_key) and (kind, variant, sha256).
                    f"media/{PREFIX}/{media_id}.png" if local else f"remote/{media_id}",
                    source_url,
                    f"{PREFIX}-{media_id}",
                    hashlib.sha256(str(media_id).encode()).hexdigest(),
                ),
            )
            cur.execute(
                """
                INSERT INTO entity_media (entity_type, entity_id, media_id, role, is_primary, sort_order)
                VALUES ('series', %s, %s, 'poster', %s, %s)
                """,
                (series_id, media_id, is_primary, sort_order),
            )
        conn.commit()
        return media_id

    def _listed(self, conn, series_id, **kwargs):
        rows = next_app.collection_series_preview_entities(conn, **kwargs)
        return next((row for row in rows if row["id"] == str(series_id)), None)

    # --- the list ------------------------------------------------------------

    def test_a_series_reports_its_seasons_and_the_discs_under_it(self):
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo", start_year="2014", end_year="2024")
            self._season(conn, series_id, 1)
            self._season(conn, series_id, 2)
            self._disc(conn, series_id=series_id, title="Season 1")
            self._disc(conn, series_id=series_id, title="Season 2")

            row = self._listed(conn, series_id)

        self.assertIsNotNone(row)
        self.assertEqual(row["title"], "Fargo")
        self.assertEqual(row["seasonCount"], 2)
        self.assertEqual(row["discCount"], 2)
        self.assertEqual(row["startYear"], "2014")

    def test_a_series_nobody_owns_a_disc_of_is_not_listed(self):
        """The series row carries no owner of its own. Listing it unconditionally
        would leak the titles on one user's shelf to everyone else."""
        with self.connect() as conn:
            series_id = self._series(conn, "Yellowstone")
            self._season(conn, series_id, 1)

            row = self._listed(conn, series_id)

        self.assertIsNone(row)

    def test_a_deleted_disc_does_not_keep_a_series_on_the_shelf(self):
        with self.connect() as conn:
            series_id = self._series(conn, "Deadwood")
            movie_id = self._disc(conn, series_id=series_id)
            with conn.cursor() as cur:
                cur.execute("UPDATE movies SET deleted_at = now() WHERE id = %s", (movie_id,))
            conn.commit()

            row = self._listed(conn, series_id)

        self.assertIsNone(row)

    def test_a_disc_counts_once_however_many_seasons_it_carries(self):
        """The count is of discs, not of coverage rows; a box set of four
        seasons is one thing on the shelf."""
        with self.connect() as conn:
            series_id = self._series(conn, "The Wire")
            movie_id = self._disc(conn, series_id=series_id, title="Complete Box")
            for number in (1, 2, 3, 4):
                self._cover(conn, movie_id, series_id, self._season(conn, series_id, number))

            row = self._listed(conn, series_id)

        self.assertEqual(row["discCount"], 1)
        self.assertEqual(row["seasonCount"], 4)

    # --- the tile's poster ---------------------------------------------------

    def test_a_series_reports_its_own_uploaded_poster(self):
        """The Library tile could only borrow a disc's cover, because the row it
        is given carried no artwork at all -- so a poster set on a series showed
        on the series page and not on its tile, and the two disagreed about the
        same show."""
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            self._disc(conn, series_id=series_id)
            media_id = self._poster(conn, series_id)

            row = self._listed(conn, series_id)

        self.assertEqual(row["posterUrl"], f"/api/next/media/assets/{media_id}")

    def test_a_fetched_poster_is_reported_as_its_source(self):
        """The other write path. A refresh stores only the `entity_media` link
        and never mirrors the URL into `series.metadata`, so a fix reading the
        mirror would pass the upload test above and still fail here."""
        with self.connect() as conn:
            series_id = self._series(conn, "Yellowstone")
            self._disc(conn, series_id=series_id)
            self._poster(conn, series_id, local=False, source_url="https://example.invalid/p.jpg")

            row = self._listed(conn, series_id)

        self.assertEqual(row["posterUrl"], "https://example.invalid/p.jpg")

    def test_a_series_without_its_own_poster_reports_none(self):
        """The backend does not borrow. Falling back to a disc's cover is the
        frontend's job, where the disc rows already are -- and it stays the right
        answer for a series nobody has given artwork to, because an empty tile
        reads as a bug rather than as a gap."""
        with self.connect() as conn:
            series_id = self._series(conn, "Deadwood")
            self._disc(conn, series_id=series_id)

            row = self._listed(conn, series_id)

        self.assertIsNone(row["posterUrl"])

    def test_the_chosen_poster_beats_one_merely_offered(self):
        """A refresh links every artwork it found as an option and marks one
        primary. Reporting whichever sorted first would hand the tile an image
        the user rejected."""
        with self.connect() as conn:
            series_id = self._series(conn, "Justified")
            self._disc(conn, series_id=series_id)
            self._poster(conn, series_id, is_primary=False, sort_order=1)
            chosen = self._poster(conn, series_id, is_primary=True, sort_order=0)

            row = self._listed(conn, series_id)

        self.assertEqual(row["posterUrl"], f"/api/next/media/assets/{chosen}")

    def test_an_option_is_still_shown_when_nothing_is_primary(self):
        """Matches `mediaAssetImage` on the series page, which prefers the
        primary and accepts any. Filtering on `is_primary` instead would leave
        the tile blank next to a page showing a picture."""
        with self.connect() as conn:
            series_id = self._series(conn, "Rectify")
            self._disc(conn, series_id=series_id)
            offered = self._poster(conn, series_id, is_primary=False, sort_order=1)

            row = self._listed(conn, series_id)

        self.assertEqual(row["posterUrl"], f"/api/next/media/assets/{offered}")

    def test_artwork_does_not_multiply_a_series_or_its_counts(self):
        """One tile per series, whatever its artwork.

        Several artwork rows joined to an aggregate is the shape that inflates a
        count or splits a row, so it is worth pinning even though the current
        query cannot do either -- the join is correlated on the aggregated row
        and returns at most one asset. This holds the outcome, not the
        implementation: a later rewrite that moves the join is free to, and this
        is what tells it whether it got away with it."""
        with self.connect() as conn:
            series_id = self._series(conn, "The Wire")
            for number in (1, 2, 3):
                self._disc(conn, series_id=series_id, title=f"Season {number}")
                self._season(conn, series_id, number)
            self._poster(conn, series_id, is_primary=False, sort_order=0)
            self._poster(conn, series_id, is_primary=False, sort_order=0)

            rows = [
                row
                for row in next_app.collection_series_preview_entities(conn)
                if row["id"] == str(series_id)
            ]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["discCount"], 3)
        self.assertEqual(rows[0]["seasonCount"], 3)

    def test_a_deleted_or_hidden_link_is_not_a_poster(self):
        """Both filters the lateral carries. Deleting a series' artwork leaves
        the link behind with a `deleted_at`, so ignoring it would keep showing
        the picture the user removed."""
        with self.connect() as conn:
            series_id = self._series(conn, "Carnivale")
            self._disc(conn, series_id=series_id)
            removed = self._poster(conn, series_id)
            hidden = self._poster(conn, series_id, is_primary=False, sort_order=1)
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE entity_media SET deleted_at = now() WHERE media_id = %s", (removed,)
                )
                cur.execute(
                    "UPDATE entity_media SET hidden_at = now() WHERE media_id = %s", (hidden,)
                )
            conn.commit()

            row = self._listed(conn, series_id)

        self.assertIsNone(row["posterUrl"])

    # --- the per-movie reference --------------------------------------------

    def test_a_disc_carries_its_series_and_a_film_carries_none(self):
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            linked = self._disc(conn, series_id=series_id)
            loose = self._disc(conn)

            rows = next_app.attach_movie_series_membership(
                conn,
                [{"id": linked}, {"id": loose}],
            )

        self.assertEqual(rows[0]["series"]["id"], str(series_id))
        self.assertEqual(rows[0]["series"]["title"], "Fargo")
        self.assertIsNone(rows[1]["series"])

    def test_the_key_is_always_present_even_with_nothing_to_say(self):
        """The frontend groups on `movie.series?.id`; an absent key and a null
        one must not read differently there."""
        with self.connect() as conn:
            rows = next_app.attach_movie_series_membership(conn, [{"id": self._disc(conn)}])
        self.assertIn("series", rows[0])
        self.assertIsNone(rows[0]["series"])

    # --- the paged half of the same list --------------------------------------

    def test_a_disc_past_the_first_page_still_carries_its_series(self):
        """The Library is served twice: a small first-paint snapshot, and the
        paged hydration that loads everything behind it. Only the snapshot
        attached the series, so a disc sitting past the page boundary arrived
        without one -- its tile could not see it, and because nothing else had
        claimed the disc it reappeared beside the tile as a loose one.

        Sort titles decide which side of the boundary a disc lands on, and a
        series' discs sort adjacently, so in practice a whole show fell out at
        once and its tile vanished. The offset here is what reproduces that; a
        page starting at zero passes with the bug live.
        """
        with self.connect() as conn:
            series_id = self._series(conn, "Zulu Show")
            # Three films sorting ahead of the discs, so the discs are only
            # reachable at a non-zero offset.
            for index in range(3):
                self._disc(conn, title=f"Aaa Film {index}")
            self._disc(conn, series_id=series_id, title="Zulu Show S1")

            page = next_library_data.library_movie_page(conn, user=None, limit=50, offset=3)

        discs = [row for row in page["items"] if row["title"] == "Zulu Show S1"]
        self.assertEqual(len(discs), 1, "the disc must be on this page for the test to mean anything")
        self.assertIsNotNone(discs[0]["series"], "a paged disc lost its series")
        self.assertEqual(discs[0]["series"]["id"], str(series_id))

    def test_a_page_and_the_snapshot_shape_a_row_identically(self):
        """The invariant, rather than the implementation: a page is the rest of
        the very same list, so a row must not depend on which side of the page
        boundary it happened to fall. Stated this way it survives a later
        refactor of how the two paths share their enrichment."""
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            self._disc(conn, series_id=series_id, title="Fargo S1")
            self._disc(conn, title="A Loose Film")

            page = next_library_data.library_movie_page(conn, user=None, limit=500, offset=0)
            snapshot = next_app.collection_dashboard_snapshot(conn)

        paged = {str(row["id"]): row["series"] for row in page["items"]}
        for row in snapshot["movies"]:
            movie_id = str(row["id"])
            if movie_id in paged:
                self.assertEqual(paged[movie_id], row["series"], f"row {movie_id} differs between the two paths")

    def test_a_paged_film_reports_a_null_series_rather_than_no_key(self):
        """Same contract the snapshot keeps: the frontend reads
        `movie.series?.id`, so an absent key and a null one must not differ."""
        with self.connect() as conn:
            self._disc(conn, title="A Loose Film")
            page = next_library_data.library_movie_page(conn, user=None, limit=500, offset=0)

        loose = [row for row in page["items"] if row["title"] == "A Loose Film"]
        self.assertEqual(len(loose), 1)
        self.assertIn("series", loose[0])
        self.assertIsNone(loose[0]["series"])

    # --- season coverage -----------------------------------------------------

    def test_coverage_names_the_seasons_a_disc_actually_carries(self):
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            season_two = self._season(conn, series_id, 2)
            self._season(conn, series_id, 1)
            movie_id = self._disc(conn, series_id=series_id)
            self._cover(conn, movie_id, series_id, season_two)

            rows = [
                row
                for row in next_app.collection_series_membership_entities(conn)
                if row["movieId"] == str(movie_id)
            ]

        self.assertEqual([row["seasonNumber"] for row in rows], [2])

    def test_a_complete_series_set_names_no_season_at_all(self):
        """Zero coverage rows on a linked disc is the complete-series case. It is
        not the same as covering season zero, and the tile has to be able to tell
        those apart."""
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            self._season(conn, series_id, 1)
            movie_id = self._disc(conn, series_id=series_id)

            rows = [
                row
                for row in next_app.collection_series_membership_entities(conn)
                if row["movieId"] == str(movie_id)
            ]

        self.assertEqual(rows, [])

    def test_a_deleted_season_stops_being_reported_as_covered(self):
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            season_id = self._season(conn, series_id, 1)
            movie_id = self._disc(conn, series_id=series_id)
            self._cover(conn, movie_id, series_id, season_id)
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE series_seasons SET deleted_at = now() WHERE id = %s", (season_id,)
                )
            conn.commit()

            rows = [
                row
                for row in next_app.collection_series_membership_entities(conn)
                if row["movieId"] == str(movie_id)
            ]

        self.assertEqual(rows, [])

    # --- the snapshot itself -------------------------------------------------

    def test_the_dashboard_snapshot_carries_both_arrays(self):
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            self._disc(conn, series_id=series_id)

            snapshot = next_app.collection_dashboard_snapshot(conn)

        self.assertIn("series", snapshot)
        self.assertIn("seriesSeasonCoverage", snapshot)
        self.assertTrue(any(row["id"] == str(series_id) for row in snapshot["series"]))


    # --- borrowing a season's poster ----------------------------------------

    def _season_poster(self, conn, season_id, *, is_primary=True, sort_order=0):
        """Give a season a poster the way `refresh_series_seasons` does: an asset
        plus a link under `entity_type='series_season'`."""
        media_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO media_assets (id, kind, variant, storage_backend, storage_key,
                                          source_url, provider_id, sha256)
                VALUES (%s, 'poster', 'display', 'remote', %s, %s, %s, %s)
                """,
                (
                    media_id,
                    f"remote/{media_id}",
                    f"https://images.example/{media_id}.jpg",
                    f"{PREFIX}-{media_id}",
                    hashlib.sha256(str(media_id).encode()).hexdigest(),
                ),
            )
            cur.execute(
                """
                INSERT INTO entity_media (entity_type, entity_id, media_id, role, is_primary, sort_order)
                VALUES ('series_season', %s, %s, 'poster', %s, %s)
                """,
                (season_id, media_id, is_primary, sort_order),
            )
        conn.commit()
        return media_id

    def test_a_series_with_no_poster_offers_its_first_season_s(self):
        """A season poster is artwork of the show; a disc cover is a photograph
        of a package. Both stand in, but only one is a picture of the thing the
        tile names -- so the tile is handed the season's as well as the disc's
        and can prefer it."""
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            first = self._season(conn, series_id, 1)
            second = self._season(conn, series_id, 2)
            self._season_poster(conn, second)
            wanted = self._season_poster(conn, first)
            self._disc(conn, series_id=series_id)

            row = self._listed(conn, series_id)

        self.assertIsNone(row["posterUrl"], "the series itself still has none")
        self.assertIn(str(wanted), row["seasonPosterUrl"], "season 1, not season 2")

    def test_the_season_poster_never_outranks_the_series_own(self):
        """The whole "unless one was uploaded and locked" clause. A series' own
        poster is the chosen one -- `entity_media` with `is_primary`, which is
        what an upload and the artwork tab both write -- so a borrowed season
        poster is offered beside it and never instead of it."""
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            season = self._season(conn, series_id, 1)
            self._season_poster(conn, season)
            own = self._poster(conn, series_id)
            self._disc(conn, series_id=series_id)

            row = self._listed(conn, series_id)

        self.assertIn(str(own), row["posterUrl"])
        self.assertNotIn(str(own), row["seasonPosterUrl"] or "")

    def test_specials_do_not_become_the_face_of_a_show(self):
        """Season 0 sorts ahead of season 1 and is the least recognisable face a
        show has. It is skipped while *guessing*; the series page still shows it
        when a reader selects it, because that is no longer a guess."""
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            specials = self._season(conn, series_id, 0)
            first = self._season(conn, series_id, 1)
            self._season_poster(conn, specials)
            wanted = self._season_poster(conn, first)
            self._disc(conn, series_id=series_id)

            row = self._listed(conn, series_id)

        self.assertIn(str(wanted), row["seasonPosterUrl"])

    def test_a_show_whose_only_artwork_is_specials_falls_through_to_a_disc(self):
        """Skipping season 0 must leave nothing rather than reach past it, so the
        disc stays the last resort instead of being quietly overtaken."""
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            specials = self._season(conn, series_id, 0)
            self._season_poster(conn, specials)
            self._disc(conn, series_id=series_id)

            row = self._listed(conn, series_id)

        self.assertIsNone(row["seasonPosterUrl"])

    def test_a_hidden_or_deleted_season_poster_is_not_borrowed(self):
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            season = self._season(conn, series_id, 1)
            media_id = self._season_poster(conn, season)
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE entity_media SET hidden_at = now() WHERE media_id = %s",
                    (media_id,),
                )
            conn.commit()
            self._disc(conn, series_id=series_id)

            row = self._listed(conn, series_id)

        self.assertIsNone(row["seasonPosterUrl"])


    # --- a disc inherits the poster of the season it carries -----------------

    def _metadata(self, conn, movie_id, patch):
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE movies SET metadata = COALESCE(metadata, '{}'::jsonb) || %s WHERE id = %s",
                (json.dumps(patch), movie_id),
            )
        conn.commit()

    def _poster_of(self, conn, movie_id):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT metadata->>'poster_url' AS url, metadata->>'poster_from_season' AS inherited"
                " FROM movies WHERE id = %s",
                (movie_id,),
            )
            return cur.fetchone()

    def _inherit(self, conn, movie_ids=None):
        with conn.cursor() as cur:
            written = next_metadata.apply_season_poster_inheritance(cur, movie_ids)
        conn.commit()
        return written

    def test_a_disc_with_no_poster_takes_the_season_s(self):
        """The reported case, and the reason this is a write rather than a
        display fallback: the disc has to *have* a poster, or the sync delta
        carries nothing and the iOS app still shows an empty tile."""
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            season = self._season(conn, series_id, 2)
            self._season_poster(conn, season)
            movie_id = self._disc(conn, series_id=series_id, title="Season 2")
            self._cover(conn, movie_id, series_id, season)

            self.assertEqual(self._inherit(conn, [movie_id]), 1)
            row = self._poster_of(conn, movie_id)

        self.assertTrue(row["url"].startswith("https://"))
        self.assertEqual(row["inherited"], "true", "the provenance flag has to travel with it")

    def test_a_set_of_several_seasons_takes_the_lowest_numbered(self):
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            third = self._season(conn, series_id, 3)
            first = self._season(conn, series_id, 1)
            self._season_poster(conn, third)
            wanted = self._season_poster(conn, first)
            movie_id = self._disc(conn, series_id=series_id, title="Seasons 1-3")
            self._cover(conn, movie_id, series_id, third)
            self._cover(conn, movie_id, series_id, first)

            self._inherit(conn, [movie_id])
            row = self._poster_of(conn, movie_id)

        self.assertIn(str(wanted), row["url"])

    def test_a_disc_that_has_its_own_poster_is_left_alone(self):
        """The first of the two conditions the feature was asked for with. A
        poster from the metadata source is simply not ours to replace."""
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            season = self._season(conn, series_id, 1)
            self._season_poster(conn, season)
            movie_id = self._disc(conn, series_id=series_id)
            self._cover(conn, movie_id, series_id, season)
            self._metadata(conn, movie_id, {"poster_url": "https://img.example/from-tmdb.jpg"})

            self.assertEqual(self._inherit(conn, [movie_id]), 0)
            row = self._poster_of(conn, movie_id)

        self.assertEqual(row["url"], "https://img.example/from-tmdb.jpg")
        self.assertIsNone(row["inherited"])

    def test_a_locked_disc_is_left_alone(self):
        """The second condition. `poster_locked` is the operator saying to leave
        this disc's artwork alone, and this is artwork."""
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            season = self._season(conn, series_id, 1)
            self._season_poster(conn, season)
            movie_id = self._disc(conn, series_id=series_id)
            self._cover(conn, movie_id, series_id, season)
            self._metadata(conn, movie_id, {"poster_locked": True})

            self.assertEqual(self._inherit(conn, [movie_id]), 0)

            self.assertIsNone(self._poster_of(conn, movie_id)["url"])

    def test_an_inherited_poster_keeps_tracking_the_season(self):
        """What the provenance flag buys. A poster this wrote may be replaced
        when the season's artwork changes; one the disc owns may not."""
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            season = self._season(conn, series_id, 1)
            self._season_poster(conn, season)
            movie_id = self._disc(conn, series_id=series_id)
            self._cover(conn, movie_id, series_id, season)
            self._inherit(conn, [movie_id])
            first = self._poster_of(conn, movie_id)["url"]

            with conn.cursor() as cur:
                cur.execute("DELETE FROM entity_media WHERE entity_type='series_season' AND entity_id=%s", (season,))
            conn.commit()
            replacement = self._season_poster(conn, season)

            self.assertEqual(self._inherit(conn, [movie_id]), 1)
            second = self._poster_of(conn, movie_id)["url"]

        self.assertNotEqual(first, second)
        self.assertIn(str(replacement), second)

    def test_running_twice_writes_nothing_the_second_time(self):
        """`updated_at` feeds the sync delta. A no-op write would wake every
        client for a poster that did not move."""
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            season = self._season(conn, series_id, 1)
            self._season_poster(conn, season)
            movie_id = self._disc(conn, series_id=series_id)
            self._cover(conn, movie_id, series_id, season)

            self.assertEqual(self._inherit(conn, [movie_id]), 1)
            self.assertEqual(self._inherit(conn, [movie_id]), 0)

    def test_specials_count_when_the_disc_names_them(self):
        """A curator who filed a disc under the specials has said what is in the
        box -- which is why season 0 is excluded when a series *tile* guesses at
        artwork and included here."""
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            specials = self._season(conn, series_id, 0)
            wanted = self._season_poster(conn, specials)
            movie_id = self._disc(conn, series_id=series_id, title="Specials")
            self._cover(conn, movie_id, series_id, specials)

            self._inherit(conn, [movie_id])

            self.assertIn(str(wanted), self._poster_of(conn, movie_id)["url"])

    def test_a_film_inherits_nothing(self):
        with self.connect() as conn:
            movie_id = self._disc(conn, title="Heat")

            self.assertEqual(self._inherit(conn, [movie_id]), 0)
            self.assertIsNone(self._poster_of(conn, movie_id)["url"])

    def test_unlinking_a_disc_drops_the_inherited_poster(self):
        """Otherwise the old season's poster stays behind as though the disc
        owned it -- and it would then survive every later run, because the check
        stops at a disc that already has a poster it did not write."""
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            season = self._season(conn, series_id, 1)
            self._season_poster(conn, season)
            movie_id = self._disc(conn, series_id=series_id)
            self._cover(conn, movie_id, series_id, season)
            self._inherit(conn, [movie_id])
            self.assertIsNotNone(self._poster_of(conn, movie_id)["url"])

            with conn.cursor() as cur:
                cur.execute("DELETE FROM movie_seasons WHERE movie_id = %s", (movie_id,))
                next_metadata.clear_orphaned_season_poster(cur, movie_id)
            conn.commit()
            row = self._poster_of(conn, movie_id)

        self.assertIsNone(row["url"])
        self.assertIsNone(row["inherited"])

    def test_unlinking_never_removes_a_poster_the_disc_owns(self):
        with self.connect() as conn:
            movie_id = self._disc(conn, title="Heat")
            self._metadata(conn, movie_id, {"poster_url": "https://img.example/own.jpg"})

            with conn.cursor() as cur:
                next_metadata.clear_orphaned_season_poster(cur, movie_id)
            conn.commit()

            self.assertEqual(self._poster_of(conn, movie_id)["url"], "https://img.example/own.jpg")

    def test_a_sweep_with_no_id_list_reaches_every_linked_disc(self):
        """What migration 078 needs: the discs that already exist are the ones a
        user is looking at today."""
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            season = self._season(conn, series_id, 1)
            self._season_poster(conn, season)
            first = self._disc(conn, series_id=series_id, title="A")
            second = self._disc(conn, series_id=series_id, title="B")
            self._cover(conn, first, series_id, season)
            self._cover(conn, second, series_id, season)

            self.assertGreaterEqual(self._inherit(conn, None), 2)

            self.assertIsNotNone(self._poster_of(conn, first)["url"])
            self.assertIsNotNone(self._poster_of(conn, second)["url"])

    def test_selecting_a_season_in_the_series_tab_gives_the_disc_its_poster(self):
        """End to end through the path the Series tab actually uses, rather than
        the helper on its own. Selecting the season is the moment the user
        expects the poster to appear."""
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            season = self._season(conn, series_id, 2)
            wanted = self._season_poster(conn, season)
            movie_id = self._disc(conn, title="Some Box")

            with conn.cursor() as cur:
                next_app.apply_movie_series_assignment(
                    cur,
                    movie_id,
                    {"series_id": series_id, "season_ids": [season]},
                    media_type=next_app.MEDIA_TYPE_SHOW,
                )
            conn.commit()
            row = self._poster_of(conn, movie_id)

        self.assertIn(str(wanted), row["url"])
        self.assertEqual(row["inherited"], "true")

    def test_moving_a_disc_to_a_season_with_no_artwork_drops_the_old_poster(self):
        """The clearing step, through the same path. Keeping the previous
        season's poster would leave the disc claiming artwork for something it no
        longer carries."""
        with self.connect() as conn:
            series_id = self._series(conn, "Fargo")
            first = self._season(conn, series_id, 1)
            bare = self._season(conn, series_id, 2)
            self._season_poster(conn, first)
            movie_id = self._disc(conn, title="Some Box")

            with conn.cursor() as cur:
                next_app.apply_movie_series_assignment(
                    cur, movie_id, {"series_id": series_id, "season_ids": [first]},
                    media_type=next_app.MEDIA_TYPE_SHOW,
                )
            conn.commit()
            self.assertIsNotNone(self._poster_of(conn, movie_id)["url"])

            with conn.cursor() as cur:
                next_app.apply_movie_series_assignment(
                    cur, movie_id, {"series_id": series_id, "season_ids": [bare]},
                    media_type=next_app.MEDIA_TYPE_SHOW,
                )
            conn.commit()

            self.assertIsNone(self._poster_of(conn, movie_id)["url"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
