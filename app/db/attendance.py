"""Read-only attendance facts (attendance_leave_monthly)."""

from __future__ import annotations

import sqlite3

from app.db.connection import row_to_dict


def get_latest_attendance(conn: sqlite3.Connection, employee_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT * FROM attendance_leave_monthly
        WHERE employee_id = ?
        ORDER BY attendance_month DESC
        LIMIT 1
        """,
        (employee_id,),
    ).fetchone()
    return row_to_dict(row)
