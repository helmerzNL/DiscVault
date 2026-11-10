"""The unread-notification badge on the way to the inbox, read as source text.

The app always knew how many notifications were unread and showed the number
only on the notifications page itself. `updateNotificationCounts()` wrote it to
an element with the id `navNotificationCount`, and no such element existed, so
the write was a no-op that nothing reported. A notification stayed invisible
until its reader happened to open the page it was waiting on.

What these tests hold in place:

**One function feeds every badge.** The snapshot, the inbox and the push
endpoints all hand their counts to `updateNotificationCounts()`. A badge fed
from anywhere else is a second number that can disagree with the first.

**Hidden at zero, and named in words.** A bare "3" on the Profile entry says
nothing to a screen reader, and not much to anyone else.

**The label stays the last child of the mobile tab.** `.mobile-tab
span:last-child` is how the tab's label gets its type size. A badge placed
after the label takes that rule from it, and the label silently renders at the
size of body text.

**The collapsed sidebar keeps its labels as names.** Collapsed is the default
on a desktop. With `display: none` on the label an entry had no accessible name
at all, so the Profile entry would have been announced by its badge alone.
"""

import glob
import json
import os
import sys
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEXT_VIEWS_UI_PATH = os.path.join(BACKEND_DIR, "next_views_ui.py")
I18N_DIR = os.path.abspath(os.path.join(BACKEND_DIR, "..", "frontend", "i18n", "next"))
BADGE_KEY = "notifications.unreadBadge"
BADGE = '<span class="nav-unread-badge hidden" data-notification-unread-badge role="img"></span>'

repo_root = os.path.abspath(os.path.join(BACKEND_DIR, "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend import next_views_ui
except ModuleNotFoundError as exc:  # Local minimal test environments may omit optional backend deps.
    if exc.name not in {"flask", "psycopg"}:
        raise
    next_views_ui = None


def _source() -> str:
    with open(NEXT_VIEWS_UI_PATH, "r", encoding="utf-8") as handle:
        return handle.read()


def _function_source(source: str, name: str) -> str:
    start = source.index("function %s(" % name)
    depth = 0
    # From the brace that opens the body: a default parameter may hold a `{}`
    # of its own, and matching from that one returns the signature alone.
    for position in range(source.index(") {\n", start) + 2, len(source)):
        char = source[position]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : position + 1]
    raise AssertionError("unbalanced braces reading %s" % name)


def _rule(css: str, selector: str) -> str:
    start = css.index(selector + " {")
    return css[start : css.index("}", start) + 1]


class UnreadBadgeScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()
        cls.update = _function_source(cls.source, "updateNotificationCounts")

    def test_the_count_is_written_to_elements_that_exist(self):
        self.assertNotIn("navNotificationCount", self.source)
        self.assertIn(
            'document.querySelectorAll("[data-notification-unread-badge]").forEach((node) => {',
            self.update,
        )

    def test_the_badge_is_hidden_at_zero(self):
        self.assertIn('node.classList.toggle("hidden", unread < 1);', self.update)

    def test_the_badge_is_named_in_words_and_keeps_the_exact_count(self):
        # The visible text is capped so it fits on an icon; the name is not.
        self.assertIn('node.textContent = unread > 99 ? "99+" : String(unread);', self.update)
        self.assertIn('.replace("{count}", String(unread));', self.update)
        self.assertIn('node.setAttribute("aria-label", badgeLabel);', self.update)

    def test_the_newest_count_is_the_one_a_re_render_repeats(self):
        # renderNotificationsView() and applyTranslations() call this with no
        # fresh count of their own. Unless the last one is kept, opening the
        # inbox puts the count it was loaded with -- zero, before the first
        # load -- back on the badge.
        self.assertIn("notificationsState.counts = counts;", self.update)
        self.assertIn("notificationsState.loaded = false;", self.update)

    def test_the_snapshot_count_reaches_the_badge(self):
        snapshot = _function_source(self.source, "loadAppSnapshot")
        self.assertIn("updateNotificationCounts((state.counts || {}).notifications || {});", snapshot)

    def test_a_language_change_renames_the_badge(self):
        self.assertIn("updateNotificationCounts();", _function_source(self.source, "applyTranslations"))


class UnreadBadgeStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_phone_rules_live_in_the_main_phone_block(self):
        # Other tests slice this file from the first `@media (max-width: 760px)`
        # to the first `@media (max-width: 560px)` after it. A new phone block
        # ahead of either moves what they read.
        main_block = self.source.index("@media (max-width: 760px) {\n      body {")
        end = self.source.index("@media (max-width: 560px)", main_block)
        rule = self.source.index("      .mobile-tab .nav-unread-badge {")
        self.assertTrue(main_block < rule < end)
        self.assertEqual(self.source.count(".mobile-tab .nav-unread-badge {"), 1)
        self.assertIn("position: relative;", _rule(self.source[main_block:end], "      .mobile-tab"))

    def test_the_collapsed_sidebar_keeps_labels_as_accessible_names(self):
        start = self.source.index("    .preview-shell.sidebar-collapsed .brand > div:last-child,")
        hidden_group = self.source[start : self.source.index("}", start)]
        self.assertIn("display: none;", hidden_group)
        self.assertNotIn(".nav-item-label", hidden_group)
        label = _rule(self.source, "    .preview-shell.sidebar-collapsed .nav-item-label span:last-child")
        self.assertIn("clip: rect(0, 0, 0, 0);", label)
        self.assertNotIn("display: none", label)

    def test_the_collapsed_sidebar_puts_the_badge_on_the_icon(self):
        badge = _rule(self.source, "    .preview-shell.sidebar-collapsed .nav-item .nav-unread-badge")
        self.assertIn("position: absolute;", badge)
        self.assertIn("position: relative;", _rule(self.source, "    .preview-shell.sidebar-collapsed .nav-item"))


@unittest.skipIf(next_views_ui is None, "Flask is not installed in this test environment")
class UnreadBadgeMarkupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = next_views_ui.ui_preview_html(app_mode=True)

    def test_three_badges_start_hidden_and_empty(self):
        self.assertEqual(self.html.count("data-notification-unread-badge role="), 3)
        self.assertEqual(self.html.count(BADGE), 3)

    def test_the_sidebar_profile_entry_carries_one_after_its_label(self):
        self.assertIn(
            '<span data-next-i18n="uiPreview.profile">Profile</span></span>'
            + BADGE
            + '<small id="navProfileRole">-</small></button>',
            self.html,
        )

    def test_the_mobile_profile_tab_carries_one_and_its_label_stays_last(self):
        start = self.html.index('<button type="button" class="mobile-tab" data-app-route="profile">')
        tab = self.html[start : self.html.index("</button>", start)]
        self.assertIn(BADGE, tab)
        self.assertTrue(tab.rstrip().endswith('<span data-next-i18n="uiPreview.profile">Profile</span>'))

    def test_the_notifications_button_on_the_profile_page_carries_one(self):
        start = self.html.index('id="profileOpenNotificationsButton"')
        button = self.html[start : self.html.index("</button>", start)]
        self.assertIn('data-app-route="notifications"', button)
        self.assertTrue(button.endswith(BADGE))


class UnreadBadgeTranslationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogs = {}
        for path in sorted(glob.glob(os.path.join(I18N_DIR, "*.json"))):
            with open(path, encoding="utf-8") as handle:
                cls.catalogs[os.path.basename(path)] = json.load(handle)

    def test_the_inline_fallback_matches_the_source_catalog(self):
        text = self.catalogs["en-US.json"][BADGE_KEY]
        self.assertIn(f'tNext("{BADGE_KEY}", "{text}")', _source())

    def test_every_locale_names_the_badge_in_its_own_words(self):
        source = self.catalogs["en-US.json"][BADGE_KEY]
        self.assertGreaterEqual(len(self.catalogs), 29)
        problems = []
        for locale, catalog in self.catalogs.items():
            value = catalog.get(BADGE_KEY, "")
            # One form for every number: the count stands after a colon rather
            # than in front of a noun, so no locale needs a plural rule.
            if value.count("{count}") != 1:
                problems.append(f"{locale}: the count placeholder is missing or repeated")
            if locale != "en-US.json" and value == source:
                problems.append(f"{locale}: still the English text")
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
