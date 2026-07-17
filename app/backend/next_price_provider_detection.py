"""Shared price-provider URL detection and product-reference derivation."""

from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import urlparse


_AMAZON_ASIN_PATTERNS = (
    re.compile(r"/dp/([A-Z0-9]{10})(?:[/?]|$)", re.IGNORECASE),
    re.compile(r"/gp/product/([A-Z0-9]{10})(?:[/?]|$)", re.IGNORECASE),
    re.compile(r"[?&]asin=([A-Z0-9]{10})(?:[&#]|$)", re.IGNORECASE),
    re.compile(r"/exec/obidos/(?:ASIN/)?([A-Z0-9]{10})(?:[/?]|$)", re.IGNORECASE),
    re.compile(r"/([A-Z0-9]{10})(?:[/?]|$)", re.IGNORECASE),
)


def extract_amazon_asin(value: Any) -> str | None:
    text = str(value or "").strip()
    for pattern in _AMAZON_ASIN_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1).upper()
    return None


def price_provider_metadata(provider: dict[str, Any]) -> dict[str, Any]:
    manifest = provider.get("manifest")
    raw = manifest.get("priceProvider") if isinstance(manifest, dict) else None
    if not isinstance(raw, dict):
        raw = provider.get("priceProvider")
    if not isinstance(raw, dict):
        return {}

    host_patterns = []
    for value in raw.get("hostPatterns") or []:
        pattern = str(value or "").strip().lower()
        if pattern and pattern not in host_patterns:
            host_patterns.append(pattern)

    result: dict[str, Any] = {"hostPatterns": host_patterns}
    product_ref = raw.get("productRef")
    if isinstance(product_ref, dict):
        product_ref_type = str(product_ref.get("type") or "").strip().lower()
        if product_ref_type:
            result["productRef"] = {"type": product_ref_type}
    return result


def price_provider_is_usable(provider: dict[str, Any]) -> bool:
    if not provider.get("installed") or not provider.get("enabled"):
        return False
    if provider.get("requiresSecrets") and not provider.get("secretsConfigured"):
        return False
    return True


def price_provider_entity(provider: dict[str, Any]) -> dict[str, Any]:
    result = dict(provider)
    result["priceProvider"] = price_provider_metadata(provider)
    result["usable"] = price_provider_is_usable(provider)
    return result


def _host_matches_pattern(host: str, pattern: str) -> bool:
    if pattern.endswith("."):
        token = pattern[:-1]
        return bool(token) and (
            host.startswith(f"{token}.")
            or f".{token}." in host
        )
    return host == pattern or host.endswith(f".{pattern}")


def derive_provider_product_ref(url: Any, provider: dict[str, Any]) -> str | None:
    metadata = price_provider_metadata(provider)
    product_ref = metadata.get("productRef")
    if isinstance(product_ref, dict) and product_ref.get("type") == "amazon_asin":
        return extract_amazon_asin(url)
    return None


def detect_price_provider(
    url: Any,
    providers: Iterable[dict[str, Any]],
) -> tuple[str | None, str | None]:
    parsed = urlparse(str(url or "").strip())
    host = str(parsed.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if parsed.scheme not in {"http", "https"} or not host:
        return None, None

    ordered = sorted(
        providers,
        key=lambda provider: (
            int(provider.get("orderIndex") or 100),
            str(provider.get("id") or ""),
        ),
    )
    for provider in ordered:
        provider_id = str(provider.get("id") or "").strip()
        if not provider_id or not price_provider_is_usable(provider):
            continue
        metadata = price_provider_metadata(provider)
        patterns = metadata.get("hostPatterns") or []
        if any(_host_matches_pattern(host, pattern) for pattern in patterns):
            return provider_id, derive_provider_product_ref(url, provider)
    return None, None
