"""Callback-only MovieVault distribution adapter for DiscVault 26."""

import hashlib
import uuid

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


def _release_uuid(payload):
    """The v4 catalog keys releases by UUID, so only a well-formed UUID is a usable lookup id.

    The metadata refresh planner hands this entrypoint the movie's stored `movieVaultId`, not a
    `releaseId` - so that key has to be accepted here or a refresh never reaches the catalog.
    But it is deliberately filtered: a `movievault_26`-era id (e.g. "mv_matrix") lives in a
    different namespace and is *not* a v4 release, so treating it as one would look up a
    different disc. Anything that isn't a UUID is simply no match for this provider.
    """
    for key in ("releaseId", "id", "movieVaultId", "movievaultId"):
        value = str((payload or {}).get(key) or "").strip()
        if not value:
            continue
        try:
            return str(uuid.UUID(value))
        except (ValueError, AttributeError, TypeError):
            continue
    return ""


def _poster_fields(record):
    # Core already resolved the poster into a URL a client can load: a
    # DiscVault-served one for a verified cached asset, or MovieVault's stable
    # anonymous asset URL when no checksum was published. Forward it verbatim so
    # the cover reaches the PWA and iOS alike. A pending/unavailable poster has
    # no URL, so nothing is forwarded.
    fields = {}
    poster_url = (record or {}).get("posterUrl")
    if poster_url:
        fields["posterUrl"] = poster_url
        poster_status = (record or {}).get("posterStatus")
        if poster_status:
            fields["posterStatus"] = poster_status
    return fields


def _resolved_details(payload, context):
    """Resolve the scanned barcode through the v2 resolver.

    The synced catalog carries a poster only once a v4 index sync has published
    one; the resolver answers per barcode and is the source the poster arrives
    on for a freshly scanned disc. Artwork is supplementary, so any resolver
    failure degrades to "no poster" and never fails the surrounding lookup."""
    callback = _callback(context, "movievaultV2ReleaseDetails")
    barcode = str((payload or {}).get("barcode") or "").strip()
    if callback is None or not _barcode_hash(barcode):
        return {}
    request = {"barcode": barcode}
    title = str((payload or {}).get("title") or "").strip()
    if title:
        request["title"] = title
    try:
        result = callback(request)
    except Exception:
        return {}
    if not isinstance(result, dict) or result.get("status") not in ("canonical_hit", "external_hit"):
        return {}
    return result


def _resolved_poster(details, *, box_set=False):
    """Pick the poster a resolver hit carries.

    On a box-set result the set's own cover is the more specific answer, so
    `boxSet` wins over `release` there; `release` describes a single disc inside
    the set. Individual members never carry their own artwork."""
    sections = (("boxSet", "release") if box_set else ("release",))
    for key in sections:
        section = (details or {}).get(key)
        if isinstance(section, dict) and section.get("posterUrl"):
            return _poster_fields(section)
    return {}


def _resolved_technical(details):
    """Audio tracks, subtitle languages and packaging the anonymous resolver
    carries for a scanned barcode. Only ever pulled from `release` - a box
    set's members are resolved individually and never inherit the set's
    technical data."""
    section = (details or {}).get("release")
    fields = {}
    if isinstance(section, dict):
        if section.get("audioTracks"):
            fields["audioTracks"] = section["audioTracks"]
        if section.get("subtitleLanguages"):
            fields["subtitleLanguages"] = section["subtitleLanguages"]
        if section.get("packaging"):
            fields["packaging"] = section["packaging"]
    return fields


def _release(record):
    movie = {key: value for key, value in {
        "title": record.get("canonicalTitle") or record.get("releaseTitle") or "",
        "releaseTitle": record.get("releaseTitle"), "year": record.get("releaseYear"),
        "format": record.get("format"), "edition": record.get("edition"),
        "studio": record.get("studio"), "distributor": record.get("distributor"),
        "runtimeMinutes": record.get("runtimeMinutes"),
        "audioTracks": record.get("audioTracks"), "subtitleLanguages": record.get("subtitleLanguages"),
        "packaging": record.get("packaging"),
        **_poster_fields(record)}.items() if value not in (None, "", [], {})}
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
    if release is None:
        return {"status": "miss", "provider": PROVIDER_ID, "items": []}
    # Fall back to the resolver's poster and/or technical specs when the
    # synced record doesn't carry them yet (not synced, or a v4-sync-disabled
    # instance still relying purely on the anonymous resolver).
    if not (release or {}).get("posterUrl") or not (release or {}).get("audioTracks"):
        details = _resolved_details(payload, context)
        resolved = {**_resolved_poster(details), **_resolved_technical(details)}
        if resolved:
            release = {**release, **resolved}
    item = _release(release)
    return {"status": "hit", **item, "items": [item]}


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
    release_id = _release_uuid(payload)
    if not release_id:
        return {"status": "miss", "provider": PROVIDER_ID}
    try:
        records = (callback({"kind": "release", "releaseId": release_id, "limit": 1}) or {}).get("results") or []
    except Exception:
        # A catalog that is unconfigured/mid-resync must degrade to a miss. Letting this raise
        # failed the *whole* plugin execution, so a refresh returned nothing from MovieVault at
        # all - no audio, subtitles or packaging - rather than just skipping this one lookup.
        return {"status": "miss", "provider": PROVIDER_ID}
    return {"status": "miss", "provider": PROVIDER_ID} if not records else {"status": "hit", **_release(records[0])}


def box_set_candidates(payload, context=None):
    records = _lookup(payload, context)
    if records is None:
        return _error()
    box_sets = [item for item in records if item.get("recordType") == "box_set"]
    # A scanned box-set's cover arrives on the resolver's `boxSet`, so fill it in
    # when the synced record has none. Only the first proposal matches the
    # scanned barcode, so the resolver's cover is applied to that one alone.
    if box_sets and not box_sets[0].get("posterUrl"):
        resolved = _resolved_poster(_resolved_details(payload, context), box_set=True)
        if resolved:
            box_sets = [{**box_sets[0], **resolved}, *box_sets[1:]]
    proposals = [_proposal(item) for item in box_sets]
    return {"status": "hit" if proposals else "miss", "provider": PROVIDER_ID, "boxSetProposal": proposals[0] if proposals else {}, "boxSetProposals": proposals, "items": proposals}
