#!/usr/bin/env python3
"""
Example: voice input → transcription → hybrid query.

Records audio from the microphone using sox or ffmpeg, transcribes
it with on-device Whisper, then routes the query through Hyphae.

Prerequisites:
    - sox or ffmpeg installed (brew install sox)
    - Whisper model downloaded (cactus download openai/whisper-small)

Run from the project root:
    python examples/voice_demo.py
"""

import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from voice import listen_and_transcribe
from main import generate_hybrid, print_result
from tools import ALL_TOOLS, execute_tool


def main():
    print("=== Voice Demo ===")
    print("Speak after the prompt. Press Ctrl+C to exit.\n")

    while True:
        try:
            input("Press Enter to record (5 seconds)...")
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        print("Listening...")
        text = listen_and_transcribe()

        if not text:
            print("(no speech detected)\n")
            continue

        print(f'Transcribed: "{text}"\n')

        messages = [{"role": "user", "content": text}]
        result = generate_hybrid(messages, ALL_TOOLS)
        print_result("Hybrid Result", result)

        for call in result.get("function_calls", []):
            print(f"\nExecuting {call['name']}...")
            output = execute_tool(call["name"], call.get("arguments", {}))
            print(f"  Result: {output}")

        print()


if __name__ == "__main__":
    main()
