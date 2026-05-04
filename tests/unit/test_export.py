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
  - Unit tests for pure helpers: _slugify, _escape_bibtex, _bibtex_key
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
        _user_id, headers = _signup(client)
        nb_id = _create_notebook(client, headers)
        resp = client.post(
            f"/api/notebooks/{nb_id}/export",
            json={"format": "pdf"},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_wrong_user_returns_403(self, client):
        _user1_id, headers1 = _signup(client, "u1")
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
        _user_id, headers = _signup(client)
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
        _user_id, headers = _signup(client)
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
        _user_id, headers = _signup(client)
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
        _user_id, headers = _signup(client)
        nb_id = _create_notebook(client, headers)
        import notebook.db as db_mod
        with db_mod.get_conn() as conn:
            _add_source(conn, nb_id, title="Polymer Study")
        resp = client.post(
            f"/api/notebooks/{nb_id}/export",
            json={"format": "bibtex"},
            headers=headers,
        )
        assert "@misc{" in resp.text

    def test_bibtex_title_field_present(self, client):
        _user_id, headers = _signup(client)
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

    def test_bibtex_url_source_uses_misc_type(self, client):
        """URL sources must use @misc, not @online (BibLaTeX-only)."""
        _user_id, headers = _signup(client)
        nb_id = _create_notebook(client, headers)
        import notebook.db as db_mod
        with db_mod.get_conn() as conn:
            _add_source(conn, nb_id, title="Web Paper", url="https://example.com/paper.pdf")
        resp = client.post(
            f"/api/notebooks/{nb_id}/export",
            json={"format": "bibtex"},
            headers=headers,
        )
        assert "@misc{" in resp.text
        assert "@online{" not in resp.text

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

    def test_bibtex_special_chars_in_title_are_escaped(self, client):
        """BibTeX special characters in source titles must be escaped."""
        _user_id, headers = _signup(client)
        nb_id = _create_notebook(client, headers)
        import notebook.db as db_mod
        with db_mod.get_conn() as conn:
            _add_source(conn, nb_id, title="Self-Healing & Conductive_Hydrogels 100%")
        resp = client.post(
            f"/api/notebooks/{nb_id}/export",
            json={"format": "bibtex"},
            headers=headers,
        )
        assert resp.status_code == 200
        # Raw special chars must not appear unescaped in field values
        assert r"\&" in resp.text
        assert r"\%" in resp.text
        assert r"\_" in resp.text

    def test_non_ascii_notebook_name_produces_ascii_filename(self, client):
        """Non-ASCII notebook names must not leak into Content-Disposition headers."""
        _, headers = _signup(client)
        nb_id = _create_notebook(client, headers, "Café Résearch Nötebook")
        resp = client.post(
            f"/api/notebooks/{nb_id}/export",
            json={"format": "markdown"},
            headers=headers,
        )
        assert resp.status_code == 200
        cd = resp.headers.get("Content-Disposition", "")
        # The filename in Content-Disposition must be ASCII-safe
        cd.encode("ascii")  # raises UnicodeEncodeError if non-ASCII present
        assert ".md" in cd

    def test_markdown_malformed_citations_json_does_not_crash(self, client):
        """A message with malformed citations JSON must not cause a 500 error."""
        _user_id, headers = _signup(client)
        nb_id = _create_notebook(client, headers, "Malformed Cits NB")
        import uuid as _uuid

        import notebook.db as db_mod
        with db_mod.get_conn() as conn:
            _add_source(conn, nb_id)
            conv_id = str(_uuid.uuid4())
            conn.execute(
                "INSERT INTO conversations (id, notebook_id, title) VALUES (?, ?, ?)",
                (conv_id, nb_id, "Broken conv"),
            )
            msg_id = str(_uuid.uuid4())
            conn.execute(
                "INSERT INTO messages (id, conversation_id, notebook_id, role, content, citations) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (msg_id, conv_id, nb_id, "assistant", "Answer.", "not-valid-json{{{"),
            )
        resp = client.post(
            f"/api/notebooks/{nb_id}/export",
            json={"format": "markdown"},
            headers=headers,
        )
        assert resp.status_code == 200


# ── Pure-function unit tests ──────────────────────────────────────────────────

class TestSlugify:
    """Unit tests for routes.export._slugify — no DB, no HTTP."""

    def _fn(self):
        from routes.export import _slugify
        return _slugify

    def test_basic_ascii(self):
        assert self._fn()("My Notebook") == "my-notebook"

    def test_non_ascii_stripped(self):
        assert self._fn()("Résumé Notes") == "rsum-notes"

    def test_special_chars_removed(self):
        assert self._fn()("Hello! World? (2024)") == "hello-world-2024"

    def test_multiple_spaces_collapse(self):
        assert self._fn()("a   b   c") == "a-b-c"

    def test_leading_trailing_dashes_stripped(self):
        assert self._fn()("---hello---") == "hello"

    def test_empty_string_returns_default(self):
        assert self._fn()("") == "notebook"

    def test_only_special_chars_returns_default(self):
        assert self._fn()("!!!???") == "notebook"

    def test_numbers_preserved(self):
        assert self._fn()("2024 Study") == "2024-study"


class TestEscapeBibtex:
    """Unit tests for routes.export._escape_bibtex."""

    def _fn(self):
        from routes.export import _escape_bibtex
        return _escape_bibtex

    def test_plain_text_unchanged(self):
        assert self._fn()("Hello World") == "Hello World"

    def test_escapes_ampersand(self):
        assert self._fn()("A & B") == r"A \& B"

    def test_escapes_hash(self):
        assert self._fn()("item #1") == r"item \#1"

    def test_escapes_underscore(self):
        assert self._fn()("snake_case") == r"snake\_case"

    def test_escapes_percent(self):
        assert self._fn()("50% done") == r"50\% done"

    def test_escapes_braces(self):
        assert self._fn()("{value}") == r"\{value\}"

    def test_multiple_specials_in_one_string(self):
        result = self._fn()("50% & #1")
        assert r"\%" in result
        assert r"\&" in result
        assert r"\#" in result


class TestBibtexKey:
    """Unit tests for routes.export._bibtex_key."""

    def _fn(self):
        from routes.export import _bibtex_key
        return _bibtex_key

    def test_key_starts_with_hyphae(self):
        key = self._fn()("My Paper", "abc123")
        assert key.startswith("hyphae_")

    def test_key_includes_short_id(self):
        src_id = "550e8400-e29b-41d4-a716-446655440000"
        key = self._fn()("Paper", src_id)
        # short_id strips dashes and takes first 6 chars
        assert "550e84" in key

    def test_two_titles_same_short_id_differ_by_slug(self):
        src_id = "aabbccdd"
        k1 = self._fn()("Alpha Paper", src_id)
        k2 = self._fn()("Beta Paper", src_id)
        assert k1 != k2

    def test_empty_title_falls_back_to_id_only(self):
        src_id = "xxyyzz"
        key = self._fn()("", src_id)
        # empty title: _slugify("") returns 'notebook', so key has that slug
        assert key.startswith("hyphae_")
        assert src_id[:6] in key

    def test_key_contains_only_safe_chars(self):
        import re
        key = self._fn()("A paper with & special % chars", "testid1")
        assert re.fullmatch(r"[a-z0-9_\-]+", key), f"Unsafe key: {key!r}"
