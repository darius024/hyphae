# Hyphae — Project Roadmap

> **Objective**: Build the best hybrid routing algorithm for tool-calling across on-device (FunctionGemma via Cactus) and cloud (Gemini 2.5 Flash), while keeping sensitive data local.
>
> Score = **F1 (60%)** + **Speed (15%)** + **On-Device Ratio (25%)** — weighted easy 20%, medium 30%, hard 50%.

---

## Completed

### Foundation
- [x] Project structure: modular FastAPI backend with routers
- [x] `scripts/start_server.sh` — portable one-command startup
- [x] `.env.example` — documented environment variables
- [x] `.gitignore` — runtime data, secrets, OS files excluded
- [x] `bootstrap.py` — centralised `sys.path` management

### Web Application
- [x] Notebook CRUD with sources, conversations, chat
- [x] SSE streaming chat with Gemini 2.5 Flash Lite
- [x] FAISS + BM25 hybrid retrieval with citations
- [x] LaTeX paper editor with live preview
- [x] Calendar with event management
- [x] VS Code-like code editor with git integration
- [x] Authentication (bcrypt, sessions)
- [x] Dark mode with CSS custom properties
- [x] PII sanitisation before cloud calls

### Code Quality (Refactoring)
- [x] Pydantic request validation on all API endpoints
- [x] CSS deduplication (~500 lines removed)
- [x] Dead code removal (debug logging, unused functions, aliases)
- [x] Error handling on all async fetch calls
- [x] Path traversal protection on file endpoints
- [x] bcrypt password hashing (replaced SHA-256)
- [x] CORS middleware
- [x] Deprecated API fixes (lifespan, timezone-aware datetime)
- [x] Consolidated privacy sanitiser modules

---

## Routing Strategies

### Baseline
- Strategy: Run local first, fall back to cloud if `confidence < threshold`
- Default threshold: 0.99 (very strict)

### Multi-Candidate (highest impact)
- Trust local if it returns any valid function call
- Only fall back to cloud when local produces nothing
- Expected: dramatically improves on-device ratio

### Tool-Count Heuristic (zero cost)
- Analyse query text before inference to estimate complexity
- Simple queries → force local; complex → use cloud directly
- No inference cost: pure string analysis

### Parallel Inference (speed)
- Run local + cloud simultaneously via ThreadPoolExecutor
- Return first valid result
- Reduces latency on hard queries

---

## Scoring Formula

$$\text{Score} = \sum_{\text{difficulty}} w_d \cdot \left[ 0.60 \cdot F1_d + 0.15 \cdot \text{SpeedScore}_d + 0.25 \cdot \text{OnDeviceRatio}_d \right]$$

where $w_{\text{easy}} = 0.20$, $w_{\text{medium}} = 0.30$, $w_{\text{hard}} = 0.50$.

---

## Quick Reference

```bash
source cactus/venv/bin/activate
export GEMINI_API_KEY="your-key"

python benchmark.py                    # run full benchmark
python scripts/submit.py --team "Hyphae" --location "London"  # submit
./scripts/start_server.sh              # start web UI
pytest tests/ -v                       # run tests
```
