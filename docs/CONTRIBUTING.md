# Contributing to Hyphae

## Setup

```bash
cd hyphae
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example ../.env   # fill in GEMINI_API_KEY
```

## Development

```bash
./scripts/start_server.sh                  # http://localhost:5000 with --reload
USE_DUMMY_EMBED=1 TRANSFORMERS_OFFLINE=1 \
  HF_HUB_OFFLINE=1 RATE_LIMIT_RPM=0 \
  pytest tests/ -v                          # full offline test run
ruff check .                                # lint
mypy src web                                # static types
python benchmark.py                         # routing benchmark
```

Three test suites depend on external resources and are skipped in CI:
``tests/unit/test_engine.py``, ``tests/unit/test_tools.py``, and
``tests/integration/test_routing.py``.  Add ``--ignore=`` flags for each
when running locally without model weights or a Gemini API key.

## Branch Workflow

```
feat/* → dev → (benchmark passes & score improves) → main
```

| Branch pattern | Purpose |
|---|---|
| `main` | Stable, always runnable |
| `dev` | Active integration branch |
| `feat/<name>` | Feature branches |
| `fix/<issue>` | Bug fixes |

## Code Standards

- **Python**: Type hints on all function signatures.  Pydantic models for
  API request/response bodies.  Use ``logging`` (never ``print()`` in web
  code).  Run ``ruff check .`` and ``mypy src web`` before pushing — CI
  enforces both.
- **Commits**: Conventional Commits (``feat:``, ``fix:``, ``refactor:``,
  ``test:``, ``docs:``, ``chore:``).  Each commit must be self-contained
  and pass tests.
- **JavaScript**: ``const``/``let`` only (no ``var``).  Event listeners
  in JS (no inline ``onclick``).  ``try/catch`` around all ``fetch()``
  calls.
- **CSS**: Use CSS custom properties (``var(--name)``).  No inline styles
  for visibility toggling.  Dark mode via ``body.dark`` selector.
- **Tests**: Unit tests in ``tests/unit/``, integration tests in
  ``tests/integration/``.  Use ``tmp_path`` fixtures for file operations.
  Patch module globals via the path the route handler uses (``routes.auth``
  not ``web.routes.auth`` — they resolve to different module objects).

## Project Structure

See [ARCHITECTURE.md](./ARCHITECTURE.md) for full system design.

## Key Constraints

| Rule | Detail |
|---|---|
| Sensitive data stays local | Never send raw experiment data to Gemini |
| No hardcoded credentials | Always use environment variables |
| No hardcoded paths | Use `__file__`-relative or `Path` resolution |
| Pydantic for all API inputs | No `body: dict` in route handlers |
