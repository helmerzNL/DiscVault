# DiscVault 📀

Manage your physical 4K UHD / Blu-ray / DVD collection with barcode scanning,
a web frontend, and an MCP server for Claude / OpenClaw.

## Structure

```
app/
├── backend/          Flask REST API + SQLite
├── frontend/         Web UI with barcode scanner (served by Nginx)
├── mcp-server/       MCP server for Claude / OpenClaw
├── deploy/
│   ├── all-in-one/   Nginx + Supervisor configs for single-container deployment
│   └── unraid/       Unraid Community Apps XML template
├── scripts/          Helper scripts (e.g. build-with-version.sh)
├── Dockerfile        All-in-one production image
└── docker-compose.yml  Multi-container dev setup
```

## Quick start

### 1. Get API keys (optional but recommended)

| Service | Free tier | URL |
|---------|-----------|-----|
| OMDb    | 1,000 req/day | https://www.omdbapi.com/apikey.aspx |
| TMDb    | Unlimited | https://www.themoviedb.org/settings/api |

Without API keys barcodes are scanned but no movie metadata is returned.
You can fill in details manually or use the "Auto-fill" button in the manual tab.

### 2. Configure

```bash
cp .env.example .env
# Fill in your API keys and other settings in .env
```

### 3. Start

```bash
docker compose up -d --build
```

Or with automatic build version increment:

```bash
./scripts/build-with-version.sh
```

Web UI available at: **http://localhost:6080**

### 4. Stop

```bash
docker compose down
```

Data is persisted in `/data` (mapped to `/mnt/user/appdata/discvault` on Unraid).

---

## Unraid deployment (all-in-one)

The recommended way to run DiscVault on Unraid is via the all-in-one image — a single container that includes
the backend, frontend (Nginx), and MCP server, managed by Supervisor.

### Directory layout on Unraid

```
/mnt/user/appdata/discvault/     ← persistent data volume
├── discvault.db
├── posters/
└── backups/
```

### Deploy via Community Applications

1. Add the XML template from `app/deploy/unraid/discvault.xml` to your Unraid templates repository.
2. Install from Community Applications: search for **DiscVault**.
3. Set at minimum `TZ`, `RP_ID`, and `RP_ORIGIN` to match your Unraid host.

### Manual Docker run

```bash
docker run -d \
  --name discvault \
  -p 6080:80 \
  -p 6090:6090 \
  -e TZ=Europe/Amsterdam \
  -e OMDB_API_KEY=<your-key> \
  -e TMDB_API_KEY=<your-key> \
  -e RP_ID=<unraid-hostname-or-ip> \
  -e RP_ORIGIN=http://<unraid-hostname-or-ip>:6080 \
  -e JWT_SECRET=<random-string> \
  -e MCP_API_KEY=<random-string> \
  -v /mnt/user/appdata/discvault:/data \
  ghcr.io/helmerzNL/DiscVault:latest
```

---

## MCP integration with Claude / OpenClaw

DiscVault exposes an HTTP MCP endpoint:

- MCP: `http://<host>:6090/mcp`
- Health: `http://<host>:6090/health`
- Via web port (Nginx proxy): `http://<host>:6080/mcp`

Example configuration (Streamable HTTP MCP):

```json
{
  "mcpServers": {
    "discvault": {
      "transport": "streamable-http",
      "url": "http://<host>:6090/mcp"
    }
  }
}
```

### Available MCP tools

| Tool | Description |
|------|-------------|
| `search_collection` | Search movies by title, director, or genre |
| `get_collection_stats` | Totals and breakdown by format, recently added |
| `get_movie_details` | Full details for a single movie by ID |
| `list_all_movies` | All movies in the collection |
| `add_movie` | Add a movie via Claude |
| `delete_movie` | Remove a movie via Claude |
| `lookup_barcode` | Look up a barcode without saving |

### Example prompts

- *"Which 4K UHD movies by Christopher Nolan do I have?"*
- *"How many discs do I own in total?"*
- *"Do I have Dune in my collection?"*
- *"What did I add most recently?"*
- *"Show me all action movies in my collection."*

---

## Web UI features

### 📷 Scan
- Open the camera and scan a barcode
- Or type/paste a barcode manually
- Movie info is fetched automatically (OMDb → TMDb → UPCitemdb)
- Select format and optional location, then save

### 📀 Collection
- Grid view of all movies with poster art
- Search by title, director, or genre
- Filter by 4K UHD / Blu-ray / DVD
- Click a movie for details and delete option

### ＋ Manual entry
- Add a movie without a barcode
- "Auto-fill" button fetches movie info by title

---

## API endpoints

```
GET    /api/health              Health check
GET    /api/stats               Collection statistics
GET    /api/movies              All movies  (optional: ?q=query&format=4K+UHD)
GET    /api/movies/:id          Single movie
POST   /api/movies              Add a movie
PUT    /api/movies/:id          Update a movie
DELETE /api/movies/:id          Delete a movie
GET    /api/lookup/:barcode     Look up a barcode
GET    /api/search_title?q=     Search movie info by title
```

---

## Data

SQLite database and posters are stored in `/data` inside the container
(mapped to `/mnt/user/appdata/discvault` on Unraid).

**Manual backup:**
```bash
cp /mnt/user/appdata/discvault/discvault.db ./discvault-backup.db
```

**Export to CSV:**
```bash
curl http://localhost:6080/api/movies | python3 -c "
import json, csv, sys
data = json.load(sys.stdin)
if data:
    w = csv.DictWriter(sys.stdout, fieldnames=data[0].keys())
    w.writeheader()
    w.writerows(data)
" > collection.csv
```
