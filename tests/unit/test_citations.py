"""Unit tests for the citations layer (web/notebook/citations.py).

Covers:
  - build_citations: empty input, field population, deduplication, snippet truncation,
    sequential numbering, chunk_id / score propagation
  - build_context_prompt: formatting, max_chunks cap
  - build_system_prompt: contains notebook name & citation instruction
  - GET /api/notebooks/{nb_id}/chunks/{chunk_id} endpoint: 200 with correct payload,
    404 for unknown notebook, 404 for unknown chunk
"""

from __future__ import annotations

import uuid

import pytest
from notebook.citations import build_citations, build_context_prompt, build_system_prompt
from notebook.models import Citation

# ─── Fixtures ────────────────────────────────────────────────────────────────

def _make_result(
    source_id: str = "src-1",
    source_title: str = "Paper A",
    page_number: int | None = 3,
    snippet: str = "Some relevant text.",
    chunk_id: str = "chunk-abc",
    score: float = 0.9,
) -> dict:
    return {
        "source_id": source_id,
        "source_title": source_title,
        "page_number": page_number,
        "snippet": snippet,
        "chunk_id": chunk_id,
        "score": score,
    }


# ─── build_citations ──────────────────────────────────────────────────────────

class TestBuildCitations:
    def test_empty_input_returns_empty_list(self):
        assert build_citations([]) == []

    def test_single_result_produces_one_citation(self):
        cits = build_citations([_make_result()])
        assert len(cits) == 1

    def test_citation_fields_populated(self):
        r = _make_result(
            source_id="s1",
            source_title="Hydrogel Study",
            page_number=7,
            snippet="Short snippet.",
            chunk_id="c-42",
            score=0.77,
        )
        cit = build_citations([r])[0]
        assert isinstance(cit, Citation)
        assert cit.number == 1
        assert cit.source_id == "s1"
        assert cit.source_title == "Hydrogel Study"
        assert cit.page_number == 7
        assert cit.snippet == "Short snippet."
        assert cit.chunk_id == "c-42"
        assert cit.score == pytest.approx(0.77)

    def test_snippet_truncated_to_200_chars(self):
        long_snippet = "x" * 500
        cit = build_citations([_make_result(snippet=long_snippet)])[0]
        assert len(cit.snippet) == 200

    def test_snippet_under_200_not_modified(self):
        short = "a" * 100
        cit = build_citations([_make_result(snippet=short)])[0]
        assert cit.snippet == short

    def test_sequential_numbering(self):
        results = [
            _make_result(source_id="s1", page_number=1),
            _make_result(source_id="s2", page_number=1),
            _make_result(source_id="s3", page_number=1),
        ]
        cits = build_citations(results)
        assert [c.number for c in cits] == [1, 2, 3]

    def test_deduplication_by_source_id_and_page(self):
        """Two chunks from the same source + page collapse into one citation."""
        r1 = _make_result(source_id="s1", page_number=2, snippet="First", chunk_id="c1")
        r2 = _make_result(source_id="s1", page_number=2, snippet="Second", chunk_id="c2")
        cits = build_citations([r1, r2])
        assert len(cits) == 1
        # The first occurrence wins
        assert cits[0].snippet == "First"
        assert cits[0].chunk_id == "c1"

    def test_same_source_different_pages_are_not_deduplicated(self):
        r1 = _make_result(source_id="s1", page_number=1)
        r2 = _make_result(source_id="s1", page_number=2)
        cits = build_citations([r1, r2])
        assert len(cits) == 2

    def test_none_page_number_treated_as_distinct_key(self):
        r1 = _make_result(source_id="s1", page_number=None)
        r2 = _make_result(source_id="s1", page_number=None)
        # Both have key ("s1", None) — second should be deduplicated
        cits = build_citations([r1, r2])
        assert len(cits) == 1

    def test_missing_chunk_id_defaults_to_none(self):
        r = _make_result()
        del r["chunk_id"]
        cit = build_citations([r])[0]
        assert cit.chunk_id is None

    def test_missing_score_defaults_to_none(self):
        r = _make_result()
        del r["score"]
        cit = build_citations([r])[0]
        assert cit.score is None

    def test_missing_source_title_defaults_to_untitled(self):
        r = _make_result()
        del r["source_title"]
        cit = build_citations([r])[0]
        assert cit.source_title == "Untitled"


# ─── build_context_prompt ─────────────────────────────────────────────────────

class TestBuildContextPrompt:
    def test_empty_returns_empty_string(self):
        assert build_context_prompt([]) == ""

    def test_formats_single_result(self):
        r = _make_result(source_title="Battery Notes", page_number=4, snippet="Capacity is high.")
        output = build_context_prompt([r])
        assert '[1] (Source: "Battery Notes", p. 4)' in output
        assert '"Capacity is high."' in output

    def test_no_page_number_omits_page(self):
        r = _make_result(source_title="Book", page_number=None, snippet="Text here.")
        output = build_context_prompt([r])
        assert "p." not in output
        assert '[1] (Source: "Book")' in output

    def test_max_chunks_respected(self):
        results = [_make_result(source_id=str(i), page_number=i) for i in range(10)]
        output = build_context_prompt(results, max_chunks=3)
        assert "[3]" in output
        assert "[4]" not in output

    def test_multiple_chunks_separated_by_blank_line(self):
        r1 = _make_result(source_id="s1", page_number=1, snippet="Alpha.")
        r2 = _make_result(source_id="s2", page_number=2, snippet="Beta.")
        output = build_context_prompt([r1, r2])
        assert "\n\n" in output


# ─── build_system_prompt ──────────────────────────────────────────────────────

class TestBuildSystemPrompt:
    def test_contains_notebook_name(self):
        prompt = build_system_prompt("context block", "My Lab Notebook")
        assert "My Lab Notebook" in prompt

    def test_contains_citation_instruction(self):
        prompt = build_system_prompt("context block", "NB")
        assert "[1]" in prompt or "Cite sources inline" in prompt

    def test_contains_context_block(self):
        prompt = build_system_prompt("THE_CONTEXT", "NB")
        assert "THE_CONTEXT" in prompt


# ─── GET /api/notebooks/{nb_id}/chunks/{chunk_id} (integration) ──────────────

@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch):
    """Redirect DB_PATH to a temp file so tests don't touch real data."""
    import notebook.db as db_mod
    from notebook.db import init_db

    temp_db = tmp_path / "test_citations.db"
    monkeypatch.setattr(db_mod, "DB_PATH", temp_db)
    init_db()


@pytest.fixture()
def client_with_chunk(tmp_path):
    """TestClient + pre-populated notebook/source/chunk.

    Uses the real auth flow: signs up a user and returns a valid Bearer token.
    """
    from fastapi.testclient import TestClient

    from web.app import app

    nb_id = str(uuid.uuid4())
    src_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())

    # Sign up so we have a real JWT and a user row in the DB
    with TestClient(app) as client:
        resp = client.post("/api/auth/signup", json={
            "email": "citations_test@example.com",
            "password": "supersecret123",
            "name": "Citations Tester",
        })
        assert resp.status_code == 200, resp.text
        token = resp.json()["token"]
        user_id = resp.json()["user"]["id"]
        headers = {"Authorization": f"Bearer {token}"}

        # Insert notebook and source/chunk directly into the DB
        import notebook.db as db_mod
        with db_mod.get_conn() as conn:
            conn.execute(
                "INSERT INTO notebooks (id, name, user_id) VALUES (?, ?, ?)",
                (nb_id, "Test Notebook", user_id),
            )
            conn.execute(
                "INSERT INTO sources (id, notebook_id, title, type, filename) VALUES (?, ?, ?, ?, ?)",
                (src_id, nb_id, "My Source", "file", "doc.pdf"),
            )
            conn.execute(
                """INSERT INTO chunks
                   (id, notebook_id, source_id, page_number, chunk_index,
                    raw_text, clean_text, token_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (chunk_id, nb_id, src_id, 2, 0, "raw text", "clean text", 10),
            )

        yield client, headers, nb_id, src_id, chunk_id


class TestGetChunkEndpoint:
    def test_returns_chunk_data(self, client_with_chunk):
        client, headers, nb_id, src_id, chunk_id = client_with_chunk
        resp = client.get(f"/api/notebooks/{nb_id}/chunks/{chunk_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == chunk_id
        assert data["source_id"] == src_id
        assert data["page_number"] == 2
        assert data["chunk_index"] == 0
        assert data["raw_text"] == "raw text"
        assert data["clean_text"] == "clean text"
        assert data["source_title"] == "My Source"
        assert data["filename"] == "doc.pdf"
        assert data["token_count"] == 10

    def test_unknown_notebook_returns_404(self, client_with_chunk):
        client, headers, _, _, chunk_id = client_with_chunk
        resp = client.get(f"/api/notebooks/nonexistent/chunks/{chunk_id}", headers=headers)
        assert resp.status_code == 404

    def test_unknown_chunk_returns_404(self, client_with_chunk):
        client, headers, nb_id, _, _ = client_with_chunk
        resp = client.get(f"/api/notebooks/{nb_id}/chunks/nonexistent-chunk", headers=headers)
        assert resp.status_code == 404

    def test_unauthenticated_returns_401(self, client_with_chunk):
        client, _, nb_id, _, chunk_id = client_with_chunk
        resp = client.get(f"/api/notebooks/{nb_id}/chunks/{chunk_id}")
        assert resp.status_code == 401

    def test_chunk_from_different_notebook_returns_404(self, client_with_chunk):
        """Chunk must not be accessible via a notebook it does not belong to."""
        client, headers, _nb_id, _src_id, chunk_id = client_with_chunk
        # Create a second notebook owned by the same user
        other_nb_resp = client.post("/api/notebooks", json={"name": "Other NB"}, headers=headers)
        assert other_nb_resp.status_code in (200, 201)
        other_nb_id = other_nb_resp.json()["id"]
        # Try to fetch the chunk using the wrong notebook ID in the URL
        resp = client.get(f"/api/notebooks/{other_nb_id}/chunks/{chunk_id}", headers=headers)
        assert resp.status_code == 404
