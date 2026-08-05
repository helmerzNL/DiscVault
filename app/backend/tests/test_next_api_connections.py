"""One connection per device on the API & MCP page.

Native clients have no refresh endpoint, so the app logs in again whenever its
token stops working. Every login used to insert another ``api_access_tokens``
row named "DiscVault iOS", and the profile payload listed each row, so the page
grew an entry per login instead of one per device.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend import next_api_token
    from app.backend import next_app  # noqa: F401 - the payload patches its lazy helpers
except ModuleNotFoundError as exc:  # Local minimal test environments may omit optional backend deps.
    if exc.name not in {"flask", "psycopg"}:
        raise
    next_api_token = None

try:
    from app.backend import next_auth
except ModuleNotFoundError as exc:  # cbor2/jwt/cryptography are only present with the full requirements.
    if exc.name not in {"flask", "psycopg", "cbor2", "jwt", "cryptography"}:
        raise
    next_auth = None


NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def token_row(**overrides):
    row = {
        "id": "00000000-0000-0000-0000-000000000001",
        "name": "DiscVault iOS",
        "scopes": ["read"],
        "permission_keys": ["api.read"],
        "created_at": NOW,
        "last_used_at": NOW,
        "expires_at": None,
        "revoked_at": None,
        "client_kind": "ios",
        "device_id": None,
        "device_label": None,
        "device_model": None,
        "user_agent": "DiscVault/26.7 (iOS)",
        "last_seen_ip": None,
        "last_seen_ip_source": None,
    }
    row.update(overrides)
    return row


@unittest.skipIf(next_api_token is None, "Flask is not installed in this test environment")
class ApiAccessConnectionGroupingTests(unittest.TestCase):
    def test_repeated_logins_of_one_device_collapse_into_one_connection(self):
        rows = [
            token_row(id=f"id-{index}", created_at=NOW - timedelta(days=index))
            for index in range(4)
        ]

        connections = next_api_token.group_api_access_tokens(rows)

        self.assertEqual(len(connections), 1)
        self.assertEqual(connections[0]["sessionCount"], 4)
        self.assertEqual(len(connections[0]["tokenIds"]), 4)

    def test_connection_spans_the_oldest_creation_and_the_newest_use(self):
        rows = [
            token_row(id="new", created_at=NOW, last_used_at=NOW),
            token_row(id="old", created_at=NOW - timedelta(days=30), last_used_at=NOW - timedelta(days=29)),
        ]

        connection = next_api_token.group_api_access_tokens(rows)[0]

        self.assertEqual(connection["createdAt"], NOW - timedelta(days=30))
        self.assertEqual(connection["lastUsedAt"], NOW)

    def test_two_devices_stay_two_connections(self):
        rows = [
            token_row(id="phone", device_id="install-phone", device_label="Helmer's iPhone"),
            token_row(id="tablet", device_id="install-tablet", device_label="Woonkamer iPad"),
        ]

        connections = next_api_token.group_api_access_tokens(rows)

        self.assertEqual(len(connections), 2)
        self.assertEqual({entry["sessionCount"] for entry in connections}, {1})
        self.assertEqual(
            {entry["displayName"] for entry in connections},
            {"Helmer's iPhone", "Woonkamer iPad"},
        )

    def test_platforms_are_not_merged_even_without_a_device_id(self):
        rows = [
            token_row(id="ios", name="DiscVault iOS", client_kind="ios"),
            token_row(id="android", name="DiscVault Android", client_kind="android"),
        ]

        self.assertEqual(len(next_api_token.group_api_access_tokens(rows)), 2)

    def test_a_manual_api_key_is_never_folded_into_a_device(self):
        rows = [
            token_row(id="app", device_id="install-phone"),
            token_row(id="manual", name="Home Assistant", client_kind=None, user_agent=None),
        ]

        self.assertEqual(len(next_api_token.group_api_access_tokens(rows)), 2)

    def test_a_connection_counts_as_revoked_only_when_nothing_in_it_works(self):
        rows = [
            token_row(id="active", device_id="install-phone", revoked_at=None),
            token_row(id="stale", device_id="install-phone", revoked_at=NOW),
        ]

        connection = next_api_token.group_api_access_tokens(rows)[0]

        self.assertIsNone(connection["revokedAt"])
        self.assertEqual(connection["sessionCount"], 2)

    def test_a_fully_revoked_connection_keeps_its_revocation(self):
        rows = [token_row(id="gone", device_id="install-phone", revoked_at=NOW)]

        self.assertEqual(next_api_token.group_api_access_tokens(rows)[0]["revokedAt"], NOW)

    def test_the_device_name_wins_over_the_model_and_the_token_name(self):
        entry = next_api_token.api_access_token_row(
            token_row(device_label="Helmer's iPhone", device_model="iPhone 16 Pro")
        )

        self.assertEqual(next_api_token.api_access_connection_display_name(entry), "Helmer's iPhone")

    def test_the_model_names_a_device_that_sent_no_name(self):
        entry = next_api_token.api_access_token_row(
            token_row(device_label=None, device_model="Google Pixel 8")
        )

        self.assertEqual(next_api_token.api_access_connection_display_name(entry), "Google Pixel 8")

    def test_the_address_shown_belongs_to_the_most_recent_use(self):
        # The representative is the newest *created* token, which need not be
        # the one that was used last.
        rows = [
            token_row(id="new", created_at=NOW, last_used_at=NOW - timedelta(days=5), last_seen_ip="1.1.1.1"),
            token_row(id="old", created_at=NOW - timedelta(days=9), last_used_at=NOW, last_seen_ip="8.8.8.8"),
        ]

        connection = next_api_token.group_api_access_tokens(rows)[0]

        self.assertEqual(connection["lastSeenIp"], "8.8.8.8")
        self.assertEqual(connection["lastUsedAt"], NOW)

    def test_a_connection_without_a_recorded_address_keeps_none(self):
        rows = [token_row(id="only", last_seen_ip=None)]

        self.assertIsNone(next_api_token.group_api_access_tokens(rows)[0]["lastSeenIp"])

    def test_a_device_that_sent_neither_falls_back_to_the_token_name(self):
        entry = next_api_token.api_access_token_row(token_row(device_label=None, device_model=None))

        self.assertEqual(next_api_token.api_access_connection_display_name(entry), "DiscVault iOS")


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, *args, **kwargs):
        return None

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return FakeCursor(self._rows)


@unittest.skipIf(next_api_token is None, "Flask is not installed in this test environment")
class ProfileApiAccessPayloadTests(unittest.TestCase):
    def setUp(self):
        self.actor = {"id": "user-1", "role": "admin", "permissions": ["api.read", "api.tokens.manage"]}

    def payload(self, rows, **kwargs):
        with (
            mock.patch("app.backend.next_app.permission_key_catalog", return_value={"api.read", "api.tokens.manage"}),
            mock.patch("app.backend.next_app.table_exists", return_value=True),
        ):
            return next_api_token.profile_api_access_payload(FakeConnection(rows), self.actor, **kwargs)

    def test_the_list_counts_connections_rather_than_logins(self):
        rows = [token_row(id=f"id-{index}") for index in range(5)]

        payload = self.payload(rows)

        self.assertEqual(len(payload["tokens"]), 1)
        self.assertEqual(payload["tokens"][0]["sessionCount"], 5)

    def test_revoked_connections_are_hidden_but_counted(self):
        rows = [
            token_row(id="active", device_id="install-phone"),
            token_row(id="gone", device_id="install-old-phone", revoked_at=NOW),
        ]

        payload = self.payload(rows)

        self.assertEqual([entry["id"] for entry in payload["tokens"]], ["active"])
        self.assertEqual(payload["revokedCount"], 1)
        self.assertFalse(payload["includesRevoked"])

    def test_revoked_connections_can_be_asked_for(self):
        rows = [
            token_row(id="active", device_id="install-phone"),
            token_row(id="gone", device_id="install-old-phone", revoked_at=NOW),
        ]

        payload = self.payload(rows, include_revoked=True)

        self.assertEqual(len(payload["tokens"]), 2)
        self.assertTrue(payload["includesRevoked"])


@unittest.skipIf(next_auth is None, "Full backend requirements are not installed in this test environment")
class NativeClientIdentityTests(unittest.TestCase):
    def test_android_is_a_recognised_client(self):
        self.assertEqual(next_auth.next_normalize_client_kind("Android"), "android")
        self.assertEqual(next_auth.next_native_token_name("android"), "DiscVault Android")

    def test_a_client_that_says_nothing_is_still_treated_as_ios(self):
        # The shipped apps and the mobile exchange predate the field; renaming
        # their tokens would relabel every existing connection.
        self.assertEqual(next_auth.next_native_token_name(""), "DiscVault iOS")

    def test_an_unknown_client_kind_is_not_accepted(self):
        self.assertEqual(next_auth.next_normalize_client_kind("windows"), "")

    def test_device_fields_are_read_from_either_spelling(self):
        context = next_auth.next_native_device_context(
            {"clientKind": "ios", "device_id": "install-1", "deviceLabel": "Helmer's iPhone", "device_model": "iPhone 16 Pro"},
            user_agent="DiscVault/26.7 (iOS)",
        )

        self.assertEqual(context["clientKind"], "ios")
        self.assertEqual(context["deviceId"], "install-1")
        self.assertEqual(context["deviceLabel"], "Helmer's iPhone")
        self.assertEqual(context["deviceModel"], "iPhone 16 Pro")
        self.assertEqual(context["userAgent"], "DiscVault/26.7 (iOS)")

    def test_blank_device_fields_are_dropped_rather_than_stored_empty(self):
        context = next_auth.next_native_device_context({"deviceId": "   ", "deviceLabel": ""})

        self.assertIsNone(context["deviceId"])
        self.assertIsNone(context["deviceLabel"])

    def test_a_device_name_cannot_outgrow_the_token_name_limit(self):
        context = next_auth.next_native_device_context({"deviceLabel": "x" * 500})

        self.assertEqual(len(context["deviceLabel"]), next_auth.NATIVE_DEVICE_FIELD_MAX_LENGTH)


if __name__ == "__main__":
    unittest.main()
