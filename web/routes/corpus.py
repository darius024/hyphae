"""Corpus document endpoints — upload, list, preview, delete, sensitivity."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Annotated, Callable, Optional

from routes.auth import get_current_user

router = APIRouter(prefix="/api", tags=["corpus"])


class _SensitivityBody(BaseModel):
    level: str = Field(..., pattern=r"^(confidential|shareable)$")

# Module-level state populated once at startup by configure().
CORPUS_DIR: str = ""
add_file: Optional[Callable] = None
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

_HIDDEN_SUFFIXES = {".bin", ".idx", ".faiss", ".npy", ".pkl"}
_sensitivity_lock = threading.Lock()


def configure(corpus_dir: str, add_file_fn):
    """Called once from app.py to wire dependencies."""
    global CORPUS_DIR, add_file
    CORPUS_DIR = corpus_dir
    add_file = add_file_fn


# ── Dependency providers ─────────────────────────────────────────────────

def _get_corpus_dir() -> str:
    """FastAPI dependency — resolves the configured corpus directory."""
    if not CORPUS_DIR:
        raise HTTPException(503, "Corpus not configured")
    return CORPUS_DIR


def _get_add_file() -> Callable:
    """FastAPI dependency — resolves the add_file ingestion function."""
    if add_file is None:
        raise HTTPException(503, "Corpus ingestion not available")
    return add_file


_CorpusDirDep = Annotated[str, Depends(_get_corpus_dir)]
_AddFileDep = Annotated[Callable, Depends(_get_add_file)]


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


def _sensitivity_path(corpus_dir: str) -> Path:
    return Path(corpus_dir) / ".sensitivity.json"


def _load_sensitivity(corpus_dir: str) -> dict:
    """Read sensitivity tags; must be called while holding _sensitivity_lock."""
    p = _sensitivity_path(corpus_dir)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}


def _save_sensitivity(corpus_dir: str, data: dict):
    """Write sensitivity tags atomically; must be called while holding _sensitivity_lock."""
    p = _sensitivity_path(corpus_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".sensitivity-")
    try:
        os.write(fd, payload.encode())
        os.fsync(fd)
        os.close(fd)
        os.replace(tmp, p)
    except Exception:
        os.close(fd)
        os.unlink(tmp)
        raise


@router.get("/documents")
async def list_documents(
    corpus_dir: _CorpusDirDep,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _user: dict = Depends(get_current_user),
):
    corpus = Path(corpus_dir)
    if not corpus.is_dir():
        return {"documents": [], "count": 0, "total": 0}
    originals_dir = corpus / ".originals"
    sens = _load_sensitivity(corpus_dir)
    all_docs = []
    for f in sorted(corpus.iterdir()):
        if not f.is_file() or f.name.startswith(".") or f.suffix.lower() in _HIDDEN_SUFFIXES:
            continue
        has_pdf = (originals_dir / (f.stem + ".pdf")).exists()
        all_docs.append({
            "name": f.name,
            "size_kb": round(f.stat().st_size / 1024, 1),
            "has_pdf": has_pdf,
            "type": "pdf" if has_pdf else f.suffix.lstrip(".").lower() or "txt",
            "sensitivity": sens.get(f.name, "shareable"),
        })
    page = all_docs[offset:offset + limit]
    return {"documents": page, "count": len(page), "total": len(all_docs)}


@router.post("/upload")
async def upload_documents(
    corpus_dir: _CorpusDirDep,
    add_file_fn: _AddFileDep,
    file: List[UploadFile] = File(...),
    _user: dict = Depends(get_current_user),
):
    originals_dir = Path(corpus_dir) / ".originals"
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
            success = add_file_fn(tmp_path, dest_name=Path(f.filename).stem + ".txt")
            if success and suffix == ".pdf":
                safe_pdf_name = _safe_name(f.filename)
                (originals_dir / safe_pdf_name).write_bytes(raw_bytes)
            results.append({"filename": f.filename, "added": bool(success)})
        finally:
            os.unlink(tmp_path)
    return {"uploaded": results}


@router.get("/documents/{name}")
async def preview_document(name: str, corpus_dir: _CorpusDirDep, _user: dict = Depends(get_current_user)):
    name = _safe_name(name)
    path = Path(corpus_dir) / name
    if not path.exists():
        raise HTTPException(404, f"Not found: {name}")
    try:
        text = path.read_text(errors="replace")
    except Exception as exc:
        raise HTTPException(500, str(exc))
    originals_dir = Path(corpus_dir) / ".originals"
    has_pdf = (originals_dir / (path.stem + ".pdf")).exists()
    return {
        "name": name,
        "preview": text,
        "size_kb": round(path.stat().st_size / 1024, 1),
        "has_pdf": has_pdf,
        "pdf_name": path.stem + ".pdf" if has_pdf else None,
    }


@router.get("/documents/{name}/raw")
async def raw_document(name: str, corpus_dir: _CorpusDirDep, _user: dict = Depends(get_current_user)):
    name = _safe_name(name)
    originals_dir = Path(corpus_dir) / ".originals"
    pdf_path = originals_dir / name
    if pdf_path.exists() and pdf_path.suffix.lower() == ".pdf":
        return FileResponse(
            str(pdf_path),
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{name}"'},
        )
    text_path = Path(corpus_dir) / name
    if text_path.exists():
        return FileResponse(
            str(text_path),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'inline; filename="{name}"'},
        )
    raise HTTPException(404, f"Not found: {name}")


@router.delete("/documents/{name}")
async def remove_document(name: str, corpus_dir: _CorpusDirDep, _user: dict = Depends(get_current_user)):
    name = _safe_name(name)
    path = Path(corpus_dir) / name
    if not path.exists():
        raise HTTPException(404, f"Not found: {name}")
    path.unlink()
    originals_dir = Path(corpus_dir) / ".originals"
    pdf_orig = originals_dir / (path.stem + ".pdf")
    if pdf_orig.exists():
        pdf_orig.unlink()
    return {"removed": name}


@router.get("/sensitivity")
async def get_sensitivity(corpus_dir: _CorpusDirDep, _user: dict = Depends(get_current_user)):
    with _sensitivity_lock:
        return {"tags": _load_sensitivity(corpus_dir)}


@router.put("/sensitivity/{name}")
async def set_sensitivity(name: str, body: _SensitivityBody, corpus_dir: _CorpusDirDep, _user: dict = Depends(get_current_user)):
    with _sensitivity_lock:
        data = _load_sensitivity(corpus_dir)
        data[name] = body.level
        _save_sensitivity(corpus_dir, data)
    return {"name": name, "level": body.level}
