"""Tags, source tagging, knowledge graph, and document linking endpoints."""

from __future__ import annotations

import logging
import uuid
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from notebook.db import get_conn, safe_update
from routes.auth import get_current_user

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["tags"])


# ── Pydantic models ──────────────────────────────────────────────────────

class TagCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    color: str = Field(default="#6366f1", pattern=r"^#[0-9a-fA-F]{6}$")

class TagUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    color: Optional[str] = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")

class SourceTagBody(BaseModel):
    tag_ids: List[str]

class LinkCreate(BaseModel):
    target_id: str
    link_type: str = Field(default="related", pattern=r"^(related|cites|extends|contradicts|supports)$")
    note: Optional[str] = None


# ── Tag CRUD ──────────────────────────────────────────────────────────────

@router.get("/tags")
async def list_tags(_user: dict = Depends(get_current_user)):
    """List all available tags."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, color, created_at FROM tags ORDER BY name"
        ).fetchall()
    return {"tags": [dict(r) for r in rows]}


@router.post("/tags", status_code=201)
async def create_tag(body: TagCreate, _user: dict = Depends(get_current_user)):
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
async def update_tag(tag_id: str, body: TagUpdate, _user: dict = Depends(get_current_user)):
    """Update a tag's name or color."""
    with get_conn() as conn:
        existing = conn.execute("SELECT * FROM tags WHERE id=?", (tag_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "Tag not found")

        fields = {}
        if body.name is not None:
            fields["name"] = body.name.strip()
        if body.color is not None:
            fields["color"] = body.color

        safe_update(conn, "tags", fields, "id", tag_id, auto_timestamp=False)

    return {"id": tag_id, "updated": True}


@router.delete("/tags/{tag_id}")
async def delete_tag(tag_id: str, _user: dict = Depends(get_current_user)):
    """Delete a tag."""
    with get_conn() as conn:
        conn.execute("DELETE FROM tags WHERE id=?", (tag_id,))
    return {"deleted": tag_id}


# ── Source tags ───────────────────────────────────────────────────────────

@router.get("/notebooks/{nb_id}/sources/{src_id}/tags")
async def get_source_tags(nb_id: str, src_id: str, _user: dict = Depends(get_current_user)):
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
async def set_source_tags(nb_id: str, src_id: str, body: SourceTagBody, _user: dict = Depends(get_current_user)):
    """Set tags for a source (replaces existing)."""
    with get_conn() as conn:
        src = conn.execute(
            "SELECT id FROM sources WHERE id=? AND notebook_id=?", (src_id, nb_id)
        ).fetchone()
        if not src:
            raise HTTPException(404, "Source not found")

        conn.execute("DELETE FROM source_tags WHERE source_id=?", (src_id,))

        for tag_id in body.tag_ids:
            try:
                conn.execute(
                    "INSERT INTO source_tags (source_id, tag_id) VALUES (?, ?)",
                    (src_id, tag_id)
                )
            except Exception:
                log.warning("Skipping invalid tag_id %s for source %s", tag_id, src_id)

    return {"source_id": src_id, "tag_ids": body.tag_ids}


# ── Knowledge graph / document links ─────────────────────────────────────

@router.get("/notebooks/{nb_id}/graph")
async def get_knowledge_graph(nb_id: str, _user: dict = Depends(get_current_user)):
    """Get the knowledge graph for a notebook (nodes = sources, edges = links)."""
    with get_conn() as conn:
        sources = conn.execute("""
            SELECT id, title, filename, type, created_at FROM sources
            WHERE notebook_id = ?
        """, (nb_id,)).fetchall()

        links = conn.execute("""
            SELECT dl.id, dl.source_id, dl.target_id, dl.link_type, dl.note
            FROM document_links dl
            JOIN sources s1 ON dl.source_id = s1.id
            JOIN sources s2 ON dl.target_id = s2.id
            WHERE s1.notebook_id = ? OR s2.notebook_id = ?
        """, (nb_id, nb_id)).fetchall()

        tags_by_source: dict[str, list] = {}
        tag_rows = conn.execute("""
            SELECT st.source_id, t.name, t.color FROM source_tags st
            JOIN tags t ON st.tag_id = t.id
            JOIN sources s ON st.source_id = s.id
            WHERE s.notebook_id = ?
        """, (nb_id,)).fetchall()
        for r in tag_rows:
            tags_by_source.setdefault(r["source_id"], []).append(
                {"name": r["name"], "color": r["color"]}
            )

    nodes = [
        {
            "id": s["id"],
            "label": s["title"] or s["filename"] or "Untitled",
            "type": s["type"],
            "tags": tags_by_source.get(s["id"], []),
        }
        for s in sources
    ]
    edges = [
        {"id": l["id"], "source": l["source_id"], "target": l["target_id"],
         "type": l["link_type"], "note": l["note"]}
        for l in links
    ]

    return {"nodes": nodes, "edges": edges}


@router.post("/notebooks/{nb_id}/sources/{src_id}/links", status_code=201)
async def create_document_link(nb_id: str, src_id: str, body: LinkCreate, _user: dict = Depends(get_current_user)):
    """Create a link between two documents."""
    link_id = str(uuid.uuid4())
    with get_conn() as conn:
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
async def delete_document_link(nb_id: str, link_id: str, _user: dict = Depends(get_current_user)):
    """Delete a document link."""
    with get_conn() as conn:
        conn.execute("DELETE FROM document_links WHERE id=?", (link_id,))
    return {"deleted": link_id}
