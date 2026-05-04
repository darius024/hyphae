# Hyphae

**Scientific Research Copilot That Respects Confidential Data**

> **Hyphae** (pronounced *hy-fee*) are the branching, thread-like filaments of a fungal network — the hidden infrastructure that connects organisms underground, sharing nutrients and signals without exposing the network itself. Just like hyphae enable communication while keeping the root system private, our system enables research reasoning while keeping raw experimental data confidential.

Built for the Cactus x Google DeepMind Hackathon

---

## What is Hyphae?

A hybrid local-first + cloud AI research platform for individual researchers. Upload your PDFs, experiment logs, and notes — Hyphae processes them **entirely on-device** using FunctionGemma via Cactus. Only abstract reasoning tasks (hypothesis generation, literature search) go to Gemini cloud, and only when you explicitly ask. Raw experimental data never leaves your machine.

The key innovation: **a privacy boundary you can see and verify.** Every query shows whether your data stayed local or touched the cloud, with a full audit log.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                         Hyphae                           │
│                                                          │
│  ┌───────────────────┐       ┌────────────────────────┐  │
│  │    LOCAL LAYER    │       │     CLOUD LAYER        │  │
│  │                   │       │                        │  │
│  │  Search corpus    │       │  Hypothesis generation │  │
│  │  Summarise notes  │       │  Literature search     │  │
│  │  Compare docs     │       │  Answer synthesis      │  │
│  │  Read / list      │       │  (only for cloud       │  │
│  │  Create notes     │       │   queries)             │  │
│  │  Text search      │       │                        │  │
│  │                   │       │  Gemini 2.5 Flash Lite │  │
│  │  FunctionGemma    │       │                        │  │
│  │  via Cactus       │       │                        │  │
│  └─────────┬─────────┘       └───────────┬────────────┘  │
│            │      ┌──────────────┐       │               │
│            └──────│   ROUTING    │───────┘               │
│                   │  Rule-based  │                       │
│                   │  → Local AI  │                       │
│                   │  → Cloud     │                       │
│                   └──────────────┘                       │
│                                                          │
│  Privacy: local queries never call Gemini, not even      │
│  for answer formatting. Cloud is opt-in per query.       │
└──────────────────────────────────────────────────────────┘
```

| Layer | What runs here | How |
|-------|---------------|-----|
| **Local** | Corpus search, summarise, compare, read, list, create notes, text search | FunctionGemma 270M on-device via Cactus SDK |
| **Cloud** | Hypothesis generation, literature search, answer synthesis for cloud queries | Gemini 2.5 Flash Lite API |
| **Routing** | 3-tier: rule-based → FunctionGemma → Gemini fallback | Confidence + privacy-aware decision engine |

## Setup

### Prerequisites

- macOS with Apple Silicon (M1+) or Linux
- Python 3.12+ (`brew install python@3.12` on macOS)
- [Gemini API key](https://aistudio.google.com/api-keys)
- [Cactus API key](https://cactuscompute.com/dashboard/api-keys)
- HuggingFace account with access to [google/functiongemma-270m-it](https://huggingface.co/google/functiongemma-270m-it)

### Quick Start

```bash
# 1. Clone the repo with submodules
git clone --recurse-submodules https://github.com/darius024/hyphae.git
cd hyphae

# 2. Login to HuggingFace (one-time, for gated model access)
pip install huggingface_hub
huggingface-cli login

# 3. Run setup (builds Cactus, downloads model weights, installs deps)
bash scripts/setup.sh

# 4. Activate the virtual environment
source .venv/bin/activate

# 5. Set your API keys
export GEMINI_API_KEY="your-gemini-key"
cactus auth   # enter your Cactus key when prompted

# 6. (Optional) Install ffmpeg for voice input
brew install ffmpeg

# 7. Start the web app
python -m uvicorn web.app:app --port 5000
```

Then open [http://localhost:5000](http://localhost:5000) in your browser.

### Manual Dependency Install

If `scripts/setup.sh` doesn't cover your platform, install manually:

```bash
cd hyphae
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install sentence-transformers   # for notebook embeddings
```

## Usage

### Web UI

```bash
source .venv/bin/activate
export GEMINI_API_KEY="your-key"
python -m uvicorn web.app:app --port 5000
```

The web interface provides:

- **Natural language research chat** — ask questions about your documents, get answers grounded in your corpus
- **Privacy-first routing** — every response shows LOCAL/CLOUD badge, a PRIVATE/CLOUD data badge, confidence level, and latency
- **Route prediction** — as you type, a live indicator below the input shows whether the query will stay on-device or use the cloud
- **Privacy audit log** — click "Audit log" to see a chronological record of every query: what ran locally, what touched the cloud, which tools were used
- **Document management** — sidebar with corpus listing, file-type icons, search/filter, click-to-preview (with in-browser PDF viewer for uploaded PDFs), drag-and-drop upload
- **Document sensitivity tagging** — mark individual documents as Confidential (lock icon) or Shareable; toggle with one click
- **Research tools panel** — collapsible panel showing all 9 available tools with LOCAL/CLOUD/HYBRID badges and parameter info
- **Quick prompts** — 4 dynamic research prompt templates that adapt to your actual corpus filenames (summarise with citations, compare documents, design experiment, literature + local blend)
- **Notebook workspaces** — create isolated research notebooks, upload sources, and chat with AI grounded in those specific documents (uses Gemini + RAG). Includes a seeded "Bioelectronics Research" demo notebook on first run
- **Notebook conversations** — open conversations in the main window with full rename (double-click or pencil icon) and delete support, relative timestamps, and a welcome message for new conversations
- **Voice input** — record from browser microphone, transcribed on-device via Whisper through Cactus
- **Chat history** — persists across page reloads via localStorage
- **Keyboard shortcuts** — `/` focus input, `Cmd+K` search docs, `Escape` closes any open modal/dialog, `Shift+Enter` newline

### CLI

```bash
python cli.py                   # interactive text mode
python cli.py --voice           # voice mode (Whisper on-device)
python cli.py "your query"     # one-shot query
```

### Corpus Management

Upload via the web UI sidebar, or use the CLI:

```bash
# Add files to your research corpus
python -m src.ingest add paper.pdf           # extracts text via PyMuPDF
python -m src.ingest add experiment_data/    # add a directory recursively
python -m src.ingest list                    # list indexed documents
python -m src.ingest remove old_notes.txt    # remove a document
```

Supported formats: PDF, TXT, Markdown, CSV, JSON, LOG.

## Research Tools

| Tool | Privacy | Description |
|------|---------|-------------|
| `search_papers` | LOCAL | Search local corpus via Cactus RAG (falls back to text scan if RAG weights unavailable) |
| `summarise_notes` | LOCAL | Summarise experiment notes on a given topic |
| `create_note` | LOCAL | Save a new research note to corpus |
| `list_documents` | LOCAL | List all local documents with sizes |
| `compare_documents` | LOCAL | Compare two documents on a topic (RAG + paragraph fallback) |
| `read_document` | LOCAL | Open and read a corpus file (truncated for safety) |
| `search_text` | LOCAL | Keyword search with paragraph-level snippets and filenames |
| `generate_hypothesis` | CLOUD | Generate testable hypotheses from abstract context |
| `search_literature` | CLOUD | Search scientific literature for prior work |

## Privacy Model

### Data Boundary

- **Local-only queries** (search, summarise, compare, read, list, create note, text search): the entire pipeline — routing, tool execution, and answer formatting — runs on your machine. **No data is sent to any cloud service**, not even for answer synthesis.
- **Cloud queries** (hypothesis, literature): only the user's abstract query is sent to Gemini. Raw corpus data is never included.
- **Answer synthesis**: only used for cloud queries. Local queries are formatted on-device.

### Sanitisation

Messages sent to the cloud are automatically sanitised by `src/privacy.py`:

- File paths → `[REDACTED]`
- Measurements, sample IDs, batch codes, lab codes
- Email addresses, URLs, IP addresses
- Dates, GPS coordinates

### Audit Log

Every query is logged with: timestamp, query text, tools used, whether data stayed local, and routing latency. View the log from the UI via the "Audit log" button, or fetch it programmatically at `GET /api/privacy-log`.

### Document Sensitivity

Tag individual documents as **Confidential** or **Shareable** from the sidebar. Confidential documents display a lock icon. Tags are stored locally in `corpus/.sensitivity.json`.

## Hybrid Routing

The routing engine (`generate_hybrid` in `main.py`) uses a 3-tier strategy:

1. **Rule-based extraction** — pattern-matches query verbs to research tools (e.g. "search" → `search_papers`, "compare" → `compare_documents`, "hypothesis" → `generate_hypothesis`). Returns instantly (~0ms) with high confidence.
2. **FunctionGemma on-device** — if rule-based matching fails, the 270M-parameter model runs locally via Cactus to select the right tool and extract arguments.
3. **Gemini cloud fallback** — if both local attempts fail or produce invalid results, falls back to Gemini 2.5 Flash Lite. If cloud also fails, returns the best-effort local result.

This ensures users always get a response while maximizing on-device execution.

## Notebooks

Notebooks are isolated research workspaces for deep-dive analysis:

1. **Create a notebook** — click "+ New" in the notebook panel to open the creation dialog
2. **Add sources** — upload PDFs, text files, or paste URLs; sources are ingested and embedded for retrieval
3. **Chat with sources** — ask questions grounded in your notebook's specific documents; answers stream in real-time with inline citations
4. **Multiple conversations** — each notebook can have multiple conversation threads

Notebooks use FAISS vector search + sentence-transformer embeddings for retrieval, with Gemini for response generation. Data is stored locally in `web/notebook.db` (SQLite). A demo "Bioelectronics Research" notebook is seeded automatically on first run.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/query` | Hybrid routing + tool execution + answer synthesis |
| `POST` | `/api/classify` | Predict whether a query will stay local or use cloud |
| `POST` | `/api/voice` | Transcribe audio + query |
| `GET` | `/api/documents` | List corpus files with types and sensitivity |
| `POST` | `/api/upload` | Upload files to corpus (preserves original PDFs) |
| `GET` | `/api/documents/{name}` | Preview document text |
| `GET` | `/api/documents/{name}/raw` | Serve original file (PDF viewer / download) |
| `DELETE` | `/api/documents/{name}` | Remove document from corpus |
| `GET` | `/api/tools` | List available tools with parameters and source |
| `GET` | `/api/sensitivity` | Get document sensitivity tags |
| `PUT` | `/api/sensitivity/{name}` | Set document sensitivity (confidential/shareable) |
| `GET` | `/api/privacy-log` | Fetch privacy audit log entries |
| `GET` | `/api/notebooks` | List notebooks |
| `POST` | `/api/notebooks` | Create notebook |
| `GET` | `/api/notebooks/{id}` | Get notebook details |
| `PATCH` | `/api/notebooks/{id}` | Update notebook name |
| `DELETE` | `/api/notebooks/{id}` | Delete notebook + all data |
| `GET` | `/api/notebooks/{id}/sources` | List notebook sources |
| `POST` | `/api/notebooks/{id}/upload` | Upload source to notebook |
| `POST` | `/api/notebooks/{id}/add-url` | Add URL source to notebook |
| `DELETE` | `/api/notebooks/{id}/sources/{sid}` | Remove source |
| `GET` | `/api/notebooks/{id}/conversations` | List conversations |
| `POST` | `/api/notebooks/{id}/conversations` | Create conversation |
| `PATCH` | `/api/notebooks/{id}/conversations/{cid}` | Rename conversation |
| `DELETE` | `/api/notebooks/{id}/conversations/{cid}` | Delete conversation |
| `GET` | `/api/notebooks/{id}/conversations/{cid}/messages` | List messages |
| `POST` | `/api/notebooks/{id}/conversations/{cid}/chat` | Non-streaming chat |
| `POST` | `/api/notebooks/{id}/conversations/{cid}/chat/stream` | Stream chat response (SSE) |
| `GET` | `/api/nb-settings` | List notebook settings |
| `PATCH` | `/api/nb-settings/{key}` | Update a setting |

## Tests

```bash
USE_DUMMY_EMBED=1 TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 RATE_LIMIT_RPM=0 \
  python -m pytest tests/ -v \
  --ignore=tests/unit/test_engine.py \
  --ignore=tests/unit/test_tools.py \
  --ignore=tests/integration/test_routing.py
ruff check .
mypy src web
```

The three ignored suites require either Cactus model weights or a live
Gemini API key and are skipped in CI.  See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md)
for the full development workflow, [docs/OPERATIONS.md](docs/OPERATIONS.md)
for environment variables and runbook tasks, and
[docs/SECURITY.md](docs/SECURITY.md) for the threat model.

Coverage spans:

- **Tool registry & dispatch** — schema validation, execution, error handling
- **Privacy sanitisation** — PII patterns, cloud safety, tool filtering
- **Corpus ingestion** — file add/remove, directory import, format filtering
- **Database layer** — schema creation, seeding, CRUD, cascade deletes, transactions, FK enforcement
- **Web API** — all REST endpoints via FastAPI TestClient
- **Auth hardening** — hashed-token storage, lockout, logout-all, sliding expiry
- **Per-user IDE isolation** — workspace namespacing, cross-user 403 enforcement
- **SSRF defence** — URL ingestion + redirect re-validation
- **FAISS persistence** — atomic writes, cache eviction with disk reload
- **Hybrid router fast-path** — rule-based extraction and cloud fallback

## Benchmark

```bash
python benchmark.py
```

Scoring: F1 accuracy (60%) + speed (15%) + on-device ratio (25%), weighted by difficulty.

## Submit

```bash
python scripts/submit.py --team "Darphie" --location "London"
```

## Project Structure

```
hyphae/
├── main.py                    # Hybrid routing engine entry point
├── benchmark.py               # Routing benchmark suite
├── cli.py                     # CLI entrypoint (text, voice, one-shot)
├── pyproject.toml             # Canonical dependency & project metadata
├── requirements.txt           # Flat pip requirements
│
├── scripts/                   # All scripts and dev utilities
│   ├── setup.sh               # First-time project setup
│   ├── start_server.sh        # Start the web server
│   ├── test_server.sh         # Start server + smoke tests
│   ├── submit.py              # Cactus Evals leaderboard submission
│   ├── tune_threshold.py      # Routing threshold tuning
│   ├── check_ids.js           # HTML ID audit
│   └── diagnose.js            # JS undeclared-var finder
│
├── src/core/                  # Core AI engine
│   ├── engine.py              # Hybrid routing: on-device vs cloud
│   ├── tools.py               # Tool definitions and execution
│   ├── privacy.py             # Privacy helpers (delegates to sanitiser)
│   ├── config.py              # Paths, env defaults
│   └── voice.py               # Whisper transcription
│
├── web/                       # FastAPI web application
│   ├── app.py                 # Application entry point, middleware, lifespan
│   ├── bootstrap.py           # Centralised sys.path setup
│   ├── routes/                # Modular API routers
│   │   ├── notebooks.py       # Notebook CRUD, sources, conversations, chat
│   │   ├── query.py           # Hybrid query, classify, tools, voice
│   │   ├── auth.py            # Authentication (bcrypt, sessions)
│   │   ├── corpus.py          # Corpus document endpoints
│   │   └── code.py            # Git clone, file browse, edit, commit
│   ├── notebook/              # Notebook domain layer
│   │   ├── db.py              # SQLite schema, connection manager
│   │   ├── models.py          # Pydantic v2 schemas
│   │   ├── ingest.py          # PDF/text/URL source ingestion
│   │   ├── retrieval.py       # FAISS + BM25 hybrid search
│   │   ├── citations.py       # Citation builder from search results
│   │   ├── embed.py           # Sentence-transformer embeddings
│   │   └── sanitiser.py       # PII sanitisation
│   └── static/                # Frontend (vanilla HTML/CSS/JS)
│       ├── index.html         # SPA shell
│       ├── app.js             # All frontend logic
│       └── style.css          # Styles (light + dark themes)
│
├── tests/                     # Test suite (pytest)
│   ├── unit/                  # Fast, isolated unit tests
│   └── integration/           # Tests requiring FastAPI TestClient
├── examples/                  # Standalone demos
├── docs/                      # Documentation
└── cactus/                    # Cactus SDK (git submodule)
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| On-device AI | FunctionGemma 270M via Cactus SDK |
| Cloud AI | Gemini 2.5 Flash Lite |
| Backend | FastAPI + Uvicorn |
| Database | SQLite (notebooks, sources, conversations) |
| Vector search | FAISS + sentence-transformers |
| PDF extraction | PyMuPDF |
| Voice | Whisper via Cactus (on-device) |
| Frontend | Vanilla HTML/CSS/JS, SSE streaming |
| Fonts | Inter (UI), Lora (prose) |

## Team

- Darius
- Stefi
