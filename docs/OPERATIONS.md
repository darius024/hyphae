# Hyphae — Operations Runbook

How to run, observe, and tune Hyphae in development and production.

## Environment Variables

### Required for cloud features

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | Google Gemini API key.  Required for cloud-fallback routing and the assistant's free-form answers.  Without it, requests that need cloud fall back gracefully with an error. |

### Server lifecycle

| Variable | Default | Purpose |
|---|---|---|
| `HYPHAE_ENV` | `development` | Set to `production` to enable strict CORS, security headers, and disable docs. |
| `CORS_ORIGINS` | empty | Comma-separated list of allowed origins.  Required in production. |
| `RATE_LIMIT_RPM` | `120` | Per-IP requests per minute on the global rate limiter.  Set to `0` to disable (used in tests). |
| `RATE_LIMIT_AUTH_RPM` | `10` | Per-IP requests per minute on auth endpoints (signup/login). |
| `SESSION_PURGE_INTERVAL` | `3600` | Background interval (seconds) for deleting expired sessions. |

### Authentication

| Variable | Default | Purpose |
|---|---|---|
| `MAX_SESSIONS_PER_USER` | `10` | Cap on concurrent sessions per user.  Oldest are pruned on login. |
| `SESSION_LIFETIME_DAYS` | `30` | Initial session lifetime. |
| `SESSION_REFRESH_WINDOW_DAYS` | `7` | When a request comes in within this many days of expiry, the session is rolled forward to a fresh full lifetime. |
| `LOCKOUT_THRESHOLD` | `10` | Consecutive failed logins before the account is temporarily locked. |
| `LOCKOUT_MINUTES` | `15` | Duration of the lockout. |

### Inference / retrieval

| Variable | Default | Purpose |
|---|---|---|
| `CLOUD_ONLY` | `0` | Set to `1` to skip on-device inference and route everything to Gemini. |
| `CACTUS_TIMEOUT` | `30` | Seconds before on-device generation is abandoned and the cloud path is tried. |
| `CACTUS_PREWARM` | `0` | Set to `1` to load the FunctionGemma model at startup instead of lazily. |
| `FUNCTIONGEMMA_PATH` | autodetect | Override path to the FunctionGemma weights. |
| `GEMINI_MODEL` | `gemini-2.5-flash-lite` | Cloud model identifier. |
| `FAISS_INDEX_TTL` | `300` | Seconds an in-memory FAISS index is cached before reloading from disk. |
| `USE_DUMMY_EMBED` | unset | When `1`, use the deterministic hash-based embedder.  Tests use this for offline reproducibility. |
| `TRANSFORMERS_OFFLINE`, `HF_HUB_OFFLINE` | unset | Standard Hugging Face flags — set to `1` in tests. |

## Health Checks

| Endpoint | Purpose | Status codes |
|---|---|---|
| `GET /health` | Liveness probe.  Returns 200 as long as the process is up. | 200 always |
| `GET /ready` | Readiness probe.  Verifies SQLite connectivity, the routing engine, and the embedder. | 200 ready / 503 not ready |

`/ready` returns a `checks` object so operators can spot a silently
degraded backend without grepping logs:

```json
{
  "status": "ready",
  "checks": {
    "db": "ok",
    "engine": "ok",
    "tools": "ok",
    "embedder": "ok"
  }
}
```

If `embedder` is `"dummy"`, the deterministic fallback is in use — fine
for tests, never acceptable in production.

## Logs

Hyphae emits structured `logging` records.  Important loggers:

| Logger | What it reports |
|---|---|
| `core.engine` | Cactus model load, cloud-fallback decisions, GEMINI_API_KEY warnings. |
| `routes.auth` | Account lockouts (WARNING).  No password material is ever logged. |
| `routes.code` | Per-user git invocations.  The user-id is included for forensics. |
| `notebook.retrieval` | FAISS index load/save, cache hits. |

## Storage Layout

```
hyphae/
├── web/notebook.db        # primary SQLite database (WAL mode)
├── web/indexes/<nbid>/    # per-notebook FAISS indexes + chunk_id maps
├── web/uploads/           # uploaded source files (PDF/text)
└── code_workspace/<user_id>/<repo>/   # per-user IDE clones
```

The database is created and migrated on first startup via `init_db()` in
`web/notebook/db.py`.  Migrations are idempotent `ALTER TABLE` statements
guarded by `try/except sqlite3.OperationalError`.

## Common Operational Tasks

### Reset a user's lockout

```sql
UPDATE users SET failed_login_count=0, locked_until=NULL WHERE email='alice@example.com';
```

### Force-revoke every session for a user

```sql
DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE email='alice@example.com');
```

### Drop a stuck FAISS index (forces reload from disk)

```bash
rm web/indexes/<notebook-id>/index.faiss
```

The next query will rebuild the in-memory cache.

### Verify the production deployment

```bash
curl -fs http://localhost:5000/health         # liveness
curl -fs http://localhost:5000/ready | jq .   # full readiness JSON
```

A 503 from `/ready` usually means the database file is missing or the
volume mount is broken — fix that before serving traffic.
