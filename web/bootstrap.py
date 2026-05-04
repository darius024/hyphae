"""
Centralised path bootstrap for Hyphae.

Call ``bootstrap()`` once at process startup (before any Hyphae imports) to
add ``src/`` and ``web/`` to ``sys.path`` so sub-package imports like
``from core.tools import ...`` and ``from notebook.db import ...`` resolve.

This is the ONLY file that should manipulate ``sys.path`` (apart from
``core/config.py`` which adds the cactus FFI binding path).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def bootstrap() -> None:
    """Add repository paths and set safe environment defaults."""
    web_dir = Path(__file__).resolve().parent                # hyphae/web
    project_root = web_dir.parent                             # hyphae/
    repo_root = project_root.parent                           # CactusHackathon/

    for p in (
        str(project_root / "src"),         # for ``from core.xxx import ...``
        str(web_dir),                       # for ``from notebook.xxx import ...``
        str(project_root),                  # for ``from web.app import app``
        str(repo_root / "cactus" / "python" / "src"),  # cactus FFI fallback
    ):
        if p not in sys.path:
            sys.path.insert(0, p)

    # Suppress the noisy HuggingFace tokenizer fork warning.  Other env
    # toggles (USE_DUMMY_EMBED, TRANSFORMERS_OFFLINE, HF_HUB_OFFLINE) are
    # **not** set here: they would silently force the dummy embedder for
    # every process and degrade FAISS retrieval to noise.  Tests opt in to
    # the dummy embedder explicitly via tests/conftest.py.
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


__all__ = ["bootstrap"]
