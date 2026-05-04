"""
Local embedding provider — sentence-transformers all-MiniLM-L6-v2.
Lazy-loaded so startup stays instant.
"""

from __future__ import annotations

import hashlib
import logging
import os
from functools import lru_cache

import numpy as np

log = logging.getLogger(__name__)

# NOTE: USE_DUMMY_EMBED is intentionally NOT defaulted here.  Setting it on
# import would silently force the deterministic hash-based fallback for every
# process, neutering FAISS semantic search.  Callers (tests, offline demos)
# opt in by exporting USE_DUMMY_EMBED=1 themselves.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

EMBED_MODEL = "all-MiniLM-L6-v2"
EMBED_DIM   = 384

# Tracks whether the active model is the real sentence-transformer or the
# deterministic fallback.  Surfaced via ``is_using_real_embedder()`` so the
# /ready probe and operators can detect a silently-degraded retrieval stack.
_USING_DUMMY: bool = True


class _DummyEmbedder:
    """Lightweight fallback when sentence-transformers cannot load (offline/env issues).

    Generates a deterministic hash-based embedding of fixed dimension so the
    pipeline can proceed without external downloads.
    """

    def __init__(self, dim: int = 384):
        self.dim = dim

    def encode(self, texts, convert_to_numpy=True, show_progress_bar=False):
        vecs = []
        for t in texts:
            h = hashlib.sha256(t.encode("utf-8", errors="ignore")).digest()
            raw = (h * ((self.dim // len(h)) + 1))[: self.dim]
            arr = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
            norm = np.linalg.norm(arr) or 1.0
            vecs.append(arr / norm)
        return np.vstack(vecs)


@lru_cache(maxsize=1)
def _get_model():
    """Return the embedding model, real if possible and dummy as a last resort.

    Resolution order:
    1. If ``USE_DUMMY_EMBED=1`` is explicitly set, use the dummy embedder.
       This is the deterministic offline path used by tests and CI.
    2. Otherwise attempt to load ``SentenceTransformer(EMBED_MODEL)``.
    3. On any failure, log a clear warning and fall back to the dummy
       embedder so ingestion does not break — but record that retrieval
       quality is degraded for ``is_using_real_embedder()``.
    """
    global _USING_DUMMY
    if os.environ.get("USE_DUMMY_EMBED") == "1":
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        log.info("Using dummy embedder (USE_DUMMY_EMBED=1)")
        _USING_DUMMY = True
        return _DummyEmbedder()

    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(EMBED_MODEL)
        log.info("Loaded sentence-transformer %s for embeddings", EMBED_MODEL)
        _USING_DUMMY = False
        return model
    except Exception as exc:
        log.warning(
            "Falling back to deterministic dummy embedder — semantic search "
            "will be DEGRADED.  Cause: %s",
            exc,
        )
        _USING_DUMMY = True
        return _DummyEmbedder()


def is_using_real_embedder() -> bool:
    """Return True if the loaded model is a real sentence-transformer.

    Forces lazy initialisation so the answer reflects the actual model
    that ``embed()`` would use, not whatever flag was set at import time.
    """
    _get_model()
    return not _USING_DUMMY


def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    try:
        model = _get_model()
        vecs = model.encode(list(texts), convert_to_numpy=True, show_progress_bar=False)
    except Exception as exc:
        log.warning("Embedding failed, retrying with dummy embedder: %s", exc)
        model = _DummyEmbedder()
        vecs = model.encode(list(texts), convert_to_numpy=True, show_progress_bar=False)
    return [v.tolist() for v in vecs]


def embed_one(text: str) -> list[float]:
    return embed([text])[0]
