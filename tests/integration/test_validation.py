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

    from web.app import app
    from routes import corpus as corpus_mod
    corpus_mod.configure(str(corpus), None)

    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture()
def nb_id(client):
    r = client.post("/api/notebooks", json={"name": "Validation Test"})
    return r.json()["id"]


class TestNotebookValidation:
    def test_create_missing_body(self, client):
        r = client.post("/api/notebooks")
        assert r.status_code == 422

    def test_create_empty_name(self, client):
        r = client.post("/api/notebooks", json={"name": ""})
        assert r.status_code == 422

    def test_update_empty_name(self, client, nb_id):
        r = client.patch(f"/api/notebooks/{nb_id}", json={"name": ""})
        assert r.status_code == 422


class TestConversationValidation:
    def test_create_missing_title(self, client, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/conversations", json={})
        assert r.status_code == 422

    def test_create_empty_title(self, client, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/conversations", json={"title": ""})
        assert r.status_code == 422

    def test_rename_empty_title(self, client, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/conversations", json={"title": "Test"})
        cid = r.json()["id"]
        r2 = client.patch(
            f"/api/notebooks/{nb_id}/conversations/{cid}",
            json={"title": ""},
        )
        assert r2.status_code == 422


class TestChatValidation:
    def test_chat_missing_message(self, client, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/conversations", json={"title": "Test"})
        cid = r.json()["id"]
        r2 = client.post(
            f"/api/notebooks/{nb_id}/conversations/{cid}/chat",
            json={},
        )
        assert r2.status_code == 422

    def test_chat_empty_message(self, client, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/conversations", json={"title": "Test"})
        cid = r.json()["id"]
        r2 = client.post(
            f"/api/notebooks/{nb_id}/conversations/{cid}/chat",
            json={"message": ""},
        )
        assert r2.status_code == 422


class TestEventValidation:
    def test_create_missing_fields(self, client, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/events", json={})
        assert r.status_code == 422

    def test_create_missing_date(self, client, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/events", json={"title": "Test"})
        assert r.status_code == 422

    def test_create_missing_title(self, client, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/events", json={"date": "2026-03-01"})
        assert r.status_code == 422


class TestSettingsValidation:
    def test_update_missing_value(self, client):
        r = client.patch("/api/nb-settings/chunk_size", json={})
        assert r.status_code == 422


class TestSensitivityValidation:
    def test_invalid_level(self, client):
        r = client.put("/api/sensitivity/doc.txt", json={"level": "secret"})
        assert r.status_code == 422


class TestClassifyValidation:
    def test_missing_message(self, client):
        r = client.post("/api/classify", json={})
        assert r.status_code == 422


class TestUrlValidation:
    def test_add_url_missing_field(self, client, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/add-url", json={})
        assert r.status_code == 422

    def test_add_url_empty(self, client, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/add-url", json={"url": ""})
        assert r.status_code == 422
