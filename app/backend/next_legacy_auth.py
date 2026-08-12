"""Security primitives shared by DiscVault Next Legacy authentication.

This module deliberately has no Flask or database dependency. Route code owns
transactions and authorization while this module owns secret handling and the
password/TOTP policy.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import os
import secrets
import struct
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
import segno

try:
    from .next_runtime_secrets import jwt_secret
except ImportError:  # pragma: no cover - direct module execution compatibility
    from next_runtime_secrets import jwt_secret


LEGACY_AUTH_SETTING = "legacy_password_login_enabled"
PASSWORD_HASH_VERSION = 1
PASSWORD_MIN_LENGTH = 15
TOTP_DIGITS = 6
TOTP_PERIOD_SECONDS = 30
TOTP_WINDOW = 1
FLOW_TTL_SECONDS = 5 * 60
# Recovery-code acknowledgement is the only step that waits on a human writing
# codes down, so it gets a longer window than the machine-paced flows.
RECOVERY_ACK_FLOW_TTL_SECONDS = 30 * 60
LOCK_ATTEMPTS = 5
LOCK_WINDOW_SECONDS = 15 * 60
LOCK_DURATION_SECONDS = 15 * 60

# Intentionally bundled rather than fetched at runtime. Entries longer than the
# minimum catch popular passphrases that a length-only policy would accept.
COMMON_PASSWORDS = frozenset(
    {
        "123456789012345",
        "1234567890123456",
        "111111111111111",
        "aaaaaaaaaaaaaaa",
        "adminadminadmin",
        "changemechangeme",
        "correcthorsebatterystaple",
        "discvaultdiscvault",
        "iloveyouiloveyou",
        "letmeinletmeinletmein",
        "password123456789",
        "passwordpassword",
        "qwertyuiopasdfgh",
        "thisisaverylongpassword",
        "welcome123456789",
    }
)

_PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)


class PasswordPolicyError(ValueError):
    """Raised when a proposed password does not meet the Legacy policy."""


@dataclass(frozen=True)
class PasswordVerification:
    valid: bool
    needs_rehash: bool = False


def legacy_auth_env_enabled() -> bool:
    """Return the fail-closed runtime capability gate."""

    return str(os.environ.get("LEGACY_AUTH_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def legacy_auth_effective(database_enabled: Any) -> bool:
    return legacy_auth_env_enabled() and bool(database_enabled)


def constant_time_text_equal(value: Any, expected: Any) -> bool:
    return secrets.compare_digest(
        str(value or "").encode("utf-8"),
        str(expected or "").encode("utf-8"),
    )


def attempt_is_throttled(identity_failures: int, ip_failures: int) -> bool:
    return int(identity_failures) >= LOCK_ATTEMPTS or int(ip_failures) >= LOCK_ATTEMPTS * 4


# Some endpoints cannot know who is knocking until after the credential check --
# a passkey assertion and a PKCE exchange both carry a credential, not a name.
# They have one dimension left to count on, so how tight that ceiling may be
# depends entirely on whether the address identifies one client or many.
#
# When it identifies one client -- a direct connection, or a proxy the operator
# configured as trusted so the real client address is recovered -- a low ceiling
# is both effective and safe: whoever trips it locks out only themselves.
IP_ONLY_LOCK_ATTEMPTS = 15

# When it does not, every caller shares one bucket. That is the state of an
# unconfigured deployment behind a reverse proxy, where the peer is always the
# proxy. A low ceiling there would be a denial-of-service lever rather than a
# defence: a handful of anonymous requests would lock every real user out of
# login. So the shared bucket gets a much looser bound, and the way to earn the
# tight one is to configure DISCVAULT_TRUSTED_PROXIES.
IP_ONLY_SHARED_LOCK_ATTEMPTS = 50


def ip_only_attempt_is_throttled(ip_failures: int, *, address_is_per_client: bool = False) -> bool:
    ceiling = IP_ONLY_LOCK_ATTEMPTS if address_is_per_client else IP_ONLY_SHARED_LOCK_ATTEMPTS
    return int(ip_failures) >= ceiling


def failed_attempt_state(
    failed_attempt_count: int,
    first_failed_at: datetime | None,
    now: datetime,
) -> tuple[int, datetime, datetime | None]:
    if (
        first_failed_at is None
        or (now - first_failed_at).total_seconds() > LOCK_WINDOW_SECONDS
    ):
        count = 1
        first = now
    else:
        count = int(failed_attempt_count or 0) + 1
        first = first_failed_at
    locked_until = (
        now + timedelta(seconds=LOCK_DURATION_SECONDS)
        if count >= LOCK_ATTEMPTS
        else None
    )
    return count, first, locked_until


def validate_password(password: Any, username: Any) -> str:
    value = str(password or "")
    normalized_username = str(username or "").strip().casefold()
    if len(value) < PASSWORD_MIN_LENGTH:
        raise PasswordPolicyError(
            f"Password must be at least {PASSWORD_MIN_LENGTH} characters"
        )
    normalized = value.strip().casefold()
    if normalized_username and normalized == normalized_username:
        raise PasswordPolicyError("Password must not be the username")
    if normalized in COMMON_PASSWORDS:
        raise PasswordPolicyError("Choose a password that is not commonly used")
    return value


def hash_password(password: str) -> str:
    return _PASSWORD_HASHER.hash(password)


def verify_password(password_hash: Any, password: Any) -> PasswordVerification:
    encoded = str(password_hash or "")
    if not encoded:
        return PasswordVerification(False)
    try:
        valid = _PASSWORD_HASHER.verify(encoded, str(password or ""))
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return PasswordVerification(False)
    return PasswordVerification(bool(valid), bool(valid and _PASSWORD_HASHER.check_needs_rehash(encoded)))


def _secret_material(secret: Any | None = None) -> bytes:
    configured = str(secret if secret is not None else jwt_secret()).encode("utf-8")
    if not configured:
        raise RuntimeError("JWT_SECRET is required for Legacy authentication")
    return configured


def _derive_key(context: bytes, secret: Any | None = None) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"discvault-next/legacy-auth/" + context,
    ).derive(_secret_material(secret))


def encrypt_totp_secret(secret_value: str, *, key_secret: Any | None = None) -> tuple[bytes, bytes]:
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(_derive_key(b"totp-aes-gcm-v1", key_secret)).encrypt(
        nonce,
        secret_value.encode("ascii"),
        b"discvault-next-totp-v1",
    )
    return nonce, ciphertext


def decrypt_totp_secret(
    nonce: bytes,
    ciphertext: bytes,
    *,
    key_secret: Any | None = None,
) -> str:
    plaintext = AESGCM(_derive_key(b"totp-aes-gcm-v1", key_secret)).decrypt(
        bytes(nonce),
        bytes(ciphertext),
        b"discvault-next-totp-v1",
    )
    return plaintext.decode("ascii")


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _totp_key(secret_value: str) -> bytes:
    padded = secret_value.strip().replace(" ", "").upper()
    padded += "=" * ((8 - len(padded) % 8) % 8)
    return base64.b32decode(padded, casefold=True)


def totp_code(secret_value: str, *, at_time: float | int | None = None, step: int | None = None) -> str:
    if step is None:
        timestamp = time.time() if at_time is None else float(at_time)
        step = int(timestamp // TOTP_PERIOD_SECONDS)
    digest = hmac.new(_totp_key(secret_value), struct.pack(">Q", int(step)), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**TOTP_DIGITS)).zfill(TOTP_DIGITS)


def verify_totp(
    secret_value: str,
    submitted_code: Any,
    *,
    at_time: float | int | None = None,
    last_accepted_step: int | None = None,
    window: int = TOTP_WINDOW,
) -> int | None:
    code = str(submitted_code or "").strip().replace(" ", "")
    if not code.isascii() or not code.isdigit() or len(code) != TOTP_DIGITS:
        return None
    timestamp = time.time() if at_time is None else float(at_time)
    current_step = int(timestamp // TOTP_PERIOD_SECONDS)
    for candidate_step in range(current_step - window, current_step + window + 1):
        if last_accepted_step is not None and candidate_step <= int(last_accepted_step):
            continue
        if hmac.compare_digest(totp_code(secret_value, step=candidate_step), code):
            return candidate_step
    return None


def totp_uri(secret_value: str, username: str, *, issuer: str = "DiscVault") -> str:
    label = quote(f"{issuer}:{username}", safe="")
    query = urlencode(
        {
            "secret": secret_value,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": TOTP_DIGITS,
            "period": TOTP_PERIOD_SECONDS,
        }
    )
    return f"otpauth://totp/{label}?{query}"


def totp_qr_data_uri(uri: str) -> str:
    output = io.BytesIO()
    segno.make(uri, error="m").save(
        output,
        kind="svg",
        scale=5,
        border=2,
        dark="#000",
        light="#fff",
        xmldecl=False,
        svgns=True,
    )
    return "data:image/svg+xml;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def generate_recovery_codes(count: int = 10) -> list[str]:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    codes: list[str] = []
    while len(codes) < count:
        raw = "".join(secrets.choice(alphabet) for _ in range(16))
        code = "-".join(raw[index : index + 4] for index in range(0, 16, 4))
        if code not in codes:
            codes.append(code)
    return codes


def normalize_recovery_code(value: Any) -> str:
    return "".join(
        character
        for character in str(value or "").upper()
        if character in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    )


def hash_recovery_code(value: Any, *, key_secret: Any | None = None, salt: bytes | None = None) -> str:
    normalized = normalize_recovery_code(value)
    code_salt = salt or secrets.token_bytes(16)
    digest = hmac.new(
        _derive_key(b"recovery-code-v1", key_secret),
        code_salt + normalized.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return "v1${}${}".format(
        base64.urlsafe_b64encode(code_salt).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
    )


def verify_recovery_code(stored_hash: Any, value: Any, *, key_secret: Any | None = None) -> bool:
    try:
        version, salt_text, _ = str(stored_hash or "").split("$", 2)
        if version != "v1":
            return False
        salt_text += "=" * ((4 - len(salt_text) % 4) % 4)
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = hash_recovery_code(value, key_secret=key_secret, salt=salt)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(str(stored_hash), expected)


def generate_flow_token() -> str:
    return secrets.token_urlsafe(32)


def secret_digest(namespace: str, value: Any, *, key_secret: Any | None = None) -> str:
    return hmac.new(
        _derive_key(b"digest-v1", key_secret),
        f"{namespace}:{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def flow_token_hash(token: Any, *, key_secret: Any | None = None) -> str:
    return secret_digest("flow", str(token or ""), key_secret=key_secret)


def identity_digest(username: Any, *, key_secret: Any | None = None) -> str:
    return secret_digest(
        "identity",
        str(username or "").strip().casefold(),
        key_secret=key_secret,
    )


def ip_digest(ip_address: Any, *, key_secret: Any | None = None) -> str:
    return secret_digest("ip", str(ip_address or "").strip(), key_secret=key_secret)


def safe_audit_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Reject accidental credential material before it can reach the audit log."""

    forbidden_fragments = ("password", "secret", "totp", "recovery_code", "token")
    return {
        key: value
        for key, value in metadata.items()
        if not any(fragment in str(key).casefold() for fragment in forbidden_fragments)
    }
