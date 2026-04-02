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


# ── purge_expired_sessions ────────────────────────────────────────────────

class TestPurgeExpiredSessions:
    """Verify that purge_expired_sessions removes only past-expired rows."""

    def _insert_session(self, expires_at: str) -> str:
        """Insert a user + session pair; return the session id."""
        import uuid
        user_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        token = str(uuid.uuid4())
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO users (id, email, password_hash, name) VALUES (?,?,?,?)",
                (user_id, f"{user_id}@example.com", "hash", "Test"),
            )
            conn.execute(
                "INSERT INTO sessions (id, user_id, token, expires_at) VALUES (?,?,?,?)",
                (session_id, user_id, token, expires_at),
            )
        return session_id

    def test_purges_expired_session(self, _use_temp_db):
        from notebook.db import purge_expired_sessions
        expired_id = self._insert_session("2000-01-01T00:00:00Z")
        deleted = purge_expired_sessions()
        assert deleted == 1
        with get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM sessions WHERE id=?", (expired_id,)
            ).fetchone()
        assert row is None

    def test_keeps_active_session(self, _use_temp_db):
        from notebook.db import purge_expired_sessions
        live_id = self._insert_session("2099-12-31T23:59:59Z")
        deleted = purge_expired_sessions()
        assert deleted == 0
        with get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM sessions WHERE id=?", (live_id,)
            ).fetchone()
        assert row is not None

    def test_mixed_removes_only_expired(self, _use_temp_db):
        from notebook.db import purge_expired_sessions
        expired_id = self._insert_session("2000-01-01T00:00:00Z")
        live_id = self._insert_session("2099-12-31T23:59:59Z")
        deleted = purge_expired_sessions()
        assert deleted == 1
        with get_conn() as conn:
            remaining = {
                r[0]
                for r in conn.execute("SELECT id FROM sessions").fetchall()
            }
        assert expired_id not in remaining
        assert live_id in remaining

    def test_returns_zero_when_nothing_to_purge(self, _use_temp_db):
        from notebook.db import purge_expired_sessions
        assert purge_expired_sessions() == 0


# ── migration idempotency ─────────────────────────────────────────────────

class TestMigrationIdempotency:
    """Running init_db() multiple times must never raise or corrupt the schema."""

    def test_init_db_three_times_is_safe(self, _use_temp_db):
        init_db()
        init_db()  # third call — all migrations use IF NOT EXISTS / try-except

    def test_sensitivity_column_survives_reinit(self, _use_temp_db):
        init_db()
        with get_conn() as conn:
            cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(sources)").fetchall()
            }
        assert "sensitivity" in cols

    def test_org_id_column_survives_reinit(self, _use_temp_db):
        init_db()
        with get_conn() as conn:
            cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(notebooks)").fetchall()
            }
        assert "org_id" in cols

    def test_calendar_events_table_survives_reinit(self, _use_temp_db):
        init_db()
        with get_conn() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "calendar_events" in tables


class TestSafeUpdate:
    """Tests for db.safe_update — the SQL-injection-safe dynamic UPDATE helper."""

    def test_no_op_when_assignments_empty(self, _use_temp_db):
        """Calling safe_update with an empty dict must not execute any SQL."""
        from notebook.db import safe_update
        with get_conn() as conn:
            # Capture the rowcount; empty assignments must leave it at -1 (no-op)
            before = conn.execute(
                "SELECT name FROM notebooks WHERE id=?", (_DEMO_NOTEBOOK_ID,)
            ).fetchone()["name"]
            safe_update(conn, "notebooks", {}, "id", _DEMO_NOTEBOOK_ID)
            after = conn.execute(
                "SELECT name FROM notebooks WHERE id=?", (_DEMO_NOTEBOOK_ID,)
            ).fetchone()["name"]
        assert before == after

    def test_updates_single_field(self, _use_temp_db):
        from notebook.db import safe_update
        with get_conn() as conn:
            safe_update(conn, "notebooks", {"name": "Renamed"}, "id", _DEMO_NOTEBOOK_ID)
            row = conn.execute(
                "SELECT name FROM notebooks WHERE id=?", (_DEMO_NOTEBOOK_ID,)
            ).fetchone()
        assert row["name"] == "Renamed"

    def test_updates_multiple_fields(self, _use_temp_db):
        from notebook.db import safe_update
        with get_conn() as conn:
            safe_update(
                conn, "notebooks",
                {"name": "Multi", "description": "desc text"},
                "id", _DEMO_NOTEBOOK_ID,
            )
            row = conn.execute(
                "SELECT name, description FROM notebooks WHERE id=?",
                (_DEMO_NOTEBOOK_ID,),
            ).fetchone()
        assert row["name"] == "Multi"
        assert row["description"] == "desc text"

    def test_auto_timestamp_updates_updated_at(self, _use_temp_db):
        from notebook.db import safe_update
        with get_conn() as conn:
            safe_update(
                conn, "notebooks", {"name": "Ts Test"}, "id", _DEMO_NOTEBOOK_ID,
                auto_timestamp=True,
            )
            row = conn.execute(
                "SELECT updated_at FROM notebooks WHERE id=?", (_DEMO_NOTEBOOK_ID,)
            ).fetchone()
        assert row["updated_at"] is not None

    def test_no_timestamp_when_disabled(self, _use_temp_db):
        """With auto_timestamp=False the query must not touch updated_at."""
        from notebook.db import safe_update
        with get_conn() as conn:
            ts_before = conn.execute(
                "SELECT updated_at FROM notebooks WHERE id=?", (_DEMO_NOTEBOOK_ID,)
            ).fetchone()["updated_at"]
            safe_update(
                conn, "notebooks", {"name": "No Ts"},
                "id", _DEMO_NOTEBOOK_ID, auto_timestamp=False,
            )
            ts_after = conn.execute(
                "SELECT updated_at FROM notebooks WHERE id=?", (_DEMO_NOTEBOOK_ID,)
            ).fetchone()["updated_at"]
        assert ts_before == ts_after

    def test_rejects_unsafe_table_name(self, _use_temp_db):
        from notebook.db import safe_update
        with get_conn() as conn:
            with pytest.raises(ValueError, match="Unsafe table name"):
                safe_update(conn, "notebooks; DROP TABLE notebooks", {"name": "x"}, "id", "y")

    def test_rejects_unsafe_column_name(self, _use_temp_db):
        from notebook.db import safe_update
        with get_conn() as conn:
            with pytest.raises(ValueError, match="Unsafe column name"):
                safe_update(conn, "notebooks", {"name=1; --": "x"}, "id", "y")

    def test_rejects_unsafe_where_col(self, _use_temp_db):
        from notebook.db import safe_update
        with get_conn() as conn:
            with pytest.raises(ValueError, match="Unsafe WHERE column"):
                safe_update(conn, "notebooks", {"name": "x"}, "id=1 OR 1=1 --", "y")

