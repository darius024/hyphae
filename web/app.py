"""
Hyphae Web API — FastAPI backend.

Thin orchestrator that bootstraps paths, mounts modular routers, and
serves static files. All domain logic lives in sub-packages:

    routes/corpus.py    — document upload, list, preview, sensitivity
    routes/notebooks.py — notebook CRUD, sources, conversations, chat
    routes/query.py     — classify, tools list, hybrid query, voice

Run:
    set -a && source .env && set +a
    ./.venv/bin/python -m uvicorn web.app:app --reload --port 5000
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

# ── Centralised path bootstrap (MUST come before any Hyphae imports) ─────
from bootstrap import bootstrap
bootstrap()

try:
    from dotenv import load_dotenv  # type: ignore
    _REPO_ROOT = Path(__file__).resolve().parents[1].parent
    _env_path = _REPO_ROOT / ".env"
    if _env_path.exists():
        load_dotenv(dotenv_path=str(_env_path))
        logging.getLogger(__name__).info("Loaded .env from %s", _env_path)
except Exception:
    pass

# ── FastAPI ───────────────────────────────────────────────────────────────
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# In production, import failures must crash the process immediately so broken
# deployments are never silently served.  In development (default), deferred
# stubs let developers run the web UI without heavy ML dependencies installed.
_IS_PRODUCTION = os.environ.get("HYPHAE_ENV", "development").lower() == "production"

# ── Hyphae core ──────────────────────────────────────────────────────────
try:
    from core.engine import generate_hybrid           # type: ignore
    from core.tools import ALL_TOOLS, execute_tool, LOCAL_ONLY_TOOLS, CLOUD_SAFE_TOOLS  # type: ignore
    from ingestion.corpus import add_file             # type: ignore
    from core.config import CORPUS_DIR                # type: ignore
except Exception as _e:
    if _IS_PRODUCTION:
        raise ImportError(f"Core imports failed in production: {_e}") from _e
    logging.getLogger(__name__).warning("Deferred Hyphae core imports: %s", _e)
    generate_hybrid = None
    ALL_TOOLS = []
    LOCAL_ONLY_TOOLS = set()
    CLOUD_SAFE_TOOLS = set()
    execute_tool = None
    add_file = None
    CORPUS_DIR = str(Path(__file__).parent.parent / "corpus")

# ── Notebook layer ───────────────────────────────────────────────────────
try:
    from notebook.db import init_db, get_conn                         # type: ignore
    from notebook.ingest import ingest_source, UPLOAD_DIR             # type: ignore
    from notebook.retrieval import hybrid_search, delete_notebook_index  # type: ignore
    from notebook.citations import build_citations, build_context_prompt, build_system_prompt  # type: ignore
    from notebook.sanitiser import sanitise_text                      # type: ignore
except Exception as _e:
    if _IS_PRODUCTION:
        raise ImportError(f"Notebook layer imports failed in production: {_e}") from _e
    logging.getLogger(__name__).warning("Deferred notebook-layer imports: %s", _e)
    def init_db(): return None
    def get_conn(): raise RuntimeError("DB not available")
    def ingest_source(src_id: str): raise RuntimeError("ingest not available")
    UPLOAD_DIR = Path(__file__).parent / "uploads"
    def hybrid_search(nb_id, q, qvec, top_k=6): return []
    def delete_notebook_index(nb_id): return None
    def build_citations(results): return []
    def build_context_prompt(results, max_chunks=6): return ""
    def build_system_prompt(context, notebook_name): return ""
    def sanitise_text(text): return text, []

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

_WEB_DIR = Path(__file__).parent

# ── Lifespan ──────────────────────────────────────────────────────────────

_SESSION_PURGE_INTERVAL = int(os.environ.get("SESSION_PURGE_INTERVAL", "3600"))


async def _session_purge_loop():
    """Background coroutine that purges expired sessions periodically."""
    from notebook.db import purge_expired_sessions
    while True:
        await asyncio.sleep(_SESSION_PURGE_INTERVAL)
        try:
            purge_expired_sessions()
        except Exception as exc:
            log.warning("Session purge failed: %s", exc)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_db()

    # Purge stale sessions left over from previous runs
    try:
        from notebook.db import purge_expired_sessions
        purge_expired_sessions()
    except Exception as exc:
        log.warning("Startup session purge failed: %s", exc)

    # Launch periodic purge as a background task
    purge_task = asyncio.create_task(_session_purge_loop())

    log.info("Hyphae started — DB initialised, session purge scheduled")
    try:
        yield
    finally:
        purge_task.cancel()
        try:
            await purge_task
        except asyncio.CancelledError:
            pass

# ── App + routers ─────────────────────────────────────────────────────────
app = FastAPI(title="Hyphae", version="2.0", lifespan=_lifespan)

from routes.notebooks import router as notebooks_router, configure as configure_notebooks
from routes.query import router as query_router, configure as configure_query
from routes.code import router as code_router
from routes.auth import router as auth_router
from routes.corpus import router as corpus_router, configure as configure_corpus
from routes.tags import router as tags_router
from routes.analytics import router as analytics_router
from routes.planning import router as planning_router
from routes.notes import router as notes_router, configure as configure_notes
from routes.collaboration import router as collaboration_router

app.include_router(notebooks_router)
app.include_router(query_router)
app.include_router(code_router)
app.include_router(auth_router)
app.include_router(corpus_router)
app.include_router(tags_router)
app.include_router(analytics_router)
app.include_router(planning_router)
app.include_router(notes_router)
app.include_router(collaboration_router)


# ── CORS ──────────────────────────────────────────────────────────────────
from fastapi.middleware.cors import CORSMiddleware

_CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "").split(",")
_CORS_ORIGINS = [o.strip() for o in _CORS_ORIGINS if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS or ["http://localhost:5000", "http://127.0.0.1:5000",
                                     "http://localhost:5001", "http://127.0.0.1:5001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Rate limiting ─────────────────────────────────────────────────────────
_GLOBAL_RPM = int(os.environ.get("RATE_LIMIT_RPM", "120"))
_AUTH_RPM = int(os.environ.get("RATE_LIMIT_AUTH_RPM", "10"))

from middleware import RequestLoggingMiddleware

if _GLOBAL_RPM > 0:
    from middleware import RateLimitMiddleware
    app.add_middleware(
        RateLimitMiddleware,
        global_rpm=_GLOBAL_RPM,
        strict_paths=["/api/auth/login", "/api/auth/signup"],
        strict_rpm=_AUTH_RPM,
    )

app.add_middleware(RequestLoggingMiddleware)

# ── No-cache middleware for static assets (dev convenience) ───────────────
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as _Req

class NoCacheStatic(BaseHTTPMiddleware):
    async def dispatch(self, request: _Req, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

app.add_middleware(NoCacheStatic)


# ── Gemini client factory ─────────────────────────────────────────────────

def _gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return None
    from google import genai
    return genai.Client(api_key=api_key)


# ── Wire dependencies into routers ───────────────────────────────────────

configure_query(
    hybrid_fn=generate_hybrid,
    all_tools=ALL_TOOLS,
    local_tools=LOCAL_ONLY_TOOLS,
    cloud_tools=CLOUD_SAFE_TOOLS,
    execute_fn=execute_tool,
    gemini_fn=_gemini_client,
)

configure_notebooks(
    conn_fn=get_conn,
    ingest_fn=ingest_source,
    upload_dir=UPLOAD_DIR,
    search_fn=hybrid_search,
    delete_idx_fn=delete_notebook_index,
    citations_fn=build_citations,
    context_fn=build_context_prompt,
    system_fn=build_system_prompt,
    sanitise_fn=sanitise_text,
    gemini_fn=_gemini_client,
)

configure_notes(gemini_fn=_gemini_client)

configure_corpus(corpus_dir=CORPUS_DIR, add_file_fn=add_file)


# ── Health / readiness probes ─────────────────────────────────────────────

@app.get("/health", tags=["ops"])
async def health():
    """Liveness probe — returns 200 if the process is running."""
    return {"status": "ok"}


@app.get("/ready", tags=["ops"])
async def ready():
    """Readiness probe — verifies DB and core dependencies are available."""
    checks: dict[str, str] = {}

    # DB connectivity
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1").fetchone()
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"fail: {exc}"

    # Core AI engine
    checks["engine"] = "ok" if generate_hybrid is not None else "unavailable"
    checks["tools"] = "ok" if execute_tool is not None else "unavailable"

    all_ok = checks["db"] == "ok"
    status_code = 200 if all_ok else 503
    return JSONResponse({"status": "ready" if all_ok else "not_ready", "checks": checks},
                        status_code=status_code)


# ── Static files + SPA ────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(str(_WEB_DIR / "static" / "index.html"),
                        headers={"Cache-Control": "no-store"})


@app.get("/style.css", include_in_schema=False)
async def css_alias():
    return FileResponse(str(_WEB_DIR / "static" / "style.css"),
                        headers={"Cache-Control": "no-store"})


@app.get("/app.js", include_in_schema=False)
async def js_alias():
    return FileResponse(str(_WEB_DIR / "static" / "app.js"),
                        headers={"Cache-Control": "no-store"})


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_alias():
    fav = _WEB_DIR / "static" / "favicon.ico"
    if fav.exists():
        return FileResponse(str(fav))
    raise HTTPException(404)
