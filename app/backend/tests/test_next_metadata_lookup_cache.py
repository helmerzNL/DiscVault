"""Provider lookups are cached, and the cache is never load-bearing.

`metadata_lookup_cache` has existed since migration 003 -- cache key, payload,
expiry, unique constraint, expiry index -- and nothing in the codebase ever read
or wrote it. Every lookup went to the provider, every time, which on a movie
refresh is one network round trip per credit.

Two properties have to hold together, and they pull in opposite directions:
the cache has to actually be used, and it must never be the reason a lookup
fails or returns something it should not. The second is why most of this file is
about what is *not* cached.
"""

import pathlib
import sys
import unittest
from unittest import mock


BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from app.backend import next_metadata
except ModuleNotFoundError as exc:  # pragma: no cover - minimal environments
    if exc.name not in {"flask", "psycopg", "requests", "PIL"}:
        raise
    next_metadata = None


OK = {"status": "ok", "result": {"status": "hit", "title": "from the provider"}}


@unittest.skipIf(next_metadata is None, "metadata dependencies are required")
class CacheableEntrypointsTests(unittest.TestCase):
    def test_read_only_catalogue_lookups_are_cacheable(self):
        for entrypoint in ("search_title", "search_barcode", "movie_details", "person_details"):
            self.assertIn(entrypoint, next_metadata.CACHEABLE_LOOKUP_ENTRYPOINTS)

    def test_side_effecting_and_live_entrypoints_are_not(self):
        # A replayed push or import is a corruption, not a stale read; a cached
        # health check reports a provider that may have gone away.
        for entrypoint in (
            "receive_metadata",
            "import_source",
            "plan_import",
            "sync_index",
            "sync_library",
            "sync_personal_lists",
            "discover_library",
            "health_check",
            "connection_request",
            "connection_recovery_action",
            "price_check",
            "prepare_barcode_update",
            "prepare_container_update",
        ):
            self.assertNotIn(entrypoint, next_metadata.CACHEABLE_LOOKUP_ENTRYPOINTS)

    def test_the_list_is_an_allowlist(self):
        # An entrypoint nobody has considered must not be cached by default: the
        # cost of forgetting should be a missed optimisation, not a replayed
        # side effect.
        self.assertNotIn("some_future_entrypoint", next_metadata.CACHEABLE_LOOKUP_ENTRYPOINTS)


@unittest.skipIf(next_metadata is None, "metadata dependencies are required")
class CacheKeyTests(unittest.TestCase):
    def _key(self, plugin_id="tmdb", entrypoint="movie_details", payload=None, context=None):
        with mock.patch.object(next_metadata, "plugin_version_for_cache", return_value="1.0.0"):
            return next_metadata.metadata_lookup_cache_key(
                plugin_id, entrypoint, payload or {"tmdbId": "1"}, context or {}
            )

    def test_the_same_question_gives_the_same_key(self):
        self.assertEqual(self._key(), self._key())

    def test_a_different_payload_is_a_different_question(self):
        self.assertNotEqual(self._key(payload={"tmdbId": "1"}), self._key(payload={"tmdbId": "2"}))

    def test_a_different_entrypoint_is_a_different_question(self):
        self.assertNotEqual(self._key(entrypoint="movie_details"), self._key(entrypoint="search_title"))

    def test_a_different_plugin_is_a_different_question(self):
        self.assertNotEqual(self._key(plugin_id="tmdb"), self._key(plugin_id="omdb"))

    def test_configuration_is_part_of_the_question(self):
        # A different API key, region or language setting changes the answer.
        # Treating them as one question serves one configuration's answer for
        # another's.
        self.assertNotEqual(
            self._key(context={"config": {"language": "en"}}),
            self._key(context={"config": {"language": "nl"}}),
        )

    def test_a_plugin_upgrade_invalidates_its_answers(self):
        payload = {"tmdbId": "1"}
        with mock.patch.object(next_metadata, "plugin_version_for_cache", return_value="1.0.0"):
            before = next_metadata.metadata_lookup_cache_key("tmdb", "movie_details", payload, {})
        with mock.patch.object(next_metadata, "plugin_version_for_cache", return_value="1.1.0"):
            after = next_metadata.metadata_lookup_cache_key("tmdb", "movie_details", payload, {})
        self.assertNotEqual(
            before,
            after,
            "an upgraded source maps fields differently; its old answers must not stand",
        )

    def test_nothing_readable_is_stored_in_the_key(self):
        # The payload can carry what a person typed and the context carries
        # plugin secrets. The key is a digest so the table holds provider data,
        # never the question that produced it.
        key = self._key(
            payload={"title": "a private search term"},
            context={"config": {"apiKey": "super-secret"}},
        )
        self.assertRegex(key, r"^[0-9a-f]{64}$")
        self.assertNotIn("private", key)
        self.assertNotIn("secret", key)


@unittest.skipIf(next_metadata is None, "metadata dependencies are required")
class WrapperBehaviourTests(unittest.TestCase):
    """What reaches the provider, and what does not."""

    def setUp(self):
        self.calls = []

        def fake_run(plugin_id, entrypoint, payload, context):
            self.calls.append((plugin_id, entrypoint))
            return dict(OK)

        self.patches = [
            mock.patch.object(next_metadata, "run_plugin_entrypoint", side_effect=fake_run),
            mock.patch.object(next_metadata, "plugin_version_for_cache", return_value="1.0.0"),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _run(self, entrypoint="movie_details", *, force=False, cached=None, conn=None):
        with mock.patch.object(next_metadata, "read_metadata_lookup_cache", return_value=cached):
            with mock.patch.object(next_metadata, "write_metadata_lookup_cache") as writer:
                result = next_metadata.run_cached_plugin_entrypoint(
                    conn if conn is not None else object(),
                    "tmdb",
                    entrypoint,
                    {"tmdbId": "1"},
                    {},
                    force=force,
                )
        return result, writer

    def test_a_hit_is_served_without_reaching_the_provider(self):
        cached = {"status": "ok", "result": {"status": "hit", "title": "from the cache"}}
        result, _ = self._run(cached=cached)
        self.assertEqual(result, cached)
        self.assertEqual(self.calls, [])

    def test_a_miss_asks_the_provider_and_stores_the_answer(self):
        result, writer = self._run(cached=None)
        self.assertEqual(result["result"]["title"], "from the provider")
        self.assertEqual(self.calls, [("tmdb", "movie_details")])
        self.assertEqual(writer.call_count, 1)

    def test_force_reaches_the_provider_even_with_a_hit(self):
        # This is what keeps "everything still refreshes" true. The refresh
        # routes default force to True; only the automatic refresh-on-open
        # sends False.
        cached = {"status": "ok", "result": {"status": "hit", "title": "from the cache"}}
        result, writer = self._run(cached=cached, force=True)
        self.assertEqual(result["result"]["title"], "from the provider")
        self.assertEqual(self.calls, [("tmdb", "movie_details")])
        self.assertEqual(writer.call_count, 0, "a forced refresh must not be short-circuited")

    def test_a_non_cacheable_entrypoint_is_never_stored(self):
        result, writer = self._run(entrypoint="receive_metadata", cached={"never": "used"})
        self.assertEqual(result["result"]["title"], "from the provider")
        self.assertEqual(writer.call_count, 0)

    def test_a_failed_lookup_is_not_stored(self):
        # Caching a failure turns one bad minute at a provider into six bad hours.
        def failing(plugin_id, entrypoint, payload, context):
            self.calls.append((plugin_id, entrypoint))
            return {"status": "error", "state": "unavailable", "error": "timeout"}

        with mock.patch.object(next_metadata, "run_plugin_entrypoint", side_effect=failing):
            with mock.patch.object(next_metadata, "read_metadata_lookup_cache", return_value=None):
                with mock.patch.object(next_metadata, "write_metadata_lookup_cache") as writer:
                    next_metadata.run_cached_plugin_entrypoint(
                        object(), "tmdb", "movie_details", {}, {}
                    )
        self.assertEqual(writer.call_count, 0)


@unittest.skipIf(next_metadata is None, "metadata dependencies are required")
class TheCacheIsNeverLoadBearingTests(unittest.TestCase):
    """A lookup must succeed even when the cache cannot be used at all.

    The pipeline is deliberately callable with a stand-in connection that has no
    cursor -- several tests and callers do exactly that -- so the cache has to
    treat an unusable connection as "no cache", not as an error.
    """

    def test_an_unusable_connection_reads_as_a_miss(self):
        self.assertIsNone(next_metadata.read_metadata_lookup_cache(object(), "tmdb", "k"))

    def test_an_unusable_connection_writes_nothing_and_raises_nothing(self):
        next_metadata.write_metadata_lookup_cache(object(), "tmdb", "k", {"status": "ok"})

    def test_a_lookup_still_answers_through_an_unusable_connection(self):
        with mock.patch.object(next_metadata, "run_plugin_entrypoint", return_value=dict(OK)):
            result = next_metadata.run_cached_plugin_entrypoint(
                object(), "tmdb", "movie_details", {"tmdbId": "1"}, {}
            )
        self.assertEqual(result["result"]["title"], "from the provider")

    def test_an_unknown_plugin_has_no_version_rather_than_an_error(self):
        self.assertEqual(next_metadata.plugin_version_for_cache("no-such-plugin"), "")


@unittest.skipIf(next_metadata is None, "metadata dependencies are required")
class WiringTests(unittest.TestCase):
    """Source-level: the calls that must and must not go through the cache."""

    @classmethod
    def setUpClass(cls):
        cls.source = (BACKEND / "next_metadata.py").read_text(encoding="utf-8")

    def test_a_push_to_a_receiver_is_never_cached(self):
        self.assertIn(
            'run_plugin_entrypoint(plugin["id"], "receive_metadata", payload, context)',
            self.source,
            "receive_metadata is a push; routing it through the cache would replay it",
        )

    def test_the_lookup_loop_goes_through_the_cache(self):
        self.assertIn("run_cached_plugin_entrypoint(\n                conn,", self.source)

    def test_force_reaches_the_pipeline(self):
        # The route already parsed `force` and then dropped it. The chain from
        # the route to the provider call is what makes an explicit refresh mean
        # something again.
        for marker in (
            "def run_metadata_source_pipeline(",
            "def preview_movie_metadata(",
            "def lookup_metadata_sources(",
            "def refresh_movie_metadata(",
        ):
            body = self.source[self.source.index(marker) : self.source.index(marker) + 900]
            self.assertIn("force", body, f"{marker} must carry force through")

    def test_expired_rows_are_swept_on_write(self):
        body = self.source[self.source.index("def write_metadata_lookup_cache(") :][:1600]
        self.assertIn("DELETE FROM metadata_lookup_cache WHERE expires_at < now()", body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
