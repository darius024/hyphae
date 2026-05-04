"""Notebook export endpoint — Markdown and BibTeX formats.

POST /api/notebooks/{nb_id}/export
Body: { "format": "markdown" | "bibtex" }

Markdown export includes:
  - Notebook metadata
  - Numbered source list
  - Full conversation history with inline citations

BibTeX export produces one @misc entry per source, with whatever
bibliographic detail is available (title, filename, url, page count).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from notebook.db import get_conn
from pydantic import BaseModel
from routes.auth import get_current_user

router = APIRouter(prefix="/api", tags=["export"])


# ── Pydantic model ──────────────────────────────────────────────────────

class ExportRequest(BaseModel):
    format: Literal["markdown", "bibtex"]


# ── Route ───────────────────────────────────────────────────────────────

@router.post("/notebooks/{nb_id}/export")
async def export_notebook(
    nb_id: str,
    body: ExportRequest,
    user: dict = Depends(get_current_user),
):
    """Export a notebook's sources and conversation history.

    Args:
        nb_id:  The notebook to export.
        body:   ``{"format": "markdown"}`` or ``{"format": "bibtex"}``.
        user:   Injected current user (auth required).

    Returns:
        A ``text/plain`` or ``text/markdown`` response with a
        ``Content-Disposition: attachment`` header so browsers trigger a
        download.

    Raises:
        404: If the notebook does not exist.
        403: If the notebook belongs to a different user.
    """
    conversations: list[dict] = []
    conv_messages: dict[str, list[dict]] = {}

    with get_conn() as conn:
        nb_row = conn.execute("SELECT * FROM notebooks WHERE id=?", (nb_id,)).fetchone()
        if nb_row is None:
            raise HTTPException(404, f"Notebook {nb_id!r} not found")
        nb = dict(nb_row)
        if nb.get("user_id") and nb["user_id"] != user["id"]:
            raise HTTPException(403, "Access denied")

        sources = [
            dict(r) for r in conn.execute(
                "SELECT * FROM sources WHERE notebook_id=? ORDER BY created_at ASC",
                (nb_id,),
            ).fetchall()
        ]

        if body.format == "markdown":
            conversations = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM conversations WHERE notebook_id=? ORDER BY created_at ASC",
                    (nb_id,),
                ).fetchall()
            ]
            for conv in conversations:
                rows = conn.execute(
                    "SELECT role, content, citations FROM messages "
                    "WHERE conversation_id=? ORDER BY created_at ASC",
                    (conv["id"],),
                ).fetchall()
                conv_messages[conv["id"]] = [dict(r) for r in rows]

    if body.format == "markdown":
        content = _render_markdown(nb, sources, conversations, conv_messages)
        media_type = "text/markdown; charset=utf-8"
        filename = f"{_slugify(nb['name'])}.md"
    else:
        content = _render_bibtex(nb, sources)
        media_type = "text/plain; charset=utf-8"
        filename = f"{_slugify(nb['name'])}.bib"

    return PlainTextResponse(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Markdown renderer ────────────────────────────────────────────────────

def _render_markdown(
    nb: dict,
    sources: list[dict],
    conversations: list[dict],
    conv_messages: dict[str, list[dict]],
) -> str:
    """Render the notebook as a Markdown document.

    The output structure is:
      1. Title & metadata block
      2. Numbered source list
      3. One H2 section per conversation with alternating user/assistant turns

    Citations stored in each assistant message are emitted as a footnote-style
    reference list at the end of that turn.
    """
    lines: list[str] = []
    exported_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    # ── Header ───────────────────────────────────────────────────────────
    lines.append(f"# {nb['name']}\n")
    if nb.get("description"):
        lines.append(f"> {nb['description']}\n")
    lines.append(f"*Exported from Hyphae on {exported_at}*\n")
    lines.append("---\n")

    # ── Sources ──────────────────────────────────────────────────────────
    lines.append("## Sources\n")
    if sources:
        for i, src in enumerate(sources, start=1):
            title = src.get("title") or src.get("filename") or "Untitled"
            parts = [f"{i}. **{title}**"]
            if src.get("filename") and src["filename"] != title:
                parts.append(f"(`{src['filename']}`)")
            if src.get("page_count"):
                parts.append(f"— {src['page_count']} pages")
            if src.get("url"):
                parts.append(f"— <{src['url']}>")
            lines.append(" ".join(parts))
        lines.append("")
    else:
        lines.append("*No sources uploaded to this notebook.*\n")
    lines.append("---\n")

    # ── Conversations ────────────────────────────────────────────────────
    lines.append("## Conversations\n")
    if not conversations:
        lines.append("*No conversations in this notebook.*\n")
    for conv in conversations:
        title = conv.get("title") or "Untitled conversation"
        lines.append(f"### {title}\n")
        messages = conv_messages.get(conv["id"], [])
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "user":
                lines.append(f"**You:** {content}\n")
            else:
                lines.append(f"**Assistant:** {content}\n")
                # Emit citation footnotes
                cit_json = msg.get("citations")
                if cit_json:
                    try:
                        cits = json.loads(cit_json) if isinstance(cit_json, str) else cit_json
                        if cits:
                            lines.append("*References:*")
                            for c in cits:
                                num   = c.get("number", "?")
                                stitle = c.get("source_title", "Unknown source")
                                page  = c.get("page_number")
                                page_str = f", p. {page}" if page else ""
                                lines.append(f"[{num}] {stitle}{page_str}")
                            lines.append("")
                    except (json.JSONDecodeError, TypeError):
                        pass
        lines.append("")

    return "\n".join(lines)


# ── BibTeX renderer ──────────────────────────────────────────────────────

def _render_bibtex(nb: dict, sources: list[dict]) -> str:
    """Render sources as BibTeX @misc entries.

    Each source becomes one entry.  The cite-key is derived from the
    source title/filename and a short hash of the source id, ensuring
    uniqueness even when titles are similar.
    """
    lines: list[str] = []
    exported_at = datetime.now(UTC).strftime("%Y-%m-%d")

    lines.append(f"% BibTeX export from Hyphae notebook: {nb['name']}")
    lines.append(f"% Exported on {exported_at}\n")

    if not sources:
        lines.append("% No sources in this notebook.")
        return "\n".join(lines)

    for src in sources:
        title   = src.get("title") or src.get("filename") or "Untitled"
        src_id  = src.get("id", "unknown")
        key     = _bibtex_key(title, src_id)
        year    = _year_from_iso(src.get("created_at", ""))

        fields: list[tuple[str, str]] = [("title", f"{{{_escape_bibtex(title)}}}")]
        if src.get("url"):
            fields.append(("howpublished", f"{{\\url{{{src['url']}}}}}"))
        elif src.get("filename"):
            fields.append(("howpublished", f"{{Local file: {_escape_bibtex(src['filename'])}}}"))
        if year:
            fields.append(("year", year))
        fields.append(("note", f"{{Exported from Hyphae notebook \\textit{{{_escape_bibtex(nb['name'])}}}}}"))

        entry_type = "misc"  # @online is BibLaTeX-only; @misc is universally supported
        field_lines = ",\n  ".join(f"{k} = {v}" for k, v in fields)
        lines.append(f"@{entry_type}{{{key},\n  {field_lines}\n}}\n")

    return "\n".join(lines)


# ── Utilities ────────────────────────────────────────────────────────────

_BIBTEX_SPECIAL = str.maketrans({
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "&": r"\&",
    "%": r"\%",
    "#": r"\#",
    "_": r"\_",
})


def _escape_bibtex(s: str) -> str:
    """Escape BibTeX special characters in user-supplied strings."""
    return str(s).translate(_BIBTEX_SPECIAL)


def _slugify(text: str) -> str:
    """Convert *text* to a filesystem-safe ASCII slug.

    Non-ASCII characters are stripped first so they don't appear verbatim
    in ``Content-Disposition`` headers, which must be ASCII-safe.
    """
    text = text.encode("ascii", "ignore").decode()  # strip non-ASCII
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s-]+", "-", text).strip("-") or "notebook"


def _bibtex_key(title: str, src_id: str) -> str:
    """Derive a unique, valid BibTeX cite-key from *title* and *src_id*."""
    slug = _slugify(title)[:30].strip("-")
    short_id = src_id.replace("-", "")[:6]
    return f"hyphae_{slug}_{short_id}" if slug else f"hyphae_{short_id}"


def _year_from_iso(iso: str) -> str:
    """Extract the 4-digit year from an ISO-8601 timestamp, or return ''."""
    if iso and len(iso) >= 4 and iso[:4].isdigit():
        return iso[:4]
    return ""
