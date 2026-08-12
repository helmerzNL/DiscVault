import glob
import json
import os
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
