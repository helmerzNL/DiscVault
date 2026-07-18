"""bol.com price-provider plugin."""

from __future__ import annotations

import json
import re
from typing import Any

import requests


DEFAULT_TIMEOUT = 20
DEFAULT_CURRENCY = "EUR"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
BLOCK_SIGNATURES = (
    "access denied",
    "bot verification",
    "captcha",
    "cloudflare",
    "automated requests",
)


def _settings(context: dict[str, Any] | None) -> dict[str, Any]:
    return (context or {}).get("settings") or {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _timeout_seconds(context: dict[str, Any] | None) -> int:
    raw = _text(_settings(context).get("timeoutSeconds") or _settings(context).get("timeout_seconds"))
    try:
        value = int(raw) if raw else DEFAULT_TIMEOUT
    except (TypeError, ValueError):
        value = DEFAULT_TIMEOUT
    return max(5, min(45, value))


def _fallback_currency(context: dict[str, Any] | None) -> str:
    value = _text(_settings(context).get("currency")).upper()
    return value or DEFAULT_CURRENCY


def _product_url(payload: dict[str, Any]) -> str:
    direct = _text(payload.get("url"))
    if direct:
        return direct
    ref = _text(payload.get("providerProductRef"))
    if ref.startswith("https://") or ref.startswith("http://"):
        return ref
    return ""


def _coerce_price(raw: Any) -> float | None:
    text = _text(raw)
    if not text:
        return None
    text = "".join(ch for ch in text if ch.isdigit() or ch in ".,")
    if not text:
        return None
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    elif "," in text and "." in text:
        text = text.replace(",", "")
    try:
        value = float(text)
        return round(value, 2) if value > 0 else None
    except (TypeError, ValueError):
        return None


def _extract_from_schema_org(html: str) -> tuple[float | None, str | None]:
    blocks = re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in blocks:
        try:
            parsed = json.loads(block.strip())
        except (TypeError, ValueError):
            continue
        stack = [parsed]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                if "offers" in current:
                    stack.append(current["offers"])
                if "price" in current:
                    price = _coerce_price(current.get("price"))
                    if price is not None:
                        currency = _text(current.get("priceCurrency")).upper() or None
                        return price, currency
                for nested in current.values():
                    if isinstance(nested, (dict, list)):
                        stack.append(nested)
            elif isinstance(current, list):
                stack.extend(current)
    return None, None


def _extract_from_open_graph(html: str) -> tuple[float | None, str | None]:
    amount_patterns = (
        r'<meta[^>]+property="product:price:amount"[^>]+content="([^"]+)"',
        r'<meta[^>]+content="([^"]+)"[^>]+property="product:price:amount"',
    )
    currency_patterns = (
        r'<meta[^>]+property="product:price:currency"[^>]+content="([^"]+)"',
        r'<meta[^>]+content="([^"]+)"[^>]+property="product:price:currency"',
    )
    raw_amount = ""
    for pattern in amount_patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            raw_amount = _text(match.group(1))
            break
    if not raw_amount:
        return None, None
    price = _coerce_price(raw_amount)
    if price is None:
        return None, None
    currency = None
    for pattern in currency_patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            currency = _text(match.group(1)).upper() or None
            break
    return price, currency


def _extract_via_regex(html: str) -> tuple[float | None, str | None]:
    symbol_match = re.search(r"(€|£|\$)\s*([0-9]+(?:[.,][0-9]{2})?)", html)
    if symbol_match:
        symbol = symbol_match.group(1)
        price = _coerce_price(symbol_match.group(2))
        if price is not None:
            return price, {"€": "EUR", "£": "GBP", "$": "USD"}.get(symbol)

    code_match = re.search(r"([0-9]+(?:[.,][0-9]{2})?)\s*(EUR|GBP|USD)", html, flags=re.IGNORECASE)
    if code_match:
        price = _coerce_price(code_match.group(1))
        if price is not None:
            return price, code_match.group(2).upper()

    return None, None


def health_check(context=None):
    return {
        "status": "available",
        "message": "bol.com provider is ready. Configure optional fallback currency/timeout if needed.",
    }


def price_check(payload=None, context=None):
    payload = payload or {}
    context = context or {}
    url = _product_url(payload)
    if not url:
        return {"status": "no_match", "error": "Provide a bol.com product URL in url or providerProductRef."}
    if "bol.com" not in url.lower():
        return {"status": "no_match", "error": "URL is not a bol.com product URL."}

    response = requests.get(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
        },
        timeout=_timeout_seconds(context),
    )
    response.raise_for_status()
    html = response.text or ""
    lower_html = html.lower()
    if any(signature in lower_html for signature in BLOCK_SIGNATURES):
        return {"status": "no_match", "error": "bol.com blocked the request (bot/challenge page)."}

    price, currency = _extract_from_schema_org(html)
    if price is None:
        price, currency = _extract_from_open_graph(html)
    if price is None:
        price, currency = _extract_via_regex(html)
    if price is None:
        return {"status": "no_match", "error": "No usable bol.com price found in the product page."}

    return {
        "status": "ok",
        "price": price,
        "currency": currency or _fallback_currency(context),
        "source": "bol",
        "source_detail": url,
        "confidence": 0.78,
    }

