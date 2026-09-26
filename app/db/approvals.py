"""Pending approvals queue."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from app.db.connection import next_id, row_to_dict, rows_to_dicts
from app.db.attendance import add_leave_days
from app.db.employees import get_employee
from app.db.leave import create_leave_request
from app.db.proposed_actions import get_proposed_action
from app.db.separations import apply_final_separation, is_due
from app.security.passwords import hash_password
from app.seed_leave_balances import (
    ANNUAL_BASE_DAYS,
    ANNUAL_SENIOR_DAYS,
    ANNUAL_SENIOR_YEARS,
    DATE_FMT_ROWS,
    EMERGENCY_ENTITLEMENT_DAYS,
    SICK_ENTITLEMENT_DAYS,
    _parse_ddmmyyyy,
    _years_of_service,
)

_LEAVE_BALANCE_COLUMNS = {"annual", "sick", "emergency"}


def _days_by_month(start_date: str, end_date: str) -> list[tuple[str, int]]:
    """Split a leave date range into (YYYY-MM, day_count) pairs so a leave
    spanning a month boundary credits each attendance_leave_monthly row
    correctly instead of only the start month."""
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except (TypeError, ValueError):
        return []
    if end < start:
        return []

    counts: dict[str, int] = {}
    current = start
    while current <= end:
        key = current.strftime("%Y-%m")
        counts[key] = counts.get(key, 0) + 1
        current += timedelta(days=1)
    return list(counts.items())



def list_pending_approvals(
    conn: sqlite3.Connection,
    status: str | None = None,
    exclude_employee_id: str | None = None,
) -> list[dict]:
    if status:
        if exclude_employee_id:
            rows = conn.execute(
                """
                SELECT * FROM pending_approvals
                WHERE status = ?
                  AND employee_id != ?
                ORDER BY created_at DESC
                """,
                (status, exclude_employee_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM pending_approvals
                WHERE status = ?
                ORDER BY created_at DESC
                """,
                (status,),
            ).fetchall()
    else:
        if exclude_employee_id:
            rows = conn.execute(
                """
                SELECT * FROM pending_approvals
                WHERE employee_id != ?
                ORDER BY created_at DESC
                """,
                (exclude_employee_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM pending_approvals
                ORDER BY created_at DESC
                """
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
    cover_employee_id: str | None = None,
) -> dict | None:
    current = get_approval(conn, approval_id)
    if current is None:
        return None
    if current.get("status") != "pending":
        # Already decided — return as-is instead of re-running the
        # side effects below (which would double-create leave requests
        # and double-deduct the leave balance on a repeat click).
        return current
    now = datetime.now(timezone.utc).isoformat()
    status = "approved" if decision == "approve" else "rejected"
    cursor = conn.execute(
        """
        UPDATE pending_approvals
        SET status = ?, decided_at = ?, decided_by = ?, decision_note = ?
        WHERE approval_id = ? AND status = 'pending'
        """,
        (status, now, decided_by, decision_note, approval_id),
    )
    if cursor.rowcount == 0:
        # Someone else decided it between our read and this write.
        return get_approval(conn, approval_id)
    proposal_id = current.get("proposal_id")
    if proposal_id:
        conn.execute(
            "UPDATE proposed_actions SET status = ? WHERE proposal_id = ?",
            (status, proposal_id),
        )
        _sync_leave_request(
            conn,
            proposal_id,
            status=status,
            decided_at=now,
            decided_by=decided_by,
            cover_employee_id=cover_employee_id,
        )
        _sync_personal_info_update(
            conn,
            proposal_id,
            status=status,
            decided_at=now,
            decided_by=decided_by,
        )
        _sync_sensitive_change(
            conn,
            proposal_id,
            status=status,
            decided_at=now,
            decided_by=decided_by,
        )
        _sync_new_hire(conn, proposal_id, status=status, decided_at=now, decided_by=decided_by)
        _sync_termination(conn, proposal_id, status=status, decided_at=now, decided_by=decided_by)
        _sync_regulation_update(conn, proposal_id, status=status, decided_at=now, decided_by=decided_by)
    return get_approval(conn, approval_id)


def _assign_cover_employee(
    conn: sqlite3.Connection,
    proposal_id: str,
    payload: dict,
    cover_employee_id: str | None,
) -> dict:
    """Record the HR manager's chosen cover employee on the proposal.

    Falls back to the system-suggested cover when the manager didn't
    override it, so an approved leave request always has a definitive
    assigned cover once decided. The chosen id/name is written back
    into proposed_actions.payload_json (no schema change needed).
    """
    chosen_id = cover_employee_id or payload.get("suggested_cover_employee_id")
    chosen_name = payload.get("suggested_cover_employee_name")
    if cover_employee_id:
        chosen = get_employee(conn, cover_employee_id)
        chosen_name = (chosen or {}).get("full_name") or cover_employee_id

    payload = dict(payload)
    payload["assigned_cover_employee_id"] = chosen_id
    payload["assigned_cover_employee_name"] = chosen_name

    conn.execute(
        "UPDATE proposed_actions SET payload_json = ? WHERE proposal_id = ?",
        (json.dumps(payload), proposal_id),
    )
    return payload


def _sync_leave_request(
    conn: sqlite3.Connection,
    proposal_id: str,
    *,
    status: str,
    decided_at: str,
    decided_by: str,
    cover_employee_id: str | None = None,
) -> None:
    """If the decided proposal is a leave_request, materialize it into leave_requests."""
    proposal = get_proposed_action(conn, proposal_id)
    if not proposal or proposal.get("action_type") != "leave_request":
        return

    try:
        payload = json.loads(proposal.get("payload_json") or "{}")
    except (TypeError, ValueError):
        return

    if status == "approved":
        payload = _assign_cover_employee(conn, proposal_id, payload, cover_employee_id)

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

    if status == "approved":
        for period, day_count in _days_by_month(
            payload.get("start_date"), payload.get("end_date")
        ):
            add_leave_days(
                conn,
                proposal["employee_id"],
                period,
                leave_type,
                day_count,
            )


def _sync_personal_info_update(
    conn: sqlite3.Connection,
    proposal_id: str,
    *,
    status: str,
    decided_at: str,
    decided_by: str,
) -> None:
    """If the decided proposal is a personal_info_update, materialize it
    into personal_info_update_requests."""
    proposal = get_proposed_action(conn, proposal_id)
    if not proposal or proposal.get("action_type") != "personal_info_update":
        return

    try:
        payload = json.loads(proposal.get("payload_json") or "{}")
    except (TypeError, ValueError):
        return

    request_id = next_id(conn, "personal_info_update_requests", "request_id", "PI")
    conn.execute(
        """
        INSERT INTO personal_info_update_requests (
            request_id, employee_id, field_name, old_value, new_value,
            status, risk_level, submitted_at, decided_at, decided_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request_id,
            proposal["employee_id"],
            payload.get("field_name"),
            payload.get("old_value"),
            payload.get("new_value"),
            status,
            proposal.get("risk_level"),
            proposal.get("created_at"),
            decided_at,
            decided_by,
        ),
    )
    conn.execute(
        "UPDATE proposed_actions SET related_request_id = ? WHERE proposal_id = ?",
        (request_id, proposal_id),
    )


def _sync_sensitive_change(
    conn: sqlite3.Connection,
    proposal_id: str,
    *,
    status: str,
    decided_at: str,
    decided_by: str,
) -> None:
    """If the decided proposal is a bank_update, materialize it into
    sensitive_change_requests."""
    proposal = get_proposed_action(conn, proposal_id)
    if not proposal or proposal.get("action_type") != "bank_update":
        return

    try:
        payload = json.loads(proposal.get("payload_json") or "{}")
    except (TypeError, ValueError):
        return

    request_id = next_id(conn, "sensitive_change_requests", "request_id", "SC")
    conn.execute(
        """
        INSERT INTO sensitive_change_requests (
            request_id, employee_id, change_type, old_bank_code, old_iban,
            new_bank_code, new_bank_name, new_iban,
            status, risk_level, submitted_at, decided_at, decided_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request_id,
            proposal["employee_id"],
            payload.get("change_type"),
            payload.get("old_bank_code"),
            payload.get("old_iban"),
            payload.get("new_bank_code"),
            payload.get("new_bank_name"),
            payload.get("new_iban"),
            status,
            proposal.get("risk_level"),
            proposal.get("created_at"),
            decided_at,
            decided_by,
        ),
    )
    conn.execute(
        "UPDATE proposed_actions SET related_request_id = ? WHERE proposal_id = ?",
        (request_id, proposal_id),
    )


# Same dev/test password as every seeded account (see DATABASE_SCHEMA.md).
# Demo project only — replace before any real use.
_NEW_HIRE_DEFAULT_PASSWORD = "ChangeMe123!"

def _next_employee_id(conn: sqlite3.Connection) -> str:
    """EMP-#### in the seeded 4-digit format (next_id would pad to 5)."""
    row = conn.execute(
        """
        SELECT MAX(CAST(SUBSTR(employee_id, 5) AS INTEGER))
        FROM employees
        WHERE employee_id LIKE 'EMP-%'
        """
    ).fetchone()
    return f"EMP-{(row[0] or 0) + 1:04d}"


def _record_decision(
    conn: sqlite3.Connection,
    proposal_id: str,
    payload: dict,
    *,
    status: str,
    decided_at: str,
    decided_by: str | None,
) -> dict:
    """Copy the decision (approver, date, note) into the proposal payload so
    the requester can see it — hr_specialist has no /approvals access.
    Only the proposal record is written; no target table."""
    row = conn.execute(
        "SELECT decision_note FROM pending_approvals WHERE proposal_id = ?",
        (proposal_id,),
    ).fetchone()
    payload = dict(payload)
    payload["decision"] = {
        "status": status,
        "decided_by": decided_by,
        "decided_at": decided_at,
        "note": row[0] if row else None,
    }
    conn.execute(
        "UPDATE proposed_actions SET payload_json = ? WHERE proposal_id = ?",
        (json.dumps(payload), proposal_id),
    )
    return payload


def _final_settlement(profile: dict, frozen_at: str) -> dict:
    """The termination profile's figures, frozen at approval. Built from
    hr_agent.get_termination_profile only — no second calculation."""
    if profile.get("error"):
        return {"frozen_at": frozen_at, "error": profile["error"]}

    wage = profile["monthly_wage"]
    award = profile["end_of_service_award"]
    notice = profile["notice"]
    leave = profile["unused_leave"]
    components = {
        key: wage.get(key)
        for key in ("basic_salary_sar", "housing_allowance_sar", "transport_allowance_sar")
    }
    cited = [
        profile["service"],
        wage,
        award["full_award_law"],
        award["ratio_law"],
        notice,
        leave,
        profile["settlement"],
    ]
    articles = sorted(
        {(c["law_id"], c["article"]) for c in cited if c.get("law_id")},
        key=lambda item: item[0],
    )
    return {
        "frozen_at": frozen_at,
        "termination_type": profile.get("termination_type"),
        "hire_date": profile.get("hire_date"),
        "termination_date": profile.get("termination_date"),
        "service": {
            key: profile["service"].get(key)
            for key in ("years", "months", "days", "total_days", "law_id", "article")
        },
        "wage_basis": {
            "monthly_wage": wage.get("value"),
            "components": components,
            "included": [key for key, value in components.items() if value],
            "law_id": wage.get("law_id"),
            "article": wage.get("article"),
        },
        "end_of_service": {
            "full_award": award.get("full_award"),
            "full_award_law": award.get("full_award_law"),
            "fraction": award.get("ratio"),
            "fraction_law": award.get("ratio_law"),
            "amount": award.get("value"),
        },
        "unused_leave": {
            "days": leave.get("annual_remaining_days"),
            "daily_wage": leave.get("daily_wage"),
            "amount": leave.get("value"),
            "law_id": leave.get("law_id"),
            "article": leave.get("article"),
        },
        "notice": {
            key: notice.get(key)
            for key in (
                "required_days",
                "days_given",
                "notice_given_on",
                "shortfall_days",
                "payer",
                "adjustment",
                "waived",
                "waiver_note",
                "law_id",
                "article",
            )
        },
        "settlement_deadline": profile.get("settlement"),
        "total": (profile.get("total") or {}).get("value"),
        "articles": [{"law_id": law_id, "article": article} for law_id, article in articles],
        "disclaimer": profile.get("disclaimer"),
    }


def _ensure_employee_cvs_table(conn: sqlite3.Connection) -> None:
    """New additive table (TEAM.md Phase 4). Existing tables are untouched."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS employee_cvs (
            employee_id TEXT NOT NULL REFERENCES employees(employee_id),
            filename TEXT,
            cv_text TEXT NOT NULL,
            uploaded_at TEXT NOT NULL,
            source TEXT NOT NULL
        )
        """
    )


def _sync_new_hire(
    conn: sqlite3.Connection,
    proposal_id: str,
    *,
    status: str,
    decided_at: str,
    decided_by: str | None,
) -> None:
    """If an approved proposal is a new_hire, create the employees row,
    its login in users, its leave_balances row, and (when a CV was attached
    to the request) its employee_cvs row. Rejected: only the decision is
    recorded on the proposal."""
    proposal = get_proposed_action(conn, proposal_id)
    if not proposal or proposal.get("action_type") != "new_hire":
        return

    try:
        payload = json.loads(proposal.get("payload_json") or "{}")
    except (TypeError, ValueError):
        return
    payload = _record_decision(
        conn, proposal_id, payload, status=status, decided_at=decided_at, decided_by=decided_by
    )
    if status != "approved":
        return
    hire = payload.get("new_employee") or {}

    employee_id = _next_employee_id(conn)
    now = datetime.now(timezone.utc).isoformat()
    department = conn.execute(
        "SELECT location_city FROM departments WHERE department_id = ?",
        (hire.get("department_id"),),
    ).fetchone()
    work_city = department[0] if department else None
    nationality = hire.get("nationality")
    basic = hire.get("basic_salary")
    housing = hire.get("housing_allowance") or 0
    transport = hire.get("transport_allowance") or 0

    conn.execute(
        """
        INSERT INTO employees (
            employee_id, full_name, employee_name_en, gender, email, mobile,
            city, work_city, nationality, is_saudi, department_id,
            job_title, job_title_en, employment_status, employment_type,
            hire_date, manager_id, salary, basic_salary, basic_salary_sar,
            housing_allowance, housing_allowance_sar, transport_allowance,
            transport_allowance_sar, is_hr_approver, created_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Active', ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?
        )
        """,
        (
            employee_id,
            hire.get("full_name"),
            hire.get("full_name"),
            hire.get("gender"),
            hire.get("email"),
            hire.get("mobile"),
            work_city,
            work_city,
            nationality,
            None if not nationality else ("Yes" if nationality.lower() == "saudi" else "No"),
            hire.get("department_id"),
            hire.get("job_title"),
            hire.get("job_title"),
            hire.get("employment_type") or "Full-time",
            hire.get("hire_date"),
            hire.get("manager_id"),
            basic,
            basic,
            basic,
            housing,
            housing,
            transport,
            transport,
            now,
        ),
    )

    user_id = next_id(conn, "users", "user_id", "USR")
    conn.execute(
        """
        INSERT INTO users (
            user_id, employee_id, username, password_hash, role,
            is_active, created_at, last_login_at
        ) VALUES (?, ?, ?, ?, 'employee', 1, ?, NULL)
        """,
        (
            user_id,
            employee_id,
            employee_id,
            hash_password(_NEW_HIRE_DEFAULT_PASSWORD),
            now,
        ),
    )

    # Same placeholder entitlement rules as app/seed_leave_balances.py.
    as_of = date.today()
    years = _years_of_service(_parse_ddmmyyyy(hire.get("hire_date")), as_of)
    annual = ANNUAL_SENIOR_DAYS if years >= ANNUAL_SENIOR_YEARS else ANNUAL_BASE_DAYS
    conn.execute(
        """
        INSERT INTO leave_balances (
            employee_id, annual_entitlement, annual_used, annual_remaining,
            sick_entitlement, sick_used, sick_remaining,
            emergency_entitlement, emergency_used, emergency_remaining,
            as_of_date
        ) VALUES (?, ?, 0, ?, ?, 0, ?, ?, 0, ?, ?)
        """,
        (
            employee_id,
            annual,
            annual,
            SICK_ENTITLEMENT_DAYS,
            SICK_ENTITLEMENT_DAYS,
            EMERGENCY_ENTITLEMENT_DAYS,
            EMERGENCY_ENTITLEMENT_DAYS,
            as_of.strftime(DATE_FMT_ROWS),
        ),
    )

    if payload.get("cv_text"):
        _ensure_employee_cvs_table(conn)
        conn.execute(
            """
            INSERT INTO employee_cvs (employee_id, filename, cv_text, uploaded_at, source)
            VALUES (?, ?, ?, ?, 'onboarding')
            """,
            (
                employee_id,
                payload.get("cv_filename"),
                payload["cv_text"],
                payload.get("cv_uploaded_at") or now,
            ),
        )

    conn.execute(
        "UPDATE proposed_actions SET related_request_id = ? WHERE proposal_id = ?",
        (employee_id, proposal_id),
    )


def _sync_termination(
    conn: sqlite3.Connection,
    proposal_id: str,
    *,
    status: str,
    decided_at: str,
    decided_by: str | None,
) -> None:
    """If an approved proposal is a termination, freeze the final settlement
    into the proposal payload and set termination_date. The employee stays
    Active with an active login through the notice period (termination_date
    is the last working day); the final status and deactivation happen the
    day after (immediately for Art. 80, waived notice, or a past date). Never deletes rows, and does
    not reassign direct reports or pending requests. Rejected: only the
    decision is recorded on the proposal."""
    # Lazy import: the settlement comes from the same read-only function the
    # Decision Brief uses, so there is one calculation.
    from app.agents.hr_agent import get_termination_profile

    proposal = get_proposed_action(conn, proposal_id)
    if not proposal or proposal.get("action_type") != "termination":
        return

    try:
        payload = json.loads(proposal.get("payload_json") or "{}")
    except (TypeError, ValueError):
        return
    if status == "approved":
        payload["final_settlement"] = _final_settlement(
            get_termination_profile(conn, proposal), decided_at
        )
    payload = _record_decision(
        conn, proposal_id, payload, status=status, decided_at=decided_at, decided_by=decided_by
    )
    if status != "approved":
        return
    employee_id = payload.get("employee_id")
    if not employee_id:
        return

    termination_type = payload.get("termination_type")
    termination_date = payload.get("termination_date")
    if is_due(termination_type, termination_date, notice_waived=bool(payload.get("notice_waived"))):
        # Art. 80, waived notice, or a last working day already past: final now.
        apply_final_separation(conn, employee_id, termination_type, termination_date)
        payload["finalized_at"] = decided_at
        conn.execute(
            "UPDATE proposed_actions SET payload_json = ? WHERE proposal_id = ?",
            (json.dumps(payload), proposal_id),
        )
    else:
        # Notice period: still employed (Active, login active) until
        # termination_date; app/db/separations.finalize_due_separations
        # applies the final status then.
        conn.execute(
            "UPDATE employees SET termination_date = ? WHERE employee_id = ?",
            (termination_date, employee_id),
        )
    conn.execute(
        "UPDATE proposed_actions SET related_request_id = ? WHERE proposal_id = ?",
        (employee_id, proposal_id),
    )


def _sync_regulation_update(
    conn: sqlite3.Connection,
    proposal_id: str,
    *,
    status: str,
    decided_at: str,
    decided_by: str | None,
) -> None:
    """If the proposal is a regulation_update: on approval save old/new
    versions in regulation_versions and update the affected saudi_labor_law
    and company_policies text (re-indexing runs after commit, see
    regulation_agent.reindex_pending); otherwise record the decision only.
    Raises (and so rolls the decision back) if the approver edited the text
    (four-eyes) or simulated data would touch the real DB / collection."""
    from app.db.regulations import apply_regulation_decision, save_payload

    proposal = get_proposed_action(conn, proposal_id)
    if not proposal or proposal.get("action_type") != "regulation_update":
        return
    payload = apply_regulation_decision(
        conn, proposal, status=status, decided_at=decided_at, decided_by=decided_by
    )
    save_payload(conn, proposal_id, payload)
