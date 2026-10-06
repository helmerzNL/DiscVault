"""The app shell negotiates the browser's language before falling back.

The collection and migration pages already consult `navigator.languages`; the
shell, which nearly every visitor lands on, initialised `localeState.locale`
straight to `nl-NL`. A fix that only negotiated "when nothing is stored" would
not have worked either, because `loadLocale()` stored the result of every
successful load -- the first fallback load included -- so one page view pinned
the browser. Only an explicit pick may be stored now.

Source-text assertions, in the idiom the other UI tests here use.
"""

import os
import re
import unittest


NEXT_VIEWS_UI_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "next_views_ui.py")
)


class ShellLocaleNegotiationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()

    def function_body(self, signature):
        start = self.source.index(signature)
        return self.source[start:start + 2500]

    def test_shell_consults_browser_languages_with_british_english_last(self):
        body = self.function_body("function preferredNextLocale()")
        self.assertIn("navigator.languages", body)
        loop_end = body.index("return normalized;")
        self.assertIn("SHELL_FALLBACK_LOCALE", body[loop_end:loop_end + 120])
        self.assertIn('const SHELL_FALLBACK_LOCALE = "en-GB";', self.source)

    def test_initial_locale_is_not_read_straight_from_storage(self):
        self.assertNotIn(
            'locale: localStorage.getItem("dv_next_locale")', self.source
        )
        self.assertIn("localeState.locale = preferredNextLocale();", self.source)

    def test_only_explicit_picks_are_pinned(self):
        body = self.function_body("async function loadLocale(")
        self.assertIn("explicit = false", body)
        pinned = re.search(r"if \(explicit\) \{(.*?)\}", body, re.S)
        self.assertIsNotNone(pinned)
        self.assertIn('setItem("dv_next_locale"', pinned.group(1))
        # No unconditional write elsewhere in the function.
        self.assertEqual(body.count('setItem("dv_next_locale"'), 1)

    def test_language_pickers_mark_the_choice_explicit(self):
        self.assertIn("loadLocale(select.value, {explicit: true})", self.source)
        self.assertIn("loadLocale(event.target.value, {explicit: true})", self.source)

    def test_aliases_match_the_other_pages(self):
        body = self.function_body("function normalizeShellLocale(")
        for alias in ('"en-gb"', '"zh-hant"', "nb-NO"):
            self.assertIn(alias, body)


if __name__ == "__main__":
    unittest.main()
