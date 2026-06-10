"""Profile account helpers for the DiscVault Next backend.

This module owns the profile account payload builders (user details and
recovery-code status). The helpers are extracted from ``next_app.py`` so that
the profile routes can reuse them without importing the oversized application
module. ``next_app.py`` re-imports every name defined here to preserve its
public surface (tests and older modules continue to import the same names from
``next_app``).

Runtime dependencies that still live in ``next_app``
(``media_asset_public_url`` and ``next_user_primary_role``) are resolved lazily
through the application module so that the existing test mocks targeting
``app.backend.next_app`` keep working during the transition.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

try:  # pragma: no cover - exercised indirectly by both layouts
    from .next_common import table_exists
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_common import table_exists


def _next_app():
    """Return the ``next_app`` module, resolving deferred dependencies lazily.

    ``media_asset_public_url`` and ``next_user_primary_role`` still live in
    ``next_app`` (they belong to the media and security domains that have not
    been split yet). Importing them lazily avoids an import cycle.
    """

    try:  # pragma: no cover - import shape depends on runtime layout
        from . import next_app
    except ImportError:  # pragma: no cover - supports gunicorn next_app:app
        import next_app
    return next_app


def next_profile_user_payload(conn, user: dict[str, Any]) -> dict[str, Any]:
    next_app = _next_app()
    user_id = user["id"]
    avatar: dict[str, Any] | None = None
    fresh_user = user
    if table_exists(conn, "users"):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.display_name,
                    u.first_name,
                    u.last_name,
                    u.status,
                    u.avatar_asset_id,
                    u.updated_at,
                    ma.id AS avatar_id,
                    ma.kind AS avatar_kind,
                    ma.variant AS avatar_variant,
                    ma.storage_backend AS avatar_storage_backend,
                    ma.storage_key AS avatar_storage_key,
                    ma.source_url AS avatar_source_url,
                    ma.provider_id AS avatar_provider_id,
                    ma.content_type AS avatar_content_type,
                    ma.width AS avatar_width,
                    ma.height AS avatar_height,
                    ma.size_bytes AS avatar_size_bytes,
                    ma.sha256 AS avatar_sha256,
                    ma.metadata AS avatar_metadata,
                    ma.created_at AS avatar_created_at
                FROM users u
                LEFT JOIN media_assets ma ON ma.id = u.avatar_asset_id
                WHERE u.id=%s
                """,
                (user_id,),
            )
            row = cur.fetchone()
        if row:
            fresh_user = row
            if row.get("avatar_id"):
                avatar = {
                    "id": row.get("avatar_id"),
                    "kind": row.get("avatar_kind"),
                    "variant": row.get("avatar_variant"),
                    "storage_backend": row.get("avatar_storage_backend"),
                    "storage_key": row.get("avatar_storage_key"),
                    "source_url": row.get("avatar_source_url"),
                    "provider_id": row.get("avatar_provider_id"),
                    "content_type": row.get("avatar_content_type"),
                    "width": row.get("avatar_width"),
                    "height": row.get("avatar_height"),
                    "size_bytes": row.get("avatar_size_bytes"),
                    "sha256": row.get("avatar_sha256"),
                    "metadata": row.get("avatar_metadata"),
                    "created_at": row.get("avatar_created_at"),
                }
    avatar_url = next_app.media_asset_public_url(avatar)
    display_name = fresh_user.get("display_name") or fresh_user.get("username")
    return {
        "id": fresh_user.get("id"),
        "username": fresh_user.get("username"),
        "displayName": display_name,
        "display_name": display_name,
        "first_name": fresh_user.get("first_name"),
        "last_name": fresh_user.get("last_name"),
        "role": fresh_user.get("role") or next_app.next_user_primary_role(conn, user_id),
        "avatarAssetId": fresh_user.get("avatar_asset_id"),
        "avatar_asset_id": fresh_user.get("avatar_asset_id"),
        "avatarUrl": avatar_url,
        "avatar_url": avatar_url,
        "updated_at": fresh_user.get("updated_at"),
    }


def next_profile_recovery_payload(conn, user_id: UUID | str) -> dict[str, Any]:
    if not table_exists(conn, "recovery_codes"):
        return {
            "available": False,
            "activeCount": 0,
            "usedCount": 0,
            "lastGeneratedAt": None,
        }
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE used_at IS NULL
                      AND (expires_at IS NULL OR expires_at > now())
                )::int AS active_count,
                COUNT(*) FILTER (WHERE used_at IS NOT NULL)::int AS used_count,
                MAX(created_at) AS last_generated_at
            FROM recovery_codes
            WHERE user_id=%s
            """,
            (user_id,),
        )
        row = cur.fetchone() or {}
    return {
        "available": True,
        "activeCount": int(row.get("active_count") or 0),
        "usedCount": int(row.get("used_count") or 0),
        "lastGeneratedAt": row.get("last_generated_at"),
    }
