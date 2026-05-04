"""Integration tests for tags, analytics, and planning API endpoints."""

import pytest
from notebook import db as db_mod
from notebook.db import get_conn, init_db


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
    r = client.post("/api/auth/signup", json={
        "email": "tagtest@example.com",
        "password": "testpassword123",
        "name": "Tag Tester",
    })
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture()
def nb_id(client, auth_headers):
    r = client.post("/api/notebooks", json={"name": "Tag Test NB"}, headers=auth_headers)
    return r.json()["id"]


@pytest.fixture()
def source_ids(client, auth_headers, nb_id):
    """Create two stub sources directly in the DB for tagging/linking tests."""
    import uuid
    ids = []
    for title in ("Doc A", "Doc B"):
        src_id = str(uuid.uuid4())
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO sources (id, notebook_id, type, title, status) VALUES (?,?,?,?,?)",
                (src_id, nb_id, "txt", title, "done"),
            )
        ids.append(src_id)
    return ids


# ═══════════════════════════════════════════════════════════════════════════
# Tags
# ═══════════════════════════════════════════════════════════════════════════

class TestTagCrud:
    def test_list_tags_empty(self, client, auth_headers):
        r = client.get("/api/tags", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["tags"] == []

    def test_create_tag(self, client, auth_headers):
        r = client.post("/api/tags", json={"name": "urgent", "color": "#ff0000"}, headers=auth_headers)
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "urgent"
        assert data["color"] == "#ff0000"
        assert "id" in data

    def test_create_duplicate_tag(self, client, auth_headers):
        client.post("/api/tags", json={"name": "dup"}, headers=auth_headers)
        r = client.post("/api/tags", json={"name": "dup"}, headers=auth_headers)
        assert r.status_code == 400

    def test_update_tag(self, client, auth_headers):
        r = client.post("/api/tags", json={"name": "old"}, headers=auth_headers)
        tag_id = r.json()["id"]
        r2 = client.patch(f"/api/tags/{tag_id}", json={"name": "new", "color": "#00ff00"}, headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["updated"] is True

    def test_update_nonexistent_tag(self, client, auth_headers):
        r = client.patch("/api/tags/no-such-id", json={"name": "x"}, headers=auth_headers)
        assert r.status_code == 404

    def test_delete_tag(self, client, auth_headers):
        r = client.post("/api/tags", json={"name": "temp"}, headers=auth_headers)
        tag_id = r.json()["id"]
        r2 = client.delete(f"/api/tags/{tag_id}", headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["deleted"] == tag_id

    def test_create_tag_invalid_color(self, client, auth_headers):
        r = client.post("/api/tags", json={"name": "bad", "color": "red"}, headers=auth_headers)
        assert r.status_code == 422

    def test_list_tags_unauthenticated(self, client):
        r = client.get("/api/tags")
        assert r.status_code == 401


class TestSourceTags:
    def test_get_source_tags_empty(self, client, auth_headers, nb_id, source_ids):
        r = client.get(f"/api/notebooks/{nb_id}/sources/{source_ids[0]}/tags", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["tags"] == []

    def test_set_and_get_source_tags(self, client, auth_headers, nb_id, source_ids):
        tag_r = client.post("/api/tags", json={"name": "ml"}, headers=auth_headers)
        tag_id = tag_r.json()["id"]

        r = client.put(
            f"/api/notebooks/{nb_id}/sources/{source_ids[0]}/tags",
            json={"tag_ids": [tag_id]},
            headers=auth_headers,
        )
        assert r.status_code == 200

        r2 = client.get(f"/api/notebooks/{nb_id}/sources/{source_ids[0]}/tags", headers=auth_headers)
        assert len(r2.json()["tags"]) == 1
        assert r2.json()["tags"][0]["name"] == "ml"

    def test_set_tags_nonexistent_source(self, client, auth_headers, nb_id):
        r = client.put(
            f"/api/notebooks/{nb_id}/sources/no-such-src/tags",
            json={"tag_ids": []},
            headers=auth_headers,
        )
        assert r.status_code == 404


class TestKnowledgeGraph:
    def test_empty_graph(self, client, auth_headers, nb_id):
        r = client.get(f"/api/notebooks/{nb_id}/graph", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["nodes"] == []
        assert r.json()["edges"] == []

    def test_graph_with_sources(self, client, auth_headers, nb_id, source_ids):
        r = client.get(f"/api/notebooks/{nb_id}/graph", headers=auth_headers)
        assert r.status_code == 200
        assert len(r.json()["nodes"]) == 2

    def test_create_and_delete_link(self, client, auth_headers, nb_id, source_ids):
        r = client.post(
            f"/api/notebooks/{nb_id}/sources/{source_ids[0]}/links",
            json={"target_id": source_ids[1], "link_type": "cites"},
            headers=auth_headers,
        )
        assert r.status_code == 201
        link_id = r.json()["id"]
        assert r.json()["link_type"] == "cites"

        r2 = client.get(f"/api/notebooks/{nb_id}/graph", headers=auth_headers)
        assert len(r2.json()["edges"]) == 1

        r3 = client.delete(f"/api/notebooks/{nb_id}/links/{link_id}", headers=auth_headers)
        assert r3.status_code == 200

    def test_self_link_rejected(self, client, auth_headers, nb_id, source_ids):
        r = client.post(
            f"/api/notebooks/{nb_id}/sources/{source_ids[0]}/links",
            json={"target_id": source_ids[0]},
            headers=auth_headers,
        )
        assert r.status_code == 400

    def test_invalid_link_type(self, client, auth_headers, nb_id, source_ids):
        r = client.post(
            f"/api/notebooks/{nb_id}/sources/{source_ids[0]}/links",
            json={"target_id": source_ids[1], "link_type": "invalid"},
            headers=auth_headers,
        )
        assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# Analytics
# ═══════════════════════════════════════════════════════════════════════════

class TestAnalytics:
    def test_record_event(self, client, auth_headers):
        r = client.post("/api/analytics/event", json={
            "event_type": "query",
            "route": "/api/query",
            "latency_ms": 123.4,
        }, headers=auth_headers)
        assert r.status_code == 200
        assert "id" in r.json()

    def test_record_event_invalid_type(self, client, auth_headers):
        r = client.post("/api/analytics/event", json={
            "event_type": "invalid_type",
        }, headers=auth_headers)
        assert r.status_code == 422

    def test_dashboard_empty(self, client, auth_headers):
        r = client.get("/api/analytics/dashboard", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["total_events"] == 0
        assert data["period_days"] == 30

    def test_dashboard_with_events(self, client, auth_headers):
        client.post("/api/analytics/event", json={"event_type": "query"}, headers=auth_headers)
        client.post("/api/analytics/event", json={"event_type": "upload"}, headers=auth_headers)

        r = client.get("/api/analytics/dashboard", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["total_events"] == 2

    def test_dashboard_unauthenticated(self, client):
        r = client.get("/api/analytics/dashboard")
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# Planning — Deadlines & Reminders
# ═══════════════════════════════════════════════════════════════════════════

class TestDeadlines:
    def test_list_deadlines_empty(self, client, auth_headers):
        r = client.get("/api/deadlines", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["deadlines"] == []

    def test_create_deadline(self, client, auth_headers):
        r = client.post("/api/deadlines", json={
            "title": "Submit paper",
            "due_date": "2026-04-01T00:00:00Z",
            "priority": "high",
        }, headers=auth_headers)
        assert r.status_code == 201
        assert r.json()["title"] == "Submit paper"

    def test_update_deadline(self, client, auth_headers):
        r = client.post("/api/deadlines", json={
            "title": "Draft",
            "due_date": "2026-05-01T00:00:00Z",
        }, headers=auth_headers)
        dl_id = r.json()["id"]
        r2 = client.patch(f"/api/deadlines/{dl_id}", json={
            "status": "completed",
            "priority": "low",
        }, headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["updated"] is True

    def test_update_nonexistent_deadline(self, client, auth_headers):
        r = client.patch("/api/deadlines/no-id", json={"title": "x"}, headers=auth_headers)
        assert r.status_code == 404

    def test_delete_deadline(self, client, auth_headers):
        r = client.post("/api/deadlines", json={
            "title": "Temp",
            "due_date": "2026-06-01T00:00:00Z",
        }, headers=auth_headers)
        dl_id = r.json()["id"]
        r2 = client.delete(f"/api/deadlines/{dl_id}", headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["deleted"] == dl_id

    def test_create_deadline_invalid_priority(self, client, auth_headers):
        r = client.post("/api/deadlines", json={
            "title": "Bad",
            "due_date": "2026-04-01",
            "priority": "critical",
        }, headers=auth_headers)
        assert r.status_code == 422


class TestReminders:
    def test_create_reminder(self, client, auth_headers):
        dl = client.post("/api/deadlines", json={
            "title": "Review",
            "due_date": "2026-04-15T00:00:00Z",
        }, headers=auth_headers)
        dl_id = dl.json()["id"]

        r = client.post("/api/reminders", json={
            "deadline_id": dl_id,
            "remind_at": "2026-04-14T09:00:00Z",
        }, headers=auth_headers)
        assert r.status_code == 201
        assert r.json()["deadline_id"] == dl_id

    def test_create_reminder_nonexistent_deadline(self, client, auth_headers):
        r = client.post("/api/reminders", json={
            "deadline_id": "fake-id",
            "remind_at": "2026-04-14T09:00:00Z",
        }, headers=auth_headers)
        assert r.status_code == 404

    def test_pending_reminders(self, client, auth_headers):
        r = client.get("/api/reminders/pending", headers=auth_headers)
        assert r.status_code == 200
        assert "reminders" in r.json()


class TestCalendar:
    def test_list_connections_empty(self, client, auth_headers):
        r = client.get("/api/calendar/connections", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["connections"] == []

    def test_connect_and_disconnect(self, client, auth_headers):
        r = client.post("/api/calendar/connect", json={
            "provider": "google",
            "access_token": "test-token-abc",
        }, headers=auth_headers)
        assert r.status_code == 201
        conn_id = r.json()["id"]

        r2 = client.get("/api/calendar/connections", headers=auth_headers)
        assert len(r2.json()["connections"]) == 1

        r3 = client.delete(f"/api/calendar/disconnect/{conn_id}", headers=auth_headers)
        assert r3.status_code == 200

    def test_sync_nonexistent(self, client, auth_headers):
        r = client.post("/api/calendar/sync/fake-id", headers=auth_headers)
        assert r.status_code == 404

    def test_connect_invalid_provider(self, client, auth_headers):
        r = client.post("/api/calendar/connect", json={
            "provider": "apple",
            "access_token": "x",
        }, headers=auth_headers)
        assert r.status_code == 422
