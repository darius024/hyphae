"""
Local embedding provider — sentence-transformers all-MiniLM-L6-v2.
Lazy-loaded so startup stays instant.
"""

from __future__ import annotations

import logging
import os
import hashlib
import numpy as np
from functools import lru_cache
from typing import List, Optional

log = logging.getLogger(__name__)

# Default to an offline/dummy embedder to avoid network downloads hanging
# notebooks or background workers. Set USE_DUMMY_EMBED=0 to load the real model.
os.environ.setdefault("USE_DUMMY_EMBED", "1")

# Reduce noisy parallelism warnings if/when transformers is used.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

EMBED_MODEL = "all-MiniLM-L6-v2"
EMBED_DIM   = 384

_model = None


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
            # Repeat/trim to desired dim
            raw = (h * ((self.dim // len(h)) + 1))[: self.dim]
            arr = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
            # Normalize
            norm = np.linalg.norm(arr) or 1.0
            vecs.append(arr / norm)
        return np.vstack(vecs)


@lru_cache(maxsize=1)
def _get_model():
    # Fast opt-out to avoid network downloads / heavy deps.
    if os.environ.get("USE_DUMMY_EMBED", "1") == "1":
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        log.info("Using dummy embedder (USE_DUMMY_EMBED=1)")
        return _DummyEmbedder()

    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(EMBED_MODEL)
    except Exception as exc:
        # If huggingface/transformers import fails (network timeout, etc.), fallback immediately.
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        log.warning("Falling back to dummy embedder: %s", exc)
        return _DummyEmbedder()


def embed(texts: List[str]) -> List[List[float]]:
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


def embed_one(text: str) -> List[float]:
    return embed([text])[0]
