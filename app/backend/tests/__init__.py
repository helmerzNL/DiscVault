"""Shared environment for backend unit tests."""

import os


os.environ.setdefault("JWT_SECRET", "discvault-unit-tests-only-not-for-production")
