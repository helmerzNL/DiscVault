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
    return hashlib.sha256(digits.encode("ascii")).hexdigest()


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


def _bucket_lookup(payload, context):
    """Resolve a barcode against MovieVault's anonymous bucket index.

    The local index only carries what this instance has synced, so a disc
    MovieVault knows about but has not distributed here yet misses entirely.
    Buckets are keyed by the SHA-256 of the EAN, which is why this only serves
    barcode lookups - there is no title index to fall back to.

    Core already degrades every failure to an empty result set; the guard here is
    belt-and-braces because the plugin runs in-process and a raise would fail the
    surrounding lookup rather than just the fallback.

    Returns {"records": [...], "attempted": bool, "outcome": "hit"|"miss"|"error"|None,
    "errorCode": str|None}. `outcome`/`errorCode` carry no user-facing data - they exist
    so a caller can tell a real catalog miss apart from a bucket lookup that was never
    reached (disabled, no barcode) or that failed (network/contract error), instead of
    both looking like the same silent "miss"."""
    if (context or {}).get("movievaultV2BucketFallback") is False:
        return {"records": [], "attempted": False, "outcome": None, "errorCode": None}
    digest = _barcode_hash((payload or {}).get("barcode"))
    callback = _callback(context, "movievaultV2BucketLookup")
    if not digest or callback is None:
        return {"records": [], "attempted": False, "outcome": None, "errorCode": None}
    try:
        result = callback({"hash": digest}) or {}
    except Exception:
        return {"records": [], "attempted": True, "outcome": "error", "errorCode": "bucket_callback_exception"}
    if result.get("state") == "unavailable":
        return {"records": [], "attempted": True, "outcome": "error", "errorCode": result.get("errorCode")}
    records = result.get("results") or []
    return {"records": records, "attempted": True, "outcome": "hit" if records else "miss", "errorCode": None}


def _bucket_fallback_audit(bucket):
    return {"attempted": bucket["attempted"], "outcome": bucket["outcome"], "errorCode": bucket["errorCode"]}


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


def _resolver_lookup(payload, context):
    """Resolve a barcode through MovieVault's anonymous release-details resolver.

    Core already degrades every failure (including a `MovieVaultV2Error`) to a
    stable `{"status": "failed", "errorCode": ...}` dict; the guard here is
    belt-and-braces, matching `_bucket_lookup()`.

    Returns {"result": {...}|None, "attempted": bool, "outcome":
    "hit"|"miss"|"ambiguous"|"error"|None, "errorCode": str|None} - the same
    shape `_bucket_lookup()` returns, and for the same reason: a caller needs
    to tell "never asked" apart from "asked and got nothing useable" apart
    from "asked and it failed", not just see a bare miss."""
    callback = _callback(context, "movievaultV2ReleaseDetails")
    barcode = str((payload or {}).get("barcode") or "").strip()
    if callback is None or not _barcode_hash(barcode):
        return {"result": None, "attempted": False, "outcome": None, "errorCode": None}
    request = {"barcode": barcode}
    title = str((payload or {}).get("title") or "").strip()
    if title:
        request["title"] = title
    try:
        result = callback(request)
    except Exception:
        return {"result": None, "attempted": True, "outcome": "error", "errorCode": "resolver_callback_exception"}
    status = (result or {}).get("status") if isinstance(result, dict) else None
    if status in ("canonical_hit", "external_hit"):
        return {"result": result, "attempted": True, "outcome": "hit", "errorCode": None}
    if status in ("ambiguous", "candidates"):
        return {"result": None, "attempted": True, "outcome": "ambiguous", "errorCode": None}
    if status == "failed":
        return {
            "result": None,
            "attempted": True,
            "outcome": "error",
            "errorCode": (result or {}).get("errorCode") or "resolver_failed",
        }
    # "miss", "pending", or an unrecognized/malformed response: nothing usable now.
    return {"result": None, "attempted": True, "outcome": "miss", "errorCode": None}


def _resolver_fallback_audit(resolver):
    return {
        "attempted": resolver["attempted"],
        "outcome": resolver["outcome"],
        "errorCode": resolver["errorCode"],
    }


def _resolved_details(payload, context):
    """Resolve the scanned barcode through the v2 resolver, for the
    supplementary poster/technical enrichment of an already-confirmed hit.

    The synced catalog carries a poster only once a v4 index sync has published
    one; the resolver answers per barcode and is the source the poster arrives
    on for a freshly scanned disc. Artwork is supplementary, so any resolver
    failure degrades to "no poster" and never fails the surrounding lookup.

    Callers that need the outcome/error diagnostic (a genuine local+bucket miss
    falling through to the resolver as a last resort) use `_resolver_lookup()`
    directly instead - this wrapper exists only for this narrower, "just the
    payload or nothing" use."""
    return _resolver_lookup(payload, context)["result"] or {}


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
    """The technical profile the anonymous resolver carries for a scanned
    barcode. Only ever pulled from `release` - a box set's members are resolved
    individually and never inherit the set's technical data.

    The resolver nests its video facts under `video` and names the disc region
    array `regions`; the sync feed puts them flat on the release and calls the
    array `discRegions`. Both are flattened to the feed's names here so the
    merge pipeline downstream sees one vocabulary.

    The resolver reports subtitles as bare language codes; they are lifted into
    the feed's structured shape with `subtitleType: "full"`, which is what the
    resolver contract can express and nothing more."""
    section = (details or {}).get("release")
    fields = {}
    if isinstance(section, dict):
        if section.get("audioTracks"):
            fields["audioTracks"] = section["audioTracks"]
        if section.get("subtitleLanguages"):
            # Converted to the sync feed's structured shape so everything downstream
            # of this adapter speaks one vocabulary. `full` is not a guess: the
            # resolver contract has no way to express SDH or any other variant, so
            # every track it reports is the complete one.
            fields["subtitles"] = [
                {"languageCode": language, "subtitleType": "full"}
                for language in section["subtitleLanguages"]
                if isinstance(language, str) and language
            ]
        if section.get("packaging"):
            fields["packaging"] = section["packaging"]
        if section.get("regions"):
            fields["discRegions"] = section["regions"]
        video = section.get("video")
        if isinstance(video, dict):
            if video.get("resolution"):
                fields["videoResolution"] = video["resolution"]
            if video.get("codecs"):
                fields["videoCodecs"] = video["codecs"]
            if video.get("hdrFormats"):
                fields["hdrFormats"] = video["hdrFormats"]
            if video.get("aspectRatios"):
                fields["aspectRatios"] = video["aspectRatios"]
    return fields


def _resolved_release_record(details):
    """Map a resolver hit's `film`/`release` shape onto the record shape
    `_release()` already expects from a synced or bucket record, so one
    mapper serves all three match sources. There is no `releaseId`/`filmId`:
    the resolver has no local identity for a release DiscVault has never
    synced - `_release()` already tolerates a missing one."""
    film = (details or {}).get("film") or {}
    release = (details or {}).get("release") or {}
    record = {
        "canonicalTitle": film.get("title"),
        "releaseYear": film.get("year"),
        "releaseTitle": release.get("title"),
        "format": release.get("format"),
        "edition": release.get("edition"),
    }
    record.update(_resolved_technical(details))
    record.update(_resolved_poster(details))
    return record


def _resolved_box_set_record(details):
    """Map a resolver hit's `boxSet` shape onto the record shape `_proposal()`
    expects from a synced or bucket box-set record. Members carry no local
    identity either, for the same reason a release resolved this way doesn't."""
    box_set = (details or {}).get("boxSet") or {}
    members = [
        {
            key: value
            for key, value in {
                "position": member.get("position"),
                "canonicalTitle": member.get("title"),
                "format": member.get("discFormat"),
            }.items()
            if value not in (None, "")
        }
        for member in box_set.get("members") or []
    ]
    record = {"title": box_set.get("title"), "members": members}
    record.update(_resolved_poster(details, box_set=True))
    return record


def _release(record):
    movie = {key: value for key, value in {
        "title": record.get("canonicalTitle") or record.get("releaseTitle") or "",
        "releaseTitle": record.get("releaseTitle"), "year": record.get("releaseYear"),
        "format": record.get("format"), "edition": record.get("edition"),
        "studio": record.get("studio"), "distributor": record.get("distributor"),
        "runtimeMinutes": record.get("runtimeMinutes"),
        "audioTracks": record.get("audioTracks"), "subtitles": record.get("subtitles"),
        "packaging": record.get("packaging"),
        "videoResolution": record.get("videoResolution"),
        "videoCodecs": record.get("videoCodecs"), "hdrFormats": record.get("hdrFormats"),
        "aspectRatios": record.get("aspectRatios"), "discRegions": record.get("discRegions"),
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
    match_source = "local_index" if release is not None else None
    bucket = None
    resolver = None
    if release is None:
        # Not in the synced index - ask the anonymous bucket before giving up.
        bucket = _bucket_lookup(payload, context)
        release = next((item for item in bucket["records"] if item.get("recordType") == "release"), None)
        if release is not None:
            match_source = "bucket_fallback"
    if release is None:
        # Local index and the anonymous bucket both missed - ask the resolver
        # as a last resort, the same as DiscVaultApp's iOS client does. A hit
        # here already carries its own technical profile, so it never needs
        # the supplementary enrichment call below.
        resolver = _resolver_lookup(payload, context)
        if resolver["result"] is not None:
            release = _resolved_release_record(resolver["result"])
            match_source = "resolver_fallback"
    if release is None:
        result = {"status": "miss", "provider": PROVIDER_ID, "items": []}
        if bucket is not None:
            result["bucketFallback"] = _bucket_fallback_audit(bucket)
        if resolver is not None:
            result["resolverFallback"] = _resolver_fallback_audit(resolver)
        return result
    # Fall back to the resolver's poster and/or technical specs when the
    # synced record doesn't carry them yet (not synced, or a v4-sync-disabled
    # instance still relying purely on the anonymous resolver). Skipped when
    # the release already came from the resolver - that call already carries
    # everything it has, and a second call would only cost another live
    # round-trip for nothing.
    if match_source != "resolver_fallback" and (
        not (release or {}).get("posterUrl") or not (release or {}).get("audioTracks")
    ):
        details = _resolved_details(payload, context)
        resolved = {**_resolved_poster(details), **_resolved_technical(details)}
        if resolved:
            release = {**release, **resolved}
    item = _release(release)
    result = {"status": "hit", **item, "items": [item], "matchSource": match_source}
    if bucket is not None:
        result["bucketFallback"] = _bucket_fallback_audit(bucket)
    if resolver is not None:
        result["resolverFallback"] = _resolver_fallback_audit(resolver)
        # The resolver's own contract marks an unmoderated external match this
        # way; canonical_hit is MovieVault-verified data and needs no notice.
        if (resolver["result"] or {}).get("status") == "external_hit":
            result["verificationStatus"] = "unreviewed_external"
    return result


def search_title(payload, context=None):
    records = _lookup(payload, context)
    if records is None:
        return _error()
    # No bucket fallback here: buckets are keyed by the hash of the EAN, so a
    # title query has nothing to look up.
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
    match_source = "local_index" if box_sets else None
    bucket = None
    resolver = None
    if not box_sets:
        # A box set is reachable by hash too: bucket records match on the set's
        # own eanHashes as well as on each member's discBarcodeHash.
        bucket = _bucket_lookup(payload, context)
        box_sets = [item for item in bucket["records"] if item.get("recordType") == "box_set"]
        if box_sets:
            match_source = "bucket_fallback"
    resolver_hit = None
    if not box_sets:
        # Local index and the anonymous bucket both missed - ask the resolver
        # as a last resort, same as search_barcode()'s primary fallback.
        resolver = _resolver_lookup(payload, context)
        resolver_hit = resolver["result"]
        if resolver_hit is not None and (resolver_hit.get("boxSet") or {}).get("members"):
            box_sets = [_resolved_box_set_record(resolver_hit)]
            match_source = "resolver_fallback"
    # A scanned box-set's cover arrives on the resolver's `boxSet`, so fill it in
    # when the synced record has none. Only the first proposal matches the
    # scanned barcode, so the resolver's cover is applied to that one alone.
    # Skipped when the proposal already came from the resolver - it already
    # carries whatever cover that call had.
    if box_sets and match_source != "resolver_fallback" and not box_sets[0].get("posterUrl"):
        resolved = _resolved_poster(_resolved_details(payload, context), box_set=True)
        if resolved:
            box_sets = [{**box_sets[0], **resolved}, *box_sets[1:]]
    proposals = [_proposal(item) for item in box_sets]
    result = {"status": "hit" if proposals else "miss", "provider": PROVIDER_ID, "boxSetProposal": proposals[0] if proposals else {}, "boxSetProposals": proposals, "items": proposals}
    if match_source:
        result["matchSource"] = match_source
    if bucket is not None:
        result["bucketFallback"] = _bucket_fallback_audit(bucket)
    if resolver is not None:
        result["resolverFallback"] = _resolver_fallback_audit(resolver)
        if (resolver_hit or {}).get("status") == "external_hit":
            result["verificationStatus"] = "unreviewed_external"
    return result
