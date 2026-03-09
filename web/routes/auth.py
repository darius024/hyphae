"""
Authentication routes for Hyphae.

Endpoints:
    POST /api/auth/signup  — Create a new user account
    POST /api/auth/login   — Login and receive session token
    POST /api/auth/logout  — Logout (invalidate session)
    GET  /api/auth/me      — Get current user info
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel, Field
import os
import re as _re

from notebook.db import get_conn

log = logging.getLogger(__name__)

router = APIRouter()

# ── Models ────────────────────────────────────────────────────────────────

# Maximum concurrent sessions kept per user (oldest are pruned on login).
_MAX_SESSIONS_PER_USER = int(os.environ.get("MAX_SESSIONS_PER_USER", "10"))

_EMAIL_RE = _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SignupRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=8, max_length=72)  # bcrypt hard limit
    name: str = Field(..., min_length=1, max_length=200)


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=72)


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    avatar_url: Optional[str] = None
    created_at: str


class LoginResponse(BaseModel):
    token: str
    user: UserResponse


# ── Helpers ───────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash password using bcrypt (adaptive cost, timing-safe)."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(stored_hash: str, password: str) -> bool:
    """Verify password against stored bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode(), stored_hash.encode())
    except Exception:
        return False


def create_session_token() -> str:
    """Generate a secure random session token."""
    return secrets.token_urlsafe(32)


def _resolve_token(authorization: Optional[str]) -> Optional[dict]:
    """Look up a Bearer token and return the user dict, or None."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]  # strip "Bearer "
    with get_conn() as conn:
        row = conn.execute("""
            SELECT u.id, u.email, u.name, u.avatar_url, u.created_at
            FROM sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.token = ? AND s.expires_at > datetime('now')
        """, (token,)).fetchone()
    if not row:
        return None
    return {"id": row[0], "email": row[1], "name": row[2],
            "avatar_url": row[3], "created_at": row[4]}


def get_optional_user(authorization: Optional[str] = Header(None)) -> Optional[dict]:
    """Return the authenticated user or None (never raises)."""
    return _resolve_token(authorization)


def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    """Return the authenticated user or raise 401."""
    user = _resolve_token(authorization)
    if user is None:
        raise HTTPException(401, "Not authenticated")
    return user


require_user = Depends(get_current_user)
"""FastAPI dependency — use as a default parameter: `user: dict = require_user`."""


# ── Routes ────────────────────────────────────────────────────────────────

@router.post("/api/auth/signup")
async def signup(req: SignupRequest):
    """Create a new user account."""
    user_id = secrets.token_hex(16)
    if not _EMAIL_RE.match(req.email):
        raise HTTPException(400, "Invalid email address")
    password_hash = hash_password(req.password)

    with get_conn() as conn:
        existing = conn.execute("SELECT 1 FROM users WHERE email=?", (req.email,)).fetchone()
        if existing:
            raise HTTPException(400, "Email already registered")

        conn.execute(
            "INSERT INTO users (id, email, password_hash, name) VALUES (?, ?, ?, ?)",
            (user_id, req.email, password_hash, req.name),
        )

        token = create_session_token()
        session_id = secrets.token_hex(16)
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()

        conn.execute(
            "INSERT INTO sessions (id, user_id, token, expires_at) VALUES (?, ?, ?, ?)",
            (session_id, user_id, token, expires_at),
        )

        row = conn.execute(
            "SELECT id, email, name, avatar_url, created_at FROM users WHERE id=?",
            (user_id,),
        ).fetchone()

        user = UserResponse(
            id=row[0], email=row[1], name=row[2],
            avatar_url=row[3], created_at=row[4],
        )

        return LoginResponse(token=token, user=user)


@router.post("/api/auth/login")
async def login(req: LoginRequest):
    """Login and receive session token."""
    with get_conn() as conn:
        cursor = conn.execute("""
            SELECT id, email, password_hash, name, avatar_url, created_at
            FROM users WHERE email = ?
        """, (req.email,))
        row = cursor.fetchone()
        
        if not row:
            raise HTTPException(401, "Invalid email or password")
        
        user_id, email, password_hash, name, avatar_url, created_at = row
        
        if not verify_password(password_hash, req.password):
            raise HTTPException(401, "Invalid email or password")
        
        # Create session
        token = create_session_token()
        session_id = secrets.token_hex(16)
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        
        conn.execute(
            "INSERT INTO sessions (id, user_id, token, expires_at) VALUES (?, ?, ?, ?)",
            (session_id, user_id, token, expires_at),
        )

        # Prune oldest sessions beyond the per-user cap.
        if _MAX_SESSIONS_PER_USER > 0:
            conn.execute("""
                DELETE FROM sessions
                WHERE user_id = ? AND id NOT IN (
                    SELECT id FROM sessions WHERE user_id = ?
                    ORDER BY expires_at DESC
                    LIMIT ?
                )
            """, (user_id, user_id, _MAX_SESSIONS_PER_USER))

        user = UserResponse(
            id=user_id, email=email, name=name,
            avatar_url=avatar_url, created_at=created_at,
        )

        return LoginResponse(token=token, user=user)


@router.post("/api/auth/logout")
async def logout(authorization: Optional[str] = Header(None)):
    """Logout (invalidate session)."""
    if not authorization or not authorization.startswith("Bearer "):
        return {"ok": True}
    token = authorization[7:]  # consistent with _resolve_token
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    return {"ok": True}


@router.get("/api/auth/me")
async def me(user: dict = Depends(get_current_user)):
    """Get current user info."""
    return UserResponse(**user)
