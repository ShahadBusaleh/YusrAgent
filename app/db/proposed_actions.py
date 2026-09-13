"""Proposed actions."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from app.db.connection import next_id, row_to_dict, rows_to_dicts


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


def create_proposed_action(
    conn: sqlite3.Connection,
    *,
    employee_id: str,
    action_type: str,
    payload: dict,
    risk_level: str,
    related_request_id: str | None = None,
) -> dict:
    proposal_id = next_id(conn, "proposed_actions", "proposal_id", "PA")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO proposed_actions (
            proposal_id, employee_id, action_type, payload_json,
            risk_level, status, created_at, related_request_id
        ) VALUES (?, ?, ?, ?, ?, 'pending_approval', ?, ?)
        """,
        (
            proposal_id,
            employee_id,
            action_type,
            json.dumps(payload),
            risk_level,
            now,
            related_request_id,
        ),
    )
    return get_proposed_action(conn, proposal_id)
