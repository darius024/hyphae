"""Notebook CRUD, sources, conversations, messages, and chat endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional

from core.config import GEMINI_MODEL
from routes.auth import get_current_user

router = APIRouter(prefix="/api", tags=["notebooks"])


# ── Request models ────────────────────────────────────────────────────

class _NotebookBody(BaseModel):
    name: str = Field("Untitled Notebook", min_length=1, max_length=200)

class _UrlBody(BaseModel):
    url: str = Field(..., min_length=1)
    title: Optional[str] = None

class _SensitivityBody(BaseModel):
    level: str = Field(..., pattern=r"^(confidential|shareable)$")

class _PaperBody(BaseModel):
    content: str = ""

class _TitleBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)

class _ChatBody(BaseModel):
    message: str = Field(..., min_length=1)

class _SettingBody(BaseModel):
    value: str

class _EventBody(BaseModel):
    title: str = Field(..., min_length=1)
    date: str = Field(..., min_length=1)
    end_date: Optional[str] = None
    type: str = "event"
    note: Optional[str] = None
log = logging.getLogger(__name__)

# Injected at startup from app.py
get_conn = None
ingest_source = None
UPLOAD_DIR: Path = Path("uploads")
hybrid_search = None
delete_notebook_index = None
build_citations = None
build_context_prompt = None
build_system_prompt = None
sanitise_text = None
_gemini_client = None


def configure(*, conn_fn, ingest_fn, upload_dir, search_fn, delete_idx_fn,
              citations_fn, context_fn, system_fn, sanitise_fn, gemini_fn):
    global get_conn, ingest_source, UPLOAD_DIR, hybrid_search
    global delete_notebook_index, build_citations, build_context_prompt
    global build_system_prompt, sanitise_text, _gemini_client
    get_conn = conn_fn
    ingest_source = ingest_fn
    UPLOAD_DIR = upload_dir
    hybrid_search = search_fn
    delete_notebook_index = delete_idx_fn
    build_citations = citations_fn
    build_context_prompt = context_fn
    build_system_prompt = system_fn
    sanitise_text = sanitise_fn
    _gemini_client = gemini_fn


# ── Helpers ────────────────────────────────────────────────────────────

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

_BAD_FILENAME_CHARS = re.compile(r'[\x00-\x1f\x7f/\\:]')
_RESERVED_NAMES = frozenset(
    [f"{p}{n}" for p in ("CON", "PRN", "AUX", "NUL", "COM", "LPT") for n in ("", *"123456789")]
)


def _safe_filename(name: str) -> str:
    """Validate a user-supplied filename against traversal, null bytes, and OS-reserved names."""
    if not name or ".." in name:
        raise HTTPException(400, "Invalid filename")
    clean = Path(name).name
    if not clean or clean != name:
        raise HTTPException(400, "Invalid filename")
    if _BAD_FILENAME_CHARS.search(clean):
        raise HTTPException(400, "Invalid filename")
    if clean.split(".")[0].upper() in _RESERVED_NAMES:
        raise HTTPException(400, "Invalid filename")
    return clean


def _nb_or_404(nb_id: str, user_id: str | None = None) -> dict:
    """Fetch a notebook or raise 404.  When *user_id* is given, also enforce
    ownership: raises 403 if the notebook is owned by a different user.
    Notebooks that pre-date ownership (user_id IS NULL) remain accessible.
    """
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM notebooks WHERE id=?", (nb_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"Notebook {nb_id} not found")
    nb = dict(row)
    if user_id is not None and nb.get("user_id") and nb["user_id"] != user_id:
        raise HTTPException(403, "Access denied")
    return nb


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


# ── Notebook CRUD ──────────────────────────────────────────────────────

@router.get("/notebooks")
async def list_notebooks(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), user: dict = Depends(get_current_user)):
    uid = user["id"]
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM notebooks WHERE user_id=? OR user_id IS NULL", (uid,)
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT n.*, COUNT(s.id) AS source_count FROM notebooks n "
            "LEFT JOIN sources s ON s.notebook_id=n.id "
            "WHERE n.user_id=? OR n.user_id IS NULL "
            "GROUP BY n.id ORDER BY n.updated_at DESC LIMIT ? OFFSET ?",
            (uid, limit, offset),
        ).fetchall()
    return {"notebooks": [dict(r) for r in rows], "total": total}


@router.post("/notebooks", status_code=201)
async def create_notebook(body: _NotebookBody, user: dict = Depends(get_current_user)):
    nb_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO notebooks (id, name, user_id) VALUES (?,?,?)",
            (nb_id, body.name.strip(), user["id"]),
        )
    return {"id": nb_id, "name": body.name.strip()}


@router.get("/notebooks/{nb_id}")
async def get_notebook(nb_id: str, user: dict = Depends(get_current_user)):
    return _nb_or_404(nb_id, user_id=user["id"])


@router.patch("/notebooks/{nb_id}")
async def update_notebook(nb_id: str, body: _NotebookBody, user: dict = Depends(get_current_user)):
    _nb_or_404(nb_id, user_id=user["id"])
    with get_conn() as conn:
        conn.execute(
            "UPDATE notebooks SET name=?, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
            (body.name.strip(), nb_id),
        )
    return _nb_or_404(nb_id, user_id=user["id"])


@router.delete("/notebooks/{nb_id}")
async def delete_notebook_endpoint(nb_id: str, user: dict = Depends(get_current_user)):
    _nb_or_404(nb_id, user_id=user["id"])
    with get_conn() as conn:
        conn.execute("DELETE FROM notebooks WHERE id=?", (nb_id,))
    upload_path = UPLOAD_DIR / nb_id
    if upload_path.exists():
        shutil.rmtree(upload_path, ignore_errors=True)
    try:
        delete_notebook_index(nb_id)
    except Exception:
        log.warning("Failed to delete index for notebook %s", nb_id, exc_info=True)
    return {"deleted": nb_id}


# ── Sources ────────────────────────────────────────────────────────────

@router.get("/notebooks/{nb_id}/sources")
async def list_sources(nb_id: str, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), user: dict = Depends(get_current_user)):
    _nb_or_404(nb_id, user_id=user["id"])
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM sources WHERE notebook_id=?", (nb_id,)).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM sources WHERE notebook_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (nb_id, limit, offset),
        ).fetchall()
    return {"sources": [dict(r) for r in rows], "total": total}


@router.post("/notebooks/{nb_id}/upload", status_code=202)
async def upload_source(nb_id: str, background_tasks: BackgroundTasks, file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    _nb_or_404(nb_id, user_id=user["id"])
    filename = _safe_filename(file.filename or "file")
    ext = Path(filename).suffix.lower().lstrip(".")
    src_type = ext if ext in ("pdf", "txt", "md") else "txt"

    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")

    dest_dir = UPLOAD_DIR / nb_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    dest.write_bytes(raw)

    src_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sources (id, notebook_id, type, filename, title, status) VALUES (?,?,?,?,?,?)",
            (src_id, nb_id, src_type, filename, Path(filename).stem, "pending"),
        )

    background_tasks.add_task(ingest_source, src_id)
    return {"source_id": src_id, "filename": filename, "status": "pending"}


@router.post("/notebooks/{nb_id}/add-url", status_code=202)
async def add_url_source(nb_id: str, background_tasks: BackgroundTasks, body: _UrlBody, user: dict = Depends(get_current_user)):
    _nb_or_404(nb_id, user_id=user["id"])
    url = body.url.strip()
    src_id = str(uuid.uuid4())
    title = body.title or url[:80]
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sources (id, notebook_id, type, url, title, status) VALUES (?,?,?,?,?,?)",
            (src_id, nb_id, "url", url, title, "pending"),
        )

    background_tasks.add_task(ingest_source, src_id)
    return {"source_id": src_id, "url": url, "status": "pending"}


@router.get("/notebooks/{nb_id}/sources/{src_id}")
async def get_source(nb_id: str, src_id: str, user: dict = Depends(get_current_user)):
    _nb_or_404(nb_id, user_id=user["id"])
    return _src_or_404(src_id, nb_id)


@router.delete("/notebooks/{nb_id}/sources/{src_id}")
async def delete_source(nb_id: str, src_id: str, user: dict = Depends(get_current_user)):
    _nb_or_404(nb_id, user_id=user["id"])
    _src_or_404(src_id, nb_id)
    with get_conn() as conn:
        conn.execute("DELETE FROM sources WHERE id=?", (src_id,))
    return {"deleted": src_id}


@router.put("/notebooks/{nb_id}/sources/{src_id}/sensitivity")
async def set_source_sensitivity(nb_id: str, src_id: str, body: _SensitivityBody, user: dict = Depends(get_current_user)):
    """Toggle confidential / shareable on a notebook source."""
    _nb_or_404(nb_id, user_id=user["id"])
    _src_or_404(src_id, nb_id)
    with get_conn() as conn:
        conn.execute("UPDATE sources SET sensitivity=? WHERE id=?", (body.level, src_id))
    return {"id": src_id, "sensitivity": body.level}


@router.get("/notebooks/{nb_id}/sources/{src_id}/raw")
async def raw_source(nb_id: str, src_id: str, user: dict = Depends(get_current_user)):
    """Return the raw file (PDF or text) for download / inline display."""
    from fastapi.responses import FileResponse as FR
    _nb_or_404(nb_id, user_id=user["id"])
    src = _src_or_404(src_id, nb_id)
    filename = src.get("filename")
    if not filename:
        raise HTTPException(404, "No file associated with this source")
    filename = _safe_filename(filename)
    file_path = UPLOAD_DIR / nb_id / filename
    if not file_path.exists():
        raise HTTPException(404, f"File not found: {filename}")
    suffix = file_path.suffix.lower()
    media = "application/pdf" if suffix == ".pdf" else "text/plain; charset=utf-8"
    return FR(
        str(file_path),
        media_type=media,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/notebooks/{nb_id}/chunks/{chunk_id}")
async def get_chunk(nb_id: str, chunk_id: str, user: dict = Depends(get_current_user)):
    """Return the full text and metadata for a single retrieved chunk.

    Used by the citation-preview popup in the UI: when a user clicks a
    [N] inline reference the browser fetches this endpoint and displays the
    chunk content alongside its source title and page number.
    """
    _nb_or_404(nb_id, user_id=user["id"])
    with get_conn() as conn:
        row = conn.execute(
            """SELECT c.id, c.source_id, c.page_number, c.chunk_index,
                      c.raw_text, c.clean_text, c.token_count,
                      s.title AS source_title, s.filename
               FROM chunks c
               LEFT JOIN sources s ON c.source_id = s.id
               WHERE c.id = ? AND c.notebook_id = ?""",
            (chunk_id, nb_id),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Chunk not found")
    return dict(row)


@router.get("/notebooks/{nb_id}/sources/{src_id}/preview")
async def preview_source(nb_id: str, src_id: str, user: dict = Depends(get_current_user)):
    """Return a text preview (first 3000 chars) of a notebook source file."""
    _nb_or_404(nb_id, user_id=user["id"])
    src = _src_or_404(src_id, nb_id)
    filename = src.get("filename")
    if not filename:
        raise HTTPException(404, "No file associated with this source")
    filename = _safe_filename(filename)
    file_path = UPLOAD_DIR / nb_id / filename
    if not file_path.exists():
        raise HTTPException(404, f"File not found: {filename}")
    try:
        text = file_path.read_text(errors="replace")[:3000]
    except Exception as exc:
        raise HTTPException(500, str(exc))
    return {
        "name": filename,
        "preview": text,
        "size_kb": round(file_path.stat().st_size / 1024, 1),
        "has_pdf": file_path.suffix.lower() == ".pdf",
    }


# ── Paper editor ───────────────────────────────────────────────────────────

@router.get("/notebooks/{nb_id}/paper")
async def get_paper(nb_id: str, user: dict = Depends(get_current_user)):
    """Retrieve the saved paper draft for this notebook."""
    _nb_or_404(nb_id, user_id=user["id"])
    paper_path = UPLOAD_DIR / nb_id / "_paper.html"
    content = paper_path.read_text(errors="replace") if paper_path.exists() else ""
    return {"notebook_id": nb_id, "content": content}


@router.post("/notebooks/{nb_id}/paper")
async def save_paper(nb_id: str, body: _PaperBody, user: dict = Depends(get_current_user)):
    """Persist the paper draft (HTML content from the editor)."""
    _nb_or_404(nb_id, user_id=user["id"])
    dest = UPLOAD_DIR / nb_id
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "_paper.html").write_text(body.content, encoding="utf-8")
    return {"saved": True}


# ── Conversations ──────────────────────────────────────────────────────

@router.get("/notebooks/{nb_id}/conversations")
async def list_conversations(nb_id: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), user: dict = Depends(get_current_user)):
    _nb_or_404(nb_id, user_id=user["id"])
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM conversations WHERE notebook_id=?", (nb_id,)
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM conversations WHERE notebook_id=? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (nb_id, limit, offset),
        ).fetchall()
    return {"conversations": [dict(r) for r in rows], "total": total}


@router.post("/notebooks/{nb_id}/conversations", status_code=201)
async def create_conversation(nb_id: str, body: _TitleBody, user: dict = Depends(get_current_user)):
    _nb_or_404(nb_id, user_id=user["id"])
    cid = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO conversations (id, notebook_id, title) VALUES (?,?,?)", (cid, nb_id, body.title.strip())
        )
    return {"id": cid, "notebook_id": nb_id, "title": body.title.strip()}


@router.patch("/notebooks/{nb_id}/conversations/{cid}")
async def rename_conversation(nb_id: str, cid: str, body: _TitleBody, user: dict = Depends(get_current_user)):
    _nb_or_404(nb_id, user_id=user["id"])
    _conv_or_404(cid, nb_id)
    with get_conn() as conn:
        conn.execute(
            "UPDATE conversations SET title=?, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
            (body.title.strip(), cid),
        )
    return {"id": cid, "title": body.title.strip()}


@router.delete("/notebooks/{nb_id}/conversations/{cid}")
async def delete_conversation(nb_id: str, cid: str, user: dict = Depends(get_current_user)):
    _nb_or_404(nb_id, user_id=user["id"])
    _conv_or_404(cid, nb_id)
    with get_conn() as conn:
        conn.execute("DELETE FROM conversations WHERE id=?", (cid,))
    return {"deleted": cid}


# ── Messages ───────────────────────────────────────────────────────────

@router.get("/notebooks/{nb_id}/conversations/{cid}/messages")
async def list_messages(nb_id: str, cid: str, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), user: dict = Depends(get_current_user)):
    _nb_or_404(nb_id, user_id=user["id"])
    _conv_or_404(cid, nb_id)
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id=?", (cid,)
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at ASC LIMIT ? OFFSET ?",
            (cid, limit, offset),
        ).fetchall()
    msgs = []
    for r in rows:
        d = dict(r)
        d["citations"] = json.loads(d.get("citations") or "[]")
        msgs.append(d)
    return {"messages": msgs, "total": total}


# ── Chat ───────────────────────────────────────────────────────────────

async def _nb_chat_core(nb_id: str, cid: str, question: str) -> dict:
    nb = _nb_or_404(nb_id)
    from notebook.embed import embed_one  # type: ignore
    qvec = embed_one(question)
    results = hybrid_search(nb_id, question, qvec, top_k=6)

    citations = build_citations(results)
    context = build_context_prompt(results, max_chunks=6)
    safe_context, _ = sanitise_text(context)
    system = build_system_prompt(safe_context, nb["name"])
    safe_q, _ = sanitise_text(question)

    client = _gemini_client()
    if client:
        from google.genai import types  # type: ignore
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[safe_q],
            config=types.GenerateContentConfig(system_instruction=system),
        )
        answer = resp.text or ""
    else:
        best = "\n".join([f"- {r['snippet']}" for r in results[:3]]) or "No context available."
        answer = f"(Offline mode) Using local context only. Notebook: {nb['name']}. Question: {question}\n\nContext:\n{best}"

    _persist_message(cid, nb_id, "user", question, [])
    _persist_message(cid, nb_id, "assistant", answer,
                     [c.model_dump() for c in citations])
    return {"answer": answer, "citations": [c.model_dump() for c in citations]}


@router.post("/notebooks/{nb_id}/conversations/{cid}/chat")
async def nb_chat(nb_id: str, cid: str, body: _ChatBody, user: dict = Depends(get_current_user)):
    _nb_or_404(nb_id, user_id=user["id"])
    _conv_or_404(cid, nb_id)
    return await _nb_chat_core(nb_id, cid, body.message.strip())


async def _stream_nb_chat(nb_id: str, cid: str, question: str) -> AsyncIterator[str]:
    nb = _nb_or_404(nb_id)
    from notebook.embed import embed_one  # type: ignore
    qvec = embed_one(question)
    results = hybrid_search(nb_id, question, qvec, top_k=6)

    citations = build_citations(results)
    context = build_context_prompt(results, max_chunks=6)
    system = build_system_prompt(context, nb["name"])
    safe_q, _ = sanitise_text(question)

    yield f"data: {json.dumps({'type': 'citations', 'citations': [c.model_dump() for c in citations]})}\n\n"

    client = _gemini_client()
    if client:
        from google.genai import types  # type: ignore
        full_answer = []
        try:
            for chunk in client.models.generate_content_stream(
                model=GEMINI_MODEL,
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

        _persist_message(cid, nb_id, "user", question, [])
        _persist_message(cid, nb_id, "assistant", "".join(full_answer),
                         [c.model_dump() for c in citations])
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
    else:
        best = "\n".join([f"- {r['snippet']}" for r in results[:3]]) or "No context available."
        fallback = f"(Offline mode) Using local context only. Notebook: {nb['name']}. Question: {question}\n\nContext:\n{best}"
        yield f"data: {json.dumps({'type': 'delta', 'text': fallback})}\n\n"
        _persist_message(cid, nb_id, "user", question, [])
        _persist_message(cid, nb_id, "assistant", fallback,
                         [c.model_dump() for c in citations])
        yield f"data: {json.dumps({'type': 'done'})}\n\n"


@router.post("/notebooks/{nb_id}/conversations/{cid}/chat/stream")
async def nb_chat_stream(nb_id: str, cid: str, body: _ChatBody, user: dict = Depends(get_current_user)):
    _nb_or_404(nb_id, user_id=user["id"])
    _conv_or_404(cid, nb_id)
    return StreamingResponse(
        _stream_nb_chat(nb_id, cid, body.message.strip()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Settings ───────────────────────────────────────────────────────────

@router.get("/nb-settings")
async def get_settings(_user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM nb_settings").fetchall()
    return {"settings": [dict(r) for r in rows]}


@router.patch("/nb-settings/{key}")
async def update_setting(key: str, body: _SettingBody, _user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO nb_settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')",
            (key, body.value),
        )
    return {"key": key, "value": body.value}


# ── Calendar events ────────────────────────────────────────────────────

@router.get("/notebooks/{nb_id}/events")
async def list_events(nb_id: str, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), user: dict = Depends(get_current_user)):
    _nb_or_404(nb_id, user_id=user["id"])
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM calendar_events WHERE notebook_id=?", (nb_id,)
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM calendar_events WHERE notebook_id=? ORDER BY date ASC LIMIT ? OFFSET ?",
            (nb_id, limit, offset),
        ).fetchall()
    return {"events": [dict(r) for r in rows], "total": total}


@router.post("/notebooks/{nb_id}/events")
async def create_event(nb_id: str, body: _EventBody, user: dict = Depends(get_current_user)):
    _nb_or_404(nb_id, user_id=user["id"])
    eid = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO calendar_events (id, notebook_id, title, date, end_date, type, note) "
            "VALUES (?,?,?,?,?,?,?)",
            (eid, nb_id, body.title.strip(), body.date.strip(),
             body.end_date, body.type, body.note),
        )
    return {"id": eid, "title": body.title.strip(), "date": body.date.strip()}


@router.delete("/notebooks/{nb_id}/events/{eid}")
async def delete_event(nb_id: str, eid: str, user: dict = Depends(get_current_user)):
    _nb_or_404(nb_id, user_id=user["id"])
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM calendar_events WHERE id=? AND notebook_id=?", (eid, nb_id)
        )
    return {"ok": True}
