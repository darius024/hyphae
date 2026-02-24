"""Backward-compatible wrapper — delegates to src/core/engine.py.

Kept at project root so benchmark.py, cli.py, and existing scripts
that do ``from main import generate_hybrid`` continue to work.

Also serves as the canonical sys.path bootstrap for root-level scripts.
"""

import sys
import os

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
for _p in (
    os.path.join(_PROJECT_ROOT, "src"),
    _PROJECT_ROOT,
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.engine import *  # noqa: F401,F403
