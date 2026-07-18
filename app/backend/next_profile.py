"""Profile account helpers for the DiscVault Next backend.

This module owns the profile account payload builders (user details and
recovery-code status). The helpers are extracted from ``next_app.py`` so that
the profile routes can reuse them without importing the oversized application
module. ``next_app.py`` re-imports every name defined here to preserve its
public surface (tests and older modules continue to import the same names from
``next_app``).

Runtime dependencies that still live in ``next_app``
(``media_asset_public_url`` and ``next_user_primary_role``) are resolved lazily
through the application module so that the existing test mocks targeting
``app.backend.next_app`` keep working during the transition.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from flask import Flask, request
from psycopg.types.json import Jsonb

try:  # pragma: no cover - exercised indirectly by both layouts
    from .next_api_token import (
        api_access_token_row,
        api_token_scopes_for_permissions,
        normalize_api_token_permissions,
        profile_api_access_payload,
    )
    from .next_audit import (
        audit_event,
        audit_event_row,
        normalize_profile_api_audit_category,
        profile_api_audit_category_condition,
        profile_api_audit_search_term,
        profile_api_audit_token_match_condition,
    )
    from .next_auth import (
        next_api_token_hash,
        next_auth_current_user,
        next_create_api_token_value,
        next_generate_recovery_codes,
        next_replace_recovery_codes,
    )
    from .next_common import NextApiError, parse_uuid, response, table_exists
    from .next_import import clean_text
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_api_token import (
        api_access_token_row,
        api_token_scopes_for_permissions,
        normalize_api_token_permissions,
        profile_api_access_payload,
    )
    from next_audit import (
        audit_event,
        audit_event_row,
        normalize_profile_api_audit_category,
        profile_api_audit_category_condition,
        profile_api_audit_search_term,
        profile_api_audit_token_match_condition,
    )
    from next_auth import (
        next_api_token_hash,
        next_auth_current_user,
        next_create_api_token_value,
        next_generate_recovery_codes,
        next_replace_recovery_codes,
    )
    from next_common import NextApiError, parse_uuid, response, table_exists
    from next_import import clean_text


def _next_app():
    """Return the ``next_app`` module, resolving deferred dependencies lazily.

    ``media_asset_public_url`` and ``next_user_primary_role`` still live in
    ``next_app`` (they belong to the media and security domains that have not
    been split yet). Importing them lazily avoids an import cycle.
    """

    try:  # pragma: no cover - import shape depends on runtime layout
        from . import next_app
    except ImportError:  # pragma: no cover - supports gunicorn next_app:app
        import next_app
    return next_app


def next_profile_user_payload(conn, user: dict[str, Any]) -> dict[str, Any]:
    next_app = _next_app()
    user_id = user["id"]
    avatar: dict[str, Any] | None = None
    fresh_user = user
    if table_exists(conn, "users"):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.display_name,
                    u.first_name,
                    u.last_name,
                    u.status,
                    u.avatar_asset_id,
                    u.updated_at,
                    ma.id AS avatar_id,
                    ma.kind AS avatar_kind,
                    ma.variant AS avatar_variant,
                    ma.storage_backend AS avatar_storage_backend,
                    ma.storage_key AS avatar_storage_key,
                    ma.source_url AS avatar_source_url,
                    ma.provider_id AS avatar_provider_id,
                    ma.content_type AS avatar_content_type,
                    ma.width AS avatar_width,
                    ma.height AS avatar_height,
                    ma.size_bytes AS avatar_size_bytes,
                    ma.sha256 AS avatar_sha256,
                    ma.metadata AS avatar_metadata,
                    ma.created_at AS avatar_created_at
                FROM users u
                LEFT JOIN media_assets ma ON ma.id = u.avatar_asset_id
                WHERE u.id=%s
                """,
                (user_id,),
            )
            row = cur.fetchone()
        if row:
            fresh_user = row
            if row.get("avatar_id"):
                avatar = {
                    "id": row.get("avatar_id"),
                    "kind": row.get("avatar_kind"),
                    "variant": row.get("avatar_variant"),
                    "storage_backend": row.get("avatar_storage_backend"),
                    "storage_key": row.get("avatar_storage_key"),
                    "source_url": row.get("avatar_source_url"),
                    "provider_id": row.get("avatar_provider_id"),
                    "content_type": row.get("avatar_content_type"),
                    "width": row.get("avatar_width"),
                    "height": row.get("avatar_height"),
                    "size_bytes": row.get("avatar_size_bytes"),
                    "sha256": row.get("avatar_sha256"),
                    "metadata": row.get("avatar_metadata"),
                    "created_at": row.get("avatar_created_at"),
                }
    avatar_url = next_app.media_asset_public_url(avatar)
    display_name = fresh_user.get("display_name") or fresh_user.get("username")
    return {
        "id": fresh_user.get("id"),
        "username": fresh_user.get("username"),
        "displayName": display_name,
        "display_name": display_name,
        "first_name": fresh_user.get("first_name"),
        "last_name": fresh_user.get("last_name"),
        "role": fresh_user.get("role") or next_app.next_user_primary_role(conn, user_id),
        "avatarAssetId": fresh_user.get("avatar_asset_id"),
        "avatar_asset_id": fresh_user.get("avatar_asset_id"),
        "avatarUrl": avatar_url,
        "avatar_url": avatar_url,
        "updated_at": fresh_user.get("updated_at"),
    }


def next_profile_recovery_payload(conn, user_id: UUID | str) -> dict[str, Any]:
    if not table_exists(conn, "recovery_codes"):
        return {
            "available": False,
            "activeCount": 0,
            "usedCount": 0,
            "lastGeneratedAt": None,
        }
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE used_at IS NULL
                      AND (expires_at IS NULL OR expires_at > now())
                )::int AS active_count,
                COUNT(*) FILTER (WHERE used_at IS NOT NULL)::int AS used_count,
                MAX(created_at) AS last_generated_at
            FROM recovery_codes
            WHERE user_id=%s
            """,
            (user_id,),
        )
        row = cur.fetchone() or {}
    return {
        "available": True,
        "activeCount": int(row.get("active_count") or 0),
        "usedCount": int(row.get("used_count") or 0),
        "lastGeneratedAt": row.get("last_generated_at"),
    }


def register_next_profile_routes(flask_app: Flask, *, connect) -> None:  # pragma: no cover - Flask integration
    """Register all /api/next/profile routes on *flask_app*.

    ``connect`` must be a callable that returns a database connection context
    manager (i.e. the ``connect`` closure defined in ``next_app.py``).

    Security helpers that have not yet been extracted from ``next_app.py``
    (``require_next_authenticated_user``, ``require_next_permission``,
    ``next_user_primary_role``, ``next_user_permission_keys``,
    ``save_uploaded_profile_avatar_file``,
    ``create_uploaded_profile_avatar_asset``, ``MAX_ARTWORK_UPLOAD_BYTES``)
    are resolved lazily via ``_next_app()`` so that test mocks targeting
    ``app.backend.next_app.<name>`` continue to work during the transition.
    """

    @flask_app.get("/api/next/profile")
    def get_next_profile():
        _app = _next_app()
        with connect() as conn:
            user = next_auth_current_user(conn)
            if not user:
                raise NextApiError("Unauthorized", 401)
            user["role"] = _app.next_user_primary_role(conn, user["id"])
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, credential_name, created_at, last_used_at, sign_count
                    FROM passkey_credentials
                    WHERE user_id=%s
                    ORDER BY created_at DESC
                    """,
                    (user["id"],),
                )
                credentials = cur.fetchall()
            user_payload = next_profile_user_payload(conn, user)
            user_payload["credentialCount"] = len(credentials)
            user["permissions"] = sorted(_app.next_user_permission_keys(conn, user["id"]))
            return response(
                {
                    "status": "ok",
                    "user": user_payload,
                    "credentials": credentials,
                    "recovery": next_profile_recovery_payload(conn, user["id"]),
                    "apiAccess": profile_api_access_payload(conn, user),
                }
            )

    @flask_app.get("/api/next/profile/api-tokens")
    def get_next_profile_api_tokens():
        _app = _next_app()
        with connect() as conn:
            actor = _app.require_next_authenticated_user(conn)
            actor["permissions"] = sorted(_app.next_user_permission_keys(conn, actor["id"]))
            return response({"status": "ok", "apiAccess": profile_api_access_payload(conn, actor)})

    @flask_app.post("/api/next/profile/api-tokens")
    def create_next_profile_api_token():
        _app = _next_app()
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise NextApiError("API token request body must be an object", 400)
        name = clean_text(body.get("name")) or "DiscVault API token"
        if len(name) > 120:
            raise NextApiError("name must be 120 characters or fewer", 400)
        with connect() as conn:
            actor = _app.require_next_permission(conn, "api.tokens.manage")
            if not table_exists(conn, "api_access_tokens"):
                raise NextApiError("API token table is not available", 503)
            permission_keys = normalize_api_token_permissions(conn, actor, body.get("permissionKeys") or body.get("permissions"))
            token_value = next_create_api_token_value()
            scopes = api_token_scopes_for_permissions(permission_keys)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO api_access_tokens (
                            user_id,
                            name,
                            token_hash,
                            scopes,
                            permission_keys,
                            created_by,
                            expires_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, NULL)
                        RETURNING id, name, scopes, permission_keys, created_at, last_used_at, expires_at, revoked_at
                        """,
                        (
                            actor["id"],
                            name,
                            next_api_token_hash(token_value),
                            Jsonb(scopes),
                            Jsonb(permission_keys),
                            actor["id"],
                        ),
                    )
                    token_row = cur.fetchone()
                audit_event(
                    conn,
                    event_type="api_token.created",
                    category="security",
                    actor=actor,
                    target_type="api_access_token",
                    target_id=token_row["id"],
                    summary=f"Created API token {name}",
                    metadata={
                        "apiTokenId": token_row["id"],
                        "apiTokenName": name,
                        "command": "create_api_token",
                        "method": request.method,
                        "endpoint": request.path,
                        "scopes": scopes,
                        "permissionKeys": permission_keys,
                    },
                )
            return response(
                {
                    "status": "ok",
                    "token": token_value,
                    "apiToken": api_access_token_row(token_row),
                    "apiAccess": profile_api_access_payload(conn, actor),
                },
                201,
            )

    @flask_app.delete("/api/next/profile/api-tokens/<token_id>")
    def revoke_next_profile_api_token(token_id: str):
        _app = _next_app()
        token_uuid = parse_uuid(token_id, "tokenId")
        if not token_uuid:
            raise NextApiError("tokenId is required", 400)
        with connect() as conn:
            actor = _app.require_next_permission(conn, "api.tokens.manage")
            if not table_exists(conn, "api_access_tokens"):
                raise NextApiError("API token table is not available", 503)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE api_access_tokens
                        SET revoked_at=COALESCE(revoked_at, now())
                        WHERE id=%s AND user_id=%s
                        RETURNING id, name, scopes, permission_keys, created_at, last_used_at, expires_at, revoked_at
                        """,
                        (token_uuid, actor["id"]),
                    )
                    token_row = cur.fetchone()
                    if not token_row:
                        raise NextApiError("API token not found", 404)
                audit_event(
                    conn,
                    event_type="api_token.revoked",
                    category="security",
                    actor=actor,
                    target_type="api_access_token",
                    target_id=token_uuid,
                    summary=f"Revoked API token {token_row.get('name')}",
                    metadata={
                        "apiTokenId": token_uuid,
                        "apiTokenName": token_row.get("name"),
                        "command": "revoke_api_token",
                        "method": request.method,
                        "endpoint": request.path,
                        "scopes": token_row.get("scopes") or [],
                        "permissionKeys": token_row.get("permission_keys") or [],
                    },
                )
            return response(
                {
                    "status": "deleted",
                    "apiToken": api_access_token_row(token_row),
                    "apiAccess": profile_api_access_payload(conn, actor),
                }
            )

    @flask_app.get("/api/next/profile/api-audit-events")
    def get_next_profile_api_audit_events():
        _app = _next_app()
        token_id = clean_text(request.args.get("tokenId") or request.args.get("token_id"))
        category_filter = normalize_profile_api_audit_category(request.args.get("category") or request.args.get("type"))
        search_term = profile_api_audit_search_term(request.args.get("q") or request.args.get("search"))
        token_uuid = None
        if token_id and token_id.lower() != "all":
            token_uuid = parse_uuid(token_id, "tokenId")
        try:
            limit = int(request.args.get("limit") or 100)
        except (TypeError, ValueError):
            limit = 100
        limit = min(max(limit, 1), 250)
        with connect() as conn:
            actor = _app.require_next_authenticated_user(conn)
            actor["permissions"] = sorted(_app.next_user_permission_keys(conn, actor["id"]))
            access = profile_api_access_payload(conn, actor)
            if not access.get("manageable") and not (access.get("tokens") or []):
                raise NextApiError("Permission required: API or MCP access", 403)
            api_tokens_available = table_exists(conn, "api_access_tokens")
            audit_events_available = table_exists(conn, "audit_events")
            diagnostics: dict[str, Any] = {
                "filters": {
                    "tokenId": str(token_uuid) if token_uuid else "all",
                    "category": category_filter,
                    "query": search_term,
                    "limit": limit,
                },
                "tables": {
                    "apiAccessTokens": api_tokens_available,
                    "auditEvents": audit_events_available,
                },
                "access": {
                    "manageable": bool(access.get("manageable")),
                    "available": bool(access.get("available")),
                    "ownedTokenCount": 0,
                    "visibleTokenCount": 0,
                },
                "counts": {
                    "matchingBeforeSearch": 0,
                    "matchingAfterSearch": 0,
                    "returnedEvents": 0,
                    "userScopedEvents": 0,
                },
                "reason": "",
            }
            if not api_tokens_available or not audit_events_available:
                diagnostics["reason"] = "missing_required_tables"
                return response(
                    {
                        "status": "ok",
                        "events": [],
                        "tokenId": str(token_uuid) if token_uuid else "all",
                        "category": category_filter,
                        "query": search_term,
                        "diagnostics": diagnostics,
                    }
                )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name
                    FROM api_access_tokens
                    WHERE user_id=%s
                    """,
                    (actor["id"],),
                )
                owned_tokens = [
                    {"id": str(row["id"]), "name": str(row.get("name") or "")}
                    for row in cur.fetchall()
                ]
            diagnostics["access"]["ownedTokenCount"] = len(owned_tokens)
            owned_tokens_by_id = {token["id"]: token for token in owned_tokens}
            owned_token_ids = list(owned_tokens_by_id)
            if token_uuid:
                requested_token_id = str(token_uuid)
                requested_token = owned_tokens_by_id.get(requested_token_id)
                visible_token_ids = [requested_token_id]
                visible_token_names = [requested_token.get("name") or ""] if requested_token else []
                if not requested_token:
                    diagnostics["reason"] = "selected_token_not_in_current_token_list"
            else:
                visible_token_ids = owned_token_ids
                visible_token_names = [token.get("name") or "" for token in owned_tokens]
            diagnostics["access"]["visibleTokenCount"] = len(visible_token_ids)
            explicit_token_match, token_match_params = profile_api_audit_token_match_condition(
                visible_token_ids,
                visible_token_names,
            )
            category_condition, category_params = profile_api_audit_category_condition(category_filter)
            actor_activity_condition = """
            (
                actor_user_id = %s
                AND (
                    category IN ('api', 'mcp')
                    OR event_type LIKE 'api.%%'
                    OR event_type LIKE 'mcp.%%'
                    OR event_type IN ('api_token.created', 'api_token.revoked')
                )
            )
            """
            actor_activity_params = [actor["id"]]
            visibility_condition = f"({actor_activity_condition} OR {explicit_token_match})"
            visibility_params = [
                *actor_activity_params,
                *token_match_params,
            ]
            if token_uuid:
                selected_token_condition, selected_token_params = profile_api_audit_token_match_condition(
                    [str(token_uuid)],
                    [requested_token.get("name") or ""] if requested_token else [],
                )
                if category_filter == "mcp":
                    visibility_condition = f"({actor_activity_condition} OR {selected_token_condition})"
                else:
                    visibility_condition = f"({actor_activity_condition} AND {selected_token_condition})"
                visibility_params = [
                    *actor_activity_params,
                    *selected_token_params,
                ]
            base_conditions = [
                category_condition,
                visibility_condition,
            ]
            base_params_list: list[Any] = [
                *category_params,
                *visibility_params,
            ]
            conditions = list(base_conditions)
            params = list(base_params_list)
            if search_term:
                search_like = f"%{search_term}%"
                conditions.append(
                    """
                    (
                        event_type ILIKE %s
                        OR category ILIKE %s
                        OR COALESCE(summary, '') ILIKE %s
                        OR COALESCE(target_type, '') ILIKE %s
                        OR COALESCE(target_id, '') ILIKE %s
                        OR COALESCE(request_ip, '') ILIKE %s
                        OR COALESCE(user_agent, '') ILIKE %s
                        OR COALESCE(metadata->>'command', '') ILIKE %s
                        OR COALESCE(metadata->>'tool', '') ILIKE %s
                        OR COALESCE(metadata->>'endpoint', '') ILIKE %s
                        OR COALESCE(metadata->>'method', '') ILIKE %s
                        OR COALESCE(metadata->>'agent', '') ILIKE %s
                        OR COALESCE(metadata->>'apiTokenName', '') ILIKE %s
                        OR COALESCE(metadata->>'mcpPath', '') ILIKE %s
                        OR metadata::text ILIKE %s
                    )
                    """
                )
                params.extend([search_like] * 15)
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT COUNT(*)::int AS event_count
                    FROM audit_events
                    WHERE {' AND '.join(base_conditions)}
                    """,
                    tuple(base_params_list),
                )
                matching_before_search = int((cur.fetchone() or {}).get("event_count") or 0)
                cur.execute(
                    f"""
                    SELECT COUNT(*)::int AS event_count
                    FROM audit_events
                    WHERE {' AND '.join(conditions)}
                    """,
                    tuple(params),
                )
                matching_after_search = int((cur.fetchone() or {}).get("event_count") or 0)
                cur.execute(
                    f"""
                    SELECT COUNT(*)::int AS event_count
                    FROM audit_events
                    WHERE {category_condition}
                      AND actor_user_id=%s
                      AND (
                        category IN ('api', 'mcp')
                        OR event_type LIKE 'api.%%'
                        OR event_type LIKE 'mcp.%%'
                        OR event_type IN ('api_token.created', 'api_token.revoked')
                      )
                    """,
                    (*category_params, actor["id"]),
                )
                user_scoped_events = int((cur.fetchone() or {}).get("event_count") or 0)
                cur.execute(
                    f"""
                    SELECT id, event_type, category, actor_user_id, actor_username, actor_role,
                           target_type, target_id, summary, metadata, request_ip, user_agent, created_at
                    FROM audit_events
                    WHERE {' AND '.join(conditions)}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (*params, limit),
                )
                rows = cur.fetchall()
            diagnostics["counts"] = {
                "matchingBeforeSearch": matching_before_search,
                "matchingAfterSearch": matching_after_search,
                "returnedEvents": len(rows),
                "userScopedEvents": user_scoped_events,
            }
            if not rows:
                if search_term and matching_before_search > 0 and matching_after_search == 0:
                    diagnostics["reason"] = "search_filter_removed_all_matches"
                elif token_uuid:
                    diagnostics["reason"] = "selected_token_has_no_matching_activity"
                elif not visible_token_ids and user_scoped_events == 0:
                    diagnostics["reason"] = "no_tokens_and_no_user_scoped_activity"
                else:
                    diagnostics["reason"] = "no_matching_activity_for_current_filters"
        return response(
            {
                "status": "ok",
                "events": [audit_event_row(row) for row in rows],
                "tokenId": str(token_uuid) if token_uuid else "all",
                "category": category_filter,
                "query": search_term,
                "diagnostics": diagnostics,
            }
        )

    @flask_app.patch("/api/next/profile")
    def patch_next_profile():
        _app = _next_app()
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise NextApiError("Profile request body must be an object", 400)
        display_name = str(body.get("display_name") or body.get("displayName") or "").strip()
        if not display_name:
            raise NextApiError("display_name is required", 400)
        if len(display_name) > 120:
            raise NextApiError("display_name must be 120 characters or fewer", 400)

        with connect() as conn:
            user = next_auth_current_user(conn)
            if not user:
                raise NextApiError("Unauthorized", 401)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE users
                        SET display_name=%s, updated_at=now()
                        WHERE id=%s
                        RETURNING id, username, display_name, first_name, last_name, status, updated_at
                        """,
                        (display_name, user["id"]),
                    )
                    updated = cur.fetchone()
            if not updated:
                raise NextApiError("User not found", 404)
            updated["role"] = _app.next_user_primary_role(conn, updated["id"])
            return response(
                {
                    "status": "ok",
                    "user": next_profile_user_payload(conn, updated),
                }
            )

    @flask_app.post("/api/next/profile/avatar")
    def upload_next_profile_avatar():
        _app = _next_app()
        if request.content_length and request.content_length > _app.MAX_ARTWORK_UPLOAD_BYTES:
            raise NextApiError("Avatar upload may not exceed 20 MB", 413)
        upload = request.files.get("file") or request.files.get("avatar")
        if not upload or not upload.filename:
            raise NextApiError("file is required", 400)
        with connect() as conn:
            user = next_auth_current_user(conn)
            if not user:
                raise NextApiError("Unauthorized", 401)
            upload_info = _app.save_uploaded_profile_avatar_file(upload)
            with conn.transaction():
                media = _app.create_uploaded_profile_avatar_asset(conn, upload_info=upload_info, actor=user)
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE users
                        SET avatar_asset_id=%s, updated_at=now()
                        WHERE id=%s
                        """,
                        (media["id"], user["id"]),
                    )
            user_payload = next_profile_user_payload(conn, user)
        return response({"status": "ok", "user": user_payload, "mediaAsset": media})

    @flask_app.delete("/api/next/profile/avatar")
    def delete_next_profile_avatar():
        with connect() as conn:
            user = next_auth_current_user(conn)
            if not user:
                raise NextApiError("Unauthorized", 401)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE users
                        SET avatar_asset_id=NULL, updated_at=now()
                        WHERE id=%s
                        """,
                        (user["id"],),
                    )
            user_payload = next_profile_user_payload(conn, user)
        return response({"status": "deleted", "user": user_payload})

    @flask_app.get("/api/next/profile/recovery")
    def get_next_profile_recovery():
        with connect() as conn:
            user = next_auth_current_user(conn)
            if not user:
                raise NextApiError("Unauthorized", 401)
            return response({"status": "ok", "recovery": next_profile_recovery_payload(conn, user["id"])})

    @flask_app.post("/api/next/profile/recovery/codes")
    def generate_next_profile_recovery_codes():
        body = request.get_json(silent=True) or {}
        if body and not isinstance(body, dict):
            raise NextApiError("Recovery request body must be an object", 400)
        try:
            count = int(body.get("count") or 8)
        except (TypeError, ValueError) as exc:
            raise NextApiError("count must be an integer", 400) from exc
        count = max(1, min(12, count))
        with connect() as conn:
            user = next_auth_current_user(conn)
            if not user:
                raise NextApiError("Unauthorized", 401)
            if not table_exists(conn, "recovery_codes"):
                raise NextApiError("Recovery is not available yet", 503)
            codes = next_generate_recovery_codes(count)
            with conn.transaction():
                with conn.cursor() as cur:
                    next_replace_recovery_codes(cur, user["id"], codes)
            return response(
                {
                    "status": "ok",
                    "codes": codes,
                    "recovery": next_profile_recovery_payload(conn, user["id"]),
                }
            )

    @flask_app.delete("/api/next/profile/recovery/codes")
    def revoke_next_profile_recovery_codes():
        with connect() as conn:
            user = next_auth_current_user(conn)
            if not user:
                raise NextApiError("Unauthorized", 401)
            if not table_exists(conn, "recovery_codes"):
                raise NextApiError("Recovery is not available yet", 503)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE recovery_codes
                        SET used_at=COALESCE(used_at, now())
                        WHERE user_id=%s
                          AND used_at IS NULL
                        """,
                        (user["id"],),
                    )
            return response({"status": "deleted", "recovery": next_profile_recovery_payload(conn, user["id"])})

    @flask_app.patch("/api/next/profile/passkeys/<credential_id>")
    def patch_next_profile_passkey(credential_id: str):
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise NextApiError("Passkey request body must be an object", 400)
        credential_name = str(body.get("credential_name") or body.get("credentialName") or "").strip()
        if not credential_name:
            raise NextApiError("credential_name is required", 400)
        if len(credential_name) > 80:
            raise NextApiError("credential_name must be 80 characters or fewer", 400)

        with connect() as conn:
            user = next_auth_current_user(conn)
            if not user:
                raise NextApiError("Unauthorized", 401)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE passkey_credentials
                        SET credential_name=%s
                        WHERE id=%s AND user_id=%s
                        RETURNING id, credential_name, created_at, last_used_at, sign_count
                        """,
                        (credential_name, credential_id, user["id"]),
                    )
                    credential = cur.fetchone()
                    if not credential:
                        raise NextApiError("Passkey not found", 404)
                    cur.execute(
                        """
                        SELECT id, credential_name, created_at, last_used_at, sign_count
                        FROM passkey_credentials
                        WHERE user_id=%s
                        ORDER BY created_at DESC
                        """,
                        (user["id"],),
                    )
                    credentials = cur.fetchall()
        return response({"status": "ok", "credential": credential, "credentials": credentials})

    @flask_app.delete("/api/next/profile/passkeys/<credential_id>")
    def delete_next_profile_passkey(credential_id: str):
        with connect() as conn:
            user = next_auth_current_user(conn)
            if not user:
                raise NextApiError("Unauthorized", 401)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) AS count FROM passkey_credentials WHERE user_id=%s",
                        (user["id"],),
                    )
                    owned_count = int(cur.fetchone()["count"])
                    if owned_count <= 1:
                        raise NextApiError("You cannot delete your last passkey from your profile", 400)
                    cur.execute(
                        "DELETE FROM passkey_credentials WHERE id=%s AND user_id=%s RETURNING id",
                        (credential_id, user["id"]),
                    )
                    deleted = cur.fetchone()
                    if not deleted:
                        raise NextApiError("Passkey not found", 404)
                    cur.execute(
                        """
                        SELECT id, credential_name, created_at, last_used_at, sign_count
                        FROM passkey_credentials
                        WHERE user_id=%s
                        ORDER BY created_at DESC
                        """,
                        (user["id"],),
                    )
                    credentials = cur.fetchall()
        return response({"status": "deleted", "credentials": credentials})
