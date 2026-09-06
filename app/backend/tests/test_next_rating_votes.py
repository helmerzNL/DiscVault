"""The vote count behind the external score, and the floor that filters on it.

Requested on #766, out of #719: the score filter can ask for "at least 7" but
not for "and enough people said so", because ``movies.rating`` was stored
without the sample behind it. A 10.0 from three votes outranked an 8.4 from
twelve thousand, and a physical collection is full of exactly the titles with
few votes.

**The claim that carries this file** is the one about NULL. ``rating_votes`` is
nullable and has no DEFAULT, and every layer has to keep "we never asked"
(``NULL``) apart from "we asked, nobody has voted" (``0``). That distinction is
what a vote floor is made of, and it is the mirror image of the mistake the
score column cannot undo: TMDB writes ``vote_average = 0.0`` for an unvoted
title, DiscVault stores it as the string "0", and nothing can now tell that from
a film whose score was never fetched -- which is why the score filter has to
treat every stored zero as "not scored". Collapsing the two here would make the
floor unable to distinguish an unvoted film from an unfetched one, and a
DEFAULT 0 would make every film in an existing library claim TMDB had answered.

The behavioural half runs the shipped code: the normalizer is imported and
called, and the client-side helpers and the predicate are lifted verbatim out of
``next_views_ui.py`` and executed under node. The wiring half is read as source
text, the way the filters beside it are asserted.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEXT_VIEWS_UI_PATH = os.path.join(BACKEND_DIR, "next_views_ui.py")
NEXT_APP_PATH = os.path.join(BACKEND_DIR, "next_app.py")
NEXT_WORKER_PATH = os.path.join(BACKEND_DIR, "next_worker.py")
MIGRATION_PATH = os.path.join(BACKEND_DIR, "migrations_next", "092_movie_rating_votes.sql")
PLUGINS_DIR = os.path.join(BACKEND_DIR, "next_plugins")
I18N_DIR = os.path.abspath(os.path.join(BACKEND_DIR, "..", "frontend", "i18n", "next"))

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import next_metadata  # noqa: E402

NODE = shutil.which("node")

VOTE_KEYS = (
    "collection.scoreMinVotes",
    "collection.voteDataMissing",
    "movieDetail.scoreVotes",
    "appAdmin.votesBackfill",
    "appAdmin.votesBackfillHelp",
    "appAdmin.votesBackfillRun",
    "appAdmin.votesBackfillPluginTooOld",
    "appAdmin.votesBackfillQueueing",
    "appAdmin.votesBackfillQueued",
    "appAdmin.votesBackfillNothing",
)


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _function_source(source: str, name: str) -> str:
    """The whole of ``function name(...) { ... }``, by brace matching."""
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


def _extract_vote_predicate(source: str) -> str:
    """The vote-floor block out of ``movieMatchesAdvancedSearch``, as a function.

    Only the wrapper is written here; every ``return false`` inside it is the
    shipped text, so a rewrite that changes what it decides changes what these
    tests decide too.
    """
    start = source.index("      const minVotes = voteFloorNumber(filters.minVotes);")
    end = source.index("\n      }\n", start) + len("\n      }\n")
    block = source[start:end]
    assert "return false;" in block, block
    return "function movieMatchesVotes(movie, filters) {\n%s      return true;\n}\n" % block


HARNESS_DRIVER = """
const cases = JSON.parse(process.argv[2]);
const has = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
const out = cases.map((item) => {
  if (item.fn === "normalizeVoteFloor") return normalizeVoteFloor(has(item, "value") ? item.value : undefined);
  if (item.fn === "voteFloorNumber") return voteFloorNumber(has(item, "value") ? item.value : undefined);
  if (item.fn === "movieVoteCount") return movieVoteCount(item.movie);
  if (item.fn === "match") return movieMatchesVotes(item.movie, item.filters);
  throw new Error("unknown case: " + item.fn);
});
process.stdout.write(JSON.stringify(out));
"""


def _harness_source() -> str:
    source = _read(NEXT_VIEWS_UI_PATH)
    return "\n".join(
        [
            _function_source(source, "normalizeVoteFloor"),
            _function_source(source, "voteFloorNumber"),
            _function_source(source, "movieVoteCount"),
            _extract_vote_predicate(source),
            HARNESS_DRIVER,
        ]
    )


class VoteCountNormalizerTests(unittest.TestCase):
    """``normalize_vote_count``, imported and called.

    It is the boundary every source converges on, and the reason it exists is
    the same one ``normalize_year_value`` records: ``movies.rating_votes`` is an
    ``integer`` with a non-negative CHECK, so a string bound against it aborts
    the whole UPDATE and loses every other field in the same refresh.
    """

    def normalize(self, value):
        return next_metadata.normalize_vote_count(value)

    def test_a_plain_integer_passes(self):
        self.assertEqual(self.normalize(1204), 1204)

    def test_zero_is_a_real_answer_and_is_kept(self):
        # The whole contract. 0 means TMDB answered and nobody has voted; it is
        # not the same as never having asked, and turning it into None would put
        # the film back in the backfill queue for the life of the install.
        self.assertEqual(self.normalize(0), 0)

    def test_a_group_separated_string_is_read_as_a_count(self):
        # OMDb sends imdbVotes as "2,043,127"; a European locale writes the same
        # number with dots or thin spaces. None of those is a decimal point.
        for raw in ("2,043,127", "2.043.127", "2 043 127"):
            with self.subTest(raw=raw):
                self.assertEqual(self.normalize(raw), 2043127)

    def test_an_unusable_value_is_none_rather_than_zero(self):
        # Coercing to 0 would make the film look answered, and it would be
        # answered wrongly: "nobody voted" is a statement, not a fallback.
        for raw in ("N/A", "", "   ", None, "12K", "many"):
            with self.subTest(raw=raw):
                self.assertIsNone(self.normalize(raw))

    def test_a_negative_count_is_refused(self):
        self.assertIsNone(self.normalize(-3))

    def test_a_bool_is_not_a_count(self):
        # bool is an int subclass, so True would otherwise store as 1 vote.
        self.assertIsNone(self.normalize(True))
        self.assertIsNone(self.normalize(False))

    def test_a_whole_float_is_accepted_and_a_fractional_one_is_not(self):
        # A JSON number that arrived as 1204.0 is still a count. 1204.5 is not,
        # and rounding it would invent a precision the source never had.
        self.assertEqual(self.normalize(1204.0), 1204)
        self.assertIsNone(self.normalize(1204.5))


@unittest.skipUnless(NODE, "node is not available")
class VoteFloorBehaviourTests(unittest.TestCase):
    """The shipped client-side helpers and predicate, executed."""

    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.mkdtemp(prefix="dv-rating-votes-")
        cls.harness = os.path.join(cls.tempdir, "vote-floor-harness.mjs")
        with open(cls.harness, "w", encoding="utf-8") as handle:
            handle.write(_harness_source())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tempdir, ignore_errors=True)

    def run_cases(self, cases):
        result = subprocess.run(
            [NODE, self.harness, json.dumps(cases)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def votes_of(self, *movies):
        return self.run_cases([{"fn": "movieVoteCount", "movie": m} for m in movies])

    def matches(self, filters, *movies):
        return self.run_cases([{"fn": "match", "movie": m, "filters": filters} for m in movies])

    # --- reading the stored value ----------------------------------------

    def test_an_absent_count_reads_as_null_not_zero(self):
        # The client half of the same rule. Read as 0, every unfetched film
        # would claim TMDB had told us nobody voted for it.
        self.assertEqual(self.votes_of({}, {"rating_votes": None}, {"rating_votes": ""}), [None, None, None])

    def test_a_stored_zero_reads_as_zero(self):
        # Deliberately the OPPOSITE call to movieScoreNumber, which has to treat
        # a stored 0 as "not scored" because the score column cannot distinguish
        # the two. This column can, so it must.
        self.assertEqual(self.votes_of({"rating_votes": 0}), [0])

    def test_the_count_is_read_from_either_spelling(self):
        self.assertEqual(
            self.votes_of({"rating_votes": 12043}, {"ratingVotes": 12043}, {"metadata": {"rating_votes": 12043}}),
            [12043, 12043, 12043],
        )

    def test_a_non_numeric_count_is_not_a_count(self):
        self.assertEqual(self.votes_of({"rating_votes": "many"}, {"rating_votes": -1}), [None, None])

    # --- the floor -------------------------------------------------------

    def test_without_a_floor_every_film_passes_including_uncounted_ones(self):
        self.assertEqual(
            self.matches({"minVotes": ""}, {"rating_votes": 12043}, {}),
            [True, True],
        )

    def test_a_floor_is_inclusive_and_keeps_the_well_voted_ones(self):
        self.assertEqual(
            self.matches(
                {"minVotes": "500"},
                {"rating_votes": 12043},
                {"rating_votes": 500},
                {"rating_votes": 499},
            ),
            [True, True, False],
        )

    def test_the_three_vote_ten_is_what_a_floor_removes(self):
        # #766 in one assertion: both films clear "score at least 8", and only
        # one of them clears the floor that makes that score mean something.
        three_votes = {"rating": "10.0", "rating_votes": 3}
        many_votes = {"rating": "8.4", "rating_votes": 12043}
        self.assertEqual(self.matches({"minVotes": "500"}, three_votes, many_votes), [False, True])

    def test_a_film_nobody_voted_for_is_out_of_any_real_floor(self):
        self.assertEqual(self.matches({"minVotes": "1"}, {"rating_votes": 0}), [False])

    def test_a_film_with_no_count_is_out_of_every_floor(self):
        # Including a floor of zero: an unknown sample cannot clear a floor, and
        # this is the case the panel's hint exists to explain rather than leave
        # looking like a broken filter.
        self.assertEqual(self.matches({"minVotes": "0"}, {}), [False])

    def test_a_floor_of_zero_means_the_count_is_known(self):
        # The distinction the column was made nullable for, at the surface a
        # person actually touches: "0" keeps an unvoted film and drops an
        # unfetched one, and nothing else in the app can ask that question.
        self.assertEqual(
            self.matches({"minVotes": "0"}, {"rating_votes": 0}, {}),
            [True, False],
        )

    def test_a_filter_saved_before_this_feature_existed_bounds_nothing(self):
        # Smart filters live in each browser's localStorage and can predate any
        # field. A missing key read as NaN would compare false against
        # everything and quietly require a vote count nobody asked for.
        self.assertEqual(self.matches({}, {"rating_votes": 12043}, {}), [True, True])

    def test_a_floor_is_canonicalised_so_one_filter_is_one_string(self):
        self.assertEqual(
            self.run_cases([{"fn": "normalizeVoteFloor", "value": v} for v in ("500", " 500 ", "1,500", "1.500", "500.0")]),
            ["500", "500", "1500", "1500", "5000"],
        )

    def test_an_empty_or_unparseable_floor_is_no_floor(self):
        self.assertEqual(
            self.run_cases([{"fn": "normalizeVoteFloor", "value": v} for v in ("", "   ", "lots", None)]),
            ["", "", "", ""],
        )

    def test_a_negative_floor_becomes_zero_rather_than_nothing(self):
        # Zero has its own meaning here, so clamping is not the same as dropping.
        self.assertEqual(self.run_cases([{"fn": "normalizeVoteFloor", "value": "-5"}]), ["0"])


class StorageAndPluginTests(unittest.TestCase):
    """The column, and the two plugins that fill it."""

    @classmethod
    def setUpClass(cls):
        cls.migration = _read(MIGRATION_PATH)

    def test_the_column_is_nullable_with_no_default(self):
        # A DEFAULT would collapse "never asked" into "nobody voted" for every
        # film in an existing library, permanently and silently.
        self.assertIn("ADD COLUMN IF NOT EXISTS rating_votes integer", self.migration)
        self.assertNotIn("DEFAULT", self.migration.split("ADD COLUMN IF NOT EXISTS rating_votes")[1].split(";")[0])
        self.assertNotIn("NOT NULL", self.migration.split("ADD COLUMN IF NOT EXISTS rating_votes")[1].split(";")[0])

    def test_a_count_cannot_be_negative(self):
        self.assertIn("movies_rating_votes_non_negative", self.migration)
        self.assertIn("rating_votes IS NULL OR rating_votes >= 0", self.migration)

    def test_the_migration_backfills_nothing(self):
        # Nothing stored can imply a vote count: having a score says nothing
        # about how many people gave it, and deriving one would fabricate the
        # exact fact the column exists to record.
        self.assertNotIn("UPDATE movies SET rating_votes", self.migration)

    def test_the_tmdb_plugin_carries_the_vote_count(self):
        source = _read(os.path.join(PLUGINS_DIR, "tmdb", "plugin.py"))
        self.assertIn('"ratingVotes": data.get("vote_count")', source)

    def test_the_omdb_plugin_parses_its_group_separated_votes(self):
        source = _read(os.path.join(PLUGINS_DIR, "omdb", "plugin.py"))
        self.assertIn('"ratingVotes": _votes(data.get("imdbVotes"))', source)
        self.assertIn("def _votes(value):", source)

    def test_both_plugin_versions_moved(self):
        # A plugin is replaced only by a strictly newer bundled copy, so a build
        # that adds a field without bumping the version never reaches an
        # existing install -- and the backfill then runs, succeeds and writes
        # nothing.
        tmdb = json.loads(_read(os.path.join(PLUGINS_DIR, "tmdb", "manifest.json")))
        omdb = json.loads(_read(os.path.join(PLUGINS_DIR, "omdb", "manifest.json")))
        self.assertEqual(tmdb["version"], "1.9.0")
        self.assertNotEqual(omdb["version"], "1.0.0")

    def test_the_admin_card_requires_the_version_that_emits_the_field(self):
        source = _read(NEXT_APP_PATH)
        tmdb = json.loads(_read(os.path.join(PLUGINS_DIR, "tmdb", "manifest.json")))
        self.assertIn('VOTES_CAPABLE_TMDB_PLUGIN_VERSION = "%s"' % tmdb["version"], source)


class BackendWiringTests(unittest.TestCase):
    """Every layer the value has to survive between TMDB and the filter."""

    @classmethod
    def setUpClass(cls):
        cls.metadata = _read(os.path.join(BACKEND_DIR, "next_metadata.py"))
        cls.app = _read(NEXT_APP_PATH)
        cls.worker = _read(NEXT_WORKER_PATH)

    def test_every_provider_spelling_maps_onto_the_column(self):
        # Without an alias the fall-through drops the raw key into the metadata
        # blob under its literal name, where nothing reads it and nothing says
        # so -- the failure MOVIE_FIELD_ALIASES documents for itself.
        for spelling in ("ratingVotes", "rating_votes", "voteCount", "vote_count"):
            with self.subTest(spelling=spelling):
                self.assertIn('"%s": "rating_votes"' % spelling, self.metadata)

    def test_the_column_is_provider_writable(self):
        self.assertIn("rating_votes", next_metadata.METADATA_MAIN_FIELDS)

    def test_the_column_is_owned_by_the_enrichment_source(self):
        # Same class as `rating` beside it: a plugin that is not the enrichment
        # provider must not write it.
        self.assertIn("rating_votes", next_metadata.METADATA_ENRICHMENT_FIELDS)

    def test_the_value_is_normalized_before_it_reaches_the_writer(self):
        self.assertIn('if key == "rating_votes":', self.metadata)
        self.assertIn("parsed_votes = normalize_vote_count(value)", self.metadata)

    def test_the_library_page_serves_the_column_on_both_branches(self):
        # The filter runs client-side over these rows, so a column added to the
        # artwork branch alone leaves the fallback installation with a filter
        # that silently matches nothing.
        self.assertEqual(self.app.count("m.rating_votes,"), 2)

    def test_the_sync_payload_publishes_it(self):
        self.assertIn('    "rating_votes",\n', self.app)

    def test_the_backfill_selects_on_null_rather_than_zero(self):
        # A film TMDB has genuinely never had a vote for is answered and must
        # leave the queue, or the job asks about it again on every run forever.
        self.assertIn("AND m.rating_votes IS NULL", self.metadata)

    def test_the_backfill_writes_only_the_one_column(self):
        block = self.metadata[self.metadata.index("def backfill_movie_rating_votes") :][:9000]
        self.assertIn("UPDATE movies SET rating_votes=%s WHERE id=%s", block)
        self.assertNotIn("refresh_movie_metadata(", block)

    def test_an_answer_without_the_field_is_skipped_not_failed(self):
        # A plugin too old to emit it is not an error; leaving the film pending
        # means a later run picks it up once the plugin is updated.
        block = self.metadata[self.metadata.index("def backfill_movie_rating_votes") :][:9000]
        self.assertIn('summary["skipped"] += 1', block)

    def test_a_missing_plugin_raises_rather_than_counting_failures(self):
        # A summary of "100 failed" rides in a job whose status says it
        # succeeded, so the reason never reaches a screen. An exception lands in
        # background_jobs.error, which the admin card reads back.
        block = self.metadata[self.metadata.index("def backfill_movie_rating_votes") :][:9000]
        self.assertIn("raise RuntimeError(", block)

    def test_the_job_type_is_its_own(self):
        self.assertIn('MOVIE_RATING_VOTES_BACKFILL_JOB_TYPE = "metadata.backfill_rating_votes"', self.metadata)
        self.assertNotEqual(
            next_metadata.MOVIE_RATING_VOTES_BACKFILL_JOB_TYPE,
            next_metadata.MOVIE_ORIGIN_BACKFILL_JOB_TYPE,
        )

    def test_the_worker_dispatches_it(self):
        self.assertIn("if job_type == MOVIE_RATING_VOTES_BACKFILL_JOB_TYPE:", self.worker)
        self.assertIn("def process_movie_rating_votes_backfill(", self.worker)

    def test_both_routes_exist(self):
        self.assertIn('@flask_app.get("/api/next/admin/metadata/rating-votes-backfill")', self.app)
        self.assertIn('@flask_app.post("/api/next/admin/metadata/rating-votes-backfill")', self.app)

    def test_the_queue_route_pins_the_ids_rather_than_a_bare_limit(self):
        # A job carrying only {"limit": 100} re-runs the same "next 100" query,
        # so a film TMDB cannot answer stays at the head of that ordering and is
        # retried by every job in the batch.
        block = self.app[self.app.index("def queue_movie_rating_votes_backfill") :][:5000]
        self.assertIn("movies_missing_rating_votes(conn, limit=None)", block)
        self.assertIn('"movieIds": slice_ids', block)

    def test_reading_the_jobs_back_needs_only_the_permission_that_queued_them(self):
        self.assertIn("allowed_types.append(MOVIE_RATING_VOTES_BACKFILL_JOB_TYPE)", self.app)


class FilterWiringTests(unittest.TestCase):
    """The seven edits a filter needs, each silent on its own."""

    @classmethod
    def setUpClass(cls):
        cls.source = _read(NEXT_VIEWS_UI_PATH)

    def test_the_input_sits_in_the_score_group(self):
        block = self.source[self.source.index('data-library-advanced-group="score"') :][:2600]
        self.assertIn('id="advancedMinVotes"', block)
        self.assertIn('type="number" min="0" step="1"', block)

    def test_the_defaults_carry_the_key(self):
        block = _function_source(self.source, "advancedSearchDefaults")
        self.assertIn('minVotes: ""', block)

    def test_a_stored_filter_is_normalized_through_the_floor_helper(self):
        block = _function_source(self.source, "normalizeAdvancedSearch")
        self.assertIn("minVotes: normalizeVoteFloor(source.minVotes)", block)

    def test_the_floor_counts_towards_the_badge(self):
        block = _function_source(self.source, "advancedSearchActiveCount")
        # Against "" and not for truthiness: a floor of "0" is a real filter.
        self.assertIn('normalized.minVotes !== ""', block)

    def test_the_control_is_read_back_and_written_back(self):
        self.assertIn("advancedMinVotes", _function_source(self.source, "readAdvancedSearchControls"))
        self.assertIn('setAdvancedControlValue("advancedMinVotes"', _function_source(self.source, "syncAdvancedSearchControls"))

    def test_the_predicate_is_in_the_advanced_search_chain(self):
        block = _function_source(self.source, "movieMatchesAdvancedSearch")
        self.assertIn("voteFloorNumber(filters.minVotes)", block)
        self.assertIn("movieVoteCount(movie)", block)

    def test_a_container_delegates_the_floor_to_its_members(self):
        block = _function_source(self.source, "containerMatchesAdvancedSearch")
        self.assertIn("voteFloorNumber(filters.minVotes) !== null", block)

    def test_the_panel_says_how_many_films_have_no_count(self):
        # The column arrives empty on every existing library, so a floor set on
        # the day this ships legitimately matches nothing. Without the number on
        # screen that is indistinguishable from a broken filter.
        self.assertIn('id="advancedVotesHint"', self.source)
        self.assertIn("movieVoteCount(movie) === null", _function_source(self.source, "voteDataMissingCount"))

    def test_the_detail_page_shows_the_sample_beside_the_score(self):
        # A vote floor is unexplainable while the page it filters never shows
        # the quantity being filtered on.
        block = _function_source(self.source, "movieScoreLabel")
        self.assertIn("movieVoteCount(movie)", block)
        self.assertIn('tNext("movieDetail.scoreVotes"', block)

    def test_the_admin_card_is_wired_end_to_end(self):
        for marker in (
            'id="appAdminVotesBackfillButton"',
            "function renderAppAdminVotesBackfill(",
            "function scheduleAppAdminVotesBackfillPoll(",
            "queueAppAdminVotesBackfill()",
            "/api/next/admin/metadata/rating-votes-backfill",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.source)

    def test_the_card_warns_when_the_plugin_cannot_answer(self):
        # A plugin below the version that emits the field answers every request
        # without it, so the job succeeds and writes nothing -- a counter that
        # looks exactly like a backfill nobody started.
        block = _function_source(self.source, "renderAppAdminVotesBackfill")
        self.assertIn("plugin.votesCapable === false", block)


class CopyTests(unittest.TestCase):
    """The strings, in every locale."""

    @classmethod
    def setUpClass(cls):
        cls.source = _read(NEXT_VIEWS_UI_PATH)
        with open(os.path.join(I18N_DIR, "en-US.json"), encoding="utf-8") as handle:
            cls.en = json.load(handle)

    def _catalogs(self):
        for name in sorted(os.listdir(I18N_DIR)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(I18N_DIR, name), encoding="utf-8") as handle:
                yield name, json.load(handle)

    def test_every_locale_carries_every_vote_key(self):
        problems = [
            "%s: %s" % (name, key)
            for name, catalog in self._catalogs()
            for key in VOTE_KEYS
            if not str(catalog.get(key, "")).strip()
        ]
        self.assertEqual(problems, [], "Missing vote count copy:\n" + "\n".join(problems))

    def test_every_placeholder_survives_translation(self):
        required = {
            "collection.voteDataMissing": ("{count}",),
            "movieDetail.scoreVotes": ("{count}",),
            "appAdmin.votesBackfillQueued": ("{count}",),
            "appAdmin.votesBackfillPluginTooOld": ("{installed}", "{required}"),
        }
        problems = []
        for name, catalog in self._catalogs():
            for key, placeholders in required.items():
                for placeholder in placeholders:
                    if placeholder not in catalog.get(key, ""):
                        problems.append("%s: %s lost %s" % (name, key, placeholder))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_no_locale_simply_echoes_the_english_help_text(self):
        # The paragraph that explains why a score without a sample misleads is
        # the one a reader most needs in their own language, and it is long
        # enough that an untranslated locale is a silent copy-paste.
        echoes = [
            name
            for name, catalog in self._catalogs()
            if name != "en-US.json"
            and catalog.get("appAdmin.votesBackfillHelp") == self.en["appAdmin.votesBackfillHelp"]
        ]
        self.assertEqual(echoes, [], "still English: %s" % echoes)

    def test_the_inline_fallbacks_match_the_source_catalog(self):
        # tNext renders the fallback whenever the catalog has not loaded, so a
        # stale one shows old wording on a slow connection only.
        for key in VOTE_KEYS:
            inline = 'tNext("%s", "%s")' % (key, self.en[key])
            markup = 'data-next-i18n="%s">%s<' % (key, self.en[key])
            with self.subTest(key=key):
                self.assertTrue(
                    inline in self.source or markup in self.source,
                    "%s disagrees with en-US.json" % key,
                )


if __name__ == "__main__":
    unittest.main()
