# DiscVault App (Developer README)

This README is focused on developing and operating DiscVault from source.

For product overview and screenshots, use the repository root README.

## Project layout

```text
app/
├── backend/            DiscVault Next API (next_app.py, Flask + Gunicorn, PostgreSQL)
├── frontend/           Shared static assets (i18n/next, flags, icons, PWA, service worker)
├── mcp-server/         MCP HTTP server
├── deploy/
│   ├── v26/            Supervisor config for the v26 (Next) single-container image
│   ├── next/           Compose + docs for running the published image
│   └── unraid/         Unraid CA template files
├── scripts/            Build helper scripts
├── Dockerfile.v26      Production image (DiscVault Next stack)
└── docker-compose.next.yml  Local multi-service development setup (PostgreSQL)
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

Required:

```env
RP_ID=localhost
RP_ORIGIN=http://localhost:6080
TZ=Europe/Amsterdam
JWT_SECRET=<stable value generated with: openssl rand -base64 48>
```

Optional:

```env
OMDB_API_KEY=...
TMDB_API_KEY=...
MCP_API_KEY=...
```

### Start stack

```bash
docker compose -f docker-compose.next.yml up -d --build
```

### Run backend tests

The test package supplies an explicit test-only secret, so production configuration
is not required:

```bash
cd backend
python -m unittest discover -s tests -t . -p "test_*.py"
```

Services:

- Web UI / API: http://localhost:6180
- Backend health: http://localhost:6180/api/next/health

### Stop stack

```bash
docker compose -f docker-compose.next.yml down
```

## Runtime architecture

- API and embedded web UI are served by `next_app.py` (Flask + Gunicorn)
- Data is stored in PostgreSQL (`DATABASE_URL`)
- A `next_worker.py` process handles background jobs
- MCP server exposes a streamable HTTP endpoint and calls the backend API

## Production image (DiscVault Next stack)

Build from `app/` using the v26 Dockerfile:

```bash
docker build -f Dockerfile.v26 -t ghcr.io/helmerznl/discvault:dev --build-arg BUILD_VERSION=dev .
```

The image runs the API on `5000` and the MCP server on `6090` via Supervisor
(`deploy/v26/supervisord.conf`) and expects a reachable PostgreSQL `DATABASE_URL`.
For running the published image with a bundled PostgreSQL service, see
`deploy/next/`.

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

### Container Member Ordering

DiscVault Next stores container member position on the membership row, not on
the movie or container row itself.

For `box_set` and `vault` containers, direct movie members are stored in
`container_movies`:

```sql
container_movies (
  container_id uuid,
  movie_id uuid,
  sort_order integer not null default 0,
  created_at timestamptz
)
```

`container_id` + `movie_id` is the primary key. `sort_order` is the persisted
member position for that movie inside that specific box-set or vault. The same
movie can therefore have different positions in different containers.

For `collection` containers, mixed members are stored in `collection_items`:

```sql
collection_items (
  collection_id uuid,
  item_type text, -- movie, vault, box_set or collection
  item_id uuid,
  sort_order integer not null default 0,
  created_at timestamptz
)
```

`collection_id` + `item_type` + `item_id` is the primary key. `sort_order` is
the persisted position of that item inside the collection.

When members are added through the bulk APIs, DiscVault appends them after the
current maximum `sort_order` for that container. Reordering writes dense
1-based positions back to the relevant membership table:

```text
POST  /api/next/bulk/containers/<containerId>/movies
PATCH /api/next/containers/<containerId>/movies/order

POST  /api/next/bulk/collections/<collectionId>/items
PATCH /api/next/collections/<collectionId>/items/order
```

The reorder endpoints require the request to include every currently linked
member. This prevents partial order updates from accidentally dropping or
duplicating positions.

Read paths order by the stored position first and then use a stable title/year
fallback for ties or legacy rows with `sort_order=0`:

```sql
ORDER BY sort_order, lower(title), year NULLS LAST
```

Imports preserve source ordering where available. Legacy SQLite
`box_set_movies.sort_order`, `vault_movies.sort_order` and
`collection_items.sort_order` are copied into the Next membership tables.
Metadata-driven imports and receiver payloads use `sortOrder`/`sort_order`
from the provider payload, falling back to the member index when no explicit
order is supplied.

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
