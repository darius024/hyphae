# Hyphae

**Scientific Research Copilot That Respects Confidential Data**

> **Hyphae** (pronounced *hy-fee*) are the branching, thread-like filaments of a fungal network — the hidden infrastructure that connects organisms underground, sharing nutrients and signals without exposing the network itself. Just like hyphae enable communication while keeping the root system private, our system enables research reasoning while keeping raw experimental data confidential.

## What is Hyphae?

A hybrid local-first + cloud AI system for scientific research. Sensitive data (PDFs, experiment logs, hardware notes) stays on-device via FunctionGemma + Cactus. Only abstract reasoning (hypothesis generation, literature reasoning) goes to the cloud via Gemini — raw experiments never leak.

The key novelty: **research reasoning without leaking raw experiments.**

Built for the Cactus × Google DeepMind Hackathon.

---

## Project Plan

### Goal

Maximize the benchmark score by improving `generate_hybrid()` in `main.py`. The score is:

$$\text{Score} = 0.60 \times F1 + 0.15 \times \text{SpeedScore} + 0.25 \times \text{OnDeviceRatio}$$

Weighted by difficulty: **easy 20% · medium 30% · hard 50%**.  
Speed score = $\max(0,\ 1 - t/500\text{ms})$. On-device ratio rewards staying local.

The challenge: FunctionGemma is fast and private but misses hard/multi-call cases. Gemini is accurate but slow and costs cloud credits. The routing strategy must balance all three.

---

### Branch Strategy

| Branch | Purpose |
|--------|---------|
| `main` | Stable, always runnable. Merged from feature branches after testing. |
| `feature/routing-strategy` | Core routing improvements in `generate_hybrid()` — confidence tuning, adaptive thresholds, caching. |
| `feature/prompt-engineering` | Improve tool descriptions and system prompts to boost FunctionGemma accuracy. |
| `feature/multi-call` | Handle hard cases (2-3 parallel tool calls) better on-device. |
| `feature/cloud-optimisation` | Reduce cloud latency — parallel local+cloud, early termination, response caching. |
| `feature/evaluation` | Local eval harness improvements, per-difficulty analysis, debug tooling. |

**Workflow:**
1. Create branch from `main`.
2. Implement feature, validate with `python benchmark.py`.
3. Only merge to `main` if score ≥ current baseline.
4. Submit to leaderboard once per hour max: `python submit.py --team "Hyphae" --location "London"`.

---

### Implementation Plan

#### Phase 1 — Baseline & Tooling (current)
- [x] Project setup: `setup.sh`, `requirements.txt`, `.env.example`, `LOCAL_SETUP.md`
- [x] Environment working: `google-genai` installed, keys loading from `.env`
- [x] Repo structure: branches defined, `.gitignore` correct, no secrets committed
- [ ] Validate baseline score by running `python benchmark.py` with Cactus + Gemini

#### Phase 2 — Routing Strategy (`feature/routing-strategy`)
Core target: **improve on-device ratio on easy/medium** while **falling back to cloud on hard multi-call cases**.

- [ ] Lower `confidence_threshold` from 0.99 to ~0.7–0.8 for easy cases to keep more on-device
- [ ] Implement difficulty-aware routing: detect likely multi-tool requests (count action verbs, "and", conjunctions) before calling local model
- [ ] Add retry logic: if local `function_calls` is empty, always fallback to cloud
- [ ] Cache repeated identical queries (same message+tools hash) to avoid double inference
- [ ] Track and log routing decisions per request for analysis

#### Phase 3 — Prompt Engineering (`feature/prompt-engineering`)
Core target: **higher F1 for FunctionGemma** without touching the routing threshold.

- [ ] Improve system prompt to emphasise multi-call capabilities
- [ ] Normalise tool descriptions (consistent style, explicit examples in descriptions)
- [ ] Add few-shot examples to system prompt for common patterns (alarm, weather, message)
- [ ] Test tool description reordering: most-likely-used tool first in the list

#### Phase 4 — Multi-Call Hard Cases (`feature/multi-call`)
Core target: **hard difficulty F1 ≥ 0.8** — currently the biggest score weight (50%).

- [ ] Pre-classify request complexity: single vs multi-tool (regex / keyword heuristic)
- [ ] For multi-tool requests detected locally: always use cloud (guaranteed multi-call support)
- [ ] For ambiguous cases: run local and validate output — if `len(function_calls) < expected_min`, retry with cloud
- [ ] Explore splitting compound requests into sub-queries for FunctionGemma

#### Phase 5 — Cloud Optimisation (`feature/cloud-optimisation`)
Core target: **reduce cloud latency** to improve speed score.

- [ ] Run local and cloud in parallel (ThreadPoolExecutor) with a timeout; use local if it finishes fast enough
- [ ] Cache cloud responses keyed on (message content, tool names) for repeated benchmark runs
- [ ] Test `gemini-2.5-flash` response time vs a smaller/faster model variant

#### Phase 6 — Final Polish & Submit
- [ ] Run full benchmark, confirm score ≥ baseline
- [ ] Clean up `main.py` — no debug prints, no commented code, signature unchanged
- [ ] Final `git commit` on `main`, then `python submit.py --team "Hyphae" --location "London"`

---

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

# 3. Run setup (clones cactus, builds, downloads model, installs deps)
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

The core file is `main.py` with three functions:
- `generate_cactus(messages, tools)` — on-device inference via FunctionGemma
- `generate_cloud(messages, tools)` — cloud inference via Gemini
- `generate_hybrid(messages, tools)` — **the routing strategy we're optimizing**

## Benchmark

```bash
source cactus/venv/bin/activate
export GEMINI_API_KEY="your-key"
python benchmark.py
```

Scoring: F1 accuracy (60%) + speed (15%) + on-device ratio (25%), weighted by difficulty (easy 20%, medium 30%, hard 50%).

## Submit

```bash
python submit.py --team "Hyphae" --location "London"
```

## Team

- Darius
- Stefi
