#!/usr/bin/env python3
"""
Example: manage the local research corpus.

Demonstrates adding PDFs/text files, listing documents, searching
the corpus via RAG, and removing documents.

Run from the project root:
    python examples/corpus_management.py
"""

import os
import sys

_root = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _root)
sys.path.insert(0, os.path.join(_root, "src"))

from core.tools import execute_tool
from ingestion.corpus import list_documents


def main():
    print("=== Corpus Management Demo ===\n")

    print("1. Listing current documents:")
    list_documents()

    print("\n2. Searching for 'battery' in corpus:")
    result = execute_tool("search_papers", {"query": "battery cycling", "top_k": 3})
    for chunk in result.get("results", []):
        score = chunk.get("score", 0)
        text = chunk.get("text", "")[:100].replace("\n", " ")
        print(f"  [{score:.2f}] {text}...")

    print("\n3. Creating a note:")
    result = execute_tool("create_note", {
        "title": "Demo observation",
        "content": "This is a sample note created by the corpus management demo.",
    })
    print(f"  Saved to: {result.get('saved', '')}")

    print("\n4. Updated document list:")
    list_documents()

    print("\nDone.")


if __name__ == "__main__":
    main()
