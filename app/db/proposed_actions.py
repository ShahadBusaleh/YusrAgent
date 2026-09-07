"""Proposed actions."""

from __future__ import annotations

import sqlite3

from app.db.connection import row_to_dict, rows_to_dicts


def list_proposed_actions(
    conn: sqlite3.Connection, employee_id: str | None = None
) -> list[dict]:
    if employee_id:
        rows = conn.execute(
            """
            SELECT * FROM proposed_actions
            WHERE employee_id = ?
            ORDER BY created_at DESC
            """,
            (employee_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM proposed_actions ORDER BY created_at DESC"
        ).fetchall()
    return rows_to_dicts(rows)


def get_proposed_action(conn: sqlite3.Connection, proposal_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM proposed_actions WHERE proposal_id = ?",
        (proposal_id,),
    ).fetchone()
    return row_to_dict(row)
