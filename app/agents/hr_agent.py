import re
from calendar import monthrange
from datetime import datetime

from app.agents.base import BaseAgent
from app.agents.leave_intent import (
    detects_leave_submission_intent,
    extract_leave_fields,
)

from app.db.connection import get_connection
from app.db.employees import find_cover_candidates, get_employee
from app.db.leave import get_leave_balance, list_leave_requests
from app.db.payroll import get_latest_payroll, get_payroll_for_period
from app.db.attendance import get_attendance_for_period, get_latest_attendance
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


def _profile_fields_for_query(query: str) -> tuple[str, ...]:
    """Return only profile fields relevant to the query."""

    if _is_payroll_query(query):
        return (
            "salary",
            "basic_salary",
        )

    if _contains_any(
        query,
        (
            "iban",
            "bank",
            "bank account",
            "bank details",
            "bank information",
        ),
    ):
        return (
            "bank_code",
            "bank_name",
            "iban",
            "bank_iban",
        )

    if _contains_any(
        query,
        (
            "phone",
            "mobile",
            "email",
            "address",
            "city",
            "contact",
        ),
    ):
        return (
            "mobile",
            "email",
            "address",
            "city",
        )

    if _contains_any(
        query,
        (
            "job title",
            "position",
            "department",
            "hire date",
            "hired",
            "employment status",
            "manager",
        ),
    ):
        return (
            "job_title",
            "department_id",
            "department_name",
            "hire_date",
            "employment_status",
            "manager_id",
        )

    return (
        "employee_id",
        "full_name",
    )

def _contains_any(query: str, words: tuple[str, ...]) -> bool:
    """Case-insensitive whole-word matching, so "late" doesn't fire on
    "calculate" or "present" on "represent"."""

    query_lower = query.lower()
    return any(
        re.search(rf"\b{re.escape(word)}\b", query_lower)
        for word in words
    )


_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_SAUDI_MOBILE_RE = re.compile(r"(?:05|\+9665|009665|9665)\d{8}")


def _is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.fullmatch(value or ""))


def _is_valid_saudi_mobile(value: str) -> bool:
    return bool(_SAUDI_MOBILE_RE.fullmatch(value or ""))


def _is_valid_saudi_iban(value: str) -> bool:
    """SA + 22 digits with a valid ISO 13616 mod-97 checksum."""

    if not re.fullmatch(r"SA\d{22}", value or ""):
        return False

    rearranged = value[4:] + value[:4]
    numeric = "".join(
        str(int(ch, 36)) for ch in rearranged
    )
    return int(numeric) % 97 == 1


def _needs_information(
    action_type: str,
    missing: list[str],
    note: str,
) -> dict:
    return {
        "status": "NEEDS_INFORMATION",
        "action_type": action_type,
        "missing_information": missing,
        "notes": [note],
    }


def _is_leave_query(query: str) -> bool:
    return _contains_any(
        query,
        (
            "leave balance",
            "remaining leave",
            "leave days",
            "how many leave",
            "vacation balance",
            "annual leave",
            "sick leave",
            "emergency leave",
            "day off",
            "days off",
            "leave request",
            "leave history",
            "my leave",
        ),
    )


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


_MONTH_NAMES = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


def _extract_period(query: str) -> str | None:
    """Pull a "YYYY-MM" period out of a payroll/attendance question, e.g.
    "June 2026" or "2026-06". Returns None when no specific month was
    named, so the caller can fall back to the latest available period."""

    match = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])\b", query)

    if match:
        return f"{match.group(1)}-{match.group(2)}"

    month_pattern = "|".join(_MONTH_NAMES)

    match = re.search(
        rf"\b({month_pattern})\b\.?\s+(20\d{{2}})\b",
        query,
        re.IGNORECASE,
    )

    if match:
        month = _MONTH_NAMES[match.group(1).lower()]
        year = match.group(2)
        return f"{year}-{month:02d}"

    return None


def _date_to_period(value: str | None) -> str | None:
    """employees.hire_date/termination_date are stored DD-MM-YYYY;
    leave_requests dates are YYYY-MM-DD. Accept either, return "YYYY-MM"."""

    if not value:
        return None

    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m")
        except ValueError:
            continue

    return None


def _approved_leave_note(
    conn, employee_id: str, period: str
) -> str | None:
    """An approved leave overlapping `period`, if any — extra context on a
    missing-payroll answer, not claimed as the cause (payroll continues
    through leave in this dataset; pre-hire/post-termination is the actual
    cause of a missing row)."""

    try:
        year, month = (int(part) for part in period.split("-"))
        month_end = f"{period}-{monthrange(year, month)[1]:02d}"
    except (ValueError, TypeError):
        return None

    row = conn.execute(
        """
        SELECT leave_type, start_date, end_date FROM leave_requests
        WHERE employee_id = ? AND status = 'approved'
          AND start_date <= ? AND end_date >= ?
        ORDER BY start_date
        LIMIT 1
        """,
        (employee_id, month_end, f"{period}-01"),
    ).fetchone()

    if not row:
        return None

    return (
        f"Note: approved {row['leave_type']} leave "
        f"{row['start_date']} to {row['end_date']} overlaps this period."
    )


def _explain_missing_record(
    conn,
    employee: dict | None,
    employee_id: str,
    period: str | None,
    subject: str,
) -> str:
    """Say *why* a payroll/attendance lookup came back empty instead of
    just reporting the absence, and ask for a specific month when that's
    genuinely the missing piece — an interactive answer, not a dead end."""

    if period:
        base = f"No {subject} found for {period}."
    else:
        # No month was named and even the latest record is missing — the
        # default-to-latest path found nothing at all.
        base = f"No {subject} on file yet."
        period = datetime.now().strftime("%Y-%m")

    if employee:
        hire_period = _date_to_period(employee.get("hire_date"))

        if hire_period and period < hire_period:
            return (
                f"{base} {employee.get('full_name') or 'This employee'} "
                f"joined on {employee.get('hire_date')}, after this period. "
                "Ask about a month from your hire date onward."
            )

        term_period = _date_to_period(employee.get("termination_date"))

        if term_period and period > term_period:
            return (
                f"{base} Employment ended on "
                f"{employee.get('termination_date')}, before this period."
            )

    leave_note = _approved_leave_note(conn, employee_id, period)

    if leave_note:
        return f"{base} {leave_note}"

    topic = subject.replace(" record", "")
    return (
        f"{base} If you meant a specific month, name it "
        f'(e.g. "what is my {topic} for June 2026?").'
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
        # Anchor on the field word so "I want to update my address to X"
        # captures X, not "update my address to X".
        match = re.search(
            rf"\b{field_name}\b.*?\b(?:to|as)\s+(.+)$",
            query,
            re.IGNORECASE,
        )

        if match:
            return match.group(1).strip().rstrip(".")

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

def _detect_write_actions(query: str) -> list[str]:
    """Detect write actions requested in the same query."""

    actions: list[str] = []

    if _is_personal_info_update(query):
        actions.append("personal_info_update")

    if _is_bank_update(query):
        actions.append("bank_update")

    if _is_certificate_request(query):
        actions.append("certificate_request")

    if detects_leave_submission_intent(query):
        actions.append("leave_request")

    return actions

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
            SELECT p.status, COUNT(*) AS count
            FROM proposed_actions pa
            JOIN pending_approvals p
                ON p.proposal_id = pa.proposal_id
            WHERE pa.employee_id = ?
            AND pa.action_type = 'leave_request'
            AND p.status IN ('approved', 'rejected')
            GROUP BY p.status
            """,
            (employee_id,),
        ).fetchall()

        for row in rows:
            status = str(row["status"] or "").lower()
            count = int(row["count"] or 0)

            if status == "approved":
                approved_count += count

            elif status == "rejected":
                denied_count += count

    # -------------------------------------------------
    # Audit log precedent
    # -------------------------------------------------

    if action_type == "leave_request":
        return {
            "employee_id": employee_id,
            "action_type": action_type,
            "approved_count": approved_count,
            "denied_count": denied_count,
            "total_count": approved_count + denied_count,
        }
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
        write_actions = _detect_write_actions(query)

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

        if len(write_actions) > 1:
            return {
                "facts": {
                    "employee_id": employee_id,
                    "request_assessment": {
                        "status": "NEEDS_INFORMATION",
                        "action_type": "multiple_actions",
                        "missing_information": [],
                        "notes": [
                            "Multiple write actions were detected. "
                            "Please submit one action at a time."
                        ],
                    },
                },
                "proposed_action": None,
                "sources": [],
            }
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
                profile_fields = _profile_fields_for_query(query)

                facts["profile"] = {
                    key: employee[key]
                    for key in profile_fields
                    if key in employee
                }

                sources.append(
                    f"employees:{employee_id}"
                )

            # -------------------------------------------------
            # Leave balance
            # -------------------------------------------------

            # `leave_balance` itself is always fetched (cheap, single row) since
            # the leave-submission branch below needs it regardless of query
            # wording. Whether it's exposed in `facts` — and therefore shown
            # by Manager's fallback summary — is gated on the query actually
            # being about leave, so an unrelated question (e.g. payroll)
            # doesn't always drag leave balance into the answer.
            leave_balance = get_leave_balance(
                conn,
                employee_id,
            )

            if leave_balance and _is_leave_query(query):
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

            if _is_leave_query(query):
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
            # Payroll query — own record only (enforced above by
            # _own_record_only, same as every other fact here). A named
            # month ("June 2026") is looked up exactly; otherwise this
            # falls back to the latest period on file. Bulk, all-employee
            # payroll is a separate HR-manager-only PDF export, not chat.
            # -------------------------------------------------

            # "salary certificate" is a certificate request, not a payroll
            # lookup.
            if _is_payroll_query(query) and not _is_certificate_request(query):
                period = _extract_period(query)

                if period:
                    payroll = get_payroll_for_period(
                        conn, employee_id, period
                    )
                else:
                    payroll = get_latest_payroll(conn, employee_id)

                if payroll:
                    facts["payroll"] = payroll

                    sources.append(
                        f"payroll_monthly:{employee_id}"
                    )
                else:
                    facts["payroll_notice"] = _explain_missing_record(
                        conn, employee, employee_id, period, "payroll record"
                    )

            # -------------------------------------------------
            # Attendance query — same shape as payroll: a named month is
            # looked up exactly, otherwise this falls back to the latest
            # period on file, and either way a miss gets a real reason
            # instead of silence.
            # -------------------------------------------------

            if _is_attendance_query(query):
                period = _extract_period(query)

                if period:
                    attendance = get_attendance_for_period(
                        conn, employee_id, period
                    )
                else:
                    attendance = get_latest_attendance(
                        conn, employee_id
                    )

                if attendance:
                    facts["attendance"] = attendance

                    sources.append(
                        f"attendance_leave_monthly:{employee_id}"
                    )
                else:
                    facts["attendance_notice"] = _explain_missing_record(
                        conn, employee, employee_id, period, "attendance record"
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

                        for item in gap:
                            item.pop("_candidate_employees", None)

                        facts["experience_gap"] = {
                            "department_id": department_id,
                            "skills": gap,
                        }

                        sources.append(
                            f"department_requirements:{department_id}"
                        )

                        sources.append(
                            f"skill_job_titles:{department_id}"
                        )

            # -------------------------------------------------
            # Personal information update
            # -------------------------------------------------

            if _is_personal_info_update(query):

                field_name = _extract_personal_info_field(
                    query
                )

                field_label = {
                    "mobile": "mobile number",
                    "email": "email address",
                    "address": "address",
                    "city": "city",
                }.get(field_name or "", "field")

                new_value = (
                    _extract_personal_info_new_value(query, field_name)
                    if field_name
                    else None
                )

                if field_name is None:
                    facts["request_assessment"] = _needs_information(
                        "personal_info_update",
                        ["field_name"],
                        "Specify whether you want to update "
                        "your mobile, email, address, or city.",
                    )

                elif not new_value:
                    facts["request_assessment"] = _needs_information(
                        "personal_info_update",
                        ["new_value"],
                        f"Please include your new {field_label} "
                        f'(e.g. "update my {field_label} to ...").',
                    )

                elif field_name == "email" and not _is_valid_email(new_value):
                    facts["request_assessment"] = _needs_information(
                        "personal_info_update",
                        ["new_value"],
                        f'"{new_value}" is not a valid email address.',
                    )

                elif field_name == "mobile" and not _is_valid_saudi_mobile(new_value):
                    facts["request_assessment"] = _needs_information(
                        "personal_info_update",
                        ["new_value"],
                        "That is not a valid Saudi mobile number. "
                        "Use the format 05XXXXXXXX or +9665XXXXXXXX.",
                    )

                else:
                    old_value = (
                        employee.get(field_name)
                        if employee
                        else None
                    )

                    facts["request_assessment"] = {
                        "status": "READY_FOR_APPROVAL",
                        "action_type": "personal_info_update",
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

                # No proposed_action until we have a real IBAN — an
                # incomplete one used to be submitted as "None" -> "None".
                if not new_iban:
                    facts["request_assessment"] = _needs_information(
                        "bank_update",
                        ["new_iban"],
                        "Please include your new Saudi IBAN "
                        "(SA followed by 22 digits) to prepare "
                        "the bank-change request.",
                    )

                elif not _is_valid_saudi_iban(new_iban):
                    facts["request_assessment"] = _needs_information(
                        "bank_update",
                        ["new_iban"],
                        "That IBAN fails the checksum. Please "
                        "double-check it and send it again.",
                    )

                else:
                    facts["request_assessment"] = {
                        "status": "READY_FOR_APPROVAL",
                        "action_type": "bank_update",
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
                    "action_type": "certificate_request",
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
                    facts["request_assessment"] = _needs_information(
                        "leave_request",
                        missing,
                        "Provide the missing leave details "
                        "(type, start date, end date) "
                        "to submit the request.",
                    )

                else:
                    leave_type = extracted["leave_type"]
                    start_date = extracted["start_date"]
                    end_date = extracted["end_date"]
                    days = extracted.get("days")

                    facts["requested_days"] = days

                    # -------------------------------------------------
                    # Leave request sanity checks
                    # -------------------------------------------------

                    request_notes = []

                    try:
                        start = datetime.strptime(start_date, "%Y-%m-%d").date()
                        end = datetime.strptime(end_date, "%Y-%m-%d").date()
                        today = datetime.now().date()

                        if start < today:
                            request_notes.append(
                                f"Requested leave starts in the past: {start_date}."
                            )

                        if end < start:
                            request_notes.append(
                                f"Leave end date {end_date} is before start date {start_date}."
                            )

                        existing_requests = list_leave_requests(
                            conn,
                            employee_id,
                        )

                        for existing in existing_requests:
                            existing_status = str(
                                existing.get("status") or ""
                            ).lower()

                            if existing_status == "rejected":
                                continue

                            existing_start = existing.get("start_date")
                            existing_end = existing.get("end_date")

                            if not existing_start or not existing_end:
                                continue

                            try:
                                existing_start_date = datetime.strptime(
                                    existing_start,
                                    "%Y-%m-%d",
                                ).date()
                                existing_end_date = datetime.strptime(
                                    existing_end,
                                    "%Y-%m-%d",
                                ).date()
                            except ValueError:
                                continue

                            if start <= existing_end_date and end >= existing_start_date:
                                request_notes.append(
                                    "Requested leave overlaps an existing "
                                    f"{existing_status or 'leave'} request "
                                    f"from {existing_start} to {existing_end}."
                                )

                    except (TypeError, ValueError):
                        request_notes.append(
                            "Leave dates could not be validated."
                        )

                    facts["request_assessment"] = {
                        "status": "READY_FOR_APPROVAL",
                        "action_type": "leave_request",
                        "missing_information": [],
                        "notes": request_notes,
                    }

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

        # Never hand Manager an action while something is still missing
        # (e.g. a certificate matched earlier, then a leave request in the
        # same message lacked dates).
        assessment = facts.get("request_assessment") or {}
        if assessment.get("status") in {"NEEDS_INFORMATION", "NOT_AUTHORIZED"}:
            proposed_action = None

        return {
            "facts": facts,
            "proposed_action": proposed_action,
            "sources": sources,
        }