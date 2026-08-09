"""A capability a plugin declares must be a capability the runtime will run.

`PLUGIN_ENTRYPOINTS` is a hard allowlist: `run_plugin_entrypoint` refuses any
name that is not in it, with `state: "entrypoint_unavailable"`. A manifest's
`capabilities` list is a separate list, written per plugin. **Two lists that must
agree, and nothing compared them.**

They disagreed for the whole life of the series feature. `series_details`,
`search_series` and `season_episodes` were declared by both bundled sources,
implemented in both, and reachable by neither -- so refreshing a series, searching
its identity, offering its seasons and fetching its episodes all called functions
the runtime declined to reach. Six unreachable capabilities, and the failure
surfaced to the reader as the word "miss".

No existing test could have caught it. Every series test replaces either the
plugin function (`tmdb._request`) or `run_plugin_entrypoint` itself, so the
allowlist was never on any path under test. That is the gap these two assertions
close: one compares the lists, the other runs the real thing once.
"""

import ast
import json
import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_plugin_runtime import PLUGIN_ENTRYPOINTS, run_plugin_entrypoint


PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "next_plugins"))


def _bundled_plugins():
    """Manifest plus the plugin's top-level function names, read without importing.

    `ast` rather than `importlib` on purpose. Importing every bundled plugin as a
    side effect of a coverage check is not free: two runtime tests bind a fake
    `requests` with `patch.dict(sys.modules)` and then call `import_module`,
    which only rebinds when the module is *not* already cached. Importing them
    here first made `import_module` a no-op and broke both.

    Reading the source is also the stronger check. It needs no plugin to import
    cleanly, so a plugin with a missing third-party dependency is still held to
    the rule instead of being skipped.
    """
    for name in sorted(os.listdir(PLUGIN_ROOT)):
        manifest_path = os.path.join(PLUGIN_ROOT, name, "manifest.json")
        plugin_path = os.path.join(PLUGIN_ROOT, name, "plugin.py")
        if not os.path.exists(manifest_path) or not os.path.exists(plugin_path):
            continue
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        with open(plugin_path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=plugin_path)
        functions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        yield name, manifest, functions


class DeclaredCapabilitiesAreReachableTests(unittest.TestCase):
    def test_every_implemented_capability_is_on_the_allowlist(self):
        """The check that found all six at once.

        Scoped to capabilities the plugin actually implements as a function of
        the same name. A manifest may legitimately name a capability whose
        entrypoint is spelled differently -- TMDB declares `people` and
        implements `people_for_movie` -- and that indirection is not this test's
        business. A capability that *is* a function and is *not* runnable is
        always a mistake, because the author wrote the code believing it would
        be called.
        """
        unreachable = [
            f"{plugin_id}.{capability}"
            for plugin_id, manifest, functions in _bundled_plugins()
            for capability in (manifest.get("capabilities") or [])
            if capability in functions and capability not in PLUGIN_ENTRYPOINTS
        ]
        self.assertEqual(unreachable, [])

    def test_the_scan_actually_sees_the_plugins(self):
        """A scan that silently matched nothing would pass the assertion above
        while checking nothing at all -- the failure mode of every test built on
        a directory walk."""
        seen = {plugin_id for plugin_id, _, _ in _bundled_plugins()}
        self.assertIn("tmdb", seen)
        self.assertIn("tvdb", seen)


class TheRuntimeActuallyReachesThemTests(unittest.TestCase):
    """One real call through the real runtime, with no stubbing.

    Deliberately not a unit test of the allowlist -- that is the assertion
    above. This is the one that exercises the path the application uses, which is
    exactly what every other series test replaces.

    No network: with no API key in the context, TMDB's `_request` raises before
    it builds a request, so the call returns a `runtime_error` carrying that
    message. The assertion is therefore about the *state*, not the answer.
    """

    def _state(self, entrypoint):
        execution = run_plugin_entrypoint("tmdb", entrypoint, {"title": "Example"}, {})
        return execution.get("state"), execution

    def test_the_series_entrypoints_are_not_refused(self):
        for entrypoint in ("series_details", "search_series", "season_episodes"):
            with self.subTest(entrypoint=entrypoint):
                state, execution = self._state(entrypoint)
                self.assertNotEqual(
                    state,
                    "entrypoint_unavailable",
                    f"{entrypoint} is declared and implemented but the runtime refuses it: {execution}",
                )

    def test_a_name_nobody_implements_is_still_refused(self):
        """The allowlist has to keep saying no, or this test would pass by
        making the runtime permissive rather than correct."""
        state, _ = self._state("definitely_not_an_entrypoint")
        self.assertEqual(state, "entrypoint_unavailable")


class AFailedExecutionIsNotAMissTests(unittest.TestCase):
    """"miss" is what a source says when it looked and found nothing.

    An unreachable entrypoint, an unloadable module and a plugin that is not
    installed are not that, and reporting them in the same word sends a reader
    to check their spelling instead of their configuration. Four runtime states
    carry no `error` field at all, which is how the fallback chain reached the
    literal string.
    """

    def _consult(self, execution):
        from app.backend import next_metadata

        original = next_metadata.run_plugin_entrypoint
        next_metadata.run_plugin_entrypoint = lambda *args, **kwargs: execution
        original_context = next_metadata.plugin_execution_context
        original_config = next_metadata.plugin_config_from_db
        next_metadata.plugin_execution_context = lambda *args, **kwargs: {}
        next_metadata.plugin_config_from_db = lambda *args, **kwargs: {}
        try:
            return next_metadata.consult_plugins(None, [{"id": "tmdb"}], "series_details", {})
        finally:
            next_metadata.run_plugin_entrypoint = original
            next_metadata.plugin_execution_context = original_context
            next_metadata.plugin_config_from_db = original_config

    def test_a_state_without_an_error_field_is_still_named(self):
        for state in ("entrypoint_unavailable", "manifest_only", "not_found"):
            with self.subTest(state=state):
                _, errors = self._consult({"status": "error", "state": state})
                self.assertEqual(errors[0]["error"], state)

    def test_a_real_error_message_still_wins(self):
        _, errors = self._consult(
            {"status": "error", "state": "runtime_error", "error": "TMDb API key is not configured"}
        )
        self.assertEqual(errors[0]["error"], "TMDb API key is not configured")

    def test_a_source_that_answered_miss_still_says_miss(self):
        """The distinction this exists to keep. A source that ran and found
        nothing is not a broken source."""
        _, errors = self._consult({"status": "ok", "result": {"status": "miss"}})
        self.assertEqual(errors[0]["error"], "miss")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
