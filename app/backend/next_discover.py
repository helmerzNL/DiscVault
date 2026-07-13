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


def _discover_people(detail: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("directorPeople", "castPeople"):
        values = detail.get(key) if isinstance(detail.get(key), list) else []
        for row in values:
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _attach_local_person_ids(conn, actor: dict[str, Any], detail: dict[str, Any]) -> None:
    rows = _discover_people(detail)
    if not rows:
        return
    _app = _next_app()
    if not _app.table_exists(conn, "people"):
        return
    by_tmdb: dict[str, str] = {}
    if _app.table_exists(conn, "person_identifiers"):
        tmdb_ids = sorted({str(row.get("tmdbId") or "").strip() for row in rows if str(row.get("tmdbId") or "").strip()})
        if tmdb_ids:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT pi.identifier, pi.person_id
                    FROM person_identifiers pi
                    JOIN people p ON p.id=pi.person_id
                    WHERE lower(pi.provider_id)='tmdb'
                      AND pi.identifier = ANY(%s)
                    ORDER BY p.name
                    """,
                    (tmdb_ids,),
                )
                for row in cur.fetchall():
                    person_id = row.get("person_id")
                    if not person_id or not _app.actor_can_view_person(conn, actor, person_id):
                        continue
                    tmdb_id = str(row.get("identifier") or "").strip()
                    if tmdb_id and tmdb_id not in by_tmdb:
                        by_tmdb[tmdb_id] = str(person_id)
    unresolved_names = sorted(
        {
            str(row.get("name") or "").strip()
            for row in rows
            if str(row.get("name") or "").strip()
            and str(row.get("tmdbId") or "").strip() not in by_tmdb
        }
    )
    by_name: dict[str, str] = {}
    for name in unresolved_names:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id
                FROM people p
                WHERE lower(p.name)=lower(%s)
                ORDER BY p.created_at DESC, p.id
                LIMIT 5
                """,
                (name,),
            )
            for person_row in cur.fetchall():
                person_id = person_row.get("id")
                if person_id and _app.actor_can_view_person(conn, actor, person_id):
                    by_name[name.lower()] = str(person_id)
                    break
    for key in ("directorPeople", "castPeople"):
        values = detail.get(key) if isinstance(detail.get(key), list) else []
        for row in values:
            if not isinstance(row, dict):
                continue
            local_person_id = ""
            tmdb_id = str(row.get("tmdbId") or "").strip()
            if tmdb_id:
                local_person_id = by_tmdb.get(tmdb_id, "")
            if not local_person_id:
                name = str(row.get("name") or "").strip().lower()
                if name:
                    local_person_id = by_name.get(name, "")
            row["localPersonId"] = local_person_id


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
            _attach_local_person_ids(conn, actor, detail)
        return response({"status": "ok", "configured": True, "detail": detail})
