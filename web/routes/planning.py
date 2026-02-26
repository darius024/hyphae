"""Deadlines, reminders, and calendar sync endpoints."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from notebook.db import get_conn
from routes.auth import get_current_user

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["planning"])


# ── Pydantic models ──────────────────────────────────────────────────────

class DeadlineCreate(BaseModel):
    title: str = Field(..., min_length=1)
    due_date: str
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
    remind_at: str

class CalendarConnect(BaseModel):
    provider: str = Field(..., pattern=r"^(google|outlook)$")
    access_token: str
    refresh_token: Optional[str] = None
    token_expiry: Optional[str] = None
    calendar_id: Optional[str] = None


# ── Deadlines ─────────────────────────────────────────────────────────────

@router.get("/deadlines")
async def list_deadlines(
    notebook_id: Optional[str] = None,
    status: Optional[str] = None,
    upcoming_days: int = Query(default=30, ge=1, le=365),
):
    """List deadlines, optionally filtered."""
    until = (datetime.now(timezone.utc) + timedelta(days=upcoming_days)).isoformat()

    query = "SELECT * FROM deadlines WHERE due_date <= ?"
    params: list = [until]

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
async def create_deadline(body: DeadlineCreate, _user: dict = Depends(get_current_user)):
    """Create a new deadline."""
    dl_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO deadlines (id, notebook_id, source_id, title, due_date, priority, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (dl_id, body.notebook_id, body.source_id, body.title, body.due_date, body.priority, body.note))

    return {"id": dl_id, "title": body.title, "due_date": body.due_date}


@router.patch("/deadlines/{dl_id}")
async def update_deadline(dl_id: str, body: DeadlineUpdate, _user: dict = Depends(get_current_user)):
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
async def delete_deadline(dl_id: str, _user: dict = Depends(get_current_user)):
    """Delete a deadline."""
    with get_conn() as conn:
        conn.execute("DELETE FROM deadlines WHERE id=?", (dl_id,))
    return {"deleted": dl_id}


# ── Reminders ─────────────────────────────────────────────────────────────

@router.post("/reminders", status_code=201)
async def create_reminder(body: ReminderCreate, _user: dict = Depends(get_current_user)):
    """Create a reminder for a deadline."""
    rem_id = str(uuid.uuid4())
    with get_conn() as conn:
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


# ── Calendar connections ──────────────────────────────────────────────────

@router.get("/calendar/connections")
async def list_calendar_connections(_user: dict = Depends(get_current_user)):
    """List user's calendar connections."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, provider, calendar_id, last_sync, created_at FROM calendar_connections
        """).fetchall()
    return {"connections": [dict(r) for r in rows]}


@router.post("/calendar/connect", status_code=201)
async def connect_calendar(body: CalendarConnect, user: dict = Depends(get_current_user)):
    """Connect a calendar provider (store OAuth tokens)."""
    conn_id = str(uuid.uuid4())
    user_id = user["id"]

    with get_conn() as conn:
        conn.execute("""
            INSERT INTO calendar_connections
            (id, user_id, provider, access_token, refresh_token, token_expiry, calendar_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (conn_id, user_id, body.provider, body.access_token,
              body.refresh_token, body.token_expiry, body.calendar_id))

    return {"id": conn_id, "provider": body.provider, "status": "connected"}


@router.post("/calendar/sync/{conn_id}")
async def sync_calendar(conn_id: str, _user: dict = Depends(get_current_user)):
    """Sync events from connected calendar (placeholder for OAuth integration)."""
    with get_conn() as conn:
        connection = conn.execute(
            "SELECT * FROM calendar_connections WHERE id=?", (conn_id,)
        ).fetchone()
        if not connection:
            raise HTTPException(404, "Calendar connection not found")

        conn.execute(
            "UPDATE calendar_connections SET last_sync=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
            (conn_id,),
        )

    return {
        "id": conn_id,
        "status": "synced",
        "message": "Calendar sync initiated. Events will be imported shortly.",
        "note": "Full OAuth flow requires client credentials setup in .env",
    }


@router.delete("/calendar/disconnect/{conn_id}")
async def disconnect_calendar(conn_id: str, _user: dict = Depends(get_current_user)):
    """Disconnect a calendar provider."""
    with get_conn() as conn:
        conn.execute("DELETE FROM calendar_connections WHERE id=?", (conn_id,))
    return {"disconnected": conn_id}
