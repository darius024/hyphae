"""
Extended features API endpoints.

Includes:
- Tags & categories for sources
- Document linking (knowledge graph)
- Usage analytics dashboard
- Deadlines & reminders
- Calendar sync (Google/Outlook)
- Note version history
- AI writing assistant
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Header, Query, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from notebook.db import get_conn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["features"])

# ══════════════════════════════════════════════════════════════════════════
# TAGS & CATEGORIES
# ══════════════════════════════════════════════════════════════════════════

class TagCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    color: str = Field(default="#6366f1", pattern=r"^#[0-9a-fA-F]{6}$")

class TagUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    color: Optional[str] = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")

class SourceTagBody(BaseModel):
    tag_ids: List[str]


@router.get("/tags")
async def list_tags():
    """List all available tags."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, color, created_at FROM tags ORDER BY name"
        ).fetchall()
    return {"tags": [dict(r) for r in rows]}


@router.post("/tags", status_code=201)
async def create_tag(body: TagCreate):
    """Create a new tag."""
    tag_id = str(uuid.uuid4())
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO tags (id, name, color) VALUES (?, ?, ?)",
                (tag_id, body.name.strip(), body.color)
            )
        except Exception:
            raise HTTPException(400, "Tag name already exists")
    return {"id": tag_id, "name": body.name.strip(), "color": body.color}


@router.patch("/tags/{tag_id}")
async def update_tag(tag_id: str, body: TagUpdate):
    """Update a tag's name or color."""
    with get_conn() as conn:
        existing = conn.execute("SELECT * FROM tags WHERE id=?", (tag_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "Tag not found")
        
        updates = []
        params = []
        if body.name is not None:
            updates.append("name=?")
            params.append(body.name.strip())
        if body.color is not None:
            updates.append("color=?")
            params.append(body.color)
        
        if updates:
            params.append(tag_id)
            conn.execute(f"UPDATE tags SET {', '.join(updates)} WHERE id=?", params)
    
    return {"id": tag_id, "updated": True}


@router.delete("/tags/{tag_id}")
async def delete_tag(tag_id: str):
    """Delete a tag."""
    with get_conn() as conn:
        conn.execute("DELETE FROM tags WHERE id=?", (tag_id,))
    return {"deleted": tag_id}


@router.get("/notebooks/{nb_id}/sources/{src_id}/tags")
async def get_source_tags(nb_id: str, src_id: str):
    """Get all tags for a source."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT t.id, t.name, t.color FROM tags t
            JOIN source_tags st ON st.tag_id = t.id
            WHERE st.source_id = ?
            ORDER BY t.name
        """, (src_id,)).fetchall()
    return {"tags": [dict(r) for r in rows]}


@router.put("/notebooks/{nb_id}/sources/{src_id}/tags")
async def set_source_tags(nb_id: str, src_id: str, body: SourceTagBody):
    """Set tags for a source (replaces existing)."""
    with get_conn() as conn:
        # Verify source exists
        src = conn.execute(
            "SELECT id FROM sources WHERE id=? AND notebook_id=?", (src_id, nb_id)
        ).fetchone()
        if not src:
            raise HTTPException(404, "Source not found")
        
        # Clear existing tags
        conn.execute("DELETE FROM source_tags WHERE source_id=?", (src_id,))
        
        # Add new tags
        for tag_id in body.tag_ids:
            try:
                conn.execute(
                    "INSERT INTO source_tags (source_id, tag_id) VALUES (?, ?)",
                    (src_id, tag_id)
                )
            except Exception:
                pass  # Skip invalid tag_ids
    
    return {"source_id": src_id, "tag_ids": body.tag_ids}


# ══════════════════════════════════════════════════════════════════════════
# DOCUMENT LINKING (KNOWLEDGE GRAPH)
# ══════════════════════════════════════════════════════════════════════════

class LinkCreate(BaseModel):
    target_id: str
    link_type: str = Field(default="related", pattern=r"^(related|cites|extends|contradicts|supports)$")
    note: Optional[str] = None

class LinkUpdate(BaseModel):
    link_type: Optional[str] = None
    note: Optional[str] = None


@router.get("/notebooks/{nb_id}/graph")
async def get_knowledge_graph(nb_id: str):
    """Get the knowledge graph for a notebook (nodes = sources, edges = links)."""
    with get_conn() as conn:
        # Get all sources as nodes
        sources = conn.execute("""
            SELECT id, title, filename, type, created_at FROM sources
            WHERE notebook_id = ?
        """, (nb_id,)).fetchall()
        
        # Get all links as edges
        links = conn.execute("""
            SELECT dl.id, dl.source_id, dl.target_id, dl.link_type, dl.note
            FROM document_links dl
            JOIN sources s1 ON dl.source_id = s1.id
            JOIN sources s2 ON dl.target_id = s2.id
            WHERE s1.notebook_id = ? OR s2.notebook_id = ?
        """, (nb_id, nb_id)).fetchall()
        
        # Get tags for coloring
        tags_by_source = {}
        tag_rows = conn.execute("""
            SELECT st.source_id, t.name, t.color FROM source_tags st
            JOIN tags t ON st.tag_id = t.id
            JOIN sources s ON st.source_id = s.id
            WHERE s.notebook_id = ?
        """, (nb_id,)).fetchall()
        for r in tag_rows:
            if r["source_id"] not in tags_by_source:
                tags_by_source[r["source_id"]] = []
            tags_by_source[r["source_id"]].append({"name": r["name"], "color": r["color"]})
    
    nodes = []
    for s in sources:
        nodes.append({
            "id": s["id"],
            "label": s["title"] or s["filename"] or "Untitled",
            "type": s["type"],
            "tags": tags_by_source.get(s["id"], [])
        })
    
    edges = [{"id": l["id"], "source": l["source_id"], "target": l["target_id"], 
              "type": l["link_type"], "note": l["note"]} for l in links]
    
    return {"nodes": nodes, "edges": edges}


@router.post("/notebooks/{nb_id}/sources/{src_id}/links", status_code=201)
async def create_document_link(nb_id: str, src_id: str, body: LinkCreate):
    """Create a link between two documents."""
    link_id = str(uuid.uuid4())
    with get_conn() as conn:
        # Verify both sources exist
        src = conn.execute("SELECT id FROM sources WHERE id=?", (src_id,)).fetchone()
        tgt = conn.execute("SELECT id FROM sources WHERE id=?", (body.target_id,)).fetchone()
        if not src or not tgt:
            raise HTTPException(404, "Source or target not found")
        if src_id == body.target_id:
            raise HTTPException(400, "Cannot link document to itself")
        
        try:
            conn.execute("""
                INSERT INTO document_links (id, source_id, target_id, link_type, note)
                VALUES (?, ?, ?, ?, ?)
            """, (link_id, src_id, body.target_id, body.link_type, body.note))
        except Exception:
            raise HTTPException(400, "Link already exists")
    
    return {"id": link_id, "source_id": src_id, "target_id": body.target_id, "link_type": body.link_type}


@router.delete("/notebooks/{nb_id}/links/{link_id}")
async def delete_document_link(nb_id: str, link_id: str):
    """Delete a document link."""
    with get_conn() as conn:
        conn.execute("DELETE FROM document_links WHERE id=?", (link_id,))
    return {"deleted": link_id}


# ══════════════════════════════════════════════════════════════════════════
# USAGE ANALYTICS
# ══════════════════════════════════════════════════════════════════════════

class UsageEvent(BaseModel):
    event_type: str = Field(..., pattern=r"^(query|tool_use|upload|chat|export)$")
    event_data: Optional[dict] = None
    route: Optional[str] = None
    tools_used: Optional[List[str]] = None
    latency_ms: Optional[float] = None


def log_usage_event(event_type: str, event_data: dict = None, route: str = None,
                    tools_used: List[str] = None, latency_ms: float = None, user_id: str = None):
    """Helper to log a usage event from anywhere in the app."""
    event_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO usage_events (id, user_id, event_type, event_data, route, tools_used, latency_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (event_id, user_id, event_type, json.dumps(event_data) if event_data else None,
              route, json.dumps(tools_used) if tools_used else None, latency_ms))
    return event_id


@router.post("/analytics/event")
async def record_usage_event(body: UsageEvent):
    """Record a usage event."""
    event_id = log_usage_event(
        event_type=body.event_type,
        event_data=body.event_data,
        route=body.route,
        tools_used=body.tools_used,
        latency_ms=body.latency_ms
    )
    return {"id": event_id}


@router.get("/analytics/dashboard")
async def get_analytics_dashboard(
    days: int = Query(default=30, ge=1, le=365)
):
    """Get analytics dashboard data."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    
    with get_conn() as conn:
        # Total events by type
        events_by_type = conn.execute("""
            SELECT event_type, COUNT(*) as count FROM usage_events
            WHERE created_at >= ? GROUP BY event_type
        """, (since,)).fetchall()
        
        # Events per day
        events_per_day = conn.execute("""
            SELECT date(created_at) as day, COUNT(*) as count FROM usage_events
            WHERE created_at >= ? GROUP BY day ORDER BY day
        """, (since,)).fetchall()
        
        # Route distribution (local vs cloud)
        route_dist = conn.execute("""
            SELECT route, COUNT(*) as count FROM usage_events
            WHERE created_at >= ? AND route IS NOT NULL GROUP BY route
        """, (since,)).fetchall()
        
        # Tool usage stats
        tool_usage = conn.execute("""
            SELECT tools_used FROM usage_events
            WHERE created_at >= ? AND tools_used IS NOT NULL
        """, (since,)).fetchall()
        
        # Aggregate tool counts
        tool_counts = {}
        for row in tool_usage:
            tools = json.loads(row["tools_used"]) if row["tools_used"] else []
            for t in tools:
                tool_counts[t] = tool_counts.get(t, 0) + 1
        
        # Average latency
        avg_latency = conn.execute("""
            SELECT AVG(latency_ms) as avg_ms FROM usage_events
            WHERE created_at >= ? AND latency_ms IS NOT NULL
        """, (since,)).fetchone()
        
        # Total queries
        total = conn.execute("""
            SELECT COUNT(*) as total FROM usage_events WHERE created_at >= ?
        """, (since,)).fetchone()
    
    return {
        "period_days": days,
        "total_events": total["total"] if total else 0,
        "events_by_type": {r["event_type"]: r["count"] for r in events_by_type},
        "events_per_day": [{"day": r["day"], "count": r["count"]} for r in events_per_day],
        "route_distribution": {r["route"]: r["count"] for r in route_dist},
        "tool_usage": tool_counts,
        "avg_latency_ms": round(avg_latency["avg_ms"], 2) if avg_latency and avg_latency["avg_ms"] else None
    }


# ══════════════════════════════════════════════════════════════════════════
# DEADLINES & REMINDERS
# ══════════════════════════════════════════════════════════════════════════

class DeadlineCreate(BaseModel):
    title: str = Field(..., min_length=1)
    due_date: str  # ISO date or datetime
    notebook_id: Optional[str] = None
    source_id: Optional[str] = None
    priority: str = Field(default="medium", pattern=r"^(low|medium|high|urgent)$")
    note: Optional[str] = None

class DeadlineUpdate(BaseModel):
    title: Optional[str] = None
    due_date: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = Field(None, pattern=r"^(pending|in_progress|completed|cancelled)$")
    note: Optional[str] = None

class ReminderCreate(BaseModel):
    deadline_id: str
    remind_at: str  # ISO datetime


@router.get("/deadlines")
async def list_deadlines(
    notebook_id: Optional[str] = None,
    status: Optional[str] = None,
    upcoming_days: int = Query(default=30, ge=1, le=365)
):
    """List deadlines, optionally filtered."""
    until = (datetime.now(timezone.utc) + timedelta(days=upcoming_days)).isoformat()
    
    query = "SELECT * FROM deadlines WHERE due_date <= ?"
    params = [until]
    
    if notebook_id:
        query += " AND notebook_id = ?"
        params.append(notebook_id)
    if status:
        query += " AND status = ?"
        params.append(status)
    
    query += " ORDER BY due_date ASC"
    
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    
    return {"deadlines": [dict(r) for r in rows]}


@router.post("/deadlines", status_code=201)
async def create_deadline(body: DeadlineCreate):
    """Create a new deadline."""
    dl_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO deadlines (id, notebook_id, source_id, title, due_date, priority, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (dl_id, body.notebook_id, body.source_id, body.title, body.due_date, body.priority, body.note))
    
    return {"id": dl_id, "title": body.title, "due_date": body.due_date}


@router.patch("/deadlines/{dl_id}")
async def update_deadline(dl_id: str, body: DeadlineUpdate):
    """Update a deadline."""
    with get_conn() as conn:
        existing = conn.execute("SELECT * FROM deadlines WHERE id=?", (dl_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "Deadline not found")
        
        updates = []
        params = []
        for field in ["title", "due_date", "priority", "status", "note"]:
            val = getattr(body, field, None)
            if val is not None:
                updates.append(f"{field}=?")
                params.append(val)
        
        if updates:
            params.append(dl_id)
            conn.execute(f"UPDATE deadlines SET {', '.join(updates)} WHERE id=?", params)
    
    return {"id": dl_id, "updated": True}


@router.delete("/deadlines/{dl_id}")
async def delete_deadline(dl_id: str):
    """Delete a deadline."""
    with get_conn() as conn:
        conn.execute("DELETE FROM deadlines WHERE id=?", (dl_id,))
    return {"deleted": dl_id}


@router.post("/reminders", status_code=201)
async def create_reminder(body: ReminderCreate):
    """Create a reminder for a deadline."""
    rem_id = str(uuid.uuid4())
    with get_conn() as conn:
        # Verify deadline exists
        dl = conn.execute("SELECT id FROM deadlines WHERE id=?", (body.deadline_id,)).fetchone()
        if not dl:
            raise HTTPException(404, "Deadline not found")
        
        conn.execute("""
            INSERT INTO reminders (id, deadline_id, remind_at)
            VALUES (?, ?, ?)
        """, (rem_id, body.deadline_id, body.remind_at))
    
    return {"id": rem_id, "deadline_id": body.deadline_id, "remind_at": body.remind_at}


@router.get("/reminders/pending")
async def get_pending_reminders():
    """Get all pending (unsent) reminders that are due."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT r.*, d.title as deadline_title, d.due_date
            FROM reminders r
            JOIN deadlines d ON r.deadline_id = d.id
            WHERE r.sent = 0 AND r.remind_at <= ?
            ORDER BY r.remind_at
        """, (now,)).fetchall()
    
    return {"reminders": [dict(r) for r in rows]}


# ══════════════════════════════════════════════════════════════════════════
# CALENDAR SYNC (Google/Outlook)
# ══════════════════════════════════════════════════════════════════════════

class CalendarConnect(BaseModel):
    provider: str = Field(..., pattern=r"^(google|outlook)$")
    access_token: str
    refresh_token: Optional[str] = None
    token_expiry: Optional[str] = None
    calendar_id: Optional[str] = None


@router.get("/calendar/connections")
async def list_calendar_connections(authorization: Optional[str] = Header(None)):
    """List user's calendar connections."""
    # In production, extract user_id from auth token
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, provider, calendar_id, last_sync, created_at FROM calendar_connections
        """).fetchall()
    return {"connections": [dict(r) for r in rows]}


@router.post("/calendar/connect", status_code=201)
async def connect_calendar(body: CalendarConnect, authorization: Optional[str] = Header(None)):
    """Connect a calendar provider (store OAuth tokens)."""
    conn_id = str(uuid.uuid4())
    # In production, get user_id from auth
    user_id = "system"  # placeholder
    
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO calendar_connections 
            (id, user_id, provider, access_token, refresh_token, token_expiry, calendar_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (conn_id, user_id, body.provider, body.access_token, 
              body.refresh_token, body.token_expiry, body.calendar_id))
    
    return {"id": conn_id, "provider": body.provider, "status": "connected"}


@router.post("/calendar/sync/{conn_id}")
async def sync_calendar(conn_id: str):
    """
    Sync events from connected calendar.
    
    Note: In production, this would use the Google/Microsoft Calendar APIs
    to fetch events and store them in calendar_events table.
    """
    with get_conn() as conn:
        connection = conn.execute(
            "SELECT * FROM calendar_connections WHERE id=?", (conn_id,)
        ).fetchone()
        if not connection:
            raise HTTPException(404, "Calendar connection not found")
        
        # Update last_sync timestamp
        conn.execute(
            "UPDATE calendar_connections SET last_sync=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
            (conn_id,)
        )
    
    # Placeholder: In production, call Google/Microsoft APIs here
    return {
        "id": conn_id,
        "status": "synced",
        "message": "Calendar sync initiated. Events will be imported shortly.",
        "note": "Full OAuth flow requires client credentials setup in .env"
    }


@router.delete("/calendar/disconnect/{conn_id}")
async def disconnect_calendar(conn_id: str):
    """Disconnect a calendar provider."""
    with get_conn() as conn:
        conn.execute("DELETE FROM calendar_connections WHERE id=?", (conn_id,))
    return {"disconnected": conn_id}


# ══════════════════════════════════════════════════════════════════════════
# NOTE VERSION HISTORY
# ══════════════════════════════════════════════════════════════════════════

class NoteCreate(BaseModel):
    title: str = Field(..., min_length=1)
    content: str = ""

class NoteUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None


@router.get("/notebooks/{nb_id}/notes")
async def list_notes(nb_id: str):
    """List all notes in a notebook."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, title, created_at, updated_at FROM notes
            WHERE notebook_id = ? ORDER BY updated_at DESC
        """, (nb_id,)).fetchall()
    return {"notes": [dict(r) for r in rows]}


@router.post("/notebooks/{nb_id}/notes", status_code=201)
async def create_note(nb_id: str, body: NoteCreate):
    """Create a new note."""
    note_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO notes (id, notebook_id, title, content) VALUES (?, ?, ?, ?)
        """, (note_id, nb_id, body.title, body.content))
        
        # Create initial version
        ver_id = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO note_versions (id, note_id, content, version_num) VALUES (?, ?, ?, 1)
        """, (ver_id, note_id, body.content))
    
    return {"id": note_id, "title": body.title}


@router.get("/notebooks/{nb_id}/notes/{note_id}")
async def get_note(nb_id: str, note_id: str):
    """Get a note with its content."""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT * FROM notes WHERE id = ? AND notebook_id = ?
        """, (note_id, nb_id)).fetchone()
        if not row:
            raise HTTPException(404, "Note not found")
    return dict(row)


@router.patch("/notebooks/{nb_id}/notes/{note_id}")
async def update_note(nb_id: str, note_id: str, body: NoteUpdate):
    """Update a note and create a new version."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT * FROM notes WHERE id = ? AND notebook_id = ?", (note_id, nb_id)
        ).fetchone()
        if not existing:
            raise HTTPException(404, "Note not found")
        
        updates = []
        params = []
        if body.title is not None:
            updates.append("title=?")
            params.append(body.title)
        if body.content is not None:
            updates.append("content=?")
            params.append(body.content)
            
            # Create new version
            last_ver = conn.execute(
                "SELECT MAX(version_num) as max_ver FROM note_versions WHERE note_id=?",
                (note_id,)
            ).fetchone()
            new_ver = (last_ver["max_ver"] or 0) + 1
            ver_id = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO note_versions (id, note_id, content, version_num) VALUES (?, ?, ?, ?)
            """, (ver_id, note_id, body.content, new_ver))
        
        if updates:
            updates.append("updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')")
            params.append(note_id)
            conn.execute(f"UPDATE notes SET {', '.join(updates)} WHERE id=?", params)
    
    return {"id": note_id, "updated": True}


@router.delete("/notebooks/{nb_id}/notes/{note_id}")
async def delete_note(nb_id: str, note_id: str):
    """Delete a note and all its versions."""
    with get_conn() as conn:
        conn.execute("DELETE FROM notes WHERE id = ? AND notebook_id = ?", (note_id, nb_id))
    return {"deleted": note_id}


@router.get("/notebooks/{nb_id}/notes/{note_id}/versions")
async def list_note_versions(nb_id: str, note_id: str):
    """List all versions of a note."""
    with get_conn() as conn:
        # Verify note exists
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
async def get_note_version(nb_id: str, note_id: str, version_num: int):
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
async def restore_note_version(nb_id: str, note_id: str, version_num: int):
    """Restore a note to a previous version (creates new version with old content)."""
    with get_conn() as conn:
        # Get the version to restore
        old_ver = conn.execute("""
            SELECT nv.content FROM note_versions nv
            JOIN notes n ON nv.note_id = n.id
            WHERE n.id = ? AND n.notebook_id = ? AND nv.version_num = ?
        """, (note_id, nb_id, version_num)).fetchone()
        if not old_ver:
            raise HTTPException(404, "Version not found")
        
        # Get current max version
        last_ver = conn.execute(
            "SELECT MAX(version_num) as max_ver FROM note_versions WHERE note_id=?",
            (note_id,)
        ).fetchone()
        new_ver = (last_ver["max_ver"] or 0) + 1
        
        # Create new version with restored content
        ver_id = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO note_versions (id, note_id, content, version_num) VALUES (?, ?, ?, ?)
        """, (ver_id, note_id, old_ver["content"], new_ver))
        
        # Update note
        conn.execute("""
            UPDATE notes SET content=?, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id=?
        """, (old_ver["content"], note_id))
    
    return {"restored": True, "new_version": new_ver}


# ══════════════════════════════════════════════════════════════════════════
# AI WRITING ASSISTANT
# ══════════════════════════════════════════════════════════════════════════

class WritingAssistRequest(BaseModel):
    content: str = Field(..., min_length=1)
    action: str = Field(..., pattern=r"^(autocomplete|grammar|style|summarize|expand|simplify)$")
    context: Optional[str] = None  # Additional context from notebook sources

# Injected at app startup
_gemini_client_fn = None

def configure_features(gemini_fn):
    global _gemini_client_fn
    _gemini_client_fn = gemini_fn


@router.post("/writing/assist")
async def writing_assist(body: WritingAssistRequest):
    """
    AI writing assistant endpoint.
    
    Actions:
    - autocomplete: Continue the text
    - grammar: Fix grammar and spelling
    - style: Improve academic writing style
    - summarize: Condense the content
    - expand: Add more detail
    - simplify: Make it clearer and simpler
    """
    if not _gemini_client_fn:
        raise HTTPException(503, "AI assistant not configured")
    
    client = _gemini_client_fn()
    if not client:
        raise HTTPException(503, "Gemini API not available")
    
    prompts = {
        "autocomplete": f"Continue writing the following academic text naturally. Write 2-3 sentences:\n\n{body.content}",
        "grammar": f"Fix any grammar, spelling, and punctuation errors in this text. Return only the corrected text:\n\n{body.content}",
        "style": f"Improve the academic writing style of this text. Make it more formal, precise, and scholarly. Return only the improved text:\n\n{body.content}",
        "summarize": f"Summarize this text concisely while keeping key points:\n\n{body.content}",
        "expand": f"Expand this text with more detail and explanation while maintaining academic tone:\n\n{body.content}",
        "simplify": f"Simplify this text to make it clearer and easier to understand:\n\n{body.content}"
    }
    
    prompt = prompts[body.action]
    if body.context:
        prompt = f"Context from research documents:\n{body.context[:2000]}\n\n{prompt}"
    
    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=[prompt]
        )
        return {
            "action": body.action,
            "result": resp.text,
            "original_length": len(body.content),
            "result_length": len(resp.text)
        }
    except Exception as e:
        log.error("Writing assist failed: %s", e)
        raise HTTPException(500, f"AI request failed: {str(e)}")


@router.post("/writing/session")
async def save_writing_session(
    notebook_id: Optional[str] = None,
    note_id: Optional[str] = None,
    content: str = "",
    ai_suggestions: Optional[str] = None
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
async def get_writing_session(session_id: str):
    """Get a saved writing session."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM writing_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Session not found")
    return dict(row)


# ══════════════════════════════════════════════════════════════════════════
# ORGANIZATIONS
# ══════════════════════════════════════════════════════════════════════════

class OrgCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=2, max_length=50, pattern=r"^[a-z0-9-]+$")
    description: Optional[str] = None

class OrgUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    avatar_url: Optional[str] = None

class OrgInvite(BaseModel):
    email: str
    role: str = Field(default="member", pattern=r"^(admin|member|viewer)$")


@router.get("/organizations")
async def list_user_organizations(x_user_id: Optional[str] = Header(default=None)):
    """List organizations the current user belongs to."""
    if not x_user_id:
        return {"organizations": []}
    
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT o.*, om.role as user_role,
                   (SELECT COUNT(*) FROM org_members WHERE org_id=o.id) as member_count,
                   (SELECT COUNT(*) FROM notebooks WHERE org_id=o.id) as notebook_count
            FROM organizations o
            JOIN org_members om ON o.id = om.org_id
            WHERE om.user_id = ?
            ORDER BY o.name
        """, (x_user_id,)).fetchall()
    
    return {"organizations": [dict(r) for r in rows]}


@router.post("/organizations", status_code=201)
async def create_organization(body: OrgCreate, x_user_id: Optional[str] = Header(default=None)):
    """Create a new organization."""
    if not x_user_id:
        raise HTTPException(401, "Authentication required")
    
    org_id = str(uuid.uuid4())
    with get_conn() as conn:
        # Check slug uniqueness
        existing = conn.execute("SELECT id FROM organizations WHERE slug=?", (body.slug,)).fetchone()
        if existing:
            raise HTTPException(400, "Organization slug already exists")
        
        # Create org
        conn.execute("""
            INSERT INTO organizations (id, name, slug, description, owner_id)
            VALUES (?, ?, ?, ?, ?)
        """, (org_id, body.name, body.slug.lower(), body.description, x_user_id))
        
        # Add owner as member with owner role
        member_id = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO org_members (id, org_id, user_id, role)
            VALUES (?, ?, ?, 'owner')
        """, (member_id, org_id, x_user_id))
    
    return {"id": org_id, "slug": body.slug.lower()}


@router.get("/organizations/{org_id}")
async def get_organization(org_id: str, x_user_id: Optional[str] = Header(default=None)):
    """Get organization details."""
    with get_conn() as conn:
        org = conn.execute("SELECT * FROM organizations WHERE id=?", (org_id,)).fetchone()
        if not org:
            raise HTTPException(404, "Organization not found")
        
        # Check membership
        if x_user_id:
            member = conn.execute(
                "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
                (org_id, x_user_id)
            ).fetchone()
        else:
            member = None
        
        # Get members
        members = conn.execute("""
            SELECT om.*, u.name, u.email, u.avatar_url
            FROM org_members om
            JOIN users u ON om.user_id = u.id
            WHERE om.org_id = ?
            ORDER BY om.role, u.name
        """, (org_id,)).fetchall()
        
        # Get notebooks
        notebooks = conn.execute("""
            SELECT id, name, description, created_at FROM notebooks
            WHERE org_id = ? ORDER BY updated_at DESC
        """, (org_id,)).fetchall()
    
    return {
        **dict(org),
        "user_role": member["role"] if member else None,
        "members": [dict(m) for m in members],
        "notebooks": [dict(n) for n in notebooks]
    }


@router.patch("/organizations/{org_id}")
async def update_organization(org_id: str, body: OrgUpdate, x_user_id: Optional[str] = Header(default=None)):
    """Update organization details (admin/owner only)."""
    if not x_user_id:
        raise HTTPException(401, "Authentication required")
    
    with get_conn() as conn:
        # Check permission
        member = conn.execute(
            "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, x_user_id)
        ).fetchone()
        if not member or member["role"] not in ("owner", "admin"):
            raise HTTPException(403, "Admin access required")
        
        updates = []
        params = []
        if body.name is not None:
            updates.append("name=?")
            params.append(body.name)
        if body.description is not None:
            updates.append("description=?")
            params.append(body.description)
        if body.avatar_url is not None:
            updates.append("avatar_url=?")
            params.append(body.avatar_url)
        
        if updates:
            updates.append("updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')")
            params.append(org_id)
            conn.execute(f"UPDATE organizations SET {', '.join(updates)} WHERE id=?", params)
    
    return {"updated": True}


@router.delete("/organizations/{org_id}")
async def delete_organization(org_id: str, x_user_id: Optional[str] = Header(default=None)):
    """Delete organization (owner only)."""
    if not x_user_id:
        raise HTTPException(401, "Authentication required")
    
    with get_conn() as conn:
        org = conn.execute("SELECT owner_id FROM organizations WHERE id=?", (org_id,)).fetchone()
        if not org:
            raise HTTPException(404, "Organization not found")
        if org["owner_id"] != x_user_id:
            raise HTTPException(403, "Only owner can delete organization")
        
        conn.execute("DELETE FROM organizations WHERE id=?", (org_id,))
    
    return {"deleted": org_id}


@router.get("/organizations/{org_id}/members")
async def list_org_members(org_id: str):
    """List organization members."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT om.*, u.name, u.email, u.avatar_url
            FROM org_members om
            JOIN users u ON om.user_id = u.id
            WHERE om.org_id = ?
            ORDER BY om.role DESC, u.name
        """, (org_id,)).fetchall()
    return {"members": [dict(r) for r in rows]}


@router.post("/organizations/{org_id}/invite", status_code=201)
async def invite_to_org(org_id: str, body: OrgInvite, x_user_id: Optional[str] = Header(default=None)):
    """Invite a user to organization by email."""
    if not x_user_id:
        raise HTTPException(401, "Authentication required")
    
    with get_conn() as conn:
        # Check permission (admin/owner)
        member = conn.execute(
            "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, x_user_id)
        ).fetchone()
        if not member or member["role"] not in ("owner", "admin"):
            raise HTTPException(403, "Admin access required")
        
        # Check if already member
        existing = conn.execute("""
            SELECT om.id FROM org_members om
            JOIN users u ON om.user_id = u.id
            WHERE om.org_id=? AND u.email=?
        """, (org_id, body.email)).fetchone()
        if existing:
            raise HTTPException(400, "User is already a member")
        
        # Check pending invite
        pending = conn.execute(
            "SELECT id FROM org_invites WHERE org_id=? AND email=? AND accepted=0",
            (org_id, body.email)
        ).fetchone()
        if pending:
            raise HTTPException(400, "Invite already pending")
        
        # Create invite
        invite_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        
        conn.execute("""
            INSERT INTO org_invites (id, org_id, email, role, token, invited_by, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (invite_id, org_id, body.email, body.role, token, x_user_id, expires))
    
    return {"invite_id": invite_id, "token": token}


@router.post("/organizations/accept-invite/{token}")
async def accept_org_invite(token: str, x_user_id: Optional[str] = Header(default=None)):
    """Accept an organization invite."""
    if not x_user_id:
        raise HTTPException(401, "Authentication required")
    
    with get_conn() as conn:
        invite = conn.execute("""
            SELECT * FROM org_invites WHERE token=? AND accepted=0
        """, (token,)).fetchone()
        
        if not invite:
            raise HTTPException(404, "Invalid or expired invite")
        
        if datetime.fromisoformat(invite["expires_at"].replace("Z", "+00:00")) < datetime.now(timezone.utc):
            raise HTTPException(400, "Invite has expired")
        
        # Get user email
        user = conn.execute("SELECT email FROM users WHERE id=?", (x_user_id,)).fetchone()
        if not user or user["email"].lower() != invite["email"].lower():
            raise HTTPException(400, "This invite was sent to a different email")
        
        # Add as member
        member_id = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO org_members (id, org_id, user_id, role)
            VALUES (?, ?, ?, ?)
        """, (member_id, invite["org_id"], x_user_id, invite["role"]))
        
        # Mark invite as accepted
        conn.execute("UPDATE org_invites SET accepted=1 WHERE id=?", (invite["id"],))
    
    return {"joined": invite["org_id"]}


@router.delete("/organizations/{org_id}/members/{user_id}")
async def remove_org_member(org_id: str, user_id: str, x_user_id: Optional[str] = Header(default=None)):
    """Remove a member from organization."""
    if not x_user_id:
        raise HTTPException(401, "Authentication required")
    
    with get_conn() as conn:
        # Check permission
        actor = conn.execute(
            "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, x_user_id)
        ).fetchone()
        
        if not actor:
            raise HTTPException(403, "Not a member of this organization")
        
        # User can remove themselves
        if user_id != x_user_id:
            # Only admin/owner can remove others
            if actor["role"] not in ("owner", "admin"):
                raise HTTPException(403, "Admin access required")
        
        # Can't remove owner
        target = conn.execute(
            "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, user_id)
        ).fetchone()
        if target and target["role"] == "owner":
            raise HTTPException(400, "Cannot remove organization owner")
        
        conn.execute(
            "DELETE FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, user_id)
        )
    
    return {"removed": user_id}


@router.patch("/organizations/{org_id}/members/{user_id}/role")
async def update_member_role(
    org_id: str, user_id: str,
    role: str = Query(..., pattern=r"^(admin|member|viewer)$"),
    x_user_id: Optional[str] = Header(default=None)
):
    """Update a member's role (admin/owner only)."""
    if not x_user_id:
        raise HTTPException(401, "Authentication required")
    
    with get_conn() as conn:
        # Check permission (only owner can change roles)
        org = conn.execute("SELECT owner_id FROM organizations WHERE id=?", (org_id,)).fetchone()
        if not org:
            raise HTTPException(404, "Organization not found")
        
        actor = conn.execute(
            "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, x_user_id)
        ).fetchone()
        if not actor or actor["role"] not in ("owner", "admin"):
            raise HTTPException(403, "Admin access required")
        
        # Can't change owner role
        target = conn.execute(
            "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, user_id)
        ).fetchone()
        if target and target["role"] == "owner":
            raise HTTPException(400, "Cannot change owner role")
        
        conn.execute(
            "UPDATE org_members SET role=? WHERE org_id=? AND user_id=?",
            (role, org_id, user_id)
        )
    
    return {"updated": True}


# ══════════════════════════════════════════════════════════════════════════
# COMMENTS
# ══════════════════════════════════════════════════════════════════════════

class CommentCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)
    notebook_id: Optional[str] = None
    source_id: Optional[str] = None
    note_id: Optional[str] = None
    conversation_id: Optional[str] = None
    parent_id: Optional[str] = None  # For replies

class CommentUpdate(BaseModel):
    content: Optional[str] = Field(None, min_length=1, max_length=5000)
    resolved: Optional[bool] = None


@router.get("/comments")
async def list_comments(
    notebook_id: Optional[str] = None,
    source_id: Optional[str] = None,
    note_id: Optional[str] = None
):
    """List comments for a specific target."""
    with get_conn() as conn:
        conditions = []
        params = []
        
        if notebook_id:
            conditions.append("c.notebook_id = ?")
            params.append(notebook_id)
        if source_id:
            conditions.append("c.source_id = ?")
            params.append(source_id)
        if note_id:
            conditions.append("c.note_id = ?")
            params.append(note_id)
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        rows = conn.execute(f"""
            SELECT c.*, u.name as user_name, u.avatar_url as user_avatar,
                   (SELECT COUNT(*) FROM comments WHERE parent_id = c.id) as reply_count
            FROM comments c
            LEFT JOIN users u ON c.user_id = u.id
            WHERE {where_clause} AND c.parent_id IS NULL
            ORDER BY c.created_at DESC
        """, params).fetchall()
    
    return {"comments": [dict(r) for r in rows]}


@router.get("/comments/{comment_id}/replies")
async def get_comment_replies(comment_id: str):
    """Get replies to a comment."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT c.*, u.name as user_name, u.avatar_url as user_avatar
            FROM comments c
            LEFT JOIN users u ON c.user_id = u.id
            WHERE c.parent_id = ?
            ORDER BY c.created_at ASC
        """, (comment_id,)).fetchall()
    return {"replies": [dict(r) for r in rows]}


@router.post("/comments", status_code=201)
async def create_comment(body: CommentCreate, x_user_id: Optional[str] = Header(default=None)):
    """Create a new comment."""
    if not x_user_id:
        raise HTTPException(401, "Authentication required")
    
    comment_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO comments (id, user_id, notebook_id, source_id, note_id, conversation_id, parent_id, content)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            comment_id, x_user_id, body.notebook_id, body.source_id,
            body.note_id, body.conversation_id, body.parent_id, body.content
        ))
        
        # Log activity
        if body.notebook_id:
            activity_id = str(uuid.uuid4())
            # Get org_id from notebook
            nb = conn.execute("SELECT org_id FROM notebooks WHERE id=?", (body.notebook_id,)).fetchone()
            org_id = nb["org_id"] if nb else None
            
            conn.execute("""
                INSERT INTO activity_feed (id, org_id, user_id, notebook_id, action, target_type, target_id)
                VALUES (?, ?, ?, ?, 'commented', 'comment', ?)
            """, (activity_id, org_id, x_user_id, body.notebook_id, comment_id))
    
    return {"id": comment_id}


@router.patch("/comments/{comment_id}")
async def update_comment(comment_id: str, body: CommentUpdate, x_user_id: Optional[str] = Header(default=None)):
    """Update a comment (author only, or resolve by anyone in thread)."""
    if not x_user_id:
        raise HTTPException(401, "Authentication required")
    
    with get_conn() as conn:
        comment = conn.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone()
        if not comment:
            raise HTTPException(404, "Comment not found")
        
        # Only author can edit content
        if body.content is not None and comment["user_id"] != x_user_id:
            raise HTTPException(403, "Only author can edit comment")
        
        updates = []
        params = []
        if body.content is not None:
            updates.append("content=?")
            params.append(body.content)
        if body.resolved is not None:
            updates.append("resolved=?")
            params.append(1 if body.resolved else 0)
        
        if updates:
            updates.append("updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')")
            params.append(comment_id)
            conn.execute(f"UPDATE comments SET {', '.join(updates)} WHERE id=?", params)
    
    return {"updated": True}


@router.delete("/comments/{comment_id}")
async def delete_comment(comment_id: str, x_user_id: Optional[str] = Header(default=None)):
    """Delete a comment (author only)."""
    if not x_user_id:
        raise HTTPException(401, "Authentication required")
    
    with get_conn() as conn:
        comment = conn.execute("SELECT user_id FROM comments WHERE id=?", (comment_id,)).fetchone()
        if not comment:
            raise HTTPException(404, "Comment not found")
        if comment["user_id"] != x_user_id:
            raise HTTPException(403, "Only author can delete comment")
        
        conn.execute("DELETE FROM comments WHERE id=?", (comment_id,))
    
    return {"deleted": comment_id}


# ══════════════════════════════════════════════════════════════════════════
# ACTIVITY FEED
# ══════════════════════════════════════════════════════════════════════════

@router.get("/activity")
async def get_activity_feed(
    org_id: Optional[str] = None,
    notebook_id: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200)
):
    """Get activity feed for org or notebook."""
    with get_conn() as conn:
        conditions = []
        params = []
        
        if org_id:
            conditions.append("a.org_id = ?")
            params.append(org_id)
        if notebook_id:
            conditions.append("a.notebook_id = ?")
            params.append(notebook_id)
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)
        
        rows = conn.execute(f"""
            SELECT a.*, u.name as user_name, u.avatar_url as user_avatar
            FROM activity_feed a
            LEFT JOIN users u ON a.user_id = u.id
            WHERE {where_clause}
            ORDER BY a.created_at DESC
            LIMIT ?
        """, params).fetchall()
    
    return {"activities": [dict(r) for r in rows]}


@router.post("/activity/log")
async def log_activity(
    action: str,
    target_type: str,
    target_id: Optional[str] = None,
    target_title: Optional[str] = None,
    notebook_id: Optional[str] = None,
    metadata: Optional[str] = None,
    x_user_id: Optional[str] = Header(default=None)
):
    """Log an activity event."""
    activity_id = str(uuid.uuid4())
    with get_conn() as conn:
        # Get org_id from notebook if provided
        org_id = None
        if notebook_id:
            nb = conn.execute("SELECT org_id FROM notebooks WHERE id=?", (notebook_id,)).fetchone()
            if nb:
                org_id = nb["org_id"]
        
        conn.execute("""
            INSERT INTO activity_feed (id, org_id, user_id, notebook_id, action, target_type, target_id, target_title, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (activity_id, org_id, x_user_id, notebook_id, action, target_type, target_id, target_title, metadata))
    
    return {"id": activity_id}


# ══════════════════════════════════════════════════════════════════════════
# ORG NOTEBOOKS - List notebooks for an organization
# ══════════════════════════════════════════════════════════════════════════

@router.get("/organizations/{org_id}/notebooks")
async def list_org_notebooks(org_id: str):
    """List all notebooks in an organization."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT n.*, 
                   (SELECT COUNT(*) FROM sources WHERE notebook_id=n.id) as source_count,
                   (SELECT COUNT(*) FROM conversations WHERE notebook_id=n.id) as conversation_count
            FROM notebooks n
            WHERE n.org_id = ?
            ORDER BY n.updated_at DESC
        """, (org_id,)).fetchall()
    return {"notebooks": [dict(r) for r in rows]}


@router.post("/organizations/{org_id}/notebooks/{nb_id}")
async def add_notebook_to_org(org_id: str, nb_id: str, x_user_id: Optional[str] = Header(default=None)):
    """Add an existing notebook to an organization."""
    if not x_user_id:
        raise HTTPException(401, "Authentication required")
    
    with get_conn() as conn:
        # Check membership
        member = conn.execute(
            "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, x_user_id)
        ).fetchone()
        if not member:
            raise HTTPException(403, "Not a member of this organization")
        
        conn.execute("UPDATE notebooks SET org_id=? WHERE id=?", (org_id, nb_id))
        
        # Log activity
        nb = conn.execute("SELECT name FROM notebooks WHERE id=?", (nb_id,)).fetchone()
        activity_id = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO activity_feed (id, org_id, user_id, notebook_id, action, target_type, target_id, target_title)
            VALUES (?, ?, ?, ?, 'shared', 'notebook', ?, ?)
        """, (activity_id, org_id, x_user_id, nb_id, nb_id, nb["name"] if nb else None))
    
    return {"added": True}


@router.delete("/organizations/{org_id}/notebooks/{nb_id}")
async def remove_notebook_from_org(org_id: str, nb_id: str, x_user_id: Optional[str] = Header(default=None)):
    """Remove a notebook from organization (makes it personal)."""
    if not x_user_id:
        raise HTTPException(401, "Authentication required")
    
    with get_conn() as conn:
        member = conn.execute(
            "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, x_user_id)
        ).fetchone()
        if not member or member["role"] not in ("owner", "admin"):
            raise HTTPException(403, "Admin access required")
        
        conn.execute("UPDATE notebooks SET org_id=NULL, user_id=? WHERE id=? AND org_id=?", 
                     (x_user_id, nb_id, org_id))
    
    return {"removed": True}
