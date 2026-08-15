"""At-rest encryption of stored integration credentials fails closed.

The rule these tests hold: a credential is either stored encrypted or not stored
at all. There is no third outcome where it is written in the clear and the caller
is told the write succeeded.

That third outcome is exactly what this module used to produce. `_encrypt_secret_value()`
wrapped the `cryptography` import in `except Exception: return text`, so a
mispackaged image - or any failure to derive the key - stored the plaintext
credential and reported success. Nothing downstream could tell: the setting row
looks ordinary, the marker prefix is simply absent, and an absent prefix is the
legacy-plaintext path that is deliberately read back without complaint. A secret
leaked that way is silent at every point where it could have been noticed.
"""

from __future__ import annotations

import builtins
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

repo_root = Path(__file__).resolve().parents[3]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from app.backend import next_runtime_secrets
from app.backend import next_settings_store as store


def _without_cryptography():
    """Make `from cryptography.fernet import Fernet` raise ImportError.

    Patched at the import hook rather than by deleting `sys.modules` entries, so
    the failure looks the way a genuinely absent package looks and no other
    import in the process is disturbed.
    """
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "cryptography.fernet" or name.startswith("cryptography"):
            raise ImportError("No module named 'cryptography'")
        return real_import(name, *args, **kwargs)

    return patch.object(builtins, "__import__", fake_import)


class SecretEncryptionFailsClosedTests(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ, {"JWT_SECRET": "settings-store-test-secret"})
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_a_secret_survives_a_round_trip(self):
        stored = store._encrypt_secret_value("s3cret-token")

        self.assertTrue(stored.startswith(store.SECRET_ENC_PREFIX))
        self.assertNotIn("s3cret-token", stored)
        self.assertEqual(store._decrypt_secret_value(stored), "s3cret-token")

    def test_missing_cryptography_raises_instead_of_storing_plaintext(self):
        with _without_cryptography():
            with self.assertRaises(store.SecretStorageError) as raised:
                store._encrypt_secret_value("s3cret-token")

        # The remedy is to rebuild the image, not to check configuration, so the
        # message has to say which of the two this is.
        message = str(raised.exception)
        self.assertIn("cryptography", message)
        self.assertIn("packaging", message)
        # And it must never quote the secret it refused to store.
        self.assertNotIn("s3cret-token", message)

    def test_missing_cryptography_raises_on_read_too(self):
        stored = store._encrypt_secret_value("s3cret-token")

        with _without_cryptography():
            with self.assertRaises(store.SecretStorageError):
                store._decrypt_secret_value(stored)

    def test_an_unconfigured_key_secret_raises_rather_than_storing_plaintext(self):
        """The second half of the old `except Exception`.

        Deriving the key reads the required JWT secret, and that raises when
        nothing is configured. Catching it alongside the import turned a
        deliberately fail-closed rule into a fail-open one.
        """
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(
                (store.SecretEncryptionError, next_runtime_secrets.RuntimeSecretConfigurationError)
            ):
                store._encrypt_secret_value("s3cret-token")

    def test_an_empty_secret_is_not_encrypted(self):
        """Storing "no value" is not storing a secret, and must not fail."""
        self.assertEqual(store._encrypt_secret_value(""), "")
        with _without_cryptography():
            self.assertEqual(store._encrypt_secret_value(""), "")


class SecretDecryptionTests(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ, {"JWT_SECRET": "settings-store-test-secret"})
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_legacy_plaintext_is_returned_unchanged(self):
        """Existing installs hold values written before the marker existed."""
        self.assertEqual(store._decrypt_secret_value("legacy-plaintext"), "legacy-plaintext")
        self.assertFalse(store._is_encrypted_secret("legacy-plaintext"))

    def test_a_changed_key_names_the_settings_an_operator_can_check(self):
        stored = store._encrypt_secret_value("s3cret-token")

        with patch.dict(os.environ, {"DISCVAULT_NEXT_KEY_ENCRYPTION_KEY": "a-different-key"}):
            with self.assertRaises(store.SecretDecryptionError) as raised:
                store._decrypt_secret_value(stored)

        message = str(raised.exception)
        self.assertIn("DISCVAULT_NEXT_KEY_ENCRYPTION_KEY", message)
        self.assertIn("JWT_SECRET", message)
        # "signing key" described only the MovieVault instance key this module
        # was extracted from; it now holds every integration credential.
        self.assertNotIn("signing key", message)
        self.assertNotIn("s3cret-token", message)


if __name__ == "__main__":
    unittest.main()
