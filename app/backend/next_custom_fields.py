"""Instance-defined custom fields: their vocabulary, and value typing.

The owner of an instance defines fields at runtime; every film may carry a value
for each. This module owns what a definition may say and which storage column a
value lands in. The wire form is sync-contract §4e; the storage rationale is
App-Guidance `projects/discvault/specs/custom-fields.md`.

The one thing worth holding on to while reading: **a value is stored in the
column matching its field's declared type**, and that pairing cannot be a
database CHECK because it needs a join. So it is enforced here, on every write,
and `value_column_for_type` is the single place that decides it.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

try:  # pragma: no cover - exercised indirectly by both layouts
    from .next_common import NextApiError
    from .next_common import table_exists
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_common import NextApiError
    from next_common import table_exists


#: The closed input vocabulary. Closed here on purpose: this is the side we
#: control, and a person defining a field picks from a list. Contract §4e.4's
#: "open at the edge" is a *client* rule about a type it has never heard of --
#: an instance never receives a type it did not itself define.
FIELD_TYPES: tuple[str, ...] = ("text", "number", "date", "boolean", "select")

#: Which column a value of each type lands in. `select` shares `value_text` with
#: `text` because what it stores is the chosen option's key -- store the code,
#: show the name, the same rule the language and region fields follow.
_VALUE_COLUMN_BY_TYPE: dict[str, str] = {
    "text": "value_text",
    "select": "value_text",
    "number": "value_number",
    "date": "value_date",
    "boolean": "value_boolean",
}

VALUE_COLUMNS: tuple[str, ...] = ("value_text", "value_number", "value_date", "value_boolean")

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")

TEXT_VALUE_LIMIT = 2000
NAME_LIMIT = 80
MAX_SELECT_OPTIONS = 100


def normalize_field_type(value: Any) -> str:
    """Return a supported field type, or raise."""
    field_type = str(value or "").strip().lower()
    if field_type not in FIELD_TYPES:
        raise NextApiError(
            f"fieldType must be one of {', '.join(FIELD_TYPES)}", 400
        )
    return field_type


def value_column_for_type(field_type: str) -> str:
    """The storage column a value of this type belongs in."""
    return _VALUE_COLUMN_BY_TYPE[normalize_field_type(field_type)]


def normalize_field_key(value: Any, *, name: str = "") -> str:
    """Return a stable slug for a definition.

    Derived from the name when none is given, because an owner typing "Rip
    status" should not also have to invent an identifier. The slug is what a
    saved filter names and what the wire carries, so it must survive a later
    rename of the human-facing label -- which is exactly why the two are separate
    columns rather than one.
    """
    raw = str(value or "").strip().lower()
    if not raw and name:
        # Derived, so truncating is the kindest thing to do: the owner never saw
        # this string and is not being overruled.
        raw = re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")[:48]
    if not _KEY_RE.match(raw):
        # An explicitly given key is refused rather than trimmed. Truncating one
        # the owner typed would store something they did not choose, under a name
        # that saved filters and stored values then depend on.
        raise NextApiError(
            "key must start with a letter, contain only lowercase letters, digits and "
            "underscores, and be at most 48 characters",
            400,
        )
    return raw


def normalize_options(value: Any, *, field_type: str) -> list[dict[str, str]]:
    """Validate a select field's options.

    Returns `[]` for every other type rather than raising: a field that stops
    being a select keeps its rows valid, and options nobody reads are harmless
    where a hard error mid-edit is not.
    """
    if normalize_field_type(field_type) != "select":
        return []
    if not isinstance(value, (list, tuple)):
        raise NextApiError("options must be an array for a select field", 400)
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, str):
            item = {"key": item, "label": item}
        if not isinstance(item, dict):
            raise NextApiError("each option must be an object with key and label", 400)
        key = normalize_field_key(item.get("key"), name=str(item.get("label") or ""))
        if key in seen:
            continue
        seen.add(key)
        label = str(item.get("label") or key).strip()[:NAME_LIMIT]
        options.append({"key": key, "label": label})
    if not options:
        raise NextApiError("a select field needs at least one option", 400)
    if len(options) > MAX_SELECT_OPTIONS:
        raise NextApiError(f"a select field takes at most {MAX_SELECT_OPTIONS} options", 400)
    return options


def normalize_field_name(value: Any) -> str:
    """The owner's label. Never translated -- see the module docstring."""
    name = str(value or "").strip()
    if not name:
        raise NextApiError("name is required", 400)
    return name[:NAME_LIMIT]


def normalize_field_value(value: Any, *, field: dict[str, Any]) -> tuple[str, Any] | None:
    """Return `(column, value)` for one field's value, or None to clear it.

    None means "no value", and it is returned for an empty string as well as for
    a literal null. That is deliberate: a text input a user emptied and a field
    they never filled are the same fact, and the row is then deleted rather than
    stored holding an empty string that sorts before every real answer.

    `False` and `0` are values, not emptiness, and the explicit checks below are
    what keep them from being swallowed by a falsy test.
    """
    field_type = normalize_field_type(field.get("field_type") or field.get("fieldType"))
    column = value_column_for_type(field_type)

    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None

    if field_type == "boolean":
        if isinstance(value, bool):
            return column, value
        text = str(value).strip().lower()
        if text in {"true", "yes", "1"}:
            return column, True
        if text in {"false", "no", "0"}:
            return column, False
        raise NextApiError(f"{field.get('key')}: value must be true or false", 400)

    if field_type == "number":
        try:
            number = Decimal(str(value).strip())
        except (InvalidOperation, ValueError):
            raise NextApiError(f"{field.get('key')}: value must be a number", 400)
        if not number.is_finite():
            raise NextApiError(f"{field.get('key')}: value must be a finite number", 400)
        return column, number

    if field_type == "date":
        if isinstance(value, datetime):
            return column, value.date()
        if isinstance(value, date):
            return column, value
        text = str(value).strip()[:10]
        try:
            return column, date.fromisoformat(text)
        except ValueError:
            raise NextApiError(f"{field.get('key')}: value must be a date as YYYY-MM-DD", 400)

    if field_type == "select":
        chosen = str(value).strip()
        allowed = {
            str(option.get("key"))
            for option in (field.get("options") or [])
            if isinstance(option, dict)
        }
        if chosen not in allowed:
            raise NextApiError(
                f"{field.get('key')}: value must be one of {', '.join(sorted(allowed)) or '(no options)'}",
                400,
            )
        return column, chosen

    text = str(value).strip()
    if len(text) > TEXT_VALUE_LIMIT:
        raise NextApiError(
            f"{field.get('key')}: value is longer than {TEXT_VALUE_LIMIT} characters", 400
        )
    return column, text


def custom_field_definition_rows(conn, *, include_archived: bool = True) -> list[dict[str, Any]]:
    """Every custom-field definition, in the owner's chosen order.

    Here rather than beside its callers because two of them cannot share a
    module: the API reads definitions to serve and validate them, and the import
    worker reads the same rows to resolve a mapped column -- and the worker must
    not import the API, which builds a Flask app on import. Two copies of this
    query would be two things that must agree and nothing making them.
    """
    if not table_exists(conn, "custom_field_definitions"):
        return []
    where = "" if include_archived else "WHERE archived_at IS NULL"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, key, name, field_type, options, sort_order,
                   archived_at, created_at, updated_at
            FROM custom_field_definitions
            {where}
            ORDER BY sort_order, key
            """
        )
        return [dict(row) for row in cur.fetchall()]


#: How an import column mapping names a custom field, as opposed to one of the
#: built-in targets (`title`, `barcode`, ...). A definition key can never
#: contain a colon -- `_KEY_RE` forbids it -- so the two namespaces cannot
#: collide, and a built-in target can never be mistaken for a field key.
IMPORT_MAPPING_PREFIX = "custom:"


def import_mapping_field_key(target: Any) -> str:
    """The custom-field key a mapping target names, or `""` for a built-in.

    Shape only. Whether a field by that key exists is a question for the
    database, and the answer changes between the moment a mapping is saved and
    the moment an import runs -- a field can be archived in between.
    """
    raw = str(target or "").strip()
    if not raw.startswith(IMPORT_MAPPING_PREFIX):
        return ""
    key = raw[len(IMPORT_MAPPING_PREFIX):].strip().lower()
    return key if _KEY_RE.match(key) else ""


def fill_movie_custom_values(
    conn, movie_id, values: Any, *, fields: list[dict[str, Any]]
) -> tuple[int, list[str]]:
    """Write imported custom values onto one film. Fill-only, never overwrite.

    Two deliberate differences from `replace_movie_custom_values`, which serves
    the edit form:

    **It fills rather than overwrites.** An import states what a file says, and
    the file is not the authority on a film the user has already curated -- the
    same rule the movie columns follow (`upsert_import_movie`'s conflict clause
    reads existing-first). A film that already carries a value for the field
    keeps it.

    **It never raises.** The edit form has one user, one film and somewhere to
    show an error; an import has thousands of rows and a summary. A row naming a
    field that has since been archived, or carrying "maybe" in a boolean column,
    must not end the run for the other 2,498 rows. Those are returned as
    reasons, for the caller to count and report -- silence would be the actual
    failure, because a skipped column looks exactly like an empty one.

    Returns `(filled, reasons)`.
    """
    if not isinstance(values, dict) or not values:
        return 0, []
    if not table_exists(conn, "movie_custom_field_values"):
        return 0, ["custom fields are not available on this instance"]
    by_key = {str(row.get("key")): row for row in fields}
    filled = 0
    reasons: list[str] = []
    for raw_key, raw_value in values.items():
        key = str(raw_key)
        field = by_key.get(key)
        if field is None:
            reasons.append(f"{key}: no such custom field")
            continue
        if field.get("archived_at"):
            reasons.append(f"{key}: field is archived")
            continue
        try:
            resolved = normalize_field_value(raw_value, field=field)
        except NextApiError as error:
            reasons.append(str(getattr(error, "message", None) or error))
            continue
        if resolved is None:
            # An empty cell is not a value and not an error: most rows of a
            # real file leave most columns blank.
            continue
        column, value = resolved
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO movie_custom_field_values (movie_id, field_id, {column})
                VALUES (%s, %s, %s)
                ON CONFLICT (movie_id, field_id) DO NOTHING
                """,
                (movie_id, field.get("id"), value),
            )
            if cur.rowcount:
                filled += 1
    return filled, reasons


def field_definition_entity(row: dict[str, Any]) -> dict[str, Any]:
    """One definition in its wire shape (contract §4e.2)."""
    return {
        "id": str(row.get("id")),
        "key": row.get("key"),
        "name": row.get("name"),
        "fieldType": row.get("field_type"),
        "options": row.get("options") or [],
        "sortOrder": int(row.get("sort_order") or 0),
        "archivedAt": row.get("archived_at"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def value_entity(row: dict[str, Any]) -> dict[str, Any]:
    """One value in its wire shape.

    `type` travels beside the value and not only on the definition. A client that
    applies the two changes in the other order, or that has not yet received the
    definition, can still render what it holds instead of dropping it -- which is
    the same instinct §4e.4 states for an unknown type.
    """
    field_type = str(row.get("field_type") or "text")
    raw = row.get(value_column_for_type(field_type))
    if isinstance(raw, Decimal):
        raw = float(raw)
    elif isinstance(raw, (date, datetime)):
        raw = raw.isoformat()[:10]
    return {
        "fieldId": str(row.get("field_id")),
        "key": row.get("key"),
        "type": field_type,
        "value": raw,
        "updatedAt": row.get("updated_at"),
    }
