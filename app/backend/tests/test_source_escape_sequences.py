"""Guard against invalid escape sequences in Python string literals.

The browser UI is emitted as JavaScript embedded in ordinary (non-raw) Python
strings. A JavaScript regex literal escapes ``/``, ``.`` and ``\\s`` with a
backslash, which Python reads as an *invalid* escape sequence unless the
backslash itself is doubled.

Python currently leaves an unknown escape untouched, so such code still works by
accident -- but it emits ``SyntaxWarning`` on every import and is scheduled to
become a hard ``SyntaxError``, at which point the module would fail to load.

This test fails as soon as any invalid escape sequence is reintroduced.
"""

import pathlib
import sys
import tokenize
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SOURCE_ROOT = REPO_ROOT / "app"

EXCLUDED_DIR_NAMES = frozenset(
    {".git", ".venv", "venv", "__pycache__", "node_modules", "site-packages"}
)

# Characters that may legally follow a backslash in a Python string literal.
VALID_ESCAPE_CHARS = frozenset("\n\\'\"abfnrtv01234567xNuU")


def _string_prefix(token_text: str) -> str:
    """Return the literal's prefix, e.g. ``f``, ``rb`` or an empty string."""
    index = 0
    while index < len(token_text) and token_text[index] not in "\"'":
        index += 1
    return token_text[:index].lower()


def _invalid_escapes(path: pathlib.Path) -> list[tuple[int, str]]:
    """Return ``(line_number, escape)`` for every invalid escape in ``path``."""
    with open(path, "rb") as handle:
        tokens = list(tokenize.tokenize(handle.readline))

    findings: list[tuple[int, str]] = []
    for token in tokens:
        if token.type != tokenize.STRING:
            continue
        prefix = _string_prefix(token.string)
        if "r" in prefix:
            continue

        body = token.string[len(prefix):]
        index = 0
        while index < len(body):
            if body[index] != "\\":
                index += 1
                continue
            following = body[index + 1] if index + 1 < len(body) else ""
            if following not in VALID_ESCAPE_CHARS:
                line = token.start[0] + body[:index].count("\n")
                findings.append((line, "\\" + following))
            index += 2
    return findings


def _python_sources() -> list[pathlib.Path]:
    paths = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if EXCLUDED_DIR_NAMES.intersection(path.parts):
            continue
        paths.append(path)
    return paths


class InvalidEscapeSequenceTests(unittest.TestCase):
    def test_app_sources_contain_no_invalid_escape_sequences(self):
        sources = _python_sources()
        self.assertTrue(sources, f"no Python sources found under {SOURCE_ROOT}")

        problems = []
        for path in sources:
            relative = path.relative_to(REPO_ROOT).as_posix()
            for line, escape in _invalid_escapes(path):
                problems.append(f"{relative}:{line}: invalid escape sequence {escape!r}")

        self.assertEqual(
            [],
            sorted(set(problems)),
            "Invalid escape sequences found. Inside embedded JavaScript, double the "
            "backslash (write '\\\\/' instead of '\\/'), or make the Python literal raw.",
        )


if __name__ == "__main__":
    unittest.main()
