#!/bin/bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo ""
echo -e "${BLUE}=============================================${NC}"
echo -e "${BLUE}  Hyphae - Setup${NC}"
echo -e "${BLUE}=============================================${NC}"
echo ""

# Step 1: Initialize cactus submodule
if [ ! -f "cactus/README.md" ]; then
    echo -e "${BLUE}[1/6] Initializing cactus submodule...${NC}"
    git submodule update --init --recursive
    echo -e "${GREEN}  Done.${NC}"
else
    echo -e "${GREEN}[1/6] cactus/ already initialized, skipping.${NC}"
fi

# Step 2: Run cactus setup
echo -e "${BLUE}[2/6] Setting up cactus (venv + deps)...${NC}"
cd cactus
if [ ! -d "venv" ]; then
    python3.12 -m venv venv
fi
source venv/bin/activate
python3 -m pip install --upgrade pip -q
pip install -r python/requirements.txt -q
pip install -e python --quiet
echo -e "${GREEN}  Done.${NC}"

# Step 3: Build python shared library
echo -e "${BLUE}[3/6] Building cactus (libcactus)...${NC}"
cactus build --python
echo -e "${GREEN}  Done.${NC}"

# Step 4: Download model
echo -e "${BLUE}[4/6] Downloading FunctionGemma model...${NC}"
if [ -f "weights/functiongemma-270m-it/config.txt" ]; then
    echo -e "${GREEN}  Model already downloaded, skipping.${NC}"
else
    echo -e "${YELLOW}  You need HuggingFace access to google/functiongemma-270m-it${NC}"
    echo -e "${YELLOW}  Request access at: https://huggingface.co/google/functiongemma-270m-it${NC}"
    echo -e "${YELLOW}  Then run: huggingface-cli login${NC}"
    echo ""
    cactus download google/functiongemma-270m-it --reconvert
    echo -e "${GREEN}  Done.${NC}"
fi

# Step 5: Download Whisper model (for voice input)
echo -e "${BLUE}[5/8] Downloading Whisper model...${NC}"
if [ -f "weights/whisper-small/config.txt" ]; then
    echo -e "${GREEN}  Whisper model already downloaded, skipping.${NC}"
else
    cactus download openai/whisper-small
    echo -e "${GREEN}  Done.${NC}"
fi

# Step 6: Install google-genai
echo -e "${BLUE}[6/8] Installing google-genai...${NC}"
pip install google-genai -q
echo -e "${GREEN}  Done.${NC}"

cd "$PROJECT_ROOT"

# Step 7: Install sox for voice recording (optional)
echo -e "${BLUE}[7/8] Checking voice recording tools...${NC}"
if command -v sox &> /dev/null; then
    echo -e "${GREEN}  sox found.${NC}"
elif command -v ffmpeg &> /dev/null; then
    echo -e "${GREEN}  ffmpeg found (fallback for voice recording).${NC}"
else
    echo -e "${YELLOW}  Neither sox nor ffmpeg found. Voice mode won't work.${NC}"
    echo -e "${YELLOW}  Install with: brew install sox${NC}"
fi

# Step 8: Check for API keys
echo -e "${BLUE}[8/8] Checking API keys...${NC}"
if [ -z "$GEMINI_API_KEY" ]; then
    echo -e "${YELLOW}  GEMINI_API_KEY not set.${NC}"
    echo -e "${YELLOW}  Get one at: https://aistudio.google.com/api-keys${NC}"
    echo -e "${YELLOW}  Then run: export GEMINI_API_KEY=\"your-key\"${NC}"
else
    echo -e "${GREEN}  GEMINI_API_KEY is set.${NC}"
fi

echo ""
echo -e "${GREEN}=============================================${NC}"
echo -e "${GREEN}  Setup complete!${NC}"
echo -e "${GREEN}=============================================${NC}"
echo ""
echo -e "To use Hyphae:"
echo -e "  ${BLUE}source cactus/venv/bin/activate${NC}"
echo -e "  ${BLUE}export GEMINI_API_KEY=\"your-key\"${NC}"
echo ""
echo -e "  ${BLUE}python cli.py${NC}                  # interactive text mode"
echo -e "  ${BLUE}python cli.py --voice${NC}           # voice mode"
echo -e "  ${BLUE}python cli.py \"your query\"${NC}     # one-shot query"
echo -e "  ${BLUE}python benchmark.py${NC}             # run benchmark"
echo ""
echo -e "To authenticate cactus cloud fallback:"
echo -e "  ${BLUE}cactus auth${NC}"
echo ""
