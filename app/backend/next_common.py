"""Shared infrastructure helpers for the DiscVault Next backend.

These helpers have no application-domain dependencies. They are extracted from
``next_app.py`` so that domain modules can reuse them without importing the
oversized application module. ``next_app.py`` re-imports every name defined here
to preserve its public surface (tests and older modules continue to import the
same names from ``next_app``).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from flask import jsonify, request


class NextApiError(RuntimeError):
    """Expected API error with a caller-facing status code."""

    def __init__(self, message: str, status_code: int = 500, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [json_ready(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def response(payload: dict[str, Any], status: int = 200):
    return jsonify(json_ready(payload)), status


_TABLE_EXISTS_CACHE_ATTR = "_dv_table_exists_cache"


def _table_exists_cache(conn) -> dict[str, bool] | None:
    """The memo belonging to one connection, or None if it cannot hold one.

    Attached to the connection object rather than kept in a module-level dict so
    it dies with the connection, and so two requests running in the same worker
    never share one. Callers hand this module stand-in objects in a few places
    (the metadata pipeline runs with a connection-shaped object that has no
    cursor), so an object that refuses attributes simply gets no memo instead of
    an error -- a cache must never be the reason something fails.
    """

    try:
        cache = getattr(conn, _TABLE_EXISTS_CACHE_ATTR, None)
        if cache is None:
            cache = {}
            setattr(conn, _TABLE_EXISTS_CACHE_ATTR, cache)
        return cache
    except (AttributeError, TypeError):  # pragma: no cover - exotic stand-ins
        return None


def forget_table_existence(conn) -> None:
    """Drop the memo, for the one caller that changes what exists.

    Nothing in the request path creates or drops a table -- migrations run on
    their own connection in ``next_database.py`` -- so this exists for restore
    and for tests, not for normal operation.
    """

    try:
        setattr(conn, _TABLE_EXISTS_CACHE_ATTR, None)
    except (AttributeError, TypeError):  # pragma: no cover - exotic stand-ins
        pass


def table_exists(conn, table_name: str) -> bool:
    """Is this table present? Memoised for the life of the connection.

    This is asked far more often than it reads: a warm ``/api/next/app/snapshot``
    issued 91 queries and 56 of them were this one round trip, repeated for the
    same handful of table names. ``/api/next/collection/movies`` was 12 of 18.
    Every one of them is a ``to_regclass`` lookup answering a question that
    cannot change while the connection is open.

    The memo is per connection, so a schema that changed between requests is
    seen by the next request. Within a request it makes the answer *consistent*
    as well as cheap -- previously a table created halfway through a request
    would have been absent to the first caller and present to the second.
    """

    cache = _table_exists_cache(conn)
    if cache is not None and table_name in cache:
        return cache[table_name]
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS table_name", (f"public.{table_name}",))
        row = cur.fetchone()
    present = bool(row and row["table_name"])
    if cache is not None:
        cache[table_name] = present
    return present


def count_table(conn, table_name: str) -> int:
    if not table_exists(conn, table_name):
        return 0
    with conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) AS count FROM "{table_name}"')
        row = cur.fetchone()
    return int(row["count"] if row else 0)


def parse_int_arg(name: str, default: int, *, minimum: int = 0, maximum: int = 1000) -> int:
    raw = request.args.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise NextApiError(f"Invalid integer value for {name}", 400) from exc
    return min(max(value, minimum), maximum)


def parse_uuid(value: Any, field_name: str) -> UUID | None:
    if value in (None, ""):
        return None
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise NextApiError(f"Invalid UUID for {field_name}", 400) from exc


def parse_bool_value(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def parse_uuid_list(values: Any, field_name: str, *, maximum: int = 250) -> list[UUID]:
    if values in (None, ""):
        return []
    if not isinstance(values, list):
        raise NextApiError(f"{field_name} must be an array", 400)
    if len(values) > maximum:
        raise NextApiError(f"At most {maximum} {field_name} values are allowed", 400)
    parsed: list[UUID] = []
    seen: set[UUID] = set()
    for value in values:
        item = parse_uuid(value, field_name)
        if not item:
            raise NextApiError(f"{field_name} must not contain empty values", 400)
        if item not in seen:
            parsed.append(item)
            seen.add(item)
    return parsed
