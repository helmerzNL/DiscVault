"""API access token helpers for the DiscVault Next backend.

This module owns the API token permission catalogs and the token payload
helpers used by the profile API access surface. The helpers are extracted from
``next_app.py`` so that domain modules can reuse them without importing the
oversized application module. ``next_app.py`` re-imports every name defined here
to preserve its public surface (tests and older modules continue to import the
same names from ``next_app``).

Runtime dependencies that still live in ``next_app`` (``permission_key_catalog``
and ``table_exists``) are resolved lazily through the application module so that
the existing test mocks targeting ``app.backend.next_app`` keep working during
the transition.
"""

from __future__ import annotations

from typing import Any

try:  # pragma: no cover - exercised indirectly by both layouts
    from .next_common import NextApiError
    from .next_import import clean_text
    from .next_mcp_activity import MCP_TOOL_NAMES
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_common import NextApiError
    from next_import import clean_text
    from next_mcp_activity import MCP_TOOL_NAMES


API_TOKEN_GRANTABLE_PERMISSION_KEYS = (
    "api.read",
    "api.write",
    "api.tokens.manage",
    "mcp.use",
    "mcp.logs.view",
    "collection.view",
    "collection.add",
    "collection.add_own",
    "collection.import",
    "collection.edit_all",
    "collection.bulk_edit",
    "containers.view",
    "containers.create",
    "containers.edit",
    "groups.view",
    "metadata.search",
    "metadata.refresh_one",
    "metadata.refresh_bulk",
    "admin.view_jobs",
)
API_TOKEN_DEFAULT_PERMISSION_KEYS = (
    "api.read",
    "mcp.use",
    "mcp.tool.search_collection",
    "mcp.tool.get_collection_stats",
    "mcp.tool.get_movie_details",
    "mcp.tool.lookup_barcode",
    "metadata.search",
)


def _next_app():
    """Return the ``next_app`` module, resolving the deferred dependency lazily.

    ``permission_key_catalog`` and ``table_exists`` still live in ``next_app``
    (they belong to the security/shared domains that have not been split yet).
    Importing them lazily avoids an import cycle and keeps the existing test
    mocks targeting ``app.backend.next_app`` effective.
    """

    try:  # pragma: no cover - import shape depends on runtime layout
        from . import next_app
    except ImportError:  # pragma: no cover - supports gunicorn next_app:app
        import next_app
    return next_app


def api_access_token_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "scopes": row.get("scopes") or [],
        "permissionKeys": row.get("permission_keys") or row.get("permissionKeys") or [],
        "createdAt": row.get("created_at"),
        "lastUsedAt": row.get("last_used_at"),
        "expiresAt": row.get("expires_at"),
        "revokedAt": row.get("revoked_at"),
    }


def api_token_permission_is_grantable(permission_key: str) -> bool:
    return (
        permission_key in API_TOKEN_GRANTABLE_PERMISSION_KEYS
        or permission_key.startswith("mcp.tool.")
    )


def profile_api_access_payload(conn, actor: dict[str, Any]) -> dict[str, Any]:
    next_app = _next_app()
    permissions = set(actor.get("permissions") or [])
    if actor.get("role") == "owner":
        permissions.update(next_app.permission_key_catalog(conn))
    manageable = any(
        key in permissions
        for key in ("api.tokens.manage", "api.read", "api.write", "mcp.use")
    ) or bool({key for key in permissions if key.startswith("mcp.tool.")})
    tokens: list[dict[str, Any]] = []
    if next_app.table_exists(conn, "api_access_tokens") and actor.get("id"):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, scopes, permission_keys, created_at, last_used_at, expires_at, revoked_at
                FROM api_access_tokens
                WHERE user_id=%s
                ORDER BY revoked_at NULLS FIRST, created_at DESC
                """,
                (actor["id"],),
            )
            tokens = [api_access_token_row(row) for row in cur.fetchall()]
    return {
        "available": next_app.table_exists(conn, "api_access_tokens"),
        "manageable": manageable,
        "tokens": tokens,
        "allowedPermissions": sorted(
            key for key in permissions
            if api_token_permission_is_grantable(key)
        ),
        "defaultPermissions": sorted(
            key for key in API_TOKEN_DEFAULT_PERMISSION_KEYS
            if key in permissions
        ),
        "mcpTools": [
            {"name": tool, "permission": f"mcp.tool.{tool}"}
            for tool in MCP_TOOL_NAMES
        ],
    }


def normalize_api_token_permissions(conn, actor: dict[str, Any], raw_values: Any) -> list[str]:
    if raw_values in (None, ""):
        raw_values = []
    if not isinstance(raw_values, list):
        raise NextApiError("permissionKeys must be an array", 400)
    known = _next_app().permission_key_catalog(conn)
    actor_permissions = set(actor.get("permissions") or [])
    if actor.get("role") == "owner":
        actor_permissions.update(known)
    normalized: list[str] = []
    for item in raw_values:
        key = clean_text(item)
        if not key:
            continue
        if key not in known:
            raise NextApiError(f"Unknown permission: {key}", 400)
        if not api_token_permission_is_grantable(key):
            raise NextApiError(f"Permission is not valid for API tokens: {key}", 400)
        if key not in actor_permissions:
            raise NextApiError(f"You cannot grant API token permission: {key}", 403)
        if key not in normalized:
            normalized.append(key)
    if not normalized:
        defaults = [
            key
            for key in API_TOKEN_DEFAULT_PERMISSION_KEYS
            if key in actor_permissions
        ]
        normalized = defaults
    if not normalized:
        raise NextApiError("No API or MCP token permissions are available for this user", 403)
    return normalized


def api_token_scopes_for_permissions(permission_keys: list[str]) -> list[str]:
    scopes: list[str] = []
    read_permissions = {
        "api.read",
        "collection.view",
        "containers.view",
        "groups.view",
        "metadata.search",
    }
    write_permissions = {
        "api.write",
        "collection.add",
        "collection.add_own",
        "collection.import",
        "collection.edit_all",
        "collection.bulk_edit",
        "containers.create",
        "containers.edit",
        "metadata.refresh_one",
        "metadata.refresh_bulk",
    }
    permission_set = set(permission_keys)
    if permission_set.intersection(read_permissions):
        scopes.append("read")
    if permission_set.intersection(write_permissions):
        scopes.append("write")
    if "mcp.use" in permission_keys or any(key.startswith("mcp.tool.") for key in permission_keys):
        scopes.append("mcp")
    return scopes
