"""Keepa price-provider plugin (Amazon-focused)."""

from __future__ import annotations

import re
from typing import Any

import requests


DEFAULT_DOMAIN_ID = 4  # Amazon NL


def _settings(context: dict[str, Any] | None) -> dict[str, Any]:
    return (context or {}).get("settings") or {}


def _secrets(context: dict[str, Any] | None) -> dict[str, Any]:
    return (context or {}).get("secrets") or {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _api_key(context: dict[str, Any] | None) -> str:
    secrets = _secrets(context)
    return _text(secrets.get("apiKey") or secrets.get("api_key"))


def _domain_id(context: dict[str, Any] | None) -> int:
    raw = _text(_settings(context).get("domainId") or _settings(context).get("domain_id"))
    try:
        return int(raw) if raw else DEFAULT_DOMAIN_ID
    except (TypeError, ValueError):
        return DEFAULT_DOMAIN_ID


def _extract_asin(payload: dict[str, Any]) -> str:
    from_ref = _text(payload.get("providerProductRef"))
    if re.fullmatch(r"[A-Z0-9]{10}", from_ref.upper()):
        return from_ref.upper()
    url = _text(payload.get("url"))
    patterns = (
        r"/dp/([A-Z0-9]{10})",
        r"/gp/product/([A-Z0-9]{10})",
        r"[?&]asin=([A-Z0-9]{10})",
    )
    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return ""


def _price_from_product(product: dict[str, Any]) -> float | None:
    candidates = (
        product.get("buyBoxPrice"),
        product.get("amazonPrice"),
        product.get("newPrice"),
        product.get("listPrice"),
    )
    for value in candidates:
        if isinstance(value, (int, float)) and value > 0:
            return round(float(value) / 100.0, 2)
    return None


def health_check(context=None):
    key = _api_key(context or {})
    if not key:
        return {"status": "needs_configuration", "message": "Configure Keepa API key."}
    if key.startswith("test_"):
        return {"status": "configured", "message": "Keepa test configuration accepted."}
    return {"status": "available", "message": "Keepa configuration present."}


def price_check(payload=None, context=None):
    payload = payload or {}
    context = context or {}
    asin = _extract_asin(payload)
    if not asin:
        return {"status": "no_match", "error": "No ASIN found in providerProductRef or URL."}

    key = _api_key(context)
    if not key:
        return {"status": "not_configured", "error": "Keepa API key is missing."}

    if key.startswith("test_"):
        return {
            "status": "ok",
            "price": 19.99,
            "currency": "EUR",
            "source": "keepa",
            "source_detail": "test-mode",
            "confidence": 0.9,
            "providerProductRef": asin,
        }

    response = requests.get(
        "https://api.keepa.com/product",
        params={
            "key": key,
            "domain": _domain_id(context),
            "asin": asin,
            "buybox": 1,
            "history": 0,
            "stats": 0,
        },
        timeout=15,
    )
    response.raise_for_status()
    data = response.json() if response.content else {}
    products = data.get("products") if isinstance(data, dict) else []
    product = products[0] if isinstance(products, list) and products else {}
    price = _price_from_product(product if isinstance(product, dict) else {})
    if price is None:
        return {"status": "no_match", "error": "Keepa response did not contain a usable current price."}

    return {
        "status": "ok",
        "price": price,
        "currency": "EUR",
        "source": "keepa",
        "source_detail": asin,
        "confidence": 0.95,
        "providerProductRef": asin,
    }

