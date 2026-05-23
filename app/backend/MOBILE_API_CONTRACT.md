# DiscVault Mobile API Contract

This document defines the backend response contract for native mobile clients.
It is intended for iOS first, but Android should use the same contract if a
native Android app is added later.

The web PWA remains a first-class client. Backend changes for mobile clients
must be additive unless a coordinated frontend migration explicitly removes a
legacy field.

## Compatibility Rules

- Do not remove existing PWA fields from shared responses.
- Prefer adding camelCase aliases next to existing snake_case fields.
- Keep snake_case fields available for the current web frontend and older
  clients.
- Mobile clients should prefer camelCase fields when available.
- If a response contains both top-level movie fields and a nested `movie`
  object, mobile clients may use the nested object as the canonical shape.
- Image URLs returned to mobile clients should be absolute URLs whenever they
  point to backend-hosted assets.

## Image Contract

The backend owns the canonical image routes.

Preferred routes:

- `/api/images/<filename>` for movie posters, container posters and backdrops
- `/api/images/offline/poster/<filename>` for offline poster variants
- `/api/images/offline/backdrop/<filename>` for offline backdrop variants
- `/api/images/profiles/<filename>` for person profile images
- `/api/images/profiles/offline/profile/<filename>` for offline profile variants

Legacy aliases remain supported for compatibility:

- `/api/posters/...`
- `/api/posters/offline/...`
- `/api/profiles/...`
- `/api/profiles/offline/...`

Mobile clients should not build image routes from database fields such as
`poster_file`, `backdrop` or `photo_file`. Use API response image fields or the
sync `assetManifest`.

## Offline Sync Images

`GET /api/sync/bootstrap` and `GET /api/sync/delta` return an `assetManifest`.
Mobile clients should use this manifest for offline image caching.

Each asset entry should include:

- `kind`: `poster`, `backdrop` or `profile`
- `entity`: `movie`, `edition_group`, `collection`, `vault`, `box_set` or
  `person`
- `entityId`
- `url`
- `absoluteUrl`
- `offlineUrl`
- `checksum` or `etag`
- `revision`
- `updatedAt`

The backend normalizes legacy image records during sync responses:

- legacy `/api/posters/...` backdrops are rewritten to `/api/images/...`
- missing offline variants are generated when possible
- external backdrops are downloaded locally when possible
- unresolved external images remain available as URLs and are logged

## Filmography Contract

`GET /api/people/<person_id>/filmography` returns `cast` and `crew` arrays.
Every movie item should expose stable IDs and image fields for native clients.

Required movie item fields:

- `tmdb_id` and `tmdbId`
- `title`
- `year`
- `poster`
- `poster_url` and `posterUrl`
- `poster_path` and `posterPath`
- `in_collection` and `inCollection`
- `in_digital` and `inDigital`

When the movie exists in the DiscVault physical collection:

- `movie_id` and `movieId` must contain the local movie id
- `collection_id` and `collectionId` should contain the linked collection id
  when available
- `collection_format` and `collectionFormat` should contain the local format
  when available
- `posterUrl` should prefer an absolute `/api/images/...` URL for the local
  poster

When the movie exists in a connected digital library:

- `digital_id` and `digitalId` should contain the local digital item id
- `digital_source` and `digitalSource` should contain the source type, for
  example `plex` or `jellyfin`
- `digital_web_url` and `digitalWebUrl` should contain the browser/web URL for
  opening the item in Plex or Jellyfin
- `digital_app_url` and `digitalAppUrl` should contain a native app URL when
  available; currently this is populated for Plex deep links
- `web_url` / `webUrl` and `app_url` / `appUrl` are aliases for the same link
  targets
- `digital` may contain the same link data as a nested object with `webUrl`,
  `appUrl`, `sourceType`, `sourceName`, `externalId` and `digitalId`

When no local poster is available:

- `poster_path` should expose the TMDb path, for example `/abc.jpg`
- `posterUrl` may point to TMDb, currently using the `w185` size

Each filmography item also includes a nested `movie` object with the same core
fields. Native apps may prefer this nested object to avoid mixing role metadata
such as `character` or `job` with movie identity fields.

## Movie Digital Availability Contract

Movie responses are enriched from `digital_library_items` at response time.
Digital links are not stored directly on physical `movies` rows.

`GET /api/movies` and sync movie payloads include lightweight availability:

- `in_digital` and `inDigital`
- `digital_sources` and `digitalSources`
- `digital_ids` and `digitalIds`
- `digital_matches_count` and `digitalMatchesCount`

`GET /api/movies/<movie_id>` includes the same lightweight fields plus full
links:

- `digital_matches` and `digitalMatches`
- each match contains `webUrl`, `appUrl`, `sourceType`, `sourceName`,
  `externalId`, `digitalId`, `tmdbId` and `imdbId`

Digital matching order:

1. `tmdb_id`
2. `imdb_id`
3. normalized `title + year`

Plex links are built from `digital_library_sources.base_url`,
`digital_library_sources.machine_id` and `digital_library_items.external_id`.
Jellyfin links are built from `digital_library_sources.base_url` and
`digital_library_items.external_id`.

Example:

```json
{
  "tmdb_id": 987654,
  "tmdbId": 987654,
  "movie_id": 42,
  "movieId": 42,
  "collection_id": 7,
  "collectionId": 7,
  "digital_id": 3,
  "digitalId": 3,
  "title": "Example Movie",
  "year": "2026",
  "poster": "https://discvault.example/api/images/poster.jpg",
  "poster_url": "https://discvault.example/api/images/poster.jpg",
  "posterUrl": "https://discvault.example/api/images/poster.jpg",
  "poster_path": "/tmdb-poster.jpg",
  "posterPath": "/tmdb-poster.jpg",
  "in_collection": true,
  "inCollection": true,
  "in_digital": true,
  "inDigital": true,
  "digital_source": "plex",
  "digitalSource": "plex",
  "digital_web_url": "https://plex.example/web/index.html#!/server/machine/details?key=%2Flibrary%2Fmetadata%2F123",
  "digitalWebUrl": "https://plex.example/web/index.html#!/server/machine/details?key=%2Flibrary%2Fmetadata%2F123",
  "digital_app_url": "plex://machine/details?key=%2Flibrary%2Fmetadata%2F123",
  "digitalAppUrl": "plex://machine/details?key=%2Flibrary%2Fmetadata%2F123",
  "digital": {
    "digitalId": 3,
    "sourceType": "plex",
    "externalId": "123",
    "webUrl": "https://plex.example/web/index.html#!/server/machine/details?key=%2Flibrary%2Fmetadata%2F123",
    "appUrl": "plex://machine/details?key=%2Flibrary%2Fmetadata%2F123"
  },
  "movie": {
    "tmdbId": 987654,
    "movieId": 42,
    "collectionId": 7,
    "posterUrl": "https://discvault.example/api/images/poster.jpg",
    "digitalWebUrl": "https://plex.example/web/index.html#!/server/machine/details?key=%2Flibrary%2Fmetadata%2F123",
    "digitalAppUrl": "plex://machine/details?key=%2Flibrary%2Fmetadata%2F123"
  }
}
```

## Container Contract

Use the normalized container model documented in `CONTAINER_SCHEMA.md`.

Mobile clients should treat:

- Vaults as containers that contain movies
- Box Sets as containers that contain movies
- Collections as containers that can contain movies, vaults and box sets

Current backend responses still include legacy fields for PWA and older client
compatibility. Native clients should move toward normalized memberships from
sync responses:

- `containerMemberships.vaultMovies`
- `containerMemberships.boxSetMovies`
- `containerMemberships.collectionItems`

## Future Android Guidance

Android should use this same contract instead of adding Android-specific
backend shapes.

Backend changes for Android should only be needed when Android requires new
product behavior, not because field names, image URLs or IDs differ from iOS.
If Android needs a new field, add it to this shared contract and keep the iOS
and PWA compatibility rules intact.
