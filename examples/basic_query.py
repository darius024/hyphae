#!/usr/bin/env python3
"""
Minimal example: run a query through Hyphae's hybrid routing.

Prerequisites:
    source cactus/venv/bin/activate
    export GEMINI_API_KEY="your-key"

Run from the project root:
    python examples/basic_query.py
"""

import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from main import generate_hybrid, print_result
from tools import ALL_TOOLS, execute_tool


def main():
    query = "Search my papers for battery cycling data"
    messages = [{"role": "user", "content": query}]

    print(f"Query: {query}\n")

    result = generate_hybrid(messages, ALL_TOOLS)
    print_result("Hybrid Result", result)

    for call in result.get("function_calls", []):
        print(f"\nExecuting {call['name']}...")
        output = execute_tool(call["name"], call.get("arguments", {}))
        print(f"  Result: {output}")


if __name__ == "__main__":
    main()
