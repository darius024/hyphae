"""
Citation builder.

Takes raw retrieval results and produces:
  - Citation objects  (for the response JSON)
  - A formatted context prompt block  (for Gemini)
  - A system prompt  (combining context + instructions)
"""

from __future__ import annotations

from typing import List

from .models import Citation


# ── Public API ────────────────────────────────────────────────────────────

def build_citations(chunk_results: List[dict]) -> List[Citation]:
    """
    Deduplicate retrieved chunks by (source_id, page_number) and
    return a numbered list of Citation objects.
    """
    seen: set = set()
    citations: List[Citation] = []
    num = 1
    for r in chunk_results:
        key = (r["source_id"], r.get("page_number"))
        if key in seen:
            continue
        seen.add(key)
        citations.append(
            Citation(
                number=num,
                source_id=r["source_id"],
                source_title=r.get("source_title", "Untitled"),
                page_number=r.get("page_number"),
                snippet=r.get("snippet", "")[:200],
            )
        )
        num += 1
    return citations


def build_context_prompt(chunk_results: List[dict], max_chunks: int = 6) -> str:
    """
    Format retrieved chunks into a numbered context block for the LLM.

    Example output:
        [1] (Source: "Paper A", p. 3)
        "The mitochondria is the powerhouse..."

        [2] (Source: "Paper B")
        "Energy production in cells..."
    """
    lines: List[str] = []
    for idx, r in enumerate(chunk_results[:max_chunks], start=1):
        title = r.get("source_title", "Untitled")
        page  = r.get("page_number")
        page_str = f", p. {page}" if page else ""
        snippet = r.get("snippet", "").strip()
        lines.append(f'[{idx}] (Source: "{title}"{page_str})\n"{snippet}"')
    return "\n\n".join(lines)


def build_system_prompt(context: str, notebook_name: str) -> str:
    """
    Full system prompt: persona + context block + citation instructions.
    """
    return (
        f'You are an expert research assistant for the notebook "{notebook_name}".\n\n'
        "Use ONLY the passages below to answer. "
        "Cite sources inline as [1], [2], etc. "
        "If the passages do not contain enough information, say so—do not speculate.\n\n"
        "--- CONTEXT ---\n"
        f"{context}\n"
        "--- END CONTEXT ---"
    )
