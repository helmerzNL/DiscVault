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
from app.backend.next_common import NextApiError, response


class _Context:
    def __init__(self, value=None):
        self.value = value

    def __enter__(self):
        return self.value

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


class AdminDedupEndpointTests(unittest.TestCase):
    def setUp(self):
        self.connection = _Connection()
        self.require_admin = mock.Mock(return_value={"id": "admin-1", "role": "admin"})
        self.require_authenticated_admin = mock.Mock(
            return_value={"id": "admin-1", "role": "admin"}
        )
        self.require_passkey_access = mock.Mock()
        self.verify_step_up_assertion = mock.Mock(
            return_value={"id": "credential-1", "new_sign_count": 4}
        )
        self.audit_event = mock.Mock()
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
        )
        self.client = self.app.test_client()

    def test_report_remains_available_to_authorized_admin(self):
        report = {"groups": [], "generatedAt": "2026-07-24T18:00:00Z"}
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(next_auth, "_dedup_build_report", return_value=report),
        ):
            result = self.client.get("/api/next/admin/dedup/report")

        self.assertEqual(result.status_code, 200)
        self.assertEqual(
            result.get_json(),
            {"status": "ok", "report": report, "executeEnabled": False},
        )
        self.require_admin.assert_called_once_with(self.connection)

    def test_execute_is_forbidden_by_default_without_entering_auth_or_merge_flow(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(next_auth, "_dedup_execute_merge") as execute_merge,
        ):
            result = self.client.post(
                "/api/next/admin/dedup/execute",
                json={"report": {"groups": []}, "credential": {"id": "credential-1"}},
            )

        self.assertEqual(result.status_code, 403)
        self.assertEqual(
            result.get_json(),
            {
                "status": "error",
                "error": "Admin dedup execution is disabled",
                "errorCode": "admin_dedup_execute_disabled",
            },
        )
        self.require_passkey_access.assert_not_called()
        self.require_authenticated_admin.assert_not_called()
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
                json={"report": {"groups": []}, "credential": {"id": "credential-1"}},
            )

        self.assertEqual(result.status_code, 403)
        self.assertEqual(result.get_json()["errorCode"], "admin_dedup_execute_disabled")
        execute_merge.assert_not_called()

    def test_explicit_true_preserves_admin_and_passkey_flow_with_mocked_merge(self):
        report = {"groups": [{"winner": "movie-1", "members": []}]}
        with (
            mock.patch.dict(
                os.environ,
                {"DISCVAULT_ADMIN_DEDUP_EXECUTE_ENABLED": "true"},
                clear=True,
            ),
            mock.patch.object(next_auth, "_dedup_execute_merge", return_value=0) as execute_merge,
        ):
            result = self.client.post(
                "/api/next/admin/dedup/execute",
                json={"report": report, "credential": {"id": "credential-1"}},
            )

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json(), {"status": "ok", "tombstoned": 0})
        self.require_passkey_access.assert_called_once_with()
        self.require_authenticated_admin.assert_called_once_with(self.connection)
        self.verify_step_up_assertion.assert_called_once_with(
            self.connection,
            challenge_key="admin_dedup:admin-1",
            expected_user_id="admin-1",
            credential={"id": "credential-1"},
        )
        execute_merge.assert_called_once_with(self.connection, report)
        self.audit_event.assert_called_once()
        self.assertIn(
            "UPDATE passkey_credentials",
            self.connection.cursor_instance.queries[0][0],
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

    def test_ui_keeps_report_scan_and_hides_execute_when_capability_is_false(self):
        source = (REPO_ROOT / "app/backend/next_views_ui.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('authApiJson("/api/next/admin/dedup/report")', source)
        self.assertIn("_dedupExecuteEnabled = result.executeEnabled === true;", source)
        self.assertIn("${_dedupExecuteEnabled ? `", source)
        self.assertIn('id="appAdminDedupExecuteBtn"', source)


if __name__ == "__main__":
    unittest.main()
