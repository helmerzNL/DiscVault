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
            }
        )
    for item in credits.get("crew") or []:
        if item.get("job") in {"Director", "Producer", "Screenplay", "Writer", "Original Music Composer", "Director of Photography"}:
            crew.append(
                {
                    "role": "crew",
                    "name": item.get("name") or "",
                    "job": item.get("job") or "",
                    "tmdbId": item.get("id"),
                    "sortOrder": 0,
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
    genres = [item.get("name") for item in data.get("genres") or [] if item.get("name")]
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
            "genre": ", ".join(genres),
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
    items = search.get("items") or []
    if not items:
        return {"status": "miss", "provider": "tmdb"}
    return _normalize_details(_details(context or {}, items[0]["tmdbId"]))


def person_details(payload, context=None):
    tmdb_id = str((payload or {}).get("tmdbId") or (payload or {}).get("tmdb_id") or "").strip()
    if not tmdb_id:
        return {"status": "miss", "provider": "tmdb", "reason": "tmdbId is required"}
    language = _language(context)
    data = _request(
        context or {},
        f"/person/{tmdb_id}",
        language=language,
        append_to_response="translations",
    )
    aliases = data.get("also_known_as") or [] if data else []
    name = (data.get("name") if data else "") or (aliases[0] if aliases else "")
    profile_url = _image(data.get("profile_path")) if data.get("profile_path") else ""
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
        "name": name,
        "biography": biography,
        "birthday": data.get("birthday") or "",
        "deathday": data.get("deathday") or "",
        "placeOfBirth": data.get("place_of_birth") or "",
        "knownFor": data.get("known_for_department") or "",
        "profileUrl": profile_url,
        "profilePath": data.get("profile_path") or "",
        "localizations": localizations,
        "language": language,
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
