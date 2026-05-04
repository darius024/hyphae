"""Hybrid AI routing engine — on-device Cactus first, cloud fallback.

This module is the public entry point: :func:`generate_hybrid` orchestrates a
three-tier strategy (rule-based → on-device FunctionGemma → cloud Gemini) and
returns a uniform result dict with ``function_calls``, ``total_time_ms`` and a
``source`` tag.

Pure helpers live in sibling modules:

* :mod:`core.json_repair` — heal small-model JSON output.
* :mod:`core.extractors` — natural-language regex extractors.
* :mod:`core.rule_extractor` — rule-based call extraction & validation.

The private aliases at the bottom of this file re-export the new public
names under their historical underscored spellings so existing call sites
(tests, ``main.py``, ``web/app.py``) continue to work unchanged.
"""
from __future__ import annotations

import concurrent.futures as _futures
import json
import logging
import os
import threading as _threading
import time
from typing import Any

from .config import GEMINI_MODEL, PROJECT_ROOT
from .extractors import (
    extract_duration as _extract_duration_from_text,
)
from .extractors import (
    extract_location as _extract_location_from_text,
)
from .extractors import (
    extract_message as _extract_message_from_text,
)
from .extractors import (
    extract_names as _extract_names_from_text,
)
from .extractors import (
    extract_reminder_title as _extract_reminder_title_from_text,
)
from .extractors import (
    extract_song as _extract_song_from_text,
)
from .extractors import (
    extract_time as _extract_time_from_text,
)
from .extractors import (
    extract_time_string as _extract_time_string_from_text,
)
from .json_repair import repair_json as _repair_json
from .rule_extractor import (
    VERB_TO_TOOL as _VERB_TO_TOOL,
)
from .rule_extractor import (
    calls_are_valid as _calls_are_valid,
)
from .rule_extractor import (
    count_actions as _count_actions,
)
from .rule_extractor import (
    expected_call_count as _expected_call_count,
)
from .rule_extractor import (
    extract_args_for_tool as _extract_args_for_tool,
)
from .rule_extractor import (
    match_tool_to_clause as _match_tool_to_clause,
)
from .rule_extractor import (
    merge_calls as _merge_calls,
)
from .rule_extractor import (
    postprocess_calls as _postprocess_calls,
)
from .rule_extractor import (
    rule_based_extract as _rule_based_extract,
)
from .tools import TOOL_DESCRIPTION_HINTS as _TOOL_DESCRIPTION_HINTS

log = logging.getLogger(__name__)

# Re-exports satisfy linters that flag the back-compat aliases as unused.
__all__ = [
    "CACTUS_AVAILABLE",
    "CLOUD_ONLY",
    "_VERB_TO_TOOL",
    "_calls_are_valid",
    "_count_actions",
    "_expected_call_count",
    "_extract_args_for_tool",
    "_extract_duration_from_text",
    "_extract_location_from_text",
    "_extract_message_from_text",
    "_extract_names_from_text",
    "_extract_reminder_title_from_text",
    "_extract_song_from_text",
    "_extract_time_from_text",
    "_extract_time_string_from_text",
    "_match_tool_to_clause",
    "_merge_calls",
    "_postprocess_calls",
    "_repair_json",
    "_rule_based_extract",
    "generate_cactus",
    "generate_cloud",
    "generate_hybrid",
    "print_result",
    "sanitise_for_cloud",
]

# ── Cactus availability + lazy model handle ─────────────────────────────────

try:
    from cactus import cactus_complete, cactus_init
    CACTUS_AVAILABLE = True
except ImportError:
    CACTUS_AVAILABLE = False

CLOUD_ONLY = os.environ.get("CLOUD_ONLY", "0") == "1"


def _find_functiongemma_path() -> str:
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
    for path in candidates:
        if os.path.isdir(path):
            return path
    return candidates[0]


functiongemma_path = _find_functiongemma_path()

_cached_cactus_model: Any = None
_cactus_init_failed = False
_cactus_lock = _threading.Lock()

# Serialises inference calls and enforces a per-request wall-clock timeout.
# max_workers=1 guarantees only one cactus_complete runs at a time.
_cactus_pool = _futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="cactus-inference")
_CACTUS_TIMEOUT = int(os.environ.get("CACTUS_TIMEOUT", "30"))


def _get_cactus_model() -> Any:
    """Return a cached Cactus model handle, initialising on first call (thread-safe)."""
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
        except Exception as error:
            log.warning("cactus_init failed: %s", error)
            _cactus_init_failed = True
            _cached_cactus_model = None
    return _cached_cactus_model


def _prewarm_cactus() -> None:
    try:
        _get_cactus_model()
    except Exception:
        pass


# Only prewarm when explicitly requested — avoids loading the model during
# test runs, benchmarks, and CLI imports where it is unnecessary.
if os.environ.get("CACTUS_PREWARM", "0") == "1":
    _threading.Thread(target=_prewarm_cactus, daemon=True).start()


# ── Cloud (Gemini) client ───────────────────────────────────────────────────

try:
    from .privacy import sanitise_for_cloud
except ImportError:
    def sanitise_for_cloud(messages):  # type: ignore[no-redef]
        return messages

if not os.environ.get("GEMINI_API_KEY"):
    log.warning("GEMINI_API_KEY is not set. Cloud calls will fail.")

_gemini_client: Any = None
_http2_client: Any = None


def _get_gemini_client() -> Any:
    """Lazily import google.genai and create a cached HTTP/2 client."""
    global _gemini_client, _http2_client
    if _gemini_client is not None:
        return _gemini_client

    try:
        from google import genai
        from google.genai import types as _types
    except Exception as error:
        log.warning("google.genai import failed: %s", error)
        _gemini_client = None
        return None

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None

    try:
        import httpx as _httpx
        _http2_client = _httpx.Client(http2=True, timeout=10.0)
        _gemini_client = genai.Client(
            api_key=api_key,
            http_options=_types.HttpOptions(client=_http2_client),
        )
    except Exception:
        try:
            _gemini_client = genai.Client(api_key=api_key)
        except Exception as error:
            log.warning("Failed to create genai.Client: %s", error)
            _gemini_client = None

    return _gemini_client


# ── Prompt builders ─────────────────────────────────────────────────────────

def _enrich_tools(tools: list[dict]) -> list[dict]:
    """Append clarifying hints to tool descriptions to reduce FunctionGemma confusion."""
    enriched = []
    for tool in tools:
        hint = _TOOL_DESCRIPTION_HINTS.get(tool["name"], "")
        if hint:
            tool = {**tool, "description": tool["description"] + hint}
        enriched.append(tool)
    return enriched


def _build_system_prompt(messages: list[dict], tools: list[dict]) -> str:
    """Build a task-specific, complexity-aware system prompt."""
    tool_names = [tool["name"] for tool in tools]
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


# ── On-device generation ────────────────────────────────────────────────────

def generate_cactus(messages: list[dict], tools: list[dict]) -> dict:
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

    cactus_tools = [{"type": "function", "function": tool} for tool in _enrich_tools(tools)]
    system_prompt = _build_system_prompt(messages, tools)

    # Capture loop-local variables so the closure is side-effect-free.
    _system = system_prompt
    _msgs = messages
    _tools = cactus_tools
    _model = model

    def _do_complete():
        return cactus_complete(
            _model,
            [{"role": "system", "content": _system}, *_msgs],
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
    except Exception as error:
        log.warning("cactus_complete failed: %s", error)
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


# ── Cloud generation ────────────────────────────────────────────────────────

def generate_cloud(messages: list[dict], tools: list[dict]) -> dict:
    """Run function calling via Gemini Cloud API with multi-call retry."""
    client = _get_gemini_client()
    if client is None:
        log.warning("No Gemini client available (GEMINI_API_KEY unset or import failed)")
        return {"function_calls": [], "total_time_ms": 0}

    try:
        from google.genai import types
    except Exception as error:
        log.warning("Failed to import google.genai.types: %s", error)
        return {"function_calls": [], "total_time_ms": 0}

    enriched_tools = _enrich_tools(tools)

    gemini_tools = [
        types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name=tool["name"],
                description=tool["description"],
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        key: types.Schema(
                            type=value["type"].upper(),
                            description=value.get("description", ""),
                        )
                        for key, value in tool["parameters"]["properties"].items()
                    },
                    required=tool["parameters"].get("required", []),
                ),
            )
            for tool in enriched_tools
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

    user_text = " ".join(message["content"] for message in messages if message["role"] == "user")
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

    except Exception as error:
        log.warning("Gemini API call failed: %s", error)
        return {"function_calls": [], "total_time_ms": (time.time() - start_time) * 1000}

    total_time_ms = (time.time() - start_time) * 1000
    return {
        "function_calls": function_calls,
        "total_time_ms": total_time_ms,
    }


# ── Hybrid orchestrator ─────────────────────────────────────────────────────

def generate_hybrid(
    messages: list[dict],
    tools: list[dict],
    confidence_threshold: float = 0.99,
) -> dict:
    """Hybrid routing: on-device Cactus first, rule-based fixup, cloud fallback."""
    start = time.time()
    expected_calls = _expected_call_count(messages, tools)
    rule_calls: list[dict] = []

    if not CLOUD_ONLY:
        local = generate_cactus(messages, tools)

        if local.get("confidence", 0) < 1.0 and local["function_calls"]:
            local["function_calls"] = _postprocess_calls(
                local["function_calls"], tools, messages
            )

        if (
            len(local["function_calls"]) >= expected_calls
            and _calls_are_valid(local["function_calls"], tools)
        ):
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
            merged = _merge_calls(local["function_calls"], rule_calls)
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
    except Exception as error:
        log.warning("Cloud fallback failed: %s", error)
        return {
            "function_calls": rule_calls if rule_calls else [],
            "total_time_ms": (time.time() - start) * 1000,
            "source": "on-device",
        }


def print_result(label: str, result: dict) -> None:
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
