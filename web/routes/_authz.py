"""Centralised authorisation helpers for notebook-scoped routes.

Before this module existed, every route file rolled its own
ownership-check helper.  ``notes.py`` and ``tags.py`` both defined a
function called ``_check_nb_owner``; ``notebooks.py`` had ``_nb_or_404``
returning the row; ``collaboration.py`` had ``_can_access_notebook``.
Some opened their own connection, others took one as an argument, and
the NULL-owner short-circuit bug we just fixed had to be patched in
five places.  Consolidating the logic here means future tightening
applies in exactly one spot.
"""

from __future__ import annotations

import sqlite3

from fastapi import HTTPException
from notebook.db import get_conn


def _row_or_none(conn: sqlite3.Connection, nb_id: str) -> sqlite3.Row | None:
    """Fetch the full notebooks row or ``None``."""
    return conn.execute("SELECT * FROM notebooks WHERE id=?", (nb_id,)).fetchone()


def assert_notebook_owner(
    conn: sqlite3.Connection, nb_id: str, user_id: str
) -> dict:
    """Return the notebook row when *user_id* directly owns it.

    Raises 404 if the row is missing or unowned (``user_id IS NULL``)
    so legacy / pre-migration data cannot be reached by direct-ID
    access.  Raises 403 when the row is owned by a different user.
    """
    row = _row_or_none(conn, nb_id)
    if row is None or row["user_id"] is None:
        raise HTTPException(404, "Notebook not found")
    if row["user_id"] != user_id:
        raise HTTPException(403, "Access denied")
    return dict(row)


def notebook_or_404(nb_id: str, user_id: str | None = None) -> dict:
    """Open a connection, fetch the notebook, and assert ownership.

    Mirrors ``assert_notebook_owner`` but is convenient for routes that
    do not already hold a connection.  When *user_id* is ``None`` the
    ownership check is skipped (used by internal callers that only
    need the row).
    """
    with get_conn() as conn:
        row = _row_or_none(conn, nb_id)
    if row is None:
        raise HTTPException(404, f"Notebook {nb_id} not found")
    nb = dict(row)
    if user_id is not None:
        if nb.get("user_id") is None:
            raise HTTPException(404, f"Notebook {nb_id} not found")
        if nb["user_id"] != user_id:
            raise HTTPException(403, "Access denied")
    return nb


def can_access_notebook(
    conn: sqlite3.Connection, nb_id: str, user_id: str
) -> bool:
    """Return True if *user_id* may read or comment on the notebook.

    Access is granted when the caller owns the notebook directly or is
    a member of the notebook's organisation.  Unowned legacy rows
    (both ``user_id`` and ``org_id`` NULL) are not accessible.
    """
    row = conn.execute(
        "SELECT user_id, org_id FROM notebooks WHERE id=?", (nb_id,)
    ).fetchone()
    if row is None:
        return False
    if row["user_id"] == user_id:
        return True
    if row["org_id"]:
        member = conn.execute(
            "SELECT 1 FROM org_members WHERE org_id=? AND user_id=?",
            (row["org_id"], user_id),
        ).fetchone()
        if member:
            return True
    return False


def resolve_notebook_for_target(
    conn: sqlite3.Connection,
    *,
    notebook_id: str | None = None,
    source_id: str | None = None,
    note_id: str | None = None,
) -> str | None:
    """Map a comment/activity target back to its parent notebook id.

    Returns ``None`` if no filter was supplied or the referenced row
    does not exist.  Callers should treat ``None`` as a 404.
    """
    if notebook_id:
        return notebook_id
    if source_id:
        row = conn.execute(
            "SELECT notebook_id FROM sources WHERE id=?", (source_id,)
        ).fetchone()
        return row["notebook_id"] if row else None
    if note_id:
        row = conn.execute(
            "SELECT notebook_id FROM notes WHERE id=?", (note_id,)
        ).fetchone()
        return row["notebook_id"] if row else None
    return None
