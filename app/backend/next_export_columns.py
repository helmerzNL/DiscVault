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
)

EXPORT_COLUMN_KEYS: tuple[str, ...] = tuple(column["key"] for column in EXPORT_COLUMNS)

EXPORT_COLUMNS_BY_KEY: dict[str, dict[str, Any]] = {column["key"]: column for column in EXPORT_COLUMNS}

DEFAULT_EXPORT_COLUMN_KEYS: tuple[str, ...] = tuple(
    column["key"] for column in EXPORT_COLUMNS if column.get("default")
)

MAX_EXPORT_ROWS = 100_000


def export_column_catalogue() -> list[dict[str, Any]]:
    """Serialisable catalogue for the frontend column picker."""
    return [
        {
            "key": column["key"],
            "i18nKey": column["i18nKey"],
            "label": column["label"],
            "default": bool(column.get("default")),
        }
        for column in EXPORT_COLUMNS
    ]


def normalize_columns(requested: Any) -> list[str]:
    """Return the requested column keys in catalogue order.

    Unknown keys are rejected so a typo surfaces immediately instead of
    silently producing an export with a missing column.
    """
    if requested is None:
        return list(DEFAULT_EXPORT_COLUMN_KEYS)
    if not isinstance(requested, (list, tuple)):
        raise NextApiError("columns must be a list", 400)
    seen: set[str] = set()
    for entry in requested:
        key = str(entry or "").strip()
        if not key:
            continue
        if key not in EXPORT_COLUMNS_BY_KEY:
            raise NextApiError(f"Unknown export column: {key}", 400)
        seen.add(key)
    if not seen:
        raise NextApiError("At least one column is required", 400)
    return [key for key in EXPORT_COLUMN_KEYS if key in seen]


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
