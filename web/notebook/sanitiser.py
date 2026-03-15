"""
PII sanitisation — strips sensitive data before any Gemini cloud call.
Raw document text is NEVER sent to the cloud.
Only user-visible query text may reach Gemini, and only after sanitisation
and only when notebook.allow_cloud is True.
"""

import re
import logging
from copy import deepcopy
from typing import List, Tuple

log = logging.getLogger(__name__)

_PATTERNS: List[Tuple[str, str, str]] = [
    ("email",       r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", "[EMAIL]"),
    # Constrain each octet to 0-255 to avoid matching version strings like 12.0.0.4
    ("ipv4",        r"\b(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]\d|\d)\.)" 
                    r"{3}(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]\d|\d)\b",     "[IP]"),
    ("url",         r"(?:https?|ftp|ftps|file|s3)://[^\s]+",             "[URL]"),
    ("phone",       r"\b(?:\+\d{1,3}[\s-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b", "[PHONE]"),
    # Also match space-separated (123 45 6789) and unseparated (123456789) SSNs
    ("ssn",         r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b|\b\d{9}\b",      "[SSN]"),
    ("date",        r"\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b", "[DATE]"),
    ("gps",         r"\b-?\d{1,3}\.\d+,\s*-?\d{1,3}\.\d+\b",            "[GPS]"),
    ("file_path",   r"(?:/[^\s]+)+",                                     "[PATH]"),
    ("lab_code",    r"\b[A-Z]{2}-\d{4,}\b",                              "[LAB_CODE]"),
    ("sample_id",   r"\b(?:sample|specimen|patient|subject)(?:[_-]?\d+|\s+#?[A-Za-z0-9][A-Za-z0-9_-]*)\b", "[SAMPLE_ID]"),
    ("measurement", r"\b\d+(?:\.\d+)?\s*(?:mg|ml|g|kg|°C|°F|nm|μm|mM)\b", "[MEASUREMENT]"),
    # Match known token formats: Google API keys, OpenAI keys, JWT-style dotted
    # bearer tokens. The previous catch-all (\w{64,}) matched protein sequences,
    # SHA-256 hashes, and base64 data in scientific documents.
    ("api_key",     r"\b(?:AIza[A-Za-z0-9_\-]{35,}|sk-[A-Za-z0-9]{40,}"
                    r"|[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,})\b", "[API_KEY]"),
]

_COMPILED = [
    (label, re.compile(pat, re.IGNORECASE), repl)
    for label, pat, repl in _PATTERNS
]


def sanitise_text(text: str) -> Tuple[str, List[str]]:
    triggered: List[str] = []
    for label, pattern, replacement in _COMPILED:
        new_text, n = pattern.subn(replacement, text)
        if n:
            triggered.append(label)
            text = new_text
    return text, triggered


def sanitise_messages(messages: List[dict]) -> Tuple[List[dict], List[str]]:
    msgs = deepcopy(messages)
    all_triggered: List[str] = []
    for msg in msgs:
        if isinstance(msg.get("content"), str):
            clean, triggered = sanitise_text(msg["content"])
            msg["content"] = clean
            all_triggered.extend(triggered)
    return msgs, list(set(all_triggered))


def is_safe_for_cloud(text: str) -> bool:
    return not any(pattern.search(text) for _, pattern, _ in _COMPILED)
