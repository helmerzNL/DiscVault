"""Push notifications and PWA route helpers for DiscVault Next backend."""

from __future__ import annotations

import base64
import hashlib
import json as json_lib
import mimetypes
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from uuid import UUID

from flask import Flask, jsonify, request, send_file
from psycopg.types.json import Jsonb
import jwt

try:  # pragma: no cover - exercised indirectly by both layouts
    from .next_audit import audit_event
    from .next_common import NextApiError, json_ready, parse_bool_value, parse_uuid, response, table_exists
    from .next_import import clean_text
    from .next_notifications import (
        NOTIFICATION_PREF_DEFAULTS,
        create_user_notification,
        notification_counts,
        notification_preference_map,
    )
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_audit import audit_event
    from next_common import NextApiError, json_ready, parse_bool_value, parse_uuid, response, table_exists
    from next_import import clean_text
    from next_notifications import (
        NOTIFICATION_PREF_DEFAULTS,
        create_user_notification,
        notification_counts,
        notification_preference_map,
    )


PWA_ICON_ASSETS = {
    "apple-touch-icon-152.png",
    "apple-touch-icon-167.png",
    "apple-touch-icon.png",
    "favicon-32.png",
    "favicon-192.png",
    "favicon-512.png",
    "header-logo.svg",
    "icon.svg",
    "logo.svg",
    "pwa-icon-192.png",
    "pwa-icon-512.png",
    "pwa-maskable-192.png",
    "pwa-maskable-512.png",
}


def _next_app():
    try:  # pragma: no cover - import shape depends on runtime layout
        from . import next_app
    except ImportError:  # pragma: no cover - supports gunicorn next_app:app
        import next_app
    return next_app


def pwa_manifest_payload(asset_prefix: str = "/api/next/assets", start_url: str = "/") -> dict[str, Any]:
    assets = asset_prefix.rstrip("/")
    return {
        "id": "/",
        "name": "DiscVault",
        "short_name": "DiscVault",
        "description": "4K UHD Blu-ray Collection Manager",
        "lang": "nl-NL",
        "dir": "ltr",
        "start_url": start_url,
        "scope": "/",
        "display": "standalone",
        "display_override": ["window-controls-overlay", "standalone", "minimal-ui"],
        "orientation": "any",
        "background_color": "#0a0a0f",
        "theme_color": "#111214",
        "categories": ["entertainment", "lifestyle", "productivity"],
        "prefer_related_applications": False,
        "launch_handler": {"client_mode": ["navigate-existing", "auto"]},
        "icons": [
            {
                "src": f"{assets}/pwa-icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": f"{assets}/pwa-icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": f"{assets}/pwa-maskable-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "maskable",
            },
            {
                "src": f"{assets}/pwa-maskable-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
        "shortcuts": [
            {
                "name": "Collectie",
                "short_name": "Collectie",
                "url": "/",
                "icons": [{"src": f"{assets}/pwa-icon-192.png", "sizes": "192x192", "type": "image/png"}],
            },
            {
                "name": "Toevoegen",
                "short_name": "Toevoegen",
                "url": "/import",
                "icons": [{"src": f"{assets}/pwa-icon-192.png", "sizes": "192x192", "type": "image/png"}],
            },
            {
                "name": "Profiel",
                "short_name": "Profiel",
                "url": "/profile",
                "icons": [{"src": f"{assets}/pwa-icon-192.png", "sizes": "192x192", "type": "image/png"}],
            },
        ],
    }


def pwa_head_tags(asset_prefix: str = "/api/next/assets", manifest_href: str = "/manifest.json") -> str:
    assets = asset_prefix.rstrip("/")
    return f"""
  <meta name="application-name" content="DiscVault">
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-title" content="DiscVault">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="format-detection" content="telephone=no">
  <meta name="theme-color" content="#f4f5f7" media="(prefers-color-scheme: light)">
  <meta name="theme-color" content="#111214" media="(prefers-color-scheme: dark)">
  <meta name="msapplication-TileColor" content="#111214">
  <meta name="msapplication-TileImage" content="{assets}/pwa-icon-192.png">
  <link rel="manifest" href="{manifest_href}">
  <link rel="apple-touch-icon" sizes="152x152" href="{assets}/apple-touch-icon-152.png">
  <link rel="apple-touch-icon" sizes="167x167" href="{assets}/apple-touch-icon-167.png">
  <link rel="apple-touch-icon" sizes="180x180" href="{assets}/apple-touch-icon.png">
  <link rel="icon" type="image/png" sizes="32x32" href="{assets}/favicon-32.png">
  <link rel="icon" type="image/png" sizes="192x192" href="{assets}/pwa-icon-192.png">
  <link rel="icon" type="image/png" sizes="512x512" href="{assets}/pwa-icon-512.png">
  <link rel="mask-icon" href="{assets}/icon.svg" color="#e8c547">""".rstrip()

def push_vapid_subject() -> str:
    return os.environ.get("VAPID_SUBJECT") or os.environ.get("DISCVAULT_VAPID_SUBJECT") or "mailto:no-reply@discvault.app"

def get_or_create_push_vapid_keys(conn) -> tuple[str, str]:
    _app = _next_app()
    public_key = _app.app_setting_value(conn, "vapid_public_key", include_secret=False)
    private_key = _app.app_setting_value(conn, "vapid_private_key", include_secret=True)
    if public_key and private_key:
        return str(public_key), str(private_key)
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    private = ec.generate_private_key(ec.SECP256R1(), default_backend())
    public_bytes = private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    public_key = base64.urlsafe_b64encode(public_bytes).rstrip(b"=").decode("ascii")
    private_raw = private.private_numbers().private_value.to_bytes(32, "big")
    private_key = base64.urlsafe_b64encode(private_raw).rstrip(b"=").decode("ascii")
    _app.set_app_setting_value(conn, "vapid_public_key", public_key, is_secret=False)
    _app.set_app_setting_value(conn, "vapid_private_key", private_key, is_secret=True)
    return public_key, private_key

def send_push_to_user(conn, user_id: UUID | str, *, title: str, body: str = "", url: str = "/notifications") -> list[str]:
    if not table_exists(conn, "push_subscriptions"):
        return ["Push subscriptions table is not available"]
    public_key, private_key = get_or_create_push_vapid_keys(conn)
    del public_key
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, endpoint, p256dh, auth
            FROM push_subscriptions
            WHERE user_id=%s
            ORDER BY is_primary DESC, updated_at DESC
            """,
            (user_id,),
        )
        subscriptions = cur.fetchall()
    if not subscriptions:
        return ["No push subscription found for this user"]
    try:
        from pywebpush import webpush
    except Exception as exc:
        return [f"pywebpush is not available: {exc}"]
    errors: list[str] = []
    stale_endpoints: list[str] = []
    for subscription in subscriptions:
        endpoint = str(subscription.get("endpoint") or "")
        try:
            headers = None
            endpoint_host = urlparse(endpoint).netloc.casefold()
            if endpoint_host in {"notify.windows.com", "wns.windows.com"}:
                headers = {"X-WNS-Type": "wns/raw", "Content-Type": "application/octet-stream"}
            webpush(
                subscription_info={
                    "endpoint": endpoint,
                    "keys": {"p256dh": subscription.get("p256dh"), "auth": subscription.get("auth")},
                },
                data=json_lib.dumps({"title": title, "body": body, "url": url}),
                vapid_private_key=private_key,
                vapid_claims={"sub": push_vapid_subject()},
                ttl=86400,
                headers=headers,
            )
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            errors.append(f"Push failed ({status or 'unknown'}): {exc}")
            if status in {404, 410} and endpoint:
                stale_endpoints.append(endpoint)
    if stale_endpoints:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM push_subscriptions WHERE endpoint = ANY(%s)", (stale_endpoints,))
    return errors

def native_push_environment(value: Any = None) -> str:
    text = str(value or os.environ.get("APNS_ENVIRONMENT") or "production").strip().lower()
    if text in {"dev", "development", "sandbox"}:
        return "sandbox"
    return "production"

def native_push_private_key() -> str:
    inline_key = str(os.environ.get("APNS_PRIVATE_KEY") or "").strip()
    if inline_key:
        return inline_key.replace("\\n", "\n")
    private_key_file = str(os.environ.get("APNS_PRIVATE_KEY_FILE") or "").strip()
    if private_key_file:
        try:
            return Path(private_key_file).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""

def native_push_apns_settings(*, include_private: bool = False) -> dict[str, Any]:
    key_id = str(os.environ.get("APNS_KEY_ID") or "").strip()
    team_id = str(os.environ.get("APNS_TEAM_ID") or "").strip()
    bundle_id = str(os.environ.get("APNS_BUNDLE_ID") or "").strip()
    private_key = native_push_private_key()
    missing = [
        key
        for key, present in {
            "APNS_KEY_ID": bool(key_id),
            "APNS_TEAM_ID": bool(team_id),
            "APNS_BUNDLE_ID": bool(bundle_id),
            "APNS_PRIVATE_KEY": bool(private_key),
        }.items()
        if not present
    ]
    settings: dict[str, Any] = {
        "provider": "apns",
        "environment": native_push_environment(),
        "bundleId": bundle_id or None,
        "configured": not missing,
        "missing": missing,
    }
    if include_private:
        settings.update({"keyId": key_id, "teamId": team_id, "privateKey": private_key})
    return settings

def native_push_config() -> dict[str, Any]:
    settings = native_push_apns_settings()
    missing = settings.get("missing") or []
    delivery_available = not missing
    return {
        "provider": "apns",
        "available": delivery_available,
        "deliveryAvailable": delivery_available,
        "configured": settings.get("configured"),
        "status": "available" if delivery_available else "not_configured",
        "environment": settings.get("environment"),
        "bundleId": settings.get("bundleId"),
        "missing": missing,
    }

def native_push_device_token_suffix(device_token: Any) -> str:
    text = str(device_token or "").strip()
    return text[-6:] if len(text) > 6 else text

def native_push_device_token_hash(device_token: Any) -> str:
    return hashlib.sha256(str(device_token or "").strip().encode("utf-8")).hexdigest()

def native_push_device_public(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    device_token = str(row.get("device_token") or "")
    token_suffix = row.get("token_suffix") or native_push_device_token_suffix(device_token)
    return {
        "id": row.get("id"),
        "platform": row.get("platform") or "ios",
        "environment": row.get("apns_environment"),
        "bundleId": row.get("app_bundle_id"),
        "deviceLabel": row.get("device_label"),
        "appVersion": row.get("app_version"),
        "tokenSuffix": token_suffix,
        "lastSeenAt": row.get("last_seen_at"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
        "revokedAt": row.get("revoked_at"),
    }

def native_push_device_rows(conn, user_id: UUID | str) -> list[dict[str, Any]]:
    if not table_exists(conn, "native_push_devices"):
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, platform, device_token, token_hash, token_suffix,
                   apns_environment, app_bundle_id, device_label,
                   app_version, user_agent, metadata, last_seen_at, created_at, updated_at, revoked_at
            FROM native_push_devices
            WHERE user_id=%s
            ORDER BY revoked_at NULLS FIRST, updated_at DESC
            """,
            (user_id,),
        )
        return cur.fetchall()

def native_push_status_payload(conn, user_id: UUID | str) -> dict[str, Any]:
    config = native_push_config()
    devices = [native_push_device_public(row) for row in native_push_device_rows(conn, user_id)]
    active_devices = [device for device in devices if device and not device.get("revokedAt")]
    return {
        **config,
        "registrationEndpoint": "/api/next/push/native/register",
        "testEndpoint": "/api/next/push/native/test",
        "devices": devices,
        "activeDeviceCount": len(active_devices),
        "tableAvailable": table_exists(conn, "native_push_devices"),
    }

def normalize_native_push_registration(body: dict[str, Any]) -> dict[str, Any]:
    platform = str(body.get("platform") or "ios").strip().lower()
    if platform != "ios":
        raise NextApiError("Only ios native push devices are supported", 400)
    device_token = str(body.get("deviceToken", body.get("device_token")) or "").strip()
    if not device_token:
        raise NextApiError("deviceToken is required", 400)
    if len(device_token) > 512:
        raise NextApiError("deviceToken is too long", 400)
    environment = native_push_environment(body.get("environment", body.get("apnsEnvironment")))
    bundle_id = clean_text(body.get("bundleId", body.get("bundle_id"))) or native_push_config().get("bundleId")
    device_label = clean_text(body.get("deviceLabel", body.get("device_label"))) or "iOS device"
    app_version = clean_text(body.get("appVersion", body.get("app_version"))) or None
    user_agent = str(body.get("userAgent") or request.headers.get("User-Agent") or "")[:500]
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    return {
        "platform": platform,
        "deviceToken": device_token,
        "tokenHash": native_push_device_token_hash(device_token),
        "tokenSuffix": native_push_device_token_suffix(device_token),
        "environment": environment,
        "bundleId": bundle_id,
        "deviceLabel": device_label,
        "appVersion": app_version,
        "userAgent": user_agent,
        "metadata": metadata,
    }

def native_push_apns_url(environment: str, device_token: str) -> str:
    host = "api.sandbox.push.apple.com" if native_push_environment(environment) == "sandbox" else "api.push.apple.com"
    return f"https://{host}/3/device/{quote(device_token, safe='')}"

def native_push_apns_auth_token(config: dict[str, Any]) -> str:
    token = jwt.encode(
        {"iss": config.get("teamId"), "iat": int(time.time())},
        config.get("privateKey") or "",
        algorithm="ES256",
        headers={"kid": config.get("keyId")},
    )
    return token.decode("utf-8") if isinstance(token, bytes) else str(token)

def native_push_payload(
    conn,
    *,
    title: str,
    body: str = "",
    topic: str = "app_updates",
    url: str = "/notifications",
    notification_id: UUID | str | None = None,
    user_id: UUID | str | None = None,
) -> dict[str, Any]:
    counts = notification_counts(conn, user_id) if user_id else {}
    unread = counts.get("unread") if isinstance(counts, dict) else 0
    notification_text = str(notification_id) if notification_id else ""
    deep_link = f"discvault://notifications/{notification_text}" if notification_text else "discvault://notifications"
    return {
        "aps": {
            "alert": {"title": title, "body": body},
            "badge": int(unread or 0),
            "sound": "default",
        },
        "notificationId": notification_text or None,
        "topic": topic,
        "url": url,
        "deepLink": deep_link,
    }

def deliver_native_push_device(
    config: dict[str, Any],
    device: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    device_token = str(device.get("device_token") or "").strip()
    device_id = str(device.get("id") or "")
    if not device_token:
        return {"status": "failed", "deviceId": device_id or None, "error": "Device token is empty", "permanent": True}
    try:
        import httpx
    except Exception as exc:
        return {
            "status": "dependency_missing",
            "deviceId": device_id or None,
            "tokenSuffix": device.get("token_suffix") or native_push_device_token_suffix(device_token),
            "error": f"httpx with HTTP/2 support is not available: {exc}",
            "permanent": False,
        }
    try:
        auth_token = native_push_apns_auth_token(config)
        with httpx.Client(http2=True, timeout=10.0) as client:
            response_obj = client.post(
                native_push_apns_url(str(device.get("apns_environment") or config.get("environment") or "production"), device_token),
                headers={
                    "authorization": f"bearer {auth_token}",
                    "apns-topic": str(device.get("app_bundle_id") or config.get("bundleId") or ""),
                    "apns-push-type": "alert",
                    "apns-priority": "10",
                    "apns-expiration": str(int(time.time()) + 86400),
                },
                json=payload,
            )
    except Exception as exc:
        return {
            "status": "failed",
            "deviceId": device_id or None,
            "tokenSuffix": device.get("token_suffix") or native_push_device_token_suffix(device_token),
            "error": f"APNS request failed: {exc}",
            "permanent": False,
        }
    reason = None
    try:
        response_body = response_obj.json() if response_obj.content else {}
        reason = response_body.get("reason") if isinstance(response_body, dict) else None
    except Exception:
        reason = None
    result = {
        "status": "sent" if response_obj.status_code == 200 else "failed",
        "deviceId": device_id or None,
        "tokenSuffix": device.get("token_suffix") or native_push_device_token_suffix(device_token),
        "statusCode": response_obj.status_code,
        "apnsId": response_obj.headers.get("apns-id"),
    }
    if response_obj.status_code != 200:
        permanent_reasons = {"BadDeviceToken", "DeviceTokenNotForTopic", "Unregistered"}
        result.update(
            {
                "reason": reason,
                "error": f"APNS rejected device token ({response_obj.status_code}{f': {reason}' if reason else ''})",
                "permanent": response_obj.status_code == 410 or reason in permanent_reasons,
            }
        )
    return result

def send_native_push_to_user(
    conn,
    user_id: UUID | str,
    *,
    title: str,
    body: str = "",
    topic: str = "app_updates",
    url: str = "/notifications",
    notification_id: UUID | str | None = None,
    device_id: UUID | str | None = None,
) -> dict[str, Any]:
    if not table_exists(conn, "native_push_devices"):
        return {"status": "unavailable", "attempted": 0, "sent": 0, "failed": 0, "errors": ["Native push devices table is not available"]}
    config = native_push_apns_settings(include_private=True)
    if not config.get("configured"):
        return {"status": "not_configured", "attempted": 0, "sent": 0, "failed": 0, "errors": ["APNS is not configured"], "missing": config.get("missing") or []}
    active_devices = [device for device in native_push_device_rows(conn, user_id) if device and not device.get("revoked_at")]
    if device_id:
        active_devices = [device for device in active_devices if str(device.get("id")) == str(device_id)]
    if not active_devices:
        return {"status": "no_devices", "attempted": 0, "sent": 0, "failed": 0, "errors": ["No native iOS device registered for this user"]}
    payload = native_push_payload(
        conn,
        title=title,
        body=body,
        topic=topic,
        url=url,
        notification_id=notification_id,
        user_id=user_id,
    )
    results = [deliver_native_push_device(config, device, payload) for device in active_devices]
    revoked_device_ids = [
        result.get("deviceId")
        for result in results
        if result.get("deviceId") and result.get("permanent")
    ]
    if revoked_device_ids:
        with conn.cursor() as cur:
            for item_device_id in revoked_device_ids:
                cur.execute(
                    """
                    UPDATE native_push_devices
                    SET revoked_at=COALESCE(revoked_at, now()), updated_at=now()
                    WHERE id=%s
                    """,
                    (item_device_id,),
                )
    sent = len([result for result in results if result.get("status") == "sent"])
    failed = len(results) - sent
    errors = [str(result.get("error") or result.get("reason") or "APNS delivery failed") for result in results if result.get("status") != "sent"]
    delivery_status = "sent" if sent == len(results) else "partial" if sent else "failed"
    if any(result.get("status") == "dependency_missing" for result in results):
        delivery_status = "dependency_missing"
    return {
        "status": delivery_status,
        "sent": sent,
        "failed": failed,
        "attempted": len(results),
        "errors": errors,
        "results": results,
        "revokedDeviceIds": revoked_device_ids,
    }

def register_next_push_routes(flask_app: Flask, *, connect) -> None:  # pragma: no cover - Flask integration
    @flask_app.get("/manifest.json")
    @flask_app.get("/api/next/manifest.json")
    def next_pwa_manifest():
        result = jsonify(pwa_manifest_payload())
        result.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return result

    @flask_app.get("/service-worker.js")
    def next_service_worker():
        path = _next_app().next_frontend_dir() / "service-worker.js"
        if not path.exists() or not path.is_file():
            raise NextApiError("Service worker not found", 404)
        result = send_file(path, mimetype="application/javascript")
        result.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return result

    @flask_app.get("/api/next/assets/<path:asset_name>")
    def next_frontend_asset(asset_name: str):
        safe_name = Path(asset_name).name
        if safe_name != asset_name or safe_name not in PWA_ICON_ASSETS:
            raise NextApiError("Asset not found", 404)
        path = _next_app().next_frontend_dir() / safe_name
        if not path.exists() or not path.is_file():
            raise NextApiError("Asset not found", 404)
        return send_file(path, mimetype=mimetypes.guess_type(path.name)[0] or "application/octet-stream")

    @flask_app.get("/apple-touch-icon-152.png")
    @flask_app.get("/apple-touch-icon-167.png")
    @flask_app.get("/apple-touch-icon.png")
    @flask_app.get("/favicon-32.png")
    @flask_app.get("/favicon-192.png")
    @flask_app.get("/favicon-512.png")
    @flask_app.get("/pwa-icon-192.png")
    @flask_app.get("/pwa-icon-512.png")
    @flask_app.get("/pwa-maskable-192.png")
    @flask_app.get("/pwa-maskable-512.png")
    def next_root_pwa_asset():
        return next_frontend_asset(request.path.rsplit("/", 1)[-1])

    @flask_app.get("/api/next/push/status")
    def next_push_status():
        endpoint = str(request.args.get("endpoint") or "").strip()
        with connect() as conn:
            actor = _next_app().require_next_authenticated_user(conn)
            user_id = actor.get("id")
            if not user_id:
                raise NextApiError("Push requires a signed-in user", 401)
            public_key, _ = get_or_create_push_vapid_keys(conn)
            subscriptions: list[dict[str, Any]] = []
            current_subscription = None
            if table_exists(conn, "push_subscriptions"):
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, endpoint, device_label, user_agent, is_primary, created_at, updated_at
                        FROM push_subscriptions
                        WHERE user_id=%s
                        ORDER BY is_primary DESC, updated_at DESC
                        """,
                        (user_id,),
                    )
                    subscriptions = cur.fetchall()
                for subscription in subscriptions:
                    if endpoint and subscription.get("endpoint") == endpoint:
                        current_subscription = subscription
                        break
            return response(
                {
                    "status": "ok",
                    "publicKey": public_key,
                    "subject": push_vapid_subject(),
                    "preferences": notification_preference_map(conn, user_id),
                    "preferenceDefaults": NOTIFICATION_PREF_DEFAULTS,
                    "nativePush": native_push_status_payload(conn, user_id),
                    "subscriptions": [
                        {
                            "id": item.get("id"),
                            "deviceLabel": item.get("device_label"),
                            "userAgent": item.get("user_agent"),
                            "isPrimary": item.get("is_primary"),
                            "createdAt": item.get("created_at"),
                            "updatedAt": item.get("updated_at"),
                            "current": endpoint and item.get("endpoint") == endpoint,
                        }
                        for item in subscriptions
                    ],
                    "currentSubscription": current_subscription,
                    "counts": notification_counts(conn, user_id),
                }
            )

    @flask_app.get("/api/next/push/native/status")
    def next_native_push_status():
        with connect() as conn:
            actor = _next_app().require_next_authenticated_user(conn)
            user_id = actor.get("id")
            if not user_id:
                raise NextApiError("Native push requires a signed-in user", 401)
            return response(
                {
                    "status": "ok",
                    "nativePush": native_push_status_payload(conn, user_id),
                    "preferences": notification_preference_map(conn, user_id),
                    "counts": notification_counts(conn, user_id),
                }
            )

    @flask_app.post("/api/next/push/native/register")
    def next_native_push_register():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise NextApiError("Native push registration request body must be an object", 400)
        registration = normalize_native_push_registration(body)
        with connect() as conn:
            actor = _next_app().require_next_authenticated_user(conn)
            user_id = actor.get("id")
            if not user_id:
                raise NextApiError("Native push requires a signed-in user", 401)
            if not table_exists(conn, "native_push_devices"):
                raise NextApiError("Native push devices table is not available", 503)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id
                        FROM native_push_devices
                        WHERE user_id=%s
                          AND token_hash=%s
                          AND apns_environment=%s
                          AND COALESCE(app_bundle_id, '')=COALESCE(%s, '')
                        ORDER BY revoked_at NULLS FIRST, updated_at DESC
                        LIMIT 1
                        """,
                        (user_id, registration["tokenHash"], registration["environment"], registration["bundleId"]),
                    )
                    existing = cur.fetchone()
                    if existing:
                        cur.execute(
                            """
                            UPDATE native_push_devices
                            SET platform=%s,
                                device_token=%s,
                                token_hash=%s,
                                token_suffix=%s,
                                app_bundle_id=%s,
                                device_label=%s,
                                app_version=%s,
                                user_agent=%s,
                                metadata=%s,
                                last_seen_at=now(),
                                updated_at=now(),
                                revoked_at=NULL
                            WHERE id=%s AND user_id=%s
                            RETURNING id, platform, device_token, token_hash, token_suffix,
                                      apns_environment, app_bundle_id, device_label, app_version,
                                      user_agent, metadata, last_seen_at, created_at, updated_at, revoked_at
                            """,
                            (
                                registration["platform"],
                                registration["deviceToken"],
                                registration["tokenHash"],
                                registration["tokenSuffix"],
                                registration["bundleId"],
                                registration["deviceLabel"],
                                registration["appVersion"],
                                registration["userAgent"],
                                Jsonb(json_ready(registration["metadata"])),
                                existing["id"],
                                user_id,
                            ),
                        )
                    else:
                        cur.execute(
                            """
                            INSERT INTO native_push_devices (
                                user_id, platform, device_token, token_hash, token_suffix,
                                apns_environment, app_bundle_id, device_label, app_version,
                                user_agent, metadata, last_seen_at, created_at, updated_at, revoked_at
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now(), now(), NULL)
                            RETURNING id, platform, device_token, token_hash, token_suffix,
                                      apns_environment, app_bundle_id, device_label, app_version,
                                      user_agent, metadata, last_seen_at, created_at, updated_at, revoked_at
                            """,
                            (
                                user_id,
                                registration["platform"],
                                registration["deviceToken"],
                                registration["tokenHash"],
                                registration["tokenSuffix"],
                                registration["environment"],
                                registration["bundleId"],
                                registration["deviceLabel"],
                                registration["appVersion"],
                                registration["userAgent"],
                                Jsonb(json_ready(registration["metadata"])),
                            ),
                        )
                    device = cur.fetchone()
                audit_event(
                    conn,
                    event_type="native_push.registered",
                    category="personal",
                    actor=actor,
                    target_type="native_push_device",
                    target_id=device.get("id") if device else None,
                    summary="Registered native iOS push device",
                    metadata={
                        "platform": registration["platform"],
                        "environment": registration["environment"],
                        "bundleId": registration["bundleId"],
                        "deviceLabel": registration["deviceLabel"],
                        "appVersion": registration["appVersion"],
                        "tokenSuffix": registration["tokenSuffix"],
                    },
                )
            return response(
                {
                    "status": "ok",
                    "device": native_push_device_public(device),
                    "nativePush": native_push_status_payload(conn, user_id),
                    "preferences": notification_preference_map(conn, user_id),
                    "counts": notification_counts(conn, user_id),
                },
                201,
            )

    @flask_app.delete("/api/next/push/native/register")
    def next_native_push_revoke():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise NextApiError("Native push revoke request body must be an object", 400)
        device_id = str(body.get("deviceId", body.get("id")) or "").strip()
        device_token = str(body.get("deviceToken", body.get("device_token")) or "").strip()
        environment = native_push_environment(body.get("environment", body.get("apnsEnvironment")))
        bundle_id = clean_text(body.get("bundleId", body.get("bundle_id"))) or native_push_config().get("bundleId")
        if not device_id and not device_token:
            raise NextApiError("deviceId or deviceToken is required", 400)
        with connect() as conn:
            actor = _next_app().require_next_authenticated_user(conn)
            user_id = actor.get("id")
            if not user_id:
                raise NextApiError("Native push requires a signed-in user", 401)
            if not table_exists(conn, "native_push_devices"):
                raise NextApiError("Native push devices table is not available", 503)
            params: tuple[Any, ...]
            if device_id:
                parsed_device_id = parse_uuid(device_id, "deviceId")
                where_clause = "id=%s AND user_id=%s"
                params = (parsed_device_id, user_id)
            else:
                where_clause = "platform='ios' AND apns_environment=%s AND token_hash=%s AND COALESCE(app_bundle_id, '')=COALESCE(%s, '') AND user_id=%s"
                params = (environment, native_push_device_token_hash(device_token), bundle_id, user_id)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        UPDATE native_push_devices
                        SET revoked_at=COALESCE(revoked_at, now()), updated_at=now()
                        WHERE {where_clause}
                        RETURNING id, platform, device_token, token_hash, token_suffix,
                                  apns_environment, app_bundle_id,
                                  device_label, app_version, user_agent, metadata,
                                  last_seen_at, created_at, updated_at, revoked_at
                        """,
                        params,
                    )
                    device = cur.fetchone()
                audit_event(
                    conn,
                    event_type="native_push.revoked",
                    category="personal",
                    actor=actor,
                    target_type="native_push_device",
                    target_id=device.get("id") if device else None,
                    summary="Revoked native iOS push device",
                    metadata={"deleted": bool(device), "environment": environment},
                )
            return response(
                {
                    "status": "deleted",
                    "deleted": bool(device),
                    "revoked": bool(device),
                    "device": native_push_device_public(device),
                    "nativePush": native_push_status_payload(conn, user_id),
                    "counts": notification_counts(conn, user_id),
                }
            )

    @flask_app.post("/api/next/push/native/test")
    def next_native_push_test():
        body = request.get_json(silent=True) or {}
        if body and not isinstance(body, dict):
            raise NextApiError("Native push test request body must be an object", 400)
        requested_device_id = str(body.get("deviceId", body.get("id")) or "").strip() if isinstance(body, dict) else ""
        parsed_device_id = parse_uuid(requested_device_id, "deviceId") if requested_device_id else None
        with connect() as conn:
            actor = _next_app().require_next_authenticated_user(conn)
            user_id = actor.get("id")
            if not user_id:
                raise NextApiError("Native push test requires a signed-in user", 401)
            with conn.transaction():
                notification = create_user_notification(
                    conn,
                    user_id,
                    title="DiscVault",
                    body="Native iOS push notifications are working.",
                    url="/notifications",
                    pref_key="security",
                    payload={"type": "native_push_test", "topic": "security"},
                    send_push=False,
                )
                delivery = send_native_push_to_user(
                    conn,
                    user_id,
                    title=notification.get("title") or "DiscVault",
                    body=notification.get("body") or "",
                    topic="security",
                    url="/notifications",
                    notification_id=notification.get("id"),
                    device_id=parsed_device_id,
                )
                audit_event(
                    conn,
                    event_type="native_push.test",
                    category="personal",
                    actor=actor,
                    target_type="user_notification",
                    target_id=notification.get("id"),
                    summary="Tested native iOS push delivery",
                    metadata={"delivery": delivery},
                )
            return response(
                {
                    "status": "ok" if delivery.get("status") in {"sent", "no_devices"} else "degraded",
                    "notification": notification,
                    "delivery": delivery,
                    "nativePush": native_push_status_payload(conn, user_id),
                    "counts": notification_counts(conn, user_id),
                }
            )

    @flask_app.post("/api/next/push/subscribe")
    def next_push_subscribe():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise NextApiError("Push subscription request body must be an object", 400)
        subscription = body.get("subscription") if isinstance(body.get("subscription"), dict) else body
        endpoint = str(subscription.get("endpoint") or "").strip()
        keys = subscription.get("keys") if isinstance(subscription.get("keys"), dict) else {}
        p256dh = str(keys.get("p256dh") or "").strip()
        auth = str(keys.get("auth") or "").strip()
        if not endpoint or not p256dh or not auth:
            raise NextApiError("Push subscription endpoint and keys are required", 400)
        device_label = clean_text(body.get("deviceLabel", body.get("device_label"))) or "DiscVault device"
        user_agent = str(body.get("userAgent") or request.headers.get("User-Agent") or "")[:500]
        requested_primary = parse_bool_value(body.get("isPrimary", body.get("is_primary")), default=False)
        with connect() as conn:
            actor = _next_app().require_next_authenticated_user(conn)
            user_id = actor.get("id")
            if not user_id:
                raise NextApiError("Push requires a signed-in user", 401)
            if not table_exists(conn, "push_subscriptions"):
                raise NextApiError("Push subscriptions table is not available", 503)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*)::int AS count FROM push_subscriptions WHERE user_id=%s", (user_id,))
                    first_subscription = int((cur.fetchone() or {}).get("count") or 0) == 0
                    is_primary = bool(requested_primary or first_subscription)
                    if is_primary:
                        cur.execute("UPDATE push_subscriptions SET is_primary=false WHERE user_id=%s", (user_id,))
                    cur.execute(
                        """
                        INSERT INTO push_subscriptions (
                            user_id, endpoint, p256dh, auth, user_agent, device_label, is_primary, created_at, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now())
                        ON CONFLICT (endpoint) DO UPDATE SET
                            user_id=EXCLUDED.user_id,
                            p256dh=EXCLUDED.p256dh,
                            auth=EXCLUDED.auth,
                            user_agent=EXCLUDED.user_agent,
                            device_label=EXCLUDED.device_label,
                            is_primary=EXCLUDED.is_primary,
                            updated_at=now()
                        RETURNING id, endpoint, device_label, user_agent, is_primary, created_at, updated_at
                        """,
                        (user_id, endpoint, p256dh, auth, user_agent, device_label, is_primary),
                    )
                    subscription_row = cur.fetchone()
                audit_event(
                    conn,
                    event_type="push.subscribed",
                    category="personal",
                    actor=actor,
                    target_type="push_subscription",
                    target_id=subscription_row.get("id") if subscription_row else None,
                    summary="Enabled push notifications",
                    metadata={"isPrimary": is_primary},
                )
            return response(
                {
                    "status": "ok",
                    "subscription": subscription_row,
                    "preferences": notification_preference_map(conn, user_id),
                    "counts": notification_counts(conn, user_id),
                },
                201,
            )

    @flask_app.delete("/api/next/push/subscribe")
    def next_push_unsubscribe():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise NextApiError("Push unsubscribe request body must be an object", 400)
        endpoint = str(body.get("endpoint") or "").strip()
        if not endpoint:
            raise NextApiError("endpoint is required", 400)
        with connect() as conn:
            actor = _next_app().require_next_authenticated_user(conn)
            user_id = actor.get("id")
            if not user_id:
                raise NextApiError("Push requires a signed-in user", 401)
            if not table_exists(conn, "push_subscriptions"):
                raise NextApiError("Push subscriptions table is not available", 503)
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM push_subscriptions WHERE user_id=%s AND endpoint=%s RETURNING id",
                        (user_id, endpoint),
                    )
                    deleted = cur.fetchone()
                audit_event(
                    conn,
                    event_type="push.unsubscribed",
                    category="personal",
                    actor=actor,
                    target_type="push_subscription",
                    target_id=deleted.get("id") if deleted else None,
                    summary="Disabled push notifications",
                    metadata={},
                )
            return response({"status": "deleted", "deleted": bool(deleted), "counts": notification_counts(conn, user_id)})

    @flask_app.patch("/api/next/push/preferences")
    def patch_next_push_preferences():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise NextApiError("Push preferences request body must be an object", 400)
        updates = body.get("preferences", body)
        if not isinstance(updates, dict):
            raise NextApiError("preferences must be an object", 400)
        with connect() as conn:
            actor = _next_app().require_next_authenticated_user(conn)
            user_id = actor.get("id")
            if not user_id:
                raise NextApiError("Push preferences require a signed-in user", 401)
            if not table_exists(conn, "notification_preferences"):
                raise NextApiError("Notification preferences table is not available", 503)
            normalized: dict[str, bool] = {}
            for key, value in updates.items():
                if key in NOTIFICATION_PREF_DEFAULTS:
                    normalized[key] = parse_bool_value(value, default=NOTIFICATION_PREF_DEFAULTS[key])
            with conn.transaction():
                with conn.cursor() as cur:
                    for key, enabled in normalized.items():
                        cur.execute(
                            """
                            INSERT INTO notification_preferences (user_id, pref_key, enabled, updated_at)
                            VALUES (%s, %s, %s, now())
                            ON CONFLICT (user_id, pref_key) DO UPDATE SET
                                enabled=EXCLUDED.enabled,
                                updated_at=now()
                            """,
                            (user_id, key, enabled),
                        )
            return response(
                {
                    "status": "ok",
                    "preferences": notification_preference_map(conn, user_id),
                    "preferenceDefaults": NOTIFICATION_PREF_DEFAULTS,
                }
            )

    @flask_app.post("/api/next/push/test")
    def next_push_test():
        with connect() as conn:
            actor = _next_app().require_next_authenticated_user(conn)
            user_id = actor.get("id")
            if not user_id:
                raise NextApiError("Push test requires a signed-in user", 401)
            with conn.transaction():
                notification = create_user_notification(
                    conn,
                    user_id,
                    title="DiscVault",
                    body="Push notifications are working.",
                    url="/notifications",
                    pref_key="security",
                    payload={"type": "push_test"},
                    send_push=True,
                )
                audit_event(
                    conn,
                    event_type="push.test",
                    category="personal",
                    actor=actor,
                    target_type="user_notification",
                    target_id=notification.get("id"),
                    summary="Sent push test notification",
                    metadata={"pushErrors": notification.get("pushErrors") or []},
                )
            return response(
                {
                    "status": "ok" if not notification.get("pushErrors") else "degraded",
                    "notification": notification,
                    "counts": notification_counts(conn, user_id),
                }
            )
