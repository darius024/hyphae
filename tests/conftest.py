"""Shared pytest fixtures for Hyphae tests."""

import os
import sys

# Tests run offline against the deterministic dummy embedder for speed and
# reproducibility.  This MUST be set before any code under test imports
# ``web.notebook.embed`` so the cached model selection picks the dummy.
os.environ.setdefault("USE_DUMMY_EMBED", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("RATE_LIMIT_RPM", "0")

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (
    os.path.join(_project_root, "src"),
    os.path.join(_project_root, "web"),
    _project_root,
):
    if _p not in sys.path:
        sys.path.insert(0, _p)


import pytest


@pytest.fixture
def tmp_corpus(tmp_path, monkeypatch):
    """Create a temporary corpus directory with sample files."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()

    (corpus / "battery_notes.txt").write_text(
        "Battery cycling test results:\n"
        "Sample A: 95% capacity retention after 500 cycles.\n"
        "Sample B: 88% capacity retention after 500 cycles.\n"
        "FEC-3 additive improved capacity retention by 7%.\n"
    )

    (corpus / "polymer_log.txt").write_text(
        "Polymer synthesis log:\n"
        "Batch 12: Mw = 45,000 g/mol, PDI = 1.8\n"
        "Batch 13: Mw = 52,000 g/mol, PDI = 1.5\n"
        "Temperature: 180°C, reaction time: 4h\n"
    )

    from core import tools as tools_mod
    from ingestion import corpus as ingest_mod
    monkeypatch.setattr(ingest_mod, "CORPUS_DIR", str(corpus))
    monkeypatch.setattr(tools_mod, "CORPUS_DIR", str(corpus))
    monkeypatch.setattr(tools_mod, "NOTES_DIR", os.path.join(str(corpus), "notes"))

    yield corpus


@pytest.fixture
def sample_tools():
    """Return a minimal set of tool schemas for testing."""
    return [
        {
            "name": "get_weather",
            "description": "Get current weather for a location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name"}
                },
                "required": ["location"],
            },
        },
        {
            "name": "send_message",
            "description": "Send a message to a contact",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient": {"type": "string", "description": "Name of the person"},
                    "message": {"type": "string", "description": "The message content"},
                },
                "required": ["recipient", "message"],
            },
        },
    ]
