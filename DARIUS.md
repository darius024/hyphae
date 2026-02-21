# Darius — Task Guide

> Focus areas: **Routing strategy**, **Cactus integration**, **performance optimisation**

---

## Your Branches

| Branch | What to do there |
|---|---|
| `feat/routing-multi-candidate` | Trust local if it returns any valid call; cloud only when local is empty |
| `feat/parallel-inference` | Run local + cloud in parallel; return the first valid result |
| `feat/routing-tool-heuristic` | Analyse the query before inference; route based on complexity |

**Workflow**:
```zsh
git checkout dev                              # always branch off dev
git checkout -b feat/routing-multi-candidate  # create your branch
# ... make changes to main.py generate_hybrid() ...
git add main.py
git commit -m "feat: multi-candidate routing — trust local if non-empty"
git push origin feat/routing-multi-candidate
```

---

## Task List

### Task 1 — Multi-Candidate Routing (biggest on-device win)
**Branch**: `feat/routing-multi-candidate`  
**Priority**: 🔴 High — likely the single best improvement for on-device ratio

**The insight**: The current strategy falls back to cloud whenever `confidence < 0.99`. But confidence might be low even when FunctionGemma produced the *correct* tool call. Instead: **trust local if it returned at least one well-formed function call**.

**What to implement in `generate_hybrid()`**:
```python
def generate_hybrid(messages, tools, confidence_threshold=0.99):
    local = generate_cactus(messages, tools)

    # Trust local if it returned any valid call (not empty)
    if local["function_calls"]:
        local["source"] = "on-device"
        return local

    # Only go to cloud when local produced nothing
    cloud = generate_cloud(messages, tools)
    cloud["source"] = "cloud (fallback)"
    cloud["local_confidence"] = local["confidence"]
    cloud["total_time_ms"] += local["total_time_ms"]
    return cloud
```

**Test it**:
```zsh
source cactus/venv/bin/activate
python benchmark.py
# Compare on-device ratio before/after
```

**Expected outcome**: on-device ratio jumps significantly for easy + medium cases.

---

### Task 2 — Tool-Count Heuristic (zero-cost routing)
**Branch**: `feat/routing-tool-heuristic`  
**Priority**: 🟡 Medium — adds smart pre-routing with no inference cost

**The insight**: You can tell if a query is hard (multi-tool) just by analysing the message text, before running any model. If it's simple → force local. If complex → use cloud directly (skip local entirely, saving time).

**What to implement**:
```python
import re

def _query_complexity(messages) -> str:
    """Estimate complexity: 'simple' or 'complex'."""
    text = " ".join(m["content"] for m in messages if m["role"] == "user").lower()
    # Connectors that suggest multiple actions
    connectors = ["and", "also", "then", "plus", "as well"]
    connector_count = sum(text.count(c) for c in connectors)
    return "complex" if connector_count >= 2 else "simple"

def generate_hybrid(messages, tools, confidence_threshold=0.99):
    complexity = _query_complexity(messages)

    if complexity == "simple":
        # Simple query — force local, don't even check confidence
        local = generate_cactus(messages, tools)
        if local["function_calls"]:
            local["source"] = "on-device"
            return local

    # Complex or local failed — go cloud
    cloud = generate_cloud(messages, tools)
    cloud["source"] = "cloud (fallback)"
    return cloud
```

**Test it**:
```zsh
python benchmark.py
# Focus on: does it correctly route easy/medium on-device and hard to cloud?
```

---

### Task 3 — Parallel Inference (speed improvement for hard cases)
**Branch**: `feat/parallel-inference`  
**Priority**: 🟡 Medium — reduces latency when cloud is needed anyway

**The insight**: For hard queries that will likely fall to cloud, you're currently paying local_time + cloud_time. If you run both in parallel, total time ≈ max(local_time, cloud_time) instead of the sum.

**What to implement**:
```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def generate_hybrid(messages, tools, confidence_threshold=0.99):
    complexity = _query_complexity(messages)  # from Task 2

    if complexity == "simple":
        # Simple → local only, no parallelism needed
        local = generate_cactus(messages, tools)
        if local["function_calls"]:
            local["source"] = "on-device"
            return local

    # Complex → run both in parallel
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_local = executor.submit(generate_cactus, messages, tools)
        future_cloud = executor.submit(generate_cloud, messages, tools)

        local = future_local.result()
        cloud = future_cloud.result()

    # Prefer local if it returned valid calls (still maximises on-device ratio)
    if local["function_calls"]:
        local["source"] = "on-device"
        return local

    cloud["source"] = "cloud (fallback)"
    cloud["local_confidence"] = local["confidence"]
    return cloud
```

**Test it**:
```zsh
python benchmark.py
# Look at: avg time for hard cases — should be lower than baseline
```

---

### Task 4 — Cactus Model Handle Caching (latency fix)
**Branch**: `dev` or `feat/routing-multi-candidate`  
**Priority**: 🟡 Medium — reduces per-call overhead

**The problem**: Currently `generate_cactus()` calls `cactus_init()` and `cactus_destroy()` on **every single call**. This loads/unloads the model from memory each time, which is very slow.

**What to implement**: Keep the model handle alive across calls using a module-level singleton.

```python
_cactus_model = None

def _get_cactus_model():
    global _cactus_model
    if _cactus_model is None:
        _cactus_model = cactus_init(functiongemma_path)
    return _cactus_model

def generate_cactus(messages, tools):
    model = _get_cactus_model()   # reuse instead of re-init
    # ... rest of function, but REMOVE cactus_destroy(model) at the end
```

> ⚠️ Only remove `cactus_destroy` from `generate_cactus`. Keep it available for cleanup at shutdown if needed.

**Test it**:
```zsh
python benchmark.py
# Latency for on-device cases should drop significantly
```

---

## Run Commands (reference)

```zsh
# Activate environment
source cactus/venv/bin/activate

# Set keys
source .env   # or: export GEMINI_API_KEY="your-key"

# Authenticate Cactus (one-time per machine)
cactus auth

# Run benchmark
python benchmark.py

# Submit (max 1x per hour — coordinate with Stefi!)
python submit.py --team "Hyphae" --location "London"
```

---

## Score Formula (keep in mind)

$$\text{Score} = 0.50 \cdot \text{hard} + 0.30 \cdot \text{medium} + 0.20 \cdot \text{easy}$$

$$\text{where each level} = 0.60 \cdot F1 + 0.15 \cdot \text{speed} + 0.25 \cdot \text{on-device ratio}$$

- **On-device ratio = 25% of score per level** → your routing work directly impacts this
- **Speed = 15%** → caching the model handle (Task 4) directly improves this
- Hard cases = 50% of total → parallel inference (Task 3) keeps hard case latency low
