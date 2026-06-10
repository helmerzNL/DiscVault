"""MCP activity helpers for the DiscVault Next backend.

These helpers track how API/MCP clients authenticate and which MCP tools are
exposed. They are extracted from ``next_app.py`` so that the profile and audit
domains can reuse them without importing the oversized application module.
``next_app.py`` re-imports every name defined here to preserve its public
surface (tests and older modules continue to import the same names from
``next_app``).
"""

from __future__ import annotations

from typing import Any

from flask import Flask, Response, request
import requests as http_requests

try:  # pragma: no cover - exercised indirectly by both layouts
    from .next_audit import api_audit_metadata, audit_event
    from .next_auth import next_auth_current_api_token_user
    from .next_common import response
    from .next_import import clean_text
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_audit import api_audit_metadata, audit_event
    from next_auth import next_auth_current_api_token_user
    from next_common import response
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


def mcp_request_api_token_value() -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    for header in ("X-DiscVault-Api-Token", "X-API-Key"):
        value = str(request.headers.get(header) or "").strip()
        if value:
            return value
    return ""


def register_next_mcp_routes(flask_app: Flask, *, connect) -> None:  # pragma: no cover - Flask integration
    """Register the MCP proxy routes on *flask_app*.

    ``connect`` must be a callable that returns a database connection context
    manager (i.e. the ``connect`` closure defined in ``next_app.py``).
    """

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
