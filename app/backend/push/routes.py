import base64
import json
from datetime import datetime

from flask import jsonify, request

try:
    from ..db import get_db
    from ..logging_utils import add_log
except ImportError:  # pragma: no cover - supports running app.py directly
    from db import get_db
    from logging_utils import add_log


_get_current_user_id = None


def init_push_dependencies(*, get_current_user_id):
    global _get_current_user_id
    _get_current_user_id = get_current_user_id


def _get_or_create_vapid_keys():
    """Return (public_key_base64url, private_key_raw_b64url)."""
    from cryptography.hazmat.primitives.asymmetric import ec as _ec
    from cryptography.hazmat.backends import default_backend as _cb
    from cryptography.hazmat.primitives.serialization import Encoding as _Enc, PublicFormat as _PubFmt

    conn = get_db()
    row_pub = conn.execute("SELECT value FROM settings WHERE key='vapid_public_key'").fetchone()
    row_priv = conn.execute("SELECT value FROM settings WHERE key='vapid_private_key'").fetchone()
    if row_pub and row_priv:
        conn.close()
        return row_pub["value"], row_priv["value"]

    priv = _ec.generate_private_key(_ec.SECP256R1(), _cb())
    pub_bytes = priv.public_key().public_bytes(_Enc.X962, _PubFmt.UncompressedPoint)
    pub_b64 = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()
    d_bytes = priv.private_numbers().private_value.to_bytes(32, "big")
    priv_raw = base64.urlsafe_b64encode(d_bytes).rstrip(b"=").decode()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('vapid_public_key',?)", (pub_b64,))
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('vapid_private_key',?)", (priv_raw,))
    conn.commit()
    conn.close()
    return pub_b64, priv_raw


def _send_push(endpoint, p256dh, auth, vapid_priv_raw, title, body, url="/"):
    """Send a single Web Push message. Returns None on success, error string on failure."""
    try:
        from pywebpush import webpush

        extra_headers = {}
        if "notify.windows.com" in endpoint or "wns.windows.com" in endpoint:
            extra_headers["X-WNS-Type"] = "wns/raw"
            extra_headers["Content-Type"] = "application/octet-stream"

        webpush(
            subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
            data=json.dumps({"title": title, "body": body, "url": url}),
            vapid_private_key=vapid_priv_raw,
            vapid_claims={"sub": "mailto:no-reply@discvault.app"},
            ttl=86400,
            headers=extra_headers if extra_headers else None,
        )
        return None
    except Exception as ex:
        status = getattr(getattr(ex, "response", None), "status_code", None)
        err_body = ""
        err_headers = ""
        try:
            resp = getattr(ex, "response", None)
            if resp is not None:
                err_body = (resp.text or "")[:400]
                err_headers = str(dict(resp.headers))[:200]
        except Exception:
            pass
        ep_hint = endpoint[:60] + "..." if len(endpoint) > 60 else endpoint
        err_msg = f"Push failed (HTTP {status}) [{ep_hint}]: {ex} | body: {err_body} | resp-headers: {err_headers}".strip()
        add_log("push", "Push delivery error", err_msg, level="error")
        if status == 410:
            try:
                c = get_db()
                c.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
                c.commit()
                c.close()
            except Exception:
                pass
        return err_msg


def push_to_user(user_id, title, body, url="/"):
    """Deliver a push notification to all subscribed devices for *user_id*."""
    errors = []
    try:
        _, priv_raw = _get_or_create_vapid_keys()
        conn = get_db()
        subs = conn.execute(
            "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id=?", (user_id,)
        ).fetchall()
        conn.close()
        if not subs:
            errors.append("No subscriptions found for this user")
        for s in subs:
            err = _send_push(s["endpoint"], s["p256dh"], s["auth"], priv_raw, title, body, url)
            if err:
                errors.append(err)
    except Exception as ex:
        errors.append(str(ex))
        add_log("push", "push_to_user error", str(ex), level="error")
    return errors


_NOTIF_PREF_DEFAULTS = {
    "group_invite": True,
}


def _user_notif_pref_enabled(user_id, pref_key):
    if pref_key not in _NOTIF_PREF_DEFAULTS:
        return False
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT enabled FROM notification_prefs WHERE user_id=? AND pref_key=?",
            (user_id, pref_key),
        ).fetchone()
        conn.close()
        if row is None:
            return _NOTIF_PREF_DEFAULTS[pref_key]
        return bool(row["enabled"])
    except Exception:
        return _NOTIF_PREF_DEFAULTS.get(pref_key, True)


def push_to_user_if_pref(user_id, pref_key, title, body, url="/"):
    if not _user_notif_pref_enabled(user_id, pref_key):
        return []
    return push_to_user(user_id, title, body, url)


def register_push_routes(app):
    @app.route("/api/push/vapid-public-key", methods=["GET"])
    def push_vapid_public_key():
        pub, _ = _get_or_create_vapid_keys()
        return jsonify({"publicKey": pub})

    @app.route("/api/push/subscribe", methods=["POST"])
    def push_subscribe():
        data = request.get_json() or {}
        endpoint = data.get("endpoint", "")
        keys = data.get("keys", {})
        p256dh = keys.get("p256dh", "")
        auth = keys.get("auth", "")
        if not endpoint or not p256dh or not auth:
            return jsonify({"error": "Missing fields"}), 400
        user_id = (_get_current_user_id() if _get_current_user_id else None) or "__global__"
        now = datetime.utcnow().isoformat()
        conn = get_db()
        conn.execute("""
            INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                user_id=excluded.user_id, p256dh=excluded.p256dh,
                auth=excluded.auth, created_at=excluded.created_at
        """, (user_id, endpoint, p256dh, auth, now))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @app.route("/api/push/subscribe", methods=["DELETE"])
    def push_unsubscribe():
        data = request.get_json() or {}
        endpoint = data.get("endpoint", "")
        if not endpoint:
            return jsonify({"error": "Missing endpoint"}), 400
        conn = get_db()
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @app.route("/api/push/test", methods=["POST"])
    def push_test():
        user_id = (_get_current_user_id() if _get_current_user_id else None) or "__global__"
        errors = push_to_user(user_id, "DiscVault", "Push-meldingen werken! 🎬", "/")
        if errors:
            all_stale = errors and all("410" in str(e) for e in errors)
            return jsonify({"ok": False, "errors": errors, "stale": all_stale}), 500
        return jsonify({"ok": True})

    @app.route("/api/push/prefs", methods=["GET"])
    def push_prefs_get():
        uid = _get_current_user_id() if _get_current_user_id else None
        if not uid:
            return jsonify({k: v for k, v in _NOTIF_PREF_DEFAULTS.items()})
        conn = get_db()
        rows = conn.execute(
            "SELECT pref_key, enabled FROM notification_prefs WHERE user_id=?", (uid,)
        ).fetchall()
        conn.close()
        prefs = dict(_NOTIF_PREF_DEFAULTS)
        for r in rows:
            if r["pref_key"] in prefs:
                prefs[r["pref_key"]] = bool(r["enabled"])
        return jsonify(prefs)

    @app.route("/api/push/prefs", methods=["PUT"])
    def push_prefs_put():
        uid = _get_current_user_id() if _get_current_user_id else None
        if not uid:
            return jsonify({"error": "Unauthorized"}), 401
        data = request.get_json() or {}
        conn = get_db()
        for key, default in _NOTIF_PREF_DEFAULTS.items():
            if key in data:
                enabled = 1 if data[key] else 0
                conn.execute(
                    "INSERT INTO notification_prefs (user_id, pref_key, enabled) VALUES (?,?,?)"
                    " ON CONFLICT(user_id, pref_key) DO UPDATE SET enabled=excluded.enabled",
                    (uid, key, enabled),
                )
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
