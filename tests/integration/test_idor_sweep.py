"""Parametrised IDOR sweep across notebook-scoped endpoints.

Each notebook-scoped endpoint must reject access by any user who is
neither the owner nor an org collaborator.  Per-route unit tests cover
the happy path; this sweep exists to catch the failure mode where a
new endpoint is added without the standard ``_check_nb_owner`` /
``can_access_notebook`` call \u2014 a class of bug the audit found three
times across notebooks/notes/tags/collaboration before consolidation.

Conventions:

* ``user_a`` creates the resource.
* ``user_b`` makes the request.
* Acceptable responses are 403 (forbidden) or 404 (not found, used
  by some endpoints to avoid leaking notebook existence).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from notebook import db as db_mod
from notebook.db import init_db


@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    init_db()


@pytest.fixture()
def client(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    from routes import corpus as corpus_mod

    from web.app import app
    corpus_mod.configure(str(corpus), None)
    return TestClient(app)


def _signup(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/signup",
        json={"email": email, "password": "testpassword123", "name": email},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture()
def user_a(client):
    return _signup(client, "alice@example.com")


@pytest.fixture()
def user_b(client):
    return _signup(client, "bob@example.com")


@pytest.fixture()
def alice_notebook(client, user_a) -> str:
    """A notebook owned by user A.  Returns its ID."""
    response = client.post(
        "/api/notebooks",
        json={"name": "Alice's notebook"},
        headers=user_a,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.fixture()
def alice_note(client, user_a, alice_notebook) -> str:
    response = client.post(
        f"/api/notebooks/{alice_notebook}/notes",
        json={"title": "Secret", "content": "Hi"},
        headers=user_a,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.fixture()
def alice_conversation(client, user_a, alice_notebook) -> str:
    response = client.post(
        f"/api/notebooks/{alice_notebook}/conversations",
        json={"title": "Private chat"},
        headers=user_a,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


# ── Endpoint catalogue ─────────────────────────────────────────────────
#
# Each entry is (method, path_template, body_factory).  Path templates
# use the placeholder names defined as keyword args in the lambda.

NotebookCase = tuple[str, str, Callable[..., dict | None]]

# Notebook-only endpoints.  Resource fixtures are not consumed here.
NOTEBOOK_CASES: list[NotebookCase] = [
    ("GET",    "/api/notebooks/{nb}", lambda **_: None),
    ("PATCH",  "/api/notebooks/{nb}", lambda **_: {"name": "hijacked"}),
    ("DELETE", "/api/notebooks/{nb}", lambda **_: None),
    ("GET",    "/api/notebooks/{nb}/sources",       lambda **_: None),
    ("GET",    "/api/notebooks/{nb}/conversations", lambda **_: None),
    ("POST",   "/api/notebooks/{nb}/conversations", lambda **_: {"title": "x"}),
    ("GET",    "/api/notebooks/{nb}/paper",         lambda **_: None),
]

# Note-scoped endpoints (require an existing note).
NOTE_CASES: list[NotebookCase] = [
    ("GET",    "/api/notebooks/{nb}/notes",                 lambda **_: None),
    ("POST",   "/api/notebooks/{nb}/notes",                 lambda **_: {"title": "x", "content": "x"}),
    ("GET",    "/api/notebooks/{nb}/notes/{note}",          lambda **_: None),
    ("PATCH",  "/api/notebooks/{nb}/notes/{note}",          lambda **_: {"title": "x"}),
    ("DELETE", "/api/notebooks/{nb}/notes/{note}",          lambda **_: None),
    ("GET",    "/api/notebooks/{nb}/notes/{note}/versions", lambda **_: None),
]

# Conversation-scoped endpoints (require an existing conversation).
CONVERSATION_CASES: list[NotebookCase] = [
    ("PATCH",  "/api/notebooks/{nb}/conversations/{conv}",          lambda **_: {"title": "x"}),
    ("DELETE", "/api/notebooks/{nb}/conversations/{conv}",          lambda **_: None),
    ("GET",    "/api/notebooks/{nb}/conversations/{conv}/messages", lambda **_: None),
]


def _request(client: TestClient, method: str, url: str, headers: dict, body: dict | None):
    if method == "GET":
        return client.get(url, headers=headers)
    if method == "DELETE":
        return client.delete(url, headers=headers)
    if method == "POST":
        return client.post(url, json=body or {}, headers=headers)
    if method == "PATCH":
        return client.patch(url, json=body or {}, headers=headers)
    if method == "PUT":
        return client.put(url, json=body or {}, headers=headers)
    raise AssertionError(f"unsupported method {method}")


def _idor_id(case: NotebookCase) -> str:
    method, path, _ = case
    return f"{method} {path}"


@pytest.mark.parametrize("case", NOTEBOOK_CASES, ids=_idor_id)
def test_notebook_endpoint_rejects_other_user(client, user_b, alice_notebook, case):
    method, template, body_factory = case
    url = template.format(nb=alice_notebook)
    response = _request(client, method, url, user_b, body_factory())
    assert response.status_code in (403, 404), (
        f"{method} {url} returned {response.status_code} for non-owner; expected 403/404"
    )


@pytest.mark.parametrize("case", NOTE_CASES, ids=_idor_id)
def test_note_endpoint_rejects_other_user(client, user_b, alice_notebook, alice_note, case):
    method, template, body_factory = case
    url = template.format(nb=alice_notebook, note=alice_note)
    response = _request(client, method, url, user_b, body_factory())
    assert response.status_code in (403, 404), (
        f"{method} {url} returned {response.status_code} for non-owner; expected 403/404"
    )


@pytest.mark.parametrize("case", CONVERSATION_CASES, ids=_idor_id)
def test_conversation_endpoint_rejects_other_user(
    client, user_b, alice_notebook, alice_conversation, case,
):
    method, template, body_factory = case
    url = template.format(nb=alice_notebook, conv=alice_conversation)
    response = _request(client, method, url, user_b, body_factory())
    assert response.status_code in (403, 404), (
        f"{method} {url} returned {response.status_code} for non-owner; expected 403/404"
    )


def test_owner_can_access_own_notebook(client, user_a, alice_notebook):
    """Sanity check: the same matrix succeeds for the legitimate owner."""
    response = client.get(f"/api/notebooks/{alice_notebook}", headers=user_a)
    assert response.status_code == 200
