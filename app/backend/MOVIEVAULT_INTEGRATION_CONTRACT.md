# DiscVault -> MovieVault Integration Contract

MovieVault is DiscVault's shared metadata service. DiscVault remains the private
collection manager; MovieVault receives shared metadata only, never personal
collection state.

MovieVault has two logical entry points:

- `search.discvault.eu`: metadata lookup.
- `movies.discvault.eu`: metadata contributions/corrections.

## MovieVault IDs

MovieVault returns stable external ids for shared entities. DiscVault should use
`movieVaultId` for long-term references, deduplication and future refreshes.
Numeric `id` values from MovieVault are internal/admin convenience ids.

Examples:

- `mv_movie_...`
- `mv_release_...`
- `mv_box_set_...`
- `mv_person_...`

## Configuration

DiscVault supports these backend configuration values:

```env
MOVIEVAULT_SEARCH_URL=https://search.discvault.eu
MOVIEVAULT_INGEST_URL=https://movies.discvault.eu
MOVIEVAULT_API_TOKEN=mv_...
MOVIEVAULT_SHARING_MODE=opt_in
```

Legacy setting names remain accepted as fallbacks during migration.

## Auth

All ingest/write calls use:

```http
Authorization: Bearer <MOVIEVAULT_API_TOKEN>
Content-Type: application/json
```

Required token scopes:

- `contributions:write`
- `contributions:read`

Search and contribution-template discovery do not require bearer auth.

## Privacy Boundary

DiscVault must never submit user ids, passkeys, sessions, private notes, purchase
data, shelf/location, watch history, watchlist, personal ratings, group
memberships, local file paths, Plex/Jellyfin tokens, private Plex/Jellyfin URLs,
or Plex/Jellyfin server identifiers.

Allowed shared metadata includes barcode/EAN/UPC, titles, year, release date,
format, edition, country, language, TMDb/IMDb ids, runtime, HDR, audio tracks,
subtitles, regions, screen ratios, distributor, studios, genre, technical specs,
box-set title, member titles, member sort order, public image URLs/hashes and
localized public metadata.

Localized fields use two-letter suffixes such as `title_nl`, `overview_de`.

## Search API

Base URL:

```text
MOVIEVAULT_SEARCH_URL=https://search.discvault.eu
```

Endpoints:

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

Barcode lookup returns `status: "found"` or `status: "not_found"`. Found
results may be `type: "release"` or `type: "box_set"`. Box-set responses should
include `members` with `title`, `year`, `tmdbId`, `imdbId` and `sortOrder`.

DiscVault uses MovieVault first, then falls back according to
`metadata_source_order` (default `movievault,tmdb,omdb,bluray_com,bluray_disc_de`).
If fallback providers enrich a MovieVault hit with additional fields, DiscVault
submits the merged public metadata back to MovieVault.

## Contribution Template Discovery

DiscVault discovers the currently supported contribution fields before
submitting data. This avoids hard-coded field drift.

Base URL:

```text
MOVIEVAULT_INGEST_URL=https://movies.discvault.eu
```

Template endpoints:

- `GET /api/v1/contribution-template`
- `GET /api/v1/contribution-template/{entityType}`

Supported entity types:

- `movie`
- `release`
- `box_set`
- `person`

Template index response:

```json
{
  "version": "2026-05-23.1",
  "entityTypes": ["movie", "release", "box_set", "person"],
  "localizedFieldPattern": "<field>_<iso-639-1-language>",
  "templates": {
    "movie": {
      "entityType": "movie",
      "required": ["title"],
      "fields": {
        "title": {"type": "string", "localized": true}
      }
    }
  },
  "notes": []
}
```

DiscVault cache rules:

- Fetch at first contribution and then at most once per 24 hours.
- Store the last successful template JSON, `version` and fetch timestamp in
  local `settings`.
- Use the cached template if MovieVault is temporarily unavailable.
- Use a bundled fallback template if no cache exists.
- Force-refresh the template after a MovieVault `400 validation_error`.

Payload rules:

- The template is an allowlist.
- Send only fields present in `fields`.
- Send only non-empty values.
- Never send local DiscVault ids.
- Check `required` before submitting.
- Remove unknown fields from the payload.
- Use canonical field names for new code.
- Localized variants are allowed only when the base field has
  `"localized": true`.

DiscVault logs the template version and source (`live`, `cache`,
`stale-cache`, `fallback`) for every contribution attempt.

## Template Field Mapping

### Movie

Movie metadata describes the public film object.

Core fields:

- `barcode` <- `movies.barcode`
- `title` <- `movies.title`
- `originalTitle` <- `movies.original_title`
- `year` <- `movies.year`
- `releaseDate` <- `movies.release_date`
- `tmdbId` <- `movies.tmdb_id`
- `imdbId` <- `movies.imdb_id`
- `overview` <- `movies.plot`
- `runtime` <- `movies.runtime`
- `people` <- `movie_people` + `people`

DiscVault also supports template-allowed release/spec fields on movie
contributions when they are public metadata: `format`, `edition`, `country`,
`language`, `hdr`, `audioTracks`, `subtitles`, `regions`, `screenRatios`,
`distributor`, `studios`, `genre`, `posterUrl`, `backdropUrl`.

### Release

Release metadata describes a physical edition with barcode:

- `barcode`
- `title`
- `country`
- `language`
- `format`
- `edition`
- `distributor`
- `hdr`
- `audioTracks`
- `subtitles`
- `regions`
- `screenRatios`

### Box Set

MovieVault uses only DiscVault's normalized `box_sets` model. Vaults and
collections are private DiscVault organization concepts.

Source tables:

- `box_sets`
- `box_set_movies`
- linked public `movies` metadata

Fields:

- `barcode`
- `title`
- `tmdbId`
- `imdbId`
- `format`
- `country`
- `language`
- `yearRange`
- `description`
- `members`

`members` items use `title`, `year`, `tmdbId`, `imdbId` and `sortOrder`.

### Person

Person metadata may be contributed for public cast/crew facts. Local person ids
must not be sent.

Fields:

- `name`
- `tmdbId`
- `biography`
- `birthday`
- `deathday`
- `placeOfBirth`
- `knownFor`

## Ingest API

Endpoint:

```http
POST /api/v1/contributions
```

Required fields:

- `idempotencyKey`
- `sourceClient`
- `sharingMode`
- `entityType`
- `payload`

Optional:

- `sourceVersion`

Allowed `sharingMode`:

- `opt_in`
- `opt_out`

Allowed `entityType`:

- `movie`
- `release`
- `box_set`
- `person`

DiscVault treats `duplicate` responses as successful/idempotent outcomes.

DiscVault idempotency currently fingerprints the public entity identity plus the
filtered payload. Exact retries remain idempotent, while a later richer payload
can be submitted as a new contribution.

## Error Handling

- `400 validation_error`: log, force-refresh contribution template, rebuild the
  payload and retry once.
- Other `400`: log and do not retry automatically.
- `401 unauthorized`: treat as MovieVault configuration/token-scope problem.
- `404`: route/resource missing; barcode not-found is HTTP 200 with
  `status: "not_found"`.
- `5xx`: log and do not block local DiscVault user flows.

## DiscVault UI/UX Rules

DiscVault must:

- let the user choose whether metadata contributions may be shared,
- respect `opt_in`, `opt_out` and disabled sharing,
- let the user inspect imported MovieVault proposals,
- keep local edits possible even if MovieVault has different metadata,
- handle MovieVault downtime gracefully.

DiscVault may:

- enrich barcode scans via MovieVault,
- show box-set member proposals,
- submit user-approved or provider-enriched public metadata,
- cache MovieVault ids/revisions locally for later refreshes.

DiscVault must not treat MovieVault as an authority over the private collection.
