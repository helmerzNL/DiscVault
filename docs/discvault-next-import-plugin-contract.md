# DiscVault Next Import Plugin Contract

DiscVault Next treats every import path as a plugin-backed source. The goal is that barcode import, legacy DiscVault import, and imports from other collection apps all flow through the same runtime, job, logging, RBAC and backup boundaries.

## Plugin categories

- `metadata_source`: enriches existing or candidate media with metadata, artwork, technical specs, people and identifiers.
- `metadata_receiver`: receives metadata from DiscVault, for example MovieVault contribution.
- `digital_media_source`: connects to digital libraries such as Plex and Jellyfin.
- `import_source`: creates or updates collection data from an external source, including legacy DiscVault SQLite and future app imports.

Plugins can declare more than one category. MovieVault, for example, can be both metadata source and metadata receiver.

## Import entrypoints

Import plugins should expose these runtime entrypoints where applicable:

- `health_check`: report availability and missing configuration.
- `inspect_source`: inspect the mounted or configured source and return counts, warnings, hashes and required actions without changing DiscVault data.
- `plan_import`: build a deterministic import plan from the inspected source. The plan must include source identity, source hash/version, expected counters and whether confirmation is required.
- `import_source`: execute the approved plan and write collection data.
- `import_security_backfill`: optional follow-up for legacy users, passkeys, groups and membership data.

The entrypoints are intentionally split so the UI, API and MCP server can show a preview before anything writes to the database.

## Barcode import

Barcode import is an import workflow, not a special UI shortcut. A barcode scan creates an import intent with a barcode, optional locale and selected target group. DiscVault then:

1. Runs metadata sources in the configured source order.
2. Builds a candidate release/movie proposal with provenance.
3. Lets the user confirm the best match or manually correct it.
4. Applies the confirmed proposal as a collection import, including artwork options and technical specs.

This keeps barcode import compatible with the same logging, permissions and future bulk import queue as app imports.

## Ordering

Ordering is stored per plugin category:

- Metadata source order controls lookup and refresh priority.
- Import source order controls which importer is offered first when more than one source can handle a file or mounted directory.
- Digital source order controls display and sync precedence.

Owners and users with the appropriate plugin management permissions can change order. Runtime execution must always record the order used in the job result.

## RBAC

The minimum permission mapping is:

- `collection.import`: inspect, plan and execute collection imports.
- `metadata.search`: search/preview metadata candidates.
- `metadata.refresh_one` and `metadata.refresh_bulk`: refresh metadata for one or more existing movies/people.
- `metadata.manage_plugins` and `metadata.manage_plugin_settings`: enable, order and configure metadata plugins.
- `metadata.manage_receivers`: enable metadata receivers such as MovieVault contribution.
- `digital_sources.view`, `digital_sources.connect`, `digital_sources.sync`, `digital_sources.manage`: digital media source lifecycle.
- `admin.view_jobs`: inspect import/plugin/metadata job history.
- `admin.restore_functional`: restore functional collection backups.

Basic roles map these permissions to fixed presets. Advanced mode can assign the same permissions to custom roles.

## API and MCP

HTTP API and MCP should expose the same lifecycle:

- registry: list installed plugins, categories, capabilities and runtime health.
- config: store plugin settings and secret references without returning secret values.
- inspect: call `inspect_source`.
- plan: call `plan_import`.
- start: queue or execute `import_source`.
- status: list active and historical jobs.
- logs: return redacted counters, warnings, plugin execution states and source identity.

MCP tools must call the API/domain layer rather than bypassing RBAC or persistence.

## Logging

Every import, metadata refresh and plugin execution is represented as a `background_jobs` row when asynchronous, or as an API result with the same shape when synchronous. Results must include:

- plugin id, entrypoint, source identity and source hash/version.
- started/finished timestamps and elapsed time.
- counters, skipped items, warnings and errors.
- provenance for accepted metadata fields.
- redacted configuration state only; never secret values.

## Backup and restore

Functional backups are collection backups only. They include movies, people, credits, containers, vaults, box-sets, collections, artwork references and the relationships between them. They do not include plugins, plugin secrets, authentication state, passkeys, user security data or runtime job logs.

Restore validates the ZIP before writing and is governed by `admin.restore_functional`. Import plugins may be used to restore or transform external backup formats, but DiscVault native functional backup remains the stable interchange format.
