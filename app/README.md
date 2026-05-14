# DiscVault 📀

Beheer je fysieke 4K UHD / Blu-ray / DVD collectie met barcode scanning,
een web frontend en een MCP server voor Claude (OpenClaw).

## Structuur

```
disc-vault/
├── backend/          Flask REST API + SQLite
├── frontend/         Web UI met barcode scanner (Nginx)
├── mcp-server/       MCP server voor Claude/OpenClaw
├── docker-compose.yml
└── .env.example
```

## Snelstart

### 1. API keys ophalen (optioneel maar aanbevolen)

| Service | Gratis tier | URL |
|---------|------------|-----|
| OMDb    | 1000 req/dag | https://www.omdbapi.com/apikey.aspx |
| TMDb    | Onbeperkt  | https://www.themoviedb.org/settings/api |

Zonder API keys worden barcodes opgezocht maar krijg je geen filmdetails.
Je kunt films dan handmatig invullen of via de "Auto-fill" knop in het handmatig-tab.

### 2. Configureren

```bash
cp .env.example .env
# Vul je API keys in .env
```

### 3. Opstarten

```bash
docker compose up -d --build
```

Optioneel met automatische versieverhoging (buildnummer):

```bash
./scripts/build-with-version.sh
```

Web UI is beschikbaar op: **http://localhost:6080**

### 4. Stoppen

```bash
docker compose down
```

Data blijft bewaard in `${APPDATA_PATH}/data` (standaard: `/mnt/user/appdata/discvault/data`).

---

## Unraid Deploy (aanbevolen voor jouw setup)

Structuur op Unraid:

```
/mnt/user/appdata/discvault/
├── app/           # Code (docker-compose.yml, Dockerfiles, etc)
│   ├── docker-compose.yml
│   ├── backend/
│   ├── frontend/
│   ├── mcp-server/
│   ├── .env
│   └── README.md
└── data/          # Persistente data (mount target in containers)
    ├── discvault.db
    ├── posters/
    └── backups/
```

### 1. Clone/plaats de code

Via Unraid Terminal of SSH:

```bash
cd /mnt/user/appdata/discvault
git clone <repo-url> app
# of: mkdir app && upload DiscVault files naar /mnt/user/appdata/discvault/app
```

### 2. Configureer .env

```bash
cd /mnt/user/appdata/discvault/app
cp .env.example .env
nano .env
```

Vul minimaal in:

```env
FRONTEND_PORT=6080
MCP_PORT=6090
RP_ID=<IP-of-hostname-van-unraid>
RP_ORIGIN=http://<IP-of-hostname-van-unraid>:6080
OMDB_API_KEY=<jouw-key>
TMDB_API_KEY=<jouw-key>
JWT_SECRET=<random-string>
TZ=Europe/Amsterdam
```

### 3. Start de stack

```bash
cd /mnt/user/appdata/discvault/app
docker compose up -d --build
```

Of met automatische versieverhoging:

```bash
cd /mnt/user/appdata/discvault/app
sh ./scripts/build-with-version.sh
```

### 4. Controleer

```bash
docker compose ps
curl http://localhost:6080/api/health
curl http://localhost:6090/health
```

Data wordt automatisch opgeslagen in `/mnt/user/appdata/discvault/data`.

---

## MCP Koppeling met OpenClaw

Voor Unraid is de simpelste optie: OpenClaw verbinden met de HTTP MCP endpoint.

DiscVault MCP endpoint:

- `http://<unraid-ip>:6090/mcp`

Health endpoint:

- `http://<unraid-ip>:6090/health`

Voorbeeldconfiguratie (Streamable HTTP MCP):

```json
{
  "mcpServers": {
    "discvault": {
      "transport": "streamable-http",
      "url": "http://<unraid-ip>:6090/mcp"
    }
  }
}
```

Als jouw OpenClaw build nog geen remote/HTTP MCP ondersteunt, gebruik dan stdio via Docker zoals hieronder:

```json
{
  "mcpServers": {
    "discvault": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "--network", "disc-vault_discvault-net",
        "-e", "DISCVAULT_API=http://backend:5000",
        "disc-vault-mcp"
      ]
    }
  }
}
```

### Beschikbare MCP tools

| Tool | Wat doet hij |
|------|-------------|
| `search_collection` | Zoek films op titel, regisseur of genre |
| `get_collection_stats` | Totalen, verdeling per formaat, recent toegevoegd |
| `get_movie_details` | Volledige details van één film op ID |
| `list_all_movies` | Alle films in de collectie |
| `add_movie` | Film toevoegen via Claude |
| `delete_movie` | Film verwijderen via Claude |
| `lookup_barcode` | Barcode opzoeken (zonder opslaan) |

### Voorbeeldvragen aan Claude

- *"Welke 4K UHD films van Christopher Nolan heb ik?"*
- *"Hoeveel schijfjes heb ik in totaal?"*
- *"Heb ik Dune al in mijn collectie?"*
- *"Welke films heb ik recent toegevoegd?"*
- *"Zoek alle action films in mijn verzameling"*

---

## Web UI functies

### 📷 Scannen
- Open camera en scan een barcode
- Of typ/plak een barcode handmatig
- Filminfo wordt automatisch opgezocht (OMDb → TMDb → UPCitemdb)
- Kies formaat en optionele locatie, dan opslaan

### 📀 Collectie
- Grid-overzicht van alle films met poster
- Zoeken op titel, regisseur, genre
- Filteren op 4K UHD / Blu-ray / DVD
- Klik op film voor details + verwijderen

### ＋ Handmatig
- Film toevoegen zonder barcode
- "Auto-fill" knop zoekt filminfo op basis van titel

---

## API Endpoints (directe toegang)

```
GET  /api/health              Status check
GET  /api/stats               Statistieken
GET  /api/movies              Alle films (optioneel ?q=zoekterm&format=4K+UHD)
GET  /api/movies/:id          Één film
POST /api/movies              Film toevoegen
PUT  /api/movies/:id          Film bijwerken
DELETE /api/movies/:id        Film verwijderen
GET  /api/lookup/:barcode     Barcode opzoeken
GET  /api/search_title?q=     Filminfo zoeken op titel
```

---

## Data

SQLite database en posters opgeslagen in `/mnt/user/appdata/discvault/data`.

**Backup maken:**
```bash
cp ${APPDATA_PATH}/data/discvault.db ./discvault-backup.db
```

**CSV exporteren:**
```bash
# Via de API — geeft JSON terug, converteren naar CSV:
curl http://localhost:6080/api/movies | python3 -c "
import json, csv, sys
data = json.load(sys.stdin)
if data:
    w = csv.DictWriter(sys.stdout, fieldnames=data[0].keys())
    w.writeheader()
    w.writerows(data)
" > collectie.csv
```
