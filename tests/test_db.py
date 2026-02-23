"""Unit tests for the database layer (web/notebook/db.py)."""

import sqlite3
import sys
import os
import pytest

from notebook.db import init_db, get_conn, DB_PATH, _DEMO_NOTEBOOK_ID, _DEMO_CONV_ID


@pytest.fixture(autouse=True)
def _use_temp_db(tmp_path, monkeypatch):
    """Redirect DB_PATH to a temp directory so tests don't touch real data."""
    temp_db = tmp_path / "test_notebook.db"
    from notebook import db as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", temp_db)
    init_db()
    yield temp_db


class TestInitDb:
    def test_creates_tables(self, _use_temp_db):
        with get_conn() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        assert "notebooks" in tables
        assert "sources" in tables
        assert "chunks" in tables
        assert "conversations" in tables
        assert "messages" in tables
        assert "nb_settings" in tables

    def test_creates_fts_table(self, _use_temp_db):
        with get_conn() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        assert "chunks_fts" in tables

    def test_seeds_default_settings(self, _use_temp_db):
        with get_conn() as conn:
            rows = conn.execute("SELECT key, value FROM nb_settings").fetchall()
        settings = {r["key"]: r["value"] for r in rows}
        assert settings["embed_model"] == "all-MiniLM-L6-v2"
        assert settings["retrieval_top_k"] == "6"
        assert settings["chunk_size"] == "400"
        assert settings["chunk_overlap"] == "80"

    def test_idempotent_init(self, _use_temp_db):
        """Calling init_db() twice should not fail or duplicate data."""
        init_db()
        with get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM nb_settings").fetchone()[0]
        assert count == 4


class TestSeedDefaults:
    def test_demo_notebook_created(self, _use_temp_db):
        with get_conn() as conn:
            nb = conn.execute(
                "SELECT * FROM notebooks WHERE id=?", (_DEMO_NOTEBOOK_ID,)
            ).fetchone()
        assert nb is not None
        assert nb["name"] == "Bioelectronics Research"

    def test_demo_conversation_created(self, _use_temp_db):
        with get_conn() as conn:
            conv = conn.execute(
                "SELECT * FROM conversations WHERE id=?", (_DEMO_CONV_ID,)
            ).fetchone()
        assert conv is not None
        assert conv["title"] == "Project overview"
        assert conv["notebook_id"] == _DEMO_NOTEBOOK_ID

    def test_seed_is_idempotent(self, _use_temp_db):
        init_db()
        with get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM notebooks WHERE id=?", (_DEMO_NOTEBOOK_ID,)
            ).fetchone()[0]
        assert count == 1


class TestGetConn:
    def test_returns_row_factory(self, _use_temp_db):
        with get_conn() as conn:
            row = conn.execute("SELECT 1 AS val").fetchone()
        assert row["val"] == 1

    def test_autocommit_on_success(self, _use_temp_db):
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO notebooks (id, name) VALUES ('test-1', 'Test NB')"
            )
        with get_conn() as conn:
            row = conn.execute(
                "SELECT name FROM notebooks WHERE id='test-1'"
            ).fetchone()
        assert row["name"] == "Test NB"

    def test_rollback_on_error(self, _use_temp_db):
        try:
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO notebooks (id, name) VALUES ('test-err', 'Should rollback')"
                )
                raise ValueError("Simulated error")
        except ValueError:
            pass
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM notebooks WHERE id='test-err'"
            ).fetchone()
        assert row is None

    def test_foreign_keys_enforced(self, _use_temp_db):
        with pytest.raises(Exception):
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO sources (id, notebook_id, type, status) "
                    "VALUES ('s1', 'nonexistent-nb', 'txt', 'pending')"
                )


class TestNotebookCrud:
    def test_create_and_read(self, _use_temp_db):
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO notebooks (id, name, description) VALUES (?, ?, ?)",
                ("nb-1", "My Notebook", "A test notebook"),
            )
        with get_conn() as conn:
            nb = conn.execute("SELECT * FROM notebooks WHERE id='nb-1'").fetchone()
        assert nb["name"] == "My Notebook"
        assert nb["description"] == "A test notebook"
        assert nb["allow_cloud"] == 0

    def test_cascade_delete_sources(self, _use_temp_db):
        with get_conn() as conn:
            conn.execute("INSERT INTO notebooks (id, name) VALUES ('nb-del', 'Delete Me')")
            conn.execute(
                "INSERT INTO sources (id, notebook_id, type, status) VALUES ('s1', 'nb-del', 'txt', 'done')"
            )
        with get_conn() as conn:
            conn.execute("DELETE FROM notebooks WHERE id='nb-del'")
        with get_conn() as conn:
            src = conn.execute("SELECT * FROM sources WHERE notebook_id='nb-del'").fetchone()
        assert src is None

    def test_cascade_delete_conversations(self, _use_temp_db):
        with get_conn() as conn:
            conn.execute("INSERT INTO notebooks (id, name) VALUES ('nb-del2', 'Delete 2')")
            conn.execute(
                "INSERT INTO conversations (id, notebook_id, title) VALUES ('c1', 'nb-del2', 'Chat')"
            )
            conn.execute(
                "INSERT INTO messages (id, conversation_id, notebook_id, role, content) "
                "VALUES ('m1', 'c1', 'nb-del2', 'user', 'hello')"
            )
        with get_conn() as conn:
            conn.execute("DELETE FROM notebooks WHERE id='nb-del2'")
        with get_conn() as conn:
            conv = conn.execute("SELECT * FROM conversations WHERE notebook_id='nb-del2'").fetchone()
            msg = conn.execute("SELECT * FROM messages WHERE notebook_id='nb-del2'").fetchone()
        assert conv is None
        assert msg is None
