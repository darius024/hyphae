
import sys, os, logging

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_CACTUS_SRC = os.path.join(_PROJECT_ROOT, "cactus", "python", "src")
functiongemma_path = os.path.join(_PROJECT_ROOT, "cactus", "weights", "functiongemma-270m-it")

sys.path.insert(0, os.path.join(_PROJECT_ROOT, "src"))
if _CACTUS_SRC not in sys.path:
    sys.path.insert(0, _CACTUS_SRC)

import json, time

log = logging.getLogger(__name__)

try:
    from cactus import cactus_init, cactus_complete, cactus_destroy
    CACTUS_AVAILABLE = True
except ImportError:
    CACTUS_AVAILABLE = False

CLOUD_ONLY = os.environ.get("CLOUD_ONLY", "0") == "1"
# NOTE: We avoid importing heavy cloud deps (google.genai, httpx, aiohttp, etc.)
# at module import time. They are lazily imported inside _get_gemini_client and
# generate_cloud so scripts that only exercise on-device code don't pay the
# cold-import cost or run into binary import errors.

try:
    from privacy import sanitise_for_cloud
except ImportError:
    def sanitise_for_cloud(messages):
        return messages

_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not _GEMINI_API_KEY:
    log.warning("GEMINI_API_KEY is not set. Cloud calls will fail.")

_gemini_client = None
_http2_client = None

def _get_gemini_client():
    """Lazily import google.genai and create a cached client.

    Returns None if imports or client construction fail (caller should handle).
    """
    global _gemini_client, _http2_client
    if _gemini_client is not None:
        return _gemini_client

    try:
        # Import locally to avoid heavy startup cost at module import time
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
        # Fallback: plain client without HTTP/2
        try:
            _gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY")) if os.environ.get("GEMINI_API_KEY") else None
        except Exception as e:
            log.warning("Failed to create genai.Client: %s", e)
            _gemini_client = None

    return _gemini_client


import re

_TOOL_DESCRIPTION_HINTS = {
    "set_alarm":       (
        " Use ONLY to alert at a specific clock time (e.g. 7:30 AM, 10 PM). "
        "Requires 'hour' (integer, 24h) and 'minute' (integer). "
        "NOT for countdowns — use set_timer for that."
    ),
    "set_timer":       (
        " Use ONLY for a countdown duration (e.g. '5 minutes', '20 minutes'). "
        "Requires 'minutes' (integer). "
        "NOT for a specific clock time — use set_alarm for that."
    ),
    "send_message":    (
        " Use ONLY to send a direct text message to a named person. "
        "Requires 'recipient' (string, person's name) and 'message' (string, the text to send). "
        "NOT for creating reminders — use create_reminder for that."
    ),
    "create_reminder": (
        " Use ONLY to create a personal reminder with a title and a time. "
        "Requires 'title' (string, what to remember) and 'time' (string, e.g. '3:00 PM'). "
        "NOT for sending messages to people — use send_message for that."
    ),
    "search_contacts": (
        " Use ONLY to find/look up a person in the contacts list by name. "
        "Requires 'query' (string, the name to search for)."
    ),
    "play_music":      (
        " Use ONLY to play a specific song, artist, or playlist. "
        "Requires 'song' (string: set this to exactly the genre/song/playlist phrase the user named, "
        "e.g. 'jazz', 'classical music', 'lo-fi beats', 'summer hits', 'Bohemian Rhapsody')."
    ),
    "get_weather":     (
        " Use ONLY to get the current weather or forecast for a city. "
        "Requires 'location' (string, city name)."
    ),
}

# Imperative action verbs that appear at the START of a clause (not as nouns)
# Split by connectors/punctuation then check the first word of each clause
_CLAUSE_SPLIT = re.compile(r'[,;]|\b(and|also|then|plus)\b', re.IGNORECASE)

# These are tools' primary action verbs — used to detect clauses that map to a tool call
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
    # Split on connectors and punctuation to get individual clauses
    clauses = [c.strip() for c in _CLAUSE_SPLIT.split(text) if c and c.strip()]
    # Filter: count only clauses that start with (or contain early) a recognised action verb
    action_count = 0
    for clause in clauses:
        words = clause.lower().split()
        if not words:
            continue
        # Check if first 1-2 words are an action verb
        if words[0] in _TOOL_ACTION_VERBS:
            action_count += 1
        elif len(words) > 1 and f"{words[0]} {words[1]}" in _TOOL_ACTION_VERBS:
            action_count += 1
    return max(action_count, 1)


def _expected_call_count(messages, tools) -> int:
    """Estimate how many function calls are needed based on query and available tools."""
    action_count = _count_actions(messages)
    # Can't need more calls than tools available
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
    """Fix common JSON issues from small models (leading zeros, trailing commas)."""
    raw_str = re.sub(r'(?<=:)\s*0(\d+)', r' \1', raw_str)
    raw_str = re.sub(r',\s*([}\]])', r'\1', raw_str)
    return raw_str


def generate_cactus(messages, tools):
    """Run function calling on-device via FunctionGemma + Cactus."""
    if not CACTUS_AVAILABLE:
        return {"function_calls": [], "total_time_ms": 0, "confidence": 0}

    model = cactus_init(functiongemma_path)

    cactus_tools = [{
        "type": "function",
        "function": t,
    } for t in _enrich_tools(tools)]

    system_prompt = _build_system_prompt(messages, tools)

    raw_str = cactus_complete(
        model,
        [{"role": "system", "content": system_prompt}] + messages,
        tools=cactus_tools,
        force_tools=True,
        max_tokens=512,
        stop_sequences=["<|im_end|>", "<end_of_turn>"],
        tool_rag_top_k=0,
        confidence_threshold=0.0,
    )

    try:
        raw = json.loads(raw_str)
    except json.JSONDecodeError:
        try:
            raw = json.loads(_repair_json(raw_str))
        except json.JSONDecodeError:
            return {"function_calls": [], "total_time_ms": 0, "confidence": 0}

    return {
        "function_calls": raw.get("function_calls", []),
        "total_time_ms": raw.get("total_time_ms", 0),
        "confidence": raw.get("confidence", 0),
    }


def generate_cloud(messages, tools):
    """Run function calling via Gemini Cloud API with multi-call retry."""
    # Ensure cloud libs are available; _get_gemini_client performs lazy import for the client
    client = _get_gemini_client()
    if client is None:
        log.warning("No Gemini client available (GEMINI_API_KEY unset or import failed)")
        return {"function_calls": [], "total_time_ms": 0}

    try:
        from google.genai import types
    except Exception as e:
        log.warning("Failed to import google.genai.types: %s", e)
        return {"function_calls": [], "total_time_ms": 0}

    # Enrich tool descriptions with clarifying hints (same as Cactus path)
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

    # Base instruction for argument extraction quality
    arg_instruction = (
        "For string arguments: extract the value verbatim from the user's message. "
        "Strip only a leading article ('the', 'a', 'an') that immediately precedes the core noun, "
        "and strip trailing sentence punctuation (periods, commas). "
        "Preserve 'the' when it is part of the core phrase (e.g. 'call the dentist' stays as-is). "
        "Examples: 'about the meeting' → title='meeting'; "
        "'to call the dentist' → title='call the dentist'; "
        "'saying I\\'ll be late.' → message='I\\'ll be late'."
    )

    # Build contents: include a system-like instruction + all user messages
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

    # System instruction for Gemini (passed separately for best compliance)
    system_prompt = _build_system_prompt(messages, tools)

    def _call_gemini(contents_in):
        return client.models.generate_content(
            model="gemini-2.5-flash-lite",
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

        # Retry once if we got fewer calls than expected for multi-action queries
        if expected_calls >= 2 and len(function_calls) < expected_calls:
            retry_instruction = (
                f"You must call EXACTLY {expected_calls} functions for this request. "
                f"You only called {len(function_calls)} — call the remaining ones too. "
                "Request: " + user_text
            )
            retry_response = _call_gemini([retry_instruction])
            retry_calls = _extract_calls(retry_response)
            # Use retry result if it returned more calls
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
_DURATION_PATTERN = re.compile(r'(\d+)\s*minutes?\b', re.IGNORECASE)


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
    """Extract proper names from the user text (capitalized words not at sentence start)."""
    words = text.split()
    names = []
    for i, w in enumerate(words):
        clean = w.strip(".,!?;:'\"")
        if clean and clean[0].isupper() and i > 0:
            prev = words[i - 1].lower().rstrip(".,!?;:'\"")
            if prev in ("to", "for", "contact", "up", "find", "message", "text"):
                names.append(clean)
    return names


def _extract_message_from_text(text):
    """Extract message content after 'saying' / 'says' / 'that says'."""
    m = re.search(r'\b(?:saying|says?|that\s+says?)\s+(.+?)(?:\s+and\s+|\s*[,;]\s*|\.?\s*$)', text, re.IGNORECASE)
    return m.group(1).rstrip(".") if m else None


def _extract_location_from_text(text):
    """Extract city/location after 'in' from weather-like queries."""
    m = re.search(r'\b(?:weather\s+(?:in|like\s+in|for)|in)\s+([A-Z][a-zA-Z\s]*?)(?:\s+and\s+|\s*[,;?.!]\s*|$)', text)
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
    """Extract reminder title between 'remind me about/to' and time."""
    m = re.search(r'\b(?:remind\s+me\s+(?:about|to)\s+)(.+?)(?:\s+at\s+\d|\s+by\s+\d|\s*[,;]\s*|\.?\s*$)', text, re.IGNORECASE)
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
    # ── Research tools (primary) ──
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
    # ── Legacy benchmark tools (kept for compatibility) ──
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
                h, mi = _extract_time_from_text(clause) or (None, None)
                if h is None:
                    h, mi = _extract_time_from_text(full_text) or (None, None)
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
    """Hybrid routing: rule-based first, FunctionGemma second, cloud fallback."""
    start = time.time()
    expected_calls = _expected_call_count(messages, tools)

    rule_calls = _rule_based_extract(messages, tools)
    if len(rule_calls) >= expected_calls and _calls_are_valid(rule_calls, tools):
        return {
            "function_calls": rule_calls,
            "total_time_ms": (time.time() - start) * 1000,
            "source": "on-device",
            "confidence": 1.0,
        }

    if CLOUD_ONLY or not CACTUS_AVAILABLE:
        cloud = generate_cloud(messages, tools)
        cloud["total_time_ms"] += (time.time() - start) * 1000
        cloud["source"] = "cloud (fallback)"
        return cloud

    local = generate_cactus(messages, tools)
    local["function_calls"] = _postprocess_calls(local["function_calls"], tools, messages)
    got_calls = len(local["function_calls"])

    if got_calls >= expected_calls and _calls_are_valid(local["function_calls"], tools):
        local["source"] = "on-device"
        return local

    try:
        cloud = generate_cloud(messages, tools)
        cloud["total_time_ms"] += local["total_time_ms"]
        cloud["source"] = "cloud (fallback)"
        return cloud
    except Exception as e:
        log.warning("Cloud fallback failed: %s", e)
        local["source"] = "on-device (best-effort)"
        return local


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


############## Example usage ##############

if __name__ == "__main__":
    tools = [{
        "name": "get_weather",
        "description": "Get current weather for a location",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City name",
                }
            },
            "required": ["location"],
        },
    }]

    messages = [
        {"role": "user", "content": "What is the weather in San Francisco?"}
    ]

    on_device = generate_cactus(messages, tools)
    print_result("FunctionGemma (On-Device Cactus)", on_device)

    cloud = generate_cloud(messages, tools)
    print_result("Gemini (Cloud)", cloud)

    hybrid = generate_hybrid(messages, tools)
    print_result("Hybrid (On-Device + Cloud Fallback)", hybrid)
