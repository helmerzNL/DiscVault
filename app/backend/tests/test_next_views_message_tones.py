"""Every message tone the PWA passes must have a rule that renders it.

`next_views_ui.py` has no global `.good` / `.bad` / `.warn` classes. Each message
container styles its own tones, so a container that grows a third tone without
the matching CSS renders it as the base `color: var(--muted)` -- which is
byte-identical to passing no tone at all. The message appears, it just does not
carry the weight it was given.

That is how `.detail-message.warn` went unnoticed. It is every message on the
movie, container, series, person and discover detail pages -- including the
pressing picker's "Which pressing is this?" and a refresh reporting fields held
back by a format mismatch -- and it read as ordinary grey text on all of them.
Nothing failed, because there is nothing that could fail: a missing CSS rule is
not an error, it is a default.

So this test derives both halves from the source and compares them:

1. which container each `setXMessage` helper writes its tone onto, from the
   `className = \\`<container> ${tone}\\`` assignment inside it;
2. which tone literals are actually passed at that helper's call sites;
3. which `.<container>.<tone>` rules the stylesheet defines.

A tone in (2) with no rule in (3) fails, and the failure names the pairs.
"""

import os
import re
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

UI_SOURCE = os.path.join(repo_root, "app", "backend", "next_views_ui.py")

#: Tones that deliberately render as the container's base style. `info` states a
#: fact the reader did not ask about -- "Waiting for your passkey prompt...",
#: "No source recognised that title." -- and muted is the correct weight for it.
#: A tone here is a decision, not an omission; `warn` is the counterexample.
BASE_STYLE_TONES = frozenset({"info"})

#: Containers with no tone styling at all, where `good` and `bad` are as dead as
#: any other tone. Giving `.form-message` a palette is a visible change to the
#: wishlist and loan surfaces and belongs to its own change, decided on its own
#: evidence. Listed rather than skipped so it stays visible.
UNSTYLED_CONTAINERS = frozenset({"form-message"})

TONE = r"good|bad|warn|info|error"

_SETTER_RE = re.compile(
    r"function (\w+)\([^)]*\)\s*\{"
    r"(?:(?!\n    function ).)*?"
    r"className = `([a-z][\w-]*(?:[ ][\w-]+)*) \$\{tone",
    re.S,
)
_TRAILING_TONE_RE = re.compile(r'"(' + TONE + r')"\s*$')
_RULE_RE = re.compile(r"\.([\w-]+)\.(" + TONE + r")\s*[,{]")


def _read_source() -> str:
    with open(UI_SOURCE, encoding="utf-8") as handle:
        return handle.read()


def _call_arguments(source: str, function_name: str):
    """Each call's argument text, found by balancing parentheses.

    A regex cannot do this: the tone is the last argument and the ones before it
    contain `tNext(...)` calls with their own parentheses and commas.
    """
    for match in re.finditer(re.escape(function_name) + r"\(", source):
        index = match.end()
        depth = 1
        while depth and index < len(source):
            if source[index] == "(":
                depth += 1
            elif source[index] == ")":
                depth -= 1
            index += 1
        yield source[match.end() : index - 1]


class MessageToneCoverageTests(unittest.TestCase):
    def setUp(self):
        self.source = _read_source()
        self.setters = {
            match.group(1): match.group(2).split()[0]
            for match in _SETTER_RE.finditer(self.source)
        }
        self.styled = {}
        for match in _RULE_RE.finditer(self.source):
            self.styled.setdefault(match.group(1), set()).add(match.group(2))

        self.passed = {}
        for setter, container in self.setters.items():
            for arguments in _call_arguments(self.source, setter):
                found = _TRAILING_TONE_RE.search(arguments.strip())
                if found:
                    self.passed.setdefault(container, set()).add(found.group(1))

    def test_the_parse_found_the_setters_it_is_meant_to_check(self):
        """A test that silently matches nothing passes for the wrong reason."""
        self.assertGreaterEqual(len(self.setters), 20)
        self.assertEqual(self.setters.get("setMovieDetailMessage"), "detail-message")
        self.assertEqual(self.setters.get("setStartupGateMessage"), "startup-message")
        self.assertIn("warn", self.passed.get("detail-message", set()))

    def test_every_tone_passed_has_a_rule_that_renders_it(self):
        missing = []
        for container, tones in sorted(self.passed.items()):
            if container in UNSTYLED_CONTAINERS:
                continue
            for tone in sorted(tones - BASE_STYLE_TONES):
                if tone not in self.styled.get(container, set()):
                    missing.append(f".{container}.{tone}")

        self.assertEqual(
            missing,
            [],
            "These tones are passed but have no CSS rule, so they render as the "
            "container's base style - indistinguishable from passing no tone: "
            + ", ".join(missing)
            + ". Add the rule beside the container's existing .good/.bad, or - if "
            "the base style is the intended weight - add the tone to "
            "BASE_STYLE_TONES with the reason.",
        )

    def test_the_warn_tone_is_styled_where_the_detail_pages_use_it(self):
        # The specific regression: every detail page passes `warn`, and until
        # 26.9.44 none of them rendered it as anything.
        self.assertIn("warn", self.styled.get("detail-message", set()))
        self.assertIn("warn", self.styled.get("bulk-create-message", set()))

    def test_the_exception_lists_still_describe_something_real(self):
        """An allow-list nobody prunes is how the next gap hides."""
        for container in UNSTYLED_CONTAINERS:
            self.assertIn(
                container,
                self.passed,
                f".{container} is listed as an exception but nothing passes it a "
                "tone any more - drop it from UNSTYLED_CONTAINERS.",
            )
            self.assertNotIn(
                container,
                self.styled,
                f".{container} now has tone rules - drop it from "
                "UNSTYLED_CONTAINERS so it is checked like the others.",
            )
        passed_tones = {tone for tones in self.passed.values() for tone in tones}
        self.assertTrue(
            BASE_STYLE_TONES <= passed_tones,
            "BASE_STYLE_TONES names a tone nothing passes any more.",
        )


if __name__ == "__main__":  # pragma: no cover - convenience for local runs
    unittest.main()
