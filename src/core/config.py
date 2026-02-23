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

if CACTUS_SRC not in sys.path:
    sys.path.insert(0, CACTUS_SRC)

_ffi_path = os.path.join(CACTUS_SRC, "cactus.py")
if os.path.isfile(_ffi_path) and "cactus" not in sys.modules:
    try:
        _spec = importlib.util.spec_from_file_location("cactus", _ffi_path)
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules["cactus"] = _mod
        _spec.loader.exec_module(_mod)
    except Exception as _e:
        logging.getLogger(__name__).warning("Failed to preload cactus FFI: %s", _e)
