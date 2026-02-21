# Stefi — Task Guide

> Focus areas: **Cloud pipeline**, **prompt engineering**, **scoring analysis**

---

## Your Branches

| Branch | What to do there |
|---|---|
| `feat/prompt-engineering` | Improve system prompts + tool descriptions for FunctionGemma |
| `feat/confidence-tuning` | Experiment with different confidence thresholds (0.99 → 0.3) |
| `feat/cloud-only-fallback` | Make project runnable without Cactus (cloud-only mode) |

**Workflow**:
```zsh
git checkout dev                        # always branch off dev
git checkout -b feat/prompt-engineering # create your branch
# ... make changes ...
git add main.py
git commit -m "feat: improve system prompt for multi-tool calls"
git push origin feat/prompt-engineering
```

---

## Task List

### Task 1 — Cloud-Only Fallback (unblock iteration)
**Branch**: `feat/cloud-only-fallback`  
**Priority**: 🔴 High — do this first so you can iterate without needing Cactus downloaded

**What to implement in `main.py`**:
- Wrap the `cactus` imports in a try/except so the file doesn't crash when Cactus is missing
- If `cactus` is not importable, `generate_cactus()` should return a safe empty result
- Add support for `CLOUD_ONLY=1` env var that forces `generate_hybrid` to skip local entirely

```python
# At the top of main.py, replace the hard import with:
try:
    from cactus import cactus_init, cactus_complete, cactus_destroy
    CACTUS_AVAILABLE = True
except ImportError:
    CACTUS_AVAILABLE = False
```

**Test it**:
```zsh
source cactus/venv/bin/activate
export GEMINI_API_KEY="your-key"
export CLOUD_ONLY=1
python benchmark.py
```

---

### Task 2 — Confidence Threshold Tuning
**Branch**: `feat/confidence-tuning`  
**Priority**: 🟡 Medium — quick wins, mostly changing one number

**What to do**:
- The current `confidence_threshold=0.99` in `generate_hybrid` is very strict → almost everything falls to cloud → on-device ratio ≈ 0% → −25% score penalty
- Test thresholds: `0.99`, `0.8`, `0.7`, `0.5`, `0.3`, `0.0`
- For each value, run `python benchmark.py` and note the score
- Find the sweet spot where F1 stays high and on-device ratio improves

**Experiment script** (add to `scripts/tune_threshold.py`):
```python
thresholds = [0.99, 0.8, 0.7, 0.5, 0.3, 0.0]
for t in thresholds:
    # temporarily monkeypatch generate_hybrid's default and run benchmark
```

**Expected insight**: Lowering threshold from 0.99 → 0.7 will push more easy/medium cases on-device without hurting F1 much.

---

### Task 3 — Prompt Engineering
**Branch**: `feat/prompt-engineering`  
**Priority**: 🟡 Medium — highest impact on hard multi-tool cases

**What to improve in `generate_cactus()` in `main.py`**:

**a) Better system prompt** — the current one is generic:
```python
# Current (weak):
{"role": "system", "content": "You are a helpful assistant that can use tools."}

# Improved:
{"role": "system", "content": (
    "You are a precise function-calling assistant. "
    "When the user asks you to do multiple things, call ALL required functions. "
    "Never skip a function call. Use ONLY the tools provided."
)}
```

**b) Tool description sharpening** — ambiguous descriptions confuse FunctionGemma:
- `set_alarm` vs `set_timer`: make the descriptions clearly distinct
- `send_message` vs `create_reminder`: add examples in the description field

**c) Few-shot examples** (for hard multi-tool cases):
- Consider adding 1-2 example assistant turns showing multi-function calls in the message history before the user query

**Test it**:
```zsh
python benchmark.py
# Compare hard cases before/after — focus on timer_music_reminder, message_weather_alarm
```

---

### Task 4 — Scoring Analysis
**Branch**: `dev` (analysis only, no code changes needed)  
**Priority**: 🟢 Ongoing — do this after every benchmark run

**What to track**:
- After each `python benchmark.py`, note: total score, F1 per difficulty, on-device ratio
- Identify which specific cases fail most (look at hard cases)
- Track in a simple table in a `RESULTS.md` file:

```markdown
| Date  | Strategy             | Score | F1 easy | F1 med | F1 hard | On-device |
|-------|----------------------|-------|---------|--------|---------|-----------|
| 21/02 | baseline (t=0.99)    | ?%    | ?       | ?      | ?       | ?%        |
| 21/02 | threshold t=0.7      | ?%    | ?       | ?      | ?       | ?%        |
```

---

## Run Commands (reference)

```zsh
# Activate environment
source cactus/venv/bin/activate

# Set keys
source .env   # or: export GEMINI_API_KEY="your-key"

# Run benchmark
python benchmark.py

# Run cloud-only (once Task 1 is done)
CLOUD_ONLY=1 python benchmark.py

# Submit (max 1x per hour — coordinate with Darius!)
python submit.py --team "Hyphae" --location "London"
```

---

## Score Formula (keep in mind)

$$\text{Score} = 0.50 \cdot \text{hard} + 0.30 \cdot \text{medium} + 0.20 \cdot \text{easy}$$

$$\text{where each level} = 0.60 \cdot F1 + 0.15 \cdot \text{speed} + 0.25 \cdot \text{on-device ratio}$$

- **Hard cases = 50% of total score** → your prompt engineering work has the biggest impact here
- On-device ratio is 25% of each difficulty level → push as many easy/medium cases local as possible
