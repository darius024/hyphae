"""Per-user isolation tests for the Code IDE routes.

These tests prove that one authenticated user cannot read, mutate, or even
discover another user's cloned repositories.  They also exercise the new
SQLite-backed ``code_repos`` table that replaces the previous global
``_active_repo`` variable + shared ``.code_state.json`` file.
"""

from pathlib import Path

import pytest
from notebook import db as db_mod
from notebook.db import init_db


@pytest.fixture(autouse=True)
def _temp_db_and_workspace(tmp_path, monkeypatch):
    """Isolate DB *and* code workspace for every test.

    ``WORKSPACE_DIR`` lives on the module — repointing it to ``tmp_path``
    keeps real clones from leaking into the developer's working tree.
    """
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    init_db()

    from routes import code as code_routes
    workspace = tmp_path / "code_workspace"
    workspace.mkdir()
    monkeypatch.setattr(code_routes, "WORKSPACE_DIR", workspace)
    # Reset per-user lock cache so leftover locks from a previous test
    # cannot survive into the next one.
    monkeypatch.setattr(code_routes, "_user_locks", {})


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from web.app import app
    return TestClient(app)


def _signup(client, email: str) -> tuple[str, str]:
    """Sign up a user and return (token, user_id)."""
    response = client.post("/api/auth/signup", json={
        "email": email,
        "password": "securepassword123",
        "name": email.split("@")[0],
    })
    assert response.status_code == 200, response.text
    body = response.json()
    return body["token"], body["user"]["id"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _seed_repo(workspace: Path, user_id: str, name: str) -> Path:
    """Create a fake "cloned" repo on disk for *user_id*."""
    repo = workspace / user_id / name
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("hello")
    return repo


def _record_repo(user_id: str, url: str, path: Path) -> None:
    """Insert a row into ``code_repos`` as if a clone had succeeded."""
    import uuid

    from notebook.db import get_conn
    from routes.code import _repo_name_from_url
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO code_repos (id, user_id, url, path, name, is_active)
               VALUES (?, ?, ?, ?, ?, 1)""",
            (str(uuid.uuid4()), user_id, url, str(path), _repo_name_from_url(url)),
        )


def test_repos_listing_is_per_user(client, tmp_path):
    """Alice's ``/api/code/repos`` must not show Bob's clones."""
    workspace = tmp_path / "code_workspace"
    alice_token, alice_id = _signup(client, "alice@example.com")
    bob_token, bob_id = _signup(client, "bob@example.com")

    alice_repo = _seed_repo(workspace, alice_id, "alice_proj")
    bob_repo = _seed_repo(workspace, bob_id, "bob_proj")
    _record_repo(alice_id, "https://example.com/alice/alice_proj.git", alice_repo)
    _record_repo(bob_id, "https://example.com/bob/bob_proj.git", bob_repo)

    alice_view = client.get("/api/code/repos", headers=_auth(alice_token)).json()
    bob_view = client.get("/api/code/repos", headers=_auth(bob_token)).json()

    assert {r["name"] for r in alice_view["repos"]} == {"alice/alice_proj"}
    assert {r["name"] for r in bob_view["repos"]} == {"bob/bob_proj"}
    assert alice_view["active"] != bob_view["active"]


def test_user_cannot_connect_to_another_users_path(client, tmp_path):
    """A 403 must be returned when trying to ``/connect`` outside own dir."""
    workspace = tmp_path / "code_workspace"
    alice_token, _alice_id = _signup(client, "alice@example.com")
    _bob_token, bob_id = _signup(client, "bob@example.com")

    bob_repo = _seed_repo(workspace, bob_id, "bob_proj")

    response = client.post(
        "/api/code/connect",
        headers=_auth(alice_token),
        json={"path": str(bob_repo), "url": "https://example.com/bob/bob_proj.git"},
    )
    assert response.status_code == 403


def test_user_cannot_delete_another_users_repo(client, tmp_path):
    """``/delete-repo`` must refuse paths outside the caller's workspace."""
    workspace = tmp_path / "code_workspace"
    alice_token, _alice_id = _signup(client, "alice@example.com")
    _bob_token, bob_id = _signup(client, "bob@example.com")

    bob_repo = _seed_repo(workspace, bob_id, "bob_proj")
    _record_repo(bob_id, "https://example.com/bob/bob_proj.git", bob_repo)

    response = client.post(
        "/api/code/delete-repo",
        headers=_auth(alice_token),
        json={"path": str(bob_repo)},
    )
    assert response.status_code == 403
    # Bob's repo and DB row must still be present.
    assert bob_repo.exists()


def test_disconnect_only_clears_caller_active_flag(client, tmp_path):
    """Alice disconnecting must not clear Bob's active repo."""
    workspace = tmp_path / "code_workspace"
    alice_token, alice_id = _signup(client, "alice@example.com")
    _bob_token, bob_id = _signup(client, "bob@example.com")

    alice_repo = _seed_repo(workspace, alice_id, "alice_proj")
    bob_repo = _seed_repo(workspace, bob_id, "bob_proj")
    _record_repo(alice_id, "https://example.com/alice/alice_proj.git", alice_repo)
    _record_repo(bob_id, "https://example.com/bob/bob_proj.git", bob_repo)

    response = client.post("/api/code/disconnect", headers=_auth(alice_token))
    assert response.status_code == 200

    from notebook.db import get_conn
    with get_conn() as conn:
        bob_active = conn.execute(
            "SELECT is_active FROM code_repos WHERE user_id=?",
            (bob_id,),
        ).fetchone()
    assert bob_active["is_active"] == 1


def test_clone_url_validation_rejects_internal_hosts(client):
    """SSRF defence: clone must reject loopback/RFC-1918 URLs."""
    token, _ = _signup(client, "alice@example.com")
    for url in (
        "https://localhost/repo.git",
        "https://127.0.0.1/repo.git",
        "https://10.0.0.5/repo.git",
        "https://192.168.1.1/repo.git",
        "http://example.com/repo.git",  # non-HTTPS
        "",
    ):
        response = client.post("/api/code/clone", headers=_auth(token), json={"url": url})
        assert response.status_code in (400, 422), (url, response.status_code, response.text)


def test_unauthenticated_requests_rejected(client):
    """All code/git routes require a valid bearer token."""
    for route in ("/api/code/repos", "/api/code/tree", "/api/git/status"):
        assert client.get(route).status_code == 401
