"""Leave balances and leave requests."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.db.connection import next_id, row_to_dict, rows_to_dicts


def get_leave_balance(conn: sqlite3.Connection, employee_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM leave_balances WHERE employee_id = ?",
        (employee_id,),
    ).fetchone()
    return row_to_dict(row)


def list_leave_requests(conn: sqlite3.Connection, employee_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM leave_requests
        WHERE employee_id = ?
        ORDER BY submitted_at DESC
        """,
        (employee_id,),
    ).fetchall()
    return rows_to_dicts(rows)


def create_leave_request(
    conn: sqlite3.Connection,
    *,
    employee_id: str,
    leave_type: str,
    start_date: str,
    end_date: str,
    days: float,
    reason: str | None,
) -> dict:
    request_id = next_id(conn, "leave_requests", "request_id", "LR")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO leave_requests (
            request_id, employee_id, leave_type, start_date, end_date,
            days, reason, status, submitted_at, decided_at, decided_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL, NULL)
        """,
        (
            request_id,
            employee_id,
            leave_type,
            start_date,
            end_date,
            days,
            reason,
            now,
        ),
    )
    row = conn.execute(
        "SELECT * FROM leave_requests WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    return dict(row)
