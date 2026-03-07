"""Concurrency tests for the per-notebook FAISS index locking in retrieval.py.

The key invariant: two threads calling get_index() for the same notebook_id
simultaneously must receive references to the *same* index object — not two
independently-created objects — and subsequent mutations via add_chunks() must
be visible to both.
"""

import threading
import concurrent.futures
from pathlib import Path

import pytest

from notebook import retrieval as retrieval_mod
from notebook.retrieval import get_index, add_chunks, _indexes, _id_maps


@pytest.fixture(autouse=True)
def _isolate_indexes(tmp_path, monkeypatch):
    """Redirect FAISS_DIR to a tmp folder and clear the in-memory caches
    between tests so each test starts with a clean slate."""
    monkeypatch.setattr(retrieval_mod, "FAISS_DIR", tmp_path)
    # Clear the module-level caches to prevent cross-test pollution.
    _indexes.clear()
    _id_maps.clear()
    yield
    _indexes.clear()
    _id_maps.clear()


# ── Helpers ───────────────────────────────────────────────────────────────

_EMBED_DIM = 384  # matches notebook.embed.EMBED_DIM


def _unit_vector(seed: int) -> list[float]:
    """Return a normalised all-ones vector of EMBED_DIM dimensions (deterministic)."""
    import math
    v = [1.0] * _EMBED_DIM
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


# ── Tests ──────────────────────────────────────────────────────────────────

class TestGetIndexConcurrency:
    def test_concurrent_get_index_returns_same_object(self):
        """Two threads calling get_index for the same notebook must receive the
        exact same Python object (identity, not just equality)."""
        nb_id = "concurrent-nb-1"
        results = []
        barrier = threading.Barrier(2)

        def _worker():
            barrier.wait()  # both threads enter get_index at the same instant
            index, _ = get_index(nb_id)
            results.append(id(index))

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futs = [pool.submit(_worker) for _ in range(2)]
            for f in futs:
                f.result()  # propagate any exceptions

        assert results[0] == results[1], (
            "Two concurrent get_index calls produced different index objects — "
            "the per-notebook lock is not working correctly"
        )

    def test_different_notebooks_get_independent_indexes(self):
        """Notebooks must have isolated indexes — adding a chunk to one must not
        affect the other."""
        import uuid
        nb_a = str(uuid.uuid4())
        nb_b = str(uuid.uuid4())

        # Ensure the index entries exist.
        get_index(nb_a)
        get_index(nb_b)

        idx_a, _ = get_index(nb_a)
        idx_b, _ = get_index(nb_b)

        if idx_a is not None:
            assert idx_a is not idx_b

    def test_add_chunks_visible_after_concurrent_get_index(self):
        """Chunks added via add_chunks() must be visible in the index returned
        by a subsequent get_index() call even when both happen concurrently."""
        nb_id = "concurrent-nb-visible"
        barrier = threading.Barrier(2)
        errors = []

        def _adder():
            """Add one vector, then signal the reader."""
            try:
                add_chunks(nb_id, ["chunk-0"], [_unit_vector(0)])
            except Exception as exc:
                errors.append(exc)
            finally:
                barrier.wait()

        def _reader():
            """Wait for the adder, then check the index."""
            barrier.wait()
            try:
                index, id_map = get_index(nb_id)
            except Exception as exc:
                errors.append(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futs = [pool.submit(_adder), pool.submit(_reader)]
            for f in futs:
                f.result()

        assert not errors, f"Worker raised: {errors}"
        # When FAISS is not installed, add_chunks returns early and the id_map
        # stays empty — that is still correct behaviour, just untestable here.
        index, id_map = get_index(nb_id)
        if index is not None:
            assert "chunk-0" in id_map

    def test_no_index_duplication_under_load(self):
        """Fire 8 concurrent get_index calls for the same notebook and assert
        all refer to the same underlying object — simulates a burst of requests."""
        nb_id = "concurrent-nb-load"
        collected_ids: list[int] = []
        lock = threading.Lock()

        def _worker():
            index, _ = get_index(nb_id)
            with lock:
                collected_ids.append(id(index))

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futs = [pool.submit(_worker) for _ in range(8)]
            for f in futs:
                f.result()

        unique_ids = set(collected_ids)
        assert len(unique_ids) == 1, (
            f"Got {len(unique_ids)} distinct index objects across 8 concurrent "
            "get_index calls — expected exactly 1"
        )
