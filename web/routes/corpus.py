"""Corpus document endpoints — upload, list, preview, delete, sensitivity."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import List

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api", tags=["corpus"])


class _SensitivityBody(BaseModel):
    level: str = Field(..., pattern=r"^(confidential|shareable)$")

# These are injected by app.py at startup
CORPUS_DIR: str = ""


_BAD_FILENAME_CHARS = re.compile(r'[\x00-\x1f\x7f/\\:]')
_RESERVED_NAMES = frozenset(
    [f"{p}{n}" for p in ("CON", "PRN", "AUX", "NUL", "COM", "LPT") for n in ("", *"123456789")]
)


def _safe_name(name: str) -> str:
    """Validate a user-supplied filename against traversal, null bytes, and OS-reserved names."""
    if not name or ".." in name:
        raise HTTPException(400, "Invalid filename")
    clean = Path(name).name
    if not clean or clean != name:
        raise HTTPException(400, "Invalid filename")
    if _BAD_FILENAME_CHARS.search(clean):
        raise HTTPException(400, "Invalid filename")
    if clean.split(".")[0].upper() in _RESERVED_NAMES:
        raise HTTPException(400, "Invalid filename")
    return clean
add_file = None
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

_HIDDEN_SUFFIXES = {".bin", ".idx", ".faiss", ".npy", ".pkl"}


def configure(corpus_dir: str, add_file_fn):
    """Called once from app.py to wire dependencies."""
    global CORPUS_DIR, add_file
    CORPUS_DIR = corpus_dir
    add_file = add_file_fn


def _sensitivity_path() -> Path:
    return Path(CORPUS_DIR) / ".sensitivity.json"


def _load_sensitivity() -> dict:
    p = _sensitivity_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}


def _save_sensitivity(data: dict):
    p = _sensitivity_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


@router.get("/documents")
async def list_documents():
    corpus = Path(CORPUS_DIR)
    if not corpus.is_dir():
        return {"documents": [], "count": 0}
    originals_dir = corpus / ".originals"
    sens = _load_sensitivity()
    docs = []
    for f in sorted(corpus.iterdir()):
        if not f.is_file() or f.name.startswith(".") or f.suffix.lower() in _HIDDEN_SUFFIXES:
            continue
        has_pdf = (originals_dir / (f.stem + ".pdf")).exists()
        docs.append({
            "name": f.name,
            "size_kb": round(f.stat().st_size / 1024, 1),
            "has_pdf": has_pdf,
            "type": "pdf" if has_pdf else f.suffix.lstrip(".").lower() or "txt",
            "sensitivity": sens.get(f.name, "shareable"),
        })
    return {"documents": docs, "count": len(docs)}


@router.post("/upload")
async def upload_documents(file: List[UploadFile] = File(...)):
    if add_file is None:
        raise HTTPException(503, "Corpus ingestion not available")
    originals_dir = Path(CORPUS_DIR) / ".originals"
    originals_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for f in file:
        if not f.filename:
            continue
        suffix = Path(f.filename).suffix.lower()
        raw_bytes = await f.read()
        if len(raw_bytes) > MAX_UPLOAD_BYTES:
            results.append({"filename": f.filename, "added": False, "error": "File too large"})
            continue
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(raw_bytes)
            tmp_path = tmp.name
        try:
            success = add_file(tmp_path, dest_name=Path(f.filename).stem + ".txt")
            if success and suffix == ".pdf":
                (originals_dir / f.filename).write_bytes(raw_bytes)
            results.append({"filename": f.filename, "added": bool(success)})
        finally:
            os.unlink(tmp_path)
    return {"uploaded": results}


@router.get("/documents/{name}")
async def preview_document(name: str):
    name = _safe_name(name)
    path = Path(CORPUS_DIR) / name
    if not path.exists():
        raise HTTPException(404, f"Not found: {name}")
    try:
        text = path.read_text(errors="replace")
    except Exception as exc:
        raise HTTPException(500, str(exc))
    originals_dir = Path(CORPUS_DIR) / ".originals"
    has_pdf = (originals_dir / (path.stem + ".pdf")).exists()
    return {
        "name": name,
        "preview": text,
        "size_kb": round(path.stat().st_size / 1024, 1),
        "has_pdf": has_pdf,
        "pdf_name": path.stem + ".pdf" if has_pdf else None,
    }


@router.get("/documents/{name}/raw")
async def raw_document(name: str):
    name = _safe_name(name)
    originals_dir = Path(CORPUS_DIR) / ".originals"
    pdf_path = originals_dir / name
    if pdf_path.exists() and pdf_path.suffix.lower() == ".pdf":
        return FileResponse(
            str(pdf_path),
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{name}"'},
        )
    text_path = Path(CORPUS_DIR) / name
    if text_path.exists():
        return FileResponse(
            str(text_path),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'inline; filename="{name}"'},
        )
    raise HTTPException(404, f"Not found: {name}")


@router.delete("/documents/{name}")
async def remove_document(name: str):
    name = _safe_name(name)
    path = Path(CORPUS_DIR) / name
    if not path.exists():
        raise HTTPException(404, f"Not found: {name}")
    path.unlink()
    originals_dir = Path(CORPUS_DIR) / ".originals"
    pdf_orig = originals_dir / (path.stem + ".pdf")
    if pdf_orig.exists():
        pdf_orig.unlink()
    return {"removed": name}


@router.get("/sensitivity")
async def get_sensitivity():
    return {"tags": _load_sensitivity()}


@router.put("/sensitivity/{name}")
async def set_sensitivity(name: str, body: _SensitivityBody):
    data = _load_sensitivity()
    data[name] = body.level
    _save_sensitivity(data)
    return {"name": name, "level": body.level}
