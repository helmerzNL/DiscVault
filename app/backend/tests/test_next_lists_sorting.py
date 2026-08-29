"""The Watchlist and Watched list can be ordered by something other than date (#719).

Reported as "the Watchlist does not sort in order of Sort-Title (or even
Title). It looks like it sorts on modified date." Half right: it sorts on the
*list* date -- `watchlist_items.added_at` for the Watchlist and
`watch_history.watched_at` for Watched -- and there was no control to ask for
anything else.

The sort is done on the client, over the arrays the Lists page already holds,
which is only correct because #730 removed the 500-entry ceiling: sorting a
truncated list would put the first 500 rows *by date* in title order and call
it the Watchlist. That dependency is worth stating because nothing about the
sorting code shows it.

These are source-contract tests. The Lists page is inline JavaScript inside
`next_views_ui.py`, so what can be checked here is what the module actually
emits -- the wiring that a copy-paste from the Library would get wrong, and the
decisions that would otherwise be invisible:

  - the menu uses its own `data-lists-sort-option`, because the Library's
    `[data-sort-option]` binding is a *global* querySelectorAll (deliberately,
    so the location detail page shares it) and would re-sort the Library from a
    click on this menu, silently;
  - the default is the order the server already returns, so no one's list
    rearranges itself on upgrade;
  - `sort_title` decides the order while `title` is displayed;
  - the per-day grouping of the Watched list belongs to a date order only.

Needs no database.
"""

import os
import re
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

SOURCE_PATH = os.path.join(os.path.dirname(__file__), "..", "next_views_ui.py")
I18N_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "i18n", "next")

SORT_OPTIONS = ("date_desc", "date_asc", "title_asc", "title_desc", "year_desc", "year_asc")


class ListsSortingSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SOURCE_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()

    def _function(self, name, span=2600):
        start = self.source.index(f"function {name}(")
        return self.source[start:start + span]

    # -- the control exists and offers what was asked for -------------------

    def test_the_menu_offers_every_declared_option(self):
        panel_start = self.source.index('id="listsSortPanel"')
        panel = self.source[panel_start:self.source.index("</div>", panel_start)]
        for option in SORT_OPTIONS:
            self.assertIn(
                f'data-lists-sort-option="{option}"',
                panel,
                f"{option} is accepted by the comparator but cannot be chosen",
            )

    def test_every_offered_option_is_one_the_comparator_accepts(self):
        offered = set(re.findall(r'data-lists-sort-option="([a-z_]+)"', self.source))
        declared = set(re.findall(r'LISTS_SORT_MODES = \[([^\]]+)\]', self.source)[0].replace('"', "").replace(" ", "").split(","))
        self.assertEqual(offered, declared, "a button nobody handles falls back to the default and looks broken")
        self.assertEqual(declared, set(SORT_OPTIONS))

    def test_an_unknown_stored_mode_falls_back_rather_than_breaking(self):
        body = self._function("normalizeListsSortMode", 300)
        self.assertIn("LISTS_SORT_MODES.includes", body)
        self.assertIn('"date_desc"', body)

    # -- the wiring a copy-paste would get wrong ----------------------------

    def test_the_lists_menu_does_not_reuse_the_librarys_sort_attribute(self):
        """The Library binds `[data-sort-option]` globally on purpose -- the
        location detail page shows the library too. A Lists menu built on that
        attribute would re-sort the Library on every click, with no error."""
        panel_start = self.source.index('id="listsSortPanel"')
        panel = self.source[panel_start:self.source.index("</div>", panel_start)]
        self.assertNotIn(
            'data-sort-option="',
            panel,
            "this panel must not share the Library's attribute",
        )

    def test_the_lists_options_have_their_own_handler_writing_their_own_key(self):
        # `document.` prefixed: the active-state sync in renderListsSortMenu
        # scopes its own lookup to the menu node, and matching that one instead
        # would test nothing about the click handler.
        start = self.source.index('document.querySelectorAll("[data-lists-sort-option]")')
        handler = self.source[start:start + 500]
        self.assertIn("listsSortMode = normalizeListsSortMode", handler)
        self.assertIn('localStorage.setItem("dv_next_lists_sort"', handler)
        self.assertIn("renderListsView()", handler)
        self.assertNotIn("collectionSortMode", handler)
        self.assertNotIn("renderCollectionSurface()", handler)

    def test_the_library_handler_is_left_alone(self):
        start = self.source.index('document.querySelectorAll("[data-sort-option]")')
        handler = self.source[start:start + 400]
        self.assertIn("collectionSortMode", handler)
        self.assertNotIn("listsSortMode", handler)

    def test_the_menu_joins_the_shared_open_close_machinery(self):
        start = self.source.index("const COLLECTION_MENUS = [")
        registry = self.source[start:self.source.index("]", start)]
        self.assertIn('menu: "listsSortMenu"', registry)
        self.assertIn('trigger: "listsSortTrigger"', registry)
        self.assertIn('panel: "listsSortPanel"', registry)

    # -- the decisions that would otherwise be invisible --------------------

    def test_the_default_is_the_order_the_server_already_returns(self):
        start = self.source.index("let listsSortMode")
        line = self.source[start:self.source.index("\n", start)]
        self.assertIn('localStorage.getItem("dv_next_lists_sort")', line)
        self.assertIn('"date_desc"', line, "any other default rearranges every existing list on upgrade")

    def test_the_order_is_taken_from_sort_title_while_title_is_displayed(self):
        body = self._function("listsEntrySortTitle", 300)
        self.assertIn("sort_title", body)
        self.assertIn("sortTitle", body)
        self.assertIn("title", body)

    def test_the_title_comparison_is_locale_aware_and_case_insensitive(self):
        body = self._function("sortPersonalListEntries")
        self.assertIn("localeCompare", body)
        self.assertIn('sensitivity: "base"', body)
        self.assertIn("localeState.locale", body)

    def test_each_list_sorts_on_its_own_date(self):
        body = self._function("listsEntryDateValue", 500)
        self.assertIn("watched_at", body)
        self.assertIn("watchlist_added_at", body)

    def test_title_breaks_ties_under_every_mode(self):
        """Two films added in the same second, or sharing a year, must not land
        in an order that changes between renders."""
        body = self._function("sortPersonalListEntries")
        tail = body[body.index("listsEntrySortTitle(a).localeCompare"):]
        self.assertIn("mode === \"title_desc\" ? -diff : diff", tail)
        for guard in ('if (diff) return mode === "date_desc"', 'if (diff) return mode === "year_desc"'):
            self.assertIn(guard, body, "a zero difference must fall through to the title comparison")

    def test_both_lists_are_sorted_before_they_are_rendered(self):
        start = self.source.index("function renderListsView(")
        render = self.source[start:self.source.index("async function loadListsView(")]
        self.assertIn('sortPersonalListEntries(listsState.watchlist, "watchlist")', render)
        self.assertIn('sortPersonalListEntries(listsState.watched, "watched")', render)

    def test_the_day_grouping_is_dropped_when_the_order_is_not_chronological(self):
        body = self._function("watchedGroupsHtml", 1800)
        self.assertIn("normalizeListsSortMode(listsSortMode)", body)
        self.assertIn('sortMode === "date_desc" || sortMode === "date_asc"', body)
        self.assertIn("watchedGroups(entries)", body)
        self.assertIn("date: null", body, "the flat case must still render, without a date heading")

    def test_the_control_is_hidden_on_the_lists_that_do_not_use_it(self):
        body = self._function("renderListsSortMenu", 2000)
        self.assertIn('active === "watchlist" || active === "watched"', body)
        self.assertIn("classList.toggle(\"hidden\", !sortable)", body)

    def test_the_date_labels_follow_the_active_tab(self):
        body = self._function("renderListsSortMenu", 2000)
        self.assertIn("lists.sortWatchedNewest", body)
        self.assertIn("lists.sortAddedNewest", body)
        self.assertIn("lists.sortWatchedOldest", body)
        self.assertIn("lists.sortAddedOldest", body)

    def test_the_active_option_is_marked_for_assistive_technology(self):
        body = self._function("renderListsSortMenu", 2000)
        self.assertIn("aria-checked", body)
        self.assertIn('button.dataset.listsSortOption === listsSortMode', body)


class ListsSortingTranslationTests(unittest.TestCase):
    """The four labels the menu builds at runtime, in every locale. The generic
    ones reuse the Library's existing `collection.sort*` keys."""

    NEW_KEYS = (
        "lists.sortAddedNewest",
        "lists.sortAddedOldest",
        "lists.sortWatchedNewest",
        "lists.sortWatchedOldest",
    )
    REUSED_KEYS = (
        "collection.sort",
        "collection.sortNameAsc",
        "collection.sortNameDesc",
        "collection.sortYearNewest",
        "collection.sortYearOldest",
    )

    def test_every_locale_carries_the_new_and_reused_keys(self):
        import json

        locales = sorted(name for name in os.listdir(I18N_DIR) if name.endswith(".json"))
        self.assertTrue(locales, "no locale files found")
        with open(os.path.join(I18N_DIR, "en-US.json"), encoding="utf-8") as handle:
            english = json.load(handle)
        for name in locales:
            with open(os.path.join(I18N_DIR, name), encoding="utf-8") as handle:
                data = json.load(handle)
            for key in self.NEW_KEYS + self.REUSED_KEYS:
                self.assertIn(key, data, f"{name} is missing {key}")
                self.assertTrue(str(data[key]).strip(), f"{name} has an empty {key}")
            if name != "en-US.json":
                translated = [key for key in self.NEW_KEYS if data[key] != english[key]]
                self.assertTrue(
                    translated,
                    f"{name} appears to have copied the English labels verbatim",
                )


if __name__ == "__main__":
    unittest.main()


import json
import shutil
import subprocess

NODE = shutil.which("node")
HARNESS_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "lists-sort-replay.mjs")


@unittest.skipUnless(NODE, "node is not available")
class ListsSortingBehaviourTests(unittest.TestCase):
    """Run the comparator, rather than reading it.

    Every assertion in the class above is a string match, and this repository
    already learned what that is worth: each source-text test of the library
    paging module passed throughout #715 while the library stopped at 700 of
    2,509 movies. A string cannot notice a case nobody thought of.

    So this executes the real functions, cut out of `next_views_ui.py` by
    `fixtures/lists-sort-replay.mjs`, against entries shaped like the ones the
    Lists endpoint returns.
    """

    def sorted_titles(self, entries, *, mode, kind="watchlist", locale="en-US"):
        payload = json.dumps({"entries": entries, "mode": mode, "kind": kind, "locale": locale})
        result = subprocess.run(
            [NODE, HARNESS_PATH, SOURCE_PATH, payload],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_a_title_sort_orders_on_sort_title_not_the_displayed_title(self):
        """The reported complaint, and the reason `sort_title` exists: an
        article at the front of a title must not decide where it files."""
        entries = [
            {"title": "The Thing", "sort_title": "Thing, The"},
            {"title": "Alien", "sort_title": "Alien"},
            {"title": "Zulu", "sort_title": "Zulu"},
            {"title": "A Bridge Too Far", "sort_title": "Bridge Too Far, A"},
        ]
        self.assertEqual(
            self.sorted_titles(entries, mode="title_asc"),
            ["Alien", "A Bridge Too Far", "The Thing", "Zulu"],
        )

    def test_the_reverse_title_sort_is_the_exact_reverse(self):
        entries = [
            {"title": "Alien", "sort_title": "Alien"},
            {"title": "The Thing", "sort_title": "Thing, The"},
            {"title": "Zulu", "sort_title": "Zulu"},
        ]
        ascending = self.sorted_titles(entries, mode="title_asc")
        self.assertEqual(self.sorted_titles(entries, mode="title_desc"), list(reversed(ascending)))

    def test_a_missing_sort_title_falls_back_to_the_title(self):
        entries = [{"title": "Solaris"}, {"title": "Andrei Rublev"}]
        self.assertEqual(self.sorted_titles(entries, mode="title_asc"), ["Andrei Rublev", "Solaris"])

    def test_case_and_accents_do_not_split_the_alphabet(self):
        entries = [
            {"title": "amelie", "sort_title": "amelie"},
            {"title": "Amélie", "sort_title": "Amélie"},
            {"title": "Barry Lyndon", "sort_title": "Barry Lyndon"},
        ]
        self.assertEqual(self.sorted_titles(entries, mode="title_asc")[-1], "Barry Lyndon")

    def test_the_watchlist_sorts_on_when_it_was_added(self):
        entries = [
            {"title": "Middle", "watchlist_added_at": "2026-02-01T00:00:00Z"},
            {"title": "Newest", "watchlist_added_at": "2026-03-01T00:00:00Z"},
            {"title": "Oldest", "watchlist_added_at": "2026-01-01T00:00:00Z"},
        ]
        self.assertEqual(
            self.sorted_titles(entries, mode="date_desc", kind="watchlist"),
            ["Newest", "Middle", "Oldest"],
        )
        self.assertEqual(
            self.sorted_titles(entries, mode="date_asc", kind="watchlist"),
            ["Oldest", "Middle", "Newest"],
        )

    def test_the_watched_list_sorts_on_when_it_was_watched(self):
        """Same modes, a different field. Reading `watchlist_added_at` here
        would sort every watched entry as if it had no date at all."""
        entries = [
            {"title": "Seen first", "watched_at": "2026-01-01T00:00:00Z", "watchlist_added_at": "2026-12-01T00:00:00Z"},
            {"title": "Seen last", "watched_at": "2026-06-01T00:00:00Z", "watchlist_added_at": "2026-01-01T00:00:00Z"},
        ]
        self.assertEqual(
            self.sorted_titles(entries, mode="date_desc", kind="watched"),
            ["Seen last", "Seen first"],
        )

    def test_year_sorting_is_numeric_rather_than_lexical(self):
        """The failure a string sort produces here is specific and looks
        plausible: 1999 after 2001 but before 998."""
        entries = [
            {"title": "b", "year": 1999, "sort_title": "b"},
            {"title": "c", "year": 2001, "sort_title": "c"},
            {"title": "a", "year": 998, "sort_title": "a"},
        ]
        self.assertEqual(self.sorted_titles(entries, mode="year_asc"), ["a", "b", "c"])
        self.assertEqual(self.sorted_titles(entries, mode="year_desc"), ["c", "b", "a"])

    def test_films_sharing_a_year_fall_back_to_title_order(self):
        entries = [
            {"title": "Zodiac", "year": 2007, "sort_title": "Zodiac"},
            {"title": "Atonement", "year": 2007, "sort_title": "Atonement"},
        ]
        self.assertEqual(self.sorted_titles(entries, mode="year_desc"), ["Atonement", "Zodiac"])

    def test_entries_added_in_the_same_second_fall_back_to_title_order(self):
        """A bulk import gives every row the same timestamp. Without the
        tie-break the order would be whatever the database happened to return,
        and could differ between two renders of the same list."""
        stamp = "2026-05-05T12:00:00Z"
        entries = [
            {"title": "Wings", "watchlist_added_at": stamp, "sort_title": "Wings"},
            {"title": "Ashes", "watchlist_added_at": stamp, "sort_title": "Ashes"},
            {"title": "Mud", "watchlist_added_at": stamp, "sort_title": "Mud"},
        ]
        self.assertEqual(self.sorted_titles(entries, mode="date_desc"), ["Ashes", "Mud", "Wings"])

    def test_entries_without_a_date_sort_last_under_newest_first(self):
        entries = [
            {"title": "Dated", "watchlist_added_at": "2026-01-01T00:00:00Z"},
            {"title": "Undated"},
        ]
        self.assertEqual(self.sorted_titles(entries, mode="date_desc"), ["Dated", "Undated"])

    def test_an_unparseable_date_does_not_reorder_the_rest(self):
        entries = [
            {"title": "Good", "watchlist_added_at": "2026-01-01T00:00:00Z"},
            {"title": "Broken", "watchlist_added_at": "not a date"},
            {"title": "Better", "watchlist_added_at": "2026-06-01T00:00:00Z"},
        ]
        self.assertEqual(self.sorted_titles(entries, mode="date_desc"), ["Better", "Good", "Broken"])

    def test_an_unknown_mode_falls_back_to_the_default_order(self):
        entries = [
            {"title": "Older", "watchlist_added_at": "2026-01-01T00:00:00Z"},
            {"title": "Newer", "watchlist_added_at": "2026-09-01T00:00:00Z"},
        ]
        self.assertEqual(self.sorted_titles(entries, mode="nonsense-from-storage"), ["Newer", "Older"])

    def test_an_empty_list_is_handled(self):
        self.assertEqual(self.sorted_titles([], mode="title_asc"), [])

    def test_the_sort_is_a_copy_and_every_entry_survives_it(self):
        entries = [{"title": f"Film {index}", "sort_title": f"Film {index:02d}"} for index in range(25)]
        self.assertEqual(len(self.sorted_titles(entries, mode="title_asc")), 25)
        self.assertEqual(
            sorted(self.sorted_titles(entries, mode="year_desc")),
            sorted(entry["title"] for entry in entries),
            "sorting must not drop or duplicate an entry",
        )
