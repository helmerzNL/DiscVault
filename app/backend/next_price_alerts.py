"""Price & deal alert helpers for DiscVault Next.

This module handles three concerns:

1.  **URL price extraction** – fetches a shop product page and extracts the
    current price from structured data (schema.org JSON-LD, Open Graph product
    meta tags) or, as a last resort, a simple currency-symbol regex.

2.  **Alert evaluation** – given a wishlist item and a newly observed price,
    decides whether to fire a notification (price ≤ target, cooldown passed)
    and writes the price history columns back to the DB.

3.  **Background sweep** – iterates over all alert-enabled wishlist items that
    are due for a check and calls 1+2 for each.  Called by the worker when a
    ``price_alert.sweep`` job is dequeued.

Plugin-based pricing (``price_check`` capability):
    Metadata plugins may optionally declare the ``price_check`` capability in
    their manifest.  When the sweep encounters an item with a ``movievault_id``
    and a plugin that supports ``price_check``, it can call
    ``run_plugin_entrypoint`` with entrypoint ``"price_check"`` to obtain a
    structured price response.  That integration is a follow-up; the hook points
    are marked with ``# PLUGIN_HOOK`` comments.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

try:
    from psycopg.types.json import Jsonb
except ModuleNotFoundError:  # pragma: no cover - test environments without psycopg
    class Jsonb:  # type: ignore[no-redef]
        def __init__(self, value: Any) -> None:
            self.value = value

try:
    from .next_common import NextApiError, json_ready, table_exists
    from .next_notifications import create_user_notification
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_common import NextApiError, json_ready, table_exists
    from next_notifications import create_user_notification

PRICE_ALERT_JOB_TYPE = "price_alert.sweep"

# Items are rechecked at most every 6 hours.
_RECHECK_INTERVAL_HOURS = 6

# At most one notification per item per day.
_ALERT_COOLDOWN_HOURS = 24

# Maximum items processed in a single sweep run.
_SWEEP_BATCH_LIMIT = 50

# Timeout for HTTP price-fetch requests (seconds).
_FETCH_TIMEOUT = 10


# ---------------------------------------------------------------------------
# Price extraction from a URL
# ---------------------------------------------------------------------------

def _fetch_html(url: str, *, session: Any = None) -> str:
    """Return the raw HTML of *url*.  Raises ``ValueError`` on failure."""
    try:
        import urllib.request

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; DiscVault-PriceBot/1.0; "
                "+https://discvault.eu/bot)"
            ),
            "Accept": "text/html,application/xhtml+xml",
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except Exception as exc:
        raise ValueError(f"Could not fetch {url}: {exc}") from exc


def _extract_from_schema_org(html: str) -> tuple[float | None, str | None]:
    """Parse schema.org JSON-LD ``Product`` / ``Offer`` blocks."""
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue

        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            offers = item.get("offers") or item.get("Offers")
            if not offers:
                continue
            offer_list = offers if isinstance(offers, list) else [offers]
            for offer in offer_list:
                if not isinstance(offer, dict):
                    continue
                price_raw = offer.get("price") or offer.get("Price")
                currency = str(offer.get("priceCurrency") or offer.get("PriceCurrency") or "").strip().upper() or None
                price = _coerce_price(price_raw)
                if price is not None:
                    return price, currency
    return None, None


def _extract_from_og_meta(html: str) -> tuple[float | None, str | None]:
    """Parse Open Graph product price meta tags."""
    price: float | None = None
    currency: str | None = None

    for match in re.finditer(
        r'<meta[^>]+property=["\']product:price:amount["\'][^>]*content=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    ):
        price = _coerce_price(match.group(1))

    for match in re.finditer(
        r'<meta[^>]+property=["\']product:price:currency["\'][^>]*content=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    ):
        currency = match.group(1).strip().upper()

    # Also support reversed attribute order: content=... property=...
    for match in re.finditer(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*property=["\']product:price:amount["\']',
        html,
        re.IGNORECASE,
    ):
        if price is None:
            price = _coerce_price(match.group(1))

    for match in re.finditer(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*property=["\']product:price:currency["\']',
        html,
        re.IGNORECASE,
    ):
        if currency is None:
            currency = match.group(1).strip().upper()

    return price, currency if price else None


def _extract_via_regex(html: str) -> tuple[float | None, str | None]:
    """Last-resort: find a price pattern like €29,99 or $12.99 in the HTML.

    Only returns a result if a clear, unambiguous match is found near common
    price-indicator class names (``itemprice``, ``price``, ``offer-price``).
    """
    pattern = re.compile(
        r'(?:class=["\'][^"\']*price[^"\']*["\'][^>]*>|price[^<]{0,40}?)'
        r'([€$£¥])\s*(\d{1,4}(?:[.,]\d{2,3})?)',
        re.IGNORECASE,
    )
    currency_map = {"€": "EUR", "$": "USD", "£": "GBP", "¥": "JPY"}
    for m in pattern.finditer(html):
        sym, amount_raw = m.group(1), m.group(2).replace(",", ".")
        price = _coerce_price(amount_raw)
        if price is not None:
            return price, currency_map.get(sym)
    return None, None


def _coerce_price(value: Any) -> float | None:
    """Normalise a raw price value to a Python float, or return None."""
    if value is None:
        return None
    s = str(value).strip()
    # Remove common non-numeric characters but preserve decimal separator
    s = re.sub(r"[^\d.,]", "", s)
    # Convert European comma-decimal to dot (e.g. "29,99" → "29.99")
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    elif "," in s and "." in s:
        # e.g. "1.299,99" → "1299.99"
        s = s.replace(".", "").replace(",", ".")
    try:
        val = float(Decimal(s))
        return val if val > 0 else None
    except (InvalidOperation, ValueError):
        return None


def extract_price_from_url(url: str, *, session: Any = None) -> tuple[float | None, str | None]:
    """Fetch *url* and extract the current price.

    Returns ``(price_float, currency_code)`` where both may be ``None`` if no
    price could be determined.  *session* is reserved for future use (e.g.
    passing an ``httpx.AsyncClient``).

    Extraction order:
    1. schema.org JSON-LD Product/Offer
    2. Open Graph ``product:price:amount`` / ``product:price:currency`` meta
    3. Regex heuristic near ``price``-labelled HTML elements
    """
    try:
        html = _fetch_html(url, session=session)
    except ValueError:
        return None, None

    price, currency = _extract_from_schema_org(html)
    if price is not None:
        return price, currency

    price, currency = _extract_from_og_meta(html)
    if price is not None:
        return price, currency

    return _extract_via_regex(html)


# ---------------------------------------------------------------------------
# Alert evaluation
# ---------------------------------------------------------------------------

def evaluate_price_alert(
    conn,
    item: dict[str, Any],
    *,
    new_price: float,
    currency: str | None = None,
) -> bool:
    """Update price columns and fire a notification if the alert threshold is met.

    Returns ``True`` if a notification was sent, ``False`` otherwise.
    """
    if not table_exists(conn, "wishlist_items"):
        return False

    item_id = item.get("id")
    user_id = item.get("user_id")
    target_price = item.get("target_price")
    alert_enabled = bool(item.get("alert_enabled"))

    if not item_id or not user_id:
        return False

    currency_value = (currency or item.get("price_currency") or "EUR").upper()

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE wishlist_items
                SET last_seen_price = %s,
                    price_currency = %s,
                    last_price_checked_at = now()
                WHERE id = %s AND user_id = %s
                """,
                (new_price, currency_value, item_id, user_id),
            )

    # Decide whether to notify.
    if not alert_enabled or target_price is None:
        return False

    try:
        threshold = float(Decimal(str(target_price)))
    except (InvalidOperation, ValueError):
        return False

    if new_price > threshold:
        return False

    # Respect cooldown.
    last_alerted = item.get("last_alerted_at")
    if last_alerted is not None:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT (now() - last_alerted_at) < interval '%s hours' AS in_cooldown
                FROM wishlist_items
                WHERE id = %s AND user_id = %s
                """,
                (_ALERT_COOLDOWN_HOURS, item_id, user_id),
            )
            row = cur.fetchone() or {}
            if row.get("in_cooldown"):
                return False

    # Fire notification.
    title_str = str(item.get("title") or "Wishlist item")
    price_display = f"{new_price:.2f} {currency_value}"
    target_display = f"{threshold:.2f} {currency_value}"
    notification_body = (
        f"{title_str} is now {price_display} — at or below your target of {target_display}."
    )
    price_url = item.get("price_url") or "/wishlist"

    try:
        create_user_notification(
            conn,
            user_id,
            title=f"Price alert: {title_str}",
            body=notification_body,
            url=price_url,
            pref_key="price_alerts",
            payload={
                "wishlistItemId": str(item_id),
                "price": new_price,
                "currency": currency_value,
                "targetPrice": threshold,
            },
        )
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE wishlist_items SET last_alerted_at = now() WHERE id = %s AND user_id = %s",
                    (item_id, user_id),
                )
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Sweep job
# ---------------------------------------------------------------------------

def run_price_alert_sweep(conn, *, limit: int = _SWEEP_BATCH_LIMIT) -> dict[str, Any]:
    """Process up to *limit* alert-enabled wishlist items that are due for a price check.

    Returns a summary dict with ``checked``, ``notified``, ``errors``, and
    ``skipped`` counts.
    """
    if not table_exists(conn, "wishlist_items"):
        return {"checked": 0, "notified": 0, "errors": 0, "skipped": 0, "status": "table_unavailable"}

    limit = min(max(int(limit or _SWEEP_BATCH_LIMIT), 1), 200)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, user_id, title, target_price, alert_enabled,
                   price_url, last_seen_price, price_currency,
                   last_price_checked_at, last_alerted_at, movievault_id
            FROM wishlist_items
            WHERE alert_enabled = true
              AND (
                  last_price_checked_at IS NULL
                  OR last_price_checked_at < now() - interval '{interval} hours'
              )
            ORDER BY last_price_checked_at ASC NULLS FIRST
            LIMIT %s
            """.format(interval=_RECHECK_INTERVAL_HOURS),
            (limit,),
        )
        items = cur.fetchall()

    checked = 0
    notified = 0
    errors = 0
    skipped = 0

    for item in items:
        price_url = item.get("price_url")
        movievault_id = item.get("movievault_id")

        new_price: float | None = None
        currency: str | None = None

        # --- URL-based extraction ---
        if price_url:
            try:
                new_price, currency = extract_price_from_url(price_url)
            except Exception:  # noqa: BLE001
                errors += 1
                continue

        # PLUGIN_HOOK: if new_price is None and movievault_id is set,
        # call run_plugin_entrypoint(plugin_id, "price_check",
        #   {"movievaultId": movievault_id, ...}) here.
        # For now, skip items where we cannot determine a price.

        if new_price is None:
            skipped += 1
            # Still update the checked_at so we don't hammer items with no price source.
            with conn.transaction():
                with conn.cursor() as cur2:
                    cur2.execute(
                        "UPDATE wishlist_items SET last_price_checked_at = now() WHERE id = %s",
                        (item.get("id"),),
                    )
            continue

        checked += 1
        try:
            fired = evaluate_price_alert(conn, item, new_price=new_price, currency=currency)
            if fired:
                notified += 1
        except Exception:  # noqa: BLE001
            errors += 1

    return {
        "checked": checked,
        "notified": notified,
        "errors": errors,
        "skipped": skipped,
        "status": "ok",
    }
