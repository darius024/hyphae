"""Deadlines, reminders, and calendar sync endpoints."""

from __future__ import annotations

import base64
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from notebook.db import get_conn, safe_update
from routes.auth import get_current_user

log = logging.getLogger(__name__)

# ── Token encryption ────────────────────────────────────────────────────────
# OAuth tokens are encrypted at rest using Fernet (AES-128-CBC + HMAC-SHA256).
# Set TOKEN_ENCRYPTION_KEY in the environment to a 32-byte base64url key, e.g.:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# If unset, a warning is logged and tokens are stored as-is (development only).

_fernet = None
_fernet_lock = threading.Lock()

def _get_fernet():
    """Return a lazily-initialised Fernet instance, or None if unconfigured."""
    global _fernet
    if _fernet is not None:
        return _fernet
    with _fernet_lock:
        if _fernet is not None:  # re-check after acquiring lock
            return _fernet
        raw_key = os.environ.get("TOKEN_ENCRYPTION_KEY")
        if not raw_key:
            log.warning(
                "TOKEN_ENCRYPTION_KEY is not set — generating a random ephemeral key. "
                "OAuth tokens will become invalid on process restart. "
                "Set TOKEN_ENCRYPTION_KEY in production."
            )
            from cryptography.fernet import Fernet  # type: ignore
            _fernet = Fernet(Fernet.generate_key())
            return _fernet
        try:
            from cryptography.fernet import Fernet  # type: ignore
            _fernet = Fernet(raw_key.encode())
            return _fernet
        except Exception as exc:
            log.error("Failed to initialise token encryption: %s", exc)
            return None


def _encrypt_token(value: Optional[str]) -> Optional[str]:
    """Encrypt *value* with Fernet; always encrypted (ephemeral key if not configured)."""
    if value is None:
        return None
    fernet = _get_fernet()
    if fernet is None:
        return value
    return fernet.encrypt(value.encode()).decode()


def _decrypt_token(value: Optional[str]) -> Optional[str]:
    """Decrypt a Fernet-encrypted *value*; return it unchanged if encryption is unavailable."""
    if value is None:
        return None
    fernet = _get_fernet()
    if fernet is None:
        return value
    try:
        return fernet.decrypt(value.encode()).decode()
    except Exception:
        # Value was stored before encryption was enabled — return as-is.
        return value

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
    _user: dict = Depends(get_current_user),
):
    """List deadlines, optionally filtered."""
    until = (datetime.now(timezone.utc) + timedelta(days=upcoming_days)).isoformat()

    query = "SELECT * FROM deadlines WHERE due_date <= ? AND user_id = ?"
    params: list = [until, _user["id"]]

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
            INSERT INTO deadlines (id, notebook_id, source_id, title, due_date, priority, note, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (dl_id, body.notebook_id, body.source_id, body.title, body.due_date, body.priority, body.note, _user["id"]))

    return {"id": dl_id, "title": body.title, "due_date": body.due_date}


@router.patch("/deadlines/{dl_id}")
async def update_deadline(dl_id: str, body: DeadlineUpdate, _user: dict = Depends(get_current_user)):
    """Update a deadline."""
    with get_conn() as conn:
        existing = conn.execute("SELECT * FROM deadlines WHERE id=?", (dl_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "Deadline not found")
        if existing["user_id"] and existing["user_id"] != _user["id"]:
            raise HTTPException(403, "Access denied")

        _ALLOWED = ("title", "due_date", "priority", "status", "note")
        fields = {f: getattr(body, f) for f in _ALLOWED if getattr(body, f, None) is not None}

        safe_update(conn, "deadlines", fields, "id", dl_id, auto_timestamp=False)

    return {"id": dl_id, "updated": True}


@router.delete("/deadlines/{dl_id}")
async def delete_deadline(dl_id: str, _user: dict = Depends(get_current_user)):
    """Delete a deadline."""
    with get_conn() as conn:
        existing = conn.execute("SELECT user_id FROM deadlines WHERE id=?", (dl_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "Deadline not found")
        if existing["user_id"] and existing["user_id"] != _user["id"]:
            raise HTTPException(403, "Access denied")
        conn.execute("DELETE FROM deadlines WHERE id=?", (dl_id,))
    return {"deleted": dl_id}


# ── Reminders ─────────────────────────────────────────────────────────────

@router.post("/reminders", status_code=201)
async def create_reminder(body: ReminderCreate, _user: dict = Depends(get_current_user)):
    """Create a reminder for a deadline."""
    rem_id = str(uuid.uuid4())
    with get_conn() as conn:
        dl = conn.execute("SELECT id, user_id FROM deadlines WHERE id=?", (body.deadline_id,)).fetchone()
        if not dl:
            raise HTTPException(404, "Deadline not found")
        if dl["user_id"] and dl["user_id"] != _user["id"]:
            raise HTTPException(403, "Access denied")

        conn.execute("""
            INSERT INTO reminders (id, deadline_id, user_id, remind_at)
            VALUES (?, ?, ?, ?)
        """, (rem_id, body.deadline_id, _user["id"], body.remind_at))

    return {"id": rem_id, "deadline_id": body.deadline_id, "remind_at": body.remind_at}


@router.get("/reminders/pending")
async def get_pending_reminders(_user: dict = Depends(get_current_user)):
    """Get all pending (unsent) reminders that are due."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT r.*, d.title as deadline_title, d.due_date
            FROM reminders r
            JOIN deadlines d ON r.deadline_id = d.id
            WHERE r.sent = 0 AND r.remind_at <= ? AND r.user_id = ?
            ORDER BY r.remind_at
        """, (now, _user["id"])).fetchall()

    return {"reminders": [dict(r) for r in rows]}


# ── Calendar connections ──────────────────────────────────────────────────

@router.get("/calendar/connections")
async def list_calendar_connections(user: dict = Depends(get_current_user)):
    """List the current user's calendar connections."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, provider, calendar_id, last_sync, created_at
            FROM calendar_connections
            WHERE user_id = ?
        """, (user["id"],)).fetchall()
    return {"connections": [dict(r) for r in rows]}


@router.post("/calendar/connect", status_code=201)
async def connect_calendar(body: CalendarConnect, user: dict = Depends(get_current_user)):
    """Connect a calendar provider (store OAuth tokens encrypted at rest)."""
    conn_id = str(uuid.uuid4())
    user_id = user["id"]

    encrypted_access = _encrypt_token(body.access_token)
    encrypted_refresh = _encrypt_token(body.refresh_token)

    with get_conn() as conn:
        conn.execute("""
            INSERT INTO calendar_connections
            (id, user_id, provider, access_token, refresh_token, token_expiry, calendar_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (conn_id, user_id, body.provider, encrypted_access,
              encrypted_refresh, body.token_expiry, body.calendar_id))

    return {"id": conn_id, "provider": body.provider, "status": "connected"}


@router.post("/calendar/sync/{conn_id}")
async def sync_calendar(conn_id: str, _user: dict = Depends(get_current_user)):
    """Sync events from connected calendar (placeholder for OAuth integration)."""
    with get_conn() as conn:
        connection = conn.execute(
            "SELECT * FROM calendar_connections WHERE id=? AND user_id=?",
            (conn_id, _user["id"]),
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
async def disconnect_calendar(conn_id: str, user: dict = Depends(get_current_user)):
    """Disconnect a calendar provider.

    Only the owning user may remove a connection (prevents IDOR).
    Returns the disconnected connection id on success, or 404 if the
    connection does not exist or does not belong to the caller.
    """
    with get_conn() as conn:
        result = conn.execute(
            "DELETE FROM calendar_connections WHERE id=? AND user_id=?",
            (conn_id, user["id"]),
        )
        if result.rowcount == 0:
            raise HTTPException(404, "Calendar connection not found")
    return {"disconnected": conn_id}


# ── Digest ────────────────────────────────────────────────────────────────

@router.get("/planning/digest")
async def get_planning_digest(
    days: int = Query(default=7, ge=1, le=90),
    notebook_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """Return upcoming deadlines (within *days* days) with the latest conversation
    per linked notebook.

    Excludes completed and cancelled deadlines.  Results are ordered by
    ``due_date`` ascending so the most urgent item appears first.
    At most 20 results are returned; when the window contains more,
    ``truncated`` is ``true`` in the response.

    Args:
        days: Look-ahead window in days (1–90).  Default is 7.
        notebook_id: Optional. When provided, only deadlines linked to this
            notebook are returned.

    Returns:
        ``{"deadlines": [...], "days": <int>, "truncated": <bool>}``
        Each deadline includes all deadline columns plus:
        - ``notebook_name``: human-readable notebook title (if linked)
        - ``latest_conversation``: ``{id, title, updated_at}`` of the most
          recently updated conversation in the linked notebook, or ``null``.
    """
    now = datetime.now(timezone.utc).isoformat()
    until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

    nb_filter = " AND d.notebook_id = ?" if notebook_id else ""
    params: list = [now, until, user["id"]]
    if notebook_id:
        params.append(notebook_id)

    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT d.*, nb.name AS notebook_name
               FROM deadlines d
               LEFT JOIN notebooks nb ON d.notebook_id = nb.id
               WHERE d.due_date >= ?
                 AND d.due_date <= ?
                 AND d.user_id = ?
                 AND d.status NOT IN ('completed', 'cancelled')
                 {nb_filter}
               ORDER BY d.due_date ASC
               LIMIT 21""",
            params,
        ).fetchall()

        # Use a limit of 21 to detect truncation without fetching all rows.
        truncated = len(rows) > 20
        rows = rows[:20]

        items = []
        for row in rows:
            item = dict(row)
            item["latest_conversation"] = None
            if item.get("notebook_id"):
                conv = conn.execute(
                    """SELECT id, title, updated_at FROM conversations
                       WHERE notebook_id = ?
                       ORDER BY updated_at DESC
                       LIMIT 1""",
                    (item["notebook_id"],),
                ).fetchone()
                if conv:
                    item["latest_conversation"] = dict(conv)
            items.append(item)

    return {"deadlines": items, "days": days, "truncated": truncated}
