#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
VERSION_FILE="$ROOT_DIR/.build-version"
ENV_FILE="$ROOT_DIR/.env"

if [ ! -f "$VERSION_FILE" ]; then
  echo "0" > "$VERSION_FILE"
fi

CURRENT="$(cat "$VERSION_FILE" | tr -d '[:space:]')"
case "$CURRENT" in
  ''|*[!0-9]*) CURRENT=0 ;;
esac

NEXT=$((CURRENT + 1))
DATE_TAG="$(date -u +%Y.%m.%d)"
BUILD_VERSION="${DATE_TAG}.${NEXT}"

echo "$NEXT" > "$VERSION_FILE"

if [ -f "$ENV_FILE" ]; then
  if grep -q '^BUILD_VERSION=' "$ENV_FILE"; then
    sed -i "s/^BUILD_VERSION=.*/BUILD_VERSION=${BUILD_VERSION}/" "$ENV_FILE"
  else
    printf '\nBUILD_VERSION=%s\n' "$BUILD_VERSION" >> "$ENV_FILE"
  fi
else
  printf 'BUILD_VERSION=%s\n' "$BUILD_VERSION" > "$ENV_FILE"
fi

echo "BUILD_VERSION=$BUILD_VERSION"
cd "$ROOT_DIR"

docker compose up -d --build
