"""Tests for the auth-hardening features added in commit 6.

Covers:
  * Session tokens are stored hashed (never as plaintext).
  * ``logout-all`` revokes every session belonging to the caller.
  * Repeated wrong passwords lock the account, and a *correct* password is
    rejected while the lock is in effect.
  * The failed-login counter resets after a successful login.
"""

from __future__ import annotations

import pytest
from notebook import db as db_mod
from notebook.db import get_conn, init_db


@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    init_db()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from web.app import app
    return TestClient(app)


def _signup(client, email: str = "alice@example.com", password: str = "securepassword123") -> str:
    response = client.post("/api/auth/signup", json={
        "email": email, "password": password, "name": "Alice",
    })
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _login(client, email: str, password: str):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_session_tokens_are_hashed_at_rest(client):
    """The DB must never store the raw bearer token."""
    token = _signup(client)
    with get_conn() as conn:
        rows = conn.execute("SELECT token FROM sessions").fetchall()
    assert rows, "expected at least one session"
    stored = rows[0]["token"]
    # Stored value must be a SHA-256 hex digest, not the raw token.
    assert stored != token
    assert len(stored) == 64
    assert all(char in "0123456789abcdef" for char in stored)


def test_logout_all_revokes_every_session(client):
    """``/logout-all`` must invalidate sessions across multiple devices."""
    _signup(client, "bob@example.com", "securepassword123")
    # Two more logins → two more sessions.
    token_a = _login(client, "bob@example.com", "securepassword123").json()["token"]
    token_b = _login(client, "bob@example.com", "securepassword123").json()["token"]

    # Both work right now.
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_a}"}).status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_b}"}).status_code == 200

    revoke = client.post("/api/auth/logout-all", headers={"Authorization": f"Bearer {token_a}"})
    assert revoke.status_code == 200
    assert revoke.json()["sessions_revoked"] >= 2

    # Both tokens now rejected.
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_a}"}).status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_b}"}).status_code == 401


def test_failed_logins_lock_account(client, monkeypatch):
    """After the threshold of failures, even the right password is denied."""
    from routes import auth as auth_mod
    monkeypatch.setattr(auth_mod, "_LOCKOUT_THRESHOLD", 3)

    _signup(client, "carol@example.com", "securepassword123")

    for _ in range(3):
        bad = _login(client, "carol@example.com", "wrongpassword")
        assert bad.status_code == 401

    locked = _login(client, "carol@example.com", "securepassword123")
    assert locked.status_code == 429
    assert "locked" in locked.text.lower()


def test_successful_login_resets_failure_counter(client, monkeypatch):
    """A correct password before the lockout threshold clears the counter."""
    from routes import auth as auth_mod
    monkeypatch.setattr(auth_mod, "_LOCKOUT_THRESHOLD", 5)

    _signup(client, "dave@example.com", "securepassword123")

    for _ in range(3):
        assert _login(client, "dave@example.com", "wrongpassword").status_code == 401

    # Correct login succeeds and clears the counter.
    assert _login(client, "dave@example.com", "securepassword123").status_code == 200

    with get_conn() as conn:
        row = conn.execute(
            "SELECT failed_login_count, locked_until FROM users WHERE email=?",
            ("dave@example.com",),
        ).fetchone()
    assert row["failed_login_count"] == 0
    assert row["locked_until"] is None


def test_logout_uses_hashed_lookup(client):
    """``/logout`` must remove the row whose hashed token matches."""
    token = _signup(client)
    response = client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    # Token no longer accepted.
    assert client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"},
    ).status_code == 401
