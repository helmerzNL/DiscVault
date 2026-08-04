TMDB_API = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/original"


def _settings(context):
    return (context or {}).get("settings") or {}


def _secrets(context):
    return (context or {}).get("secrets") or {}


def _api_key(context):
    return str(_secrets(context).get("apiKey") or _secrets(context).get("api_key") or "").strip()


def _language(context):
    return str(_settings(context).get("language") or "en-US").strip() or "en-US"


def _request(context, path, **params):
    import requests

    api_key = _api_key(context)
    if not api_key:
        raise RuntimeError("TMDb API key is not configured")
    response = requests.get(
        f"{TMDB_API}{path}",
        params={**params, "api_key": api_key},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def _image(path):
    return f"{IMAGE_BASE}{path}" if path else ""


def _certifications(release_dates):
    ratings = {}
    for entry in (release_dates or {}).get("results") or []:
        country = entry.get("iso_3166_1") or ""
        cert = ""
        for release in sorted(entry.get("release_dates") or [], key=lambda item: 0 if item.get("type") == 3 else 1):
            cert = (release.get("certification") or "").strip()
            if cert:
                break
        if country and cert:
            ratings[country] = cert
    return ratings


def _videos(data):
    trailer = ""
    extras = []
    for item in (data.get("videos") or {}).get("results") or []:
        if item.get("site") != "YouTube" or not item.get("key"):
            continue
        url = f"https://www.youtube.com/watch?v={item['key']}"
        if item.get("type") == "Trailer" and not trailer:
            trailer = url
        elif item.get("type") in {"Featurette", "Behind the Scenes", "Clip", "Bloopers", "Teaser"}:
            extras.append(
                {
                    "url": url,
                    "label": item.get("name") or item.get("type"),
                    "type": item.get("type"),
                    "source": "tmdb",
                }
            )
    return trailer, extras


CREW_LIMIT = 75


def _credits(data):
    credits = data.get("credits") or {}
    cast = []
    crew = []
    for index, item in enumerate((credits.get("cast") or [])[:20]):
        cast.append(
            {
                "role": "actor",
                "name": item.get("name") or "",
                "character": item.get("character") or "",
                "tmdbId": item.get("id"),
                "sortOrder": index,
                "profileUrl": _image(item.get("profile_path")),
            }
        )
    # TMDb's combined movie-credits response already lists every crew member
    # (and their profile photo) in this same call -- no extra API request per
    # person, unlike person_details(). Kept to a generous cap rather than a
    # job-title allowlist so departments beyond director/writer/producer show
    # up too, without one huge blockbuster's credits list growing unbounded.
    for index, item in enumerate((credits.get("crew") or [])[:CREW_LIMIT]):
        crew.append(
            {
                "role": "crew",
                "name": item.get("name") or "",
                "job": item.get("job") or "",
                "tmdbId": item.get("id"),
                # Offset past cast's own 0..19 range and kept in TMDb's original
                # department/job order -- previously a constant 0 tied every crew
                # member with cast member #1 in movie_credit_entities()'s
                # `ORDER BY sort_order, name`, so the (sort_order, name)-ordered,
                # LIMIT-ed query silently dropped crew alphabetically instead of
                # by TMDb's actual importance order once the list got large.
                "sortOrder": 20 + index,
                "profileUrl": _image(item.get("profile_path")),
            }
        )
    return cast + crew


def _locale_key(language, country=""):
    language = str(language or "").strip().lower()
    country = str(country or "").strip().upper()
    if not language:
        return ""
    return f"{language}-{country}" if country else language


def _localizations(data):
    rows = []
    seen = set()
    for item in ((data.get("translations") or {}).get("translations") or []):
        payload = item.get("data") or {}
        title = payload.get("title") or ""
        overview = payload.get("overview") or ""
        lang = _locale_key(item.get("iso_639_1"), item.get("iso_3166_1"))
        if not lang or not (title or overview):
            continue
        key = lang.lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "lang": lang,
                "title": title,
                "overview": overview,
                "source": "tmdb",
            }
        )
    return rows


def _person_localizations(data):
    rows = []
    seen = set()
    for item in ((data.get("translations") or {}).get("translations") or []):
        payload = item.get("data") or {}
        biography = (payload.get("biography") or "").strip()
        lang = _locale_key(item.get("iso_639_1"), item.get("iso_3166_1"))
        if not lang or not biography:
            continue
        key = lang.lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "lang": lang,
                "biography": biography,
                "source": "tmdb",
            }
        )
    return rows


def _normalize_details(data):
    # TMDB genre ids are the source of truth for the canonical genre catalog
    # (see next_genres.py). Returning ids here -- instead of localized names
    # -- keeps the stored association independent of the TMDB request
    # language. `genres` is always present on a /movie/{id} response (it can
    # be an empty list), so its presence signals an authoritative genre
    # answer that should replace any existing associations.
    genre_ids = [item.get("id") for item in data.get("genres") or [] if item.get("id") is not None]
    studios = [item.get("name") for item in data.get("production_companies") or [] if item.get("name")]
    crew = (data.get("credits") or {}).get("crew") or []
    cast = (data.get("credits") or {}).get("cast") or []
    directors = [item.get("name") for item in crew if item.get("job") == "Director" and item.get("name")]
    producers = [item.get("name") for item in crew if item.get("job") == "Producer" and item.get("name")]
    actors = [item.get("name") for item in cast[:5] if item.get("name")]
    backdrops = sorted(
        (data.get("images") or {}).get("backdrops") or [],
        key=lambda item: item.get("vote_average") or 0,
        reverse=True,
    )
    posters = sorted(
        (data.get("images") or {}).get("posters") or [],
        key=lambda item: item.get("vote_average") or 0,
        reverse=True,
    )
    poster_urls = [_image(item.get("file_path")) for item in posters[:10] if item.get("file_path")]
    if not poster_urls and data.get("poster_path"):
        poster_urls = [_image(data.get("poster_path"))]
    backdrop_urls = [_image(item.get("file_path")) for item in backdrops[:10] if item.get("file_path")]
    if not backdrop_urls and data.get("backdrop_path"):
        backdrop_urls = [_image(data.get("backdrop_path"))]
    trailer, extra_videos = _videos(data)
    ratings = _certifications(data.get("release_dates") or {})
    imdb_id = data.get("imdb_id") or ""
    return {
        "status": "hit",
        "provider": "tmdb",
        "sourceLabel": "TMDb",
        "sourceRef": f"tmdb:{data.get('id')}",
        "movie": {
            "title": data.get("title") or "",
            "originalTitle": data.get("original_title") or "",
            "year": str(data.get("release_date") or "")[:4],
            "releaseDate": data.get("release_date") or "",
            "overview": data.get("overview") or "",
            "runtimeMinutes": data.get("runtime"),
            "rating": str(data.get("vote_average") or "")[:4],
            "director": ", ".join(directors),
            "actor": ", ".join(actors),
            "producer": ", ".join(producers),
            "studios": ", ".join(studios),
            "posterUrl": poster_urls[0] if poster_urls else "",
            "posters": poster_urls,
            "backdropUrl": backdrop_urls[0] if backdrop_urls else "",
            "backdropUrls": backdrop_urls,
            "trailerUrl": trailer,
            "videos": extra_videos,
            "audienceRating": ratings.get("US") or "",
        },
        "technicalSpecs": {
            "contentRatings": ratings,
        },
        "localizations": _localizations(data),
        "credits": _credits(data),
        # Authoritative TMDB genre ids (see next_genres.py for the id -> key
        # mapping). This lives outside `movie` on purpose: `movie` fields
        # flow through the generic free-text metadata merge policy, while
        # genres are a relational, always-replace-on-hit association.
        "genreIds": genre_ids,
        "tmdbId": data.get("id"),
        "imdbId": imdb_id,
    }


def _details(context, tmdb_id):
    return _request(
        context,
        f"/movie/{tmdb_id}",
        language=_language(context),
        append_to_response="credits,videos,images,release_dates,translations,alternative_titles",
        include_image_language="null,en",
    )


def _filmography_item(item, credit_type):
    if (item.get("media_type") or "movie") != "movie":
        return None
    title = item.get("title") or item.get("name") or item.get("original_title") or ""
    if not title:
        return None
    return {
        "id": item.get("id"),
        "tmdbId": item.get("id"),
        "media_type": "movie",
        "title": title,
        "originalTitle": item.get("original_title") or "",
        "year": str(item.get("release_date") or item.get("first_air_date") or "")[:4],
        "releaseDate": item.get("release_date") or item.get("first_air_date") or "",
        "posterUrl": _image(item.get("poster_path")),
        "posterPath": item.get("poster_path") or "",
        "backdropUrl": _image(item.get("backdrop_path")),
        "character": item.get("character") or "",
        "job": item.get("job") or "",
        "creditType": credit_type,
        "voteAverage": item.get("vote_average"),
        "source": "TMDb",
    }


def _filmography_items(items, credit_type):
    normalized = []
    seen = set()
    for item in items or []:
        entry = _filmography_item(item, credit_type)
        if not entry:
            continue
        key = (
            entry.get("tmdbId"),
            entry.get("creditType"),
            entry.get("character"),
            entry.get("job"),
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(entry)
    return normalized


def health_check(context=None):
    if not _api_key(context or {}):
        return {"status": "needs_configuration", "message": "Configure a TMDb API key."}
    data = _request(context or {}, "/configuration")
    return {"status": "available", "message": "TMDb reachable.", "imagesBaseUrl": (data.get("images") or {}).get("secure_base_url")}


def search_title(payload, context=None):
    title = str((payload or {}).get("title") or "").strip()
    year = str((payload or {}).get("year") or "").strip()
    if not title:
        return {"status": "skipped", "provider": "tmdb", "items": []}
    data = _request(context or {}, "/search/movie", query=title, year=year, language=_language(context))
    items = []
    for item in data.get("results") or []:
        items.append(
            {
                "provider": "tmdb",
                "providerLabel": "TMDb",
                "id": item.get("id"),
                "tmdbId": item.get("id"),
                "title": item.get("title") or "",
                "originalTitle": item.get("original_title") or "",
                "year": str(item.get("release_date") or "")[:4],
                "overview": item.get("overview") or "",
                "posterUrl": _image(item.get("poster_path")),
            }
        )
    return {"status": "hit" if items else "miss", "provider": "tmdb", "items": items[:8]}


def _normalized_title(value):
    import re
    import unicodedata

    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.sub(r"[^\w]+", " ", text).replace("_", " ").split())


def _exact_title_match(payload, items):
    expected_title = _normalized_title((payload or {}).get("title"))
    expected_year = str((payload or {}).get("year") or "").strip()
    if not expected_title:
        return None
    for item in items or []:
        if not isinstance(item, dict):
            continue
        titles = {
            _normalized_title(item.get("title")),
            _normalized_title(item.get("originalTitle") or item.get("original_title")),
        }
        if expected_title not in titles:
            continue
        item_year = str(item.get("year") or "").strip()
        if expected_year and item_year != expected_year:
            continue
        return item
    return None


def lookup_external_id(payload, context=None):
    tmdb_id = str((payload or {}).get("tmdbId") or (payload or {}).get("tmdb_id") or "").strip()
    imdb_id = str((payload or {}).get("imdbId") or (payload or {}).get("imdb_id") or "").strip()
    if tmdb_id:
        return _normalize_details(_details(context or {}, tmdb_id))
    if imdb_id:
        found = _request(context or {}, f"/find/{imdb_id}", external_source="imdb_id", language=_language(context))
        movies = found.get("movie_results") or []
        if movies:
            return _normalize_details(_details(context or {}, movies[0]["id"]))
    return {"status": "miss", "provider": "tmdb"}


def movie_details(payload, context=None):
    direct = lookup_external_id(payload or {}, context or {})
    if direct.get("status") == "hit":
        return direct
    search = search_title(payload or {}, context or {})
    matched = _exact_title_match(payload or {}, search.get("items") or [])
    if not matched:
        return {"status": "miss", "provider": "tmdb"}
    tmdb_id = matched.get("tmdbId") or matched.get("id")
    if not tmdb_id:
        return {"status": "miss", "provider": "tmdb"}
    return _normalize_details(_details(context or {}, tmdb_id))


def _import_wikidata_awards():
    try:
        import wikidata_awards  # type: ignore

        return wikidata_awards
    except Exception:
        import os
        import sys

        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        try:
            import wikidata_awards  # type: ignore

            return wikidata_awards
        except Exception:
            return None


def _profile_urls(images):
    profiles = sorted(
        (images or {}).get("profiles") or [],
        key=lambda item: item.get("vote_average") or 0,
        reverse=True,
    )
    urls = [_image(item.get("file_path")) for item in profiles[:12] if item.get("file_path")]
    return urls


def person_details(payload, context=None):
    tmdb_id = str((payload or {}).get("tmdbId") or (payload or {}).get("tmdb_id") or "").strip()
    if not tmdb_id:
        return {"status": "miss", "provider": "tmdb", "reason": "tmdbId is required"}
    language = _language(context)
    data = _request(
        context or {},
        f"/person/{tmdb_id}",
        language=language,
        append_to_response="images,external_ids,translations",
    )
    aliases = [str(a).strip() for a in (data.get("also_known_as") or []) if str(a or "").strip()]
    name = (data.get("name") if data else "") or (aliases[0] if aliases else "")
    profile_url = _image(data.get("profile_path")) if data.get("profile_path") else ""
    profiles = _profile_urls(data.get("images") or {})
    if profile_url and profile_url not in profiles:
        profiles.insert(0, profile_url)
    imdb_id = str((data.get("external_ids") or {}).get("imdb_id") or "").strip()
    localizations = _person_localizations(data)
    biography = (data.get("biography") or "").strip()
    if not biography and localizations:
        configured = str(language or "").strip().lower()
        biography = next(
            (row["biography"] for row in localizations if row["lang"].lower() == configured),
            "",
        ) or next(
            (row["biography"] for row in localizations if row["lang"].lower().split("-")[0] == configured.split("-")[0]),
            "",
        ) or localizations[0]["biography"]
    return {
        "status": "hit" if name else "miss",
        "provider": "tmdb",
        "sourceLabel": "TMDb",
        "sourceRef": f"tmdb:person:{tmdb_id}",
        "tmdbId": tmdb_id,
        "imdbId": imdb_id,
        "name": name,
        "biography": biography,
        "birthday": data.get("birthday") or "",
        "deathday": data.get("deathday") or "",
        "placeOfBirth": data.get("place_of_birth") or "",
        "knownFor": data.get("known_for_department") or "",
        "alsoKnownAs": aliases,
        "profileUrl": profile_url,
        "profilePath": data.get("profile_path") or "",
        "profiles": profiles,
        "localizations": localizations,
        "language": language,
    }


def person_awards(payload, context=None):
    """Fetch award/nomination history for a person from Wikidata.

    Resolves the person via their TMDb id (Wikidata P4985) or IMDb id (P345)
    and returns awards normalized and grouped by award.
    """
    payload = payload or {}
    tmdb_id = str(payload.get("tmdbId") or payload.get("tmdb_id") or "").strip()
    imdb_id = str(payload.get("imdbId") or payload.get("imdb_id") or "").strip()
    wikidata_id = str(payload.get("wikidataId") or payload.get("wikidata_id") or "").strip()
    if not (tmdb_id or imdb_id or wikidata_id):
        return {"status": "miss", "provider": "wikidata", "reason": "tmdbId, imdbId or wikidataId is required"}
    module = _import_wikidata_awards()
    if module is None:
        return {"status": "error", "provider": "wikidata", "reason": "wikidata_awards module unavailable"}
    language = str(_settings(context).get("language") or "en").split("-")[0] or "en"
    result = module.fetch_person_awards(
        tmdb_id=tmdb_id or None,
        imdb_id=imdb_id or None,
        wikidata_id=wikidata_id or None,
        language=language,
    )
    awards = result.get("awards") or []
    return {
        "status": "hit" if awards else "miss",
        "provider": "wikidata",
        "sourceLabel": "Wikidata",
        "sourceRef": f"wikidata:{result.get('wikidataId') or ''}",
        "tmdbId": tmdb_id,
        "imdbId": imdb_id,
        "wikidataId": result.get("wikidataId") or "",
        "awards": awards,
        "awardGroups": module.group_awards(awards),
        "counts": {"awards": len(awards)},
    }


def person_filmography(payload, context=None):
    tmdb_id = str((payload or {}).get("tmdbId") or (payload or {}).get("tmdb_id") or "").strip()
    if not tmdb_id:
        return {"status": "miss", "provider": "tmdb", "reason": "tmdbId is required"}
    data = _request(context or {}, f"/person/{tmdb_id}/combined_credits", language=_language(context))
    cast = _filmography_items(data.get("cast") or [], "actor")
    crew = _filmography_items(data.get("crew") or [], "crew")
    return {
        "status": "hit" if cast or crew else "miss",
        "provider": "tmdb",
        "sourceLabel": "TMDb",
        "sourceRef": f"tmdb:person:{tmdb_id}",
        "tmdbId": tmdb_id,
        "combinedCredits": {
            "cast": cast,
            "crew": crew,
        },
        "counts": {
            "cast": len(cast),
            "crew": len(crew),
            "total": len(cast) + len(crew),
        },
    }
