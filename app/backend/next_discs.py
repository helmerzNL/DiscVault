"""The discs inside a release: vocabulary, and the shape of a disc payload.

Migration 075 added ``movie_discs`` and its two link tables. This module is the
validation half — the SQL and the routes live in ``next_app.py`` beside the
movie edit path they extend, the same split ``next_series`` uses.

Two rules shape everything here.

**A disc narrows the release; it never replaces it.** The barcode, the
catalogue numbers, the packaging axes and ``movies.disc_count`` stay on the
release because that is what they describe. So does ``movie_technical_specs``,
which keeps answering for a release nobody has broken down into discs — the
overwhelming majority of them. A disc that leaves a field empty is not
contradicting the release-level value, it is declining to narrow it, and a
reader falls back rather than showing a gap.

**Medium and content are two axes, not one list.** ``disc_type`` says what the
disc *is* (a UHD, a Blu-ray, a DVD) and ``disc_role`` says what is *on* it (the
feature, bonus material, or both). Folding "Special Features" into the medium
list would repeat exactly the mistake migration 067 was written to undo: one
bucket holding two questions, so a row can answer neither properly. Asked the
two-axis way, "the bonus disc is a DVD while the feature is a UHD" — the normal
shape of a boxed set — is sayable.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

try:
    from .next_common import NextApiError
    from .next_import import clean_text
    from .next_metadata import normalize_audio_tracks, normalize_subtitles
except ImportError:  # pragma: no cover - supports direct module execution
    from next_common import NextApiError
    from next_import import clean_text
    from next_metadata import normalize_audio_tracks, normalize_subtitles


# --- The two axes ---------------------------------------------------------

#: What the disc physically is. Ordered as a person picking from a list would
#: expect rather than alphabetically: the three that account for almost every
#: shelf first, then the rest, then the escape hatch.
#:
#: No CHECK constraint backs this (see 075) and none should: a value that
#: arrives from an import or an older client is stored and reported, not
#: refused, exactly as ``movie_technical_specs.carrier_type`` treats its own
#: vocabulary.
DISC_TYPES = (
    "uhd_bluray",
    "bluray",
    "bluray_3d",
    "dvd",
    "hd_dvd",
    "laserdisc",
    "vcd",
    "cd_audio",
    "dvd_audio",
    "sacd",
    "umd",
    "other",
)

#: What is on the disc. Deliberately three values and not a free tag set: the
#: question this answers is "do I have to put this one on to watch the film",
#: and every finer distinction a collector wants — deleted scenes, a commentary
#: disc, the making-of — is a *label*, which the disc already has a field for.
DISC_ROLES = ("feature", "bonus", "feature_and_bonus")

DISC_TYPE_VALUES = frozenset(DISC_TYPES)
DISC_ROLE_VALUES = frozenset(DISC_ROLES)

#: The value that unlocks the free-text field. Its own name rather than the
#: literal repeated at each call site, because the schema's CHECK constraint
#: names it too and the two must not drift.
DISC_TYPE_OTHER = "other"

#: Matches ``movies.disc_count``'s ceiling (070). A release cannot hold more
#: discs than its own count column can express, so the two bounds are the same
#: number by construction rather than by coincidence.
MAX_DISCS = 999

DISC_TEXT_LIMITS = {
    "disc_type_other": 80,
    "label": 160,
    "notes": 2000,
    "video_resolution": 40,
}

#: The technical columns a disc carries, in the order the UI shows them. Named
#: exactly as ``movie_technical_specs`` names them so one renderer, one set of
#: i18n keys and one normalizer serve both levels.
DISC_LIST_COLUMNS = ("video_codecs", "hdr", "screen_ratios", "regions")
DISC_TRACK_COLUMNS = ("audio_tracks", "subtitles")

#: Every writable column, for the UPDATE in ``next_app``. ``sort_order`` is not
#: here: it is derived from the position of the entry in the submitted list, not
#: read from it.
DISC_COLUMNS = (
    "disc_type",
    "disc_role",
    "disc_type_other",
    "label",
    "video_resolution",
    "notes",
    *DISC_LIST_COLUMNS,
    *DISC_TRACK_COLUMNS,
)


def normalize_disc_type(value: Any) -> str | None:
    """A known disc type, or None when nothing usable was given.

    Unknown text is *not* silently coerced to ``other``: that would throw away
    the only copy of what the caller actually said while claiming a value they
    never picked. The caller gets a 400 naming the value instead, and a genuinely
    unlisted medium is entered as ``other`` plus its own free text — which is
    what that pair is for.
    """
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    lowered = cleaned.casefold()
    if lowered not in DISC_TYPE_VALUES:
        raise NextApiError(f"discType: unknown value {cleaned!r}", 400)
    return lowered


def normalize_disc_role(value: Any) -> str | None:
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    lowered = cleaned.casefold()
    if lowered not in DISC_ROLE_VALUES:
        raise NextApiError(f"discRole: unknown value {cleaned!r}", 400)
    return lowered


def _disc_text(entry: dict[str, Any], column: str, *aliases: str) -> str | None:
    keys = (column, *aliases)
    if not any(key in entry for key in keys):
        return None
    value = clean_text(next(entry[key] for key in keys if key in entry))
    if value is None:
        return None
    limit = DISC_TEXT_LIMITS[column]
    if len(value) > limit:
        raise NextApiError(f"{keys[-1]} must be {limit} characters or fewer", 400)
    return value


def _disc_list(entry: dict[str, Any], column: str, *aliases: str) -> list[str]:
    """A comma-tolerant string list, matching ``_movie_edit_csv_list``.

    Absent and empty both produce ``[]``. Unlike the release-level edit payload
    there is no presence-keyed distinction to preserve here: a disc entry is
    always submitted whole, because the client that can express one field can
    express all of them.
    """
    keys = (column, *aliases)
    raw = next((entry[key] for key in keys if key in entry), None)
    if raw is None:
        return []
    items = list(raw) if isinstance(raw, (list, tuple)) else str(raw).split(",")
    result: list[str] = []
    for item in items:
        text = clean_text(item)
        if text and text not in result:
            result.append(text)
    return result


def _disc_tracks(entry: dict[str, Any], column: str, alias: str, normalizer) -> list[Any]:
    raw = next((entry[key] for key in (column, alias) if key in entry), None)
    if raw is None:
        return []
    try:
        return normalizer(raw)
    except ValueError as exc:
        raise NextApiError(f"{alias}: {exc}", 422, "invalid_request") from exc


def _disc_uuid_list(entry: dict[str, Any], column: str, alias: str) -> list[UUID | str]:
    """Season or episode references on a disc, de-duplicated and order-preserving.

    An empty list is an answer — "this disc names no season" — and is returned
    as an empty list rather than folded into None, the same distinction
    ``normalize_season_ids`` draws one level up.

    A reference is either a server uuid or a ``public_id``. Both travel on the
    sync wire — season and episode entities carry both keys — and a client is
    entitled to hold whichever it stored (sync-contract §4.9). A uuid is parsed
    here; anything else is kept as opaque text for the write path to resolve
    against ``public_id``, where an unresolvable value is refused *by name*
    rather than guessed at.
    """
    raw = next((entry[key] for key in (column, alias) if key in entry), None)
    if raw is None:
        return []
    if isinstance(raw, (str, bytes)) or not hasattr(raw, "__iter__"):
        raise NextApiError(f"{alias} must be a list", 400)
    seen: set[UUID | str] = set()
    result: list[UUID | str] = []
    for item in raw:
        text = clean_text(item)
        if text is None:
            continue
        parsed: UUID | str
        try:
            parsed = UUID(text)
        except (TypeError, ValueError):
            if len(text) > 120:
                raise NextApiError(
                    f"{alias} contains an invalid id: {text[:40]}…", 400
                ) from None
            parsed = text
        if parsed in seen:
            continue
        seen.add(parsed)
        result.append(parsed)
    return result


def disc_payload(entry: Any, *, index: int) -> dict[str, Any]:
    """Validate and normalize one disc from an edit body."""
    if not isinstance(entry, dict):
        raise NextApiError(f"discs[{index}] must be an object", 400)

    disc_id_text = clean_text(entry.get("id"))
    disc_id: UUID | None = None
    if disc_id_text is not None:
        try:
            disc_id = UUID(disc_id_text)
        except (TypeError, ValueError):
            raise NextApiError(f"discs[{index}]: invalid id {disc_id_text}", 400) from None

    disc_type = normalize_disc_type(entry.get("discType", entry.get("disc_type")))
    other = _disc_text(entry, "disc_type_other", "discTypeOther")
    if other is not None and disc_type != DISC_TYPE_OTHER:
        # The schema's CHECK would refuse this too, as a constraint violation
        # with a Postgres message. A sentence is more use, and it says the thing
        # the caller has to do about it.
        raise NextApiError(
            f"discs[{index}]: a free-text disc type requires discType to be "
            f"{DISC_TYPE_OTHER!r}",
            400,
        )

    return {
        "id": disc_id,
        "disc_type": disc_type,
        "disc_role": normalize_disc_role(entry.get("discRole", entry.get("disc_role"))),
        "disc_type_other": other,
        "label": _disc_text(entry, "label"),
        "notes": _disc_text(entry, "notes"),
        "video_resolution": _disc_text(entry, "video_resolution", "videoResolution"),
        "video_codecs": _disc_list(entry, "video_codecs", "videoCodecs"),
        "hdr": _disc_list(entry, "hdr", "hdrFormats", "hdr_formats"),
        "screen_ratios": _disc_list(
            entry, "screen_ratios", "screenRatios", "aspectRatios", "screenRatio"
        ),
        "regions": _disc_list(entry, "regions", "discRegions", "disc_regions"),
        "audio_tracks": _disc_tracks(
            entry, "audio_tracks", "audioTracks", normalize_audio_tracks
        ),
        "subtitles": _disc_tracks(entry, "subtitles", "subtitles", normalize_subtitles),
        "season_ids": _disc_uuid_list(entry, "season_ids", "seasonIds"),
        "episode_ids": _disc_uuid_list(entry, "episode_ids", "episodeIds"),
    }


def discs_payload(body: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Read the disc list from an edit body, or None when unstated.

    Presence-keyed like every other part of the movie edit payload: a client
    that predates discs sends neither key and must not have a release's discs
    deleted out from under it. An explicit empty list *is* a statement — "this
    release has no discs recorded" — and does delete them.

    Duplicate ids are refused rather than de-duplicated. Two entries claiming
    the same disc are two different descriptions of it, and silently keeping
    whichever came last would discard an edit the user made.
    """
    keys = ("discs",)
    if not any(key in body for key in keys):
        return None
    raw = next(body[key] for key in keys if key in body)
    if raw is None:
        return []
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise NextApiError("discs must be a list", 400)
    if len(raw) > MAX_DISCS:
        raise NextApiError(f"A release may hold at most {MAX_DISCS} discs", 400)
    discs = [disc_payload(entry, index=index) for index, entry in enumerate(raw)]
    seen: set[UUID] = set()
    for disc in discs:
        disc_id = disc["id"]
        if disc_id is None:
            continue
        if disc_id in seen:
            raise NextApiError(f"discs names the same disc twice: {disc_id}", 400)
        seen.add(disc_id)
    return discs


def disc_is_empty(disc: dict[str, Any]) -> bool:
    """Whether a normalized disc says anything at all.

    A row that names no type, no role, no label, no spec and no episode is not a
    disc — it is a form row somebody opened and left alone.
    """
    if disc.get("id") is not None:
        # An existing disc is never dropped for being empty: it was saved
        # deliberately, and emptying its fields is an edit, not an abandonment.
        return False
    for key, value in disc.items():
        if key == "id":
            continue
        if value:
            return False
    return True


def drop_blank_discs(discs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Discard a disc list that turns out to say nothing, and only then.

    A list of nothing but blank rows is somebody who opened the editor and left
    it alone, and storing it would flip a release from "nobody has broken this
    down" to "broken down into one unknown disc" — a different claim, made by
    accident. That list becomes empty.

    A *mixed* list keeps every entry, blanks included, and that is the part
    worth stating. Once the editor seeds Disc 1 from the release, clicking "Add
    disc" produces a described Disc 1 beside a blank Disc 2, and the blank one
    is not an abandoned row — it is the whole point of the click. Dropping it
    would delete the disc the user had just said exists, and quietly, because a
    release that saved one disc back looks exactly like one that only ever had
    one.
    """
    if all(disc_is_empty(disc) for disc in discs):
        return []
    return list(discs)


def disc_signature(disc: dict[str, Any]) -> str:
    """A stable comparison key, used by the tests and by change detection."""
    return json.dumps(
        {key: value for key, value in sorted(disc.items()) if key != "id"},
        sort_keys=True,
        default=str,
    )
