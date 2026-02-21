"""
Document ingestion pipeline.

extract → clean → chunk → embed → store in SQLite + FAISS
Supports: PDF (via PyMuPDF), TXT/MD, URLs (via httpx + trafilatura)
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import List, Tuple

log = logging.getLogger(__name__)

UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

CHUNK_SIZE    = 400   # words
CHUNK_OVERLAP = 80


# ── Text extraction ───────────────────────────────────────────────────────

def extract_pdf(file_path: str) -> Tuple[str, int]:
    try:
        import fitz  # type: ignore
        doc = fitz.open(file_path)
        pages = [page.get_text() for page in doc]
        doc.close()
        return "\n\n".join(pages), len(pages)
    except ImportError:
        raise RuntimeError("PyMuPDF required. pip install pymupdf")


def extract_text_file(file_path: str) -> str:
    return Path(file_path).read_text(errors="replace")


async def extract_url(url: str) -> str:
    try:
        import httpx          # type: ignore
        import trafilatura    # type: ignore
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            text = trafilatura.extract(resp.text) or resp.text
            return text
    except ImportError:
        raise RuntimeError("httpx + trafilatura required. pip install httpx trafilatura")


# ── Text cleaning ─────────────────────────────────────────────────────────

_MULTI_NL = re.compile(r"\n{3,}")
_MULTI_SP = re.compile(r"[ \t]{2,}")


def clean_text(text: str) -> str:
    text = _MULTI_NL.sub("\n\n", text)
    text = _MULTI_SP.sub(" ", text)
    return text.strip()


# ── Chunking ──────────────────────────────────────────────────────────────

def chunk_words(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    words = text.split()
    if not words:
        return []
    step = max(1, size - overlap)
    chunks = []
    start = 0
    while start < len(words):
        chunk = " ".join(words[start : start + size])
        if chunk:
            chunks.append(chunk)
        start += step
    return chunks


def chunk_pages(pages: List[str]) -> List[dict]:
    result = []
    idx = 0
    for page_num, page_text in enumerate(pages, start=1):
        cleaned = clean_text(page_text)
        for sub in chunk_words(cleaned):
            result.append({
                "page_number": page_num,
                "chunk_index": idx,
                "raw_text":    sub,
                "clean_text":  sub,
                "token_count": len(sub.split()),
            })
            idx += 1
    return result


# ── Main pipeline ─────────────────────────────────────────────────────────

def _set_source_status(source_id: str, status: str, error: str = None):
    from db import get_conn
    with get_conn() as conn:
        conn.execute(
            "UPDATE sources SET status=?, error=?, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
            (status, error, source_id),
        )


async def ingest_source(source_id: str) -> None:
    from db import get_conn
    from embed import embed
    from retrieval import add_chunks

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
    if row is None:
        log.error("ingest_source: source %s not found", source_id)
        return

    nb_id     = row["notebook_id"]
    src_type  = row["type"]
    filename  = row["filename"]
    url       = row["url"]

    _set_source_status(source_id, "processing")
    try:
        pages: List[str] = []
        page_count = 1

        if src_type == "pdf":
            full_path = str(UPLOAD_DIR / nb_id / filename)
            raw, page_count = extract_pdf(full_path)
            pages = raw.split("\n\n")
        elif src_type in ("txt", "md"):
            full_path = str(UPLOAD_DIR / nb_id / filename)
            pages = [extract_text_file(full_path)]
        elif src_type == "url":
            pages = [await extract_url(url)]
        else:
            raise ValueError(f"Unknown source type: {src_type}")

        chunk_dicts = chunk_pages(pages)
        if not chunk_dicts:
            raise ValueError("No text extracted")

        texts   = [c["clean_text"] for c in chunk_dicts]
        vectors = embed(texts)

        chunk_rows = []
        for cd, vec in zip(chunk_dicts, vectors):
            chunk_rows.append({
                "id":          str(uuid.uuid4()),
                "notebook_id": nb_id,
                "source_id":   source_id,
                "chunk_index": cd["chunk_index"],
                "page_number": cd.get("page_number"),
                "raw_text":    cd["raw_text"],
                "clean_text":  cd["clean_text"],
                "token_count": cd["token_count"],
                "_vec":        vec,
            })

        with get_conn() as conn:
            conn.execute(
                "UPDATE sources SET page_count=?, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                (page_count, source_id),
            )
            conn.executemany(
                """INSERT INTO chunks
                   (id, notebook_id, source_id, chunk_index, page_number,
                    raw_text, clean_text, token_count)
                   VALUES (:id,:notebook_id,:source_id,:chunk_index,:page_number,
                           :raw_text,:clean_text,:token_count)""",
                [{k: v for k, v in r.items() if k != "_vec"} for r in chunk_rows],
            )

        faiss_ids = add_chunks(
            nb_id,
            [r["id"] for r in chunk_rows],
            [r["_vec"] for r in chunk_rows],
        )

        with get_conn() as conn:
            conn.executemany(
                "UPDATE chunks SET faiss_id=? WHERE id=?",
                [(fid, r["id"]) for fid, r in zip(faiss_ids, chunk_rows)],
            )

        _set_source_status(source_id, "done")
        log.info("Ingested source %s: %d chunks, %d pages", source_id, len(chunk_rows), page_count)

    except Exception as exc:
        log.exception("ingest_source failed for %s", source_id)
        _set_source_status(source_id, "failed", str(exc))
