"""
FAISS vector index + SQLite FTS5 BM25 — hybrid retrieval per notebook.

One FAISS IndexFlatIP per notebook stored at web/indexes/<notebook_id>.index
Hybrid ranking: normalised cosine + BM25 score merged via Reciprocal Rank Fusion.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from .db import get_conn
from .embed import EMBED_DIM

log = logging.getLogger(__name__)

FAISS_DIR = Path(__file__).parents[1] / "indexes"
FAISS_DIR.mkdir(exist_ok=True)

# Maximum seconds an index may sit idle in memory before being evicted.
# Tune via FAISS_INDEX_TTL env var; 0 disables eviction.
_INDEX_TTL = int(os.environ.get("FAISS_INDEX_TTL", "300"))
_EVICT_INTERVAL = max(30, _INDEX_TTL // 2) if _INDEX_TTL else 0

_indexes: dict[str, Any] = {}
_id_maps: dict[str, list[str]] = {}
_last_access: dict[str, float] = {}  # notebook_id -> monotonic timestamp of last use

# Per-notebook RLocks prevent two concurrent requests from simultaneously
# creating duplicate index objects or corrupting FAISS .index files on disk.
_nb_locks: dict[str, threading.RLock] = {}
_nb_locks_mu = threading.Lock()  # protects the _nb_locks dict itself


def _get_nb_lock(notebook_id: str) -> threading.RLock:
    """Return (creating lazily) a per-notebook reentrant lock."""
    # Fast-path: lock already exists.
    try:
        return _nb_locks[notebook_id]
    except KeyError:
        pass
    with _nb_locks_mu:
        if notebook_id not in _nb_locks:
            _nb_locks[notebook_id] = threading.RLock()
        return _nb_locks[notebook_id]


def _evict_idle_indexes() -> None:
    """Remove in-memory indexes that have not been accessed within _INDEX_TTL seconds.

    Acquires each per-notebook lock individually so active notebooks are not blocked.
    """
    if not _INDEX_TTL:
        return
    cutoff = time.monotonic() - _INDEX_TTL
    # Snapshot current keys to avoid mutating the dict during iteration.
    with _nb_locks_mu:
        candidates = list(_last_access.keys())
    for nb_id in candidates:
        last = _last_access.get(nb_id, 0)
        if last < cutoff:
            with _get_nb_lock(nb_id):
                # Re-check under the lock in case it was just accessed.
                if _last_access.get(nb_id, 0) < cutoff:
                    _indexes.pop(nb_id, None)
                    _id_maps.pop(nb_id, None)
                    _last_access.pop(nb_id, None)
                    log.debug("Evicted idle FAISS index for notebook %s", nb_id)


def _eviction_loop() -> None:
    """Background daemon thread that periodically calls _evict_idle_indexes."""
    while True:
        time.sleep(_EVICT_INTERVAL)
        try:
            _evict_idle_indexes()
        except Exception:
            log.exception("Error during FAISS index eviction")


if _EVICT_INTERVAL:
    _eviction_thread = threading.Thread(
        target=_eviction_loop, daemon=True, name="faiss-eviction"
    )
    _eviction_thread.start()


def _faiss():
    try:
        import faiss  # type: ignore
        return faiss
    except ImportError:
        log.warning("faiss-cpu not installed; vector search will be skipped")
        return None


def _np():
    import numpy as np  # type: ignore
    return np


def _index_path(notebook_id: str) -> Path:
    return FAISS_DIR / f"{notebook_id}.index"


def _idmap_path(notebook_id: str) -> Path:
    return FAISS_DIR / f"{notebook_id}.ids"


def get_index(notebook_id: str):
    with _get_nb_lock(notebook_id):
        _last_access[notebook_id] = time.monotonic()
        if notebook_id not in _indexes:
            faiss = _faiss()
            if faiss is None:
                _indexes[notebook_id] = None
                _id_maps[notebook_id] = []
                return _indexes[notebook_id], _id_maps[notebook_id]
            idx_path = _index_path(notebook_id)
            idmap_path = _idmap_path(notebook_id)
            if idx_path.exists():
                index = faiss.read_index(str(idx_path))
                id_map = idmap_path.read_text().splitlines() if idmap_path.exists() else []
            else:
                index = faiss.IndexFlatIP(EMBED_DIM)
                id_map = []
            _indexes[notebook_id] = index
            _id_maps[notebook_id] = id_map
        return _indexes[notebook_id], _id_maps[notebook_id]


def _save_index(notebook_id: str) -> None:
    with _get_nb_lock(notebook_id):
        faiss = _faiss()
        if faiss is None or _indexes.get(notebook_id) is None:
            return
        index, id_map = _indexes[notebook_id], _id_maps[notebook_id]
        final_path = _index_path(notebook_id)
        tmp_path = final_path.with_suffix(".tmp")
        faiss.write_index(index, str(tmp_path))
        tmp_path.replace(final_path)
        _idmap_path(notebook_id).write_text("\n".join(id_map))


def add_chunks(notebook_id: str, chunk_ids: list[str], vectors: list[list[float]]) -> list[int]:
    """Add vectors to the notebook FAISS index. Returns list of assigned FAISS IDs."""
    np = _np()
    with _get_nb_lock(notebook_id):
        index, id_map = get_index(notebook_id)  # RLock allows safe reentry
        if index is None:
            log.warning("Skipping vector add: faiss not available")
            return []
        mat = np.array(vectors, dtype=np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        mat = mat / norms
        start = len(id_map)
        index.add(mat)
        id_map.extend(chunk_ids)
        _save_index(notebook_id)  # RLock allows safe reentry
        return list(range(start, start + len(chunk_ids)))


def vector_search(notebook_id: str, query_vec: list[float], top_k: int = 6) -> list[tuple[str, float]]:
    """Return [(chunk_id, cosine_score)] sorted descending."""
    np = _np()
    with _get_nb_lock(notebook_id):
        index, id_map = get_index(notebook_id)
        if index is None:
            return []
        if index.ntotal == 0:
            return []
        q = np.array([query_vec], dtype=np.float32)
        norm = np.linalg.norm(q)
        if norm > 0:
            q = q / norm
        k = min(top_k, index.ntotal)
        scores, indices = index.search(q, k)
        results = []
        for score, idx in zip(scores[0], indices[0], strict=False):
            if 0 <= idx < len(id_map):
                results.append((id_map[idx], float(score)))
        return results


def bm25_search(notebook_id: str, query: str, top_k: int = 6) -> list[tuple[str, float]]:
    """Full-text BM25 search via FTS5. Returns [(chunk_id, rank_score)]."""
    safe_query = re.sub(r'[^\w\s]', ' ', query).strip()
    if not safe_query:
        return []
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT chunk_id, rank
               FROM chunks_fts
               WHERE chunks_fts MATCH ? AND notebook_id = ?
               ORDER BY rank
               LIMIT ?""",
            (safe_query, notebook_id, top_k),
        ).fetchall()
    results = []
    for r in rows:
        score = -r["rank"] if r["rank"] is not None else 0.0
        results.append((r["chunk_id"], float(score)))
    return results


def hybrid_search(notebook_id: str, query: str, query_vec: list[float], top_k: int = 6) -> list[dict]:
    """
    Merge vector + BM25 results via Reciprocal Rank Fusion (k=60).
    Returns list of dicts with chunk_id, source_id, source_title, page_number, snippet, score.
    """
    vec_hits = vector_search(notebook_id, query_vec, top_k * 2)
    bm25_hits = bm25_search(notebook_id, query, top_k * 2)

    K = 60
    rrf: dict[str, float] = {}
    for rank, (cid, _) in enumerate(vec_hits):
        rrf[cid] = rrf.get(cid, 0.0) + 1.0 / (K + rank + 1)
    for rank, (cid, _) in enumerate(bm25_hits):
        rrf[cid] = rrf.get(cid, 0.0) + 1.0 / (K + rank + 1)

    sorted_ids = sorted(rrf, key=lambda c: rrf[c], reverse=True)[:top_k]
    if not sorted_ids:
        return []

    placeholders = ",".join("?" * len(sorted_ids))
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT c.id, c.source_id, c.page_number, c.clean_text,
                       s.title as source_title
                FROM chunks c
                LEFT JOIN sources s ON c.source_id = s.id
                WHERE c.id IN ({placeholders})""",
            sorted_ids,
        ).fetchall()

    id_to_row = {r["id"]: r for r in rows}
    results = []
    for cid in sorted_ids:
        r = id_to_row.get(cid)
        if r is None:
            continue
        results.append({
            "chunk_id":     cid,
            "source_id":    r["source_id"],
            "source_title": r["source_title"],
            "page_number":  r["page_number"],
            "snippet":      r["clean_text"][:300],
            "score":        round(rrf[cid], 4),
        })
    return results


def delete_notebook_index(notebook_id: str) -> None:
    """Remove FAISS index files and clear in-memory cache for a notebook."""
    with _get_nb_lock(notebook_id):
        _indexes.pop(notebook_id, None)
        _id_maps.pop(notebook_id, None)
        _last_access.pop(notebook_id, None)
        _index_path(notebook_id).unlink(missing_ok=True)
        _idmap_path(notebook_id).unlink(missing_ok=True)
