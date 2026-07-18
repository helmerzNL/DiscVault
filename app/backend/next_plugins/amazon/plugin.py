"""Amazon price-provider plugin."""

from __future__ import annotations

import json
import re
from typing import Any

import requests


DEFAULT_TIMEOUT = 20
DEFAULT_CURRENCY = "EUR"
DEFAULT_DOMAIN = "www.amazon.nl"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
BLOCK_SIGNATURES = (
    "robot check",
    "captchacharacters",
    "automated access to amazon data",
    "sorry, we just need to make sure you",
    "/errors/validatecaptcha",
)
PRICE_PATTERNS = (
    r'class=["\'][^"\']*a-offscreen[^"\']*["\'][^>]*>\s*([^<]{2,30})\s*<',
    r'id=["\']priceblock_ourprice["\'][^>]*>\s*([^<]{2,20})\s*<',
    r'id=["\']priceblock_dealprice["\'][^>]*>\s*([^<]{2,20})\s*<',
    r'id=["\']priceblock_saleprice["\'][^>]*>\s*([^<]{2,20})\s*<',
    r'class=["\'][^"\']*a-price-whole[^"\']*["\'][^>]*>\s*(\d+).*?'
    r'class=["\'][^"\']*a-price-fraction[^"\']*["\'][^>]*>\s*(\d+)',
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


def _default_domain(context: dict[str, Any] | None) -> str:
    value = _text(_settings(context).get("defaultDomain") or _settings(context).get("default_domain")).lower()
    return value or DEFAULT_DOMAIN


def _extract_asin(value: Any) -> str:
    text = _text(value).upper()
    if re.fullmatch(r"[A-Z0-9]{10}", text):
        return text
    patterns = (
        r"/dp/([A-Z0-9]{10})",
        r"/gp/product/([A-Z0-9]{10})",
        r"/exec/obidos/(?:ASIN/)?([A-Z0-9]{10})",
        r"[?&]asin=([A-Z0-9]{10})",
        r"/([A-Z0-9]{10})(?:[/?]|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return ""


def _product_url(payload: dict[str, Any], context: dict[str, Any] | None = None) -> tuple[str, str]:
    direct = _text(payload.get("url"))
    asin_from_ref = _extract_asin(payload.get("providerProductRef"))
    asin = asin_from_ref or _extract_asin(direct)
    if asin:
        domain = _default_domain(context)
        return f"https://{domain}/dp/{asin}", asin
    if direct.startswith("https://") or direct.startswith("http://"):
        return direct, ""
    return "", ""


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


def _currency_from_text(text: str, fallback: str = "EUR") -> str:
    if "€" in text:
        return "EUR"
    if "£" in text:
        return "GBP"
    if "$" in text:
        return "USD"
    upper = text.upper()
    for code in ("EUR", "GBP", "USD"):
        if code in upper:
            return code
    return fallback


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


def _extract_amazon_price(html: str, fallback_currency: str) -> tuple[float | None, str | None]:
    for pattern in PRICE_PATTERNS:
        try:
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        except re.error:
            continue
        if not match:
            continue
        groups = [group for group in match.groups() if group and group.strip()]
        if not groups:
            continue
        if len(groups) >= 2 and re.fullmatch(r"\d+", groups[0]) and re.fullmatch(r"\d+", groups[1]):
            raw_text = f"{groups[0]}.{groups[1]}"
        else:
            raw_text = groups[0]
        price = _coerce_price(raw_text)
        if price is not None:
            return price, _currency_from_text(raw_text, fallback_currency)
    return _extract_from_schema_org(html)


def health_check(context=None):
    return {
        "status": "available",
        "message": "Amazon provider is ready. Configure optional fallback currency/timeout if needed.",
    }


def price_check(payload=None, context=None):
    payload = payload or {}
    context = context or {}
    url, asin = _product_url(payload, context)
    if not url:
        return {"status": "no_match", "error": "Provide an Amazon product URL or ASIN in providerProductRef."}
    if "amazon." not in url.lower():
        return {"status": "no_match", "error": "URL is not an Amazon product URL."}

    response = requests.get(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-GB,en;q=0.9,nl;q=0.8",
            "Accept-Encoding": "gzip, deflate",
        },
        timeout=_timeout_seconds(context),
    )
    response.raise_for_status()
    html = response.text or ""
    lower_html = html.lower()
    if any(signature in lower_html for signature in BLOCK_SIGNATURES):
        return {"status": "no_match", "error": "Amazon blocked the request (bot/challenge page)."}

    price, currency = _extract_amazon_price(html, _fallback_currency(context))
    if price is None:
        return {"status": "no_match", "error": "No usable Amazon price found in the product page."}

    return {
        "status": "ok",
        "price": price,
        "currency": currency or _fallback_currency(context),
        "source": "amazon",
        "source_detail": asin or url,
        "confidence": 0.7,
        "providerProductRef": asin or None,
    }
