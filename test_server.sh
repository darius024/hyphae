#!/bin/bash
# Start server in background and test it
cd /Users/stefi/Desktop/Projects/Hyphae/hyphae

export CLOUD_ONLY=1
# Provide your own keys via environment before running this script
: "${GEMINI_API_KEY:?set GEMINI_API_KEY}"
: "${CACTUS_API_KEY:?set CACTUS_API_KEY}"
: "${HUGGINGFACE_API_KEY:?set HUGGINGFACE_API_KEY}"
export PYTHONPATH="/Users/stefi/Desktop/Projects/Hyphae/hyphae:/Users/stefi/Desktop/Projects/Hyphae/hyphae/web"

# Kill old
pkill -f "uvicorn web.app:app" 2>/dev/null
sleep 1

# Start server in background
/Users/stefi/Desktop/Projects/Hyphae/.venv-1/bin/python -m uvicorn web.app:app --host 127.0.0.1 --port 5001 --log-level info &
SERVER_PID=$!
echo "Server PID: $SERVER_PID"

# Wait for it to be ready
for i in $(seq 1 60); do
    sleep 2
    if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5001/ 2>/dev/null | grep -q 200; then
        echo "Server ready after $((i*2)) seconds"
        # Test the API
        echo "=== Notebooks API ==="
        curl -s http://127.0.0.1:5001/api/notebooks | python3 -c "import json,sys; nbs=json.load(sys.stdin); print(f'{len(nbs)} notebooks'); [print(f'  [{n[\"id\"][:8]}] {n[\"name\"]}') for n in nbs]" 2>/dev/null || echo "(no notebooks)"
        echo "=== Checking new features in served JS ==="
        curl -s http://127.0.0.1:5001/static/app.js 2>/dev/null | grep -c "showToast" | xargs -I{} echo "showToast references: {}"
        curl -s http://127.0.0.1:5001/static/app.js 2>/dev/null | grep -c "applyTheme" | xargs -I{} echo "applyTheme references: {}"
        curl -s http://127.0.0.1:5001/static/app.js 2>/dev/null | grep -c "_renderNbItems" | xargs -I{} echo "_renderNbItems references: {}"
        echo "=== Checking new features in served CSS ==="
        curl -s http://127.0.0.1:5001/static/style.css 2>/dev/null | grep -c "body.dark" | xargs -I{} echo "body.dark rules: {}"
        curl -s http://127.0.0.1:5001/static/style.css 2>/dev/null | grep -c "nb-search" | xargs -I{} echo "nb-search rules: {}"
        echo "=== Server running on http://127.0.0.1:5001 (PID: $SERVER_PID) ==="
        exit 0
    fi
    echo "Waiting... ($((i*2))s)"
done

echo "Server failed to start in 120s"
kill $SERVER_PID 2>/dev/null
exit 1
