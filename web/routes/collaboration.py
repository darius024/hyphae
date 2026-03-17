"""Organizations, members, invites, comments, and activity feed endpoints."""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from notebook.db import get_conn, safe_update
from routes.auth import get_current_user

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["collaboration"])


# ── Pydantic models ──────────────────────────────────────────────────────

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

class CommentCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)
    notebook_id: Optional[str] = None
    source_id: Optional[str] = None
    note_id: Optional[str] = None
    conversation_id: Optional[str] = None
    parent_id: Optional[str] = None

class CommentUpdate(BaseModel):
    content: Optional[str] = Field(None, min_length=1, max_length=5000)
    resolved: Optional[bool] = None


# ══════════════════════════════════════════════════════════════════════════
# ORGANIZATIONS
# ══════════════════════════════════════════════════════════════════════════

@router.get("/organizations")
async def list_user_organizations(user: dict = Depends(get_current_user)):
    """List organizations the current user belongs to."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT o.*, om.role as user_role,
                   (SELECT COUNT(*) FROM org_members WHERE org_id=o.id) as member_count,
                   (SELECT COUNT(*) FROM notebooks WHERE org_id=o.id) as notebook_count
            FROM organizations o
            JOIN org_members om ON o.id = om.org_id
            WHERE om.user_id = ?
            ORDER BY o.name
        """, (user["id"],)).fetchall()

    return {"organizations": [dict(r) for r in rows][:200]}


@router.post("/organizations", status_code=201)
async def create_organization(body: OrgCreate, user: dict = Depends(get_current_user)):
    """Create a new organization."""
    org_id = str(uuid.uuid4())
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM organizations WHERE slug=?", (body.slug,)).fetchone()
        if existing:
            raise HTTPException(400, "Organization slug already exists")

        conn.execute("""
            INSERT INTO organizations (id, name, slug, description, owner_id)
            VALUES (?, ?, ?, ?, ?)
        """, (org_id, body.name, body.slug.lower(), body.description, user["id"]))

        member_id = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO org_members (id, org_id, user_id, role)
            VALUES (?, ?, ?, 'owner')
        """, (member_id, org_id, user["id"]))

    return {"id": org_id, "slug": body.slug.lower()}


@router.get("/organizations/{org_id}")
async def get_organization(org_id: str, user: dict = Depends(get_current_user)):
    """Get organization details. Only accessible to current members."""
    with get_conn() as conn:
        org = conn.execute("SELECT * FROM organizations WHERE id=?", (org_id,)).fetchone()
        if not org:
            raise HTTPException(404, "Organization not found")

        member = conn.execute(
            "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, user["id"]),
        ).fetchone()
        if not member:
            raise HTTPException(403, "You are not a member of this organization")

        members = conn.execute("""
            SELECT om.*, u.name, u.email, u.avatar_url
            FROM org_members om
            JOIN users u ON om.user_id = u.id
            WHERE om.org_id = ?
            ORDER BY om.role, u.name
            LIMIT 500
        """, (org_id,)).fetchall()

        notebooks = conn.execute("""
            SELECT id, name, description, created_at FROM notebooks
            WHERE org_id = ? ORDER BY updated_at DESC
            LIMIT 200
        """, (org_id,)).fetchall()

    return {
        **dict(org),
        "user_role": member["role"] if member else None,
        "members": [dict(m) for m in members],
        "notebooks": [dict(n) for n in notebooks],
    }


@router.patch("/organizations/{org_id}")
async def update_organization(org_id: str, body: OrgUpdate, user: dict = Depends(get_current_user)):
    """Update organization details (admin/owner only)."""
    with get_conn() as conn:
        member = conn.execute(
            "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, user["id"]),
        ).fetchone()
        if not member or member["role"] not in ("owner", "admin"):
            raise HTTPException(403, "Admin access required")

        fields = {}
        if body.name is not None:
            fields["name"] = body.name
        if body.description is not None:
            fields["description"] = body.description
        if body.avatar_url is not None:
            fields["avatar_url"] = body.avatar_url

        safe_update(conn, "organizations", fields, "id", org_id)

    return {"updated": True}


@router.delete("/organizations/{org_id}")
async def delete_organization(org_id: str, user: dict = Depends(get_current_user)):
    """Delete organization (owner only)."""
    with get_conn() as conn:
        org = conn.execute("SELECT owner_id FROM organizations WHERE id=?", (org_id,)).fetchone()
        if not org:
            raise HTTPException(404, "Organization not found")
        if org["owner_id"] != user["id"]:
            raise HTTPException(403, "Only owner can delete organization")

        conn.execute("DELETE FROM organizations WHERE id=?", (org_id,))

    return {"deleted": org_id}


# ── Members ───────────────────────────────────────────────────────────────

@router.get("/organizations/{org_id}/members")
async def list_org_members(org_id: str, user: dict = Depends(get_current_user)):
    """List organization members. Only accessible to current members."""
    with get_conn() as conn:
        member = conn.execute(
            "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, user["id"]),
        ).fetchone()
        if not member:
            raise HTTPException(403, "You are not a member of this organization")

        rows = conn.execute("""
            SELECT om.*, u.name, u.email, u.avatar_url
            FROM org_members om
            JOIN users u ON om.user_id = u.id
            WHERE om.org_id = ?
            ORDER BY om.role DESC, u.name
            LIMIT 500
        """, (org_id,)).fetchall()
    return {"members": [dict(r) for r in rows]}


@router.post("/organizations/{org_id}/invite", status_code=201)
async def invite_to_org(org_id: str, body: OrgInvite, user: dict = Depends(get_current_user)):
    """Invite a user to organization by email."""
    with get_conn() as conn:
        member = conn.execute(
            "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, user["id"]),
        ).fetchone()
        if not member or member["role"] not in ("owner", "admin"):
            raise HTTPException(403, "Admin access required")

        existing = conn.execute("""
            SELECT om.id FROM org_members om
            JOIN users u ON om.user_id = u.id
            WHERE om.org_id=? AND u.email=?
        """, (org_id, body.email)).fetchone()
        if existing:
            raise HTTPException(400, "User is already a member")

        pending = conn.execute(
            "SELECT id FROM org_invites WHERE org_id=? AND email=? AND accepted=0",
            (org_id, body.email),
        ).fetchone()
        if pending:
            raise HTTPException(400, "Invite already pending")

        invite_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

        conn.execute("""
            INSERT INTO org_invites (id, org_id, email, role, token, invited_by, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (invite_id, org_id, body.email, body.role, token, user["id"], expires))

    return {"invite_id": invite_id, "token": token}


@router.post("/organizations/accept-invite/{token}")
async def accept_org_invite(token: str, user: dict = Depends(get_current_user)):
    """Accept an organization invite."""
    with get_conn() as conn:
        invite = conn.execute("""
            SELECT * FROM org_invites WHERE token=? AND accepted=0
        """, (token,)).fetchone()

        if not invite:
            raise HTTPException(404, "Invalid or expired invite")

        if datetime.fromisoformat(invite["expires_at"].replace("Z", "+00:00")) < datetime.now(timezone.utc):
            raise HTTPException(400, "Invite has expired")

        db_user = conn.execute("SELECT email FROM users WHERE id=?", (user["id"],)).fetchone()
        if not db_user or db_user["email"].lower() != invite["email"].lower():
            raise HTTPException(400, "This invite was sent to a different email")

        member_id = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO org_members (id, org_id, user_id, role)
            VALUES (?, ?, ?, ?)
        """, (member_id, invite["org_id"], user["id"], invite["role"]))

        conn.execute("UPDATE org_invites SET accepted=1 WHERE id=?", (invite["id"],))

    return {"joined": invite["org_id"]}


@router.delete("/organizations/{org_id}/members/{user_id}")
async def remove_org_member(org_id: str, user_id: str, user: dict = Depends(get_current_user)):
    """Remove a member from organization."""
    with get_conn() as conn:
        actor = conn.execute(
            "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, user["id"]),
        ).fetchone()

        if not actor:
            raise HTTPException(403, "Not a member of this organization")

        if user_id != user["id"]:
            if actor["role"] not in ("owner", "admin"):
                raise HTTPException(403, "Admin access required")

        target = conn.execute(
            "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, user_id),
        ).fetchone()
        if target and target["role"] == "owner":
            raise HTTPException(400, "Cannot remove organization owner")

        conn.execute(
            "DELETE FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, user_id),
        )

    return {"removed": user_id}


@router.patch("/organizations/{org_id}/members/{user_id}/role")
async def update_member_role(
    org_id: str,
    user_id: str,
    role: str = Query(..., pattern=r"^(admin|member|viewer)$"),
    user: dict = Depends(get_current_user),
):
    """Update a member's role (admin/owner only)."""
    with get_conn() as conn:
        org = conn.execute("SELECT owner_id FROM organizations WHERE id=?", (org_id,)).fetchone()
        if not org:
            raise HTTPException(404, "Organization not found")

        actor = conn.execute(
            "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, user["id"]),
        ).fetchone()
        if not actor or actor["role"] not in ("owner", "admin"):
            raise HTTPException(403, "Admin access required")

        target = conn.execute(
            "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, user_id),
        ).fetchone()
        if target and target["role"] == "owner":
            raise HTTPException(400, "Cannot change owner role")

        conn.execute(
            "UPDATE org_members SET role=? WHERE org_id=? AND user_id=?",
            (role, org_id, user_id),
        )

    return {"updated": True}


# ── Org notebooks ─────────────────────────────────────────────────────────

@router.get("/organizations/{org_id}/notebooks")
async def list_org_notebooks(org_id: str, user: dict = Depends(get_current_user)):
    """List all notebooks in an organization."""
    with get_conn() as conn:
        member = conn.execute(
            "SELECT 1 FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, user["id"]),
        ).fetchone()
        if not member:
            raise HTTPException(403, "Not a member of this organization")
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
async def add_notebook_to_org(org_id: str, nb_id: str, user: dict = Depends(get_current_user)):
    """Add an existing notebook to an organization."""
    with get_conn() as conn:
        member = conn.execute(
            "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, user["id"]),
        ).fetchone()
        if not member:
            raise HTTPException(403, "Not a member of this organization")

        conn.execute(
            "UPDATE notebooks SET org_id=? WHERE id=? AND user_id=?",
            (org_id, nb_id, user["id"]),
        )

        nb = conn.execute("SELECT name FROM notebooks WHERE id=?", (nb_id,)).fetchone()
        activity_id = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO activity_feed (id, org_id, user_id, notebook_id, action, target_type, target_id, target_title)
            VALUES (?, ?, ?, ?, 'shared', 'notebook', ?, ?)
        """, (activity_id, org_id, user["id"], nb_id, nb_id, nb["name"] if nb else None))

    return {"added": True}


@router.delete("/organizations/{org_id}/notebooks/{nb_id}")
async def remove_notebook_from_org(org_id: str, nb_id: str, user: dict = Depends(get_current_user)):
    """Remove a notebook from organization (makes it personal)."""
    with get_conn() as conn:
        member = conn.execute(
            "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, user["id"]),
        ).fetchone()
        if not member or member["role"] not in ("owner", "admin"):
            raise HTTPException(403, "Admin access required")

        conn.execute(
            "UPDATE notebooks SET org_id=NULL, user_id=? WHERE id=? AND org_id=?",
            (user["id"], nb_id, org_id),
        )

    return {"removed": True}


# ══════════════════════════════════════════════════════════════════════════
# COMMENTS
# ══════════════════════════════════════════════════════════════════════════

@router.get("/comments")
async def list_comments(
    notebook_id: Optional[str] = None,
    source_id: Optional[str] = None,
    note_id: Optional[str] = None,
    _user: dict = Depends(get_current_user),
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

        if not conditions:
            raise HTTPException(400, "At least one filter (notebook_id, source_id, or note_id) is required")

        where_clause = " AND ".join(conditions)

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
async def get_comment_replies(comment_id: str, _user: dict = Depends(get_current_user)):
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
async def create_comment(body: CommentCreate, user: dict = Depends(get_current_user)):
    """Create a new comment."""
    comment_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO comments (id, user_id, notebook_id, source_id, note_id, conversation_id, parent_id, content)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            comment_id, user["id"], body.notebook_id, body.source_id,
            body.note_id, body.conversation_id, body.parent_id, body.content,
        ))

        if body.notebook_id:
            activity_id = str(uuid.uuid4())
            nb = conn.execute("SELECT org_id FROM notebooks WHERE id=?", (body.notebook_id,)).fetchone()
            org_id = nb["org_id"] if nb else None

            conn.execute("""
                INSERT INTO activity_feed (id, org_id, user_id, notebook_id, action, target_type, target_id)
                VALUES (?, ?, ?, ?, 'commented', 'comment', ?)
            """, (activity_id, org_id, user["id"], body.notebook_id, comment_id))

    return {"id": comment_id}


@router.patch("/comments/{comment_id}")
async def update_comment(comment_id: str, body: CommentUpdate, user: dict = Depends(get_current_user)):
    """Update a comment (author only, or resolve by anyone in thread)."""
    with get_conn() as conn:
        comment = conn.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone()
        if not comment:
            raise HTTPException(404, "Comment not found")

        if body.content is not None and comment["user_id"] != user["id"]:
            raise HTTPException(403, "Only author can edit comment")

        fields = {}
        if body.content is not None:
            fields["content"] = body.content
        if body.resolved is not None:
            fields["resolved"] = 1 if body.resolved else 0

        safe_update(conn, "comments", fields, "id", comment_id)

    return {"updated": True}


@router.delete("/comments/{comment_id}")
async def delete_comment(comment_id: str, user: dict = Depends(get_current_user)):
    """Delete a comment (author only)."""
    with get_conn() as conn:
        comment = conn.execute("SELECT user_id FROM comments WHERE id=?", (comment_id,)).fetchone()
        if not comment:
            raise HTTPException(404, "Comment not found")
        if comment["user_id"] != user["id"]:
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
    limit: int = Query(default=50, ge=1, le=200),
    _user: dict = Depends(get_current_user),
):
    """Get activity feed for org or notebook."""
    with get_conn() as conn:
        conditions = []
        params: list = []

        if org_id:
            conditions.append("a.org_id = ?")
            params.append(org_id)
        if notebook_id:
            conditions.append("a.notebook_id = ?")
            params.append(notebook_id)

        if not conditions:
            raise HTTPException(400, "At least one filter (org_id or notebook_id) is required")

        where_clause = " AND ".join(conditions)
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
    user: dict = Depends(get_current_user),
):
    """Log an activity event."""
    activity_id = str(uuid.uuid4())
    with get_conn() as conn:
        org_id = None
        if notebook_id:
            nb = conn.execute("SELECT org_id FROM notebooks WHERE id=?", (notebook_id,)).fetchone()
            if nb:
                org_id = nb["org_id"]

        conn.execute("""
            INSERT INTO activity_feed (id, org_id, user_id, notebook_id, action, target_type, target_id, target_title, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (activity_id, org_id, user["id"], notebook_id, action, target_type, target_id, target_title, metadata))

    return {"id": activity_id}
