# Hyphae

**Scientific Research Copilot That Respects Confidential Data**

> **Hyphae** (pronounced *hy-fee*) are the branching, thread-like filaments of a fungal network — the hidden infrastructure that connects organisms underground, sharing nutrients and signals without exposing the network itself. Just like hyphae enable communication while keeping the root system private, our system enables research reasoning while keeping raw experimental data confidential.

## What is Hyphae?

A hybrid local-first + cloud AI system for scientific research. Sensitive data (PDFs, experiment logs, hardware notes) stays on-device via FunctionGemma + Cactus. Only abstract reasoning (hypothesis generation, literature reasoning) goes to the cloud via Gemini — raw experiments never leak.

The key novelty: **research reasoning without leaking raw experiments.**

Built for the Cactus x Google DeepMind Hackathon.

## Architecture

```
┌──────────────────────────────────────────────────┐
│                    Hyphae                        │
│                                                  │
│  ┌─────────────────┐    ┌─────────────────────┐  │
│  │   LOCAL LAYER   │    │    CLOUD LAYER      │  │
│  │                 │    │                     │  │
│  │  PDFs           │    │  Hypothesis         │  │
│  │  Experiment logs│    │  generation         │  │
│  │  Hardware notes │    │  Literature         │  │
│  │                 │    │  reasoning          │  │
│  │  FunctionGemma  │    │  Gemini 2.5 Flash   │  │
│  │  via Cactus     │    │                     │  │
│  └────────┬────────┘    └──────────┬──────────┘  │
│           │    ┌──────────────┐    │              │
│           └────│   ROUTING    │────┘              │
│                │  Confidence  │                   │
│                │  + Privacy   │                   │
│                └──────────────┘                   │
└──────────────────────────────────────────────────┘
  Raw data never leaves the device.
```

| Layer | What | How |
|-------|------|-----|
| **Local** | PDFs, experiment logs, hardware notes | FunctionGemma on-device via Cactus |
| **Cloud** | Hypothesis generation, literature reasoning | Gemini 2.5 Flash API |
| **Routing** | Smart confidence + privacy-aware decision | 3-tier hybrid: local → retry → cloud fallback |

## Setup

### Prerequisites
- macOS with Apple Silicon (M1+)
- Python 3.12 (`brew install python@3.12`)
- HuggingFace account with access to [google/functiongemma-270m-it](https://huggingface.co/google/functiongemma-270m-it)
- [Gemini API key](https://aistudio.google.com/api-keys) — claim hackathon credits via [London link](https://trygcp.dev/claim/cactus-x-gdm-hackathon-london)
- [Cactus API key](https://cactuscompute.com/dashboard/api-keys)

### Quick Start

```bash
# 1. Clone the repo (with submodules)
git clone --recurse-submodules https://github.com/darius024/hyphae.git && cd hyphae

# 2. Login to HuggingFace (one-time, for gated model access)
pip install huggingface_hub
huggingface-cli login

# 3. Run setup (initializes submodule, builds, downloads model, installs deps)
bash setup.sh

# 4. Activate the environment
source cactus/venv/bin/activate

# 5. Set API keys
export GEMINI_API_KEY="your-gemini-key"
cactus auth  # enter your cactus key when prompted

# 6. Run the benchmark
python benchmark.py
```

## Usage

### Web UI

```bash
source cactus/venv/bin/activate
export GEMINI_API_KEY="your-key"
python web/app.py                  # start on port 5000
PORT=8080 python web/app.py        # custom port
```

The web interface is the primary way to use Hyphae. It provides:

- **Natural language answers** — queries are routed through tool execution, then synthesized into a readable response via Gemini
- **Document management** — sidebar with corpus listing, search/filter, click-to-preview, drag-and-drop upload (PDFs, text, markdown, CSV)
- **Voice input** — record from the browser microphone, auto-converted and transcribed on-device via Whisper
- **Routing transparency** — every response shows a LOCAL/CLOUD badge, confidence level (HIGH/MED/LOW), and latency
- **Chat history** — conversations persist across page reloads via localStorage, with a clear button to reset
- **Mobile responsive** — hamburger menu for sidebar access on small screens
- **Keyboard shortcuts** — `/` to focus input, `Cmd+K` to search documents, `Escape` to dismiss, `Cmd+Enter` to send

### CLI

```bash
python cli.py                    # interactive text mode
python cli.py --voice            # voice mode (Whisper on-device)
python cli.py "your query"      # one-shot query
```

```
  > Search my notes about battery capacity retention
  [LOCAL] routed in 210ms
  -> search_papers({"query": "battery capacity retention"})  [LOCAL-ONLY]
     Found 3 passages:
     1. [0.87] FEC-3 additive shows improved capacity retention vs baseline...

  > Generate hypotheses about why FEC-3 improves cycling stability
  [CLOUD] routed in 850ms
  -> generate_hypothesis({"context": "FEC-3 improves cycling stability"})  [CLOUD-SAFE]
     1. FEC-3 forms a more stable SEI layer...
```

### Corpus management

```bash
python -m src.ingest add paper.pdf        # add a PDF (text extracted via PyMuPDF)
python -m src.ingest add notes/           # add a directory recursively
python -m src.ingest list                 # list indexed documents
python -m src.ingest remove <filename>    # remove a document
```

Or use the web UI sidebar to upload, preview, search, and remove documents.

## Research Tools

| Tool | Privacy | Description |
|------|---------|-------------|
| `search_papers` | LOCAL-ONLY | Search local corpus via Cactus RAG |
| `summarise_notes` | LOCAL-ONLY | Summarise experiment notes on a topic |
| `create_note` | LOCAL-ONLY | Save a research note locally |
| `list_documents` | LOCAL-ONLY | List all local documents |
| `compare_documents` | LOCAL-ONLY | Compare two documents on a topic via RAG |
| `generate_hypothesis` | CLOUD-SAFE | Generate hypotheses from abstract context |
| `search_literature` | CLOUD-SAFE | Search scientific literature |

## Hybrid Routing

The routing engine (`generate_hybrid` in `main.py`) uses a 3-tier strategy:

1. **Local first** — run FunctionGemma on-device via Cactus. Accept if confidence is high and the result is valid.
2. **Local retry** — if the first attempt is incomplete or invalid, retry once on-device.
3. **Cloud fallback** — if both local attempts fail, fall back to Gemini 2.5 Flash. If cloud also fails, return the best-effort local result.

This ensures users always get a response while maximizing on-device execution for privacy.

## Privacy

Messages sent to the cloud are automatically sanitised by `src/privacy.py`. The following are stripped before any data reaches Gemini:

- **File paths** (`/data/experiment_1.csv` → `[REDACTED]`)
- **Measurements** (`3.5 mg`, `25.0 °C`)
- **Sample/batch IDs** (`sample #A42`, `batch B-17`)
- **Lab codes** (`AB-1234`)
- **Email addresses** (`jane@lab.org`)
- **URLs** (`https://internal.lab.io/...`)
- **IP addresses** (`192.168.1.42`)
- **Dates** (`2025-03-15`)
- **GPS coordinates** (`51.5074, -0.1278`)

Tools marked LOCAL-ONLY never send data to cloud under any circumstances.

## Response Synthesis

After tool execution, the backend sends the user's question and tool results to Gemini to generate a natural language answer. This means the UI shows a readable response like "Your battery cycling data shows 95% capacity retention after 500 cycles..." instead of raw tool output. Tool call details are still available in a collapsible section below each response.

## Tests

```bash
python -m pytest tests/ -v
```

34 unit tests covering:
- **Tool registry** — all tools have required fields, local/cloud partition is complete
- **Tool dispatch** — `execute_tool` routes correctly, handles unknown tools and missing args
- **Privacy sanitisation** — all 9 pattern types strip correctly, normal text preserved, no mutation
- **Corpus ingestion** — add/remove/list files, PDF handling, directory recursion, unsupported formats

## Benchmark

```bash
python benchmark.py
```

Scoring: F1 accuracy (60%) + speed (15%) + on-device ratio (25%), weighted by difficulty (easy 20%, medium 30%, hard 50%).

## Submit

```bash
python submit.py --team "Hyphae" --location "London"
```

## Project Structure

```
hyphae/
├── main.py                 # Hybrid routing engine (stays at root for submit.py)
├── benchmark.py            # Hackathon benchmark
├── submit.py               # Leaderboard submission
├── cli.py                  # CLI entrypoint (text, voice, one-shot)
├── setup.sh                # One-command setup
├── requirements.txt        # Python dependencies
│
├── src/                    # Library modules
│   ├── config.py           # Centralized cactus/model paths
│   ├── tools.py            # Research tool definitions + execution
│   ├── privacy.py          # Cloud message sanitiser (9 pattern types)
│   ├── voice.py            # On-device voice input via Whisper
│   └── ingest.py           # Corpus ingestion CLI + PDF extraction
│
├── tests/                  # Unit tests (pytest, 34 tests)
│   ├── conftest.py         # Shared fixtures (temp corpus, monkeypatch)
│   ├── test_tools.py       # Tool dispatch + registry tests
│   ├── test_privacy.py     # Sanitisation + cloud safety tests
│   ├── test_ingest.py      # Ingestion + corpus management tests
│   └── test_routing.py     # Routing integration tests
│
├── web/                    # Flask web app
│   ├── app.py              # API backend (query, docs, upload, voice, preview)
│   └── static/             # Frontend (HTML/CSS/JS)
│       ├── index.html      # Chat UI with sidebar + preview modal
│       ├── style.css       # Dark theme, responsive, animations
│       └── app.js          # Chat logic, history, shortcuts, doc management
│
├── examples/               # Usage examples
│   ├── basic_query.py      # Minimal hybrid query
│   ├── corpus_management.py # PDF ingestion demo
│   └── voice_demo.py       # Voice transcription demo
│
├── docs/                   # Documentation (PLAN, DARIUS, STEFI, RESULTS)
├── scripts/                # Utility scripts (tune_threshold.py)
├── corpus/                 # Local research documents (never sent to cloud)
└── cactus/                 # Cactus SDK (git submodule)
```

## Team

- Darius
- Stefi
