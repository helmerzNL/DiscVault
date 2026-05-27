# DiscVault Next Docker Compose

This compose file runs the PostgreSQL-backed DiscVault Next services from the
published `:dev` image. It does not need a local source checkout on the Docker
host.

## Files

```text
docker-compose.yml
.env
```

Create `.env` from `.env.example` and change `POSTGRES_PASSWORD` before first
start.

## Start

```bash
docker compose pull
docker compose up -d postgres
docker compose --profile tools run --rm migrate
docker compose up -d next-api next-worker
```

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
docker compose --profile tools run --rm import-sqlite
```

The default importer migrates functional collection data and media references.
Users/passkeys/watch history are intentionally not included in this default
deployment command.

## Notes

- `next-api` exposes the Next API on `${DISCVAULT_NEXT_API_PORT:-6180}`.
- `next-worker` processes pending `background_jobs`.
- PostgreSQL data is stored in the named Docker volume `postgres-data`.
- This stack is separate from the current production DiscVault container.
