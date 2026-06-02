import os
from urllib.parse import quote

import requests


def _settings(context):
    return (context or {}).get("settings") or {}


def _secrets(context):
    return (context or {}).get("secrets") or {}


def _base_url(context):
    movievault = (context or {}).get("movievault") or {}
    return str(
        movievault.get("searchUrl")
        or os.environ.get("MOVIEVAULT_SEARCH_URL")
        or os.environ.get("MOVIEVAULT_BASE_URL")
        or "https://search.discvault.eu"
    ).strip().rstrip("/")


def _token(context):
    return str(_secrets(context).get("token") or _secrets(context).get("apiToken") or "").strip()


def _headers(context):
    headers = {"Accept": "application/json"}
    token = _token(context)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get(context, path, **params):
    response = requests.get(f"{_base_url(context)}{path}", params=params, headers=_headers(context), timeout=10)
    response.raise_for_status()
    return response.json()


def _items(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("items", "results", "movies", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return [payload]


def _first(payload):
    items = _items(payload)
    return items[0] if items and isinstance(items[0], dict) else {}


def _text(value):
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _first_value(item, *keys):
    if not isinstance(item, dict):
        return ""
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    return ""


def _parse_year(value):
    text = _text(value)
    return text[:4] if len(text) >= 4 and text[:4].isdigit() else ""


def _image_url(value):
    text = _text(value)
    return text if text.startswith(("http://", "https://")) else ""


def _movie_payload(item):
    if not isinstance(item, dict):
        return {}
    return {
        "title": _text(item.get("title") or item.get("name")),
        "originalTitle": _text(item.get("originalTitle") or item.get("original_title")),
        "year": _text(item.get("year") or item.get("releaseYear") or item.get("release_year"))[:4],
        "releaseDate": _text(item.get("releaseDate") or item.get("release_date")),
        "overview": _text(item.get("overview") or item.get("plot") or item.get("description")),
        "runtimeMinutes": item.get("runtime") or item.get("runtimeMinutes"),
        "genre": _text(item.get("genre") or item.get("genres")),
        "director": _text(item.get("director") or item.get("directors")),
        "actor": _text(item.get("actor") or item.get("actors") or item.get("cast")),
        "producer": _text(item.get("producer") or item.get("producers")),
        "studios": _text(item.get("studios") or item.get("studio")),
        "format": _text(item.get("format") or item.get("mediaType") or item.get("media_type")),
        "edition": _text(item.get("edition")),
        "country": _text(item.get("country")),
        "language": _text(item.get("language")),
        "rating": _text(item.get("rating") or item.get("imdbRating")),
        "posterUrl": _text(item.get("posterUrl") or item.get("poster_url") or item.get("poster")),
        "backdropUrl": _text(item.get("backdropUrl") or item.get("backdrop_url") or item.get("backdrop")),
        "backdropUrls": item.get("backdropUrls") or item.get("backdrop_urls") or [],
        "trailerUrl": _text(item.get("trailerUrl") or item.get("trailer_url")),
        "videos": item.get("videos") or [],
        "audienceRating": _text(item.get("audienceRating") or item.get("audience_rating")),
    }


def _member_list(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("members", "movies", "items", "titles", "boxSetMovies", "box_set_movies", "releases"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    for key in ("boxSetProposal", "box_set_proposal", "boxSet", "box_set", "proposal", "data"):
        nested = payload.get(key)
        found = _member_list(nested)
        if found:
            return found
    return []


def _member_source(item):
    if not isinstance(item, dict):
        return {}
    for key in ("movie", "release", "metadata", "details"):
        value = item.get(key)
        if isinstance(value, dict):
            return {**value, **{k: v for k, v in item.items() if k not in {key}}}
    return item


def _normalize_member(item, index):
    if isinstance(item, str):
        title = _text(item)
        return {"title": title, "sort_order": index, "sortOrder": index} if title else {}
    source = _member_source(item)
    title = _text(_first_value(source, "title", "name", "originalTitle", "original_title"))
    if not title:
        return {}
    movie = _movie_payload(source)
    year = _parse_year(_first_value(source, "year", "releaseYear", "release_year", "releaseDate", "release_date") or movie.get("year"))
    sort_order = _text(_first_value(source, "sortOrder", "sort_order", "discNumber", "disc_number")) or index
    disc_number = _text(_first_value(source, "discNumber", "disc_number", "disc", "diskNumber", "disk_number"))
    poster = _image_url(_first_value(source, "posterUrl", "poster_url", "poster", "coverUrl", "cover_url", "image"))
    backdrop = _image_url(_first_value(source, "backdropUrl", "backdrop_url", "backdrop"))
    member = {
        "title": title,
        "originalTitle": _text(_first_value(source, "originalTitle", "original_title") or movie.get("originalTitle")),
        "original_title": _text(_first_value(source, "original_title", "originalTitle") or movie.get("originalTitle")),
        "year": year,
        "releaseDate": _text(_first_value(source, "releaseDate", "release_date") or movie.get("releaseDate")),
        "release_date": _text(_first_value(source, "release_date", "releaseDate") or movie.get("releaseDate")),
        "tmdbId": _text(_first_value(source, "tmdbId", "tmdb_id")),
        "tmdb_id": _text(_first_value(source, "tmdb_id", "tmdbId")),
        "imdbId": _text(_first_value(source, "imdbId", "imdb_id")),
        "imdb_id": _text(_first_value(source, "imdb_id", "imdbId")),
        "overview": _text(_first_value(source, "overview", "plot", "description") or movie.get("overview")),
        "plot": _text(_first_value(source, "plot", "overview", "description") or movie.get("overview")),
        "runtime": _first_value(source, "runtime", "runtimeMinutes", "runtime_minutes") or movie.get("runtimeMinutes"),
        "format": _text(_first_value(source, "format", "mediaType", "media_type") or movie.get("format")),
        "genre": _text(_first_value(source, "genre", "genres") or movie.get("genre")),
        "director": _text(_first_value(source, "director", "directors") or movie.get("director")),
        "actor": _text(_first_value(source, "actor", "actors", "cast") or movie.get("actor")),
        "poster": poster,
        "posterUrl": poster,
        "poster_url": poster,
        "backdrop": backdrop,
        "backdropUrl": backdrop,
        "backdrop_url": backdrop,
        "backdropUrls": source.get("backdropUrls") or source.get("backdrop_urls") or movie.get("backdropUrls") or [],
        "backdrop_urls": source.get("backdrop_urls") or source.get("backdropUrls") or movie.get("backdropUrls") or [],
        "sortOrder": sort_order,
        "sort_order": sort_order,
        "discNumber": disc_number,
        "disc_number": disc_number,
        "source": _text(source.get("source") or "MovieVault"),
        "sourceRef": _text(_first_value(source, "sourceRef", "source_ref", "id", "movieVaultId", "movievault_id")),
    }
    return {key: value for key, value in member.items() if value not in (None, "", [], {})}


def _member_needs_identification(member):
    return not (member.get("tmdbId") or member.get("tmdb_id") or member.get("imdbId") or member.get("imdb_id"))


def _format_key(value):
    text = _text(value).casefold().replace("-", " ").replace("_", " ").replace("/", " ")
    text = " ".join(text.split())
    if not text:
        return ""
    if "ultra hd" in text or "uhd" in text or "4k" in text:
        return "ultra_hd_blu_ray"
    if "blu ray" in text or "bluray" in text or text == "bd":
        return "blu_ray"
    if text in {"dvd", "dvd video"}:
        return "dvd"
    if "hd dvd" in text or "hddvd" in text:
        return "hd_dvd"
    if "laserdisc" in text or "laser disc" in text:
        return "laserdisc"
    if "svcd" in text or "vcd" in text:
        return "vcd_svcd"
    return text


def _selected_format(context=None, item=None):
    context = context or {}
    item = item or {}
    return _text(
        _first_value(
            {**item, **context},
            "selectedFormat",
            "selected_format",
            "format",
            "mediaType",
            "media_type",
            "editionFormat",
            "edition_format",
        )
    )


def _compatible_format(candidate, expected):
    candidate_key = _format_key(candidate)
    expected_key = _format_key(expected)
    return not candidate_key or not expected_key or candidate_key == expected_key


def _merge_member_enrichment(member, enrichment, expected_format=""):
    proposal = enrichment.get("proposal") if isinstance(enrichment, dict) else {}
    proposal = proposal if isinstance(proposal, dict) else {}
    movie_updates = proposal.get("movieUpdates") or {}
    metadata_updates = proposal.get("metadataUpdates") or {}
    media_updates = proposal.get("mediaUpdates") or {}
    identifiers = proposal.get("identifiers") or {}
    enriched = dict(member)

    mappings = {
        "title": ("title",),
        "original_title": ("original_title", "originalTitle"),
        "originalTitle": ("original_title", "originalTitle"),
        "year": ("year",),
        "release_date": ("release_date", "releaseDate"),
        "releaseDate": ("release_date", "releaseDate"),
        "overview": ("overview", "plot"),
        "plot": ("overview", "plot"),
        "runtime": ("runtime_minutes", "runtimeMinutes", "runtime"),
        "format": ("format",),
        "genre": ("genre",),
        "director": ("director",),
        "actor": ("actor",),
    }
    for target, keys in mappings.items():
        if enriched.get(target):
            continue
        for key in keys:
            value = movie_updates.get(key) or metadata_updates.get(key)
            if target == "format" and expected_format and not _compatible_format(value, expected_format):
                continue
            if value not in (None, "", [], {}):
                enriched[target] = value
                break
    if expected_format and not enriched.get("format"):
        enriched["format"] = expected_format

    if identifiers.get("tmdb") and not (enriched.get("tmdbId") or enriched.get("tmdb_id")):
        enriched["tmdbId"] = str(identifiers["tmdb"])
        enriched["tmdb_id"] = str(identifiers["tmdb"])
    if identifiers.get("imdb") and not (enriched.get("imdbId") or enriched.get("imdb_id")):
        enriched["imdbId"] = str(identifiers["imdb"])
        enriched["imdb_id"] = str(identifiers["imdb"])

    poster = metadata_updates.get("poster_url") or (media_updates.get("poster") or {}).get("sourceUrl")
    if poster and not (enriched.get("poster") or enriched.get("posterUrl") or enriched.get("poster_url")):
        enriched["poster"] = poster
        enriched["posterUrl"] = poster
        enriched["poster_url"] = poster
    backdrop = metadata_updates.get("backdrop_url") or (media_updates.get("backdrop") or {}).get("sourceUrl")
    if backdrop and not (enriched.get("backdrop") or enriched.get("backdropUrl") or enriched.get("backdrop_url")):
        enriched["backdrop"] = backdrop
        enriched["backdropUrl"] = backdrop
        enriched["backdrop_url"] = backdrop

    sources = []
    for item in enrichment.get("sourceSummary") or []:
        if item.get("state") in {"applied", "hit"}:
            sources.append(item.get("pluginId"))
    if sources:
        enriched["identifiedBy"] = sources
        enriched["memberConfidence"] = "identified_by_metadata_plugins"
    return {key: value for key, value in enriched.items() if value not in (None, "", [], {})}


def _identify_member_with_other_plugins(member, context):
    lookup = (context or {}).get("metadataLookup")
    if not callable(lookup):
        return member, None
    query = {
        "title": member.get("title") or member.get("originalTitle") or member.get("original_title") or "",
        "year": member.get("year") or "",
        "tmdbId": member.get("tmdbId") or member.get("tmdb_id") or "",
        "imdbId": member.get("imdbId") or member.get("imdb_id") or "",
        "format": member.get("format") or (context or {}).get("format") or "",
    }
    if not query["title"] and not query["tmdbId"] and not query["imdbId"]:
        return member, None
    try:
        enrichment = lookup(query, excludeProviders=["movievault"])
    except Exception as exc:  # Fallback discovery should never make MovieVault unusable.
        return {**member, "identificationWarning": str(exc)}, None
    expected_format = member.get("format") or (context or {}).get("selectedFormat") or (context or {}).get("format") or ""
    return _merge_member_enrichment(member, enrichment or {}, expected_format), enrichment


def _normalize_box_set_proposal(payload, context=None):
    item = _first(payload)
    if not item:
        return {}
    if not _member_list(item):
        nested = (
            item.get("boxSetProposal")
            or item.get("box_set_proposal")
            or item.get("boxSet")
            or item.get("box_set")
        )
        if isinstance(nested, dict):
            item = nested

    title = _text(_first_value(item, "title", "name", "boxSetTitle", "box_set_title"))
    selected_format = _selected_format(context, item)
    raw_members = _member_list(item)
    members = []
    lookup_summaries = []
    seen = set()
    for index, raw_member in enumerate(raw_members[:50], start=1):
        member = _normalize_member(raw_member, index)
        if not member:
            continue
        if selected_format and not member.get("format"):
            member["format"] = selected_format
        if selected_format and member.get("format") and not _compatible_format(member.get("format"), selected_format):
            member["format"] = selected_format
        if _member_needs_identification(member):
            member, enrichment = _identify_member_with_other_plugins(member, {**(context or {}), "selectedFormat": selected_format})
            if isinstance(enrichment, dict):
                lookup_summaries.append(
                    {
                        "member": member.get("title"),
                        "sourceOrder": enrichment.get("sourceOrder") or [],
                        "proposalStats": enrichment.get("proposalStats") or {},
                    }
                )
        key = (
            _text(member.get("tmdbId") or member.get("tmdb_id") or member.get("imdbId") or member.get("imdb_id")),
            _text(member.get("title")).casefold(),
            _text(member.get("year")),
        )
        if key in seen:
            continue
        seen.add(key)
        members.append(member)

    if not title and members:
        title = _text(item.get("boxSetTitle") or item.get("collectionTitle") or item.get("name"))
    if not title:
        return {}

    proposal = {
        "title": title,
        "name": title,
        "source": "MovieVault",
        "provider": "movievault",
        "movievault_id": _text(_first_value(item, "movieVaultId", "movievaultId", "movievault_id", "id")),
        "barcode": _text(_first_value(item, "barcode", "ean", "upc")),
        "year": _parse_year(_first_value(item, "year", "releaseYear", "release_year")),
        "year_range": _text(_first_value(item, "yearRange", "year_range")),
        "format": selected_format or _text(_first_value(item, "format", "mediaType", "media_type")),
        "poster": _image_url(_first_value(item, "posterUrl", "poster_url", "poster", "image")),
        "poster_url": _image_url(_first_value(item, "posterUrl", "poster_url", "poster", "image")),
        "backdrop": _image_url(_first_value(item, "backdropUrl", "backdrop_url", "backdrop")),
        "backdrop_url": _image_url(_first_value(item, "backdropUrl", "backdrop_url", "backdrop")),
        "backdrop_urls": item.get("backdrop_urls") or item.get("backdropUrls") or [],
        "movies": members,
        "members": members,
        "member_count": len(members),
        "member_source": "MovieVault",
        "member_confidence": "identified" if members and all(not _member_needs_identification(m) for m in members) else "candidate",
        "metadata_plugin_fallbacks": lookup_summaries,
    }
    if lookup_summaries:
        proposal["member_source"] = "MovieVault + metadata plugins"
    return {key: value for key, value in proposal.items() if value not in (None, "", [], {})}


def _technical_payload(item):
    if not isinstance(item, dict):
        return {}
    return {
        "format": _text(item.get("format") or item.get("mediaType") or item.get("media_type")),
        "hdr": _text(item.get("hdr") or item.get("hdrFormat") or item.get("hdr_format")),
        "packaging": _text(item.get("packaging")),
        "screenRatios": _text(item.get("screenRatios") or item.get("screen_ratios")),
        "audioTracks": item.get("audioTracks") or item.get("audio_tracks") or [],
        "subtitles": item.get("subtitles") or [],
        "regions": item.get("regions") or [],
        "contentRatings": item.get("contentRatings") or item.get("content_ratings") or {},
    }


def _normalize_result(payload, *, source_ref=""):
    item = _first(payload)
    if not item:
        return {"status": "miss", "provider": "movievault"}
    movie = _movie_payload(item)
    if not movie.get("title"):
        return {"status": "miss", "provider": "movievault"}
    return {
        "status": "hit",
        "provider": "movievault",
        "sourceLabel": "MovieVault",
        "sourceRef": source_ref or _text(item.get("id") or item.get("movieVaultId") or item.get("movievault_id")),
        "movie": movie,
        "technicalSpecs": _technical_payload(item),
        "tmdbId": _text(item.get("tmdbId") or item.get("tmdb_id")),
        "imdbId": _text(item.get("imdbId") or item.get("imdb_id")),
        "boxSetProposal": _normalize_box_set_proposal(item, None)
        or item.get("boxSetProposal")
        or item.get("box_set_proposal")
        or item.get("box_set"),
    }


def health_check(context=None):
    context = context or {}
    connection = context.get("movievault") or {}
    try:
        response = requests.get(f"{_base_url(context)}/api/v1/health", headers={"Accept": "application/json"}, timeout=8)
        status = "available" if response.status_code < 500 else "unavailable"
        if connection.get("error"):
            status = "connection_error"
        elif connection.get("requiresReset"):
            status = "reset_required"
        elif not connection.get("tokenSet") and connection.get("enabled", True):
            status = "needs_connection"
        elif connection.get("linkStatus") == "revoked":
            status = "revoked"
        elif connection.get("linkStatus") == "disabled":
            status = "disabled"
        return {
            "status": status,
            "message": f"HTTP {response.status_code}",
            "connection": {
                "authMethod": connection.get("authMethod"),
                "instanceId": connection.get("instanceId"),
                "instanceName": connection.get("instanceName"),
                "keyId": connection.get("keyId"),
                "lastHandshakeAt": connection.get("lastHandshakeAt"),
                "linkStatus": connection.get("linkStatus"),
                "requiresReset": bool(connection.get("requiresReset")),
                "tokenPrefix": connection.get("tokenPrefix"),
                "tokenSet": bool(connection.get("tokenSet")),
            },
        }
    except Exception as exc:
        return {"status": "unavailable", "message": str(exc)}


def search_barcode(payload, context=None):
    barcode = str((payload or {}).get("barcode") or "").strip()
    if not barcode:
        return {"status": "skipped", "provider": "movievault"}
    return _normalize_result(_get(context or {}, f"/api/v1/barcodes/{quote(barcode)}"), source_ref=f"barcode:{barcode}")


def search_title(payload, context=None):
    title = str((payload or {}).get("title") or "").strip()
    year = str((payload or {}).get("year") or "").strip()
    if not title:
        return {"status": "skipped", "provider": "movievault", "items": []}
    data = _get(context or {}, "/api/v1/movies", q=title, year=year)
    items = []
    for item in _items(data)[:8]:
        movie = _movie_payload(item)
        if movie.get("title"):
            items.append(
                {
                    "provider": "movievault",
                    "providerLabel": "MovieVault",
                    "id": _text(item.get("id") or item.get("movieVaultId") or item.get("movievault_id")),
                    "title": movie.get("title"),
                    "year": movie.get("year"),
                    "posterUrl": movie.get("posterUrl"),
                    "movie": movie,
                }
            )
    return {"status": "hit" if items else "miss", "provider": "movievault", "items": items}


def movie_details(payload, context=None):
    barcode = str((payload or {}).get("barcode") or "").strip()
    title = str((payload or {}).get("title") or "").strip()
    year = str((payload or {}).get("year") or "").strip()
    if barcode:
        result = search_barcode(payload, context)
        if result.get("status") == "hit":
            return result
    if not title:
        return {"status": "skipped", "provider": "movievault"}
    return _normalize_result(_get(context or {}, "/api/v1/movies", q=title, year=year), source_ref=f"title:{title}")


def box_set_candidates(payload, context=None):
    title = str((payload or {}).get("title") or "").strip()
    year = str((payload or {}).get("year") or "").strip()
    barcode = str((payload or {}).get("barcode") or "").strip()
    data = _get(context or {}, "/api/v1/box-sets", q=title, year=year, barcode=barcode)
    proposal = _normalize_box_set_proposal(data, context or {})
    if not proposal or len(proposal.get("movies") or []) < 2:
        return {"status": "miss", "provider": "movievault", "boxSetProposal": {}}
    return {
        "status": "hit",
        "provider": "movievault",
        "sourceLabel": "MovieVault",
        "sourceRef": proposal.get("movievault_id") or proposal.get("barcode") or title,
        "boxSetProposal": proposal,
    }


def receive_metadata(payload, context=None):
    return {
        "status": "not_implemented",
        "message": "MovieVault receiver contribution execution is handled by the dedicated contribution flow.",
    }
