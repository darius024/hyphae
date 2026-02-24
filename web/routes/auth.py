"""
Authentication routes for Hyphae.

Endpoints:
    POST /api/auth/signup  — Create a new user account
    POST /api/auth/login   — Login and receive session token
    POST /api/auth/logout  — Logout (invalidate session)
    GET  /api/auth/me      — Get current user info
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel

from notebook.db import get_conn

log = logging.getLogger(__name__)

router = APIRouter()

# ── Models ────────────────────────────────────────────────────────────────

class SignupRequest(BaseModel):
    email: str
    password: str
    name: str


class LoginRequest(BaseModel):
    email: str
    password: str


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
    """Hash password using SHA256 with salt."""
    salt = secrets.token_hex(16)
    pwdhash = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${pwdhash}"


def verify_password(stored_hash: str, password: str) -> bool:
    """Verify password against stored hash."""
    try:
        salt, pwdhash = stored_hash.split('$')
        check_hash = hashlib.sha256((salt + password).encode()).hexdigest()
        return check_hash == pwdhash
    except Exception:
        return False


def create_session_token() -> str:
    """Generate a secure random session token."""
    return secrets.token_urlsafe(32)


def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    """Dependency to get current user from Authorization header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    
    token = authorization.replace("Bearer ", "")
    
    with get_conn() as conn:
        cursor = conn.execute("""
            SELECT u.id, u.email, u.name, u.avatar_url, u.created_at
            FROM sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.token = ? AND s.expires_at > datetime('now')
        """, (token,))
        row = cursor.fetchone()
        
        if not row:
            raise HTTPException(401, "Invalid or expired token")
        
        return {
            "id": row[0],
            "email": row[1],
            "name": row[2],
            "avatar_url": row[3],
            "created_at": row[4]
        }


# ── Routes ────────────────────────────────────────────────────────────────

@router.post("/api/auth/signup")
async def signup(req: SignupRequest):
    """Create a new user account."""
    if len(req.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    
    user_id = secrets.token_hex(16)
    password_hash = hash_password(req.password)
    
    with get_conn() as conn:
        try:
            conn.execute("""
                INSERT INTO users (id, email, password_hash, name)
                VALUES (?, ?, ?, ?)
            """, (user_id, req.email, password_hash, req.name))
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(400, "Email already registered")
        
        # Create session
        token = create_session_token()
        session_id = secrets.token_hex(16)
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        
        conn.execute("""
            INSERT INTO sessions (id, user_id, token, expires_at)
            VALUES (?, ?, ?, ?)
        """, (session_id, user_id, token, expires_at))
        conn.commit()
        
        cursor = conn.execute("""
            SELECT id, email, name, avatar_url, created_at
            FROM users WHERE id = ?
        """, (user_id,))
        row = cursor.fetchone()
        
        user = UserResponse(
            id=row[0],
            email=row[1],
            name=row[2],
            avatar_url=row[3],
            created_at=row[4]
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
        
        conn.execute("""
            INSERT INTO sessions (id, user_id, token, expires_at)
            VALUES (?, ?, ?, ?)
        """, (session_id, user_id, token, expires_at))
        conn.commit()
        
        user = UserResponse(
            id=user_id,
            email=email,
            name=name,
            avatar_url=avatar_url,
            created_at=created_at
        )
        
        return LoginResponse(token=token, user=user)


@router.post("/api/auth/logout")
async def logout(authorization: Optional[str] = Header(None)):
    """Logout (invalidate session)."""
    if not authorization or not authorization.startswith("Bearer "):
        return {"ok": True}
    
    token = authorization.replace("Bearer ", "")
    
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    
    return {"ok": True}


@router.get("/api/auth/me")
async def me(user: dict = Depends(get_current_user)):
    """Get current user info."""
    return UserResponse(**user)
