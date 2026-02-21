# Hyphae — Implementation Plan

> **Objective**: Win the Cactus × Google DeepMind Hackathon by building the best hybrid routing algorithm for tool-calling across on-device (FunctionGemma via Cactus) and cloud (Gemini 2.5 Flash), while keeping sensitive data local.
>
> Score = **F1 (60%)** + **Speed (15%)** + **On-Device Ratio (25%)** — weighted easy 20%, medium 30%, hard 50%.

---

## Repo Branches

| Branch | Purpose |
|---|---|
| `main` | Stable, always runnable. Only merge when a change improves benchmark score. |
| `dev` | Active integration branch. All feature branches merge here first. |
| `feat/routing-<name>` | One branch per routing strategy (see Phase 2). |
| `feat/prompt-engineering` | Improvements to system prompts and tool descriptions. |
| `feat/cloud-only-fallback` | Safe cloud-only mode when Cactus is unavailable. |
| `feat/confidence-tuning` | Experiments with threshold values. |
| `feat/parallel-inference` | Parallel local + cloud calls; pick winner. |
| `fix/<issue>` | Bug fixes. |

**Workflow**:  
```
feat/* → dev → (benchmark passes & score improves) → main
```

---

## Phases

### Phase 0 — Foundation ✅ (done)
- [x] Project structure: `main.py`, `benchmark.py`, `submit.py`
- [x] `setup.sh` — one-command environment setup
- [x] `.env.example` — secure secrets management
- [x] `.gitignore` — `.env`, `.venv`, `cactus/` ignored
- [x] `LOCAL_SETUP.md` — local run instructions
- [x] `scripts/check_env.py` — validate API keys are loaded

---

### Phase 1 — Stability & Cloud-Only Mode
**Branch**: `feat/cloud-only-fallback`  
**Goal**: Make the codebase runnable even when Cactus model weights are not yet downloaded, for fast cloud-side iteration.

- [ ] Wrap `cactus` imports in `main.py` with a try/except → set `CACTUS_AVAILABLE = False` if missing
- [ ] `generate_cactus()` returns a safe stub `{"function_calls": [], "confidence": 0, "total_time_ms": 0}` when `CACTUS_AVAILABLE=False`
- [ ] Add `CLOUD_ONLY=1` env var option that forces `generate_hybrid` to skip local entirely
- [ ] Validate `GEMINI_API_KEY` is set at startup; print a clear error if missing

**Why**: Lets both team members iterate on routing logic without needing Cactus on their machine.

---

### Phase 2 — Routing Strategy Experiments
Each strategy gets its own branch from `dev`.

#### 2a. Baseline (already in `main.py`)
- Strategy: Run local first, fall back to cloud if `confidence < threshold` (default 0.99)
- Problem: threshold 0.99 is very strict → almost everything falls to cloud → low on-device ratio → bad score

#### 2b. Confidence Threshold Tuning
**Branch**: `feat/confidence-tuning`
- [ ] Benchmark the current score with threshold 0.99, 0.7, 0.5, 0.3, 0.0
- [ ] Find the sweet spot: higher threshold → more cloud fallback → better F1 but worse on-device ratio
- [ ] Make threshold per-difficulty: stricter for hard, looser for easy

#### 2c. Multi-Candidate Strategy
**Branch**: `feat/routing-multi-candidate`
- [ ] Run local inference and check if it returned **any** valid function call
- [ ] If local returns at least 1 well-formed call (non-empty `function_calls`), trust it → on-device
- [ ] Only fallback to cloud when local returns empty result
- [ ] Expected: dramatically improves on-device ratio

#### 2d. Tool-Count Heuristic
**Branch**: `feat/routing-tool-heuristic`
- [ ] Count the number of expected tools in the request (heuristic from message length / keyword density)
- [ ] If query seems simple (1 tool, short message) → force on-device
- [ ] If query seems complex (multi-tool, long message) → use cloud
- [ ] No extra inference cost: pure string analysis before any model call

#### 2e. Parallel Inference (Race Strategy)
**Branch**: `feat/parallel-inference`
- [ ] Run both `generate_cactus` and `generate_cloud` **in parallel** via `ThreadPoolExecutor`
- [ ] If local finishes first and has valid result: cancel cloud call, return local
- [ ] If cloud finishes first or local fails: return cloud result
- [ ] Set aggressive timeout (2s) on local before accepting cloud
- [ ] Improves speed on hard queries without sacrificing F1

#### 2f. Prompt Engineering
**Branch**: `feat/prompt-engineering`
- [ ] Improve the system prompt in `generate_cactus` to be more directive for multi-tool calls
- [ ] Add few-shot examples in the system prompt for hard cases (multi-tool)
- [ ] Improve tool descriptions to reduce ambiguity (e.g. `set_alarm` vs `set_timer`)
- [ ] Test: do richer tool descriptions improve local confidence scores?

---

### Phase 3 — Scoring Optimisation
**Branch**: `dev` (iterative, small PRs)  
**Goal**: Push total score above 80%.

- [ ] Combine best strategies from Phase 2 into a unified `generate_hybrid`
- [ ] Add per-query source logging to identify which cases fall to cloud and why
- [ ] Re-run `benchmark.py` after every meaningful change — compare before/after scores
- [ ] Profile slowest cases: identify if Cactus init latency is the bottleneck (consider caching the model handle)
- [ ] Consider **caching the Cactus model handle** across calls (currently `cactus_init` + `cactus_destroy` per call → expensive)

---

### Phase 4 — Submission
- [ ] Final run of `python benchmark.py` — confirm score improvement
- [ ] Commit final `main.py` to `main`
- [ ] Submit: `python submit.py --team "Hyphae" --location "London"`
- [ ] Max 1 submission per hour — time carefully!

---

## Key Constraints (from project rules)

| Rule | Detail |
|---|---|
| Do not change `generate_hybrid` signature | Must stay compatible with `benchmark.py` |
| Use `gemini-2.5-flash` | NOT `gemini-2.0-flash` (deprecated) |
| Venv at `cactus/venv/` | `source cactus/venv/bin/activate` before running |
| Sensitive data stays local | Never send raw experiment data to Gemini |
| No hardcoded credentials | Always use env vars |

---

## Scoring Formula (reference)

$$\text{Score} = \sum_{\text{difficulty}} w_d \cdot \left[ 0.60 \cdot F1_d + 0.15 \cdot \text{SpeedScore}_d + 0.25 \cdot \text{OnDeviceRatio}_d \right]$$

where $w_{\text{easy}} = 0.20$, $w_{\text{medium}} = 0.30$, $w_{\text{hard}} = 0.50$, and:

$$\text{SpeedScore} = \max\left(0,\ 1 - \frac{\text{avg\_time\_ms}}{500}\right)$$

**Takeaway**: Hard cases are 50% of the score. Getting multi-tool calls right on hard queries is the single biggest lever. Also note: on-device ratio is 25% — maximising local inference helps even if F1 is slightly lower.

---

## Quick Run Reference

```zsh
# Activate environment
source cactus/venv/bin/activate

# Set keys (if not already in .env)
export GEMINI_API_KEY="your-key"

# Run full benchmark
python benchmark.py

# Submit to leaderboard (max 1x per hour)
python submit.py --team "Hyphae" --location "London"
```

---

## Team

| Person | Focus |
|---|---|
| Darius | Routing strategy, Cactus integration |
| Stefi | Cloud pipeline, prompt engineering, scoring analysis |
