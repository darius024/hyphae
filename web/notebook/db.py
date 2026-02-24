"""
SQLite database — Hyphae Notebook layer.

Tables: notebooks, sources, chunks, conversations, messages, settings
FTS5 virtual table chunks_fts for BM25 full-text search.
"""

import sqlite3
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

log = logging.getLogger(__name__)

# DB file lives in web/ (one level up from this module)
DB_PATH = Path(__file__).parents[1] / "notebook.db"

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS notebooks (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    allow_cloud INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS sources (
    id          TEXT PRIMARY KEY,
    notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    title       TEXT,
    type        TEXT NOT NULL,
    filename    TEXT,
    url         TEXT,
    page_count  INTEGER,
    status      TEXT NOT NULL DEFAULT 'pending',
    error       TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS chunks (
    id          TEXT PRIMARY KEY,
    notebook_id TEXT NOT NULL,
    source_id   TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    page_number INTEGER,
    raw_text    TEXT NOT NULL,
    clean_text  TEXT NOT NULL,
    token_count INTEGER,
    faiss_id    INTEGER,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_chunks_nb     ON chunks(notebook_id);
CREATE INDEX IF NOT EXISTS idx_chunks_src    ON chunks(source_id);
CREATE INDEX IF NOT EXISTS idx_chunks_faiss  ON chunks(notebook_id, faiss_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    clean_text,
    chunk_id    UNINDEXED,
    notebook_id UNINDEXED,
    content='chunks',
    content_rowid='rowid'
);

CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    title       TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    notebook_id     TEXT NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    citations       TEXT,
    source          TEXT,
    latency_ms      REAL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS nb_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

INSERT OR IGNORE INTO nb_settings(key, value) VALUES
    ('embed_model',         'all-MiniLM-L6-v2'),
    ('retrieval_top_k',     '6'),
    ('chunk_size',          '400'),
    ('chunk_overlap',       '80');

-- ── Users & Authentication ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name          TEXT NOT NULL,
    avatar_url    TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- ── Sessions (optional: for session-based auth instead of JWT) ──────────

CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token      TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);
CREATE INDEX IF NOT EXISTS idx_sessions_user  ON sessions(user_id);
"""

_FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, clean_text, chunk_id, notebook_id)
    VALUES (new.rowid, new.clean_text, new.id, new.notebook_id);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, clean_text, chunk_id, notebook_id)
    VALUES ('delete', old.rowid, old.clean_text, old.id, old.notebook_id);
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, clean_text, chunk_id, notebook_id)
    VALUES ('delete', old.rowid, old.clean_text, old.id, old.notebook_id);
    INSERT INTO chunks_fts(rowid, clean_text, chunk_id, notebook_id)
    VALUES (new.rowid, new.clean_text, new.id, new.notebook_id);
END;
"""


_DEMO_NOTEBOOK_ID = "demo-bioelectronics-notebook"
_DEMO_CONV_ID = "demo-conversation-001"


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(_DDL)
        conn.executescript(_FTS_TRIGGERS)
        # ── Migrations (safe to re-run) ──────────────────────────────────
        try:
            conn.execute("ALTER TABLE sources ADD COLUMN sensitivity TEXT NOT NULL DEFAULT 'shareable'")
            conn.commit()
        except Exception:
            pass  # column already exists
        # Calendar events table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS calendar_events (
                id          TEXT PRIMARY KEY,
                notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
                title       TEXT NOT NULL,
                date        TEXT NOT NULL,
                end_date    TEXT,
                type        TEXT NOT NULL DEFAULT 'event',
                note        TEXT,
                created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cal_nb ON calendar_events(notebook_id, date)")
        conn.commit()
        _seed_defaults(conn)
        conn.commit()
        log.info("Notebook DB ready at %s", DB_PATH)
    finally:
        conn.close()


def _seed_defaults(conn: sqlite3.Connection) -> None:
    """Create a demo notebook with an example conversation on first run."""
    exists = conn.execute(
        "SELECT 1 FROM notebooks WHERE id=?", (_DEMO_NOTEBOOK_ID,)
    ).fetchone()
    if exists:
        return

    conn.execute(
        "INSERT INTO notebooks (id, name, description) VALUES (?,?,?)",
        (
            _DEMO_NOTEBOOK_ID,
            "Bioelectronics Research",
            "Self-healing conductive hydrogels for neural interfaces — "
            "synthesis, impedance, and biocompatibility data.",
        ),
    )
    conn.execute(
        "INSERT INTO conversations (id, notebook_id, title) VALUES (?,?,?)",
        (_DEMO_CONV_ID, _DEMO_NOTEBOOK_ID, "Project overview"),
    )


@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
