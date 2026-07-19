"""Fail-closed runtime secret resolution shared by API and worker processes."""

from __future__ import annotations

import os


JWT_SECRET_ENV_NAMES = ("DISCVAULT_NEXT_JWT_SECRET", "JWT_SECRET")
KEY_ENCRYPTION_ENV_NAMES = (
    "DISCVAULT_NEXT_KEY_ENCRYPTION_KEY",
    "DISCVAULT_KEY_ENCRYPTION_KEY",
)


class RuntimeSecretConfigurationError(RuntimeError):
    """Raised when a required runtime secret is not configured."""


def _first_non_empty(names: tuple[str, ...]) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def jwt_secret() -> str:
    """Return the configured JWT secret without deriving an insecure fallback."""
    configured = _first_non_empty(JWT_SECRET_ENV_NAMES)
    if configured:
        return configured
    raise RuntimeSecretConfigurationError(
        "JWT_SECRET is required. Set JWT_SECRET (or DISCVAULT_NEXT_JWT_SECRET) "
        "to a stable random value in the container environment; for example, "
        "generate one with: openssl rand -base64 48"
    )


def key_encryption_secret() -> str:
    """Return the dedicated encryption secret, or the required JWT secret."""
    configured = _first_non_empty(KEY_ENCRYPTION_ENV_NAMES)
    return configured or jwt_secret()


def validate_runtime_secrets() -> None:
    """Validate secrets needed by both the API and background worker."""
    jwt_secret()
    key_encryption_secret()
