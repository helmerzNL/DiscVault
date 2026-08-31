import os
import sys
import unittest
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend import next_app
    from app.backend import next_profile
    from app.backend import next_push
    from app.backend import next_views_ui
except ModuleNotFoundError as exc:  # Local minimal test environments may omit optional backend deps.
    if exc.name not in {"flask", "psycopg"}:
        raise
    next_app = None
    next_profile = None
    next_push = None
    next_views_ui = None


class _ElementAncestryParser(HTMLParser):
    VOID_ELEMENTS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self):
        super().__init__()
        self.stack = []
        self.ancestors_by_id = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        element_id = attributes.get("id")
        if element_id:
            self.ancestors_by_id[element_id] = tuple(item_id for _, item_id in self.stack if item_id)
        if tag not in self.VOID_ELEMENTS:
            self.stack.append((tag, element_id))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return


class _FakeCursor:
    def __init__(self, table_name):
        self._table_name = table_name
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *args):
        # ``table_exists`` probes ``to_regclass`` first.
        if "to_regclass" in args[0]:
            self._row = {"table_name": self._table_name}
        else:
            self._row = {"active_count": 2, "used_count": 1, "last_generated_at": "2026-01-01"}

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, table_name):
        self._table_name = table_name

    def cursor(self):
        return _FakeCursor(self._table_name)


class _RoleCursor:
    def __init__(self, role_name):
        self._role_name = role_name
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=None):
        if "to_regclass" in query:
            self._row = {"table_name": "public.roles"}
        elif "SELECT name FROM roles" in query:
            self._row = {"name": self._role_name}

    def fetchone(self):
        return self._row


class _RoleConn:
    def __init__(self, role_name):
        self._role_name = role_name

    def cursor(self):
        return _RoleCursor(self._role_name)


class _ProfileCursor:
    def __init__(self):
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=None):
        if "FROM users u" in query:
            self._row = {
                "id": "user-1",
                "username": "reviewer",
                "display_name": "Review User",
                "role": None,
                "avatar_asset_id": None,
                "avatar_id": None,
            }
        elif "SELECT name FROM roles" in query:
            self._row = {"name": "Media Viewer"}

    def fetchone(self):
        return self._row


class _ProfileConn:
    def cursor(self):
        return _ProfileCursor()


@unittest.skipIf(next_profile is None, "Flask is not installed in this test environment")
class NextProfileHelperTests(unittest.TestCase):
    def test_profile_helpers_are_reexported_from_next_app(self):
        for name in ("next_profile_user_payload", "next_profile_recovery_payload", "register_next_profile_routes"):
            self.assertIs(getattr(next_app, name), getattr(next_profile, name), name)

    def test_recovery_payload_reports_unavailable_when_table_missing(self):
        payload = next_profile.next_profile_recovery_payload(_FakeConn(None), "user-1")
        self.assertEqual(
            payload,
            {"available": False, "activeCount": 0, "usedCount": 0, "lastGeneratedAt": None},
        )

    def test_recovery_payload_counts_active_and_used_codes(self):
        payload = next_profile.next_profile_recovery_payload(_FakeConn("public.recovery_codes"), "user-1")
        self.assertEqual(
            payload,
            {"available": True, "activeCount": 2, "usedCount": 1, "lastGeneratedAt": "2026-01-01"},
        )

    def test_profile_role_uses_human_readable_name(self):
        self.assertEqual(
            next_profile._profile_role_display_name(_RoleConn("Media Viewer"), "media_viewer"),
            "Media Viewer",
        )

    def test_profile_role_falls_back_to_key_when_name_is_missing(self):
        self.assertEqual(
            next_profile._profile_role_display_name(_RoleConn(None), "custom_role"),
            "custom_role",
        )

    def test_profile_payload_keeps_role_key_and_adds_display_name(self):
        app_stub = SimpleNamespace(
            media_asset_public_url=lambda avatar: "",
            next_user_primary_role=lambda conn, user_id: "media_viewer",
        )
        with (
            patch.object(next_profile, "_next_app", return_value=app_stub),
            patch.object(next_profile, "table_exists", return_value=True),
        ):
            payload = next_profile.next_profile_user_payload(
                _ProfileConn(),
                {"id": "user-1"},
            )

        self.assertEqual(payload["role"], "media_viewer")
        self.assertEqual(payload["roleDisplayName"], "Media Viewer")
        self.assertEqual(payload["role_display_name"], "Media Viewer")


@unittest.skipIf(next_views_ui is None, "Flask is not installed in this test environment")
class NextProfileUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = next_views_ui.ui_preview_html(app_mode=True)

    def test_profile_navigation_uses_accessible_icon_tabs(self):
        tabs = {
            "Account": "account",
            "Preferences": "preferences",
            "Notifications": "notifications",
            "Groups": "groups",
            "Structure": "structure",
            "Security": "security",
            "Api": "api",
            "About": "about",
        }
        self.assertIn('class="detail-submenu profile-submenu profile-navigation" role="tablist"', self.html)
        for suffix, name in tabs.items():
            self.assertIn(f'id="profileTab{suffix}"', self.html)
            self.assertIn(f'aria-controls="profilePanel{suffix}"', self.html)
            self.assertIn(f'data-profile-tab="{name}"', self.html)
            self.assertIn(f'id="profilePanel{suffix}"', self.html)
            self.assertIn(f'aria-labelledby="profileTab{suffix}"', self.html)

    def test_profile_navigation_uses_canonical_mdi_icons(self):
        expected_paths = {
            "preferences": "M8 13C6.14 13 4.59 14.28 4.14 16H2V18H4.14",
            "notification_settings": "M22.72 19.5C22.74 19.33 22.75 19.17 22.75 19",
            "structure": "M12 13H7V18H12V20H5V10H7V11H12V13",
            "api": "M7 7H5A2 2 0 0 0 3 9V17H5V13H7V17H9",
        }
        for icon_name, path_start in expected_paths.items():
            with self.subTest(icon_name=icon_name):
                self.assertIn(f'class="nav-symbol {icon_name}"', self.html)
                self.assertIn(f'<path d="{path_start}', self.html)

    def test_profile_navigation_has_desktop_preference_and_mobile_icon_override(self):
        self.assertIn('data-profile-menu-style-choice="icon_text"', self.html)
        self.assertIn('data-profile-menu-style-choice="icon_only"', self.html)
        self.assertIn('document.documentElement.dataset.profileMenuStyle = selected;', self.html)
        self.assertIn('.profile-navigation .profile-tab-label {\n        display: none;', self.html)
        self.assertIn('html[data-profile-menu-style="icon_only"] .profile-navigation .profile-tab-label', self.html)
        self.assertIn('localStorage.setItem(PROFILE_MENU_STYLE_STORAGE_KEY, style);', self.html)
        self.assertIn('currentAuthStatus.auth_enabled === false', self.html)
        self.assertIn('setProfileMenuStyle(effectiveProfileMenuStyle());', self.html)

    def test_preferences_dashboard_uses_accessible_icon_tabs(self):
        tabs = {
            "Appearance": ("appearance", "M12,22A10,10 0 0,1 2,12"),
            "Library": ("library", "M9 3V18H12V3H9"),
            "Collectors": ("collectors", "M20 21H4V10H6V19H18V10H20V21"),
        }
        self.assertIn('class="preferences-category-nav" role="tablist"', self.html)
        for suffix, (name, path_start) in tabs.items():
            with self.subTest(name=name):
                self.assertIn(f'id="preferenceTab{suffix}"', self.html)
                self.assertIn(f'aria-controls="preferencePanel{suffix}"', self.html)
                self.assertIn(f'data-preferences-tab="{name}"', self.html)
                self.assertIn(f'id="preferencePanel{suffix}"', self.html)
                self.assertIn(f'aria-labelledby="preferenceTab{suffix}"', self.html)
                self.assertIn(f'<path d="{path_start}', self.html)
        self.assertIn("function handlePreferenceTabKeydown(button, event)", self.html)
        self.assertIn('button.setAttribute("tabindex", active ? "0" : "-1");', self.html)
        self.assertIn('panel.setAttribute("aria-hidden", active ? "false" : "true");', self.html)
        self.assertIn(".preferences-category-nav .preferences-category-copy {\n        display: none;", self.html)

    def test_preferences_dashboard_groups_settings_and_removes_legacy_modal(self):
        for card in ("browse", "details", "pricing", "mode", "display", "management"):
            self.assertIn(f'key: "{card}"', self.html)
        self.assertIn('data-preference-card="loans-system"', self.html)
        for element_id in (
            "profileLanguagePicker",
            "profileLanguageSelect",
            "profilePreferenceList",
            "profileCollectorPreferenceList",
            "loansSystemSettingRow",
        ):
            self.assertEqual(self.html.count(f'id="{element_id}"'), 1, element_id)

        library_groups = self.html.split("const preferenceLibraryGroups = [", 1)[1].split(
            "const preferenceCollectorGroups = [", 1
        )[0]
        collector_groups = self.html.split("const preferenceCollectorGroups = [", 1)[1].split(
            "const DEFAULT_PRICE_DISPLAY_CURRENCIES", 1
        )[0]
        self.assertNotIn("delete_container_members_with_container", library_groups)
        self.assertIn(
            '["delete_container_members_with_container", '
            '"preferences.deleteContainerMembersWithContainer", '
            '"preferences.deleteContainerMembersWithContainerHelp", "collectors_mode"]',
            collector_groups,
        )
        collectors_disable_block = self.html.split(
            'if (key === "collectors_mode" && !value) {', 1
        )[1].split("}", 1)[0]
        self.assertIn(
            "preferences.delete_container_members_with_container = false;",
            collectors_disable_block,
        )
        collectors_patch_block = self.html.split(
            'if (key === "collectors_mode" && !value) {', 2
        )[2].split("}", 1)[0]
        self.assertIn(
            "patch.delete_container_members_with_container = false;",
            collectors_patch_block,
        )
        self.assertIn('class="preferences-setting-card system wide"', self.html)
        self.assertNotIn('id="preferencesBackdrop"', self.html)
        self.assertNotIn('id="legacyPreferenceList"', self.html)
        self.assertNotIn("legacyList", self.html)

    def test_notifications_dashboard_groups_status_preferences_and_devices(self):
        for card in ("status", "devices"):
            self.assertIn(f'data-notification-settings-card="{card}"', self.html)
        for group in ("system", "library", "sharing"):
            self.assertIn(f'data-push-preference-card="{group}"', self.html)
            self.assertIn(f'data-push-preference-group="{group}"', self.html)

        for element_id in (
            "pushBrowserState",
            "pushDeviceState",
            "pushEnableButton",
            "pushDisableButton",
            "pushRefreshButton",
            "pushTestButton",
            "pushPriceCheckButton",
            "pushPreferenceList",
            "pushDeviceList",
            "pushProfileMessage",
        ):
            self.assertEqual(self.html.count(f'id="{element_id}"'), 1, element_id)

        self.assertIn('id="pushProfileMessage" aria-live="polite"', self.html)
        self.assertIn("const pushPreferenceGroups = {", self.html)
        self.assertIn(
            'system: [\n'
            '        ["app_updates", "notifications.prefAppUpdates", '
            '"notifications.prefAppUpdatesHelp"],\n'
            '        ["security", "notifications.prefSecurity", '
            '"notifications.prefSecurityHelp"]',
            self.html,
        )
        self.assertIn(
            'sharing: [\n'
            '        ["group_invites", "notifications.prefGroupInvites", '
            '"notifications.prefGroupInvitesHelp"]',
            self.html,
        )
        self.assertIn('aria-label="${escapeHtml(label)}"', self.html)
        self.assertIn('enableButton.classList.toggle("hidden", pushProfile.subscribed);', self.html)
        self.assertIn('disableButton.classList.toggle("hidden", !pushProfile.subscribed);', self.html)
        self.assertIn('class="push-device-row ${item.current ? "current" : ""}"', self.html)
        self.assertIn(".notification-overview-grid,", self.html)
        self.assertIn(".notification-preference-grid,", self.html)

    def test_groups_dashboard_uses_overview_cards_and_preserves_bindings(self):
        for card in ("create", "scope"):
            self.assertIn(f'data-groups-dashboard-card="{card}"', self.html)

        for element_id in (
            "memberGroupCreateSection",
            "memberGroupCreateForm",
            "memberGroupNameInput",
            "memberGroupCreateButton",
            "memberGroupScopeSummary",
            "memberGroupMessage",
            "memberGroupsHeading",
            "memberGroupList",
        ):
            self.assertEqual(self.html.count(f'id="{element_id}"'), 1, element_id)

        self.assertIn('id="memberGroupMessage" aria-live="polite"', self.html)
        self.assertIn('class="member-group-card" data-member-group-id="${id}"', self.html)
        self.assertIn('class="member-group-metrics"', self.html)
        self.assertIn('class="member-group-body ${invitePanel ? "" : "members-only"}"', self.html)
        self.assertIn('data-member-group-open="${id}"', self.html)
        self.assertIn('data-member-group-invite-send="${id}"', self.html)
        self.assertIn('data-member-group-remove-user="${id}"', self.html)
        self.assertIn('data-member-group-delete="${id}"', self.html)
        self.assertIn("const canInvite = canManageMemberGroup(group);", self.html)
        self.assertIn(
            "const canDelete = isOwner && (isSystemOwner ? members.length === 0 : members.length === 1);",
            self.html,
        )
        self.assertIn(".groups-overview-grid,", self.html)
        self.assertIn(".member-group-body {", self.html)

    def test_groups_dashboard_renames_inline_without_browser_prompt(self):
        self.assertIn("let editingMemberGroupId = \"\";", self.html)
        self.assertIn("function startMemberGroupRename(groupId)", self.html)
        self.assertIn("function cancelMemberGroupRename(groupId)", self.html)
        self.assertIn("async function saveMemberGroupRename(groupId, form)", self.html)
        self.assertIn('data-member-group-rename-form="${id}"', self.html)
        self.assertIn('data-member-group-rename-input="${id}"', self.html)
        self.assertIn('data-member-group-rename-cancel="${id}"', self.html)
        self.assertIn('if (event.key !== "Escape") return;', self.html)
        self.assertIn("saveMemberGroupRename(form.dataset.memberGroupRenameForm, form);", self.html)
        self.assertNotIn(
            'window.prompt(tNext("groups.renamePrompt", "New group name")',
            self.html,
        )

    def test_structure_dashboard_uses_accessible_workspace_cards(self):
        self.assertIn('class="profile-dashboard structure-dashboard"', self.html)
        tabs = {
            "box_set": "structureContainersPanel",
            "vault": "structureContainersPanel",
            "collection": "structureContainersPanel",
            "locations": "structureLocationsPanel",
        }
        for tab, panel_id in tabs.items():
            with self.subTest(tab=tab):
                self.assertIn(f'data-structure-tab="{tab}"', self.html)
                self.assertIn(f'aria-controls="{panel_id}"', self.html)

        for element_id in (
            "containerManagerCreateForm",
            "containerManagerTitle",
            "containerManagerCreateButton",
            "containerManagerMessage",
            "containerManagerListHeading",
            "containerManagerCount",
            "containerManagerList",
            "locationCreateForm",
            "locationManagerMessage",
            "locationsTree",
        ):
            self.assertEqual(self.html.count(f'id="{element_id}"'), 1, element_id)

        self.assertIn('id="containerManagerMessage" aria-live="polite"', self.html)
        self.assertIn('class="container-manager-row" data-container-manager-id="${id}"', self.html)
        self.assertIn("function handleStructureTabKeydown(button, event)", self.html)
        self.assertIn('button.setAttribute("aria-selected", active ? "true" : "false");', self.html)
        self.assertIn(".structure-workspace {", self.html)
        self.assertIn(".locations-row {", self.html)

    def test_security_dashboard_preserves_credentials_and_recovery_actions(self):
        self.assertIn('class="profile-dashboard security-dashboard"', self.html)
        for card in ("passkeys", "legacy", "recovery"):
            self.assertIn(f'data-security-dashboard-card="{card}"', self.html)

        for element_id in (
            "profileRefreshPasskeysButton",
            "profilePasskeyList",
            "profileNewPasskeyNameInput",
            "profileAddPasskeyButton",
            "profileSecurityMessage",
            "profileLegacySecurity",
            "profileLegacyPasswordForm",
            "profileLegacyMfaStatus",
            "profileLegacyMfaActions",
            "profileLegacyMfaEnableButton",
            "profileLegacyMfaSetup",
            "profileLegacyMfaPasswordForm",
            "profileLegacyMfaCurrentPassword",
            "profileLegacyMfaTotpStep",
            "profileLegacyMfaQr",
            "profileLegacyMfaManualKey",
            "profileLegacyMfaTotpForm",
            "profileLegacyMfaCode",
            "profileLegacyMfaRecoveryStep",
            "profileLegacyMfaRecoveryCodes",
            "profileLegacyMfaRecoveryAck",
            "profileLegacyMfaFinishButton",
            "profileLegacyMfaCancelButton",
            "profileRecoveryActiveCount",
            "profileRecoveryLastGenerated",
            "profileRecoveryCodes",
            "profileGenerateRecoveryButton",
            "profileRevokeRecoveryButton",
            "profileRecoveryMessage",
        ):
            self.assertEqual(self.html.count(f'id="{element_id}"'), 1, element_id)

        self.assertIn('class="profile-passkey security-credential-card"', self.html)
        self.assertIn('id="profileSecurityMessage" aria-live="polite"', self.html)
        self.assertIn('id="profileRecoveryMessage" aria-live="polite"', self.html)
        self.assertIn(".profile-security-stats {", self.html)
        self.assertIn("function renderProfileLegacyMfa()", self.html)
        self.assertIn("const enabled = Boolean(credential.mfa_required) && enrolled;", self.html)
        self.assertIn("function startProfileMfaEnrollment(event)", self.html)
        self.assertIn("function verifyProfileMfaEnrollment(event)", self.html)
        self.assertIn("function finishProfileMfaEnrollment()", self.html)
        self.assertIn("/api/next/auth/legacy/mfa/enroll/start", self.html)
        self.assertIn("/api/next/auth/legacy/mfa/setup/verify", self.html)
        self.assertIn("/api/next/auth/legacy/recovery-codes/ack", self.html)

    def test_api_dashboard_uses_accessible_tabs_and_summary_cards(self):
        self.assertIn('class="profile-dashboard api-dashboard"', self.html)
        tabs = {
            "General": ("general", "profileApiPanelGeneral"),
            "Create": ("create", "profileApiPanelCreate"),
            "Activity": ("activity", "profileApiPanelActivity"),
        }
        for suffix, (tab, panel_id) in tabs.items():
            with self.subTest(tab=tab):
                self.assertIn(f'id="profileApiTab{suffix}"', self.html)
                self.assertIn(f'data-profile-api-tab="{tab}"', self.html)
                self.assertIn(f'aria-controls="{panel_id}"', self.html)
                self.assertIn(f'id="{panel_id}" role="tabpanel"', self.html)

        for element_id in (
            "profileApiMcpSummary",
            "profileApiTokenList",
            "profileApiTokenForm",
            "profileApiPermissionList",
            "profileCreateApiTokenButton",
            "profileNewApiToken",
            "profileApiMessage",
            "profileApiAuditRefreshButton",
            "profileApiAuditList",
        ):
            self.assertEqual(self.html.count(f'id="{element_id}"'), 1, element_id)

        self.assertIn("function handleProfileApiTabKeydown(button, event)", self.html)
        self.assertIn('panel.setAttribute("aria-hidden", active ? "false" : "true");', self.html)
        self.assertIn('class="profile-api-summary-item"', self.html)
        self.assertIn('class="profile-passkey profile-api-token-card ${revoked ? "disabled" : ""}"', self.html)

    def test_about_dashboard_balances_version_credits_and_debug_info(self):
        self.assertIn('class="profile-dashboard about-dashboard"', self.html)
        self.assertIn('class="profile-about-grid"', self.html)
        self.assertIn(
            'class="profile-dashboard-card primary profile-about-card profile-about-card--version"',
            self.html,
        )
        self.assertIn('class="profile-dashboard-card profile-about-card profile-about-card--debug"', self.html)
        for element_id in (
            "profileAppVersion",
            "profileUpdateBlock",
            "profileUpdateChannel",
            "profileUpdateCheckButton",
            "profileUpdateResult",
            "profileBuildSha",
            "profileOfflineStatus",
        ):
            self.assertEqual(self.html.count(f'id="{element_id}"'), 1, element_id)
        self.assertIn(".profile-about-grid {", self.html)
        self.assertIn(".profile-about-version {", self.html)

    def test_about_dashboard_invites_support_through_a_locally_served_button(self):
        """The donation card, and the sidebar link that reaches it.

        Two things are asserted rather than assumed. The button image resolves
        to DiscVault's own asset route: linking Buy Me a Coffee's CDN would
        report every visit to the About page to a third party and would leave
        the card broken on an install with no route to the internet. And the
        link opens in a new tab with ``rel="noopener noreferrer"``, because the
        opener reference is what an external page would otherwise be handed.
        """
        self.assertIn(
            'class="profile-dashboard-card profile-about-card profile-about-card--support"',
            self.html,
        )
        self.assertIn('data-next-i18n="profile.supportDevelopment"', self.html)
        self.assertIn('data-next-i18n="profile.supportDevelopmentHelp"', self.html)
        self.assertIn('src="/api/next/assets/buymeacoffee-button.png"', self.html)
        self.assertNotIn("cdn.buymeacoffee.com", self.html)
        self.assertIn(
            '<a class="profile-about-coffee" id="profileBuyMeACoffee" '
            'href="https://buymeacoffee.com/flux76" target="_blank" rel="noopener noreferrer">',
            self.html,
        )
        self.assertEqual(self.html.count('id="profileBuyMeACoffee"'), 1)
        self.assertIn(".profile-about-coffee {", self.html)

    def test_buy_me_a_coffee_button_is_whitelisted_and_present_on_disk(self):
        """The two halves of a served asset, checked together.

        The route only answers for names in ``PWA_ICON_ASSETS`` and only when
        the file is there, and it answers a miss with a 404 rather than an
        error. So a card that references the button while either half is
        missing renders a broken image and nothing anywhere reports why.
        """
        self.assertIn("buymeacoffee-button.png", next_push.PWA_ICON_ASSETS)
        asset = next_app.next_frontend_dir() / "buymeacoffee-button.png"
        self.assertTrue(asset.is_file(), asset)

    def test_sidebar_support_link_reaches_the_about_panel_on_desktop_only(self):
        """A quiet text link in the sidebar, hidden wherever the version block is.

        It shares the collapsed-rail and narrow-viewport rules with
        ``.sidebar-footer`` deliberately: those are the two states where the
        sidebar has no room for supporting text, and a link that survived them
        would either overflow the rail or crowd the mobile top bar.
        """
        self.assertIn(
            '<button type="button" class="sidebar-support" id="sidebarSupportLink" '
            'data-next-i18n="uiPreview.supportDiscVault">Support DiscVault</button>',
            self.html,
        )
        self.assertIn(".preview-shell.sidebar-collapsed .sidebar-support,", self.html)
        self.assertIn("      .sidebar-support { display: none; }", self.html)
        self.assertIn(
            'document.getElementById("sidebarSupportLink")?.addEventListener("click", () => openAboutPage());',
            self.html,
        )
        # The tab is selected before the page renders, so the About panel is
        # what opens rather than whichever tab the reader last used.
        self.assertIn(
            'function openAboutPage() {',
            self.html,
        )
        about_page = self.html.split("function openAboutPage() {", 1)[1].split("}", 1)[0]
        self.assertLess(
            about_page.index('setProfileTab("about")'),
            about_page.index("showProfilePage()"),
        )

    def test_admin_access_dashboard_preserves_invite_and_passkey_management(self):
        self.assertIn(
            'id="appAdminPanelAccess" role="tabpanel" aria-labelledby="appAdminTabAccess"',
            self.html,
        )
        for card in ("access-overview", "invite-create", "active-invites", "passkeys"):
            self.assertIn(f'data-admin-dashboard-card="{card}"', self.html)

        for element_id in (
            "appAdminRegistrationMode",
            "appAdminUserCount",
            "appAdminCredentialCount",
            "appAdminSecurityMessage",
            "appAdminInviteForm",
            "appAdminInviteUsername",
            "appAdminCreateInviteButton",
            "appAdminInviteCodeOutput",
            "appAdminInviteMessage",
            "appAdminInvitesList",
            "appAdminCredentialsList",
        ):
            self.assertEqual(self.html.count(f'id="{element_id}"'), 1, element_id)

        self.assertIn('id="appAdminSecurityMessage" aria-live="polite"', self.html)
        self.assertIn('id="appAdminInviteMessage" aria-live="polite"', self.html)
        self.assertIn('class="profile-passkey app-admin-entity-card app-admin-invite-card', self.html)
        self.assertIn('class="profile-passkey app-admin-entity-card app-admin-passkey-card"', self.html)

    def test_admin_submenu_uses_responsive_icon_navigation(self):
        self.assertIn('<div class="app-admin-workspace">', self.html)
        self.assertIn('class="app-admin-submenu detail-submenu"', self.html)
        self.assertIn(".app-admin-workspace {", self.html)
        self.assertIn("grid-template-columns: minmax(190px, 220px) minmax(0, 1fr);", self.html)
        self.assertIn("@media (max-width: 860px)", self.html)
        self.assertIn("function syncAppAdminSubmenuOrientation()", self.html)
        self.assertIn("submenu.querySelector('[aria-selected=\"true\"]')?.scrollIntoView", self.html)
        self.assertIn('"ArrowUp", "ArrowDown"', self.html)
        self.assertIn("button.scrollIntoView({block: \"nearest\", inline: \"nearest\"});", self.html)
        for tab in (
            "Access",
            "Users",
            "Roles",
            "Operations",
            "Plugins",
            "Digital",
            "Metadata",
            "Backup",
            "Audit",
        ):
            self.assertIn(f'id="appAdminTab{tab}" role="tab"', self.html)
        self.assertEqual(self.html.count('class="app-admin-tab-icon"'), 9)
        self.assertEqual(self.html.count('class="app-admin-tab-label"'), 9)
        self.assertIn(
            'html[data-profile-menu-style="icon_only"] .app-admin-workspace {',
            self.html,
        )
        self.assertIn(
            'html[data-profile-menu-style="icon_only"] .app-admin-submenu .app-admin-tab-label {',
            self.html,
        )
        self.assertIn(".app-admin-submenu .app-admin-tab-label {\n        display: none;", self.html)
        self.assertIn('id="profileMenuStyleAdminHelp"', self.html)
        self.assertIn(
            'profileMenuStyleAdminHelp")?.classList.toggle("hidden", !allowed)',
            self.html,
        )

    def test_admin_users_dashboard_preserves_user_and_group_management(self):
        self.assertIn(
            'id="appAdminPanelUsers" role="tabpanel" aria-labelledby="appAdminTabUsers"',
            self.html,
        )
        for card in ("legacy-auth", "legacy-user-create", "users", "groups"):
            self.assertIn(f'data-admin-dashboard-card="{card}"', self.html)

        for element_id in (
            "appAdminLegacyCard",
            "appAdminLegacyStatus",
            "appAdminLegacyEnable",
            "appAdminLegacyDisable",
            "appAdminLegacyMessage",
            "appAdminLegacyCreateCard",
            "appAdminLegacyUserForm",
            "appAdminUsersList",
            "appAdminUsersMessage",
            "appAdminGroupForm",
            "appAdminGroupsList",
            "appAdminGroupsMessage",
        ):
            self.assertEqual(self.html.count(f'id="{element_id}"'), 1, element_id)

        self.assertIn('class="profile-passkey app-admin-entity-card app-admin-user-card', self.html)
        self.assertIn('class="profile-passkey app-admin-entity-card admin-group-row"', self.html)
        self.assertIn('class="profile-add-passkey app-admin-legacy-controls"', self.html)
        self.assertIn("function handleAppAdminTabKeydown(button, event)", self.html)
        self.assertIn("function handleAppAdminUsersTabKeydown(button, event)", self.html)
        self.assertIn('panel.setAttribute("aria-hidden", active ? "false" : "true");', self.html)
        for tab, panel in (
            ("Settings", "Settings"),
            ("Users", "Users"),
            ("Groups", "Groups"),
        ):
            self.assertIn(
                f'id="appAdminUsersTab{tab}" role="tab"',
                self.html,
            )
            self.assertIn(
                f'id="appAdminUsersPanel{panel}" role="tabpanel"',
                self.html,
            )

    def test_admin_submenus_share_preferences_layout_and_accent_selection(self):
        for class_name in (
            "app-admin-people-tabs",
            "app-admin-roles-tabs",
            "app-admin-operations-tabs",
            "app-admin-plugin-tabs",
            "app-admin-digital-tabs",
            "app-admin-metadata-tabs",
            "app-admin-backup-tabs",
            "app-admin-audit-tabs",
        ):
            self.assertIn(
                f"profile-dashboard-tabs app-admin-section-tabs {class_name}",
                self.html,
            )
        self.assertEqual(
            self.html.count(
                'class="detail-submenu profile-dashboard-tabs app-admin-section-tabs'
            ),
            8,
        )
        self.assertIn(".app-admin-section-tabs button.active,", self.html)
        self.assertIn('.app-admin-section-tabs button[aria-selected="true"] {', self.html)
        self.assertIn("color: var(--accent-bright);", self.html)
        self.assertIn(
            "background: color-mix(in srgb, var(--accent) 18%, var(--bg-solid));",
            self.html,
        )

    def test_admin_roles_dashboard_separates_basic_and_advanced_workflows(self):
        self.assertIn(
            'id="appAdminPanelRoles" role="tabpanel" aria-labelledby="appAdminTabRoles"',
            self.html,
        )
        for tab in ("Overview", "Roles", "Permissions", "Simulator"):
            self.assertIn(f'id="appAdminRolesTab{tab}" role="tab"', self.html)
            self.assertIn(f'id="appAdminRolesPanel{tab}" role="tabpanel"', self.html)

        for element_id in (
            "appAdminRbacMode",
            "appAdminRoleCreateForm",
            "appAdminRolesList",
            "appAdminRoleEditor",
            "appAdminRoleEditForm",
            "appAdminPermissionEditor",
            "appAdminRoleFeaturePreview",
            "appAdminPermissionMatrix",
            "appAdminRoleSimulationSelect",
        ):
            self.assertEqual(self.html.count(f'id="{element_id}"'), 1, element_id)

        self.assertIn("function handleAppAdminRolesTabKeydown(button, event)", self.html)
        self.assertIn('button.dataset.appAdminRolesTab === "permissions"', self.html)
        self.assertIn('const visibleRoles = advanced ? roles : roles.filter((role) => !role.custom);', self.html)

    def test_admin_operations_dashboard_uses_focused_submenus(self):
        self.assertIn(
            'id="appAdminPanelOperations" role="tabpanel" aria-labelledby="appAdminTabOperations"',
            self.html,
        )
        for tab in ("Overview", "Collection", "Readiness", "Policy", "Activity"):
            self.assertIn(f'id="appAdminOperationsTab{tab}" role="tab"', self.html)
            self.assertIn(f'id="appAdminOperationsPanel{tab}" role="tabpanel"', self.html)
        self.assertEqual(self.html.count('data-app-admin-operations-tab='), 5)
        self.assertEqual(self.html.count('aria-hidden="true">-</span></button>'), 5)

        for element_id in (
            "appAdminOperationsDashboard",
            "appAdminCollectionHealthSummary",
            "appAdminCollectionHealthIssues",
            "appAdminOperationsFeatures",
            "appAdminFeatureClusters",
            "appAdminDuplicateCenter",
            "appAdminProviderPolicy",
            "appAdminApiTokenPresets",
            "appAdminOperationsSignals",
        ):
            self.assertEqual(self.html.count(f'id="{element_id}"'), 1, element_id)

        self.assertIn("activeOperationsTab: localStorage.getItem", self.html)
        self.assertIn("function setAppAdminOperationsTab(tab)", self.html)
        self.assertIn("function handleAppAdminOperationsTabKeydown(button, event)", self.html)
        self.assertIn('localStorage.setItem("dv_next_admin_operations_tab"', self.html)
        self.assertIn('data-app-admin-operations-panel="collection"', self.html)
        self.assertEqual(self.html.count('class="tag app-admin-operations-tab-status"'), 5)
        for tone in ("good", "bad", "blue"):
            self.assertIn(
                f'.app-admin-operations-tabs button[data-status-tone="{tone}"] .nav-symbol',
                self.html,
            )
        self.assertIn('button.dataset.statusTone = tone || "neutral";', self.html)
        self.assertIn("function appAdminOperationsOverviewStatusHtml(status)", self.html)
        self.assertIn('if (value !== "attention")', self.html)
        self.assertIn('class="operations-attention-icon" role="img"', self.html)
        self.assertIn(
            "<strong>${appAdminOperationsOverviewStatusHtml(health.status || \"ok\")}</strong>",
            self.html,
        )
        self.assertIn(".app-admin-operations-tabs .app-admin-operations-tab-status {", self.html)
        self.assertIn("grid-auto-columns: minmax(68px, 1fr);", self.html)
        self.assertIn(".app-admin-operations-tabs::-webkit-scrollbar {", self.html)
        self.assertNotIn(
            ".app-admin-operations-tabs .app-admin-operations-tab-status {\n"
            "        display: none;",
            self.html,
        )

    def test_admin_plugins_dashboard_uses_task_oriented_subtabs(self):
        self.assertIn(
            'id="appAdminPanelPlugins" role="tabpanel" aria-labelledby="appAdminTabPlugins"',
            self.html,
        )
        for tab in ("Overview", "Installed", "Packages", "Activity"):
            self.assertIn(f'id="appAdminPluginTab{tab}" role="tab"', self.html)
            self.assertIn(f'id="appAdminPluginPanel{tab}" role="tabpanel"', self.html)
        self.assertEqual(self.html.count("data-app-admin-plugin-tab="), 4)
        self.assertEqual(self.html.count('class="app-admin-plugin-tab-panel'), 4)

        # No more wrapping 8-button type-tab submenu; a single compact filter set instead.
        self.assertNotIn("data-app-admin-plugin-type-tab=", self.html)
        self.assertIn('id="appAdminPluginTypeFilter"', self.html)
        self.assertIn('id="appAdminPluginStatusFilter"', self.html)
        self.assertIn('id="appAdminPluginSearchInput"', self.html)
        for category in (
            "all",
            "metadata_source",
            "metadata_receiver",
            "digital_media_source",
            "personal_list_source",
            "price_provider",
            "import_source",
            "system",
            "other",
        ):
            self.assertIn(f'value="{category}"', self.html)
        for status in ("all", "enabled", "disabled", "config-needed", "update-available"):
            self.assertIn(f'value="{status}"', self.html)

        # Existing renderers/surfaces are reused, not duplicated.
        for element_id in (
            "appAdminPluginDashboard",
            "appAdminPluginAttention",
            "appAdminMetadataPriorityPanel",
            "appAdminPluginExecutionDashboard",
            "appAdminPluginPackageValidator",
            "appAdminPersonalListSyncDashboard",
            "appAdminContributionCenter",
            "appAdminContributionQueue",
            "appAdminPluginJobsList",
            "appAdminPluginsList",
            "appAdminPluginImportFile",
            "appAdminImportPluginButton",
            "appAdminPluginAutoUpdateToggle",
            "appAdminRefreshPluginJobsButton",
        ):
            self.assertEqual(self.html.count(f'id="{element_id}"'), 1, element_id)

        self.assertIn('activePluginTab: localStorage.getItem("dv_next_admin_plugin_tab") || "overview"', self.html)
        self.assertIn('activePluginTypeTab: localStorage.getItem("dv_next_admin_plugin_type_tab") || "all"', self.html)
        self.assertIn("function setAppAdminPluginTab(tab)", self.html)
        self.assertIn("function handleAppAdminPluginTabKeydown(button, event)", self.html)
        self.assertIn('registry: "installed", jobs: "activity"', self.html)
        self.assertIn("function appAdminPluginMatchesStatusFilter(plugin, filter)", self.html)
        self.assertIn("function appAdminPluginMatchesSearch(plugin, term)", self.html)
        self.assertIn("function renderAppAdminPluginAttention()", self.html)
        self.assertIn('const canReorder = canManage && !!sectionCategory;', self.html)
        self.assertIn('appAdmin.activePluginTypeTab === "all"', self.html)
        self.assertIn('plugins.filter(matchesFilters)', self.html)
        self.assertNotIn('tNext("appAdmin.failedPluginJobs"', self.html)

    def test_admin_digital_and_metadata_dashboards_use_task_oriented_subtabs(self):
        for tab, panel in (("Overview", "Overview"), ("Sources", "Sources")):
            self.assertIn(f'id="appAdminDigitalTab{tab}" role="tab"', self.html)
            self.assertIn(f'id="appAdminDigitalPanel{panel}" role="tabpanel"', self.html)
        for tab, panel in (("Overview", "Overview"), ("Artwork", "Artwork"), ("Jobs", "Jobs")):
            self.assertIn(f'id="appAdminMetadataTab{tab}" role="tab"', self.html)
            self.assertIn(f'id="appAdminMetadataPanel{panel}" role="tabpanel"', self.html)

        for element_id in (
            "appAdminDigitalSourceTotal",
            "appAdminDigitalSourceActive",
            "appAdminDigitalSourceItems",
            "appAdminDigitalSourceMatched",
            "appAdminDigitalSourcesList",
            "appAdminMetadataJobTotal",
            "appAdminMetadataJobActive",
            "appAdminMetadataJobFailed",
            "appAdminMetadataArtworkTotal",
            "appAdminArtworkTrashList",
            "appAdminMetadataJobsList",
        ):
            self.assertEqual(self.html.count(f'id="{element_id}"'), 1, element_id)

        self.assertIn('activeDigitalTab: localStorage.getItem("dv_next_admin_digital_tab") || "overview"', self.html)
        self.assertIn('activeMetadataTab: localStorage.getItem("dv_next_admin_metadata_tab") || "overview"', self.html)
        self.assertIn("function setAppAdminDigitalTab(tab)", self.html)
        self.assertIn("function handleAppAdminDigitalTabKeydown(button, event)", self.html)
        self.assertIn("function setAppAdminMetadataTab(tab)", self.html)
        self.assertIn("function handleAppAdminMetadataTabKeydown(button, event)", self.html)
        self.assertIn(
            'setElementVisible(document.querySelector(\'[data-app-admin-metadata-tab="artwork"]\')',
            self.html,
        )
        self.assertIn("sources.reduce((total, source) => total + sourceItems(source), 0)", self.html)
        self.assertIn("source.matchedItemCount ?? source.matchedCount", self.html)
        self.assertIn("source.last_status || source.status", self.html)
        self.assertIn("source.enabled !== false && activeStatuses.has", self.html)
        self.assertIn("metadataJobCounts: {total: 0, byStatus: {}}", self.html)
        self.assertIn("appAdmin.metadataJobCounts = payload.counts", self.html)
        self.assertIn("Object.entries(statusCounts).reduce(", self.html)

        # Existing plugin action hooks are preserved unchanged.
        for action in (
            "data-app-admin-plugin-config-form=",
            "data-app-admin-plugin-enable=",
            "data-app-admin-plugin-update=",
            "data-app-admin-plugin-rollback=",
            "data-app-admin-plugin-export=",
            "data-app-admin-plugin-delete=",
        ):
            self.assertIn(action, self.html)

    def test_admin_backup_and_audit_dashboards_use_task_oriented_subtabs(self):
        for tab, panel in (
            ("Overview", "Overview"),
            ("Create", "Create"),
            ("Restore", "Restore"),
            ("Activity", "Activity"),
        ):
            self.assertIn(f'id="appAdminBackupTab{tab}" role="tab"', self.html)
            self.assertIn(f'id="appAdminBackupPanel{panel}" role="tabpanel"', self.html)
        for tab, panel in (("Overview", "Overview"), ("Events", "Events")):
            self.assertIn(f'id="appAdminAuditTab{tab}" role="tab"', self.html)
            self.assertIn(f'id="appAdminAuditPanel{panel}" role="tabpanel"', self.html)

        for element_id in (
            "appAdminBackupState",
            "appAdminBackupArchiveCount",
            "appAdminBackupJobCount",
            "appAdminBackupReportState",
            "appAdminBackupScopes",
            "appAdminBackupForm",
            "appAdminBackupList",
            "appAdminBackupReport",
            "appAdminBackupJobs",
            "appAdminAuditTotal",
            "appAdminAuditSecurityCount",
            "appAdminAuditAdminCount",
            "appAdminAuditBackupCount",
            "appAdminAuditCategory",
            "appAdminAuditList",
        ):
            self.assertEqual(self.html.count(f'id="{element_id}"'), 1, element_id)

        self.assertIn('activeBackupTab: localStorage.getItem("dv_next_admin_backup_tab") || "overview"', self.html)
        self.assertIn('activeAuditTab: localStorage.getItem("dv_next_admin_audit_tab") || "overview"', self.html)
        self.assertIn("function setAppAdminBackupTab(tab)", self.html)
        self.assertIn("function handleAppAdminBackupTabKeydown(button, event)", self.html)
        self.assertIn("function setAppAdminAuditTab(tab)", self.html)
        self.assertIn("function handleAppAdminAuditTabKeydown(button, event)", self.html)
        self.assertIn(
            'setElementVisible(document.querySelector(\'[data-app-admin-backup-tab="create"]\')',
            self.html,
        )
        self.assertIn(
            'setElementVisible(document.querySelector(\'[data-app-admin-backup-tab="restore"]\')',
            self.html,
        )
        self.assertIn("appAdmin.auditCounts = payload.counts", self.html)
        self.assertIn("Number(byCategory.security || 0)", self.html)
        self.assertGreaterEqual(
            self.html.count('tNext("appAdmin.backupZipValid", "ZIP is valid")'),
            4,
        )
        self.assertNotIn('tNext("appAdmin.valid"', self.html)
        self.assertNotIn('tNext("appAdmin.invalid"', self.html)

    def test_admin_role_creation_generates_key_and_starts_permission_wizard(self):
        for element_id in (
            "appAdminRoleName",
            "appAdminRoleDescription",
            "appAdminRoleKey",
            "appAdminRoleKeyHelp",
            "appAdminRoleCreateMessage",
            "appAdminCreateRoleButton",
            "appAdminRoleWizardMessage",
        ):
            self.assertEqual(self.html.count(f'id="{element_id}"'), 1, element_id)

        self.assertIn('id="appAdminRoleKey" autocomplete="off" maxlength="64" readonly', self.html)
        self.assertIn('id="appAdminRoleCreateMessage" role="status" aria-live="polite"', self.html)
        self.assertIn('id="appAdminRoleWizardMessage" role="status" aria-live="polite"', self.html)
        self.assertIn("function appAdminRoleKeySlug(name)", self.html)
        self.assertIn('.normalize("NFKD")', self.html)
        self.assertIn('.replace(/[^a-z0-9]+/g, "_")', self.html)
        self.assertIn('const base = `cr_${slug}`.slice(0, 64)', self.html)
        self.assertIn("while (existingKeys.has(candidate))", self.html)
        self.assertIn('const suffix = `_${suffixNumber}`;', self.html)
        self.assertIn("function renderAppAdminRoleCreateForm()", self.html)
        self.assertIn('form.setAttribute("aria-busy", busy ? "true" : "false");', self.html)
        self.assertIn("button.disabled = busy || !key;", self.html)
        self.assertIn('if (!appAdminCanManageRbac() || (appAdmin.roleCreation || {}).busy) return;', self.html)
        self.assertIn("body: JSON.stringify({key, name, description, permissions: []})", self.html)
        self.assertIn("selectAppAdminRole(roleId, 2);", self.html)
        self.assertIn('setAppAdminMessage("appAdminRoleWizardMessage"', self.html)

    def test_admin_role_view_opens_permission_wizard(self):
        for element_id in (
            "appAdminRoleWizardHeading",
            "appAdminRoleWizardSummary",
            "appAdminRoleWizardStep1",
            "appAdminRoleWizardStep2",
            "appAdminRoleWizardStep3",
            "appAdminRoleWizardDetails",
            "appAdminRoleWizardPermissions",
            "appAdminRoleWizardReview",
            "appAdminRoleWizardBackButton",
            "appAdminRoleWizardNextButton",
            "appAdminRoleReview",
        ):
            self.assertEqual(self.html.count(f'id="{element_id}"'), 1, element_id)

        for step in ("1", "2", "3"):
            self.assertIn(f'data-app-admin-role-wizard-step="{step}"', self.html)
            self.assertIn(f'data-app-admin-role-wizard-panel="{step}"', self.html)
            self.assertIn(f'aria-controls="appAdminRoleWizard', self.html)
            self.assertIn(f'aria-labelledby="appAdminRoleWizardStep{step}"', self.html)

        self.assertIn("function selectAppAdminRole(roleId, initialStep = 1)", self.html)
        self.assertIn('setAppAdminRolesTab("permissions");', self.html)
        self.assertIn('editor?.scrollIntoView({behavior: "smooth", block: "start"});', self.html)
        self.assertIn(
            '(wizard.step > 1 ? panelHeading : document.getElementById("appAdminRoleWizardHeading"))?.focus();',
            self.html,
        )
        self.assertIn('<details class="app-admin-role-preview">', self.html)
        self.assertIn('<summary data-next-i18n="appAdmin.featurePreview">', self.html)

    def test_admin_role_wizard_preserves_draft_and_system_roles_are_read_only(self):
        self.assertIn(
            'roleWizard: {roleId: "", step: 1, name: "", description: "", permissions: [], openDomains: [], dirty: false}',
            self.html,
        )
        self.assertIn("function appAdminEnsureRoleWizard(role)", self.html)
        self.assertIn("function appAdminCaptureRoleWizardDraft()", self.html)
        self.assertIn("function appAdminRoleReviewHtml(role, draft)", self.html)
        self.assertIn("const editable = !!(canManage && selectedRole?.custom);", self.html)
        self.assertIn("nameInput.disabled = !editable;", self.html)
        self.assertIn('saveButton.classList.toggle("hidden", wizard.step !== 3 || !editable);', self.html)
        self.assertIn("const permissions = [...(wizard.permissions || [])];", self.html)
        self.assertIn("body: JSON.stringify({name, description, permissions})", self.html)

    def test_admin_permission_categories_render_as_collapsed_accordions(self):
        self.assertIn('class="app-admin-permission-domain-toggle"', self.html)
        self.assertIn('data-app-admin-permission-domain-toggle=', self.html)
        self.assertIn('aria-expanded="${open.has(domain) ? "true" : "false"}"', self.html)
        self.assertIn('class="app-admin-permission-domain-panel ${open.has(domain) ? "" : "hidden"}"', self.html)
        self.assertIn('data-app-admin-permission-count=', self.html)
        self.assertIn("wizard.openDomains = [...openDomains];", self.html)
        self.assertIn("appAdmin.permissionDomainHelp", self.html)
        self.assertIn("appAdmin.permissionEffectHelp", self.html)

    def test_library_advanced_panel_groups_existing_controls(self):
        self.assertIn('id="advancedSearchToggleButton"', self.html)
        self.assertIn('aria-controls="advancedSearchPanel"', self.html)
        self.assertIn('class="advanced-search-panel hidden" id="advancedSearchPanel"', self.html)
        self.assertIn('role="region" aria-labelledby="advancedSearchPanelTitle"', self.html)
        for group in ("release", "media", "personal", "smart"):
            self.assertIn(f'data-library-advanced-group="{group}"', self.html)

        for element_id in (
            "advancedYearFrom",
            "advancedYearTo",
            "advancedCrewQuery",
            "advancedDigitalFilter",
            "advancedArtworkFilter",
            "advancedContainerType",
            "advancedPersonalFilter",
            "advancedLocationFilter",
            "smartFilterSelect",
            "advancedSearchSaveButton",
            "advancedSearchDeleteButton",
            "advancedSearchResetButton",
            "advancedSearchApplyButton",
        ):
            self.assertEqual(self.html.count(f'id="{element_id}"'), 1, element_id)

    def test_library_bulk_panel_groups_existing_actions(self):
        self.assertIn('id="selectModeButton"', self.html)
        self.assertIn('aria-controls="bulkBar"', self.html)
        self.assertIn(
            'class="bulk-bar" id="bulkBar" role="region" aria-labelledby="bulkPanelTitle"',
            self.html,
        )
        for group in ("metadata", "groups", "tags", "containers", "location", "delete"):
            self.assertIn(f'data-library-bulk-group="{group}"', self.html)

        for element_id in (
            "bulkCount",
            "bulkClearSelection",
            "bulkGroupTarget",
            "bulkTagPicker",
            "bulkContainerTarget",
            "bulkContainerCreateForm",
            "bulkContainerCreateTitle",
            "bulkContainerCreateSubmit",
            "bulkContainerCreateMessage",
            "bulkContainerActionButton",
            "bulkLocationTarget",
            "bulkLocationActionButton",
        ):
            self.assertEqual(self.html.count(f'id="{element_id}"'), 1, element_id)
        for selection in ("all", "none", "clear"):
            self.assertEqual(self.html.count(f'data-bulk-select="{selection}"'), 1, selection)
        for action in (
            "metadata",
            "group-add",
            "group-remove",
            "tags-add",
            "tags-remove",
            "container",
            "location",
            "delete",
        ):
            self.assertIn(f'disabled data-bulk-action="{action}"', self.html, action)

    def test_library_action_groups_adapt_at_mobile_breakpoint(self):
        self.assertEqual(self.html.count("data-library-adaptive-group "), 10)
        self.assertIn("function syncLibraryAdaptiveGroups()", self.html)
        self.assertIn('window.matchMedia("(max-width: 760px)").matches', self.html)
        self.assertIn("groups.forEach((group) => { group.open = !mobile; });", self.html)
        self.assertIn('window.addEventListener("resize", syncLibraryAdaptiveGroups);', self.html)
        self.assertIn(".library-adaptive-group > summary:focus-visible {", self.html)
        self.assertIn(".bulk-target.danger {", self.html)

    def test_account_dashboard_preserves_existing_profile_bindings(self):
        self.assertIn('class="account-dashboard"', self.html)
        self.assertIn('id="profileAccountDisplayName"', self.html)
        for element_id in (
            "profileUsername",
            "profileRole",
            "profileUserCount",
            "profileCredentialCount",
            "profileEditForm",
            "profileDisplayNameInput",
            "profileAvatarForm",
            "profileAvatarFileInput",
            "profileSignOutButton",
        ):
            self.assertEqual(self.html.count(f'id="{element_id}"'), 1, element_id)

    def test_about_content_stays_inside_about_tab_panel(self):
        parser = _ElementAncestryParser()
        parser.feed(self.html)
        self.assertIn(
            "profilePanelAbout",
            parser.ancestors_by_id["profileOfflineStatus"],
        )


if __name__ == "__main__":
    unittest.main()
