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

Create `.env` from `.env.example` and change `POSTGRES_PASSWORD` before first
start.

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
