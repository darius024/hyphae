import json
import os
from pathlib import Path
from datetime import datetime

from config import CACTUS_SRC, RAG_MODEL_PATH, CORPUS_DIR
from cactus import cactus_init, cactus_rag_query, cactus_complete, cactus_reset

NOTES_DIR = os.path.join(CORPUS_DIR, "notes")

_rag_model = None


def _get_rag_model():
    global _rag_model
    if _rag_model is None:
        os.makedirs(CORPUS_DIR, exist_ok=True)
        _rag_model = cactus_init(
            RAG_MODEL_PATH,
            corpus_dir=CORPUS_DIR,
        )
    return _rag_model


# ── Tool schemas (JSON for FunctionGemma / Gemini) ──────────────────────

TOOL_SEARCH_PAPERS = {
    "name": "search_papers",
    "description": "Search local research documents, PDFs, and experiment logs for relevant passages. Data never leaves the device.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query describing what to find"},
            "top_k": {"type": "integer", "description": "Number of results to return (default 5)"},
        },
        "required": ["query"],
    },
}

TOOL_SUMMARISE_NOTES = {
    "name": "summarise_notes",
    "description": "Summarise local experiment notes and logs on a given topic. Runs entirely on-device for privacy.",
    "parameters": {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "Topic or keyword to summarise notes about"},
        },
        "required": ["topic"],
    },
}

TOOL_CREATE_NOTE = {
    "name": "create_note",
    "description": "Save a new research note or observation locally.",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short title for the note"},
            "content": {"type": "string", "description": "The note content"},
        },
        "required": ["title", "content"],
    },
}

TOOL_LIST_DOCUMENTS = {
    "name": "list_documents",
    "description": "List all local research documents, PDFs, and notes available in the corpus.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

TOOL_GENERATE_HYPOTHESIS = {
    "name": "generate_hypothesis",
    "description": "Generate research hypotheses based on abstract context. Safe for cloud — no raw data is sent.",
    "parameters": {
        "type": "object",
        "properties": {
            "context": {"type": "string", "description": "Abstract research context or observation to reason about"},
            "field": {"type": "string", "description": "Scientific field or domain"},
        },
        "required": ["context"],
    },
}

TOOL_SEARCH_LITERATURE = {
    "name": "search_literature",
    "description": "Search scientific literature and known research for relevant prior work. Safe for cloud.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Literature search query"},
        },
        "required": ["query"],
    },
}

TOOL_COMPARE_DOCUMENTS = {
    "name": "compare_documents",
    "description": "Compare two local documents on a specific topic. Runs entirely on-device via RAG — no raw data is sent to cloud.",
    "parameters": {
        "type": "object",
        "properties": {
            "doc_a": {"type": "string", "description": "Name or keyword identifying the first document"},
            "doc_b": {"type": "string", "description": "Name or keyword identifying the second document"},
            "topic": {"type": "string", "description": "Topic or aspect to compare on"},
        },
        "required": ["doc_a", "doc_b", "topic"],
    },
}

ALL_TOOLS = [
    TOOL_SEARCH_PAPERS,
    TOOL_SUMMARISE_NOTES,
    TOOL_CREATE_NOTE,
    TOOL_LIST_DOCUMENTS,
    TOOL_GENERATE_HYPOTHESIS,
    TOOL_SEARCH_LITERATURE,
    TOOL_COMPARE_DOCUMENTS,
]

LOCAL_ONLY_TOOLS = {"search_papers", "summarise_notes", "create_note", "list_documents", "compare_documents"}
CLOUD_SAFE_TOOLS = {"generate_hypothesis", "search_literature"}


# ── Tool execution ──────────────────────────────────────────────────────

def execute_tool(name, arguments):
    """Execute a tool call and return the result dict."""
    dispatch = {
        "search_papers": _exec_search_papers,
        "summarise_notes": _exec_summarise_notes,
        "create_note": _exec_create_note,
        "list_documents": _exec_list_documents,
        "generate_hypothesis": _exec_generate_hypothesis,
        "search_literature": _exec_search_literature,
        "compare_documents": _exec_compare_documents,
    }
    fn = dispatch.get(name)
    if fn is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        return fn(**arguments)
    except Exception as e:
        return {"error": str(e)}


def _exec_search_papers(query, top_k=5):
    model = _get_rag_model()
    chunks = cactus_rag_query(model, query, top_k=int(top_k))
    return {
        "results": chunks,
        "count": len(chunks),
        "source": "local",
    }


def _exec_summarise_notes(topic):
    model = _get_rag_model()
    chunks = cactus_rag_query(model, topic, top_k=3)
    if not chunks:
        return {"summary": "No notes found on this topic.", "source": "local"}

    context = "\n---\n".join(c["text"] for c in chunks)
    cactus_reset(model)
    response = cactus_complete(
        model,
        [
            {"role": "system", "content": "Summarise the following research notes concisely. Only use the provided text."},
            {"role": "user", "content": context},
        ],
        max_tokens=256,
    )
    try:
        result = json.loads(response)
        return {"summary": result.get("response", ""), "source": "local"}
    except json.JSONDecodeError:
        return {"summary": context[:500], "source": "local"}


def _exec_create_note(title, content):
    os.makedirs(NOTES_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = title.lower().replace(" ", "_")[:40]
    filename = f"{timestamp}_{slug}.md"
    filepath = os.path.join(NOTES_DIR, filename)

    with open(filepath, "w") as f:
        f.write(f"# {title}\n\n")
        f.write(f"*Created: {datetime.now().isoformat()}*\n\n")
        f.write(content + "\n")

    return {"saved": filepath, "source": "local"}


def _exec_list_documents():
    if not os.path.isdir(CORPUS_DIR):
        return {"documents": [], "count": 0}

    docs = []
    for pattern in ["**/*.txt", "**/*.md", "**/*.pdf"]:
        for path in Path(CORPUS_DIR).glob(pattern):
            docs.append({
                "path": str(path),
                "name": path.name,
                "size_kb": round(path.stat().st_size / 1024, 1),
            })

    docs.sort(key=lambda d: d["name"])
    return {"documents": docs, "count": len(docs), "source": "local"}


def _exec_generate_hypothesis(context, field="general science"):
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    prompt = (
        f"You are a research scientist in {field}. "
        f"Based on the following abstract observation, propose 2-3 testable hypotheses.\n\n"
        f"Observation: {context}"
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[prompt],
    )

    return {
        "hypotheses": response.text,
        "source": "cloud",
        "note": "No raw experimental data was sent to generate this.",
    }


def _exec_search_literature(query):
    from google import genai

    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    prompt = (
        f"You are a scientific literature search assistant. "
        f"For the query below, list 3-5 relevant published papers or known research findings "
        f"with authors, year, and a one-line summary.\n\nQuery: {query}"
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[prompt],
    )

    return {
        "results": response.text,
        "source": "cloud",
    }


def _exec_compare_documents(doc_a, doc_b, topic):
    """Compare two documents on a topic using local RAG. No data leaves the device."""
    model = _get_rag_model()

    chunks_a = cactus_rag_query(model, f"{doc_a} {topic}", top_k=3)
    chunks_b = cactus_rag_query(model, f"{doc_b} {topic}", top_k=3)

    if not chunks_a and not chunks_b:
        return {"comparison": "No relevant content found in either document.", "source": "local"}

    context_a = "\n".join(c["text"] for c in chunks_a) if chunks_a else "(no matches)"
    context_b = "\n".join(c["text"] for c in chunks_b) if chunks_b else "(no matches)"

    cactus_reset(model)
    prompt = (
        f"Compare the following two sources on the topic of '{topic}'.\n\n"
        f"--- Source A ({doc_a}) ---\n{context_a}\n\n"
        f"--- Source B ({doc_b}) ---\n{context_b}\n\n"
        f"Provide a concise comparison highlighting similarities and differences."
    )

    response = cactus_complete(
        model,
        [
            {"role": "system", "content": "You are a research assistant. Compare the provided sources concisely."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=512,
    )

    try:
        result = json.loads(response)
        return {"comparison": result.get("response", ""), "source": "local"}
    except json.JSONDecodeError:
        return {
            "comparison": f"Source A ({doc_a}): {context_a[:300]}\n\nSource B ({doc_b}): {context_b[:300]}",
            "source": "local",
        }
