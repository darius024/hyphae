"""
Code & Git API routes for the Hyphae Code IDE.

Users connect their own GitHub/GitLab/etc repo via HTTPS URL.
The server clones it into a workspace folder, and all file/git
operations target that cloned repo.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from routes.auth import get_current_user

log = logging.getLogger(__name__)

router = APIRouter()

# ── Workspace directory for cloned repos ──────────────────────────────────
_WEB_DIR = Path(__file__).resolve().parents[1]            # …/hyphae/web
WORKSPACE_DIR = _WEB_DIR.parent / "code_workspace"        # …/hyphae/code_workspace
WORKSPACE_DIR.mkdir(exist_ok=True)
STATE_FILE = WORKSPACE_DIR / ".code_state.json"

# Currently active repo root (set after clone or reconnect)
_active_repo: Optional[Path] = None


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"repos": [], "active": None}


def _save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _repo_root() -> Path:
    global _active_repo
    if _active_repo and _active_repo.exists():
        return _active_repo
    # Try to restore from state
    state = _load_state()
    if state.get("active"):
        p = Path(state["active"])
        if p.exists():
            _active_repo = p
            return p
    raise HTTPException(400, "No repository connected. Clone a repo first.")


def _set_active(path: Path, url: str):
    global _active_repo
    _active_repo = path
    state = _load_state()
    state["active"] = str(path)
    # Add/update in recents
    repos = [r for r in state.get("repos", []) if r["url"] != url]
    repos.insert(0, {"url": url, "path": str(path), "name": _repo_name_from_url(url)})
    state["repos"] = repos[:10]  # Keep last 10
    _save_state(state)


def _repo_name_from_url(url: str) -> str:
    """Extract 'owner/repo' from a git URL."""
    m = re.search(r'[/:]([^/:]+/[^/.]+?)(?:\.git)?$', url)
    return m.group(1) if m else url.split("/")[-1].replace(".git", "")


# ── Helpers ───────────────────────────────────────────────────────────────

def _safe_path(rel: str) -> Path:
    root = _repo_root()
    resolved = (root / rel).resolve()
    if not str(resolved).startswith(str(root)):
        raise HTTPException(403, "Path traversal not allowed")
    return resolved


def _git(*args: str) -> subprocess.CompletedProcess:
    root = _repo_root()
    cmd = ["git", "-C", str(root)] + list(args)
    log.info("git %s", " ".join(args))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)


_SAFE_BRANCH_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._/\-]*$')
_INTERNAL_HOST_RE = re.compile(
    r'://(localhost|127\.|10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|0\.0\.0\.0|\[::1\])',
    re.IGNORECASE,
)


def _validate_clone_url(url: str) -> str:
    """Reject non-HTTPS URLs and those targeting internal networks."""
    url = url.strip()
    if not url:
        raise HTTPException(400, "URL is required")
    if not url.startswith("https://"):
        raise HTTPException(400, "Only HTTPS clone URLs are allowed")
    if _INTERNAL_HOST_RE.search(url):
        raise HTTPException(400, "Internal/loopback URLs are not allowed")
    return url


def _safe_git_arg(arg: str) -> str:
    """Reject arguments that look like git flags to prevent option injection."""
    if arg.startswith("-"):
        raise HTTPException(400, f"Invalid argument: {arg!r}")
    return arg


IGNORED_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", ".venv-fast",
    "venv", ".mypy_cache", ".pytest_cache", ".tox", "dist",
    "build", ".egg-info", ".eggs",
}
IGNORED_FILES = {".DS_Store", "Thumbs.db"}
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB


# ══════════════════════════════════════════════════════════════════════════
#  Clone / Connect / Disconnect
# ══════════════════════════════════════════════════════════════════════════

class CloneRequest(BaseModel):
    url: str = Field(..., min_length=1)


@router.post("/api/code/clone")
async def code_clone(req: CloneRequest, _user: dict = Depends(get_current_user)):
    """Clone a git repo from HTTPS URL into the workspace."""
    url = _validate_clone_url(req.url)

    repo_name = _repo_name_from_url(url)
    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', repo_name)
    dest = WORKSPACE_DIR / safe_name

    if dest.exists():
        # Already cloned — just pull latest
        log.info("Repo already exists at %s, pulling...", dest)
        result = subprocess.run(
            ["git", "-C", str(dest), "pull", "--ff-only"],
            capture_output=True, text=True, timeout=60, check=False
        )
        _set_active(dest, url)
        return {
            "ok": True, "path": str(dest), "name": repo_name,
            "message": "Repository updated (pull)" if result.returncode == 0
                       else "Repository opened (pull skipped)"
        }

    # Clone
    log.info("Cloning %s into %s", url, dest)
    result = subprocess.run(
        ["git", "clone", "--depth", "50", url, str(dest)],
        capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode != 0:
        raise HTTPException(400, result.stderr or "Clone failed")

    _set_active(dest, url)
    return {"ok": True, "path": str(dest), "name": repo_name, "message": "Repository cloned successfully"}


@router.get("/api/code/repos")
async def code_repos(_user: dict = Depends(get_current_user)):
    """List recent/cloned repos."""
    state = _load_state()
    repos = []
    for r in state.get("repos", []):
        exists = Path(r["path"]).exists()
        repos.append({**r, "exists": exists})
    return {"repos": repos, "active": state.get("active")}


class ConnectRequest(BaseModel):
    path: str
    url: str


@router.post("/api/code/connect")
async def code_connect(req: ConnectRequest, _user: dict = Depends(get_current_user)):
    """Re-connect to a previously cloned repo."""
    p = Path(req.path).resolve()
    if not str(p).startswith(str(WORKSPACE_DIR)):
        raise HTTPException(403, "Cannot connect to paths outside workspace")
    if not p.exists():
        raise HTTPException(404, "Repository folder not found. It may have been deleted.")
    _set_active(p, req.url)
    return {"ok": True, "name": _repo_name_from_url(req.url)}


@router.post("/api/code/disconnect")
async def code_disconnect(_user: dict = Depends(get_current_user)):
    """Disconnect the current repo (does NOT delete files)."""
    global _active_repo
    _active_repo = None
    state = _load_state()
    state["active"] = None
    _save_state(state)
    return {"ok": True}


class DeleteRepoRequest(BaseModel):
    path: str


@router.post("/api/code/delete-repo")
async def code_delete_repo(req: DeleteRepoRequest, _user: dict = Depends(get_current_user)):
    """Delete a cloned repo from disk."""
    p = Path(req.path)
    if not str(p.resolve()).startswith(str(WORKSPACE_DIR)):
        raise HTTPException(403, "Cannot delete paths outside workspace")
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    state = _load_state()
    state["repos"] = [r for r in state.get("repos", []) if r["path"] != req.path]
    if state.get("active") == req.path:
        state["active"] = None
    _save_state(state)
    global _active_repo
    if _active_repo and str(_active_repo) == req.path:
        _active_repo = None
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════════════
#  Code / File routes
# ══════════════════════════════════════════════════════════════════════════

@router.get("/api/code/tree")
async def code_tree(_user: dict = Depends(get_current_user)):
    """Return directory tree as nested JSON."""
    root = _repo_root()
    def walk(p: Path, rel: str = ""):
        children = []
        try:
            entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except PermissionError:
            return {"name": p.name, "is_dir": True, "children": []}

        for entry in entries:
            if entry.name in IGNORED_DIRS or entry.name in IGNORED_FILES:
                continue
            if entry.name.startswith(".") and entry.name not in {".env", ".gitignore", ".flake8", ".editorconfig"}:
                continue
            child_rel = f"{rel}/{entry.name}" if rel else entry.name
            if entry.is_dir():
                children.append(walk(entry, child_rel))
            else:
                children.append({"name": entry.name, "is_dir": False})
        return {"name": p.name, "is_dir": True, "children": children}

    return walk(root)


@router.get("/api/code/read")
async def code_read(path: str = Query(...), _user: dict = Depends(get_current_user)):
    """Read a file's content."""
    fp = _safe_path(path)
    if not fp.exists():
        raise HTTPException(404, f"File not found: {path}")
    if not fp.is_file():
        raise HTTPException(400, "Not a file")
    if fp.stat().st_size > MAX_FILE_SIZE:
        raise HTTPException(400, "File too large (>2MB)")
    try:
        content = fp.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"path": path, "content": content}


class WriteRequest(BaseModel):
    path: str
    content: str


@router.post("/api/code/write")
async def code_write(req: WriteRequest, _user: dict = Depends(get_current_user)):
    """Write content to a file (creates directories if needed)."""
    fp = _safe_path(req.path)
    fp.parent.mkdir(parents=True, exist_ok=True)
    try:
        fp.write_text(req.content, encoding="utf-8")
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"ok": True, "path": req.path}


class MkdirRequest(BaseModel):
    path: str


@router.post("/api/code/mkdir")
async def code_mkdir(req: MkdirRequest, _user: dict = Depends(get_current_user)):
    """Create a directory."""
    fp = _safe_path(req.path)
    fp.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": req.path}


@router.get("/api/code/search")
async def code_search(q: str = Query(...), _user: dict = Depends(get_current_user)):
    """Search for text across the repository using grep."""
    if not q.strip():
        return {"results": []}
    result = _git("grep", "-n", "-i", "--max-count=5", "-r", q, "--", ".")
    if not result.stdout:
        return {"results": []}
    # Parse grep output: file:line:text
    by_file: dict[str, list] = {}
    for line in result.stdout.strip().split("\n")[:100]:
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        fpath, lineno, text = parts
        # Remove leading "./" if present
        if fpath.startswith("./"):
            fpath = fpath[2:]
        if fpath not in by_file:
            by_file[fpath] = []
        by_file[fpath].append({"line": int(lineno), "text": text[:200]})

    results = [{"file": f, "matches": m} for f, m in by_file.items()]
    return {"results": results[:20]}


# ── File preview (binary: PDF, images, media) ────────────────────────────

# Extension → MIME mapping for preview-able files
_PREVIEW_MIME = {
    # PDF
    "pdf": "application/pdf",
    # Images
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "svg": "image/svg+xml", "webp": "image/webp",
    "ico": "image/x-icon", "bmp": "image/bmp",
    # Audio
    "mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg",
    "flac": "audio/flac", "aac": "audio/aac", "m4a": "audio/mp4",
    # Video
    "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
    "avi": "video/x-msvideo", "mkv": "video/x-matroska",
    # Fonts (optional preview)
    "woff": "font/woff", "woff2": "font/woff2", "ttf": "font/ttf",
}

PREVIEW_EXTENSIONS = set(_PREVIEW_MIME.keys())
MAX_PREVIEW_SIZE = 100 * 1024 * 1024   # 100 MB cap for media


@router.get("/api/code/preview")
async def code_preview(path: str = Query(...), _user: dict = Depends(get_current_user)):
    """Serve a binary file for preview (PDF, images, audio, video)."""
    fp = _safe_path(path)
    if not fp.exists():
        raise HTTPException(404, f"File not found: {path}")
    if not fp.is_file():
        raise HTTPException(400, "Not a file")

    ext = fp.suffix.lstrip(".").lower()
    mime = _PREVIEW_MIME.get(ext)
    if not mime:
        raise HTTPException(400, f"Unsupported preview type: .{ext}")

    if fp.stat().st_size > MAX_PREVIEW_SIZE:
        raise HTTPException(400, "File too large for preview (>100MB)")

    return FileResponse(
        path=str(fp),
        media_type=mime,
        headers={"Content-Disposition": f'inline; filename="{fp.name}"'},
    )


# ══════════════════════════════════════════════════════════════════════════
#  Git routes
# ══════════════════════════════════════════════════════════════════════════

@router.get("/api/git/status")
async def git_status(_user: dict = Depends(get_current_user)):
    """Return staged and unstaged file lists."""
    # Staged files
    staged_result = _git("diff", "--cached", "--name-status")
    staged = []
    if staged_result.stdout:
        for line in staged_result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                staged.append({"status": parts[0], "path": parts[1]})

    # Unstaged (modified + untracked)
    unstaged_result = _git("status", "--porcelain", "-u")
    unstaged = []
    staged_paths = {s["path"] for s in staged}
    if unstaged_result.stdout:
        for line in unstaged_result.stdout.strip().split("\n"):
            if not line.strip() or len(line) < 4:
                continue
            xy = line[:2]
            fpath = line[3:].strip()
            # Skip files already in staged
            if fpath in staged_paths and xy[1] == ' ':
                continue
            status = 'M'
            if xy[1] == 'M' or xy[0] == 'M':
                status = 'M'
            elif xy == '??':
                status = '?'
            elif xy[1] == 'D' or xy[0] == 'D':
                status = 'D'
            elif xy[1] == 'A' or xy[0] == 'A':
                status = 'A'
            unstaged.append({"status": status, "path": fpath})

    return {"staged": staged, "unstaged": unstaged}


@router.get("/api/git/diff")
async def git_diff(path: str = Query(""), _user: dict = Depends(get_current_user)):
    """Get diff for a specific file or the whole repo."""
    if path:
        _safe_git_arg(path)
        result = _git("diff", "--cached", "--", path)
        if not result.stdout.strip():
            result = _git("diff", "--", path)
        if not result.stdout.strip():
            # Maybe untracked — show full file content
            result = _git("diff", "--no-index", "/dev/null", path)
    else:
        result = _git("diff")
    return {"diff": result.stdout or "(no changes)"}


class StageRequest(BaseModel):
    paths: list[str]


@router.post("/api/git/stage")
async def git_stage(req: StageRequest, _user: dict = Depends(get_current_user)):
    """Stage files."""
    for p in req.paths:
        _git("add", "--", _safe_git_arg(p))
    return {"ok": True}


@router.post("/api/git/unstage")
async def git_unstage(req: StageRequest, _user: dict = Depends(get_current_user)):
    """Unstage files."""
    for p in req.paths:
        _git("reset", "HEAD", "--", _safe_git_arg(p))
    return {"ok": True}


class CommitRequest(BaseModel):
    message: str = Field(..., min_length=1)


@router.post("/api/git/commit")
async def git_commit(req: CommitRequest, _user: dict = Depends(get_current_user)):
    """Commit staged changes."""
    result = _git("commit", "-m", req.message)
    if result.returncode != 0:
        raise HTTPException(400, result.stderr or "Commit failed")
    return {"ok": True, "output": result.stdout}


@router.post("/api/git/push")
async def git_push(_user: dict = Depends(get_current_user)):
    """Push to origin."""
    result = _git("push", "origin", "HEAD")
    if result.returncode != 0:
        # Try with set-upstream
        result2 = _git("push", "--set-upstream", "origin", "HEAD")
        if result2.returncode != 0:
            raise HTTPException(400, result2.stderr or "Push failed")
        return {"ok": True, "output": result2.stdout + result2.stderr}
    return {"ok": True, "output": result.stdout + result.stderr}


@router.post("/api/git/pull")
async def git_pull(_user: dict = Depends(get_current_user)):
    """Pull from origin."""
    result = _git("pull", "--rebase")
    if result.returncode != 0:
        raise HTTPException(400, result.stderr or "Pull failed")
    return {"ok": True, "output": result.stdout}


@router.get("/api/git/branches")
async def git_branches(_user: dict = Depends(get_current_user)):
    """List all branches and the current one."""
    result = _git("branch", "-a")
    current = "main"
    all_branches = []
    if result.stdout:
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line.startswith("* "):
                current = line[2:].strip()
                all_branches.append(current)
            elif line.startswith("remotes/origin/"):
                name = line.replace("remotes/origin/", "")
                if name != "HEAD" and not name.startswith("HEAD "):
                    if name not in all_branches:
                        all_branches.append(name)
            else:
                if line and line not in all_branches:
                    all_branches.append(line)
    return {"current": current, "all": all_branches}


class CheckoutRequest(BaseModel):
    branch: str = Field(..., min_length=1)
    create: bool = False


@router.post("/api/git/checkout")
async def git_checkout(req: CheckoutRequest, _user: dict = Depends(get_current_user)):
    """Switch branch or create a new one."""
    if not _SAFE_BRANCH_RE.match(req.branch):
        raise HTTPException(400, "Invalid branch name")
    if req.create:
        result = _git("checkout", "-b", req.branch)
    else:
        result = _git("checkout", req.branch)
    if result.returncode != 0:
        raise HTTPException(400, result.stderr or "Checkout failed")
    return {"ok": True, "branch": req.branch}


@router.get("/api/git/log")
async def git_log(n: int = Query(20, ge=1, le=100), _user: dict = Depends(get_current_user)):
    """Return recent commits."""
    result = _git("log", f"--max-count={n}", "--format=%H|%an|%ai|%s")
    commits = []
    if result.stdout:
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("|", 3)
            if len(parts) >= 4:
                commits.append({
                    "hash": parts[0],
                    "author": parts[1],
                    "date": parts[2][:10],
                    "message": parts[3],
                })
    return {"commits": commits}
