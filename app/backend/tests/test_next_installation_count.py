"""The anonymous installation count: what is sent, when, and when nothing is.

The rules under test come from App-Guidance ->
``projects/discvault/specs/active-installation-count.md`` (section 4 and section
6), and each of them is one a later change could break without anything else
noticing: a report that carries one field too many, a failed attempt that marks
the day anyway, an off switch that still makes the request, a second worker that
reports on the same day, or an owner who was never told.

Nothing here touches the network or a database. The HTTP call is a function
handed in by the test, and ``app_settings`` is a dict behind the handful of
statements the module issues -- any statement the fake does not recognise fails
the test, so the module cannot quietly start reading or writing something else.
The statements themselves are proven against PostgreSQL in
``test_next_installation_count_postgres``.
"""

import contextlib
import copy
import glob
import json
import logging
import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import flask

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_auth
from app.backend import next_installation_count as count
from app.backend import next_notifications
from app.backend import next_worker

OWNER_ID = "00000000-0000-0000-0000-0000000000a1"
RELEASE = "26.11.0"
ENABLED_ENV: dict[str, str] = {}

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
I18N_DIR = os.path.join(BACKEND_DIR, "..", "frontend", "i18n", "next")
UI_STRING_KEYS = (
    "preferences.cardInstallationCount",
    "preferences.cardInstallationCountHelp",
    "preferences.installationCount",
    "preferences.installationCountHelp",
    "preferences.installationCountEnvDisabled",
    "notifications.installationCountTitle",
    "notifications.installationCountBody",
)

# The failed attempts below log on purpose. Without a handler of its own the
# logger would print each line into the test output.
_QUIET = logging.NullHandler()


def setUpModule():
    count.logger.addHandler(_QUIET)


def tearDownModule():
    count.logger.removeHandler(_QUIET)


def utc(year, month, day, hour=12, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def _unwrap(value):
    """A ``Jsonb`` parameter as the Python value it wraps."""
    return getattr(value, "obj", value)


class FakeCursor:
    def __init__(self, connection):
        self._connection = connection
        self._rows = []
        self.rowcount = -1

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, query, params=None):
        text = " ".join(str(query).split())
        self._rows, self.rowcount = self._connection.database.run(text, tuple(params or ()))

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    """One transaction: what it wrote is undone when the block raises."""

    def __init__(self, database):
        self.database = database

    def __enter__(self):
        self.database.open_connections += 1
        self.database.connections_opened += 1
        self._snapshot = (
            copy.deepcopy(self.database.settings),
            list(self.database.notifications),
        )
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.database.open_connections -= 1
        if exc_type is not None:
            self.database.settings, self.database.notifications = self._snapshot
        return False

    def cursor(self):
        return FakeCursor(self)


class FakeDatabase:
    """``app_settings`` and the owner's inbox, behind the module's own SQL."""

    def __init__(self, settings=None, *, owner_id=OWNER_ID):
        self.settings = dict(settings or {})
        self.notifications = []
        self.owner_id = owner_id
        self.missing_tables = set()
        self.open_connections = 0
        self.connections_opened = 0
        self.before_claim = None
        self.fail_notification = False

    def connect(self):
        return FakeConnection(self)

    def run(self, text, params):
        if "to_regclass" in text:
            # Asked in two spellings: the settings store's and the shared one.
            name = str(params[0]).removeprefix("public.")
            present = name not in self.missing_tables
            return [{"exists": present, "table_name": name if present else None}], 1
        if text.startswith("SELECT key, value FROM app_settings WHERE key = ANY"):
            rows = [
                {"key": key, "value": copy.deepcopy(self.settings[key])}
                for key in params[0]
                if key in self.settings
            ]
            return rows, len(rows)
        if text.startswith("INSERT INTO app_settings") and "DO NOTHING" in text:
            key, value = params[0], _unwrap(params[1])
            self._claiming(key)
            if key in self.settings:
                return [], 0
            self.settings[key] = value
            return [], 1
        if text.startswith("INSERT INTO app_settings") and "DO UPDATE" in text:
            self.settings[params[0]] = _unwrap(params[1])
            return [], 1
        if text.startswith("UPDATE app_settings SET value=%s, updated_at=now() WHERE key=%s AND value=%s"):
            value, key, expected = _unwrap(params[0]), params[1], _unwrap(params[2])
            self._claiming(key)
            if key not in self.settings or self.settings[key] != expected:
                return [], 0
            self.settings[key] = value
            return [], 1
        if "FROM users u" in text and "r.key = 'owner'" in text:
            return ([{"id": self.owner_id}] if self.owner_id else []), 1
        if text.startswith("INSERT INTO user_notifications"):
            if self.fail_notification:
                raise RuntimeError("the inbox refused the row")
            row = {
                "id": f"notification-{len(self.notifications) + 1}",
                "user_id": params[0],
                "title": params[1],
                "body": params[2],
                "payload": _unwrap(params[3]),
            }
            self.notifications.append(row)
            return [dict(row)], 1
        raise AssertionError(f"the installation count issued an unexpected statement: {text}")

    def _claiming(self, key):
        if key == count.ATTEMPT_KEY and self.before_claim is not None:
            hook, self.before_claim = self.before_claim, None
            hook()


class Recorder:
    """Stands in for the HTTP call and remembers what it was asked to send."""

    def __init__(self, database, *answers):
        self.database = database
        self.answers = list(answers) or [(204, None)]
        self.calls = []

    def __call__(self, url, body):
        self.calls.append(
            {
                "url": url,
                "body": body,
                "payload": json.loads(body.decode("utf-8")),
                "open_connections": self.database.open_connections,
                "notifications": len(self.database.notifications),
                "notice_recorded": count.NOTICE_CREATED_AT_KEY in self.database.settings,
            }
        )
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def run(database, post, now, *, environ=ENABLED_ENV, version=RELEASE):
    return count.report_if_due(
        database.connect,
        now=now,
        environ=environ,
        version=version,
        post=post,
    )


class PayloadTests(unittest.TestCase):
    def test_the_report_has_exactly_the_four_fields(self):
        """One field more is one way to tell two servers apart. The count is
        only compatible with "no tracking" while there is none."""
        payload = count.build_payload(RELEASE, 3)
        self.assertEqual(set(payload), {"protocol", "platform", "version", "sinceDays"})
        self.assertEqual(
            payload,
            {"protocol": "presence-1", "platform": "server", "version": RELEASE, "sinceDays": 3},
        )

    def test_the_body_is_the_one_in_the_contract_byte_for_byte(self):
        self.assertEqual(
            count.encode_payload(count.build_payload(RELEASE, 3)),
            b'{"protocol":"presence-1","platform":"server","version":"26.11.0","sinceDays":3}',
        )
        self.assertEqual(
            count.encode_payload(count.build_payload(RELEASE, None)),
            b'{"protocol":"presence-1","platform":"server","version":"26.11.0","sinceDays":null}',
        )

    def test_since_days_counts_whole_utc_days_and_is_null_the_first_time(self):
        self.assertIsNone(count.since_days(date(2026, 11, 9), None))
        self.assertEqual(count.since_days(date(2026, 11, 9), date(2026, 11, 8)), 1)
        self.assertEqual(count.since_days(date(2026, 11, 9), date(2026, 11, 6)), 3)
        self.assertEqual(count.since_days(date(2027, 1, 2), date(2026, 12, 30)), 3)


class GateTests(unittest.TestCase):
    def test_only_a_plain_release_version_reports(self):
        for version in ("26.11.0", "1.2.3", "999.999.9999"):
            self.assertTrue(count.is_release_version(version), version)
        for version in (
            "build-0123456789ab",
            "v26.11.0",
            "26.11.0-beta",
            "26.11.0-3-g1a2b3c4-dirty",
            "26.11",
            "26.11.0.1",
            "1234.1.1",
            "26.11.12345",
            "26.11.0\n",
            " 26.11.0",
            "２６.11.0",
            "unknown",
            "next-dev",
            "",
            None,
        ):
            self.assertFalse(count.is_release_version(version), repr(version))

    def test_the_environment_switch_turns_it_off_in_any_spelling(self):
        for value in ("off", "OFF", "false", "False", "0", "no", " No "):
            self.assertTrue(count.disabled_by_environment({count.DISABLE_ENV: value}), value)
        for environ in ({}, {count.DISABLE_ENV: ""}, {count.DISABLE_ENV: "on"}, {count.DISABLE_ENV: "true"}):
            self.assertFalse(count.disabled_by_environment(environ), environ)

    def test_the_owner_setting_is_on_when_it_was_never_stored(self):
        """On by default, for a new installation and for an existing one after
        the upgrade (ADR-0020). A missing row must not read as "off"."""
        self.assertTrue(count.owner_setting_enabled(None))
        self.assertTrue(count.owner_setting_enabled(True))
        self.assertFalse(count.owner_setting_enabled(False))
        self.assertFalse(count.owner_setting_enabled("off"))

    def test_reports_go_to_the_api_origin_and_never_to_movievault(self):
        self.assertEqual(count.presence_url({}), "https://api.discvault.eu/v2/presence")
        # The MovieVault origin is somebody else's setting.
        self.assertEqual(
            count.presence_url({"MOVIEVAULT_V2_ORIGIN": "https://movievault.example"}),
            "https://api.discvault.eu/v2/presence",
        )
        self.assertEqual(
            count.presence_url({count.API_ORIGIN_ENV: "https://presence.example/"}),
            "https://presence.example/v2/presence",
        )

    def test_an_invalid_origin_override_falls_back_to_the_default(self):
        for override in ("ftp://presence.example", "https://user:pw@presence.example", "https://x.example/path", "nonsense"):
            self.assertEqual(count.api_origin({count.API_ORIGIN_ENV: override}), count.DEFAULT_API_ORIGIN, override)


class PlanTests(unittest.TestCase):
    def plan(self, now, last_accepted_on=None, attempt=None):
        return count.plan_report(now, last_accepted_on=last_accepted_on, attempt=attempt)

    def test_the_first_report_has_no_previous_one(self):
        plan = self.plan(utc(2026, 11, 9))
        self.assertTrue(plan.due)
        self.assertIsNone(plan.since_days)
        self.assertEqual(plan.today, date(2026, 11, 9))
        self.assertEqual(plan.attempt["count"], 1)

    def test_nothing_more_is_sent_on_the_day_of_an_accepted_report(self):
        plan = self.plan(utc(2026, 11, 9, 23, 59), last_accepted_on="2026-11-09")
        self.assertFalse(plan.due)
        self.assertEqual(plan.reason, "already_reported")

    def test_the_next_utc_day_reports_the_interval(self):
        self.assertEqual(self.plan(utc(2026, 11, 10, 0, 0, 1), last_accepted_on="2026-11-09").since_days, 1)
        self.assertEqual(self.plan(utc(2026, 11, 12), last_accepted_on="2026-11-09").since_days, 3)

    def test_the_day_is_the_utc_day_whatever_zone_the_clock_is_read_in(self):
        """01:30 in Amsterdam on the 10th is still the 9th in UTC."""
        amsterdam = timezone(timedelta(hours=1))
        now = datetime(2026, 11, 10, 0, 30, tzinfo=amsterdam)
        self.assertFalse(self.plan(now, last_accepted_on="2026-11-09").due)
        self.assertTrue(self.plan(now, last_accepted_on="2026-11-08").due)

    def test_a_stored_date_in_the_future_sends_nothing(self):
        """The clock was set back. ``sinceDays`` may not be negative, and
        waiting for the clock is the mistake that can be taken back."""
        self.assertFalse(self.plan(utc(2026, 11, 9), last_accepted_on="2026-11-12").due)

    def test_an_unreadable_stored_date_counts_as_none(self):
        plan = self.plan(utc(2026, 11, 9), last_accepted_on="yesterday")
        self.assertTrue(plan.due)
        self.assertIsNone(plan.since_days)

    def test_an_attempt_within_the_hour_is_not_repeated(self):
        attempt = {"day": "2026-11-09", "count": 1, "lastAttemptAt": utc(2026, 11, 9, 10).isoformat()}
        self.assertEqual(self.plan(utc(2026, 11, 9, 10, 59, 59), attempt=attempt).reason, "retry_interval")
        plan = self.plan(utc(2026, 11, 9, 11), attempt=attempt)
        self.assertTrue(plan.due)
        self.assertEqual(plan.attempt["count"], 2)

    def test_the_hour_also_holds_across_midnight(self):
        attempt = {"day": "2026-11-09", "count": 1, "lastAttemptAt": utc(2026, 11, 9, 23, 30).isoformat()}
        self.assertFalse(self.plan(utc(2026, 11, 10, 0, 10), attempt=attempt).due)
        self.assertTrue(self.plan(utc(2026, 11, 10, 0, 30), attempt=attempt).due)

    def test_six_attempts_a_day_is_the_limit(self):
        attempt = {"day": "2026-11-09", "count": 6, "lastAttemptAt": utc(2026, 11, 9, 6).isoformat()}
        plan = self.plan(utc(2026, 11, 9, 20), attempt=attempt)
        self.assertFalse(plan.due)
        self.assertEqual(plan.reason, "attempt_limit")

    def test_the_limit_starts_over_on_the_next_utc_day(self):
        attempt = {"day": "2026-11-09", "count": 6, "lastAttemptAt": utc(2026, 11, 9, 6).isoformat()}
        plan = self.plan(utc(2026, 11, 10, 0, 0, 5), attempt=attempt)
        self.assertTrue(plan.due)
        self.assertEqual(plan.attempt, {
            "day": "2026-11-10",
            "count": 1,
            "lastAttemptAt": utc(2026, 11, 10, 0, 0, 5).isoformat(),
        })

    def test_a_stored_retry_after_is_waited_out(self):
        attempt = {
            "day": "2026-11-09",
            "count": 1,
            "lastAttemptAt": utc(2026, 11, 9, 10).isoformat(),
            "retryNotBefore": utc(2026, 11, 9, 16).isoformat(),
        }
        self.assertEqual(self.plan(utc(2026, 11, 9, 15, 59), attempt=attempt).reason, "retry_after")
        self.assertTrue(self.plan(utc(2026, 11, 9, 16), attempt=attempt).due)

    def test_bookkeeping_that_is_not_an_object_counts_as_none(self):
        for attempt in (None, "", 7, ["2026-11-09"], {"day": 9, "count": "many", "lastAttemptAt": 12}):
            self.assertTrue(self.plan(utc(2026, 11, 9), attempt=attempt).due, attempt)


class ResponseTests(unittest.TestCase):
    NOW = utc(2026, 11, 9, 10)

    def test_only_a_2xx_is_a_counted_report(self):
        for status in (200, 204, 299):
            self.assertTrue(count.interpret_response(status, None, self.NOW).accepted, status)
        for status in (301, 302, 400, 404, 429, 500, 503):
            self.assertFalse(count.interpret_response(status, None, self.NOW).accepted, status)
        outcome = count.interpret_response(None, None, self.NOW)
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.reason, "network_error")

    def test_a_429_asking_for_longer_than_an_hour_is_honoured(self):
        outcome = count.interpret_response(429, "7200", self.NOW)
        self.assertEqual(outcome.retry_not_before, self.NOW + timedelta(hours=2))

    def test_a_429_asking_for_less_is_covered_by_the_hour_already(self):
        self.assertIsNone(count.interpret_response(429, "60", self.NOW).retry_not_before)
        self.assertIsNone(count.interpret_response(429, "3600", self.NOW).retry_not_before)
        self.assertIsNone(count.interpret_response(429, None, self.NOW).retry_not_before)

    def test_retry_after_is_read_from_a_429_only(self):
        self.assertIsNone(count.interpret_response(503, "7200", self.NOW).retry_not_before)

    def test_retry_after_may_be_a_date(self):
        delay = count.retry_after_delay("Mon, 09 Nov 2026 13:00:00 GMT", self.NOW)
        self.assertEqual(delay, timedelta(hours=3))
        self.assertEqual(count.retry_after_delay("Mon, 09 Nov 2026 09:00:00 GMT", self.NOW), timedelta(0))

    def test_retry_after_is_bounded_and_nonsense_is_ignored(self):
        self.assertEqual(count.retry_after_delay("999999999999", self.NOW), count.MAX_RETRY_AFTER)
        self.assertEqual(count.retry_after_delay("9" * 5000, self.NOW), count.MAX_RETRY_AFTER)
        self.assertEqual(count.retry_after_delay("Fri, 01 Jan 2100 00:00:00 GMT", self.NOW), count.MAX_RETRY_AFTER)
        for value in (None, "", "soon", "-5", "1.5"):
            self.assertIsNone(count.retry_after_delay(value, self.NOW), value)


class ReportTests(unittest.TestCase):
    def test_the_first_report_is_sent_and_the_day_is_remembered(self):
        database = FakeDatabase()
        post = Recorder(database)
        self.assertEqual(run(database, post, utc(2026, 11, 9)), "accepted")

        self.assertEqual(len(post.calls), 1)
        call = post.calls[0]
        self.assertEqual(call["url"], "https://api.discvault.eu/v2/presence")
        self.assertEqual(
            call["payload"],
            {"protocol": "presence-1", "platform": "server", "version": RELEASE, "sinceDays": None},
        )
        self.assertEqual(database.settings[count.LAST_ACCEPTED_ON_KEY], "2026-11-09")

    def test_no_transaction_is_held_open_across_the_request(self):
        database = FakeDatabase()
        post = Recorder(database)
        run(database, post, utc(2026, 11, 9))
        self.assertEqual(post.calls[0]["open_connections"], 0)

    def test_the_same_utc_day_sends_no_second_report(self):
        database = FakeDatabase()
        post = Recorder(database)
        run(database, post, utc(2026, 11, 9, 8))
        self.assertEqual(run(database, post, utc(2026, 11, 9, 20)), "already_reported")
        self.assertEqual(len(post.calls), 1)

    def test_a_later_day_reports_the_days_since_the_last_accepted_one(self):
        database = FakeDatabase()
        post = Recorder(database)
        run(database, post, utc(2026, 11, 9))
        run(database, post, utc(2026, 11, 12))
        self.assertEqual([call["payload"]["sinceDays"] for call in post.calls], [None, 3])
        self.assertEqual(database.settings[count.LAST_ACCEPTED_ON_KEY], "2026-11-12")

    def test_a_refused_report_does_not_mark_the_day(self):
        """Marking the day on a failure would make the server vanish from that
        day's count and report a wrong interval the next."""
        database = FakeDatabase({count.LAST_ACCEPTED_ON_KEY: "2026-11-06"})
        post = Recorder(database, (503, None))
        self.assertEqual(run(database, post, utc(2026, 11, 9, 8)), "failed")
        self.assertEqual(database.settings[count.LAST_ACCEPTED_ON_KEY], "2026-11-06")

    def test_the_attempt_after_a_failure_keeps_the_old_interval(self):
        database = FakeDatabase({count.LAST_ACCEPTED_ON_KEY: "2026-11-06"})
        post = Recorder(database, (None, None), (204, None))
        run(database, post, utc(2026, 11, 9, 8))
        self.assertEqual(run(database, post, utc(2026, 11, 9, 9, 30)), "accepted")
        self.assertEqual([call["payload"]["sinceDays"] for call in post.calls], [3, 3])
        self.assertEqual(database.settings[count.LAST_ACCEPTED_ON_KEY], "2026-11-09")

    def test_a_failure_carried_over_midnight_reports_the_longer_interval(self):
        database = FakeDatabase({count.LAST_ACCEPTED_ON_KEY: "2026-11-06"})
        post = Recorder(database, (500, None), (204, None))
        run(database, post, utc(2026, 11, 9, 8))
        run(database, post, utc(2026, 11, 10, 8))
        self.assertEqual([call["payload"]["sinceDays"] for call in post.calls], [3, 4])

    def test_a_failed_attempt_is_not_repeated_within_the_hour(self):
        database = FakeDatabase()
        post = Recorder(database, (500, None))
        run(database, post, utc(2026, 11, 9, 8))
        self.assertEqual(run(database, post, utc(2026, 11, 9, 8, 5)), "retry_interval")
        self.assertEqual(run(database, post, utc(2026, 11, 9, 8, 59, 59)), "retry_interval")
        self.assertEqual(len(post.calls), 1)
        self.assertEqual(run(database, post, utc(2026, 11, 9, 9)), "failed")
        self.assertEqual(len(post.calls), 2)

    def test_a_redirect_is_a_failed_attempt(self):
        database = FakeDatabase()
        self.assertEqual(run(database, Recorder(database, (302, None)), utc(2026, 11, 9)), "failed")
        self.assertNotIn(count.LAST_ACCEPTED_ON_KEY, database.settings)

    def test_at_most_six_attempts_are_made_in_one_utc_day(self):
        database = FakeDatabase()
        post = Recorder(database, (None, None))
        outcomes = [run(database, post, utc(2026, 11, 9, hour)) for hour in range(0, 24, 2)]
        self.assertEqual(len(post.calls), 6)
        self.assertEqual(outcomes[:6], ["failed"] * 6)
        self.assertEqual(set(outcomes[6:]), {"attempt_limit"})
        # The next UTC day starts over.
        self.assertEqual(run(database, post, utc(2026, 11, 10, 0, 30)), "failed")
        self.assertEqual(len(post.calls), 7)

    def test_retry_after_on_a_429_postpones_the_next_attempt(self):
        database = FakeDatabase()
        post = Recorder(database, (429, "10800"), (204, None))
        run(database, post, utc(2026, 11, 9, 8))
        self.assertEqual(run(database, post, utc(2026, 11, 9, 10)), "retry_after")
        self.assertEqual(len(post.calls), 1)
        self.assertEqual(run(database, post, utc(2026, 11, 9, 11)), "accepted")
        self.assertEqual(len(post.calls), 2)

    def test_the_environment_switch_makes_no_request_and_asks_no_database(self):
        database = FakeDatabase({count.LAST_ACCEPTED_ON_KEY: "2026-11-06"})
        post = Recorder(database)
        for value in ("off", "false", "0", "NO"):
            outcome = run(database, post, utc(2026, 11, 9), environ={count.DISABLE_ENV: value})
            self.assertEqual(outcome, "disabled_by_environment")
        self.assertEqual(post.calls, [])
        self.assertEqual(database.connections_opened, 0)
        # The stored date is kept, so turning it back on reports the true interval.
        self.assertEqual(database.settings, {count.LAST_ACCEPTED_ON_KEY: "2026-11-06"})

    def test_the_owner_setting_makes_no_request_and_keeps_the_stored_date(self):
        database = FakeDatabase({count.ENABLED_KEY: False, count.LAST_ACCEPTED_ON_KEY: "2026-11-06"})
        post = Recorder(database)
        self.assertEqual(run(database, post, utc(2026, 11, 9)), "disabled_by_owner")
        self.assertEqual(post.calls, [])
        self.assertEqual(database.settings[count.LAST_ACCEPTED_ON_KEY], "2026-11-06")

        # Turned back on, it reports the true interval.
        database.settings[count.ENABLED_KEY] = True
        self.assertEqual(run(database, post, utc(2026, 11, 9)), "accepted")
        self.assertEqual(post.calls[0]["payload"]["sinceDays"], 3)

    def test_it_is_on_when_the_owner_never_touched_the_setting(self):
        database = FakeDatabase()
        self.assertNotIn(count.ENABLED_KEY, database.settings)
        post = Recorder(database)
        self.assertEqual(run(database, post, utc(2026, 11, 9)), "accepted")
        self.assertEqual(len(post.calls), 1)

    def test_a_development_build_sends_nothing_at_all(self):
        database = FakeDatabase()
        post = Recorder(database)
        for version in ("build-0123456789ab", "26.11.0-4-gabc1234", "unknown"):
            self.assertEqual(run(database, post, utc(2026, 11, 9), version=version), "not_a_release")
        self.assertEqual(post.calls, [])
        self.assertEqual(database.connections_opened, 0)

    def test_the_version_the_image_was_built_as_is_the_one_reported(self):
        database = FakeDatabase()
        post = Recorder(database)
        environ = {**ENABLED_ENV, count.IMAGE_VERSION_ENV: "26.12.7"}
        self.assertEqual(
            count.report_if_due(database.connect, now=utc(2026, 11, 9), environ=environ, post=post),
            "accepted",
        )
        self.assertEqual(post.calls[0]["payload"]["version"], "26.12.7")

    def test_a_source_checkout_is_not_an_installation(self):
        """`app/VERSION` is a plain X.Y.Z in every clone, so the version file
        cannot be what makes a build a release. Only the value the published
        image was built with does; without it nothing is sent."""
        database = FakeDatabase()
        post = Recorder(database)
        for environ in (
            dict(ENABLED_ENV),
            {**ENABLED_ENV, count.IMAGE_VERSION_ENV: ""},
            {**ENABLED_ENV, count.IMAGE_VERSION_ENV: "next-dev"},
            {**ENABLED_ENV, count.IMAGE_VERSION_ENV: "dev"},
            # The other version variables do not make a release either.
            {**ENABLED_ENV, "BUILD_VERSION": "26.12.7", "DISCVAULT_BACKEND_VERSION": "26.12.7"},
        ):
            self.assertIsNone(count.release_version(environ))
            self.assertEqual(
                count.report_if_due(database.connect, now=utc(2026, 11, 9), environ=environ, post=post),
                "not_a_release",
            )
        self.assertEqual(post.calls, [])
        self.assertEqual(database.connections_opened, 0)
        self.assertEqual(count.release_version({count.IMAGE_VERSION_ENV: " 26.12.7 "}), "26.12.7")

    def test_two_workers_deciding_at_once_send_one_report(self):
        """Both read the same state and both find a report due. The claim is a
        compare-and-set on what they read, so the second one loses."""
        database = FakeDatabase({count.NOTICE_CREATED_AT_KEY: "2026-11-01T00:00:00+00:00"})
        post = Recorder(database)
        now = utc(2026, 11, 9)
        second_worker = []
        database.before_claim = lambda: second_worker.append(run(database, post, now))

        first_worker = run(database, post, now)

        self.assertEqual(second_worker, ["accepted"])
        self.assertEqual(first_worker, "claimed_elsewhere")
        self.assertEqual(len(post.calls), 1)

    def test_the_same_race_on_a_later_attempt(self):
        database = FakeDatabase(
            {
                count.NOTICE_CREATED_AT_KEY: "2026-11-01T00:00:00+00:00",
                count.ATTEMPT_KEY: {"day": "2026-11-08", "count": 2, "lastAttemptAt": utc(2026, 11, 8, 9).isoformat()},
            }
        )
        post = Recorder(database)
        now = utc(2026, 11, 9)
        second_worker = []
        database.before_claim = lambda: second_worker.append(run(database, post, now))

        self.assertEqual(run(database, post, now), "claimed_elsewhere")
        self.assertEqual(second_worker, ["accepted"])
        self.assertEqual(len(post.calls), 1)

    def test_a_failure_is_one_warning_line_a_day_and_carries_no_answer(self):
        database = FakeDatabase()
        post = Recorder(database, (500, None))
        with self.assertLogs(count.logger, level="DEBUG") as logs:
            run(database, post, utc(2026, 11, 9, 8))
            run(database, post, utc(2026, 11, 9, 10))
            run(database, post, utc(2026, 11, 9, 12))
        levels = [record.levelname for record in logs.records]
        self.assertEqual(levels, ["WARNING", "DEBUG", "DEBUG"])
        self.assertIn("http_500", logs.records[0].getMessage())

    def test_nothing_the_database_does_reaches_the_caller(self):
        def broken_connect():
            raise RuntimeError("the database is gone")

        post = MagicMock()
        with self.assertLogs(count.logger, level="WARNING") as logs:
            outcome = count.report_if_due(
                broken_connect, now=utc(2026, 11, 9), environ=ENABLED_ENV, version=RELEASE, post=post
            )
        self.assertEqual(outcome, "error")
        post.assert_not_called()
        self.assertEqual(len(logs.records), 1)
        self.assertNotIn("the database is gone", logs.records[0].getMessage())

    def test_a_database_without_its_settings_table_yet_is_left_alone(self):
        database = FakeDatabase()
        database.missing_tables.add("app_settings")
        post = Recorder(database)
        self.assertEqual(run(database, post, utc(2026, 11, 9)), "not_ready")
        self.assertEqual(post.calls, [])


class OwnerNoticeTests(unittest.TestCase):
    """The owner is told once, and before anything is sent."""

    def setUp(self):
        # With ``send_push=False`` the inbox helper never reaches the push
        # code. If it ever does, this is where the test finds out.
        guard = patch.object(
            next_notifications,
            "_next_app",
            side_effect=AssertionError("the notice must not be pushed to any device"),
        )
        guard.start()
        self.addCleanup(guard.stop)

    def test_the_first_run_creates_the_notice_before_the_first_report(self):
        database = FakeDatabase()
        post = Recorder(database)
        run(database, post, utc(2026, 11, 9))

        self.assertEqual(len(database.notifications), 1)
        self.assertIn(count.NOTICE_CREATED_AT_KEY, database.settings)
        # At the moment of the request the notice was already committed.
        self.assertEqual(post.calls[0]["notifications"], 1)
        self.assertTrue(post.calls[0]["notice_recorded"])

    def test_the_notice_goes_to_the_owner_and_to_nobody_else(self):
        database = FakeDatabase()
        run(database, Recorder(database), utc(2026, 11, 9))
        self.assertEqual([row["user_id"] for row in database.notifications], [OWNER_ID])

    def test_the_notice_says_what_it_is_about(self):
        database = FakeDatabase()
        run(database, Recorder(database), utc(2026, 11, 9))
        notice = database.notifications[0]
        self.assertEqual(notice["title"], "Anonymous installation count is on")
        for fragment in ("api.discvault.eu", "version", "days since", "No identifier", "turn it off"):
            self.assertIn(fragment, notice["body"])
        self.assertEqual(notice["payload"]["kind"], count.NOTICE_KIND)
        self.assertEqual(notice["payload"]["url"], "/profile")

    def test_the_notice_is_not_created_a_second_time(self):
        database = FakeDatabase()
        post = Recorder(database)
        run(database, post, utc(2026, 11, 9))
        run(database, post, utc(2026, 11, 9, 18))
        run(database, post, utc(2026, 11, 10))
        run(database, post, utc(2026, 11, 20))
        self.assertEqual(len(database.notifications), 1)
        self.assertEqual(len(post.calls), 3)

    def test_a_restart_does_not_create_it_again(self):
        """What survives a restart is the database, and nothing else is asked."""
        database = FakeDatabase()
        run(database, Recorder(database), utc(2026, 11, 9))
        recorded = database.settings[count.NOTICE_CREATED_AT_KEY]

        restarted = FakeDatabase(copy.deepcopy(database.settings))
        run(restarted, Recorder(restarted), utc(2026, 11, 10))
        self.assertEqual(restarted.notifications, [])
        self.assertEqual(restarted.settings[count.NOTICE_CREATED_AT_KEY], recorded)

    def test_a_read_or_deleted_notice_is_not_created_again(self):
        database = FakeDatabase()
        run(database, Recorder(database), utc(2026, 11, 9))
        database.notifications.clear()
        run(database, Recorder(database), utc(2026, 11, 10))
        self.assertEqual(database.notifications, [])

    def test_with_no_owner_nothing_is_sent_until_there_is_one(self):
        database = FakeDatabase(owner_id=None)
        post = Recorder(database)
        self.assertEqual(run(database, post, utc(2026, 11, 9)), "no_owner_to_tell")
        self.assertEqual(post.calls, [])
        self.assertEqual(database.notifications, [])
        self.assertNotIn(count.NOTICE_CREATED_AT_KEY, database.settings)

        database.owner_id = OWNER_ID
        self.assertEqual(run(database, post, utc(2026, 11, 9, 1)), "accepted")
        self.assertEqual(len(database.notifications), 1)
        self.assertEqual(post.calls[0]["notifications"], 1)

    def test_no_report_leaves_when_the_notice_cannot_be_written(self):
        database = FakeDatabase()
        database.fail_notification = True
        post = Recorder(database)
        with self.assertLogs(count.logger, level="WARNING"):
            self.assertEqual(run(database, post, utc(2026, 11, 9)), "error")
        self.assertEqual(post.calls, [])
        # Rolled back with it: the next check tries again instead of believing
        # the owner was told.
        self.assertNotIn(count.NOTICE_CREATED_AT_KEY, database.settings)
        self.assertNotIn(count.ATTEMPT_KEY, database.settings)

        database.fail_notification = False
        self.assertEqual(run(database, post, utc(2026, 11, 9, 1)), "accepted")
        self.assertEqual(len(database.notifications), 1)

    def test_while_the_count_is_off_no_notice_is_created(self):
        """A notice that says "on" would be untrue. It is created the first
        time the count is on, which is still before the first report."""
        database = FakeDatabase({count.ENABLED_KEY: False})
        post = Recorder(database)
        run(database, post, utc(2026, 11, 9))
        run(database, post, utc(2026, 11, 9), environ={count.DISABLE_ENV: "off"})
        run(database, post, utc(2026, 11, 9), version="build-0123456789ab")
        self.assertEqual(database.notifications, [])

        database.settings[count.ENABLED_KEY] = True
        run(database, post, utc(2026, 11, 10))
        self.assertEqual(len(database.notifications), 1)
        self.assertEqual(post.calls[0]["notifications"], 1)

    def test_a_second_worker_does_not_tell_the_owner_again(self):
        database = FakeDatabase()
        with database.connect() as conn:
            self.assertTrue(count.ensure_owner_notice(conn, utc(2026, 11, 9)))
        with database.connect() as conn:
            self.assertTrue(count.ensure_owner_notice(conn, utc(2026, 11, 9)))
        self.assertEqual(len(database.notifications), 1)

    def test_the_notice_waits_for_the_inbox_table(self):
        database = FakeDatabase()
        database.missing_tables.add("user_notifications")
        post = Recorder(database)
        self.assertEqual(run(database, post, utc(2026, 11, 9)), "no_owner_to_tell")
        self.assertEqual(post.calls, [])


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(count, "_next_check_at", 0.0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.clock = 1000.0
        self.checks = []
        self.spawned = 0
        patcher = patch.object(
            count,
            "report_if_due",
            side_effect=lambda connect, **kwargs: self.checks.append(connect),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _inline(self, target):
        self.spawned += 1
        target()

    def schedule(self, environ=ENABLED_ENV):
        return count.schedule_check(
            "connect",
            monotonic=lambda: self.clock,
            environ=environ,
            spawn=self._inline,
        )

    def test_the_poll_loop_pays_for_one_check_every_few_minutes(self):
        self.assertTrue(self.schedule())
        for _ in range(100):
            self.clock += 2
            self.assertFalse(self.schedule())
        self.assertEqual(self.checks, ["connect"])

        self.clock = 1000.0 + count.CHECK_INTERVAL_SECONDS
        self.assertTrue(self.schedule())
        self.assertEqual(len(self.checks), 2)

    def test_the_environment_switch_starts_nothing(self):
        self.assertFalse(self.schedule(environ={count.DISABLE_ENV: "off"}))
        self.assertEqual(self.checks, [])
        self.assertEqual(self.spawned, 0)

    def test_a_check_still_running_is_not_joined_by_a_second(self):
        self.assertTrue(count._check_in_flight.acquire(blocking=False))
        try:
            self.assertFalse(self.schedule())
            self.assertEqual(self.checks, [])
        finally:
            count._check_in_flight.release()

    def test_the_next_check_can_start_once_one_has_finished(self):
        self.schedule()
        self.assertFalse(count._check_in_flight.locked())

    def test_a_thread_that_cannot_start_leaves_the_next_check_possible(self):
        def refuse(target):
            raise RuntimeError("can't start new thread")

        self.assertFalse(
            count.schedule_check("connect", monotonic=lambda: self.clock, environ=ENABLED_ENV, spawn=refuse)
        )
        self.assertFalse(count._check_in_flight.locked())

    def test_the_check_runs_on_a_daemon_thread_of_its_own(self):
        """So a name lookup that hangs behind an egress firewall delays no job,
        and a worker that is told to stop does not wait for it."""
        with patch.object(count, "threading") as threading_module:
            count._spawn(lambda: None)
        threading_module.Thread.assert_called_once()
        self.assertTrue(threading_module.Thread.call_args.kwargs["daemon"])
        threading_module.Thread.return_value.start.assert_called_once_with()


class WorkerLoopTests(unittest.TestCase):
    def _one_iteration(self, hook):
        next_worker.STOP = False

        def stop_after_one(worker_id, quiet_idle=True):
            next_worker.STOP = True
            return 0

        with (
            patch.object(next_worker, "_maybe_enqueue_price_sweep"),
            patch.object(next_worker, "_maybe_enqueue_artwork_trash_purge"),
            patch.object(next_worker, "enqueue_due_scheduled_jobs"),
            patch.object(next_worker, "connect", return_value=MagicMock()) as connect,
            patch.object(next_worker, "_maybe_enqueue_movievault_v2_sync"),
            patch.object(next_worker, "schedule_installation_count_check", hook),
            patch.object(next_worker, "run_once", side_effect=stop_after_one) as run_once,
            patch.object(next_worker.time, "sleep"),
        ):
            result = next_worker.work_loop("worker-1", 0.01)
        return result, run_once, connect

    def test_the_loop_offers_the_count_a_turn_on_every_poll(self):
        hook = MagicMock(return_value=False)
        _, _, connect = self._one_iteration(hook)
        # Handed the worker's own connection factory, and nothing else: the
        # hook opens a connection only when a check is actually due.
        hook.assert_called_once_with(connect)

    def test_the_count_cannot_take_the_loop_down(self):
        hook = MagicMock(side_effect=RuntimeError("the count is broken"))
        result, run_once, _ = self._one_iteration(hook)
        self.assertEqual(result, 0)
        run_once.assert_called_once()


class TransportTests(unittest.TestCase):
    """What actually goes on the wire, with the opener replaced."""

    def _send(self, opener):
        with patch.object(count.urllib.request, "build_opener", return_value=opener) as build:
            result = count.post_report("https://api.discvault.eu/v2/presence", b"{}")
        return result, build

    def test_the_request_carries_no_cookie_no_credential_and_no_identity(self):
        response = MagicMock()
        response.status = 204
        response.headers = {}
        response.__enter__.return_value = response
        opener = MagicMock()
        opener.open.return_value = response

        result, build = self._send(opener)

        self.assertEqual(result, (204, None))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, "https://api.discvault.eu/v2/presence")
        self.assertEqual(
            {name.lower(): value for name, value in request.header_items()},
            {
                "accept": "application/json",
                "content-type": "application/json",
                "user-agent": "DiscVault-InstallationCount",
            },
        )
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 10)
        # Its own opener, and one that follows no redirect.
        handlers = build.call_args.args
        self.assertEqual(len(handlers), 1)
        self.assertIsNone(handlers[0].redirect_request(None, None, 302, "Found", {}, "https://elsewhere.example"))
        # The answer's body is never read, so it cannot end up in a log.
        response.read.assert_not_called()

    def test_an_error_status_is_returned_with_its_retry_after(self):
        error = count.urllib.error.HTTPError(
            "https://api.discvault.eu/v2/presence", 429, "Too Many Requests", {"Retry-After": "7200"}, None
        )
        opener = MagicMock()
        opener.open.side_effect = error
        self.assertEqual(self._send(opener)[0], (429, "7200"))

    def test_an_unreachable_endpoint_is_not_an_exception(self):
        for failure in (
            count.urllib.error.URLError("no route to host"),
            TimeoutError("timed out"),
            ConnectionResetError("reset"),
            count.http.client.RemoteDisconnected("closed"),
        ):
            opener = MagicMock()
            opener.open.side_effect = failure
            self.assertEqual(self._send(opener)[0], (None, None), failure)


class _SettingsCursor:
    def __init__(self, connection):
        self._connection = connection
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, query, params=None):
        self._rows = self._connection.answer(" ".join(str(query).split()), tuple(params or ()))

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _SettingsConnection:
    """Enough of a connection for the owner-settings route and its gate."""

    def __init__(self, settings=None, *, role="owner"):
        self.settings = dict(settings or {})
        self.role = role
        self.audit_events = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def cursor(self):
        return _SettingsCursor(self)

    def transaction(self):
        return contextlib.nullcontext()

    def commit(self):
        pass

    def answer(self, text, params):
        if "FROM user_roles ur" in text:
            return [{"key": self.role}] if self.role else []
        if text.startswith("SELECT value FROM app_settings WHERE key=%s"):
            return [{"value": self.settings[params[0]]}] if params[0] in self.settings else []
        if text.startswith("INSERT INTO app_settings"):
            self.settings[params[0]] = _unwrap(params[1])
            return []
        if text.startswith("INSERT INTO audit_events"):
            self.audit_events.append(
                {
                    "event_type": params[0],
                    "category": params[1],
                    "actor_user_id": params[2],
                    "actor_role": params[4],
                    "target_id": params[6],
                    "metadata": _unwrap(params[8]),
                }
            )
            return []
        raise AssertionError(f"the owner-settings route issued an unexpected statement: {text}")


class _ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class OwnerSettingRouteTests(unittest.TestCase):
    """The owner's off switch: who may use it, and what it says when unset."""

    PATH = "/api/next/auth/owner/settings"
    USER = {"id": OWNER_ID, "username": "owner"}

    def _client(self, connection):
        app = flask.Flask(__name__)

        @app.errorhandler(_ApiError)
        def refused(error):
            return flask.jsonify({"error": str(error)}), error.status

        next_auth.register_next_auth_routes(
            app,
            connect=lambda: connection,
            table_exists=lambda conn, name: True,
            response=lambda payload, status=200: (flask.jsonify(payload), status),
            next_api_error=_ApiError,
        )
        return app.test_client()

    def _call(self, connection, *, method="GET", body=None, user=USER, environ=ENABLED_ENV):
        with (
            patch.object(next_auth, "next_auth_current_user", return_value=dict(user) if user else None),
            patch.object(next_auth, "movievault_v2_contribution_state", return_value={}),
            patch.dict(os.environ, environ, clear=False),
        ):
            if not environ:
                # The suite itself runs with the count switched off.
                os.environ.pop(count.DISABLE_ENV, None)
            client = self._client(connection)
            if method == "POST":
                return client.post(self.PATH, json=body)
            return client.get(self.PATH)

    def test_the_setting_reads_as_on_when_it_was_never_stored(self):
        connection = _SettingsConnection()
        answer = self._call(connection)
        self.assertEqual(answer.status_code, 200)
        settings = answer.get_json()["settings"]
        self.assertIs(settings["installation_count_enabled"], True)
        self.assertIs(settings["installation_count_disabled_by_environment"], False)
        # Reading it stores nothing: "never touched" stays distinguishable.
        self.assertNotIn(count.ENABLED_KEY, connection.settings)

    def test_the_owner_can_turn_it_off_and_on_again(self):
        connection = _SettingsConnection()
        answer = self._call(connection, method="POST", body={"installation_count_enabled": False})
        self.assertEqual(answer.status_code, 200)
        self.assertIs(answer.get_json()["settings"]["installation_count_enabled"], False)
        self.assertIs(connection.settings[count.ENABLED_KEY], False)
        # The change is on the audit trail, under the owner who made it.
        self.assertEqual(
            connection.audit_events,
            [
                {
                    "event_type": "settings.installation_count",
                    "category": "settings",
                    "actor_user_id": OWNER_ID,
                    "actor_role": "owner",
                    "target_id": count.ENABLED_KEY,
                    "metadata": {"enabled": False},
                }
            ],
        )

        answer = self._call(connection, method="POST", body={"installation_count_enabled": True})
        self.assertIs(answer.get_json()["settings"]["installation_count_enabled"], True)
        self.assertIs(connection.settings[count.ENABLED_KEY], True)

    def test_what_the_owner_stored_is_what_the_worker_reads(self):
        connection = _SettingsConnection()
        self._call(connection, method="POST", body={"installation_count_enabled": False})
        database = FakeDatabase(connection.settings)
        post = Recorder(database)
        self.assertEqual(run(database, post, utc(2026, 11, 9)), "disabled_by_owner")
        self.assertEqual(post.calls, [])

    def test_another_owner_setting_does_not_touch_this_one(self):
        connection = _SettingsConnection()
        self._call(connection, method="POST", body={"movievault_v2_contribution_enabled": True})
        self.assertNotIn(count.ENABLED_KEY, connection.settings)

    def test_an_admin_who_is_not_the_owner_is_refused(self):
        connection = _SettingsConnection(role="admin")
        answer = self._call(connection, method="POST", body={"installation_count_enabled": False})
        self.assertEqual(answer.status_code, 403)
        self.assertNotIn(count.ENABLED_KEY, connection.settings)
        self.assertEqual(self._call(connection).status_code, 403)

    def test_nobody_signed_in_is_refused(self):
        connection = _SettingsConnection()
        answer = self._call(connection, method="POST", body={"installation_count_enabled": False}, user=None)
        self.assertEqual(answer.status_code, 401)
        self.assertNotIn(count.ENABLED_KEY, connection.settings)

    def test_the_operators_switch_is_reported_so_the_ui_can_say_so(self):
        connection = _SettingsConnection()
        answer = self._call(connection, environ={count.DISABLE_ENV: "off"})
        settings = answer.get_json()["settings"]
        self.assertIs(settings["installation_count_disabled_by_environment"], True)
        # The owner's own choice is reported as it is, not folded into it.
        self.assertIs(settings["installation_count_enabled"], True)


class OwnerSurfaceTests(unittest.TestCase):
    """The card, the notice and their wording, read from the source.

    The PWA is one inline script with no test runner of its own, so the things
    worth holding are checked as text: the switch exists and posts the right
    key, the notice is opened by its kind, and every string the owner reads has
    a catalog entry in every locale.
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BACKEND_DIR, "next_views_ui.py"), encoding="utf-8") as handle:
            cls.ui = handle.read()
        cls.catalogs = {}
        for path in sorted(glob.glob(os.path.join(I18N_DIR, "*.json"))):
            with open(path, encoding="utf-8") as handle:
                cls.catalogs[os.path.basename(path)] = json.load(handle)
        cls.source = cls.catalogs["en-US.json"]

    def test_the_card_sits_with_the_other_owner_settings(self):
        self.assertIn('<div class="hidden" id="installationCountSettingRow"></div>', self.ui)
        self.assertIn("renderInstallationCountSetting();", self.ui)
        self.assertIn("JSON.stringify({installation_count_enabled: !!enabled})", self.ui)

    def test_the_card_is_drawn_for_the_owner_only(self):
        start = self.ui.index("function canManageInstallationCount()")
        body = self.ui[start:self.ui.index("}", self.ui.index("return", start)) + 1]
        self.assertIn('currentRole() === "owner"', body)

    def test_the_switch_is_locked_when_the_operator_turned_it_off(self):
        self.assertIn("const lockedOff = installationCount.disabledByEnvironment;", self.ui)
        self.assertIn('${lockedOff ? "disabled" : ""}', self.ui)
        self.assertIn('"preferences.installationCountEnvDisabled"', self.ui)

    def test_opening_the_notice_leads_to_the_setting(self):
        self.assertIn('if ((notification.payload || {}).kind === "installation_count_notice") {', self.ui)
        start = self.ui.index("function openInstallationCountSetting()")
        body = self.ui[start:start + 400]
        self.assertIn('setProfileTab("preferences");', body)
        self.assertIn('setPreferenceTab("collectors");', body)
        # The kind the PWA recognises is the kind the server stores.
        self.assertEqual(count.NOTICE_KIND, "installation_count_notice")

    def test_every_string_has_its_inline_fallback_from_the_source_catalog(self):
        """``tNext(key, fallback)`` renders the fallback whenever the catalog
        has not loaded, so the two must not drift apart."""
        for key in UI_STRING_KEYS:
            self.assertIn(f'tNext("{key}", "{self.source[key]}")', self.ui, key)

    def test_the_stored_notice_is_the_english_wording_of_the_catalog(self):
        """Clients that do not know the kind show the stored text."""
        self.assertEqual(count.NOTICE_TITLE, self.source["notifications.installationCountTitle"])
        self.assertEqual(
            count.NOTICE_BODY,
            self.source["notifications.installationCountBody"]
            .replace("{preferences}", self.source["preferences.title"])
            .replace("{collectors}", self.source["preferences.tabCollectors"]),
        )

    def test_the_english_wording_says_what_the_owner_has_to_know(self):
        title = self.source["notifications.installationCountTitle"]
        body = self.source["notifications.installationCountBody"]
        self.assertIn("nonymous installation count", title)
        self.assertIn("is on", title)
        card = self.source["preferences.cardInstallationCountHelp"]
        for text in (body, card):
            for fragment in ("api.discvault.eu", "version", "days since", "dentifier", "collection", "account"):
                self.assertIn(fragment, text)
        self.assertIn("turn it off", body)

    def test_every_locale_carries_its_own_wording(self):
        """Present and non-empty is what the completeness suite checks. A
        locale that echoes the English would pass that and still be untranslated."""
        self.assertEqual(len(self.catalogs), 29)
        for locale, catalog in self.catalogs.items():
            for key in UI_STRING_KEYS:
                self.assertTrue(catalog.get(key, "").strip(), f"{locale}: {key}")
                if locale != "en-US.json":
                    self.assertNotEqual(catalog[key], self.source[key], f"{locale} still shows the English {key}")

    def test_the_names_that_must_survive_translation_do(self):
        for locale, catalog in self.catalogs.items():
            body = catalog["notifications.installationCountBody"]
            for placeholder in ("{preferences}", "{collectors}"):
                self.assertIn(placeholder, body, locale)
            self.assertIn("api.discvault.eu", body, locale)
            self.assertIn("api.discvault.eu", catalog["preferences.cardInstallationCountHelp"], locale)
            self.assertIn(
                "DISCVAULT_INSTALLATION_COUNT",
                catalog["preferences.installationCountEnvDisabled"],
                locale,
            )


if __name__ == "__main__":
    unittest.main()
