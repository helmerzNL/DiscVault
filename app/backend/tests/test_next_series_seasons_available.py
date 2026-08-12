"""Offering the seasons a source knows, so a person can say which they own.

A series created any way other than the MovieVault feed had no seasons at all,
and no route to gain one: `POST /api/next/series/<id>/seasons` existed, nothing
called it, and the edit form hides its season picker when the list is empty. So
the seasons card showed nothing, no disc could be marked as covering a season,
and episodes -- which hang off a season -- were unreachable. The Add screen had
just become a way to create exactly such a series.

(Card, not tab: it sat in the Overview panel then. It has a tab of its own now,
which `WhereTheSeasonsLiveTests` below is about.)

The rule that made this a design question rather than an oversight is
`test_a_season_the_feed_never_recorded_is_not_created`: enrichment does not
create seasons nobody owns. The source knows every season the show has; the
collection knows the ones a disc covers. Those are different facts, and this
route serves the first so a person can assert the second.

What is pinned here is that separation -- reading and offering here, storing
somewhere else -- and that a failed source stays distinguishable from a series
with nothing to offer.
"""

import os
import re
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


DATABASE_URL = os.environ.get("DATABASE_URL")
PREFIX = "seasons-available-test"


class TheOfferNeverStoresTests(unittest.TestCase):
    """Source-level, because the guarantee is about what the code does not do.

    A test that calls the function and counts rows proves it did not write *this
    time*. Reading the body proves there is no write to reach -- which is the
    claim the section makes.
    """

    def _body(self, name):
        import inspect

        return inspect.getsource(getattr(next_metadata, name))

    def test_the_offer_issues_no_write(self):
        body = self._body("available_series_seasons")
        for statement in ("INSERT", "UPDATE", "DELETE"):
            with self.subTest(statement=statement):
                self.assertNotIn(statement, body)

    def test_it_reuses_the_refresh_s_own_consult(self):
        """Not a second copy of the source loop. The picker must offer what the
        refresh would describe, and two hand-written loops drift."""
        self.assertIn("consult_series_sources(", self._body("available_series_seasons"))

    def test_the_route_does_not_create_seasons_either(self):
        """The tempting shortcut is one call that fetches and imports. That is
        the version which claims every season TMDB knows, so a shelf holding
        series 1 and 2 reports eight."""
        import inspect

        source = inspect.getsource(next_app)
        start = source.index("def available_series_seasons_route(")
        body = source[start:source.index("\n    @flask_app.", start)]
        self.assertNotIn("INSERT", body)
        self.assertIn("available_series_seasons(", body)


class MergeCarriesTheLabelFieldsTests(unittest.TestCase):
    """"Season 3" alone is not enough to choose from.

    The merge dropped title, year and episode count while its only consumer was
    the refresh, which writes overviews and nothing else. Picking the right box
    off a shelf is recognition, not counting, so the picker needs them -- on the
    same first-source-wins terms as everything else.
    """

    def test_a_season_carries_its_label(self):
        merged = next_metadata.merge_series_details(
            [
                (
                    "tmdb",
                    {
                        "series": {},
                        "seasons": [
                            {
                                "seasonNumber": 2,
                                "title": "Season Two",
                                "year": "2012",
                                "episodeCount": 10,
                            }
                        ],
                    },
                )
            ]
        )
        season = merged["seasons"][2]
        self.assertEqual(season["title"], "Season Two")
        self.assertEqual(season["year"], "2012")
        self.assertEqual(season["episodeCount"], 10)

    def test_the_first_source_still_wins_per_field(self):
        merged = next_metadata.merge_series_details(
            [
                ("tmdb", {"series": {}, "seasons": [{"seasonNumber": 1, "title": "First"}]}),
                (
                    "tvdb",
                    {
                        "series": {},
                        "seasons": [{"seasonNumber": 1, "title": "Second", "episodeCount": 13}],
                    },
                ),
            ]
        )
        season = merged["seasons"][1]
        self.assertEqual(season["title"], "First")
        # The later source still completes what the first left empty.
        self.assertEqual(season["episodeCount"], 13)

    def test_a_season_the_source_says_nothing_about_stays_empty(self):
        """The rule `test_a_season_the_source_mentions_but_says_nothing_about_is_reported`
        pins: reported, but carrying nothing. Adding label fields must not turn
        an empty entry into a furnished one."""
        merged = next_metadata.merge_series_details(
            [("tmdb", {"series": {}, "seasons": [{"seasonNumber": 3}]})]
        )
        self.assertEqual(merged["seasons"][3], {})

    def test_an_episode_count_that_is_not_a_count_is_ignored(self):
        for value in (None, "ten", True, -1):
            with self.subTest(value=value):
                merged = next_metadata.merge_series_details(
                    [("tmdb", {"series": {}, "seasons": [{"seasonNumber": 1, "episodeCount": value}]})]
                )
                self.assertNotIn("episodeCount", merged["seasons"][1])


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class SeasonsAvailableRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = next_app.app.test_client()

    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM series_seasons WHERE series_id IN (SELECT id FROM series WHERE public_id LIKE %s)",
                    (f"{PREFIX}-%",),
                )
                cur.execute(
                    "DELETE FROM series_identifiers WHERE series_id IN (SELECT id FROM series WHERE public_id LIKE %s)",
                    (f"{PREFIX}-%",),
                )
                cur.execute("DELETE FROM series WHERE public_id LIKE %s", (f"{PREFIX}-%",))
            conn.commit()

    def _series(self, conn, title="Example Show"):
        series_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO series (id, public_id, title, sort_title) VALUES (%s,%s,%s,%s)",
                (series_id, f"{PREFIX}-{series_id}", title, title),
            )
        conn.commit()
        return series_id

    def test_a_series_with_no_identifier_says_so(self):
        """The same dead end the identity picker exists to escape, reported in
        the same words -- so the page sends the reader there instead of leaving
        them to conclude the show has no seasons."""
        with self.connect() as conn:
            series_id = self._series(conn)
            result = next_metadata.available_series_seasons(conn, series_id)
        self.assertEqual(result["reason"], "no series identifier")
        self.assertEqual(result["seasons"], [])

    def test_the_route_answers_and_writes_nothing(self):
        with self.connect() as conn:
            series_id = self._series(conn)

        response = self.client.get(f"/api/next/series/{series_id}/seasons/available")
        self.assertEqual(response.status_code, 200, response.data[:300])
        self.assertIn("seasons", response.get_json()["result"])

        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS n FROM series_seasons WHERE series_id = %s", (series_id,)
                )
                self.assertEqual(cur.fetchone()["n"], 0)

    def test_a_missing_series_is_a_404(self):
        self.assertEqual(
            self.client.get(f"/api/next/series/{uuid.uuid4()}/seasons/available").status_code,
            404,
        )

    def test_an_existing_season_is_marked_rather_than_hidden(self):
        """Filtering the row out would silently answer a different question --
        "what is missing" -- and make a complete series look like an empty
        result. `exists` keeps the list readable as the whole show with your part
        of it marked."""
        with self.connect() as conn:
            series_id = self._series(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO series_seasons (id, public_id, series_id, season_number)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (uuid.uuid4(), f"{PREFIX}-season-{uuid.uuid4().hex[:8]}", series_id, 1),
                )
            conn.commit()

            def fake_consult(_conn, _row, _identifiers):
                return (
                    [("tmdb", {"status": "hit", "series": {}, "seasons": [
                        {"seasonNumber": 1, "title": "One"},
                        {"seasonNumber": 2, "title": "Two"},
                    ]})],
                    [],
                )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO series_identifiers (series_id, provider_id, identifier_type, identifier)
                    VALUES (%s, 'tmdb', 'tmdb_tv', '1399')
                    """,
                    (series_id,),
                )
            conn.commit()

            original_consult = next_metadata.consult_series_sources
            original_plugins = next_metadata.series_detail_source_plugins
            next_metadata.consult_series_sources = fake_consult
            next_metadata.series_detail_source_plugins = lambda _conn: [{"id": "tmdb"}]
            try:
                result = next_metadata.available_series_seasons(conn, series_id)
            finally:
                next_metadata.consult_series_sources = original_consult
                next_metadata.series_detail_source_plugins = original_plugins

        by_number = {season["seasonNumber"]: season for season in result["seasons"]}
        self.assertEqual(sorted(by_number), [1, 2])
        self.assertTrue(by_number[1]["exists"])
        self.assertFalse(by_number[2]["exists"])


class WhereTheSeasonsLiveTests(unittest.TestCase):
    """Season management has a tab of its own, and its absence explains itself.

    The picker was first put beside the existing Seasons card, which sat in the
    series page's **Overview** panel -- the path of least resistance rather than
    where a person looks for season management. The page opens on Discs, so the
    button was two clicks away behind a name that promises something else, and
    the first report was that it did not exist.

    Sliced on panel boundaries in the rendered document rather than checked by
    line number or by a bare substring: a bare `in` on the whole page passes
    while the markup sits in any panel at all, which is exactly the thing that
    was wrong.
    """

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("JWT_SECRET", "test-secret")
        from app.backend import next_app  # noqa: F401  (registers the module the view imports)
        from app.backend.next_views_ui import ui_preview_html

        cls.html = ui_preview_html(app_mode=True)

    def _panel(self, panel_id, next_panel_id):
        start = self.html.index(f'id="{panel_id}"')
        end = self.html.index(f'id="{next_panel_id}"')
        return self.html[start:end]

    def test_the_submenu_offers_a_seasons_tab(self):
        self.assertIn('data-detail-panel="seriesDetailSeasonsPanel"', self.html)

    def test_it_sits_between_the_discs_and_the_overview(self):
        """Seasons belong nearer the discs than the metadata, and the tab order
        is the only thing that says so."""
        order = re.findall(r'data-detail-panel="(seriesDetail\w+)"', self.html)
        self.assertEqual(
            order[:3],
            ["seriesDetailDiscsPanel", "seriesDetailSeasonsPanel", "seriesDetailOverviewPanel"],
        )

    def test_the_card_moved_rather_than_being_copied(self):
        seasons = self._panel("seriesDetailSeasonsPanel", "seriesDetailOverviewPanel")
        overview = self._panel("seriesDetailOverviewPanel", "seriesDetailPostersPanel")
        for marker in ('id="seriesSeasonPickerButton"', 'id="seriesDetailSeasons"', 'id="seriesSeasonPicker"'):
            with self.subTest(marker=marker):
                self.assertIn(marker, seasons)
                self.assertNotIn(marker, overview)
        # One button, not two: the page renders once and a stray copy would take
        # the same id.
        self.assertEqual(self.html.count('id="seriesSeasonPickerButton"'), 1)

    def test_the_edit_form_stays_on_the_overview_tab(self):
        """`setSeriesEditPanelVisible` activates the Overview panel when the edit
        form opens. Moving the form too would have made that line point at the
        wrong tab, silently."""
        overview = self._panel("seriesDetailOverviewPanel", "seriesDetailPostersPanel")
        self.assertIn('id="seriesEditForm"', overview)
        self.assertIn('activateDetailTab("seriesDetail", "seriesDetailOverviewPanel")', self.html)


class TheEpisodeGateExplainsItselfTests(unittest.TestCase):
    """An affordance that vanishes without a word is indistinguishable from a
    series that has none.

    The episode button appears only with Collectors mode on, which is a real
    cost decision -- one request per season at the source -- and not something
    to leave a reader guessing at. This is the same collapse of distinct
    outcomes the refresh message, the identity search and the plugin error
    string each had to be fixed for.
    """

    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(__file__), "..", "next_views_ui.py")
        with open(os.path.abspath(path), encoding="utf-8") as handle:
            cls.source = handle.read()

    def _rows_function(self):
        start = self.source.index("function seriesSeasonRowsHtml(")
        return self.source[start:self.source.index("\n    function ", start + 1)]

    def test_the_reason_is_shown_when_the_switch_is_off(self):
        body = self._rows_function()
        self.assertIn("episodesCollectorsHint", body)
        self.assertIn("collectorsModeEnabled()", body)

    def test_the_browser_carries_it_on_its_one_rendering_path(self):
        """The season browser has a single shape.

        It used to have two -- plain field rows when no season had a poster,
        cards otherwise -- and the hint had to be attached to both or it went
        missing in exactly the case it exists for. One rail and one stage is
        one place to attach it, which is the point of collapsing the split.
        """
        body = self._rows_function()
        browser = body.index('return `<div class="series-season-browser">')
        self.assertIn("episodesHint", body[browser:body.index("\n", browser)])
        self.assertNotIn("return detailFieldRows(", body)

    def test_it_is_said_once_rather_than_per_season(self):
        """Eight seasons must not carry the same sentence eight times -- the
        statement is about the page, not about a season."""
        for name in ("function seriesSeasonRailHtml(", "function seriesSeasonStageHtml("):
            start = self.source.index(name)
            body = self.source[start:self.source.index("\n    function ", start + 1)]
            self.assertNotIn("episodesCollectorsHint", body)

    def test_a_season_that_fetched_nothing_is_not_re_fetched_on_every_visit(self):
        """Stepping back to a season must not ask the source again.

        The Collectors switch exists because opening a season costs one request
        per season upstream. A cache that only remembers the seasons that
        answered would make the empty and failed ones free to re-ask -- and a
        reader stepping along the rail would hit the source once per step.
        """
        start = self.source.index("async function loadSeasonEpisodes(")
        body = self.source[start:self.source.index("\n    function ", start + 1)]
        self.assertIn("seriesSeasonEpisodeCache.set(seasonId, {message:", body)
        self.assertIn("seriesSeasonEpisodeCache.set(seasonId, {episodes:", body)
        # The failed request is the one outcome deliberately left uncached.
        catch = body.index("} catch (error) {")
        self.assertNotIn("seriesSeasonEpisodeCache.set", body[catch:])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
