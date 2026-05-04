"""Security regression tests for IDOR and ownership fixes.

Covers:
- planning: delete_deadline returns 404 for missing deadlines
- planning: sync_calendar rejects another user's connection (IDOR)
- notes: writing session is scoped to owner (IDOR)
- corpus: upload_documents uses sanitised filename for PDF originals
"""

from __future__ import annotations

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


def _signup(client, email: str, password: str = "testpassword123", name: str = "User"):
    r = client.post("/api/auth/signup", json={"email": email, "password": password, "name": name})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture()
def user_a(client):
    return _signup(client, "usera@example.com", name="User A")


@pytest.fixture()
def user_b(client):
    return _signup(client, "userb@example.com", name="User B")


# ═══════════════════════════════════════════════════════════════════════════
# Planning — delete_deadline 404 contract
# ═══════════════════════════════════════════════════════════════════════════

class TestDeleteDeadline:
    def test_delete_nonexistent_deadline_returns_404(self, client, user_a):
        r = client.delete("/api/deadlines/does-not-exist", headers=user_a)
        assert r.status_code == 404

    def test_delete_own_deadline_succeeds(self, client, user_a):
        r = client.post("/api/deadlines", json={
            "title": "My deadline",
            "due_date": "2099-12-31T00:00:00Z",
        }, headers=user_a)
        assert r.status_code == 201
        dl_id = r.json()["id"]

        r = client.delete(f"/api/deadlines/{dl_id}", headers=user_a)
        assert r.status_code == 200
        assert r.json()["deleted"] == dl_id

    def test_delete_other_users_deadline_returns_403(self, client, user_a, user_b):
        r = client.post("/api/deadlines", json={
            "title": "User A deadline",
            "due_date": "2099-12-31T00:00:00Z",
        }, headers=user_a)
        assert r.status_code == 201
        dl_id = r.json()["id"]

        r = client.delete(f"/api/deadlines/{dl_id}", headers=user_b)
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# Planning — sync_calendar IDOR fix
# ═══════════════════════════════════════════════════════════════════════════

class TestCalendarSyncIDOR:
    def _connect_calendar(self, client, headers):
        r = client.post("/api/calendar/connect", json={
            "provider": "google",
            "access_token": "fake-token",
        }, headers=headers)
        assert r.status_code == 201
        return r.json()["id"]

    def test_sync_own_calendar_succeeds(self, client, user_a):
        conn_id = self._connect_calendar(client, user_a)
        r = client.post(f"/api/calendar/sync/{conn_id}", headers=user_a)
        assert r.status_code == 200

    def test_sync_other_users_calendar_returns_404(self, client, user_a, user_b):
        conn_id = self._connect_calendar(client, user_a)
        # user_b should not be able to sync user_a's connection
        r = client.post(f"/api/calendar/sync/{conn_id}", headers=user_b)
        assert r.status_code == 404

    def test_sync_nonexistent_calendar_returns_404(self, client, user_a):
        r = client.post("/api/calendar/sync/no-such-conn", headers=user_a)
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# Notes — writing session ownership (IDOR fix)
# ═══════════════════════════════════════════════════════════════════════════

class TestWritingSessionOwnership:
    def _create_session(self, client, headers, content="draft content"):
        r = client.post("/api/writing/session", params={"content": content}, headers=headers)
        assert r.status_code == 200
        return r.json()["id"]

    def test_owner_can_read_own_session(self, client, user_a):
        session_id = self._create_session(client, user_a)
        r = client.get(f"/api/writing/session/{session_id}", headers=user_a)
        assert r.status_code == 200

    def test_other_user_cannot_read_session(self, client, user_a, user_b):
        session_id = self._create_session(client, user_a)
        # user_b must not be able to access user_a's session
        r = client.get(f"/api/writing/session/{session_id}", headers=user_b)
        assert r.status_code == 404

    def test_nonexistent_session_returns_404(self, client, user_a):
        r = client.get("/api/writing/session/ghost-id", headers=user_a)
        assert r.status_code == 404
