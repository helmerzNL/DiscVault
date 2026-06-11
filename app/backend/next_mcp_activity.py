"""MCP activity helpers for the DiscVault Next backend.

These helpers track how API/MCP clients authenticate and which MCP tools are
exposed. They are extracted from ``next_app.py`` so that the profile and audit
domains can reuse them without importing the oversized application module.
``next_app.py`` re-imports every name defined here to preserve its public
surface (tests and older modules continue to import the same names from
``next_app``).
"""

from __future__ import annotations

import re
from typing import Any

from flask import Flask, Response, request
import requests as http_requests

try:  # pragma: no cover - exercised indirectly by both layouts
    from .next_audit import api_audit_metadata, audit_event, redact_sensitive_payload
    from .next_auth import next_auth_current_api_token_user
    from .next_common import parse_int_arg, response, table_exists
    from .next_import import clean_text
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_audit import api_audit_metadata, audit_event, redact_sensitive_payload
    from next_auth import next_auth_current_api_token_user
    from next_common import parse_int_arg, response, table_exists
    from next_import import clean_text


MCP_TOOL_NAMES = (
    "search_collection",
    "get_collection_stats",
    "get_movie_details",
    "add_movie",
    "delete_movie",
    "lookup_barcode",
    "list_all_movies",
    "get_watchlist",
    "get_watch_history",
    "get_groups",
)
MCP_IOS_CLIENT_PATTERNS = ("ios", "discvault-ios", "discvault/ios")
MCP_LOG_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "api-key",
    "apikey",
    "api_key",
    "access-token",
    "access_token",
    "refreshtoken",
    "refresh-token",
    "refresh_token",
    "session",
    "sessionid",
    "session-id",
    "session_id",
    "private-key",
    "private_key",
    "secret",
    "token",
    "password",
}
MCP_LOG_SENSITIVE_REPLACEMENTS = (
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED]"),
    (
        re.compile(
            r"(?i)\b(authorization|cookie|api[_-]?key|access[_-]?token|refresh[_-]?token|session[_-]?id|private[_-]?key)\s*[:=]\s*[^,\s;}]+"
        ),
        r"\1=[REDACTED]",
    ),
)


def _next_app():
    """Return ``next_app`` lazily to keep MCP helpers importable in tests."""
    try:  # pragma: no cover - import shape depends on runtime layout
        from . import next_app
    except ImportError:  # pragma: no cover - supports gunicorn next_app:app
        import next_app
    return next_app


def mcp_request_api_token_value() -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    for header in ("X-DiscVault-Api-Token", "X-API-Key"):
        value = str(request.headers.get(header) or "").strip()
        if value:
            return value
    return ""


def mcp_log_text(value: Any, *, maximum: int = 500) -> str:
    text = clean_text(value)
    if not text:
        return ""
    for pattern, replacement in MCP_LOG_SENSITIVE_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text[:maximum]


def mcp_redact_metadata_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in MCP_LOG_SENSITIVE_KEYS:
                redacted[str(key)] = "[REDACTED]" if item not in (None, "") else item
            else:
                redacted[str(key)] = mcp_redact_metadata_value(item)
        return redacted
    if isinstance(value, list):
        return [mcp_redact_metadata_value(item) for item in value]
    if isinstance(value, str):
        return mcp_log_text(value, maximum=2000)
    return value


def mcp_log_first(metadata: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, list):
            for item in value:
                text = mcp_log_text(item, maximum=160)
                if text:
                    return text
            continue
        text = mcp_log_text(value, maximum=160)
        if text:
            return text
    return ""


def mcp_log_level(row: dict[str, Any], metadata: dict[str, Any]) -> str:
    event_type = mcp_log_text(row.get("event_type"), maximum=120).lower()
    if "failed" in event_type or "error" in event_type:
        return "error"
    try:
        response_status = int(metadata.get("responseStatus") or metadata.get("status") or 0)
    except (TypeError, ValueError):
        response_status = 0
    if response_status >= 500:
        return "error"
    if response_status >= 400:
        return "warning"
    return "info"


def mcp_log_status_code(metadata: dict[str, Any]) -> int | None:
    try:
        status_code = int(metadata.get("responseStatus") or metadata.get("status") or 0)
    except (TypeError, ValueError):
        return None
    return status_code or None


def mcp_log_event(row: dict[str, Any], metadata: dict[str, Any]) -> str:
    explicit = mcp_log_first(metadata, "event", "eventName", "mcpEvent")
    if explicit:
        return explicit
    event_type = mcp_log_text(row.get("event_type"), maximum=120).lower()
    if "failed" in event_type or "error" in event_type:
        return "tool_call.failed"
    if event_type == "mcp.catalog_read":
        return "catalog.read"
    if event_type.startswith("mcp.request"):
        status_code = mcp_log_status_code(metadata)
        return "tool_call.failed" if status_code and status_code >= 400 else "tool_call.completed"
    if event_type:
        return event_type.replace("mcp.", "mcp.")
    return "tool_call.completed"


def mcp_safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    request_id = mcp_log_first(metadata, "request_id", "requestId", "correlationId", "traceId")
    if request_id:
        safe["request_id"] = request_id
    if metadata.get("mcpSessionId") or metadata.get("session_id") or metadata.get("sessionId"):
        safe["session_id"] = "[REDACTED]"
    duration_ms = metadata.get("duration_ms") or metadata.get("durationMs") or metadata.get("elapsed_ms")
    if duration_ms is not None:
        safe["duration_ms"] = duration_ms
    status_code = mcp_log_status_code(metadata)
    if status_code is not None:
        safe["status_code"] = status_code
    transport = mcp_log_first(metadata, "transport", "protocol")
    safe["transport"] = transport or "http"
    if "request" in metadata:
        safe["input"] = mcp_redact_metadata_value(redact_sensitive_payload(metadata.get("request")))
    result = metadata.get("result") or metadata.get("response")
    if result is not None:
        safe["result"] = mcp_redact_metadata_value(redact_sensitive_payload(result))
    error = metadata.get("proxyError") or metadata.get("error")
    if error:
        safe["error"] = mcp_log_text(error, maximum=500)
    server = metadata.get("server") or metadata.get("serverInfo")
    if server is not None:
        safe["server"] = mcp_redact_metadata_value(redact_sensitive_payload(server))
    command = mcp_log_first(metadata, "command")
    if command:
        safe["command"] = command
    methods = metadata.get("mcpMethods")
    if isinstance(methods, list) and methods:
        safe["methods"] = [mcp_log_text(item, maximum=160) for item in methods if mcp_log_text(item, maximum=160)]
    tools = metadata.get("mcpTools")
    if isinstance(tools, list) and tools:
        safe["tools"] = [mcp_log_text(item, maximum=160) for item in tools if mcp_log_text(item, maximum=160)]
    endpoint = mcp_log_first(metadata, "endpoint", "mcpPath", "path")
    if endpoint:
        safe["endpoint"] = endpoint
    query = metadata.get("query")
    if isinstance(query, dict) and query:
        safe["query"] = mcp_redact_metadata_value(redact_sensitive_payload(query))
    api_token_name = mcp_log_first(metadata, "apiTokenName")
    if api_token_name:
        safe["api_token_name"] = api_token_name
    api_token_scopes = metadata.get("apiTokenScopes")
    if isinstance(api_token_scopes, list) and api_token_scopes:
        safe["api_token_scopes"] = [mcp_log_text(item, maximum=80) for item in api_token_scopes if mcp_log_text(item, maximum=80)]
    api_token_permissions = metadata.get("apiTokenPermissions")
    if isinstance(api_token_permissions, list) and api_token_permissions:
        safe["api_token_permissions"] = [
            mcp_log_text(item, maximum=120) for item in api_token_permissions if mcp_log_text(item, maximum=120)
        ]
    return mcp_redact_metadata_value(redact_sensitive_payload(safe))


def mcp_activity_log_entry(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    tool_name = mcp_log_first(metadata, "tool_name", "toolName", "mcpTools", "tool", "command")
    method = mcp_log_first(metadata, "method", "mcpMethods")
    path = mcp_log_first(metadata, "mcpPath", "path", "endpoint")
    user_agent = mcp_log_text(row.get("user_agent") or metadata.get("agent"), maximum=240)
    client = mcp_log_first(metadata, "client", "mcpClient", "apiTokenName", "agent")
    agent = mcp_log_first(metadata, "agent", "mcpClient", "client", "apiTokenName") or user_agent or client
    return {
        "id": str(row.get("id") or ""),
        "timestamp": row.get("created_at"),
        "level": mcp_log_level(row, metadata),
        "event": mcp_log_event(row, metadata),
        "message": mcp_log_text(row.get("summary") or metadata.get("message") or "MCP activity", maximum=500),
        "source": mcp_log_text(metadata.get("source") or row.get("category") or "mcp", maximum=80) or "mcp",
        "client": client,
        "agent": agent,
        "user_agent": user_agent,
        "ip_address": mcp_log_text(row.get("request_ip") or metadata.get("requestIp") or "", maximum=80),
        "tool_name": tool_name,
        "method": method,
        "path": path,
        "metadata": mcp_safe_metadata(metadata),
    }


def mcp_activity_log_is_ios(entry: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(entry.get(key) or "").lower()
        for key in ("source", "client", "user_agent")
    )
    return any(pattern in haystack for pattern in MCP_IOS_CLIENT_PATTERNS)


def register_next_mcp_routes(flask_app: Flask, *, connect) -> None:  # pragma: no cover - Flask integration
    """Register the MCP proxy routes on *flask_app*.

    ``connect`` must be a callable that returns a database connection context
    manager (i.e. the ``connect`` closure defined in ``next_app.py``).
    """

    @flask_app.get("/api/next/mcp/logs")
    def next_mcp_logs():
        limit = parse_int_arg("limit", 50, minimum=1, maximum=100)
        query_limit = min(limit * 3, 300)
        with connect() as conn:
            _next_app().require_any_next_permission(conn, ("mcp.logs.view", "admin.view_audit"))
            if not table_exists(conn, "audit_events"):
                return response({"logs": []})
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        event_type,
                        category,
                        summary,
                        metadata,
                        request_ip,
                        user_agent,
                        created_at
                    FROM audit_events
                    WHERE category = 'mcp'
                       OR event_type LIKE 'mcp.%%'
                       OR event_type LIKE 'api.mcp_%%'
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (query_limit,),
                )
                rows = cur.fetchall()
        logs: list[dict[str, Any]] = []
        for row in rows:
            entry = mcp_activity_log_entry(row)
            if mcp_activity_log_is_ios(entry):
                continue
            logs.append(entry)
            if len(logs) >= limit:
                break
        return response({"logs": logs})

    def mcp_proxy_request(path: str = "/mcp"):
        target = f"http://127.0.0.1:6090{path}"
        body_bytes = request.get_data()
        body_json = request.get_json(silent=True) if body_bytes else None
        bearer_actor: dict[str, Any] | None = None
        api_token = mcp_request_api_token_value()
        if api_token:
            with connect() as conn:
                bearer_actor = next_auth_current_api_token_user(conn, api_token)
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower()
            in {
                "accept",
                "authorization",
                "content-type",
                "mcp-session-id",
            }
        }
        try:
            proxied = http_requests.request(
                request.method,
                target,
                data=body_bytes,
                headers=headers,
                timeout=60,
            )
        except http_requests.RequestException as exc:
            if bearer_actor:
                with connect() as conn:
                    audit_event(
                        conn,
                        event_type="mcp.request_failed",
                        category="mcp",
                        actor=bearer_actor,
                        target_type="api_access_token",
                        target_id=(bearer_actor.get("apiToken") or {}).get("id"),
                        summary=f"MCP proxy failed: {exc}",
                        metadata=api_audit_metadata(
                            bearer_actor,
                            command=f"mcp.{request.method.lower()}",
                            request_payload=body_json if isinstance(body_json, (dict, list)) else None,
                            extra={
                                "category": "mcp",
                                "mcpPath": path,
                                "proxyError": str(exc),
                            },
                        ),
                    )
            return response(
                {
                    "status": "error",
                    "error": "MCP server is not reachable",
                    "detail": str(exc),
                },
                503,
            )
        if bearer_actor:
            messages = body_json if isinstance(body_json, list) else ([body_json] if isinstance(body_json, dict) else [])
            methods: list[str] = []
            tools: list[str] = []
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                method = clean_text(msg.get("method"))
                if method:
                    methods.append(method)
                params = msg.get("params")
                if isinstance(params, dict):
                    tool_name = clean_text(params.get("name"))
                    if tool_name:
                        tools.append(tool_name)
            command = tools[0] if tools else (methods[0] if methods else f"mcp.{request.method.lower()}")
            metadata = api_audit_metadata(
                bearer_actor,
                command=command,
                request_payload=body_json if isinstance(body_json, (dict, list)) else None,
                extra={
                    "category": "mcp",
                    "mcpPath": path,
                    "mcpMethods": methods,
                    "mcpTools": tools,
                    "mcpSessionId": request.headers.get("Mcp-Session-Id") or proxied.headers.get("Mcp-Session-Id"),
                    "responseStatus": proxied.status_code,
                    "responseContentType": proxied.headers.get("Content-Type"),
                },
            )
            with connect() as conn:
                audit_event(
                    conn,
                    event_type="mcp.request",
                    category="mcp",
                    actor=bearer_actor,
                    target_type="api_access_token",
                    target_id=(bearer_actor.get("apiToken") or {}).get("id"),
                    summary=f"MCP {command} -> {proxied.status_code}",
                    metadata=metadata,
                )
        response_headers = {}
        # Only echo known-safe MCP protocol content types to prevent reflective XSS.
        _SAFE_MCP_CONTENT_TYPES = ("application/json", "text/event-stream")
        content_type = proxied.headers.get("Content-Type", "")
        if any(safe in content_type for safe in _SAFE_MCP_CONTENT_TYPES):
            response_headers["Content-Type"] = content_type
        else:
            response_headers["Content-Type"] = "application/json"
        mcp_session_id = proxied.headers.get("Mcp-Session-Id")
        if mcp_session_id:
            response_headers["Mcp-Session-Id"] = mcp_session_id
        return Response(proxied.content, status=proxied.status_code, headers=response_headers)

    @flask_app.route("/mcp", methods=["GET", "POST", "DELETE"])
    def mcp_proxy():
        return mcp_proxy_request("/mcp")

    @flask_app.route("/mcp/", methods=["GET", "POST", "DELETE"])
    def mcp_proxy_trailing_slash():
        return mcp_proxy_request("/mcp")

    @flask_app.route("/mcp/<path:subpath>", methods=["GET", "POST", "DELETE"])
    def mcp_proxy_subpath(subpath: str):
        cleaned = clean_text(subpath).strip("/")
        return mcp_proxy_request(f"/mcp/{cleaned}" if cleaned else "/mcp")

    @flask_app.get("/mcp-health")
    def mcp_health_proxy():
        return mcp_proxy_request("/health")
