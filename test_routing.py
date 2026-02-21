"""
Test suite for hybrid routing using Hyphae research tools.

Mirrors the benchmark structure (easy / medium / hard) but targets
the actual research-copilot tool set so we can iterate on routing
quality without running the full benchmark each time.

Usage:
    python test_routing.py              # run all tests
    python test_routing.py easy         # run only easy tests
    python test_routing.py --cloud-only # force cloud path
"""

import sys, os

sys.path.insert(0, "cactus/python/src")
os.environ["CACTUS_NO_CLOUD_TELE"] = "1"

import json
from main import generate_hybrid
from benchmark import compute_f1, compute_total_score
from tools import (
    TOOL_SEARCH_PAPERS,
    TOOL_SUMMARISE_NOTES,
    TOOL_CREATE_NOTE,
    TOOL_LIST_DOCUMENTS,
    TOOL_GENERATE_HYPOTHESIS,
    TOOL_SEARCH_LITERATURE,
)

RESEARCH_TESTS = [
    # ── Easy: single tool, direct request ────────────────────────────
    {
        "name": "search_polymer",
        "difficulty": "easy",
        "messages": [{"role": "user", "content": "Search my papers for polymer degradation results."}],
        "tools": [TOOL_SEARCH_PAPERS],
        "expected_calls": [{"name": "search_papers", "arguments": {"query": "polymer degradation results"}}],
    },
    {
        "name": "list_docs",
        "difficulty": "easy",
        "messages": [{"role": "user", "content": "Show me all documents in the corpus."}],
        "tools": [TOOL_LIST_DOCUMENTS],
        "expected_calls": [{"name": "list_documents", "arguments": {}}],
    },
    {
        "name": "summarise_battery",
        "difficulty": "easy",
        "messages": [{"role": "user", "content": "Summarise my notes on battery cycling."}],
        "tools": [TOOL_SUMMARISE_NOTES],
        "expected_calls": [{"name": "summarise_notes", "arguments": {"topic": "battery cycling"}}],
    },
    {
        "name": "create_observation",
        "difficulty": "easy",
        "messages": [{"role": "user", "content": "Create a note titled 'Batch 7 anomaly' with content 'Unexpected peak at 340nm in UV-Vis spectrum'."}],
        "tools": [TOOL_CREATE_NOTE],
        "expected_calls": [{"name": "create_note", "arguments": {"title": "Batch 7 anomaly", "content": "Unexpected peak at 340nm in UV-Vis spectrum"}}],
    },
    {
        "name": "hypothesis_simple",
        "difficulty": "easy",
        "messages": [{"role": "user", "content": "Generate hypotheses about increasing catalyst yield."}],
        "tools": [TOOL_GENERATE_HYPOTHESIS],
        "expected_calls": [{"name": "generate_hypothesis", "arguments": {"context": "increasing catalyst yield"}}],
    },
    {
        "name": "literature_crispr",
        "difficulty": "easy",
        "messages": [{"role": "user", "content": "Search the literature for CRISPR off-target effects."}],
        "tools": [TOOL_SEARCH_LITERATURE],
        "expected_calls": [{"name": "search_literature", "arguments": {"query": "CRISPR off-target effects"}}],
    },

    # ── Medium: pick the right tool from 2-4 options ─────────────────
    {
        "name": "search_among_three",
        "difficulty": "medium",
        "messages": [{"role": "user", "content": "Find all my notes about thermal conductivity."}],
        "tools": [TOOL_SEARCH_PAPERS, TOOL_SUMMARISE_NOTES, TOOL_LIST_DOCUMENTS],
        "expected_calls": [{"name": "search_papers", "arguments": {"query": "thermal conductivity"}}],
    },
    {
        "name": "summarise_among_four",
        "difficulty": "medium",
        "messages": [{"role": "user", "content": "Give me a summary of my experiment logs on corrosion."}],
        "tools": [TOOL_SEARCH_PAPERS, TOOL_SUMMARISE_NOTES, TOOL_CREATE_NOTE, TOOL_LIST_DOCUMENTS],
        "expected_calls": [{"name": "summarise_notes", "arguments": {"topic": "corrosion"}}],
    },
    {
        "name": "create_among_three",
        "difficulty": "medium",
        "messages": [{"role": "user", "content": "Save a note titled 'PCR result' with content 'Band visible at 500bp'."}],
        "tools": [TOOL_SEARCH_PAPERS, TOOL_CREATE_NOTE, TOOL_SUMMARISE_NOTES],
        "expected_calls": [{"name": "create_note", "arguments": {"title": "PCR result", "content": "Band visible at 500bp"}}],
    },
    {
        "name": "hypothesis_among_four",
        "difficulty": "medium",
        "messages": [{"role": "user", "content": "Generate hypotheses about why cell viability dropped after treatment."}],
        "tools": [TOOL_SEARCH_PAPERS, TOOL_GENERATE_HYPOTHESIS, TOOL_SEARCH_LITERATURE, TOOL_LIST_DOCUMENTS],
        "expected_calls": [{"name": "generate_hypothesis", "arguments": {"context": "cell viability dropped after treatment"}}],
    },
    {
        "name": "literature_among_three",
        "difficulty": "medium",
        "messages": [{"role": "user", "content": "Search the literature for graphene oxide applications in water treatment."}],
        "tools": [TOOL_SEARCH_PAPERS, TOOL_SEARCH_LITERATURE, TOOL_GENERATE_HYPOTHESIS],
        "expected_calls": [{"name": "search_literature", "arguments": {"query": "graphene oxide applications in water treatment"}}],
    },
    {
        "name": "list_among_all",
        "difficulty": "medium",
        "messages": [{"role": "user", "content": "What documents do I have?"}],
        "tools": [TOOL_SEARCH_PAPERS, TOOL_SUMMARISE_NOTES, TOOL_CREATE_NOTE, TOOL_LIST_DOCUMENTS, TOOL_GENERATE_HYPOTHESIS, TOOL_SEARCH_LITERATURE],
        "expected_calls": [{"name": "list_documents", "arguments": {}}],
    },

    # ── Hard: multiple tools needed in one query ─────────────────────
    {
        "name": "search_and_summarise",
        "difficulty": "hard",
        "messages": [{"role": "user", "content": "Search my papers for electrode materials and summarise notes on impedance spectroscopy."}],
        "tools": [TOOL_SEARCH_PAPERS, TOOL_SUMMARISE_NOTES, TOOL_LIST_DOCUMENTS, TOOL_CREATE_NOTE],
        "expected_calls": [
            {"name": "search_papers", "arguments": {"query": "electrode materials"}},
            {"name": "summarise_notes", "arguments": {"topic": "impedance spectroscopy"}},
        ],
    },
    {
        "name": "search_and_hypothesis",
        "difficulty": "hard",
        "messages": [{"role": "user", "content": "Search my notes on RNA folding and generate hypotheses about secondary structure stability."}],
        "tools": [TOOL_SEARCH_PAPERS, TOOL_GENERATE_HYPOTHESIS, TOOL_SEARCH_LITERATURE, TOOL_CREATE_NOTE],
        "expected_calls": [
            {"name": "search_papers", "arguments": {"query": "RNA folding"}},
            {"name": "generate_hypothesis", "arguments": {"context": "secondary structure stability"}},
        ],
    },
    {
        "name": "literature_and_note",
        "difficulty": "hard",
        "messages": [{"role": "user", "content": "Search the literature for perovskite solar cells and create a note titled 'Perovskite review' with content 'Need to compare efficiency metrics'."}],
        "tools": [TOOL_SEARCH_LITERATURE, TOOL_CREATE_NOTE, TOOL_SEARCH_PAPERS, TOOL_SUMMARISE_NOTES],
        "expected_calls": [
            {"name": "search_literature", "arguments": {"query": "perovskite solar cells"}},
            {"name": "create_note", "arguments": {"title": "Perovskite review", "content": "Need to compare efficiency metrics"}},
        ],
    },
    {
        "name": "search_summarise_hypothesis",
        "difficulty": "hard",
        "messages": [{"role": "user", "content": "Search for my data on nanoparticle synthesis, summarise notes on yield optimisation, and generate hypotheses about size distribution."}],
        "tools": [TOOL_SEARCH_PAPERS, TOOL_SUMMARISE_NOTES, TOOL_GENERATE_HYPOTHESIS, TOOL_LIST_DOCUMENTS, TOOL_CREATE_NOTE],
        "expected_calls": [
            {"name": "search_papers", "arguments": {"query": "nanoparticle synthesis"}},
            {"name": "summarise_notes", "arguments": {"topic": "yield optimisation"}},
            {"name": "generate_hypothesis", "arguments": {"context": "size distribution"}},
        ],
    },
]


def run_research_tests(difficulty_filter=None):
    """Run research-tool routing tests."""
    tests = RESEARCH_TESTS
    if difficulty_filter:
        tests = [t for t in tests if t["difficulty"] == difficulty_filter]

    total = len(tests)
    results = []

    for i, case in enumerate(tests, 1):
        print(f"[{i}/{total}] {case['name']} ({case['difficulty']})...", end=" ", flush=True)
        result = generate_hybrid(case["messages"], case["tools"])
        f1 = compute_f1(result["function_calls"], case["expected_calls"])
        source = result.get("source", "unknown")
        print(f"F1={f1:.2f} | {result['total_time_ms']:.0f}ms | {source}")
        results.append({
            "name": case["name"],
            "difficulty": case["difficulty"],
            "total_time_ms": result["total_time_ms"],
            "f1": f1,
            "source": source,
            "predicted": result["function_calls"],
            "expected": case["expected_calls"],
        })

    print(f"\n{'='*60}")
    print("Research Routing Test Results")
    print(f"{'='*60}\n")

    for difficulty in ["easy", "medium", "hard"]:
        group = [r for r in results if r["difficulty"] == difficulty]
        if not group:
            continue
        avg_f1 = sum(r["f1"] for r in group) / len(group)
        on_device = sum(1 for r in group if r["source"] == "on-device")
        print(f"  {difficulty:<8} F1={avg_f1:.2f}  on-device={on_device}/{len(group)}")

    avg_f1 = sum(r["f1"] for r in results) / len(results)
    score = compute_total_score(results)
    print(f"\n  overall  F1={avg_f1:.2f}  score={score:.1f}%")

    # Show failures
    failures = [r for r in results if r["f1"] < 1.0]
    if failures:
        print(f"\n--- Failures ({len(failures)}) ---")
        for r in failures:
            print(f"  {r['name']}: F1={r['f1']:.2f}")
            print(f"    expected: {json.dumps(r['expected'], indent=2)}")
            print(f"    got:      {json.dumps(r['predicted'], indent=2)}")

    return results


if __name__ == "__main__":
    difficulty = None
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg == "--cloud-only":
            os.environ["CLOUD_ONLY"] = "1"
        elif arg in ("easy", "medium", "hard"):
            difficulty = arg
    run_research_tests(difficulty)
