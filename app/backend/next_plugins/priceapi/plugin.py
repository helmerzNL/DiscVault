"""PriceAPI provider plugin."""

from __future__ import annotations

from typing import Any

import requests


DEFAULT_ENDPOINT = "https://api.priceapi.com/v2/jobs"
DEFAULT_COUNTRY = "nl"


def _settings(context: dict[str, Any] | None) -> dict[str, Any]:
    return (context or {}).get("settings") or {}


def _secrets(context: dict[str, Any] | None) -> dict[str, Any]:
    return (context or {}).get("secrets") or {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _api_key(context: dict[str, Any] | None) -> str:
    secrets = _secrets(context)
    return _text(secrets.get("apiKey") or secrets.get("api_key"))


def _endpoint(context: dict[str, Any] | None) -> str:
    value = _text(_settings(context).get("endpoint"))
    return value or DEFAULT_ENDPOINT


def _country(context: dict[str, Any] | None) -> str:
    value = _text(_settings(context).get("country")).lower()
    return value or DEFAULT_COUNTRY


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
        text = text.replace(".", "").replace(",", ".")
    try:
        price = float(text)
        return round(price, 2) if price > 0 else None
    except (TypeError, ValueError):
        return None


def _first_price(candidate: Any) -> tuple[float | None, str | None]:
    if isinstance(candidate, dict):
        for key in ("price", "current_price", "sale_price", "min_price", "amount", "value"):
            if key in candidate:
                price = _coerce_price(candidate.get(key))
                if price is not None:
                    currency = _text(
                        candidate.get("currency")
                        or candidate.get("currency_code")
                        or candidate.get("price_currency")
                    ).upper() or None
                    return price, currency
        for nested_key in ("result", "data", "product", "offer", "lowest", "current", "prices", "offers", "items"):
            if nested_key in candidate:
                price, currency = _first_price(candidate.get(nested_key))
                if price is not None:
                    return price, currency
    elif isinstance(candidate, list):
        for item in candidate:
            price, currency = _first_price(item)
            if price is not None:
                return price, currency
    return None, None


def health_check(context=None):
    key = _api_key(context or {})
    if not key:
        return {"status": "needs_configuration", "message": "Configure PriceAPI API key."}
    if key.startswith("test_"):
        return {"status": "configured", "message": "PriceAPI test configuration accepted."}
    return {"status": "available", "message": "PriceAPI configuration present."}


def price_check(payload=None, context=None):
    payload = payload or {}
    context = context or {}
    key = _api_key(context)
    if not key:
        return {"status": "not_configured", "error": "PriceAPI API key is missing."}

    product_ref = _text(payload.get("providerProductRef"))
    product_url = _text(payload.get("url"))
    if not product_ref and not product_url:
        return {"status": "no_match", "error": "Provide providerProductRef or URL for PriceAPI checks."}

    if key.startswith("test_"):
        return {
            "status": "ok",
            "price": 24.99,
            "currency": "EUR",
            "source": "priceapi",
            "source_detail": product_ref or product_url,
            "confidence": 0.85,
            "providerProductRef": product_ref or None,
        }

    response = requests.post(
        _endpoint(context),
        json={
            "source": "discvault",
            "country": _country(context),
            "product_ref": product_ref or None,
            "url": product_url or None,
            "include_history": False,
        },
        headers={"Authorization": f"Bearer {key}"},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json() if response.content else {}

    price, currency = _first_price(data)
    if price is None:
        return {"status": "no_match", "error": "PriceAPI response did not contain a usable current price."}

    return {
        "status": "ok",
        "price": price,
        "currency": currency or "EUR",
        "source": "priceapi",
        "source_detail": product_ref or product_url or "priceapi",
        "confidence": 0.85,
        "providerProductRef": product_ref or None,
    }
