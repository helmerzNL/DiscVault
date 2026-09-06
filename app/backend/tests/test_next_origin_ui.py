"""The origin filter in the SPA, read as source text.

There is no JavaScript test runner in this repository, so the inline script in
next_views_ui.py is asserted the way every other UI test here asserts it: by
reading it. That catches a wiring step left out, which is the failure mode a
seven-edit filter actually has -- every one of these is silent on its own.

The claim worth the most is test_a_stored_value_is_not_reset_against_loaded_rows.
The genre filter drops a stored selection that matches no loaded movie
(applyGenreOptions), and gets away with it because a genre filter cannot be
saved. These two live inside saved smart filters, and the library hydrates
progressively -- so the same behaviour here would take a filter naming Japan,
find no Japanese films on page 1 of 6, silently reset itself to "any", and show
the user their entire library as though it all matched.
"""

import os
import re
import sys
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEXT_VIEWS_UI_PATH = os.path.join(BACKEND_DIR, "next_views_ui.py")

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import next_export_columns  # noqa: E402


def _source() -> str:
    with open(NEXT_VIEWS_UI_PATH, "r", encoding="utf-8") as handle:
        return handle.read()


# A fixed-width window after the function's name is a proxy for "inside this
# function", and it silently stops being one as soon as the function grows: the
# assertion then fails for a reason that has nothing to do with what it claims.
# Adding the score filter to the same panel pushed three of these windows past
# the function they meant. Brace matching reads the real one, so it cannot go
# stale.
def _function_source(source: str, name: str) -> str:
    start = source.index("function %s(" % name)
    depth = 0
    for position in range(source.index("{", start), len(source)):
        char = source[position]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : position + 1]
    raise AssertionError("unbalanced braces reading %s" % name)


class AdvancedSearchWiringTests(unittest.TestCase):
    """All six edits a Pattern B filter needs, each silent if skipped."""

    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_the_defaults_carry_both_keys(self):
        block = _function_source(self.source, "advancedSearchDefaults")
        self.assertIn('originCountry: "any"', block)
        self.assertIn('originalLanguage: "any"', block)

    def test_the_active_count_counts_both(self):
        block = _function_source(self.source, "advancedSearchActiveCount")
        self.assertIn('normalized.originCountry !== "any"', block)
        self.assertIn('normalized.originalLanguage !== "any"', block)

    def test_the_controls_are_read_back(self):
        block = _function_source(self.source, "readAdvancedSearchControls")
        self.assertIn("advancedOriginCountry", block)
        self.assertIn("advancedOriginLanguage", block)

    def test_the_selects_exist_in_the_panel_markup(self):
        self.assertIn('data-library-advanced-group="origin"', self.source)
        self.assertIn('<select id="advancedOriginCountry">', self.source)
        self.assertIn('<select id="advancedOriginLanguage">', self.source)

    def test_the_movie_predicate_filters_on_both(self):
        start = self.source.index("function movieMatchesAdvancedSearch")
        block = self.source[start : self.source.index("function ", start + 40) + 4000]
        self.assertIn("movieOriginCountryValues(movie).includes(filters.originCountry)", block)
        self.assertIn("movieOriginLanguageValue(movie) !== filters.originalLanguage", block)

    def test_a_container_delegates_the_origin_filters_to_its_members(self):
        # Without this a box set of French films disappears under a France
        # filter, because a container has no origin of its own.
        start = self.source.index("function containerMatchesAdvancedSearch")
        block = self.source[start : start + 2500]
        self.assertIn('filters.originCountry !== "any"', block)
        self.assertIn('filters.originalLanguage !== "any"', block)


class StoredValueSurvivalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_a_stored_value_is_not_reset_against_loaded_rows(self):
        # Validated on shape only. If this ever grows an `includes(...)` check
        # against the loaded movies, a saved smart filter silently widens to the
        # whole library while hydration is still running.
        start = self.source.index("function normalizeAdvancedSearch")
        block = self.source[start : start + 2200]
        self.assertIn("/^[A-Za-z]{2}$/.test", block)
        self.assertIn("normalizeOriginLanguageValue(source.originalLanguage)", block)

    def test_an_unmatched_stored_option_is_appended_rather_than_dropped(self):
        start = self.source.index("function populateOriginFilterSelect")
        block = self.source[start : start + 1200]
        self.assertIn('if (current && current !== "any" && !options.includes(current)) options.push(current);', block)


class DisplayNameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_country_names_come_from_intl_not_from_translation_keys(self):
        # 250 regions across 29 locales is not a translation task, and the
        # App-Guidance rule says the platform answers it.
        start = self.source.index("function regionLabel")
        block = self.source[start : start + 900]
        self.assertIn('new Intl.DisplayNames([localeState.locale], {type: "region"})', block)

    def test_region_label_falls_back_to_the_code_rather_than_a_blank(self):
        start = self.source.index("function regionLabel")
        block = self.source[start : start + 900]
        self.assertIn("return regionDisplayNames.of(raw) || raw;", block)

    def test_rating_country_label_delegates_instead_of_rebuilding_intl(self):
        start = self.source.index("function ratingCountryLabel")
        block = self.source[start : start + 300]
        self.assertIn("regionLabel(code)", block)
        self.assertNotIn("new Intl.DisplayNames", block)

    def test_flags_are_not_used_for_origin(self):
        # app/frontend/flags holds 31 SVGs and the route 404s anything else, so a
        # flag list would be right for Europe and blank for most of the world.
        start = self.source.index("function movieOriginCountryValues")
        block = self.source[start : start + 800]
        self.assertNotIn("flagCodeForCountry", block)
        self.assertNotIn("flagIconHtml", block)


class OriginIsTheFilmNotTheDiscTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_the_accessors_read_the_origin_fields_and_not_the_release_fields(self):
        start = self.source.index("function movieOriginCountryValues")
        block = self.source[start : start + 700]
        self.assertIn("movie?.origin_countries", block)
        self.assertIn("movie?.original_language", block)
        # movie.country / movie.language are the disc's release market.
        self.assertNotIn("movie?.country", block)
        self.assertNotIn("movie?.language", block)

    def test_the_detail_page_shows_both_pairs_so_they_cannot_be_confused(self):
        start = self.source.index('document.getElementById("movieDetailRelease")')
        block = self.source[start : start + 2500]
        self.assertIn('tNext("movieDetail.releaseCountry", "Release country")', block)
        self.assertIn('tNext("movieDetail.originCountry", "Country of origin")', block)
        self.assertIn('tNext("movieDetail.language", "Language")', block)
        self.assertIn('tNext("movieDetail.originalLanguage", "Original language")', block)


class ExportWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_the_new_columns_ship_default_off(self):
        by_key = {column["key"]: column for column in next_export_columns.EXPORT_COLUMNS}
        for key in ("originCountry", "originalLanguage"):
            with self.subTest(key=key):
                self.assertFalse(by_key[key]["default"])

    def test_the_row_builder_reads_the_origin_accessors(self):
        start = self.source.index("function libraryExportRow")
        block = self.source[start : start + 1400]
        self.assertIn("originCountry: movieOriginCountryValues(movie).map(regionLabel)", block)
        self.assertIn("originalLanguage:", block)


class BackfillIsReachableTests(unittest.TestCase):
    """The backfill has a route in the admin screen, not only in the API.

    Both endpoints existed from the start and nothing in the SPA called either
    of them. The library said "{count} films have no origin data yet" beside
    the filters, and the only way to act on that was to issue the POST by hand.
    An operator reading the admin screen would never have found it.

    That is why the assertion is about the *call*, not about the markup. A
    button with no listener, or a panel that renders counters it never loads,
    reproduces the original bug exactly while looking finished.
    """

    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()

    def test_the_admin_screen_reads_the_backfill_counts(self):
        self.assertIn(
            'authApiJson("/api/next/admin/metadata/origin-backfill")', self.source
        )

    def test_the_admin_screen_queues_the_backfill(self):
        start = self.source.index("async function queueAppAdminOriginBackfill")
        block = self.source[start : start + 900]
        self.assertIn('"/api/next/admin/metadata/origin-backfill"', block)
        self.assertIn('method: "POST"', block)
        # The header the whole feature was broken without, twice over.
        self.assertIn('"Content-Type": "application/json"', block)

    def test_the_button_is_wired_to_the_handler(self):
        self.assertIn(
            'document.getElementById("appAdminOriginBackfillButton")?.addEventListener('
            '"click", () => queueAppAdminOriginBackfill());',
            self.source,
        )

    def test_the_counts_load_with_the_metadata_panel(self):
        """Opening the panel must fill the counters, not just offer the button.

        The counters are the answer to "why is my origin filter empty"; a card
        showing two dashes says nothing and invites a pointless run.
        """
        start = self.source.index("async function refreshAppAdminMetadataJobs")
        block = self.source[start : start + 1600]
        self.assertIn("await refreshAppAdminOriginBackfill();", block)

    def test_both_counters_are_rendered(self):
        start = self.source.index("function renderAppAdminOriginBackfill")
        block = self.source[start : start + 900]
        self.assertIn("appAdminOriginBackfillPending", block)
        self.assertIn("appAdminOriginBackfillUnresolvable", block)


class BackfillReportsWhatItDidTests(unittest.TestCase):
    """Pressing the button has to change something the operator can see.

    It did not. The confirmation was written into `appAdminMetadataMessage` and
    then overwritten milliseconds later by `refreshAppAdminMetadataJobs`, which
    writes "Metadata jobs loaded." into the same node; the job list on that same
    panel filters on the refresh job type, so the queued origin jobs never
    appeared there either; and the counters came from the POST, which reads them
    before it queues anything. Three separate silences, and together they are
    the whole of "I pressed Fill in origin data and nothing seems to happen"
    (#719).
    """

    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_the_confirmation_is_set_after_the_refresh_that_would_overwrite_it(self):
        start = self.source.index("async function queueAppAdminOriginBackfill")
        block = self.source[start : start + 2200]
        refresh = block.index("await refreshAppAdminMetadataJobs();")
        confirmation = block.index('tNext("appAdmin.originBackfillQueued"')
        self.assertLess(
            refresh,
            confirmation,
            "the queue confirmation is set before the refresh that overwrites it",
        )

    def test_the_card_names_the_outstanding_jobs(self):
        start = self.source.index("function renderAppAdminOriginBackfill")
        block = self.source[start : start + 4000]
        self.assertIn("appAdminOriginBackfillJobs", block)
        self.assertIn('tNext("appAdmin.originBackfillRunning"', block)

    def test_the_card_reports_the_last_run_rather_than_a_single_done(self):
        # A run that asked TMDB about 100 films and wrote none of them is the
        # outcome worth seeing, and only the split says it.
        start = self.source.index("function renderAppAdminOriginBackfill")
        block = self.source[start : start + 4000]
        self.assertIn('tNext("appAdmin.originBackfillLastRun"', block)
        self.assertIn("{updated}", block)
        self.assertIn("{skipped}", block)
        self.assertIn('tNext("appAdmin.originBackfillLastRunFailed"', block)

    def test_a_tmdb_plugin_too_old_for_origin_is_named_on_the_card(self):
        # Below 1.8.0 the plugin never returns `filmOrigin`, so the job succeeds
        # and writes nothing. Nothing else in the app can say so.
        start = self.source.index("function renderAppAdminOriginBackfill")
        block = self.source[start : start + 4000]
        self.assertIn("originCapable === false", block)
        self.assertIn('tNext("appAdmin.originBackfillPluginTooOld"', block)

    def test_the_button_is_disabled_while_a_run_is_still_outstanding(self):
        start = self.source.index("function renderAppAdminOriginBackfill")
        block = self.source[start : start + 4000]
        self.assertIn('button.disabled = !(Number(counts?.pending) > 0) || outstanding > 0', block)

    def test_the_poll_stops_when_the_queue_empties_or_the_tab_is_left(self):
        """A timer that outlives its panel is a request every five seconds, forever."""
        start = self.source.index("function scheduleAppAdminOriginBackfillPoll")
        block = self.source[start : start + 1200]
        self.assertIn('appAdmin.activeTab !== "metadata"', block)
        self.assertIn('document.getElementById("appAdminOriginBackfillButton")', block)
        self.assertIn("stopAppAdminOriginBackfillPoll();", block)
        self.assertIn("jobs?.outstanding", block)

    def test_reopening_the_panel_picks_a_running_backfill_back_up(self):
        start = self.source.index("async function refreshAppAdminMetadataJobs")
        block = self.source[start : start + 2000]
        self.assertIn("scheduleAppAdminOriginBackfillPoll();", block)


if __name__ == "__main__":
    unittest.main()
