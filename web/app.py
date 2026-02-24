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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# ── Hyphae core (guarded) ────────────────────────────────────────────────
try:
    from core.engine import generate_hybrid           # type: ignore
    from core.tools import ALL_TOOLS, execute_tool, LOCAL_ONLY_TOOLS, CLOUD_SAFE_TOOLS  # type: ignore
    from ingestion.corpus import add_file             # type: ignore
    from core.config import CORPUS_DIR                # type: ignore
except Exception as _e:
    logging.getLogger(__name__).warning("Deferred Hyphae core imports: %s", _e)
    generate_hybrid = None
    ALL_TOOLS = []
    LOCAL_ONLY_TOOLS = set()
    CLOUD_SAFE_TOOLS = set()
    execute_tool = None
    add_file = None
    CORPUS_DIR = str(Path(__file__).parent.parent / "corpus")

# ── Notebook layer (guarded) ─────────────────────────────────────────────
try:
    from notebook.db import init_db, get_conn                         # type: ignore
    from notebook.ingest import ingest_source, UPLOAD_DIR             # type: ignore
    from notebook.retrieval import hybrid_search, delete_notebook_index  # type: ignore
    from notebook.citations import build_citations, build_context_prompt, build_system_prompt  # type: ignore
    from notebook.sanitiser import sanitise_text                      # type: ignore
except Exception as _e:
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

@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_db()
    log.info("Hyphae started — DB initialised")
    yield

# ── App + routers ─────────────────────────────────────────────────────────
app = FastAPI(title="Hyphae", version="2.0", lifespan=_lifespan)

from routes.notebooks import router as notebooks_router, configure as configure_notebooks
from routes.query import router as query_router, configure as configure_query
from routes.code import router as code_router
from routes.auth import router as auth_router

app.include_router(notebooks_router)
app.include_router(query_router)
app.include_router(code_router)
app.include_router(auth_router)


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
