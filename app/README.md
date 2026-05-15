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

Default endpoint:

- `http://<host>:6090/mcp`

Proxied via web port:

- `http://<host>:6080/mcp`

Example client config:

```json
{
  "mcpServers": {
    "discvault": {
      "transport": "streamable-http",
      "url": "http://localhost:6090/mcp"
    }
  }
}
```

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
