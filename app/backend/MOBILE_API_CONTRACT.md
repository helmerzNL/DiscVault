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

## Barcode Lookup Contract

`GET /api/lookup/<barcode>` returns a single JSON response. The PWA scanner may
use `GET /api/lookup/<barcode>?stream=1`, which returns newline-delimited JSON
progress events and ends with a final `type: "done"` object. The final object
uses the same status values as the non-streaming endpoint.

When the scanned barcode already exists locally:

- loose physical movies return `status: "movie_exists"` with `movie` and
  `barcode`
- box sets return `status: "box_set_exists"` with `box_set` and `barcode`
- vaults return `status: "vault_exists"` with `vault`, `container`,
  `container_type: "vault"` and `barcode`

Example loose movie response:

```json
{
  "status": "movie_exists",
  "movie": {
    "id": 42,
    "title": "Example Movie",
    "barcode": "5051890315526"
  },
  "barcode": "5051890315526"
}
```

Example streaming final event:

```json
{
  "type": "done",
  "status": "movie_exists",
  "movie": {
    "id": 42,
    "title": "Example Movie",
    "barcode": "5051890315526"
  },
  "barcode": "5051890315526"
}
```

Older backend builds used `status: "exists"` for existing loose movies. Clients
may keep accepting `exists` as a fallback, but new clients should prefer
`movie_exists`.

## Movie Search Contract

`GET /api/movies?q=<query>` is the shared collection search endpoint for PWA and
mobile clients.

Search should match user-visible collection entities by:

- movie title and original title
- barcode / EAN
- director
- actor and crew names
- actor character names
- crew jobs
- genre, distributor, studio and legacy box-set text fields

Barcode / EAN search must cover:

- loose physical movies via `movies.barcode`
- box-set container cards via `box_sets.barcode`
- vault container cards via `vaults.barcode`

When container cards are included in movie list responses, they should expose a
top-level `barcode` field when the corresponding box-set or vault has one. PWA
and mobile clients may use that `barcode` field for local filtering when the
full movie list is already loaded.

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
- alternative movie backdrops from `movies.backdrops` are included as separate
  `kind: "backdrop"` asset entries with the same movie `entityId`; mobile
  clients should key assets by `url` / `absoluteUrl`, not by `entityId` alone

## Offline Sync Cast And Crew

`GET /api/sync/bootstrap` and `GET /api/sync/delta` include cast and crew
grouped per movie.

Preferred field:

- `movieCast`

Compatibility aliases:

- `movie_cast`
- `castByMovie`
- `cast_by_movie`

Each group contains:

- `movieId`
- `cast`

`cast` contains both actors and crew. Actors use `role: "actor"` when possible.
Crew members use `role: "crew"` and should include `job`.

Example:

```json
{
  "movieCast": [
    {
      "movieId": 123,
      "cast": [
        {
          "personId": 456,
          "name": "Will Smith",
          "role": "actor",
          "character": "Deadshot",
          "job": null,
          "photoUrl": "https://discvault.example/api/images/profiles/will.jpg",
          "photoFile": "will.jpg",
          "tmdbId": 2888
        },
        {
          "personId": 789,
          "name": "David Ayer",
          "role": "crew",
          "character": null,
          "job": "Director",
          "photoUrl": "https://discvault.example/api/images/profiles/david.jpg",
          "photoFile": "david.jpg",
          "tmdbId": 1234
        }
      ]
    }
  ]
}
```

The corresponding person profile images are also included in `assetManifest`
as `kind: "profile"` entries.

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
- `digital_app_url` and `digitalAppUrl` should contain the preferred open URL
  for native clients; for Plex this currently matches the hosted
  `https://app.plex.tv/desktop/...` URL because the older `plex://` custom
  scheme is not reliable across clients
- `digital_native_url` and `digitalNativeUrl` may contain a platform-specific
  native URL. For Plex this follows the documented
  `plex://play/?metadataKey=/library/metadata/<ratingKey>&server=<machineId>`
  format. Native apps should treat it as optional and always fall back to
  `digitalWebUrl`.
- `web_url` / `webUrl` and `app_url` / `appUrl` are aliases for the same link
  targets
- `digital` may contain the same link data as a nested object with `webUrl`,
  `appUrl`, `nativeUrl`, `sourceType`, `sourceName`, `externalId` and
  `digitalId`

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

Plex links are built with the hosted Plex app URL:
`https://app.plex.tv/desktop/#!/server/<machine_id>/details?key=<metadata_key>`.
The backend uses `digital_library_sources.machine_id` and
`digital_library_items.external_id` to build this URL.
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
  "digital_web_url": "https://app.plex.tv/desktop/#!/server/machine/details?key=%2Flibrary%2Fmetadata%2F123",
  "digitalWebUrl": "https://app.plex.tv/desktop/#!/server/machine/details?key=%2Flibrary%2Fmetadata%2F123",
  "digital_app_url": "https://app.plex.tv/desktop/#!/server/machine/details?key=%2Flibrary%2Fmetadata%2F123",
  "digitalAppUrl": "https://app.plex.tv/desktop/#!/server/machine/details?key=%2Flibrary%2Fmetadata%2F123",
  "digital_native_url": "plex://play/?metadataKey=%2Flibrary%2Fmetadata%2F123&server=machine",
  "digitalNativeUrl": "plex://play/?metadataKey=%2Flibrary%2Fmetadata%2F123&server=machine",
  "digital": {
    "digitalId": 3,
    "sourceType": "plex",
    "externalId": "123",
    "webUrl": "https://app.plex.tv/desktop/#!/server/machine/details?key=%2Flibrary%2Fmetadata%2F123",
    "appUrl": "https://app.plex.tv/desktop/#!/server/machine/details?key=%2Flibrary%2Fmetadata%2F123",
    "nativeUrl": "plex://play/?metadataKey=%2Flibrary%2Fmetadata%2F123&server=machine"
  },
  "movie": {
    "tmdbId": 987654,
    "movieId": 42,
    "collectionId": 7,
    "posterUrl": "https://discvault.example/api/images/poster.jpg",
    "digitalWebUrl": "https://app.plex.tv/desktop/#!/server/machine/details?key=%2Flibrary%2Fmetadata%2F123",
    "digitalAppUrl": "https://app.plex.tv/desktop/#!/server/machine/details?key=%2Flibrary%2Fmetadata%2F123",
    "digitalNativeUrl": "plex://play/?metadataKey=%2Flibrary%2Fmetadata%2F123&server=machine"
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
