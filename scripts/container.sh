#!/usr/bin/env bash
# Containerised DriftWarden: build the image and run it with the Docker socket
# and a persistent ./data directory mounted. Invoked by `make start` / `make stop`
# through Git Bash on Windows and plain bash elsewhere, so the recipe never
# depends on cmd.exe or PowerShell syntax.
#
# Usage: scripts/container.sh start|stop|logs|status [IMAGE] [CONTAINER] [PORT]
# The optional positional arguments (passed by the Makefile, which may itself be
# running under cmd.exe where VAR=value prefixes do not exist) override the
# environment variables IMAGE, CONTAINER, PORT; DATA_DIR comes from the environment.

set -euo pipefail

IMAGE="${2:-${IMAGE:-driftwarden:latest}}"
CONTAINER="${3:-${CONTAINER:-driftwarden}}"
PORT="${4:-${PORT:-8000}}"
DATA_DIR="${DATA_DIR:-data}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Absolute host path for the data mount. On Git Bash / MSYS the pwd looks like
# /c/Users/...; Docker Desktop needs C:/Users/... for bind mounts.
host_path() {
  local p="$1"
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -m "$p"
  else
    printf '%s' "$p"
  fi
}

# The socket path must not be rewritten by MSYS path conversion, hence the
# leading double slash; on Linux and macOS "//var/run/docker.sock" resolves to
# the same socket.
SOCKET="//var/run/docker.sock:/var/run/docker.sock"
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"

case "${1:-}" in
  start)
    echo "🐳 Building $IMAGE ..."
    docker build -t "$IMAGE" .
    if [ ! -f .env ]; then
      echo "❌ .env not found in $ROOT; copy .env.example to .env first." >&2
      exit 1
    fi
    mkdir -p "$DATA_DIR"
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    DATA_HOST="$(host_path "$ROOT/$DATA_DIR")"
    echo "🚀 Starting $CONTAINER on http://localhost:$PORT (database in $DATA_DIR/warden.db) ..."
    docker run -d --name "$CONTAINER" \
      -p "$PORT:8000" \
      --env-file .env \
      -e API_HOST=0.0.0.0 \
      -e API_PORT=8000 \
      -e DATABASE_URL=sqlite:////data/warden.db \
      -e DOCKER_SOCKET_PATH=/var/run/docker.sock \
      -v "$SOCKET" \
      -v "$DATA_HOST:/data" \
      "$IMAGE" >/dev/null
    echo "✅ $CONTAINER is up. Logs: make logs  Stop: make stop"
    ;;
  stop)
    if docker rm -f "$CONTAINER" >/dev/null 2>&1; then
      echo "🛑 Removed container $CONTAINER (data kept in $DATA_DIR/)."
    else
      echo "ℹ️  No container named $CONTAINER is running."
    fi
    ;;
  logs)
    exec docker logs -f --tail 100 "$CONTAINER"
    ;;
  status)
    docker ps -a --filter "name=^/${CONTAINER}$" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    ;;
  *)
    echo "Usage: bash scripts/container.sh {start|stop|logs|status}" >&2
    exit 1
    ;;
esac
