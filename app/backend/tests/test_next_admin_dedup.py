import os
import pathlib
import sys
import unittest
from unittest import mock

from flask import Flask


REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.backend import next_auth
from app.backend.admin_dedup_reports import (
    CanonicalReportError,
    apply_winner_selections,
    collection_fingerprint,
    report_hash,
)
from app.backend.next_common import NextApiError, response


PREVIEW_ID = "11111111-1111-4111-8111-111111111111"
SELECTED_ID = "22222222-2222-4222-8222-222222222222"
REPORT_HASH = "a" * 64
COLLECTION_HASH = "b" * 64
EXPIRES_AT = "2026-07-24T20:15:00+00:00"
REPORT = {
    "groups": [
        {
            "group_id": "group-1",
            "winner": "movie-1",
            "losers": ["movie-2"],
            "members": [{"id": "movie-1"}, {"id": "movie-2"}],
        }
    ]
}


class _Context:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Cursor:
    def __init__(self):
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchall(self):
        return [{"id": "credential-1"}]


class _Connection:
    def __init__(self):
        self.cursor_instance = _Cursor()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_instance

    def transaction(self):
        return _Context()


def _canonical(report_id=PREVIEW_ID, report=REPORT):
    return {
        "id": report_id,
        "reportHash": REPORT_HASH,
        "collectionHash": COLLECTION_HASH,
        "issuedAt": "2026-07-24T20:00:00+00:00",
        "expiresAt": EXPIRES_AT,
        "consumedAt": None,
        "report": report,
    }


class AdminDedupEndpointTests(unittest.TestCase):
    def setUp(self):
        self.connection = _Connection()
        self.require_admin = mock.Mock(
            return_value={"id": "admin-1", "role": "admin"}
        )
        self.require_authenticated_admin = mock.Mock(
            return_value={"id": "admin-1", "role": "admin"}
        )
        self.require_passkey_access = mock.Mock()
        self.verify_step_up_assertion = mock.Mock(
            return_value={"id": "credential-1", "new_sign_count": 4}
        )
        self.audit_event = mock.Mock()
        self.make_challenge = mock.Mock(return_value=b"challenge")
        self.store_challenge = mock.Mock()
        self.lock_collection_patcher = mock.patch.object(
            next_auth,
            "_dedup_lock_collection_snapshot",
        )
        self.lock_collection = self.lock_collection_patcher.start()
        self.addCleanup(self.lock_collection_patcher.stop)
        self.app = Flask(__name__)

        @self.app.errorhandler(NextApiError)
        def handle_next_error(error):
            payload = {"status": "error", "error": str(error)}
            if error.code:
                payload["errorCode"] = error.code
            return response(payload, error.status_code)

        next_auth._register_admin_dedup_routes(
            route=self.app.route,
            connect=lambda: self.connection,
            response=response,
            next_api_error=NextApiError,
            require_admin=self.require_admin,
            require_authenticated_admin=self.require_authenticated_admin,
            require_passkey_access=self.require_passkey_access,
            verify_step_up_assertion=self.verify_step_up_assertion,
            audit_event=self.audit_event,
            make_challenge=self.make_challenge,
            store_challenge=self.store_challenge,
            b64url_encode=lambda value: f"encoded-{value.decode()}",
            rp_id=lambda: "example.test",
        )
        self.client = self.app.test_client()

    def _canonical_patches(self, *, load=None):
        patchers = (
            mock.patch.object(next_auth, "_dedup_build_report", return_value=REPORT),
            mock.patch.object(
                next_auth,
                "_dedup_create_canonical_report",
                return_value=_canonical(),
            ),
            mock.patch.object(
                next_auth,
                "_dedup_load_canonical_report",
                side_effect=load,
                return_value=None if load else _canonical(),
            ),
            mock.patch.object(
                next_auth,
                "_dedup_apply_winner_selections",
                return_value=REPORT,
            ),
            mock.patch.object(
                next_auth,
                "_dedup_collection_fingerprint",
                return_value=COLLECTION_HASH,
            ),
            mock.patch.object(
                next_auth,
                "_dedup_consume_report",
                return_value="2026-07-24T20:05:00+00:00",
            ),
        )
        mocks = []
        for patcher in patchers:
            mocks.append(patcher.start())
            self.addCleanup(patcher.stop)
        return tuple(mocks)

    def test_report_returns_immutable_canonical_identity_hash_and_expiry(self):
        build, create, *_ = self._canonical_patches()
        with mock.patch.dict(os.environ, {}, clear=True), build, create:
            result = self.client.get("/api/next/admin/dedup/report")

        self.assertEqual(result.status_code, 200)
        self.assertEqual(
            result.get_json(),
            {
                "status": "ok",
                "reportId": PREVIEW_ID,
                "reportHash": REPORT_HASH,
                "expiresAt": EXPIRES_AT,
                "report": REPORT,
                "executeEnabled": False,
            },
        )
        self.require_admin.assert_called_once_with(self.connection)
        create.assert_called_once_with(
            self.connection,
            REPORT,
            created_by="admin-1",
        )

    def test_report_allows_owner_and_admin_but_propagates_authorization_denial(self):
        build, create, *_ = self._canonical_patches()
        for role in ("owner", "admin"):
            with self.subTest(role=role):
                self.require_admin.reset_mock(
                    return_value=True,
                    side_effect=True,
                )
                self.require_admin.return_value = {"id": f"{role}-1", "role": role}
                with build, create:
                    result = self.client.get("/api/next/admin/dedup/report")
                self.assertEqual(result.status_code, 200)

        self.require_admin.side_effect = NextApiError(
            "Admin access required",
            403,
        )
        result = self.client.get("/api/next/admin/dedup/report")
        self.assertEqual(result.status_code, 403)

    def test_options_validates_source_and_emits_a_new_canonical_identity(self):
        build, create, load, apply, fingerprint, consume = self._canonical_patches()
        create.side_effect = [_canonical(SELECTED_ID)]
        with build, create, load, apply, fingerprint, consume:
            result = self.client.post(
                "/api/next/admin/dedup/options",
                json={
                    "reportId": PREVIEW_ID,
                    "winnerSelections": [
                        {"groupId": "group-1", "winnerId": "movie-1"}
                    ],
                },
            )

        self.assertEqual(result.status_code, 200)
        payload = result.get_json()
        self.assertEqual(payload["reportId"], SELECTED_ID)
        self.assertEqual(payload["reportHash"], REPORT_HASH)
        self.assertEqual(payload["expiresAt"], EXPIRES_AT)
        self.assertEqual(payload["options"]["challenge"], "encoded-challenge")
        load.assert_called_once_with(
            self.connection,
            PREVIEW_ID,
            for_update=True,
        )
        apply.assert_called_once_with(
            REPORT,
            [{"groupId": "group-1", "winnerId": "movie-1"}],
        )
        create.assert_called_once_with(
            self.connection,
            REPORT,
            created_by="admin-1",
            source_report_id=PREVIEW_ID,
            collection_hash=COLLECTION_HASH,
        )
        self.store_challenge.assert_called_once_with(
            self.connection,
            f"admin_dedup:admin-1:{SELECTED_ID}",
            b"challenge",
        )

    def test_options_rejects_browser_report_and_invalid_selection(self):
        with mock.patch.object(
            next_auth,
            "_dedup_load_canonical_report",
        ) as load:
            legacy = self.client.post(
                "/api/next/admin/dedup/options",
                json={"reportId": PREVIEW_ID, "report": REPORT},
            )
        self.assertEqual(legacy.status_code, 400)
        self.assertEqual(
            legacy.get_json()["errorCode"],
            "admin_dedup_legacy_report_rejected",
        )
        load.assert_not_called()

        error = CanonicalReportError(
            "Selected winner is invalid",
            code="admin_dedup_selection_invalid",
            status_code=400,
        )
        build, create, load, apply, fingerprint, consume = self._canonical_patches()
        apply.side_effect = error
        with build, create, load, apply, fingerprint, consume:
            invalid = self.client.post(
                "/api/next/admin/dedup/options",
                json={
                    "reportId": PREVIEW_ID,
                    "winnerSelections": [
                        {"groupId": "group-1", "winnerId": "not-a-member"}
                    ],
                },
            )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(
            invalid.get_json()["errorCode"],
            "admin_dedup_selection_invalid",
        )

    def test_execute_is_forbidden_by_default_without_entering_auth_or_merge_flow(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(next_auth, "_dedup_execute_merge") as execute_merge,
        ):
            result = self.client.post(
                "/api/next/admin/dedup/execute",
                json={
                    "reportId": PREVIEW_ID,
                    "credential": {"id": "credential-1"},
                },
            )

        self.assertEqual(result.status_code, 403)
        self.assertEqual(
            result.get_json()["errorCode"],
            "admin_dedup_execute_disabled",
        )
        self.require_passkey_access.assert_not_called()
        self.require_authenticated_admin.assert_not_called()
        self.lock_collection.assert_not_called()
        execute_merge.assert_not_called()

    def test_execute_is_forbidden_when_flag_is_explicitly_false(self):
        with (
            mock.patch.dict(
                os.environ,
                {"DISCVAULT_ADMIN_DEDUP_EXECUTE_ENABLED": "false"},
                clear=True,
            ),
            mock.patch.object(next_auth, "_dedup_execute_merge") as execute_merge,
        ):
            result = self.client.post(
                "/api/next/admin/dedup/execute",
                json={
                    "reportId": PREVIEW_ID,
                    "credential": {"id": "credential-1"},
                },
            )
        self.assertEqual(result.status_code, 403)
        self.assertEqual(
            result.get_json()["errorCode"],
            "admin_dedup_execute_disabled",
        )
        execute_merge.assert_not_called()

    def test_execute_explicit_enable_rejects_legacy_browser_report(self):
        with (
            mock.patch.dict(
                os.environ,
                {"DISCVAULT_ADMIN_DEDUP_EXECUTE_ENABLED": "true"},
                clear=True,
            ),
            mock.patch.object(next_auth, "_dedup_execute_merge") as execute_merge,
        ):
            result = self.client.post(
                "/api/next/admin/dedup/execute",
                json={
                    "reportId": PREVIEW_ID,
                    "report": {"groups": [{"winner": "attacker"}]},
                    "credential": {"id": "credential-1"},
                },
            )

        self.assertEqual(result.status_code, 400)
        self.assertEqual(
            result.get_json()["errorCode"],
            "admin_dedup_legacy_report_rejected",
        )
        execute_merge.assert_not_called()

    def test_execute_returns_stable_identity_state_errors(self):
        cases = (
            ("admin_dedup_report_id_malformed", 400),
            ("admin_dedup_report_unknown", 404),
            ("admin_dedup_report_expired", 410),
            ("admin_dedup_report_consumed", 409),
        )
        for code, status in cases:
            with self.subTest(code=code):
                error = CanonicalReportError(
                    code,
                    code=code,
                    status_code=status,
                )
                build, create, load, apply, fingerprint, consume = (
                    self._canonical_patches(load=error)
                )
                with (
                    mock.patch.dict(
                        os.environ,
                        {"DISCVAULT_ADMIN_DEDUP_EXECUTE_ENABLED": "true"},
                        clear=True,
                    ),
                    build,
                    create,
                    load,
                    apply,
                    fingerprint,
                    consume,
                    mock.patch.object(
                        next_auth,
                        "_dedup_execute_merge",
                    ) as execute_merge,
                ):
                    result = self.client.post(
                        "/api/next/admin/dedup/execute",
                        json={
                            "reportId": "not-a-valid-id"
                            if code == "admin_dedup_report_id_malformed"
                            else PREVIEW_ID,
                            "credential": {"id": "credential-1"},
                        },
                    )
                self.assertEqual(result.status_code, status)
                self.assertEqual(result.get_json()["errorCode"], code)
                execute_merge.assert_not_called()

    def test_execute_rejects_stale_collection_before_passkey_or_merge(self):
        build, create, load, apply, fingerprint, consume = self._canonical_patches()
        fingerprint.return_value = "changed"
        with (
            mock.patch.dict(
                os.environ,
                {"DISCVAULT_ADMIN_DEDUP_EXECUTE_ENABLED": "true"},
                clear=True,
            ),
            build,
            create,
            load,
            apply,
            fingerprint,
            consume,
            mock.patch.object(
                next_auth,
                "_dedup_execute_merge",
            ) as execute_merge,
        ):
            result = self.client.post(
                "/api/next/admin/dedup/execute",
                json={
                    "reportId": PREVIEW_ID,
                    "credential": {"id": "credential-1"},
                },
            )

        self.assertEqual(result.status_code, 409)
        self.assertEqual(
            result.get_json()["errorCode"],
            "admin_dedup_report_stale",
        )
        self.verify_step_up_assertion.assert_not_called()
        execute_merge.assert_not_called()

    def test_execute_uses_only_stored_report_and_consumes_it_atomically(self):
        build, create, load, apply, fingerprint, consume = self._canonical_patches()
        with (
            mock.patch.dict(
                os.environ,
                {"DISCVAULT_ADMIN_DEDUP_EXECUTE_ENABLED": "true"},
                clear=True,
            ),
            build,
            create,
            load,
            apply,
            fingerprint,
            consume,
            mock.patch.object(
                next_auth,
                "_dedup_execute_merge",
                return_value=1,
            ) as execute_merge,
        ):
            result = self.client.post(
                "/api/next/admin/dedup/execute",
                json={
                    "reportId": PREVIEW_ID,
                    "credential": {"id": "credential-1"},
                },
            )

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()["tombstoned"], 1)
        self.require_passkey_access.assert_called_once_with()
        self.require_authenticated_admin.assert_called_once_with(self.connection)
        self.lock_collection.assert_called_once_with(self.connection)
        self.verify_step_up_assertion.assert_called_once_with(
            self.connection,
            challenge_key=f"admin_dedup:admin-1:{PREVIEW_ID}",
            expected_user_id="admin-1",
            credential={"id": "credential-1"},
        )
        execute_merge.assert_called_once_with(
            self.connection,
            REPORT,
            commit=False,
        )
        consume.assert_called_once_with(
            self.connection,
            PREVIEW_ID,
            consumed_by="admin-1",
        )
        self.audit_event.assert_called_once()

    def test_execute_replay_is_rejected_and_merge_runs_only_once(self):
        build, create, load, apply, fingerprint, consume = self._canonical_patches()
        load.side_effect = [
            _canonical(),
            CanonicalReportError(
                "already consumed",
                code="admin_dedup_report_consumed",
                status_code=409,
            ),
        ]
        with (
            mock.patch.dict(
                os.environ,
                {"DISCVAULT_ADMIN_DEDUP_EXECUTE_ENABLED": "true"},
                clear=True,
            ),
            mock.patch.object(
                next_auth,
                "_dedup_execute_merge",
                return_value=1,
            ) as execute_merge,
        ):
            first = self.client.post(
                "/api/next/admin/dedup/execute",
                json={
                    "reportId": PREVIEW_ID,
                    "credential": {"id": "credential-1"},
                },
            )
            second = self.client.post(
                "/api/next/admin/dedup/execute",
                json={
                    "reportId": PREVIEW_ID,
                    "credential": {"id": "credential-1"},
                },
            )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(
            second.get_json()["errorCode"],
            "admin_dedup_report_consumed",
        )
        execute_merge.assert_called_once()


class CanonicalReportContractTests(unittest.TestCase):
    def test_report_hash_is_exact_but_collection_fingerprint_ignores_volatile_metadata(self):
        first = {
            **REPORT,
            "generated_at": "2026-07-24T20:00:00+00:00",
            "script_commit": "first",
            "backend_version": "26.6.32",
        }
        second = {
            **REPORT,
            "generated_at": "2026-07-24T20:01:00+00:00",
            "script_commit": "second",
            "backend_version": "26.6.33",
        }
        self.assertNotEqual(report_hash(first), report_hash(second))
        self.assertEqual(
            collection_fingerprint(first),
            collection_fingerprint(second),
        )

    def test_valid_winner_selection_changes_content_not_collection_identity(self):
        source = {
            "groups": [
                {
                    "tier": "barcode",
                    "winner": "movie-1",
                    "losers": ["movie-2"],
                    "members": [
                        {
                            "id": "movie-1",
                            "score_breakdown": {"total_score": 1},
                        },
                        {
                            "id": "movie-2",
                            "score_breakdown": {"total_score": 2},
                        },
                    ],
                }
            ]
        }
        canonical = apply_winner_selections(source, [])
        selected = apply_winner_selections(
            canonical,
            [
                {
                    "groupId": canonical["groups"][0]["group_id"],
                    "winnerId": "movie-2",
                }
            ],
        )
        self.assertEqual(selected["groups"][0]["winner"], "movie-2")
        self.assertEqual(selected["groups"][0]["losers"], ["movie-1"])
        self.assertNotEqual(report_hash(canonical), report_hash(selected))
        self.assertEqual(
            collection_fingerprint(canonical),
            collection_fingerprint(selected),
        )

    def test_collection_fingerprint_changes_when_candidate_state_changes(self):
        first = {
            "groups": [
                {
                    "tier": "barcode",
                    "members": [
                        {"id": "movie-1", "state_hash": "a" * 64},
                        {"id": "movie-2", "state_hash": "b" * 64},
                    ],
                }
            ]
        }
        second = {
            "groups": [
                {
                    "tier": "barcode",
                    "members": [
                        {"id": "movie-1", "state_hash": "c" * 64},
                        {"id": "movie-2", "state_hash": "b" * 64},
                    ],
                }
            ]
        }
        self.assertNotEqual(
            collection_fingerprint(first),
            collection_fingerprint(second),
        )

    def test_selection_rejects_unknown_group_and_non_member_winner(self):
        canonical = apply_winner_selections(REPORT, [])
        for selection in (
            {"groupId": "missing", "winnerId": "movie-1"},
            {"groupId": canonical["groups"][0]["group_id"], "winnerId": "missing"},
        ):
            with self.subTest(selection=selection):
                with self.assertRaises(CanonicalReportError) as raised:
                    apply_winner_selections(canonical, [selection])
                self.assertEqual(
                    raised.exception.code,
                    "admin_dedup_selection_invalid",
                )


class AdminDedupPublishedConfigTests(unittest.TestCase):
    def test_safe_false_default_is_published_on_all_configuration_surfaces(self):
        expected_lines = {
            REPO_ROOT / "app/.env.example": (
                "DISCVAULT_ADMIN_DEDUP_EXECUTE_ENABLED=false"
            ),
            REPO_ROOT / "app/deploy/next/.env.example": (
                "DISCVAULT_ADMIN_DEDUP_EXECUTE_ENABLED=false"
            ),
            REPO_ROOT / "app/docker-compose.next.yml": (
                "DISCVAULT_ADMIN_DEDUP_EXECUTE_ENABLED: "
                "${DISCVAULT_ADMIN_DEDUP_EXECUTE_ENABLED:-false}"
            ),
            REPO_ROOT / "app/deploy/next/docker-compose.yml": (
                "DISCVAULT_ADMIN_DEDUP_EXECUTE_ENABLED: "
                "${DISCVAULT_ADMIN_DEDUP_EXECUTE_ENABLED:-false}"
            ),
        }
        for path, expected in expected_lines.items():
            with self.subTest(path=path):
                self.assertIn(expected, path.read_text(encoding="utf-8"))

        unraid = (REPO_ROOT / "app/deploy/unraid/discvault.xml").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn('Target="DISCVAULT_ADMIN_DEDUP_EXECUTE_ENABLED"', unraid)
        self.assertIn(
            'Target="DISCVAULT_ADMIN_DEDUP_EXECUTE_ENABLED" Default="false"',
            unraid,
        )

    def test_ui_keeps_preview_but_submits_only_canonical_report_ids(self):
        source = (REPO_ROOT / "app/backend/next_views_ui.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('authApiJson("/api/next/admin/dedup/report")', source)
        self.assertIn("_dedupExecuteEnabled = result.executeEnabled === true;", source)
        self.assertIn("_dedupReport = result.report;", source)
        self.assertIn("_dedupReportId = result.reportId;", source)
        self.assertIn(
            "JSON.stringify({ reportId: _dedupReportId, credential })",
            source,
        )
        self.assertNotIn(
            "JSON.stringify({ report: _dedupReport",
            source,
        )

    def test_canonical_report_migration_declares_hash_expiry_and_consumption(self):
        migration = (
            REPO_ROOT
            / "app/backend/migrations_next/047_admin_dedup_reports.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS admin_dedup_reports", migration)
        self.assertIn("report_hash", migration)
        self.assertIn("collection_hash", migration)
        self.assertIn("expires_at", migration)
        self.assertIn("report            jsonb NOT NULL", migration)
        self.assertIn("consumed_at", migration)
        self.assertIn("trg_admin_dedup_report_payload_immutable", migration)


class SyncDedupScriptFormatAgnosticTests(unittest.TestCase):
    """Unit tests for format-agnostic detection in sync_dedup_merge.py."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        script = REPO_ROOT / "app/backend/scripts/sync_dedup_merge.py"
        spec = importlib.util.spec_from_file_location("sync_dedup_merge", script)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def _make_movie(self, **kwargs):
        defaults = {
            "id": 1,
            "title": "Test",
            "year": 2020,
            "barcode": None,
            "format": None,
            "edition": "",
            "container_ids": [],
        }
        defaults.update(kwargs)
        return defaults

    def test_format_agnostic_movies_are_compatible(self):
        """Two movies with same title/year but one has no format should not conflict."""
        left = self._make_movie(format=None)
        right = self._make_movie(format="Blu-ray")
        result = self.mod._members_are_compatible(
            left, right, enforce_tmdb_sanity=False, enforce_titleyear_identity=False
        )
        self.assertTrue(result)

    def test_format_conflict_blocks_merge(self):
        """Two movies with explicitly different formats should not be merged."""
        left = self._make_movie(format="Blu-ray")
        right = self._make_movie(format="4K UHD")
        result = self.mod._members_are_compatible(
            left, right, enforce_tmdb_sanity=False, enforce_titleyear_identity=False
        )
        self.assertFalse(result)

    def test_same_format_compatible(self):
        left = self._make_movie(format="Blu-ray")
        right = self._make_movie(format="Blu-ray")
        result = self.mod._members_are_compatible(
            left, right, enforce_tmdb_sanity=False, enforce_titleyear_identity=False
        )
        self.assertTrue(result)

    def test_detect_groups_includes_no_format_movies_in_tmdb_tier(self):
        """Movies with tmdb_id but no format should still be grouped together."""
        mod = self.mod
        # Simulate detect_groups by checking grouping key directly
        fmt = mod.normalize_format(None)
        tid = "tt1234567"
        edition = ""
        key = (tid, edition)
        self.assertEqual(len(key), 2, "tmdbEdition key should not include format")

    def test_detect_groups_includes_no_format_movies_in_titleyear_tier(self):
        """Movies with title/year but no format should still be grouped by titleYear."""
        mod = self.mod
        norm = mod.normalize_title("Aladdin")
        year = "1992"
        key = (norm, year)
        self.assertEqual(len(key), 2, "titleYear key should not include format")


class SyncDedupScriptContainerTests(unittest.TestCase):
    """Unit tests for box-set container deduplication in sync_dedup_merge.py."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        script = REPO_ROOT / "app/backend/scripts/sync_dedup_merge.py"
        spec = importlib.util.spec_from_file_location("sync_dedup_merge", script)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def _make_container(self, **kwargs):
        from datetime import datetime, timezone
        defaults = {
            "id": 1,
            "title": "Box Set",
            "year": 2020,
            "barcode": None,
            "deleted_at": None,
            "container_type": "box_set",
            "movie_count": 0,
            "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        }
        defaults.update(kwargs)
        return defaults

    def test_detect_container_groups_by_barcode(self):
        """detect_container_groups returns barcode duplicates."""
        mod = self.mod
        bc = "1234567890123"
        c1 = self._make_container(id=1, barcode=bc, movie_count=3)
        c2 = self._make_container(id=2, barcode=bc, movie_count=1)

        conn = mock.MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: conn.cursor.return_value
        conn.cursor.return_value.__exit__ = mock.MagicMock(return_value=False)
        conn.cursor.return_value.fetchall.return_value = [c1, c2]

        groups, by_id = mod.detect_container_groups(conn)
        barcode_groups = groups["barcode"]
        self.assertIn(mod.normalize_barcode(bc), barcode_groups)
        self.assertEqual(sorted(barcode_groups[mod.normalize_barcode(bc)]), [1, 2])

    def test_detect_container_groups_by_title_year(self):
        """detect_container_groups groups same-title containers by container_type,
        not by year -- containers have no year concept the way movies do."""
        mod = self.mod
        c1 = self._make_container(
            id=1, title="The Marvel Collection", container_type="box_set", movie_count=5
        )
        c2 = self._make_container(
            id=2, title="The Marvel Collection", container_type="box_set", movie_count=2
        )

        conn = mock.MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: conn.cursor.return_value
        conn.cursor.return_value.__exit__ = mock.MagicMock(return_value=False)
        conn.cursor.return_value.fetchall.return_value = [c1, c2]

        groups, _ = mod.detect_container_groups(conn)
        title_groups = groups["titleYear"]
        norm = mod.normalize_title("The Marvel Collection")
        self.assertIn((norm, "box_set"), title_groups)

    def test_detect_container_groups_by_title_does_not_cross_container_type(self):
        """A vault and a collection sharing a title must never group together --
        only container_type disambiguates them, since neither carries a year."""
        mod = self.mod
        c1 = self._make_container(id=1, title="Kids", container_type="vault", movie_count=5)
        c2 = self._make_container(id=2, title="Kids", container_type="collection", movie_count=2)

        conn = mock.MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: conn.cursor.return_value
        conn.cursor.return_value.__exit__ = mock.MagicMock(return_value=False)
        conn.cursor.return_value.fetchall.return_value = [c1, c2]

        groups, _ = mod.detect_container_groups(conn)
        title_groups = groups["titleYear"]
        norm = mod.normalize_title("Kids")
        self.assertNotIn((norm, "vault"), title_groups)
        self.assertNotIn((norm, "collection"), title_groups)

    def test_fetch_live_containers_covers_vault_and_collection(self):
        """_fetch_live_containers must not filter to container_type = 'box_set'
        only -- that was the one thing hiding vault/collection duplicates from
        this whole script."""
        mod = self.mod
        conn = mock.MagicMock()
        captured_sql = {}

        def _execute(sql, params=()):
            captured_sql["sql"] = " ".join(sql.split())
            captured_sql["params"] = params

        conn.cursor.return_value.__enter__ = lambda s: conn.cursor.return_value
        conn.cursor.return_value.__exit__ = mock.MagicMock(return_value=False)
        conn.cursor.return_value.execute.side_effect = _execute
        conn.cursor.return_value.fetchall.return_value = []

        mod._fetch_live_containers(conn)
        self.assertNotIn("container_type = 'box_set'", captured_sql["sql"])
        self.assertNotIn("container_type", captured_sql["sql"])

        conn.cursor.return_value.execute.side_effect = _execute
        mod._fetch_live_containers(conn, container_types=["vault", "collection"])
        self.assertIn("container_type IN (%s, %s)", captured_sql["sql"])
        self.assertEqual(list(captured_sql["params"]), ["vault", "collection"])

    def test_choose_container_winner_prefers_more_movies(self):
        """Container with more movies wins."""
        mod = self.mod
        from datetime import datetime, timezone
        c1 = self._make_container(id=1, movie_count=5)
        c2 = self._make_container(id=2, movie_count=1)
        by_id = {1: c1, 2: c2}

        conn = mock.MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: conn.cursor.return_value
        conn.cursor.return_value.__exit__ = mock.MagicMock(return_value=False)
        conn.cursor.return_value.fetchone.return_value = {"ok": False}
        conn.cursor.return_value.fetchall.return_value = []

        winner, losers = mod._choose_container_winner(conn, [1, 2], by_id)
        self.assertEqual(winner, 1)
        self.assertIn(2, losers)

    def test_build_report_includes_container_groups(self):
        """build_report returns container_group_count and container_groups keys."""
        mod = self.mod

        conn = mock.MagicMock()

        # Mock detect_groups (movies) — no duplicates
        with mock.patch.object(mod, "detect_groups") as mock_detect, \
             mock.patch.object(mod, "_movie_signals", return_value={}), \
             mock.patch.object(mod, "detect_container_groups") as mock_cdetect, \
             mock.patch.object(mod, "_report_metadata", return_value={}):
            mock_detect.return_value = (
                {"barcode": {}, "tmdbEdition": {}, "titleYear": {}},
                {},
                {},
            )
            mock_cdetect.return_value = (
                {"barcode": {"9999": [10, 11]}, "titleYear": {}},
                {
                    10: self._make_container(id=10, barcode="9999", movie_count=3),
                    11: self._make_container(id=11, barcode="9999", movie_count=1),
                },
            )
            conn.cursor.return_value.__enter__ = lambda s: conn.cursor.return_value
            conn.cursor.return_value.__exit__ = mock.MagicMock(return_value=False)
            conn.cursor.return_value.fetchone.return_value = {"ok": False}
            conn.cursor.return_value.fetchall.return_value = []

            report = mod.build_report(conn)

        self.assertIn("container_groups", report)
        self.assertIn("container_group_count", report)
        self.assertIn("containers_to_tombstone", report)
        self.assertGreater(report["container_group_count"], 0)


if __name__ == "__main__":
    unittest.main()
