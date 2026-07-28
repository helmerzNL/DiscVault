"""Paged library data routes for the DiscVault Next backend.

The Library used to receive a single, hard-capped snapshot of 200 movies. This
module exposes a paged endpoint so the frontend can hydrate the full library in
background chunks instead, which is a prerequisite for exporting "all movies".
"""

from __future__ import annotations

from typing import Any

from flask import Flask, request

try:  # pragma: no cover - exercised indirectly by both layouts
    from .next_common import NextApiError, response, table_exists
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_common import NextApiError, response, table_exists


DEFAULT_PAGE_SIZE = 200
MAX_PAGE_SIZE = 500


def _next_app():
    try:  # pragma: no cover - import shape depends on runtime layout
        from . import next_app
    except ImportError:  # pragma: no cover - supports gunicorn next_app:app
        import next_app
    return next_app


def parse_page_params(args: Any) -> tuple[int, int]:
    """Return a sanitised ``(limit, offset)`` pair from query arguments."""
    raw_limit = (args.get("limit") or "").strip()
    raw_offset = (args.get("offset") or "").strip()
    try:
        limit = int(raw_limit) if raw_limit else DEFAULT_PAGE_SIZE
    except ValueError:
        raise NextApiError("limit must be an integer", 400) from None
    try:
        offset = int(raw_offset) if raw_offset else 0
    except ValueError:
        raise NextApiError("offset must be an integer", 400) from None
    if limit < 1:
        raise NextApiError("limit must be greater than zero", 400)
    if offset < 0:
        raise NextApiError("offset must not be negative", 400)
    return min(limit, MAX_PAGE_SIZE), offset


def library_movie_page(conn, *, user: dict[str, Any] | None, limit: int, offset: int) -> dict[str, Any]:
    """Build one page of library movies plus the paging metadata."""
    app = _next_app()
    total = app.collection_movie_total_count(conn, actor=user)
    items = app.collection_movie_preview_entities(conn, limit=limit, offset=offset, actor=user)
    items = app.attach_personal_list_state(conn, items, user.get("id") if user else None)
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "hasMore": offset + len(items) < total,
    }


def register_next_library_data_routes(flask_app: Flask, *, connect) -> None:  # pragma: no cover - Flask integration
    @flask_app.get("/api/next/collection/movies")
    def collection_movies_page():
        limit, offset = parse_page_params(request.args)
        app = _next_app()
        with connect() as conn:
            auth_enabled = app.next_auth_effective_enabled(conn, table_exists)
            user = app.next_auth_current_user(conn) if auth_enabled else None
            if auth_enabled and not user:
                return response({"status": "ok", "items": [], "total": 0, "limit": limit, "offset": offset, "hasMore": False})
            if user:
                user["role"] = app.next_user_primary_role(conn, user["id"])
                user["permissions"] = sorted(app.next_user_permission_keys(conn, user["id"]))
            page = library_movie_page(conn, user=user, limit=limit, offset=offset)
        return response({"status": "ok", **page})
