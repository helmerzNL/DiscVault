# DiscVault Database Contract for MovieVault

This document describes DiscVault's local database model from the perspective of
MovieVault integration. DiscVault is the private collection manager. MovieVault is a
shared metadata service. MovieVault must never depend on DiscVault user/private
collection state.

## Ownership Boundary

DiscVault owns:

- personal collection records
- user accounts, roles, passkeys and sessions
- watchlist and watch history
- purchase data, shelf/location, notes and personal ratings
- local Plex/Jellyfin configuration and private deep-link metadata
- local image cache files and offline sync state

MovieVault may receive only shared metadata:

- barcode / EAN / UPC
- title, original title, year and release date
- format, edition, country and language
- TMDb and IMDb ids
- runtime, HDR, audio tracks, subtitles, regions and screen ratios
- distributor, studios and genre
- public plot/overview and localized title/overview fields
- public poster/backdrop/profile URLs or hashes
- box-set title and member title metadata
- member sort order

## Core Movie Table

Table: `movies`

One row represents one physical DiscVault movie item. It is not necessarily one
abstract film: multiple rows may represent different editions or discs of the same
movie.

Important identity and metadata columns:

| Column | Meaning | Share with MovieVault |
| --- | --- | --- |
| `id` | Local DiscVault row id | No |
| `barcode` | Physical release barcode/EAN/UPC, unique locally | Yes |
| `title` | Local/display title | Yes |
| `sort_title` | Sorting helper | Yes, optional |
| `original_title` | Original title | Yes |
| `year` | Release or movie year | Yes |
| `release_date` | Release date | Yes |
| `edition` | Edition label, e.g. Steelbook | Yes |
| `edition_release_year` | Edition release year | Yes |
| `edition_release_date` | Edition release date | Yes |
| `country` | Release country | Yes |
| `language` | Release language | Yes |
| `director` | Text summary | Yes |
| `actor` | Text summary | Yes |
| `producer` | Text summary | Yes |
| `studios` | Studio names | Yes |
| `genre` | Genre names | Yes |
| `audience_rating` | Primary certification | Yes |
| `content_ratings` | JSON/text certifications by country | Yes |
| `format` | Physical format, default `4K UHD` | Yes |
| `runtime` | Runtime text/minutes | Yes |
| `hdr` | HDR formats | Yes |
| `packaging` | Packaging type | Yes |
| `screen_ratios` | Aspect ratios | Yes |
| `audio_tracks` | Audio track summary | Yes |
| `subtitles` | Subtitle summary | Yes |
| `regions` | Region codes | Yes |
| `plot` | Public overview | Yes |
| `title_nl`, `title_fr`, `title_de`, `title_es`, `title_pt`, `title_it` | Localized titles | Yes |
| `plot_nl`, `plot_fr`, `plot_de`, `plot_es`, `plot_pt`, `plot_it` | Localized overviews | Yes |
| `extras` | Public extras/special features | Yes |
| `imdb_id` | IMDb id | Yes |
| `imdb_url` | IMDb URL | Yes, optional |
| `tmdb_id` | TMDb id | Yes |
| `poster` | Public poster URL/source URL | Yes |
| `backdrop` | Public backdrop URL/source URL | Yes |
| `backdrops` | JSON/text list of backdrop URLs | Yes |
| `trailer_url` | Trailer URL | Yes |
| `videos` | Video metadata | Yes |
| `distributor` | Distributor/label | Yes |

Private/local-only movie columns:

| Column | Meaning |
| --- | --- |
| `owner_id` | DiscVault user ownership |
| `purchase_date` | User purchase date |
| `purchase_price` | User purchase price |
| `rating` | Personal rating |
| `location` | Shelf/location |
| `notes` | Private notes |
| `poster_file` | Local cached poster filename |
| `added_at`, `updated_at`, `sync_revision` | Local lifecycle/offline sync fields |
| `edition_group_id`, `super_group_id`, `collection_id` | Legacy local container links |

MovieVault must not store or request the private/local-only fields.

## Normalized Container Model

DiscVault has three normalized container concepts:

- Vault: groups loose movies only.
- Box Set: groups loose movies only.
- Collection: can contain loose movies, vaults and box sets.

Legacy `movies.edition_group_id`, `movies.super_group_id`, `movies.collection_id`
and `edition_groups` are still maintained for PWA/iOS compatibility. New logic should
use the normalized tables.

### Vaults

Table: `vaults`

| Column | Meaning | Share with MovieVault |
| --- | --- | --- |
| `id` | Local vault id | No |
| `title` | Vault title | Usually no, unless used as public metadata |
| `barcode` | Optional container barcode | Yes if this is a release barcode |
| `tmdb_id`, `imdb_id` | External ids | Yes |
| `year` | Year/year range | Yes |
| `description` | Public description | Yes if metadata, no if user-written private text |
| `poster_file`, `backdrop` | Local media/cache fields | Only public URL/hash, not local file path |
| `primary_movie_id`, `badge_label`, `created_at`, `legacy_edition_group_id` | Local UI/migration fields | No |

Membership table: `vault_movies`

| Column | Meaning |
| --- | --- |
| `vault_id` | Local vault id |
| `movie_id` | Local movie id |
| `sort_order` | Member order |

MovieVault does not need local `vault_id`/`movie_id`. If vault-like metadata is ever
shared, send stable public identifiers, barcode and member title metadata.

### Box Sets

Table: `box_sets`

| Column | Meaning | Share with MovieVault |
| --- | --- | --- |
| `id` | Local box-set id | No |
| `title` | Box-set title | Yes |
| `barcode` | Box-set barcode/EAN/UPC | Yes |
| `tmdb_id`, `imdb_id` | External ids if available | Yes |
| `year` | Year or year range | Yes |
| `description` | Public description | Yes |
| `poster_file`, `backdrop` | Local media/cache fields | Only public URL/hash, not local file path |
| `primary_movie_id`, `badge_label`, `created_at`, `legacy_edition_group_id` | Local UI/migration fields | No |

Membership table: `box_set_movies`

| Column | Meaning | Share with MovieVault |
| --- | --- | --- |
| `box_set_id` | Local box-set id | No |
| `movie_id` | Local movie id | No |
| `sort_order` | Member order | Yes |

When contributing a box set, DiscVault should send:

```json
{
  "entityType": "box_set",
  "payload": {
    "barcode": "5051890315526",
    "title": "Example Complete Collection 4K",
    "format": "4K UHD",
    "country": "Germany",
    "language": "de",
    "yearRange": "2001-2011",
    "members": [
      {
        "title": "Example Movie",
        "year": 2001,
        "tmdbId": 123,
        "imdbId": "tt1234567",
        "sortOrder": 1
      }
    ]
  }
}
```

### Collections

Table: `collections`

Collections are personal/organizational DiscVault groupings. They are not public
MovieVault metadata by default.

| Column | Meaning | Share with MovieVault |
| --- | --- | --- |
| `id` | Local collection id | No |
| `title` | Collection title | Usually no |
| `badge_label`, `poster_file`, `backdrop`, `description`, `created_at` | Local UI fields | No by default |

Membership table: `collection_items`

| Column | Meaning |
| --- | --- |
| `collection_id` | Local collection id |
| `item_type` | `movie`, `vault` or `box_set` |
| `item_id` | Local id of the item type |
| `sort_order` | Local display order |

Collections may contain many movies, many vaults and many box sets at the same time.
They should not be uploaded to MovieVault automatically because they often represent
personal collection organization rather than public release metadata.

## People And Cast/Crew

Table: `people`

| Column | Meaning | Share with MovieVault |
| --- | --- | --- |
| `id` | Local person id | No |
| `tmdb_id` | TMDb person id | Yes |
| `name` | Person name | Yes |
| `photo_file` | Local cached profile filename | No local paths; public URL/hash only |
| `biography`, localized biography fields | Public biography text | Yes if sourced from public metadata |
| `birthday`, `deathday`, `place_of_birth`, `known_for` | Public facts | Yes |
| `updated_at`, `sync_revision` | Local sync fields | No |

Relationship table: `movie_people`

| Column | Meaning | Share with MovieVault |
| --- | --- | --- |
| `movie_id` | Local movie id | No |
| `person_id` | Local person id | No |
| `role` | `actor`, `cast`, `crew`, etc. | Yes |
| `character` | Character name | Yes |
| `job` | Crew job, e.g. Director | Yes |
| `sort_order` | Cast/crew order | Yes |

When sharing people/cast data, use TMDb/IMDb ids or MovieVault ids where available,
not DiscVault local ids.

## Digital Library Tables

Tables:

- `digital_library_sources`
- `digital_library_items`

These are local Plex/Jellyfin integration tables. They are private.

Do not share:

- `base_url`
- encrypted tokens
- Plex machine/server ids
- private external ids that only work inside a local server
- library ids
- local sync status

`digital_library_items` can be used inside DiscVault to link a physical movie to a
local Plex/Jellyfin item, but it must not be treated as MovieVault metadata unless the
data has been explicitly sanitized.

## Settings, Auth And Logs

Tables such as `settings`, `users`, `passkey_credentials`, `sessions`,
`custom_roles`, `role_permissions`, `user_roles`, `invite_codes`, `logs`,
`push_subscriptions`, `watchlist` and `watch_history` are DiscVault-private.

MovieVault must not receive:

- API tokens
- passkeys
- session/JWT metadata
- user ids
- roles/permissions
- logs
- watchlist/watch history
- notification subscriptions

MovieVault integration settings are stored in `settings` with keys such as:

- `movievault_search_url`
- `movievault_ingest_url`
- `movievault_contribution_url`
- `movievault_api_token`
- `movievault_sharing_mode`
- `movievault_contribution_enabled`
- `metadata_source_order`

These settings are operational configuration, not metadata.

## Local Sync Tables

Tables:

- `sync_state`
- `sync_changes`
- `sync_tombstones`
- `sync_operations`

These power native mobile offline sync. They are local DiscVault sync mechanics and
should not be shared with MovieVault.

Every relevant local mutation increments a monotonic `sync_revision`. Deletes produce
tombstones. This is for DiscVault clients, not MovieVault.

## Assets

DiscVault stores local image references in:

- `movies.poster_file`
- `people.photo_file`
- container `poster_file`
- `movies.backdrop`, `movies.backdrops`
- container `backdrop`

Public API routes expose images through stable routes:

- `/api/images/<filename>`
- `/api/images/offline/poster/<filename>`
- `/api/images/offline/backdrop/<filename>`
- `/api/images/profiles/<filename>`
- `/api/images/profiles/offline/profile/<filename>`

MovieVault may receive public image URLs or checksums/hashes. MovieVault must not
receive local filesystem paths.

## External MovieVault IDs

MovieVault responses may include stable ids:

- `movieVaultId`
- examples: `mv_movie_...`, `mv_release_...`, `mv_box_set_...`, `mv_person_...`

DiscVault should cache these stable ids for future deduplication and refreshes. Local
numeric DiscVault ids must not be used as public long-term references. As of this
contract, DiscVault's schema still needs explicit cache columns/tables for these ids.

Recommended future local cache fields/tables:

- `movievault_entities(local_entity, local_id, movievault_id, revision, updated_at)`
- or additive nullable columns on relevant tables, e.g. `movievault_id`,
  `movievault_revision`, `movievault_updated_at`

## Recommended MovieVault Contribution Mapping

### Movie Contribution

DiscVault source fields:

- `movies.title` -> `title`
- `movies.original_title` -> `originalTitle`
- `movies.year` -> `year`
- `movies.release_date` -> `releaseDate`
- `movies.tmdb_id` -> `tmdbId`
- `movies.imdb_id` -> `imdbId`
- `movies.runtime` -> `runtime`
- `movies.plot` -> `overview`
- localized `plot_*` -> localized `overview_*`
- localized `title_*` -> localized `title_*`
- technical fields -> same camelCase contract names

### Release Contribution

Use when a physical disc/release barcode is known:

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

### Box-Set Contribution

Use `box_sets` plus `box_set_movies` and each member movie's public metadata:

- box-set `barcode`, `title`, `country`, `language`, `format`, `yearRange`
- member `title`, `year`, `tmdbId`, `imdbId`, `sortOrder`

## Important Compatibility Notes

- The normalized container tables are the preferred model.
- Legacy fields are still mirrored for PWA and iOS compatibility.
- MovieVault should not assume local ids are stable outside a DiscVault instance.
- DiscVault may have multiple local movie rows for one abstract movie.
- DiscVault may have both a physical movie row and a digital-library match for the
  same title; only the physical/shared metadata is eligible for MovieVault.
- User consent/configuration controls whether contributions are submitted.

