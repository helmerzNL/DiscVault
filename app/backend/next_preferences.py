"""Preferences and mobile bootstrap helpers for DiscVault Next backend."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from flask import Flask, request
from psycopg.types.json import Jsonb

try:  # pragma: no cover - exercised indirectly by both layouts
    from .next_api_token import profile_api_access_payload
    from .next_auth import next_auth_current_user, next_auth_effective_enabled
    from .next_common import NextApiError, json_ready, parse_bool_value, parse_uuid, response, table_exists
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_api_token import profile_api_access_payload
    from next_auth import next_auth_current_user, next_auth_effective_enabled
    from next_common import NextApiError, json_ready, parse_bool_value, parse_uuid, response, table_exists


APP_PREFERENCE_DEFAULTS: dict[str, Any] = {
    "theme": "system",
    "show_featured_hero": True,
    "show_collection_search": True,
    "show_auto_videos": True,
    "show_local_title": True,
    "show_extended_people_pages": False,
    "collectors_mode": False,
    "merge_editions_as_title": False,
    "show_container_format_badges": True,
    "show_container_member_badges": True,
    "show_digital_badge_on_tiles": True,
    "delete_container_members_with_container": False,
    "show_metadata_jobs": True,
    "rating_country": "NL",
    "default_media_group_id": "",
}
APP_PREFERENCE_ALIASES = {
    "show_search_button": "show_collection_search",
}
APP_BOOLEAN_PREFERENCES = {
    "show_featured_hero",
    "show_collection_search",
    "show_auto_videos",
    "show_local_title",
    "show_extended_people_pages",
    "collectors_mode",
    "merge_editions_as_title",
    "show_container_format_badges",
    "show_container_member_badges",
    "show_digital_badge_on_tiles",
    "delete_container_members_with_container",
    "show_metadata_jobs",
}
APP_CHOICE_PREFERENCES = {
    "theme": {"system", "light", "dark"},
    "rating_country": {"NL", "DE", "FR", "ES", "PT", "IT", "US", "GB", "CA", "PL", "CZ", "HU", "RO", "BG", "GR", "UA", "EE", "LT", "TR", "JP", "TW", "KR"},
}
APP_PREFERENCE_SECTIONS: dict[str, tuple[str, ...]] = {
    "appearance": ("theme",),
    "library": (
        "show_featured_hero",
        "show_collection_search",
        "show_auto_videos",
        "show_local_title",
        "show_extended_people_pages",
        "show_digital_badge_on_tiles",
        "rating_country",
        "default_media_group_id",
    ),
    "collectors": (
        "collectors_mode",
        "merge_editions_as_title",
        "show_container_format_badges",
        "show_container_member_badges",
        "delete_container_members_with_container",
        "show_metadata_jobs",
    ),
}


def _next_app():
    try:  # pragma: no cover - import shape depends on runtime layout
        from . import next_app
    except ImportError:  # pragma: no cover - supports gunicorn next_app:app
        import next_app
    return next_app


def normalized_app_preference_key(key: Any) -> str:
    text = str(key or "").strip()
    return APP_PREFERENCE_ALIASES.get(text, text)


def validate_app_preference(key: str, value: Any) -> Any:
    key = normalized_app_preference_key(key)
    if key not in APP_PREFERENCE_DEFAULTS:
        raise NextApiError(f"Unknown preference: {key}", 400)
    if key in APP_BOOLEAN_PREFERENCES:
        return parse_bool_value(value, default=bool(APP_PREFERENCE_DEFAULTS[key]))
    if key in APP_CHOICE_PREFERENCES:
        text = str(value or APP_PREFERENCE_DEFAULTS[key]).strip()
        text = text.upper() if key == "rating_country" else text.lower()
        if text not in APP_CHOICE_PREFERENCES[key]:
            raise NextApiError(f"Invalid value for preference {key}", 400)
        return text
    if key == "default_media_group_id":
        text = str(value or "").strip()
        if text:
            parse_uuid(text, "defaultMediaGroupId")
        return text
    return value


def app_global_preferences(conn) -> dict[str, Any]:
    values = dict(APP_PREFERENCE_DEFAULTS)
    settings = _next_app().app_settings_map(conn)
    for key in APP_PREFERENCE_DEFAULTS:
        if key in settings:
            values[key] = validate_app_preference(key, settings[key])
    if "show_search_button" in settings and "show_collection_search" not in settings:
        values["show_collection_search"] = validate_app_preference("show_collection_search", settings["show_search_button"])
    return values


def app_user_preferences(conn, user_id: UUID | str | None) -> dict[str, Any]:
    if not user_id or not table_exists(conn, "user_preferences"):
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT key, value
            FROM user_preferences
            WHERE user_id=%s
            """,
            (user_id,),
        )
        rows = cur.fetchall()
    values: dict[str, Any] = {}
    for row in rows:
        key = normalized_app_preference_key(row["key"])
        if key not in APP_PREFERENCE_DEFAULTS:
            continue
        values[key] = validate_app_preference(key, row["value"])
    return values


def app_effective_preferences(conn, user_id: UUID | str | None = None) -> dict[str, Any]:
    values = app_global_preferences(conn)
    values.update(app_user_preferences(conn, user_id))
    return values


def actor_delete_container_members_enabled(conn, actor: dict[str, Any] | None) -> bool:
    actor_id = (actor or {}).get("id")
    if not actor_id:
        return bool(APP_PREFERENCE_DEFAULTS["delete_container_members_with_container"])
    return bool(app_effective_preferences(conn, actor_id).get("delete_container_members_with_container"))


def set_app_user_preferences(conn, user_id: UUID | str, updates: dict[str, Any]) -> dict[str, Any]:
    if not table_exists(conn, "user_preferences"):
        raise NextApiError("User preferences table is not available", 503)
    normalized: dict[str, Any] = {}
    for raw_key, raw_value in updates.items():
        key = normalized_app_preference_key(raw_key)
        normalized[key] = validate_app_preference(key, raw_value)
    with conn.cursor() as cur:
        for key, value in normalized.items():
            cur.execute(
                """
                INSERT INTO user_preferences (user_id, key, value, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (user_id, key) DO UPDATE SET
                    value=EXCLUDED.value,
                    updated_at=now()
                """,
                (user_id, key, Jsonb(json_ready(value))),
            )
    return app_effective_preferences(conn, user_id)


def app_preference_sections_payload(preferences: dict[str, Any]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for key, preference_keys in APP_PREFERENCE_SECTIONS.items():
        sections.append(
            {
                "key": key,
                "preferences": [
                    {
                        "key": preference_key,
                        "value": preferences.get(preference_key, APP_PREFERENCE_DEFAULTS.get(preference_key)),
                        "default": APP_PREFERENCE_DEFAULTS.get(preference_key),
                        "type": "boolean"
                        if preference_key in APP_BOOLEAN_PREFERENCES
                        else "choice"
                        if preference_key in APP_CHOICE_PREFERENCES
                        else "string",
                        "choices": sorted(APP_CHOICE_PREFERENCES.get(preference_key, [])),
                    }
                    for preference_key in preference_keys
                    if preference_key in APP_PREFERENCE_DEFAULTS
                ],
            }
        )
    return sections


def mobile_feature_capabilities(conn, actor: dict[str, Any]) -> dict[str, Any]:
    permissions = _next_app().actor_effective_permission_keys(conn, actor)

    def has_any(*keys: str) -> bool:
        return "*" in permissions or bool(permissions.intersection(keys))

    return {
        "collection": {
            "view": has_any("collection.view", "collection.view_all", "collection.view_own", "collection.view_group"),
            "viewAll": has_any("collection.view_all"),
            "viewOwn": has_any("collection.view_own", "collection.view"),
            "viewGroups": has_any("collection.view_group", "collection.view", "groups.view", "groups.view_all"),
            "add": has_any("collection.add", "collection.add_own", "collection.import", "collection.edit_all"),
            "addOwn": has_any("collection.add_own", "collection.add"),
            "import": has_any("collection.import"),
            "edit": has_any("collection.edit_all", "collection.edit_own", "collection.edit_group"),
            "delete": has_any("collection.delete_all", "collection.delete_own", "collection.delete_group"),
            "bulkEdit": has_any("collection.bulk_edit", "collection.edit_all"),
        },
        "metadata": {
            "search": has_any("metadata.search", "collection.import", "collection.add", "collection.add_own", "collection.edit_all"),
            "refreshOne": has_any("metadata.refresh_one"),
            "refreshBulk": has_any("metadata.refresh_bulk"),
            "viewJobs": has_any("admin.view_jobs", "metadata.refresh_one", "metadata.refresh_bulk"),
            "plugins": has_any("metadata.manage_plugins", "metadata.view_plugin_health", "metadata.manage_plugin_settings"),
        },
        "import": {
            "barcodeScanner": has_any("metadata.search", "collection.add", "collection.add_own", "collection.import"),
            "manualSearch": has_any("metadata.search", "collection.add", "collection.add_own", "collection.import"),
            "boxSetDetection": has_any("metadata.search", "collection.add", "collection.import", "containers.create"),
            "fileImport": has_any("collection.import"),
        },
        "containers": {
            "view": has_any("containers.view", "collection.view", "collection.view_all", "collection.view_group"),
            "create": has_any("containers.create", "collection.import"),
            "edit": has_any("containers.edit"),
            "delete": has_any("containers.delete"),
        },
        "groups": {
            "view": has_any("groups.view", "groups.view_all", "groups.manage", "users.view"),
            "create": has_any("groups.create", "groups.manage"),
            "invite": has_any("groups.invite", "groups.manage"),
            "manage": has_any("groups.manage"),
        },
        "personal": {
            "watchlist": has_any("watchlist.manage"),
            "statistics": has_any("watchlist.manage"),
            "wishlistPriceTrendStats": has_any("watchlist.manage"),
            "notifications": True,
            "push": True,
            "loanRequests": has_any("lending.request"),
            "manageLoansSystem": has_any("security.manage_loans_system"),
        },
        "api": {
            "read": has_any("api.read"),
            "write": has_any("api.write"),
            "tokensManage": has_any("api.tokens.manage"),
            "mcp": has_any("mcp.use") or any(key.startswith("mcp.tool.") for key in permissions),
        },
        "offline": {
            "readCache": True,
            "queuedMutations": True,
            "syncDelta": True,
            "conflictDetection": True,
        },
    }


def mobile_endpoint_contract_payload() -> dict[str, Any]:
    return {
        "auth": {
            "start": "/api/next/auth/mobile/start",
            "exchange": "/api/next/auth/mobile/exchange",
            "status": "/api/next/auth/status",
        },
        "bootstrap": "/api/next/mobile/bootstrap",
        "profile": "/api/next/profile",
        "preferences": "/api/next/preferences",
        "notifications": "/api/next/notifications",
        "push": {
            "status": "/api/next/push/status",
            "preferences": "/api/next/push/preferences",
        },
        "sync": {
            "state": "/api/next/sync/state",
            "bootstrap": "/api/next/sync/bootstrap",
            "delta": "/api/next/sync/delta",
            "mutations": "/api/next/sync/mutations",
        },
        "import": {
            "metadataLookup": "/api/next/metadata/lookup",
            "importMovie": "/api/next/import/movie",
        },
        "metadata": {
            "refreshMovie": "/api/next/movies/{movieId}/metadata/refresh",
            "refreshContainer": "/api/next/containers/{containerId}/metadata/refresh",
            "jobs": "/api/next/metadata/jobs",
        },
        "loans": {
            "list": "/api/next/loans",
            "borrowed": "/api/next/loans/borrowed",
            "return": "/api/next/loans/{loanId}/return",
        },
        "loanRequests": {
            "create": "/api/next/movies/{movieId}/loan-requests",
            "list": "/api/next/loan-requests",
            "approve": "/api/next/loan-requests/{loanRequestId}/approve",
            "decline": "/api/next/loan-requests/{loanRequestId}/decline",
            "cancel": "/api/next/loan-requests/{loanRequestId}/cancel",
        },
        "stats": {
            "personal": "/api/next/stats/personal",
            "wishlistPriceTrendField": "wishlistPriceTrend",
        },
    }


def register_next_preferences_routes(flask_app: Flask, *, connect) -> None:  # pragma: no cover - Flask integration
    @flask_app.get("/api/next/mobile/bootstrap")
    def next_mobile_bootstrap():
        _app = _next_app()
        with connect() as conn:
            actor = _app.require_next_authenticated_user(conn)
            user_id = actor.get("id")
            if not user_id:
                raise NextApiError("Mobile bootstrap requires a signed-in user", 401)
            preferences = app_effective_preferences(conn, user_id)
            token_permissions = _app.actor_api_token_permission_keys(actor)
            effective_permissions = sorted(_app.actor_effective_permission_keys(conn, actor))
            push_public_key = None
            push_subject = None
            if table_exists(conn, "push_subscriptions"):
                push_public_key, _ = _app.get_or_create_push_vapid_keys(conn)
                push_subject = _app.push_vapid_subject()
            return response(
                {
                    "status": "ok",
                    "mobile": {
                        "contractVersion": "2026-07-08.1",
                        "platform": "ios",
                        "recommendedClient": {
                            "offlineFirst": True,
                            "authFlow": "ASWebAuthenticationSession + PKCE",
                            "barcodeImport": "metadata lookup first, then import selected movie or box-set candidate",
                        },
                    },
                    "app": {
                        "name": "DiscVault",
                        "productName": "DiscVault 26",
                        "version": _app.build_version(),
                        "serverTime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    },
                    "auth": {
                        "authenticated": True,
                        "role": actor.get("role"),
                        "tokenPermissionKeys": sorted(token_permissions) if token_permissions is not None else [],
                        "effectivePermissionKeys": effective_permissions,
                    },
                    "user": _app.next_profile_user_payload(conn, actor),
                    "capabilities": mobile_feature_capabilities(conn, actor),
                    "preferences": {
                        "values": preferences,
                        "defaults": APP_PREFERENCE_DEFAULTS,
                        "sections": app_preference_sections_payload(preferences),
                        "ratingCountries": sorted(APP_CHOICE_PREFERENCES.get("rating_country", [])),
                    },
                    "notifications": {
                        "counts": _app.notification_counts(conn, user_id),
                        "preferences": _app.notification_preference_map(conn, user_id),
                        "preferenceDefaults": _app.NOTIFICATION_PREF_DEFAULTS,
                        "webPush": {
                            "available": bool(push_public_key),
                            "publicKey": push_public_key,
                            "subject": push_subject,
                        },
                        "nativePush": _app.native_push_status_payload(conn, user_id),
                    },
                    "localization": {
                        "sourceLocale": _app.NEXT_I18N_SOURCE_LOCALE,
                        "defaultLocale": _app.NEXT_I18N_DEFAULT_LOCALE,
                        "locales": _app.supported_next_locales(),
                    },
                    "apiAccess": profile_api_access_payload(conn, actor),
                    "instanceSettings": {
                        "loansSystemEnabled": _app.loans_system_enabled(conn),
                    },
                    "endpoints": mobile_endpoint_contract_payload(),
                }
            )

    @flask_app.get("/api/next/preferences")
    def get_app_preferences():
        with connect() as conn:
            user = next_auth_current_user(conn) if next_auth_effective_enabled(conn, table_exists) else None
            return response(
                {
                    "status": "ok",
                    "preferences": app_effective_preferences(conn, user.get("id") if user else None),
                    "defaults": APP_PREFERENCE_DEFAULTS,
                    "userScoped": bool(user),
                }
            )

    @flask_app.patch("/api/next/preferences")
    def patch_app_preferences():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise NextApiError("Preferences request body must be an object", 400)
        updates = body.get("preferences", body)
        if not isinstance(updates, dict):
            raise NextApiError("preferences must be an object", 400)
        with connect() as conn:
            user = next_auth_current_user(conn) if next_auth_effective_enabled(conn, table_exists) else None
            if next_auth_effective_enabled(conn, table_exists) and not user:
                raise NextApiError("Unauthorized", 401)
            if not user:
                return response(
                    {
                        "status": "ok",
                        "preferences": app_effective_preferences(conn),
                        "userScoped": False,
                        "updated": False,
                    }
                )
            with conn.transaction():
                preferences = set_app_user_preferences(conn, user["id"], updates)
        return response({"status": "ok", "preferences": preferences, "userScoped": True, "updated": True})
