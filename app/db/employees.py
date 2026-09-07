"""Employee profile access. Matches employees + departments columns as-is."""

from __future__ import annotations

import sqlite3

from app.db.connection import row_to_dict, rows_to_dicts

PROFILE_UPDATE_FIELDS = ("mobile", "address", "city", "email")


def get_employee(conn: sqlite3.Connection, employee_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT e.*, d.department_name
        FROM employees e
        LEFT JOIN departments d ON d.department_id = e.department_id
        WHERE e.employee_id = ?
        """,
        (employee_id,),
    ).fetchone()
    return row_to_dict(row)


def list_employees(
    conn: sqlite3.Connection,
    *,
    q: str | None = None,
    department_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    sql = """
        SELECT e.employee_id, e.full_name, e.email, e.mobile, e.job_title,
               e.employment_status, e.department_id, d.department_name, e.manager_id
        FROM employees e
        LEFT JOIN departments d ON d.department_id = e.department_id
        WHERE 1 = 1
    """
    params: list[object] = []
    if q:
        sql += " AND (e.full_name LIKE ? OR e.employee_id LIKE ? OR e.email LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like])
    if department_id:
        sql += " AND e.department_id = ?"
        params.append(department_id)
    sql += " ORDER BY e.full_name LIMIT ?"
    params.append(limit)
    return rows_to_dicts(conn.execute(sql, params).fetchall())


def update_employee_profile(
    conn: sqlite3.Connection, employee_id: str, fields: dict
) -> dict | None:
    updates = {k: v for k, v in fields.items() if k in PROFILE_UPDATE_FIELDS and v is not None}
    if not updates:
        return get_employee(conn, employee_id)
    assignments = ", ".join(f"{col} = ?" for col in updates)
    values = list(updates.values()) + [employee_id]
    conn.execute(
        f"UPDATE employees SET {assignments} WHERE employee_id = ?",
        values,
    )
    return get_employee(conn, employee_id)
