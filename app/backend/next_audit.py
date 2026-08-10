"""Audit activity helpers for the DiscVault Next backend.

This module owns audit-event persistence, request-IP resolution, API/MCP audit
metadata, and the profile-facing API audit filters. The helpers are extracted
from ``next_app.py`` so that domain modules can reuse them without importing the
oversized application module. ``next_app.py`` re-imports every name defined here
to preserve its public surface (tests and older modules continue to import the
same names from ``next_app``).
"""

from __future__ import annotations

import ipaddress
from typing import Any

from flask import Flask, has_request_context, request
from psycopg.types.json import Jsonb

try:  # pragma: no cover - exercised indirectly by both layouts
    from .next_common import json_ready, parse_int_arg, response, table_exists
    from .next_import import clean_text
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_common import json_ready, parse_int_arg, response, table_exists
    from next_import import clean_text


def _next_app():
    """Return the ``next_app`` module lazily to avoid circular imports.

    ``require_next_permission`` still lives in ``next_app`` (it belongs to the
    security domain that has not been split yet). Importing it lazily keeps
    test mocks targeting ``app.backend.next_app`` effective.
    """
    try:  # pragma: no cover - import shape depends on runtime layout
        from . import next_app
    except ImportError:  # pragma: no cover - supports gunicorn next_app:app
        import next_app
    return next_app


def redact_sensitive_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in {"secret", "secrets", "token", "password", "apikey", "api_key"}:
                if isinstance(item, dict):
                    redacted[key] = {name: "***" for name in item}
                elif isinstance(item, list):
                    redacted[key] = ["***" for _ in item]
                elif item in (None, ""):
                    redacted[key] = item
                else:
                    redacted[key] = "***"
            else:
                redacted[key] = redact_sensitive_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive_payload(item) for item in value]
    return value


def audit_event(
    conn,
    *,
    event_type: str,
    category: str = "system",
    actor: dict[str, Any] | None = None,
    target_type: str | None = None,
    target_id: Any = None,
    summary: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not table_exists(conn, "audit_events"):
        return
    actor = actor or {}
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit_events (
                event_type,
                category,
                actor_user_id,
                actor_username,
                actor_role,
                target_type,
                target_id,
                summary,
                metadata,
                request_ip,
                user_agent
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event_type,
                category,
                actor.get("id"),
                actor.get("username"),
                actor.get("role"),
                target_type,
                str(target_id) if target_id is not None else None,
                summary,
                Jsonb(json_ready(redact_sensitive_payload(metadata or {}))),
                # Outside a request there is no caller to describe, and that is
                # a normal state rather than an error: a worker, a migration or
                # a test writes the same events. Recording the row without the
                # request-derived columns is strictly better than refusing to
                # record it -- an audit trail that raises is one that stops
                # existing exactly when something unusual is happening.
                public_request_ip() if has_request_context() else None,
                request.headers.get("User-Agent") if has_request_context() else None,
            ),
        )


def normalize_request_ip_candidate(value: Any) -> str:
    text = str(value or "").strip().strip('"').strip("'")
    if not text:
        return ""
    if text.lower().startswith("for="):
        text = text[4:].strip().strip('"').strip("'")
    if ";" in text:
        text = text.split(";", 1)[0].strip()
    if text.startswith("[") and "]" in text:
        return text[1:text.index("]")].strip()
    if text.count(":") == 1:
        host, port = text.rsplit(":", 1)
        if port.isdigit():
            text = host
    return text.strip()


def request_ip_details() -> dict[str, Any]:
    candidates: list[dict[str, str]] = []

    def add_candidate(source: str, value: Any) -> None:
        candidate = normalize_request_ip_candidate(value)
        if not candidate:
            return
        try:
            parsed = ipaddress.ip_address(candidate)
        except ValueError:
            return
        normalized = str(parsed)
        if any(item["ip"] == normalized and item["source"] == source for item in candidates):
            return
        candidates.append(
            {
                "ip": normalized,
                "source": source,
                "scope": "public" if parsed.is_global else "private",
            }
        )

    for header in (
        "X-DiscVault-Client-IP",
        "CF-Connecting-IP",
        "True-Client-IP",
        "Fastly-Client-IP",
        "Fly-Client-IP",
        "X-Azure-ClientIP",
        "X-Real-IP",
        "X-Client-IP",
        "X-Cluster-Client-IP",
    ):
        add_candidate(header, request.headers.get(header))
    for header in ("X-Forwarded-For", "X-Original-Forwarded-For"):
        forwarded_for = request.headers.get(header)
        if not forwarded_for:
            continue
        for index, part in enumerate(str(forwarded_for).split(",")):
            add_candidate(f"{header}[{index}]", part)
    forwarded = request.headers.get("Forwarded")
    if forwarded:
        for segment_index, segment in enumerate(str(forwarded).split(",")):
            for part in segment.split(";"):
                part = part.strip()
                if part.lower().startswith("for="):
                    add_candidate(f"Forwarded[{segment_index}]", part)
    add_candidate("remote_addr", request.remote_addr)

    selected = ""
    selected_source = ""
    for item in candidates:
        if item["scope"] == "public":
            selected = item["ip"]
            selected_source = item["source"]
            break
    return {
        "ip": selected,
        "source": selected_source,
        "candidates": candidates,
    }


def public_request_ip() -> str:
    return str(request_ip_details().get("ip") or "")


def request_ip_audit_metadata() -> dict[str, Any]:
    details = request_ip_details()
    metadata: dict[str, Any] = {
        "requestIp": details.get("ip") or "",
        "requestIpSource": details.get("source") or "",
    }
    candidates = details.get("candidates")
    if candidates:
        metadata["requestIpCandidates"] = candidates
    return metadata


def audit_event_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "eventType": row["event_type"],
        "category": row["category"],
        "actorUserId": row.get("actor_user_id"),
        "actorUsername": row.get("actor_username"),
        "actorRole": row.get("actor_role"),
        "targetType": row.get("target_type"),
        "targetId": row.get("target_id"),
        "summary": row.get("summary"),
        "metadata": redact_sensitive_payload(row.get("metadata") or {}),
        "requestIp": row.get("request_ip"),
        "userAgent": row.get("user_agent"),
        "createdAt": row.get("created_at"),
    }


def api_request_query_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in request.args.keys():
        values = request.args.getlist(key)
        payload[key] = values if len(values) > 1 else (values[0] if values else "")
    return payload


def api_audit_metadata(
    actor: dict[str, Any] | None,
    *,
    command: str,
    request_payload: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actor = actor or {}
    api_token = actor.get("apiToken") or {}
    metadata: dict[str, Any] = {
        "command": command,
        "method": request.method,
        "endpoint": request.path,
        "query": api_request_query_payload(),
        "agent": request.headers.get("X-DiscVault-Agent")
        or request.headers.get("X-MCP-Client")
        or request.headers.get("User-Agent"),
        "tool": request.headers.get("X-DiscVault-MCP-Tool") or command,
        "apiTokenId": api_token.get("id"),
        "apiTokenName": api_token.get("name"),
        "apiTokenScopes": api_token.get("scopes") or [],
        "apiTokenPermissions": api_token.get("permissionKeys") or [],
    }
    metadata.update(request_ip_audit_metadata())
    if request_payload is not None:
        metadata["request"] = request_payload
    if extra:
        metadata.update(extra)
    return metadata


PROFILE_API_AUDIT_CATEGORIES = {"all", "api", "mcp", "security"}


def normalize_profile_api_audit_category(value: Any) -> str:
    category = (clean_text(value) or "").lower()
    return category if category in PROFILE_API_AUDIT_CATEGORIES else "all"


def profile_api_audit_search_term(value: Any) -> str:
    return (clean_text(value) or "")[:120]


def profile_api_audit_category_condition(category: str) -> tuple[str, list[Any]]:
    category = normalize_profile_api_audit_category(category)
    if category == "api":
        return "(category = %s AND event_type LIKE 'api.%%' AND event_type NOT LIKE 'api.mcp_%%')", ["api"]
    if category == "mcp":
        return """
            (
                (category = %s AND event_type LIKE 'mcp.%%')
                OR event_type LIKE 'api.mcp_%%'
            )
            """, ["mcp"]
    if category == "security":
        return "(event_type IN ('api_token.created', 'api_token.revoked'))", []
    return (
        """
        (
            (
                category IN ('api', 'mcp')
                AND (
                    event_type LIKE 'api.%%'
                    OR event_type LIKE 'mcp.%%'
                )
            )
            OR event_type IN ('api_token.created', 'api_token.revoked')
        )
        """,
        [],
    )


def profile_api_audit_token_match_condition(
    token_ids: list[str],
    token_names: list[str] | None = None,
) -> tuple[str, list[Any]]:
    token_ids = [str(token_id) for token_id in token_ids if token_id]
    token_names = [str(name) for name in (token_names or []) if name]
    if not token_ids:
        return "(false)", []
    token_placeholders = ", ".join(["%s"] * len(token_ids))
    conditions = [
        f"metadata->>'apiTokenId' IN ({token_placeholders})",
        f"metadata->>'api_token_id' IN ({token_placeholders})",
        f"metadata->>'tokenId' IN ({token_placeholders})",
        f"metadata->>'accessTokenId' IN ({token_placeholders})",
        f"(target_type = 'api_access_token' AND target_id IN ({token_placeholders}))",
    ]
    params: list[Any] = [
        *token_ids,
        *token_ids,
        *token_ids,
        *token_ids,
        *token_ids,
    ]
    if token_names:
        name_placeholders = ", ".join(["%s"] * len(token_names))
        conditions.append(f"metadata->>'apiTokenName' IN ({name_placeholders})")
        params.extend(token_names)
    return f"({' OR '.join(conditions)})", params


def audit_api_interaction(
    conn,
    actor: dict[str, Any],
    *,
    command: str,
    event_type: str,
    target_type: str | None = None,
    target_id: Any = None,
    summary: str | None = None,
    request_payload: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    audit_event(
        conn,
        event_type=event_type,
        category="api",
        actor=actor,
        target_type=target_type,
        target_id=target_id,
        summary=summary,
        metadata=api_audit_metadata(actor, command=command, request_payload=request_payload, extra=metadata),
    )


def audit_event_counts(conn) -> dict[str, Any]:
    if not table_exists(conn, "audit_events"):
        return {"total": 0, "byCategory": {}}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT category, COUNT(*)::int AS count
            FROM audit_events
            GROUP BY category
            """
        )
        rows = cur.fetchall()
    by_category = {str(row.get("category") or "system"): int(row.get("count") or 0) for row in rows}
    return {"total": sum(by_category.values()), "byCategory": by_category}


def register_next_audit_routes(flask_app: Flask, *, connect) -> None:  # pragma: no cover - Flask integration
    """Register the admin audit-events route on *flask_app*.

    ``connect`` must be a callable that returns a database connection context
    manager (i.e. the ``connect`` closure defined in ``next_app.py``).
    """
    from flask import request as _request  # already imported at module level; alias for clarity

    @flask_app.get("/api/next/audit/events")
    def audit_events():
        limit = parse_int_arg("limit", 100, minimum=1, maximum=250)
        category = clean_text(_request.args.get("category") or "")
        with connect() as conn:
            _next_app().require_next_permission(conn, "admin.view_audit")
            if not table_exists(conn, "audit_events"):
                return response({"status": "ok", "events": [], "counts": {"total": 0, "byCategory": {}}})
            counts = audit_event_counts(conn)
            clauses: list[str] = []
            params: list[Any] = []
            if category:
                clauses.append("category=%s")
                params.append(category)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        id,
                        event_type,
                        category,
                        actor_user_id,
                        actor_username,
                        actor_role,
                        target_type,
                        target_id,
                        summary,
                        metadata,
                        request_ip,
                        user_agent,
                        created_at
                    FROM audit_events
                    {where}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (*params, limit),
                )
                rows = cur.fetchall()
        return response({"status": "ok", "events": [audit_event_row(row) for row in rows], "counts": counts})
