"""
Hyphae Web API — FastAPI backend.

Thin orchestrator that mounts modular routers and handles core
query/voice routing. Domain logic lives in:
    routes/corpus.py    — document upload, list, preview, sensitivity
    routes/notebooks.py — notebook CRUD, sources, conversations, chat, settings

Run:
    set -a && source .env && set +a
    ./.venv/bin/python -m uvicorn web.app:app --reload --port 5000
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Path setup ────────────────────────────────────────────────────────────
_WEB_DIR = Path(__file__).parent
_PROJECT_ROOT = _WEB_DIR.parent
_REPO_ROOT = _PROJECT_ROOT.parent

sys.path.insert(0, str(_REPO_ROOT / "cactus" / "python" / "src"))
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_WEB_DIR))
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

try:
    from dotenv import load_dotenv  # type: ignore
    _env_path = _REPO_ROOT / ".env"
    if _env_path.exists():
        load_dotenv(dotenv_path=str(_env_path))
        logging.getLogger(__name__).info("Loaded .env from %s", _env_path)
except Exception:
    pass

# ── FastAPI ───────────────────────────────────────────────────────────────
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ── Hyphae core (lazy imports guarded) ───────────────────────────────────
try:
    from main import generate_hybrid          # type: ignore
    from tools import ALL_TOOLS, execute_tool, LOCAL_ONLY_TOOLS, CLOUD_SAFE_TOOLS  # type: ignore
    from ingest import add_file               # type: ignore
    from config import CORPUS_DIR             # type: ignore
except Exception as _e:
    logging.getLogger(__name__).warning("Deferred Hyphae core imports: %s", _e)
    generate_hybrid = None
    ALL_TOOLS = []
    LOCAL_ONLY_TOOLS = set()
    CLOUD_SAFE_TOOLS = set()
    execute_tool = None
    add_file = None
    CORPUS_DIR = str(_PROJECT_ROOT / "assets")

# ── Notebook layer (guarded imports) ─────────────────────────────────────
try:
    from db import init_db, get_conn          # type: ignore
    from ingest_nb import ingest_source, UPLOAD_DIR  # type: ignore
    from retrieval import hybrid_search, delete_notebook_index  # type: ignore
    from citations import build_citations, build_context_prompt, build_system_prompt  # type: ignore
    from privacy import sanitise_text         # type: ignore
except Exception as _e:
    logging.getLogger(__name__).warning("Deferred notebook-layer imports: %s", _e)
    def init_db(): return None
    def get_conn(): raise RuntimeError("DB not available")
    def ingest_source(src_id: str): raise RuntimeError("ingest not available")
    UPLOAD_DIR = _WEB_DIR / "uploads"
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

# ── App + routers ─────────────────────────────────────────────────────────
app = FastAPI(title="Hyphae", version="2.0")

from routes.corpus import router as corpus_router, configure as configure_corpus
from routes.notebooks import router as notebooks_router, configure as configure_notebooks

configure_corpus(CORPUS_DIR, add_file)
app.include_router(corpus_router)
app.include_router(notebooks_router)


@app.on_event("startup")
def _startup():
    init_db()
    log.info("Hyphae started — DB initialised")


# ── Static files + SPA ────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(str(_WEB_DIR / "static" / "index.html"))


@app.get("/style.css", include_in_schema=False)
async def css_alias():
    return FileResponse(str(_WEB_DIR / "static" / "style.css"))


@app.get("/app.js", include_in_schema=False)
async def js_alias():
    return FileResponse(str(_WEB_DIR / "static" / "app.js"))


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_alias():
    fav = _WEB_DIR / "static" / "favicon.ico"
    if fav.exists():
        return FileResponse(str(fav))
    raise HTTPException(404)


# ═══════════════════════════════════════════════════════════════════════════
# Gemini client
# ═══════════════════════════════════════════════════════════════════════════

def _gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return None
    from google import genai
    return genai.Client(api_key=api_key)


# ═══════════════════════════════════════════════════════════════════════════
# Answer synthesis — local-first, cloud only when needed
# ═══════════════════════════════════════════════════════════════════════════

def _is_all_local(tool_results: list) -> bool:
    return all(tr["tool"] in LOCAL_ONLY_TOOLS for tr in tool_results)


def _format_local_answer(user_message: str, tool_results: list) -> str:
    parts = []
    for tr in tool_results:
        rd = tr["result"]
        if "error" in rd:
            parts.append(f"**{tr['tool']}** encountered an error: {rd['error']}")
            continue
        t = tr["tool"]
        if t == "search_papers":
            chunks = rd.get("results", [])
            if chunks:
                parts.append(f"Found **{len(chunks)}** relevant passage(s):")
                for i, c in enumerate(chunks[:5], 1):
                    src = c.get("source") or c.get("name") or ""
                    cite = f" [{src}]" if src else ""
                    parts.append(f"{i}. {c.get('text', '')[:400]}{cite}")
            else:
                parts.append("No matching passages found in your corpus.")
        elif t == "search_text":
            matches = rd.get("matches", [])
            if matches:
                parts.append(f"Found **{len(matches)}** text match(es):")
                for m in matches:
                    parts.append(f"- **{m.get('name', '')}**: {(m.get('paragraph') or m.get('snippet', ''))[:300]}")
            else:
                parts.append("No text matches found.")
        elif t == "summarise_notes":
            parts.append(rd.get("summary", "No summary available."))
        elif t == "create_note":
            parts.append(f"Note saved to `{rd.get('saved', 'corpus')}`.")
        elif t == "list_documents":
            docs = rd.get("documents", [])
            parts.append(f"**{len(docs)}** document(s) in your corpus:")
            for d in docs:
                parts.append(f"- {d['name']} ({d.get('size_kb', '?')} KB)")
        elif t == "read_document":
            name = rd.get("name", "document")
            trunc = " *(truncated)*" if rd.get("truncated") else ""
            parts.append(f"**{name}** ({rd.get('size_kb', '?')} KB){trunc}:\n{rd.get('content', '')[:800]}")
        elif t == "compare_documents":
            parts.append(rd.get("comparison", "No comparison available."))
        else:
            parts.append(f"**{t}**: {json.dumps(rd, indent=2)[:400]}")
    return "\n\n".join(parts)


def synthesise_cloud_answer(user_message: str, tool_results: list) -> Optional[str]:
    client = _gemini_client()
    if not client or not tool_results:
        return None

    results_text = ""
    for tr in tool_results:
        rd = tr["result"]
        if "error" in rd:
            results_text += f"\nTool {tr['tool']} failed: {rd['error']}\n"
            continue
        if tr["tool"] == "search_papers":
            chunks = rd.get("results", [])
            results_text += f"\n[search_papers found {len(chunks)} passages]\n"
            for c in chunks[:5]:
                results_text += f"- {c.get('text', '')[:300]}\n"
        elif tr["tool"] == "summarise_notes":
            results_text += f"\n[summary]\n{rd.get('summary', '')}\n"
        elif tr["tool"] == "create_note":
            results_text += f"\n[note saved to {rd.get('saved', '')}]\n"
        elif tr["tool"] == "list_documents":
            docs = rd.get("documents", [])
            results_text += f"\n[{len(docs)} documents in corpus]\n"
            for d in docs:
                results_text += f"- {d['name']} ({d.get('size_kb', '?')} KB)\n"
        elif tr["tool"] == "generate_hypothesis":
            results_text += f"\n[hypotheses]\n{rd.get('hypotheses', '')}\n"
        elif tr["tool"] == "search_literature":
            results_text += f"\n[literature]\n{rd.get('results', '')}\n"
        elif tr["tool"] == "compare_documents":
            results_text += f"\n[comparison]\n{rd.get('comparison', '')}\n"
        else:
            results_text += f"\n[{tr['tool']}]\n{json.dumps(rd, indent=2)[:500]}\n"

    prompt = (
        f'The user asked: "{user_message}"\n\n'
        f"The system executed tools and got these results:\n{results_text}\n\n"
        "Based on these results, write a helpful, concise answer to the user's question. "
        "Reference specific data from the results. Do not mention tool names or internal details. "
        "Write as a knowledgeable research assistant."
    )
    try:
        resp = client.models.generate_content(model="gemini-2.5-flash-lite", contents=[prompt])
        return resp.text
    except Exception as exc:
        log.warning("synthesise_cloud_answer failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Privacy classification + audit log
# ═══════════════════════════════════════════════════════════════════════════

_CLOUD_KEYWORDS = {
    "hypothesis", "hypotheses", "hypothesize", "hypothesise", "propose", "predict",
    "literature", "published", "papers", "citations", "cite", "prior",
}

_privacy_log: list = []


@app.post("/api/classify")
async def api_classify(body: dict):
    text = (body.get("message") or "").strip().lower()
    if not text:
        return {"route": "unknown"}
    words = set(text.split())
    needs_cloud = bool(words & _CLOUD_KEYWORDS)
    return {"route": "cloud" if needs_cloud else "local"}


@app.get("/api/privacy-log")
async def api_privacy_log():
    return {"entries": _privacy_log[-100:]}


def _log_privacy_event(query: str, tools_used: list, data_local: bool, routing_ms: float):
    _privacy_log.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "query": query[:120],
        "tools": [t["tool"] for t in tools_used],
        "data_local": data_local,
        "routing_ms": routing_ms,
    })


# ═══════════════════════════════════════════════════════════════════════════
# Tools list
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/tools")
async def api_tools():
    if not ALL_TOOLS:
        return {"tools": [], "count": 0}
    tools = []
    for t in ALL_TOOLS:
        name = t.get("name")
        param_obj = t.get("parameters", {}).get("properties", {}) or {}
        required = set(t.get("parameters", {}).get("required", []) or [])
        params = []
        for pname, pinfo in param_obj.items():
            params.append({
                "name": pname,
                "description": pinfo.get("description", ""),
                "type": pinfo.get("type", "string"),
                "required": pname in required,
            })
        source = "local" if name in LOCAL_ONLY_TOOLS else "cloud" if name in CLOUD_SAFE_TOOLS else "hybrid"
        tools.append({
            "name": name,
            "description": t.get("description", ""),
            "parameters": params,
            "source": source,
        })
    return {"tools": tools, "count": len(tools)}


# ═══════════════════════════════════════════════════════════════════════════
# Query endpoint (hybrid routing + tools + synthesis)
# ═══════════════════════════════════════════════════════════════════════════

@app.post("/api/query")
async def api_query(body: dict):
    user_message = (body.get("message") or "").strip()
    if not user_message:
        raise HTTPException(400, "message is required")
    if generate_hybrid is None or execute_tool is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Hyphae core is not available. Check server logs for import errors."},
        )

    messages = [{"role": "user", "content": user_message}]
    tools = body.get("tools") or list(ALL_TOOLS)

    t0 = time.time()
    result = generate_hybrid(messages, tools)
    routing_ms = round((time.time() - t0) * 1000, 1)

    tool_results = []
    for fc in result.get("function_calls", []):
        tr = execute_tool(fc["name"], fc.get("arguments", {}))
        tool_results.append({"tool": fc["name"], "arguments": fc.get("arguments", {}), "result": tr})

    all_local = _is_all_local(tool_results)
    if all_local:
        answer = _format_local_answer(user_message, tool_results)
        data_stayed_local = True
    else:
        answer = synthesise_cloud_answer(user_message, tool_results)
        data_stayed_local = False

    _log_privacy_event(user_message, tool_results, data_stayed_local, routing_ms)

    return {
        "source": result.get("source", "unknown"),
        "routing_ms": routing_ms,
        "function_calls": result.get("function_calls", []),
        "tool_results": tool_results,
        "answer": answer,
        "confidence": result.get("confidence"),
        "data_local": data_stayed_local,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Voice endpoint
# ═══════════════════════════════════════════════════════════════════════════

def _to_wav(input_path: str) -> str:
    if input_path.endswith(".wav"):
        return input_path
    wav_path = input_path.rsplit(".", 1)[0] + ".wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", wav_path],
            check=True, capture_output=True,
        )
        return wav_path
    except Exception as exc:
        log.warning("ffmpeg conversion failed: %s", exc)
        return input_path


@app.post("/api/voice")
async def api_voice(audio: UploadFile = File(...)):
    suffix = Path(audio.filename).suffix if audio.filename else ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await audio.read())
        tmp_path = tmp.name

    wav_path = _to_wav(tmp_path)
    cleanup = {tmp_path, wav_path}
    try:
        from voice import transcribe_file  # type: ignore
        transcript = transcribe_file(wav_path)
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={
                "error": f"Transcription failed: {exc}",
                "hint": "Install whisper weights and ensure ffmpeg is installed (brew install ffmpeg)."
            }
        )
    finally:
        for p in cleanup:
            try:
                os.unlink(p)
            except OSError:
                pass

    if not transcript.strip():
        return JSONResponse(status_code=400, content={"error": "Could not transcribe audio."})
    if generate_hybrid is None or execute_tool is None:
        return JSONResponse(status_code=503, content={"error": "Hyphae core is not available."})

    messages = [{"role": "user", "content": transcript}]
    t0 = time.time()
    result = generate_hybrid(messages, list(ALL_TOOLS))
    routing_ms = round((time.time() - t0) * 1000, 1)

    tool_results = []
    for fc in result.get("function_calls", []):
        tr = execute_tool(fc["name"], fc.get("arguments", {}))
        tool_results.append({"tool": fc["name"], "arguments": fc.get("arguments", {}), "result": tr})

    all_local = _is_all_local(tool_results)
    answer = _format_local_answer(transcript, tool_results) if all_local else synthesise_cloud_answer(transcript, tool_results)

    return {
        "transcript": transcript,
        "source": result.get("source", "unknown"),
        "routing_ms": routing_ms,
        "function_calls": result.get("function_calls", []),
        "tool_results": tool_results,
        "answer": answer,
    }


# Wire _gemini_client into notebooks router (must be after function definition)
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
