"""Discover routes for TMDb-based feed and details."""

from __future__ import annotations

from typing import Any

from flask import Flask, request

try:  # pragma: no cover - exercised in both package layouts
    from .next_common import parse_int_arg, response
    from .next_tmdb_discover import discover_detail, discover_feed, normalize_locale, tmdb_discover_context
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_common import parse_int_arg, response
    from next_tmdb_discover import discover_detail, discover_feed, normalize_locale, tmdb_discover_context


def _next_app():
    try:  # pragma: no cover - import shape depends on runtime layout
        from . import next_app
    except ImportError:  # pragma: no cover - supports gunicorn next_app:app
        import next_app
    return next_app


def _require_discover_actor(conn) -> dict[str, Any]:
    return _next_app().require_any_next_permission(
        conn,
        ("collection.view", "collection.view_own", "collection.view_group", "collection.view_all"),
    )


def _missing_config_payload(message: str) -> dict[str, Any]:
    return {
        "status": "ok",
        "configured": False,
        "message": message,
        "items": [],
        "page": 1,
        "totalPages": 1,
        "hasMore": False,
        "supportedKinds": ["movie", "tv"],
        "supportedModes": ["popular", "trending"],
    }


def register_next_discover_routes(flask_app: Flask, *, connect) -> None:  # pragma: no cover - Flask wiring
    @flask_app.get("/api/next/discover")
    def next_discover_feed():
        kind = str(request.args.get("kind") or "movie").strip().lower()
        mode = str(request.args.get("mode") or "popular").strip().lower()
        page = parse_int_arg(request.args.get("page"), default=1, minimum=1)
        locale = normalize_locale(request.args.get("locale"))
        with connect() as conn:
            actor = _require_discover_actor(conn)
            context_state = tmdb_discover_context(conn, actor=actor, locale=locale)
            if not context_state.get("configured"):
                return response(_missing_config_payload(context_state.get("message") or "TMDb is not configured."))
            payload = discover_feed(context_state["context"], media_type=kind, mode=mode, page=page)
        return response(
            {
                "status": "ok",
                "configured": True,
                "supportedKinds": ["movie", "tv"],
                "supportedModes": ["popular", "trending"],
                **payload,
            }
        )

    @flask_app.get("/api/next/discover/<media_type>/<tmdb_id>")
    def next_discover_detail(media_type: str, tmdb_id: str):
        locale = normalize_locale(request.args.get("locale"))
        with connect() as conn:
            actor = _require_discover_actor(conn)
            context_state = tmdb_discover_context(conn, actor=actor, locale=locale)
            if not context_state.get("configured"):
                return response(
                    {
                        "status": "ok",
                        "configured": False,
                        "message": context_state.get("message") or "TMDb is not configured.",
                        "detail": None,
                    }
                )
            detail = discover_detail(context_state["context"], media_type=media_type, tmdb_id=tmdb_id)
        return response({"status": "ok", "configured": True, "detail": detail})
