"""Refresh a box set as a whole: give each member its MovieVault release back, then refresh it.

A box-set member imported before MovieVault release ids were kept (helmerzNL/DiscVault#689,
2026-11-03) carries no identifier at all. Its metadata refresh asks MovieVault about a release it
cannot name, finds nothing, and reports success. So a format MovieVault has since corrected never
reaches that member, however often somebody refreshes it.

This module repairs that for one box set, on request:

1. look the box set's barcode up the way the import does and take the members it names;
2. match every *stored* member to one of them, inside this box only;
3. write the release id onto each member that lacks one;
4. queue the ordinary metadata refresh for the members that now have one.

Step 4 is the point of the design. The refresh job already knows the rules for every field: user
locks, source precedence, and the format rule of identity-routing 5a (a source that found the disc
by identity may correct its format, a combo is never narrowed). Doing the format here as well
would be a second copy of that rule, and two copies drift. This module therefore never writes a
format. It only restores the identity the refresh was missing.

The specification is `projects/discvault/changes/2026-11-27-format-correction-and-box-set-refresh.md`
in Flux76HQ/App-Guidance, section 4.1.

The module has no import from `next_app`. The route hands in the pieces that live there (the source
lookup, the job queue, the sync emitter), which keeps the matching testable without a database and
avoids a circular import.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence
from uuid import UUID

try:
    from .next_common import NextApiError
except ImportError:  # pragma: no cover - supports direct module execution
    from next_common import NextApiError

OUTCOME_IDENTIFIED = "identified"
OUTCOME_ALREADY_IDENTIFIED = "already_identified"
OUTCOME_CONFLICT = "conflict"
OUTCOME_NO_RELEASE_ID = "no_release_id"
OUTCOME_UNMATCHED = "unmatched"

# The outcomes that leave a member with a release id MovieVault can be asked about, and so the only
# ones worth a refresh job.
REFRESHABLE_OUTCOMES = frozenset({OUTCOME_IDENTIFIED, OUTCOME_ALREADY_IDENTIFIED})

MATCHED_BY_TITLE = "title"
MATCHED_BY_POSITION = "position"

ERROR_NOT_A_BOX_SET = "box_set_refresh_not_a_box_set"
ERROR_NO_BARCODE = "box_set_refresh_no_barcode"
ERROR_NO_PROPOSAL = "box_set_refresh_no_proposal"

# GTIN-8, UPC-A, EAN-13 and GTIN-14: the lengths the resolver itself accepts. The length test is what
# keeps a synthetic `IMPORT-5050582369601-BOX-03` from passing as a barcode once its letters are gone.
_BARCODE_PATTERN = re.compile(r"^(?:\d{8}|\d{12}|\d{13}|\d{14})$")

# Members are never matched in bulk or by guesswork, so the cap only has to stop a corrupt box from
# turning one click into hundreds of jobs. Real sets are far smaller.
MAX_MEMBERS = 50


@dataclass(frozen=True)
class StoredMember:
    """A movie that is linked to the box set in this instance."""

    movie_id: str
    title: str
    sort_order: int
    release_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProposedMember:
    """A member the source says the box set holds."""

    position: int | None
    title: str
    release_id: str = ""


@dataclass(frozen=True)
class MemberDecision:
    movie_id: str
    title: str
    outcome: str
    release_id: str = ""
    matched_by: str = ""
    proposal_position: int | None = None
    proposal_title: str = ""

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "movieId": self.movie_id,
            "title": self.title,
            "outcome": self.outcome,
        }
        if self.matched_by:
            payload["matchedBy"] = self.matched_by
        if self.proposal_position is not None:
            payload["position"] = self.proposal_position
        return payload


@dataclass(frozen=True)
class RepairPlan:
    decisions: tuple[MemberDecision, ...]
    # Titles the source names that no stored member was matched to. Reported, never created: a refresh
    # that changes which discs a set contains is an import, which is a separate and explicit act.
    proposal_only_titles: tuple[str, ...] = field(default_factory=tuple)


def normalise_title(value: Any) -> str:
    """Case-fold, drop accents, and keep letters and digits only.

    Comparing titles is only safe *inside one box*, where two members rarely share a name. An empty
    result (a title that is all punctuation) matches nothing rather than everything.
    """
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(character for character in text if not unicodedata.combining(character))
    return re.sub(r"[\W_]+", " ", text, flags=re.UNICODE).strip()


def real_barcode(value: Any) -> str:
    """The barcode when it is a real one, otherwise an empty string."""
    text = re.sub(r"[\s-]+", "", str(value or ""))
    return text if _BARCODE_PATTERN.match(text) else ""


def plan_member_repairs(
    stored: Sequence[StoredMember],
    proposed: Sequence[ProposedMember],
) -> RepairPlan:
    """Match each stored member to a proposed one and decide what to do about its identity.

    The match runs from the stored side, so a member is only ever paired with one release, and a
    release is only ever claimed once.

    1. **Title.** A normalised title that is unique among the stored members and unique among the
       proposed ones is a match.
    2. **Position.** Only when both sides hold the same number of members: a member left over from
       step 1 is paired with the unclaimed proposed member whose `position` equals its stored
       `sort_order`. The import numbers the members from the proposal's own order, so the two agree
       unless somebody reordered the set, and a reorder leaves the claimed positions taken.

    Anything else is `unmatched` and left alone. A similar title is not a match.
    """
    stored_members = list(stored)[:MAX_MEMBERS]
    proposed_members = list(proposed)[:MAX_MEMBERS]

    stored_by_title: dict[str, list[int]] = defaultdict(list)
    for index, member in enumerate(stored_members):
        key = normalise_title(member.title)
        if key:
            stored_by_title[key].append(index)
    proposed_by_title: dict[str, list[int]] = defaultdict(list)
    for index, member in enumerate(proposed_members):
        key = normalise_title(member.title)
        if key:
            proposed_by_title[key].append(index)

    claimed: dict[int, tuple[int, str]] = {}
    for key, stored_indexes in stored_by_title.items():
        proposed_indexes = proposed_by_title.get(key, [])
        if len(stored_indexes) == 1 and len(proposed_indexes) == 1:
            claimed[stored_indexes[0]] = (proposed_indexes[0], MATCHED_BY_TITLE)

    if len(stored_members) == len(proposed_members):
        taken = {proposed_index for proposed_index, _ in claimed.values()}
        for index, member in enumerate(stored_members):
            if index in claimed:
                continue
            for proposed_index, candidate in enumerate(proposed_members):
                if proposed_index in taken:
                    continue
                if candidate.position is not None and candidate.position == member.sort_order:
                    claimed[index] = (proposed_index, MATCHED_BY_POSITION)
                    taken.add(proposed_index)
                    break

    decisions: list[MemberDecision] = []
    for index, member in enumerate(stored_members):
        match = claimed.get(index)
        if match is None:
            decisions.append(
                MemberDecision(movie_id=member.movie_id, title=member.title, outcome=OUTCOME_UNMATCHED)
            )
            continue
        proposed_index, matched_by = match
        candidate = proposed_members[proposed_index]
        if not candidate.release_id:
            outcome = OUTCOME_NO_RELEASE_ID
        elif candidate.release_id in member.release_ids:
            outcome = OUTCOME_ALREADY_IDENTIFIED
        elif member.release_ids:
            # The member already names another release. That may be a deliberate choice, or a stale
            # one; either way it is not this module's to overwrite.
            outcome = OUTCOME_CONFLICT
        else:
            outcome = OUTCOME_IDENTIFIED
        decisions.append(
            MemberDecision(
                movie_id=member.movie_id,
                title=member.title,
                outcome=outcome,
                release_id=candidate.release_id,
                matched_by=matched_by,
                proposal_position=candidate.position,
                proposal_title=candidate.title,
            )
        )

    claimed_proposals = {proposed_index for proposed_index, _ in claimed.values()}
    proposal_only = tuple(
        member.title for index, member in enumerate(proposed_members) if index not in claimed_proposals
    )
    return RepairPlan(decisions=tuple(decisions), proposal_only_titles=proposal_only)


def proposed_members_from_source(raw_members: Iterable[Any]) -> list[ProposedMember]:
    """Read the member dicts a box-set proposal carries.

    The names are the ones `box_set_member_movievault_id` accepts, because the same fact arrives
    under several spellings depending on how far the proposal has travelled. The live resolver names
    no release at all for a member, which is why `release_id` can be empty and the outcome is
    `no_release_id` instead of a guess.
    """
    members: list[ProposedMember] = []
    for item in raw_members:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or item.get("canonicalTitle") or "").strip()
        if not title:
            continue
        raw_position = item.get("position")
        try:
            position = int(raw_position) if raw_position is not None and str(raw_position).strip() else None
        except (TypeError, ValueError):
            position = None
        release_id = str(
            item.get("releaseId")
            or item.get("release_id")
            or item.get("movieVaultId")
            or item.get("movievaultId")
            or item.get("movievault_id")
            or ""
        ).strip()
        members.append(ProposedMember(position=position, title=title, release_id=release_id))
    return members


def _load_box_set(conn, container_id: UUID) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, container_type, title, barcode FROM containers WHERE id = %s",
            (container_id,),
        )
        row = cur.fetchone()
    if not row:
        raise NextApiError("Container not found", 404, "container_not_found")
    return dict(row)


def _load_stored_members(conn, container_id: UUID, provider_id: str) -> list[StoredMember]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.id::text AS movie_id,
                   m.title AS title,
                   cm.sort_order AS sort_order,
                   COALESCE(
                       array_agg(mi.identifier ORDER BY mi.identifier)
                           FILTER (WHERE mi.identifier IS NOT NULL),
                       ARRAY[]::text[]
                   ) AS release_ids
            FROM container_movies cm
            JOIN movies m ON m.id = cm.movie_id AND m.deleted_at IS NULL
            LEFT JOIN movie_identifiers mi
                   ON mi.movie_id = m.id
                  AND mi.provider_id = %s
                  AND mi.identifier_type = 'movie_id'
            WHERE cm.container_id = %s
            GROUP BY m.id, m.title, cm.sort_order
            ORDER BY cm.sort_order, m.title
            """,
            (provider_id, container_id),
        )
        rows = cur.fetchall()
    return [
        StoredMember(
            movie_id=str(row["movie_id"]),
            title=str(row["title"] or ""),
            sort_order=int(row["sort_order"] or 0),
            release_ids=tuple(str(value) for value in (row["release_ids"] or [])),
        )
        for row in rows
    ]


def _refresh_pending(conn, job_type: str, movie_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM background_jobs j
                WHERE j.job_type = %s
                  AND j.status IN ('pending', 'running')
                  AND j.payload ->> 'movieId' = %s
            ) AS pending
            """,
            (job_type, movie_id),
        )
        row = cur.fetchone()
    return bool(row and row["pending"])


def refresh_box_set_members(
    conn,
    container_id: UUID,
    *,
    actor: dict[str, Any] | None,
    lookup_members: Callable[[Any, str, dict[str, Any] | None], list[dict[str, Any]]],
    create_job: Callable[..., Any],
    emit_identifiers_change: Callable[..., Any],
    job_type: str,
    provider_id: str,
) -> dict[str, Any]:
    """Repair the member identities of one box set and queue their refreshes.

    `lookup_members(conn, barcode, actor)` returns the member dicts of the box-set proposal for that
    barcode, as the import would see them. It reaches the network, so the caller's transaction is
    committed first and nothing is held open while a provider answers.

    Raises `NextApiError` with a stable code when the container is not a box set (422), has no real
    barcode (422), or the source names fewer than two members (422). It never creates, removes or
    reorders a member, and it never writes a format.
    """
    container = _load_box_set(conn, container_id)
    if container["container_type"] != "box_set":
        raise NextApiError("Only a box set can be refreshed this way.", 422, ERROR_NOT_A_BOX_SET)
    barcode = real_barcode(container.get("barcode"))
    if not barcode:
        raise NextApiError(
            "This box set has no barcode to look the members up by.", 422, ERROR_NO_BARCODE
        )

    # No snapshot and no lock while the providers answer, for the reason the import gives.
    conn.commit()
    proposed = proposed_members_from_source(lookup_members(conn, barcode, actor))
    if len(proposed) < 2:
        raise NextApiError(
            "The catalogue names fewer than two members for this barcode.", 422, ERROR_NO_PROPOSAL
        )

    stored = _load_stored_members(conn, container_id, provider_id)
    plan = plan_member_repairs(stored, proposed)

    identified = 0
    queued = 0
    queue_available = _table_exists(conn, "background_jobs")
    for decision in plan.decisions:
        if decision.outcome == OUTCOME_IDENTIFIED:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO movie_identifiers (movie_id, provider_id, identifier_type, identifier)
                    VALUES (%s, %s, 'movie_id', %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (decision.movie_id, provider_id, decision.release_id),
                )
            emit_identifiers_change(conn, decision.movie_id, operation="upsert")
            identified += 1
        if decision.outcome in REFRESHABLE_OUTCOMES and queue_available:
            if not _refresh_pending(conn, job_type, decision.movie_id):
                create_job(
                    conn,
                    job_type=job_type,
                    payload={
                        "movieId": decision.movie_id,
                        "dryRun": False,
                        "reason": "box_set_refresh",
                        "source": f"{provider_id}.box_set_refresh",
                    },
                )
                queued += 1

    counts: dict[str, int] = defaultdict(int)
    for decision in plan.decisions:
        counts[decision.outcome] += 1
    return {
        "containerId": str(container_id),
        "members": [decision.as_payload() for decision in plan.decisions],
        "counts": {
            OUTCOME_IDENTIFIED: counts[OUTCOME_IDENTIFIED],
            OUTCOME_ALREADY_IDENTIFIED: counts[OUTCOME_ALREADY_IDENTIFIED],
            OUTCOME_CONFLICT: counts[OUTCOME_CONFLICT],
            OUTCOME_NO_RELEASE_ID: counts[OUTCOME_NO_RELEASE_ID],
            OUTCOME_UNMATCHED: counts[OUTCOME_UNMATCHED],
        },
        "identified": identified,
        "jobsQueued": queued,
        "proposalOnlyTitles": list(plan.proposal_only_titles),
    }


def _table_exists(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL AS present", (table_name,))
        row = cur.fetchone()
    return bool(row and row["present"])
