#!/bin/bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo ""
echo -e "${BLUE}=============================================${NC}"
echo -e "${BLUE}  Hyphae - Setup${NC}"
echo -e "${BLUE}=============================================${NC}"
echo ""

# Step 1: Clone cactus if not present
if [ ! -d "cactus" ]; then
    echo -e "${BLUE}[1/6] Cloning cactus...${NC}"
    git clone https://github.com/cactus-compute/cactus
    echo -e "${GREEN}  Done.${NC}"
else
    echo -e "${GREEN}[1/6] cactus/ already exists, skipping clone.${NC}"
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

# Step 5: Install google-genai
echo -e "${BLUE}[5/6] Installing google-genai...${NC}"
pip install google-genai -q
echo -e "${GREEN}  Done.${NC}"

cd "$PROJECT_ROOT"

# Step 6: Check for API keys
echo -e "${BLUE}[6/6] Checking API keys...${NC}"
if [ -f ".env" ]; then
    echo -e "${GREEN}  .env file found.${NC}"
else
    echo -e "${YELLOW}  No .env file found. Creating template...${NC}"
    cat > .env << 'ENVEOF'
GEMINI_API_KEY=your-gemini-api-key-here
ENVEOF
    echo -e "${YELLOW}  Edit .env with your Gemini API key from: https://aistudio.google.com/api-keys${NC}"
fi

echo ""
echo -e "${GREEN}=============================================${NC}"
echo -e "${GREEN}  Setup complete!${NC}"
echo -e "${GREEN}=============================================${NC}"
echo ""
echo -e "To run the benchmark:"
echo -e "  ${BLUE}source cactus/venv/bin/activate${NC}"
echo -e "  ${BLUE}export GEMINI_API_KEY=\"your-key\"${NC}"
echo -e "  ${BLUE}python benchmark.py${NC}"
echo ""
echo -e "To authenticate cactus cloud fallback:"
echo -e "  ${BLUE}cactus auth${NC}"
echo ""
