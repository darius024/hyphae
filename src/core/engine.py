"""Hybrid AI routing engine — on-device Cactus first, cloud fallback.

Provides generate_hybrid() as the primary entry point for function calling.
Three-tier routing: rule-based → FunctionGemma (on-device) → Gemini (cloud).
"""

import os, logging
import json, time
import threading as _threading
import concurrent.futures as _futures
import re

from .config import GEMINI_MODEL, PROJECT_ROOT
from .tools import TOOL_DESCRIPTION_HINTS as _TOOL_DESCRIPTION_HINTS

_CACTUS_SRC = os.path.join(PROJECT_ROOT, "cactus", "python", "src")

log = logging.getLogger(__name__)

try:
    from cactus import cactus_init, cactus_complete, cactus_destroy
    CACTUS_AVAILABLE = True
except ImportError:
    CACTUS_AVAILABLE = False

CLOUD_ONLY = os.environ.get("CLOUD_ONLY", "0") == "1"

def _find_functiongemma_path():
    """Locate FunctionGemma weights, checking env var and common paths."""
    env = os.environ.get("FUNCTIONGEMMA_PATH")
    if env and os.path.isdir(env):
        return env
    candidates = [
        os.path.join(PROJECT_ROOT, "cactus", "weights", "functiongemma-270m-it"),
        os.path.join(PROJECT_ROOT, "weights", "functiongemma-270m-it"),
        os.path.join(os.path.expanduser("~"), ".cactus", "weights", "functiongemma-270m-it"),
        "weights/functiongemma-270m-it",
    ]
    for p in candidates:
        if os.path.isdir(p):
            return p
    return candidates[0]

functiongemma_path = _find_functiongemma_path()

_cached_cactus_model = None
_cactus_init_failed = False
_cactus_lock = _threading.Lock()

# Serialises inference calls and enforces a per-request wall-clock timeout.
# max_workers=1 guarantees only one cactus_complete runs at a time.
_cactus_pool = _futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="cactus-inference")
_CACTUS_TIMEOUT = int(os.environ.get("CACTUS_TIMEOUT", "30"))

def _get_cactus_model():
    """Return a cached Cactus model handle, initialising once on first call (thread-safe)."""
    global _cached_cactus_model, _cactus_init_failed
    if _cactus_init_failed:
        return None
    if _cached_cactus_model is not None:
        return _cached_cactus_model
    with _cactus_lock:
        if _cached_cactus_model is not None:
            return _cached_cactus_model
        if _cactus_init_failed:
            return None
        if not CACTUS_AVAILABLE:
            _cactus_init_failed = True
            return None
        try:
            _cached_cactus_model = cactus_init(functiongemma_path)
            if _cached_cactus_model is None:
                _cactus_init_failed = True
        except Exception as e:
            log.warning("cactus_init failed: %s", e)
            _cactus_init_failed = True
            _cached_cactus_model = None
    return _cached_cactus_model

def _prewarm_cactus():
    try:
        _get_cactus_model()
    except Exception:
        pass

# Only prewarm when explicitly requested — avoids loading the model during
# test runs, benchmarks, and CLI imports where it is unnecessary.
if os.environ.get("CACTUS_PREWARM", "0") == "1":
    _threading.Thread(target=_prewarm_cactus, daemon=True).start()

try:
    from .privacy import sanitise_for_cloud
except ImportError:
    def sanitise_for_cloud(messages):
        return messages

_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not _GEMINI_API_KEY:
    log.warning("GEMINI_API_KEY is not set. Cloud calls will fail.")

_gemini_client = None
_http2_client = None

def _get_gemini_client():
    """Lazily import google.genai and create a cached client."""
    global _gemini_client, _http2_client
    if _gemini_client is not None:
        return _gemini_client

    try:
        from google import genai
        from google.genai import types as _types
    except Exception as e:
        log.warning("google.genai import failed: %s", e)
        _gemini_client = None
        return None

    try:
        import httpx as _httpx
        _http2_client = _httpx.Client(http2=True, timeout=10.0)
        _gemini_client = genai.Client(
            api_key=os.environ.get("GEMINI_API_KEY"),
            http_options=_types.HttpOptions(client=_http2_client),
        ) if os.environ.get("GEMINI_API_KEY") else None
    except Exception:
        try:
            _gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY")) if os.environ.get("GEMINI_API_KEY") else None
        except Exception as e:
            log.warning("Failed to create genai.Client: %s", e)
            _gemini_client = None

    return _gemini_client


# Non-capturing group prevents re.split from emitting the matched conjunction
# as a separate element, which would otherwise appear as an extra clause and
# cause spurious action over-counting.
_CLAUSE_SPLIT = re.compile(r'[,;]|\b(?:and|also|then|plus)\b', re.IGNORECASE)

_TOOL_ACTION_VERBS = {
    "set", "send", "text", "play", "check", "get", "find", "look",
    "remind", "create", "wake", "search", "call",
}


def _enrich_tools(tools):
    """Append clarifying hints to tool descriptions to reduce FunctionGemma confusion."""
    enriched = []
    for t in tools:
        hint = _TOOL_DESCRIPTION_HINTS.get(t["name"], "")
        if hint:
            t = {**t, "description": t["description"] + hint}
        enriched.append(t)
    return enriched


def _count_actions(messages) -> int:
    """Count distinct actions by splitting on connectors and checking each clause for an action verb."""
    text = " ".join(m["content"] for m in messages if m["role"] == "user")
    clauses = [c.strip() for c in _CLAUSE_SPLIT.split(text) if c and c.strip()]
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


def _expected_call_count(messages, tools) -> int:
    """Estimate how many function calls are needed based on query and available tools."""
    action_count = _count_actions(messages)
    return min(action_count, len(tools))


def _build_system_prompt(messages, tools) -> str:
    """Build a task-specific, complexity-aware system prompt."""
    tool_names = [t["name"] for t in tools]
    expected_calls = _expected_call_count(messages, tools)

    base = (
        "You are a precise function-calling assistant. "
        "You MUST call functions using ONLY the tools provided — never invent tool names. "
        "Use exact argument types (string, integer) as defined in each tool's schema. "
        "For string arguments: extract the value verbatim from the user's message. "
        "Strip only leading articles ('the', 'a', 'an') that immediately precede the core noun phrase, "
        "and strip trailing sentence punctuation (periods, commas). "
        "Preserve all other words including 'the' when it is part of the core phrase. "
        "For the 'song' parameter: strip a trailing standalone genre word 'music' only when it directly "
        "follows a genre name adjective (e.g. 'jazz music' → song='jazz'), "
        "but keep 'music' when it is part of a full title (e.g. 'classical music' → song='classical music'). "
        "Examples: 'about the meeting' -> title='meeting'; "
        "'to call the dentist' -> title='call the dentist'; "
        "'saying I\\'ll be late.' -> message='I\\'ll be late'; "
        "'some jazz music' -> song='jazz'; 'classical music' -> song='classical music'."
    )

    if expected_calls >= 2:
        base += (
            f" The user is requesting {expected_calls} separate actions. "
            f"You MUST return ALL {expected_calls} function calls — one per action. "
            "Do NOT skip or merge actions into a single call."
        )
    else:
        base += " The user is requesting a single action. Call exactly one function."

    if len(tools) > 1:
        base += (
            f" Choose carefully between: {', '.join(tool_names)}. "
            "Read each tool's description closely — some tools look similar but are NOT interchangeable."
        )

    return base


def _repair_json(raw_str):
    """Fix common JSON issues from small models (leading zeros, trailing commas).

    Uses 0+([1-9]\d*) instead of 0(\d+) so that valid JSON floats like 0.5
    are never touched (the decimal point fails [1-9]), and multiple consecutive
    leading zeros (e.g. 00123) are all consumed by the 0+ prefix.
    """
    raw_str = re.sub(r'(?<=:)\s*0+([1-9]\d*)', r' \1', raw_str)
    raw_str = re.sub(r',\s*([}\]])', r'\1', raw_str)
    return raw_str


def generate_cactus(messages, tools):
    """Run function calling on-device via FunctionGemma + Cactus.

    Uses rule-based extraction as a fast path (~0ms).  Only falls back to
    the actual Cactus model when rule-based cannot produce valid results.
    """
    rule_calls = _rule_based_extract(messages, tools)
    expected = _expected_call_count(messages, tools)
    if len(rule_calls) >= expected and _calls_are_valid(rule_calls, tools):
        return {
            "function_calls": rule_calls,
            "total_time_ms": 0,
            "confidence": 1.0,
        }

    model = _get_cactus_model()
    if model is None:
        return {"function_calls": rule_calls, "total_time_ms": 0, "confidence": 0}

    cactus_tools = [{
        "type": "function",
        "function": t,
    } for t in _enrich_tools(tools)]

    system_prompt = _build_system_prompt(messages, tools)

    # Capture loop-local variables so the closure is side-effect-free.
    _system = system_prompt
    _msgs = messages
    _tools = cactus_tools
    _model = model

    def _do_complete():
        return cactus_complete(
            _model,
            [{"role": "system", "content": _system}] + _msgs,
            tools=_tools,
            force_tools=True,
            max_tokens=300,
            stop_sequences=["<|im_end|>", "<end_of_turn>"],
            tool_rag_top_k=0,
            confidence_threshold=0.0,
        )

    try:
        raw_str = _cactus_pool.submit(_do_complete).result(timeout=_CACTUS_TIMEOUT)
    except _futures.TimeoutError:
        log.warning("cactus_complete timed out after %ds", _CACTUS_TIMEOUT)
        return {"function_calls": rule_calls, "total_time_ms": 0, "confidence": 0}
    except Exception as e:
        log.warning("cactus_complete failed: %s", e)
        return {"function_calls": rule_calls, "total_time_ms": 0, "confidence": 0}

    try:
        raw = json.loads(raw_str)
    except json.JSONDecodeError:
        try:
            raw = json.loads(_repair_json(raw_str))
        except json.JSONDecodeError:
            return {"function_calls": rule_calls, "total_time_ms": 0, "confidence": 0}

    return {
        "function_calls": raw.get("function_calls", []),
        "total_time_ms": raw.get("total_time_ms", 0),
        "confidence": raw.get("confidence", 0),
    }


def generate_cloud(messages, tools):
    """Run function calling via Gemini Cloud API with multi-call retry."""
    client = _get_gemini_client()
    if client is None:
        log.warning("No Gemini client available (GEMINI_API_KEY unset or import failed)")
        return {"function_calls": [], "total_time_ms": 0}

    try:
        from google.genai import types
    except Exception as e:
        log.warning("Failed to import google.genai.types: %s", e)
        return {"function_calls": [], "total_time_ms": 0}

    enriched_tools = _enrich_tools(tools)

    gemini_tools = [
        types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        k: types.Schema(type=v["type"].upper(), description=v.get("description", ""))
                        for k, v in t["parameters"]["properties"].items()
                    },
                    required=t["parameters"].get("required", []),
                ),
            )
            for t in enriched_tools
        ])
    ]

    expected_calls = _expected_call_count(messages, tools)

    arg_instruction = (
        "For string arguments: extract the value verbatim from the user's message. "
        "Strip only a leading article ('the', 'a', 'an') that immediately precedes the core noun, "
        "and strip trailing sentence punctuation (periods, commas). "
        "Preserve 'the' when it is part of the core phrase (e.g. 'call the dentist' stays as-is). "
        "Examples: 'about the meeting' → title='meeting'; "
        "'to call the dentist' → title='call the dentist'; "
        "'saying I\\'ll be late.' → message='I\\'ll be late'."
    )

    user_text = " ".join(m["content"] for m in messages if m["role"] == "user")
    if expected_calls >= 2:
        instruction = (
            f"The user is asking you to perform {expected_calls} separate actions. "
            f"You MUST call ALL {expected_calls} required functions in your response. "
            f"Do not skip any action. {arg_instruction}"
        )
        contents = [instruction + "\n\n" + user_text]
    else:
        contents = [arg_instruction + "\n\n" + user_text]

    start_time = time.time()

    system_prompt = _build_system_prompt(messages, tools)

    def _call_gemini(contents_in):
        return client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents_in,
            config=types.GenerateContentConfig(
                tools=gemini_tools,
                system_instruction=system_prompt,
            ),
        )

    def _extract_calls(response):
        calls = []
        for candidate in response.candidates:
            for part in candidate.content.parts:
                if part.function_call:
                    calls.append({
                        "name": part.function_call.name,
                        "arguments": dict(part.function_call.args),
                    })
        return calls

    try:
        response = _call_gemini(contents)
        function_calls = _extract_calls(response)

        if expected_calls >= 2 and len(function_calls) < expected_calls:
            retry_instruction = (
                f"You must call EXACTLY {expected_calls} functions for this request. "
                f"You only called {len(function_calls)} — call the remaining ones too. "
                "Request: " + user_text
            )
            retry_response = _call_gemini([retry_instruction])
            retry_calls = _extract_calls(retry_response)
            if len(retry_calls) > len(function_calls):
                function_calls = retry_calls

    except Exception as e:
        log.warning("Gemini API call failed: %s", e)
        return {"function_calls": [], "total_time_ms": (time.time() - start_time) * 1000}

    total_time_ms = (time.time() - start_time) * 1000

    return {
        "function_calls": function_calls,
        "total_time_ms": total_time_ms,
    }


_STRING_PREFIX_NOISE = re.compile(
    r'^(saying\s+|says?\s+|that\s+says?\s+|that\s+)', re.IGNORECASE
)

_TIME_PATTERN = re.compile(
    r'(\d{1,2})(?::(\d{2}))?\s*(AM|PM|am|pm|a\.m\.|p\.m\.)\b'
)
_DURATION_PATTERN = re.compile(r'(\d+)[\s-]*minutes?\b', re.IGNORECASE)


def _extract_time_from_text(text):
    """Extract hour (24h) and minute from natural language time expressions."""
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


def _extract_duration_from_text(text):
    """Extract minutes from duration expressions like '5 minutes'."""
    m = _DURATION_PATTERN.search(text)
    return int(m.group(1)) if m else None


def _extract_names_from_text(text):
    """Extract proper names from the user text (capitalized words after action keywords)."""
    words = text.split()
    names = []
    _NAME_PREC = {
        "to", "for", "contact", "up", "find", "message", "text",
        "send", "search", "tell", "call", "named", "ask",
    }
    for i, w in enumerate(words):
        clean = w.strip(".,!?;:'\"")
        if clean and clean[0].isupper() and i > 0:
            prev = words[i - 1].lower().rstrip(".,!?;:'\"")
            if prev in _NAME_PREC:
                names.append(clean)
    return names


def _extract_message_from_text(text):
    """Extract message content after 'saying' / 'says' / 'telling' / 'that says'."""
    m = re.search(
        r'\b(?:saying|says?|that\s+says?|telling\s+\w+)\s+(.+?)(?:\s+and\s+|\s*[,;]\s*|\.?\s*$)',
        text, re.IGNORECASE,
    )
    return m.group(1).rstrip(".") if m else None


def _extract_location_from_text(text):
    """Extract city/location from weather-like queries."""
    m = re.search(
        r'\b(?:weather\s+(?:in|like\s+in|for|of)|forecast\s+(?:in|for)|in)\s+'
        r'([A-Z][a-zA-Z\s]*?)(?:\s+and\s+|\s*[,;?.!]\s*|$)',
        text,
    )
    return m.group(1).strip().rstrip(".,?!") if m else None


_KEEP_MUSIC_SUFFIX = {"classical", "country", "chamber", "world"}

def _extract_song_from_text(text):
    """Extract song/playlist name after 'play'."""
    m = re.search(r'\b[Pp]lay\s+(?:some\s+)?(.+?)(?:\s+and\s+|\s*[,;]\s*|\.?\s*$)', text)
    if not m:
        return None
    song = m.group(1).strip().rstrip(".")
    words = song.split()
    if (len(words) >= 2 and words[-1].lower() == "music"
            and words[-2].lower() not in _KEEP_MUSIC_SUFFIX):
        song = " ".join(words[:-1])
    return song


def _extract_reminder_title_from_text(text):
    """Extract reminder title from 'remind me about/to ...' or 'reminder to/for ...' patterns."""
    patterns = [
        r'\b(?:remind\s+me\s+(?:about|to)\s+)(.+?)(?:\s+at\s+\d|\s+by\s+\d|\s*[,;]\s*|\.?\s*$)',
        r'\b(?:(?:create|set)\s+(?:a\s+)?reminder\s+(?:to|for|about)\s+)(.+?)(?:\s+at\s+\d|\s+by\s+\d|\s*[,;]\s*|\.?\s*$)',
        r'\b(?:reminder\s+(?:to|for|about)\s+)(.+?)(?:\s+at\s+\d|\s+by\s+\d|\s*[,;]\s*|\.?\s*$)',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            title = m.group(1).strip().rstrip(".,")
            title = re.sub(r'^the\s+', '', title, flags=re.IGNORECASE)
            return title
    return None


def _extract_time_string_from_text(text):
    """Extract a time expression as a string (e.g. '3:00 PM')."""
    m = re.search(r'(\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm|a\.m\.|p\.m\.))', text)
    return m.group(1).strip() if m else None


def _postprocess_calls(function_calls, tools, messages=None):
    """Fix recoverable FunctionGemma errors using regex extraction from user text."""
    tool_map = {t["name"]: t for t in tools}
    user_text = ""
    if messages:
        user_text = " ".join(m["content"] for m in messages if m["role"] == "user")

    cleaned = []
    for fc in function_calls:
        spec = tool_map.get(fc["name"])
        if spec is None:
            cleaned.append(fc)
            continue
        props = spec.get("parameters", {}).get("properties", {})
        args = dict(fc.get("arguments", {}))

        for key, val in list(args.items()):
            ptype = props.get(key, {}).get("type", "")
            if ptype == "string" and isinstance(val, str):
                val = _STRING_PREFIX_NOISE.sub("", val).strip().rstrip(".")
                args[key] = val
            if ptype == "integer" and isinstance(val, float):
                args[key] = int(val)

        if user_text:
            if fc["name"] in ("set_alarm",) or ("hour" in args and "minute" in args):
                h, mi = _extract_time_from_text(user_text)
                if h is not None:
                    args["hour"] = h
                    args["minute"] = mi

            if "minutes" in args and "minutes" in props:
                dur = _extract_duration_from_text(user_text)
                if dur is not None:
                    args["minutes"] = dur

            for key in list(args.keys()):
                ptype = props.get(key, {}).get("type", "")
                pdesc = props.get(key, {}).get("description", "").lower()

                if ptype != "string":
                    continue

                is_name_param = "person" in pdesc or key in ("recipient", "query")
                is_msg_param = "message" in pdesc or "content" in pdesc or key == "message"
                is_loc_param = key == "location" or "city" in pdesc or "location" in pdesc
                is_song_param = key == "song" or "song" in pdesc or "playlist" in pdesc
                is_title_param = key == "title" or "title" in pdesc
                is_time_param = key == "time" and "time" in pdesc
                needs_fix = not isinstance(args[key], str) or len(str(args.get(key, "")).strip()) == 0

                if is_name_param:
                    names = _extract_names_from_text(user_text)
                    if names:
                        args[key] = names[0]
                elif is_msg_param:
                    msg = _extract_message_from_text(user_text)
                    if msg:
                        args[key] = msg
                elif is_title_param:
                    title = _extract_reminder_title_from_text(user_text)
                    if title:
                        args[key] = title
                elif is_time_param:
                    t = _extract_time_string_from_text(user_text)
                    if t:
                        args[key] = t
                elif is_song_param:
                    song = _extract_song_from_text(user_text)
                    if song:
                        args[key] = song
                elif is_loc_param:
                    loc = _extract_location_from_text(user_text)
                    if loc:
                        args[key] = loc
                elif needs_fix:
                    args[key] = ""

        cleaned.append({**fc, "arguments": args})
    return cleaned


_VERB_TO_TOOL = {
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


def _match_tool_to_clause(clause, tools):
    """Score each tool against a clause and return the best match."""
    clause_lower = clause.lower()
    clause_words = set(re.findall(r'[a-z]+', clause_lower))
    tool_names = {t["name"] for t in tools}
    best_tool = None
    best_score = 0

    for w in clause_words:
        mapped = _VERB_TO_TOOL.get(w)
        if mapped and mapped in tool_names:
            for t in tools:
                if t["name"] == mapped:
                    return t

    for t in tools:
        score = 0
        name_words = t["name"].replace("_", " ").split()
        for nw in name_words:
            if nw in clause_lower:
                score += 3
        desc_words = t.get("description", "").lower().split()
        for dw in desc_words:
            if len(dw) > 3 and dw in clause_words:
                score += 1
        if score > best_score:
            best_score = score
            best_tool = t
    return best_tool if best_score > 0 else None


def _extract_args_for_tool(tool, clause, full_text):
    """Extract argument values from a clause for a given tool using regex."""
    props = tool.get("parameters", {}).get("properties", {})
    args = {}
    for key, spec in props.items():
        ptype = spec.get("type", "")
        pdesc = spec.get("description", "").lower()

        if ptype == "integer":
            if key in ("hour", "minute") or "hour" in pdesc or "alarm" in pdesc:
                h, mi = _extract_time_from_text(clause)
                if h is None:
                    h, mi = _extract_time_from_text(full_text)
                if h is not None:
                    if key == "hour" or "hour" in pdesc:
                        args[key] = h
                    elif key == "minute" or "minute" in pdesc:
                        args[key] = mi
            elif key == "minutes" or "minute" in pdesc or "duration" in pdesc:
                dur = _extract_duration_from_text(clause)
                if dur is None:
                    dur = _extract_duration_from_text(full_text)
                if dur is not None:
                    args[key] = dur
            else:
                m = re.search(r'(\d+)', clause)
                if m:
                    args[key] = int(m.group(1))

        elif ptype == "string":
            is_name = "person" in pdesc or key in ("recipient", "query")
            is_msg = "message" in pdesc or "content" in pdesc or key == "message"
            is_loc = key == "location" or "city" in pdesc or "location" in pdesc
            is_song = key == "song" or "song" in pdesc or "playlist" in pdesc
            is_title = key == "title" or "title" in pdesc
            is_time = key == "time" and "time" in pdesc

            if is_name:
                names = _extract_names_from_text(clause) or _extract_names_from_text(full_text)
                if names:
                    args[key] = names[0]
            elif is_msg:
                msg = _extract_message_from_text(clause) or _extract_message_from_text(full_text)
                if msg:
                    args[key] = msg
            elif is_title:
                title = _extract_reminder_title_from_text(clause) or _extract_reminder_title_from_text(full_text)
                if title:
                    args[key] = title
            elif is_time:
                t = _extract_time_string_from_text(clause) or _extract_time_string_from_text(full_text)
                if t:
                    args[key] = t
            elif is_song:
                song = _extract_song_from_text(clause) or _extract_song_from_text(full_text)
                if song:
                    args[key] = song
            elif is_loc:
                loc = _extract_location_from_text(clause) or _extract_location_from_text(full_text)
                if loc:
                    args[key] = loc

    if "hour" in args and "minute" not in args and "minute" in props:
        args["minute"] = 0

    return args


def _rule_based_extract(messages, tools):
    """Rule-based function calling: split query into clauses, match tools, extract args."""
    user_text = " ".join(m["content"] for m in messages if m["role"] == "user")
    clauses = [c.strip() for c in _CLAUSE_SPLIT.split(user_text) if c and c.strip() and len(c.strip()) > 2]
    if not clauses:
        clauses = [user_text]

    used_tools = set()
    calls = []
    for clause in clauses:
        tool = _match_tool_to_clause(clause, tools)
        if tool is None or tool["name"] in used_tools:
            continue
        args = _extract_args_for_tool(tool, clause, user_text)
        required = tool.get("parameters", {}).get("required", [])
        if all(r in args for r in required):
            calls.append({"name": tool["name"], "arguments": args})
            used_tools.add(tool["name"])

    return calls


def _merge_calls(cactus_calls, rule_calls, tools):
    """Merge Cactus and rule-based calls, preferring rule-based for conflicts."""
    seen = set()
    merged = []
    for fc in rule_calls:
        if fc["name"] not in seen:
            merged.append(fc)
            seen.add(fc["name"])
    for fc in cactus_calls:
        if fc["name"] not in seen:
            merged.append(fc)
            seen.add(fc["name"])
    return merged


def _calls_are_valid(function_calls, tools):
    """Check tool names, required args, types, and value ranges."""
    tool_map = {t["name"]: t for t in tools}
    for fc in function_calls:
        spec = tool_map.get(fc["name"])
        if spec is None:
            return False
        props = spec.get("parameters", {}).get("properties", {})
        required = spec.get("parameters", {}).get("required", [])
        args = fc.get("arguments", {})
        if not all(r in args for r in required):
            return False
        for key, val in args.items():
            ptype = props.get(key, {}).get("type", "")
            if ptype == "string" and not isinstance(val, str):
                return False
            if ptype == "string" and isinstance(val, str) and len(val.strip()) == 0:
                return False
            if ptype == "integer" and not isinstance(val, (int, float)):
                return False
            if ptype == "integer" and val < 0:
                return False
            if ptype == "integer" and isinstance(val, int) and val > 10000:
                return False
    return True


def generate_hybrid(messages, tools, confidence_threshold=0.99):
    """Hybrid routing: on-device Cactus first, rule-based fixup, cloud fallback."""
    start = time.time()
    expected_calls = _expected_call_count(messages, tools)

    if not CLOUD_ONLY:
        local = generate_cactus(messages, tools)

        if local.get("confidence", 0) < 1.0 and local["function_calls"]:
            local["function_calls"] = _postprocess_calls(
                local["function_calls"], tools, messages
            )

        if (len(local["function_calls"]) >= expected_calls
                and _calls_are_valid(local["function_calls"], tools)):
            local["source"] = "on-device"
            return local

        rule_calls = _rule_based_extract(messages, tools)
        if len(rule_calls) >= expected_calls and _calls_are_valid(rule_calls, tools):
            return {
                "function_calls": rule_calls,
                "total_time_ms": (time.time() - start) * 1000,
                "source": "on-device",
                "confidence": 1.0,
            }

        if local["function_calls"] or rule_calls:
            merged = _merge_calls(local["function_calls"], rule_calls, tools)
            if len(merged) >= expected_calls and _calls_are_valid(merged, tools):
                return {
                    "function_calls": merged,
                    "total_time_ms": (time.time() - start) * 1000,
                    "source": "on-device",
                    "confidence": local.get("confidence", 0),
                }
    else:
        rule_calls = _rule_based_extract(messages, tools)
        if len(rule_calls) >= expected_calls and _calls_are_valid(rule_calls, tools):
            return {
                "function_calls": rule_calls,
                "total_time_ms": (time.time() - start) * 1000,
                "source": "on-device",
                "confidence": 1.0,
            }

    try:
        cloud = generate_cloud(messages, tools)
        cloud["total_time_ms"] += (time.time() - start) * 1000
        cloud["source"] = "cloud (fallback)"
        return cloud
    except Exception as e:
        log.warning("Cloud fallback failed: %s", e)
        return {
            "function_calls": rule_calls if rule_calls else [],
            "total_time_ms": (time.time() - start) * 1000,
            "source": "on-device",
        }


def print_result(label, result):
    """Pretty-print a generation result."""
    print(f"\n=== {label} ===\n")
    if "source" in result:
        print(f"Source: {result['source']}")
    if "confidence" in result:
        print(f"Confidence: {result['confidence']:.4f}")
    if "local_confidence" in result:
        print(f"Local confidence (below threshold): {result['local_confidence']:.4f}")
    print(f"Total time: {result['total_time_ms']:.2f}ms")
    for call in result["function_calls"]:
        print(f"Function: {call['name']}")
        print(f"Arguments: {json.dumps(call['arguments'], indent=2)}")
