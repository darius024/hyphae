"""Integration tests for authentication endpoints."""

import pytest

from notebook import db as db_mod
from notebook.db import init_db, get_conn


@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch):
    temp_db = tmp_path / "test.db"
    monkeypatch.setattr(db_mod, "DB_PATH", temp_db)
    init_db()


@pytest.fixture()
def client():
    from web.app import app
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture()
def registered_user(client):
    """Register a user and return (email, password, token)."""
    email = "test@example.com"
    password = "securepassword123"
    r = client.post("/api/auth/signup", json={
        "email": email,
        "password": password,
        "name": "Test User",
    })
    assert r.status_code == 200
    return email, password, r.json()["token"]


class TestSignup:
    def test_successful_signup(self, client):
        r = client.post("/api/auth/signup", json={
            "email": "new@example.com",
            "password": "password123",
            "name": "New User",
        })
        assert r.status_code == 200
        data = r.json()
        assert "token" in data
        assert data["user"]["email"] == "new@example.com"
        assert data["user"]["name"] == "New User"

    def test_short_password_rejected(self, client):
        r = client.post("/api/auth/signup", json={
            "email": "short@example.com",
            "password": "short",
            "name": "Short Pass",
        })
        assert r.status_code == 422

    def test_duplicate_email_rejected(self, client, registered_user):
        email, password, _ = registered_user
        r = client.post("/api/auth/signup", json={
            "email": email,
            "password": "anotherpassword",
            "name": "Duplicate",
        })
        assert r.status_code == 400

    def test_missing_fields_rejected(self, client):
        r = client.post("/api/auth/signup", json={"email": "a@b.com"})
        assert r.status_code == 422


class TestLogin:
    def test_successful_login(self, client, registered_user):
        email, password, _ = registered_user
        r = client.post("/api/auth/login", json={
            "email": email,
            "password": password,
        })
        assert r.status_code == 200
        data = r.json()
        assert "token" in data
        assert data["user"]["email"] == email

    def test_wrong_password(self, client, registered_user):
        email, _, _ = registered_user
        r = client.post("/api/auth/login", json={
            "email": email,
            "password": "wrongpassword",
        })
        assert r.status_code == 401

    def test_nonexistent_email(self, client):
        r = client.post("/api/auth/login", json={
            "email": "nobody@example.com",
            "password": "anything",
        })
        assert r.status_code == 401


class TestLogout:
    def test_logout_invalidates_session(self, client, registered_user):
        _, _, token = registered_user
        r = client.post("/api/auth/logout", headers={
            "Authorization": f"Bearer {token}",
        })
        assert r.status_code == 200

        r2 = client.get("/api/auth/me", headers={
            "Authorization": f"Bearer {token}",
        })
        assert r2.status_code == 401

    def test_logout_without_token_is_ok(self, client):
        r = client.post("/api/auth/logout")
        assert r.status_code == 200


class TestMe:
    def test_returns_user_info(self, client, registered_user):
        _, _, token = registered_user
        r = client.get("/api/auth/me", headers={
            "Authorization": f"Bearer {token}",
        })
        assert r.status_code == 200
        assert r.json()["email"] == "test@example.com"

    def test_unauthenticated_rejected(self, client):
        r = client.get("/api/auth/me")
        assert r.status_code == 401

    def test_invalid_token_rejected(self, client):
        r = client.get("/api/auth/me", headers={
            "Authorization": "Bearer invalid-token-here",
        })
        assert r.status_code == 401
