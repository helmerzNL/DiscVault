"""Shared conservative identity rules for title/year deduplication."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


def normalize_edition_identity(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", folded.casefold()).strip()


def normalize_container_identities(values: Iterable[object] | None) -> frozenset[str]:
    return frozenset(
        normalized
        for value in values or ()
        if (normalized := str(value or "").strip().casefold())
    )


def title_year_identity_compatible(
    *,
    left_edition: object = None,
    right_edition: object = None,
    left_container_ids: Iterable[object] | None = None,
    right_container_ids: Iterable[object] | None = None,
) -> bool:
    left_memberships = normalize_container_identities(left_container_ids)
    right_memberships = normalize_container_identities(right_container_ids)
    if (left_memberships or right_memberships) and left_memberships != right_memberships:
        return False

    left_edition_key = normalize_edition_identity(left_edition)
    right_edition_key = normalize_edition_identity(right_edition)
    if (left_memberships or right_memberships) and bool(left_edition_key) != bool(
        right_edition_key
    ):
        return False
    return not (
        left_edition_key
        and right_edition_key
        and left_edition_key != right_edition_key
    )
