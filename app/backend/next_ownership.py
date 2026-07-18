"""Shared ownership helpers for resources created by users and system jobs."""

from __future__ import annotations

from typing import Any


def instance_owner_id(conn) -> Any | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.id
            FROM users u
            JOIN user_roles ur ON ur.user_id = u.id
            JOIN roles r ON r.id = ur.role_id
            WHERE r.key = 'owner'
            ORDER BY
                CASE WHEN u.status = 'active' THEN 0 ELSE 1 END,
                u.created_at ASC,
                u.id ASC
            LIMIT 1
            """
        )
        row = cur.fetchone()
    return row.get("id") if row else None


def actor_or_instance_owner_id(conn, actor: dict[str, Any] | None = None) -> Any | None:
    actor_id = actor.get("id") if isinstance(actor, dict) else None
    return actor_id or instance_owner_id(conn)
