"""Notification preference, delivery, and inbox helpers for DiscVault Next backend."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from flask import Flask, request
from psycopg.types.json import Jsonb

try:  # pragma: no cover - exercised indirectly by both layouts
    from .next_common import NextApiError, json_ready, parse_bool_value, parse_uuid, response, table_exists
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_common import NextApiError, json_ready, parse_bool_value, parse_uuid, response, table_exists


NOTIFICATION_PREF_DEFAULTS: dict[str, bool] = {
    "app_updates": True,
    "imports": True,
    "metadata_jobs": True,
    "group_invites": True,
    "security": True,
    "price_alerts": True,
    # A moderation verdict arrives days after the person stopped looking, so
    # this is the one contribution event worth pushing rather than leaving to
    # be found.
    "contributions": True,
}


def _next_app():
    try:  # pragma: no cover - import shape depends on runtime layout
        from . import next_app
    except ImportError:  # pragma: no cover - supports gunicorn next_app:app
        import next_app
    return next_app


def notification_preference_map(conn, user_id: UUID | str | None) -> dict[str, bool]:
    values = dict(NOTIFICATION_PREF_DEFAULTS)
    if not user_id or not table_exists(conn, "notification_preferences"):
        return values
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pref_key, enabled
            FROM notification_preferences
            WHERE user_id=%s
            """,
            (user_id,),
        )
        for row in cur.fetchall():
            if row["pref_key"] in values:
                values[row["pref_key"]] = bool(row["enabled"])
    return values


def notification_counts(conn, user_id: UUID | str | None) -> dict[str, int]:
    if not user_id or not table_exists(conn, "user_notifications"):
        return {"total": 0, "unread": 0}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                COUNT(*)::int AS total,
                COUNT(*) FILTER (WHERE read_at IS NULL)::int AS unread
            FROM user_notifications
            WHERE user_id=%s
            """,
            (user_id,),
        )
        row = cur.fetchone() or {}
    return {"total": int(row.get("total") or 0), "unread": int(row.get("unread") or 0)}


def user_notification_rows(conn, user_id: UUID | str, limit: int = 100) -> list[dict[str, Any]]:
    if not table_exists(conn, "user_notifications"):
        return []
    limit = min(max(int(limit or 100), 1), 250)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, event_id, title, body, payload, read_at, created_at
            FROM user_notifications
            WHERE user_id=%s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (user_id, limit),
        )
        return cur.fetchall()


def create_user_notification(
    conn,
    user_id: UUID | str,
    *,
    title: str,
    body: str = "",
    url: str = "/notifications",
    pref_key: str = "app_updates",
    payload: dict[str, Any] | None = None,
    send_push: bool = True,
) -> dict[str, Any]:
    if not table_exists(conn, "user_notifications"):
        raise NextApiError("Notifications table is not available", 503)
    notification_payload = dict(payload or {})
    notification_payload.setdefault("url", url)
    notification_payload.setdefault("prefKey", pref_key)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_notifications (user_id, title, body, payload)
            VALUES (%s, %s, %s, %s)
            RETURNING id, event_id, title, body, payload, read_at, created_at
            """,
            (user_id, title, body, Jsonb(json_ready(notification_payload))),
        )
        notification = cur.fetchone() or {}
    errors: list[str] = []
    native_delivery: dict[str, Any] = {"status": "skipped", "attempted": 0, "sent": 0, "failed": 0, "errors": []}
    if send_push and notification_preference_map(conn, user_id).get(pref_key, True):
        _app = _next_app()
        errors = _app.send_push_to_user(conn, user_id, title=title, body=body, url=url)
        native_delivery = _app.send_native_push_to_user(
            conn,
            user_id,
            title=title,
            body=body,
            topic=pref_key,
            url=url,
            notification_id=notification.get("id"),
        )
    notification["pushErrors"] = errors
    notification["nativePushDelivery"] = native_delivery
    return notification


def register_next_notifications_routes(flask_app: Flask, *, connect) -> None:  # pragma: no cover - Flask integration
    @flask_app.get("/api/next/notifications")
    def next_notifications():
        limit = min(max(int(request.args.get("limit", 100)), 1), 250)
        with connect() as conn:
            actor = _next_app().require_next_authenticated_user(conn)
            user_id = actor.get("id")
            if not user_id:
                raise NextApiError("Notifications require a signed-in user", 401)
            return response(
                {
                    "status": "ok",
                    "notifications": user_notification_rows(conn, user_id, limit=limit),
                    "counts": notification_counts(conn, user_id),
                }
            )

    @flask_app.patch("/api/next/notifications/<notification_id>")
    def patch_next_notification(notification_id: str):
        notification_uuid = parse_uuid(notification_id, "notificationId")
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise NextApiError("Notification request body must be an object", 400)
        mark_read = parse_bool_value(body.get("read", True), default=True)
        with connect() as conn:
            actor = _next_app().require_next_authenticated_user(conn)
            user_id = actor.get("id")
            if not user_id:
                raise NextApiError("Notifications require a signed-in user", 401)
            if not table_exists(conn, "user_notifications"):
                raise NextApiError("Notifications table is not available", 503)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE user_notifications
                        SET read_at = CASE WHEN %s THEN COALESCE(read_at, now()) ELSE NULL END
                        WHERE id=%s AND user_id=%s
                        RETURNING id, event_id, title, body, payload, read_at, created_at
                        """,
                        (mark_read, notification_uuid, user_id),
                    )
                    notification = cur.fetchone()
            if not notification:
                raise NextApiError("Notification not found", 404)
            return response({"status": "ok", "notification": notification, "counts": notification_counts(conn, user_id)})

    @flask_app.post("/api/next/notifications/read-all")
    def mark_all_next_notifications_read():
        with connect() as conn:
            actor = _next_app().require_next_authenticated_user(conn)
            user_id = actor.get("id")
            if not user_id:
                raise NextApiError("Notifications require a signed-in user", 401)
            if not table_exists(conn, "user_notifications"):
                raise NextApiError("Notifications table is not available", 503)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE user_notifications
                        SET read_at=COALESCE(read_at, now())
                        WHERE user_id=%s AND read_at IS NULL
                        """,
                        (user_id,),
                    )
            return response(
                {
                    "status": "ok",
                    "notifications": user_notification_rows(conn, user_id, limit=100),
                    "counts": notification_counts(conn, user_id),
                }
            )
