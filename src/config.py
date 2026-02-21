"""Centralized path configuration for Hyphae.

All cactus SDK, model weight, and corpus paths are defined here so they
can be imported by any module without hardcoding paths in multiple places.
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CACTUS_DIR = os.path.join(PROJECT_ROOT, "cactus")
CACTUS_SRC = os.path.join(CACTUS_DIR, "python", "src")
FUNCTIONGEMMA_PATH = os.path.join(CACTUS_DIR, "weights", "functiongemma-270m-it")
WHISPER_PATH = os.path.join(CACTUS_DIR, "weights", "whisper-small")
RAG_MODEL_PATH = os.path.join(CACTUS_DIR, "weights", "lfm2-vl-450m")
CORPUS_DIR = os.environ.get("HYPHAE_CORPUS", os.path.join(PROJECT_ROOT, "corpus"))

if CACTUS_SRC not in sys.path:
    sys.path.insert(0, CACTUS_SRC)
