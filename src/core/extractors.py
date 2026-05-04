"""Natural-language regex extractors for function-call argument inference.

These small helpers pull values out of free-form user text so the rule-based
extractor and Cactus post-processor can populate function arguments without
calling a model.  Each helper accepts plain text and returns either the
extracted value or ``None``.

The extractors are deliberately *narrow*: they only handle patterns that
appear repeatedly in the routing benchmarks.  Anything ambiguous returns
``None`` so the caller can fall through to a model-based path.
"""
from __future__ import annotations

import re

# ── Time / duration ─────────────────────────────────────────────────────────

# "3pm", "3:30 pm", "10 a.m.", "10:00am" — captures hour, optional minute,
# and the AM/PM marker (with or without dots).
_TIME_PATTERN = re.compile(
    r"(\d{1,2})(?::(\d{2}))?\s*(AM|PM|am|pm|a\.m\.|p\.m\.)\b"
)

# "5 minutes", "20-minute", "30  min" — duration as an integer count of minutes.
_DURATION_PATTERN = re.compile(r"(\d+)[\s-]*minutes?\b", re.IGNORECASE)


def extract_time(text: str) -> tuple[int | None, int | None]:
    """Return ``(hour, minute)`` in 24-hour form, or ``(None, None)``.

    Examples
    --------
    >>> extract_time("set an alarm for 7:30 AM")
    (7, 30)
    >>> extract_time("wake me at 9pm")
    (21, 0)
    >>> extract_time("no time mentioned")
    (None, None)
    """
    m = _TIME_PATTERN.search(text)
    if not m:
        return None, None
    hour = int(m.group(1))
    minute = int(m.group(2)) if m.group(2) else 0
    period = m.group(3).lower().replace(".", "")
    if period == "pm" and hour != 12:
        hour += 12
    elif period == "am" and hour == 12:
        hour = 0
    return hour, minute


def extract_duration(text: str) -> int | None:
    """Return minutes from "5 minutes" / "30 min", or ``None``."""
    m = _DURATION_PATTERN.search(text)
    return int(m.group(1)) if m else None


def extract_time_string(text: str) -> str | None:
    """Return the literal time expression (e.g. ``"3:00 PM"``), or ``None``."""
    m = re.search(r"(\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm|a\.m\.|p\.m\.))", text)
    return m.group(1).strip() if m else None


# ── Names, messages, locations, songs, reminders ────────────────────────────

# Words that, when *preceding* a capitalised token, signal that token is a
# proper-noun person/contact name (used by send_message, search_contacts, …).
_NAME_PRECEDING_WORDS = frozenset({
    "to", "for", "contact", "up", "find", "message", "text",
    "send", "search", "tell", "call", "named", "ask",
})


def extract_names(text: str) -> list[str]:
    """Return capitalised words that follow a name-introducing keyword."""
    words = text.split()
    names: list[str] = []
    for index, word in enumerate(words):
        clean = word.strip(".,!?;:'\"")
        if clean and clean[0].isupper() and index > 0:
            previous = words[index - 1].lower().rstrip(".,!?;:'\"")
            if previous in _NAME_PRECEDING_WORDS:
                names.append(clean)
    return names


def extract_message(text: str) -> str | None:
    """Return content following 'saying' / 'says' / 'tells X' patterns."""
    match = re.search(
        r"\b(?:saying|says?|that\s+says?|telling\s+\w+)\s+(.+?)"
        r"(?:\s+and\s+|\s*[,;]\s*|\.?\s*$)",
        text,
        re.IGNORECASE,
    )
    return match.group(1).rstrip(".") if match else None


def extract_location(text: str) -> str | None:
    """Return the city/location from a weather-like query."""
    match = re.search(
        r"\b(?:weather\s+(?:in|like\s+in|for|of)|forecast\s+(?:in|for)|in)\s+"
        r"([A-Z][a-zA-Z\s]*?)(?:\s+and\s+|\s*[,;?.!]\s*|$)",
        text,
    )
    return match.group(1).strip().rstrip(".,?!") if match else None


# Genres where we *keep* the trailing word "music" because it is part of the
# canonical name (e.g. "classical music", "country music").  Other genres
# strip the redundant suffix ("jazz music" → "jazz").
_KEEP_MUSIC_SUFFIX = frozenset({"classical", "country", "chamber", "world"})


def extract_song(text: str) -> str | None:
    """Return the song / playlist phrase that follows ``"play"``."""
    match = re.search(r"\b[Pp]lay\s+(?:some\s+)?(.+?)(?:\s+and\s+|\s*[,;]\s*|\.?\s*$)", text)
    if not match:
        return None
    song = match.group(1).strip().rstrip(".")
    words = song.split()
    if (
        len(words) >= 2
        and words[-1].lower() == "music"
        and words[-2].lower() not in _KEEP_MUSIC_SUFFIX
    ):
        song = " ".join(words[:-1])
    return song


def extract_reminder_title(text: str) -> str | None:
    """Return the reminder title from 'remind me to / about ...' style phrases."""
    patterns = (
        r"\b(?:remind\s+me\s+(?:about|to)\s+)(.+?)"
        r"(?:\s+at\s+\d|\s+by\s+\d|\s*[,;]\s*|\.?\s*$)",
        r"\b(?:(?:create|set)\s+(?:a\s+)?reminder\s+(?:to|for|about)\s+)(.+?)"
        r"(?:\s+at\s+\d|\s+by\s+\d|\s*[,;]\s*|\.?\s*$)",
        r"\b(?:reminder\s+(?:to|for|about)\s+)(.+?)"
        r"(?:\s+at\s+\d|\s+by\s+\d|\s*[,;]\s*|\.?\s*$)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            title = match.group(1).strip().rstrip(".,")
            title = re.sub(r"^the\s+", "", title, flags=re.IGNORECASE)
            return title
    return None


# ── Argument-string post-processing ─────────────────────────────────────────

# Removes verbose lead-ins from string arguments emitted by FunctionGemma:
# "saying hello" -> "hello"; "that says foo" -> "foo".
STRING_PREFIX_NOISE = re.compile(
    r"^(saying\s+|says?\s+|that\s+says?\s+|that\s+)", re.IGNORECASE
)
