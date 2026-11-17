"""Box-set barcode checks: is a box-set's barcode also on a release?

A box-set (`containers.container_type = 'box_set'`) and a release (a row in
`movies`, i.e. one physical edition of a film or series) are two different
things, and a printed barcode belongs to exactly one of them. When the same
code -- or approximately the same code, see
``dedup_identity.barcode_match_keys`` -- turns up on both, one of two things
went wrong, and the two are told apart by membership:

* **The release is a member of that box-set** (``member_carries_box_barcode``).
  A film inside the box was given the box's barcode, typically because the box
  was scanned once and its EAN was copied to every disc. The member's barcode
  is wrong; the box-set is fine.
* **The release is not a member** (``box_set_imported_as_release``). The box
  was also added as if it were a single film -- the scan resolved to a release
  instead of a box-set. That release row is the duplicate.

The check only reports; it changes nothing. Which record is wrong is a human
call, and both remedies (fix a member's barcode, or remove the stray release)
already exist in the UI.

Both the resolving barcode (`movies.barcode`) and the descriptive scannable
codes (`movie_product_identifiers`, EAN/UPC/ISBN) are read, because a box EAN
recorded only as a secondary identifier is still a box EAN on a release.
Tombstoned rows on either side are ignored.
"""

from __future__ import annotations

from typing import Any

try:
    from .dedup_identity import barcode_match_keys
    from .next_common import table_exists
except ImportError:  # pragma: no cover - direct module execution compatibility
    from dedup_identity import barcode_match_keys
    from next_common import table_exists


KIND_MEMBER_CARRIES_BOX_BARCODE = "member_carries_box_barcode"
KIND_BOX_SET_IMPORTED_AS_RELEASE = "box_set_imported_as_release"

_SCANNABLE_IDENTIFIER_TYPES = ("ean", "upc", "isbn")


def _digits(value: Any) -> str:
    return "".join(character for character in str(value or "") if character.isdigit())


def _live_box_sets(conn) -> list[dict[str, Any]]:
    member_count = (
        "(SELECT COUNT(*)::int FROM container_movies cm WHERE cm.container_id = c.id)"
        if table_exists(conn, "container_movies")
        else "0"
    )
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT c.id, c.title, c.year, c.barcode, {member_count} AS member_count
            FROM containers c
            WHERE c.container_type = 'box_set'
              AND c.deleted_at IS NULL
              AND NULLIF(BTRIM(COALESCE(c.barcode, '')), '') IS NOT NULL
            ORDER BY lower(c.title), c.id
            """
        )
        return list(cur.fetchall())


def _release_codes(conn) -> list[dict[str, Any]]:
    """Every barcode a live release is known by, one row per (release, code)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.id, m.title, m.year, m.format, m.barcode AS code,
                   'barcode' AS source
            FROM movies m
            WHERE m.deleted_at IS NULL
              AND NULLIF(BTRIM(COALESCE(m.barcode, '')), '') IS NOT NULL
            """
        )
        rows = list(cur.fetchall())
    if table_exists(conn, "movie_product_identifiers"):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.id, m.title, m.year, m.format, mpi.identifier_value AS code,
                       'product_identifier' AS source
                FROM movie_product_identifiers mpi
                JOIN movies m ON m.id = mpi.movie_id AND m.deleted_at IS NULL
                WHERE mpi.identifier_type = ANY(%s)
                """,
                (list(_SCANNABLE_IDENTIFIER_TYPES),),
            )
            rows.extend(cur.fetchall())
    return rows


def _memberships(conn, box_set_ids: list[Any]) -> set[tuple[str, str]]:
    if not box_set_ids or not table_exists(conn, "container_movies"):
        return set()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT container_id, movie_id
            FROM container_movies
            WHERE container_id = ANY(%s)
            """,
            ([str(value) for value in box_set_ids],),
        )
        return {(str(row["container_id"]), str(row["movie_id"])) for row in cur.fetchall()}


def find_box_set_release_barcode_overlaps(conn) -> list[dict[str, Any]]:
    """Box-sets whose barcode (approximately) also identifies a live release.

    One entry per affected box-set, each listing the matching releases with the
    ``kind`` of problem each one represents (see the module docstring).
    """
    if not table_exists(conn, "containers") or not table_exists(conn, "movies"):
        return []
    box_sets = _live_box_sets(conn)
    if not box_sets:
        return []

    releases_by_key: dict[str, list[dict[str, Any]]] = {}
    for row in _release_codes(conn):
        for key in barcode_match_keys(row.get("code")):
            releases_by_key.setdefault(key, []).append(row)

    memberships = _memberships(conn, [box_set["id"] for box_set in box_sets])
    overlaps: list[dict[str, Any]] = []
    for box_set in box_sets:
        box_set_id = str(box_set["id"])
        box_digits = _digits(box_set.get("barcode"))
        matched: dict[str, dict[str, Any]] = {}
        for key in barcode_match_keys(box_set.get("barcode")):
            for release in releases_by_key.get(key, []):
                release_id = str(release["id"])
                exact = _digits(release.get("code")) == box_digits
                current = matched.get(release_id)
                # One entry per release; prefer the exact code, then the
                # resolving barcode over a secondary identifier.
                if current is not None and (
                    current["match"] == "exact"
                    and (not exact or current["source"] == "barcode")
                ):
                    continue
                is_member = (box_set_id, release_id) in memberships
                matched[release_id] = {
                    "movieId": release_id,
                    "title": release.get("title"),
                    "year": release.get("year"),
                    "format": release.get("format"),
                    "barcode": release.get("code"),
                    "source": release.get("source"),
                    "match": "exact" if exact else "approximate",
                    "isMember": is_member,
                    "kind": (
                        KIND_MEMBER_CARRIES_BOX_BARCODE
                        if is_member
                        else KIND_BOX_SET_IMPORTED_AS_RELEASE
                    ),
                }
        if not matched:
            continue
        releases = sorted(
            matched.values(),
            key=lambda item: (str(item.get("title") or "").casefold(), item["movieId"]),
        )
        overlaps.append(
            {
                "containerId": box_set_id,
                "title": box_set.get("title"),
                "year": box_set.get("year"),
                "barcode": box_set.get("barcode"),
                "memberCount": int(box_set.get("member_count") or 0),
                "match": (
                    "exact"
                    if all(item["match"] == "exact" for item in releases)
                    else "approximate"
                ),
                "releases": releases,
            }
        )
    return overlaps


def box_set_release_barcode_summary(conn, *, limit: int | None = None) -> dict[str, Any]:
    """Counts plus (optionally capped) items, in the shape the admin UI reads."""
    overlaps = find_box_set_release_barcode_overlaps(conn)
    releases = [release for item in overlaps for release in item["releases"]]
    return {
        "counts": {
            "boxSets": len(overlaps),
            "releases": len(releases),
            KIND_MEMBER_CARRIES_BOX_BARCODE: sum(
                1 for release in releases if release["kind"] == KIND_MEMBER_CARRIES_BOX_BARCODE
            ),
            KIND_BOX_SET_IMPORTED_AS_RELEASE: sum(
                1 for release in releases if release["kind"] == KIND_BOX_SET_IMPORTED_AS_RELEASE
            ),
        },
        "items": overlaps if limit is None else overlaps[:limit],
    }
