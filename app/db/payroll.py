"""Read-only payroll facts (payroll_monthly)."""

from __future__ import annotations

import sqlite3

from app.db.connection import row_to_dict, rows_to_dicts


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


def get_payroll_for_period(
    conn: sqlite3.Connection, employee_id: str, period: str
) -> dict | None:
    row = conn.execute(
        "SELECT * FROM payroll_monthly WHERE employee_id = ? AND pay_period = ?",
        (employee_id, period),
    ).fetchone()
    return row_to_dict(row)


def list_payroll_periods(conn: sqlite3.Connection) -> list[str]:
    """Distinct pay periods, newest first — for the Payroll month picker."""
    rows = conn.execute(
        "SELECT DISTINCT pay_period FROM payroll_monthly ORDER BY pay_period DESC"
    ).fetchall()
    return [row[0] for row in rows]


def list_payroll_for_period(conn: sqlite3.Connection, period: str) -> list[dict]:
    """Every employee's payroll row for one period, for the bulk PDF export."""
    rows = conn.execute(
        """
        SELECT p.*, e.full_name, e.job_title, d.department_name
        FROM payroll_monthly p
        JOIN employees e ON e.employee_id = p.employee_id
        LEFT JOIN departments d ON d.department_id = e.department_id
        WHERE p.pay_period = ?
        ORDER BY e.full_name
        """,
        (period,),
    ).fetchall()
    return rows_to_dicts(rows)
