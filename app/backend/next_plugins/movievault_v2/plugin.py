"""Callback-only MovieVault distribution adapter for DiscVault 26."""

import hashlib

PROVIDER_ID = "movievault_v2"


def _callback(context, name):
    value = (context or {}).get(name)
    return value if callable(value) else None


def _error():
    return {"status": "error", "provider": PROVIDER_ID, "reason": "core_bridge_unavailable"}


def _limit(context):
    try:
        return min(max(int(((context or {}).get("settings") or {}).get("maximumResults", 12)), 1), 50)
    except (TypeError, ValueError):
        return 12


def _barcode_hash(value):
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) not in {8, 12, 13, 14}:
        return ""
    check = (10 - sum(int(digit) * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(digits[:-1]))) % 10) % 10
    return hashlib.sha256(digits.encode("ascii")).hexdigest() if check == int(digits[-1]) else ""


def _lookup(payload, context):
    callback = _callback(context, "movievaultV2Lookup")
    if callback is None:
        return None
    digest = _barcode_hash((payload or {}).get("barcode"))
    request = ({"kind": "barcode", "hash": digest, "limit": _limit(context)} if digest else {"kind": "title", "query": str((payload or {}).get("title") or "").strip(), "limit": _limit(context)})
    if request.get("query") == "":
        return []
    result = callback(request)
    return (result or {}).get("results") or []


def _poster_fields(record):
    # The core lookup already localizes the poster into an authenticated,
    # DiscVault-served URL (/api/next/movievault-v2/posters/...) when the cache
    # is ready; forward it verbatim so the cover reaches the PWA and iOS clients
    # alike. A pending/unavailable poster has no URL, so nothing is forwarded.
    fields = {}
    poster_url = record.get("posterUrl")
    if poster_url:
        fields["posterUrl"] = poster_url
        poster_status = record.get("posterStatus")
        if poster_status:
            fields["posterStatus"] = poster_status
    return fields


def _release(record):
    movie = {key: value for key, value in {
        "title": record.get("canonicalTitle") or record.get("releaseTitle") or "",
        "releaseTitle": record.get("releaseTitle"), "year": record.get("releaseYear"),
        "format": record.get("format"), "edition": record.get("edition"),
        "studio": record.get("studio"), "distributor": record.get("distributor"),
        "runtimeMinutes": record.get("runtimeMinutes"),
        **_poster_fields(record)}.items() if value not in (None, "")}
    return {key: value for key, value in {
        "provider": PROVIDER_ID, "id": record.get("releaseId"), "releaseId": record.get("releaseId"),
        "filmId": record.get("filmId"), "title": movie["title"], "movie": movie,
        "studio": record.get("studio"), "distributor": record.get("distributor"),
        "runtimeMinutes": record.get("runtimeMinutes"), "format": record.get("format"),
        "edition": record.get("edition"), "sourceRef": f"release:{record.get('releaseId') or ''}",
        **_poster_fields(record)}.items()
        if value not in (None, "", [], {})}


def _proposal(record):
    members = [{key: value for key, value in {
        "position": member.get("position"), "releaseId": member.get("releaseId"),
        "filmId": member.get("filmId"), "title": member.get("canonicalTitle"),
        "releaseTitle": member.get("releaseTitle"), "edition": member.get("releaseEdition"),
        "releaseEdition": member.get("releaseEdition"), "format": member.get("format"),
        "studio": member.get("studio"), "distributor": member.get("distributor"),
        "runtimeMinutes": member.get("runtimeMinutes"), "relationship": "contains"}.items()
        if value not in (None, "", [], {})} for member in record.get("members") or []]
    return {"id": record.get("boxSetId"), "boxSetId": record.get("boxSetId"), "title": record.get("title"), "members": members, "memberCount": len(members), "isBoxSet": True, **_poster_fields(record)}


def health_check(context=None):
    callback = _callback(context, "movievaultV2Status")
    if callback is None:
        return _error()
    state = callback({}) or {}
    return {"status": {"current": "available", "stale": "degraded", "syncing": "syncing", "unconfigured": "needs_configuration"}.get(state.get("state"), "unavailable"), "provider": PROVIDER_ID, **state}


def sync_index(payload=None, context=None):
    callback = _callback(context, "movievaultV2Sync")
    return _error() if callback is None else {"status": "completed", "provider": PROVIDER_ID, **(callback({}) or {})}


def search_barcode(payload, context=None):
    records = _lookup(payload, context)
    if records is None:
        return _error()
    release = next((item for item in records if item.get("recordType") == "release"), None)
    return {"status": "miss", "provider": PROVIDER_ID, "items": []} if release is None else {"status": "hit", **_release(release), "items": [_release(release)]}


def search_title(payload, context=None):
    records = _lookup(payload, context)
    if records is None:
        return _error()
    items = [_release(item) for item in records[:_limit(context)] if item.get("recordType") == "release"]
    return {"status": "hit" if items else "miss", "provider": PROVIDER_ID, "items": items}


def movie_details(payload, context=None):
    callback = _callback(context, "movievaultV2Lookup")
    if callback is None:
        return _error()
    release_id = str((payload or {}).get("releaseId") or (payload or {}).get("id") or "")
    records = (callback({"kind": "release", "releaseId": release_id, "limit": 1}) or {}).get("results") or []
    return {"status": "miss", "provider": PROVIDER_ID} if not records else {"status": "hit", **_release(records[0])}


def box_set_candidates(payload, context=None):
    records = _lookup(payload, context)
    if records is None:
        return _error()
    proposals = [_proposal(item) for item in records if item.get("recordType") == "box_set"]
    return {"status": "hit" if proposals else "miss", "provider": PROVIDER_ID, "boxSetProposal": proposals[0] if proposals else {}, "boxSetProposals": proposals, "items": proposals}
