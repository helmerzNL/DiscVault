"""Shared environment for backend unit tests."""

import os


os.environ.setdefault("JWT_SECRET", "discvault-unit-tests-only-not-for-production")
# A test run is not an installation. Set, not defaulted: no environment a suite
# happens to run in may let it report to api.discvault.eu. The tests of the
# count itself hand their own environment to the functions they call.
os.environ["DISCVAULT_INSTALLATION_COUNT"] = "off"
