"""Tests for FAISS index persistence in :mod:`web.notebook.retrieval`.

We exercise the on-disk save/load round-trip: add some chunks, evict the
in-memory copy, then read the index back and confirm vector search returns
the same chunk IDs in the same order.
"""
from __future__ import annotations

import importlib
import sys

import pytest

pytest.importorskip("faiss")
import numpy as np


@pytest.fixture
def isolated_faiss(tmp_path, monkeypatch):
    """Re-import retrieval.py with FAISS_DIR pointing at a private temp dir.

    Each test gets a fresh module so the in-memory ``_indexes`` dict and
    eviction thread state cannot bleed across tests.
    """
    # Point the module-level FAISS_DIR at the temp dir before import-time
    # initialization runs.
    monkeypatch.setenv("FAISS_INDEX_TTL", "0")  # disable eviction thread
    sys.modules.pop("notebook.retrieval", None)
    sys.modules.pop("web.notebook.retrieval", None)
    retrieval = importlib.import_module("notebook.retrieval")
    monkeypatch.setattr(retrieval, "FAISS_DIR", tmp_path)
    yield retrieval
    sys.modules.pop("notebook.retrieval", None)


def _unit_vector(seed: int, dim: int = 384) -> list[float]:
    """Return a deterministic unit-norm vector indexed by *seed*."""
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(dim).astype(np.float32)
    vec /= np.linalg.norm(vec)
    return vec.tolist()


class TestRoundTrip:
    def test_add_then_search_returns_added_chunk(self, isolated_faiss):
        retrieval = isolated_faiss
        nb_id = "nb-roundtrip"
        chunk_ids = ["chunk-a", "chunk-b", "chunk-c"]
        vectors = [_unit_vector(seed) for seed in (1, 2, 3)]

        retrieval.add_chunks(nb_id, chunk_ids, vectors)
        # Querying with the exact stored vector should return that chunk first.
        hits = retrieval.vector_search(nb_id, vectors[1], top_k=1)
        assert len(hits) == 1
        assert hits[0][0] == "chunk-b"

    def test_index_survives_in_memory_eviction(self, isolated_faiss):
        retrieval = isolated_faiss
        nb_id = "nb-persist"
        chunk_ids = ["alpha", "beta"]
        vectors = [_unit_vector(seed) for seed in (10, 11)]
        retrieval.add_chunks(nb_id, chunk_ids, vectors)

        # Drop the in-memory state so the next call must re-read from disk.
        retrieval._indexes.pop(nb_id, None)
        retrieval._id_maps.pop(nb_id, None)
        retrieval._last_access.pop(nb_id, None)

        hits = retrieval.vector_search(nb_id, vectors[0], top_k=1)
        assert hits and hits[0][0] == "alpha"

    def test_index_files_written_atomically(self, isolated_faiss, tmp_path):
        """The .tmp swap-file must not linger after a successful write."""
        retrieval = isolated_faiss
        retrieval.add_chunks("nb-atomic", ["c1"], [_unit_vector(20)])
        index_files = list(retrieval.FAISS_DIR.iterdir())
        # We expect <nb_id>.index plus <nb_id>.ids; never a leftover .tmp.
        assert any(p.suffix == ".index" for p in index_files)
        assert any(p.suffix == ".ids" for p in index_files)
        assert not any(p.suffix == ".tmp" for p in index_files)


class TestEmptyIndexBehavior:
    def test_search_on_empty_index_returns_no_results(self, isolated_faiss):
        retrieval = isolated_faiss
        hits = retrieval.vector_search("never-populated", _unit_vector(1), top_k=5)
        assert hits == []
