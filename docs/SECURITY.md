# Hyphae — Security Notes

This document captures the threat model, the defences currently in place,
and the known gaps an operator should be aware of.

## Threat Model

Hyphae is a multi-tenant research assistant that handles:

- **User credentials** (email + password, persisted as bcrypt hashes).
- **Bearer session tokens** (256 bits of entropy, hashed at rest).
- **Private research notes, papers, and source documents** uploaded by
  the user, embedded into per-notebook FAISS indexes.
- **Cloned source-code repositories** under each user's IDE workspace.
- **Calendar OAuth tokens** (encrypted-at-rest with `TOKEN_ENCRYPTION_KEY`).

The threats we explicitly defend against:

1. **Cross-tenant data access** — one authenticated user reading,
   modifying, or even discovering another user's notebooks, files, or
   git repositories.
2. **Credential theft via DB exfiltration** — a leaked DB snapshot must
   not yield usable bearer tokens or cleartext passwords.
3. **Online password attacks** — credential stuffing, password spraying,
   timing-based user enumeration.
4. **SSRF through user-supplied URLs** — both notebook source ingestion
   and git clones accept URLs and must refuse to hit internal services.
5. **Server-side input injection** — SQL injection, path traversal,
   git option injection.
6. **PII leakage to the cloud** — sensitive document content must not
   reach Gemini without consent.

We do **not** currently defend against malicious browser extensions,
local OS compromise, or a compromised Gemini API key.

## Defences in Place

### Authentication

- bcrypt password hashing with adaptive cost.
- Constant-time login: a dummy bcrypt verify is performed when the email
  is unknown so response times do not leak account existence.
- Session tokens are SHA-256 hashed before being persisted; the database
  never contains a usable bearer token.  See `web/routes/auth.py`.
- Sliding 30-day session expiry, refreshed automatically when a request
  arrives within 7 days of expiry.
- Failed-login lockout (default 10 attempts → 15 min lock).  The counter
  resets on a successful login.  Counter UPDATEs are committed before
  the 401 response so the bookkeeping survives `HTTPException` unwinding.
- `POST /api/auth/logout-all` revokes every session for the caller.

### Per-Tenant Isolation

- **Notebooks**: every notebook is bound to a `user_id` (or an `org_id`
  for shared workspaces).  Routes verify ownership before serving data.
- **IDE workspaces**: each user's git clones live under
  `code_workspace/<user_id>/<repo>`.  `connect`/`delete-repo` reject
  any path that escapes the caller's workspace (`Path.is_relative_to`
  check → 403).
- **Per-user `asyncio.Lock`**: serialises concurrent git mutations for
  the same user to prevent race conditions on the working tree.

### URL Validation (SSRF)

Both the notebook URL ingester (`web/notebook/ingest.py`) and the IDE
clone endpoint (`web/routes/code.py`) reject:

- Non-HTTPS schemes (no `http://`, `file://`, `gopher://`, etc.).
- Loopback hosts: `localhost`, `127.0.0.0/8`, `[::1]`.
- RFC-1918 private ranges: `10.0.0.0/8`, `172.16.0.0/12`,
  `192.168.0.0/16`.
- Link-local: `169.254.0.0/16`.
- Cloud metadata endpoints (covered by the loopback + 169.254 rules).

The notebook ingester also re-validates after each redirect and caps
the response body at `MAX_FETCH_BYTES`.  See
`tests/integration/test_ssrf_redirects.py`.

### Path Traversal

`web/routes/code.py` resolves every user-supplied relative path under
the active repo root and rejects any result that is not
`Path.is_relative_to(root)` → 403.  The same pattern is used for the
notebook upload directory.

### Git Option Injection

`_safe_git_arg()` in `web/routes/code.py` rejects any positional argument
that begins with `-`, blocking attacks like passing
`--upload-pack=...` as a "branch name".  Branch names are additionally
constrained to `^[a-zA-Z0-9][a-zA-Z0-9._/\-]*$`.

### SQL Injection

All queries use parameterised SQL.  The single dynamic-SQL path
(`safe_update` in `web/notebook/db.py`) validates every column name
against `^[a-z][a-z0-9_]*$` before string interpolation.

### PII Sanitisation

`web/notebook/sanitiser.py` (re-exported by `src/core/privacy.py`)
strips emails, phone numbers, and other PII patterns from any payload
before it is sent to Gemini.  The hybrid router in `src/core/engine.py`
routes sensitive document content to the on-device path by default.

### Rate Limiting

`web/middleware.py` enforces per-IP request quotas.  Auth endpoints have
a stricter limit (`RATE_LIMIT_AUTH_RPM`, default 10/min) than general
traffic (`RATE_LIMIT_RPM`, default 120/min).  Both can be tuned via
environment variables — see [OPERATIONS.md](./OPERATIONS.md).

## Known Gaps

- **No CSRF protection on cookie-based auth**: Hyphae uses bearer tokens
  exclusively, so CSRF is N/A.  Do not introduce cookie-based session
  auth without adding a SameSite=strict flag plus a CSRF token.
- **No password complexity policy** beyond the 8-character minimum.
  Adding entropy checks (e.g. zxcvbn) is a worthwhile next step.
- **No 2FA**.  Sensitive accounts should be guarded by an external IdP.
- **TLS termination is the operator's responsibility.**  Hyphae itself
  speaks plain HTTP and assumes a reverse proxy in production.

## Reporting

Please report security issues privately rather than opening a public
issue.  Include reproduction steps and the affected commit hash.
