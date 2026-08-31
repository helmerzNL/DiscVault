"""Column catalogue for Library exports.

This module is the single source of truth for the columns a Library export can
contain. The frontend fetches the catalogue to build its column picker, and the
renderers in ``next_export.py`` use the same definitions for headers, ordering
and layout hints, so the two can never drift apart.

Values are extracted on the client, because the Library list view already
computes exactly what it shows (deduplicated credits, preferred content rating,
format badges, aggregated container rows). Re-deriving that in Python would
duplicate a dozen helpers and guarantee drift; instead the client posts the
rendered cell values and this module validates and orders them.
"""

from __future__ import annotations

from typing import Any

try:  # pragma: no cover - exercised indirectly by both layouts
    from .next_common import NextApiError
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_common import NextApiError


# ``weight`` drives the proportional column width in the PDF renderer.
EXPORT_COLUMNS: tuple[dict[str, Any], ...] = (
    {"key": "title", "i18nKey": "collection.titleColumn", "label": "Title", "default": True, "weight": 3.2},
    {"key": "year", "i18nKey": "collection.yearColumn", "label": "Year", "default": True, "weight": 0.8},
    {"key": "barcode", "i18nKey": "movieDetail.barcode", "label": "Barcode", "default": True, "weight": 1.6},
    {"key": "format", "i18nKey": "movieDetail.format", "label": "Format", "default": True, "weight": 1.3},
    {"key": "director", "i18nKey": "movieDetail.director", "label": "Director", "default": True, "weight": 2.0},
    {"key": "actors", "i18nKey": "movieDetail.actors", "label": "Actors", "default": True, "weight": 2.8},
    {"key": "studio", "i18nKey": "collection.studioColumn", "label": "Studio", "default": True, "weight": 1.8},
    {"key": "contentRating", "i18nKey": "movieDetail.contentRating", "label": "Content rating", "default": True, "weight": 1.1},
    {"key": "tags", "i18nKey": "lists.tags", "label": "Tags", "default": True, "weight": 1.8},
    {"key": "watchActivity", "i18nKey": "collection.behaviorColumn", "label": "Viewing activity", "default": True, "weight": 1.6},
    # Appended, and default off. Both are rules rather than preferences.
    #
    # Appended because a stored column selection is a list of keys, and inserting
    # a column between the existing ten would change the order of a file somebody
    # already has.
    #
    # Default off because the export column set is a contract shared by the PWA,
    # iOS and Android (App-Guidance library-export-columns.md 1): a column that
    # does not exist on every implementation ships off, so an export with the
    # default selection is the same file everywhere. They turn on once the other
    # two carry them.
    {"key": "originCountry", "i18nKey": "movieDetail.originCountry", "label": "Country of origin", "default": False, "weight": 1.4},
    {"key": "originalLanguage", "i18nKey": "movieDetail.originalLanguage", "label": "Original language", "default": False, "weight": 1.4},
    {"key": "personalRating", "i18nKey": "lists.myRating", "label": "My rating", "default": False, "weight": 0.9},
)

EXPORT_COLUMN_KEYS: tuple[str, ...] = tuple(column["key"] for column in EXPORT_COLUMNS)

EXPORT_COLUMNS_BY_KEY: dict[str, dict[str, Any]] = {column["key"]: column for column in EXPORT_COLUMNS}

DEFAULT_EXPORT_COLUMN_KEYS: tuple[str, ...] = tuple(
    column["key"] for column in EXPORT_COLUMNS if column.get("default")
)

MAX_EXPORT_ROWS = 100_000


#: Custom-field columns are namespaced so they can never collide with a built-in
#: key, and so `normalize_columns` can tell "unknown built-in" (a typo, worth a
#: 400) from "a field this instance defines" without guessing.
CUSTOM_COLUMN_PREFIX = "custom:"


def custom_column_key(field_key: str) -> str:
    return f"{CUSTOM_COLUMN_PREFIX}{field_key}"


def custom_export_columns(custom_fields: Any) -> list[dict[str, Any]]:
    """Catalogue entries for one instance's custom fields.

    Always default-off, and never part of DEFAULT_EXPORT_COLUMN_KEYS. §1 of
    App-Guidance's library-export-columns.md rules that a column absent from some
    *implementation* ships off so the default selection is the same file
    everywhere. A custom field is absent from another *instance*, which that
    document does not yet have a category for -- but the same answer holds, and
    more strongly: two installations legitimately define different fields, and no
    amount of default-on could make their default exports match.

    Archived fields keep their column. A film may still carry a value, and an
    export that dropped it would lose data the user typed.
    """
    columns: list[dict[str, Any]] = []
    for field in custom_fields or []:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or "").strip()
        if not key:
            continue
        columns.append(
            {
                "key": custom_column_key(key),
                # No i18nKey: the label is what the owner typed, and there is no
                # translation catalogue that could ever hold it.
                "i18nKey": "",
                "label": str(field.get("name") or key),
                "default": False,
                "weight": 1.4,
                "custom": True,
            }
        )
    return columns


def export_column_catalogue(custom_fields: Any = None) -> list[dict[str, Any]]:
    """Serialisable catalogue for the frontend column picker."""
    return [
        {
            "key": column["key"],
            "i18nKey": column["i18nKey"],
            "label": column["label"],
            "default": bool(column.get("default")),
            **({"custom": True} if column.get("custom") else {}),
        }
        for column in (*EXPORT_COLUMNS, *custom_export_columns(custom_fields))
    ]


def normalize_columns(requested: Any, custom_fields: Any = None) -> list[str]:
    """Return the requested column keys in catalogue order.

    Unknown keys are rejected so a typo surfaces immediately instead of
    silently producing an export with a missing column.
    """
    if requested is None:
        return list(DEFAULT_EXPORT_COLUMN_KEYS)
    if not isinstance(requested, (list, tuple)):
        raise NextApiError("columns must be a list", 400)
    allowed_custom = {
        custom_column_key(str(field.get("key")))
        for field in (custom_fields or [])
        if isinstance(field, dict) and field.get("key")
    }
    seen: set[str] = set()
    custom_seen: list[str] = []
    for entry in requested:
        key = str(entry or "").strip()
        if not key:
            continue
        if key.startswith(CUSTOM_COLUMN_PREFIX):
            # Still rejected when unknown: a field that was never defined is as
            # much a typo as a misspelled built-in, and a silently dropped column
            # produces an export missing something the caller asked for.
            if key not in allowed_custom:
                raise NextApiError(f"Unknown export column: {key}", 400)
            if key not in custom_seen:
                custom_seen.append(key)
            continue
        if key not in EXPORT_COLUMNS_BY_KEY:
            raise NextApiError(f"Unknown export column: {key}", 400)
        seen.add(key)
    if not seen and not custom_seen:
        raise NextApiError("At least one column is required", 400)
    # Built-ins in catalogue order, then custom fields in the order the instance
    # defines them -- appended, never interleaved, so a stored column selection
    # keeps producing the same column order.
    ordered_custom = [key for key in allowed_custom_order(custom_fields) if key in custom_seen]
    return [key for key in EXPORT_COLUMN_KEYS if key in seen] + ordered_custom


def allowed_custom_order(custom_fields: Any) -> list[str]:
    """Custom column keys in the order the instance defines its fields."""
    return [
        custom_column_key(str(field.get("key")))
        for field in (custom_fields or [])
        if isinstance(field, dict) and field.get("key")
    ]


def column_headers(columns: list[str], labels: Any = None) -> list[str]:
    """Header labels for ``columns``, preferring client-supplied translations."""
    resolved = labels if isinstance(labels, dict) else {}
    headers = []
    for key in columns:
        label = str(resolved.get(key) or "").strip()
        headers.append(label or EXPORT_COLUMNS_BY_KEY[key]["label"])
    return headers


def column_weights(columns: list[str]) -> list[float]:
    return [float(EXPORT_COLUMNS_BY_KEY[key].get("weight") or 1.0) for key in columns]


def normalize_rows(rows: Any, columns: list[str]) -> list[list[str]]:
    """Coerce the posted rows into a rectangular matrix of strings."""
    if rows is None:
        return []
    if not isinstance(rows, (list, tuple)):
        raise NextApiError("rows must be a list", 400)
    if len(rows) > MAX_EXPORT_ROWS:
        raise NextApiError(f"Too many rows, the maximum is {MAX_EXPORT_ROWS}", 400)
    matrix: list[list[str]] = []
    for row in rows:
        if isinstance(row, dict):
            matrix.append([_cell_text(row.get(key)) for key in columns])
        elif isinstance(row, (list, tuple)):
            padded = list(row) + [""] * (len(columns) - len(row))
            matrix.append([_cell_text(value) for value in padded[: len(columns)]])
        else:
            raise NextApiError("Each row must be an object or a list", 400)
    return matrix


def _cell_text(value: Any) -> str:
    if value is None or value is False:
        return ""
    if value is True:
        return "true"
    if isinstance(value, (list, tuple)):
        return ", ".join(_cell_text(entry) for entry in value if entry not in (None, ""))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value)
    # Control characters break XLSX and make CSV unreadable; newlines are kept.
    return "".join(char for char in text if char in "\n\t" or ord(char) >= 32).strip()
