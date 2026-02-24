#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

# Source .env from repo root (optional)
if [ -f "$REPO_ROOT/.env" ]; then
  set -a
  source "$REPO_ROOT/.env"
  set +a
fi

export PYTHONPATH="$REPO_ROOT:$REPO_ROOT/web${PYTHONPATH:+:$PYTHONPATH}"

PYTHON="${PYTHON:-$REPO_ROOT/cactus/venv/bin/python}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-5000}"
LOG_LEVEL="${LOG_LEVEL:-info}"
RELOAD="${RELOAD:-1}"

if [ ! -x "$PYTHON" ]; then
  echo "Python not found/executable at: $PYTHON" >&2
  echo "Tip: set PYTHON=/path/to/python and re-run." >&2
  exit 1
fi

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  if [ "$PORT" = "5000" ] && ! lsof -nP -iTCP:5001 -sTCP:LISTEN >/dev/null 2>&1; then
    PORT=5001
  elif [ "$PORT" = "5000" ] && ! lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
    PORT=8000
  else
    echo "Port already in use: $PORT" >&2
    echo "Tip: set PORT=#### and re-run." >&2
    exit 1
  fi
fi

echo "Using Python: $PYTHON"
echo "Starting server at http://$HOST:$PORT"

ARGS=( -m uvicorn web.app:app --host "$HOST" --port "$PORT" --log-level "$LOG_LEVEL" )
if [ "$RELOAD" != "0" ]; then
  ARGS+=( --reload )
fi

exec "$PYTHON" "${ARGS[@]}"
