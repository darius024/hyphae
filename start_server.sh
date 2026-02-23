#!/bin/bash
cd /Users/stefi/Desktop/Projects/Hyphae/hyphae

# Source .env from repo root
if [ -f ../.env ]; then
    set -a && source ../.env && set +a
fi

export PYTHONPATH="/Users/stefi/Desktop/Projects/Hyphae/hyphae:/Users/stefi/Desktop/Projects/Hyphae/hyphae/web"

# Use the fast venv (fresh, responsive)
PYTHON=/Users/stefi/Desktop/Projects/Hyphae/.venv-fast/bin/python

echo "Using Python: $PYTHON"
exec "$PYTHON" -m uvicorn web.app:app --host 127.0.0.1 --port 5001 --log-level info
