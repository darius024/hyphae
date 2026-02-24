#!/usr/bin/env bash
# Start server in background and run basic smoke tests.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Source .env from repo root (optional)
if [ -f "$REPO_ROOT/.env" ]; then
  set -a; source "$REPO_ROOT/.env"; set +a
fi

export CLOUD_ONLY=1
: "${GEMINI_API_KEY:?set GEMINI_API_KEY}"
export PYTHONPATH="$REPO_ROOT:$REPO_ROOT/web${PYTHONPATH:+:$PYTHONPATH}"

PYTHON="${PYTHON:-$REPO_ROOT/cactus/venv/bin/python}"
PORT="${PORT:-5001}"

if [ ! -x "$PYTHON" ]; then
  echo "Python not found at: $PYTHON — set PYTHON=/path/to/python" >&2
  exit 1
fi

# Kill any previous test server
pkill -f "uvicorn web.app:app.*--port $PORT" 2>/dev/null || true
sleep 1

# Start server in background
"$PYTHON" -m uvicorn web.app:app --host 127.0.0.1 --port "$PORT" --log-level info &
SERVER_PID=$!
echo "Server PID: $SERVER_PID"

cleanup() { kill "$SERVER_PID" 2>/dev/null || true; }
trap cleanup EXIT

# Wait for server to be ready
for i in $(seq 1 30); do
  sleep 2
  if curl -sf -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; then
    echo "Server ready after $((i*2))s"

    echo "=== Notebooks API ==="
    curl -s "http://127.0.0.1:$PORT/api/notebooks" | python3 -m json.tool 2>/dev/null || echo "(no notebooks)"

    echo "=== Smoke checks ==="
    curl -sf "http://127.0.0.1:$PORT/static/app.js" >/dev/null && echo "app.js: OK" || echo "app.js: FAIL"
    curl -sf "http://127.0.0.1:$PORT/static/style.css" >/dev/null && echo "style.css: OK" || echo "style.css: FAIL"

    echo "=== Server running on http://127.0.0.1:$PORT (PID: $SERVER_PID) ==="
    exit 0
  fi
  echo "Waiting... ($((i*2))s)"
done

echo "Server failed to start in 60s"
exit 1
