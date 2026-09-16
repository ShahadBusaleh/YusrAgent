"""Read-only payroll facts (payroll_monthly)."""

from __future__ import annotations

import sqlite3

from app.db.connection import row_to_dict


def get_latest_payroll(conn: sqlite3.Connection, employee_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT * FROM payroll_monthly
        WHERE employee_id = ?
        ORDER BY pay_period DESC
        LIMIT 1
        """,
        (employee_id,),
    ).fetchone()
    return row_to_dict(row)
