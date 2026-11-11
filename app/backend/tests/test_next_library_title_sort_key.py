"""A name ordering skips one leading article -- on the fallback only.

App-Guidance `projects/discvault/specs/library-ordering.md` §1a, which the iOS
app adopted first (DiscVaultApp `Sources/Models/TitleSortKey.swift`, covered by
`DiscVaultTests/LeadingArticleSortTests.swift`). Until this change the PWA
ordered on §1 alone, so the same collection read in a different order on a
phone and in a browser: *The Matrix* under **M** on one, under **T** on the
other, with nothing anywhere to say which was right.

The rule in one paragraph: a row with a sort title sorts on it exactly as
typed. A row without one sorts on its name with one leading article skipped.
English articles are skipped for every viewer; the articles of the language the
app is displayed in are skipped as well; no other language's are. The text on
screen never changes.

Everything here runs the real functions under node, cut out of
`next_views_ui.py` by `fixtures/library-title-sort-replay.mjs`. A source-text
assertion cannot tell "The The" from "The", and the cases that matter here are
all of that kind. The cases deliberately mirror the iOS suite, so the two
clients are held to the same answers.

Needs no database.
"""

import json
import os
import shutil
import subprocess
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SOURCE_PATH = os.path.join(BACKEND_DIR, "next_views_ui.py")
APP_SOURCE_PATH = os.path.join(BACKEND_DIR, "next_app.py")
I18N_DIR = os.path.join(BACKEND_DIR, "..", "frontend", "i18n", "next")
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
LIBRARY_HARNESS = os.path.join(FIXTURES, "library-title-sort-replay.mjs")
LISTS_HARNESS = os.path.join(FIXTURES, "lists-sort-replay.mjs")

NODE = shutil.which("node")


def _run(harness, payload):
    result = subprocess.run(
        [NODE, harness, SOURCE_PATH, json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def movie(title, sort_title=None, year=None):
    row = {"title": title}
    if sort_title is not None:
        row["sort_title"] = sort_title
    if year is not None:
        row["year"] = year
    return {"kind": "movie", "movie": row, "title": title}


def series(title, sort_title=None):
    # Series are serialised camelCase; movies arrive as raw rows.
    row = {"title": title}
    if sort_title is not None:
        row["sortTitle"] = sort_title
    return {"kind": "series", "series": row, "title": title}


def container(title):
    # No sort_title column at all (library-ordering §1 corollary).
    return {"kind": "container", "container": {"title": title}, "title": title}


@unittest.skipUnless(NODE, "node is not available")
class LeadingArticleRuleTests(unittest.TestCase):
    def skip(self, names, locale="en-US"):
        return _run(LIBRARY_HARNESS, {"op": "skip", "names": names, "locale": locale})

    def assertSkips(self, expected, locale="en-US"):
        names = list(expected)
        self.assertEqual(dict(zip(names, self.skip(names, locale))), expected, f"viewer locale {locale}")

    def test_english_articles_are_skipped(self):
        self.assertSkips({
            "The Matrix": "Matrix",
            "A Clockwork Orange": "Clockwork Orange",
            "An American Werewolf in London": "American Werewolf in London",
            "the big lebowski": "big lebowski",
        })

    def test_an_article_is_a_whole_word_followed_by_whitespace(self):
        unchanged = ["A.I. Artificial Intelligence", "A-ha: The Movie", "I, Robot", "Theodora", "Anaconda", "Annie Hall"]
        self.assertEqual(self.skip(unchanged), unchanged)

    def test_a_title_that_is_only_an_article_keeps_its_name(self):
        self.assertSkips({"The": "The", "A": "A", "  The  ": "The"})

    def test_only_one_article_is_skipped(self):
        self.assertSkips({"The The": "The", "A The Matrix": "The Matrix"})

    def test_surrounding_whitespace_is_ignored(self):
        self.assertSkips({"  The   Matrix ": "Matrix", "The\tMatrix": "Matrix"})

    def test_diacritics_are_respected(self):
        """'À' is a preposition and Dutch 'Eén' is a numeral; dropping the
        accent would turn each into the article it resembles."""
        self.assertSkips({"À bout de souffle": "À bout de souffle"}, locale="fr-FR")
        self.assertSkips({"Eén": "Eén", "Eén nacht": "Eén nacht", "Een nacht": "nacht"}, locale="nl-NL")

    def test_every_language_skips_its_own_articles(self):
        cases = {
            "nl-NL": {"De Avonden": "Avonden", "Het Diner": "Diner", "Een vrouw": "vrouw"},
            "de-DE": {"Das Boot": "Boot", "Der Untergang": "Untergang", "Ein Herz": "Herz"},
            "fr-FR": {"Les Misérables": "Misérables", "La Haine": "Haine", "Une femme": "femme"},
            "es-ES": {"El laberinto del fauno": "laberinto del fauno", "Los otros": "otros"},
            "it-IT": {"Il Postino": "Postino", "La vita è bella": "vita è bella", "Gli uccelli": "uccelli"},
            "pt-PT": {"O Auto da Compadecida": "Auto da Compadecida", "Os Maias": "Maias"},
            "da-DK": {"Den skaldede frisør": "skaldede frisør", "Et hus": "hus"},
            "sv-SE": {"Det sjunde inseglet": "sjunde inseglet", "Ett hus": "hus"},
            "nb-NO": {"Den store stygge ulven": "store stygge ulven", "Ei jente": "jente"},
            "el-GR": {"Ο Νονός": "Νονός", "Η Λίστα": "Λίστα", "Ένα όνειρο": "όνειρο"},
            "hu-HU": {"Az ember tragédiája": "ember tragédiája", "Egy nap": "nap"},
            "ro-RO": {"O vară": "vară", "Un pas": "pas"},
        }
        for locale, expected in cases.items():
            with self.subTest(locale=locale):
                self.assertSkips(expected, locale=locale)

    def test_elided_articles_attach_to_the_next_word(self):
        self.assertSkips({"L'avventura": "avventura", "Un'ora sola ti vorrei": "ora sola ti vorrei"}, locale="it-IT")
        self.assertSkips({
            "L'Armée des ombres": "Armée des ombres",
            "L’Armée des ombres": "Armée des ombres",
            "L'": "L'",
            "L' Armée": "L' Armée",
        }, locale="fr-FR")

    def test_the_dutch_contracted_article_in_either_apostrophe(self):
        self.assertSkips({
            "'t Schaep met de 5 pooten": "Schaep met de 5 pooten",
            "’t Schaep met de 5 pooten": "Schaep met de 5 pooten",
            "‘t Schaep met de 5 pooten": "Schaep met de 5 pooten",
        }, locale="nl-NL")

    def test_english_applies_in_every_app_language(self):
        for locale in app_locales():
            with self.subTest(locale=locale):
                self.assertEqual(self.skip(["The Matrix"], locale), ["Matrix"])

    def test_another_languages_article_is_an_ordinary_word(self):
        """With every row applied at once, these all file one word along --
        for every viewer, including the ones who never see such a title."""
        unchanged = ["Die Hard", "Das Boot", "Il Postino", "Den of Thieves", "I Am Legend",
                     "O Brother, Where Art Thou?", "Les Misérables", "Un Chien Andalou"]
        self.assertEqual(self.skip(unchanged, "en-US"), unchanged)
        self.assertEqual(self.skip(["Die Hard", "Das Boot"], "nl-NL"), ["Die Hard", "Das Boot"])

    def test_the_test_is_lexical(self):
        """A German viewer loses the 'Die' of Die Hard. The remedy is the
        row's sort title, never an exception list in the comparator."""
        self.assertEqual(self.skip(["Die Hard"], "de-DE"), ["Hard"])
        order = _run(LIBRARY_HARNESS, {
            "op": "keys", "locale": "de-DE",
            "rows": [{"name": "Die Hard", "sortTitle": "Die Hard"}, {"name": "Heat"}, {"name": "Casablanca"}],
        })
        self.assertEqual(order, ["Casablanca", "Die Hard", "Heat"], "typing the title into the sort title pins it")

    def test_punctuation_after_the_word_is_not_an_article(self):
        self.assertEqual(self.skip(["I, Robot"], "it-IT"), ["I, Robot"])

    def test_region_and_script_subtags_resolve_to_the_language(self):
        self.assertEqual(self.skip(["O Auto"], "pt-PT"), ["Auto"])
        self.assertEqual(self.skip(["O Auto"], "pt"), ["Auto"])
        self.assertEqual(self.skip(["Het Diner"], "nl_NL"), ["Diner"])
        for locale in ("zh-TW", "zh-CN", "pl-PL", "", "xx-XX"):
            with self.subTest(locale=locale):
                self.assertEqual(self.skip(["The Matrix", "Die Hard", "Het Diner"], locale), ["Matrix", "Die Hard", "Het Diner"])


@unittest.skipUnless(NODE, "node is not available")
class TitleSortKeyTests(unittest.TestCase):
    def order(self, rows, locale="en-US", descending=False):
        return _run(LIBRARY_HARNESS, {"op": "keys", "rows": rows, "locale": locale, "descending": descending})

    def test_a_sort_title_is_compared_as_typed(self):
        rows = [
            {"name": "The Matrix", "sortTitle": "The Matrix"},
            {"name": "Unforgiven"},
            {"name": "The Abyss"},
            {"name": "Heat"},
        ]
        self.assertEqual(
            self.order(rows),
            ["The Abyss", "Heat", "The Matrix", "Unforgiven"],
            "pinned under T, while The Abyss still files under A",
        )

    def test_a_blank_sort_title_falls_back_to_the_name(self):
        for blank in (None, "", "   "):
            with self.subTest(sort_title=blank):
                rows = [{"name": "The Matrix", "sortTitle": blank}, {"name": "Heat"}]
                self.assertEqual(self.order(rows), ["Heat", "The Matrix"])

    def test_the_sort_title_still_decides_the_order(self):
        rows = [
            {"name": "Dune: Part Two", "sortTitle": "Dune 2"},
            {"name": "The Matrix"},
            {"name": "Dune", "sortTitle": "Dune 1"},
            {"name": "The Abyss"},
        ]
        self.assertEqual(self.order(rows), ["The Abyss", "Dune", "Dune: Part Two", "The Matrix"])

    def test_equal_keys_fall_back_to_the_full_name(self):
        self.assertEqual(self.order([{"name": "The Matrix"}, {"name": "Matrix"}]), ["Matrix", "The Matrix"])
        self.assertEqual(self.order([{"name": "Matrix"}, {"name": "The Matrix"}]), ["Matrix", "The Matrix"])
        self.assertEqual(
            self.order([{"name": "Matrix"}, {"name": "The Matrix"}], descending=True),
            ["The Matrix", "Matrix"],
        )

    def test_descending_is_the_same_order_reversed(self):
        rows = [{"name": name} for name in ("The Matrix", "Alien", "A Bridge Too Far", "Zulu", "Matrix", "An Education")]
        self.assertEqual(self.order(rows, descending=True), list(reversed(self.order(rows))))


@unittest.skipUnless(NODE, "node is not available")
class LibraryComparatorTests(unittest.TestCase):
    """The three Library comparators of library-ordering §3, each run for real.
    A grid that skips articles beside a list view that does not is #716 again:
    every row present, two orders, nothing red."""

    MIXED = [
        movie("The Matrix", year="1999"),
        container("The Matrix Collection"),
        series("The Wire"),
        movie("Heat", year="1995"),
        movie("Avatar", year="2009"),
        movie("Fight Club", year="1999"),
    ]
    MIXED_ORDER = ["Avatar", "Fight Club", "Heat", "The Matrix", "The Matrix Collection", "The Wire"]

    def grid(self, items, mode="title_asc", locale="en-US"):
        return _run(LIBRARY_HARNESS, {"op": "grid", "items": items, "mode": mode, "locale": locale})

    def list_view(self, items, key="title", direction="asc", locale="en-US"):
        return _run(LIBRARY_HARNESS, {"op": "list", "items": items, "sort": {"key": key, "direction": direction}, "locale": locale})

    def detail(self, items, key="title", direction="asc", locale="en-US"):
        return _run(LIBRARY_HARNESS, {"op": "detail", "items": items, "sort": {"key": key, "direction": direction}, "locale": locale})

    def test_the_grid_files_movies_series_and_containers_by_one_key(self):
        self.assertEqual(self.grid(self.MIXED), self.MIXED_ORDER)

    def test_the_grid_reverse_is_the_exact_reverse(self):
        items = self.MIXED + [movie("Matrix")]
        self.assertEqual(self.grid(items, "title_desc"), list(reversed(self.grid(items, "title_asc"))))

    def test_the_grid_breaks_a_year_tie_on_the_title_key(self):
        self.assertEqual(self.grid([movie("The Matrix", year="1999"), movie("Fight Club", year="1999")], "year_desc"),
                         ["Fight Club", "The Matrix"])

    def test_the_list_view_title_column_agrees_with_the_grid(self):
        self.assertEqual(self.list_view(self.MIXED), self.MIXED_ORDER)

    def test_the_list_view_reverse_is_the_exact_reverse(self):
        items = self.MIXED + [movie("Matrix")]
        self.assertEqual(self.list_view(items, direction="desc"), list(reversed(self.list_view(items))))

    def test_the_list_view_tiebreak_uses_the_title_key(self):
        """Every row has the same (empty) format here, so the order is
        entirely the tiebreaker's."""
        self.assertEqual(self.list_view(self.MIXED, key="format"), self.MIXED_ORDER)

    def test_the_series_disc_table_agrees_with_the_grid(self):
        self.assertEqual(self.detail(self.MIXED), self.MIXED_ORDER)
        items = self.MIXED + [movie("Matrix")]
        self.assertEqual(self.detail(items, direction="desc"), list(reversed(self.detail(items))))

    def test_a_series_sort_title_is_read_from_its_camelcase_field(self):
        items = [series("The Office (US)", sort_title="Office (US)"), series("The Wire"), movie("Paths of Glory")]
        self.assertEqual(self.grid(items), ["The Office (US)", "Paths of Glory", "The Wire"])

    def test_a_movie_sort_title_is_never_skipped(self):
        items = [movie("The Matrix", sort_title="The Matrix"), movie("Heat"), movie("The Abyss")]
        self.assertEqual(self.grid(items), ["The Abyss", "Heat", "The Matrix"])
        self.assertEqual(self.list_view(items), ["The Abyss", "Heat", "The Matrix"])

    def test_the_order_follows_the_display_language(self):
        items = [movie("Het Diner"), movie("Alien"), movie("De Lift"), movie("Zus & Zo")]
        self.assertEqual(self.grid(items, locale="nl-NL"), ["Alien", "Het Diner", "De Lift", "Zus & Zo"])
        self.assertEqual(self.grid(items, locale="en-US"), ["Alien", "De Lift", "Het Diner", "Zus & Zo"])

    def test_the_displayed_title_is_never_changed(self):
        self.assertIn("The Matrix", self.grid([movie("The Matrix")]))


@unittest.skipUnless(NODE, "node is not available")
class ListsTabTests(unittest.TestCase):
    def test_the_watchlist_files_by_the_same_key_as_the_library(self):
        entries = [
            {"title": "The Thing"},
            {"title": "Alien", "sort_title": "Alien"},
            {"title": "Zulu"},
            {"title": "A Bridge Too Far"},
            {"title": "The Abyss"},
            {"title": "The Matrix", "sort_title": "The Matrix"},
        ]
        result = _run(LISTS_HARNESS, {"entries": entries, "mode": "title_asc", "kind": "watchlist", "locale": "en-US"})
        self.assertEqual(result, ["The Abyss", "Alien", "A Bridge Too Far", "The Matrix", "The Thing", "Zulu"])

    def test_the_dutch_article_is_skipped_for_a_dutch_viewer(self):
        entries = [{"title": "Het Diner"}, {"title": "De Lift"}, {"title": "Alien"}]
        result = _run(LISTS_HARNESS, {"entries": entries, "mode": "title_asc", "kind": "watchlist", "locale": "nl-NL"})
        self.assertEqual(result, ["Alien", "Het Diner", "De Lift"])


def app_locales():
    return sorted(name[:-5] for name in os.listdir(I18N_DIR) if name.endswith(".json"))


@unittest.skipUnless(NODE, "node is not available")
class ArticleTableCoverageTests(unittest.TestCase):
    """'One row per language the app is translated into' (library-ordering
    §1a). A language with no articles is declared as such, so a new locale
    that is in neither list fails here -- in the change that adds it -- rather
    than quietly sorting on English articles alone."""

    @classmethod
    def setUpClass(cls):
        cls.tables = _run(LIBRARY_HARNESS, {"op": "tables"})

    def test_every_app_language_is_decided(self):
        decided = set(self.tables["articles"]) | set(self.tables["none"])
        for locale in app_locales():
            language = locale.split("-")[0].lower()
            with self.subTest(locale=locale):
                self.assertIn(language, decided, f"{locale} has neither an article row nor a declaration that it needs none")

    def test_no_language_is_both(self):
        self.assertEqual(set(self.tables["articles"]) & set(self.tables["none"]), set())

    def test_no_row_for_a_language_the_app_does_not_ship(self):
        languages = {locale.split("-")[0].lower() for locale in app_locales()}
        self.assertEqual(set(self.tables["articles"]) - languages, set())
        self.assertEqual(set(self.tables["none"]) - languages, set())


class SourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SOURCE_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()
        with open(APP_SOURCE_PATH, encoding="utf-8") as handle:
            cls.app_source = handle.read()

    def test_the_series_disc_grid_and_list_use_the_key(self):
        """In grid and list mode the discs used to be drawn in the order the
        server sent, which is §1 only. The detail table re-sorted; the other
        two modes did not, so one series page had two orders."""
        start = self.source.index("function renderSeriesDiscs(detail) {")
        body = self.source[start:self.source.index("function seriesDiscDetailTableHtml(", start)]
        self.assertIn("titleSortKey(disc.title, disc.sort_title || disc.sortTitle)", body)
        self.assertIn("compareTitleSortKeys(left.key, right.key)", body)
        self.assertIn("const decorated = ordered.map(", body)

    def test_the_server_page_order_is_left_alone(self):
        """Deliberate (library-ordering, open question settled with this
        change): the page order is a delivery order. The SPA hydrates the whole
        library and re-sorts it, so the article rule lives in the client's
        comparators, and the server keeps the total order -- ending in m.id --
        that OFFSET paging needs and migration 086 indexes."""
        self.assertIn("ORDER BY lower(COALESCE(m.sort_title, m.title)), m.year NULLS LAST, m.id", self.app_source)

    def test_the_article_table_is_declared_before_any_render_can_read_it(self):
        """A `const` read before its line has run throws. The table sits beside
        localeState, near the top of the script, not beside the helpers."""
        table = self.source.index("const LEADING_ARTICLES = {")
        cache = self.source.index("let leadingArticleCache = null;")
        self.assertLess(table, self.source.index("localeState.locale = preferredNextLocale();"))
        self.assertLess(cache, self.source.index("function leadingArticlesFor(locale) {"))


if __name__ == "__main__":
    unittest.main()
