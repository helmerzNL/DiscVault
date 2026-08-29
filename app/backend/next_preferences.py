"""Preferences and mobile bootstrap helpers for DiscVault Next backend."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import requests
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
    "accent": "bluray",
    "profile_menu_style": "icon_text",
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
    # Off, so the shipped behaviour is unchanged: deleting a disc does not
    # un-watch the film, and watch history records something that happened.
    # On, for the reader who means "gone" literally -- see
    # personal-lists-on-deletion.md 2b (#719).
    "delete_removes_watch_history": False,
    "show_metadata_jobs": True,
    "price_monitoring_enabled": True,
    "preferred_price_currency": "",
    "rating_country": "NL",
    "default_media_group_id": "",
    # Off by default, and gated again by the owner setting
    # `movievault_v2_contribution_enabled`: a user cannot turn on sharing that
    # the instance owner has not allowed. See release_contribution_enabled().
    "share_release_selections": False,
    # A second preference rather than a reuse of the one above. Offering a disc
    # you scanned and editing a record everyone else reads are different acts
    # with different consequences; one checkbox for both would mean consenting
    # to the second by having agreed to the first. Gated again by the same
    # owner setting -- see field_correction_enabled().
    "share_field_corrections": False,
    # Whether the confirmation sheet keeps its full explanation after the first
    # time. The diff itself is always shown -- contribution-2 requires explicit
    # field confirmation -- so this governs the wording, not the confirmation.
    "confirm_every_upload": False,
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
    "delete_removes_watch_history",
    "show_metadata_jobs",
    "price_monitoring_enabled",
    "share_release_selections",
    "share_field_corrections",
    "confirm_every_upload",
}
APP_CHOICE_PREFERENCES = {
    "theme": {"system", "light", "dark"},
    "accent": {"bluray", "amethyst", "chrome", "emerald", "teal", "crimson", "magenta", "ember"},
    "profile_menu_style": {"icon_text", "icon_only"},
    "preferred_price_currency": {"", "EUR", "USD", "GBP", "CAD", "AUD", "CHF", "JPY"},
    "rating_country": {"NL", "DE", "FR", "ES", "PT", "IT", "US", "GB", "CA", "PL", "CZ", "HU", "RO", "BG", "GR", "UA", "EE", "LT", "TR", "JP", "TW", "KR"},
}
APP_PREFERENCE_SECTIONS: dict[str, tuple[str, ...]] = {
    "appearance": ("theme", "accent", "profile_menu_style"),
    "library": (
        "show_featured_hero",
        "show_collection_search",
        "show_auto_videos",
        "show_local_title",
        "show_extended_people_pages",
        "show_digital_badge_on_tiles",
        "price_monitoring_enabled",
        "preferred_price_currency",
        "rating_country",
        "default_media_group_id",
        # On Library rather than Collectors for the same reason the sharing
        # preferences are: the Collectors tab is hidden unless the user holds
        # container-management permissions, and what happens to your own watch
        # history is not a container capability.
        "delete_removes_watch_history",
    ),
    "collectors": (
        "collectors_mode",
        "merge_editions_as_title",
        "show_container_format_badges",
        "show_container_member_badges",
        "delete_container_members_with_container",
        "show_metadata_jobs",
    ),
    # Its own section rather than an entry under "library": this is the only
    # preference that sends anything outside the instance, and burying that
    # among display toggles would be the wrong kind of quiet.
    "sharing": (
        "share_release_selections",
        "share_field_corrections",
        "confirm_every_upload",
    ),
}

PRICE_DISPLAY_SUPPORTED_CURRENCIES: tuple[str, ...] = ("EUR", "USD", "GBP", "CAD", "AUD", "CHF", "JPY")
PRICE_DISPLAY_FALLBACK_RATES: dict[str, float] = {
    "EUR": 1.0,
    "USD": 1.08,
    "GBP": 0.86,
    "CAD": 1.47,
    "AUD": 1.66,
    "CHF": 0.94,
    "JPY": 173.0,
}
_PRICE_DISPLAY_RATE_CACHE: dict[str, Any] = {"expires_at": None, "payload": None}


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
        if key == "profile_menu_style":
            if not isinstance(value, str):
                raise NextApiError(f"Invalid value for preference {key}", 400)
            text = value.strip()
        else:
            text = str(value or APP_PREFERENCE_DEFAULTS[key]).strip()
        text = text.upper() if key in {"rating_country", "preferred_price_currency"} else text.lower()
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


def price_display_exchange_rates(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    cached = _PRICE_DISPLAY_RATE_CACHE.get("payload")
    expires_at = _PRICE_DISPLAY_RATE_CACHE.get("expires_at")
    if cached and isinstance(expires_at, datetime) and expires_at > now:
        return cached

    symbols = ",".join(code for code in PRICE_DISPLAY_SUPPORTED_CURRENCIES if code != "EUR")
    try:
        response = requests.get(
            f"https://api.frankfurter.app/latest?from=EUR&to={symbols}",
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json() or {}
        raw_rates = payload.get("rates") if isinstance(payload, dict) else {}
        rates = {"EUR": 1.0}
        for code in PRICE_DISPLAY_SUPPORTED_CURRENCIES:
            if code == "EUR":
                continue
            raw_value = raw_rates.get(code) if isinstance(raw_rates, dict) else None
            try:
                rates[code] = float(raw_value)
            except (TypeError, ValueError):
                rates[code] = PRICE_DISPLAY_FALLBACK_RATES[code]
        result = {
            "base": "EUR",
            "exchangeRates": rates,
            "updatedAt": payload.get("date") if isinstance(payload, dict) else None,
            "source": "frankfurter",
        }
        _PRICE_DISPLAY_RATE_CACHE["payload"] = result
        _PRICE_DISPLAY_RATE_CACHE["expires_at"] = now + timedelta(hours=12)
        return result
    except Exception:
        if cached:
            return cached
        return {
            "base": "EUR",
            "exchangeRates": dict(PRICE_DISPLAY_FALLBACK_RATES),
            "updatedAt": None,
            "source": "fallback",
        }


def price_display_context(preferences: dict[str, Any] | None = None) -> dict[str, Any]:
    prefs = preferences or {}
    monitoring_enabled = bool(prefs.get("price_monitoring_enabled", APP_PREFERENCE_DEFAULTS["price_monitoring_enabled"]))
    preferred_currency = str(prefs.get("preferred_price_currency") or "").strip().upper()
    if preferred_currency not in APP_CHOICE_PREFERENCES["preferred_price_currency"]:
        preferred_currency = ""
    payload = {
        "monitoringEnabled": monitoring_enabled,
        "preferredCurrency": preferred_currency or None,
        "supportedCurrencies": list(PRICE_DISPLAY_SUPPORTED_CURRENCIES),
        "exchangeRates": {},
        "updatedAt": None,
        "source": None,
    }
    if monitoring_enabled and preferred_currency:
        rates = price_display_exchange_rates()
        payload["exchangeRates"] = dict(rates.get("exchangeRates") or {})
        payload["updatedAt"] = rates.get("updatedAt")
        payload["source"] = rates.get("source")
    return payload


def actor_delete_container_members_enabled(conn, actor: dict[str, Any] | None) -> bool:
    actor_id = (actor or {}).get("id")
    if not actor_id:
        return bool(APP_PREFERENCE_DEFAULTS["delete_container_members_with_container"])
    return bool(app_effective_preferences(conn, actor_id).get("delete_container_members_with_container"))


def user_delete_removes_watch_history(conn, user_id: Any) -> bool:
    """Whether this user's watch history goes when a movie is deleted.

    Read per *user*, not per actor, and that distinction is the whole point:
    one person's delete reaches every user's lists, but only the owner of a
    history may decide whether it is history. An admin who wants deletes to be
    absolute gets that for their own entries; someone else's stay.
    """
    if not user_id:
        return bool(APP_PREFERENCE_DEFAULTS["delete_removes_watch_history"])
    return bool(app_effective_preferences(conn, user_id).get("delete_removes_watch_history"))


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
        # Physical storage locations. `assign` is deliberately the only write:
        # sync-contract §4c.2 keeps creating, renaming and moving the tree a PWA
        # action, because the `locations` table has no per-record client id and
        # a create path without identity-ladder rung 1 produces duplicates.
        "locations": {
            "view": has_any("containers.view", "collection.view", "collection.view_all", "collection.view_group"),
            "assign": has_any("collection.edit_all", "collection.edit_own", "collection.edit_group", "containers.edit"),
            "manage": False,
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
            "reconcile": "/api/next/sync/reconcile",
            "userBootstrap": "/api/next/sync/user/bootstrap",
            "userDelta": "/api/next/sync/user/delta",
        },
        # Read-only on purpose (sync-contract §4c.2). The tree arrives in the
        # sync bootstrap's `locations` array; this route is for a client that
        # wants it on its own, and `open` is what a scanned QR resolves to.
        "locations": {
            "list": "/api/next/locations",
            "open": "/open/locations/{locationPublicId}",
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
