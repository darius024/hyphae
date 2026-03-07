"""
SQLite database — Hyphae Notebook layer.

Tables: notebooks, sources, chunks, conversations, messages, settings
FTS5 virtual table chunks_fts for BM25 full-text search.
"""

import re
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

-- ── Tags & Categories ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tags (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    color       TEXT DEFAULT '#6366f1',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS source_tags (
    source_id   TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    tag_id      TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (source_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_source_tags_src ON source_tags(source_id);
CREATE INDEX IF NOT EXISTS idx_source_tags_tag ON source_tags(tag_id);

-- ── Document Links (Knowledge Graph) ────────────────────────────────────

CREATE TABLE IF NOT EXISTS document_links (
    id          TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    target_id   TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    link_type   TEXT DEFAULT 'related',
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(source_id, target_id)
);

CREATE INDEX IF NOT EXISTS idx_doclinks_src ON document_links(source_id);
CREATE INDEX IF NOT EXISTS idx_doclinks_tgt ON document_links(target_id);

-- ── Usage Analytics ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS usage_events (
    id          TEXT PRIMARY KEY,
    user_id     TEXT REFERENCES users(id) ON DELETE SET NULL,
    event_type  TEXT NOT NULL,
    event_data  TEXT,
    route       TEXT,
    tools_used  TEXT,
    latency_ms  REAL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_usage_type ON usage_events(event_type);
CREATE INDEX IF NOT EXISTS idx_usage_date ON usage_events(created_at);
CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_events(user_id);

-- ── Paper Deadlines & Reminders ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS deadlines (
    id          TEXT PRIMARY KEY,
    notebook_id TEXT REFERENCES notebooks(id) ON DELETE CASCADE,
    source_id   TEXT REFERENCES sources(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    due_date    TEXT NOT NULL,
    priority    TEXT DEFAULT 'medium',
    status      TEXT DEFAULT 'pending',
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_deadlines_due ON deadlines(due_date);
CREATE INDEX IF NOT EXISTS idx_deadlines_nb ON deadlines(notebook_id);

CREATE TABLE IF NOT EXISTS reminders (
    id          TEXT PRIMARY KEY,
    user_id     TEXT REFERENCES users(id) ON DELETE CASCADE,
    deadline_id TEXT REFERENCES deadlines(id) ON DELETE CASCADE,
    remind_at   TEXT NOT NULL,
    sent        INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_reminders_at ON reminders(remind_at, sent);

-- ── Calendar Sync Tokens ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS calendar_connections (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider     TEXT NOT NULL,
    access_token TEXT,
    refresh_token TEXT,
    token_expiry TEXT,
    calendar_id  TEXT,
    sync_token   TEXT,
    last_sync    TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_calcn_user ON calendar_connections(user_id);

-- ── Note Versions (Version History) ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS notes (
    id          TEXT PRIMARY KEY,
    notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_notes_nb ON notes(notebook_id);

CREATE TABLE IF NOT EXISTS note_versions (
    id          TEXT PRIMARY KEY,
    note_id     TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    content     TEXT NOT NULL,
    version_num INTEGER NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_notever_note ON note_versions(note_id);

-- ── AI Writing Sessions ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS writing_sessions (
    id          TEXT PRIMARY KEY,
    notebook_id TEXT REFERENCES notebooks(id) ON DELETE CASCADE,
    note_id     TEXT REFERENCES notes(id) ON DELETE CASCADE,
    content     TEXT,
    ai_suggestions TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ══════════════════════════════════════════════════════════════════════════
-- ORGANIZATIONS & COLLABORATION
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS organizations (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE,
    description TEXT,
    avatar_url  TEXT,
    owner_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_org_slug ON organizations(slug);
CREATE INDEX IF NOT EXISTS idx_org_owner ON organizations(owner_id);

CREATE TABLE IF NOT EXISTS org_members (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role         TEXT NOT NULL DEFAULT 'member',  -- owner, admin, member, viewer
    joined_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(org_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_orgmem_org ON org_members(org_id);
CREATE INDEX IF NOT EXISTS idx_orgmem_user ON org_members(user_id);

CREATE TABLE IF NOT EXISTS org_invites (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    email       TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'member',
    token       TEXT NOT NULL UNIQUE,
    invited_by  TEXT REFERENCES users(id) ON DELETE SET NULL,
    accepted    INTEGER DEFAULT 0,
    expires_at  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_orginv_token ON org_invites(token);
CREATE INDEX IF NOT EXISTS idx_orginv_email ON org_invites(email);

-- Link notebooks to organizations (optional - null means personal)
-- Add org_id column to notebooks via migration below

-- ══════════════════════════════════════════════════════════════════════════
-- COMMENTS & ANNOTATIONS
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS comments (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    notebook_id  TEXT REFERENCES notebooks(id) ON DELETE CASCADE,
    source_id    TEXT REFERENCES sources(id) ON DELETE CASCADE,
    note_id      TEXT REFERENCES notes(id) ON DELETE CASCADE,
    conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
    parent_id    TEXT REFERENCES comments(id) ON DELETE CASCADE,
    content      TEXT NOT NULL,
    resolved     INTEGER DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_comments_nb ON comments(notebook_id);
CREATE INDEX IF NOT EXISTS idx_comments_src ON comments(source_id);
CREATE INDEX IF NOT EXISTS idx_comments_note ON comments(note_id);
CREATE INDEX IF NOT EXISTS idx_comments_parent ON comments(parent_id);
CREATE INDEX IF NOT EXISTS idx_comments_user ON comments(user_id);

-- ══════════════════════════════════════════════════════════════════════════
-- ACTIVITY FEED
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS activity_feed (
    id           TEXT PRIMARY KEY,
    org_id       TEXT REFERENCES organizations(id) ON DELETE CASCADE,
    user_id      TEXT REFERENCES users(id) ON DELETE SET NULL,
    notebook_id  TEXT REFERENCES notebooks(id) ON DELETE CASCADE,
    action       TEXT NOT NULL,  -- created, updated, commented, shared, uploaded
    target_type  TEXT NOT NULL,  -- notebook, source, note, comment, deadline
    target_id    TEXT,
    target_title TEXT,
    metadata     TEXT,  -- JSON extra data
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_activity_org ON activity_feed(org_id, created_at);
CREATE INDEX IF NOT EXISTS idx_activity_nb ON activity_feed(notebook_id, created_at);
CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_feed(user_id, created_at);

-- ════════════════════════════════════════════════════════════════════════
-- CALENDAR EVENTS
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS calendar_events (
    id          TEXT PRIMARY KEY,
    notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    date        TEXT NOT NULL,
    end_date    TEXT,
    type        TEXT NOT NULL DEFAULT 'event',
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_cal_nb ON calendar_events(notebook_id, date);
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
        except sqlite3.OperationalError:
            pass  # column already exists
        
        # Add org_id to notebooks (nullable - null means personal)
        try:
            conn.execute("ALTER TABLE notebooks ADD COLUMN org_id TEXT REFERENCES organizations(id) ON DELETE SET NULL")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_nb_org ON notebooks(org_id)")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        
        # Add user_id to notebooks (for personal notebooks)
        try:
            conn.execute("ALTER TABLE notebooks ADD COLUMN user_id TEXT REFERENCES users(id) ON DELETE CASCADE")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_nb_user ON notebooks(user_id)")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        
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


_SAFE_COLUMN = re.compile(r"^[a-z][a-z0-9_]*$")


def safe_update(
    conn: sqlite3.Connection,
    table: str,
    assignments: dict,
    where_col: str,
    where_val,
    *,
    auto_timestamp: bool = True,
) -> None:
    """Execute an UPDATE with validated column names.

    Every key in *assignments* is checked against a strict identifier pattern
    before interpolation, eliminating the risk of SQL injection through
    dynamically-built SET clauses.
    """
    if not assignments:
        return
    if not _SAFE_COLUMN.match(table):
        raise ValueError(f"Unsafe table name: {table!r}")
    if not _SAFE_COLUMN.match(where_col):
        raise ValueError(f"Unsafe WHERE column: {where_col!r}")
    for col in assignments:
        if not _SAFE_COLUMN.match(col):
            raise ValueError(f"Unsafe column name: {col!r}")

    parts = [f"{col}=?" for col in assignments]
    params: list = list(assignments.values())

    if auto_timestamp:
        parts.append("updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')")

    params.append(where_val)
    conn.execute(f"UPDATE {table} SET {', '.join(parts)} WHERE {where_col}=?", params)


def purge_expired_sessions() -> int:
    """Delete sessions whose expires_at is in the past. Returns count deleted."""
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM sessions WHERE expires_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"
        )
        deleted = cur.rowcount
    if deleted:
        log.info("Purged %d expired session(s)", deleted)
    return deleted
