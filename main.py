"""Backward-compatible wrapper — delegates to src/core/engine.py.

Kept at project root so benchmark.py, submit.py, and existing scripts
that do ``from main import generate_hybrid`` continue to work.
"""

import sys, os

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_PROJECT_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from core.engine import *  # noqa: F401,F403
