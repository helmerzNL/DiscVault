"""Shared infrastructure helpers for the DiscVault Next backend.

These helpers have no application-domain dependencies. They are extracted from
``next_app.py`` so that domain modules can reuse them without importing the
oversized application module. ``next_app.py`` re-imports every name defined here
to preserve its public surface (tests and older modules continue to import the
same names from ``next_app``).
"""

from __future__ import annotations

import re
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
    """Read a whole-number query argument: validated, then clamped.

    Nineteen routes parsed their own with a bare ``int(request.args.get(...))``.
    Most of them clamped afterwards, so the sizes were fine; what none of them
    had was *validation*, and ``?limit=abc`` therefore raised ``ValueError``
    inside the handler and reached the caller as a 500. A malformed request is
    the caller's mistake and has to be told apart from the server's.

    An **empty** value counts as absent. Five of those routes were written as
    ``int(request.args.get("limit") or 100)``, which already meant that, and a
    client emitting a bare ``?limit=`` should not start receiving 400s because
    the parsing moved.
    """

    raw = request.args.get(name)
    if raw is None or not str(raw).strip():
        raw = default
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


# One spelling for one physical format, mirroring `physicalFormatLabel` in the
# PWA and used by every surface that shows a format to a person.
#
# `movies.format` is free text on purpose: providers and sync clients write raw
# codes into it (`4K_UHD`, `BLURAY`), a person can type their own, and the
# column never constrained any of it. So the same shelf holds several spellings
# of one format, and anything that groups on the raw value reports them as
# different things - which is how the statistics page came to show both
# "4K UHD + Blu-Ray" and "4K UHD + Blu-ray".
#
# Only the four shapes the app agrees on are rewritten. Anything else is
# returned as it was typed: inventing a spelling for a value nobody recognises
# would merge things that are not the same, and that mistake is invisible.
_FOUR_K_TOKENS = ("4k", "uhd", "ultra hd")
_BLURAY_RE = re.compile(r"blu[\s-]?ray|(?:^|[^a-z])bd(?:[^a-z]|$)")
_FORMAT_COMBO_RE = re.compile(r"[+/&]")
#: DiscVault's own canonical *combo* code carries no `+/&` separator: the clients
#: store the two-disc pack as the single token `4K_UHD_BLURAY` (iOS `DiscFormat`,
#: the PWA filter) and the sync push writes that raw value into `movies.format`.
#: An underscore *joining* a Blu-ray token to the 4K name is that pack and only
#: that pack. It is not the same string as `Ultra HD Blu-ray`, which is the name
#: of one 4K disc and must stay `4K UHD`: there the Blu-ray token is separated by
#: a space, never an underscore. Without this the correction sheet read the
#: stored `4K_UHD_BLURAY` as `4K UHD`, dropping the Blu-ray leg and proposing a
#: false correction against a catalogue holding the full combo.
_FORMAT_COMBO_UNDERSCORE_RE = re.compile(r"_(?:blu[\s-]?ray|bd)\b|\bbd_|bluray_")


def physical_format_label(value: Any) -> str:
    """The display spelling of one physical format."""
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    lowered = text.lower()
    has_four_k = any(token in lowered for token in _FOUR_K_TOKENS)
    has_bluray = bool(_BLURAY_RE.search(lowered))
    is_combo = bool(
        _FORMAT_COMBO_RE.search(lowered) or _FORMAT_COMBO_UNDERSCORE_RE.search(lowered)
    )
    if has_four_k and has_bluray and is_combo:
        return "4K UHD + Blu-ray"
    if has_four_k:
        return "4K UHD"
    if has_bluray:
        return "Blu-ray"
    if "dvd" in lowered and "hd dvd" not in lowered:
        return "DVD"
    return text


# The identity/conflict rules an import writes by (`import_movie_existing_id`
# in next_worker.py) and the rules the preview predicts by (`import_source_conflicts`
# in next_app.py) used to be two hand-written copies. They drifted: the writer
# skipped a format-incompatible row and kept looking, ordered candidates by
# recency, and only ever checked `tmdb`/`imdb` identifiers; the preview took
# the first row Postgres happened to return, applied no format check, and
# matched against any provider identifier ever recorded. Two copies of "how do
# we decide this is the same film" cannot both be right, and only one of them
# runs at write time - so this is the one both sides now call.
def physical_media_format_key(value: Any) -> str:
    """A coarse bucket for a physical media format string.

    Two formats compare equal only after this normalisation: case, separators
    (`-`/`_`/`/`) and near-synonyms ("4K UHD" vs "Ultra HD Blu-ray") are folded
    away, but nothing is invented for a format nobody recognises.
    """
    text = (str(value or "").strip()).casefold().replace("-", " ").replace("_", " ").replace("/", " ")
    text = " ".join(text.split())
    if not text:
        return ""
    if "ultra hd" in text or "uhd" in text or "4k" in text:
        return "ultra_hd_blu_ray"
    if "blu ray" in text or "bluray" in text or text == "bd":
        return "blu_ray"
    if text in {"dvd", "dvd video"}:
        return "dvd"
    if "hd dvd" in text or "hddvd" in text:
        return "hd_dvd"
    if "laserdisc" in text or "laser disc" in text:
        return "laserdisc"
    if "svcd" in text or "vcd" in text:
        return "vcd_svcd"
    return text


def physical_media_formats_compatible(candidate: Any, expected: Any) -> bool:
    """Whether a candidate row's format may stand in for the imported item's.

    An unrecognised or blank format on either side never blocks a match -
    only two *recognised, different* formats do.
    """
    candidate_key = physical_media_format_key(candidate)
    expected_key = physical_media_format_key(expected)
    return not candidate_key or not expected_key or candidate_key == expected_key


def import_writer_identifiers(item: dict[str, Any]) -> dict[str, str]:
    """The only provider identifiers an import write ever matches or stores by.

    `import_movie_existing_id` looks up `movie_identifiers` rows filtered to
    exactly these two providers, and `upsert_import_movie` only ever writes
    these two providers with `identifier_type='movie_id'`. A preview that
    matched on a broader set (e.g. a plugin's own `identifiers` dict) would
    report a conflict the writer can never see - so this is the one set both
    sides look at.
    """

    def _text(value: Any) -> str:
        return " ".join(str(value or "").split())

    return {
        "tmdb": _text(item.get("tmdbId") or item.get("tmdb_id")),
        "imdb": _text(item.get("imdbId") or item.get("imdb_id")),
    }


def best_format_compatible_match(
    candidates: Any,
    expected_format: Any,
    *,
    title: str = "",
    year: str = "",
) -> dict[str, Any] | None:
    """The first candidate (already in preference order) whose format fits.

    `candidates` must already be ordered most-preferred-first - most-recently
    -updated row first for a real query, most-recently-proposed-create first
    for an in-batch pool - so this only ever adds the format filter on top of
    that ordering, exactly as `import_movie_existing_id` does today.

    A candidate is also rejected if it conflicts with `title`/`year` on
    *both* fields at once. A shared TMDb/IMDb id can identify a whole TV
    series across many season/movie rows, so a provider-identifier match
    that is merely one field off (a different season's on-disc title, or a
    re-release year) is still very likely the same film; one that disagrees
    on both is not (see #793). `title`/`year` default to empty because a
    title+year candidate's own query already guarantees no conflict is
    possible - passing them there would never trigger, so this check is only
    load-bearing for a provider-identifier match.
    """
    for candidate in candidates:
        if not physical_media_formats_compatible(candidate.get("format"), expected_format):
            continue
        candidate_title = str(candidate.get("title") or "").strip()
        candidate_year = str(candidate.get("year") or "").strip()
        title_conflicts = bool(title and candidate_title and title.casefold() != candidate_title.casefold())
        year_conflicts = bool(year and candidate_year and str(year) != candidate_year)
        if title_conflicts and year_conflicts:
            continue
        return candidate
    return None


def resolve_import_identity_match(
    *,
    barcode: str,
    identifiers: dict[str, str],
    title: str,
    year: str,
    item_format: Any,
    barcode_candidates,
    identifier_candidates,
    title_year_candidates,
) -> tuple[str, dict[str, Any]] | None:
    """The shared barcode -> identifier -> title+year precedence.

    Returns `(reason, row)` for the winning candidate, or `None`. Each
    `*_candidates` callable supplies that stage's rows in preference order;
    the caller decides where those rows come from - a live query for the
    writer, a live query merged with an in-batch pool for the preview - which
    is what lets both sides share this one precedence instead of maintaining
    two.

    - `barcode_candidates()` -> rows for an exact barcode match. A barcode
      match is trusted regardless of format (matching both existing callers),
      so only the first row is used.
    - `identifier_candidates(provider, identifier)` -> rows for one provider
      identifier, most-preferred first.
    - `title_year_candidates(title, year)` -> rows for a case-insensitive
      title + exact year match, most-preferred first.
    """
    if barcode:
        rows = barcode_candidates()
        if rows:
            return "barcode", rows[0]
    for provider, identifier in identifiers.items():
        if not identifier:
            continue
        row = best_format_compatible_match(
            identifier_candidates(provider, identifier), item_format, title=title, year=year
        )
        if row:
            return provider, row
    if title and year:
        row = best_format_compatible_match(title_year_candidates(title, year), item_format, title=title, year=year)
        if row:
            return "title_year", row
    return None
