MCP_TOOLS = [
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
    "get_collection_stats_detailed",
    "get_top_formats",
    "get_top_genres",
    "get_top_directors",
    "get_top_actors",
]


def health_check(context):
    return {
        "status": "ok",
        "service": "mcp",
        "builtIn": True,
        "transport": "streamable_http",
        "endpoint": "/mcp",
        "toolCount": len(MCP_TOOLS),
        "tools": MCP_TOOLS,
        "requiresApiToken": True,
        "permissions": ["mcp.use", *[f"mcp.tool.{tool}" for tool in MCP_TOOLS]],
    }
