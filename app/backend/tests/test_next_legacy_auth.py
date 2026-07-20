import base64
import json
import os
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from argon2 import PasswordHasher, Type
from cryptography.exceptions import InvalidTag
from flask import Flask


REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.backend import next_backup
from app.backend.next_auth import (
    _legacy_bootstrap_mfa_optional,
    _passkey_access_valid,
    _passkey_configuration_valid,
    _request_host_is_local_ip,
    _rp_origins,
)
from app.backend.next_legacy_auth import (
    PASSWORD_MIN_LENGTH,
    PasswordPolicyError,
    attempt_is_throttled,
    constant_time_text_equal,
    decrypt_totp_secret,
    encrypt_totp_secret,
    flow_token_hash,
    failed_attempt_state,
    generate_recovery_codes,
    hash_password,
    hash_recovery_code,
    identity_digest,
    ip_digest,
    legacy_auth_env_enabled,
    legacy_auth_effective,
    safe_audit_metadata,
    totp_code,
    totp_qr_data_uri,
    totp_uri,
    validate_password,
    verify_password,
    verify_recovery_code,
    verify_totp,
)


class LegacyAuthSecurityTests(unittest.TestCase):
    def setUp(self):
        self.secret_patch = mock.patch.dict(
            os.environ,
            {"JWT_SECRET": "unit-test-stable-secret", "LEGACY_AUTH_ENABLED": "true"},
            clear=True,
        )
        self.secret_patch.start()

    def tearDown(self):
        self.secret_patch.stop()

    def test_environment_gate_is_fail_closed(self):
        self.assertTrue(legacy_auth_env_enabled())
        self.assertTrue(legacy_auth_effective(True))
        self.assertFalse(legacy_auth_effective(False))
        for value in ("", "false", "0", "disabled"):
            with mock.patch.dict(os.environ, {"LEGACY_AUTH_ENABLED": value}):
                self.assertFalse(legacy_auth_env_enabled())
                self.assertFalse(legacy_auth_effective(True))

    def test_passkeys_require_explicit_non_placeholder_rp_configuration(self):
        invalid_configurations = (
            {"RP_ID": "", "RP_ORIGINS": ""},
            {"RP_ID": "localhost", "RP_ORIGINS": "http://localhost:6080"},
            {
                "RP_ID": "discvault.example.com",
                "RP_ORIGINS": "https://discvault.example.com",
            },
            {"RP_ID": "192.168.1.10", "RP_ORIGINS": "https://192.168.1.10"},
            {"RP_ID": "vault.home.arpa", "RP_ORIGINS": ""},
            {"RP_ID": "vault.home.arpa", "RP_ORIGINS": "http://vault.home.arpa"},
            {"RP_ID": "vault.home.arpa", "RP_ORIGINS": "https://other.home.arpa"},
            {"RP_ID": "https://vault.home.arpa", "RP_ORIGINS": "https://vault.home.arpa"},
        )
        for configuration in invalid_configurations:
            with self.subTest(configuration=configuration):
                with mock.patch.dict(os.environ, configuration, clear=False):
                    self.assertFalse(_passkey_configuration_valid())
        with mock.patch.dict(
            os.environ,
            {
                "RP_ID": "vault.home.arpa",
                "RP_ORIGINS": "https://vault.home.arpa,https://admin.vault.home.arpa",
            },
            clear=False,
        ):
            self.assertTrue(_passkey_configuration_valid())

    def test_passkey_access_and_local_mfa_exception_follow_request_host(self):
        app = Flask(__name__)
        placeholder_config = {
            "RP_ID": "localhost",
            "RP_ORIGINS": "http://localhost:6080",
            "LEGACY_AUTH_ENABLED": "true",
        }
        for base_url, remote_addr in (
            ("http://10.0.0.8:6080", "10.0.0.42"),
            ("http://172.16.4.2:6080", "172.16.4.3"),
            ("http://192.168.1.20:6080", "192.168.1.21"),
            ("http://127.0.0.1:6080", "127.0.0.1"),
            ("http://[::1]:6080", "::1"),
        ):
            with self.subTest(base_url=base_url):
                with mock.patch.dict(os.environ, placeholder_config, clear=False):
                    with app.test_request_context(
                        "/",
                        base_url=base_url,
                        environ_base={"REMOTE_ADDR": remote_addr},
                    ):
                        self.assertTrue(_request_host_is_local_ip())
                        self.assertTrue(_legacy_bootstrap_mfa_optional())
        with mock.patch.dict(os.environ, placeholder_config, clear=False):
            with app.test_request_context(
                "/",
                base_url="http://203.0.113.10:6080",
                environ_base={"REMOTE_ADDR": "203.0.113.11"},
            ):
                self.assertFalse(_request_host_is_local_ip())
                self.assertFalse(_legacy_bootstrap_mfa_optional())
            with app.test_request_context(
                "/",
                base_url="http://203.0.113.10:6080",
                headers={"X-Forwarded-Host": "192.168.1.20:6080"},
                environ_base={"REMOTE_ADDR": "203.0.113.11"},
            ):
                self.assertFalse(_request_host_is_local_ip())
                self.assertFalse(_legacy_bootstrap_mfa_optional())
            with app.test_request_context(
                "/",
                base_url="http://192.168.1.20:6080",
                environ_base={"REMOTE_ADDR": "203.0.113.11"},
            ):
                self.assertFalse(_request_host_is_local_ip())
                self.assertFalse(_legacy_bootstrap_mfa_optional())
            with app.test_request_context(
                "/",
                base_url="http://192.168.1.20:6080",
                headers={"X-Forwarded-For": "203.0.113.11"},
                environ_base={"REMOTE_ADDR": "192.168.1.2"},
            ):
                self.assertFalse(_request_host_is_local_ip())
                self.assertFalse(_legacy_bootstrap_mfa_optional())

        valid_config = {
            "RP_ID": "vault.home.arpa",
            "RP_ORIGINS": "https://vault.home.arpa",
            "LEGACY_AUTH_ENABLED": "true",
        }
        with mock.patch.dict(os.environ, valid_config, clear=False):
            with app.test_request_context(
                "/",
                base_url="https://vault.home.arpa",
                environ_base={"REMOTE_ADDR": "192.168.1.21"},
            ):
                self.assertTrue(_passkey_access_valid())
                self.assertFalse(_legacy_bootstrap_mfa_optional())
            with app.test_request_context(
                "/",
                base_url="http://192.168.1.20:6080",
                environ_base={"REMOTE_ADDR": "192.168.1.21"},
            ):
                self.assertFalse(_passkey_access_valid())
                self.assertFalse(_legacy_bootstrap_mfa_optional())

        canonical_config = {
            "RP_ID": "VAULT.HOME.ARPA",
            "RP_ORIGINS": "https://VAULT.HOME.ARPA:443",
            "LEGACY_AUTH_ENABLED": "true",
        }
        with mock.patch.dict(os.environ, canonical_config, clear=False):
            with app.test_request_context(
                "/",
                base_url="https://vault.home.arpa",
                environ_base={"REMOTE_ADDR": "192.168.1.21"},
            ):
                self.assertTrue(_passkey_configuration_valid())
                self.assertTrue(_passkey_access_valid())
                self.assertEqual(_rp_origins(), ["https://vault.home.arpa"])

    def test_constant_time_text_compare_accepts_unicode_input(self):
        self.assertTrue(constant_time_text_equal("cafe", "cafe"))
        self.assertFalse(constant_time_text_equal("café", "cafe"))

    def test_attempt_throttle_and_lockout_window(self):
        self.assertFalse(attempt_is_throttled(4, 19))
        self.assertTrue(attempt_is_throttled(5, 0))
        self.assertTrue(attempt_is_throttled(0, 20))
        now = datetime(2026, 7, 18, tzinfo=timezone.utc)
        count, first, locked_until = failed_attempt_state(
            4, now - timedelta(minutes=3), now
        )
        self.assertEqual(count, 5)
        self.assertEqual(first, now - timedelta(minutes=3))
        self.assertEqual(locked_until, now + timedelta(minutes=15))
        reset_count, reset_first, reset_lock = failed_attempt_state(
            99, now - timedelta(minutes=16), now
        )
        self.assertEqual((reset_count, reset_first, reset_lock), (1, now, None))

    def test_password_policy_is_length_only_plus_username_and_denylist(self):
        self.assertEqual(
            validate_password("spaces and punctuation !", "owner"),
            "spaces and punctuation !",
        )
        with self.assertRaises(PasswordPolicyError):
            validate_password("short", "owner")
        with self.assertRaises(PasswordPolicyError):
            validate_password("correcthorsebatterystaple", "owner")
        with self.assertRaises(PasswordPolicyError):
            validate_password("A-Username-Long", "a-username-long")
        self.assertEqual(PASSWORD_MIN_LENGTH, 15)

    def test_argon2id_hash_verify_invalid_and_rehash(self):
        password = "a private phrase with length"
        encoded = hash_password(password)
        self.assertTrue(encoded.startswith("$argon2id$"))
        self.assertTrue(verify_password(encoded, password).valid)
        self.assertFalse(verify_password(encoded, "wrong password value").valid)
        old = PasswordHasher(
            time_cost=1,
            memory_cost=8192,
            parallelism=1,
            hash_len=16,
            salt_len=16,
            type=Type.ID,
        ).hash(password)
        result = verify_password(old, password)
        self.assertTrue(result.valid)
        self.assertTrue(result.needs_rehash)

    def test_totp_matches_rfc_vector_window_and_replay_guard(self):
        # RFC 6238 SHA-1 seed; the RFC's 8-digit 94287082 becomes 287082 here.
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        self.assertEqual(totp_code(secret, at_time=59), "287082")
        accepted = verify_totp(secret, "287082", at_time=59)
        self.assertEqual(accepted, 1)
        self.assertIsNone(
            verify_totp(secret, "287082", at_time=59, last_accepted_step=accepted)
        )
        adjacent_code = totp_code(secret, step=2)
        self.assertEqual(verify_totp(secret, adjacent_code, at_time=59), 2)
        self.assertIsNone(verify_totp(secret, "１２３４５６", at_time=59))

    def test_totp_secret_is_aes_gcm_encrypted_and_qr_is_local(self):
        secret = "JBSWY3DPEHPK3PXP"
        nonce, ciphertext = encrypt_totp_secret(secret)
        self.assertNotIn(secret.encode("ascii"), ciphertext)
        self.assertEqual(decrypt_totp_secret(nonce, ciphertext), secret)
        with self.assertRaises(InvalidTag):
            decrypt_totp_secret(nonce, ciphertext, key_secret="another-secret")
        uri = totp_uri(secret, "owner")
        self.assertTrue(uri.startswith("otpauth://totp/DiscVault%3Aowner?"))
        qr_data_uri = totp_qr_data_uri(uri)
        self.assertTrue(qr_data_uri.startswith("data:image/svg+xml;base64,"))
        qr_svg = base64.b64decode(qr_data_uri.partition(",")[2]).decode("utf-8")
        self.assertIn('<path fill="#fff"', qr_svg)
        self.assertIn('stroke="#000"', qr_svg)

    def test_recovery_codes_are_random_hashed_and_single_value_verifiable(self):
        codes = generate_recovery_codes()
        self.assertEqual(len(codes), 10)
        self.assertEqual(len(set(codes)), 10)
        stored = hash_recovery_code(codes[0])
        self.assertNotIn(codes[0].replace("-", ""), stored)
        self.assertTrue(verify_recovery_code(stored, codes[0].lower()))
        self.assertFalse(verify_recovery_code(stored, codes[1]))
        self.assertFalse(verify_recovery_code(stored, "éééé-éééé-éééé-éééé"))

    def test_flow_attempt_digests_and_audit_redaction_hide_input(self):
        for digest in (
            flow_token_hash("flow-secret"),
            identity_digest("OwnerName"),
            ip_digest("192.0.2.10"),
        ):
            self.assertEqual(len(digest), 64)
            self.assertNotIn("secret", digest)
        clean = safe_audit_metadata(
            {
                "password": "bad",
                "totpSecret": "bad",
                "recovery_code": "bad",
                "flowToken": "bad",
                "mfaRequired": True,
            }
        )
        self.assertEqual(clean, {"mfaRequired": True})


class LegacyAuthContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth_source = (REPO_ROOT / "app/backend/next_auth.py").read_text(
            encoding="utf-8"
        )
        cls.backup_source = (REPO_ROOT / "app/backend/next_backup.py").read_text(
            encoding="utf-8"
        )
        cls.ui_source = (REPO_ROOT / "app/backend/next_views_ui.py").read_text(
            encoding="utf-8"
        )
        cls.collection_source = (
            REPO_ROOT / "app/backend/next_views_collection.py"
        ).read_text(encoding="utf-8")
        cls.app_source = (REPO_ROOT / "app/backend/next_app.py").read_text(
            encoding="utf-8"
        )
        cls.migration = (
            REPO_ROOT / "app/backend/migrations_next/041_legacy_auth.sql"
        ).read_text(encoding="utf-8")

    def test_migration_has_purpose_specific_tables_and_bounded_indexes(self):
        for table in (
            "legacy_password_credentials",
            "legacy_totp_credentials",
            "legacy_mfa_recovery_codes",
            "legacy_auth_flows",
            "legacy_auth_attempts",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", self.migration)
        self.assertIn("'legacy_password_login_enabled'", self.migration)
        self.assertIn("last_accepted_step", self.migration)
        self.assertIn("confirmed_at               timestamptz,", self.migration)
        self.assertNotIn("confirmed_at               timestamptz NOT NULL", self.migration)
        self.assertIn("identity_digest", self.migration)
        self.assertNotIn("plaintext", self.migration.lower())

    def test_routes_cover_bootstrap_password_mfa_admin_and_mobile_context(self):
        for route in (
            "/api/next/auth/legacy/bootstrap/start",
            "/api/next/auth/legacy/bootstrap/verify",
            "/api/next/auth/legacy/login",
            "/api/next/auth/legacy/password/change",
            "/api/next/auth/legacy/mfa/enroll/start",
            "/api/next/auth/legacy/mfa/setup",
            "/api/next/auth/legacy/mfa/setup/verify",
            "/api/next/auth/legacy/mfa/verify",
            "/api/next/auth/legacy/mfa/recovery",
            "/api/next/auth/legacy/settings/enable/options",
            "/api/next/auth/legacy/settings/enable/verify",
            "/legacy-credential",
        ):
            self.assertIn(route, self.auth_source)
        self.assertIn("pg_advisory_xact_lock", self.auth_source)
        self.assertIn("create_mobile_auth_code", self.auth_source)
        self.assertIn("native_login_response", self.auth_source)

    def test_passkey_policy_is_checked_in_options_and_verify(self):
        self.assertGreaterEqual(
            self.auth_source.count("not passkey_registration_allowed(conn, user_id)"),
            2,
        )

    def test_legacy_admin_routes_require_a_real_authenticated_admin(self):
        self.assertIn("def require_authenticated_admin(conn)", self.auth_source)
        create_start = self.auth_source.index("def create_legacy_user():")
        create_body = self.auth_source[create_start : create_start + 3500]
        self.assertIn("admin = require_authenticated_admin(conn)", create_body)
        manage_start = self.auth_source.index("def manage_legacy_credential(user_id: str):")
        manage_body = self.auth_source[manage_start : manage_start + 2500]
        self.assertIn("admin = require_authenticated_admin(conn)", manage_body)

    def test_totp_is_confirmed_only_after_recovery_codes_are_acknowledged(self):
        self.assertGreaterEqual(self.auth_source.count("VALUES (%s, %s, %s, NULL, %s)"), 2)
        ack_start = self.auth_source.index("def legacy_recovery_codes_ack():")
        ack_body = self.auth_source[ack_start : ack_start + 3500]
        self.assertIn("SET confirmed_at=now(), updated_at=now()", ack_body)
        self.assertIn("SET mfa_required=true, updated_at=now()", ack_body)

    def test_profile_mfa_enrollment_requires_password_and_reuses_guarded_flow(self):
        start = self.auth_source.index("def legacy_mfa_enroll_start():")
        body = self.auth_source[start : start + 5000]
        self.assertIn("if not _current_user_payload()", body)
        self.assertIn("user = current_user(conn)", body)
        self.assertIn('raise next_api_error("Unauthorized", 401)', body)
        self.assertIn(
            'credential.get("mfa_required") and credential.get("mfa_enrolled")',
            body,
        )
        self.assertIn("credential.get(\"must_change_password\")", body)
        self.assertIn("credential.get(\"locked_until\")", body)
        self.assertIn("credential.get(\"credential_expires_at\")", body)
        self.assertIn("verify_password(", body)
        self.assertIn("body.get(\"current_password\")", body)
        self.assertIn("credential_unavailable or not password_verification.valid", body)
        self.assertIn("recent_legacy_attempts(", body)
        self.assertIn("attempt_is_throttled(", body)
        self.assertIn("failed_attempt_state(", body)
        self.assertIn("record_legacy_attempt(", body)
        self.assertIn("conn.commit()", body)
        self.assertIn(
            'return issue_mfa_enrollment(conn, user, {"profileEnrollment": True})',
            body,
        )
        verify_start = self.auth_source.index("def legacy_mfa_setup_verify():")
        verify_body = self.auth_source[verify_start : verify_start + 3500]
        self.assertIn(
            '"profileEnrollment": flow["payload"].get("profileEnrollment") is True',
            verify_body,
        )
        ack_start = self.auth_source.index("def legacy_recovery_codes_ack():")
        ack_body = self.auth_source[ack_start : ack_start + 5000]
        self.assertIn('flow["payload"].get("profileEnrollment") is True', ack_body)
        self.assertIn('event_type="auth.legacy_mfa_enabled"', ack_body)
        profile_ack = ack_body.index('flow["payload"].get("profileEnrollment") is True')
        complete_login = ack_body.index("return legacy_complete_login(")
        self.assertLess(profile_ack, complete_login)
        self.assertIn('return response({"status": "ok", "stage": "complete"})', ack_body)

    def test_disabling_legacy_requires_an_active_owner_passkey(self):
        settings_start = self.auth_source.index("def legacy_settings():")
        settings_body = self.auth_source[settings_start : settings_start + 1800]
        self.assertIn("active_owner_passkey_count(conn) == 0", settings_body)

    def test_review_login_uses_shared_flow_and_reconciles_media_viewer(self):
        review_start = self.auth_source.index("def review_login():")
        review_body = self.auth_source[review_start : review_start + 250]
        self.assertIn("return legacy_login()", review_body)
        self.assertIn("def reconcile_review_legacy_user", self.auth_source)
        self.assertIn('assign_role(conn, reviewer["id"], "media_viewer")', self.auth_source)
        self.assertIn("mfa_required=false", self.auth_source)

    def test_backup_exports_only_password_policy_and_forces_mfa_reenrollment(self):
        specs = {spec.name: spec for spec in next_backup.USER_ACCOUNT_BACKUP_TABLE_SPECS}
        self.assertIn("legacy_password_credentials", specs)
        self.assertNotIn("legacy_totp_credentials", specs)
        self.assertNotIn("legacy_mfa_recovery_codes", specs)
        columns = specs["legacy_password_credentials"].columns
        self.assertIn("password_hash", columns)
        self.assertIn("mfa_required", columns)
        self.assertNotIn("encrypted_secret", columns)
        self.assertIn(
            'DELETE FROM legacy_totp_credentials WHERE user_id = ANY(%s)',
            self.backup_source,
        )
        self.assertIn("SET failed_attempt_count=0, first_failed_at=NULL", self.backup_source)

    def test_active_ui_surfaces_use_shared_legacy_login(self):
        self.assertIn("/api/next/auth/legacy/login", self.ui_source)
        self.assertIn("/api/next/auth/legacy/login", self.collection_source)
        self.assertIn("startupLegacyButton", self.ui_source)
        self.assertIn("appAdminLegacyUserForm", self.ui_source)
        self.assertIn("profileLegacyPasswordForm", self.ui_source)
        self.assertIn(
            'id="appLoginButton" data-next-i18n="auth.loginDescription"',
            self.ui_source,
        )
        registration_start = self.ui_source.index(
            "function renderAppRegistrationMode(auth)"
        )
        registration_body = self.ui_source[
            registration_start : registration_start + 5000
        ]
        self.assertIn(
            'reviewLoginAvailable ? "legacyAuth.passkeyRecommended" : "auth.loginDescription"',
            registration_body,
        )
        self.assertGreaterEqual(
            self.ui_source.count('class="legacy-checkbox-row'), 6
        )
        self.assertIn(
            'label.legacy-checkbox-row input[type="checkbox"]',
            self.ui_source,
        )
        self.assertIn(
            '"passkey_configuration_valid": passkey_configuration_valid',
            self.auth_source,
        )
        self.assertIn('"passkey_access_valid": passkey_access_valid', self.auth_source)
        self.assertIn(
            '"legacy_bootstrap_mfa_optional": bool(',
            self.auth_source,
        )
        self.assertIn(
            'mfa_required = body.get("mfa_enabled") is True if mfa_optional else True',
            self.auth_source,
        )
        self.assertNotIn(
            'if not mfa_optional and body.get("password_risk_accepted") is not True:',
            self.auth_source,
        )
        self.assertIn("mfa_required=False", self.auth_source)
        self.assertIn("function passkeyProcessAvailable()", self.ui_source)
        self.assertIn("function passkeyConfigurationGuidanceVisible()", self.ui_source)
        self.assertIn(
            "return Boolean(currentAuthStatus.passkey_access_valid);",
            self.ui_source,
        )
        self.assertIn(
            "|| !Boolean(currentAuthStatus.request_host_is_local_ip)",
            self.ui_source,
        )
        self.assertIn("const passkeyOnboardingAvailable", self.ui_source)
        self.assertIn("legacyBootstrap && browserPasskeysUnsupported", self.ui_source)
        self.assertIn('id="startupAuthGuidance"', self.ui_source)
        self.assertIn('id="appAuthGuidance"', self.ui_source)
        self.assertIn('href="https://docs.discvault.eu"', self.ui_source)
        self.assertIn('id="startupLegacyMfaEnabled"', self.ui_source)
        self.assertNotIn('id="startupLegacyRiskOption"', self.ui_source)
        self.assertNotIn('id="startupLegacyRiskAccepted"', self.ui_source)
        self.assertNotIn("password_risk_accepted:", self.ui_source)
        self.assertIn(
            "mfa_enabled: !startupLegacyMfaOptional",
            self.ui_source,
        )
        self.assertNotIn('id="appLoginDescription"', self.ui_source)
        self.assertIn(
            'id="appReviewToggleButton" data-next-i18n="auth.signIn">Sign in</button>',
            self.ui_source,
        )
        self.assertIn(
            'id="appReviewLoginButton" data-next-i18n="auth.signIn">Sign in</button>',
            self.ui_source,
        )
        self.assertIn(
            'labels[legacyStage] || tNext("auth.signIn", "Sign in")',
            self.ui_source,
        )
        self.assertNotIn(
            'setLoginMessage(tNext("auth.loginDescription", "Log in with your Passkey"))',
            self.ui_source,
        )
        self.assertIn("function passkeyProcessAvailable()", self.collection_source)
        self.assertIn(
            "function passkeyConfigurationGuidanceVisible()",
            self.collection_source,
        )
        self.assertIn(
            "|| !Boolean(authState.request_host_is_local_ip)",
            self.collection_source,
        )
        self.assertIn(
            "(setupRequired || passkeyConfigurationGuidanceVisible())",
            self.collection_source,
        )

    def test_totp_challenge_only_accepts_an_authenticator_code(self):
        self.assertNotIn('id="appLegacyRecoveryCodeToggle"', self.ui_source)
        self.assertNotIn('id="appLegacyRecoveryCodeField"', self.ui_source)
        self.assertNotIn('id="appLegacyRecoveryCode"', self.ui_source)
        self.assertNotIn("function setLegacyRecoveryCodeExpanded(expanded)", self.ui_source)
        self.assertIn('const mfaChallenge = legacyStage === "mfa_challenge";', self.ui_source)
        self.assertIn(
            'document.getElementById("appRecoveryToggleButton")?.classList.toggle("hidden", mfaChallenge);',
            self.ui_source,
        )
        self.assertIn(
            'url = "/api/next/auth/legacy/mfa/verify";',
            self.ui_source,
        )
        self.assertNotIn('"/api/next/auth/legacy/mfa/recovery"', self.ui_source)

    def test_owner_onboarding_is_method_neutral_and_validates_confirmation(self):
        self.assertIn(
            'id="startupLegacyButton" data-next-i18n="legacyAuth.setupOwner">Start</button>',
            self.ui_source,
        )
        self.assertIn('id="startupOwnerUsernameField"', self.ui_source)
        self.assertIn('id="startupLegacyPasswordConfirm"', self.ui_source)
        self.assertIn(
            'if (password !== passwordConfirmation) {',
            self.ui_source,
        )
        self.assertIn(
            'tNext("legacyAuth.passwordMismatch", "Passwords do not match.")',
            self.ui_source,
        )
        self.assertIn("width: 100%;", self.ui_source)
        self.assertIn("overflow-wrap: anywhere;", self.ui_source)
        self.assertIn(
            'steps.classList.toggle("hidden", phase === "owner_setup");',
            self.ui_source,
        )
        self.assertIn(
            'tNext("startup.helloUser", "Hello, {username}")',
            self.ui_source,
        )
        self.assertIn(
            'document.getElementById("startupOwnerUsernameField")?.classList.toggle("hidden", legacyWizardOpen);',
            self.ui_source,
        )
        self.assertIn(
            'document.getElementById("startupRefreshButton")?.classList.toggle("hidden", !refreshPhases.has(phase));',
            self.ui_source,
        )
        self.assertIn(
            'document.getElementById("startupLogoutButton")?.classList.toggle("hidden", !currentAuthStatus.authenticated);',
            self.ui_source,
        )
        self.assertIn(
            'message.textContent = ownerPasskeyMessage || (phase === "owner_setup" ? "" : startup.message || "");',
            self.ui_source,
        )
        self.assertIn(
            'id="authReviewButton" class="hidden" data-next-i18n="auth.signIn">Sign in</button>',
            self.collection_source,
        )
        self.assertIn('tNext("auth.signIn", "Sign in")', self.collection_source)
        self.assertIn(
            "renderPasskeyConfigurationGuidance(description);",
            self.collection_source,
        )
        self.assertGreaterEqual(
            self.auth_source.count("require_passkey_access()"),
            9,
        )
        for route_function in (
            "def register_options():",
            "def register_verify():",
            "def login_options():",
            "def login_verify():",
            "def ownership_transfer_options():",
            "def ownership_transfer_verify():",
            "def legacy_settings_enable_options():",
            "def legacy_settings_enable_verify():",
        ):
            route_start = self.auth_source.index(route_function)
            route_body = self.auth_source[route_start : route_start + 300]
            self.assertIn("require_passkey_access()", route_body)
        for route_function in (
            "def legacy_migration_auth_options():",
            "def legacy_migration_auth_verify():",
        ):
            route_start = self.app_source.index(route_function)
            route_body = self.app_source[route_start : route_start + 400]
            self.assertIn("_passkey_access_valid()", route_body)
        register_verify_start = self.auth_source.index("def register_verify():")
        register_verify_body = self.auth_source[
            register_verify_start : register_verify_start + 6500
        ]
        self.assertIn(
            "pg_advisory_xact_lock(hashtext('discvault-legacy-bootstrap'))",
            register_verify_body,
        )
        self.assertIn("LEGACY_BOOTSTRAP_PENDING_STATUS", self.auth_source)
        self.assertIn(
            '"recoveryCodesAcknowledged": True',
            self.auth_source,
        )
        admin_visibility_start = self.ui_source.index(
            "function renderAppAdminVisibility()"
        )
        admin_visibility = self.ui_source[
            admin_visibility_start : admin_visibility_start + 300
        ]
        self.assertIn(
            "const allowed = isNativeAdminUser() && canUseAppAdmin();",
            admin_visibility,
        )
        self.assertIn("function clearProfileRecoveryCodes()", self.ui_source)
        self.assertIn(
            'if (route !== "profile") clearProfileRecoveryCodes();',
            self.ui_source,
        )
        self.assertIn(
            'if (selected !== "security") clearProfileRecoveryCodes();',
            self.ui_source,
        )
        self.assertIn('class="profile-security-column"', self.ui_source)
        self.assertIn(
            'data-next-i18n="legacyAuth.passwordSecurity">Legacy security',
            self.ui_source,
        )
        self.assertIn(
            'data-next-i18n="legacyAuth.mfaStatus">2FA status',
            self.ui_source,
        )
        self.assertNotIn('id="profileLegacyRecoveryCount"', self.ui_source)
        self.assertIn(
            "Recovery codes let you regain access when your usual sign-in methods are unavailable.",
            self.ui_source,
        )
        self.assertGreaterEqual(
            self.auth_source.count("next_replace_recovery_codes("),
            3,
        )
        self.assertGreaterEqual(
            self.auth_source.count("next_consume_recovery_code("),
            3,
        )
        backup_specs = {
            spec.name: spec for spec in next_backup.USER_ACCOUNT_BACKUP_TABLE_SPECS
        }
        self.assertIn(
            "legacy_code_hash",
            backup_specs["recovery_codes"].columns,
        )
        self.assertLess(
            self.ui_source.index("async function copyLegacyCodes(codes)"),
            self.ui_source.index("async function runStartupLegacyBootstrap()"),
        )

    def test_deployment_gate_defaults_false_everywhere(self):
        files = (
            "app/.env.example",
            "app/deploy/next/.env.example",
            "app/docker-compose.next.yml",
            "app/deploy/next/docker-compose.yml",
            "app/deploy/unraid/discvault.xml",
        )
        for relative in files:
            text = (REPO_ROOT / relative).read_text(encoding="utf-8-sig")
            self.assertIn("LEGACY_AUTH_ENABLED", text, relative)
            self.assertIn("false", text.lower(), relative)

    def test_every_locale_contains_nonempty_legacy_values(self):
        locale_dir = REPO_ROOT / "app/frontend/i18n/next"
        english = json.loads((locale_dir / "en-US.json").read_text(encoding="utf-8"))
        legacy_keys = {key for key in english if key.startswith("legacyAuth.")}
        self.assertGreaterEqual(len(legacy_keys), 50)
        for path in locale_dir.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            self.assertEqual(legacy_keys - set(data), set(), path.name)
            self.assertTrue(all(str(data[key]).strip() for key in legacy_keys), path.name)


if __name__ == "__main__":
    unittest.main()
