"""Field corrections: resolving a local record to a MovieVault target, and
working out what may honestly be said about it.

This is the server-owned half of `contribution-2`. Everything a client could get
wrong lives here: which record upstream a local one is, which fields may be
corrected, what the catalogue currently holds, and what the local row says. A
client asks; it never decides.

That boundary is not tidiness. `docs/contracts/contribution-v2.md` requires it --
the browser and the native apps all reach MovieVault's contribution surface
through their own DiscVault instance, and the credentials never leave the
server. A client that re-derived the eligible-field list would re-derive the
mistakes with it, three times over, in three languages.

The hard part is not the protocol. It is that DiscVault and MovieVault describe
a disc differently, and a field that looks like a counterpart often is not one.
Every exclusion below is a place where translating would have invented data.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

try:
    from .next_metadata import movie_identifiers
    from .next_metadata import movie_locked_fields
    from .next_movievault_v2 import MOVIEVAULT_V2_PLUGIN_ID
except ImportError:  # pragma: no cover - supports direct module execution
    from next_metadata import movie_identifiers
    from next_metadata import movie_locked_fields
    from next_movievault_v2 import MOVIEVAULT_V2_PLUGIN_ID

#: The provider whose identifier names a MovieVault release. Written by the v2
#: plugin since 26.8.9; read here as the first and most reliable way to say
#: which catalogue record a local movie is.
MOVIEVAULT_V2_PROVIDER = "movievault_v2"

_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
#: MovieVault stores `country_code char(2)` and validates `^[A-Z]{2}$`.
_COUNTRY_PATTERN = re.compile(r"^[A-Z]{2}$")
#: MovieVault puts no pattern on `language_code`, which means it would happily
#: store "Dutch". DiscVault's `movies.language` is free text, so the shape is
#: checked here rather than upstream -- the alternative is polluting a shared
#: catalogue with a value only this instance can read.
_LANGUAGE_PATTERN = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$")


class FieldCorrectionError(Exception):
    """A refusal a caller can act on, carrying a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


#: What MovieVault will accept for a release (`contribution-2` §"Eligible
#: fields") mapped onto where DiscVault keeps it. A field absent from this table
#: is in `RELEASE_FIELDS_WITHHELD` with the reason it cannot travel.
RELEASE_FIELD_SOURCES: dict[str, tuple[str, str]] = {
    "title": ("movie", "title"),
    "edition": ("movie", "edition"),
    "format": ("movie", "format"),
    "countryCode": ("movie", "country"),
    "languageCode": ("movie", "language"),
    "releaseDate": ("movie", "release_date"),
    "runtimeMinutes": ("movie", "runtime_minutes"),
    "distributor": ("metadata", "distributor"),
}

#: Eligible upstream, deliberately not offered here. Each entry is a place where
#: DiscVault and MovieVault use one word for two things, and translating would
#: have produced a confident wrong answer rather than a visible gap.
RELEASE_FIELDS_WITHHELD: dict[str, str] = {
    # MovieVault holds a list and the contract makes it a complete replacement.
    # DiscVault holds one `movies.barcode`. Sending `[barcode]` would delete
    # every other EAN the release has, and the moderator would see a plausible
    # one-item list rather than a deletion.
    "eans": "discvault_holds_one_barcode",
    # `content.releases.region` is free text at release level. DiscVault's
    # `regions` is a normalised list of disc regions on the technical spec.
    # Same word, different fact.
    "region": "different_field_upstream",
    # MovieVault has one `studio`; DiscVault has `studios`, a list. Joining a
    # list into one string is lossy in a way a moderator cannot see.
    "studio": "discvault_holds_a_list",
    # DiscVault has no disc count. `movie_technical_specs` does not carry one
    # and the edit form does not offer one.
    "discCount": "not_stored_by_discvault",
}

#: A box set is nearly empty on this route, and that is a fact about the data
#: rather than a limitation of the mechanism. `containers` holds title, barcode,
#: badge_label, year and description -- of MovieVault's six correctable box-set
#: fields, only the title has a counterpart.
BOX_SET_FIELD_SOURCES: dict[str, tuple[str, str]] = {
    "title": ("container", "title"),
}

BOX_SET_FIELDS_WITHHELD: dict[str, str] = {
    "edition": "not_stored_by_discvault",
    "format": "not_stored_by_discvault",
    "countryCode": "not_stored_by_discvault",
    "languageCode": "not_stored_by_discvault",
    # `containers.year` is the box set's own year; MovieVault's `year_range`
    # describes the span of the films inside it. Different questions.
    "yearRange": "different_field_upstream",
}

#: Which local lock names cover a correctable field. The lock vocabulary is
#: snake_case canonical (`movie_locked_fields`), the wire vocabulary is
#: camelCase, and one lock can cover more than one spelling.
_FIELD_LOCK_NAMES: dict[str, tuple[str, ...]] = {
    "title": ("title",),
    "edition": ("edition",),
    "format": ("format",),
    "countryCode": ("country",),
    "languageCode": ("language",),
    "releaseDate": ("release_date",),
    "runtimeMinutes": ("runtime_minutes",),
    "distributor": ("distributor",),
}


def barcode_lookup_hash(barcode: Any) -> str | None:
    """The sha256 MovieVault's lookup index is keyed by.

    Digits only, and only at a length an EAN/UPC/GTIN actually uses -- the same
    normalisation the v2 plugin and both native clients apply, because a hash
    computed differently is simply a different hash and would silently miss.
    """
    digits = re.sub(r"[^0-9]", "", str(barcode or ""))
    if len(digits) not in {8, 12, 13, 14}:
        return None
    return hashlib.sha256(digits.encode("ascii")).hexdigest()


def _active_generation(conn: Any) -> Any:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT active_generation FROM movievault_v2_sync_state WHERE plugin_id = %s",
            (MOVIEVAULT_V2_PLUGIN_ID,),
        )
        row = cur.fetchone()
    return (dict(row).get("active_generation") if row else None) or None


def _stored_release_id(conn: Any, movie_id: Any) -> str | None:
    """The catalogue release this movie was matched to, if it carries one.

    Preferred over the barcode because it survives a barcode the moderator
    declined: such a record has no lookup hash at all, and the stored id is then
    the only thread back to it.
    """
    identifiers = movie_identifiers(conn, movie_id)
    value = str(identifiers.get("movieVaultId") or "").strip()
    if not value or not _UUID_PATTERN.fullmatch(value):
        return None
    # `movieVaultId` is only a v2 release id when a v2 identifier row is what
    # produced it. `select_movievault_identifier` prefers the v2 generation, but
    # an instance with only a `movievault_26` row would land a non-UUID here --
    # and an import-derived UUIDv5 would land a UUID that names nothing. The
    # provider is therefore checked rather than the shape alone.
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM movie_identifiers
            WHERE movie_id = %s AND provider_id = %s
              AND identifier_type = 'movie_id' AND identifier = %s
            """,
            (movie_id, MOVIEVAULT_V2_PROVIDER, value),
        )
        return value if cur.fetchone() else None


def _entity_revision(conn: Any, generation: Any, entity_type: str, entity_id: Any) -> int | None:
    table, key = (
        ("movievault_v2_releases", "release_id")
        if entity_type == "release"
        else ("movievault_v2_box_sets", "box_set_id")
    )
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT revision FROM {table} WHERE generation = %s AND {key} = %s",
            (generation, entity_id),
        )
        row = cur.fetchone()
    return int(dict(row)["revision"]) if row else None


def _lookup_entity(conn: Any, generation: Any, lookup_hash: str) -> tuple[str, Any] | None:
    """One barcode may name several records; only an unambiguous hit is a target.

    A barcode that resolves to two entities is exactly the ambiguity the
    fallback picker exists for, and guessing one here would attach a correction
    to a record nobody chose.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT entity_type, entity_id
            FROM movievault_v2_lookup_hashes
            WHERE generation = %s AND lookup_hash = %s
            """,
            (generation, lookup_hash),
        )
        rows = [dict(row) for row in cur.fetchall()]
    if len(rows) != 1:
        return None
    return str(rows[0]["entity_type"]), rows[0]["entity_id"]


def resolve_target(conn: Any, *, entity: str, record: dict[str, Any]) -> dict[str, Any] | None:
    """Which MovieVault record this local one is, and which version was seen.

    Order matters: the stored identifier first, the barcode second. A record
    that has both should be named by the id, because the id says "this is that
    release" while a barcode says "something with this barcode" -- and the
    second is only as good as the mirror's current contents.

    Returns None when nothing resolves. That is not an error; for a movie it
    means the record is a proposal rather than a correction, and for a box set
    it means there is nothing to correct.
    """
    generation = _active_generation(conn)
    if not generation:
        return None

    if entity == "movie":
        release_id = _stored_release_id(conn, record.get("id"))
        if release_id:
            revision = _entity_revision(conn, generation, "release", release_id)
            if revision is not None:
                return {
                    "entityType": "release",
                    "entityId": str(release_id),
                    "baseRevision": revision,
                    "matchedBy": "identifier",
                }

    lookup_hash = barcode_lookup_hash(record.get("barcode"))
    if not lookup_hash:
        return None
    match = _lookup_entity(conn, generation, lookup_hash)
    if not match:
        return None
    entity_type, entity_id = match
    # A movie may not correct a box set and a container may not correct a
    # release. The barcode index holds both, and a box-set barcode on a movie
    # row is a real shape -- it means the disc is part of a set.
    if (entity == "movie") != (entity_type == "release"):
        return None
    revision = _entity_revision(conn, generation, entity_type, entity_id)
    if revision is None:
        return None
    return {
        "entityType": entity_type,
        "entityId": str(entity_id),
        "baseRevision": revision,
        "matchedBy": "barcode",
    }


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        text = " ".join(value.split())
        return text or None
    return value


def _local_release_values(record: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field, (source, column) in RELEASE_FIELD_SOURCES.items():
        raw = metadata.get(column) if source == "metadata" else record.get(column)
        value = _clean(raw)
        if field == "releaseDate" and value is not None:
            value = value.isoformat() if hasattr(value, "isoformat") else str(value)
        if field == "runtimeMinutes" and value is not None:
            value = int(value)
        values[field] = value
    return values


def _local_box_set_values(record: dict[str, Any]) -> dict[str, Any]:
    return {field: _clean(record.get(column)) for field, (_, column) in BOX_SET_FIELD_SOURCES.items()}


def mirror_values(conn: Any, target: dict[str, Any]) -> dict[str, Any] | None:
    """What the catalogue holds, in the wire vocabulary.

    This is what becomes `expected`. It has to come from the mirror rather than
    from anything the client sent: `expected` is the claim "I was looking at
    this", and only the server knows what was actually shown.
    """
    generation = _active_generation(conn)
    if not generation:
        return None
    if target["entityType"] == "release":
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT release_title, edition, format, country_code, language_code,
                       release_date, runtime_minutes, distributor
                FROM movievault_v2_releases
                WHERE generation = %s AND release_id = %s
                """,
                (generation, target["entityId"]),
            )
            row = cur.fetchone()
        if not row:
            return None
        row = dict(row)
        release_date = row.get("release_date")
        return {
            "title": _clean(row.get("release_title")),
            "edition": _clean(row.get("edition")),
            "format": _clean(row.get("format")),
            "countryCode": _clean(row.get("country_code")),
            "languageCode": _clean(row.get("language_code")),
            "releaseDate": release_date.isoformat() if release_date else None,
            "runtimeMinutes": int(row["runtime_minutes"]) if row.get("runtime_minutes") else None,
            "distributor": _clean(row.get("distributor")),
        }
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT title FROM movievault_v2_box_sets
            WHERE generation = %s AND box_set_id = %s
            """,
            (generation, target["entityId"]),
        )
        row = cur.fetchone()
    return {"title": _clean(dict(row).get("title"))} if row else None


def _withheld_reason(field: str, entity: str, value: Any) -> str | None:
    """Why a field with a local counterpart still may not travel.

    Both cases are about shape rather than policy. MovieVault validates
    `countryCode` as two capitals and would reject "Netherlands" outright; it
    puts no pattern at all on `languageCode`, which means it would *accept*
    "Dutch" and quietly poison a shared catalogue. The first is a bad request,
    the second is worse, and neither is worth guessing a mapping for.
    """
    if value is None:
        return None
    if field == "countryCode" and not _COUNTRY_PATTERN.fullmatch(str(value)):
        return "local_value_is_not_a_country_code"
    if field == "languageCode" and not _LANGUAGE_PATTERN.fullmatch(str(value)):
        return "local_value_is_not_a_language_code"
    return None


def eligible_fields(
    *,
    entity: str,
    local: dict[str, Any],
    locked: set[str],
) -> tuple[list[str], dict[str, str]]:
    """The fields this record may offer, and why each of the others may not.

    The refusals are returned rather than dropped so a client can explain
    itself. "Not offered" and "offered but unchanged" look identical in a UI
    that only receives a list.
    """
    sources = RELEASE_FIELD_SOURCES if entity == "movie" else BOX_SET_FIELD_SOURCES
    withheld = dict(RELEASE_FIELDS_WITHHELD if entity == "movie" else BOX_SET_FIELDS_WITHHELD)
    allowed: list[str] = []
    for field in sources:
        lock_names = _FIELD_LOCK_NAMES.get(field, ())
        if any(name in locked for name in lock_names):
            # A lock is a local override, pinned against metadata refresh.
            # Publishing it would push one person's preference into a shared
            # fact -- the exact inversion of what a lock is for.
            withheld[field] = "locked_locally"
            continue
        reason = _withheld_reason(field, entity, local.get(field))
        if reason:
            withheld[field] = reason
            continue
        allowed.append(field)
    return allowed, withheld


def build_changes(
    *,
    allowed: list[str],
    local: dict[str, Any],
    mirror: dict[str, Any],
    fields: list[str] | None = None,
) -> list[dict[str, Any]]:
    """The `changes[]` a correction would carry.

    Only fields that actually differ. A change that proposes what the catalogue
    already holds costs a moderator a decision and teaches nobody anything, and
    `contribution-2` bounds a submission at 25 fields -- spending them on
    agreement would be the wrong trade.
    """
    selected = set(fields) if fields is not None else None
    changes: list[dict[str, Any]] = []
    for field in allowed:
        if selected is not None and field not in selected:
            continue
        proposed = local.get(field)
        expected = mirror.get(field)
        if proposed is None or proposed == expected:
            continue
        changes.append({"field": field, "expected": expected, "proposed": proposed})
    return changes


def correction_preview(
    conn: Any,
    *,
    entity: str,
    record: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Everything a client needs to show a confirmation, in one answer.

    Deliberately not four round trips: the diff is only meaningful against the
    revision it was computed from, and a client that fetched the target, then
    the mirror, then the locks would be assembling a view the server could have
    handed it consistent.
    """
    target = resolve_target(conn, entity=entity, record=record)
    if not target:
        return {
            "mode": "proposal" if entity == "movie" else "unavailable",
            "target": None,
            "changes": [],
            "withheld": {},
        }
    mirror = mirror_values(conn, target)
    if mirror is None:
        # The lookup index named a record the mirror no longer holds. A stale
        # index is not a correction opportunity.
        return {"mode": "unavailable", "target": None, "changes": [], "withheld": {}}

    if entity == "movie":
        local = _local_release_values(record, metadata or {})
        locked = movie_locked_fields(metadata or {})
    else:
        local = _local_box_set_values(record)
        # Containers have no field locks -- the only container lock is artwork,
        # and no artwork field is correctable. So nothing is excluded here, and
        # equally nothing on a box set can be protected from being offered.
        locked = set()

    allowed, withheld = eligible_fields(entity=entity, local=local, locked=locked)
    return {
        "mode": "correction",
        "target": target,
        "changes": build_changes(allowed=allowed, local=local, mirror=mirror, fields=fields),
        "withheld": withheld,
    }
