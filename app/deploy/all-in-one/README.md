# DiscVault All-in-One Container

This build runs frontend (nginx), backend API (gunicorn), and MCP service in a single container.

## Build

```bash
docker build -t discvault:all-in-one --build-arg BUILD_VERSION=dev .
```

## Run

```bash
docker run -d \
  --name discvault \
  -p 6080:80 \
  -p 6090:6090 \
  -e TZ=Europe/Amsterdam \
  -e OMDB_API_KEY=... \
  -e TMDB_API_KEY=... \
  -e RP_ID=localhost \
  -e RP_ORIGIN=http://localhost:6080 \
  -e JWT_SECRET=... \
  -e MCP_API_KEY=... \
  -v /mnt/user/appdata/discvault:/data \
  discvault:all-in-one
```

## Endpoints

- Web UI: `http://<host>:6080`
- API health: `http://<host>:6080/api/health`
- MCP (via web port): `http://<host>:6080/mcp`
- MCP health (via web port): `http://<host>:6080/mcp-health`
- MCP direct (optional): `http://<host>:6090/mcp`
