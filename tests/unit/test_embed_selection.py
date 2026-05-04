"""Tests for the embedder selection logic in ``web.notebook.embed``.

These verify that:
- ``USE_DUMMY_EMBED=1`` forces the deterministic fallback.
- ``USE_DUMMY_EMBED`` unset prefers the real ``SentenceTransformer`` model.
- A real-model load failure falls back to the dummy embedder *and* the
  ``is_using_real_embedder()`` indicator reflects the degradation.
"""
from __future__ import annotations

import importlib
import sys

import pytest


def _reload_embed_module(monkeypatch, dummy_flag: str | None):
    """Reload ``web.notebook.embed`` with the desired USE_DUMMY_EMBED state.

    The module caches the model resolution via ``functools.lru_cache``, so we
    must drop and re-import it for each scenario.
    """
    if dummy_flag is None:
        monkeypatch.delenv("USE_DUMMY_EMBED", raising=False)
    else:
        monkeypatch.setenv("USE_DUMMY_EMBED", dummy_flag)
    sys.modules.pop("notebook.embed", None)
    sys.modules.pop("web.notebook.embed", None)
    return importlib.import_module("notebook.embed")


class TestEmbedderSelection:
    def test_dummy_flag_forces_fallback(self, monkeypatch):
        embed_mod = _reload_embed_module(monkeypatch, "1")
        assert embed_mod.is_using_real_embedder() is False
        vec = embed_mod.embed_one("hello world")
        assert len(vec) == embed_mod.EMBED_DIM

    def test_real_model_preferred_when_flag_unset(self, monkeypatch):
        """Without USE_DUMMY_EMBED, embed should load the real model."""
        # Stub sentence_transformers so the test is hermetic.
        class _FakeST:
            def __init__(self, name):
                self.name = name

            def encode(self, texts, convert_to_numpy=True, show_progress_bar=False):
                import numpy as np
                return np.zeros((len(texts), 384), dtype="float32")

        fake_pkg = type(sys)("sentence_transformers")
        fake_pkg.SentenceTransformer = _FakeST
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake_pkg)

        embed_mod = _reload_embed_module(monkeypatch, None)
        assert embed_mod.is_using_real_embedder() is True

    def test_load_failure_falls_back_and_marks_degraded(self, monkeypatch):
        """If sentence_transformers raises, embed must keep working but
        is_using_real_embedder() must report False so /ready surfaces the issue."""
        class _BadST:
            def __init__(self, name):  # pragma: no cover - exercised via raises
                raise RuntimeError("simulated load failure")

        fake_pkg = type(sys)("sentence_transformers")
        fake_pkg.SentenceTransformer = _BadST
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake_pkg)

        embed_mod = _reload_embed_module(monkeypatch, None)
        # Embedding still works …
        vec = embed_mod.embed_one("hello")
        assert len(vec) == embed_mod.EMBED_DIM
        # … but the indicator reflects degraded mode.
        assert embed_mod.is_using_real_embedder() is False


@pytest.fixture(autouse=True)
def _restore_dummy_for_other_tests(monkeypatch):
    """After each test in this module, restore the conftest default so other
    tests in the suite continue to use the deterministic dummy embedder."""
    yield
    sys.modules.pop("notebook.embed", None)
    sys.modules.pop("web.notebook.embed", None)
    monkeypatch.setenv("USE_DUMMY_EMBED", "1")
