"""Integration tests for notes (with version history) and collaboration endpoints."""

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

    from web.app import app
    from routes import corpus as corpus_mod
    corpus_mod.configure(str(corpus), None)

    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture()
def auth_headers(client):
    r = client.post("/api/auth/signup", json={
        "email": "notes@example.com",
        "password": "testpassword123",
        "name": "Notes Tester",
    })
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture()
def second_auth(client):
    """A second user for collaboration tests."""
    r = client.post("/api/auth/signup", json={
        "email": "collab@example.com",
        "password": "testpassword123",
        "name": "Collab User",
    })
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture()
def nb_id(client, auth_headers):
    r = client.post("/api/notebooks", json={"name": "Notes NB"}, headers=auth_headers)
    return r.json()["id"]


# ═══════════════════════════════════════════════════════════════════════════
# Notes CRUD
# ═══════════════════════════════════════════════════════════════════════════

class TestNoteCrud:
    def test_list_notes_empty(self, client, auth_headers, nb_id):
        r = client.get(f"/api/notebooks/{nb_id}/notes", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["notes"] == []

    def test_create_note(self, client, auth_headers, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/notes", json={
            "title": "My Note",
            "content": "Hello world",
        }, headers=auth_headers)
        assert r.status_code == 201
        assert r.json()["title"] == "My Note"
        assert "id" in r.json()

    def test_get_note(self, client, auth_headers, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/notes", json={
            "title": "Read Me", "content": "Body text",
        }, headers=auth_headers)
        note_id = r.json()["id"]

        r2 = client.get(f"/api/notebooks/{nb_id}/notes/{note_id}", headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["content"] == "Body text"

    def test_get_nonexistent_note(self, client, auth_headers, nb_id):
        r = client.get(f"/api/notebooks/{nb_id}/notes/no-such-id", headers=auth_headers)
        assert r.status_code == 404

    def test_update_note(self, client, auth_headers, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/notes", json={
            "title": "Original", "content": "v1",
        }, headers=auth_headers)
        note_id = r.json()["id"]

        r2 = client.patch(f"/api/notebooks/{nb_id}/notes/{note_id}", json={
            "title": "Updated", "content": "v2",
        }, headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["updated"] is True

    def test_update_nonexistent_note(self, client, auth_headers, nb_id):
        r = client.patch(f"/api/notebooks/{nb_id}/notes/fake", json={"title": "x"}, headers=auth_headers)
        assert r.status_code == 404

    def test_delete_note(self, client, auth_headers, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/notes", json={"title": "Temp"}, headers=auth_headers)
        note_id = r.json()["id"]
        r2 = client.delete(f"/api/notebooks/{nb_id}/notes/{note_id}", headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["deleted"] == note_id

    def test_list_notes_unauthenticated(self, client, nb_id):
        r = client.get(f"/api/notebooks/{nb_id}/notes")
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# Version history
# ═══════════════════════════════════════════════════════════════════════════

class TestVersionHistory:
    def test_initial_version_created(self, client, auth_headers, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/notes", json={
            "title": "Versioned", "content": "first draft",
        }, headers=auth_headers)
        note_id = r.json()["id"]

        r2 = client.get(f"/api/notebooks/{nb_id}/notes/{note_id}/versions", headers=auth_headers)
        assert r2.status_code == 200
        versions = r2.json()["versions"]
        assert len(versions) == 1
        assert versions[0]["version_num"] == 1

    def test_update_creates_new_version(self, client, auth_headers, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/notes", json={
            "title": "V", "content": "v1",
        }, headers=auth_headers)
        note_id = r.json()["id"]

        client.patch(f"/api/notebooks/{nb_id}/notes/{note_id}", json={"content": "v2"}, headers=auth_headers)
        client.patch(f"/api/notebooks/{nb_id}/notes/{note_id}", json={"content": "v3"}, headers=auth_headers)

        r2 = client.get(f"/api/notebooks/{nb_id}/notes/{note_id}/versions", headers=auth_headers)
        assert len(r2.json()["versions"]) == 3

    def test_get_specific_version(self, client, auth_headers, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/notes", json={
            "title": "V", "content": "original",
        }, headers=auth_headers)
        note_id = r.json()["id"]

        r2 = client.get(f"/api/notebooks/{nb_id}/notes/{note_id}/versions/1", headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["content"] == "original"

    def test_get_nonexistent_version(self, client, auth_headers, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/notes", json={"title": "V"}, headers=auth_headers)
        note_id = r.json()["id"]
        r2 = client.get(f"/api/notebooks/{nb_id}/notes/{note_id}/versions/99", headers=auth_headers)
        assert r2.status_code == 404

    def test_restore_version(self, client, auth_headers, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/notes", json={
            "title": "V", "content": "original",
        }, headers=auth_headers)
        note_id = r.json()["id"]

        client.patch(f"/api/notebooks/{nb_id}/notes/{note_id}", json={"content": "changed"}, headers=auth_headers)

        r2 = client.post(f"/api/notebooks/{nb_id}/notes/{note_id}/restore/1", headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["restored"] is True
        assert r2.json()["new_version"] == 3

        r3 = client.get(f"/api/notebooks/{nb_id}/notes/{note_id}", headers=auth_headers)
        assert r3.json()["content"] == "original"

    def test_restore_nonexistent_version(self, client, auth_headers, nb_id):
        r = client.post(f"/api/notebooks/{nb_id}/notes", json={"title": "V"}, headers=auth_headers)
        note_id = r.json()["id"]
        r2 = client.post(f"/api/notebooks/{nb_id}/notes/{note_id}/restore/99", headers=auth_headers)
        assert r2.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# Writing sessions
# ═══════════════════════════════════════════════════════════════════════════

class TestWritingSessions:
    def test_writing_assist_no_gemini(self, client, auth_headers):
        r = client.post("/api/writing/assist", json={
            "content": "Some text",
            "action": "grammar",
        }, headers=auth_headers)
        assert r.status_code == 503

    def test_writing_assist_invalid_action(self, client, auth_headers):
        r = client.post("/api/writing/assist", json={
            "content": "Some text",
            "action": "translate",
        }, headers=auth_headers)
        assert r.status_code == 422

    def test_save_and_get_session(self, client, auth_headers):
        r = client.post("/api/writing/session", headers=auth_headers)
        assert r.status_code == 200
        session_id = r.json()["id"]

        r2 = client.get(f"/api/writing/session/{session_id}", headers=auth_headers)
        assert r2.status_code == 200

    def test_get_nonexistent_session(self, client, auth_headers):
        r = client.get("/api/writing/session/fake-id", headers=auth_headers)
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# Organizations
# ═══════════════════════════════════════════════════════════════════════════

class TestOrganizations:
    def test_list_orgs_empty(self, client, auth_headers):
        r = client.get("/api/organizations", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["organizations"] == []

    def test_create_org(self, client, auth_headers):
        r = client.post("/api/organizations", json={
            "name": "Test Lab",
            "slug": "test-lab",
            "description": "A test organization",
        }, headers=auth_headers)
        assert r.status_code == 201
        assert r.json()["slug"] == "test-lab"

    def test_create_duplicate_slug(self, client, auth_headers):
        client.post("/api/organizations", json={"name": "A", "slug": "dup-slug"}, headers=auth_headers)
        r = client.post("/api/organizations", json={"name": "B", "slug": "dup-slug"}, headers=auth_headers)
        assert r.status_code == 400

    def test_get_org(self, client, auth_headers):
        r = client.post("/api/organizations", json={"name": "Lab", "slug": "lab"}, headers=auth_headers)
        org_id = r.json()["id"]

        r2 = client.get(f"/api/organizations/{org_id}", headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["name"] == "Lab"
        assert r2.json()["user_role"] == "owner"
        assert len(r2.json()["members"]) == 1

    def test_get_nonexistent_org(self, client, auth_headers):
        r = client.get("/api/organizations/fake-id", headers=auth_headers)
        assert r.status_code == 404

    def test_update_org(self, client, auth_headers):
        r = client.post("/api/organizations", json={"name": "Old", "slug": "old-org"}, headers=auth_headers)
        org_id = r.json()["id"]

        r2 = client.patch(f"/api/organizations/{org_id}", json={"name": "New Name"}, headers=auth_headers)
        assert r2.status_code == 200

    def test_delete_org(self, client, auth_headers):
        r = client.post("/api/organizations", json={"name": "Del", "slug": "del-org"}, headers=auth_headers)
        org_id = r.json()["id"]
        r2 = client.delete(f"/api/organizations/{org_id}", headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["deleted"] == org_id

    def test_delete_org_non_owner(self, client, auth_headers, second_auth):
        r = client.post("/api/organizations", json={"name": "X", "slug": "x-org"}, headers=auth_headers)
        org_id = r.json()["id"]
        r2 = client.delete(f"/api/organizations/{org_id}", headers=second_auth)
        assert r2.status_code == 403

    def test_invalid_slug(self, client, auth_headers):
        r = client.post("/api/organizations", json={
            "name": "Bad", "slug": "HAS SPACES",
        }, headers=auth_headers)
        assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# Org members & invites
# ═══════════════════════════════════════════════════════════════════════════

class TestOrgMembers:
    @pytest.fixture()
    def org_id(self, client, auth_headers):
        r = client.post("/api/organizations", json={"name": "Team", "slug": "team"}, headers=auth_headers)
        return r.json()["id"]

    def test_list_members(self, client, auth_headers, org_id):
        r = client.get(f"/api/organizations/{org_id}/members", headers=auth_headers)
        assert r.status_code == 200
        assert len(r.json()["members"]) == 1

    def test_invite_user(self, client, auth_headers, org_id):
        r = client.post(f"/api/organizations/{org_id}/invite", json={
            "email": "invite@example.com", "role": "member",
        }, headers=auth_headers)
        assert r.status_code == 201
        assert "token" in r.json()

    def test_invite_duplicate(self, client, auth_headers, org_id):
        client.post(f"/api/organizations/{org_id}/invite", json={
            "email": "dup@example.com",
        }, headers=auth_headers)
        r = client.post(f"/api/organizations/{org_id}/invite", json={
            "email": "dup@example.com",
        }, headers=auth_headers)
        assert r.status_code == 400

    def test_accept_invite(self, client, auth_headers, second_auth, org_id):
        r = client.post(f"/api/organizations/{org_id}/invite", json={
            "email": "collab@example.com", "role": "member",
        }, headers=auth_headers)
        token = r.json()["token"]

        r2 = client.post(f"/api/organizations/accept-invite/{token}", headers=second_auth)
        assert r2.status_code == 200
        assert r2.json()["joined"] == org_id

    def test_accept_invite_wrong_email(self, client, auth_headers, second_auth, org_id):
        r = client.post(f"/api/organizations/{org_id}/invite", json={
            "email": "other@example.com",
        }, headers=auth_headers)
        token = r.json()["token"]

        r2 = client.post(f"/api/organizations/accept-invite/{token}", headers=second_auth)
        assert r2.status_code == 400

    def test_cannot_remove_owner(self, client, auth_headers, org_id):
        me = client.get("/api/auth/me", headers=auth_headers).json()
        r = client.delete(f"/api/organizations/{org_id}/members/{me['id']}", headers=auth_headers)
        assert r.status_code == 400

    def test_org_notebooks(self, client, auth_headers, org_id, nb_id):
        r = client.get(f"/api/organizations/{org_id}/notebooks", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["notebooks"] == []

        client.post(f"/api/organizations/{org_id}/notebooks/{nb_id}", headers=auth_headers)

        r2 = client.get(f"/api/organizations/{org_id}/notebooks", headers=auth_headers)
        assert len(r2.json()["notebooks"]) == 1


# ═══════════════════════════════════════════════════════════════════════════
# Comments
# ═══════════════════════════════════════════════════════════════════════════

class TestComments:
    def test_list_comments_empty(self, client, auth_headers, nb_id):
        r = client.get(f"/api/comments?notebook_id={nb_id}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["comments"] == []

    def test_create_comment(self, client, auth_headers, nb_id):
        r = client.post("/api/comments", json={
            "content": "Great work!",
            "notebook_id": nb_id,
        }, headers=auth_headers)
        assert r.status_code == 201
        assert "id" in r.json()

    def test_create_and_list_comments(self, client, auth_headers, nb_id):
        client.post("/api/comments", json={"content": "Comment 1", "notebook_id": nb_id}, headers=auth_headers)
        client.post("/api/comments", json={"content": "Comment 2", "notebook_id": nb_id}, headers=auth_headers)

        r = client.get(f"/api/comments?notebook_id={nb_id}", headers=auth_headers)
        assert len(r.json()["comments"]) == 2

    def test_reply_to_comment(self, client, auth_headers, nb_id):
        r = client.post("/api/comments", json={
            "content": "Parent", "notebook_id": nb_id,
        }, headers=auth_headers)
        parent_id = r.json()["id"]

        r2 = client.post("/api/comments", json={
            "content": "Reply", "parent_id": parent_id,
        }, headers=auth_headers)
        assert r2.status_code == 201

        r3 = client.get(f"/api/comments/{parent_id}/replies", headers=auth_headers)
        assert len(r3.json()["replies"]) == 1
        assert r3.json()["replies"][0]["content"] == "Reply"

    def test_update_comment(self, client, auth_headers, nb_id):
        r = client.post("/api/comments", json={"content": "Old"}, headers=auth_headers)
        cid = r.json()["id"]

        r2 = client.patch(f"/api/comments/{cid}", json={"content": "New"}, headers=auth_headers)
        assert r2.status_code == 200

    def test_update_comment_not_author(self, client, auth_headers, second_auth):
        r = client.post("/api/comments", json={"content": "Mine"}, headers=auth_headers)
        cid = r.json()["id"]

        r2 = client.patch(f"/api/comments/{cid}", json={"content": "Hacked"}, headers=second_auth)
        assert r2.status_code == 403

    def test_resolve_comment(self, client, auth_headers):
        r = client.post("/api/comments", json={"content": "Issue"}, headers=auth_headers)
        cid = r.json()["id"]

        r2 = client.patch(f"/api/comments/{cid}", json={"resolved": True}, headers=auth_headers)
        assert r2.status_code == 200

    def test_delete_comment(self, client, auth_headers):
        r = client.post("/api/comments", json={"content": "Del"}, headers=auth_headers)
        cid = r.json()["id"]
        r2 = client.delete(f"/api/comments/{cid}", headers=auth_headers)
        assert r2.status_code == 200

    def test_delete_comment_not_author(self, client, auth_headers, second_auth):
        r = client.post("/api/comments", json={"content": "Mine"}, headers=auth_headers)
        cid = r.json()["id"]
        r2 = client.delete(f"/api/comments/{cid}", headers=second_auth)
        assert r2.status_code == 403

    def test_delete_nonexistent_comment(self, client, auth_headers):
        r = client.delete("/api/comments/fake-id", headers=auth_headers)
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# Activity feed
# ═══════════════════════════════════════════════════════════════════════════

class TestActivityFeed:
    def test_activity_feed_empty(self, client, auth_headers):
        r = client.get("/api/activity", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["activities"] == []

    def test_activity_logged_on_comment(self, client, auth_headers, nb_id):
        client.post("/api/comments", json={
            "content": "Test", "notebook_id": nb_id,
        }, headers=auth_headers)

        r = client.get(f"/api/activity?notebook_id={nb_id}", headers=auth_headers)
        assert r.status_code == 200
        assert len(r.json()["activities"]) >= 1

    def test_activity_unauthenticated(self, client):
        r = client.get("/api/activity")
        assert r.status_code == 401
