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
import os
import re as _re
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
from fastapi import APIRouter, Depends, Header, HTTPException
from notebook.db import get_conn
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

router = APIRouter()

# ── Configuration knobs ───────────────────────────────────────────────────

# Maximum concurrent sessions kept per user (oldest are pruned on login).
_MAX_SESSIONS_PER_USER = int(os.environ.get("MAX_SESSIONS_PER_USER", "10"))

# Failed-login lockout: after N failures the account is locked for
# ``LOCKOUT_MINUTES`` minutes.  Blunts password-spray and credential stuffing.
_LOCKOUT_THRESHOLD = int(os.environ.get("LOCKOUT_THRESHOLD", "10"))
_LOCKOUT_MINUTES = int(os.environ.get("LOCKOUT_MINUTES", "15"))

# Session lifetime + sliding-window refresh.
_SESSION_LIFETIME_DAYS = int(os.environ.get("SESSION_LIFETIME_DAYS", "30"))
_SESSION_REFRESH_WINDOW_DAYS = int(os.environ.get("SESSION_REFRESH_WINDOW_DAYS", "7"))

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
    avatar_url: str | None = None
    created_at: str


class LoginResponse(BaseModel):
    token: str
    user: UserResponse


# ── Helpers ───────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash password using bcrypt (adaptive cost, timing-safe)."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


# Pre-computed hash used to equalise login timing when the email is not found,
# preventing user enumeration via response-time side-channel.
_DUMMY_HASH: str = hash_password("hyphae-timing-guard")


def verify_password(stored_hash: str, password: str) -> bool:
    """Verify password against stored bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode(), stored_hash.encode())
    except Exception:
        return False


def create_session_token() -> str:
    """Generate a secure random session token."""
    return secrets.token_urlsafe(32)


def _hash_token(raw: str) -> str:
    """Hash a session token for storage at rest.

    The DB stores SHA-256 hex digests so a leaked database snapshot does not
    yield usable bearer tokens.  Tokens themselves are 256 bits of entropy,
    so a fast hash is sufficient (no need for bcrypt here).
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _new_expiry() -> str:
    return (datetime.now(UTC) + timedelta(days=_SESSION_LIFETIME_DAYS)).isoformat()


def _resolve_token(authorization: str | None) -> dict | None:
    """Look up a Bearer token and return the user dict, or None.

    Performs sliding-window expiry: if the session's remaining lifetime is
    less than ``_SESSION_REFRESH_WINDOW_DAYS``, the expiry is rolled forward
    so active users are not logged out unnecessarily.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    token_hash = _hash_token(token)
    with get_conn() as conn:
        row = conn.execute("""
            SELECT u.id, u.email, u.name, u.avatar_url, u.created_at,
                   s.id AS session_id, s.expires_at
            FROM sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.token = ? AND s.expires_at > datetime('now')
        """, (token_hash,)).fetchone()
        if not row:
            return None
        # Sliding refresh: roll expiry forward when close to expiring.
        try:
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            remaining = expires_at - datetime.now(UTC)
            if remaining < timedelta(days=_SESSION_REFRESH_WINDOW_DAYS):
                conn.execute(
                    "UPDATE sessions SET expires_at=? WHERE id=?",
                    (_new_expiry(), row["session_id"]),
                )
        except (ValueError, TypeError):
            pass
    return {"id": row[0], "email": row[1], "name": row[2],
            "avatar_url": row[3], "created_at": row[4]}


def get_optional_user(authorization: str | None = Header(None)) -> dict | None:
    """Return the authenticated user or None (never raises)."""
    return _resolve_token(authorization)


def get_current_user(authorization: str | None = Header(None)) -> dict:
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

        conn.execute(
            "INSERT INTO sessions (id, user_id, token, expires_at) VALUES (?, ?, ?, ?)",
            (session_id, user_id, _hash_token(token), _new_expiry()),
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
    """Login and receive session token.

    Enforces a per-account lockout after ``_LOCKOUT_THRESHOLD`` consecutive
    failures.  Successful logins reset the failure counter.
    """
    with get_conn() as conn:
        row = conn.execute("""
            SELECT id, email, password_hash, name, avatar_url, created_at,
                   COALESCE(failed_login_count, 0) AS failed_login_count,
                   locked_until
            FROM users WHERE email = ?
        """, (req.email,)).fetchone()

        if not row:
            verify_password(_DUMMY_HASH, req.password)  # equalise timing
            raise HTTPException(401, "Invalid email or password")

        # Lockout check.
        locked_until = row["locked_until"]
        if locked_until:
            try:
                until = datetime.fromisoformat(locked_until)
                if until.tzinfo is None:
                    until = until.replace(tzinfo=UTC)
                if until > datetime.now(UTC):
                    raise HTTPException(
                        429,
                        "Account temporarily locked due to repeated failed logins. "
                        "Try again later.",
                    )
            except ValueError:
                pass  # malformed timestamp — ignore lockout

        user_id = row["id"]
        if not verify_password(row["password_hash"], req.password):
            new_count = row["failed_login_count"] + 1
            if new_count >= _LOCKOUT_THRESHOLD:
                lock_until = (
                    datetime.now(UTC) + timedelta(minutes=_LOCKOUT_MINUTES)
                ).isoformat()
                conn.execute(
                    "UPDATE users SET failed_login_count=?, locked_until=? WHERE id=?",
                    (new_count, lock_until, user_id),
                )
                log.warning("Account locked after %d failed logins: %s", new_count, req.email)
            else:
                conn.execute(
                    "UPDATE users SET failed_login_count=? WHERE id=?",
                    (new_count, user_id),
                )
            # Persist the counter update before unwinding via HTTPException —
            # the get_conn() context manager rolls back on exceptions, which
            # would otherwise discard the failed-login bookkeeping.
            conn.commit()
            raise HTTPException(401, "Invalid email or password")

        # Success: reset counter, record login time, issue new session.
        conn.execute(
            """UPDATE users
               SET failed_login_count=0, locked_until=NULL,
                   last_login_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
               WHERE id=?""",
            (user_id,),
        )

        token = create_session_token()
        session_id = secrets.token_hex(16)
        conn.execute(
            "INSERT INTO sessions (id, user_id, token, expires_at) VALUES (?, ?, ?, ?)",
            (session_id, user_id, _hash_token(token), _new_expiry()),
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
            id=user_id, email=row["email"], name=row["name"],
            avatar_url=row["avatar_url"], created_at=row["created_at"],
        )

        return LoginResponse(token=token, user=user)


@router.post("/api/auth/logout")
async def logout(authorization: str | None = Header(None)):
    """Logout (invalidate the caller's current session)."""
    if not authorization or not authorization.startswith("Bearer "):
        return {"ok": True}
    token = authorization[7:]
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (_hash_token(token),))
    return {"ok": True}


@router.post("/api/auth/logout-all")
async def logout_all(user: dict = Depends(get_current_user)):
    """Invalidate every session belonging to the authenticated user.

    Useful when a user suspects token compromise: they can revoke every
    other device with a single call from any logged-in client.
    """
    with get_conn() as conn:
        deleted = conn.execute(
            "DELETE FROM sessions WHERE user_id=?", (user["id"],),
        ).rowcount
    return {"ok": True, "sessions_revoked": deleted}


@router.get("/api/auth/me")
async def me(user: dict = Depends(get_current_user)):
    """Get current user info."""
    return UserResponse(**user)
