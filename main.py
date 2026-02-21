
import sys
sys.path.insert(0, "cactus/python/src")
functiongemma_path = "cactus/weights/functiongemma-270m-it"

import json, os, time

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


_TOOL_DESCRIPTION_HINTS = {
    "set_alarm":       " Use this to wake up or be alerted at a specific clock time (e.g. 7:30 AM). NOT for countdowns.",
    "set_timer":       " Use this for a countdown (e.g. '5 minutes from now'). NOT for a specific clock time.",
    "send_message":    " Use this to send a text/message to a specific person. NOT for reminders.",
    "create_reminder": " Use this to create a reminder with a title. NOT for sending messages to people.",
    "search_contacts": " Use this to look up / find a person in contacts by name.",
    "play_music":      " Use this to play a song, artist, or playlist by name.",
    "get_weather":     " Use this to get the current weather or forecast for a city.",
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
    """Heuristic: count how many distinct actions the user is asking for."""
    text = " ".join(m["content"] for m in messages if m["role"] == "user").lower()
    connectors = [" and ", " also ", ", and ", " then ", " plus ", " as well", " too "]
    return 1 + sum(text.count(c) for c in connectors)


def _build_system_prompt(messages, tools) -> str:
    """Build a task-specific system prompt based on query complexity."""
    tool_names = [t["name"] for t in tools]
    action_count = _count_actions(messages)

    base = (
        "You are a precise function-calling assistant. "
        "You must ONLY call functions from the provided list. "
        "Always use exact argument types as specified (string, integer, etc). "
        "Never make up function names or arguments not in the schema."
    )

    if action_count >= 2:
        multi = (
            " The user is requesting MULTIPLE actions. "
            f"You MUST call ALL {action_count} required functions — do not skip any. "
            "Return all function calls in a single response."
        )
        base += multi

    if len(tools) > 2:
        picker = (
            f" Available tools: {', '.join(tool_names)}. "
            "Pick the most specific tool for each action."
        )
        base += picker

    return base


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
    """Run function calling via Gemini Cloud API."""
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

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
            for t in tools
        ])
    ]

    contents = [m["content"] for m in messages if m["role"] == "user"]

    start_time = time.time()

    try:
        gemini_response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=types.GenerateContentConfig(tools=gemini_tools),
        )
    except Exception as e:
        print(f"WARNING: Gemini API call failed: {e}", file=sys.stderr)
        return {"function_calls": [], "total_time_ms": (time.time() - start_time) * 1000}

    total_time_ms = (time.time() - start_time) * 1000

    function_calls = []
    for candidate in gemini_response.candidates:
        for part in candidate.content.parts:
            if part.function_call:
                function_calls.append({
                    "name": part.function_call.name,
                    "arguments": dict(part.function_call.args),
                })

    return {
        "function_calls": function_calls,
        "total_time_ms": total_time_ms,
    }


def generate_hybrid(messages, tools, confidence_threshold=0.99):
    """Baseline hybrid inference strategy; fall back to cloud if Cactus Confidence is below threshold."""
    if CLOUD_ONLY or not CACTUS_AVAILABLE:
        cloud = generate_cloud(messages, tools)
        cloud["source"] = "cloud (fallback)"
        return cloud

    local = generate_cactus(messages, tools)

    if local["confidence"] >= confidence_threshold:
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
