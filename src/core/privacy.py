"""Privacy helpers for the Hyphae engine layer.

PII sanitisation is delegated to the shared ``notebook.sanitiser`` module
which provides labelled pattern replacements ([EMAIL], [IP], etc.).
This module adds engine-specific helpers for tool-level privacy filtering.
"""

from .tools import LOCAL_ONLY_TOOLS

try:
    from notebook.sanitiser import sanitise_text, sanitise_messages  # noqa: F401
except ImportError:
    from copy import deepcopy as _deepcopy
    import re as _re

    _FALLBACK = [(_re.compile(r"https?://\S+"), "[URL]"),
                 (_re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}"), "[EMAIL]")]

    def sanitise_text(text: str):
        triggered = []
        for pat, repl in _FALLBACK:
            text, n = pat.subn(repl, text)
            if n:
                triggered.append(repl)
        return text, triggered

    def sanitise_messages(messages):
        cleaned, all_trig = [], []
        for msg in messages:
            msg = _deepcopy(msg)
            if isinstance(msg.get("content"), str):
                msg["content"], trig = sanitise_text(msg["content"])
                all_trig.extend(trig)
            cleaned.append(msg)
        return cleaned, list(set(all_trig))


def sanitise_for_cloud(messages):
    """Strip sensitive data from messages before sending to cloud."""
    cleaned, _ = sanitise_messages(messages)
    return cleaned


def is_cloud_safe(tool_name):
    """Return True if a tool's data can safely be sent to or processed by cloud."""
    return tool_name not in LOCAL_ONLY_TOOLS


def filter_tools_for_cloud(tools):
    """Return only tools that are safe for cloud execution."""
    return [t for t in tools if is_cloud_safe(t["name"])]


def filter_tools_for_local(tools):
    """Return only tools that should run on-device."""
    return [t for t in tools if t["name"] in LOCAL_ONLY_TOOLS]
