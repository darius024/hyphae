
import sys
sys.path.insert(0, "cactus/python/src")
functiongemma_path = "cactus/weights/functiongemma-270m-it"

import json, os, re, time
from cactus import cactus_init, cactus_complete, cactus_destroy, cactus_reset
from google import genai
from google.genai import types
from privacy import sanitise_for_cloud

SYSTEM_PROMPT = (
    "You are a function calling model. "
    "Given a user query, call the correct function with the exact arguments from the query. "
    "Use only the values explicitly stated. Do not invent or assume extra values."
)

_cactus_model = None


def _get_cactus_model():
    global _cactus_model
    if _cactus_model is None:
        _cactus_model = cactus_init(functiongemma_path)
    return _cactus_model


def _repair_json(raw_str):
    """Fix common JSON issues from small models (leading zeros, trailing commas)."""
    raw_str = re.sub(r'(?<=:)\s*0(\d+)', r' \1', raw_str)
    raw_str = re.sub(r',\s*([}\]])', r'\1', raw_str)
    return raw_str


def _run_cactus_once(messages, cactus_tools):
    """Single cactus_complete call with JSON repair."""
    model = _get_cactus_model()
    cactus_reset(model)

    raw_str = cactus_complete(
        model,
        [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        tools=cactus_tools,
        force_tools=True,
        max_tokens=512,
        temperature=0.0,
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


def generate_cactus(messages, tools):
    """Run function calling on-device via FunctionGemma + Cactus."""
    cactus_tools = [{"type": "function", "function": t} for t in tools]
    return _run_cactus_once(messages, cactus_tools)


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

    safe_messages = sanitise_for_cloud(messages)
    contents = [m["content"] for m in safe_messages if m["role"] == "user"]

    start_time = time.time()

    gemini_response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=types.GenerateContentConfig(tools=gemini_tools),
    )

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


def _calls_are_valid(function_calls, tools):
    """Check tool names exist and required arguments are present."""
    tool_map = {t["name"]: t for t in tools}
    for fc in function_calls:
        spec = tool_map.get(fc["name"])
        if spec is None:
            return False
        required = spec.get("parameters", {}).get("required", [])
        args = fc.get("arguments", {})
        if not all(r in args for r in required):
            return False
    return True


def generate_hybrid(messages, tools, confidence_threshold=0.99):
    """On-device-first routing with retry. Always stays on-device."""
    local = generate_cactus(messages, tools)

    if local["function_calls"] and _calls_are_valid(local["function_calls"], tools):
        local["source"] = "on-device"
        return local

    retry = generate_cactus(messages, tools)
    retry["total_time_ms"] += local["total_time_ms"]
    retry["source"] = "on-device"
    return retry


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
