#!/usr/bin/env python3
"""
DiscVault MCP Server
Supports both stdio and Streamable HTTP transport (MCP spec 2025-11-25).

  stdio:             python server.py
  Streamable HTTP:   python server.py --http --port 6090
"""

import json
import sys
import os
import uuid
import argparse
import requests as http_requests
from typing import Any

API_BASE = os.environ.get("DISCVAULT_API", "http://localhost:5000")
MCP_API_KEY = os.environ.get("MCP_API_KEY", "")


# ── Backend API helpers ──────────────────────────────────────────────────────

def _headers() -> dict:
    h = {}
    if MCP_API_KEY:
        h["Authorization"] = f"Bearer {MCP_API_KEY}"
    return h

def api_get(path: str, params: dict = None) -> Any:
    r = http_requests.get(f"{API_BASE}{path}", params=params, headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def api_post(path: str, data: dict) -> Any:
    r = http_requests.post(f"{API_BASE}{path}", json=data, headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def api_delete(path: str) -> Any:
    r = http_requests.delete(f"{API_BASE}{path}", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


# ── Tool definitions ─────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "search_collection",
        "description": "Search your physical disc collection by title, director or genre.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term"},
                "format": {"type": "string", "enum": ["4K UHD", "Blu-ray", "DVD", ""]}
            },
            "required": []
        }
    },
    {
        "name": "get_collection_stats",
        "description": "Get collection statistics: total count, breakdown by format, recent additions.",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_movie_details",
        "description": "Get full details of a specific movie by its ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "movie_id": {"type": "integer", "description": "Movie ID"}
            },
            "required": ["movie_id"]
        }
    },
    {
        "name": "add_movie",
        "description": "Add a movie to the collection.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "barcode": {"type": "string"}, "title": {"type": "string"},
                "year": {"type": "string"}, "director": {"type": "string"},
                "genre": {"type": "string"},
                "format": {"type": "string", "enum": ["4K UHD", "Blu-ray", "DVD"]},
                "location": {"type": "string"}, "notes": {"type": "string"}
            },
            "required": ["barcode", "title"]
        }
    },
    {
        "name": "delete_movie",
        "description": "Remove a movie from the collection by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {"movie_id": {"type": "integer"}},
            "required": ["movie_id"]
        }
    },
    {
        "name": "lookup_barcode",
        "description": "Look up a barcode to find movie info (without saving).",
        "inputSchema": {
            "type": "object",
            "properties": {"barcode": {"type": "string"}},
            "required": ["barcode"]
        }
    },
    {
        "name": "list_all_movies",
        "description": "List all movies in the collection.",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    }
]


# ── Tool execution ───────────────────────────────────────────────────────────

def execute_tool(name: str, args: dict) -> str:
    try:
        if name == "search_collection":
            data = api_get("/api/movies", {"q": args.get("query",""), "format": args.get("format","")})
            if not data:
                return "No movies found matching your search."
            lines = [f"Found {len(data)} movie(s):\n"]
            for m in data:
                lines.append(f"• [{m['id']}] **{m['title']}** ({m.get('year','?')}) — {m.get('format','?')}")
                for k, lbl in [('director','Director'),('genre','Genre'),('rating','IMDb'),
                               ('runtime','Runtime'),('box_set','Box Set'),
                               ('distributor','Distributor'),('location','Location')]:
                    if m.get(k): lines.append(f"  {lbl}: {m[k]}")
            return "\n".join(lines)

        elif name == "get_collection_stats":
            data = api_get("/api/stats")
            lines = [f"📀 Collection Stats\n", f"Total discs: {data['total']}\n"]
            for f in data.get("by_format", []):
                lines.append(f"  • {f['format']}: {f['count']}")
            if data.get("recent"):
                lines.append("\nRecently added:")
                for m in data["recent"]:
                    lines.append(f"  • {m['title']} ({m.get('year','?')})")
            return "\n".join(lines)

        elif name == "get_movie_details":
            return json.dumps(api_get(f"/api/movies/{args['movie_id']}"), indent=2, ensure_ascii=False)

        elif name == "add_movie":
            result = api_post("/api/movies", args)
            m = result.get("movie", {})
            return f"Added {m.get('title')} ({m.get('year')}) — ID {m.get('id')}"

        elif name == "delete_movie":
            api_delete(f"/api/movies/{args['movie_id']}")
            return f"Movie {args['movie_id']} removed."

        elif name == "lookup_barcode":
            data = api_get(f"/api/lookup/{args['barcode']}")
            if data["status"] == "exists":
                m = data["movie"]
                return f"Already in collection: {m['title']} ({m.get('year')}) — ID {m['id']}"
            elif data["status"] == "found":
                m = data["movie"]
                return f"Found: {m.get('title')} ({m.get('year')}) — Director: {m.get('director')} — Not in collection yet."
            return f"Barcode {args['barcode']} not found."

        elif name == "list_all_movies":
            data = api_get("/api/movies")
            if not data:
                return "Collection is empty."
            lines = [f"Full collection ({len(data)} titles):\n"]
            for m in data:
                lines.append(f"[{m['id']}] {m['title']} ({m.get('year','?')}) — {m.get('format','?')}")
            return "\n".join(lines)

        return f"Unknown tool: {name}"
    except http_requests.exceptions.ConnectionError:
        return f"Cannot connect to DiscVault API at {API_BASE}."
    except Exception as e:
        return f"Error: {str(e)}"


# ── JSON-RPC handler (shared) ────────────────────────────────────────────────

SERVER_INFO = {
    "protocolVersion": "2025-11-05",
    "capabilities": {"tools": {}},
    "serverInfo": {"name": "discvault", "version": "3.0.0"}
}

def handle_message(msg: dict) -> dict | None:
    method = msg.get("method", "")
    msg_id = msg.get("id")
    params = msg.get("params", {})

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": msg_id, "result": SERVER_INFO}
    elif method == "notifications/initialized":
        return None
    elif method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    elif method == "tools/call":
        text = execute_tool(params.get("name", ""), params.get("arguments", {}))
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "content": [{"type": "text", "text": text}]
        }}
    elif method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    elif msg_id is not None:
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return None


# ── Stdio transport ──────────────────────────────────────────────────────────

def run_stdio():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle_message(msg)
        if resp:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


# ── Streamable HTTP transport (MCP spec 2025-11-05) ─────────────────────────
#
# POST /mcp   — client sends JSON-RPC, server returns JSON-RPC
# GET  /mcp   — optional SSE stream (we return 405, POST-only is spec-valid)
# DELETE /mcp — end session
#

def run_http(host: str, port: int):
    from flask import Flask, Response, request as freq, jsonify as fj
    from flask_cors import CORS

    app = Flask(__name__)
    CORS(app, expose_headers=["Mcp-Session-Id"])

    sessions = set()

    @app.route("/mcp", methods=["POST"])
    def mcp_post():
        # Create or reuse session
        session_id = freq.headers.get("Mcp-Session-Id", "")
        if not session_id:
            session_id = uuid.uuid4().hex
            sessions.add(session_id)

        body = freq.get_json(force=True, silent=True)
        if body is None:
            return fj({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "Parse error"}}), 400

        # Support batched requests (JSON-RPC array)
        is_batch = isinstance(body, list)
        messages = body if is_batch else [body]

        responses = []
        for msg in messages:
            resp = handle_message(msg)
            if resp is not None:
                responses.append(resp)

        headers = {"Mcp-Session-Id": session_id}

        if not responses:
            return "", 202, headers
        elif is_batch:
            return Response(json.dumps(responses),
                            status=200, mimetype="application/json", headers=headers)
        else:
            return Response(json.dumps(responses[0]),
                            status=200, mimetype="application/json", headers=headers)

    @app.route("/mcp", methods=["GET"])
    def mcp_get():
        # Optional SSE stream — we use POST-only mode which is spec-compliant
        # Return 405 so clients know to use POST only
        return fj({"error": "Use POST for JSON-RPC. SSE stream not implemented."}), 405

    @app.route("/mcp", methods=["DELETE"])
    def mcp_delete():
        session_id = freq.headers.get("Mcp-Session-Id", "")
        sessions.discard(session_id)
        return "", 204

    @app.route("/health", methods=["GET"])
    def health():
        return fj({"status": "ok", "transport": "streamable-http", "version": "3.0.0"})

    print(f"DiscVault MCP (Streamable HTTP) on {host}:{port}/mcp", file=sys.stderr)
    app.run(host=host, port=port, threaded=True)


# ── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DiscVault MCP Server")
    parser.add_argument("--http", action="store_true",
                        help="Streamable HTTP transport (default: stdio)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=6090)
    args = parser.parse_args()

    if args.http:
        run_http(args.host, args.port)
    else:
        run_stdio()
