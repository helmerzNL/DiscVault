"""Regression tests for fail-closed runtime secret configuration."""

from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

repo_root = Path(__file__).resolve().parents[3]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from app.backend import next_auth
from app.backend import next_movievault_connection
from app.backend import next_runtime_secrets
from app.backend import next_worker


class RuntimeSecretTests(unittest.TestCase):
    def test_jwt_secret_rejects_missing_configuration(self):
        with patch.dict(
            os.environ,
            {"DATABASE_URL": "postgresql://database-secret@db/discvault"},
            clear=True,
        ):
            with self.assertRaises(next_runtime_secrets.RuntimeSecretConfigurationError) as raised:
                next_runtime_secrets.jwt_secret()

        message = str(raised.exception)
        self.assertIn("JWT_SECRET is required", message)
        self.assertIn("openssl rand -base64 48", message)
        self.assertNotIn("database-secret", message)

    def test_jwt_secret_rejects_whitespace_only_values(self):
        with patch.dict(
            os.environ,
            {
                "DISCVAULT_NEXT_JWT_SECRET": "  ",
                "JWT_SECRET": "\t",
            },
            clear=True,
        ):
            with self.assertRaises(next_runtime_secrets.RuntimeSecretConfigurationError):
                next_runtime_secrets.jwt_secret()

    def test_next_specific_jwt_secret_takes_precedence(self):
        with patch.dict(
            os.environ,
            {
                "DISCVAULT_NEXT_JWT_SECRET": "next-specific-secret",
                "JWT_SECRET": "shared-secret",
            },
            clear=True,
        ):
            self.assertEqual(next_runtime_secrets.jwt_secret(), "next-specific-secret")
            self.assertEqual(next_auth._jwt_secret(), "next-specific-secret")

    def test_shared_jwt_secret_is_supported(self):
        with patch.dict(os.environ, {"JWT_SECRET": "shared-secret"}, clear=True):
            self.assertEqual(next_runtime_secrets.jwt_secret(), "shared-secret")
            next_runtime_secrets.validate_runtime_secrets()

    def test_key_encryption_uses_required_jwt_secret_by_default(self):
        with patch.dict(os.environ, {"JWT_SECRET": "shared-secret"}, clear=True):
            expected = base64.urlsafe_b64encode(hashlib.sha256(b"shared-secret").digest())
            self.assertEqual(next_movievault_connection._key_encryption_key(), expected)

    def test_dedicated_key_encryption_secret_takes_precedence(self):
        with patch.dict(
            os.environ,
            {
                "DISCVAULT_NEXT_KEY_ENCRYPTION_KEY": "dedicated-secret",
                "JWT_SECRET": "shared-secret",
            },
            clear=True,
        ):
            expected = base64.urlsafe_b64encode(hashlib.sha256(b"dedicated-secret").digest())
            self.assertEqual(next_movievault_connection._key_encryption_key(), expected)

    def test_key_encryption_rejects_database_derived_fallback(self):
        with patch.dict(
            os.environ,
            {"DATABASE_URL": "postgresql://database-secret@db/discvault"},
            clear=True,
        ):
            with self.assertRaises(next_runtime_secrets.RuntimeSecretConfigurationError):
                next_movievault_connection._key_encryption_key()

    def test_api_startup_rejects_missing_secret_without_disclosing_database_url(self):
        env = os.environ.copy()
        for name in next_runtime_secrets.JWT_SECRET_ENV_NAMES:
            env.pop(name, None)
        env["DATABASE_URL"] = "postgresql://database-secret@db/discvault"

        result = subprocess.run(
            [sys.executable, "-c", "from app.backend import next_app"],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("JWT_SECRET is required", result.stderr)
        self.assertNotIn("database-secret", result.stderr)

    def test_api_startup_accepts_configured_secret(self):
        env = os.environ.copy()
        env["JWT_SECRET"] = "api-startup-test-secret"
        env.pop("DISCVAULT_NEXT_JWT_SECRET", None)

        result = subprocess.run(
            [sys.executable, "-c", "from app.backend import next_app"],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_worker_startup_rejects_missing_secret_before_database_access(self):
        with patch.dict(
            os.environ,
            {"DATABASE_URL": "postgresql://database-secret@db/discvault"},
            clear=True,
        ):
            with self.assertRaises(next_runtime_secrets.RuntimeSecretConfigurationError):
                next_worker.main(["run-once"])

    def test_worker_startup_accepts_configured_secret(self):
        mock_conn = MagicMock()
        with (
            patch.dict(os.environ, {"JWT_SECRET": "worker-startup-test-secret"}, clear=True),
            patch.object(next_worker, "wait_for_database", return_value=mock_conn),
            patch.object(next_worker, "run_once", return_value=0) as run_once,
            patch.object(next_worker.signal, "signal"),
        ):
            self.assertEqual(next_worker.main(["run-once", "--worker-id", "test-worker"]), 0)

        run_once.assert_called_once_with("test-worker")


if __name__ == "__main__":
    unittest.main()
