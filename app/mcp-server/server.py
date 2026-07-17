#!/usr/bin/env python3
"""
DiscVault MCP Server.

Transports:
  stdio:             python server.py
  Streamable HTTP:   python server.py --http --port 6090

HTTP authentication:
  Authorization: Bearer <DiscVault API token>

API tokens are created in DiscVault Profile -> API & MCP. The backend enforces
both the user role and the token-scoped permissions for each proxied request.
"""

import argparse
import contextvars
import ipaddress
import json
import os
import sys
import uuid
from typing import Any

import requests as http_requests

API_BASE = os.environ.get("DISCVAULT_API", "http://localhost:5000")
MCP_API_KEY = os.environ.get("MCP_API_KEY", "")
SERVER_VERSION = os.environ.get("DISCVAULT_MCP_VERSION", "26.0.0")
MCP_FORWARD_HEADER_NAMES = (
    "X-DiscVault-Client-IP",
    "CF-Connecting-IP",
    "True-Client-IP",
    "Fastly-Client-IP",
    "Fly-Client-IP",
    "X-Azure-ClientIP",
    "X-Real-IP",
    "X-Client-IP",
    "X-Cluster-Client-IP",
    "X-Forwarded-For",
    "X-Original-Forwarded-For",
    "Forwarded",
)
REQUEST_FORWARD_HEADERS: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "REQUEST_FORWARD_HEADERS",
    default={},
)


def _public_ip(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "," in text:
        text = text.split(",", 1)[0].strip()
    if text.lower().startswith("for="):
        text = text[4:].strip().strip('"').strip("'")
    if ";" in text:
        text = text.split(";", 1)[0].strip()
    try:
        parsed = ipaddress.ip_address(text)
    except ValueError:
        return ""
    return str(parsed) if parsed.is_global else ""


def _request_forward_headers(flask_request: Any) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name in MCP_FORWARD_HEADER_NAMES:
        value = str(flask_request.headers.get(name) or "").strip()
        if value:
            headers[name] = value[:500]
    public_ip = ""
    for name in MCP_FORWARD_HEADER_NAMES:
        public_ip = _public_ip(headers.get(name))
        if public_ip:
            break
    if not public_ip:
        public_ip = _public_ip(getattr(flask_request, "remote_addr", ""))
    if public_ip:
        headers["X-DiscVault-Client-IP"] = public_ip
        headers.setdefault("X-Real-IP", public_ip)
        headers.setdefault("X-Forwarded-For", public_ip)
    return headers


def _headers(user_bearer: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = dict(REQUEST_FORWARD_HEADERS.get({}))
    if user_bearer:
        headers["Authorization"] = f"Bearer {user_bearer}"
    elif MCP_API_KEY:
        headers["Authorization"] = f"Bearer {MCP_API_KEY}"
    return headers


def api_get(path: str, params: dict | None = None, bearer: str | None = None) -> Any:
    response = http_requests.get(
        f"{API_BASE}{path}",
        params=params,
        headers=_headers(bearer),
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def api_post(path: str, data: dict, bearer: str | None = None) -> Any:
    response = http_requests.post(
        f"{API_BASE}{path}",
        json=data,
        headers=_headers(bearer),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def api_delete(path: str, bearer: str | None = None) -> Any:
    response = http_requests.delete(
        f"{API_BASE}{path}",
        headers=_headers(bearer),
        timeout=30,
    )
    response.raise_for_status()
    if not response.content:
        return {}
    return response.json()


TOOLS = [
    {
        "name": "search_collection",
        "description": "Search the DiscVault collection by title, barcode, director, actor, genre or format.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term"},
                "format": {"type": "string", "enum": ["4K UHD", "Blu-ray", "DVD", ""]},
            },
            "required": [],
        },
    },
    {
        "name": "get_collection_stats",
        "description": "Get collection statistics from DiscVault.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_movie_details",
        "description": "Get full details of a DiscVault movie by UUID.",
        "inputSchema": {
            "type": "object",
            "properties": {"movie_id": {"type": "string", "description": "DiscVault Next movie UUID"}},
            "required": ["movie_id"],
        },
    },
    {
        "name": "add_movie",
        "description": "Add a movie to the DiscVault collection.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "barcode": {"type": "string"},
                "title": {"type": "string"},
                "year": {"type": "string"},
                "original_title": {"type": "string"},
                "format": {"type": "string", "enum": ["4K UHD", "Blu-ray", "DVD"]},
                "edition": {"type": "string"},
                "country": {"type": "string"},
                "language": {"type": "string"},
                "overview": {"type": "string"},
                "location": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "delete_movie",
        "description": "Remove a movie from DiscVault by UUID.",
        "inputSchema": {
            "type": "object",
            "properties": {"movie_id": {"type": "string", "description": "DiscVault Next movie UUID"}},
            "required": ["movie_id"],
        },
    },
    {
        "name": "lookup_barcode",
        "description": "Look up a barcode through the configured DiscVault metadata plugins.",
        "inputSchema": {
            "type": "object",
            "properties": {"barcode": {"type": "string"}},
            "required": ["barcode"],
        },
    },
    {
        "name": "list_all_movies",
        "description": "List all movies in the DiscVault collection.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_watchlist",
        "description": "Get the authenticated user's DiscVault watchlist.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_watch_history",
        "description": "Get the authenticated user's DiscVault watch history.",
        "inputSchema": {
            "type": "object",
            "properties": {"movie_id": {"type": "string", "description": "Optional DiscVault movie UUID"}},
            "required": [],
        },
    },
    {
        "name": "get_groups",
        "description": "List DiscVault media groups.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]


def _movie_summary(movie: dict) -> str:
    metadata = movie.get("metadata") or {}
    title = movie.get("title") or metadata.get("title") or "Untitled"
    year = movie.get("year") or metadata.get("year") or "?"
    media_format = movie.get("format") or metadata.get("format") or "?"
    return f"[{movie.get('id')}] {title} ({year}) - {media_format}"


def _movie_from_list_entry(entry: dict) -> dict:
    return entry.get("movie") or entry.get("snapshot") or entry


def _execute_tool_inner(name: str, args: dict, bearer: str | None = None) -> str:
    try:
        if name == "search_collection":
            data = api_get(
                "/api/next/api/v1/movies",
                {"q": args.get("query", ""), "format": args.get("format", ""), "limit": 50},
                bearer=bearer,
            )
            items = data.get("items", []) if isinstance(data, dict) else []
            if not items:
                return "No movies found matching your search."
            lines = [f"Found {len(items)} movie(s):\n"]
            for movie in items:
                metadata = movie.get("metadata") or {}
                lines.append(f"- {_movie_summary(movie)}")
                for key, label in (("director", "Director"), ("genre", "Genre"), ("rating", "Rating")):
                    value = movie.get(key) or metadata.get(key)
                    if value:
                        lines.append(f"  {label}: {value}")
            return "\n".join(lines)

        if name == "get_collection_stats":
            data = api_get("/api/next/api/v1/stats", bearer=bearer)
            counts = data.get("counts", {}) if isinstance(data, dict) else {}
            lines = ["DiscVault collection stats\n"]
            for key in ("movies", "containers", "people", "movieCredits", "mediaAssets", "mediaGroups", "users"):
                if key in counts:
                    lines.append(f"- {key}: {counts[key]}")
            return "\n".join(lines)

        if name == "get_movie_details":
            return json.dumps(
                api_get(f"/api/next/api/v1/movies/{args['movie_id']}", bearer=bearer),
                indent=2,
                ensure_ascii=False,
            )

        if name == "add_movie":
            result = api_post("/api/next/api/v1/movies", args, bearer=bearer)
            movie = (result.get("detail") or {}).get("movie") or result.get("movie") or {}
            return f"Added {_movie_summary(movie)}"

        if name == "delete_movie":
            api_delete(f"/api/next/api/v1/movies/{args['movie_id']}", bearer=bearer)
            return f"Movie {args['movie_id']} removed."

        if name == "lookup_barcode":
            data = api_get(f"/api/next/api/v1/lookup/{args['barcode']}", bearer=bearer)
            metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
            return json.dumps(metadata or {"barcode": args["barcode"], "status": "not_found"}, indent=2, ensure_ascii=False)

        if name == "list_all_movies":
            data = api_get("/api/next/api/v1/movies", {"limit": 1000}, bearer=bearer)
            items = data.get("items", []) if isinstance(data, dict) else []
            if not items:
                return "Collection is empty."
            lines = [f"Full collection ({len(items)} titles):\n"]
            lines.extend(_movie_summary(movie) for movie in items)
            return "\n".join(lines)

        if name == "get_watchlist":
            data = api_get("/api/next/api/v1/watchlist", bearer=bearer)
            items = data.get("items", []) if isinstance(data, dict) else []
            if not items:
                return "Your watchlist is empty."
            lines = [f"Your watchlist ({len(items)} title(s)):\n"]
            for entry in items:
                movie = _movie_from_list_entry(entry)
                date = entry.get("list_date") or entry.get("created_at")
                lines.append(f"- {_movie_summary(movie)}")
                if date:
                    lines.append(f"  Added: {str(date)[:10]}")
            return "\n".join(lines)

        if name == "get_watch_history":
            data = api_get("/api/next/api/v1/watched", bearer=bearer)
            items = data.get("items", []) if isinstance(data, dict) else []
            movie_id = args.get("movie_id")
            if movie_id:
                items = [entry for entry in items if str(_movie_from_list_entry(entry).get("id")) == str(movie_id)]
            if not items:
                return f"No watch history found{f' for movie {movie_id}' if movie_id else ''}."
            lines = [f"Watch history ({len(items)} item(s)):\n"]
            for entry in items:
                movie = _movie_from_list_entry(entry)
                date = entry.get("list_date") or entry.get("watched_at") or entry.get("created_at")
                lines.append(f"- {_movie_summary(movie)} - watched {str(date)[:10] if date else '?'}")
            return "\n".join(lines)

        if name == "get_groups":
            data = api_get("/api/next/api/v1/groups", bearer=bearer)
            items = data.get("items", []) if isinstance(data, dict) else []
            if not items:
                return "You are not a member of any groups."
            lines = [f"Groups ({len(items)}):\n"]
            for group in items:
                line = f"- [{group.get('id')}] {group.get('name')}"
                if group.get("description"):
                    line += f" - {group['description']}"
                lines.append(line)
            return "\n".join(lines)

        return f"Unknown tool: {name}"
    except http_requests.exceptions.ConnectionError:
        return f"Cannot connect to DiscVault API at {API_BASE}."
    except http_requests.exceptions.HTTPError as exc:
        response = exc.response
        if response is not None and response.status_code == 401:
            return "Authentication failed. Check your DiscVault API token in Profile -> API & MCP."
        if response is not None and response.status_code == 403:
            return "Permission denied. This token or user role does not allow the requested MCP tool."
        body = ""
        if response is not None:
            try:
                body = f" {response.json()}"
            except Exception:
                body = f" {response.text[:300]}"
        return f"API error: {exc}{body}"
    except Exception as exc:
        return f"Error: {exc}"


def execute_tool(name: str, args: dict, bearer: str | None = None) -> str:
    try:
        catalog = api_get("/api/next/mcp/catalog", bearer=bearer)
        allowed_tools = {
            item.get("name")
            for item in catalog.get("tools", [])
            if item.get("allowed")
        }
        if name not in allowed_tools:
            return "Permission denied. This token or user role does not allow the requested MCP tool."
    except http_requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code in {401, 403}:
            return "Permission denied. This token or user role cannot use the DiscVault MCP server."
        return f"API error while checking MCP permissions: {exc}"
    except http_requests.exceptions.ConnectionError:
        return f"Cannot connect to DiscVault API at {API_BASE}."
    return _execute_tool_inner(name, args or {}, bearer)


# MCP protocol versions this server can speak, newest first. The default is the
# version we advertise when the client does not request one (or requests an
# unknown one). During `initialize` we echo the client's requested version when
# it is one we support, so newer or older clients negotiate a common version
# instead of being rejected.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

SERVER_CAPABILITIES = {"tools": {}}
SERVER_DESCRIPTOR = {"name": "discvault", "version": SERVER_VERSION}


def negotiate_protocol_version(requested: object) -> str:
    if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return DEFAULT_PROTOCOL_VERSION


def build_server_info(requested: object = None) -> dict:
    return {
        "protocolVersion": negotiate_protocol_version(requested),
        "capabilities": SERVER_CAPABILITIES,
        "serverInfo": SERVER_DESCRIPTOR,
    }


def handle_message(msg: dict, bearer: str | None = None) -> dict | None:
    method = msg.get("method", "")
    msg_id = msg.get("id")
    params = msg.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": build_server_info(params.get("protocolVersion")),
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        text = execute_tool(params.get("name", ""), params.get("arguments", {}), bearer=bearer)
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"content": [{"type": "text", "text": text}]}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if msg_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    return None


def run_stdio() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_message(msg)
        if response:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


def run_http(host: str, port: int) -> None:
    from flask import Flask, Response, jsonify, request
    from flask_cors import CORS

    app = Flask(__name__)
    CORS(app, expose_headers=["Mcp-Session-Id"])

    sessions: set[str] = set()

    def get_bearer() -> str | None:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        return None

    @app.route("/mcp", methods=["POST"])
    def mcp_post():
        forward_token = REQUEST_FORWARD_HEADERS.set(_request_forward_headers(request))
        bearer = get_bearer()
        try:
            if not bearer:
                return jsonify(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32001, "message": "Unauthorized: Bearer token required"},
                    }
                ), 401

            session_id = request.headers.get("Mcp-Session-Id", "")
            if not session_id:
                session_id = uuid.uuid4().hex
                sessions.add(session_id)

            body = request.get_json(force=True, silent=True)
            if body is None:
                return jsonify(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Parse error"},
                    }
                ), 400

            is_batch = isinstance(body, list)
            messages = body if is_batch else [body]
            responses = []
            for msg in messages:
                try:
                    response = handle_message(msg, bearer=bearer)
                except Exception as exc:
                    response = {
                        "jsonrpc": "2.0",
                        "id": msg.get("id") if isinstance(msg, dict) else None,
                        "error": {"code": -32603, "message": f"Internal error: {exc}"},
                    }
                if response is not None:
                    responses.append(response)

            headers = {"Mcp-Session-Id": session_id}
            if not responses:
                return "", 202, headers
            payload = responses if is_batch else responses[0]
            return Response(json.dumps(payload), status=200, mimetype="application/json", headers=headers)
        finally:
            REQUEST_FORWARD_HEADERS.reset(forward_token)

    @app.route("/mcp", methods=["GET"])
    def mcp_get():
        return jsonify({"error": "Use POST for JSON-RPC. SSE stream is not implemented."}), 405

    @app.route("/mcp", methods=["DELETE"])
    def mcp_delete():
        session_id = request.headers.get("Mcp-Session-Id", "")
        sessions.discard(session_id)
        return "", 204

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "transport": "streamable-http", "version": SERVER_VERSION})

    print(f"DiscVault MCP (Streamable HTTP) on {host}:{port}/mcp", file=sys.stderr)
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DiscVault MCP Server")
    parser.add_argument("--http", action="store_true", help="Streamable HTTP transport (default: stdio)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=6090)
    parsed = parser.parse_args()

    if parsed.http:
        run_http(parsed.host, parsed.port)
    else:
        run_stdio()
