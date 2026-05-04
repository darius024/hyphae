"""Code & Git API routes for the Hyphae Code IDE.

Each authenticated user has their own private workspace.  Repositories are
cloned under ``code_workspace/<user_id>/<safe_repo_name>`` and the active
repo is tracked per-user in the ``code_repos`` SQLite table.  This replaces
the previous global ``_active_repo`` variable, which leaked one user's repo
state to every other authenticated user.

A per-user :class:`asyncio.Lock` serialises git operations so concurrent
requests from the same user cannot race on the working tree.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from notebook.db import get_conn
from pydantic import BaseModel, Field
from routes.auth import get_current_user

log = logging.getLogger(__name__)

router = APIRouter()

# ── Workspace directory ─────────────────────────────────────────────────────

_WEB_DIR = Path(__file__).resolve().parents[1]            # …/hyphae/web
WORKSPACE_DIR = _WEB_DIR.parent / "code_workspace"        # …/hyphae/code_workspace
WORKSPACE_DIR.mkdir(exist_ok=True)


def _user_workspace(user_id: str) -> Path:
    """Return (creating if needed) the per-user workspace directory.

    The user_id is validated against an allow-list of UUID/hex-style
    characters before being joined to the workspace path.  This blocks
    path traversal via crafted user-id values.
    """
    if not re.fullmatch(r"[a-zA-Z0-9_\-]+", user_id):
        raise HTTPException(400, "Invalid user id")
    user_dir = WORKSPACE_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


# ── Per-user concurrency control ────────────────────────────────────────────

_user_locks: dict[str, asyncio.Lock] = {}
_user_locks_mu = asyncio.Lock()


async def _get_user_lock(user_id: str) -> asyncio.Lock:
    """Return (creating lazily) the per-user asyncio lock."""
    if user_id in _user_locks:
        return _user_locks[user_id]
    async with _user_locks_mu:
        if user_id not in _user_locks:
            _user_locks[user_id] = asyncio.Lock()
        return _user_locks[user_id]


# ── Repo state (DB-backed, per-user) ────────────────────────────────────────

def _repo_name_from_url(url: str) -> str:
    """Extract 'owner/repo' from a git URL."""
    match = re.search(r"[/:]([^/:]+/[^/.]+?)(?:\.git)?$", url)
    return match.group(1) if match else url.split("/")[-1].replace(".git", "")


def _safe_repo_dirname(url: str) -> str:
    """Convert a repo URL to a filesystem-safe directory name."""
    name = _repo_name_from_url(url)
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", name)


def _set_active_repo(user_id: str, url: str, path: Path) -> None:
    """Insert/update the user's repo row and mark it active.

    All other repos for this user are demoted to ``is_active=0`` in a single
    transaction so the (user_id, is_active) state stays single-valued.
    """
    name = _repo_name_from_url(url)
    repo_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "UPDATE code_repos SET is_active=0 WHERE user_id=?",
            (user_id,),
        )
        conn.execute(
            """INSERT INTO code_repos (id, user_id, url, path, name, is_active, last_active_at)
               VALUES (?, ?, ?, ?, ?, 1, strftime('%Y-%m-%dT%H:%M:%SZ','now'))
               ON CONFLICT(user_id, url) DO UPDATE SET
                   path=excluded.path,
                   name=excluded.name,
                   is_active=1,
                   last_active_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')""",
            (repo_id, user_id, url, str(path), name),
        )


def _get_active_repo(user_id: str) -> Path:
    """Return the path to the currently active repo for *user_id*.

    Raises 400 when no repo has been connected.  Verifies the recorded
    path still exists on disk; if not, the row is cleared and a 400 is
    raised so the client can prompt the user to re-clone.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT path FROM code_repos WHERE user_id=? AND is_active=1",
            (user_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(400, "No repository connected. Clone a repo first.")
    path = Path(row["path"])
    if not path.exists():
        with get_conn() as conn:
            conn.execute(
                "UPDATE code_repos SET is_active=0 WHERE user_id=? AND path=?",
                (user_id, str(path)),
            )
        raise HTTPException(400, "Active repository folder is missing — please re-clone.")
    return path


def _list_repos(user_id: str) -> list[dict]:
    """Return all repos this user has cloned, newest first."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT url, path, name, is_active
               FROM code_repos WHERE user_id=?
               ORDER BY last_active_at DESC LIMIT 20""",
            (user_id,),
        ).fetchall()
    repos = []
    for row in rows:
        repos.append({
            "url": row["url"],
            "path": row["path"],
            "name": row["name"],
            "exists": Path(row["path"]).exists(),
            "active": bool(row["is_active"]),
        })
    return repos


def _get_active_path_or_none(user_id: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT path FROM code_repos WHERE user_id=? AND is_active=1",
            (user_id,),
        ).fetchone()
    return row["path"] if row else None


# ── Path / argument safety ──────────────────────────────────────────────────

def _safe_path(user_id: str, rel: str) -> Path:
    """Resolve *rel* under the user's active repo, blocking path traversal."""
    root = _get_active_repo(user_id)
    resolved = (root / rel).resolve()
    if not resolved.is_relative_to(root):
        raise HTTPException(403, "Path traversal not allowed")
    return resolved


def _git(user_id: str, *args: str) -> subprocess.CompletedProcess:
    """Run ``git`` against *user_id*'s active repo."""
    root = _get_active_repo(user_id)
    cmd = ["git", "-C", str(root), *list(args)]
    log.info("user=%s git %s", user_id, " ".join(args))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)


_SAFE_BRANCH_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/\-]*$")
_INTERNAL_HOST_RE = re.compile(
    r"://(localhost|127\.|10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|0\.0\.0\.0|\[::1\])",
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
async def code_clone(req: CloneRequest, user: dict = Depends(get_current_user)):
    """Clone a git repo from HTTPS URL into the *current user's* workspace."""
    url = _validate_clone_url(req.url)
    user_id = user["id"]
    lock = await _get_user_lock(user_id)

    async with lock:
        user_dir = _user_workspace(user_id)
        dest = user_dir / _safe_repo_dirname(url)

        if dest.exists():
            log.info("user=%s repo already exists at %s, pulling", user_id, dest)
            result = subprocess.run(
                ["git", "-C", str(dest), "pull", "--ff-only"],
                capture_output=True, text=True, timeout=60, check=False,
            )
            _set_active_repo(user_id, url, dest)
            return {
                "ok": True,
                "path": str(dest),
                "name": _repo_name_from_url(url),
                "message": (
                    "Repository updated (pull)" if result.returncode == 0
                    else "Repository opened (pull skipped)"
                ),
            }

        log.info("user=%s cloning %s into %s", user_id, url, dest)
        result = subprocess.run(
            ["git", "clone", "--depth", "50", url, str(dest)],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if result.returncode != 0:
            raise HTTPException(400, result.stderr or "Clone failed")

        _set_active_repo(user_id, url, dest)
        return {
            "ok": True,
            "path": str(dest),
            "name": _repo_name_from_url(url),
            "message": "Repository cloned successfully",
        }


@router.get("/api/code/repos")
async def code_repos(user: dict = Depends(get_current_user)):
    """List the current user's cloned repos."""
    user_id = user["id"]
    return {"repos": _list_repos(user_id), "active": _get_active_path_or_none(user_id)}


class ConnectRequest(BaseModel):
    path: str
    url: str


@router.post("/api/code/connect")
async def code_connect(req: ConnectRequest, user: dict = Depends(get_current_user)):
    """Re-connect to a previously cloned repo owned by *this* user."""
    user_id = user["id"]
    user_dir = _user_workspace(user_id)
    target = Path(req.path).resolve()
    # Path must live inside *this* user's workspace — never allow another
    # user's clone, and never escape the workspace root.
    if not target.is_relative_to(user_dir):
        raise HTTPException(403, "Cannot connect to paths outside your workspace")
    if not target.exists():
        raise HTTPException(404, "Repository folder not found. It may have been deleted.")
    _set_active_repo(user_id, req.url, target)
    return {"ok": True, "name": _repo_name_from_url(req.url)}


@router.post("/api/code/disconnect")
async def code_disconnect(user: dict = Depends(get_current_user)):
    """Clear the active repo for this user (does NOT delete files)."""
    with get_conn() as conn:
        conn.execute("UPDATE code_repos SET is_active=0 WHERE user_id=?", (user["id"],))
    return {"ok": True}


class DeleteRepoRequest(BaseModel):
    path: str


@router.post("/api/code/delete-repo")
async def code_delete_repo(req: DeleteRepoRequest, user: dict = Depends(get_current_user)):
    """Delete a cloned repo from disk (only if it belongs to this user)."""
    user_id = user["id"]
    user_dir = _user_workspace(user_id)
    target = Path(req.path).resolve()
    if not target.is_relative_to(user_dir):
        raise HTTPException(403, "Cannot delete paths outside your workspace")
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM code_repos WHERE user_id=? AND path=?",
            (user_id, str(target)),
        )
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════════════
#  Code / File routes
# ══════════════════════════════════════════════════════════════════════════

@router.get("/api/code/tree")
async def code_tree(user: dict = Depends(get_current_user)):
    """Return the active repo's directory tree as nested JSON."""
    root = _get_active_repo(user["id"])

    def walk(directory: Path, rel: str = ""):
        children = []
        try:
            entries = sorted(directory.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except PermissionError:
            return {"name": directory.name, "is_dir": True, "children": []}

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
        return {"name": directory.name, "is_dir": True, "children": children}

    return walk(root)


@router.get("/api/code/read")
async def code_read(path: str = Query(...), user: dict = Depends(get_current_user)):
    """Read a file's content from the active repo."""
    fpath = _safe_path(user["id"], path)
    if not fpath.exists():
        raise HTTPException(404, f"File not found: {path}")
    if not fpath.is_file():
        raise HTTPException(400, "Not a file")
    if fpath.stat().st_size > MAX_FILE_SIZE:
        raise HTTPException(400, "File too large (>2MB)")
    try:
        content = fpath.read_text(encoding="utf-8", errors="replace")
    except Exception as error:
        raise HTTPException(500, str(error))
    return {"path": path, "content": content}


class WriteRequest(BaseModel):
    path: str
    content: str


@router.post("/api/code/write")
async def code_write(req: WriteRequest, user: dict = Depends(get_current_user)):
    """Write content to a file in the active repo (creates dirs as needed)."""
    fpath = _safe_path(user["id"], req.path)
    fpath.parent.mkdir(parents=True, exist_ok=True)
    try:
        fpath.write_text(req.content, encoding="utf-8")
    except Exception as error:
        raise HTTPException(500, str(error))
    return {"ok": True, "path": req.path}


class MkdirRequest(BaseModel):
    path: str


@router.post("/api/code/mkdir")
async def code_mkdir(req: MkdirRequest, user: dict = Depends(get_current_user)):
    """Create a directory inside the active repo."""
    fpath = _safe_path(user["id"], req.path)
    fpath.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": req.path}


@router.get("/api/code/search")
async def code_search(q: str = Query(...), user: dict = Depends(get_current_user)):
    """Search for text across the active repository using ``git grep``."""
    if not q.strip():
        return {"results": []}
    result = _git(user["id"], "grep", "-n", "-i", "--max-count=5", "-r", "-e", q, "--", ".")
    if not result.stdout:
        return {"results": []}
    by_file: dict[str, list] = {}
    for line in result.stdout.strip().split("\n")[:100]:
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        fpath, lineno, text = parts
        if fpath.startswith("./"):
            fpath = fpath[2:]
        if fpath not in by_file:
            by_file[fpath] = []
        by_file[fpath].append({"line": int(lineno), "text": text[:200]})

    results = [{"file": fname, "matches": matches} for fname, matches in by_file.items()]
    return {"results": results[:20]}


# ── Binary preview ────────────────────────────────────────────────────────

_PREVIEW_MIME = {
    "pdf": "application/pdf",
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "svg": "image/svg+xml", "webp": "image/webp",
    "ico": "image/x-icon", "bmp": "image/bmp",
    "mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg",
    "flac": "audio/flac", "aac": "audio/aac", "m4a": "audio/mp4",
    "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
    "avi": "video/x-msvideo", "mkv": "video/x-matroska",
    "woff": "font/woff", "woff2": "font/woff2", "ttf": "font/ttf",
}

PREVIEW_EXTENSIONS = set(_PREVIEW_MIME.keys())
MAX_PREVIEW_SIZE = 100 * 1024 * 1024


@router.get("/api/code/preview")
async def code_preview(path: str = Query(...), user: dict = Depends(get_current_user)):
    """Serve a binary file (PDF/image/audio/video) from the active repo."""
    fpath = _safe_path(user["id"], path)
    if not fpath.exists():
        raise HTTPException(404, f"File not found: {path}")
    if not fpath.is_file():
        raise HTTPException(400, "Not a file")

    ext = fpath.suffix.lstrip(".").lower()
    mime = _PREVIEW_MIME.get(ext)
    if not mime:
        raise HTTPException(400, f"Unsupported preview type: .{ext}")

    if fpath.stat().st_size > MAX_PREVIEW_SIZE:
        raise HTTPException(400, "File too large for preview (>100MB)")

    return FileResponse(
        path=str(fpath),
        media_type=mime,
        headers={"Content-Disposition": f'inline; filename="{fpath.name}"'},
    )


# ══════════════════════════════════════════════════════════════════════════
#  Git routes (per-user serialised via _get_user_lock)
# ══════════════════════════════════════════════════════════════════════════

@router.get("/api/git/status")
async def git_status(user: dict = Depends(get_current_user)):
    """Return staged and unstaged file lists."""
    user_id = user["id"]
    staged_result = _git(user_id, "diff", "--cached", "--name-status")
    staged = []
    if staged_result.stdout:
        for line in staged_result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                staged.append({"status": parts[0], "path": parts[1]})

    unstaged_result = _git(user_id, "status", "--porcelain", "-u")
    unstaged = []
    staged_paths = {entry["path"] for entry in staged}
    if unstaged_result.stdout:
        for line in unstaged_result.stdout.strip().split("\n"):
            if not line.strip() or len(line) < 4:
                continue
            xy = line[:2]
            fpath = line[3:].strip()
            if fpath in staged_paths and xy[1] == " ":
                continue
            status = "M"
            if xy[1] == "M" or xy[0] == "M":
                status = "M"
            elif xy == "??":
                status = "?"
            elif xy[1] == "D" or xy[0] == "D":
                status = "D"
            elif xy[1] == "A" or xy[0] == "A":
                status = "A"
            unstaged.append({"status": status, "path": fpath})

    return {"staged": staged, "unstaged": unstaged}


@router.get("/api/git/diff")
async def git_diff(path: str = Query(""), user: dict = Depends(get_current_user)):
    """Get diff for a specific file or the whole repo."""
    user_id = user["id"]
    if path:
        _safe_git_arg(path)
        result = _git(user_id, "diff", "--cached", "--", path)
        if not result.stdout.strip():
            result = _git(user_id, "diff", "--", path)
        if not result.stdout.strip():
            result = _git(user_id, "diff", "--no-index", "/dev/null", path)
    else:
        result = _git(user_id, "diff")
    return {"diff": result.stdout or "(no changes)"}


class StageRequest(BaseModel):
    paths: list[str]


@router.post("/api/git/stage")
async def git_stage(req: StageRequest, user: dict = Depends(get_current_user)):
    """Stage files."""
    user_id = user["id"]
    lock = await _get_user_lock(user_id)
    async with lock:
        for path in req.paths:
            _git(user_id, "add", "--", _safe_git_arg(path))
    return {"ok": True}


@router.post("/api/git/unstage")
async def git_unstage(req: StageRequest, user: dict = Depends(get_current_user)):
    """Unstage files."""
    user_id = user["id"]
    lock = await _get_user_lock(user_id)
    async with lock:
        for path in req.paths:
            _git(user_id, "reset", "HEAD", "--", _safe_git_arg(path))
    return {"ok": True}


class CommitRequest(BaseModel):
    message: str = Field(..., min_length=1)


@router.post("/api/git/commit")
async def git_commit(req: CommitRequest, user: dict = Depends(get_current_user)):
    """Commit staged changes."""
    user_id = user["id"]
    lock = await _get_user_lock(user_id)
    async with lock:
        result = _git(user_id, "commit", "-m", req.message)
    if result.returncode != 0:
        raise HTTPException(400, result.stderr or "Commit failed")
    return {"ok": True, "output": result.stdout}


@router.post("/api/git/push")
async def git_push(user: dict = Depends(get_current_user)):
    """Push to origin."""
    user_id = user["id"]
    lock = await _get_user_lock(user_id)
    async with lock:
        result = _git(user_id, "push", "origin", "HEAD")
        if result.returncode != 0:
            result2 = _git(user_id, "push", "--set-upstream", "origin", "HEAD")
            if result2.returncode != 0:
                raise HTTPException(400, result2.stderr or "Push failed")
            return {"ok": True, "output": result2.stdout + result2.stderr}
    return {"ok": True, "output": result.stdout + result.stderr}


@router.post("/api/git/pull")
async def git_pull(user: dict = Depends(get_current_user)):
    """Pull from origin."""
    user_id = user["id"]
    lock = await _get_user_lock(user_id)
    async with lock:
        result = _git(user_id, "pull", "--rebase")
    if result.returncode != 0:
        raise HTTPException(400, result.stderr or "Pull failed")
    return {"ok": True, "output": result.stdout}


@router.get("/api/git/branches")
async def git_branches(user: dict = Depends(get_current_user)):
    """List all branches and the current one."""
    result = _git(user["id"], "branch", "-a")
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
                if name != "HEAD" and not name.startswith("HEAD ") and name not in all_branches:
                    all_branches.append(name)
            elif line and line not in all_branches:
                all_branches.append(line)
    return {"current": current, "all": all_branches}


class CheckoutRequest(BaseModel):
    branch: str = Field(..., min_length=1)
    create: bool = False


@router.post("/api/git/checkout")
async def git_checkout(req: CheckoutRequest, user: dict = Depends(get_current_user)):
    """Switch branch or create a new one."""
    if not _SAFE_BRANCH_RE.match(req.branch):
        raise HTTPException(400, "Invalid branch name")
    user_id = user["id"]
    lock = await _get_user_lock(user_id)
    async with lock:
        if req.create:
            result = _git(user_id, "checkout", "-b", req.branch)
        else:
            result = _git(user_id, "checkout", req.branch)
    if result.returncode != 0:
        raise HTTPException(400, result.stderr or "Checkout failed")
    return {"ok": True, "branch": req.branch}


@router.get("/api/git/log")
async def git_log(
    n: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    """Return recent commits."""
    result = _git(user["id"], "log", f"--max-count={n}", "--format=%H|%an|%ai|%s")
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
