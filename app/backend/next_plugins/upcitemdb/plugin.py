import re

import requests


def _detect_format(value):
    text = str(value or "").lower()
    if re.search(r"4k|uhd|ultra[\s-]*hd", text):
        return "4K UHD"
    if re.search(r"blu[- ]?ray", text):
        return "Blu-ray"
    if re.search(r"\bdvd\b", text):
        return "DVD"
    return ""


def health_check(context=None):
    return {
        "status": "available",
        "message": "UPCItemDB trial lookup is available.",
    }


def search_barcode(payload, context=None):
    barcode = str((payload or {}).get("barcode") or "").strip()
    if not barcode:
        return {"status": "skipped", "provider": "upcitemdb", "items": []}
    response = requests.get(
        "https://api.upcitemdb.com/prod/trial/lookup",
        params={"upc": barcode},
        timeout=6,
    )
    response.raise_for_status()
    items = response.json().get("items") or []
    if not items:
        return {"status": "miss", "provider": "upcitemdb", "barcode": barcode, "items": []}
    normalized = []
    for item in items[:5]:
        title = str(item.get("title") or "").strip()
        normalized.append(
            {
                "provider": "upcitemdb",
                "providerLabel": "UPCItemDB",
                "title": title,
                "barcode": barcode,
                "detectedFormat": _detect_format(title),
                "brand": item.get("brand") or "",
                "category": item.get("category") or "",
                "sourceUrl": item.get("detailPageURL") or "",
            }
        )
    first = normalized[0]
    return {
        "status": "hit",
        "provider": "upcitemdb",
        "sourceLabel": "UPCItemDB",
        "barcode": barcode,
        "items": normalized,
        "movie": {
            "title": first.get("title") or "",
            "format": first.get("detectedFormat") or "",
        },
    }
