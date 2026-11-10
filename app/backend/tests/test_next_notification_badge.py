"""The unread-notification badge on the way to the inbox.

The app always had the unread count: every snapshot carries it. What it did
not have was anywhere to show it. `updateNotificationCounts()` wrote the
number into an element with the id `navNotificationCount`, and no such
element existed, so an unread notification stayed invisible until somebody
opened the notifications page for another reason. That is how the one-time
notice about the installation count went unseen in 26.11.2.

So these tests pin the three things that failure was made of: that the badge
exists on every entry that leads to the inbox, that the one function every
count already goes through is what fills it, and that it is silent at zero.
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend import next_views_ui
except ModuleNotFoundError as exc:
    if exc.name not in {"flask", "psycopg"}:
        raise
    next_views_ui = None

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "backend/next_views_ui.py"
I18N_DIR = ROOT / "frontend/i18n/next"
KEY = "notifications.unreadCount"
BADGE = "data-notification-unread-badge"


def _function_source(source: str, name: str) -> str:
    """The whole of ``function name(...) { ... }``.

    The parameter list is stepped over before the braces are matched:
    `updateNotificationCounts(counts = notificationsState.counts || {})` has
    a pair of its own, and matching from the first brace returns the default
    value instead of the function.
    """
    start = source.index("function %s(" % name)
    index = source.index("(", start)
    depth = 0
    for index in range(index, len(source)):
        depth += {"(": 1, ")": -1}.get(source[index], 0)
        if depth == 0:
            break
    index = source.index("{", index)
    depth = 0
    for position in range(index, len(source)):
        depth += {"{": 1, "}": -1}.get(source[position], 0)
        if depth == 0:
            return source[start : position + 1]
    raise AssertionError("unbalanced braces reading %s" % name)


def _button(html: str, marker: str) -> str:
    """The markup of the one `<button>` that carries `marker`."""
    at = html.index(marker)
    start = html.rindex("<button", 0, at)
    return html[start : html.index("</button>", start)]


@unittest.skipIf(next_views_ui is None, "Flask is not installed in this test environment")
class BadgeMarkupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = next_views_ui.ui_preview_html(app_mode=True)
        cls.sidebar = _button(cls.html, 'class="nav-item" data-app-route="profile"')
        cls.tab = _button(cls.html, 'class="mobile-tab" data-app-route="profile"')
        cls.profile = _button(cls.html, 'id="profileOpenNotificationsButton"')

    def test_every_way_to_the_inbox_carries_a_badge(self):
        for name, button in (("sidebar", self.sidebar), ("tab bar", self.tab), ("profile page", self.profile)):
            self.assertEqual(button.count(BADGE), 1, name)
        # And nowhere else: three places to fill, all found by one selector.
        self.assertEqual(self.html.count(BADGE + " "), 3)

    def test_the_page_is_served_with_the_badge_hidden_and_empty(self):
        """Before the first snapshot there is no count. A badge that shipped
        visible would show an empty pill until the script got to it."""
        for button in (self.sidebar, self.tab, self.profile):
            start = button.index("<span", button.rindex("<", 0, button.index(BADGE)))
            badge = button[start : button.index("</span>", start) + len("</span>")]
            self.assertIn('class="unread-badge', badge)
            self.assertIn(" hidden", badge.split(">")[0])
            self.assertIn('role="img"', badge)
            self.assertNotIn("aria-label", badge)
            self.assertTrue(badge.endswith("></span>"), badge)

    def test_the_sidebar_badge_is_beside_the_label_and_not_inside_it(self):
        """`.nav-item-label span:last-child` styles the label by position and
        hides it when the sidebar collapses. A badge inside the label would be
        the last child: it would take the label's styles in the open sidebar
        and vanish in the collapsed one, which is the default."""
        label_start = self.sidebar.index('class="nav-item-label"')
        badge_at = self.sidebar.index(BADGE)
        inside = self.sidebar[self.sidebar.rindex("<span", 0, label_start) : self.sidebar.rindex("<span", 0, badge_at)]
        self.assertEqual(inside.count("<span"), inside.count("</span>"), "the badge is nested in the label")
        self.assertLess(badge_at, self.sidebar.index('id="navProfileRole"'))

    def test_the_tab_label_is_still_the_last_child_of_its_tab(self):
        """`.mobile-tab span:last-child` is what makes the label a label. A
        badge appended after it would take that styling and the label would
        lose it, on every phone."""
        self.assertLess(self.tab.index(BADGE), self.tab.index('data-next-i18n="uiPreview.profile"'))
        self.assertTrue(self.tab.rstrip().endswith("</span>"))
        self.assertIn('data-next-i18n="uiPreview.profile"', self.tab[self.tab.rindex("<span") :])

    def test_nothing_still_looks_for_the_element_that_never_existed(self):
        self.assertNotIn("navNotificationCount", self.html)


class BadgeStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")

    def _block_around(self, rule: str) -> tuple[str, str]:
        """The `@media` header a rule sits under, and that block's text."""
        at = self.source.index(rule)
        start = self.source.rindex("@media", 0, at)
        header = self.source[start : self.source.index("{", start)].strip()
        end = self.source.index("@media", at)
        return header, self.source[start:end]

    def test_the_phone_rule_lives_in_the_main_phone_block(self):
        """Several tests slice this file from the first `@media (max-width:
        760px)` and expect the shell's phone rules in the block they get. The
        tab bar's rules are in that block; the badge's go beside them, and
        never into a new block ahead of it."""
        rule = ".mobile-tab .nav-unread-badge {"
        self.assertEqual(self.source.count(rule), 1)
        header, block = self._block_around(rule)
        self.assertEqual(header, "@media (max-width: 760px)")
        self.assertIn(".mobile-tabbar {", block)
        self.assertEqual(self.source.count("@media (max-width: 760px)"), 3)

    def test_the_collapsed_sidebar_moves_the_badge_onto_the_icon(self):
        """Collapsed is the default on a desktop, and there the row the badge
        stands in is gone: label and role are both hidden."""
        rule = ".preview-shell.sidebar-collapsed .nav-item .nav-unread-badge {"
        start = self.source.index(rule)
        body = self.source[start : self.source.index("}", start)]
        self.assertIn("position: absolute;", body)
        self.assertNotIn("display: none", body)
        # The hiding rule for a collapsed sidebar names the role and the
        # label, and must go on not naming the badge.
        hide_start = self.source.index(".preview-shell.sidebar-collapsed .brand > div:last-child,")
        hidden = self.source[hide_start : self.source.index("{", hide_start)]
        self.assertNotIn("unread-badge", hidden)

    def test_zero_is_hidden_by_the_shared_rule(self):
        self.assertIn(".hidden {\n      display: none !important;", self.source)


@unittest.skipUnless(shutil.which("node"), "node is unavailable")
class BadgeBehaviourTests(unittest.TestCase):
    """`updateNotificationCounts` as shipped, run against a stand-in page."""

    def run_js(self, body: str, locale: str = "en-US") -> None:
        source = SOURCE.read_text(encoding="utf-8")
        script = _function_source(source, "updateNotificationCounts") + r"""
const assert = require('node:assert/strict');
const messages = JSON.parse(require('node:fs').readFileSync(process.argv[1], 'utf8'));
const tNext = (key, fallback) => messages[key] || fallback || key;
const makeBadge = () => {
  const node = {classes: new Set(['unread-badge', 'hidden']), textContent: '', attributes: {}};
  node.classList = {toggle: (name, on) => { if (on) node.classes.add(name); else node.classes.delete(name); }};
  node.setAttribute = (name, value) => { node.attributes[name] = String(value); };
  node.removeAttribute = (name) => { delete node.attributes[name]; };
  node.shown = () => !node.classes.has('hidden');
  return node;
};
const badges = [makeBadge(), makeBadge(), makeBadge()];
const tiles = {notificationsUnreadCount: {textContent: ''}, notificationsTotalCount: {textContent: ''}};
const document = {
  getElementById: (id) => tiles[id] || null,
  querySelectorAll: (selector) => {
    assert.equal(selector, '[data-notification-unread-badge]');
    return badges;
  },
};
let notificationsState = {loaded: false, items: [], counts: {total: 0, unread: 0}};
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", script + body, str(I18N_DIR / (locale + ".json"))],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_unread_notification_shows_on_every_badge(self):
        self.run_js(r"""
updateNotificationCounts({total: 5, unread: 3});
for (const badge of badges) {
  assert.equal(badge.shown(), true);
  assert.equal(badge.textContent, '3');
  assert.equal(badge.attributes['aria-label'], 'Unread notifications: 3');
}
assert.equal(tiles.notificationsUnreadCount.textContent, '3');
assert.equal(tiles.notificationsTotalCount.textContent, '5');
""")

    def test_zero_hides_the_badge_and_leaves_nothing_to_announce(self):
        """Hidden is not enough on its own terms: a `0` left in a hidden node
        is one stylesheet mistake away from being read out."""
        self.run_js(r"""
updateNotificationCounts({total: 5, unread: 3});
updateNotificationCounts({total: 5, unread: 0});
for (const badge of badges) {
  assert.equal(badge.shown(), false);
  assert.equal(badge.textContent, '');
  assert.equal('aria-label' in badge.attributes, false);
}
assert.equal(tiles.notificationsUnreadCount.textContent, '0');
""")

    def test_a_count_that_is_not_a_count_is_zero(self):
        self.run_js(r"""
for (const unread of [undefined, null, '', 'many', NaN, -2, 0.4]) {
  updateNotificationCounts({total: 5, unread: 4});
  updateNotificationCounts({total: 5, unread});
  assert.equal(badges[0].shown(), false, String(unread));
  assert.equal(tiles.notificationsUnreadCount.textContent, '0', String(unread));
}
updateNotificationCounts({});
assert.equal(badges[0].shown(), false);
updateNotificationCounts({total: 9, unread: '7'});
assert.equal(badges[0].textContent, '7');
""")

    def test_the_badge_stops_at_99_but_its_name_does_not(self):
        self.run_js(r"""
updateNotificationCounts({total: 99, unread: 99});
assert.equal(badges[0].textContent, '99');
updateNotificationCounts({total: 140, unread: 128});
assert.equal(badges[0].textContent, '99+');
assert.equal(badges[0].attributes['aria-label'], 'Unread notifications: 128');
assert.equal(tiles.notificationsUnreadCount.textContent, '128');
""")

    def test_a_redraw_without_a_count_repeats_the_last_one(self):
        """The snapshot hands its count straight to this function and nothing
        else kept it. Opening the inbox redraws before its own request
        returns, and a language change redraws with no count at all; both
        used to fall back to whatever the inbox last held, which before its
        first load is zero -- so the badge went out while the inbox loaded."""
        self.run_js(r"""
updateNotificationCounts({total: 5, unread: 3});          // what loadAppSnapshot does
assert.deepEqual(notificationsState.counts, {total: 5, unread: 3});
updateNotificationCounts(notificationsState.counts);      // what renderNotificationsView does
assert.equal(badges[0].textContent, '3');
updateNotificationCounts();                               // what applyTranslations does
assert.equal(badges[0].shown(), true);
assert.equal(badges[0].textContent, '3');
assert.equal(tiles.notificationsTotalCount.textContent, '5');
""")

    def test_the_name_comes_from_the_catalogue_of_the_language_in_use(self):
        self.run_js(
            r"""
updateNotificationCounts({total: 2, unread: 2});
assert.equal(badges[0].attributes['aria-label'], 'Ongelezen notificaties: 2');
assert.equal(badges[0].textContent, '2');
""",
            locale="nl-NL",
        )

    def test_a_language_change_renames_the_badge(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("updateNotificationCounts();", _function_source(source, "applyTranslations"))

    def test_every_source_of_a_count_still_goes_through_the_one_function(self):
        """The badge is fed by the function, not by its callers, so a count
        that reaches the function reaches the badge. These are the callers
        that have a count to give."""
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn(
            "updateNotificationCounts((state.counts || {}).notifications || {});",
            _function_source(source, "loadAppSnapshot"),
        )
        self.assertIn("updateNotificationCounts(notificationsState.counts);", _function_source(source, "renderNotificationsView"))
        for name in ("openNotification", "markAllNotificationsRead", "loadNotifications"):
            self.assertIn("renderNotificationsView();", _function_source(source, name), name)
        self.assertEqual(source.count("querySelectorAll(\"[data-notification-unread-badge]\")"), 1)


class BadgeTranslationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogs = {}
        for path in sorted(glob.glob(str(I18N_DIR / "*.json"))):
            with open(path, encoding="utf-8") as handle:
                cls.catalogs[os.path.basename(path)[: -len(".json")]] = json.load(handle)

    def test_every_locale_names_the_badge_and_keeps_the_number(self):
        self.assertEqual(len(self.catalogs), 29)
        for locale, catalog in self.catalogs.items():
            self.assertIn(KEY, catalog, locale)
            self.assertEqual(catalog[KEY].count("{count}"), 1, locale)
            # More than the number: the point of the name is the words.
            self.assertGreater(len(catalog[KEY].replace("{count}", "").strip(" :：")), 3, locale)

    def test_no_locale_echoes_the_english(self):
        english = self.catalogs["en-US"][KEY]
        for locale, catalog in self.catalogs.items():
            if locale != "en-US":
                self.assertNotEqual(catalog[KEY], english, locale)

    def test_the_wording_holds_for_one_and_for_many(self):
        """There is no plural handling in `tNext`, and the catalogue's locales
        need up to four plural forms between them. A label followed by the
        number is right for every count in all of them; `{count} unread
        notifications` is wrong for 1 in English alone."""
        self.assertEqual(self.catalogs["en-US"][KEY], "Unread notifications: {count}")
        for locale, catalog in self.catalogs.items():
            self.assertFalse(catalog[KEY].startswith("{count}"), locale)

    def test_the_inline_fallback_matches_the_source_catalog(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('tNext("%s", "%s")' % (KEY, self.catalogs["en-US"][KEY]), source)


if __name__ == "__main__":
    unittest.main()
