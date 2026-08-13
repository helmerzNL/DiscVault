"""A plugin module survives between calls, and what that makes possible.

`load_runtime_module` used to re-execute a plugin's `plugin.py` on every
entrypoint call. The execution itself is cheap -- 0.5 ms for the TMDB plugin,
2.1 ms for the largest one -- so that was never the cost. What it cost was
everything a module is allowed to keep.

Two plugins were already written expecting otherwise and quietly got nothing:
`tvdb` caches its auth token in `_TOKENS`, so it re-authenticated on every
single call, and `movievault_26` keeps a template cache that never produced a
hit. Both key their caches by the configuration they belong to, so persistence
shares nothing across configurations.

It also makes connection reuse possible at all: `requests.get` opens a fresh
connection per call, and a module-level session cannot help while the module
holding it is discarded a moment later.
"""

import pathlib
import sys
import textwrap
import unittest
from unittest import mock


BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from app.backend import next_plugin_runtime as runtime
except ModuleNotFoundError as exc:  # pragma: no cover - minimal environments
    if exc.name not in {"flask", "psycopg", "requests"}:
        raise
    runtime = None


@unittest.skipIf(runtime is None, "plugin runtime dependencies are required")
class ModuleReuseTests(unittest.TestCase):
    def setUp(self):
        runtime.reset_plugin_discovery_cache()
        self.addCleanup(runtime.reset_plugin_discovery_cache)
        self._tmp = pathlib.Path(
            __import__("tempfile").mkdtemp(prefix="dv-plugin-reuse-")
        )
        self.addCleanup(lambda: __import__("shutil").rmtree(self._tmp, ignore_errors=True))

    def _plugin(self, body: str) -> "runtime.PluginDiscovery":
        module_path = self._tmp / "plugin.py"
        module_path.write_text(textwrap.dedent(body), encoding="utf-8")
        return runtime.PluginDiscovery(
            manifest={"id": "probe", "version": "1.0.0"},
            path=self._tmp,
            module_path=module_path,
            runtime={"loaded": True, "entrypoints": []},
        )

    COUNTING_BODY = """
        LOADS = []
        LOADS.append(1)

        def health_check(context=None):
            return {"status": "ok", "loads": len(LOADS)}
    """

    def test_the_module_is_executed_once(self):
        plugin = self._plugin(self.COUNTING_BODY)
        first = runtime.load_runtime_module(plugin)
        second = runtime.load_runtime_module(plugin)
        self.assertIs(first, second, "a second call must not re-execute the file")

    def test_module_level_state_survives_between_calls(self):
        # This is the property tvdb's token cache and movievault_26's template
        # cache were written against, and never had.
        plugin = self._plugin(
            """
            SEEN = {}

            def remember(key):
                SEEN[key] = SEEN.get(key, 0) + 1
                return SEEN[key]
            """
        )
        runtime.load_runtime_module(plugin).remember("a")
        again = runtime.load_runtime_module(plugin).remember("a")
        self.assertEqual(again, 2, "state written by one call must be visible to the next")

    def test_rewriting_the_file_loads_the_new_code(self):
        # A plugin upgrade rewrites plugin.py; the next call must run it.
        plugin = self._plugin("VALUE = 'before'")
        self.assertEqual(runtime.load_runtime_module(plugin).VALUE, "before")
        plugin.module_path.write_text("VALUE = 'after'\n", encoding="utf-8")
        self.assertEqual(runtime.load_runtime_module(plugin).VALUE, "after")

    def test_a_size_only_change_is_still_seen(self):
        # Size is in the stamp alongside st_mtime_ns so a rewrite inside one
        # clock tick cannot serve the previous module.
        plugin = self._plugin("VALUE = 'x'")
        runtime.load_runtime_module(plugin)
        stamp_before = runtime._module_file_stamp(plugin.module_path)
        plugin.module_path.write_text("VALUE = 'xx'\n", encoding="utf-8")
        stamp_after = runtime._module_file_stamp(plugin.module_path)
        self.assertNotEqual(stamp_before, stamp_after)

    def test_a_same_second_same_size_rewrite_is_not_served_from_stale_bytecode(self):
        # This is the case that made the test above fail before the loader was
        # bypassed. CPython validates a cached .pyc on the source's modification
        # time in *whole seconds* plus its size, so two versions of equal length
        # written within one second are indistinguishable to it and the previous
        # bytecode runs. The image sets PYTHONDONTWRITEBYTECODE so no .pyc is
        # written in production -- which means this correctness rests on an
        # environment variable, and the failure it buys back is a plugin upgrade
        # quietly running the old code.
        plugin = self._plugin("VALUE = 'before'")
        self.assertEqual(runtime.load_runtime_module(plugin).VALUE, "before")
        replacement = "VALUE = 'after'\n"
        self.assertEqual(
            len(replacement),
            len("VALUE = 'before'"),
            "the point of this test is that the two are the same length",
        )
        plugin.module_path.write_text(replacement, encoding="utf-8")
        self.assertEqual(runtime.load_runtime_module(plugin).VALUE, "after")

    def test_the_module_keeps_its_identity(self):
        # Bypassing the loader must not cost the module the attributes plugins
        # and tracebacks rely on.
        plugin = self._plugin("import os\nWHO = __name__\nWHERE = __file__\n")
        module = runtime.load_runtime_module(plugin)
        self.assertTrue(module.WHO)
        self.assertEqual(module.WHERE, str(plugin.module_path))
        self.assertIsNotNone(module.__spec__)

    def test_a_reset_drops_loaded_modules(self):
        # A module can hold a live HTTP session; it should not outlive the
        # discovery it belonged to.
        plugin = self._plugin(self.COUNTING_BODY)
        first = runtime.load_runtime_module(plugin)
        runtime.reset_plugin_discovery_cache()
        second = runtime.load_runtime_module(plugin)
        self.assertIsNot(first, second)

    def test_a_plugin_without_a_module_is_still_none(self):
        plugin = runtime.PluginDiscovery(
            manifest={"id": "manifest-only", "version": "1.0.0"},
            path=self._tmp,
            module_path=None,
            runtime={"loaded": False, "entrypoints": []},
        )
        self.assertIsNone(runtime.load_runtime_module(plugin))

    def test_a_missing_file_does_not_serve_a_stale_module(self):
        plugin = self._plugin("VALUE = 'present'")
        runtime.load_runtime_module(plugin)
        plugin.module_path.unlink()
        stamp = runtime._module_file_stamp(plugin.module_path)
        self.assertEqual(stamp[1:], (None, None))


@unittest.skipIf(runtime is None, "plugin runtime dependencies are required")
class TmdbSessionTests(unittest.TestCase):
    """The TMDB plugin is the one a credit-heavy refresh hammers."""

    @classmethod
    def setUpClass(cls):
        cls.source = (BACKEND / "next_plugins" / "tmdb" / "plugin.py").read_text(encoding="utf-8")

    def test_requests_are_made_through_a_shared_session(self):
        self.assertIn("_session().get(", self.source)
        self.assertNotIn("requests.get(", self.source)

    def test_the_session_is_built_once_under_a_lock(self):
        # Person credits are refreshed through a ThreadPoolExecutor with four
        # workers, so two threads can reach this at the same moment.
        body = self.source[self.source.index("def _session()") :][:2200]
        self.assertIn("_SESSION_LOCK", body)
        self.assertIn("requests.Session()", body)

    def test_cookies_are_refused(self):
        # The session is shared across every user and configuration in the
        # process; a cookie jar is mutable state that would carry between them.
        body = self.source[self.source.index("def _session()") :][:2200]
        self.assertIn("allowed_domains=[]", body)

    def test_the_pool_is_bounded_and_does_not_retry(self):
        body = self.source[self.source.index("def _session()") :][:2200]
        self.assertIn("pool_maxsize=", body)
        self.assertIn("max_retries=0", body, "retries would change today's failure behaviour")

    def test_the_manifest_version_moved(self):
        import json

        manifest = json.loads(
            (BACKEND / "next_plugins" / "tmdb" / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertNotEqual(manifest["version"], "1.7.0", "a changed plugin ships a new version")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
