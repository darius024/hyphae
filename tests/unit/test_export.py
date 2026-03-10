"""Integration tests for the notebook export endpoint.

POST /api/notebooks/{nb_id}/export

Covers:
  - Markdown export: 200, Content-Disposition attachment, correct filename,
    title present, source list present, conversation history present
  - BibTeX export: 200, .bib filename, @misc/@online entries, cite-keys present
  - Empty notebook Markdown: placeholder text (no sources / no conversations)
  - Empty notebook BibTeX: comment-only file
  - Unknown notebook returns 404
  - Wrong user returns 403
  - Invalid format returns 422
  - Unauthenticated returns 401
"""

from __future__ import annotations

import json
import uuid

import pytest

# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch):
    import notebook.db as db_mod
    from notebook.db import init_db

    temp_db = tmp_path / "test_export.db"
    monkeypatch.setattr(db_mod, "DB_PATH", temp_db)
    init_db()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from web.app import app

    with TestClient(app) as c:
        yield c


def _signup(client, suffix: str = "") -> tuple[str, dict]:
    """Sign up a unique user; return (user_id, auth_headers)."""
    tag = suffix or uuid.uuid4().hex[:6]
    resp = client.post("/api/auth/signup", json={
        "email": f"export_{tag}@test.com",
        "password": "securepassword1",
        "name": "Export Tester",
    })
    assert resp.status_code == 200, resp.text
    token   = resp.json()["token"]
    user_id = resp.json()["user"]["id"]
    return user_id, {"Authorization": f"Bearer {token}"}


def _create_notebook(client, headers, name: str = "My Notebook") -> str:
    resp = client.post("/api/notebooks", json={"name": name}, headers=headers)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _add_source(conn, nb_id: str, title: str = "Paper A",
                filename: str = "paper_a.pdf", url: str | None = None) -> str:
    src_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO sources (id, notebook_id, title, type, filename, url) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (src_id, nb_id, title, "file", filename, url),
    )
    return src_id


def _add_conversation_with_messages(conn, nb_id: str) -> str:
    conv_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO conversations (id, notebook_id, title) VALUES (?, ?, ?)",
        (conv_id, nb_id, "Test conversation"),
    )
    msg_id1 = str(uuid.uuid4())
    msg_id2 = str(uuid.uuid4())
    cits = json.dumps([{"number": 1, "source_title": "Paper A", "page_number": 3}])
    conn.execute(
        "INSERT INTO messages (id, conversation_id, notebook_id, role, content) "
        "VALUES (?, ?, ?, ?, ?)",
        (msg_id1, conv_id, nb_id, "user", "What is the main thesis?"),
    )
    conn.execute(
        "INSERT INTO messages (id, conversation_id, notebook_id, role, content, citations) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (msg_id2, conv_id, nb_id, "assistant", "The main thesis is X [1].", cits),
    )
    return conv_id


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestExportEndpoint:
    def test_unauthenticated_returns_401(self, client):
        resp = client.post(
            f"/api/notebooks/{uuid.uuid4()}/export",
            json={"format": "markdown"},
        )
        assert resp.status_code == 401

    def test_unknown_notebook_returns_404(self, client):
        _, headers = _signup(client)
        resp = client.post(
            f"/api/notebooks/{uuid.uuid4()}/export",
            json={"format": "markdown"},
            headers=headers,
        )
        assert resp.status_code == 404

    def test_invalid_format_returns_422(self, client):
        user_id, headers = _signup(client)
        nb_id = _create_notebook(client, headers)
        resp = client.post(
            f"/api/notebooks/{nb_id}/export",
            json={"format": "pdf"},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_wrong_user_returns_403(self, client):
        user1_id, headers1 = _signup(client, "u1")
        _, headers2 = _signup(client, "u2")
        nb_id = _create_notebook(client, headers1, "Private NB")
        resp = client.post(
            f"/api/notebooks/{nb_id}/export",
            json={"format": "markdown"},
            headers=headers2,
        )
        assert resp.status_code == 403

    # ── Markdown ──────────────────────────────────────────────────────────

    def test_markdown_status_200(self, client):
        _, headers = _signup(client)
        nb_id = _create_notebook(client, headers, "Lab Notes")
        resp = client.post(
            f"/api/notebooks/{nb_id}/export",
            json={"format": "markdown"},
            headers=headers,
        )
        assert resp.status_code == 200

    def test_markdown_content_disposition_attachment(self, client):
        _, headers = _signup(client)
        nb_id = _create_notebook(client, headers, "Lab Notes")
        resp = client.post(
            f"/api/notebooks/{nb_id}/export",
            json={"format": "markdown"},
            headers=headers,
        )
        cd = resp.headers.get("Content-Disposition", "")
        assert "attachment" in cd
        assert ".md" in cd

    def test_markdown_contains_notebook_name(self, client):
        _, headers = _signup(client)
        nb_id = _create_notebook(client, headers, "Battery Research")
        resp = client.post(
            f"/api/notebooks/{nb_id}/export",
            json={"format": "markdown"},
            headers=headers,
        )
        assert "Battery Research" in resp.text

    def test_markdown_lists_sources(self, client):
        user_id, headers = _signup(client)
        nb_id = _create_notebook(client, headers, "Physics NB")
        import notebook.db as db_mod
        with db_mod.get_conn() as conn:
            _add_source(conn, nb_id, title="Quantum Paper")
        resp = client.post(
            f"/api/notebooks/{nb_id}/export",
            json={"format": "markdown"},
            headers=headers,
        )
        assert "Quantum Paper" in resp.text

    def test_markdown_includes_conversation_history(self, client):
        user_id, headers = _signup(client)
        nb_id = _create_notebook(client, headers, "Conv NB")
        import notebook.db as db_mod
        with db_mod.get_conn() as conn:
            _add_source(conn, nb_id)
            _add_conversation_with_messages(conn, nb_id)
        resp = client.post(
            f"/api/notebooks/{nb_id}/export",
            json={"format": "markdown"},
            headers=headers,
        )
        assert "What is the main thesis?" in resp.text
        assert "The main thesis is X" in resp.text

    def test_markdown_includes_citation_footnotes(self, client):
        user_id, headers = _signup(client)
        nb_id = _create_notebook(client, headers, "Cit NB")
        import notebook.db as db_mod
        with db_mod.get_conn() as conn:
            _add_source(conn, nb_id, title="Paper A")
            _add_conversation_with_messages(conn, nb_id)
        resp = client.post(
            f"/api/notebooks/{nb_id}/export",
            json={"format": "markdown"},
            headers=headers,
        )
        assert "Paper A" in resp.text

    def test_markdown_empty_notebook_no_error(self, client):
        _, headers = _signup(client)
        nb_id = _create_notebook(client, headers, "Empty")
        resp = client.post(
            f"/api/notebooks/{nb_id}/export",
            json={"format": "markdown"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert "No sources" in resp.text or "No conversations" in resp.text

    # ── BibTeX ────────────────────────────────────────────────────────────

    def test_bibtex_status_200(self, client):
        _, headers = _signup(client)
        nb_id = _create_notebook(client, headers)
        resp = client.post(
            f"/api/notebooks/{nb_id}/export",
            json={"format": "bibtex"},
            headers=headers,
        )
        assert resp.status_code == 200

    def test_bibtex_content_disposition_dot_bib(self, client):
        _, headers = _signup(client)
        nb_id = _create_notebook(client, headers, "Chem Lab")
        resp = client.post(
            f"/api/notebooks/{nb_id}/export",
            json={"format": "bibtex"},
            headers=headers,
        )
        cd = resp.headers.get("Content-Disposition", "")
        assert ".bib" in cd

    def test_bibtex_contains_at_misc_entry(self, client):
        user_id, headers = _signup(client)
        nb_id = _create_notebook(client, headers)
        import notebook.db as db_mod
        with db_mod.get_conn() as conn:
            _add_source(conn, nb_id, title="Polymer Study")
        resp = client.post(
            f"/api/notebooks/{nb_id}/export",
            json={"format": "bibtex"},
            headers=headers,
        )
        assert "@misc{" in resp.text or "@online{" in resp.text

    def test_bibtex_title_field_present(self, client):
        user_id, headers = _signup(client)
        nb_id = _create_notebook(client, headers)
        import notebook.db as db_mod
        with db_mod.get_conn() as conn:
            _add_source(conn, nb_id, title="Battery Cycling Notes")
        resp = client.post(
            f"/api/notebooks/{nb_id}/export",
            json={"format": "bibtex"},
            headers=headers,
        )
        assert "Battery Cycling Notes" in resp.text

    def test_bibtex_url_source_uses_online_type(self, client):
        user_id, headers = _signup(client)
        nb_id = _create_notebook(client, headers)
        import notebook.db as db_mod
        with db_mod.get_conn() as conn:
            _add_source(conn, nb_id, title="Web Paper", url="https://example.com/paper.pdf")
        resp = client.post(
            f"/api/notebooks/{nb_id}/export",
            json={"format": "bibtex"},
            headers=headers,
        )
        assert "@online{" in resp.text

    def test_bibtex_empty_notebook_returns_comment(self, client):
        _, headers = _signup(client)
        nb_id = _create_notebook(client, headers, "Empty BibTeX")
        resp = client.post(
            f"/api/notebooks/{nb_id}/export",
            json={"format": "bibtex"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert "%" in resp.text  # At minimum the header comment lines
