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


def table_exists(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS table_name", (f"public.{table_name}",))
        row = cur.fetchone()
    return bool(row and row["table_name"])


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
