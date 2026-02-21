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

TOOL_READ_DOCUMENT = {
    "name": "read_document",
    "description": "Open a local corpus file and return its text (truncated for safety).",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Exact filename in the corpus (e.g., notes.txt)"},
            "max_chars": {"type": "integer", "description": "Optional character cap (default 4000)"},
        },
        "required": ["name"],
    },
}

TOOL_SEARCH_TEXT = {
    "name": "search_text",
    "description": "Search inside local corpus files for a keyword or phrase and return matching snippets.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Text to search for (case-insensitive)"},
            "max_snippets": {"type": "integer", "description": "Maximum snippets to return (default 5)"},
        },
        "required": ["query"],
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
    TOOL_READ_DOCUMENT,
    TOOL_SEARCH_TEXT,
]

LOCAL_ONLY_TOOLS = {"search_papers", "summarise_notes", "create_note", "list_documents", "compare_documents", "read_document", "search_text"}
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
        "read_document": _exec_read_document,
        "search_text": _exec_search_text,
    }
    fn = dispatch.get(name)
    if fn is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        return fn(**arguments)
    except Exception as e:
        return {"error": str(e)}


def _exec_search_papers(query, top_k=5):
    try:
        model = _get_rag_model()
        chunks = cactus_rag_query(model, query, top_k=int(top_k))
        return {
            "results": chunks,
            "count": len(chunks),
            "source": "local",
        }
    except Exception as exc:
        # Fallback: simple text scan across corpus so queries still work when RAG weights are missing.
        fallback = _exec_search_text(query, max_snippets=int(top_k))
        fallback["note"] = f"RAG model unavailable ({exc}); used text scan fallback"
        return fallback


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


def _exec_read_document(name, max_chars=4000):
    """Return the text content of a corpus file (truncated)."""
    path = Path(CORPUS_DIR) / name
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Document not found: {name}")
    text = path.read_text(errors="replace")
    snippet = text[: int(max_chars)]
    truncated = len(text) > len(snippet)
    return {
        "name": name,
        "content": snippet,
        "truncated": truncated,
        "size_kb": round(path.stat().st_size / 1024, 1),
        "source": "local",
    }


def _exec_search_text(query, max_snippets=5):
    """Case-insensitive search over corpus text files with paragraph snippets and filenames for citation."""
    query_low = query.lower()
    matches = []

    for pattern in ["**/*.txt", "**/*.md"]:
        for path in Path(CORPUS_DIR).glob(pattern):
            try:
                text = path.read_text(errors="replace")
            except Exception:
                continue

            if query_low not in text.lower():
                continue

            paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
            for para in paragraphs:
                if query_low in para.lower():
                    snippet = para[:600]
                    matches.append({
                        "name": path.name,
                        "paragraph": snippet,
                        "source": "local",
                    })
                    if len(matches) >= max_snippets:
                        break
            if len(matches) >= max_snippets:
                break
        if len(matches) >= max_snippets:
            break

    return {"matches": matches, "count": len(matches), "source": "local"}


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
            model="gemini-2.5-flash-lite",
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
            model="gemini-2.5-flash-lite",
        contents=[prompt],
    )

    return {
        "results": response.text,
        "source": "cloud",
    }


def _exec_compare_documents(doc_a, doc_b, topic):
    """Compare two documents on a topic using local RAG. No data leaves the device."""
    def _paragraph_hits(doc_name):
        path = Path(CORPUS_DIR) / doc_name
        if not path.exists():
            return []
        try:
            text = path.read_text(errors="replace")
        except Exception:
            return []
        topic_low = topic.lower()
        tokens = [w for w in topic_low.replace("/", " ").replace("-", " ").split() if len(w) > 3]
        paras = [p.strip() for p in text.split("\n\n") if p.strip()]
        hits = [p for p in paras if topic_low in p.lower()]
        if hits:
            return hits
        # fallback: any token match (looser)
        loose = []
        for p in paras:
            pl = p.lower()
            if any(t in pl for t in tokens):
                loose.append(p)
        return loose if loose else paras[:2]

    try:
        model = _get_rag_model()
        chunks_a = cactus_rag_query(model, f"{doc_a} {topic}", top_k=3)
        chunks_b = cactus_rag_query(model, f"{doc_b} {topic}", top_k=3)
    except Exception:
        chunks_a = chunks_b = []

    if not chunks_a and not chunks_b:
        # Fallback to paragraph scan if RAG missing or no hits
        chunks_a = [{"text": p} for p in _paragraph_hits(doc_a)[:3]]
        chunks_b = [{"text": p} for p in _paragraph_hits(doc_b)[:3]]

    if not chunks_a and not chunks_b:
        return {"comparison": "No relevant content found in either document.", "source": "local"}

    context_a = "\n".join(c["text"] for c in chunks_a) if chunks_a else "(no matches)"
    context_b = "\n".join(c["text"] for c in chunks_b) if chunks_b else "(no matches)"

    try:
        model = _get_rag_model()
        cactus_reset(model)
        response = cactus_complete(
            model,
            [
                {"role": "system", "content": "You are a research assistant. Compare the provided sources concisely with inline citations [doc]."},
                {"role": "user", "content": (
                    f"Compare the following two sources on '{topic}'. Include inline citations like [doc_a] and [doc_b].\n\n"
                    f"--- Source A ({doc_a}) ---\n{context_a}\n\n"
                    f"--- Source B ({doc_b}) ---\n{context_b}\n\n"
                    f"Provide similarities, differences, and a one-line takeaway."
                )},
            ],
            max_tokens=512,
        )
        try:
            result = json.loads(response)
            return {"comparison": result.get("response", ""), "source": "local"}
        except json.JSONDecodeError:
            pass
    except Exception:
        pass

    return {
        "comparison": f"Source A ({doc_a}): {context_a[:300]}\n\nSource B ({doc_b}): {context_b[:300]}",
        "source": "local",
    }
