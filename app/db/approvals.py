"""Pending approvals queue."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.db.connection import row_to_dict, rows_to_dicts


def list_pending_approvals(
    conn: sqlite3.Connection, status: str | None = None
) -> list[dict]:
    if status:
        rows = conn.execute(
            """
            SELECT * FROM pending_approvals
            WHERE status = ?
            ORDER BY created_at DESC
            """,
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM pending_approvals ORDER BY created_at DESC"
        ).fetchall()
    return rows_to_dicts(rows)


def get_approval(conn: sqlite3.Connection, approval_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM pending_approvals WHERE approval_id = ?",
        (approval_id,),
    ).fetchone()
    return row_to_dict(row)


def decide_approval(
    conn: sqlite3.Connection,
    approval_id: str,
    *,
    decision: str,
    decided_by: str,
    decision_note: str | None,
) -> dict | None:
    current = get_approval(conn, approval_id)
    if current is None:
        return None
    now = datetime.now(timezone.utc).isoformat()
    status = "approved" if decision == "approve" else "rejected"
    conn.execute(
        """
        UPDATE pending_approvals
        SET status = ?, decided_at = ?, decided_by = ?, decision_note = ?
        WHERE approval_id = ?
        """,
        (status, now, decided_by, decision_note, approval_id),
    )
    proposal_id = current.get("proposal_id")
    if proposal_id:
        conn.execute(
            "UPDATE proposed_actions SET status = ? WHERE proposal_id = ?",
            (status, proposal_id),
        )
    return get_approval(conn, approval_id)
