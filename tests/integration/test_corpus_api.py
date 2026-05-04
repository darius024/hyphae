"""Integration tests for the corpus upload/list/preview/delete endpoints.

These exercise the full HTTP layer end-to-end using a temporary corpus
directory and a lightweight add_file stub that copies files without needing
PyMuPDF or any ML dependencies.
"""

import io
import shutil
from pathlib import Path

import pytest
from notebook import db as db_mod
from notebook.db import init_db


@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    init_db()


@pytest.fixture()
def corpus_dir(tmp_path):
    d = tmp_path / "corpus"
    d.mkdir()
    return d


@pytest.fixture()
def client(corpus_dir, monkeypatch):
    from routes import corpus as corpus_mod

    from web.app import app

    # Stub add_file: simply copy the temp file written by the router into
    # CORPUS_DIR under dest_name (mirrors what ingestion.corpus.add_file does
    # for plain text without requiring PyMuPDF).
    def _stub_add_file(filepath: str, dest_name: str | None = None) -> bool:
        src = Path(filepath)
        if dest_name is None:
            dest_name = src.stem + ".txt"
        dest = corpus_dir / dest_name
        shutil.copy2(str(src), str(dest))
        return True

    corpus_mod.configure(str(corpus_dir), _stub_add_file)

    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture()
def auth_headers(client):
    r = client.post("/api/auth/signup", json={
        "email": "corpus@example.com",
        "password": "testpassword123",
        "name": "Corpus Tester",
    })
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── Tests ──────────────────────────────────────────────────────────────────

class TestListDocuments:
    def test_empty_corpus_returns_empty_list(self, client, auth_headers):
        r = client.get("/api/documents", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["documents"] == []
        assert data["count"] == 0
        assert data["total"] == 0

    def test_requires_authentication(self, client):
        r = client.get("/api/documents")
        assert r.status_code == 401


class TestUploadDocuments:
    def test_upload_txt_file(self, client, auth_headers, corpus_dir):
        content = b"Sample research data: FEC-3 showed improvement.\n"
        r = client.post(
            "/api/upload",
            files={"file": ("notes.txt", io.BytesIO(content), "text/plain")},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data["uploaded"]) == 1
        assert data["uploaded"][0]["added"] is True
        assert data["uploaded"][0]["filename"] == "notes.txt"

    def test_uploaded_file_appears_in_list(self, client, auth_headers):
        content = b"Battery cycling experiment results.\n"
        client.post(
            "/api/upload",
            files={"file": ("battery.txt", io.BytesIO(content), "text/plain")},
            headers=auth_headers,
        )
        r = client.get("/api/documents", headers=auth_headers)
        assert r.status_code == 200
        names = [d["name"] for d in r.json()["documents"]]
        assert "battery.txt" in names

    def test_upload_requires_authentication(self, client):
        r = client.post(
            "/api/upload",
            files={"file": ("x.txt", io.BytesIO(b"data"), "text/plain")},
        )
        assert r.status_code == 401


class TestPreviewDocument:
    def test_preview_returns_text_content(self, client, auth_headers, corpus_dir):
        (corpus_dir / "preview_me.txt").write_text("Hello from the corpus.\n")
        r = client.get("/api/documents/preview_me.txt", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "preview_me.txt"
        assert "Hello from the corpus." in data["preview"]

    def test_preview_missing_file_returns_404(self, client, auth_headers):
        r = client.get("/api/documents/no_such_file.txt", headers=auth_headers)
        assert r.status_code == 404

    def test_preview_requires_authentication(self, client, corpus_dir):
        (corpus_dir / "secret.txt").write_text("secret data")
        r = client.get("/api/documents/secret.txt")
        assert r.status_code == 401


class TestDeleteDocument:
    def test_delete_existing_document(self, client, auth_headers, corpus_dir):
        (corpus_dir / "to_delete.txt").write_text("temporary\n")
        r = client.delete("/api/documents/to_delete.txt", headers=auth_headers)
        assert r.status_code == 200
        assert not (corpus_dir / "to_delete.txt").exists()

    def test_delete_missing_document_returns_404(self, client, auth_headers):
        r = client.delete("/api/documents/ghost.txt", headers=auth_headers)
        assert r.status_code == 404

    def test_deleted_file_absent_from_list(self, client, auth_headers, corpus_dir):
        (corpus_dir / "ephemeral.txt").write_text("gone soon\n")
        client.delete("/api/documents/ephemeral.txt", headers=auth_headers)
        r = client.get("/api/documents", headers=auth_headers)
        names = [d["name"] for d in r.json()["documents"]]
        assert "ephemeral.txt" not in names
