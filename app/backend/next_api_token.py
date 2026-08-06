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
        "clientKind": row.get("client_kind") or row.get("clientKind"),
        "deviceId": row.get("device_id") or row.get("deviceId"),
        "deviceLabel": row.get("device_label") or row.get("deviceLabel"),
        "deviceModel": row.get("device_model") or row.get("deviceModel"),
        "lastSeenIp": row.get("last_seen_ip") or row.get("lastSeenIp"),
        "lastSeenIpSource": row.get("last_seen_ip_source") or row.get("lastSeenIpSource"),
    }


def api_access_connection_key(row: dict[str, Any]) -> tuple:
    """The identity of the *connection* a token belongs to.

    A device that sends a ``device_id`` owns one row and keeps it, so the id is
    the whole key. Tokens minted before that field existed carry nothing to tell
    them apart, so they are keyed on what the client happened to leave behind —
    enough to collapse the repeated logins of one app install for display,
    without pretending the server can prove two rows are the same device.
    """

    entry = api_access_token_row(row)
    device_id = entry.get("deviceId")
    if device_id:
        return ("device", device_id)
    return (
        "legacy",
        entry.get("name") or "",
        entry.get("clientKind") or "",
        row.get("user_agent") or row.get("userAgent") or "",
    )


def _max_timestamp(left: Any, right: Any) -> Any:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def _min_timestamp(left: Any, right: Any) -> Any:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def api_access_connection_display_name(entry: dict[str, Any]) -> str:
    """What to call this connection: the user's own name for the device wins."""

    return (
        entry.get("deviceLabel")
        or entry.get("deviceModel")
        or entry.get("name")
        or "DiscVault app"
    )


def group_api_access_tokens(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse token rows into one entry per connection.

    Native clients have no refresh endpoint, so before device ids existed every
    login added a row. Grouping keeps the page readable for the tokens already
    in the database; new logins from a device-aware client reuse their row and
    never reach here with more than one.

    ``rows`` is expected in the query's order (active first, newest first); the
    grouping preserves it, so the first row of a group is its representative.
    """

    grouped: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        entry = api_access_token_row(row)
        key = api_access_connection_key(row)
        existing = grouped.get(key)
        if existing is None:
            entry["tokenIds"] = [entry["id"]]
            entry["sessionCount"] = 1
            entry["displayName"] = api_access_connection_display_name(entry)
            grouped[key] = entry
            continue
        existing["tokenIds"].append(entry["id"])
        existing["sessionCount"] += 1
        existing["createdAt"] = _min_timestamp(existing.get("createdAt"), entry.get("createdAt"))
        # The address shown must belong to the most recent use in the group, not
        # to whichever token happened to represent it.
        if entry.get("lastSeenIp") and _max_timestamp(
            existing.get("lastUsedAt"), entry.get("lastUsedAt")
        ) == entry.get("lastUsedAt"):
            existing["lastSeenIp"] = entry.get("lastSeenIp")
            existing["lastSeenIpSource"] = entry.get("lastSeenIpSource")
        existing["lastUsedAt"] = _max_timestamp(existing.get("lastUsedAt"), entry.get("lastUsedAt"))
        # A connection only counts as revoked once nothing in it still works;
        # the representative is an active row whenever the group has one.
        if existing.get("revokedAt") is not None:
            existing["revokedAt"] = (
                _max_timestamp(existing["revokedAt"], entry.get("revokedAt"))
                if entry.get("revokedAt") is not None
                else None
            )
    return list(grouped.values())


def api_token_permission_is_grantable(permission_key: str) -> bool:
    return (
        permission_key in API_TOKEN_GRANTABLE_PERMISSION_KEYS
        or permission_key.startswith("mcp.tool.")
    )


def profile_api_access_payload(
    conn, actor: dict[str, Any], *, include_revoked: bool = False
) -> dict[str, Any]:
    next_app = _next_app()
    permissions = set(actor.get("permissions") or [])
    if actor.get("role") == "owner":
        permissions.update(next_app.permission_key_catalog(conn))
    manageable = any(
        key in permissions
        for key in ("api.tokens.manage", "api.read", "api.write", "mcp.use")
    ) or bool({key for key in permissions if key.startswith("mcp.tool.")})
    tokens: list[dict[str, Any]] = []
    revoked_count = 0
    if next_app.table_exists(conn, "api_access_tokens") and actor.get("id"):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, scopes, permission_keys, created_at, last_used_at, expires_at, revoked_at,
                       client_kind, device_id, device_label, device_model, user_agent,
                       last_seen_ip, last_seen_ip_source
                FROM api_access_tokens
                WHERE user_id=%s
                ORDER BY revoked_at NULLS FIRST, created_at DESC
                """,
                (actor["id"],),
            )
            connections = group_api_access_tokens(cur.fetchall())
            revoked_count = sum(1 for entry in connections if entry.get("revokedAt") is not None)
            tokens = (
                connections
                if include_revoked
                else [entry for entry in connections if entry.get("revokedAt") is None]
            )
    return {
        "available": next_app.table_exists(conn, "api_access_tokens"),
        "manageable": manageable,
        "tokens": tokens,
        "includesRevoked": include_revoked,
        "revokedCount": revoked_count,
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
