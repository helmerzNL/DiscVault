"""One name, one function, in a script that is one scope.

The PWA is a single inline `<script>` several thousand lines long, so every
`function foo()` in it shares one scope. JavaScript does not object to two
declarations of one name — the later one silently wins, for the whole script,
including for callers written above it.

That is not hypothetical here. `languageLabel` was declared twice: once taking
a language *code* and resolving it through `Intl.DisplayNames`, and once, four
thousand lines later, taking a locale *item* off the language picker. The
second won. Every caller of the first got `""` back, because a string has no
`.nativeName`, so:

- audio tracks and subtitles showed the raw code on every screen, in the
  detail view and in the edit form;
- `languageWithCode` could never append a name to a code;
- `guessAudioTrack`, which turns a legacy free-text track into a structured
  one by finding a language *name* in the prose, matched nothing and always
  returned null — so free-text tracks stayed free text, and kept withholding
  their whole field from a contribution.

Nothing failed. It read as "we do not show language names", for months.

Source-text assertions, in the idiom the other UI tests here use.
"""

import os
import re
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEXT_VIEWS_UI_PATH = os.path.join(BACKEND_DIR, "next_views_ui.py")

#: Declarations that are deliberately repeated, with the reason. Empty today:
#: every duplicate found when this test was written was a bug.
ALLOWED_DUPLICATES: dict[str, str] = {}


class InlineScriptScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()

    def declarations(self) -> dict[str, list[int]]:
        """Every top-level `function name(` in the inline script, by line.

        Indentation is the discriminator: the script is emitted from Python
        string literals indented four spaces, and a nested helper is indented
        further. It is a heuristic, and it only has to be good enough to catch
        two functions declared at the same level with the same name.
        """
        found: dict[str, list[int]] = {}
        for number, line in enumerate(self.source.splitlines(), start=1):
            match = re.match(r"^ {4}(?:async )?function ([A-Za-z_$][\w$]*)\s*\(", line)
            if match:
                found.setdefault(match.group(1), []).append(number)
        return found

    def test_no_function_is_declared_twice_in_one_scope(self):
        declarations = self.declarations()
        self.assertGreater(len(declarations), 100, "the scan found no functions to check")
        duplicates = {
            name: lines
            for name, lines in declarations.items()
            if len(lines) > 1 and name not in ALLOWED_DUPLICATES
        }
        self.assertEqual(
            duplicates,
            {},
            "declared more than once in one scope; the later one silently wins: "
            + ", ".join(f"{name} at lines {lines}" for name, lines in sorted(duplicates.items())),
        )

    def test_the_language_helpers_are_the_pair_that_was_conflated(self):
        """Named explicitly as well as swept, because these two are the ones
        whose signatures differ enough to return a plausible wrong answer
        rather than to throw."""
        declarations = self.declarations()
        self.assertEqual(len(declarations.get("languageLabel", [])), 1)
        self.assertEqual(len(declarations.get("localeOptionLabel", [])), 1)
        self.assertIn("Intl.DisplayNames", self.source)

    def test_the_edit_form_labels_a_track_language_with_its_name(self):
        """The request this came from: the edit form should read like
        MovieVault's, which names the language rather than spelling its code.
        `languageWithCode` already did that — it just had nothing to work with
        while `languageLabel` was shadowed."""
        self.assertIn("languageWithCode", self.source)
        for row in ("audioTrackRowHtml", "subtitleRowHtml"):
            start = self.source.index(f"function {row}(")
            body = self.source[start : start + 900]
            self.assertIn("languageWithCode", body, f"{row} does not name the language")


class ContributionReadabilityTests(unittest.TestCase):
    """A correction is read by comparing it to the film on screen.

    So it has to be rendered by the same functions that render the film. Two
    formatters for one fact drift, and this pair had: the release screen said
    `Nederlands (DTS-HD MA 5.1)` while the sheet said `nl DTS-HD MA 5.1`.
    """

    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()

    def test_the_sheet_renders_tracks_with_the_release_screen_renderers(self):
        start = self.source.index("function contributeValueText(")
        body = self.source[start : self.source.index("function contributeButton(")]
        self.assertIn("subtitlesText(value)", body)
        self.assertIn("audioTracksText(value)", body)

    def test_the_two_values_are_laid_out_to_be_compared(self):
        """A grid with a label column, so both values start at the same place.
        Prefixed prose put them at a different offset on every field, which is
        the thing that made a six-subtitle change unreadable."""
        self.assertIn("contribute-change-compare", self.source)
        self.assertIn("grid-template-columns: auto minmax(0, 1fr)", self.source)

    def test_the_structured_lists_are_stacked_one_entry_per_line(self):
        self.assertIn("CONTRIBUTE_STACKED_FIELDS", self.source)
        for field in ("audioTracks", "subtitles", "discs"):
            self.assertIn(f'"{field}"', self.source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
