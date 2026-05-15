FROM python:3.12-slim

ARG BUILD_VERSION=dev

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/data/discvault.db \
    POSTER_DIR=/data/posters \
    PROFILE_DIR=/data/profiles \
    BACKUP_DIR=/data/backups \
    RP_NAME=DiscVault \
    RP_ID=localhost \
    RP_ORIGINS=http://localhost:6080 \
    TZ=Europe/Amsterdam

WORKDIR /opt/discvault

RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx supervisor curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default

# Python dependencies (backend + mcp)
COPY backend/requirements.txt /tmp/requirements-backend.txt
COPY mcp-server/requirements.txt /tmp/requirements-mcp.txt
RUN pip install -r /tmp/requirements-backend.txt \
    && pip install -r /tmp/requirements-mcp.txt

# App code
COPY backend /opt/discvault/backend
COPY mcp-server /opt/discvault/mcp-server
COPY frontend /opt/discvault/frontend

# Frontend static assets into nginx root
RUN mkdir -p /usr/share/nginx/html \
    && cp -r /opt/discvault/frontend/* /usr/share/nginx/html/ \
    && printf '{"version":"%s"}\n' "$BUILD_VERSION" > /usr/share/nginx/html/version.json

# Runtime configs
COPY deploy/all-in-one/nginx.conf /etc/nginx/conf.d/default.conf
COPY deploy/all-in-one/supervisord.conf /etc/supervisor/conf.d/discvault.conf

# Data dirs
RUN mkdir -p /data/posters /data/profiles /data/backups /data/avatars

VOLUME ["/data"]

LABEL net.unraid.docker.icon="https://raw.githubusercontent.com/helmerzNL/DiscVault/main/app/frontend/favicon-192.png" \
      net.unraid.docker.webui="http://[IP]:[PORT:80]" \
      net.unraid.docker.managed="dockerman"

EXPOSE 80 6090

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=5 \
  CMD curl -fsS http://127.0.0.1/api/health >/dev/null || exit 1

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/discvault.conf"]
