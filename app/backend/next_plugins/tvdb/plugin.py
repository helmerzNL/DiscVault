"""TheTVDB as a series source.

Television only, and deliberately so. TVDB indexes films too, but its reason to
exist here is the depth TMDB does not have: episode titles and numbering for
shows TMDB is thin on, and coverage of older and non-US series. A source that
answers every question makes the plugin order unreadable -- the user ranks
sources to decide who wins a field, and a source with an opinion on everything
wins fields nobody meant it to.

Every URL and response field below is taken from TheTVDB's own OpenAPI document
(`thetvdb/v4-api`, `docs/swagger.yml`) and their official Python client
(`thetvdb/tvdb-v4-python`), not from memory. That mattered: the API is behind an
egress block from the environment this was written in, so the shapes could not be
tried, only read. Reading the producer's own spec is the difference between
untested and unfounded.
"""

TVDB_API = "https://api4.thetvdb.com/v4"


def _settings(context):
    return (context or {}).get("settings") or {}


def _secrets(context):
    return (context or {}).get("secrets") or {}


def _api_key(context):
    return str(_secrets(context).get("apiKey") or _secrets(context).get("api_key") or "").strip()


def _pin(context):
    return str(_secrets(context).get("pin") or "").strip()


def _language(context):
    return str(_settings(context).get("language") or "eng").strip() or "eng"


# A token is valid for a month, so logging in per call would multiply every
# lookup by two requests against a source whose terms ask for restraint. Cached
# on the api key rather than globally: two installations, or a key being
# corrected, must not hand each other a token.
_TOKENS = {}


def _token(context):
    import requests

    api_key = _api_key(context)
    if not api_key:
        raise RuntimeError("TheTVDB API key is not configured")
    cached = _TOKENS.get(api_key)
    if cached:
        return cached
    body = {"apikey": api_key}
    pin = _pin(context)
    if pin:
        # Only sent when set. TheTVDB's own client omits the field entirely for a
        # project key, and an empty string is not the same as absent.
        body["pin"] = pin
    response = requests.post(f"{TVDB_API}/login", json=body, timeout=10)
    response.raise_for_status()
    token = ((response.json() or {}).get("data") or {}).get("token") or ""
    if not token:
        raise RuntimeError("TheTVDB login returned no token")
    _TOKENS[api_key] = token
    return token


def _request(context, path, **params):
    import requests

    def call(token):
        return requests.get(
            f"{TVDB_API}{path}",
            params={key: value for key, value in params.items() if value not in (None, "")},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )

    response = call(_token(context))
    if response.status_code == 401:
        # The cached token outlived its month, or the key was rotated. One retry
        # with a fresh token, never a loop: a genuinely rejected key would
        # otherwise hammer the login endpoint.
        _TOKENS.pop(_api_key(context), None)
        response = call(_token(context))
    response.raise_for_status()
    payload = response.json() or {}
    if str(payload.get("status") or "") == "failure":
        raise RuntimeError(payload.get("message") or "TheTVDB request failed")
    return payload.get("data") or {}


def _series_identifier(payload):
    identifiers = (payload or {}).get("seriesIdentifiers") or {}
    return str(identifiers.get("tvdb") or "").strip()


def _artwork_urls(record, wanted_type):
    """Artwork of one kind, best score first.

    `artworks` is a flat list across every kind -- posters, backgrounds, banners,
    icons -- so filtering by `type` is what keeps a banner out of the poster slot.
    The numeric type ids are TheTVDB's own: 2 is a series poster, 3 a background.
    """
    urls = []
    for artwork in (record or {}).get("artworks") or []:
        if not isinstance(artwork, dict) or artwork.get("type") != wanted_type:
            continue
        url = str(artwork.get("image") or "").strip()
        if url and url not in urls:
            urls.append(url)
    return urls


def _default_seasons(record):
    """Only the seasons of the series' own default ordering.

    `seasons` carries every ordering TheTVDB holds -- default, DVD, absolute --
    in one list, so a show with a DVD order returns season 1 twice. Taking them
    all would make the season list disagree with itself, and which of the two
    won would depend on list order.
    """
    default_type = str(((record or {}).get("defaultSeasonType")) or "")
    seasons = []
    for season in (record or {}).get("seasons") or []:
        if not isinstance(season, dict):
            continue
        season_type = season.get("type") or {}
        type_id = str(season_type.get("id") or "")
        type_name = str(season_type.get("type") or "")
        if default_type and type_id and type_id != default_type:
            continue
        if not default_type and type_name and type_name != "official":
            continue
        number = season.get("number")
        if not isinstance(number, int) or isinstance(number, bool):
            continue
        seasons.append(
            {
                "seasonNumber": number,
                "title": str(season.get("name") or ""),
                "year": str(season.get("year") or "")[:4],
                "posterUrl": str(season.get("image") or ""),
            }
        )
    return seasons


def series_details(payload, context=None):
    """Describe a series TheTVDB has already been told the identity of.

    Refuses to search, exactly as the TMDB plugin does and for the same reason:
    §7b of the media-type document allows only a source querying a series
    namespace to speak to series identity, and matching on a title is a guess
    wearing an answer's clothes. `search_series` below is the other case -- a
    person typed the title and will choose.
    """
    identifier = _series_identifier(payload)
    if not identifier:
        return {"status": "miss", "provider": "tvdb"}
    record = _request(context or {}, f"/series/{identifier}/extended", meta="translations")
    if not record:
        return {"status": "miss", "provider": "tvdb"}
    posters = _artwork_urls(record, 2)
    backdrops = _artwork_urls(record, 3)
    return {
        "status": "hit",
        "provider": "tvdb",
        "sourceLabel": "TheTVDB",
        "sourceRef": f"tvdb:series:{identifier}",
        "series": {
            "title": str(record.get("name") or ""),
            "overview": str(record.get("overview") or ""),
            "startYear": str(record.get("firstAired") or "")[:4],
            "endYear": str(record.get("lastAired") or "")[:4],
            "posterUrl": posters[0] if posters else str(record.get("image") or ""),
            "posters": posters,
            "backdropUrl": backdrops[0] if backdrops else "",
            "backdrops": backdrops,
        },
        "seasons": _default_seasons(record),
    }


def search_series(payload, context=None):
    """Candidate series for a title a person typed.

    A candidate carries `identifierType` alongside its value, because which
    namespace an id belongs to is knowledge this source has and the caller would
    otherwise have to reconstruct.
    """
    title = str((payload or {}).get("title") or "").strip()
    if not title:
        return {"status": "skipped", "provider": "tvdb", "items": []}
    results = _request(
        context or {},
        "/search",
        query=title,
        type="series",
        year=str((payload or {}).get("year") or "").strip(),
    )
    items = []
    for result in results or []:
        if not isinstance(result, dict):
            continue
        # `tvdb_id` rather than `id`: the latter is a prefixed string like
        # "series-1234", which is a search-result key and not the id any other
        # endpoint here accepts.
        identifier = str(result.get("tvdb_id") or "").strip()
        if not identifier:
            continue
        items.append(
            {
                "provider": "tvdb",
                "providerLabel": "TheTVDB",
                "identifierType": "tvdb",
                "identifier": identifier,
                "title": str(result.get("name") or ""),
                "year": str(result.get("year") or "")[:4],
                "overview": str(result.get("overview") or ""),
                "posterUrl": str(result.get("image_url") or ""),
            }
        )
    return {"status": "hit" if items else "miss", "provider": "tvdb", "items": items[:8]}


def season_episodes(payload, context=None):
    """The episode list for one season.

    One call per season rather than one for the show, which is why the surface
    that triggers it sits behind the Collectors switch: a ten-season show is ten
    requests, and the switch is who agreed to pay for them. The `season`
    parameter is TheTVDB's own filter, so the cost is one season's worth rather
    than the whole series filtered down here.
    """
    identifier = _series_identifier(payload)
    number = (payload or {}).get("seasonNumber")
    if not identifier or not isinstance(number, int) or isinstance(number, bool):
        return {"status": "skipped", "provider": "tvdb", "episodes": []}
    data = _request(
        context or {},
        f"/series/{identifier}/episodes/default",
        season=number,
        lang=_language(context),
    )
    episodes = []
    for episode in (data or {}).get("episodes") or []:
        if not isinstance(episode, dict):
            continue
        episode_number = episode.get("number")
        if not isinstance(episode_number, int) or isinstance(episode_number, bool):
            continue
        # The `season` filter is the source's, so trust but verify: a season a
        # request did not ask for landing in the list would file episodes under
        # the wrong season, which the schema forbids but only after the fact.
        if episode.get("seasonNumber") not in (None, number):
            continue
        episodes.append(
            {
                "episodeNumber": episode_number,
                "title": str(episode.get("name") or ""),
                "overview": str(episode.get("overview") or ""),
                "airDate": str(episode.get("aired") or ""),
                "runtimeMinutes": episode.get("runtime"),
                "stillUrl": str(episode.get("image") or ""),
            }
        )
    return {
        "status": "hit" if episodes else "miss",
        "provider": "tvdb",
        "episodes": episodes,
    }
