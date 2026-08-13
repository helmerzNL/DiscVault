"""Opening a film must not hold an API worker while a provider thinks.

The refresh that runs when a film is opened is not something the user asked
for -- it is the app keeping itself current. It used to run inline on
``POST /api/next/movies/<id>/metadata/refresh``, and the comment above it said
that was safe because cached people are skipped and live calls run
concurrently, so it is "fast in the common case".

The common case was never the problem. Two ``pg_stat_activity`` samples from
the beta instance caught this exact call holding a transaction open for 10.1
and 14.5 seconds -- not computing anything, waiting for TMDB, OMDb or
MovieVault to answer. Gunicorn serves this instance with two synchronous
workers, so for those seconds one of the two was gone, and the app felt frozen
in a way that had nothing to do with how much data it holds.

There is no promise available about how fast somebody else's server answers.
The fix is therefore not to make the call faster but to take it off the path
that has to answer the user: the identical refresh is queued as a background
job, the worker performs it, and the page updates when it lands.

**Nothing is dropped and nothing became optional.** The job carries the same
arguments the inline call used, the worker calls the same
``refresh_movie_metadata`` and the same person cascade, and the result is
cached the same way. These tests exist to keep that true -- a version of this
change that quietly stopped refreshing people, or stopped refreshing at all,
would feel faster still and would be a regression rather than a fix.
"""

import pathlib
import re
import sys
import unittest


BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

UI_SOURCE = (BACKEND / "next_views_ui.py").read_text(encoding="utf-8")
APP_SOURCE = (BACKEND / "next_app.py").read_text(encoding="utf-8")
WORKER_SOURCE = (BACKEND / "next_worker.py").read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    """The text of one JavaScript function, up to the next sibling declaration."""

    start = source.index(f"function {name}(")
    remainder = source[start + len(name) :]
    match = re.search(r"\n    (?:async )?function ", remainder)
    end = start + len(name) + (match.start() if match else len(remainder))
    return source[start:end]


class TheOpenPathQueuesRatherThanWaitsTests(unittest.TestCase):
    def setUp(self):
        self.opener = _function_body(UI_SOURCE, "queueMovieDetailMetadataRefresh")

    def test_it_posts_a_job_instead_of_running_the_refresh_inline(self):
        self.assertIn("/metadata/jobs`", self.opener)
        self.assertNotIn(
            "/metadata/refresh`",
            self.opener,
            "the inline route is what held a worker for 10-14 seconds",
        )

    def test_the_queued_job_asks_for_exactly_the_work_the_inline_call_did(self):
        # If this drifts, the app gets faster by doing less -- which is not the
        # change that was made.
        for argument in (
            "dryRun: false",
            "refreshPeople: true",
            'personRefreshScope: "all"',
            "force: false",
        ):
            self.assertIn(argument, self.opener, argument)

    def test_it_re_renders_the_detail_once_the_job_completes(self):
        self.assertIn("renderMovieDetail(refreshed.detail", self.opener)
        self.assertIn('movieDetail.applied', self.opener)

    def test_a_failed_job_is_reported_rather_than_swallowed(self):
        self.assertIn('job.status === "failed"', self.opener)
        self.assertIn("movieDetail.applyFailed", self.opener)

    def test_it_says_nothing_false_when_the_job_outlives_the_polling(self):
        # The refresh is still queued and the worker still holds it, so the
        # message must stay "Refreshing metadata..." rather than claiming
        # success or reporting a failure that did not happen.
        self.assertIn("if (!job) {", self.opener)
        body = self.opener[self.opener.index("if (!job) {") :]
        tail = body[: body.index("}")]
        self.assertNotIn("movieDetail.applied", tail)
        self.assertNotIn("applyFailed", tail)


class ThePollingIsBoundedAndPoliteTests(unittest.TestCase):
    def setUp(self):
        self.waiter = _function_body(UI_SOURCE, "awaitMovieMetadataJob")

    def test_it_stops_when_the_user_navigates_away(self):
        self.assertIn("activeDetailMovieId !== movieId", self.waiter)

    def test_it_stops_on_either_terminal_state(self):
        self.assertIn('job.status === "completed"', self.waiter)
        self.assertIn('job.status === "failed"', self.waiter)

    def test_it_backs_off_rather_than_hammering(self):
        delays = [int(value) for value in re.findall(r"\b(\d{3,4})\b", self.waiter)]
        self.assertTrue(delays, "expected an explicit delay schedule")
        self.assertLessEqual(
            delays[0],
            1000,
            "the first look should be soon -- a cached refresh finishes quickly",
        )
        self.assertGreater(
            delays[-1],
            delays[0],
            "a refresh still running after ten seconds is waiting on a provider, "
            "and polling harder does not make it answer sooner",
        )
        self.assertGreaterEqual(
            sum(delays) / 1000,
            45,
            "the window should cover a genuinely slow provider",
        )

    def test_it_is_bounded(self):
        self.assertNotIn("while (", self.waiter, "an unbounded poll is a leak")


class TheDeliberateRouteChoicesTests(unittest.TestCase):
    def test_the_manual_refresh_button_stays_synchronous(self):
        # A button the user pressed, with a spinner in front of them, is a
        # different case: they are waiting on purpose and expect the answer.
        self.assertIn(
            "/api/next/movies/${encodeURIComponent(activeDetailMovieId)}/metadata/refresh`",
            UI_SOURCE,
        )

    def test_the_queue_route_exists_and_is_gated_the_same_way(self):
        marker = APP_SOURCE.index('def queue_movie_metadata_refresh(')
        body = APP_SOURCE[marker : marker + 2000]
        self.assertIn('require_next_permission(conn, "metadata.refresh_one")', body)
        self.assertIn("actor_can_view_movie(conn, actor, movie_uuid)", body)

    def test_the_worker_performs_the_same_refresh(self):
        # The whole claim of this change is that the work is unchanged and only
        # its location moved.
        marker = WORKER_SOURCE.index("def process_metadata_refresh(")
        body = WORKER_SOURCE[marker : marker + 2000]
        self.assertIn("refresh_movie_metadata(conn, movie_id", body)
        self.assertIn("refresh_movie_person_metadata_cascade(", body)
        for key in ("refreshPeople", "personRefreshScope", "force"):
            self.assertIn(key, body, f"the worker must honour {key} from the payload")


class TranslationsAreCompleteTests(unittest.TestCase):
    def test_every_locale_carries_the_new_key(self):
        import json

        catalogs = sorted((REPO_ROOT / "app" / "frontend" / "i18n" / "next").glob("*.json"))
        self.assertGreaterEqual(len(catalogs), 29)
        for path in catalogs:
            data = json.loads(path.read_text(encoding="utf-8"))
            value = data.get("movieDetail.applyFailed")
            self.assertTrue(value, f"{path.name} is missing movieDetail.applyFailed")
            self.assertNotEqual(
                value,
                data.get("movieDetail.applied"),
                f"{path.name} reuses the success string for the failure",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
