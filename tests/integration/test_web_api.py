"""Integration tests for the FastAPI web API endpoints.

Uses FastAPI TestClient (synchronous httpx) so no running server is needed.
Redirects the database to a temporary file per test session.
"""

import sys
import os
import json
import pytest

from notebook import db as db_mod
from notebook.db import init_db, get_conn


@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch):
    temp_db = tmp_path / "test.db"
    monkeypatch.setattr(db_mod, "DB_PATH", temp_db)
    init_db()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "sample_doc.txt").write_text("Sample document content for testing.")
    (corpus / ".sensitivity.json").write_text('{"sample_doc.txt": "shareable"}')

    from web.app import app
    from routes import corpus as corpus_mod
    corpus_mod.configure(str(corpus), None)

    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture()
def auth_headers(client):
    """Register a test user and return Authorization headers."""
    r = client.post("/api/auth/signup", json={
        "email": "test@example.com",
        "password": "testpassword123",
        "name": "Test User",
    })
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ═══════════════════════════════════════════════════════════════════════════
# Corpus endpoints
# ═══════════════════════════════════════════════════════════════════════════

class TestDocumentsApi:
    def test_list_documents(self, client, auth_headers):
        r = client.get("/api/documents", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 1
        names = [d["name"] for d in data["documents"]]
        assert "sample_doc.txt" in names

    def test_list_documents_unauthenticated(self, client):
        r = client.get("/api/documents")
        assert r.status_code == 401

    def test_preview_document(self, client, auth_headers):
        r = client.get("/api/documents/sample_doc.txt", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "Sample document content" in data["preview"]
        assert data["size_kb"] >= 0

    def test_preview_nonexistent(self, client, auth_headers):
        r = client.get("/api/documents/nonexistent.txt", headers=auth_headers)
        assert r.status_code == 404

    def test_delete_document(self, client, auth_headers):
        r = client.delete("/api/documents/sample_doc.txt", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["removed"] == "sample_doc.txt"
        r2 = client.get("/api/documents/sample_doc.txt", headers=auth_headers)
        assert r2.status_code == 404

    def test_delete_nonexistent(self, client, auth_headers):
        r = client.delete("/api/documents/nope.txt", headers=auth_headers)
        assert r.status_code == 404


class TestSensitivityApi:
    def test_get_sensitivity(self, client, auth_headers):
        r = client.get("/api/sensitivity", headers=auth_headers)
        assert r.status_code == 200
        assert "tags" in r.json()

    def test_set_sensitivity(self, client, auth_headers):
        r = client.put("/api/sensitivity/sample_doc.txt", json={"level": "confidential"}, headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["level"] == "confidential"

    def test_set_invalid_level(self, client, auth_headers):
        r = client.put("/api/sensitivity/sample_doc.txt", json={"level": "secret"}, headers=auth_headers)
        assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# Privacy / classify endpoints
# ═══════════════════════════════════════════════════════════════════════════

class TestClassifyApi:
    def test_local_query(self, client, auth_headers):
        r = client.post("/api/classify", json={"message": "summarize my notes"}, headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["route"] == "local"

    def test_cloud_query(self, client, auth_headers):
        r = client.post("/api/classify", json={"message": "search published literature"}, headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["route"] == "cloud"

    def test_empty_query_rejected(self, client, auth_headers):
        r = client.post("/api/classify", json={"message": ""}, headers=auth_headers)
        assert r.status_code == 422

    def test_classify_unauthenticated(self, client):
        r = client.post("/api/classify", json={"message": "test"})
        assert r.status_code == 401


class TestPrivacyLogApi:
    def test_returns_entries(self, client, auth_headers):
        r = client.get("/api/privacy-log", headers=auth_headers)
        assert r.status_code == 200
        assert "entries" in r.json()


# ═══════════════════════════════════════════════════════════════════════════
# Tools endpoint
# ═══════════════════════════════════════════════════════════════════════════

class TestToolsApi:
    def test_returns_list(self, client, auth_headers):
        r = client.get("/api/tools", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "tools" in data
        assert "count" in data


# ═══════════════════════════════════════════════════════════════════════════
# Notebook endpoints
# ═══════════════════════════════════════════════════════════════════════════

class TestNotebookApi:
    def test_list_notebooks(self, client, auth_headers):
        r = client.get("/api/notebooks", headers=auth_headers)
        assert r.status_code == 200
        assert "notebooks" in r.json()

    def test_list_notebooks_unauthenticated(self, client):
        r = client.get("/api/notebooks")
        assert r.status_code == 401

    def test_create_notebook(self, client, auth_headers):
        r = client.post("/api/notebooks", json={"name": "Test Notebook"}, headers=auth_headers)
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "Test Notebook"
        assert "id" in data

    def test_create_notebook_unauthenticated(self, client):
        r = client.post("/api/notebooks", json={"name": "No Auth"})
        assert r.status_code == 401

    def test_get_notebook(self, client, auth_headers):
        r = client.post("/api/notebooks", json={"name": "Get Test"}, headers=auth_headers)
        nb_id = r.json()["id"]
        r2 = client.get(f"/api/notebooks/{nb_id}", headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["name"] == "Get Test"

    def test_get_nonexistent(self, client, auth_headers):
        r = client.get("/api/notebooks/nonexistent-id", headers=auth_headers)
        assert r.status_code == 404

    def test_update_notebook(self, client, auth_headers):
        r = client.post("/api/notebooks", json={"name": "Original"}, headers=auth_headers)
        nb_id = r.json()["id"]
        r2 = client.patch(f"/api/notebooks/{nb_id}", json={"name": "Updated"}, headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["name"] == "Updated"

    def test_delete_notebook(self, client, auth_headers):
        r = client.post("/api/notebooks", json={"name": "Delete Me"}, headers=auth_headers)
        nb_id = r.json()["id"]
        r2 = client.delete(f"/api/notebooks/{nb_id}", headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["deleted"] == nb_id
        r3 = client.get(f"/api/notebooks/{nb_id}", headers=auth_headers)
        assert r3.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# Conversation endpoints
# ═══════════════════════════════════════════════════════════════════════════

class TestConversationApi:
    @pytest.fixture()
    def nb_id(self, client, auth_headers):
        r = client.post("/api/notebooks", json={"name": "Conv Test"}, headers=auth_headers)
        return r.json()["id"]

    def test_list_conversations_empty(self, client, auth_headers, nb_id):
        r = client.get(f"/api/notebooks/{nb_id}/conversations", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["conversations"] == []

    def test_create_conversation(self, client, auth_headers, nb_id):
        r = client.post(
            f"/api/notebooks/{nb_id}/conversations",
            json={"title": "My Chat"},
            headers=auth_headers,
        )
        assert r.status_code == 201
        assert r.json()["title"] == "My Chat"

    def test_rename_conversation(self, client, auth_headers, nb_id):
        r = client.post(
            f"/api/notebooks/{nb_id}/conversations",
            json={"title": "Old Title"},
            headers=auth_headers,
        )
        cid = r.json()["id"]
        r2 = client.patch(
            f"/api/notebooks/{nb_id}/conversations/{cid}",
            json={"title": "New Title"},
            headers=auth_headers,
        )
        assert r2.status_code == 200
        assert r2.json()["title"] == "New Title"

    def test_rename_empty_title_fails(self, client, auth_headers, nb_id):
        r = client.post(
            f"/api/notebooks/{nb_id}/conversations",
            json={"title": "Test"},
            headers=auth_headers,
        )
        cid = r.json()["id"]
        r2 = client.patch(
            f"/api/notebooks/{nb_id}/conversations/{cid}",
            json={"title": ""},
            headers=auth_headers,
        )
        assert r2.status_code == 422

    def test_delete_conversation(self, client, auth_headers, nb_id):
        r = client.post(
            f"/api/notebooks/{nb_id}/conversations",
            json={"title": "To Delete"},
            headers=auth_headers,
        )
        cid = r.json()["id"]
        r2 = client.delete(
            f"/api/notebooks/{nb_id}/conversations/{cid}",
            headers=auth_headers,
        )
        assert r2.status_code == 200
        r3 = client.get(f"/api/notebooks/{nb_id}/conversations", headers=auth_headers)
        ids = [c["id"] for c in r3.json()["conversations"]]
        assert cid not in ids

    def test_list_messages_empty(self, client, auth_headers, nb_id):
        r = client.post(
            f"/api/notebooks/{nb_id}/conversations",
            json={"title": "Empty"},
            headers=auth_headers,
        )
        cid = r.json()["id"]
        r2 = client.get(f"/api/notebooks/{nb_id}/conversations/{cid}/messages", headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["messages"] == []


# ═══════════════════════════════════════════════════════════════════════════
# Settings endpoints
# ═══════════════════════════════════════════════════════════════════════════

class TestSettingsApi:
    def test_get_settings(self, client, auth_headers):
        r = client.get("/api/nb-settings", headers=auth_headers)
        assert r.status_code == 200
        keys = [s["key"] for s in r.json()["settings"]]
        assert "embed_model" in keys

    def test_update_setting(self, client, auth_headers):
        r = client.patch("/api/nb-settings/retrieval_top_k", json={"value": "10"}, headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["value"] == "10"

    def test_update_missing_value(self, client, auth_headers):
        r = client.patch("/api/nb-settings/chunk_size", json={}, headers=auth_headers)
        assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# Static / SPA
# ═══════════════════════════════════════════════════════════════════════════

class TestStaticRoutes:
    def test_index_returns_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "Hyphae" in r.text

    def test_static_css(self, client):
        r = client.get("/static/style.css")
        assert r.status_code == 200

    def test_static_js(self, client):
        r = client.get("/static/app.js")
        assert r.status_code == 200
