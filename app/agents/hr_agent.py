import re

from app.agents.base import BaseAgent
from app.agents.leave_intent import (
    detects_leave_submission_intent,
    extract_leave_fields,
)

from app.db.connection import get_connection
from app.db.employees import find_cover_candidates, get_employee
from app.db.leave import get_leave_balance, list_leave_requests
from app.db.payroll import get_latest_payroll
from app.db.attendance import get_latest_attendance
from app.db.skills import get_department_experience_gap


_STAFF_ROLES = {"hr_specialist", "hr_manager", "admin"}

_EXPERIENCE_GAP_ROLES = {"hr_manager", "admin"}

_PROFILE_FIELDS = (
    "employee_id",
    "full_name",
    "job_title",
    "department_id",
    "department_name",
    "hire_date",
    "employment_status",
    "manager_id",
    "salary",
    "basic_salary",
    "mobile",
    "address",
    "city",
    "email",
    "bank_code",
    "bank_name",
    "iban",
    "bank_iban",
)


def _own_record_only(user: dict, employee_id: str) -> bool:
    """Employees may only read their own employee_id. HR/admin may read any."""

    role = (user or {}).get("role")
    user_eid = (user or {}).get("employee_id")

    if not employee_id or not role:
        return False

    if role in _STAFF_ROLES:
        return True

    if role == "employee":
        return bool(user_eid) and user_eid == employee_id

    return False


def _safe_profile(employee: dict) -> dict:
    """Return only approved HR profile fields."""

    return {
        key: employee[key]
        for key in _PROFILE_FIELDS
        if key in employee
    }


def _contains_any(query: str, words: tuple[str, ...]) -> bool:
    """Simple case-insensitive keyword matching."""

    query_lower = query.lower()
    return any(word in query_lower for word in words)


def _is_payroll_query(query: str) -> bool:
    return _contains_any(
        query,
        (
            "payroll",
            "payslip",
            "pay slip",
            "salary",
            "net pay",
            "gross pay",
            "basic salary",
            "allowance",
            "deduction",
        ),
    )


def _is_attendance_query(query: str) -> bool:
    return _contains_any(
        query,
        (
            "attendance",
            "absent",
            "absence",
            "late",
            "lateness",
            "present",
            "attendance rate",
        ),
    )


def _is_personal_info_update(query: str) -> bool:
    return (
        _contains_any(
            query,
            (
                "change",
                "update",
                "edit",
                "correct",
                "modify",
            ),
        )
        and _contains_any(
            query,
            (
                "phone",
                "mobile",
                "email",
                "address",
                "city",
            ),
        )
    )


def _extract_personal_info_field(query: str) -> str | None:
    """Identify which low-risk personal field is being changed."""

    query_lower = query.lower()

    if "phone" in query_lower or "mobile" in query_lower:
        return "mobile"

    if "email" in query_lower:
        return "email"

    if "address" in query_lower:
        return "address"

    if "city" in query_lower:
        return "city"

    return None


def _extract_personal_info_new_value(
    query: str,
    field_name: str,
) -> str | None:
    """Extract the new value from a personal-info update request."""

    if field_name == "mobile":
        match = re.search(
            r"(?:to|as)\s+(\+?\d[\d\s-]{7,})",
            query,
            re.IGNORECASE,
        )

        if match:
            return re.sub(r"[\s-]", "", match.group(1))

    if field_name == "email":
        match = re.search(
            r"(?:to|as)\s+([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})",
            query,
            re.IGNORECASE,
        )

        if match:
            return match.group(1)

    if field_name in {"address", "city"}:
        match = re.search(
            r"(?:to|as)\s+(.+)$",
            query,
            re.IGNORECASE,
        )

        if match:
            return match.group(1).strip()

    return None


def _is_bank_update(query: str) -> bool:
    return (
        _contains_any(
            query,
            (
                "iban",
                "bank account",
                "bank details",
                "bank information",
                "bank account number",
            ),
        )
        and _contains_any(
            query,
            (
                "change",
                "update",
                "edit",
                "modify",
                "replace",
            ),
        )
    )


def _extract_new_iban(query: str) -> str | None:
    """Extract a Saudi IBAN from a bank-change request."""

    cleaned_query = query.replace(" ", "").replace("-", "")

    match = re.search(
        r"SA\d{22}",
        cleaned_query,
        re.IGNORECASE,
    )

    if match:
        return match.group(0).upper()

    return None


def _is_certificate_request(query: str) -> bool:
    return _contains_any(
        query,
        (
            "certificate",
            "employment certificate",
            "employment letter",
            "salary certificate",
            "salary letter",
            "experience letter",
            "employment verification",
        ),
    )


def _is_experience_gap_query(query: str) -> bool:
    """Detect read-only workforce skill-gap questions."""

    return _contains_any(
        query,
        (
            "experience gap",
            "skill gap",
            "skills gap",
            "team skills",
            "skill coverage",
            "skills coverage",
            "department skills",
            "skill shortage",
            "skills shortage",
        ),
    )


def _first_value(
    record: dict,
    keys: tuple[str, ...],
) -> object | None:
    """Return the first available value from a list of possible column names."""

    for key in keys:
        value = record.get(key)

        if value is not None:
            return value

    return None


def get_historical_precedent(
    conn,
    employee_id: str,
    action_type: str,
) -> dict:
    """
    Return historical approved/denied precedent.

    Uses existing audit_log and leave_requests data only.
    Does not create or modify any records.
    """

    approved_count = 0
    denied_count = 0

    # -------------------------------------------------
    # Leave request precedent
    # -------------------------------------------------

    if action_type == "leave_request":
        rows = conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM leave_requests
            WHERE employee_id = ?
            GROUP BY status
            """,
            (employee_id,),
        ).fetchall()

        for row in rows:
            status = str(row["status"] or "").lower()
            count = int(row["count"] or 0)

            if status == "approved":
                approved_count += count

            elif status in {"rejected", "denied"}:
                denied_count += count

    # -------------------------------------------------
    # Audit log precedent
    # -------------------------------------------------

    audit_rows = conn.execute(
        """
        SELECT event_type, details
        FROM audit_log
        WHERE employee_id = ?
        ORDER BY timestamp DESC
        """,
        (employee_id,),
    ).fetchall()

    for row in audit_rows:
        event_type = str(
            row["event_type"] or ""
        ).lower()

        details = str(
            row["details"] or ""
        ).lower()

        combined = f"{event_type} {details}"

        if action_type == "leave_request":
            relevant = (
                "leave" in combined
                or "leave_request" in combined
            )

        elif action_type == "personal_info_update":
            relevant = (
                "personal" in combined
                or "profile" in combined
                or "personal_info_update" in combined
            )

        elif action_type == "bank_update":
            relevant = (
                "bank" in combined
                or "iban" in combined
                or "sensitive_change" in combined
            )

        elif action_type == "certificate_request":
            relevant = (
                "certificate" in combined
                or "employment_letter" in combined
            )

        else:
            relevant = False

        if not relevant:
            continue

        if "approved" in combined:
            approved_count += 1

        elif (
            "denied" in combined
            or "rejected" in combined
        ):
            denied_count += 1

    return {
        "employee_id": employee_id,
        "action_type": action_type,
        "approved_count": approved_count,
        "denied_count": denied_count,
        "total_count": approved_count + denied_count,
    }


class HRAgent(BaseAgent):

    def run(self, input: dict) -> dict:
        """
        HR / Execution Agent.

        Retrieves structured HR facts from the database.
        Does not make policy decisions or approvals.

        Output contract:
            {
                "facts": dict,
                "proposed_action": dict | None,
                "sources": list
            }
        """

        user = input.get("user") or {}
        query = str(input.get("query") or "")

        employee_id = str(
            input.get("employee_id")
            or user.get("employee_id")
            or ""
        ).strip()

        empty = {
            "facts": {},
            "proposed_action": None,
            "sources": [],
        }

        if not _own_record_only(user, employee_id):
            return empty

        conn = get_connection()

        # Initialize before any conditional branches.
        proposed_action = None
        leave_balance = None

        try:
            facts: dict = {
                "employee_id": employee_id
            }

            sources: list[str] = []

            # -------------------------------------------------
            # Employee profile
            # -------------------------------------------------

            employee = get_employee(
                conn,
                employee_id,
            )

            if employee:
                facts["profile"] = _safe_profile(employee)

                sources.append(
                    f"employees:{employee_id}"
                )

            # -------------------------------------------------
            # Leave balance
            # -------------------------------------------------

            leave_balance = get_leave_balance(
                conn,
                employee_id,
            )

            if leave_balance:
                facts["leave_balance"] = leave_balance

                annual_remaining = leave_balance.get(
                    "annual_remaining"
                )

                if annual_remaining is not None:
                    facts["annual_remaining"] = annual_remaining
                    facts["remaining_balance"] = annual_remaining

                sources.append(
                    f"leave_balances:{employee_id}"
                )

            # -------------------------------------------------
            # Previous leave requests
            # -------------------------------------------------

            requests = list_leave_requests(
                conn,
                employee_id,
            )

            if requests:
                facts["leave_requests"] = requests

                sources.append(
                    f"leave_requests:{employee_id}"
                )

            # -------------------------------------------------
            # Payroll query
            # -------------------------------------------------

            if _is_payroll_query(query):
                payroll = get_latest_payroll(
                    conn,
                    employee_id,
                )

                if payroll:
                    facts["payroll"] = payroll

                    sources.append(
                        f"payroll_monthly:{employee_id}"
                    )

            # -------------------------------------------------
            # Attendance query
            # -------------------------------------------------

            if _is_attendance_query(query):
                attendance = get_latest_attendance(
                    conn,
                    employee_id,
                )

                if attendance:
                    facts["attendance"] = attendance

                    sources.append(
                        f"attendance_leave_monthly:{employee_id}"
                    )

            # -------------------------------------------------
            # Experience Gap
            # -------------------------------------------------

            if _is_experience_gap_query(query):

                role = (user or {}).get("role")

                if role not in _EXPERIENCE_GAP_ROLES:
                    facts["request_assessment"] = {
                        "status": "NOT_AUTHORIZED",
                        "missing_information": [],
                        "notes": [
                            "Experience Gap Insight is available "
                            "to HR managers and admins only."
                        ],
                    }

                else:
                    department_id = (
                        input.get("department_id")
                        or (
                            facts.get("profile") or {}
                        ).get("department_id")
                    )

                    if not department_id:
                        facts["request_assessment"] = {
                            "status": "NEEDS_INFORMATION",
                            "missing_information": [
                                "department_id"
                            ],
                            "notes": [
                                "Provide a department to calculate "
                                "the experience gap."
                            ],
                        }

                    else:
                        gap = get_department_experience_gap(
                            conn,
                            department_id,
                        )

                        facts["experience_gap"] = {
                            "department_id": department_id,
                            "skills": gap,
                        }

                        sources.append(
                            f"department_requirements:{department_id}"
                        )

                        sources.append(
                            f"employee_skills:{department_id}"
                        )

            # -------------------------------------------------
            # Personal information update
            # -------------------------------------------------

            if _is_personal_info_update(query):

                field_name = _extract_personal_info_field(
                    query
                )

                if field_name is None:
                    facts["request_assessment"] = {
                        "status": "NEEDS_INFORMATION",
                        "missing_information": [
                            "field_name"
                        ],
                        "notes": [
                            "Specify whether you want to update "
                            "your mobile, email, address, or city."
                        ],
                    }

                else:
                    old_value = (
                        employee.get(field_name)
                        if employee
                        else None
                    )

                    new_value = _extract_personal_info_new_value(
                        query,
                        field_name,
                    )

                    if not new_value:
                        facts["request_assessment"] = {
                            "status": "NEEDS_INFORMATION",
                            "missing_information": [
                                "new_value"
                            ],
                            "notes": [
                                f"Provide the new value for "
                                f"{field_name}."
                            ],
                        }

                    else:
                        facts["request_assessment"] = {
                            "status": "READY_FOR_APPROVAL",
                            "missing_information": [],
                            "notes": [],
                        }

                        proposed_action = {
                            "action_type": "personal_info_update",
                            "payload": {
                                "employee_id": employee_id,
                                "field_name": field_name,
                                "old_value": old_value,
                                "new_value": new_value,
                            },
                        }

            # -------------------------------------------------
            # Bank / IBAN update
            # -------------------------------------------------

            if _is_bank_update(query):

                old_bank_code = _first_value(
                    employee or {},
                    (
                        "bank_code",
                        "old_bank_code",
                    ),
                )

                old_iban = _first_value(
                    employee or {},
                    (
                        "iban",
                        "bank_iban",
                        "old_iban",
                    ),
                )

                new_iban = _extract_new_iban(query)

                if not new_iban:
                    facts["request_assessment"] = {
                        "status": "NEEDS_INFORMATION",
                        "missing_information": [
                            "new_iban"
                        ],
                        "notes": [
                            "Provide a valid Saudi IBAN to prepare "
                            "the bank-change request."
                        ],
                    }

                    proposed_action = {
                        "action_type": "bank_update",
                        "payload": {
                            "employee_id": employee_id,
                            "change_type": "iban_update",
                            "old_bank_code": old_bank_code,
                            "old_iban": old_iban,
                            "new_bank_code": None,
                            "new_bank_name": None,
                            "new_iban": None,
                        },
                    }

                else:
                    facts["request_assessment"] = {
                        "status": "READY_FOR_APPROVAL",
                        "missing_information": [],
                        "notes": [],
                    }

                    proposed_action = {
                        "action_type": "bank_update",
                        "payload": {
                            "employee_id": employee_id,
                            "change_type": "iban_update",
                            "old_bank_code": old_bank_code,
                            "old_iban": old_iban,
                            "new_bank_code": None,
                            "new_bank_name": None,
                            "new_iban": new_iban,
                        },
                    }

            # -------------------------------------------------
            # Certificate request
            # -------------------------------------------------

            if _is_certificate_request(query):

                profile = facts.get("profile") or {}

                proposed_action = {
                    "action_type": "certificate_request",
                    "payload": {
                        "employee_id": employee_id,
                        "full_name": profile.get("full_name"),
                        "job_title": profile.get("job_title"),
                        "hire_date": profile.get("hire_date"),
                    },
                }

                facts["request_assessment"] = {
                    "status": "READY_FOR_APPROVAL",
                    "missing_information": [],
                    "notes": [],
                }

            # -------------------------------------------------
            # Leave submission
            # -------------------------------------------------

            if detects_leave_submission_intent(query):

                extracted = extract_leave_fields(query)

                missing = [
                    field
                    for field in (
                        "leave_type",
                        "start_date",
                        "end_date",
                    )
                    if not extracted.get(field)
                ]

                if missing:
                    facts["request_assessment"] = {
                        "status": "NEEDS_INFORMATION",
                        "missing_information": missing,
                        "notes": [
                            "Provide the missing leave details "
                            "(type, start date, end date) "
                            "to submit the request."
                        ],
                    }

                else:
                    leave_type = extracted["leave_type"]
                    start_date = extracted["start_date"]
                    end_date = extracted["end_date"]
                    days = extracted.get("days")

                    facts["requested_days"] = days

                    if leave_balance:
                        type_remaining = leave_balance.get(
                            f"{leave_type}_remaining"
                        )

                        if type_remaining is not None:
                            facts["remaining_balance"] = (
                                type_remaining
                            )

                    candidates = find_cover_candidates(
                        conn,
                        employee_id,
                        start_date,
                        end_date,
                    )

                    top_cover = (
                        candidates[0]
                        if candidates
                        else None
                    )

                    proposed_action = {
                        "action_type": "leave_request",
                        "payload": {
                            "employee_id": employee_id,
                            "leave_type": leave_type,
                            "start_date": start_date,
                            "end_date": end_date,
                            "days": days,
                            "reason": extracted.get("reason"),
                            "suggested_cover_employee_id": (
                                top_cover.get("employee_id")
                                if top_cover
                                else None
                            ),
                            "suggested_cover_employee_name": (
                                top_cover.get("full_name")
                                if top_cover
                                else None
                            ),
                            "cover_candidates": candidates,
                        },
                    }

        finally:
            conn.close()

        return {
            "facts": facts,
            "proposed_action": proposed_action,
            "sources": sources,
        }