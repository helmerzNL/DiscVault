#!/usr/bin/env python3
"""Refuse a module that reads a name nothing ever defines.

`python -m py_compile` is the only whole-file check this repository runs
over the backend, and it proves a module *parses*. A function that reads a
name it never bound parses perfectly and raises `NameError` the first time
a request reaches that line:

    with connect() as conn:
        require_next_permission(conn, "containers.edit")   # result dropped
        ...
        capture_collection_value_snapshot(conn, actor)     # never bound

That is issue #720 verbatim -- every bulk "add to box-set" returned
`name 'actor' is not defined`, on a line that compiled cleanly and that no
test reached. The same file also called `json.dumps` in the import
preview's box-set branch while importing the module as `json_lib`.

Both are the same shape, and both are decidable without running anything:
a function-scope name that Python resolves to a module global, where the
module defines no such global and the builtins have no such name, can only
raise. This script reports those and nothing else -- it is deliberately not
a linter, because a check that also reports style has to be argued with,
and one that only reports "this line cannot run" does not.

Usage:

    python scripts/check_undefined_names.py .
    python scripts/check_undefined_names.py next_app.py next_auth.py

A directory is walked for every `*.py` beneath it, so CI names the tree
rather than a list of modules -- a list stops covering whatever is added
after it is written, and nothing fails to say so.

Exits non-zero, listing file, line and name, when a module fails.
"""

from __future__ import annotations

import builtins
import symtable
import sys
from pathlib import Path

# Module attributes Python provides at import time. They are real globals at
# runtime, but `symtable` cannot see them because nothing in the source
# assigns them.
MODULE_DUNDERS = frozenset(
    {
        "__file__",
        "__name__",
        "__doc__",
        "__package__",
        "__spec__",
        "__loader__",
        "__builtins__",
        "__path__",
        "__debug__",
    }
)


def _scope_path(parents: list[str], table: symtable.SymbolTable) -> str:
    return ".".join([*parents, table.get_name()])


def _walk(
    table: symtable.SymbolTable,
    parents: list[str],
    defined: frozenset[str],
    findings: list[tuple[int, str, str]],
) -> None:
    if table.get_type() == "function":
        scope = _scope_path(parents[:-1], table)
        for symbol in table.get_symbols():
            name = symbol.get_name()
            if not symbol.is_referenced() or not symbol.is_global():
                continue
            if name in defined or name in MODULE_DUNDERS or hasattr(builtins, name):
                continue
            findings.append((table.get_lineno(), scope, name))
    for child in table.get_children():
        _walk(child, [*parents, child.get_name()], defined, findings)


def check_path(path: Path) -> list[str]:
    """Return one message per undefined global read inside a function."""
    source = path.read_text(encoding="utf-8")
    table = symtable.symtable(source, str(path), "exec")
    # Anything bound anywhere at module level counts as defined, including
    # names bound only under an `if` or in a `try`/`except ImportError`
    # fallback: whether that branch runs is not a question this check can
    # answer, and guessing would make it refuse working modules.
    defined = frozenset(symbol.get_name() for symbol in table.get_symbols())
    findings: list[tuple[int, str, str]] = []
    for child in table.get_children():
        _walk(child, [child.get_name()], defined, findings)
    return [
        f"{path}:{lineno}: {scope}() reads '{name}', which no module-level "
        f"definition or builtin provides"
        for lineno, scope, name in sorted(findings)
    ]


def _python_files(arguments: list[str]) -> list[Path]:
    """Expand directories to the `*.py` files beneath them, sorted."""
    paths: list[Path] = []
    for argument in arguments:
        path = Path(argument)
        if path.is_dir():
            paths.extend(
                candidate
                for candidate in sorted(path.rglob("*.py"))
                if "__pycache__" not in candidate.parts
            )
        else:
            paths.append(path)
    return paths


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_undefined_names.py <file.py> [...]", file=sys.stderr)
        return 2
    messages: list[str] = []
    for path in _python_files(argv):
        try:
            messages.extend(check_path(path))
        except SyntaxError as exc:
            messages.append(f"{path}:{exc.lineno}: could not parse: {exc.msg}")
    if messages:
        print("\n".join(messages))
        print(
            f"\n{len(messages)} name(s) cannot resolve at runtime. "
            "Bind the name, or import it under the name the module actually uses.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
