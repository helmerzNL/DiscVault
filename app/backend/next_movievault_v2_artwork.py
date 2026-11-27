"""MovieVault artwork for one movie: its own release's cached poster, else another edition's.

The artwork rule (App-Guidance `projects/discvault/specs/artwork-precedence.md`) puts MovieVault's
cover in front of TMDB's, and says that a release without a poster of its own borrows the poster
of the first other release of the same film that has one: another edition's cover is still the
catalogue's, and a better answer than a cinema poster. This module is the server's half of that
second sentence.

It only reads the instance's own copy of the catalogue and the instance's own poster cache. It sends
nothing, so it may run on every refresh. A poster that has not been cached yet is *pending*: nothing
is returned, the next refresh finds it, and no remote MovieVault address is ever handed to a caller
(`next_movievault_v2._poster_status_fields` keeps that promise for the index, and so does this).

The order of "first other release" is the one Android uses (`V4CatalogDao.firstPosterForFilm`):
`revision` ascending, then `release_id` ascending. Two devices reading the same catalogue therefore
reach the same cover.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

# What the poster route serves. A MovieVault cover that has been cached is a `media_assets` row of
# the instance and is reached through this path, never through a MovieVault address.
LOCAL_POSTER_PREFIX = "/api/next/movievault-v2/posters/"

_LOCAL_POSTER_PATTERN = re.compile(
    r"^" + re.escape(LOCAL_POSTER_PREFIX) + r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)


def local_poster_url(media_asset_id: Any) -> str:
    return f"{LOCAL_POSTER_PREFIX}{media_asset_id}"


def media_asset_id_from_local_poster_url(value: Any) -> uuid.UUID | None:
    """The media asset a MovieVault poster path names, or None for any other string.

    Nothing but this exact shape counts. A relative path that is not a MovieVault poster is not an
    image the media pipeline may follow, which is why the check is a full match on a UUID.
    """
    match = _LOCAL_POSTER_PATTERN.match(str(value or "").strip())
    if not match:
        return None
    try:
        return uuid.UUID(match.group(1))
    except ValueError:  # pragma: no cover - the pattern already guarantees the shape
        return None


def _row_value(row: Any, key: str, index: int) -> Any:
    if row is None:
        return None
    return row[key] if isinstance(row, dict) else row[index]


def film_poster_for_release(conn: Any, release_id: Any) -> dict[str, Any] | None:
    """The cached cover to show for `release_id`, or None when there is nothing usable yet.

    Own cover first. Without one, the first other release of the same film that has a cover, in the
    order the module docstring names. That release is chosen whether or not its cover is cached yet:
    if it is not, the answer is None (pending), never the next release in line, because a different
    answer today and tomorrow is exactly what the order exists to prevent.

    Returns ``{"mediaAssetId", "releaseId", "source", "url"}`` where `source` is ``"release"`` or
    ``"film"``.
    """
    try:
        release_uuid = uuid.UUID(str(release_id))
    except (ValueError, TypeError):
        return None
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('movievault_v2_poster_cache') IS NOT NULL AS present")
        if not _row_value(cur.fetchone(), "present", 0):
            return None
        cur.execute(
            """
            WITH active AS (
                SELECT active_generation AS generation
                FROM movievault_v2_sync_state
                WHERE plugin_id = 'movievault_v2'
            ),
            own AS (
                SELECT r.release_id, r.film_id, r.poster
                FROM movievault_v2_releases r
                JOIN active a ON a.generation = r.generation
                WHERE r.release_id = %s
            ),
            chosen AS (
                SELECT o.release_id, o.poster, 'release' AS source
                FROM own o
                WHERE o.poster IS NOT NULL
                UNION ALL
                SELECT s.release_id, s.poster, 'film' AS source
                FROM (
                    SELECT r.release_id, r.poster
                    FROM movievault_v2_releases r
                    JOIN active a ON a.generation = r.generation
                    JOIN own o ON o.film_id = r.film_id
                    WHERE r.release_id <> o.release_id
                      AND r.poster IS NOT NULL
                      AND o.poster IS NULL
                    ORDER BY r.revision ASC, r.release_id ASC
                    LIMIT 1
                ) s
            )
            SELECT c.release_id, c.source, pc.media_asset_id
            FROM chosen c
            LEFT JOIN movievault_v2_poster_cache pc
                   ON pc.asset_id = c.poster->>'assetId'
                  AND pc.variant = 'display'
                  AND pc.checksum = c.poster->'display'->>'checksum'
                  AND pc.status = 'ready'
                  AND pc.media_asset_id IS NOT NULL
            LIMIT 1
            """,
            (release_uuid,),
        )
        row = cur.fetchone()
    media_asset_id = _row_value(row, "media_asset_id", 2)
    if not media_asset_id:
        return None
    return {
        "mediaAssetId": str(media_asset_id),
        "releaseId": str(_row_value(row, "release_id", 0)),
        "source": str(_row_value(row, "source", 1)),
        "url": local_poster_url(media_asset_id),
    }
