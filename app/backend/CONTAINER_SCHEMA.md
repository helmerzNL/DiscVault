# DiscVault Container Schema

This document captures the normalized container model for Vaults, Box Sets and
Collections. It is the reference for backend work and for migrating the iOS app
away from the legacy movie fields.

## Current Compatibility Rule

The backend currently writes both models:

- normalized tables are the source of truth for new container membership logic;
- legacy `movies` and `edition_groups` columns remain populated for existing
  frontend and iOS compatibility.

Do not remove legacy fields until both the web frontend and iOS app have moved
to normalized APIs.

## Normalized Tables

### Vaults

Vaults group loose movies only.

- `vaults`
  - one row per vault
  - `id` intentionally matches the legacy `edition_groups.id` when migrated
  - `legacy_edition_group_id` links back to the old model
- `vault_movies`
  - many-to-many membership table
  - columns: `vault_id`, `movie_id`, `sort_order`
  - primary key: `(vault_id, movie_id)`

### Box Sets

Box Sets group loose movies only.

- `box_sets`
  - one row per box set
  - `id` intentionally matches the legacy `edition_groups.id` when migrated
  - `legacy_edition_group_id` links back to the old model
- `box_set_movies`
  - many-to-many membership table
  - columns: `box_set_id`, `movie_id`, `sort_order`
  - primary key: `(box_set_id, movie_id)`

### Collections

Collections can contain movies, vaults and box sets at the same time.

- `collections`
  - one row per collection
- `collection_items`
  - polymorphic membership table
  - columns: `collection_id`, `item_type`, `item_id`, `sort_order`
  - `item_type` is one of:
    - `movie`
    - `vault`
    - `box_set`
  - primary key: `(collection_id, item_type, item_id)`

This allows one collection to contain any mix such as 10 box sets, 10 vaults and
20 loose movies.

## Legacy Compatibility Fields

These fields still exist and are kept in sync:

- `movies.edition_group_id`
  - legacy link for a movie inside a vault
  - mirrored to `vault_movies`
- `movies.super_group_id`
  - legacy link for a movie inside a box set
  - mirrored to `box_set_movies`
- `movies.collection_id`
  - legacy link for a loose movie inside a collection
  - mirrored to `collection_items` with `item_type='movie'`
- `edition_groups.group_type`
  - legacy container type: `vault` or `boxset`
- `edition_groups.parent_group_id`
  - legacy box-set nesting support
  - should not be used for new client logic once normalized APIs are available
- `edition_groups.collection_id`
  - legacy link from a vault or box set to a collection
  - mirrored to `collection_items` with `item_type='vault'` or `box_set`

## Startup Migration

The backend runs the container migration during `init_db()` on every startup:

1. `_init_container_tables(conn)` creates the normalized tables.
2. `_migrate_legacy_containers(conn)` backfills normalized data from legacy
   `edition_groups`, `movies.edition_group_id`, `movies.super_group_id`,
   `movies.collection_id` and `edition_groups.collection_id`.

The migration uses `INSERT OR IGNORE`, so it is idempotent and safe to run on
each deployment.

## Type Conversion

Changing an `edition_group` type through `PUT /api/edition-groups/<id>` with
`group_type` converts both models.

### Vault to Box Set

- creates or refreshes `box_sets`
- moves membership into `box_set_movies`
- removes old `vault_movies` membership for that id
- updates movies from `edition_group_id=<id>` to `super_group_id=<id>`
- updates `collection_items` from `item_type='vault'` to `item_type='box_set'`

### Box Set to Vault

- creates or refreshes `vaults`
- moves membership into `vault_movies`
- removes old `box_set_movies` membership for that id
- updates movies from `super_group_id=<id>` to `edition_group_id=<id>`
- updates `collection_items` from `item_type='box_set'` to `item_type='vault'`

If the target row already exists with the same id, memberships are merged with
`INSERT OR IGNORE`.

## Current API Behavior

The existing public API still accepts legacy-shaped payloads and mirrors them to
the normalized tables.

### Creating A Movie

`POST /api/movies` currently accepts:

- `edition_group_id`: writes `movies.edition_group_id` and `vault_movies`
- `super_group_id`: writes `movies.super_group_id` and `box_set_movies`
- `collection_id`: writes `movies.collection_id` and `collection_items`

### Updating A Movie

`PUT /api/movies/<id>` keeps the normalized tables synchronized when any of
these legacy fields changes:

- `edition_group_id`
- `super_group_id`
- `collection_id`

### Bulk And Member APIs

Bulk assignment and member APIs also write both models:

- adding to a vault writes `vault_movies` and `movies.edition_group_id`
- adding to a box set writes `box_set_movies` and `movies.super_group_id`
- adding loose movies to a collection writes `collection_items` and
  `movies.collection_id`

## iOS Migration Guidance

For the iOS app, migrate in phases.

1. Keep reading existing movie fields while adding support for normalized
   response data.
2. Prefer normalized container concepts in the UI:
   - Vault contains movies.
   - Box Set contains movies.
   - Collection contains movies, vaults and box sets.
3. During the compatibility phase, send legacy payload fields to existing APIs:
   - `edition_group_id` for vault membership
   - `super_group_id` for box-set membership
   - `collection_id` for loose collection movie membership
4. Add or switch to normalized API payloads only after the backend exposes
   explicit `vault_id`, `box_set_id` and `collection_items` write endpoints.
5. Do not model Box Sets as containing Vaults in new iOS logic. Legacy
   `parent_group_id` exists only for compatibility and migration.

## Future Backend Work

Add normalized write APIs before removing legacy compatibility:

- create/update vaults through `/api/vaults`
- create/update box sets through `/api/box-sets`
- add/remove vault movies with `vault_id`
- add/remove box-set movies with `box_set_id`
- add/remove collection items with `{ item_type, item_id }`
- accept normalized aliases in `POST /api/movies` and `PUT /api/movies/<id>`

Once iOS and web use those APIs, legacy write mirroring can be reduced and later
removed in a separate migration.
