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
| **Routing** | Smart confidence + privacy-aware decision | Hybrid strategy in `generate_hybrid()` |

## Setup

### Prerequisites
- macOS with Apple Silicon (M1+)
- Python 3.12 (`brew install python@3.12`)
- HuggingFace account with access to [google/functiongemma-270m-it](https://huggingface.co/google/functiongemma-270m-it)
- [Gemini API key](https://aistudio.google.com/api-keys) — claim hackathon credits via [London link](https://trygcp.dev/claim/cactus-x-gdm-hackathon-london)
- [Cactus API key](https://cactuscompute.com/dashboard/api-keys)

### Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/darius024/hyphae.git && cd hyphae

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

### Interactive text mode

```bash
source cactus/venv/bin/activate
export GEMINI_API_KEY="your-key"
python cli.py
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

### Voice mode

```bash
python cli.py --voice    # speak queries, Whisper transcribes on-device
```

### One-shot query

```bash
python cli.py "list all my documents"
```

## Research Tools

| Tool | Privacy | Description |
|------|---------|-------------|
| `search_papers` | LOCAL-ONLY | Search local corpus via Cactus RAG |
| `summarise_notes` | LOCAL-ONLY | Summarise experiment notes on a topic |
| `create_note` | LOCAL-ONLY | Save a research note locally |
| `list_documents` | LOCAL-ONLY | List all local documents |
| `compare_documents` | LOCAL-ONLY | Compare two documents on a topic |
| `generate_hypothesis` | CLOUD-SAFE | Generate hypotheses from abstract context |
| `search_literature` | CLOUD-SAFE | Search scientific literature |

## Privacy

Messages sent to the cloud are automatically sanitised by `privacy.py`:
- File paths, measurements, sample IDs, and lab codes are stripped
- Only abstract intent reaches Gemini
- Tools marked LOCAL-ONLY never send data to cloud

## Benchmark

```bash
python benchmark.py
```

Scoring: F1 accuracy (60%) + speed (15%) + on-device ratio (25%), weighted by difficulty (easy 20%, medium 30%, hard 50%).

## Submit

```bash
python submit.py --team "Hyphae" --location "London"
```

### Web UI

```bash
python web/app.py                  # start on port 5000
PORT=8080 python web/app.py        # custom port
```

Chat interface with document sidebar, drag-and-drop upload, and voice input.

### Corpus management

```bash
python -m src.ingest add paper.pdf        # add a PDF
python -m src.ingest add notes/           # add a directory
python -m src.ingest list                 # list indexed documents
python -m src.ingest remove <filename>    # remove a document
```

## Tests

```bash
python -m pytest tests/ -v
```

29 unit tests covering tool dispatch, privacy sanitisation, and corpus ingestion.

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
│   ├── privacy.py          # Cloud message sanitiser
│   ├── voice.py            # On-device voice input via Whisper
│   └── ingest.py           # Corpus ingestion CLI
│
├── tests/                  # Unit tests (pytest)
│   ├── conftest.py         # Shared fixtures
│   ├── test_tools.py       # Tool dispatch tests
│   ├── test_privacy.py     # Sanitisation tests
│   ├── test_ingest.py      # Ingestion tests
│   └── test_routing.py     # Routing integration tests
│
├── web/                    # Flask web app
│   ├── app.py              # API backend
│   └── static/             # Frontend (HTML/CSS/JS)
│
├── examples/               # Usage examples
│   ├── basic_query.py      # Minimal hybrid query
│   ├── corpus_management.py # PDF ingestion demo
│   └── voice_demo.py       # Voice transcription demo
│
├── docs/                   # Documentation
├── scripts/                # Utility scripts
├── corpus/                 # Local research documents (never sent to cloud)
└── cactus/                 # Cactus SDK (git submodule)
```

## Team

- Darius
- Stefi
