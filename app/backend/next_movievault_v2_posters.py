"""Bounded, anonymous poster caching for the MovieVault-v2 distribution-4
contract.

This module implements the background-job side of issue #293: for every
changed selected poster discovered during index sync (see
``next_movievault_v2._enqueue_poster_cache``), fetch the MovieVault-hosted
bytes anonymously and store a validated, authenticated DiscVault-local copy
in ``media_assets`` before ever exposing it to a browser or mobile client.

Non-negotiables enforced here:

- The remote fetch is anonymous and bounded: no cookies, auth headers,
  instance identity, contribution credentials, or request telemetry are
  sent or persisted (see ``next_movievault_v2._request``, reused as-is).
- The response is fully validated - HTTP status, exact media type, byte
  limit, SHA-256 against the producer-declared checksum, and a real image
  decode with bounded dimensions - before any local file is atomically
  activated.
- On any validation failure the prior valid cached poster (if any) is
  retained; the cache row is only ever moved to ``degraded``/``error``,
  never silently marked ``ready`` on failure, and interrupted/invalid
  downloads never activate partial bytes (write-to-temp + atomic replace).
- A ``429`` response is honoured through a bounded ``Retry-After`` wait
  (clamped, few attempts) and then deferred to a later job - poster
  caching is a background job, so a rate limit never fails the lookup
  that discovered the poster.
- Cleanup only removes cache rows/bytes that are no longer referenced by
  any current release/box-set poster and have been stale past the
  repository's existing artwork retention convention (7 days) - never tied
  to collection membership and never based on retained request telemetry.
"""

from __future__ import annotations

import hashlib
import io
import os
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable

from PIL import Image, UnidentifiedImageError

try:
    from psycopg.types.json import Jsonb
except ModuleNotFoundError:  # pragma: no cover - test environments without psycopg
    class Jsonb:  # type: ignore[no-redef]
        def __init__(self, value: Any) -> None:
            self.value = value

try:
    from .next_movievault_v2 import (
        POSTER_CACHE_JOB_TYPE,
        POSTER_CLEANUP_JOB_TYPE,
        MovieVaultV2Error,
        _request,
    )
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_movievault_v2 import (
        POSTER_CACHE_JOB_TYPE,
        POSTER_CLEANUP_JOB_TYPE,
        MovieVaultV2Error,
        _request,
    )

__all__ = [
    "POSTER_CACHE_JOB_TYPE",
    "POSTER_CLEANUP_JOB_TYPE",
    "run_poster_cache_job",
    "run_poster_cleanup",
]

# HTTP status/media-type/byte-limit bounds for the anonymous poster fetch.
POSTER_FETCH_TIMEOUT_SECONDS = 20
POSTER_MAX_BYTES = 8 * 1024 * 1024
POSTER_MAX_DIMENSION = 4000

# Exact media types accepted from MovieVault - no wildcard matching.
POSTER_CONTENT_TYPES: dict[str, str] = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}
POSTER_EXTENSIONS: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

# MovieVault's poster variant names ("thumbnail"/"display") mapped onto
# DiscVault's own media_assets.variant enum ("thumb"/"display").
VARIANT_MEDIA_ASSET_MAP: dict[str, str] = {"thumbnail": "thumb", "display": "display"}

# Matches the existing "artwork_trash_retention" default of 7 days.
POSTER_CACHE_RETENTION_SECONDS = 7 * 24 * 60 * 60

# Bounded handling of MovieVault's documented 429 rate limit. The fetch runs
# inside a transaction holding a FOR UPDATE lock on the cache row, so the wait
# is clamped hard; anything longer is left to the next scheduled job rather
# than held open here.
POSTER_RATE_LIMIT_STATUS = 429
POSTER_RATE_LIMIT_MAX_RETRIES = 2
POSTER_RATE_LIMIT_MIN_WAIT_SECONDS = 1
POSTER_RATE_LIMIT_MAX_WAIT_SECONDS = 5


def _retry_after_seconds(value: str | None) -> int:
    """Parse a `Retry-After` header (delta-seconds or HTTP-date, per RFC 9110)
    and clamp it into the bounded wait window. An absent or unparseable value
    falls back to the minimum wait so a rate limit is always respected."""
    if value is None:
        return POSTER_RATE_LIMIT_MIN_WAIT_SECONDS
    raw = value.strip()
    seconds: float | None = None
    try:
        seconds = float(int(raw))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            seconds = (parsed - datetime.now(timezone.utc)).total_seconds()
    if seconds is None:
        return POSTER_RATE_LIMIT_MIN_WAIT_SECONDS
    return int(
        max(
            POSTER_RATE_LIMIT_MIN_WAIT_SECONDS,
            min(POSTER_RATE_LIMIT_MAX_WAIT_SECONDS, seconds),
        )
    )


def _poster_data_dir() -> Path:
    """Self-contained duplicate of next_app.legacy_data_dir(). Kept local to
    avoid importing next_app from a module that next_app itself may load
    (next_worker imports this module without importing the Flask app)."""
    raw = (
        os.environ.get("DISCVAULT_LEGACY_DATA_DIR")
        or os.environ.get("DISCVAULT_SQLITE_IMPORT_DATA")
        or "/data"
    )
    return Path(raw).expanduser()


def _content_type_key(header_value: str | None) -> str | None:
    if not header_value:
        return None
    return header_value.split(";", 1)[0].strip().lower()


def _decode_and_validate_image(content: bytes, content_type: str) -> tuple[int, int]:
    expected_format = POSTER_CONTENT_TYPES[content_type]
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
        with Image.open(io.BytesIO(content)) as image:
            if image.format != expected_format:
                raise MovieVaultV2Error("poster_decode_invalid")
            width, height = image.size
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise MovieVaultV2Error("poster_decode_invalid") from exc
    if width <= 0 or height <= 0 or width > POSTER_MAX_DIMENSION or height > POSTER_MAX_DIMENSION:
        raise MovieVaultV2Error("poster_dimensions_invalid")
    return width, height


def _store_poster_bytes(content: bytes, checksum: str, variant: str, extension: str) -> str:
    data_dir = _poster_data_dir().resolve()
    target_dir = data_dir / "media" / "movievault_posters" / variant / checksum[:2] / checksum[2:4]
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{checksum}.{extension}"
    if not target.exists():
        temp_path = target_dir / f".{checksum}.{uuid.uuid4().hex}.tmp"
        temp_path.write_bytes(content)
        os.replace(temp_path, target)
    return target.relative_to(data_dir).as_posix()


def _activate_media_asset(
    cur: Any,
    *,
    variant: str,
    storage_key: str,
    content_type: str,
    width: int,
    height: int,
    size_bytes: int,
    checksum: str,
    asset_id: str,
) -> str:
    media_variant = VARIANT_MEDIA_ASSET_MAP[variant]
    cur.execute(
        """
        INSERT INTO media_assets (
            kind, variant, storage_backend, storage_key, provider_id,
            content_type, width, height, size_bytes, sha256
        )
        VALUES ('poster', %s, 'local', %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (kind, variant, sha256) DO UPDATE SET
            storage_key = EXCLUDED.storage_key
        RETURNING id
        """,
        (
            media_variant,
            storage_key,
            f"movievault_v2:{asset_id}",
            content_type,
            width,
            height,
            size_bytes,
            checksum,
        ),
    )
    row = cur.fetchone()
    media_asset_id = row["id"] if isinstance(row, dict) else row[0]
    return str(media_asset_id)


def run_poster_cache_job(
    conn: Any,
    payload: dict[str, Any],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Fetch, validate, store, and atomically activate one cached poster
    variant. Never raises on a validated remote/content failure - those are
    recorded on the cache row (degraded/error) so the prior valid poster
    keeps serving. Only programming/database errors propagate."""
    cache_id = payload.get("cacheId")
    asset_id = payload.get("assetId")
    variant = payload.get("variant")
    checksum = payload.get("checksum")
    origin = payload.get("origin")
    path = payload.get("path")
    if not all([cache_id, asset_id, variant, checksum, origin, path]):
        raise MovieVaultV2Error("poster_job_payload_invalid")
    if variant not in VARIANT_MEDIA_ASSET_MAP:
        raise MovieVaultV2Error("poster_job_payload_invalid")

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, media_asset_id FROM movievault_v2_poster_cache WHERE id = %s FOR UPDATE",
                (cache_id,),
            )
            row = cur.fetchone()
            if row is None:
                return {"handled": True, "skipped": "cache_row_missing", "cacheId": str(cache_id)}
            prior_media_asset_id = row["media_asset_id"] if isinstance(row, dict) else row[1]

            outcome = _fetch_and_activate(
                cur,
                origin=str(origin),
                path=str(path),
                checksum=str(checksum),
                variant=str(variant),
                asset_id=str(asset_id),
                sleep=sleep,
            )

            if outcome["status"] == "ready":
                cur.execute(
                    """
                    UPDATE movievault_v2_poster_cache
                    SET status = 'ready',
                        media_asset_id = %s,
                        attempts = 0,
                        last_error = NULL,
                        checked_at = now()
                    WHERE id = %s
                    """,
                    (outcome["mediaAssetId"], cache_id),
                )
            else:
                # Preserve the prior valid poster: media_asset_id is left untouched.
                fallback_status = "degraded" if prior_media_asset_id else "error"
                cur.execute(
                    """
                    UPDATE movievault_v2_poster_cache
                    SET status = %s,
                        attempts = attempts + 1,
                        last_error = %s,
                        checked_at = now()
                    WHERE id = %s
                    """,
                    (fallback_status, outcome["error"], cache_id),
                )
    return {
        "handled": True,
        "cacheId": str(cache_id),
        "assetId": str(asset_id),
        "variant": str(variant),
        "outcome": outcome["status"],
    }


def _fetch_and_activate(
    cur: Any,
    *,
    origin: str,
    path: str,
    checksum: str,
    variant: str,
    asset_id: str,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    accept = ", ".join(POSTER_CONTENT_TYPES)
    for attempt in range(POSTER_RATE_LIMIT_MAX_RETRIES + 1):
        try:
            status, content, headers = _request(
                f"{origin}{path}",
                accept=accept,
                timeout_seconds=POSTER_FETCH_TIMEOUT_SECONDS,
                maximum_bytes=POSTER_MAX_BYTES,
                passthrough_statuses=frozenset({POSTER_RATE_LIMIT_STATUS}),
            )
        except MovieVaultV2Error as exc:
            return {"status": "failed", "error": str(exc)}
        if status != POSTER_RATE_LIMIT_STATUS:
            break
        if attempt == POSTER_RATE_LIMIT_MAX_RETRIES:
            # Rate limited beyond the bounded window: the cache row is marked
            # degraded/error by the caller and retried by a later job. This is
            # a background job, so it never affects a live lookup.
            return {"status": "failed", "error": "rate_limited"}
        sleep(_retry_after_seconds(headers.get("retry-after")))
    if status != 200:
        return {"status": "failed", "error": f"http_status_{status}"}
    content_type = _content_type_key(headers.get("content-type"))
    if content_type not in POSTER_CONTENT_TYPES:
        return {"status": "failed", "error": "content_type_invalid"}
    if len(content) == 0 or len(content) > POSTER_MAX_BYTES:
        return {"status": "failed", "error": "byte_limit_invalid"}
    digest = hashlib.sha256(content).hexdigest()
    if digest != checksum:
        return {"status": "failed", "error": "checksum_mismatch"}
    try:
        width, height = _decode_and_validate_image(content, content_type)
    except MovieVaultV2Error as exc:
        return {"status": "failed", "error": str(exc)}
    try:
        storage_key = _store_poster_bytes(
            content, checksum, variant, POSTER_EXTENSIONS[content_type]
        )
    except OSError as exc:
        return {"status": "failed", "error": f"storage_error:{exc}"}
    media_asset_id = _activate_media_asset(
        cur,
        variant=variant,
        storage_key=storage_key,
        content_type=content_type,
        width=width,
        height=height,
        size_bytes=len(content),
        checksum=checksum,
        asset_id=asset_id,
    )
    return {"status": "ready", "mediaAssetId": media_asset_id}


def run_poster_cleanup(
    conn: Any,
    *,
    limit: int = 200,
    retention_seconds: int = POSTER_CACHE_RETENTION_SECONDS,
) -> dict[str, Any]:
    """Delete cache rows (and their local media_assets bytes) that no longer
    match any current release/box-set poster and have been stale past the
    retention window. Independent of collection membership - only the
    sync-owned poster tables and media_assets are consulted."""
    removed = 0
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, media_asset_id
                FROM movievault_v2_poster_cache pc
                WHERE checked_at < now() - (%s || ' seconds')::interval
                  AND NOT EXISTS (
                        SELECT 1 FROM movievault_v2_releases r
                        WHERE r.poster IS NOT NULL
                          AND r.poster->>'assetId' = pc.asset_id
                          AND (r.poster->pc.variant->>'checksum') = pc.checksum
                  )
                  AND NOT EXISTS (
                        SELECT 1 FROM movievault_v2_box_sets b
                        WHERE b.poster IS NOT NULL
                          AND b.poster->>'assetId' = pc.asset_id
                          AND (b.poster->pc.variant->>'checksum') = pc.checksum
                  )
                ORDER BY checked_at ASC
                LIMIT %s
                FOR UPDATE SKIP LOCKED
                """,
                (retention_seconds, limit),
            )
            candidates = cur.fetchall()
            for candidate in candidates:
                cache_id = candidate["id"] if isinstance(candidate, dict) else candidate[0]
                media_asset_id = candidate["media_asset_id"] if isinstance(candidate, dict) else candidate[1]
                if media_asset_id:
                    cur.execute(
                        "SELECT storage_backend, storage_key FROM media_assets WHERE id = %s",
                        (media_asset_id,),
                    )
                    asset_row = cur.fetchone()
                    cur.execute(
                        "SELECT 1 FROM movievault_v2_poster_cache WHERE media_asset_id = %s AND id != %s",
                        (media_asset_id, cache_id),
                    )
                    still_referenced = cur.fetchone() is not None
                    cur.execute("DELETE FROM movievault_v2_poster_cache WHERE id = %s", (cache_id,))
                    if not still_referenced:
                        cur.execute("DELETE FROM media_assets WHERE id = %s", (media_asset_id,))
                        if asset_row:
                            backend = asset_row["storage_backend"] if isinstance(asset_row, dict) else asset_row[0]
                            storage_key = asset_row["storage_key"] if isinstance(asset_row, dict) else asset_row[1]
                            if backend == "local" and storage_key:
                                try:
                                    (_poster_data_dir().resolve() / storage_key).unlink(missing_ok=True)
                                except OSError:
                                    pass
                else:
                    cur.execute("DELETE FROM movievault_v2_poster_cache WHERE id = %s", (cache_id,))
                removed += 1
    return {"handled": True, "removed": removed, "retentionSeconds": retention_seconds}
