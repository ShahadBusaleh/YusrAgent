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


def find_cover_candidates(
    conn: sqlite3.Connection,
    employee_id: str,
    start_date: str,
    end_date: str,
    limit: int = 3,
) -> list[dict]:
    """Active colleagues free of overlapping leave during [start_date, end_date].

    Prefers the requester's own department; falls back to same-manager
    colleagues if the department has no free match.
    """
    requester = get_employee(conn, employee_id)
    if not requester:
        return []

    overlap_clause = """
        e.employee_id NOT IN (
            SELECT lr.employee_id FROM leave_requests lr
            WHERE lr.status IN ('pending', 'approved')
              AND lr.start_date <= ? AND lr.end_date >= ?
        )
    """

    def _query(scope_column: str, scope_value: str | None) -> list[dict]:
        if not scope_value:
            return []
        sql = f"""
            SELECT e.employee_id, e.full_name, e.job_title
            FROM employees e
            WHERE e.{scope_column} = ? AND e.employee_id != ? AND LOWER(e.employment_status) = 'active'
              AND {overlap_clause}
            ORDER BY e.full_name LIMIT ?
        """
        rows = conn.execute(
            sql, (scope_value, employee_id, end_date, start_date, limit)
        ).fetchall()
        return rows_to_dicts(rows)

    candidates = _query("department_id", requester.get("department_id"))
    if candidates:
        return candidates
    return _query("manager_id", requester.get("manager_id"))


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
