# DiscVault App (Developer README)

This README is focused on developing and operating DiscVault from source.

For product overview and screenshots, use the repository root README.

## Project layout

```text
app/
├── backend/            Flask API (SQLite, auth, import/export, logs)
├── frontend/           Static web app (served by Nginx)
├── mcp-server/         MCP HTTP server
├── deploy/
│   ├── all-in-one/     Nginx + Supervisor config for single-container image
│   └── unraid/         Unraid CA template files
├── scripts/            Build helper scripts
├── Dockerfile          All-in-one production image
└── docker-compose.yml  Local multi-service development setup
```

## Local development

### Prerequisites

- Docker + Docker Compose
- Optional metadata API keys:
  - OMDb: https://www.omdbapi.com/apikey.aspx
  - TMDb: https://www.themoviedb.org/settings/api

### Configure environment

```bash
cp .env.example .env
```

Required for realistic local auth behavior:

```env
RP_ID=localhost
RP_ORIGIN=http://localhost:6080
TZ=Europe/Amsterdam
```

Optional:

```env
OMDB_API_KEY=...
TMDB_API_KEY=...
JWT_SECRET=...
MCP_API_KEY=...
```

### Start stack

```bash
docker compose up -d --build
```

Services:

- Web UI: http://localhost:6080
- Backend health: http://localhost:6080/api/health
- MCP health: http://localhost:6090/health

### Stop stack

```bash
docker compose down
```

## Runtime architecture

- Frontend is served by Nginx
- API is Flask + Gunicorn
- Data is SQLite at `/data/discvault.db`
- MCP server exposes streamable HTTP endpoint and calls the backend API

## All-in-one production image

Build from `app/`:

```bash
docker build -t ghcr.io/helmerzNL/DiscVault:dev --build-arg BUILD_VERSION=dev .
```

Run:

```bash
docker run -d \
  --name discvault \
  -p 6080:80 \
  -p 6090:6090 \
  -e TZ=Europe/Amsterdam \
  -e RP_ID=localhost \
  -e RP_ORIGIN=http://localhost:6080 \
  -v /mnt/user/appdata/discvault:/data \
  ghcr.io/helmerzNL/DiscVault:dev
```

## Release flow

Image publishing is handled by GitHub Actions in `.github/workflows/docker-publish.yml`.

1. Merge changes to `main`
2. Create and push a semver tag

```bash
git tag v1.0.1
git push origin v1.0.1
```

This publishes multi-arch images to GHCR.

## GitHub Pages website

Website source is under `website/` and is deployed by `.github/workflows/pages.yml`.

For domain setup, configure DNS and custom domain in repository Pages settings.

## MCP endpoint usage

Default endpoint: `http://<host>:6090/mcp`  
Proxied via web port: `http://<host>:6080/mcp`

### MCP authentication

Each user can generate a **personal API key** in their profile settings:
**Settings → Profiel → API-sleutels → Sleutel aanmaken**

Use it as a Bearer token in your MCP client config:

```json
{
  "mcpServers": {
    "discvault": {
      "transport": "streamable-http",
      "url": "http://your-server:6080/mcp",
      "headers": {
        "Authorization": "Bearer your-personal-api-key"
      }
    }
  }
}
```

The MCP server automatically scopes all responses to your account.
You will only ever see your own watchlist, watch history, collection and groups.

**Available MCP tools:**

| Tool | Description |
|------|-------------|
| `search_collection` | Search discs by title, director or genre |
| `list_all_movies` | List your entire collection |
| `get_movie_details` | Full details for a movie by ID |
| `get_collection_stats` | Count + format breakdown |
| `add_movie` | Add a disc to your collection |
| `delete_movie` | Remove a disc |
| `lookup_barcode` | Look up a barcode without saving |
| `get_watchlist` | Your personal watchlist |
| `get_watch_history` | Recently watched titles |
| `get_groups` | Groups you are a member of |

## API surface (summary)

```text
GET    /api/health
GET    /api/stats
GET    /api/movies
GET    /api/movies/:id
POST   /api/movies
PUT    /api/movies/:id
DELETE /api/movies/:id
GET    /api/lookup/:barcode
GET    /api/search_title?q=
```

## Data and backups

Persistent files are stored in `/data` in the container.

Manual backup example:

```bash
cp /mnt/user/appdata/discvault/discvault.db ./discvault-backup.db
```

## DiscVault Next preparation

DiscVault Next planning starts with a read-only audit of the current SQLite
database and data volume. The audit script does not import the Flask app and
does not run migrations.

Local example:

```bash
python scripts/db_audit.py --db .local/discvault.db
```

Production-copy example:

```bash
python scripts/db_audit.py \
  --db /path/to/copied/discvault.db \
  --data-dir /path/to/copied/data \
  --output-dir /path/to/audit-output
```

The generated JSON contains schema objects, row counts, table classifications,
foreign keys, indexes, media file inventory, integrity checks and SQLite-specific
SQL findings for PostgreSQL migration planning.

Create a canonical export package for PostgreSQL import planning:

```bash
python scripts/sqlite_export.py --db .local/discvault.db
```

By default this export excludes security, device-specific and log/control data
such as passkeys, invite codes, push subscriptions and logs. Include those only
for an explicit admin/security migration rehearsal:

```bash
python scripts/sqlite_export.py \
  --db /path/to/copied/discvault.db \
  --data-dir /path/to/copied/data \
  --output-dir /path/to/export-output \
  --include-security \
  --include-device \
  --include-logs
```

### DiscVault Next PostgreSQL skeleton

`docker-compose.next.yml` starts a PostgreSQL service and provides tool profiles
for the first DiscVault Next migrations on a Docker host. It does not replace the
current SQLite runtime yet.

For a Docker host that should use the published `:dev` image instead of building
from source, use `deploy/next/docker-compose.yml`.

Local Docker is not required for every developer step. The GitHub Actions
workflow `DiscVault Next PostgreSQL Smoke` validates the migration runner against
a temporary PostgreSQL service and runs a minimal importer smoke test.

Start PostgreSQL:

```bash
docker compose -f docker-compose.next.yml up -d postgres
```

Apply pending DiscVault Next migrations:

```bash
docker compose -f docker-compose.next.yml --profile tools run --rm migrate
```

Show migration status:

```bash
docker compose -f docker-compose.next.yml --profile tools run --rm migration-status
```

Start the minimal PostgreSQL-backed Next API on a Docker host:

```bash
docker compose -f docker-compose.next.yml up -d next-api
```

Initial endpoints:

```text
GET http://localhost:6180/api/next/health
GET http://localhost:6180/api/next/stats
GET http://localhost:6180/api/next/settings
GET http://localhost:6180/api/next/digital-sources
GET http://localhost:6180/api/next/digital-items
GET http://localhost:6180/api/next/plugins/registry
GET http://localhost:6180/api/next/metadata/plugins
GET http://localhost:6180/api/next/movies
GET http://localhost:6180/api/next/containers
GET http://localhost:6180/api/next/sync/state
GET http://localhost:6180/api/next/sync/bootstrap
GET http://localhost:6180/api/next/sync/delta?since=0
POST http://localhost:6180/api/next/sync/mutations
GET http://localhost:6180/api/next/jobs
POST http://localhost:6180/api/next/jobs
GET http://localhost:6180/api/next/jobs/<jobId>
```

The first migration set creates the PostgreSQL foundation for users/passkeys,
RBAC, movies, people, containers, media assets, metadata plugins, plugin
runtime state, digital media source sync, events, offline sync, push
notifications, entitlements and migration import state.

The initial sync API is shared by future PWA, iOS and Android clients. Clients
use bootstrap to fill a local cache, delta to catch up by revision, and
mutations to send idempotent offline changes back to the server.

The worker foundation uses `background_jobs` and row locks. API requests create
jobs and return quickly; `next-worker` claims pending jobs with `FOR UPDATE SKIP
LOCKED`. Implemented job types include `sync.noop`, SQLite import, and generic
plugin execution. Digital media source sync jobs persist Plex/Jellyfin items and
match them to imported movies by TMDb, IMDb or title/year.

Run a read-only dry-run against a copied legacy data directory:

```bash
python backend/next_import.py \
  --db /path/to/copied/data/discvault.db \
  --data-dir /path/to/copied/data \
  --dry-run
```

Import the copied SQLite data into the Next PostgreSQL schema on a Docker host:

```bash
DISCVAULT_SQLITE_IMPORT_DATA=/path/to/copied/data \
docker compose -f docker-compose.next.yml --profile tools run --rm import-sqlite
```

If Docker is not available locally, run this on the beta host or another machine
that has access to the copied data directory.

The default import migrates functional collection data, people, credits,
containers and media references. Users/passkeys, watch history and other
personal/device data require explicit importer flags and should only be used for
an intentional security migration rehearsal.
