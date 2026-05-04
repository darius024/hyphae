# Hyphae — Architecture

> A hybrid on-device/cloud research copilot with privacy-first design.

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser (SPA)                                                  │
│  index.html + app.js + style.css                                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │   Chat   │ │  Write   │ │ Calendar │ │  Code    │           │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘           │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTP / SSE
┌────────────────────────▼────────────────────────────────────────┐
│  FastAPI (web/app.py)                                           │
│  ┌─────────────┐ ┌─────────────┐ ┌──────────┐ ┌────────────┐  │
│  │ notebooks.py│ │  query.py   │ │ auth.py  │ │  code.py   │  │
│  │ CRUD, chat, │ │ hybrid      │ │ signup   │ │ git ops,   │  │
│  │ sources,    │ │ routing,    │ │ login    │ │ file edit   │  │
│  │ calendar    │ │ voice, tools│ │ sessions │ │            │  │
│  └──────┬──────┘ └──────┬──────┘ └──────────┘ └────────────┘  │
│         │               │                                       │
│  ┌──────▼──────┐ ┌──────▼──────┐                               │
│  │ notebook/   │ │  core/      │                               │
│  │ db, ingest, │ │ engine,     │                               │
│  │ retrieval,  │ │ tools,      │                               │
│  │ citations,  │ │ privacy,    │                               │
│  │ sanitiser   │ │ config      │                               │
│  └──────┬──────┘ └──────┬──────┘                               │
│         │               │                                       │
│    SQLite + FAISS   Cactus SDK + Gemini API                    │
└─────────────────────────────────────────────────────────────────┘
```

## Directory Structure

```
hyphae/
├── web/                    # FastAPI web application
│   ├── app.py              # Application entry point, middleware, lifespan
│   ├── bootstrap.py        # Centralised sys.path setup
│   ├── routes/
│   │   ├── notebooks.py    # Notebook CRUD, sources, conversations, chat
│   │   ├── query.py        # Hybrid query, classify, tools, voice
│   │   ├── auth.py         # Authentication: bcrypt, hashed sessions, lockout
│   │   ├── corpus.py       # Legacy corpus document endpoints
│   │   ├── code.py         # Per-user git workspace + file/edit/commit API
│   │   ├── tags.py         # Tags & source labelling
│   │   ├── analytics.py    # Usage events and aggregates
│   │   ├── planning.py     # Deadlines, reminders, calendar sync
│   │   ├── notes.py        # Notes, versions, knowledge-graph links
│   │   ├── collaboration.py# Sharing, comments, activity feed
│   │   └── export.py       # Notebook export (PDF, JSON, etc.)
│   ├── notebook/
│   │   ├── db.py           # SQLite schema, connection manager
│   │   ├── models.py       # Pydantic v2 schemas
│   │   ├── ingest.py       # PDF/text/URL source ingestion
│   │   ├── retrieval.py    # FAISS + BM25 hybrid search
│   │   ├── citations.py    # Citation builder from search results
│   │   ├── embed.py        # Sentence-transformer embeddings
│   │   └── sanitiser.py    # PII sanitisation (shared across layers)
│   └── static/
│       ├── index.html      # SPA shell
│       ├── app.js          # All frontend logic
│       └── style.css       # All styles (light + dark themes)
├── src/
│   └── core/
│       ├── engine.py       # Hybrid routing: on-device vs cloud
│       ├── tools.py        # Tool definitions and execution
│       ├── privacy.py      # Privacy helpers (delegates to sanitiser)
│       ├── config.py       # Paths, env defaults
│       └── voice.py        # Whisper transcription
├── tests/
│   ├── unit/               # Fast, isolated unit tests
│   │   ├── test_db.py
│   │   ├── test_sanitiser.py
│   │   ├── test_privacy.py
│   │   ├── test_router.py        # Hybrid routing fast-path coverage
│   │   ├── test_extractors.py    # Rule-based argument extraction
│   │   ├── test_json_repair.py   # Cactus JSON post-processing
│   │   ├── test_faiss_persistence.py
│   │   ├── test_tools.py
│   │   └── test_ingest.py
│   ├── integration/        # Tests requiring FastAPI TestClient
│   │   ├── test_web_api.py
│   │   ├── test_auth_api.py
│   │   ├── test_auth_hardening.py   # Lockout, hashed tokens, logout-all
│   │   ├── test_code_isolation.py   # Per-user IDE workspace isolation
│   │   ├── test_ssrf_redirects.py   # SSRF defence on URL ingest
│   │   └── test_validation.py
│   └── conftest.py         # Shared fixtures
├── scripts/                # All scripts and dev utilities
│   ├── setup.sh            # First-time project setup
│   ├── start_server.sh     # Start the web server
│   ├── test_server.sh      # Start server + smoke tests
│   ├── submit.py           # Submit to Cactus Evals leaderboard
│   ├── tune_threshold.py   # Routing threshold tuning
│   ├── check_ids.js        # HTML ID audit
│   └── diagnose.js         # JS undeclared-var finder
├── examples/               # Standalone demos
├── docs/                   # Documentation
├── cli.py                  # CLI interface
├── benchmark.py            # Routing benchmark suite
├── main.py                 # Backward-compatible engine wrapper
├── pyproject.toml          # Canonical dependency & project metadata
└── requirements.txt        # Flat pip requirements (mirrors pyproject.toml)
```

## Key Design Decisions

### Hybrid Routing (Privacy-First)

The core innovation: queries are routed to either on-device (FunctionGemma 270M via Cactus SDK) or cloud (Gemini 2.5 Flash Lite) based on confidence scoring. Sensitive data (raw documents, measurements, sample IDs) never leaves the device.

1. Local inference runs first via `generate_cactus()`
2. If confidence is below threshold or result is empty, falls back to `generate_cloud()`
3. PII sanitisation (`sanitiser.py`) scrubs data before any cloud call

### Notebook Architecture

Each notebook is an isolated workspace:
- **Sources**: uploaded PDFs/text/URLs, ingested into chunks
- **Conversations**: chat threads grounded in the notebook's sources
- **Retrieval**: hybrid FAISS vector search + BM25 full-text search
- **Citations**: responses cite specific source passages with page numbers

### Authentication

- bcrypt password hashing (adaptive cost, timing-safe)
- Session tokens are SHA-256 hashed before storage; the database never
  contains a usable bearer token
- Sliding 30-day expiry with a 7-day refresh window
- Failed-login lockout (10 fails → 15 min) with counter reset on success
- ``POST /api/auth/logout-all`` for one-shot revocation across devices

### Per-User IDE Workspace

Each user's repos live under ``code_workspace/<user_id>/<repo>``.  Active
repo state is tracked in the ``code_repos`` SQLite table, keyed by
``user_id``.  A per-user :class:`asyncio.Lock` serialises concurrent git
mutations so two requests from the same user never race.

### Frontend

Single-page app with no framework — vanilla HTML/CSS/JS:
- **Chat**: SSE streaming from Gemini, markdown rendering, LaTeX via MathJax
- **Write**: Split-pane LaTeX editor with live preview
- **Calendar**: Event management with agenda view
- **Code**: VS Code-like IDE with git integration

## Running

```bash
cd hyphae
cp .env.example ../.env   # fill in GEMINI_API_KEY
./scripts/start_server.sh  # starts on http://localhost:5000
```

See [OPERATIONS.md](./OPERATIONS.md) for environment variables, health
checks, log format, and rate-limit knobs.  See [SECURITY.md](./SECURITY.md)
for the threat model and security-relevant defences.

## Testing

```bash
cd hyphae
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
USE_DUMMY_EMBED=1 TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 RATE_LIMIT_RPM=0 \
  pytest tests/ -v \
  --ignore=tests/unit/test_engine.py \
  --ignore=tests/unit/test_tools.py \
  --ignore=tests/integration/test_routing.py
ruff check .
```

The three ignored suites require either the Cactus model weights or a
live Gemini API key and are skipped in CI.
