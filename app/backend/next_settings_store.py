"""``app_settings`` accessors and at-rest encryption for stored secrets.

These helpers were written inside ``next_movievault_connection.py`` and were
imported from there by the MovieVault v2 contribution and field-correction
modules. That module went with the ``movievault_26`` plugin, and none of what is
here was ever about MovieVault 26: it reads and writes rows in ``app_settings``
and wraps a secret in Fernet before it is stored. Keeping it in one small module
is what lets the v2 modules stay unchanged in behaviour while the plugin and its
transport are removed.

The encryption rule is the one that matters and it is unchanged: the key is
derived from a dedicated key-encryption secret, or failing that the required JWT
secret, and **never** from anything the database itself carries - a database dump
must not contain enough to recover the credentials stored in it.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

try:
    from psycopg.types.json import Jsonb
except ModuleNotFoundError:  # pragma: no cover - allows import-only tests without psycopg
    class Jsonb:  # type: ignore[no-redef]
        def __init__(self, value: Any) -> None:
            self.value = value

try:
    from .next_runtime_secrets import key_encryption_secret
except ImportError:  # pragma: no cover - supports running modules directly
    from next_runtime_secrets import key_encryption_secret


# Marker prefix for Fernet-encrypted secrets stored at rest. Values without this
# prefix are treated as legacy plaintext and transparently migrated on next read.
SECRET_ENC_PREFIX = "enc.v1:"


class SecretStorageError(RuntimeError):
    """Base for failures to encrypt a secret for storage, or to read one back."""


class SecretEncryptionError(SecretStorageError):
    """Raised when a secret cannot be encrypted, so it must not be stored."""


class SecretDecryptionError(SecretStorageError):
    """Raised when a stored secret cannot be decrypted with the current key."""


def _fernet_class():
    """The Fernet implementation, or a hard failure.

    ``cryptography`` is a hard dependency (`requirements.txt`), so it being
    absent is a mispackaged image, never an optional feature left switched off.
    Distinguished from every other failure and reported as such, because the
    remedy is completely different: rebuild the image, rather than check the
    key-encryption configuration.
    """
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover - a hard dependency in prod
        raise SecretStorageError(
            "The 'cryptography' package is required to store integration "
            "credentials encrypted at rest and is not installed. This is a "
            "packaging fault in the running image, not a configuration problem."
        ) from exc
    return Fernet


def _text(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def _table_exists(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL AS exists", (table_name,))
        row = cur.fetchone()
    return bool(row and row["exists"])


def _setting_value(conn, key: str, default: Any = None, *, include_secret: bool = False) -> Any:
    if not _table_exists(conn, "app_settings"):
        return default
    sql = "SELECT value FROM app_settings WHERE key=%s"
    params: tuple[Any, ...] = (key,)
    if not include_secret:
        sql += " AND is_secret=false"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return row["value"] if row else default


def _set_setting(conn, key: str, value: Any, *, is_secret: bool = False, actor_id: Any = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app_settings (key, value, is_secret, updated_at, updated_by)
            VALUES (%s, %s, %s, now(), %s)
            ON CONFLICT (key) DO UPDATE SET
                value=EXCLUDED.value,
                is_secret=EXCLUDED.is_secret,
                updated_at=now(),
                updated_by=EXCLUDED.updated_by
            """,
            (key, Jsonb(value), is_secret, actor_id),
        )


def _delete_setting(conn, key: str) -> None:
    if not _table_exists(conn, "app_settings"):
        return
    with conn.cursor() as cur:
        cur.execute("DELETE FROM app_settings WHERE key=%s", (key,))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _key_encryption_key() -> bytes:
    """Derive a stable Fernet key for encrypting instance secrets at rest.

    A dedicated key-encryption secret takes precedence over the required app JWT
    secret. No database-derived fallback is permitted because a database dump must
    not contain enough information to recover encrypted integration credentials.
    """
    configured = key_encryption_secret()
    return base64.urlsafe_b64encode(hashlib.sha256(configured.encode("utf-8")).digest())


def _encrypt_secret_value(plaintext: str) -> str:
    """Fernet-encrypt a secret for storage. Returns plaintext unchanged if empty.

    Fails closed. This used to return the plaintext unchanged when importing
    ``cryptography`` raised, which is the worst available outcome: the caller
    stores the value believing it is encrypted, the row looks ordinary, and
    nothing anywhere reports that a credential is sitting in the database in the
    clear. A raise stops the write instead, and stops it at the one moment the
    problem is still cheap - before the secret is on disk.

    The only exception caught is the import itself. Catching everything also
    swallowed a failure to derive the key (a missing JWT_SECRET, a
    RuntimeSecretConfigurationError) and turned that fail-closed rule into a
    silent fail-open too.
    """
    text = plaintext or ""
    if not text:
        return text
    fernet = _fernet_class()
    try:
        token = fernet(_key_encryption_key()).encrypt(text.encode("utf-8"))
    except SecretStorageError:
        raise
    except Exception as exc:
        raise SecretEncryptionError(
            "Unable to encrypt an integration credential for storage; verify "
            "DISCVAULT_NEXT_KEY_ENCRYPTION_KEY (or the JWT_SECRET it falls back "
            "to) is set to a stable value."
        ) from exc
    return SECRET_ENC_PREFIX + token.decode("ascii")


def _decrypt_secret_value(stored: Any) -> str:
    """Decrypt a value written by :func:`_encrypt_secret_value`.

    Legacy plaintext values (no marker prefix) are returned as-is so existing
    installs keep working; callers re-encrypt them on next write.
    """
    text = _text(stored)
    if not text.startswith(SECRET_ENC_PREFIX):
        return text
    fernet = _fernet_class()
    try:
        token = text[len(SECRET_ENC_PREFIX):].encode("ascii")
        return fernet(_key_encryption_key()).decrypt(token).decode("utf-8")
    except SecretStorageError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        # Names the settings an operator can actually check. "Stored signing
        # key" described only the MovieVault instance key this module was
        # extracted from; it now holds every integration credential, and a
        # message naming the wrong one sends the reader looking in the wrong
        # place. A changed key-encryption secret is the usual cause: the
        # ciphertext is intact and the key no longer opens it.
        raise SecretDecryptionError(
            "Unable to decrypt a stored integration credential; verify "
            "DISCVAULT_NEXT_KEY_ENCRYPTION_KEY (or the JWT_SECRET it falls back "
            "to) still holds the value the credential was stored under, or "
            "reset the stored identity to register a new one."
        ) from exc


def _is_encrypted_secret(stored: Any) -> bool:
    return _text(stored).startswith(SECRET_ENC_PREFIX)
