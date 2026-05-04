"""JSON repair helpers for small-model function-call output.

FunctionGemma occasionally emits JSON-ish text that fails strict ``json.loads``
(leading zeros on integer literals, trailing commas before ``]``/``}``).
``repair_json`` heals the common cases without touching valid JSON floats
like ``0.5`` or boolean/null tokens.
"""
from __future__ import annotations

import re

# Strip leading zeros from integer values **after a colon**, e.g. `"x": 007` -> `"x": 7`.
# The ``[1-9]`` after the zero prefix means we never touch ``0.5`` (the next
# char is a dot, which fails the class) and ``0`` itself (no following digit).
_LEADING_ZERO_INT = re.compile(r"(?<=:)\s*0+([1-9]\d*)")

# Drop a comma immediately preceding a closing brace or bracket — a frequent
# small-model artefact when the model "remembers" an item it didn't emit.
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def repair_json(raw: str) -> str:
    """Apply local syntactic repairs to a JSON-ish string.

    Idempotent — calling twice yields the same result as calling once.

    Repairs applied:
    1. Leading zeros on integer literals (``007`` -> ``7``).
    2. Trailing commas before ``]`` or ``}``.

    Returns the (possibly modified) string.  Caller is responsible for the
    subsequent ``json.loads`` and any error handling.
    """
    raw = _LEADING_ZERO_INT.sub(r" \1", raw)
    raw = _TRAILING_COMMA.sub(r"\1", raw)
    return raw
