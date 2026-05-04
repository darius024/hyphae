"""Rule-based function-call extraction.

Provides a fast, model-free path that splits a query into clauses, scores
each clause against the available tools, and extracts arguments via the
helpers in :mod:`core.extractors`.

This module also hosts the validators (``calls_are_valid``) and the
post-processor that repairs FunctionGemma output by re-running the same
extractors over the original user text.
"""
from __future__ import annotations

import re
from typing import Any

from .extractors import (
    STRING_PREFIX_NOISE,
    extract_duration,
    extract_location,
    extract_message,
    extract_names,
    extract_reminder_title,
    extract_song,
    extract_time,
    extract_time_string,
)

# ── Types ───────────────────────────────────────────────────────────────────

ToolSpec = dict[str, Any]
ToolCall = dict[str, Any]
Message = dict[str, str]


# ── Clause splitting & action counting ──────────────────────────────────────

# Non-capturing group prevents re.split from emitting the matched conjunction
# as a separate element, which would otherwise be counted as an extra clause.
_CLAUSE_SPLIT = re.compile(r"[,;]|\b(?:and|also|then|plus)\b", re.IGNORECASE)

_TOOL_ACTION_VERBS = frozenset({
    "set", "send", "text", "play", "check", "get", "find", "look",
    "remind", "create", "wake", "search", "call",
})


def count_actions(messages: list[Message]) -> int:
    """Count distinct actions in the user messages.

    Splits on connectors (``,``, ``;``, ``and``, ``also``, ``then``, ``plus``)
    and returns at least 1 even for queries that contain no recognised verb.
    """
    text = " ".join(message["content"] for message in messages if message["role"] == "user")
    clauses = [clause.strip() for clause in _CLAUSE_SPLIT.split(text) if clause and clause.strip()]
    action_count = 0
    for clause in clauses:
        words = clause.lower().split()
        if not words:
            continue
        if words[0] in _TOOL_ACTION_VERBS:
            action_count += 1
        elif len(words) > 1 and f"{words[0]} {words[1]}" in _TOOL_ACTION_VERBS:
            action_count += 1
    return max(action_count, 1)


def expected_call_count(messages: list[Message], tools: list[ToolSpec]) -> int:
    """Return the number of function calls the router should produce."""
    return min(count_actions(messages), len(tools))


# ── Verb → tool name mapping (rule-based intent classifier) ─────────────────

VERB_TO_TOOL: dict[str, str] = {
    "search": "search_papers", "find": "search_papers", "look": "search_papers",
    "query": "search_papers", "retrieve": "search_papers", "rag": "search_papers",
    "summarize": "summarise_notes", "summarise": "summarise_notes",
    "summary": "summarise_notes", "recap": "summarise_notes",
    "compare": "compare_documents", "contrast": "compare_documents",
    "diff": "compare_documents", "difference": "compare_documents",
    "hypothesis": "generate_hypothesis", "hypotheses": "generate_hypothesis",
    "hypothesize": "generate_hypothesis", "hypothesise": "generate_hypothesis",
    "propose": "generate_hypothesis", "predict": "generate_hypothesis",
    "literature": "search_literature", "published": "search_literature",
    "papers": "search_literature", "citations": "search_literature",
    "prior": "search_literature", "cite": "search_literature",
    "read": "read_document", "open": "read_document", "show": "read_document",
    "view": "read_document", "display": "read_document",
    "list": "list_documents", "corpus": "list_documents",
    "documents": "list_documents", "files": "list_documents",
    "note": "create_note", "save": "create_note", "record": "create_note",
    "write": "create_note", "jot": "create_note",
    "grep": "search_text", "scan": "search_text", "keyword": "search_text",
    "wake": "set_alarm", "alarm": "set_alarm",
    "timer": "set_timer", "countdown": "set_timer",
    "remind": "create_reminder", "reminder": "create_reminder",
    "text": "send_message", "message": "send_message", "msg": "send_message",
    "play": "play_music", "listen": "play_music",
    "weather": "get_weather", "forecast": "get_weather", "temperature": "get_weather",
    "contact": "search_contacts",
}


def match_tool_to_clause(clause: str, tools: list[ToolSpec]) -> ToolSpec | None:
    """Return the most likely tool for *clause*.

    Resolution order:
    1. Verb hit in :data:`VERB_TO_TOOL` (highest precedence).
    2. Token overlap against the tool's name and description (additive score).
    """
    clause_lower = clause.lower()
    clause_words = set(re.findall(r"[a-z]+", clause_lower))
    tool_names = {tool["name"] for tool in tools}

    for word in clause_words:
        mapped = VERB_TO_TOOL.get(word)
        if mapped and mapped in tool_names:
            for tool in tools:
                if tool["name"] == mapped:
                    return tool

    best_tool: ToolSpec | None = None
    best_score = 0
    for tool in tools:
        score = 0
        for name_word in tool["name"].replace("_", " ").split():
            if name_word in clause_lower:
                score += 3
        for desc_word in tool.get("description", "").lower().split():
            if len(desc_word) > 3 and desc_word in clause_words:
                score += 1
        if score > best_score:
            best_score = score
            best_tool = tool
    return best_tool if best_score > 0 else None


# ── Argument extraction for a chosen tool ───────────────────────────────────

def _is_string_role(key: str, description: str, role: str) -> bool:
    """Heuristic: does (*key*, *description*) play the named semantic *role*?"""
    description = description.lower()
    if role == "name":
        return "person" in description or key in ("recipient", "query")
    if role == "message":
        return "message" in description or "content" in description or key == "message"
    if role == "location":
        return key == "location" or "city" in description or "location" in description
    if role == "song":
        return key == "song" or "song" in description or "playlist" in description
    if role == "title":
        return key == "title" or "title" in description
    if role == "time":
        return key == "time" and "time" in description
    return False


def _resolve_string_arg(
    role: str,
    clause: str,
    full_text: str,
) -> str | None:
    """Pick the right extractor for *role* and try clause-then-full-text."""
    extractors = {
        "name": lambda text: (extract_names(text) or [None])[0],
        "message": extract_message,
        "title": extract_reminder_title,
        "time": extract_time_string,
        "song": extract_song,
        "location": extract_location,
    }
    extractor = extractors.get(role)
    if extractor is None:
        return None
    return extractor(clause) or extractor(full_text)


def extract_args_for_tool(
    tool: ToolSpec,
    clause: str,
    full_text: str,
) -> dict[str, Any]:
    """Build an argument dict for *tool* by extracting from *clause* / *full_text*."""
    properties = tool.get("parameters", {}).get("properties", {})
    args: dict[str, Any] = {}

    for key, spec in properties.items():
        ptype = spec.get("type", "")
        pdesc = spec.get("description", "").lower()

        if ptype == "integer":
            if key in ("hour", "minute") or "hour" in pdesc or "alarm" in pdesc:
                hour, minute = extract_time(clause)
                if hour is None:
                    hour, minute = extract_time(full_text)
                if hour is not None:
                    if key == "hour" or "hour" in pdesc:
                        args[key] = hour
                    elif key == "minute" or "minute" in pdesc:
                        args[key] = minute
            elif key == "minutes" or "minute" in pdesc or "duration" in pdesc:
                duration = extract_duration(clause) or extract_duration(full_text)
                if duration is not None:
                    args[key] = duration
            else:
                match = re.search(r"(\d+)", clause)
                if match:
                    args[key] = int(match.group(1))

        elif ptype == "string":
            for role in ("name", "message", "title", "time", "song", "location"):
                if _is_string_role(key, pdesc, role):
                    value = _resolve_string_arg(role, clause, full_text)
                    if value:
                        args[key] = value
                    break

    if "hour" in args and "minute" not in args and "minute" in properties:
        args["minute"] = 0

    return args


# ── Public entry points ─────────────────────────────────────────────────────

def rule_based_extract(
    messages: list[Message],
    tools: list[ToolSpec],
) -> list[ToolCall]:
    """Extract function calls without invoking any model.

    Splits the user text into clauses, picks one tool per clause, builds
    arguments, and emits a call only when all *required* parameters are
    populated.  Each tool is used at most once.
    """
    user_text = " ".join(message["content"] for message in messages if message["role"] == "user")
    clauses = [
        clause.strip()
        for clause in _CLAUSE_SPLIT.split(user_text)
        if clause and clause.strip() and len(clause.strip()) > 2
    ]
    if not clauses:
        clauses = [user_text]

    used: set[str] = set()
    calls: list[ToolCall] = []
    for clause in clauses:
        tool = match_tool_to_clause(clause, tools)
        if tool is None or tool["name"] in used:
            continue
        args = extract_args_for_tool(tool, clause, user_text)
        required = tool.get("parameters", {}).get("required", [])
        if all(key in args for key in required):
            calls.append({"name": tool["name"], "arguments": args})
            used.add(tool["name"])
    return calls


def merge_calls(
    cactus_calls: list[ToolCall],
    rule_calls: list[ToolCall],
) -> list[ToolCall]:
    """Merge two call lists, preserving rule-based entries on tool-name conflict."""
    seen: set[str] = set()
    merged: list[ToolCall] = []
    for call in rule_calls:
        if call["name"] not in seen:
            merged.append(call)
            seen.add(call["name"])
    for call in cactus_calls:
        if call["name"] not in seen:
            merged.append(call)
            seen.add(call["name"])
    return merged


def calls_are_valid(function_calls: list[ToolCall], tools: list[ToolSpec]) -> bool:
    """Return True iff every call references a known tool with valid args."""
    tool_map = {tool["name"]: tool for tool in tools}
    for call in function_calls:
        spec = tool_map.get(call["name"])
        if spec is None:
            return False
        properties = spec.get("parameters", {}).get("properties", {})
        required = spec.get("parameters", {}).get("required", [])
        args = call.get("arguments", {})
        if not all(key in args for key in required):
            return False
        for key, value in args.items():
            ptype = properties.get(key, {}).get("type", "")
            if ptype == "string" and not isinstance(value, str):
                return False
            if ptype == "string" and isinstance(value, str) and len(value.strip()) == 0:
                return False
            if ptype == "integer" and not isinstance(value, (int, float)):
                return False
            if ptype == "integer" and value < 0:
                return False
            if ptype == "integer" and isinstance(value, int) and value > 10000:
                return False
    return True


def postprocess_calls(
    function_calls: list[ToolCall],
    tools: list[ToolSpec],
    messages: list[Message] | None = None,
) -> list[ToolCall]:
    """Heal recoverable FunctionGemma errors via regex extraction over the original text."""
    tool_map = {tool["name"]: tool for tool in tools}
    user_text = ""
    if messages:
        user_text = " ".join(message["content"] for message in messages if message["role"] == "user")

    cleaned: list[ToolCall] = []
    for call in function_calls:
        spec = tool_map.get(call["name"])
        if spec is None:
            cleaned.append(call)
            continue
        properties = spec.get("parameters", {}).get("properties", {})
        args = dict(call.get("arguments", {}))

        # Trim cosmetic noise from string args; coerce float→int where the schema asks.
        for key, value in list(args.items()):
            ptype = properties.get(key, {}).get("type", "")
            if ptype == "string" and isinstance(value, str):
                args[key] = STRING_PREFIX_NOISE.sub("", value).strip().rstrip(".")
            if ptype == "integer" and isinstance(value, float):
                args[key] = int(value)

        if user_text:
            # Refresh time / duration / string args from the user text when
            # the schema demands them, regardless of whether the model filled
            # them or not — the user text is the source of truth.
            if call["name"] == "set_alarm" or ("hour" in args and "minute" in args):
                hour, minute = extract_time(user_text)
                if hour is not None:
                    args["hour"] = hour
                    args["minute"] = minute

            if "minutes" in args and "minutes" in properties:
                duration = extract_duration(user_text)
                if duration is not None:
                    args["minutes"] = duration

            for key in list(args.keys()):
                spec_for_key = properties.get(key, {})
                if spec_for_key.get("type") != "string":
                    continue
                pdesc = spec_for_key.get("description", "").lower()
                for role in ("name", "message", "title", "time", "song", "location"):
                    if _is_string_role(key, pdesc, role):
                        value = _resolve_string_arg(role, user_text, user_text)
                        if value:
                            args[key] = value
                        break
                else:
                    if not isinstance(args[key], str) or not str(args.get(key, "")).strip():
                        args[key] = ""

        cleaned.append({**call, "arguments": args})
    return cleaned
