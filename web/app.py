"""
Hyphae Web API — FastAPI backend.

Hyphae corpus endpoints (preserved):
    POST   /api/query                   — hybrid routing + tools + Gemini synthesis
    GET    /api/documents               — list corpus files
    POST   /api/upload                  — upload PDF/TXT to corpus
    GET    /api/documents/{name}        — preview a corpus document
    DELETE /api/documents/{name}        — remove a corpus document
    POST   /api/voice                   — transcribe audio then query

Notebook endpoints (new):
    GET    /api/notebooks                                   — list notebooks
    POST   /api/notebooks                                   — create notebook
    GET    /api/notebooks/{nb_id}                           — get notebook
    PATCH  /api/notebooks/{nb_id}                          — update notebook name
    DELETE /api/notebooks/{nb_id}                          — delete notebook + data
    GET    /api/notebooks/{nb_id}/sources                  — list sources
    POST   /api/notebooks/{nb_id}/upload                   — upload file → ingest
    POST   /api/notebooks/{nb_id}/add-url                  — add URL → ingest
    DELETE /api/notebooks/{nb_id}/sources/{src_id}         — remove source
    GET    /api/notebooks/{nb_id}/conversations            — list conversations
    POST   /api/notebooks/{nb_id}/conversations            — create conversation
    GET    /api/notebooks/{nb_id}/conversations/{cid}/messages
    POST   /api/notebooks/{nb_id}/conversations/{cid}/chat         — non-streaming
    POST   /api/notebooks/{nb_id}/conversations/{cid}/chat/stream  — SSE streaming
    GET    /api/nb-settings                                — list all settings
    PATCH  /api/nb-settings/{key}                         — update a setting

Run:
    set -a && source .env && set +a
    ./.venv/bin/python -m uvicorn web.app:app --reload --port 5000
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import AsyncIterator, List, Optional

# ── Path setup ────────────────────────────────────────────────────────────
_WEB_DIR      = Path(__file__).parent
# _PROJECT_ROOT points to the hyphae/ directory (the app package root).
_PROJECT_ROOT = _WEB_DIR.parent
# _REPO_ROOT is one level above hyphae/ and contains the main cactus sources.
_REPO_ROOT = _PROJECT_ROOT.parent

# Preferred sys.path order:
#   1) cactus python sources (so `import cactus` resolves to the actual bindings)
#   2) repo root (other top-level packages)
#   3) hyphae/ (app modules)
#   4) web/ (local notebook layer — place before hyphae/src to prefer web/privacy.py)
#   5) hyphae/src (legacy modules)
sys.path.insert(0, str(_REPO_ROOT / "cactus" / "python" / "src"))
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_WEB_DIR))
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

# Load .env from repo root if present so GEMINI_API_KEY and other secrets are
# available before importing heavy modules (e.g., main) that expect them.
try:
    from dotenv import load_dotenv  # type: ignore

    _env_path = _REPO_ROOT / ".env"
    if _env_path.exists():
        load_dotenv(dotenv_path=str(_env_path))
        logging.getLogger(__name__).info("Loaded .env from %s", _env_path)
except Exception:
    # If python-dotenv isn't installed or load fails, continue — caller can still
    # export env vars manually before starting the server.
    pass

# ── FastAPI ───────────────────────────────────────────────────────────────
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# ── Hyphae core (lazy imports guarded) ───────────────────────────────────
try:
    # These imports can be heavy or platform-specific; import when available.
    from main   import generate_hybrid          # type: ignore
    from tools  import ALL_TOOLS, execute_tool  # type: ignore
    from ingest import add_file, list_documents as list_corpus, remove_document  # type: ignore
    from config import CORPUS_DIR               # type: ignore
except Exception as _e:
    # Defer failures so the web UI can still load for static/demo purposes.
    logging.getLogger(__name__).warning("Deferred Hyphae core imports: %s", _e)
    generate_hybrid = None
    ALL_TOOLS = []
    execute_tool = None
    add_file = None
    list_corpus = None
    remove_document = None
    # fallback corpus dir to avoid crashing endpoints that inspect files
    CORPUS_DIR = str(_PROJECT_ROOT / "assets")

# ── Notebook layer (guarded imports)
try:
    from db        import init_db, get_conn     # type: ignore
    from ingest_nb import ingest_source, UPLOAD_DIR  # type: ignore
    from retrieval import hybrid_search, delete_notebook_index  # type: ignore
    from citations import build_citations, build_context_prompt, build_system_prompt  # type: ignore
    from privacy   import sanitise_text         # type: ignore
except Exception as _e:
    logging.getLogger(__name__).warning("Deferred notebook-layer imports: %s", _e)
    # Provide safe fallbacks so the UI can be served without full backend.
    def init_db():
        return None
    def get_conn():
        raise RuntimeError("DB not available")
    def ingest_source(src_id: str):
        raise RuntimeError("ingest not available")
    UPLOAD_DIR = _WEB_DIR / "uploads"
    def hybrid_search(nb_id, q, qvec, top_k=6):
        return []
    def delete_notebook_index(nb_id):
        return None
    def build_citations(results):
        return []
    def build_context_prompt(results, max_chunks=6):
        return ""
    def build_system_prompt(context, notebook_name):
        return ""
    def sanitise_text(text):
        return text, []

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

# ── App ───────────────────────────────────────────────────────────────────
app = FastAPI(title="Hyphae", version="2.0")


@app.on_event("startup")
def _startup():
    init_db()
    log.info("Hyphae started — DB initialised")


# Serve static SPA
app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")

# Compatibility aliases for static assets requested at root (e.g., /style.css)
from fastapi.responses import FileResponse

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

@app.get("/apple-touch-icon.png", include_in_schema=False)
async def apple_touch_icon_alias():
    icon = _WEB_DIR / "static" / "apple-touch-icon.png"
    if icon.exists():
        return FileResponse(str(icon))
    raise HTTPException(404)

@app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
async def apple_touch_icon_pre_alias():
    icon = _WEB_DIR / "static" / "apple-touch-icon-precomposed.png"
    if icon.exists():
        return FileResponse(str(icon))
    raise HTTPException(404)


@app.get("/", include_in_schema=False)
async def index():
    from fastapi.responses import FileResponse
    return FileResponse(str(_WEB_DIR / "static" / "index.html"))


# ═══════════════════════════════════════════════════════════════════════════
# Helper — Gemini synthesis (Gemini only, no OpenAI)
# ═══════════════════════════════════════════════════════════════════════════

def _gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return None
    from google import genai  # lazy import — avoids slow startup
    return genai.Client(api_key=api_key)


def synthesise_answer(user_message: str, tool_results: list) -> Optional[str]:
    """Generate a Gemini-based natural language answer from tool execution results."""
    client = _gemini_client()
    if not client or not tool_results:
        return None

    results_text = ""
    for tr in tool_results:
        result_data = tr["result"]
        if "error" in result_data:
            results_text += f"\nTool {tr['tool']} failed: {result_data['error']}\n"
            continue
        if tr["tool"] == "search_papers":
            chunks = result_data.get("results", [])
            results_text += f"\n[search_papers found {len(chunks)} passages]\n"
            for c in chunks[:5]:
                results_text += f"- {c.get('text', '')[:300]}\n"
        elif tr["tool"] == "summarise_notes":
            results_text += f"\n[summary]\n{result_data.get('summary', '')}\n"
        elif tr["tool"] == "create_note":
            results_text += f"\n[note saved to {result_data.get('saved', '')}]\n"
        elif tr["tool"] == "list_documents":
            docs = result_data.get("documents", [])
            results_text += f"\n[{len(docs)} documents in corpus]\n"
            for d in docs:
                results_text += f"- {d['name']} ({d.get('size_kb', '?')} KB)\n"
        elif tr["tool"] == "generate_hypothesis":
            results_text += f"\n[hypotheses]\n{result_data.get('hypotheses', '')}\n"
        elif tr["tool"] == "search_literature":
            results_text += f"\n[literature]\n{result_data.get('results', '')}\n"
        elif tr["tool"] == "compare_documents":
            results_text += f"\n[comparison]\n{result_data.get('comparison', '')}\n"
        else:
            results_text += f"\n[{tr['tool']}]\n{json.dumps(result_data, indent=2)[:500]}\n"

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
        log.warning("synthesise_answer failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Existing Hyphae corpus endpoints
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

    answer = synthesise_answer(user_message, tool_results)
    return {
        "source":         result.get("source", "unknown"),
        "routing_ms":     routing_ms,
        "function_calls": result.get("function_calls", []),
        "tool_results":   tool_results,
        "answer":         answer,
        "confidence":     result.get("confidence"),
    }


@app.get("/api/documents")
async def api_documents():
    corpus = Path(CORPUS_DIR)
    if not corpus.is_dir():
        return {"documents": [], "count": 0}
    docs = [
        {"name": f.name, "size_kb": round(f.stat().st_size / 1024, 1)}
        for f in sorted(corpus.iterdir())
        if f.is_file() and not f.name.startswith(".")
    ]
    return {"documents": docs, "count": len(docs)}


@app.post("/api/upload")
async def api_upload(file: List[UploadFile] = File(...)):
    results = []
    for f in file:
        if not f.filename:
            continue
        suffix = Path(f.filename).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await f.read())
            tmp_path = tmp.name
        try:
            success = add_file(tmp_path, dest_name=Path(f.filename).stem + ".txt")
            results.append({"filename": f.filename, "added": bool(success)})
        finally:
            os.unlink(tmp_path)
    return {"uploaded": results}


@app.get("/api/documents/{name}")
async def api_preview_document(name: str):
    path = Path(CORPUS_DIR) / name
    if not path.exists():
        raise HTTPException(404, f"Not found: {name}")
    try:
        text = path.read_text(errors="replace")[:2000]
    except Exception as exc:
        raise HTTPException(500, str(exc))
    return {"name": name, "preview": text, "size_kb": round(path.stat().st_size / 1024, 1)}


@app.delete("/api/documents/{name}")
async def api_remove_document(name: str):
    path = Path(CORPUS_DIR) / name
    if not path.exists():
        raise HTTPException(404, f"Not found: {name}")
    path.unlink()
    return {"removed": name}


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
        # Return a clear error so the frontend can surface guidance instead of a network failure.
        return JSONResponse(
            status_code=500,
            content={
                "error": f"Transcription failed: {exc}",
                "hint": "Install whisper weights: `cactus download openai/whisper-small` and ensure ffmpeg is installed (brew install ffmpeg)."
            }
        )
    finally:
        for p in cleanup:
            try:
                os.unlink(p)
            except OSError:
                pass

    if not transcript.strip():
        return JSONResponse(status_code=400, content={"error": "Could not transcribe audio. Try speaking louder or closer to the microphone."})
    if generate_hybrid is None or execute_tool is None:
        return JSONResponse(status_code=503, content={"error": "Hyphae core is not available. Check server logs for import errors."})

    messages = [{"role": "user", "content": transcript}]
    t0 = time.time()
    result = generate_hybrid(messages, list(ALL_TOOLS))
    routing_ms = round((time.time() - t0) * 1000, 1)

    tool_results = []
    for fc in result.get("function_calls", []):
        tr = execute_tool(fc["name"], fc.get("arguments", {}))
        tool_results.append({"tool": fc["name"], "arguments": fc.get("arguments", {}), "result": tr})

    answer = synthesise_answer(transcript, tool_results)
    return {
        "transcript":     transcript,
        "source":         result.get("source", "unknown"),
        "routing_ms":     routing_ms,
        "function_calls": result.get("function_calls", []),
        "tool_results":   tool_results,
        "answer":         answer,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Notebook helpers
# ═══════════════════════════════════════════════════════════════════════════

def _nb_or_404(nb_id: str) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM notebooks WHERE id=?", (nb_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"Notebook {nb_id} not found")
    return dict(row)


def _src_or_404(src_id: str, nb_id: str) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sources WHERE id=? AND notebook_id=?", (src_id, nb_id)
        ).fetchone()
    if row is None:
        raise HTTPException(404, f"Source {src_id} not found")
    return dict(row)


def _conv_or_404(conv_id: str, nb_id: str) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id=? AND notebook_id=?", (conv_id, nb_id)
        ).fetchone()
    if row is None:
        raise HTTPException(404, f"Conversation {conv_id} not found")
    return dict(row)


# ═══════════════════════════════════════════════════════════════════════════
# Notebook CRUD
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/notebooks")
async def list_notebooks():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT n.*, COUNT(s.id) AS source_count FROM notebooks n "
            "LEFT JOIN sources s ON s.notebook_id=n.id GROUP BY n.id ORDER BY n.updated_at DESC"
        ).fetchall()
    return {"notebooks": [dict(r) for r in rows]}


@app.post("/api/notebooks", status_code=201)
async def create_notebook(body: dict):
    name = (body.get("name") or "Untitled Notebook").strip()
    nb_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO notebooks (id, name) VALUES (?,?)", (nb_id, name)
        )
    return {"id": nb_id, "name": name}


@app.get("/api/notebooks/{nb_id}")
async def get_notebook(nb_id: str):
    return _nb_or_404(nb_id)


@app.patch("/api/notebooks/{nb_id}")
async def update_notebook(nb_id: str, body: dict):
    _nb_or_404(nb_id)
    name = (body.get("name") or "").strip()
    if name:
        with get_conn() as conn:
            conn.execute(
                "UPDATE notebooks SET name=?, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                (name, nb_id),
            )
    return _nb_or_404(nb_id)


@app.delete("/api/notebooks/{nb_id}")
async def delete_notebook(nb_id: str):
    _nb_or_404(nb_id)
    with get_conn() as conn:
        conn.execute("DELETE FROM notebooks WHERE id=?", (nb_id,))
    # clean up uploads and FAISS index
    upload_path = UPLOAD_DIR / nb_id
    if upload_path.exists():
        shutil.rmtree(upload_path, ignore_errors=True)
    try:
        delete_notebook_index(nb_id)
    except Exception:
        pass
    return {"deleted": nb_id}


# ═══════════════════════════════════════════════════════════════════════════
# Sources
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/notebooks/{nb_id}/sources")
async def list_sources(nb_id: str):
    _nb_or_404(nb_id)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sources WHERE notebook_id=? ORDER BY created_at DESC", (nb_id,)
        ).fetchall()
    return {"sources": [dict(r) for r in rows]}


@app.post("/api/notebooks/{nb_id}/upload", status_code=202)
async def upload_source(nb_id: str, background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    _nb_or_404(nb_id)
    filename = Path(file.filename).name if file.filename else "file"
    ext = Path(filename).suffix.lower().lstrip(".")
    src_type = ext if ext in ("pdf", "txt", "md") else "txt"

    dest_dir = UPLOAD_DIR / nb_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    dest.write_bytes(await file.read())

    src_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sources (id, notebook_id, type, filename, title, status) VALUES (?,?,?,?,?,?)",
            (src_id, nb_id, src_type, filename, Path(filename).stem, "pending"),
        )

    background_tasks.add_task(ingest_source, src_id)
    return {"source_id": src_id, "filename": filename, "status": "pending"}


@app.post("/api/notebooks/{nb_id}/add-url", status_code=202)
async def add_url_source(nb_id: str, background_tasks: BackgroundTasks, body: dict):
    _nb_or_404(nb_id)
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url is required")

    src_id = str(uuid.uuid4())
    title = body.get("title") or url[:80]
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sources (id, notebook_id, type, url, title, status) VALUES (?,?,?,?,?,?)",
            (src_id, nb_id, "url", url, title, "pending"),
        )

    background_tasks.add_task(ingest_source, src_id)
    return {"source_id": src_id, "url": url, "status": "pending"}


@app.get("/api/notebooks/{nb_id}/sources/{src_id}")
async def get_source(nb_id: str, src_id: str):
    return _src_or_404(src_id, nb_id)


@app.delete("/api/notebooks/{nb_id}/sources/{src_id}")
async def delete_source(nb_id: str, src_id: str):
    _src_or_404(src_id, nb_id)
    with get_conn() as conn:
        conn.execute("DELETE FROM sources WHERE id=?", (src_id,))
    return {"deleted": src_id}


# ═══════════════════════════════════════════════════════════════════════════
# Conversations & messages
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/notebooks/{nb_id}/conversations")
async def list_conversations(nb_id: str):
    _nb_or_404(nb_id)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM conversations WHERE notebook_id=? ORDER BY updated_at DESC", (nb_id,)
        ).fetchall()
    return {"conversations": [dict(r) for r in rows]}


@app.post("/api/notebooks/{nb_id}/conversations", status_code=201)
async def create_conversation(nb_id: str, body: dict):
    _nb_or_404(nb_id)
    title = (body.get("title") or "New Conversation").strip()
    cid = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO conversations (id, notebook_id, title) VALUES (?,?,?)", (cid, nb_id, title)
        )
    return {"id": cid, "notebook_id": nb_id, "title": title}


@app.get("/api/notebooks/{nb_id}/conversations/{cid}/messages")
async def list_messages(nb_id: str, cid: str):
    _conv_or_404(cid, nb_id)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at ASC", (cid,)
        ).fetchall()
    msgs = []
    for r in rows:
        d = dict(r)
        d["citations"] = json.loads(d.get("citations") or "[]")
        msgs.append(d)
    return {"messages": msgs}


def _persist_message(conv_id: str, nb_id: str, role: str, content: str, citations: list):
    mid = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (id, conversation_id, notebook_id, role, content, citations) VALUES (?,?,?,?,?,?)",
            (mid, conv_id, nb_id, role, content, json.dumps(citations)),
        )
        conn.execute(
            "UPDATE conversations SET updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
            (conv_id,),
        )
    return mid


async def _nb_chat_core(nb_id: str, cid: str, question: str) -> dict:
    """Shared logic: retrieve context → build prompt → call Gemini."""
    nb = _nb_or_404(nb_id)

    # embed question + retrieve
    from embed import embed_one  # type: ignore
    qvec = embed_one(question)
    results = hybrid_search(nb_id, question, qvec, top_k=6)

    citations = build_citations(results)
    context   = build_context_prompt(results, max_chunks=6)
    system    = build_system_prompt(context, nb["name"])

    # sanitise before cloud call
    safe_q, _ = sanitise_text(question)

    client = _gemini_client()
    if client:
        from google.genai import types  # type: ignore
        resp = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=[safe_q],
            config=types.GenerateContentConfig(system_instruction=system),
        )
        answer = resp.text or ""
    else:
        # Offline/local fallback: stitch a concise answer from retrieved snippets.
        best = "\n".join([f"- {r['snippet']}" for r in results[:3]]) or "No context available."
        answer = (
            "(Offline mode) Using local context only. "
            f"Notebook: {nb['name']}. Question: {question}\n\nContext:\n{best}"
        )

    # persist
    _persist_message(cid, nb_id, "user", question, [])
    _persist_message(cid, nb_id, "assistant", answer,
                     [c.model_dump() for c in citations])

    return {
        "answer":    answer,
        "citations": [c.model_dump() for c in citations],
    }


@app.post("/api/notebooks/{nb_id}/conversations/{cid}/chat")
async def nb_chat(nb_id: str, cid: str, body: dict):
    _conv_or_404(cid, nb_id)
    question = (body.get("message") or "").strip()
    if not question:
        raise HTTPException(400, "message is required")
    return await _nb_chat_core(nb_id, cid, question)


async def _stream_nb_chat(nb_id: str, cid: str, question: str) -> AsyncIterator[str]:
    """SSE generator for streaming Gemini response."""
    nb = _nb_or_404(nb_id)

    from embed import embed_one  # type: ignore
    qvec = embed_one(question)
    results = hybrid_search(nb_id, question, qvec, top_k=6)

    citations = build_citations(results)
    context   = build_context_prompt(results, max_chunks=6)
    system    = build_system_prompt(context, nb["name"])
    safe_q, _ = sanitise_text(question)

    # send citations first so the UI can render them
    yield f"data: {json.dumps({'type': 'citations', 'citations': [c.model_dump() for c in citations]})}\n\n"

    client = _gemini_client()
    if client:
        from google.genai import types  # type: ignore
        full_answer = []
        try:
            for chunk in client.models.generate_content_stream(
                model="gemini-2.5-flash-lite",
                contents=[safe_q],
                config=types.GenerateContentConfig(system_instruction=system),
            ):
                text = chunk.text or ""
                if text:
                    full_answer.append(text)
                    yield f"data: {json.dumps({'type': 'delta', 'text': text})}\n\n"
                    await asyncio.sleep(0)
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
            return

        # persist after full response
        _persist_message(cid, nb_id, "user", question, [])
        _persist_message(cid, nb_id, "assistant", "".join(full_answer),
                         [c.model_dump() for c in citations])
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
    else:
        # Offline fallback: stream a single delta built from local context.
        best = "\n".join([f"- {r['snippet']}" for r in results[:3]]) or "No context available."
        fallback = (
            "(Offline mode) Using local context only. "
            f"Notebook: {nb['name']}. Question: {question}\n\nContext:\n{best}"
        )
        yield f"data: {json.dumps({'type': 'delta', 'text': fallback})}\n\n"
        _persist_message(cid, nb_id, "user", question, [])
        _persist_message(cid, nb_id, "assistant", fallback,
                         [c.model_dump() for c in citations])
        yield f"data: {json.dumps({'type': 'done'})}\n\n"


@app.post("/api/notebooks/{nb_id}/conversations/{cid}/chat/stream")
async def nb_chat_stream(nb_id: str, cid: str, body: dict):
    _conv_or_404(cid, nb_id)
    question = (body.get("message") or "").strip()
    if not question:
        raise HTTPException(400, "message is required")
    return StreamingResponse(
        _stream_nb_chat(nb_id, cid, question),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ═══════════════════════════════════════════════════════════════════════════
# Settings
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/nb-settings")
async def get_settings():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM nb_settings").fetchall()
    return {"settings": [dict(r) for r in rows]}


@app.patch("/api/nb-settings/{key}")
async def update_setting(key: str, body: dict):
    value = body.get("value")
    if value is None:
        raise HTTPException(400, "value is required")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO nb_settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')",
            (key, str(value)),
        )
    return {"key": key, "value": str(value)}
