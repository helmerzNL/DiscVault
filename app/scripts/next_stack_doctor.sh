#!/usr/bin/env sh
# Read-only diagnostics for a DiscVault Next stack that reports
# "dependency failed to start: container <stack>-postgres-1 is unhealthy".
#
# Every command here inspects; none of them change the stack. Run it while the
# stack is up (or mid-failure) and hand the whole report to whoever is
# debugging. See "PostgreSQL is reported unhealthy on an existing stack" in
# app/deploy/next/README.md for how to read the output.
#
# Usage: app/scripts/next_stack_doctor.sh [compose-project-name]
set -eu

PROJECT="${1:-discvault_next_deploy}"

section() {
  printf '\n========================================================================\n'
  printf '%s\n' "$1"
  printf -- '------------------------------------------------------------------------\n'
}

note() {
  printf '  %s\n' "$1"
}

# Resolve a container by its Compose labels rather than by guessing the
# "<project>-<service>-1" name, which is wrong for scaled or renamed services.
container_for() {
  docker ps -a \
    --filter "label=com.docker.compose.project=${PROJECT}" \
    --filter "label=com.docker.compose.service=$1" \
    --format '{{.Names}}' 2>/dev/null | head -1
}

# docker inspect --format '{{json ...}}' emits one dense line; make it readable
# when python3 is around, otherwise pass it through untouched.
pretty_json() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -m json.tool 2>/dev/null || cat
  else
    cat
  fi
}

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found on PATH; run this on the Docker host." >&2
  exit 1
fi

PG="$(container_for postgres)"
API="$(container_for next-api)"
WORKER="$(container_for next-worker)"
MCP="$(container_for next-mcp)"

printf 'DiscVault Next stack doctor\n'
printf 'Compose project : %s\n' "$PROJECT"
printf 'postgres        : %s\n' "${PG:-<not found>}"
printf 'next-api        : %s\n' "${API:-<not found>}"
printf 'next-worker     : %s\n' "${WORKER:-<not found>}"
printf 'next-mcp        : %s\n' "${MCP:-<not found>}"

if [ -z "$PG" ]; then
  printf '\nNo postgres container found for project "%s".\n' "$PROJECT"
  printf 'List the projects on this host with:\n'
  printf "  docker ps -a --format '{{.Label \"com.docker.compose.project\"}}' | sort -u\n"
  exit 1
fi

section "1. Container status — which service is actually unhealthy?"
note "Compose names the *dependency* in its error. If next-api is the unhealthy"
note "one, next-worker/next-mcp report that, not postgres."
docker ps -a \
  --filter "label=com.docker.compose.project=${PROJECT}" \
  --format 'table {{.Names}}\t{{.Status}}' 2>/dev/null || true

section "2. PostgreSQL health probe output — the decisive evidence"
note "pg_isready exit codes: 0 accepting | 1 rejecting (still starting up)"
note "                       2 no response | 3 no attempt made (bad parameters)"
HEALTH="$(docker inspect --format '{{json .State.Health}}' "$PG" 2>/dev/null || echo null)"
if [ "$HEALTH" = "null" ] || [ -z "$HEALTH" ]; then
  note "No healthcheck recorded on this container."
else
  printf '%s' "$HEALTH" | pretty_json
fi

section "3. PostgreSQL log"
note "Look for: 'automatic recovery in progress' (unclean shutdown, slow start),"
note "'database files are incompatible with server' (datadir from another major"
note "version), 'invalid permissions'/'wrong ownership' (bind mount), or repeated"
note "'password authentication failed' (two stacks sharing one network)."
docker logs --tail 200 "$PG" 2>&1 || true

section "4. Is postgres restart-looping?"
note "A rising RestartCount means postgres never stays up; the healthcheck is"
note "then a symptom rather than the cause."
docker inspect \
  --format 'RestartCount={{.RestartCount}} Status={{.State.Status}} ExitCode={{.State.ExitCode}} StartedAt={{.State.StartedAt}} FinishedAt={{.State.FinishedAt}}' \
  "$PG" 2>/dev/null || true

section "5. Did the healthcheck reach the container intact?"
note "Expect \"pg_isready -h 127.0.0.1 -p 5432 ...\" and a StartPeriod of"
note "120000000000 (120s). A command without -h, or a MISSING StartPeriod,"
note "means the running container was created from an OUTDATED compose file —"
note "the stack needs redeploying from the current one."
note "Expect a LITERAL \${POSTGRES_USER:-postgres} in the command: Compose turns"
note "\$\$ into one \$ and the container shell expands the rest. If you see an"
note "empty -U \"\" or -U \"\$\", the compose file was re-interpolated by a"
note "management UI and the escape was destroyed — that alone fails the probe"
note "forever (exit code 3)."
docker inspect --format '{{json .Config.Healthcheck}}' "$PG" 2>/dev/null | pretty_json || true

section "6. How long does the probe really take?"
note "The compose healthcheck allows 5s (timeout: 5s). Anything slower fails"
note "structurally, no matter how healthy the database is."
if command -v time >/dev/null 2>&1 || [ -n "${BASH_VERSION:-}" ]; then
  START="$(date +%s)"
  docker exec "$PG" sh -c \
    'pg_isready -h 127.0.0.1 -p 5432 -U "$POSTGRES_USER" -d "$POSTGRES_DB"; echo "exit=$?"' 2>&1 || true
  END="$(date +%s)"
  note "elapsed: $((END - START))s (includes docker exec overhead)"
fi

section "7. Rule out next-api as the real blocker"
note "/api/next/health returns 503 until migrations report 'ready', and it runs"
note "an artwork-trash purge in a write transaction on every probe. A slow or"
note "503 response here makes next-api the unhealthy service."
if [ -n "$API" ]; then
  docker inspect --format '{{json .State.Health}}' "$API" 2>/dev/null | pretty_json || true
  # Read the published port off the container rather than assuming the default,
  # which is wrong for any stack that moved it to avoid a collision.
  API_PORT="$(docker port "$API" 5000/tcp 2>/dev/null | head -1 | sed 's/.*://')"
  if [ -n "$API_PORT" ] && command -v curl >/dev/null 2>&1; then
    note "curl http://localhost:${API_PORT}/api/next/health"
    curl -s -o /dev/null -w '  http_code=%{http_code} time_total=%{time_total}s\n' \
      "http://localhost:${API_PORT}/api/next/health" 2>&1 || true
  else
    note "no published port for next-api (or curl missing); probing from inside instead"
    docker exec "$API" python -c \
      "import time,urllib.request,urllib.error;s=time.time()
try:
    r=urllib.request.urlopen('http://localhost:5000/api/next/health');c=r.status
except urllib.error.HTTPError as e:
    c=e.code
print('  http_code=%s time_total=%.6fs' % (c, time.time()-s))" 2>&1 || true
  fi
else
  note "next-api container not found; skipped."
fi

section "8. Do two stacks share one Docker network?"
note "'postgres' must resolve to exactly one address. More than one means"
note "another stack's database is answering roughly half of the connections."
if [ -n "$API" ]; then
  docker exec "$API" getent hosts postgres 2>&1 || true
else
  note "next-api container not found; skipped."
fi
docker inspect --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}' \
  "$PG" 2>/dev/null || true

section "9. Where does the PostgreSQL data directory live?"
note "A datadir on a network share (NFS/SMB) is a known source of slow and"
note "unreliable PostgreSQL starts."
docker inspect \
  --format '{{range .Mounts}}{{.Type}} {{.Source}} -> {{.Destination}}{{"\n"}}{{end}}' \
  "$PG" 2>/dev/null || true

printf '\n========================================================================\n'
printf 'Done. Nothing above modified the stack.\n'
printf 'Match the findings against the table in app/deploy/next/README.md,\n'
printf 'section "PostgreSQL is reported unhealthy on an existing stack".\n'
