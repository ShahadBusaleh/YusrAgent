"""Audit log reads and writes."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.db.connection import rows_to_dicts


def list_audit_log(
    conn: sqlite3.Connection,
    *,
    employee_id: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
) -> list[dict]:
    sql = "SELECT * FROM audit_log WHERE 1 = 1"
    params: list[object] = []
    if employee_id:
        sql += " AND employee_id = ?"
        params.append(employee_id)
    if event_type:
        sql += " AND event_type = ?"
        params.append(event_type)
    sql += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    return rows_to_dicts(conn.execute(sql, params).fetchall())


def write_audit(
    conn: sqlite3.Connection,
    *,
    actor: str,
    event_type: str,
    employee_id: str | None,
    details: str | None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO audit_log (timestamp, actor, event_type, employee_id, details)
        VALUES (?, ?, ?, ?, ?)
        """,
        (now, actor, event_type, employee_id, details),
    )
