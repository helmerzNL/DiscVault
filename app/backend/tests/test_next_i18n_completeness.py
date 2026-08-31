import collections
import glob
import json
import os
import unicodedata
import unittest


I18N_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "frontend",
        "i18n",
        "next",
    )
)
NEXT_VIEWS_UI_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "next_views_ui.py",
    )
)
SOURCE_LOCALE = "en-US.json"


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# Unicode blocks that distinguish one locale's writing system from another.
# Anything outside them - punctuation, digits, symbols - is ignored.
_SCRIPT_RANGES = (
    ("Cyrillic", 0x0400, 0x04FF),
    ("Greek", 0x0370, 0x03FF),
    ("Kana", 0x3040, 0x30FF),
    ("Hangul", 0xAC00, 0xD7AF),
    ("Han", 0x4E00, 0x9FFF),
    ("Han", 0x3400, 0x4DBF),
    ("Latin", 0x0041, 0x024F),
    ("Latin", 0x1E00, 0x1EFF),
)
_CJK = frozenset(("Han", "Kana", "Hangul"))


def _script_of(char):
    code = ord(char)
    for name, low, high in _SCRIPT_RANGES:
        if low <= code <= high:
            return name
    return "Other"


def _scripts_in(value):
    return {
        _script_of(c)
        for c in value
        if unicodedata.category(c).startswith("L")
    }


def _writing_systems_of(catalog):
    """The scripts a catalog actually writes in, inferred from the catalog.

    Read from the file itself rather than a hardcoded table, so adding a
    locale needs no registration here. Japanese and Korean mix Han with
    Kana/Hangul, so a CJK-dominant catalog keeps its top two scripts.
    """
    counts = collections.Counter()
    for value in catalog.values():
        counts.update(_scripts_in(value))
    if not counts:
        return {"Latin"}
    ranked = [name for name, _ in counts.most_common(2)]
    own = {ranked[0]}
    if ranked[0] in _CJK:
        own.update(name for name in ranked if name in _CJK)
        own.add("Han")
    return own


class NextI18nCompletenessTests(unittest.TestCase):
    """Guards against locale catalogs drifting out of sync with en-US.

    en-US is the source of truth. Every other locale JSON under
    app/frontend/i18n/next/ must carry exactly the same key set — no
    missing keys (which silently fall back to English) and no stale
    extras (keys removed from the source).
    """

    @classmethod
    def setUpClass(cls):
        cls.source_path = os.path.join(I18N_DIR, SOURCE_LOCALE)
        cls.source = _load(cls.source_path)
        cls.source_keys = set(cls.source.keys())
        cls.locale_files = sorted(
            p
            for p in glob.glob(os.path.join(I18N_DIR, "*.json"))
            if os.path.basename(p) != SOURCE_LOCALE
        )

    def test_source_catalog_present(self):
        self.assertTrue(os.path.isfile(self.source_path))
        self.assertGreater(len(self.source_keys), 0)

    def test_source_catalog_includes_sidebar_toggle_key(self):
        self.assertIn("uiPreview.toggleSidebar", self.source)

    def test_source_catalog_includes_tmdb_configuration_guidance(self):
        self.assertIn("importCenter.tmdbKeyRequiredTitle", self.source)
        self.assertIn("importCenter.tmdbKeyRequiredHelp", self.source)
        self.assertIn("importCenter.requestTmdbKey", self.source)
        self.assertIn("importCenter.configureTmdbKey", self.source)

    def test_source_catalog_includes_movie_detail_section_tabs(self):
        self.assertIn("movieDetail.release", self.source)
        self.assertIn("movieDetail.technical", self.source)
        self.assertIn("movieDetail.collectors", self.source)
        self.assertIn("movieDetail.castCrew", self.source)

    def test_source_catalog_includes_container_conversion_messages(self):
        for key in (
            "containerDetail.convertBarcodeConflict",
            "containerDetail.convertConfirm",
            "containerDetail.converted",
            "containerDetail.converting",
            "containerDetail.convertPermissionDenied",
        ):
            self.assertIn(key, self.source)

    def test_source_catalog_includes_rewatch_action_sheet(self):
        self.assertIn("lists.logRewatch", self.source)
        self.assertIn("lists.watchedToday", self.source)
        self.assertIn("lists.watchedYesterday", self.source)

    def test_source_catalog_includes_public_shop_url_denial(self):
        self.assertIn("lists.wishlistPriceUrlNotPublic", self.source)

    def test_source_catalog_includes_responsive_media_actions(self):
        for key in (
            "common.more",
            "common.share",
            "common.hide",
            "common.unhide",
            "movieDetail.artworkHidden",
            "movieDetail.artworkUnhidden",
            "movieDetail.hiddenArtwork",
            "movieDetail.hideAgain",
            "movieDetail.hidingArtwork",
            "movieDetail.showHidden",
            "movieDetail.unhidingArtwork",
        ):
            self.assertIn(key, self.source)

    def test_source_catalog_includes_library_list_labels(self):
        for key in (
            "collection.behaviorColumn",
            "collection.hideWatched",
            "collection.hideWatchlist",
            "collection.posterColumn",
            "collection.studioColumn",
        ):
            self.assertIn(key, self.source)

    def test_the_detail_page_saves_a_title_not_a_movie(self):
        """The page holds films and series discs alike -- a disc of a show is a
        `movies` row with `media_type='SHOW'` and is edited here -- so saving one
        said "Movie saved." over a series. The noun has to be the neutral one the
        page already uses for the record itself."""
        source = _load(os.path.join(I18N_DIR, SOURCE_LOCALE))
        self.assertEqual(source["movieDetail.saved"], "Title saved.")
        self.assertEqual(source["movieDetail.saving"], "Saving title...")
        for path in sorted(glob.glob(os.path.join(I18N_DIR, "*.json"))):
            locale = os.path.basename(path)
            if locale == SOURCE_LOCALE:
                continue
            catalog = _load(path)
            for key in ("movieDetail.saved", "movieDetail.saving"):
                # Every locale must carry its own wording. An untranslated locale
                # would pass the completeness check above by echoing English,
                # which is exactly how a half-finished sweep survives review.
                self.assertNotEqual(
                    catalog[key],
                    source[key],
                    f"{locale} still shows the English {key}",
                )

    def test_the_inline_fallback_matches_the_source_catalog(self):
        """`tNext(key, fallback)` renders the fallback whenever the catalog has
        not loaded. Leaving it behind when the wording changes puts the old text
        on a slow connection only -- the one condition nobody checks."""
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            ui_source = handle.read()
        catalog = _load(os.path.join(I18N_DIR, SOURCE_LOCALE))
        for key in ("movieDetail.saved", "movieDetail.saving"):
            self.assertIn(
                f'tNext("{key}", "{catalog[key]}")',
                ui_source,
                f"the inline fallback for {key} disagrees with en-US.json",
            )

    def test_locale_files_discovered(self):
        self.assertGreater(len(self.locale_files), 0)

    def test_every_locale_has_full_source_key_set(self):
        problems = []
        for path in self.locale_files:
            locale = os.path.basename(path)
            data = _load(path)
            keys = set(data.keys())
            missing = self.source_keys - keys
            extra = keys - self.source_keys
            if missing or extra:
                problems.append(
                    "{locale}: missing={missing} extra={extra}".format(
                        locale=locale,
                        missing=sorted(missing)[:10],
                        extra=sorted(extra)[:10],
                    )
                )
        self.assertEqual(problems, [], "Locale catalogs out of sync:\n" + "\n".join(problems))

    def test_all_values_are_non_empty_strings(self):
        problems = []
        for path in self.locale_files:
            locale = os.path.basename(path)
            data = _load(path)
            for key, value in data.items():
                if not isinstance(value, str) or value.strip() == "":
                    problems.append("{0}:{1}".format(locale, key))
        self.assertEqual(problems, [], "Empty/non-string translations:\n" + "\n".join(problems[:20]))

    def test_no_locale_carries_another_locales_writing_system(self):
        """A translation must never be written in a third script.

        The two presence tests above cannot see a translation filed under the
        wrong locale: the key is there and the string is non-empty, so a whole
        block written into the wrong files passes clean. That is what happened
        to the custom-field strings - a list of translations in authoring
        order was zipped against the alphabetically sorted list of locale
        files, and all 29 shifted.

        Latin is always allowed. Around a thousand keys per locale are still
        untranslated and fall back to English, and brand names stay Latin
        everywhere; flagging those would bury the signal. But no locale falls
        back to Greek, Cyrillic, kana or hangul, so a script that is neither
        the catalog's own nor Latin can only be another locale's text sitting
        in the wrong file.

        This cannot catch one Latin-script language filed as another - French
        landing in de-DE. It does not need to: a mix-up of this kind is never
        confined to one file, and on the failure that prompted this test five
        catalogs tripped it.
        """
        problems = []
        for path in [self.source_path, *self.locale_files]:
            locale = os.path.basename(path)
            catalog = _load(path)
            allowed = _writing_systems_of(catalog) | {"Latin", "Other"}
            foreign = [
                "{0} -> {1!r}".format(key, value[:40])
                for key, value in catalog.items()
                if _scripts_in(value) - allowed
            ]
            if foreign:
                problems.append(
                    "{0}: {1} value(s) in a foreign script, e.g. {2}".format(
                        locale, len(foreign), "; ".join(foreign[:3])
                    )
                )
        self.assertEqual(
            problems,
            [],
            "Translations filed under the wrong locale:\n" + "\n".join(problems),
        )

    def test_auth_and_onboarding_copy_is_complete_and_method_neutral(self):
        problems = []
        for path in [self.source_path, *self.locale_files]:
            locale = os.path.basename(path)
            data = _load(path)
            if data.get("legacyAuth.signIn") != data.get("auth.signIn"):
                problems.append(f"{locale}: legacyAuth.signIn")
            if data.get("auth.signInWithUsernamePassword") != data.get("auth.signIn"):
                problems.append(f"{locale}: auth.signInWithUsernamePassword")
            if "{username}" not in data.get("startup.helloUser", ""):
                problems.append(f"{locale}: startup.helloUser")
            for key in (
                "legacyAuth.setupOwner",
                "legacyAuth.useRecoveryCode",
                "startup.description.owner_setup",
                "startup.phase.owner_setup",
            ):
                if not data.get(key, "").strip():
                    problems.append(f"{locale}: {key}")
        self.assertEqual(problems, [], "Auth/onboarding copy problems:\n" + "\n".join(problems))

    def test_ui_preview_uses_localized_sidebar_toggle_attributes(self):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('id="sidebarCollapseToggle"', source)
        self.assertIn('data-next-i18n-aria="uiPreview.toggleSidebar"', source)
        self.assertIn('data-next-i18n-title="uiPreview.toggleSidebar"', source)

    def test_container_editor_confirms_authorized_type_conversion(self):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("const canConvert = !!((detail.actions || {}).canConvert);", source)
        self.assertIn('"containerDetail.convertConfirm"', source)
        self.assertIn("containerType: requestedType", source)

    def test_plugin_config_uses_submit_feedback_and_typed_boolean_control(self):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('data-app-admin-plugin-config-form=', source)
        self.assertIn('data-value-type="boolean" type="checkbox"', source)
        self.assertIn('addEventListener("submit", (event) => {', source)
        self.assertIn('role="status" aria-live="polite"', source)

    def test_import_lookup_surfaces_non_blocking_tmdb_key_guidance(self):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("metadata?.enrichment?.tmdb", source)
        self.assertIn('data-import-configure-tmdb="1"', source)
        self.assertIn("https://www.themoviedb.org/settings/api", source)


if __name__ == "__main__":
    unittest.main()
