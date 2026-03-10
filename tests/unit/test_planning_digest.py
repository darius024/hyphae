"""Unit/integration tests for the GET /api/planning/digest endpoint.

Covers:
  - Empty result when no upcoming deadlines exist
  - Returns only deadlines within the requested window
  - Excludes completed and cancelled deadlines
  - Optional notebook_id filter
  - latest_conversation is populated when a conversation exists
  - latest_conversation is null when no conversation exists
  - ``days`` query-param bounds (min 1, max 90)
  - Unauthenticated request returns 401
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _iso(offset_days: int) -> str:
    """Return an ISO-8601 UTC timestamp offset_days from now."""
    return (datetime.now(timezone.utc) + timedelta(days=offset_days)).isoformat()


# ─── Shared fixture ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch):
    """Redirect DB to a fresh temp file for every test."""
    import notebook.db as db_mod
    from notebook.db import init_db

    temp_db = tmp_path / "test_digest.db"
    monkeypatch.setattr(db_mod, "DB_PATH", temp_db)
    init_db()


@pytest.fixture()
def client_with_user():
    """TestClient + a signed-up user; yields (client, headers, user_id)."""
    from fastapi.testclient import TestClient
    from web.app import app

    with TestClient(app) as client:
        resp = client.post("/api/auth/signup", json={
            "email": f"digest_{uuid.uuid4().hex[:6]}@test.com",
            "password": "securepassword1",
            "name": "Digest Tester",
        })
        assert resp.status_code == 200, resp.text
        token   = resp.json()["token"]
        user_id = resp.json()["user"]["id"]
        headers = {"Authorization": f"Bearer {token}"}
        yield client, headers, user_id


def _insert_deadline(conn, user_id: str, notebook_id: str | None,
                     due_offset_days: int, status: str = "pending",
                     title: str = "Test deadline") -> str:
    """Insert a deadline and return its id."""
    dl_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO deadlines (id, user_id, notebook_id, title, due_date, status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (dl_id, user_id, notebook_id, title, _iso(due_offset_days), status),
    )
    return dl_id


def _insert_notebook(conn, user_id: str) -> str:
    nb_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO notebooks (id, name, user_id) VALUES (?, ?, ?)",
        (nb_id, "Test NB", user_id),
    )
    return nb_id


def _insert_conversation(conn, nb_id: str, title: str = "Conv 1") -> str:
    conv_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO conversations (id, notebook_id, title) VALUES (?, ?, ?)",
        (conv_id, nb_id, title),
    )
    return conv_id


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestDigestEndpoint:
    def test_unauthenticated_returns_401(self, client_with_user):
        client, _, _ = client_with_user
        resp = client.get("/api/planning/digest")
        assert resp.status_code == 401

    def test_no_deadlines_returns_empty_list(self, client_with_user):
        client, headers, _ = client_with_user
        resp = client.get("/api/planning/digest", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["deadlines"] == []
        assert body["days"] == 7

    def test_days_param_echoed_in_response(self, client_with_user):
        client, headers, _ = client_with_user
        resp = client.get("/api/planning/digest?days=14", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["days"] == 14

    def test_deadline_within_window_is_returned(self, client_with_user):
        client, headers, user_id = client_with_user
        import notebook.db as db_mod
        with db_mod.get_conn() as conn:
            nb_id = _insert_notebook(conn, user_id)
            _insert_deadline(conn, user_id, nb_id, due_offset_days=3, title="Upcoming task")

        resp = client.get("/api/planning/digest?days=7", headers=headers)
        assert resp.status_code == 200
        deadlines = resp.json()["deadlines"]
        assert len(deadlines) == 1
        assert deadlines[0]["title"] == "Upcoming task"

    def test_deadline_outside_window_is_excluded(self, client_with_user):
        client, headers, user_id = client_with_user
        import notebook.db as db_mod
        with db_mod.get_conn() as conn:
            nb_id = _insert_notebook(conn, user_id)
            _insert_deadline(conn, user_id, nb_id, due_offset_days=10)

        resp = client.get("/api/planning/digest?days=7", headers=headers)
        assert resp.json()["deadlines"] == []

    def test_completed_deadline_excluded(self, client_with_user):
        client, headers, user_id = client_with_user
        import notebook.db as db_mod
        with db_mod.get_conn() as conn:
            nb_id = _insert_notebook(conn, user_id)
            _insert_deadline(conn, user_id, nb_id, due_offset_days=2, status="completed")

        resp = client.get("/api/planning/digest?days=7", headers=headers)
        assert resp.json()["deadlines"] == []

    def test_cancelled_deadline_excluded(self, client_with_user):
        client, headers, user_id = client_with_user
        import notebook.db as db_mod
        with db_mod.get_conn() as conn:
            nb_id = _insert_notebook(conn, user_id)
            _insert_deadline(conn, user_id, nb_id, due_offset_days=2, status="cancelled")

        resp = client.get("/api/planning/digest?days=7", headers=headers)
        assert resp.json()["deadlines"] == []

    def test_in_progress_deadline_included(self, client_with_user):
        client, headers, user_id = client_with_user
        import notebook.db as db_mod
        with db_mod.get_conn() as conn:
            nb_id = _insert_notebook(conn, user_id)
            _insert_deadline(conn, user_id, nb_id, due_offset_days=2,
                             status="in_progress", title="Active task")

        resp = client.get("/api/planning/digest?days=7", headers=headers)
        deadlines = resp.json()["deadlines"]
        assert len(deadlines) == 1
        assert deadlines[0]["title"] == "Active task"

    def test_notebook_id_filter(self, client_with_user):
        client, headers, user_id = client_with_user
        import notebook.db as db_mod
        with db_mod.get_conn() as conn:
            nb1 = _insert_notebook(conn, user_id)
            nb2 = _insert_notebook(conn, user_id)
            _insert_deadline(conn, user_id, nb1, due_offset_days=2, title="NB1 task")
            _insert_deadline(conn, user_id, nb2, due_offset_days=2, title="NB2 task")

        resp = client.get(f"/api/planning/digest?notebook_id={nb1}", headers=headers)
        assert resp.status_code == 200
        deadlines = resp.json()["deadlines"]
        assert len(deadlines) == 1
        assert deadlines[0]["title"] == "NB1 task"

    def test_latest_conversation_populated(self, client_with_user):
        client, headers, user_id = client_with_user
        import notebook.db as db_mod
        with db_mod.get_conn() as conn:
            nb_id = _insert_notebook(conn, user_id)
            _insert_deadline(conn, user_id, nb_id, due_offset_days=2)
            conv_id = _insert_conversation(conn, nb_id, title="My Conv")

        resp = client.get("/api/planning/digest?days=7", headers=headers)
        dl = resp.json()["deadlines"][0]
        assert dl["latest_conversation"] is not None
        assert dl["latest_conversation"]["id"] == conv_id
        assert dl["latest_conversation"]["title"] == "My Conv"

    def test_latest_conversation_null_when_none(self, client_with_user):
        client, headers, user_id = client_with_user
        import notebook.db as db_mod
        with db_mod.get_conn() as conn:
            nb_id = _insert_notebook(conn, user_id)
            _insert_deadline(conn, user_id, nb_id, due_offset_days=2)
            # deliberately no conversation

        resp = client.get("/api/planning/digest?days=7", headers=headers)
        dl = resp.json()["deadlines"][0]
        assert dl["latest_conversation"] is None

    def test_notebook_name_populated(self, client_with_user):
        client, headers, user_id = client_with_user
        import notebook.db as db_mod
        with db_mod.get_conn() as conn:
            nb_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO notebooks (id, name, user_id) VALUES (?, ?, ?)",
                (nb_id, "Science Lab", user_id),
            )
            _insert_deadline(conn, user_id, nb_id, due_offset_days=2)

        resp = client.get("/api/planning/digest?days=7", headers=headers)
        dl = resp.json()["deadlines"][0]
        assert dl["notebook_name"] == "Science Lab"

    def test_results_ordered_by_due_date(self, client_with_user):
        client, headers, user_id = client_with_user
        import notebook.db as db_mod
        with db_mod.get_conn() as conn:
            nb_id = _insert_notebook(conn, user_id)
            _insert_deadline(conn, user_id, nb_id, due_offset_days=5, title="Far")
            _insert_deadline(conn, user_id, nb_id, due_offset_days=2, title="Near")

        resp = client.get("/api/planning/digest?days=7", headers=headers)
        titles = [d["title"] for d in resp.json()["deadlines"]]
        assert titles == ["Near", "Far"]

    def test_days_below_minimum_rejected(self, client_with_user):
        client, headers, _ = client_with_user
        resp = client.get("/api/planning/digest?days=0", headers=headers)
        assert resp.status_code == 422

    def test_days_above_maximum_rejected(self, client_with_user):
        client, headers, _ = client_with_user
        resp = client.get("/api/planning/digest?days=91", headers=headers)
        assert resp.status_code == 422
