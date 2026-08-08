"""Series and season shaping for TV releases.

Migration 063 created ``series``, ``series_seasons`` and the ``movie_seasons``
join, and left them unused: the type was a flag on a disc, with no way to say
*which* series a disc belongs to. This module is the validation half of closing
that gap; the SQL and the routes live in ``next_app.py`` beside the container
endpoints they are modelled on.

Three rules from the schema are worth restating, because they shape the payloads
here rather than being enforced only at write time:

- ``movies_series_requires_show`` — a disc may only carry a ``series_id`` when
  its ``media_type`` is ``SHOW``. Callers get a 400, not a constraint violation.
- ``uq_series_seasons_number_live`` — season numbers are unique per series among
  live rows, so a re-added season number is a conflict rather than a duplicate.
- Zero season links means "the complete series, or unspecified". It is not a
  missing value, so an empty list is a legitimate thing to send.
"""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

try:
    from .next_common import NextApiError
    from .next_import import clean_text
except ImportError:  # pragma: no cover - supports direct module execution
    from next_common import NextApiError
    from next_import import clean_text


SERIES_TEXT_LIMITS = {
    "title": 300,
    "sort_title": 300,
    "original_title": 300,
    "start_year": 40,
    "end_year": 40,
    "overview": 8000,
}

SEASON_TEXT_LIMITS = {
    "title": 300,
    "year": 40,
    "overview": 8000,
}

# Matches the movies table, which stores years as text because a source can
# legitimately hold "1999-2006" or a partial year.
MAX_SEASON_NUMBER = 10_000


def _text_field(
    body: dict[str, Any],
    column: str,
    *aliases: str,
    limits: dict[str, int],
    required: bool = False,
) -> str | None:
    """Read one text column from a body that may use either spelling.

    Presence-keyed like ``movie_media_type_value``: a key that is absent is not
    the same as a key sent empty, and only the latter clears a stored value.
    """
    keys = (column, *aliases)
    if not any(key in body for key in keys):
        if required:
            raise NextApiError(f"{keys[0]} is required", 400)
        return None
    raw = next(body[key] for key in keys if key in body)
    value = clean_text(raw)
    if value is None:
        if required:
            raise NextApiError(f"{keys[0]} is required", 400)
        return None
    limit = limits[column]
    if len(value) > limit:
        raise NextApiError(f"{keys[0]} must be {limit} characters or fewer", 400)
    return value


def series_payload(body: dict[str, Any], *, require_title: bool = True) -> dict[str, Any]:
    """Validate and normalize the writable columns of a series."""
    if not isinstance(body, dict):
        raise NextApiError("Series request body must be an object", 400)
    title = _text_field(body, "title", limits=SERIES_TEXT_LIMITS, required=require_title)
    sort_title = _text_field(
        body, "sort_title", "sortTitle", limits=SERIES_TEXT_LIMITS
    )
    return {
        "title": title,
        # Falling back to the title keeps list ordering stable for a caller that
        # never sends a sort title, which is every caller today.
        "sort_title": sort_title or title,
        "original_title": _text_field(
            body, "original_title", "originalTitle", limits=SERIES_TEXT_LIMITS
        ),
        "start_year": _text_field(
            body, "start_year", "startYear", limits=SERIES_TEXT_LIMITS
        ),
        "end_year": _text_field(body, "end_year", "endYear", limits=SERIES_TEXT_LIMITS),
        "overview": _text_field(body, "overview", limits=SERIES_TEXT_LIMITS),
    }


def season_number_value(body: dict[str, Any]) -> int:
    """A season number, which the schema requires and constrains to >= 0.

    Zero is deliberately allowed: specials are season 0 on TMDB and on the
    printed spine of plenty of box sets.
    """
    keys = ("season_number", "seasonNumber")
    if not any(key in body for key in keys):
        raise NextApiError("seasonNumber is required", 400)
    raw = next(body[key] for key in keys if key in body)
    if isinstance(raw, bool):
        raise NextApiError("seasonNumber must be a whole number", 400)
    try:
        number = int(str(raw).strip())
    except (TypeError, ValueError):
        raise NextApiError("seasonNumber must be a whole number", 400) from None
    if number < 0:
        raise NextApiError("seasonNumber must be zero or greater", 400)
    if number > MAX_SEASON_NUMBER:
        raise NextApiError(
            f"seasonNumber must be {MAX_SEASON_NUMBER} or fewer", 400
        )
    return number


def episode_count_value(body: dict[str, Any]) -> int | None:
    keys = ("episode_count", "episodeCount")
    if not any(key in body for key in keys):
        return None
    raw = next(body[key] for key in keys if key in body)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, bool):
        raise NextApiError("episodeCount must be a whole number", 400)
    try:
        count = int(str(raw).strip())
    except (TypeError, ValueError):
        raise NextApiError("episodeCount must be a whole number", 400) from None
    if count < 0:
        raise NextApiError("episodeCount must be zero or greater", 400)
    return count


def season_payload(body: dict[str, Any], *, require_number: bool = True) -> dict[str, Any]:
    """Validate and normalize the writable columns of a season."""
    if not isinstance(body, dict):
        raise NextApiError("Season request body must be an object", 400)
    payload: dict[str, Any] = {
        "title": _text_field(body, "title", limits=SEASON_TEXT_LIMITS),
        "year": _text_field(body, "year", limits=SEASON_TEXT_LIMITS),
        "overview": _text_field(body, "overview", limits=SEASON_TEXT_LIMITS),
        "episode_count": episode_count_value(body),
    }
    has_number = any(key in body for key in ("season_number", "seasonNumber"))
    if require_number or has_number:
        payload["season_number"] = season_number_value(body)
    return payload


def normalize_season_ids(value: Any) -> list[UUID]:
    """Read the season links for a disc, de-duplicated and order-preserving.

    An empty list is meaningful — it is "the complete series, or unspecified" —
    so it is returned as an empty list rather than folded into None. Deciding
    between "no link" and "not stated" is the caller's job, keyed on whether the
    key was present at all.
    """
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not hasattr(value, "__iter__"):
        raise NextApiError("seasonIds must be a list", 400)
    seen: set[UUID] = set()
    season_ids: list[UUID] = []
    for entry in value:
        text = clean_text(entry)
        if text is None:
            continue
        try:
            season_id = UUID(text)
        except (TypeError, ValueError):
            raise NextApiError(f"seasonIds contains an invalid id: {text}", 400) from None
        if season_id in seen:
            continue
        seen.add(season_id)
        season_ids.append(season_id)
    return season_ids


# --------------------------------------------------------------------------
# Resolving a series a metadata source stated
# --------------------------------------------------------------------------
#
# These live here rather than beside the series routes in ``next_app`` for a
# concrete reason: ``next_app`` imports ``next_metadata``, and the caller is
# ``next_metadata``. Putting the SQL where the routes are would close that
# import loop. This module already owns the validation half and has no heavy
# dependencies, so it is the seam that does not cost an import cycle.

SERIES_IDENTIFIER_TYPE = "tmdb_tv"


def series_id_for_identifier(cur: Any, *, provider_id: str, identifier: str) -> UUID | None:
    """The live series carrying this provider identifier, if one already does.

    Soft-deleted series are ignored rather than resurrected. A deleted series is
    a statement by whoever deleted it, and quietly reviving it because a feed
    mentioned the show again would overrule that with no trace.
    """
    cur.execute(
        """
        SELECT s.id
        FROM series_identifiers si
        JOIN series s ON s.id = si.series_id
        WHERE si.provider_id = %s
          AND si.identifier_type = %s
          AND si.identifier = %s
          AND s.deleted_at IS NULL
        LIMIT 1
        """,
        (provider_id, SERIES_IDENTIFIER_TYPE, identifier),
    )
    row = cur.fetchone()
    if not row:
        return None
    return row["id"] if isinstance(row, dict) else row[0]


def ensure_series(cur: Any, *, provider_id: str, identifier: str, title: str) -> UUID:
    """Find the series this identifier names, or create it once.

    The lookup is by identifier and never by title. A title match would mint a
    fresh series for every edition of the same show, and it is not identity in
    any case: the same show is titled differently across regions, and two
    unrelated shows can share a name. The identifier is the only anchor that
    survives both.

    A created series carries nothing but its title. That is deliberate rather
    than lazy: the feed states identity and structure, and the descriptive
    content -- overview, artwork -- is fetched separately from a source DiscVault
    has its own relationship with. A row with a title and an id is enough to
    hang seasons and discs off, which is what this exists to do.
    """
    existing = series_id_for_identifier(cur, provider_id=provider_id, identifier=identifier)
    if existing is not None:
        return existing
    series_uuid = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO series (id, public_id, title)
        VALUES (%s, %s, %s)
        """,
        (series_uuid, f"next-series-{series_uuid.hex[:12]}", title),
    )
    cur.execute(
        """
        INSERT INTO series_identifiers (series_id, provider_id, identifier_type, identifier)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (series_id, provider_id, identifier_type, identifier) DO NOTHING
        """,
        (series_uuid, provider_id, SERIES_IDENTIFIER_TYPE, identifier),
    )
    return series_uuid


def ensure_seasons(cur: Any, series_uuid: UUID, seasons: list[dict[str, Any]]) -> list[UUID]:
    """The season rows for these numbers, creating the ones that do not exist.

    Returns ids in the order given, because that order is the curator's: the
    feed publishes seasons by ``release_seasons.position`` rather than by number,
    since a set may present specials first or lead with the season it is marketed
    on. ``movie_seasons.sort_order`` preserves it.

    An existing season is reused and never rewritten. Its title may have been
    edited locally, and a re-sync is not a reason to overwrite that -- the same
    posture the merge policy takes for every other field a user can touch.
    """
    season_ids: list[UUID] = []
    for season in seasons:
        number = season.get("season_number")
        if not isinstance(number, int) or isinstance(number, bool):
            continue
        cur.execute(
            """
            SELECT id FROM series_seasons
            WHERE series_id = %s AND season_number = %s AND deleted_at IS NULL
            """,
            (series_uuid, number),
        )
        row = cur.fetchone()
        if row:
            season_ids.append(row["id"] if isinstance(row, dict) else row[0])
            continue
        season_uuid = uuid.uuid4()
        cur.execute(
            """
            INSERT INTO series_seasons (
                id, public_id, series_id, season_number, title, year, episode_count
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                season_uuid,
                f"next-season-{season_uuid.hex[:12]}",
                series_uuid,
                number,
                season.get("title"),
                season.get("year"),
                season.get("episode_count"),
            ),
        )
        season_ids.append(season_uuid)
    return season_ids


def provider_series_payload(value: Any) -> dict[str, Any] | None:
    """Shape a provider's stated series into what the resolver needs.

    Returns None when the source said nothing usable. That is not the same as
    saying "no series": nothing here ever proposes *unlinking*, because a
    provider that stays silent has not disagreed with a link a user made.
    """
    if not isinstance(value, dict):
        return None
    identifier = clean_text(value.get("tmdbTvId"))
    provider_id = clean_text(value.get("providerId") or value.get("provider"))
    title = clean_text(value.get("title"))
    if not identifier or not provider_id or not title:
        return None
    seasons: list[dict[str, Any]] = []
    seen: set[int] = set()
    for entry in value.get("seasons") or []:
        if not isinstance(entry, dict):
            continue
        number = entry.get("seasonNumber")
        if not isinstance(number, int) or isinstance(number, bool):
            continue
        if not 0 <= number <= MAX_SEASON_NUMBER or number in seen:
            continue
        seen.add(number)
        seasons.append(
            {
                "season_number": number,
                "title": clean_text(entry.get("title")),
                "year": clean_text(entry.get("year")),
                "episode_count": (
                    entry["episodeCount"]
                    if isinstance(entry.get("episodeCount"), int)
                    and not isinstance(entry.get("episodeCount"), bool)
                    and entry["episodeCount"] >= 0
                    else None
                ),
            }
        )
    return {
        "provider_id": provider_id,
        "identifier": identifier,
        "title": title,
        "seasons": seasons,
    }
