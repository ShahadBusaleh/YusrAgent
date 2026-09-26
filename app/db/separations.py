"""Final separation of terminated employees (Phase 4, TEAM.md).

termination_date is the LAST WORKING DAY. An approved termination leaves the
employee Active (notice period) with termination_date set; the day after
termination_date, finalize_due_separations() applies the final status and
deactivates the login. Art. 80 and waived notice apply at approval.

finalize_due_separations() runs on API startup and daily from
app/api/routers/onboarding.py; _sync_termination calls apply_final_separation()
directly when the separation applies immediately. Never deletes rows.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

# employment_status by termination type; values match the seeded statuses.
_FINAL_STATUS = {
    "resignation": "Resigned",
    "retirement": "Retired",
    "end_of_contract": "End of Contract",
}


def final_status(termination_type: str | None) -> str:
    return _FINAL_STATUS.get(termination_type or "", "Terminated")


def parse_day(value: str | None) -> date | None:
    """employees.termination_date is stored DD-MM-YYYY."""
    try:
        return datetime.strptime(str(value or "").strip(), "%d-%m-%Y").date()
    except ValueError:
        return None


def is_due(
    termination_type: str | None,
    termination_date: str | None,
    today: date | None = None,
    notice_waived: bool = False,
) -> bool:
    """Final now: Art. 80, waived notice, or a last working day already past."""
    if termination_type == "article_80" or notice_waived:
        return True
    day = parse_day(termination_date)
    return day is not None and day < (today or date.today())


def apply_final_separation(
    conn: sqlite3.Connection,
    employee_id: str,
    termination_type: str | None,
    termination_date: str | None,
) -> None:
    conn.execute(
        """
        UPDATE employees
        SET employment_status = ?, termination_date = ?
        WHERE employee_id = ?
        """,
        (final_status(termination_type), termination_date, employee_id),
    )
    conn.execute("UPDATE users SET is_active = 0 WHERE employee_id = ?", (employee_id,))


def _approved_termination(conn: sqlite3.Connection, employee_id: str) -> tuple[str, dict] | None:
    rows = conn.execute(
        """
        SELECT proposal_id, payload_json FROM proposed_actions
        WHERE action_type = 'termination' AND status = 'approved'
        ORDER BY created_at DESC
        """
    ).fetchall()
    for proposal_id, payload_json in rows:
        try:
            payload = json.loads(payload_json or "{}")
        except (TypeError, ValueError):
            continue
        if payload.get("employee_id") == employee_id:
            return proposal_id, payload
    return None


def finalize_due_separations(conn: sqlite3.Connection, today: date | None = None) -> list[str]:
    """Apply the final status to every Active employee whose last working day
    (termination_date) is before today and who has an approved termination.
    Idempotent. The caller commits. Returns the employee_ids finalized."""
    today = today or date.today()
    finalized: list[str] = []
    rows = conn.execute(
        """
        SELECT employee_id, termination_date FROM employees
        WHERE employment_status = 'Active' AND termination_date IS NOT NULL
        """
    ).fetchall()
    for employee_id, termination_date in rows:
        day = parse_day(termination_date)
        if day is None or day >= today:  # still their last working day, or before it
            continue
        match = _approved_termination(conn, employee_id)
        if match is None:
            # A date without an approved termination isn't ours to act on.
            continue
        proposal_id, payload = match
        apply_final_separation(conn, employee_id, payload.get("termination_type"), termination_date)
        payload["finalized_at"] = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE proposed_actions SET payload_json = ? WHERE proposal_id = ?",
            (json.dumps(payload), proposal_id),
        )
        finalized.append(employee_id)
    return finalized
