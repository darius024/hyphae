#!/usr/bin/env python3
"""Check presence of API keys in environment or .env and try to initialize clients.

This script will:
- Look for GEMINI_API_KEY, CACTUS_API_KEY, HUGGINGFACE_API_KEY in os.environ.
- If not found, attempt to parse a local `.env` file (simple parser).
- Try to construct a `google.genai.Client` with GEMINI_API_KEY (no network calls).
"""
import os
import sys


def load_dotenv(path):
    vals = {}
    try:
        with open(path, "r") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                # support: export KEY="val"  or KEY=val
                if line.startswith("export "):
                    line = line[len("export "):]
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                vals[k.strip()] = v
    except FileNotFoundError:
        pass
    return vals


def main():
    env = dict(os.environ)
    # try to load .env if any key missing
    keys = ["GEMINI_API_KEY", "CACTUS_API_KEY", "HUGGINGFACE_API_KEY"]
    missing = [k for k in keys if k not in env or not env[k]]
    if missing:
        loaded = load_dotenv(".env")
        for k in missing:
            if k in loaded and loaded[k]:
                env[k] = loaded[k]

    print("Environment keys present:")
    for k in keys:
        print(f"  {k}: {'YES' if k in env and env[k] else 'NO'}")

    # Try to import and initialize google.genai client if GEMINI key present
    if env.get("GEMINI_API_KEY"):
        try:
            import google.genai as genai
            client = genai.Client(api_key=env.get("GEMINI_API_KEY"))
            print("google.genai client: OK (constructed)")
        except Exception as e:
            print(f"google.genai client: FAILED to construct: {e}")
    else:
        print("google.genai client: SKIPPED (no GEMINI_API_KEY)")

    # No network calls are performed.


if __name__ == "__main__":
    main()
