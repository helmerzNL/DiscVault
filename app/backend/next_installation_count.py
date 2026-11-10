"""Anonymous count of active installations: this server's daily report.

Once per UTC day a running DiscVault server tells ``api.discvault.eu`` that it
is in use. The report carries the released version and the number of days since
this server's own previous accepted report, and nothing else:

    POST https://api.discvault.eu/v2/presence
    {"protocol":"presence-1","platform":"server","version":"26.11.0","sinceDays":3}

**Nothing in it may tell one server from another.** No instance id, no
contribution identity, no hostname, no user count, no cookie, no credential.
DiscVault promises no tracking, and a count only fits inside that promise while
the receiver cannot follow a server from one day to the next -- which is why
the server deduplicates itself with ``sinceDays`` instead of being recognised.
A field that looks harmless later ("just the country") is a new decision with
its own ADR, never an addition here.

The rules this module implements are specified in ``Flux76HQ/App-Guidance`` ->
``projects/discvault/specs/active-installation-count.md`` (section 4 for the
client rules, section 6 for the server); the decision is ADR-0020:

* at most one accepted report per UTC day, and only a ``2xx`` answer counts;
* a failed attempt leaves the last-accepted date alone, so the next
  ``sinceDays`` still refers to a report that was really counted;
* never more than one attempt an hour, never more than six a UTC day, and a
  ``429`` with ``Retry-After`` is honoured when it asks for longer;
* on by default, with two off switches -- the owner setting and the
  ``DISCVAULT_INSTALLATION_COUNT`` environment variable -- and with either one
  off there is no request to the endpoint at all;
* only a released image reports: a source checkout or a development build
  sends nothing;
* the owner is told first: a one-time notice lands in the owner's notification
  inbox before the first report can leave, and the PWA keeps it on the screen
  until the owner has answered it (``pending_owner_notice``). On a new
  installation the setup wizard says the same thing before there is an owner
  at all (``is_reporting``).

State lives in ``app_settings``, in this server's own database, so a restore
from backup continues the same interval instead of appearing as a new
installation.

The origin is ``api.discvault.eu`` and deliberately not the MovieVault origin
the catalogue is read from: the report shares no host, no opener and no
credential with the catalogue, resolver or contribution calls, and it does not
depend on the MovieVault plugin being enabled.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Mapping

from psycopg.types.json import Jsonb

try:
    from .next_movievault_v2 import MovieVaultV2Error
    from .next_movievault_v2 import _NoRedirect
    from .next_movievault_v2 import normalize_origin
    from .next_notifications import create_user_notification
    from .next_ownership import instance_owner_id
    from .next_settings_store import _set_setting
    from .next_settings_store import _table_exists
except ImportError:  # pragma: no cover - supports python next_worker.py
    from next_movievault_v2 import MovieVaultV2Error
    from next_movievault_v2 import _NoRedirect
    from next_movievault_v2 import normalize_origin
    from next_notifications import create_user_notification
    from next_ownership import instance_owner_id
    from next_settings_store import _set_setting
    from next_settings_store import _table_exists

logger = logging.getLogger(__name__)

PROTOCOL = "presence-1"
PLATFORM = "server"
PRESENCE_PATH = "/v2/presence"

#: Not the MovieVault origin, and not derived from it. Overridable only out of
#: band, for a non-standard deployment or a test stack; an invalid override
#: falls back to this default, like ``MOVIEVAULT_V2_ORIGIN`` does.
DEFAULT_API_ORIGIN = "https://api.discvault.eu"
API_ORIGIN_ENV = "DISCVAULT_API_ORIGIN"

#: The operator's off switch. It wins over the owner setting, so an operator
#: can have the count off before the first start.
DISABLE_ENV = "DISCVAULT_INSTALLATION_COUNT"
_OFF_VALUES = frozenset({"off", "false", "0", "no"})

#: Owner setting. **Absent means on** (ADR-0020, decision 5): the count is on
#: for a new installation and for an existing one after the upgrade, which is
#: the opposite of ``movievault_v2_contribution_enabled`` and chosen on purpose.
ENABLED_KEY = "installation_count_enabled"
#: UTC date (``YYYY-MM-DD``) of the last report the service accepted.
LAST_ACCEPTED_ON_KEY = "installation_count_last_accepted_on"
#: One row for the attempt bookkeeping -- ``day``, ``count``, ``lastAttemptAt``
#: and an optional ``retryNotBefore`` -- because they have to change together
#: for the claim to be a single compare-and-set.
ATTEMPT_KEY = "installation_count_attempt"
#: When the one-time owner notice was created. Its presence is the whole fact:
#: the notice is never created a second time, whether or not it was read.
NOTICE_CREATED_AT_KEY = "installation_count_notice_created_at"

_STATE_KEYS = (ENABLED_KEY, LAST_ACCEPTED_ON_KEY, ATTEMPT_KEY, NOTICE_CREATED_AT_KEY)

USER_AGENT = "DiscVault-InstallationCount"
TIMEOUT_SECONDS = 10
MIN_RETRY_INTERVAL = timedelta(hours=1)
MAX_ATTEMPTS_PER_DAY = 6
#: ``Retry-After`` comes from a response, so it is bounded before it is stored:
#: an absurd value must not silence a server for years.
MAX_RETRY_AFTER = timedelta(days=7)

#: How often the worker's poll loop lets one check through. The loop polls
#: every two seconds; the database is asked about the count this often instead.
CHECK_INTERVAL_SECONDS = 300.0

#: ``X.Y.Z``, ASCII digits only. Anything else -- ``build-<sha>``, ``git
#: describe`` output, ``next-dev``, ``unknown`` -- is a development build, and
#: those are not installations.
_RELEASE_VERSION_RE = re.compile(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,4}")

#: Where the version comes from: the value the published image was built with
#: (both Dockerfiles set it from ``app/VERSION``). Deliberately not
#: ``versioning.backend_version()``: that falls back to the ``app/VERSION``
#: file, which is a plain ``X.Y.Z`` in every source checkout too, so a
#: developer running the worker from a clone would be counted as a server.
#: A process that was not started from a released image has no such variable
#: and reports nothing.
IMAGE_VERSION_ENV = "DISCVAULT_IMAGE_VERSION"

#: The notice the owner gets once, before the first report. The stored text is
#: the English fallback for clients that do not know the kind; the PWA renders
#: its own localized wording from ``payload.kind`` (``notificationDisplayTitle``
#: in ``next_views_ui.py``), like the unconfigured-plugins notice.
NOTICE_KIND = "installation_count_notice"
NOTICE_TITLE = "Anonymous installation count is on"
#: Why the count exists, said before what it sends: a notice that only lists
#: what leaves the server reads as surveillance, however little it lists.
NOTICE_PURPOSE = (
    "The count is only there to estimate how much DiscVault is used, so that "
    "the MovieVault backend can be scaled to match."
)
NOTICE_DETAILS = (
    "Once a day this server tells api.discvault.eu that it is running: its "
    "DiscVault version and the number of days since its previous report. No "
    "identifier, and no account or collection data. You can turn it off under "
    "Preferences > Collectors."
)
NOTICE_BODY = f"{NOTICE_PURPOSE} {NOTICE_DETAILS}"
NOTICE_URL = "/profile"


# --- pure rules ---------------------------------------------------------------


def api_origin(environ: Mapping[str, str] | None = None) -> str:
    """The origin reports go to: the override when valid, the default otherwise."""
    override = (os.environ if environ is None else environ).get(API_ORIGIN_ENV)
    if override and override.strip():
        try:
            return normalize_origin(override)
        except MovieVaultV2Error:
            pass
    return DEFAULT_API_ORIGIN


def presence_url(environ: Mapping[str, str] | None = None) -> str:
    return f"{api_origin(environ)}{PRESENCE_PATH}"


def disabled_by_environment(environ: Mapping[str, str] | None = None) -> bool:
    """True when the operator switched the count off for this process."""
    value = (os.environ if environ is None else environ).get(DISABLE_ENV)
    return str(value or "").strip().lower() in _OFF_VALUES


def owner_setting_enabled(stored: Any) -> bool:
    """The owner setting as stored, where a missing value means on."""
    if stored is None:
        return True
    if isinstance(stored, str):
        return stored.strip().lower() not in _OFF_VALUES
    return bool(stored)


def is_release_version(version: Any) -> bool:
    return isinstance(version, str) and _RELEASE_VERSION_RE.fullmatch(version) is not None


def release_version(environ: Mapping[str, str] | None = None) -> str | None:
    """The released version this process was built as, or ``None`` when it is not a release."""
    value = str((os.environ if environ is None else environ).get(IMAGE_VERSION_ENV) or "").strip()
    return value if is_release_version(value) else None


def since_days(today: date, last_accepted_on: date | None) -> int | None:
    """Whole UTC days since the last accepted report; ``None`` when there was none."""
    if last_accepted_on is None:
        return None
    return (today - last_accepted_on).days


def build_payload(version: str, days_since: int | None) -> dict[str, Any]:
    """The whole report. Exactly these four fields, for the reason at the top."""
    return {
        "protocol": PROTOCOL,
        "platform": PLATFORM,
        "version": version,
        "sinceDays": days_since,
    }


def encode_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ReportPlan:
    """What one check decided, before anything is written or sent."""

    due: bool
    reason: str
    today: date | None = None
    since_days: int | None = None
    #: The attempt bookkeeping to claim before the request is made.
    attempt: dict[str, Any] | None = None


def plan_report(now: datetime, *, last_accepted_on: Any, attempt: Any) -> ReportPlan:
    """Decide whether a report is due at ``now``, from the stored state alone.

    Wherever the clock disagrees with the stored state -- a last-accepted date
    or a last attempt that lies in the future because the clock was set back --
    the answer is to send nothing until the clock has caught up. ``sinceDays``
    may never be negative, and an extra report is the one mistake that cannot
    be taken back.
    """
    now = now.astimezone(timezone.utc)
    today = now.date()
    last_accepted = _parse_date(last_accepted_on)
    if last_accepted is not None and last_accepted >= today:
        return ReportPlan(False, "already_reported")

    state = attempt if isinstance(attempt, dict) else {}
    attempts_today = 0
    if _parse_date(state.get("day")) == today:
        count = state.get("count")
        attempts_today = count if isinstance(count, int) and not isinstance(count, bool) else 0
    if attempts_today >= MAX_ATTEMPTS_PER_DAY:
        return ReportPlan(False, "attempt_limit")
    # Every attempt counts here, accepted or not: the rule this keeps is "never
    # more than one request an hour", which also covers a crash between the
    # claim and the answer.
    last_attempt_at = _parse_datetime(state.get("lastAttemptAt"))
    if last_attempt_at is not None and now < last_attempt_at + MIN_RETRY_INTERVAL:
        return ReportPlan(False, "retry_interval")
    retry_not_before = _parse_datetime(state.get("retryNotBefore"))
    if retry_not_before is not None and now < retry_not_before:
        return ReportPlan(False, "retry_after")

    return ReportPlan(
        True,
        "due",
        today=today,
        since_days=since_days(today, last_accepted),
        attempt={
            "day": today.isoformat(),
            "count": attempts_today + 1,
            "lastAttemptAt": now.isoformat(),
        },
    )


def retry_after_delay(value: Any, now: datetime) -> timedelta | None:
    """A ``Retry-After`` header as a delay, bounded by ``MAX_RETRY_AFTER``.

    Both forms the header allows: a number of seconds, or an HTTP date.
    """
    text = str(value or "").strip()
    if not text:
        return None
    if text.isascii() and text.isdigit():
        try:
            seconds = int(text)
        except ValueError:  # more digits than int() parses: beyond any bound
            return MAX_RETRY_AFTER
        return min(timedelta(seconds=min(seconds, 10**9)), MAX_RETRY_AFTER)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(timedelta(0), min(when - now, MAX_RETRY_AFTER))


@dataclass(frozen=True)
class ReportOutcome:
    accepted: bool
    #: Short and value-free: it is the only part of an answer that is logged.
    reason: str
    retry_not_before: datetime | None = None


def interpret_response(status: int | None, retry_after: Any, now: datetime) -> ReportOutcome:
    """Only a ``2xx`` is a counted report. Everything else is a failed attempt."""
    if status is None:
        return ReportOutcome(False, "network_error")
    if 200 <= status < 300:
        return ReportOutcome(True, "accepted")
    if status == 429:
        delay = retry_after_delay(retry_after, now)
        if delay is not None and delay > MIN_RETRY_INTERVAL:
            return ReportOutcome(False, "http_429", now + delay)
    return ReportOutcome(False, f"http_{status}")


# --- transport ----------------------------------------------------------------


def post_report(url: str, body: bytes, *, timeout: float = TIMEOUT_SECONDS) -> tuple[int | None, str | None]:
    """Send one report. Returns the status and the ``Retry-After`` header.

    Its own opener on every call: no cookie jar, no credential, nothing shared
    with any other outbound call, and no redirect is followed -- a redirect is
    simply an answer that is not a ``2xx``. The response body is never read.
    """
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            return int(response.status), response.headers.get("Retry-After")
    except urllib.error.HTTPError as exc:
        try:
            return int(exc.code), exc.headers.get("Retry-After") if exc.headers else None
        finally:
            exc.close()
    except (OSError, http.client.HTTPException, ValueError):
        # Unreachable, timed out, cut off, or not HTTP at all. A server behind
        # an egress firewall lands here every time, and is simply not counted.
        return None, None


# --- state --------------------------------------------------------------------


def _read_state(conn) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT key, value FROM app_settings WHERE key = ANY(%s) AND is_secret = false",
            (list(_STATE_KEYS),),
        )
        return {row["key"]: row["value"] for row in cur.fetchall()}


def _insert_if_absent(conn, key: str, value: Any) -> bool:
    """Store ``value`` under ``key`` unless the key exists. True when it was stored.

    A second writer waits on the first one's uncommitted row and then sees the
    conflict, so exactly one of any number of concurrent callers gets True.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app_settings (key, value, is_secret, updated_at)
            VALUES (%s, %s, false, now())
            ON CONFLICT (key) DO NOTHING
            """,
            (key, Jsonb(value)),
        )
        return cur.rowcount == 1


def _claim_attempt(conn, stored: Mapping[str, Any], attempt: dict[str, Any]) -> bool:
    """Take this attempt, or learn that another worker already did.

    A compare-and-set on the attempt row as it was read: two workers that both
    decided a report is due read the same row, and only one of them can replace
    it. The row is never deleted, so every claim changes it and a stale reader
    always loses.
    """
    if ATTEMPT_KEY not in stored:
        return _insert_if_absent(conn, ATTEMPT_KEY, attempt)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE app_settings SET value=%s, updated_at=now() WHERE key=%s AND value=%s",
            (Jsonb(attempt), ATTEMPT_KEY, Jsonb(stored[ATTEMPT_KEY])),
        )
        return cur.rowcount == 1


def ensure_owner_notice(conn, now: datetime) -> bool:
    """Tell the instance owner, once, that the count is on. True once told.

    The owner only: not the other admins, not ordinary users, and without a
    push -- it is a notice to find in the inbox, not an event. With no owner
    account there is nobody to tell and no owner setting to turn it off with,
    so this answers False and no report is sent until there is one.

    The setting row and the notification are written in the caller's
    transaction, so they commit together or not at all, and the conflict on the
    setting key is what stops a second worker or a restart from telling twice.
    """
    for table in ("users", "user_roles", "roles", "user_notifications"):
        if not _table_exists(conn, table):
            return False
    owner_id = instance_owner_id(conn)
    if not owner_id:
        return False
    if not _insert_if_absent(conn, NOTICE_CREATED_AT_KEY, now.astimezone(timezone.utc).isoformat()):
        return True
    create_user_notification(
        conn,
        owner_id,
        title=NOTICE_TITLE,
        body=NOTICE_BODY,
        url=NOTICE_URL,
        pref_key="app_updates",
        payload={"kind": NOTICE_KIND},
        send_push=False,
    )
    return True


def _state_if_reporting(
    conn,
    environ: Mapping[str, str] | None,
    version: str | None,
) -> dict[str, Any] | None:
    """The stored state when this server takes part in the count, else ``None``.

    The three questions the check asks before it does anything -- neither off
    switch set, and a released image -- for the places that *tell* somebody
    about the count. They must not announce a count that is not running.
    """
    if disabled_by_environment(environ):
        return None
    if version is None:
        version = release_version(environ)
    if not is_release_version(version):
        return None
    if not _table_exists(conn, "app_settings"):
        return None
    stored = _read_state(conn)
    if not owner_setting_enabled(stored.get(ENABLED_KEY)):
        return None
    return stored


def is_reporting(
    conn,
    *,
    environ: Mapping[str, str] | None = None,
    version: str | None = None,
) -> bool:
    """Whether this server reports. What the setup wizard asks before it says so."""
    return _state_if_reporting(conn, environ, version) is not None


def pending_owner_notice(
    conn,
    user_id: Any,
    *,
    now: datetime | None = None,
    environ: Mapping[str, str] | None = None,
    version: str | None = None,
) -> dict[str, Any] | None:
    """The one-time notice this user has not answered yet, or ``None``.

    A row in the inbox tells nobody anything by itself: the PWA shows no unread
    count outside the notifications page, so the notice sat there unseen and
    the owner of a freshly upgraded server was told nothing. The PWA therefore
    asks for it with every snapshot and keeps it on the screen until the owner
    closes it or opens the setting; either one marks the row read, on every
    device at once.

    The notice is created here when it is owed and still missing. On a new
    installation the worker's first check runs before there is an owner
    account, and its next one is up to ``CHECK_INTERVAL_SECONDS`` later -- the
    owner would open the app to nothing and learn of the count from the inbox,
    if ever. The once-only key is the same one the worker uses, so whichever
    comes first creates it and the other finds it there.

    Only while this server really reports: with either off switch set, or on a
    build that is not a released image, there is nothing to announce and an
    older unread notice is not put back on the screen.
    """
    if not user_id:
        return None
    stored = _state_if_reporting(conn, environ, version)
    if stored is None or not _table_exists(conn, "user_notifications"):
        return None
    if NOTICE_CREATED_AT_KEY not in stored:
        moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if not ensure_owner_notice(conn, moment):
            return None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id
            FROM user_notifications
            WHERE user_id=%s AND read_at IS NULL AND payload->>'kind' = %s
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            """,
            (user_id, NOTICE_KIND),
        )
        row = cur.fetchone()
    return {"id": str(row["id"])} if row else None


# --- the check ----------------------------------------------------------------

ConnectionFactory = Callable[[], Any]
#: Sends one report; answers with the status (``None`` when there was no
#: answer) and the ``Retry-After`` header. ``post_report`` unless a test says so.
PostReport = Callable[[str, bytes], tuple[int | None, str | None]]


def report_if_due(
    connect: ConnectionFactory,
    *,
    now: datetime | None = None,
    environ: Mapping[str, str] | None = None,
    version: str | None = None,
    post: PostReport | None = None,
) -> str:
    """Run one check and send the report when one is due. Never raises.

    Returns a short code for what happened, which is what the tests read.
    """
    try:
        return _report_if_due(connect, now=now, environ=environ, version=version, post=post)
    except Exception as exc:  # noqa: BLE001 - the count must never fail anything
        logger.warning("Installation count: the check could not run (%s).", type(exc).__name__)
        return "error"


def _report_if_due(
    connect: ConnectionFactory,
    *,
    now: datetime | None,
    environ: Mapping[str, str] | None,
    version: str | None,
    post: PostReport | None,
) -> str:
    if disabled_by_environment(environ):
        return "disabled_by_environment"
    if version is None:
        version = release_version(environ)
    if not is_release_version(version):
        return "not_a_release"
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    with connect() as conn:
        if not _table_exists(conn, "app_settings"):
            return "not_ready"
        stored = _read_state(conn)
        if not owner_setting_enabled(stored.get(ENABLED_KEY)):
            return "disabled_by_owner"
        # The owner is told before anything is sent. The notice commits with
        # this transaction, which ends before the request below is made.
        if NOTICE_CREATED_AT_KEY not in stored and not ensure_owner_notice(conn, now):
            return "no_owner_to_tell"
        plan = plan_report(
            now,
            last_accepted_on=stored.get(LAST_ACCEPTED_ON_KEY),
            attempt=stored.get(ATTEMPT_KEY),
        )
        if not plan.due:
            return plan.reason
        if not _claim_attempt(conn, stored, plan.attempt):
            return "claimed_elsewhere"

    # The claim is committed and the connection is closed: no transaction is
    # held open across the network.
    status, retry_after = (post or post_report)(
        presence_url(environ),
        encode_payload(build_payload(version, plan.since_days)),
    )
    outcome = interpret_response(status, retry_after, now)

    if outcome.accepted:
        with connect() as conn:
            _set_setting(conn, LAST_ACCEPTED_ON_KEY, plan.today.isoformat())
        return "accepted"

    # A failed attempt stores nothing but its own bookkeeping, which the claim
    # already wrote. The last-accepted date is untouched.
    if outcome.retry_not_before is not None:
        with connect() as conn:
            _set_setting(
                conn,
                ATTEMPT_KEY,
                {**plan.attempt, "retryNotBefore": outcome.retry_not_before.isoformat()},
            )
    # One line a day at most, however many attempts fail: a server that cannot
    # reach the endpoint is a normal state, not an incident.
    log = logger.warning if plan.attempt["count"] == 1 else logger.debug
    log(
        "Installation count: the daily report was not accepted (%s); it is retried later "
        "and nothing else depends on it. See docs/installation-count.md.",
        outcome.reason,
    )
    return "failed"


# --- the worker's hook --------------------------------------------------------

_next_check_at = 0.0
_check_in_flight = threading.Lock()


def _spawn(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name="installation-count", daemon=True).start()


def schedule_check(
    connect: ConnectionFactory,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    environ: Mapping[str, str] | None = None,
    spawn: Callable[[Callable[[], None]], None] = _spawn,
) -> bool:
    """The worker loop's hook. Returns True when a check was started.

    Called on every poll. Almost every call is one clock comparison; every
    ``CHECK_INTERVAL_SECONDS`` one check is let through. That check runs on a
    thread of its own, because the socket timeout does not bound a name lookup
    and a server behind an egress firewall would otherwise stall its job queue
    for as long as its resolver takes to give up.
    """
    global _next_check_at
    moment = monotonic()
    if moment < _next_check_at:
        return False
    _next_check_at = moment + CHECK_INTERVAL_SECONDS
    if disabled_by_environment(environ):
        return False
    if not _check_in_flight.acquire(blocking=False):
        return False

    def run() -> None:
        try:
            report_if_due(connect, environ=environ)
        finally:
            _check_in_flight.release()

    try:
        spawn(run)
    except RuntimeError:  # the interpreter cannot start another thread
        _check_in_flight.release()
        return False
    return True
