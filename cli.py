#!/usr/bin/env python3
"""Hyphae CLI — Scientific Research Copilot that respects confidential data."""

import argparse
import json
import time

from main import generate_hybrid  # also bootstraps sys.path
from core.tools import ALL_TOOLS, execute_tool, LOCAL_ONLY_TOOLS
from core.privacy import sanitise_for_cloud


def _format_source(source):
    if source == "on-device":
        return "[LOCAL]"
    return "[CLOUD]"


def _run_query(user_text, verbose=False):
    """Route a query through hybrid inference, execute tool calls, print results."""
    messages = [{"role": "user", "content": user_text}]

    start = time.time()
    result = generate_hybrid(messages, ALL_TOOLS)
    route_ms = (time.time() - start) * 1000

    source = result.get("source", "unknown")
    calls = result.get("function_calls", [])

    print(f"\n  {_format_source(source)} routed in {route_ms:.0f}ms")

    if not calls:
        print("  No tool calls generated.")
        return

    for call in calls:
        name = call["name"]
        args = call.get("arguments", {})
        privacy = "LOCAL-ONLY" if name in LOCAL_ONLY_TOOLS else "CLOUD-SAFE"

        print(f"\n  -> {name}({json.dumps(args)})  [{privacy}]")

        exec_start = time.time()
        output = execute_tool(name, args)
        exec_ms = (time.time() - exec_start) * 1000

        if "error" in output:
            print(f"     ERROR: {output['error']}")
        else:
            _print_tool_output(name, output)

        if verbose:
            print(f"     ({exec_ms:.0f}ms)")

    print()


def _print_tool_output(name, output):
    """Pretty-print tool execution results."""
    if name == "search_papers":
        results = output.get("results", [])
        print(f"     Found {output.get('count', 0)} passages:")
        for i, r in enumerate(results[:3], 1):
            text = r.get("text", "")[:120].replace("\n", " ")
            score = r.get("score", 0)
            print(f"     {i}. [{score:.2f}] {text}...")

    elif name == "summarise_notes":
        print(f"     {output.get('summary', '')[:300]}")

    elif name == "create_note":
        print(f"     Saved to: {output.get('saved', '')}")

    elif name == "list_documents":
        docs = output.get("documents", [])
        print(f"     {output.get('count', 0)} documents:")
        for d in docs[:10]:
            print(f"       - {d['name']} ({d['size_kb']}KB)")

    elif name == "generate_hypothesis":
        print(f"     {output.get('hypotheses', '')[:400]}")

    elif name == "search_literature":
        print(f"     {output.get('results', '')[:400]}")

    else:
        print(f"     {json.dumps(output, indent=2)[:300]}")


def interactive_text():
    """Interactive text mode — type queries, get tool calls + results."""
    print("\n  Hyphae — Scientific Research Copilot")
    print("  Type a question. 'quit' to exit.\n")

    while True:
        try:
            user_input = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Goodbye.")
            break

        if not user_input or user_input.lower() in ("quit", "exit", "q"):
            print("  Goodbye.")
            break

        _run_query(user_input, verbose=True)


def interactive_voice():
    """Voice mode — speak queries, Whisper transcribes, tools execute."""
    from core.voice import listen_and_transcribe

    print("\n  Hyphae — Voice Mode")
    print("  Press Enter to speak, 'quit' to exit.\n")

    while True:
        try:
            cmd = input("  [Enter to speak, 'quit' to exit] ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Goodbye.")
            break

        if cmd.lower() in ("quit", "exit", "q"):
            print("  Goodbye.")
            break

        print("  Listening...")
        text = listen_and_transcribe()
        if not text:
            print("  (no speech detected)")
            continue

        print(f"  Heard: \"{text}\"")
        _run_query(text, verbose=True)


def main():
    parser = argparse.ArgumentParser(description="Hyphae — Scientific Research Copilot")
    parser.add_argument("--voice", action="store_true", help="Use voice input via Whisper")
    parser.add_argument("query", nargs="*", help="One-shot query (omit for interactive mode)")
    args = parser.parse_args()

    if args.query:
        _run_query(" ".join(args.query))
    elif args.voice:
        interactive_voice()
    else:
        interactive_text()


if __name__ == "__main__":
    main()
