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
./scripts/start_server.sh  # http://localhost:5000 with --reload
pytest tests/ -v           # run all tests
python benchmark.py        # run routing benchmark
```

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

- **Python**: Type hints on all function signatures. Pydantic models for API request/response bodies. Use `logging` module (never `print()` in web code).
- **JavaScript**: `const`/`let` only (no `var`). Event listeners in JS (no inline `onclick`). `try/catch` around all `fetch()` calls.
- **CSS**: Use CSS custom properties (`var(--name)`). No inline styles for visibility toggling. Dark mode via `body.dark` selector.
- **Tests**: Unit tests in `tests/unit/`, integration tests in `tests/integration/`. Use `tmp_path` fixtures for file operations.

## Project Structure

See [ARCHITECTURE.md](./ARCHITECTURE.md) for full system design.

## Key Constraints

| Rule | Detail |
|---|---|
| Sensitive data stays local | Never send raw experiment data to Gemini |
| No hardcoded credentials | Always use environment variables |
| No hardcoded paths | Use `__file__`-relative or `Path` resolution |
| Pydantic for all API inputs | No `body: dict` in route handlers |
