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

Generate `JWT_SECRET` once with `openssl rand -base64 48`, store it in `.env`,
and keep it unchanged across updates and restarts. The stack fails before serving
requests when this value is missing. Changing it invalidates active sessions and
requires reconnecting integrations whose credentials were encrypted with the old
value.

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

### Preflight check before starting

Two classes of mistake account for nearly every failed start: a required variable
that is not actually in `.env`, and a value that collides with another DiscVault
stack on the same host. Both are cheap to rule out beforehand.

**1. Every required variable is present in `.env`.**

`POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` are mandatory. Compose
reads a variable from the shell environment *before* it reads `.env`, so a
deployment tool that injects one of them will mask its absence from the file.
The stack starts fine until it is deployed some other way — a manual
`docker compose up` over SSH, for example — and then fails on a variable that
looked present all along. Pin them in `.env`:

```bash
for v in POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD JWT_SECRET; do
  grep -q "^${v}=" .env || echo "MISSING in .env: ${v}"
done
```

Across several stacks at once, list the files that lack a given variable:

```bash
grep -L '^POSTGRES_DB=' */.env
```

`POSTGRES_DB` must match the database that already exists in the volume,
otherwise `DATABASE_URL` points at a database that was never created. On an
existing stack, confirm the name first:

```bash
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -l'
```

**2. Nothing collides with another stack on this host.**

`DISCVAULT_NEXT_NETWORK_NAME`, `DISCVAULT_NEXT_API_PORT`, and
`DISCVAULT_NEXT_MCP_PORT` must each be unique per stack. From the directory
holding the stacks:

```bash
grep -H -E '^(DISCVAULT_NEXT_NETWORK_NAME|DISCVAULT_NEXT_API_PORT|DISCVAULT_NEXT_MCP_PORT)=' \
  */.env | sort -t: -k2
```

Every value must appear exactly once. A duplicated port fails loudly with
`port is already allocated`; a duplicated network name fails *quietly* and is
far more damaging — see
[Repeated `password authentication failed`](#repeated-password-authentication-failed-in-the-postgresql-log).

**3. Compose resolves to what you expect.**

```bash
docker compose config | grep -E 'DATABASE_URL|POSTGRES_(USER|DB)'
```

Note that this shows what a *new* container would receive. A container that is
already running keeps the environment it was created with, so after editing
`.env` compare against the live container:

```bash
docker compose exec next-api sh -c 'echo "$DATABASE_URL"'
```

### First start takes longer

On a clean installation PostgreSQL runs `initdb` and `next-api` applies every
migration before Gunicorn binds. Both containers therefore have a 120s
`start_period`: failures inside that window do not mark them unhealthy, so
`docker compose up` no longer aborts with
`dependency failed to start: container ...-postgres-1 is unhealthy` while the
database is still initializing.

The PostgreSQL health check probes TCP on `127.0.0.1:5432` rather than the Unix
socket, because `initdb` briefly runs a temporary socket-only server. Probing
TCP keeps the check correctly negative until the real server accepts
connections, so dependent services never start against the temporary server.

A missing `POSTGRES_DB`, `POSTGRES_USER`, or `POSTGRES_PASSWORD` fails the `up`
immediately with a message naming the variable, rather than producing a
container that never becomes healthy. See the
[preflight check](#preflight-check-before-starting) for verifying they are
really in `.env` and not merely inherited from the shell environment.

All of this applies to a *clean* installation. A stack whose data directory is
already populated does not run `initdb`, so PostgreSQL should become healthy
within seconds; if it does not, see
[PostgreSQL is reported unhealthy on an existing stack](#postgresql-is-reported-unhealthy-on-an-existing-stack).

When this service is published directly behind a reverse proxy, the Next
collection UI is available at `/`, `/app`, and `/api/next/app`.

## Passkeys

Passkeys require a stable relying party configuration. Set these values in
`.env` before enabling authentication:

```text
JWT_SECRET=<long stable random secret>
RP_ID=discvault.example.com
RP_NAME=DiscVault
RP_ORIGINS=https://discvault.example.com
```

`RP_ID` is the browser hostname without scheme or port. `RP_ORIGINS` is a
comma-separated list of allowed origins. Browsers require a secure context for
passkeys, so use HTTPS for non-localhost deployments.

## Optional Legacy password authentication

Set `LEGACY_AUTH_ENABLED=true` only when password + TOTP login is required.
This environment value exposes the capability but does not enable password
login on an existing installation. An Owner or Admin must accept the warning in
**Users & roles** and approve activation with a fresh passkey assertion.

A brand-new instance with no users or credentials may use the setup wizard's
Legacy option for its first Owner. It requires a policy-compliant password,
mandatory TOTP enrollment, and recovery-code acknowledgement before the first
session; no passkey is required for that one atomic bootstrap. Later activation
never receives this exception. Disabling the environment value immediately
hides and rejects all password endpoints.

User-account backups retain Argon2id hashes and Legacy policy, but omit TOTP
secrets, recovery material, and active flows. MFA-enabled users enroll a new
authenticator after restore.

When both `LEGACY_AUTH_ENABLED=true` and the existing `REVIEW_LOGIN_*`
configuration are present, startup idempotently reconciles the review identity
as an ordinary `media_viewer` password user with MFA disabled and the configured
expiry. The old review-login endpoint is retained only as a compatibility alias
to the shared Legacy flow, including mobile PKCE continuation. New deployments
should use the normal Legacy login endpoint and UI.

## Admin dedup execution safety gate

`DISCVAULT_ADMIN_DEDUP_EXECUTE_ENABLED` defaults to `false`. With that safe
default, authorized admins can still request
`GET /api/next/admin/dedup/report`, but the UI hides the destructive merge
action and `POST /api/next/admin/dedup/execute` returns HTTP 403 with
`errorCode: "admin_dedup_execute_disabled"`.

Only set the value to `true` for a release whose matching logic and generated
report have been explicitly reviewed. Enabling it does not bypass the existing
authenticated admin and passkey step-up requirements.

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
`/opt/discvault/backend/next_plugins`; this folder is part of the image and
should be treated as read-only. Installed/uploaded plugins are stored outside the
code bundle in `/data/plugins` by default.

The default installed plugin directory can be changed with
`DISCVAULT_PLUGIN_INSTALL_DIR`. If that is not set, DiscVault uses
`$DISCVAULT_DATA_DIR/plugins`, falling back to `/data/plugins`. Extra read paths
can still be added with `DISCVAULT_PLUGIN_PATHS` using the platform path
separator. Data plugins are discovered before bundled plugins, so a plugin in
`/data/plugins/<plugin_id>` can replace the bundled plugin with the same id
without modifying the container image.

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

Plugin execution uses a generic entrypoint contract. Admins can run a plugin
entrypoint synchronously for quick validation or queue it for the worker:

```bash
curl -X POST http://localhost:6180/api/next/plugins/plex/execute \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint":"discover_library","payload":{"dryRun":true}}'
curl -X POST http://localhost:6180/api/next/plugins/plex/jobs \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint":"sync_library","payload":{"dryRun":true}}'
```

The built-in Plex and Jellyfin modules now use their configured server URLs and
tokens/API keys for connector calls. Example `.example` URLs are accepted without
network access for smoke tests; real URLs perform live discovery/sync calls.
Queued `sync_library` jobs persist normalized digital items in PostgreSQL,
match them to imported movies by TMDb, IMDb or title/year, and expose the result
through the collection APIs.

Digital source inspection:

```bash
curl http://localhost:6180/api/next/digital-sources
curl http://localhost:6180/api/next/digital-items?limit=200
```

The collection movie list includes `digital_count` and `digital_sources` when a
movie is matched by a digital source. Movie detail JSON includes `digitalItems`.
The current admin panel reads plugin manifest schemas, renders settings/secrets
fields, saves configuration, runs health checks, and can discover or queue sync
jobs from the Plugins tab.

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

For repeated migration testing, set `DISCVAULT_NEXT_ENABLE_TEST_RESET=true` in
`.env` and recreate `next-api`. The migration wizard then shows a guarded
test-reset button. It clears imported PostgreSQL collection, users, groups,
passkeys, non-secret app settings, jobs and migration history, but keeps the
schema, roles, permissions, plugin registry/configuration and filesystem media
under `/data`.

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

## Troubleshooting

Before working through the cases below, run the
[preflight check](#preflight-check-before-starting) — most failed starts are a
missing `.env` variable or a value that collides with another stack.

### PostgreSQL is reported unhealthy on an existing stack

Symptoms:

- `docker compose up` (or a management UI that runs it) takes minutes and then
  aborts with
  `dependency failed to start: container <stack>-postgres-1 is unhealthy`.
- The stack already has a populated data directory, so `initdb` does not run.

This case is **not** the one covered by
[First start takes longer](#first-start-takes-longer). The 120s `start_period`
on `postgres` exists for clean installs, where `initdb` is genuinely slow on NAS
storage. On an existing data directory PostgreSQL should accept TCP connections
within seconds, so an unhealthy container means something else is wrong — and
the long `start_period` merely delays the failure by up to 220s
(`start_period` 120s + `retries` 10 × `interval` 10s) before Compose gives up.

Note also that the health check probes TCP rather than the Unix socket. The
socket probe it replaced reported success during startup phases in which the
server was not yet accepting real connections, so a slow start used to be
invisible. A start that now looks slow may always have been slow.

Collect the evidence in one pass:

```bash
app/scripts/next_stack_doctor.sh <compose-project-name>
```

The script is read-only — it inspects containers, reads logs, and runs
`pg_isready`; it changes nothing. The project name defaults to
`discvault_next_deploy`. Then match the findings:

| Observation | Cause | Fix |
| --- | --- | --- |
| Probe exits `3`, or the health command shows an empty `-U ""` | A management UI re-interpolated the compose file and destroyed the `$$` escape, leaving an invalid `pg_isready` invocation that can never succeed | Drop the credentials from the probe — `pg_isready -h 127.0.0.1 -p 5432` is still a correct readiness check and has nothing left to mis-interpolate |
| Probe exits `1`; log shows `automatic recovery in progress` | PostgreSQL is replaying WAL after an unclean shutdown | Give the server time to stop cleanly next time: set `stop_grace_period: 60s` on the `postgres` service, so Compose does not `SIGKILL` it after the default 10s |
| Probe exits `2` and `RestartCount` climbs | PostgreSQL is not staying up at all | Read the reason in the log: bind-mount permissions (the container runs as uid 70), or a data directory created by a different major version — `postgres:17-alpine` is a tag, not a digest |
| The probe itself takes longer than 5s | `timeout: 5s` is too tight for this storage | Raise `timeout`, and check whether the data directory sits on a network share |
| `next-api` is the unhealthy container; `/api/next/health` is slow or returns 503 | Migrations are not `ready`, or the endpoint's per-probe artwork-trash purge exceeds the timeout | Wait out the migration; if the purge is the cause, the endpoint should not be doing write work on a 10s liveness probe |
| `getent hosts postgres` returns more than one address | Two stacks share a Docker network | Give each stack a unique `DISCVAULT_NEXT_NETWORK_NAME`; see [below](#repeated-password-authentication-failed-in-the-postgresql-log) |
| Everything is healthy, only slow | The deploy is gated on health checks | Nothing is broken: `docker compose up -d --wait=false` returns immediately and the stack converges on its own |

### Repeated `password authentication failed` in the PostgreSQL log

Symptoms:

- `postgres` logs `FATAL: password authentication failed for user "<POSTGRES_USER>"`
  every few seconds, matching the `host all all all scram-sha-256` line of
  `pg_hba.conf`.
- `next-api` never becomes healthy, because its startup migration cannot connect.
- The credentials are nevertheless correct. Both of these succeed:

```bash
PW="$(sed -n 's/^POSTGRES_PASSWORD=//p' .env | head -1)"
docker compose exec -T -e PGPASSWORD="$PW" postgres \
  sh -c 'psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select 1"'
docker compose exec -T postgres sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select 1"'
```

The first command proves the password in `.env` is accepted by the cluster; the
second proves the container's own credentials work. `POSTGRES_USER` and
`POSTGRES_DB` are expanded *inside* the container in both cases — they are not
set in the host shell.

When the cluster accepts its own credentials but keeps rejecting connections,
another client is knocking. On a host running more than one DiscVault stack the
usual cause is a shared Docker network. `DISCVAULT_NEXT_NETWORK_NAME` becomes
the literal network name and Compose adds no project prefix, so two stacks that
keep the same value join the same network. Every stack has a service named
`postgres`, so that alias resolves to several containers and Docker balances
connections across them. Roughly half of each stack's connections then reach the
other stack's database and are rejected, because each stack has its own
password.

Confirm it. The first command must return exactly one address, and the second
must list containers from one stack only:

```bash
docker exec <stack>-next-api-1 getent hosts postgres
docker network inspect <network-name> -f '{{range .Containers}}{{println .Name}}{{end}}'
```

The default log format omits the client address. Add it temporarily to identify
the caller:

```bash
docker exec <stack>-postgres-1 psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "ALTER SYSTEM SET log_line_prefix = '%m [%p] %q%u@%d from %h '" \
  -c "SELECT pg_reload_conf()"
docker logs --tail 20 -f <stack>-postgres-1
```

Map the logged address back to a container, then restore the log format:

```bash
docker ps -q | xargs -r docker inspect \
  -f '{{.Name}}{{range .NetworkSettings.Networks}} {{.IPAddress}}{{end}}'
docker exec <stack>-postgres-1 psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "ALTER SYSTEM RESET log_line_prefix" -c "SELECT pg_reload_conf()"
```

The fix is to give every stack its own network. Set a unique
`DISCVAULT_NEXT_NETWORK_NAME` in each `.env`, then recreate the affected stacks
with `docker compose up -d --force-recreate`. Note that the superuser is
`POSTGRES_USER`, not `postgres`; connections over the container's Unix socket
are trusted, so no password is needed for these admin commands.

Two other causes are worth ruling out when the stacks are already isolated:

- `POSTGRES_PASSWORD` was changed after the data directory was initialized. The
  image only applies that variable during the initial `initdb`, so the cluster
  keeps the old password while `DATABASE_URL` already carries the new one. Fix
  it in place with
  `ALTER USER "<POSTGRES_USER>" WITH PASSWORD '<new password>';`.
- The password contains a character that is unsafe in a URI. `DATABASE_URL` is
  assembled by plain string interpolation, so an unencoded `/` truncates the
  authority and `%xx` is percent-decoded by libpq. Verify what the container
  actually parses:

```bash
docker compose run --rm --no-deps --entrypoint python next-api -c \
  "import os;from urllib.parse import urlsplit;u=urlsplit(os.environ['DATABASE_URL']);print(u.username,u.hostname,u.port,u.path,len(u.password or ''))"
```

A password length that differs from the value in `.env`, or an unexpected host,
means the URI is being mangled. Use an alphanumeric password or percent-encode
it.

## Notes

- `next-api` exposes the Next API on `${DISCVAULT_NEXT_API_PORT:-6180}`.
- `next-mcp` publishes the MCP server on `${DISCVAULT_NEXT_MCP_PORT:-6090}`. Change
  `DISCVAULT_NEXT_MCP_PORT` in `.env` if `6090` is already used by another stack to
  avoid a `port is already allocated` error.
- The Docker network name is `${DISCVAULT_NEXT_NETWORK_NAME:-discvault-next}`. Change
  `DISCVAULT_NEXT_NETWORK_NAME` in `.env` to run multiple stacks side by side without
  network name collisions. This is the actual Docker network name; no Compose project
  prefix is added. Two stacks that keep the same value also share the `postgres`
  service alias, which makes each stack reach the other stack's database; see
  Troubleshooting.
- `next-worker` processes pending `background_jobs`.
- PostgreSQL data is stored on a host bind mount at `DISCVAULT_NEXT_POSTGRES_DATA`
  (default `./postgres-data`, relative to this compose file). Point it at an
  absolute path on the same storage as your other data/backups so the database is
  backed up alongside the media.
  If you previously deployed with the named `postgres-data` volume, migrate its
  contents once (e.g. `pg_dump`/restore or copying the volume contents) before
  switching, otherwise PostgreSQL will initialize an empty database.
  Docker creates the bind-mount directory if it does not exist. The postgres
  container runs as uid 70; an existing non-empty directory owned by another user
  can fail to start.
- This stack is separate from the current production DiscVault container.
