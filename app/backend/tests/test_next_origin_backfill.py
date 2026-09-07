"""The origin backfill job actually runs, and says what it did.

`backfill_movie_origins` shipped with no test at all, and what shipped could
never have worked: it handed `plugin_execution_context` the frozen
`PluginDiscovery` dataclass that `discovered_plugin` returns, where every other
caller in the codebase hands it a plugin record *dict* read from the `plugins`
table. `plugin_execution_context` starts with `plugin.get("manifest")`, so every
job with a non-empty batch raised `AttributeError` before the first TMDB request
and the worker failed it. The admin counter could not move, for anybody, ever --
which is exactly how it was reported (#719).

So the claim these tests make is the one nothing made before: call the function
and look at what came back. `test_a_batch_is_filled_from_tmdb` fails on the
unfixed code.

The rest guard the two silent outcomes around it. A missing API key used to be
recorded as N ordinary failures and a job that "succeeded" with `updated: 0`;
it now raises, so the reason lands in `background_jobs.error` where the admin
screen can read it. And a TMDB answer that carries no origin stays `skipped`
rather than becoming a write -- an empty write would satisfy nothing and leave
the film pending anyway.
"""

import os
import sys
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import next_metadata  # noqa: E402


class _StandInConnection:
    """A connection-shaped object that owns no transaction and no cursor.

    Everything this job would ask a database for is stubbed per test, so a
    cursor call here means the code under test reached for the database on a
    path these tests thought it did not.
    """

    def cursor(self):  # pragma: no cover - reaching this is the failure
        raise AssertionError("backfill_movie_origins queried the database directly")


TMDB_PLUGIN_RECORD = {
    "id": "tmdb",
    "name": "TMDb",
    "enabled": True,
    "installed": True,
    "categories": ["metadata_source"],
    "capabilities": ["movie_details"],
    "manifest": {"id": "tmdb", "version": "1.8.0", "requiresSecrets": True},
}


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.written = []
        self.plugins = [TMDB_PLUGIN_RECORD]
        self.config = {"settings": {}, "secretsConfigured": True, "secretNames": ["apiKey"]}
        self.rows = [{"id": "movie-1", "tmdb_id": "603"}]
        self.execution = {
            "status": "ok",
            "result": {"filmOrigin": {"originalLanguage": "en", "originCountries": ["US"]}},
        }

        def _run(plugin_id, entrypoint, payload, context):
            self.calls.append((plugin_id, entrypoint, payload, context))
            return self.execution

        self._patch("metadata_source_plugins", lambda conn: self.plugins)
        self._patch("plugin_config_from_db", lambda conn, plugin_id: self.config)
        self._patch("plugin_secret_values", lambda conn, config: {"apiKey": "test-key"})
        self._patch("movievault_v2_plugin_context", lambda conn, plugin_id, context: context)
        self._patch("movies_missing_film_origin", lambda conn, limit=100: list(self.rows))
        self._patch("run_plugin_entrypoint", _run)
        self._patch(
            "replace_movie_film_origin",
            lambda conn, movie_id, origin: self.written.append((movie_id, origin)),
        )

    def _patch(self, name, replacement):
        original = getattr(next_metadata, name)
        setattr(next_metadata, name, replacement)
        self.addCleanup(setattr, next_metadata, name, original)

    def test_a_batch_is_filled_from_tmdb(self):
        summary = next_metadata.backfill_movie_origins(_StandInConnection(), limit=100)

        self.assertEqual(summary["requested"], 1)
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual([call[1] for call in self.calls], ["movie_details"])
        self.assertEqual(self.written[0][0], "movie-1")

    def test_the_context_handed_to_the_plugin_is_a_real_execution_context(self):
        # The bug was invisible precisely because nothing looked at this value.
        next_metadata.backfill_movie_origins(_StandInConnection(), limit=100)
        context = self.calls[0][3]
        self.assertEqual(context["pluginId"], "tmdb")
        self.assertTrue(context["enabled"])
        self.assertEqual(context["secrets"], {"apiKey": "test-key"})

    def test_an_unconfigured_tmdb_key_raises_instead_of_failing_every_film(self):
        # Previously: N silent `failed` entries and a job whose status said it
        # succeeded. The operator's only symptom was a counter that never moved.
        self.config = {"settings": {}, "secretsConfigured": False, "secretNames": ["apiKey"]}
        with self.assertRaises(RuntimeError) as caught:
            next_metadata.backfill_movie_origins(_StandInConnection(), limit=100)
        self.assertIn("tmdb", str(caught.exception).lower())
        self.assertEqual(self.calls, [])

    def test_a_missing_tmdb_plugin_raises_rather_than_reporting_failures(self):
        self.plugins = []
        with self.assertRaises(RuntimeError):
            next_metadata.backfill_movie_origins(_StandInConnection(), limit=100)

    def test_an_answer_without_origin_is_skipped_and_never_written(self):
        self.execution = {"status": "ok", "result": {}}
        summary = next_metadata.backfill_movie_origins(_StandInConnection(), limit=100)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["updated"], 0)
        self.assertEqual(self.written, [])

    def test_a_plugin_error_is_counted_as_a_failure_and_does_not_stop_the_batch(self):
        self.rows = [{"id": "movie-1", "tmdb_id": "603"}, {"id": "movie-2", "tmdb_id": "604"}]
        answers = [{"status": "error", "error": "rate limited"}, self.execution]

        def _run(plugin_id, entrypoint, payload, context):
            self.calls.append((plugin_id, entrypoint, payload, context))
            return answers[len(self.calls) - 1]

        self._patch("run_plugin_entrypoint", _run)
        summary = next_metadata.backfill_movie_origins(_StandInConnection(), limit=100)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["updated"], 1)

    def test_a_film_without_a_tmdb_id_is_unresolved_rather_than_failed(self):
        self.rows = [{"id": "movie-1", "tmdb_id": ""}]
        summary = next_metadata.backfill_movie_origins(_StandInConnection(), limit=100)
        self.assertEqual(summary["unresolved"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(self.calls, [])

    def test_an_empty_batch_asks_tmdb_nothing_and_raises_nothing(self):
        self.rows = []
        summary = next_metadata.backfill_movie_origins(_StandInConnection(), limit=100)
        self.assertEqual(summary["requested"], 0)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
