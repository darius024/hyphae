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

os.environ.setdefault("USE_DUMMY_EMBED", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

EMBED_MODEL = "all-MiniLM-L6-v2"
EMBED_DIM   = 384


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
    if os.environ.get("USE_DUMMY_EMBED", "1") == "1":
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        log.info("Using dummy embedder (USE_DUMMY_EMBED=1)")
        return _DummyEmbedder()

    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(EMBED_MODEL)
    except Exception as exc:
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        log.warning("Falling back to dummy embedder: %s", exc)
        return _DummyEmbedder()


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
