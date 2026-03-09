"""Query, classify, tools, privacy-log, and voice endpoints."""

from __future__ import annotations

import collections
import json
import logging
import os
import subprocess
import tempfile
import time
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import List

from core.config import GEMINI_MODEL
from routes.auth import get_current_user

router = APIRouter(prefix="/api", tags=["query"])


# ── Request models ────────────────────────────────────────────────────

class _MessageBody(BaseModel):
    message: str = Field(..., min_length=1)

class _QueryBody(BaseModel):
    message: str = Field(..., min_length=1)
    tools: Optional[List[dict]] = None
log = logging.getLogger(__name__)

# Injected at startup from app.py
generate_hybrid = None
ALL_TOOLS = []
LOCAL_ONLY_TOOLS: set = set()
CLOUD_SAFE_TOOLS: set = set()
execute_tool = None
_gemini_client_fn = None


def configure(*, hybrid_fn, all_tools, local_tools, cloud_tools, execute_fn, gemini_fn):
    global generate_hybrid, ALL_TOOLS, LOCAL_ONLY_TOOLS, CLOUD_SAFE_TOOLS
    global execute_tool, _gemini_client_fn
    generate_hybrid = hybrid_fn
    ALL_TOOLS = all_tools
    LOCAL_ONLY_TOOLS = local_tools
    CLOUD_SAFE_TOOLS = cloud_tools
    execute_tool = execute_fn
    _gemini_client_fn = gemini_fn


# ── Answer synthesis ─────────────────────────────────────────────────────

def _is_all_local(tool_results: list) -> bool:
    return all(tr["tool"] in LOCAL_ONLY_TOOLS for tr in tool_results)


def _format_local_answer(user_message: str, tool_results: list) -> str:
    parts = []
    for tr in tool_results:
        rd = tr["result"]
        if "error" in rd:
            parts.append(f"**{tr['tool']}** encountered an error: {rd['error']}")
            continue
        t = tr["tool"]
        if t == "search_papers":
            chunks = rd.get("results", [])
            if chunks:
                parts.append(f"Found **{len(chunks)}** relevant passage(s):")
                for i, c in enumerate(chunks[:5], 1):
                    src = c.get("source") or c.get("name") or ""
                    cite = f" [{src}]" if src else ""
                    parts.append(f"{i}. {c.get('text', '')[:400]}{cite}")
            else:
                parts.append("No matching passages found in your corpus.")
        elif t == "search_text":
            matches = rd.get("matches", [])
            if matches:
                parts.append(f"Found **{len(matches)}** text match(es):")
                for m in matches:
                    parts.append(f"- **{m.get('name', '')}**: {(m.get('paragraph') or m.get('snippet', ''))[:300]}")
            else:
                parts.append("No text matches found.")
        elif t == "summarise_notes":
            parts.append(rd.get("summary", "No summary available."))
        elif t == "create_note":
            parts.append(f"Note saved to `{rd.get('saved', 'corpus')}`.")
        elif t == "list_documents":
            docs = rd.get("documents", [])
            parts.append(f"**{len(docs)}** document(s) in your corpus:")
            for d in docs:
                parts.append(f"- {d['name']} ({d.get('size_kb', '?')} KB)")
        elif t == "read_document":
            name = rd.get("name", "document")
            trunc = " *(truncated)*" if rd.get("truncated") else ""
            parts.append(f"**{name}** ({rd.get('size_kb', '?')} KB){trunc}:\n{rd.get('content', '')[:800]}")
        elif t == "compare_documents":
            parts.append(rd.get("comparison", "No comparison available."))
        else:
            parts.append(f"**{t}**: {json.dumps(rd, indent=2)[:400]}")
    return "\n\n".join(parts)


def _synthesise_cloud_answer(user_message: str, tool_results: list) -> Optional[str]:
    client = _gemini_client_fn()
    if not client or not tool_results:
        return None

    results_text = ""
    for tr in tool_results:
        rd = tr["result"]
        if "error" in rd:
            results_text += f"\nTool {tr['tool']} failed: {rd['error']}\n"
            continue
        if tr["tool"] == "search_papers":
            chunks = rd.get("results", [])
            results_text += f"\n[search_papers found {len(chunks)} passages]\n"
            for c in chunks[:5]:
                results_text += f"- {c.get('text', '')[:300]}\n"
        elif tr["tool"] == "summarise_notes":
            results_text += f"\n[summary]\n{rd.get('summary', '')}\n"
        elif tr["tool"] == "create_note":
            results_text += f"\n[note saved to {rd.get('saved', '')}]\n"
        elif tr["tool"] == "list_documents":
            docs = rd.get("documents", [])
            results_text += f"\n[{len(docs)} documents in corpus]\n"
            for d in docs:
                results_text += f"- {d['name']} ({d.get('size_kb', '?')} KB)\n"
        elif tr["tool"] == "generate_hypothesis":
            results_text += f"\n[hypotheses]\n{rd.get('hypotheses', '')}\n"
        elif tr["tool"] == "search_literature":
            results_text += f"\n[literature]\n{rd.get('results', '')}\n"
        elif tr["tool"] == "compare_documents":
            results_text += f"\n[comparison]\n{rd.get('comparison', '')}\n"
        else:
            results_text += f"\n[{tr['tool']}]\n{json.dumps(rd, indent=2)[:500]}\n"

    prompt = (
        f'The user asked: "{user_message}"\n\n'
        f"The system executed tools and got these results:\n{results_text}\n\n"
        "Based on these results, write a helpful, concise answer to the user's question. "
        "Reference specific data from the results. Do not mention tool names or internal details. "
        "Write as a knowledgeable research assistant."
    )
    try:
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=[prompt])
        return resp.text
    except Exception as exc:
        log.warning("synthesise_cloud_answer failed: %s", exc)
        return None


# ── Privacy classification + audit log ───────────────────────────────────

_CLOUD_KEYWORDS = {
    "hypothesis", "hypotheses", "hypothesize", "hypothesise", "propose", "predict",
    "literature", "published", "papers", "citations", "cite", "prior",
}

_privacy_log: collections.deque = collections.deque(maxlen=1000)


@router.post("/classify")
async def api_classify(body: _MessageBody, _user: dict = Depends(get_current_user)):
    words = set(body.message.strip().lower().split())
    needs_cloud = bool(words & _CLOUD_KEYWORDS)
    return {"route": "cloud" if needs_cloud else "local"}


@router.get("/privacy-log")
async def api_privacy_log(_user: dict = Depends(get_current_user)):
    return {"entries": list(_privacy_log)[-100:]}


def _log_privacy_event(query: str, tools_used: list, data_local: bool, routing_ms: float):
    _privacy_log.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "query": query[:120],
        "tools": [t["tool"] for t in tools_used],
        "data_local": data_local,
        "routing_ms": routing_ms,
    })


# ── Tools list ───────────────────────────────────────────────────────────

@router.get("/tools")
async def api_tools(_user: dict = Depends(get_current_user)):
    if not ALL_TOOLS:
        return {"tools": [], "count": 0}
    tools = []
    for t in ALL_TOOLS:
        name = t.get("name")
        param_obj = t.get("parameters", {}).get("properties", {}) or {}
        required = set(t.get("parameters", {}).get("required", []) or [])
        params = []
        for pname, pinfo in param_obj.items():
            params.append({
                "name": pname,
                "description": pinfo.get("description", ""),
                "type": pinfo.get("type", "string"),
                "required": pname in required,
            })
        source = "local" if name in LOCAL_ONLY_TOOLS else "cloud" if name in CLOUD_SAFE_TOOLS else "hybrid"
        tools.append({
            "name": name,
            "description": t.get("description", ""),
            "parameters": params,
            "source": source,
        })
    return {"tools": tools, "count": len(tools)}


# ── Query endpoint ───────────────────────────────────────────────────────

@router.post("/query")
async def api_query(body: _QueryBody, _user: dict = Depends(get_current_user)):
    user_message = body.message.strip()
    if generate_hybrid is None or execute_tool is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Hyphae core is not available. Check server logs for import errors."},
        )

    messages = [{"role": "user", "content": user_message}]
    tools = body.tools or list(ALL_TOOLS)

    t0 = time.time()
    result = generate_hybrid(messages, tools)
    routing_ms = round((time.time() - t0) * 1000, 1)

    tool_results = []
    for fc in result.get("function_calls", []):
        tr = execute_tool(fc["name"], fc.get("arguments", {}))
        tool_results.append({"tool": fc["name"], "arguments": fc.get("arguments", {}), "result": tr})

    all_local = _is_all_local(tool_results)
    if all_local:
        answer = _format_local_answer(user_message, tool_results)
        data_stayed_local = True
    else:
        answer = _synthesise_cloud_answer(user_message, tool_results)
        data_stayed_local = False

    _log_privacy_event(user_message, tool_results, data_stayed_local, routing_ms)

    return {
        "source": result.get("source", "unknown"),
        "routing_ms": routing_ms,
        "function_calls": result.get("function_calls", []),
        "tool_results": tool_results,
        "answer": answer,
        "confidence": result.get("confidence"),
        "data_local": data_stayed_local,
    }


# ── Voice endpoint ───────────────────────────────────────────────────────

def _to_wav(input_path: str) -> str:
    if input_path.endswith(".wav"):
        return input_path
    wav_path = input_path.rsplit(".", 1)[0] + ".wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", wav_path],
            check=True, capture_output=True, timeout=30,
        )
        return wav_path
    except Exception as exc:
        log.warning("ffmpeg conversion failed: %s", exc)
        return input_path


@router.post("/voice")
async def api_voice(audio: UploadFile = File(...), _user: dict = Depends(get_current_user)):
    MAX_AUDIO_BYTES = 25 * 1024 * 1024  # 25 MB
    raw = await audio.read(MAX_AUDIO_BYTES + 1)
    if len(raw) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audio file too large (max 25 MB)")
    suffix = Path(audio.filename).suffix if audio.filename else ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name

    wav_path = _to_wav(tmp_path)
    cleanup = {tmp_path, wav_path}
    try:
        from core.voice import transcribe_file  # type: ignore
        transcript = transcribe_file(wav_path)
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={
                "error": f"Transcription failed: {exc}",
                "hint": "Install whisper weights and ensure ffmpeg is installed (brew install ffmpeg)."
            }
        )
    finally:
        for p in cleanup:
            with suppress(OSError):
                os.unlink(p)

    if not transcript.strip():
        return JSONResponse(status_code=400, content={"error": "Could not transcribe audio."})
    if generate_hybrid is None or execute_tool is None:
        return JSONResponse(status_code=503, content={"error": "Hyphae core is not available."})

    messages = [{"role": "user", "content": transcript}]
    t0 = time.time()
    result = generate_hybrid(messages, list(ALL_TOOLS))
    routing_ms = round((time.time() - t0) * 1000, 1)

    tool_results = []
    for fc in result.get("function_calls", []):
        tr = execute_tool(fc["name"], fc.get("arguments", {}))
        tool_results.append({"tool": fc["name"], "arguments": fc.get("arguments", {}), "result": tr})

    all_local = _is_all_local(tool_results)
    answer = _format_local_answer(transcript, tool_results) if all_local else _synthesise_cloud_answer(transcript, tool_results)

    return {
        "transcript": transcript,
        "source": result.get("source", "unknown"),
        "routing_ms": routing_ms,
        "function_calls": result.get("function_calls", []),
        "tool_results": tool_results,
        "answer": answer,
    }
