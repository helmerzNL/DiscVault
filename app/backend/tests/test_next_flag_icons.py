"""Flag icons for the content-rating badge and the language picker.

The whole bug class here produces no error anywhere. A flag is an ``<img>``
pointing at ``/api/next/flags/<code>.svg``; when the file is not there the
route answers 404, the browser draws its broken-image glyph, and nothing is
logged on either side. The rating badge still shows the age label, so the
failure reads as a styling glitch rather than a missing file.

Two ways in, both of which had happened (#749):

**A flag named by language code.** Denmark's and Sweden's flags sat on disk as
``da.svg`` and ``sv.svg``. Every caller passes a *country* -- ``info.country``
on a rating, the region of a locale -- so both asked for ``dk.svg`` / ``se.svg``
and got neither. Naming is the whole fix, which is why the assertion below is
on the directory listing and not on a lookup table.

**A country in the list with no flag at all.** ``RATING_COUNTRIES_ORDER`` is the
picker's menu; ``AU``, ``BR``, ``NZ``, ``IN``, ``PH`` and ``MY`` were offered
there with no file behind them. Adding a country to that list is a one-word
edit that silently breaks a flag, so the list is tied to the directory here.
"""

import os
import re
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEXT_VIEWS_UI_PATH = os.path.join(BACKEND_DIR, "next_views_ui.py")
FLAGS_DIR = os.path.abspath(
    os.path.join(BACKEND_DIR, "..", "frontend", "flags")
)
I18N_DIR = os.path.abspath(
    os.path.join(BACKEND_DIR, "..", "frontend", "i18n", "next")
)


def _source() -> str:
    with open(NEXT_VIEWS_UI_PATH, "r", encoding="utf-8") as handle:
        return handle.read()


def _flag_files() -> set:
    return {
        name[:-4]
        for name in os.listdir(FLAGS_DIR)
        if name.endswith(".svg")
    }


def _js_block(source: str, opener: str, length: int = 1600) -> str:
    start = source.index(opener)
    return source[start : start + length]


def _string_array(source: str, name: str) -> list:
    block = _js_block(source, f"const {name} = [")
    body = block[block.index("[") : block.index("]") + 1]
    return re.findall(r'"([^"]+)"', body)


def _alias_map(source: str, function_name: str) -> dict:
    block = _js_block(source, f"function {function_name}(value) {{")
    body = block[block.index("const map = {") : block.index("};") + 1]
    return dict(re.findall(r'(\w+): "(\w+)"', body))


class FlagFileNamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()
        cls.files = _flag_files()

    def test_the_declared_codes_are_the_directory(self):
        """FLAG_FILE_CODES is what makes an unknown code render nothing.

        It only can if it says what is actually on disk. Drifting either way is
        invisible: a code listed with no file draws a broken image, and a file
        omitted from the list drops a flag that exists.
        """
        declared = _string_array(self.source, "FLAG_FILE_CODES")
        self.assertEqual(sorted(declared), sorted(self.files))
        self.assertEqual(declared, sorted(declared), "keep the list sorted")

    def test_flags_are_named_by_country_not_language(self):
        """The rename that fixed #749, stated so it cannot be undone.

        `da` and `sv` are languages; the files are Denmark's and Sweden's flags
        and every caller looks them up by country.
        """
        self.assertIn("dk", self.files)
        self.assertIn("se", self.files)
        self.assertNotIn("da", self.files)
        self.assertNotIn("sv", self.files)

    def test_every_flag_name_survives_the_route(self):
        """`next_frontend_flag` serves `[a-z]{2}.svg` and 404s the rest."""
        for code in sorted(self.files):
            self.assertRegex(code, r"^[a-z]{2}$")


class RatingCountryFlagTests(unittest.TestCase):
    """Every country the picker offers must resolve to a file.

    Resolution is modelled after `flagCodeForCountry`: the code itself when a
    file carries that name, else an alias. Anything else has no flag, and the
    badge shows the age label with nothing beside it.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = _source()
        cls.files = _flag_files()
        cls.country_aliases = _alias_map(cls.source, "flagCodeForCountry")
        cls.locale_aliases = _alias_map(cls.source, "flagCodeForLocale")

    def _resolve_country(self, value: str) -> str:
        raw = value.strip().lower()
        if raw in self.files:
            return raw
        if raw in self.country_aliases:
            return self.country_aliases[raw]
        return self._resolve_locale(raw)

    def _resolve_locale(self, value: str) -> str:
        raw = value.replace("_", "-").lower()
        base, _, region = raw.partition("-")
        if region in self.files:
            return region
        code = self.locale_aliases.get(base, base)
        return code if code in self.files else ""

    def test_every_offered_rating_country_has_a_flag(self):
        countries = _string_array(self.source, "RATING_COUNTRIES_ORDER")
        self.assertIn("DK", countries)
        self.assertIn("AU", countries)
        missing = [c for c in countries if not self._resolve_country(c)]
        self.assertEqual(missing, [], f"rating countries with no flag: {missing}")

    def test_every_shipped_locale_has_a_flag(self):
        """The language picker resolves the same way, off the locale's region."""
        locales = sorted(
            name[:-5] for name in os.listdir(I18N_DIR) if name.endswith(".json")
        )
        self.assertTrue(locales, "no locale catalogues found")
        missing = [loc for loc in locales if not self._resolve_locale(loc)]
        self.assertEqual(missing, [], f"locales with no flag: {missing}")

    def test_an_unknown_country_resolves_to_nothing(self):
        for value in ("", "zz", "xx-YY", "not-a-country"):
            self.assertEqual(self._resolve_country(value), "")


class FlagIconHtmlTests(unittest.TestCase):
    """The guard that turns an unresolvable code into no icon at all.

    Without it the helper interpolates whatever it was given straight into the
    src -- so an unknown country asks for a file that cannot exist and the user
    sees a broken image where a missing flag would have been unremarkable.
    """

    @classmethod
    def setUpClass(cls):
        cls.block = _js_block(_source(), "function flagIconHtml(value, label", 420)

    def test_it_returns_early_when_there_is_no_flag(self):
        self.assertIn("if (!code) return \"\";", self.block)
        self.assertLess(
            self.block.index("if (!code) return \"\";"),
            self.block.index("<img class=\"flag-icon\""),
        )


if __name__ == "__main__":
    unittest.main()
