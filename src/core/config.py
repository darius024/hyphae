"""Centralized path configuration for Hyphae.

All cactus SDK, model weight, and corpus paths are defined here so they
can be imported by any module without hardcoding paths in multiple places.

This module also pre-loads the Cactus FFI bindings into sys.modules so
that ``from cactus import cactus_init`` works correctly.  Without this,
the hyphae/cactus/ submodule directory shadows the FFI bindings file
(cactus/python/src/cactus.py) by acting as a namespace package.
"""

import importlib.util
import logging
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CACTUS_DIR = os.path.join(PROJECT_ROOT, "cactus")
CACTUS_SRC = os.path.join(CACTUS_DIR, "python", "src")
FUNCTIONGEMMA_PATH = os.path.join(CACTUS_DIR, "weights", "functiongemma-270m-it")
WHISPER_PATH = os.path.join(CACTUS_DIR, "weights", "whisper-small")
RAG_MODEL_PATH = os.path.join(CACTUS_DIR, "weights", "lfm2-vl-450m")
CORPUS_DIR = os.environ.get("HYPHAE_CORPUS", os.path.join(PROJECT_ROOT, "corpus"))

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

# ── Upload / fetch size limits ────────────────────────────────────────
# Single source of truth so notebook attachments, corpus uploads, and
# remote URL ingestion all agree on the cap (and so an operator can
# tune them via env without grepping).
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
MAX_FETCH_BYTES = int(os.environ.get("MAX_FETCH_BYTES", str(50 * 1024 * 1024)))

if CACTUS_SRC not in sys.path:
    sys.path.insert(0, CACTUS_SRC)

_ffi_path = os.path.join(CACTUS_SRC, "cactus.py")
_cloud_only = os.environ.get("CLOUD_ONLY", "0") == "1"
if os.path.isfile(_ffi_path) and "cactus" not in sys.modules and not _cloud_only:
    try:
        _spec = importlib.util.spec_from_file_location("cactus", _ffi_path)
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules["cactus"] = _mod
        _spec.loader.exec_module(_mod)
    except Exception as _e:
        logging.getLogger(__name__).warning("Failed to preload cactus FFI: %s", _e)
