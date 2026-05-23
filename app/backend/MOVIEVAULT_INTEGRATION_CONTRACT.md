# DiscVault -> MovieVault Integration Contract

MovieVault is DiscVault's shared metadata service. DiscVault remains the private
collection manager; MovieVault receives shared metadata only, never personal collection
state.

## Services

- `MOVIEVAULT_SEARCH_URL=https://search.discvault.eu`
- `MOVIEVAULT_INGEST_URL=https://movies.discvault.eu`
- `MOVIEVAULT_API_TOKEN=mv_...`
- `MOVIEVAULT_SHARING_MODE=opt_in`

Legacy DiscVault setting names are accepted as fallbacks during migration, but the names
above are the contract names.

## Search API

Search calls do not require a bearer token.

- `GET /api/v1/health`
- `GET /api/v1/barcodes/{barcode}`
- `GET /api/v1/movies?q=<query>`
- `GET /api/v1/movies?tmdbId=<id>`
- `GET /api/v1/movies?imdbId=<id>`
- `GET /api/v1/box-sets?q=<query>`
- `GET /api/v1/box-sets?barcode=<ean>`
- `GET /api/v1/box-sets?tmdbId=<id>`
- `GET /api/v1/box-sets?imdbId=<id>`
- `GET /api/v1/box-sets/{id}`
- `GET /api/v1/box-sets/{id}/members`

Barcode lookup returns `status: "found"` or `status: "not_found"`. Found results may be
`type: "release"` or `type: "box_set"`. Box-set responses should include `members` with
`title`, `year`, `tmdbId`, `imdbId`, and `sortOrder` where available.

DiscVault uses MovieVault first, then falls back according to `metadata_source_order`
(default `movievault,tmdb,omdb,bluray_com,bluray_disc_de`).

## Ingest API

Write calls use:

```http
Authorization: Bearer <MOVIEVAULT_API_TOKEN>
Content-Type: application/json
```

Endpoint:

- `POST /api/v1/contributions`
- `GET /api/v1/contributions/{contributionId}`

DiscVault posts movie contributions when MovieVault was tried first but had no usable
result and another provider did. The payload follows MovieVault's contract:

```json
{
  "idempotencyKey": "...",
  "sourceClient": "discvault",
  "sourceVersion": "3.4.x",
  "sharingMode": "opt_in",
  "entityType": "movie",
  "payload": {
    "title": "Example Movie",
    "originalTitle": "Example Movie",
    "year": 2001,
    "releaseDate": "2001-06-01",
    "tmdbId": 123,
    "imdbId": "tt1234567",
    "runtime": 120,
    "overview": "Example overview",
    "overview_nl": "Voorbeeldomschrijving"
  }
}
```

`sharingMode` may be `opt_in`, `opt_out`, or `disabled`. DiscVault defaults to `opt_in`.
When disabled, DiscVault does not submit contributions.

## Privacy

DiscVault must never submit user ids, passkeys, sessions, private notes, purchase data,
shelf/location, watch history, watchlist, personal ratings, group memberships, local file
paths, Plex/Jellyfin tokens, private Plex/Jellyfin URLs, or Plex/Jellyfin server ids.

Allowed shared metadata includes barcode/EAN/UPC, titles, year, release date, format,
edition, country, language, TMDb/IMDb ids, runtime, HDR, audio tracks, subtitles, regions,
screen ratios, distributor, studios, genre, technical specifications, box-set title,
box-set member titles, member sort order, public image URLs or hashes, and localized
metadata fields such as `title_nl` and `overview_de`.

## Error Handling

- `400 validation_error`: log and do not retry without changing payload.
- `401 unauthorized`: treat as MovieVault configuration error.
- `404`: route/resource missing; barcode not-found is represented by HTTP 200 with
  `status: "not_found"`.
- `5xx`: do not block DiscVault user flows; retry/backoff can be added around queued
  contribution delivery.
