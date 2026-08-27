"""The gate that refuses a name nothing binds, tested on both answers.

A checker with no test of its own is the thing it was written to prevent:
it reports nothing, and "nothing" is indistinguishable from "clean". The
first version of this gate was exactly that, and it was wrong in the
direction that hides work -- it built its set of defined names from every
symbol the module *mentions*, so a module-level read of an undefined name
vouched for the function-level read of the same name and the finding was
suppressed.

So these tests pin both directions: what must be reported, and what must
never be, because a gate that cries wolf gets switched off and then it
protects nothing.
"""

from __future__ import annotations

import os
import sys
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.scripts.check_undefined_names import check_path


class UndefinedNameGateTests(unittest.TestCase):
    def findings(self, source: str) -> list[str]:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "module_under_test.py"
            path.write_text(textwrap.dedent(source), encoding="utf-8")
            return check_path(path)

    def assertFlags(self, source: str, name: str) -> None:
        messages = self.findings(source)
        self.assertTrue(messages, f"expected a finding for {name!r}, got none")
        self.assertTrue(
            any(f"'{name}'" in message for message in messages),
            f"expected a finding naming {name!r}, got {messages}",
        )

    def assertClean(self, source: str) -> None:
        self.assertEqual(self.findings(source), [])

    # ---- what must be reported ----------------------------------------

    def test_a_function_reading_a_name_nothing_binds_is_reported(self):
        self.assertFlags(
            """
            def f():
                return missing
            """,
            "missing",
        )

    def test_a_module_level_read_does_not_vouch_for_the_function_read(self):
        # The regression: `print(missing)` puts `missing` in the module's
        # symbol table without binding it, and the first version of the gate
        # accepted that as a definition.
        self.assertFlags(
            """
            print(missing)

            def f():
                return missing
            """,
            "missing",
        )

    def test_the_dropped_return_value_shape_is_reported(self):
        # Issue #720 in miniature.
        self.assertFlags(
            """
            def check():
                return {"id": 1}

            def route():
                check()
                return snapshot(actor)
            """,
            "actor",
        )

    def test_a_module_imported_under_another_name_is_reported(self):
        self.assertFlags(
            """
            import json as json_lib

            def f(value):
                return json.dumps(value)
            """,
            "json",
        )

    # ---- what must never be reported -----------------------------------

    def test_ordinary_imports_and_definitions_are_clean(self):
        self.assertClean(
            """
            import json
            from pathlib import Path

            CONSTANT = 1

            def helper():
                return CONSTANT

            def f():
                return json.dumps({"path": str(Path("."))}), helper()
            """
        )

    def test_a_name_bound_by_global_in_another_function_is_clean(self):
        self.assertClean(
            """
            def setup():
                global cache
                cache = {}

            def use():
                return cache
            """
        )

    def test_a_conditional_or_fallback_binding_counts_as_defined(self):
        # Whether the branch runs is not a question a symbol table can
        # answer, and guessing would refuse working modules.
        self.assertClean(
            """
            try:
                from fast import loads
            except ImportError:
                loads = None

            if loads is None:
                fallback = 1
            else:
                fallback = 2

            def f(raw):
                return loads(raw) if loads else fallback
            """
        )

    def test_builtins_and_module_dunders_are_clean(self):
        self.assertClean(
            """
            from pathlib import Path

            def f(values):
                return sorted(len(v) for v in values), Path(__file__).name
            """
        )

    def test_locals_closures_and_comprehensions_are_clean(self):
        self.assertClean(
            """
            def outer(rows):
                seen = set()

                def inner(row):
                    return row in seen

                return [row for row in rows if not inner(row)]
            """
        )


if __name__ == "__main__":
    unittest.main()
