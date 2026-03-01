"""Notes with version history and AI writing assistant endpoints."""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.config import GEMINI_MODEL
from notebook.db import get_conn, safe_update
from routes.auth import get_current_user

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["notes"])

# Injected at app startup via configure()
_gemini_client_fn = None


def configure(*, gemini_fn):
    """Wire the Gemini client factory — called once from app.py."""
    global _gemini_client_fn
    _gemini_client_fn = gemini_fn


# ── Pydantic models ──────────────────────────────────────────────────────

class NoteCreate(BaseModel):
    title: str = Field(..., min_length=1)
    content: str = ""

class NoteUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None

class WritingAssistRequest(BaseModel):
    content: str = Field(..., min_length=1)
    action: str = Field(..., pattern=r"^(autocomplete|grammar|style|summarize|expand|simplify)$")
    context: Optional[str] = None


# ── Note CRUD ─────────────────────────────────────────────────────────────

@router.get("/notebooks/{nb_id}/notes")
async def list_notes(nb_id: str, _user: dict = Depends(get_current_user)):
    """List all notes in a notebook."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, title, created_at, updated_at FROM notes
            WHERE notebook_id = ? ORDER BY updated_at DESC
        """, (nb_id,)).fetchall()
    return {"notes": [dict(r) for r in rows]}


@router.post("/notebooks/{nb_id}/notes", status_code=201)
async def create_note(nb_id: str, body: NoteCreate, _user: dict = Depends(get_current_user)):
    """Create a new note."""
    note_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO notes (id, notebook_id, title, content) VALUES (?, ?, ?, ?)
        """, (note_id, nb_id, body.title, body.content))

        ver_id = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO note_versions (id, note_id, content, version_num) VALUES (?, ?, ?, 1)
        """, (ver_id, note_id, body.content))

    return {"id": note_id, "title": body.title}


@router.get("/notebooks/{nb_id}/notes/{note_id}")
async def get_note(nb_id: str, note_id: str, _user: dict = Depends(get_current_user)):
    """Get a note with its content."""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT * FROM notes WHERE id = ? AND notebook_id = ?
        """, (note_id, nb_id)).fetchone()
        if not row:
            raise HTTPException(404, "Note not found")
    return dict(row)


@router.patch("/notebooks/{nb_id}/notes/{note_id}")
async def update_note(nb_id: str, note_id: str, body: NoteUpdate, _user: dict = Depends(get_current_user)):
    """Update a note and create a new version."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT * FROM notes WHERE id = ? AND notebook_id = ?", (note_id, nb_id)
        ).fetchone()
        if not existing:
            raise HTTPException(404, "Note not found")

        fields = {}
        if body.title is not None:
            fields["title"] = body.title
        if body.content is not None:
            fields["content"] = body.content

            last_ver = conn.execute(
                "SELECT MAX(version_num) as max_ver FROM note_versions WHERE note_id=?",
                (note_id,),
            ).fetchone()
            new_ver = (last_ver["max_ver"] or 0) + 1
            ver_id = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO note_versions (id, note_id, content, version_num) VALUES (?, ?, ?, ?)
            """, (ver_id, note_id, body.content, new_ver))

        safe_update(conn, "notes", fields, "id", note_id)

    return {"id": note_id, "updated": True}


@router.delete("/notebooks/{nb_id}/notes/{note_id}")
async def delete_note(nb_id: str, note_id: str, _user: dict = Depends(get_current_user)):
    """Delete a note and all its versions."""
    with get_conn() as conn:
        conn.execute("DELETE FROM notes WHERE id = ? AND notebook_id = ?", (note_id, nb_id))
    return {"deleted": note_id}


# ── Version history ───────────────────────────────────────────────────────

@router.get("/notebooks/{nb_id}/notes/{note_id}/versions")
async def list_note_versions(nb_id: str, note_id: str, _user: dict = Depends(get_current_user)):
    """List all versions of a note."""
    with get_conn() as conn:
        note = conn.execute(
            "SELECT id FROM notes WHERE id = ? AND notebook_id = ?", (note_id, nb_id)
        ).fetchone()
        if not note:
            raise HTTPException(404, "Note not found")

        rows = conn.execute("""
            SELECT id, version_num, created_at, LENGTH(content) as content_length
            FROM note_versions WHERE note_id = ? ORDER BY version_num DESC
        """, (note_id,)).fetchall()

    return {"versions": [dict(r) for r in rows]}


@router.get("/notebooks/{nb_id}/notes/{note_id}/versions/{version_num}")
async def get_note_version(nb_id: str, note_id: str, version_num: int, _user: dict = Depends(get_current_user)):
    """Get a specific version of a note."""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT nv.* FROM note_versions nv
            JOIN notes n ON nv.note_id = n.id
            WHERE n.id = ? AND n.notebook_id = ? AND nv.version_num = ?
        """, (note_id, nb_id, version_num)).fetchone()
        if not row:
            raise HTTPException(404, "Version not found")
    return dict(row)


@router.post("/notebooks/{nb_id}/notes/{note_id}/restore/{version_num}")
async def restore_note_version(nb_id: str, note_id: str, version_num: int, _user: dict = Depends(get_current_user)):
    """Restore a note to a previous version (creates new version with old content)."""
    with get_conn() as conn:
        old_ver = conn.execute("""
            SELECT nv.content FROM note_versions nv
            JOIN notes n ON nv.note_id = n.id
            WHERE n.id = ? AND n.notebook_id = ? AND nv.version_num = ?
        """, (note_id, nb_id, version_num)).fetchone()
        if not old_ver:
            raise HTTPException(404, "Version not found")

        last_ver = conn.execute(
            "SELECT MAX(version_num) as max_ver FROM note_versions WHERE note_id=?",
            (note_id,),
        ).fetchone()
        new_ver = (last_ver["max_ver"] or 0) + 1

        ver_id = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO note_versions (id, note_id, content, version_num) VALUES (?, ?, ?, ?)
        """, (ver_id, note_id, old_ver["content"], new_ver))

        conn.execute("""
            UPDATE notes SET content=?, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id=?
        """, (old_ver["content"], note_id))

    return {"restored": True, "new_version": new_ver}


# ── AI writing assistant ─────────────────────────────────────────────────

_WRITING_PROMPTS = {
    "autocomplete": "Continue writing the following academic text naturally. Write 2-3 sentences:\n\n{content}",
    "grammar": "Fix any grammar, spelling, and punctuation errors in this text. Return only the corrected text:\n\n{content}",
    "style": "Improve the academic writing style of this text. Make it more formal, precise, and scholarly. Return only the improved text:\n\n{content}",
    "summarize": "Summarize this text concisely while keeping key points:\n\n{content}",
    "expand": "Expand this text with more detail and explanation while maintaining academic tone:\n\n{content}",
    "simplify": "Simplify this text to make it clearer and easier to understand:\n\n{content}",
}


@router.post("/writing/assist")
async def writing_assist(body: WritingAssistRequest, _user: dict = Depends(get_current_user)):
    """AI writing assistant — autocomplete, grammar, style, summarize, expand, simplify."""
    if not _gemini_client_fn:
        raise HTTPException(503, "AI assistant not configured")

    client = _gemini_client_fn()
    if not client:
        raise HTTPException(503, "Gemini API not available")

    prompt = _WRITING_PROMPTS[body.action].format(content=body.content)
    if body.context:
        prompt = f"Context from research documents:\n{body.context[:2000]}\n\n{prompt}"

    try:
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=[prompt])
        return {
            "action": body.action,
            "result": resp.text,
            "original_length": len(body.content),
            "result_length": len(resp.text),
        }
    except Exception as e:
        log.error("Writing assist failed: %s", e)
        raise HTTPException(500, f"AI request failed: {str(e)}")


@router.post("/writing/session")
async def save_writing_session(
    notebook_id: Optional[str] = None,
    note_id: Optional[str] = None,
    content: str = "",
    ai_suggestions: Optional[str] = None,
    _user: dict = Depends(get_current_user),
):
    """Save a writing session state."""
    session_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO writing_sessions (id, notebook_id, note_id, content, ai_suggestions)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, notebook_id, note_id, content, ai_suggestions))
    return {"id": session_id}


@router.get("/writing/session/{session_id}")
async def get_writing_session(session_id: str, _user: dict = Depends(get_current_user)):
    """Get a saved writing session."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM writing_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Session not found")
    return dict(row)
