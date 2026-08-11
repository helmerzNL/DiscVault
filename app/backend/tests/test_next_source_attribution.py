"""Per-source attribution: whose words, and who decides.

Attribution is a licence obligation rather than a design flourish. TMDB, TVDB
and Fanart each require a specific credit in specific words, so the only correct
place for those words is the source's own manifest -- and the only correct
treatment is to render them as given.

These tests exist to keep that from eroding. The mechanism is easy to "improve"
into something that composes a nicer sentence, or translates a required one, and
either change quietly breaks a licence while every screen still looks fine.
"""

import json
import os
import sys
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import next_plugin_runtime  # noqa: E402

# `next_app` first on purpose: it and `next_views_ui` import each other, and only
# the `next_app`-first order resolves. Importing the view module directly lands
# halfway through the cycle.
import next_app  # noqa: E402,F401
from next_views_ui import ui_preview_attribution_cards  # noqa: E402


TMDB_MANIFEST = os.path.join(BACKEND_DIR, "next_plugins", "tmdb", "manifest.json")
NEXT_APP_PATH = os.path.join(BACKEND_DIR, "next_app.py")


class PluginAttributionManifestTests(unittest.TestCase):
    def test_a_manifest_without_attribution_asks_for_no_card(self):
        """Not every source requires a credit, and "none" is a real answer.

        Inventing a card to fill the gap would put words in a source's mouth,
        which is the exact failure this whole mechanism exists to avoid.
        """
        for manifest in ({}, {"attribution": None}, {"attribution": "TMDB"}, {"attribution": {}}):
            with self.subTest(manifest=manifest):
                self.assertIsNone(next_plugin_runtime.plugin_attribution(manifest))

    def test_a_statement_is_required_but_a_disclaimer_is_not(self):
        result = next_plugin_runtime.plugin_attribution(
            {"attribution": {"statement": "Data provided by Example"}}
        )
        self.assertEqual(result["statement"], "Data provided by Example")
        self.assertEqual(result["disclaimer"], "")

    def test_a_key_alone_is_enough_to_earn_a_card(self):
        result = next_plugin_runtime.plugin_attribution(
            {"attribution": {"statementKey": "profile.tmdbDataProvidedBy"}}
        )
        self.assertEqual(result["statementKey"], "profile.tmdbDataProvidedBy")

    def test_a_logo_path_is_refused_rather_than_trimmed(self):
        """Silently reading `logo.svg` when the manifest asked for
        `../../secret.svg` hides the mistake instead of surfacing it."""
        for value in ("../../etc/passwd", "nested/logo.svg", "/etc/passwd"):
            with self.subTest(value=value):
                result = next_plugin_runtime.plugin_attribution(
                    {"attribution": {"statement": "x", "logo": value}}
                )
                self.assertEqual(result["logo"], "")

    def test_only_https_links_survive(self):
        for value in ("http://example.com", "javascript:alert(1)", "example.com"):
            with self.subTest(value=value):
                result = next_plugin_runtime.plugin_attribution(
                    {"attribution": {"statement": "x", "url": value}}
                )
                self.assertEqual(result["url"], "")
        result = next_plugin_runtime.plugin_attribution(
            {"attribution": {"statement": "x", "url": "https://example.com"}}
        )
        self.assertEqual(result["url"], "https://example.com")


class AttributionCardRenderingTests(unittest.TestCase):
    ICON = "<span></span>"

    def test_literal_wording_is_rendered_untranslated(self):
        """The safe default for a new source.

        A machine translation of a required sentence is a different sentence,
        and nobody here can tell whether the licence still accepts it. So a card
        without a reviewed i18n key carries no `data-next-i18n` at all.
        """
        html = ui_preview_attribution_cards(
            [
                {
                    "name": "Example",
                    "statement": "Metadata provided by Example",
                    "disclaimer": "Example does not endorse DiscVault.",
                }
            ],
            self.ICON,
        )
        self.assertIn("Metadata provided by Example", html)
        self.assertIn("Example does not endorse DiscVault.", html)
        self.assertNotIn("data-next-i18n", html)

    def test_a_reviewed_key_is_translated(self):
        html = ui_preview_attribution_cards(
            [
                {
                    "name": "TMDb",
                    "statementKey": "profile.tmdbDataProvidedBy",
                    "statement": "Data provided by TMDB",
                    "disclaimerKey": "profile.tmdbDisclaimer",
                    "disclaimer": "DiscVault is not endorsed or certified by TMDB.",
                }
            ],
            self.ICON,
        )
        self.assertIn('data-next-i18n="profile.tmdbDataProvidedBy"', html)
        self.assertIn('data-next-i18n="profile.tmdbDisclaimer"', html)

    def test_the_tmdb_card_still_says_exactly_what_it_said_before(self):
        """This card was hardcoded before it was data-driven. The refactor is
        only safe if the rendered credit is unchanged."""
        with open(TMDB_MANIFEST, encoding="utf-8") as handle:
            manifest = json.load(handle)
        attribution = next_plugin_runtime.plugin_attribution(manifest)
        html = ui_preview_attribution_cards(
            [
                {
                    "name": "TMDb",
                    "logoUrl": "/api/next/plugins/tmdb/attribution-logo",
                    **attribution,
                }
            ],
            self.ICON,
        )
        self.assertIn(
            '<span data-next-i18n="profile.tmdbDataProvidedBy">Data provided by TMDB</span>',
            html,
        )
        self.assertIn(
            '<p class="profile-about-legal" data-next-i18n="profile.tmdbDisclaimer">'
            "DiscVault is not endorsed or certified by TMDB.</p>",
            html,
        )
        self.assertIn('class="profile-about-source-logo"', html)

    def test_a_remote_logo_is_dropped(self):
        """A credit is not allowed to cost a page view to the source on every
        visit to the profile page."""
        for value in ("https://image.tmdb.org/logo.svg", "//evil.example/logo.svg", "data:,x"):
            with self.subTest(value=value):
                html = ui_preview_attribution_cards(
                    [{"name": "X", "statement": "x", "logoUrl": value}], self.ICON
                )
                self.assertNotIn("<img", html)

    def test_wording_is_escaped_rather_than_trusted(self):
        """A manifest is a file on disk, and a third-party plugin ships its own."""
        html = ui_preview_attribution_cards(
            [{"name": "X", "statement": "<script>alert(1)</script>"}], self.ICON
        )
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_nothing_to_credit_renders_nothing(self):
        self.assertEqual(ui_preview_attribution_cards([], self.ICON), "")
        self.assertEqual(ui_preview_attribution_cards(None, self.ICON), "")
        self.assertEqual(ui_preview_attribution_cards([{"name": "X"}], self.ICON), "")


class AttributionWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(NEXT_APP_PATH, encoding="utf-8") as handle:
            cls.app_source = handle.read()

    def test_only_enabled_sources_are_credited(self):
        """Attribution follows the source, not the feature: a plugin that is
        installed but switched off is not being used."""
        start = self.app_source.index("def collection_attribution_entities(")
        body = self.app_source[start : self.app_source.index("\ndef ", start + 1)]
        self.assertIn("WHERE installed = true AND enabled = true", body)
        self.assertIn("ORDER BY order_index", body)

    def test_the_logo_route_cannot_escape_the_plugin_directory(self):
        start = self.app_source.index("def plugin_attribution_logo(")
        body = self.app_source[start : self.app_source.index("\n    @flask_app", start + 1)]
        self.assertIn("installed = true AND enabled = true", body)
        # `.resolve()` before the containment check is what makes a symlink
        # inside the plugin directory unable to point outside it.
        self.assertIn("plugin_root = Path(source_path).resolve()", body)
        self.assertIn("path.is_relative_to(plugin_root)", body)

    def test_the_snapshot_declares_attributions_both_full_and_empty(self):
        self.assertIn('"attributions": collection_attribution_entities(conn)', self.app_source)
        empty = self.app_source[self.app_source.index("def empty_collection_dashboard_snapshot") :]
        self.assertIn('"attributions": []', empty[:1400])


if __name__ == "__main__":
    unittest.main()
