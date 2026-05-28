# DiscVault Next Docker Compose

This compose file runs the PostgreSQL-backed DiscVault Next services from the
published `:dev` image. It does not need a local source checkout on the Docker
host.

It is written for Unraid-style Compose Manager usage: importing the compose file
and `.env` should be enough to start the normal stack in one action.

## Files

```text
docker-compose.yml
.env
docker-compose.import.yml  # optional, only for importing an old data copy
```

Create `.env` from `.env.example` and change `POSTGRES_PASSWORD` before first
start.

## Start

Start the compose project from Unraid's Docker Compose Manager UI.

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

## Import A Copied DiscVault Data Directory

Set `DISCVAULT_SQLITE_IMPORT_DATA` in `.env` to a directory containing:

```text
discvault.db
posters/
profiles/
avatars/
```

Then run:

```bash
docker compose -f docker-compose.import.yml up import-sqlite
```

On Unraid Docker Compose Manager, import `docker-compose.import.yml` as a
separate one-off project only after the copied data directory is in place. The
normal `docker-compose.yml` intentionally does not include the import service, so
starting the stack from the UI will not accidentally run an import.

The default importer migrates functional collection data and media references.
Users/passkeys/watch history are intentionally not included in this default
deployment command.

## Notes

- `next-api` exposes the Next API on `${DISCVAULT_NEXT_API_PORT:-6180}`.
- `next-worker` processes pending `background_jobs`.
- PostgreSQL data is stored in the named Docker volume `postgres-data`.
- This stack is separate from the current production DiscVault container.
