"""Shared conservative identity rules for title/year deduplication."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping


def normalize_edition_identity(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", folded.casefold()).strip()


MEDIA_TYPE_MOVIE = "MOVIE"
MEDIA_TYPE_SHOW = "SHOW"

_MEDIA_TYPE_ALIASES = {
    "movie": MEDIA_TYPE_MOVIE,
    "film": MEDIA_TYPE_MOVIE,
    "show": MEDIA_TYPE_SHOW,
    "tv": MEDIA_TYPE_SHOW,
    "tv_show": MEDIA_TYPE_SHOW,
    "tvshow": MEDIA_TYPE_SHOW,
    "tv_series": MEDIA_TYPE_SHOW,
    "tvseries": MEDIA_TYPE_SHOW,
    "series": MEDIA_TYPE_SHOW,
}


def normalize_media_type(value: object) -> str | None:
    """Normalize a media type to the exact wire vocabulary, or to absent.

    Lenient in, exact out (sync-contract.md §3b). An unrecognised value is
    **absent**, never ``MOVIE``: the clients' silent fallback to movie is a
    client-side leniency, and copying it here would turn an unknown value into
    a confident wrong answer that the ladder then treats as fact.
    """
    text = str(value or "").strip()
    if not text:
        return None
    return _MEDIA_TYPE_ALIASES.get(text.casefold().replace(" ", "_"))


def media_type_conflicts(left: object, right: object) -> bool:
    """True when both sides state a media type and the two disagree.

    Shaped like the barcode conflict rule: a one-sided value is inconclusive and
    blocks nothing. Tier 1 (per-record token) is exempt from this veto entirely --
    see ``find_movie_by_client_id``.
    """
    left_key = normalize_media_type(left)
    right_key = normalize_media_type(right)
    return bool(left_key and right_key and left_key != right_key)


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


def select_tmdb_identifier(
    identifiers: Iterable[Mapping[str, object]],
) -> str | None:
    """Select the tier-3 TMDB value from an ordered identifier row set.

    Provider and identifier-type comparisons are case-insensitive, matching the
    client contract. They are deliberately not whitespace-trimmed: server write
    paths already trim these storage keys, and preserving exact-key semantics
    keeps this selector aligned with the iOS implementation. Identifier values
    are trimmed and blank values are absent.

    A ``movie_id`` row wins when present. Otherwise the first TMDB row in the
    caller's deterministic order is the compatibility fallback.
    """
    tmdb_rows = []
    for row in identifiers:
        if str(row.get("provider_id") or "").casefold() != "tmdb":
            continue
        value = str(row.get("identifier") or "").strip()
        if value:
            tmdb_rows.append((row, value))
    selected = next(
        (
            candidate
            for candidate in tmdb_rows
            if str(candidate[0].get("identifier_type") or "").casefold()
            == "movie_id"
        ),
        None,
    )
    if selected is None and tmdb_rows:
        selected = tmdb_rows[0]
    if selected is None:
        return None
    return selected[1]


def select_movievault_identifier(
    identifiers: Iterable[Mapping[str, object]],
) -> str | None:
    """Select the first nonblank MovieVault movie identifier."""
    for row in identifiers:
        provider = str(row.get("provider_id") or "").casefold()
        identifier_type = str(row.get("identifier_type") or "").casefold()
        value = str(row.get("identifier") or "").strip()
        if (
            provider in {"movievault", "movievault_26"}
            and identifier_type == "movie_id"
            and value
        ):
            return value
    return None


def extract_identity_identifiers(
    identifiers: Iterable[Mapping[str, object]],
) -> dict[str, str | None]:
    """Extract the server identity providers from one ordered identifier set."""
    rows = list(identifiers)
    return {
        "tmdb": select_tmdb_identifier(rows),
        "movievault": select_movievault_identifier(rows),
    }
