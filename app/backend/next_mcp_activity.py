"""MCP activity helpers for the DiscVault Next backend.

These helpers track how API/MCP clients authenticate and which MCP tools are
exposed. They are extracted from ``next_app.py`` so that the profile and audit
domains can reuse them without importing the oversized application module.
``next_app.py`` re-imports every name defined here to preserve its public
surface (tests and older modules continue to import the same names from
``next_app``).
"""

from __future__ import annotations

from flask import request


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
