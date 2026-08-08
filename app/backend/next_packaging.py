"""Physical media packaging vocabulary: carrier, outer packaging and finishes.

Packaging used to be a single flat list mixing three independent things - the
case itself (steelbook, digipak), the cardboard around it (slipcover, o-card)
and, not represented at all, the surface finish (spot UV, lenticular, foil).
Because they shared one bucket, nothing stopped a record from claiming both
`steelbook` and `amaray`, and the terms collectors actually use - FuturePak,
hardbox, fullslip, one-click - had nowhere to go.

The vocabulary is therefore split over two axes plus a tag set:

- ``CARRIER_TYPES``    - what the disc physically sits in. Exactly one per title.
- ``OUTER_PACKAGING``  - cardboard around that case. Several may apply: a
  boutique release can carry an o-card inside a slipcase.
- ``FINISHES``         - surface treatment. Describes the *look* of a carrier or
  slip, not its shape, so it is an orthogonal tag set.

This module is the single source of truth. The list previously lived in
``next_movievault_v2`` - a sync provider - while also being duplicated inline in
that same module, in the edit form markup, and in the title-noise regexes, with
no test binding the copies together. Import from here instead of re-declaring.
"""

from __future__ import annotations

from typing import Any, Iterable

# --- The three axes -------------------------------------------------------

# What the disc sits in. `keep_case` and `amaray` are the same object - the
# legacy enum carried both, which is part of the confusion this split fixes -
# so `keep_case` is canonical and `amaray` maps onto it.
CARRIER_TYPES = (
    "keep_case",
    "eco_case",
    "multi_disc_case",
    "steelbook",
    "futurepak",
    "hardbox",
    "digipak",
    "digisleeve",
    "digibook",
    "mediabook",
    "card_wallet",
    "paper_sleeve",
    "other",
)

# Cardboard around the carrier. An empty list means none - that is a real
# answer ("bare case"), not missing data.
OUTER_PACKAGING = (
    "slipcover",
    "o_card",
    "fullslip",
    "half_slip",
    "quarter_slip",
    "lenticular_slip",
    "double_lenticular_slip",
    "slipcase",
    "rigid_box",
    "one_click",
)

# Surface treatment, orthogonal to both axes above.
FINISHES = (
    "spot_uv",
    "embossed",
    "debossed",
    "foil",
    "holofoil",
    "matte",
    "glossy",
    "reversible_cover",
    "padded",
)

# Scanavo steelbook generations, distinguished by spine shape and hinge. Only
# meaningful for a metal carrier; the edit form hides the control otherwise.
STEELBOOK_FORMATS = ("g1", "g2", "g3")
STEELBOOK_CARRIERS = frozenset({"steelbook", "futurepak"})

CARRIER_TYPE_VALUES = frozenset(CARRIER_TYPES)
OUTER_PACKAGING_VALUES = frozenset(OUTER_PACKAGING)
FINISH_VALUES = frozenset(FINISHES)
STEELBOOK_FORMAT_VALUES = frozenset(STEELBOOK_FORMATS)

MAX_OUTER_PACKAGING = len(OUTER_PACKAGING)
MAX_FINISHES = len(FINISHES)

# --- Legacy flat list -----------------------------------------------------

# The nine values MovieVault's distribution-4 feed still sends, and what the
# `packaging` column held before the split. Kept as the wire vocabulary and as
# the derived column's alphabet - not as something a user picks.
LEGACY_PACKAGING_VALUES = frozenset(
    {
        "keep_case",
        "amaray",
        "steelbook",
        "slipcover",
        "slipcase",
        "digibook",
        "mediabook",
        "digipak",
        "box",
    }
)
MAX_LEGACY_PACKAGING = 9

# Which axis each legacy value belongs to. Matched case-insensitively on the
# way in: migration 053 backfilled scalars verbatim ("Steelbook") and the
# movievault_26 plugin emits TitleCase, neither of which the snake_case i18n
# lookup could ever resolve.
LEGACY_CARRIER_MAP = {
    "keep_case": "keep_case",
    "amaray": "keep_case",
    "steelbook": "steelbook",
    "digipak": "digipak",
    "digibook": "digibook",
    "mediabook": "mediabook",
}
LEGACY_OUTER_MAP = {
    "slipcover": "slipcover",
    "slipcase": "slipcase",
    "box": "rigid_box",
}

# Reverse direction, for keeping the derived `packaging` column populated so
# backup/restore, import and the MCP server keep seeing the shape they expect.
CARRIER_TO_LEGACY = {
    "keep_case": "keep_case",
    "eco_case": "keep_case",
    "multi_disc_case": "keep_case",
    "steelbook": "steelbook",
    "futurepak": "steelbook",
    "hardbox": "box",
    "digipak": "digipak",
    "digisleeve": "digipak",
    "digibook": "digibook",
    "mediabook": "mediabook",
}
OUTER_TO_LEGACY = {
    "slipcover": "slipcover",
    "o_card": "slipcover",
    "fullslip": "slipcover",
    "half_slip": "slipcover",
    "quarter_slip": "slipcover",
    "lenticular_slip": "slipcover",
    "double_lenticular_slip": "slipcover",
    "slipcase": "slipcase",
    "rigid_box": "box",
    "one_click": "box",
}


def _clean(value: Any) -> str:
    """Lowercase and trim a candidate value, tolerating non-strings."""
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def normalize_carrier(value: Any) -> str | None:
    """Return a known carrier, or None when the value is empty/unknown."""
    cleaned = _clean(value)
    if not cleaned:
        return None
    if cleaned in CARRIER_TYPE_VALUES:
        return cleaned
    return LEGACY_CARRIER_MAP.get(cleaned)


def normalize_steelbook_format(value: Any) -> str | None:
    cleaned = _clean(value)
    if cleaned in STEELBOOK_FORMAT_VALUES:
        return cleaned
    return None


def _normalize_list(values: Any, allowed: frozenset[str], legacy: dict[str, str]) -> list[str]:
    """Normalize to a de-duplicated, order-preserving list of known values."""
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, Iterable):
        return []
    result: list[str] = []
    for item in values:
        cleaned = _clean(item)
        if not cleaned:
            continue
        mapped = cleaned if cleaned in allowed else legacy.get(cleaned)
        if mapped and mapped not in result:
            result.append(mapped)
    return result


def normalize_outer_packaging(values: Any) -> list[str]:
    return _normalize_list(values, OUTER_PACKAGING_VALUES, LEGACY_OUTER_MAP)


def normalize_finishes(values: Any) -> list[str]:
    return _normalize_list(values, FINISH_VALUES, {})


def split_packaging(values: Any) -> tuple[str | None, list[str]]:
    """Split a flat packaging list into (carrier, outer_packaging).

    Accepts both alphabets. The legacy nine arrive on the distribution-4 feed
    and in rows written before migration 067; the canonical vocabulary arrives
    on distribution-5 and from any source that already speaks it.

    Accepting canonical values is not hypothetical tidiness - it was a bug.
    Only the six `LEGACY_CARRIER_MAP` keys were matched, so a feed value that
    was *already* a `CARRIER_TYPES` member - `futurepak`, `hardbox`,
    `digisleeve` - was discarded exactly like garbage, and silently: nothing
    logged it. The raw value survived only in the flat mirror, and the first
    user edit of the axes recomputed that mirror and erased it. So the finer
    terms could arrive and still leave the axes empty.

    The first recognized carrier wins - a well-formed record names at most one -
    while every recognized outer value is kept, de-duplicated, in order.
    """
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, Iterable):
        return None, []
    carrier: str | None = None
    outer: list[str] = []
    for item in values:
        cleaned = _clean(item)
        if not cleaned:
            continue
        if carrier is None:
            # Canonical first, then the legacy alias table. `amaray` is only in
            # the legacy map, and correctly folds onto `keep_case`.
            mapped_carrier = (
                cleaned if cleaned in CARRIER_TYPE_VALUES else LEGACY_CARRIER_MAP.get(cleaned)
            )
            if mapped_carrier:
                carrier = mapped_carrier
                continue
        mapped = (
            cleaned if cleaned in OUTER_PACKAGING_VALUES else LEGACY_OUTER_MAP.get(cleaned)
        )
        if mapped and mapped not in outer:
            outer.append(mapped)
    return carrier, outer


#: Retained under the original name: migration 067's comment and the ingest path
#: both refer to it, and it now handles more than the legacy alphabet.
split_legacy_packaging = split_packaging


def derive_legacy_packaging(carrier: Any, outer: Any) -> list[str]:
    """Rebuild the flat legacy list from the two axes.

    The `packaging` column stays populated from this so that consumers which
    predate the split - backup/restore, import, the MCP server - keep working
    while the axes become the source of truth in the UI.
    """
    result: list[str] = []
    mapped_carrier = CARRIER_TO_LEGACY.get(normalize_carrier(carrier) or "")
    if mapped_carrier:
        result.append(mapped_carrier)
    for value in normalize_outer_packaging(outer):
        mapped = OUTER_TO_LEGACY.get(value)
        if mapped and mapped not in result:
            result.append(mapped)
    return result
