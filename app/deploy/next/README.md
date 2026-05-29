# DiscVault Next Docker Compose

This compose file runs the PostgreSQL-backed DiscVault Next services from the
published `:dev` image. It does not need a local source checkout on the Docker
host.

It is written for Unraid-style Compose Manager usage, but the recommended beta
operation path is the CLI with a fixed Compose project name. Use
`discvault_next_deploy` consistently so container, volume, and network names stay
stable between updates.

## Files

```text
docker-compose.yml
.env
docker-compose.import.yml  # legacy optional one-off CLI importer
```

Create `.env` from `.env.example` and change `POSTGRES_PASSWORD` and
`JWT_SECRET` before first start.

## Start

Start the compose project from the directory containing `docker-compose.yml` and
`.env`:

```bash
docker compose -p discvault_next_deploy up -d
```

Update/recreate from a new published image:

```bash
docker pull ghcr.io/helmerznl/discvault:dev
docker compose -p discvault_next_deploy up -d --force-recreate
```

The default stack starts:

```text
postgres
next-api
next-worker
```

`next-api` runs pending PostgreSQL migrations during container startup before
Gunicorn starts. `next-worker` waits until `next-api` is healthy.

Health check:

```bash
curl http://localhost:6180/api/next/health
curl http://localhost:6180/api/next/stats
```

When this service is published directly behind a reverse proxy, the Next
collection UI is available at `/`, `/app`, and `/api/next/app`.

## Passkeys

Passkeys require a stable relying party configuration. Set these values in
`.env` before enabling authentication:

```text
JWT_SECRET=<long stable random secret>
RP_ID=appdev.discvault.eu
RP_NAME=DiscVault
RP_ORIGINS=https://appdev.discvault.eu
```

`RP_ID` is the browser hostname without scheme or port. `RP_ORIGINS` is a
comma-separated list of allowed origins. Browsers require a secure context for
passkeys, so use HTTPS for non-localhost deployments.

Auth status:

```bash
curl http://localhost:6180/api/next/auth/status
```

If legacy settings migrated `auth_enabled=true` but no users/passkeys were
imported, Next reports `configured_auth_enabled=true` and `auth_enabled=false`
until the first passkey exists. This keeps first-user setup reachable while
preserving the configured intent.

After the first passkey exists, Next keeps `/`, `/app`, `/api/next/app`,
`/api/next/auth/*`, and health public enough for the browser shell and passkey
flow. Collection data APIs require a Bearer token from passkey login.

Passkey login also sets an HttpOnly `dv_next_session` cookie with the same
24-hour session lifetime. The browser app still sends a Bearer token for API
fetches, but the cookie lets direct browser navigation to protected same-origin
API URLs work after login.

The Next collection view includes an owner/admin management panel after
passkey login. Owners and admins can view users, change roles, disable or delete
users, remove passkeys, toggle authentication, toggle invite-only registration,
and create 48-hour invite codes for new accounts.

The admin panel is split into Security, Users, Roles, and Plugins tabs. The
Roles tab shows the current Basic/Advanced RBAC mode, the permission catalog,
and the managed role list. The Plugins tab reads the generic plugin registry,
can enable or disable discovered plugins, and can run plugin health checks.

Ownership transfer is owner-only and requires step-up authentication. The
target user must be active and already have an admin-like role. During transfer,
the current owner must approve with their passkey again; the target becomes
owner and the previous owner is kept as admin.

MovieVault metadata receiving/contribution is separate from using MovieVault as
a lookup plugin. The owner can toggle `movievault_contribution_enabled` from
the admin panel; normal admins cannot change this receiver mode.

RBAC is available as an API foundation for the later full admin UI. The default
mode is `basic`, with the protected owner plus Beheerder, Media Editor, Media
Fan, and Media Viewer roles. Owners can switch to `advanced` mode and create
custom roles from the fixed permission catalog:

```bash
curl http://localhost:6180/api/next/auth/rbac
curl -X PATCH http://localhost:6180/api/next/auth/rbac \
  -H 'Content-Type: application/json' \
  -d '{"mode":"advanced"}'
curl -X POST http://localhost:6180/api/next/auth/roles \
  -H 'Content-Type: application/json' \
  -d '{"key":"curator","name":"Curator","permissions":["collection.view","metadata.search"]}'
curl -X PATCH http://localhost:6180/api/next/auth/users/<user-id>/roles \
  -H 'Content-Type: application/json' \
  -d '{"roles":["curator","media_viewer"]}'
```

Switching back to `basic` does not delete custom roles or assignments. It only
limits which roles are assignable through the basic role layer.

## Plugin Runtime

DiscVault Next loads plugins from manifest folders. The built-in plugin folder is
`/opt/discvault/backend/next_plugins`; extra folders can be added later with
`DISCVAULT_PLUGIN_PATHS` using the platform path separator.

Plugin categories are separate by design:

- `metadata_source`: DiscVault reads metadata from this source to enrich movies
  and physical releases.
- `metadata_receiver`: DiscVault can send/share metadata to this receiver.
- `digital_media_source`: DiscVault can connect to a digital library such as
  Plex or Jellyfin.

The API syncs discovered manifests into the generic `plugins` table. Metadata
source/receiver plugins are also mirrored into the existing `metadata_plugins`
table so the current metadata settings screen keeps working during the
transition.

Registry check:

```bash
curl http://localhost:6180/api/next/plugins/registry
curl http://localhost:6180/api/next/metadata/plugins
```

These endpoints require authentication once passkeys are enabled.

Admin plugin changes are exposed as API foundation for the later full plugin UI:

```bash
curl -X PATCH http://localhost:6180/api/next/plugins/plex \
  -H 'Content-Type: application/json' \
  -d '{"enabled":true,"orderIndex":105}'
```

The PATCH route requires an owner/admin session. Metadata source plugins are
kept in sync with the legacy-compatible `metadata_plugins` table; digital media
source plugins are updated in the generic `plugins` table.

Plugin configuration and health checks use separate admin endpoints:

```bash
curl http://localhost:6180/api/next/plugins/plex/config
curl -X PATCH http://localhost:6180/api/next/plugins/plex/config \
  -H 'Content-Type: application/json' \
  -d '{"settings":{"baseUrl":"https://plex.example"},"secrets":{"token":"replace-me"}}'
curl http://localhost:6180/api/next/plugins/plex/health
```

Secret values are stored as secret `app_settings` entries and are never returned
by the API. Responses only expose configured state, secret names, and internal
secret references.

Logs:

```bash
docker compose -p discvault_next_deploy logs --tail=100 next-api
docker compose -p discvault_next_deploy logs --tail=100 next-worker
docker compose -p discvault_next_deploy logs --tail=100 postgres
```

## Existing Data Directory

Set `DISCVAULT_DATA_DIR` in `.env` to the existing DiscVault data directory on
the Docker host. It is mounted into the Next API and worker as `/data`.

Expected contents:

```text
discvault.db
posters/
profiles/
avatars/
```

The migration reads `/data/discvault.db` and records PostgreSQL references to
the existing media files. It does not move or copy existing posters, backdrops,
profiles, or avatars; those files remain on the filesystem in their existing
folders.

Migration readiness API:

```bash
curl http://localhost:6180/api/next/migration/readiness
curl http://localhost:6180/api/next/migration/status
```

To start an import job after readiness reports `ready_for_confirmation`:

```bash
curl -X POST http://localhost:6180/api/next/migration/start \
  -H 'Content-Type: application/json' \
  -d '{}'
```

## Optional One-Off CLI Import

The normal path is the API-driven migration assistant above. A one-off CLI
import remains available as a troubleshooting or power-user path:

```bash
docker compose -p discvault_next_deploy --profile tools run --rm import-sqlite
```

The normal `docker-compose.yml` includes this import service behind the `tools`
profile, so starting the stack normally will not accidentally run an import.

The default importer migrates functional collection data and media references.
Users/passkeys/watch history are intentionally not included in this default
deployment command.

## Notes

- `next-api` exposes the Next API on `${DISCVAULT_NEXT_API_PORT:-6180}`.
- `next-worker` processes pending `background_jobs`.
- PostgreSQL data is stored in the Compose named Docker volume
  `discvault_next_deploy_postgres-data` when using the recommended project name.
- This stack is separate from the current production DiscVault container.
