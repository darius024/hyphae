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
