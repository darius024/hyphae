"""Integration tests for Pydantic request validation (422 responses)."""

import pytest
from notebook import db as db_mod
from notebook.db import init_db


@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch):
    temp_db = tmp_path / "test.db"
    monkeypatch.setattr(db_mod, "DB_PATH", temp_db)
    init_db()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    corpus.mkdir()

    from routes import corpus as corpus_mod

    from web.app import app
    corpus_mod.configure(str(corpus), None)

    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture()
def auth_headers(client):
    """Register a test user and return Authorization headers."""
    r = client.post("/api/auth/signup", json={
        "email": "val@example.com",
        "password": "testpassword123",
        "name": "Validation User",
    })
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture()
def nb_id(client, auth_headers):
    r = client.post("/api/notebooks", json={"name": "Validation Test"}, headers=auth_headers)
    return r.json()["id"]


class TestNotebookValidation:
    def test_create_missing_body(self, client, auth_headers):
        r = client.post("/api/notebooks", headers=auth_headers)
        assert r.status_code == 422

    def test_create_empty_name(self, client, auth_headers):
        r = client.post("/api/notebooks", json={"name": ""}, headers=auth_headers)
        assert r.status_code == 422

    def test_update_empty_name(self, client, auth_headers, nb_id):
        r = client.patch(f"/api/notebooks/{nb_id}", json={"name": ""}, headers=auth_headers)
        assert r.status_code == 422


class TestConversationValidation:
    def test_create_missing_title(self, client, auth_headers, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/conversations", json={}, headers=auth_headers)
        assert r.status_code == 422

    def test_create_empty_title(self, client, auth_headers, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/conversations", json={"title": ""}, headers=auth_headers)
        assert r.status_code == 422

    def test_rename_empty_title(self, client, auth_headers, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/conversations", json={"title": "Test"}, headers=auth_headers)
        cid = r.json()["id"]
        r2 = client.patch(
            f"/api/notebooks/{nb_id}/conversations/{cid}",
            json={"title": ""},
            headers=auth_headers,
        )
        assert r2.status_code == 422


class TestChatValidation:
    def test_chat_missing_message(self, client, auth_headers, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/conversations", json={"title": "Test"}, headers=auth_headers)
        cid = r.json()["id"]
        r2 = client.post(
            f"/api/notebooks/{nb_id}/conversations/{cid}/chat",
            json={},
            headers=auth_headers,
        )
        assert r2.status_code == 422

    def test_chat_empty_message(self, client, auth_headers, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/conversations", json={"title": "Test"}, headers=auth_headers)
        cid = r.json()["id"]
        r2 = client.post(
            f"/api/notebooks/{nb_id}/conversations/{cid}/chat",
            json={"message": ""},
            headers=auth_headers,
        )
        assert r2.status_code == 422


class TestEventValidation:
    def test_create_missing_fields(self, client, auth_headers, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/events", json={}, headers=auth_headers)
        assert r.status_code == 422

    def test_create_missing_date(self, client, auth_headers, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/events", json={"title": "Test"}, headers=auth_headers)
        assert r.status_code == 422

    def test_create_missing_title(self, client, auth_headers, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/events", json={"date": "2026-03-01"}, headers=auth_headers)
        assert r.status_code == 422


class TestSettingsValidation:
    def test_update_missing_value(self, client, auth_headers):
        r = client.patch("/api/nb-settings/chunk_size", json={}, headers=auth_headers)
        assert r.status_code == 422


class TestSensitivityValidation:
    def test_invalid_level(self, client, auth_headers):
        r = client.put("/api/sensitivity/doc.txt", json={"level": "secret"}, headers=auth_headers)
        assert r.status_code == 422


class TestClassifyValidation:
    def test_missing_message(self, client, auth_headers):
        r = client.post("/api/classify", json={}, headers=auth_headers)
        assert r.status_code == 422


class TestUrlValidation:
    def test_add_url_missing_field(self, client, auth_headers, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/add-url", json={}, headers=auth_headers)
        assert r.status_code == 422

    def test_add_url_empty(self, client, auth_headers, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/add-url", json={"url": ""}, headers=auth_headers)
        assert r.status_code == 422
