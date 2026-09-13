"""Pending approvals queue."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from app.db.connection import next_id, row_to_dict, rows_to_dicts
from app.db.leave import create_leave_request
from app.db.proposed_actions import get_proposed_action

_LEAVE_BALANCE_COLUMNS = {"annual", "sick", "emergency"}


def list_pending_approvals(
    conn: sqlite3.Connection, status: str | None = None
) -> list[dict]:
    if status:
        rows = conn.execute(
            """
            SELECT * FROM pending_approvals
            WHERE status = ?
            ORDER BY created_at DESC
            """,
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM pending_approvals ORDER BY created_at DESC"
        ).fetchall()
    return rows_to_dicts(rows)


def get_approval(conn: sqlite3.Connection, approval_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM pending_approvals WHERE approval_id = ?",
        (approval_id,),
    ).fetchone()
    return row_to_dict(row)


def create_pending_approval(
    conn: sqlite3.Connection,
    *,
    proposal_id: str,
    employee_id: str,
    action_summary: str,
    risk_level: str,
) -> dict:
    approval_id = next_id(conn, "pending_approvals", "approval_id", "AP")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO pending_approvals (
            approval_id, proposal_id, employee_id, action_summary,
            risk_level, status, created_at, decided_at, decided_by, decision_note
        ) VALUES (?, ?, ?, ?, ?, 'pending', ?, NULL, NULL, NULL)
        """,
        (approval_id, proposal_id, employee_id, action_summary, risk_level, now),
    )
    return get_approval(conn, approval_id)


def decide_approval(
    conn: sqlite3.Connection,
    approval_id: str,
    *,
    decision: str,
    decided_by: str,
    decision_note: str | None,
) -> dict | None:
    current = get_approval(conn, approval_id)
    if current is None:
        return None
    now = datetime.now(timezone.utc).isoformat()
    status = "approved" if decision == "approve" else "rejected"
    conn.execute(
        """
        UPDATE pending_approvals
        SET status = ?, decided_at = ?, decided_by = ?, decision_note = ?
        WHERE approval_id = ?
        """,
        (status, now, decided_by, decision_note, approval_id),
    )
    proposal_id = current.get("proposal_id")
    if proposal_id:
        conn.execute(
            "UPDATE proposed_actions SET status = ? WHERE proposal_id = ?",
            (status, proposal_id),
        )
        _sync_leave_request(conn, proposal_id, status=status, decided_at=now, decided_by=decided_by)
    return get_approval(conn, approval_id)


def _sync_leave_request(
    conn: sqlite3.Connection,
    proposal_id: str,
    *,
    status: str,
    decided_at: str,
    decided_by: str,
) -> None:
    """If the decided proposal is a leave_request, materialize it into leave_requests."""
    proposal = get_proposed_action(conn, proposal_id)
    if not proposal or proposal.get("action_type") != "leave_request":
        return

    try:
        payload = json.loads(proposal.get("payload_json") or "{}")
    except (TypeError, ValueError):
        return

    created = create_leave_request(
        conn,
        employee_id=proposal["employee_id"],
        leave_type=payload.get("leave_type"),
        start_date=payload.get("start_date"),
        end_date=payload.get("end_date"),
        days=payload.get("days"),
        reason=payload.get("reason"),
    )
    conn.execute(
        "UPDATE leave_requests SET status = ?, decided_at = ?, decided_by = ? WHERE request_id = ?",
        (status, decided_at, decided_by, created["request_id"]),
    )
    conn.execute(
        "UPDATE proposed_actions SET related_request_id = ? WHERE proposal_id = ?",
        (created["request_id"], proposal_id),
    )

    leave_type = str(payload.get("leave_type") or "").strip().lower()
    days = payload.get("days")
    if status == "approved" and leave_type in _LEAVE_BALANCE_COLUMNS and days:
        conn.execute(
            f"""
            UPDATE leave_balances
            SET {leave_type}_used = {leave_type}_used + ?,
                {leave_type}_remaining = {leave_type}_remaining - ?
            WHERE employee_id = ?
            """,
            (days, days, proposal["employee_id"]),
        )
