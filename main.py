
import sys
sys.path.insert(0, "cactus/python/src")
functiongemma_path = "cactus/weights/functiongemma-270m-it"

import json, os, time, functools

try:
    from cactus import cactus_init, cactus_complete, cactus_destroy
    CACTUS_AVAILABLE = True
except ImportError:
    CACTUS_AVAILABLE = False

# Set CLOUD_ONLY=1 to skip local inference entirely (useful when Cactus is not installed)
CLOUD_ONLY = os.environ.get("CLOUD_ONLY", "0") == "1"

from google import genai
from google.genai import types

_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not _GEMINI_API_KEY:
    print("WARNING: GEMINI_API_KEY is not set. Cloud calls will fail.", file=sys.stderr)

# Module-level singleton — avoids re-creating the client on every call.
# Uses HTTP/2 for connection multiplexing and keep-alive to reduce per-call latency.
try:
    import httpx as _httpx
    _http2_client = _httpx.Client(http2=True, timeout=10.0)
    _gemini_client = genai.Client(
        api_key=_GEMINI_API_KEY,
        http_options=types.HttpOptions(client=_http2_client),
    ) if _GEMINI_API_KEY else None
except Exception:
    # Fallback: plain client without HTTP/2
    _gemini_client = genai.Client(api_key=_GEMINI_API_KEY) if _GEMINI_API_KEY else None


import re

_TOOL_DESCRIPTION_HINTS = {
    "set_alarm":       " Clock time only (e.g. 7:30 AM). NOT countdown.",
    "set_timer":       " Countdown duration only (e.g. 5 min). NOT clock time.",
    "send_message":    " Text to a person. NOT reminders.",
    "create_reminder": " Personal reminder with title+time. NOT messaging.",
    "search_contacts": " Look up a person by name.",
    "play_music":      " song=genre/title as stated (e.g. jazz, lo-fi beats, classical music).",
    "get_weather":     " Weather for a city.",
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


@functools.lru_cache(maxsize=128)
def _build_system_prompt_cached(tool_names_tuple: tuple, expected_calls: int) -> str:
    """Build and cache system prompt — identical (tools, n_calls) hits return cached string."""
    base = (
        "You are a precise function-calling assistant. "
        "Call ONLY provided tools with exact types. "
        "Extract string args verbatim from the user message. "
        "Strip leading articles (a/an/the) and trailing punctuation (.,!) from string args. "
        "song: use genre/title as stated (jazz music->jazz, classical music->classical music). "
    )

    if expected_calls >= 2:
        base += f"Call ALL {expected_calls} functions — do not skip any. "
    else:
        base += "Call exactly one function. "

    if len(tool_names_tuple) > 1:
        base += f"Choose carefully among: {', '.join(tool_names_tuple)}."

    return base


def _build_system_prompt(messages, tools) -> str:
    """Build a task-specific, complexity-aware system prompt."""
    tool_names = tuple(t["name"] for t in tools)
    expected_calls = _expected_call_count(messages, tools)
    return _build_system_prompt_cached(tool_names, expected_calls)


@functools.lru_cache(maxsize=128)
def _build_gemini_tools_cached(tools_key: tuple) -> list:
    """Build and cache types.Tool objects — identical tool-sets hit cache, skip Pydantic construction.

    tools_key: tuple of (name, description, props_json, required_json) per tool.
    """
    declarations = []
    for name, description, props_json, required_json in tools_key:
        props = json.loads(props_json)
        required = json.loads(required_json)
        declarations.append(
            types.FunctionDeclaration(
                name=name,
                description=description,
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        k: types.Schema(type=v["type"].upper(), description=v.get("description", ""))
                        for k, v in props.items()
                    },
                    required=required,
                ),
            )
        )
    return [types.Tool(function_declarations=declarations)]


def _tools_cache_key(tools) -> tuple:
    """Convert tools list into a hashable cache key."""
    enriched = _enrich_tools(tools)
    return tuple(
        (
            t["name"],
            t["description"],
            json.dumps(t["parameters"]["properties"], sort_keys=True),
            json.dumps(t["parameters"].get("required", []), sort_keys=True),
        )
        for t in enriched
    )


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
    )

    cactus_destroy(model)

    try:
        raw = json.loads(raw_str)
    except json.JSONDecodeError:
        return {
            "function_calls": [],
            "total_time_ms": 0,
            "confidence": 0,
        }

    return {
        "function_calls": raw.get("function_calls", []),
        "total_time_ms": raw.get("total_time_ms", 0),
        "confidence": raw.get("confidence", 0),
    }


def generate_cloud(messages, tools):
    """Run function calling via Gemini Cloud API with multi-call retry."""
    # Use module-level cached client (avoids re-init latency on every call)
    client = _gemini_client or genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    # Build (cached) tool objects — skips Pydantic construction on repeated calls
    gemini_tools = _build_gemini_tools_cached(_tools_cache_key(tools))

    expected_calls = _expected_call_count(messages, tools)
    user_text = " ".join(m["content"] for m in messages if m["role"] == "user")

    # For multi-action queries, prepend a brief count reminder in the user turn
    if expected_calls >= 2:
        contents = [f"Perform ALL {expected_calls} actions requested:\n\n{user_text}"]
    else:
        contents = [user_text]

    # System prompt is lru_cache'd — same tool-set + n_calls hits cached string
    system_prompt = _build_system_prompt(messages, tools)

    start_time = time.time()

    def _call_gemini(contents_in):
        return client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=contents_in,
            config=types.GenerateContentConfig(
                tools=gemini_tools,
                system_instruction=system_prompt,
                # Disable extended thinking — saves ~500-800ms for simple tasks
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )

    def _extract_calls(response):
        calls = []
        for candidate in response.candidates:
            for part in candidate.content.parts:
                if part.function_call:
                    # Strip trailing punctuation from string args to match expected format
                    args = {}
                    for k, v in part.function_call.args.items():
                        if isinstance(v, str):
                            v = v.rstrip(".,!?;:")
                        args[k] = v
                    calls.append({
                        "name": part.function_call.name,
                        "arguments": args,
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
            if len(retry_calls) > len(function_calls):
                function_calls = retry_calls

    except Exception as e:
        print(f"WARNING: Gemini API call failed: {e}", file=sys.stderr)
        return {"function_calls": [], "total_time_ms": (time.time() - start_time) * 1000}

    total_time_ms = (time.time() - start_time) * 1000

    return {
        "function_calls": function_calls,
        "total_time_ms": total_time_ms,
    }


def generate_hybrid(messages, tools, confidence_threshold=0.99):
    """Hybrid routing: on-device first, cloud fallback on low confidence or incomplete result."""
    if CLOUD_ONLY or not CACTUS_AVAILABLE:
        cloud = generate_cloud(messages, tools)
        cloud["source"] = "cloud (fallback)"
        return cloud

    local = generate_cactus(messages, tools)
    expected_calls = _expected_call_count(messages, tools)
    got_calls = len(local["function_calls"])

    # Trust local only if: confidence high enough AND returned enough calls
    if local["confidence"] >= confidence_threshold and got_calls >= expected_calls:
        local["source"] = "on-device"
        return local

    # If local returned calls but confidence is low, check completeness:
    # a complete-but-low-confidence result is still better than going to cloud
    # only for simple single-call queries (expected_calls == 1)
    if got_calls >= expected_calls and expected_calls == 1 and got_calls > 0:
        local["source"] = "on-device"
        return local

    cloud = generate_cloud(messages, tools)
    cloud["source"] = "cloud (fallback)"
    cloud["local_confidence"] = local["confidence"]
    cloud["total_time_ms"] += local["total_time_ms"]
    return cloud


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
