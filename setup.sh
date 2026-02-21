#!/usr/bin/env zsh
# Setup script for the Hyphae project (macOS, zsh)
# This automates the local steps described in README.md as far as possible.

set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")" && pwd)
cd "$ROOT_DIR"

echo "[hyphae] Starting setup in $ROOT_DIR"

# 1) Clone the external 'cactus' repo if missing
# Allow users to override the cactus directory or the repo URL via env vars:
#   CACTUS_DIR (default: ./cactus)
#   CACTUS_REPO_URL (default: upstream URL; may be private or moved)
CACTUS_DIR=${CACTUS_DIR:-cactus}
CACTUS_REPO_URL=${CACTUS_REPO_URL:-https://github.com/cactus-com/cactus}

SKIP_CACTUS=0
if [ -d "$CACTUS_DIR" ]; then
  echo "[hyphae] Using existing '$CACTUS_DIR' directory; skipping clone." 
else
  echo "[hyphae] Attempting to clone cactus repo from $CACTUS_REPO_URL into $CACTUS_DIR..."
  if git clone "$CACTUS_REPO_URL" "$CACTUS_DIR"; then
    echo "[hyphae] Successfully cloned into '$CACTUS_DIR'."
  else
    echo "[hyphae] Failed to clone $CACTUS_REPO_URL. The repo may be private or moved.";
    echo "[hyphae] Options:"
    echo "  - Set CACTUS_DIR to point to a local checkout: export CACTUS_DIR=~/path/to/cactus"
    echo "  - Set CACTUS_REPO_URL to a different repo: export CACTUS_REPO_URL=git@github.com:you/your-fork.git"
    echo "  - Or clone manually: git clone <repo-url> $CACTUS_DIR"
    echo "[hyphae] Continuing setup but skipping cactus build/download steps.";
    SKIP_CACTUS=1
  fi
fi

echo "\n[hyphae] Setting up Python virtual environment (.venv)"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt


echo "\n[hyphae] cactus build/download steps (optional and may take a long time):"
echo "Note: these commands come from project README and require network and build tools."
echo "If you choose to run them, they may require opening a new shell after sourcing ./setup so 'cactus' is in PATH."

if [ "$SKIP_CACTUS" != "1" ]; then
  cd "$CACTUS_DIR"
  if [ -f ./setup ]; then
    echo "Sourcing ./setup (in current shell)..."
    # shellcheck disable=SC1091
    source ./setup || true
  else
    echo "No ./setup script found in $CACTUS_DIR. If the upstream repo changed, run their install steps manually." 
  fi

  echo "Running: cactus build --python"
  if command -v cactus >/dev/null 2>&1; then
    cactus build --python || echo "cactus build failed; continue if you only want to run cloud parts";
  else
    echo "cactus command not found in PATH. You may need to re-open terminal (per README) or ensure cactus is installed in this shell." 
  fi

  echo "Downloading recommended FunctionGemma weights (may be large):"
  if command -v cactus >/dev/null 2>&1; then
    cactus download google/functiongemma-270m-it --reconvert || echo "download failed or skipped";
  else
    echo "Skipping model download because 'cactus' command isn't available in this shell.";
  fi

  cd "$ROOT_DIR"
else
  echo "[hyphae] Skipping cactus build/download because no cactus checkout is available.";
fi

echo "\n[hyphae] Setup complete (local artifacts created). Next steps printed in LOCAL_SETUP.md"
echo "If you need to authenticate cactus, run: (in a new terminal) cd cactus && source ./setup && cactus auth"
echo "Remember to set your GEMINI_API_KEY before running cloud calls: export GEMINI_API_KEY=\"your-key\""

echo "Run 'python benchmark.py' to run the benchmark once you have the model weights and keys."
