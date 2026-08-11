"""The release-level technical row: its one writer, and its one derivation.

`movie_technical_specs` holds the flattened technical description of a release
-- resolution, codecs, HDR, ratios, regions, audio tracks, subtitles. Two
functions may write it, and they live together here because they are two halves
of one rule rather than two neighbours:

* `upsert_movie_technical_edits` is **the** writer. A second copy of this SQL is
  how one fact acquires two spellings, so every path that changes the row --
  the edit form, the sync mutation, enrichment, a contribution -- comes through
  it.
* `derive_release_technical_from_discs` is what calls it once a release has
  discs, because from that moment the row is a summary of them rather than an
  authored answer.

**Why this is its own module rather than part of `next_app`.** The one-time
backfill (`scripts/backfill_disc_union.py`) has to converge historical releases
through the same derivation the save path uses -- a second implementation would
be a second chance to get the losing-a-fact part wrong, on the one path where
nobody is looking at the result. Importing `next_app` to reach it is not
available: that module builds the Flask application at import time and so
demands the runtime secrets, which a data sweep pointed at a copied database
has no business needing. Splitting the two functions out is what lets the
script share them instead of copying them.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

try:  # pragma: no cover - import shape depends on runtime layout
    from .next_common import json_ready
    from .next_discs import UNION_LIST_COLUMNS, union_entries, union_release_technical
    from .next_packaging import derive_legacy_packaging
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_common import json_ready
    from next_discs import UNION_LIST_COLUMNS, union_entries, union_release_technical
    from next_packaging import derive_legacy_packaging


logger = logging.getLogger(__name__)

#: The release columns a disc owns once the release has any. Derived from the
#: union rule rather than retyped: this *is* `union_release_technical`'s output
#: shape, so the two cannot drift into disagreeing about what is derived.
DISC_DERIVED_RELEASE_COLUMNS: frozenset[str] = frozenset(UNION_LIST_COLUMNS) | {"video_resolution"}


def drop_disc_derived_edits(cur, movie_uuid: UUID, edits: dict[str, Any]) -> dict[str, Any]:
    """Client edits to the derived columns, removed once the release has discs.

    The PWA hides these seven fields as soon as a release names discs
    (sync-contract §4.7), but hiding is a client courtesy and the server was
    accepting the write from anyone who did not know the rule -- the iOS app
    being exactly such a client today. Neither outcome was a plain overwrite:

    * Without a disc list in the same body nothing derives, so the value was
      stored verbatim and the release quietly contradicted its own discs until
      some later save happened to carry discs.
    * With one, `derive_release_technical_from_discs` could not tell a fresh
      client edit from a value stranded on the release from before the discs
      existed -- both read as "held here, on no disc" -- and pushed it down
      onto disc 1, where it then looked like an authored disc fact forever.

    Dropped rather than refused, mirroring `packaging` in next_app: a 422 would
    break every client that sends these fields today, and breaking them is not
    worth it to reject a value we can simply decline to store. The same
    caveat applies as there -- the client gets a 200 and no complaint -- so the
    warning is the only trace, and it names the fields.

    Read from the database, not from the payload. A body that creates a
    release's *first* discs still holds its release-level values legitimately:
    at this point the movie has no discs, nothing is dropped, and
    `apply_movie_discs` goes on to seed disc 1 from them exactly as before.
    """
    if not edits:
        return edits
    submitted = sorted(column for column in DISC_DERIVED_RELEASE_COLUMNS if column in edits)
    if not submitted:
        return edits
    cur.execute("SELECT 1 FROM movie_discs WHERE movie_id=%s LIMIT 1", (movie_uuid,))
    if cur.fetchone() is None:
        return edits
    logger.warning(
        "technical: ignoring client-sent %s for movie %s - this release has discs, so "
        "those columns are the union of them and read-only for clients "
        "(sync-contract 4.7). The client should edit the discs instead.",
        ", ".join(submitted),
        movie_uuid,
    )
    return {
        column: value
        for column, value in edits.items()
        if column not in DISC_DERIVED_RELEASE_COLUMNS
    }


def upsert_movie_technical_edits(cur, movie_uuid: UUID, edits: dict[str, Any]) -> None:
    if not edits:
        return
    edits = dict(edits)
    derive_packaging = bool(edits.pop("_derive_packaging", False))
    cur.execute(
        "INSERT INTO movie_technical_specs (movie_id, updated_at) VALUES (%s, now()) ON CONFLICT (movie_id) DO NOTHING",
        (movie_uuid,),
    )
    if derive_packaging:
        # Merge the submitted axes over the stored ones before rebuilding the
        # flat `packaging` mirror, so a body carrying only one axis keeps the
        # other. See _movie_edit_case_axes.
        cur.execute(
            "SELECT carrier_type, outer_packaging FROM movie_technical_specs WHERE movie_id=%s",
            (movie_uuid,),
        )
        stored = cur.fetchone() or {}
        if not isinstance(stored, dict):
            stored = {"carrier_type": stored[0], "outer_packaging": stored[1]}
        carrier = edits["carrier_type"] if "carrier_type" in edits else stored.get("carrier_type")
        outer = (
            edits["outer_packaging"]
            if "outer_packaging" in edits
            else (stored.get("outer_packaging") or [])
        )
        edits["packaging"] = derive_legacy_packaging(carrier, outer)
    assignments: list[str] = []
    values: list[Any] = []
    for col in ("video_resolution", "carrier_type", "steelbook_format"):
        if col in edits:
            assignments.append(f"{col}=%s")
            values.append(edits[col])
    for col in (
        "audio_tracks",
        "subtitles",
        "packaging",
        "outer_packaging",
        "finishes",
        "hdr",
        "screen_ratios",
        "regions",
        "video_codecs",
    ):
        if col in edits:
            assignments.append(f"{col}=%s")
            values.append(Jsonb(json_ready(edits[col] or [])))
    if "content_ratings" in edits:
        assignments.append("content_ratings = COALESCE(content_ratings, '{}'::jsonb) || %s")
        values.append(Jsonb(json_ready(edits["content_ratings"] or {})))
    if not assignments:
        return
    assignments.append("updated_at=now()")
    values.append(movie_uuid)
    cur.execute(
        f"UPDATE movie_technical_specs SET {', '.join(assignments)} WHERE movie_id=%s",
        values,
    )


def disc_union_snapshot(cur, movie_uuid: UUID) -> dict[str, list[Any]]:
    """What the discs hold *now*, per column. Taken before a save overwrites it.

    The push-down below needs to tell two things apart that look identical once
    the write has happened: a value the release has always held and no disc ever
    did (a genuine leftover, which must survive), and a value the user has just
    removed from the last disc that had it (a deletion, which must land). Both
    end up as "on the release, not on any disc".

    The difference is entirely in the past tense, so it has to be read before
    the discs are written.
    """
    cur.execute(
        "SELECT " + ", ".join(UNION_LIST_COLUMNS.values()) + " "
        "FROM movie_discs WHERE movie_id=%s",
        (movie_uuid,),
    )
    discs = [dict(row) for row in cur.fetchall()]
    return {
        disc_column: union_entries(discs, disc_column)
        for disc_column in UNION_LIST_COLUMNS.values()
    }


def derive_release_technical_from_discs(
    cur, movie_uuid: UUID, *, previously_on_discs: dict[str, list[Any]] | None = None
) -> None:
    """Make the release row the union of its discs, losing nothing on the way.

    Once a release has discs, the release-level technical fields stop being
    authored and become a summary of them: a reader asking what is on this
    release means "across all of it", and the disc is the more specific of the
    two places the same fact could live. Two authored copies of one fact is how
    they drift.

    **A leftover is pushed down before the union is taken.** A release may hold
    a value no disc does -- recorded before discs existed, or written by a sync
    client that predates them -- and deriving straight over it would delete a
    fact nobody retracted. So it is appended to disc 1 first, where it becomes
    an authored value again, and only then does the union include it. After one
    save the release row is a pure derivation; before it, nothing is lost.

    **But only a value the discs never held.** Pass `previously_on_discs` — the
    union as it stood before this save — and a value the user has just removed
    from the last disc that carried it is recognised as a deletion rather than
    as a leftover. Without it the push-down puts it straight back, so removing
    an HDR format, an audio track or a region from a disc silently could not be
    done at all: the derivation restored it in the same transaction, and the
    only symptom was that the edit did not stick.

    Omitting the argument keeps the old, broader behaviour, which is right for a
    caller that genuinely has no before-state — the one-time backfill converges
    releases nobody is editing, and for those every difference *is* historical.

    Only the columns a disc actually has. Packaging, finishes, the carrier and
    the content ratings describe the box rather than what is pressed onto a
    platter, and stay authored at release level.
    """
    columns = ", ".join(UNION_LIST_COLUMNS)
    cur.execute(
        f"SELECT {columns}, video_resolution FROM movie_technical_specs WHERE movie_id=%s",
        (movie_uuid,),
    )
    stored = cur.fetchone()
    cur.execute(
        "SELECT id, " + ", ".join(UNION_LIST_COLUMNS.values()) + ", video_resolution "
        "FROM movie_discs WHERE movie_id=%s ORDER BY sort_order, created_at",
        (movie_uuid,),
    )
    discs = [dict(row) for row in cur.fetchall()]
    if not discs:
        return

    if stored:
        stored = dict(stored)
        leftovers: dict[str, list[Any]] = {}
        for release_column, disc_column in UNION_LIST_COLUMNS.items():
            held = stored.get(release_column)
            if not isinstance(held, list) or not held:
                continue
            on_discs = union_entries(discs, disc_column)
            was_on_discs = (previously_on_discs or {}).get(disc_column) or []
            missing = [
                item
                for item in held
                if item not in on_discs and item not in was_on_discs
            ]
            if missing:
                leftovers[disc_column] = missing
        if leftovers:
            first = discs[0]
            for disc_column, missing in leftovers.items():
                current = first.get(disc_column)
                first[disc_column] = (current if isinstance(current, list) else []) + missing
            cur.execute(
                "UPDATE movie_discs SET "
                + ", ".join(f"{column}=%s" for column in leftovers)
                + " WHERE id=%s",
                (*(Jsonb(json_ready(first[column])) for column in leftovers), first["id"]),
            )

    derived = union_release_technical(discs)
    if derived:
        upsert_movie_technical_edits(cur, movie_uuid, derived)
