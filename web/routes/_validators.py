"""Shared field-level validators reused across route models.

Keeping these in one place avoids the trap that landed us in
`fix: accept ISO datetimes`: per-route regexes drift, the frontend
emits a slightly different format, and validation silently 422s.
"""

from __future__ import annotations

import re

# RFC 5321 caps the local part at 64 octets and the whole address at
# 254 octets.  We don't try to be authoritative — Pydantic's EmailStr
# would require the email-validator dependency — but we do want every
# route that touches an email to fail in the same way.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
EMAIL_RE = re.compile(EMAIL_PATTERN)

# Accept either a plain calendar date (``YYYY-MM-DD``) or an ISO-8601
# timestamp with optional seconds, fractional seconds and timezone
# offset.  Mirrors what ``Date.prototype.toISOString()`` produces.
DATE_OR_DATETIME_PATTERN = (
    r"^\d{4}-\d{2}-\d{2}"
    r"(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+\-]\d{2}:?\d{2})?)?$"
)


def is_valid_email(value: str) -> bool:
    """Return ``True`` when *value* matches our shared email shape."""
    return bool(EMAIL_RE.match(value))
