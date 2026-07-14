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
import logging
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
    from .next_plugin_runtime import run_plugin_entrypoint
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_common import NextApiError, json_ready, table_exists
    from next_notifications import create_user_notification
    from next_plugin_runtime import run_plugin_entrypoint

PRICE_ALERT_JOB_TYPE = "price_alert.sweep"

# Items are rechecked at most every 6 hours.
_RECHECK_INTERVAL_HOURS = 6

# At most one notification per item per day.
_ALERT_COOLDOWN_HOURS = 24

# Maximum items processed in a single sweep run.
_SWEEP_BATCH_LIMIT = 50

# Timeout for HTTP price-fetch requests (seconds).
_FETCH_TIMEOUT = 10

_PRESET_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "amazon": ("amazon.",),
    "bol_com": ("bol.com",),
}

# Amazon-specific bot/CAPTCHA detection signatures.
_AMAZON_BOT_SIGNATURES: tuple[str, ...] = (
    "robot check",
    "captchacharacters",
    "automated access to amazon data",
    "sorry, we just need to make sure you",
    "/errors/validateCaptcha",
)

# Ordered list of Amazon price CSS selectors to try (most reliable first).
_AMAZON_PRICE_PATTERNS: tuple[str, ...] = (
    # Modern layout – hidden offscreen text inside the price badge
    r'class=["\'][^"\']*a-offscreen[^"\']*["\'][^>]*>\s*([^<]{2,30})\s*<',
    # Legacy explicit price blocks
    r'id=["\']priceblock_ourprice["\'][^>]*>\s*([^<]{2,20})\s*<',
    r'id=["\']priceblock_dealprice["\'][^>]*>\s*([^<]{2,20})\s*<',
    r'id=["\']priceblock_saleprice["\'][^>]*>\s*([^<]{2,20})\s*<',
    # Combined whole + fraction (e.g. "29" + "99") — uses .*? to cross the
    # </span> tag boundary between the two elements.
    r'class=["\'][^"\']*a-price-whole[^"\']*["\'][^>]*>\s*(\d+).*?'
    r'class=["\'][^"\']*a-price-fraction[^"\']*["\'][^>]*>\s*(\d+)',
)

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Price extraction from a URL
# ---------------------------------------------------------------------------

def _fetch_html(url: str, *, session: Any = None, browser_like: bool = False) -> str:
    """Return the raw HTML of *url*.  Raises ``ValueError`` on failure.

    When *browser_like* is True (used for sites with bot-detection), the
    request mimics a real Chrome browser as closely as possible over plain
    HTTP.
    """
    import urllib.request

    if browser_like:
        headers = {
            "User-Agent": _BROWSER_USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
            "DNT": "1",
        }
    else:
        headers = {
            "User-Agent": _BROWSER_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        }

    try:
        import gzip as _gzip
        import zlib as _zlib

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
            charset = resp.headers.get_content_charset() or "utf-8"
            raw = resp.read()
            encoding = resp.headers.get("Content-Encoding", "").lower().strip()
            if encoding == "gzip":
                raw = _gzip.decompress(raw)
            elif encoding == "deflate":
                try:
                    raw = _zlib.decompress(raw)
                except _zlib.error:
                    raw = _zlib.decompress(raw, -_zlib.MAX_WBITS)
            # brotli ("br") requires the optional `brotli` package; skip gracefully.
            return raw.decode(charset, errors="replace")
    except Exception as exc:
        raise ValueError(f"Could not fetch {url}: {exc}") from exc


def _is_amazon_bot_blocked(html: str) -> bool:
    """Return True when Amazon returned a bot/CAPTCHA challenge page."""
    lower = html.lower()
    return any(sig in lower for sig in _AMAZON_BOT_SIGNATURES)


def _extract_amazon_asin(url: str) -> str | None:
    """Extract the ASIN from any known Amazon URL format, or return None."""
    patterns = [
        r"/dp/([A-Z0-9]{10})",
        r"/gp/product/([A-Z0-9]{10})",
        r"/exec/obidos/(?:ASIN/)?([A-Z0-9]{10})",
        r"[?&]asin=([A-Z0-9]{10})",
        r"/([A-Z0-9]{10})(?:[/?]|$)",
    ]
    for pattern in patterns:
        m = re.search(pattern, url, re.IGNORECASE)
        if m:
            candidate = m.group(1).upper()
            if re.fullmatch(r"[A-Z0-9]{10}", candidate):
                return candidate
    return None


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


def _extract_text_by_css_selector(html: str, selector_value: str) -> str | None:
    selector = (selector_value or "").strip()
    if not selector:
        return None

    if selector.startswith("#"):
        token = re.escape(selector[1:])
        pattern = rf'<(?P<tag>\w+)[^>]*id=["\']{token}["\'][^>]*>(?P<text>.*?)</(?P=tag)>'
    elif selector.startswith("."):
        token = re.escape(selector[1:])
        pattern = (
            rf'<(?P<tag>\w+)[^>]*class=["\'][^"\']*{token}[^"\']*["\'][^>]*>'
            rf'(?P<text>.*?)</(?P=tag)>'
        )
    else:
        tag = re.escape(selector.lower())
        pattern = rf"<(?P<tag>{tag})[^>]*>(?P<text>.*?)</(?P=tag)>"

    match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    raw = re.sub(r"<[^>]+>", " ", match.group("text"))
    return re.sub(r"\s+", " ", raw).strip() or None


def _extract_text_by_regex_capture(html: str, selector_value: str) -> str | None:
    try:
        match = re.search(selector_value, html, re.IGNORECASE | re.DOTALL)
    except re.error:
        return None
    if not match:
        return None
    groups = [group for group in match.groups() if group]
    if groups:
        return str(groups[0]).strip() or None
    return str(match.group(0)).strip() or None


def _currency_from_text(text: str | None, fallback: str = "EUR") -> str:
    raw = str(text or "")
    if "$" in raw:
        return "USD"
    if "£" in raw:
        return "GBP"
    if "¥" in raw:
        return "JPY"
    if "€" in raw:
        return "EUR"
    return fallback


def _extract_price_from_profile(
    html: str,
    *,
    selector_type: str | None,
    selector_value: str | None,
    fallback_currency: str | None = None,
) -> tuple[float | None, str | None]:
    stype = (selector_type or "").strip().lower()
    svalue = (selector_value or "").strip()
    if not stype or not svalue:
        return None, None
    if stype == "css_text":
        text = _extract_text_by_css_selector(html, svalue)
    elif stype == "regex_capture":
        text = _extract_text_by_regex_capture(html, svalue)
    else:
        return None, None
    if not text:
        return None, None
    return _coerce_price(text), _currency_from_text(text, (fallback_currency or "EUR").upper())


def _extract_price_from_amazon_html(html: str, fallback_currency: str = "EUR") -> tuple[float | None, str | None]:
    """Try all known Amazon price patterns in priority order.

    Returns ``(price, currency)`` or ``(None, None)`` if nothing matched.
    Amazon's markup varies by product type and A/B test; we try multiple
    patterns so we are resilient to layout changes.
    """
    # Try multi-pattern selectors first (most specific).
    for pattern in _AMAZON_PRICE_PATTERNS:
        try:
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        except re.error:
            continue
        if not match:
            continue
        groups = [g for g in match.groups() if g and g.strip()]
        if not groups:
            continue
        # Whole + fraction pattern produces two groups (e.g. "29" + "99").
        if len(groups) >= 2 and re.fullmatch(r"\d+", groups[0].strip()) and re.fullmatch(r"\d+", groups[1].strip()):
            raw_text = f"{groups[0].strip()}.{groups[1].strip()}"
        else:
            raw_text = groups[0].strip()
        price = _coerce_price(raw_text)
        if price is not None:
            currency = _currency_from_text(raw_text, fallback_currency)
            return price, currency

    # Fall back to schema.org – Amazon does embed it on many product pages.
    price, currency = _extract_from_schema_org(html)
    if price is not None:
        return price, currency or fallback_currency

    return None, None


def _extract_price_from_domain_preset(url: str, html: str) -> tuple[float | None, str | None, str | None]:
    host = (urlparse(url).netloc or "").casefold()
    if any(keyword in host for keyword in _PRESET_DOMAIN_KEYWORDS["amazon"]):
        if _is_amazon_bot_blocked(html):
            # Signal blocked so callers can surface a clear message.
            return None, None, "blocked_amazon"
        price, currency = _extract_price_from_amazon_html(html)
        if price is not None:
            return price, currency or "EUR", "preset_amazon"

    if any(keyword in host for keyword in _PRESET_DOMAIN_KEYWORDS["bol_com"]):
        text = _extract_text_by_regex_capture(
            html,
            r'(?:data-test=["\']price["\'][^>]*>\s*([^<]+)\s*<|class=["\'][^"\']*promo-price[^"\']*["\'][^>]*>\s*([^<]+)\s*<)',
        )
        if text:
            return _coerce_price(text), _currency_from_text(text, "EUR"), "preset"

    return None, None, None


def extract_price_from_url_with_source(
    url: str,
    *,
    session: Any = None,
    selector_type: str | None = None,
    selector_value: str | None = None,
    selector_options: dict[str, Any] | None = None,
) -> tuple[float | None, str | None, str | None]:
    """Fetch *url* and extract the current price.

    Returns ``(price_float, currency_code, extraction_source)`` where all
    values may be ``None`` if no price could be determined.

    Extraction order:
    1. Domain preset (Amazon multi-selector / Bol.com) with browser-like headers
    2. Per-shop saved selector profile (css_text / regex_capture)
    3. schema.org JSON-LD Product/Offer
    4. Open Graph ``product:price:amount`` / ``product:price:currency`` meta
    5. Regex heuristic near ``price``-labelled HTML elements

    For Amazon URLs the fetch uses realistic browser headers (Chrome UA +
    full Accept/Sec-Fetch headers) to avoid bot-detection.  If Amazon
    returns a CAPTCHA/robot-check page the extraction source is set to
    ``"blocked_amazon"`` and ``(None, None, "blocked_amazon")`` is returned
    so callers can surface a clear diagnostic message.
    """
    host = (urlparse(url).netloc or "").casefold()
    is_amazon = any(keyword in host for keyword in _PRESET_DOMAIN_KEYWORDS["amazon"])

    try:
        html = _fetch_html(url, session=session, browser_like=is_amazon)
    except ValueError:
        return None, None, None

    fallback_currency = str((selector_options or {}).get("currency") or "EUR").strip().upper() or "EUR"

    price, currency, source = _extract_price_from_domain_preset(url, html)
    if source == "blocked_amazon":
        # Propagate the block signal without a price so callers know why.
        return None, None, "blocked_amazon"
    if price is not None:
        return price, (currency or fallback_currency), (source or "preset")

    price, currency = _extract_price_from_profile(
        html,
        selector_type=selector_type,
        selector_value=selector_value,
        fallback_currency=fallback_currency,
    )
    if price is not None:
        return price, currency or fallback_currency, "profile"

    price, currency = _extract_from_schema_org(html)
    if price is not None:
        return price, currency, "schema_org"

    price, currency = _extract_from_og_meta(html)
    if price is not None:
        return price, currency, "og"

    price, currency = _extract_via_regex(html)
    if price is not None:
        return price, currency, "regex"
    return None, None, None


def extract_price_from_url(url: str, *, session: Any = None) -> tuple[float | None, str | None]:
    price, currency, _source = extract_price_from_url_with_source(url, session=session)
    return price, currency


def _enabled_price_provider_plugin_ids(conn) -> list[str]:
    if not table_exists(conn, "plugins"):
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id
            FROM plugins
            WHERE installed = true
              AND enabled = true
              AND (
                  capabilities @> '["price_check"]'::jsonb
                  OR categories @> '["price_provider"]'::jsonb
              )
            ORDER BY order_index ASC, id ASC
            """
        )
        rows = cur.fetchall()
    return [str(row.get("id")) for row in rows if row.get("id")]


def _provider_result_value(plugin_result: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in plugin_result:
            return plugin_result.get(key)
    return None


def _run_price_provider_check(
    plugin_id: str,
    *,
    item_id: Any,
    movievault_id: Any = None,
    shop: dict[str, Any] | None = None,
) -> tuple[float | None, str | None, str | None, str | None, str | None, str | None, float | None]:
    logger.info(
        "price_provider_check_start plugin=%s item=%s shop=%s url=%s",
        plugin_id,
        item_id,
        (shop or {}).get("id"),
        (shop or {}).get("price_url"),
    )
    payload: dict[str, Any] = {"itemId": str(item_id) if item_id else None}
    if movievault_id:
        payload["movievaultId"] = str(movievault_id)
    if shop:
        payload["shopId"] = str(shop.get("id")) if shop.get("id") else None
        payload["url"] = shop.get("price_url")
        payload["currency"] = shop.get("price_currency")
        payload["providerProductRef"] = shop.get("provider_product_ref")
        payload["shopName"] = shop.get("shop_name")

    execution = run_plugin_entrypoint(plugin_id, "price_check", payload, {})
    execution_status = str(execution.get("status") or "").lower()
    plugin_result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
    provider_status = str(_provider_result_value(plugin_result, "status") or execution_status or "error")
    provider_error = str(
        _provider_result_value(plugin_result, "error")
        or execution.get("error")
        or ""
    ).strip() or None

    if execution_status != "ok":
        logger.warning(
            "price_provider_check_failed plugin=%s item=%s shop=%s status=%s error=%s",
            plugin_id,
            item_id,
            (shop or {}).get("id"),
            provider_status,
            provider_error,
        )
        return None, None, None, provider_status, provider_error, None, None

    raw_price = _provider_result_value(plugin_result, "price")
    price = _coerce_price(raw_price)
    if price is None:
        logger.warning(
            "price_provider_check_no_price plugin=%s item=%s shop=%s status=%s error=%s",
            plugin_id,
            item_id,
            (shop or {}).get("id"),
            provider_status,
            provider_error,
        )
        return None, None, None, provider_status, provider_error, None, None

    currency = str(_provider_result_value(plugin_result, "currency") or "").strip().upper() or None
    source_detail = str(
        _provider_result_value(plugin_result, "source_detail", "sourceDetail", "source")
        or ""
    ).strip() or None
    confidence_raw = _provider_result_value(plugin_result, "confidence")
    confidence = None
    if confidence_raw is not None:
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError):
            confidence = None

    logger.info(
        "price_provider_check_ok plugin=%s item=%s shop=%s price=%s currency=%s source_detail=%s",
        plugin_id,
        item_id,
        (shop or {}).get("id"),
        price,
        currency,
        source_detail,
    )
    return price, currency, f"provider:{plugin_id}", provider_status, provider_error, source_detail, confidence


def _record_shop_price(
    conn,
    *,
    shop_id: Any,
    item_id: Any,
    user_id: Any,
    price: float | None,
    currency: str | None,
    extraction_source: str | None = None,
    provider_id: str | None = None,
    provider_status: str | None = None,
    provider_error: str | None = None,
    source_detail: str | None = None,
    confidence: float | None = None,
) -> None:
    if not table_exists(conn, "wishlist_item_shops"):
        return
    currency_value = str(currency or "EUR").strip().upper() or "EUR"
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE wishlist_item_shops
                SET last_seen_price = %s,
                    price_currency = %s,
                    provider_last_status = %s,
                    provider_last_error = %s,
                    last_price_checked_at = now(),
                    updated_at = now()
                WHERE id = %s AND wishlist_item_id = %s AND user_id = %s
                """,
                (
                    price,
                    currency_value,
                    provider_status,
                    provider_error,
                    shop_id,
                    item_id,
                    user_id,
                ),
            )
            if price is not None and table_exists(conn, "wishlist_item_shop_prices"):
                cur.execute(
                    """
                    INSERT INTO wishlist_item_shop_prices
                        (user_id, wishlist_item_id, wishlist_item_shop_id, observed_price, price_currency, extraction_source, provider_id, source_detail, confidence)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        item_id,
                        shop_id,
                        price,
                        currency_value,
                        extraction_source or "unknown",
                        provider_id,
                        source_detail,
                        confidence,
                    ),
                )


def _fetch_item_prices_from_shops(conn, item: dict[str, Any]) -> tuple[float | None, str | None]:
    if not table_exists(conn, "wishlist_item_shops"):
        return None, None
    item_id = item.get("id")
    user_id = item.get("user_id")
    if not item_id or not user_id:
        return None, None

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, shop_name, price_url, price_currency, selector_type, selector_value, selector_options,
                   provider_plugin_id, provider_product_ref
            FROM wishlist_item_shops
            WHERE wishlist_item_id = %s AND user_id = %s
            ORDER BY created_at ASC
            LIMIT 10
            """,
            (item_id, user_id),
        )
        shops = cur.fetchall()

    best_price: float | None = None
    best_currency: str | None = None
    for shop in shops:
        raw_url = (shop.get("price_url") or "").strip()
        provider_id = clean_provider_id = str(shop.get("provider_plugin_id") or "").strip() or None
        provider_status: str | None = None
        provider_error: str | None = None
        source_detail: str | None = None
        confidence: float | None = None
        extraction_source: str | None = None
        shop_price: float | None = None
        detected_currency: str | None = None

        if clean_provider_id:
            try:
                (
                    shop_price,
                    detected_currency,
                    extraction_source,
                    provider_status,
                    provider_error,
                    source_detail,
                    confidence,
                ) = _run_price_provider_check(
                    clean_provider_id,
                    item_id=item_id,
                    movievault_id=item.get("movievault_id"),
                    shop=shop,
                )
            except Exception as exc:  # noqa: BLE001
                provider_status = "error"
                provider_error = str(exc)
                logger.warning(
                    "price_provider_check_exception plugin=%s item=%s shop=%s error=%s",
                    clean_provider_id,
                    item_id,
                    shop.get("id"),
                    provider_error,
                )

        if not raw_url:
            _record_shop_price(
                conn,
                shop_id=shop.get("id"),
                item_id=item_id,
                user_id=user_id,
                price=shop_price,
                currency=detected_currency or shop.get("price_currency"),
                extraction_source=extraction_source,
                provider_id=provider_id,
                provider_status=provider_status or ("ok" if shop_price is not None else "no_source"),
                provider_error=provider_error,
                source_detail=source_detail,
                confidence=confidence,
            )
            continue

        if shop_price is None:
            try:
                shop_price, detected_currency, extraction_source = extract_price_from_url_with_source(
                    raw_url,
                    selector_type=shop.get("selector_type"),
                    selector_value=shop.get("selector_value"),
                    selector_options=shop.get("selector_options") if isinstance(shop.get("selector_options"), dict) else {},
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "price_url_extract_exception item=%s shop=%s url=%s provider=%s",
                    item_id,
                    shop.get("id"),
                    raw_url,
                    provider_id,
                    exc_info=True,
                )
                _record_shop_price(
                    conn,
                    shop_id=shop.get("id"),
                    item_id=item_id,
                    user_id=user_id,
                    price=None,
                    currency=shop.get("price_currency"),
                    extraction_source="failed",
                    provider_id=provider_id,
                    provider_status=provider_status or "error",
                    provider_error=provider_error,
                    source_detail=source_detail,
                    confidence=confidence,
                )
                continue
        if shop_price is None:
            logger.warning(
                "price_check_no_price item=%s shop=%s url=%s provider=%s provider_status=%s provider_error=%s extraction_source=%s",
                item_id,
                shop.get("id"),
                raw_url,
                provider_id,
                provider_status,
                provider_error,
                extraction_source,
            )
        effective_currency = str(detected_currency or shop.get("price_currency") or "EUR").strip().upper() or "EUR"
        _record_shop_price(
            conn,
            shop_id=shop.get("id"),
            item_id=item_id,
            user_id=user_id,
            price=shop_price,
            currency=effective_currency,
            extraction_source=extraction_source,
            provider_id=provider_id,
            provider_status=provider_status or ("ok" if shop_price is not None else "no_match"),
            provider_error=provider_error,
            source_detail=source_detail,
            confidence=confidence,
        )
        if shop_price is None:
            continue
        if best_price is None or shop_price < best_price:
            best_price = shop_price
            best_currency = effective_currency

    return best_price, best_currency


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

    expected_currency = str(item.get("price_currency") or "EUR").strip().upper() or "EUR"
    seen_currency = str(currency or expected_currency).strip().upper() or expected_currency
    currency_value = expected_currency

    # Avoid comparing prices across currencies (no conversion support).
    if currency and seen_currency != expected_currency:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE wishlist_items SET last_price_checked_at = now() WHERE id = %s AND user_id = %s",
                    (item_id, user_id),
                )
        return False

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE wishlist_items
                SET last_seen_price = %s,
                    last_price_checked_at = now()
                WHERE id = %s AND user_id = %s
                """,
                (new_price, item_id, user_id),
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
        item_id = item.get("id")

        new_price: float | None = None
        currency: str | None = None

        # --- Multi-shop extraction ---
        try:
            new_price, currency = _fetch_item_prices_from_shops(conn, item)
        except Exception:  # noqa: BLE001
            errors += 1
            continue

        # --- URL-based extraction (legacy fallback when no shop price is available) ---
        if new_price is None and price_url:
            try:
                new_price, currency = extract_price_from_url(price_url)
            except Exception:  # noqa: BLE001
                errors += 1
                continue

        # --- Plugin-based extraction (fallback when URL yields nothing) ---
        if new_price is None and movievault_id:
            try:
                for plugin_id in _enabled_price_provider_plugin_ids(conn):
                    (
                        plugin_price,
                        plugin_currency,
                        _source,
                        _provider_status,
                        _provider_error,
                        _source_detail,
                        _confidence,
                    ) = _run_price_provider_check(
                        plugin_id,
                        item_id=item.get("id"),
                        movievault_id=movievault_id,
                    )
                    if plugin_price is not None:
                        new_price = plugin_price
                        currency = plugin_currency or currency
                        break
            except Exception:  # noqa: BLE001
                pass

        if new_price is None:
            skipped += 1
            # Still update the checked_at so we don't hammer items with no price source.
            with conn.transaction():
                with conn.cursor() as cur2:
                    cur2.execute(
                        "UPDATE wishlist_items SET last_price_checked_at = now() WHERE id = %s",
                        (item_id,),
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
