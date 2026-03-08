"""Backward-compatible wrapper — delegates to src/core/engine.py.

Kept at project root so benchmark.py, cli.py, and existing scripts
that do ``from main import generate_hybrid`` continue to work.

Also serves as the canonical sys.path bootstrap for root-level scripts.
"""

import json
import sys
import os

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
for _p in (
    os.path.join(_PROJECT_ROOT, "src"),
    _PROJECT_ROOT,
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.engine import generate_hybrid, generate_cactus, generate_cloud  # noqa: F401


def print_result(label: str, result: dict) -> None:
    """Pretty-print a hybrid routing result (used by example scripts)."""
    source = result.get("source", "unknown")
    calls = result.get("function_calls", [])
    ms = result.get("total_time_ms", 0)
    print(f"\n{label}")
    print(f"  source={source}  time={ms:.0f}ms  calls={len(calls)}")
    for call in calls:
        print(f"  -> {call['name']}({json.dumps(call.get('arguments', {}))})")
