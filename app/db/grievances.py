"""Grievance persistence helpers."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.db.connection import next_id, row_to_dict, rows_to_dicts


def list_grievances(
    conn: sqlite3.Connection,
    status: str | None = None,
) -> list[dict]:
    if status:
        rows = conn.execute(
            """
            SELECT *
            FROM grievances
            WHERE status = ?
            ORDER BY submitted_at DESC
            """,
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT *
            FROM grievances
            ORDER BY submitted_at DESC
            """
        ).fetchall()

    return rows_to_dicts(rows)


def get_grievance(
    conn: sqlite3.Connection,
    grievance_id: str,
) -> dict | None:
    row = conn.execute(
        """
        SELECT *
        FROM grievances
        WHERE grievance_id = ?
        """,
        (grievance_id,),
    ).fetchone()

    return row_to_dict(row)


def create_grievance(

    conn: sqlite3.Connection,
    *,
    employee_id: str | None,
    identity_visible: bool,
    complaint: str,
    consultant_recommendation: str | None,
    sources: str | None,
) -> dict:
    grievance_id = next_id(
        conn,
        "grievances",
        "grievance_id",
        "GR",
    )

    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """
        INSERT INTO grievances (
            grievance_id,
            employee_id,
            identity_visible,
            complaint,
            consultant_recommendation,
            sources,
            status,
            submitted_at,
            decided_at,
            decided_by,
            hr_response
        )
        VALUES (?, ?, ?, ?, ?, ?, 'PENDING_HR_REVIEW', ?, NULL, NULL, NULL)
        """,
        (
            grievance_id,
            employee_id if identity_visible else None,
            int(identity_visible),
            complaint,
            consultant_recommendation,
            sources,
            now,
        ),
    )

    return get_grievance(conn, grievance_id)


def decide_grievance(
    conn: sqlite3.Connection,
    *,
    grievance_id: str,
    decision: str,
    decided_by: str,
    response: str | None = None,
) -> dict | None:
    status_map = {
        "accept": "SUBMITTED",
        "reject": "SENT_BACK",
    }

    new_status = status_map.get(decision)

    if not new_status:
        raise ValueError("Invalid grievance decision.")

    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """
        UPDATE grievances
        SET
            status = ?,
            decided_at = ?,
            decided_by = ?,
            hr_response = ?
        WHERE grievance_id = ?
        """,
        (
            new_status,
            now,
            decided_by,
            response,
            grievance_id,
        ),
    )

    return get_grievance(conn, grievance_id)