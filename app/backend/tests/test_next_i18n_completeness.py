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
NEXT_VIEWS_COLLECTION_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "next_views_collection.py",
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

    def test_startup_auth_copy_is_method_neutral(self):
        """Startup auth copy must not name passkeys as the only method.

        The startup gate accepts any owner authentication method (PR #354),
        so these three keys must stay method-neutral in every catalog and in
        the collection-view fallbacks, while genuine passkey-enrollment copy
        stays passkey-specific.
        """
        neutral_keys = (
            "startup.description.sign_in_required",
            "startup.step.auth",
            "startup.step.authDetail",
        )

        # English source carries the intended method-neutral wording.
        self.assertEqual(
            self.source["startup.description.sign_in_required"],
            "Sign in to continue setup.",
        )
        self.assertEqual(self.source["startup.step.auth"], "Owner account")
        self.assertEqual(
            self.source["startup.step.authDetail"],
            "Authentication protects this DiscVault Next environment.",
        )

        # No catalog may reintroduce passkey-specific wording for these keys.
        # Besides the English literal, use each locale's own translated
        # passkey noun (auth.passkey) as a marker so a passkey-only regression
        # cannot slip through in non-Latin scripts (Chinese, Slavic, etc.).
        base_markers = (
            "passkey",  # en + several Latin-script locales
            "passkeys",
        )
        problems = []
        for path in [self.source_path, *self.locale_files]:
            locale = os.path.basename(path)
            data = _load(path)
            markers = set(base_markers)
            localized_passkey = data.get("auth.passkey", "").strip().lower()
            if localized_passkey:
                markers.add(localized_passkey)
            for key in neutral_keys:
                value = data.get(key, "")
                if not value.strip():
                    problems.append(f"{locale}: {key} empty")
                    continue
                lowered = value.lower()
                if any(marker in lowered for marker in markers):
                    problems.append(f"{locale}: {key} -> {value}")
        self.assertEqual(
            problems,
            [],
            "Passkey-specific startup copy remains:\n" + "\n".join(problems),
        )

        # Collection-view fallbacks must be method-neutral, not passkey-only.
        with open(NEXT_VIEWS_COLLECTION_PATH, encoding="utf-8") as handle:
            collection = handle.read()
        self.assertIn(
            'tNext("startup.description.sign_in_required", "Sign in to continue setup.")',
            collection,
        )
        self.assertIn(
            'tNext("startup.description.owner_setup", "Follow the steps to create an '
            'owner account and complete setup.")',
            collection,
        )
        self.assertIn('tNext("startup.step.auth", "Owner account")', collection)
        self.assertIn(
            'tNext("startup.step.authDetail", "Authentication protects this '
            'DiscVault Next environment.")',
            collection,
        )
        # Old passkey-specific fallbacks must be gone.
        self.assertNotIn("Sign in with a passkey to continue setup.", collection)
        self.assertNotIn("Create the first owner passkey to finish setup.", collection)
        self.assertNotIn('tNext("startup.step.auth", "Owner passkey")', collection)
        self.assertNotIn(
            "Passkeys protect this DiscVault Next environment.", collection
        )

        # Genuine passkey enrollment copy stays passkey-specific.
        self.assertEqual(self.source["auth.createOwnerPasskey"], "Create owner passkey")
        self.assertEqual(self.source["auth.passkey"], "Passkey")
        self.assertIn(
            'data-next-i18n="auth.createOwnerPasskey">Create owner passkey', collection
        )
        self.assertIn('value="Owner passkey"', collection)


if __name__ == "__main__":
    unittest.main()
