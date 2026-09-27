import json
import re
from calendar import monthrange
from datetime import date, datetime, timedelta
from statistics import median

from app.agents.base import BaseAgent
from app.agents.separation_review import review_reason
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


# =========================================================
# Phase 4: new hire / termination (HR staff only)
# =========================================================
#
# Both are HIGH-risk proposed_actions: nothing is written here. The
# employee/users/leave_balances rows are only created or updated by
# `_sync_new_hire` / `_sync_termination` in app/db/approvals.py after an
# HR manager approves.

TERMINATION_TYPES = (
    "termination_by_employer",
    "article_80",
    "resignation",
    "end_of_contract",
    "mutual_agreement",
    "retirement",
    "force_majeure",
)

_EMPLOYMENT_TYPES = ("Full-time", "Contract", "Part-time", "Temporary")

_NEW_HIRE_FIELDS = (
    "full_name",
    "gender",
    "nationality",
    "email",
    "mobile",
    "department_id",
    "job_title",
    "job_grade",
    "manager_id",
    "employment_type",
    "hire_date",
    "basic_salary",
    "housing_allowance",
    "transport_allowance",
)

_NEW_HIRE_REQUIRED = (
    "full_name",
    "department_id",
    "job_title",
    "job_grade",
    "basic_salary",
    "hire_date",
)

# Article 80 procedure kind -> the "key: value" field that confirms it.
# Which kinds a ground needs is read from the LAW075 text, not listed here.
_ART80_PROCEDURE_FIELDS = {
    "objection": "article_80_objection_date",
    "written_warning": "article_80_warning_date",
    "authority_report": "article_80_report_date",
    "confirmation": "article_80_confirmed",
}

_TERMINATION_FIELDS = (
    "employee_id",
    "termination_type",
    "termination_date",
    "reason",
    "notice_waived",
    "notice_waiver_note",
    "article_80_ground",
    "article_80_details",
    *_ART80_PROCEDURE_FIELDS.values(),
)

# --- New-hire pay checks (Saudi private sector) ---
# A Saudi whose wage (basic + housing, the GOSI-registered wage) is below
# this counts as 0.5 in Nitaqat (Saudization).
NITAQAT_FULL_COUNT_MIN_WAGE_SAR = 4000
# Hard floor for a basic salary: this share of the lowest basic salary of
# any active employee. Catches typos like "1"; it is our own sanity floor,
# not a legal minimum wage.
BASIC_SALARY_FLOOR_SHARE = 0.5
# An allowance is "far" from typical when its share of basic differs from
# the typical share by more than this fraction of the typical share.
ALLOWANCE_RATIO_TOLERANCE = 0.4
# Grades with fewer active employees use company-wide allowance ratios.
_MIN_GRADE_SAMPLE = 3

# --- End-of-service reason checks ---
REASON_MIN_CHARS = 20
_REASON_MIN_WORDS = 3

# Structured "key: value" pairs separated by ";" or new lines — the shape
# the Ask Yusor forms send. Only known keys are read, so a free-text
# reason containing a colon can't inject another field.
_STAFFING_FIELD_RE = re.compile(
    r"\b("
    + "|".join(
        sorted(set(_NEW_HIRE_FIELDS + _TERMINATION_FIELDS), key=len, reverse=True)
    )
    + r")\s*:\s*([^;\n]*)",
    re.IGNORECASE,
)

_EMPLOYEE_ID_RE = re.compile(r"\bEMP-\d{4}\b", re.IGNORECASE)

# (law_id, article) for every figure in the termination profile.
_LAW_END_CASES = ("LAW071", "74")
_LAW_NOTICE = ("LAW072", "75")
_LAW_NOTICE_COMPENSATION = ("LAW073", "76")
_LAW_ILLEGITIMATE_TERMINATION = ("LAW074", "77")
_LAW_ARTICLE_80 = ("LAW075", "80")
_LAW_AWARD = ("LAW077", "84")
_LAW_RESIGNATION_AWARD = ("LAW078", "85")
_LAW_FULL_AWARD = ("LAW079", "87")
_LAW_SETTLEMENT = ("LAW080", "88")
_LAW_UNUSED_LEAVE = ("LAW046", "111")

# Article 75 (amended 2025) for a monthly-paid worker, by who ends the
# contract. Types not listed have no Article 75 notice period.
_NOTICE_DAYS = {
    "termination_by_employer": (60, "employer"),
    "resignation": (30, "employee"),
}

TERMINATION_DISCLAIMER = "Indicative estimate, not legal advice."


def _is_new_hire(query: str) -> bool:
    return _contains_any(
        query,
        (
            "hire new employee",
            "hire a new employee",
            "add new employee",
            "add a new employee",
            "onboard new employee",
            "onboard a new employee",
        ),
    )


def _is_termination(query: str) -> bool:
    return _contains_any(
        query,
        (
            "terminate employee",
            "terminate the employment of",
            "terminate the service of",
            "end the employment of",
            "end the service of",
            "end of service for employee",
        ),
    )


def _parse_staffing_fields(query: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key, value in _STAFFING_FIELD_RE.findall(query or ""):
        value = value.strip()
        if value:
            fields.setdefault(key.lower(), value)
    return fields


def _parse_day(value: str | None) -> date | None:
    """DD-MM-YYYY (the employees table format), DD/MM/YYYY or YYYY-MM-DD."""

    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


def _format_day(value: date) -> str:
    return value.strftime("%d-%m-%Y")


def _signed_amount(value: str | None) -> float | None:
    text = re.sub(r"(?i)sar|,", "", str(value or "")).strip()
    try:
        return float(text)
    except ValueError:
        return None


def _to_amount(value: str | None) -> float | None:
    amount = _signed_amount(value)
    return amount if amount is not None and amount >= 0 else None


# ---------------------------------------------------------------------------
# New-hire pay checks. Blocks stop the request; warnings ride in the
# proposal payload for the approver. The company scale comes from active
# employees in the DB, so it follows the data instead of hardcoded figures.
# ---------------------------------------------------------------------------


def _median_ratio(rows: list[tuple[float, float, float]], index: int) -> float:
    return round(median(row[index] / row[0] for row in rows), 3)


def get_salary_scale(conn) -> dict:
    """Read-only company salary scale: min/max basic per job_grade and the
    typical housing/transport share of basic, from active employees."""

    rows = conn.execute(
        """
        SELECT job_grade, basic_salary_sar,
               COALESCE(housing_allowance_sar, 0), COALESCE(transport_allowance_sar, 0)
        FROM employees
        WHERE employment_status = 'Active' AND basic_salary_sar > 0
        """
    ).fetchall()
    everyone = [(float(r[1]), float(r[2]), float(r[3])) for r in rows]
    by_grade: dict[str, list[tuple[float, float, float]]] = {}
    for grade, basic, housing, transport in rows:
        if grade:
            by_grade.setdefault(str(grade), []).append((float(basic), float(housing), float(transport)))

    grades = {}
    for grade in sorted(by_grade):
        items = by_grade[grade]
        sample = items if len(items) >= _MIN_GRADE_SAMPLE else everyone
        grades[grade] = {
            "min_basic": min(row[0] for row in items),
            "max_basic": max(row[0] for row in items),
            "employees": len(items),
            "housing_ratio": _median_ratio(sample, 1),
            "transport_ratio": _median_ratio(sample, 2),
            "ratio_basis": "grade" if sample is items else "company",
        }

    lowest = min((row[0] for row in everyone), default=0.0)
    return {
        "grades": grades,
        "lowest_basic": lowest,
        "basic_floor": round(lowest * BASIC_SALARY_FLOOR_SHARE, 2),
        "basic_floor_share": BASIC_SALARY_FLOOR_SHARE,
        "nitaqat_min_wage": NITAQAT_FULL_COUNT_MIN_WAGE_SAR,
        "allowance_tolerance": ALLOWANCE_RATIO_TOLERANCE,
        "source": "employees (active)",
    }


def _is_saudi(nationality: str | None) -> bool:
    # Same test _sync_new_hire uses to fill employees.is_saudi.
    return str(nationality or "").strip().lower() == "saudi"


def check_new_hire_pay(
    conn,
    *,
    job_grade: str | None,
    nationality: str | None,
    basic_salary,
    housing_allowance=0,
    transport_allowance=0,
    scale: dict | None = None,
) -> dict:
    """Read-only pay checks for a new hire.

    Returns {"problems": [...], "warnings": [...]}: problems block the
    request, warnings go to the approver. Each warning carries a `code`,
    an English `text` and the figures the UI needs to translate it.
    """

    scale = scale or get_salary_scale(conn)
    problems: list[str] = []
    warnings: list[dict] = []

    basic = _signed_amount(basic_salary) if isinstance(basic_salary, str) else basic_salary
    amounts = {}
    for name, value in (("housing_allowance", housing_allowance), ("transport_allowance", transport_allowance)):
        amount = _signed_amount(value) if isinstance(value, str) else value
        if amount is None:
            problems.append(f"{name} must be an amount in SAR (0 or more).")
        elif amount < 0:
            problems.append(f"{name} cannot be negative.")
        amounts[name] = amount

    if basic is None or basic <= 0:
        problems.append("basic_salary must be a positive amount in SAR.")
    elif basic < scale["basic_floor"]:
        problems.append(
            f"basic_salary {basic:,.2f} SAR is too low: the minimum accepted is "
            f"{scale['basic_floor']:,.2f} SAR ({scale['basic_floor_share']:g} × the lowest "
            f"basic salary of any active employee, {scale['lowest_basic']:,.2f} SAR). "
            "Check the amount."
        )

    grade = str(job_grade or "").strip().upper()
    band = scale["grades"].get(grade)
    if not grade:
        problems.append("job_grade is required.")
    elif band is None:
        problems.append("job_grade must be one of: " + ", ".join(scale["grades"]) + ".")

    if problems:
        return {"problems": problems, "warnings": warnings}

    housing = amounts["housing_allowance"]
    transport = amounts["transport_allowance"]

    wage = basic + housing
    if _is_saudi(nationality) and wage < scale["nitaqat_min_wage"]:
        warnings.append(
            {
                "code": "nitaqat_half",
                "text": (
                    f"Saudi employee with a wage (basic + housing) of {wage:,.2f} SAR, "
                    f"below {scale['nitaqat_min_wage']:,} SAR: counts as 0.5 in Nitaqat "
                    "(Saudization)."
                ),
                "wage": wage,
                "threshold": scale["nitaqat_min_wage"],
            }
        )

    if not band["min_basic"] <= basic <= band["max_basic"]:
        warnings.append(
            {
                "code": "grade_range",
                "text": (
                    f"Basic salary {basic:,.2f} SAR is outside the company range for "
                    f"grade {grade}: {band['min_basic']:,.2f}–{band['max_basic']:,.2f} SAR "
                    f"(from {band['employees']} active employees)."
                ),
                "grade": grade,
                "basic": basic,
                "min": band["min_basic"],
                "max": band["max_basic"],
                "employees": band["employees"],
            }
        )

    for code, name, amount in (
        ("housing_ratio", "Housing allowance", housing),
        ("transport_ratio", "Transport allowance", transport),
    ):
        typical = band[code]
        ratio = amount / basic
        if typical and abs(ratio - typical) > typical * scale["allowance_tolerance"]:
            basis = f"grade {grade}" if band["ratio_basis"] == "grade" else "all active employees"
            warnings.append(
                {
                    "code": code,
                    "text": (
                        f"{name} is {ratio:.0%} of basic; typical for {basis} is "
                        f"{typical:.0%} ({basic * typical:,.2f} SAR)."
                    ),
                    "grade": grade,
                    "ratio": round(ratio, 3),
                    "typical": typical,
                    "typical_amount": round(basic * typical, 2),
                    "basis": band["ratio_basis"],
                }
            )

    return {"problems": problems, "warnings": warnings}


# ---------------------------------------------------------------------------
# End-of-service reason and Article 80 checks.
# ---------------------------------------------------------------------------

_LETTERS_RE = re.compile(r"[^\W\d_]+")
# A "word" that is one short chunk repeated: "aaaa", "haha", "asdfasdf".
_REPEATED_CHUNK_RE = re.compile(r"(.{1,4}?)\1+")
_VOWEL_RE = re.compile(r"[aeiouy]")


def _is_real_word(word: str) -> bool:
    word = word.lower()
    if len(word) < 2 or _REPEATED_CHUNK_RE.fullmatch(word):
        return False
    # Latin words need a vowel; other scripts (Arabic) pass on length.
    return not word.isascii() or bool(_VOWEL_RE.search(word))


def check_reason_text(label: str, text: str | None, min_chars: int = REASON_MIN_CHARS) -> list[str]:
    """Hard blocks for a free-text reason: too short, or no real words
    (only symbols, digits, repeated letters or the same word over again)."""

    text = str(text or "").strip()
    if len(text) < min_chars:
        return [
            f"{label} must be at least {min_chars} characters (it has {len(text)}). "
            "Describe what happened."
        ]
    words = {w.lower() for w in _LETTERS_RE.findall(text) if _is_real_word(w)}
    if len(words) < _REASON_MIN_WORDS:
        return [
            f"{label} must be written in real words (at least {_REASON_MIN_WORDS} "
            "different ones), not symbols or repeated letters."
        ]
    return []


_ART80_ITEM_RE = re.compile(r"\((\d+)\)\s*(.*?)(?=\s*\(\d+\)|$)", re.DOTALL)
_SENTENCE_RE = re.compile(r"[^.]+(?:\.|$)")


def _procedure_kind(text: str) -> str | None:
    lower = text.lower()
    if "written warning" in lower:
        return "written_warning"
    if "report" in lower and "authorit" in lower:
        return "authority_report"
    if "objection" in lower:
        return "objection"
    return None


def get_article_80_grounds(conn) -> dict | None:
    """Read-only: the Article 80 grounds and the procedural conditions
    each one needs, parsed from the current LAW075 row (so an approved
    regulation_update to that row is picked up). None if the row is gone."""

    row = conn.execute(
        """
        SELECT id, article, title, rule, conditions, exceptions
        FROM saudi_labor_law WHERE id = ? AND article = ?
        """,
        _LAW_ARTICLE_80,
    ).fetchone()
    if row is None:
        return None

    # Conditions apply to every ground; each sentence is one confirmation.
    general = []
    for sentence in _SENTENCE_RE.findall(row["conditions"] or ""):
        sentence = sentence.strip()
        if sentence and sentence != "-":
            general.append({"kind": _procedure_kind(sentence) or "confirmation", "text": sentence})

    # Exceptions like "For absence, dismissal must be preceded by a written
    # warning" apply to the ground that mentions that subject.
    scoped = []
    for sentence in _SENTENCE_RE.findall(row["exceptions"] or ""):
        match = re.match(r"\s*For ([^,]+),", sentence)
        kind = _procedure_kind(sentence)
        if match and kind:
            scoped.append((match.group(1).strip().lower()[:5], kind, sentence.strip()))

    grounds = []
    for number, text in _ART80_ITEM_RE.findall(row["rule"] or ""):
        text = re.sub(r"[;.,\s]+$", "", text.strip())
        text = re.sub(r"\s*;?\s*(?:and|or)$", "", text)
        requirements = []
        kind = _procedure_kind(text)
        if kind:
            requirements.append({"kind": kind, "text": text})
        for stem, scoped_kind, sentence in scoped:
            if stem in text.lower() and scoped_kind not in {r["kind"] for r in requirements}:
                requirements.append({"kind": scoped_kind, "text": sentence})
        requirements.extend(general)
        for requirement in requirements:
            requirement["field"] = _ART80_PROCEDURE_FIELDS[requirement["kind"]]
            requirement["needs_date"] = requirement["kind"] != "confirmation"
        grounds.append({"number": int(number), "text": text, "requirements": requirements})

    return {
        "law_id": row["id"],
        "article": row["article"],
        "title": row["title"],
        "grounds": grounds,
    }


def _article_80_problems(conn, fields: dict, hire_date: date | None, today: date) -> tuple[list[str], dict | None]:
    """Validate the Article 80 ground, details and procedures. Returns
    (problems, the payload's article_80 block)."""

    article = get_article_80_grounds(conn)
    if not article or not article["grounds"]:
        return ["The Article 80 text (LAW075) could not be read, so this request can't be checked."], None

    listing = "; ".join(f"({g['number']}) {g['text']}" for g in article["grounds"])
    raw_ground = str(fields.get("article_80_ground") or "").strip()
    number_match = re.match(r"\(?(\d+)\)?", raw_ground)
    ground = None
    if number_match:
        ground = next((g for g in article["grounds"] if g["number"] == int(number_match.group(1))), None)
    if ground is None:
        return [
            "article_80_ground is required: give the number of the Article 80 ground "
            f"({article['law_id']}) — {listing}."
        ], None

    details = fields.get("article_80_details") or fields.get("reason")
    problems = check_reason_text("article_80_details", details)

    procedures = []
    for requirement in ground["requirements"]:
        value = str(fields.get(requirement["field"]) or "").strip()
        if not requirement["needs_date"]:
            if value.lower() not in {"yes", "true", "1"}:
                problems.append(f"{requirement['field']}: yes is required — {requirement['text']}")
            else:
                procedures.append({"kind": requirement["kind"], "text": requirement["text"], "confirmed": True})
            continue
        when = _parse_day(value)
        if when is None:
            problems.append(
                f"{requirement['field']} (DD-MM-YYYY) is required to confirm: {requirement['text']}"
            )
        elif when > today:
            problems.append(f"{requirement['field']} cannot be in the future.")
        elif hire_date and when < hire_date:
            problems.append(f"{requirement['field']} cannot be before the employee's hire date.")
        else:
            procedures.append(
                {"kind": requirement["kind"], "text": requirement["text"], "confirmed": True, "date": _format_day(when)}
            )

    block = {
        "law_id": article["law_id"],
        "article": article["article"],
        "ground_number": ground["number"],
        "ground_text": ground["text"],
        "details": str(details or "").strip(),
        "procedures": procedures,
    }
    return problems, block


def _open_termination(conn, employee_id: str) -> dict | None:
    """The employee's termination that is still in progress: pending
    approval, or approved but not yet finalized (notice period)."""
    rows = conn.execute(
        """
        SELECT proposal_id, status, payload_json FROM proposed_actions
        WHERE action_type = 'termination' AND status IN ('pending_approval', 'approved')
        ORDER BY created_at DESC
        """
    ).fetchall()
    for proposal_id, status, payload_json in rows:
        try:
            payload = json.loads(payload_json or "{}")
        except (TypeError, ValueError):
            continue
        if payload.get("employee_id") != employee_id or payload.get("finalized_at"):
            continue
        return {
            "proposal_id": proposal_id,
            "status": status,
            "termination_date": payload.get("termination_date"),
        }
    return None


def _staffing_result(
    requester_id: str,
    action_type: str,
    status: str,
    notes: list[str],
    sources: list[str],
    *,
    missing: list[str] | None = None,
    proposed_action: dict | None = None,
) -> dict:
    return {
        "facts": {
            "employee_id": requester_id,
            "request_assessment": {
                "status": status,
                "action_type": action_type,
                "missing_information": missing or [],
                "notes": notes,
            },
        },
        "proposed_action": proposed_action,
        "sources": sources,
    }


def _new_hire_request(conn, requester_id: str, query: str) -> dict:
    fields = _parse_staffing_fields(query)
    sources = [f"employees:{requester_id}"]

    missing = [field for field in _NEW_HIRE_REQUIRED if not fields.get(field)]
    if missing:
        return _staffing_result(
            requester_id,
            "new_hire",
            "NEEDS_INFORMATION",
            [
                "To add a new employee, provide "
                + ", ".join(missing)
                + ' as "key: value" pairs separated by semicolons.'
            ],
            sources,
            missing=missing,
        )

    problems: list[str] = []

    department_id = fields["department_id"].upper()
    department = conn.execute(
        "SELECT department_id, department_name FROM departments WHERE department_id = ?",
        (department_id,),
    ).fetchone()
    if department is None:
        problems.append(f"Department {department_id} does not exist.")

    job_grade = fields["job_grade"].strip().upper()
    pay = check_new_hire_pay(
        conn,
        job_grade=job_grade,
        nationality=fields.get("nationality"),
        basic_salary=fields["basic_salary"],
        housing_allowance=fields.get("housing_allowance") or "0",
        transport_allowance=fields.get("transport_allowance") or "0",
    )
    problems.extend(pay["problems"])
    basic_salary = _to_amount(fields["basic_salary"])
    housing = _to_amount(fields.get("housing_allowance") or "0")
    transport = _to_amount(fields.get("transport_allowance") or "0")

    hire_date = _parse_day(fields["hire_date"])
    if hire_date is None:
        problems.append("hire_date must be a date in DD-MM-YYYY format.")

    manager_id = (fields.get("manager_id") or "").upper() or None
    manager_notice = None
    if manager_id:
        manager = get_employee(conn, manager_id)
        if not manager or manager.get("employment_status") != "Active":
            problems.append(f"Manager {manager_id} is not an active employee.")
        else:
            leaving = _open_termination(conn, manager_id)
            if leaving and leaving["status"] == "approved":
                # Warning only: the hire can still go ahead.
                manager_notice = (
                    f"Manager {manager_id} is in their notice period until "
                    f"{leaving['termination_date']}."
                )

    email = fields.get("email")
    if email:
        if not _is_valid_email(email):
            problems.append("email is not a valid email address.")
        elif conn.execute(
            "SELECT 1 FROM employees WHERE LOWER(email) = LOWER(?)",
            (email,),
        ).fetchone():
            problems.append("That email is already used by another employee.")

    mobile = fields.get("mobile")
    if mobile and not _is_valid_saudi_mobile(mobile):
        problems.append("mobile must be a Saudi mobile number (05XXXXXXXX).")

    gender = (fields.get("gender") or "").title() or None
    if gender and gender not in ("Male", "Female"):
        problems.append("gender must be Male or Female.")

    employment_type = "Full-time"
    if fields.get("employment_type"):
        employment_type = next(
            (
                t
                for t in _EMPLOYMENT_TYPES
                if t.lower() == fields["employment_type"].strip().lower()
            ),
            "",
        )
        if not employment_type:
            problems.append(
                "employment_type must be one of: " + ", ".join(_EMPLOYMENT_TYPES) + "."
            )

    if problems:
        return _staffing_result(
            requester_id, "new_hire", "NEEDS_INFORMATION", problems, sources
        )

    new_employee = {
        "full_name": fields["full_name"],
        "gender": gender,
        "nationality": fields.get("nationality"),
        "email": email,
        "mobile": mobile,
        "department_id": department_id,
        "department_name": department["department_name"],
        "job_title": fields["job_title"],
        "job_grade": job_grade,
        "manager_id": manager_id,
        "employment_type": employment_type,
        "hire_date": _format_day(hire_date),
        "basic_salary": basic_salary,
        "housing_allowance": housing,
        "transport_allowance": transport,
    }

    sources.append(f"departments:{department_id}")
    if manager_id:
        sources.append(f"employees:{manager_id}")

    return _staffing_result(
        requester_id,
        "new_hire",
        "READY_FOR_APPROVAL",
        [
            f"New hire {new_employee['full_name']} as {new_employee['job_title']} "
            f"in {new_employee['department_name']}, starting "
            f"{new_employee['hire_date']}. The employee record and login are "
            "created only after an HR manager approves.",
            *([manager_notice] if manager_notice else []),
            *(f"Warning: {w['text']}" for w in pay["warnings"]),
        ],
        sources,
        proposed_action={
            "action_type": "new_hire",
            "payload": {
                # The proposal belongs to the HR requester (the new
                # employee has no employees row yet), which also stops
                # the requester approving their own request.
                "employee_id": requester_id,
                "requested_by": requester_id,
                # Top-level copy: governance.classify_risk only inspects
                # top-level payload keys, and basic_salary is what makes a
                # hire HIGH risk.
                "basic_salary": basic_salary,
                "new_employee": new_employee,
                # Shown to the approver above Approve; they don't block.
                "warnings": pay["warnings"],
            },
        },
    )


def _termination_request(conn, requester_id: str, query: str) -> dict:
    fields = _parse_staffing_fields(query)

    target_id = (fields.get("employee_id") or "").upper()
    if not target_id:
        match = _EMPLOYEE_ID_RE.search(query or "")
        target_id = match.group(0).upper() if match else ""

    sources = [f"employees:{target_id or requester_id}"]

    termination_type = re.sub(r"[\s-]+", "_", (fields.get("termination_type") or "").strip().lower())
    # Article 80 replaces the free-text reason with a ground + details.
    reason_value = fields.get("reason")
    if termination_type == "article_80":
        reason_value = fields.get("article_80_details") or reason_value

    missing = [
        field
        for field, value in (
            ("employee_id", target_id),
            ("termination_type", termination_type),
            ("article_80_details" if termination_type == "article_80" else "reason", reason_value),
        )
        if not value
    ]
    if missing:
        return _staffing_result(
            requester_id,
            "termination",
            "NEEDS_INFORMATION",
            [
                "To end an employee's service, provide "
                + ", ".join(missing)
                + ' as "key: value" pairs. termination_type is one of: '
                + ", ".join(TERMINATION_TYPES)
                + "."
            ],
            sources,
            missing=missing,
        )

    if target_id == requester_id:
        return _staffing_result(
            requester_id,
            "termination",
            "NOT_AUTHORIZED",
            ["You cannot submit an end-of-service request for yourself."],
            sources,
        )

    problems: list[str] = []

    if termination_type not in TERMINATION_TYPES:
        problems.append(
            "termination_type must be one of: " + ", ".join(TERMINATION_TYPES) + "."
        )

    employee = get_employee(conn, target_id)
    if employee is None:
        problems.append(f"Employee {target_id} does not exist.")
    elif employee.get("employment_status") != "Active":
        problems.append(
            f"Employee {target_id} is already {employee.get('employment_status')}."
        )
    else:
        existing = _open_termination(conn, target_id)
        if existing:
            # One termination at a time: point to the one already in progress.
            if existing["status"] == "pending_approval":
                problems.append(
                    f"Employee {target_id} already has an end-of-service request waiting "
                    f"for approval ({existing['proposal_id']}, last working day "
                    f"{existing['termination_date']})."
                )
            else:
                problems.append(
                    f"Employee {target_id} already has an approved end of service "
                    f"({existing['proposal_id']}) and is in the notice period until "
                    f"{existing['termination_date']}."
                )

    notes: list[str] = []
    today = date.today()
    notice_days = _NOTICE_DAYS.get(termination_type, (0, None))[0]
    notice_waived = str(fields.get("notice_waived") or "").strip().lower() in {"yes", "true", "1"}
    waiver_note = (fields.get("notice_waiver_note") or "").strip()

    if termination_type == "article_80":
        # Art. 80: effective immediately, no notice period.
        termination_date = today
        if fields.get("termination_date") and _parse_day(fields["termination_date"]) != today:
            notes.append("Article 80 dismissal takes effect immediately, so today's date is used.")
        notice_waived, waiver_note = False, ""
    elif fields.get("termination_date"):
        termination_date = _parse_day(fields["termination_date"])
        if termination_date is None:
            problems.append("termination_date must be a date in DD-MM-YYYY format.")
    elif notice_days:
        # Last working day defaults to the end of the Art. 75 notice period.
        termination_date = today + timedelta(days=notice_days)
        notes.append(
            f"Last working day set to the end of the {notice_days}-day notice period (Art. 75)."
        )
    else:
        termination_date = today
        notes.append("termination_date was not given, so today's date is used.")

    if notice_days and termination_date and termination_date < today + timedelta(days=notice_days):
        if not notice_waived:
            problems.append(
                f"The last working day must be at least {notice_days} days from today "
                f"({_format_day(today + timedelta(days=notice_days))}, Art. 75). For an "
                "earlier date, mark the notice as waived (notice_waived: yes) and add "
                "a notice_waiver_note."
            )
        elif not waiver_note:
            problems.append("A notice_waiver_note is required when the notice is waived.")
    elif notice_waived:
        # Nothing to waive: the date already covers the full notice period.
        notice_waived, waiver_note = False, ""

    hire_date = _parse_day((employee or {}).get("hire_date"))
    if termination_date and hire_date and termination_date < hire_date:
        problems.append("termination_date cannot be before the employee's hire date.")

    article_80 = None
    if termination_type == "article_80":
        art80_problems, article_80 = _article_80_problems(conn, fields, hire_date, today)
        problems.extend(art80_problems)
    else:
        problems.extend(check_reason_text("reason", reason_value))

    if problems:
        return _staffing_result(
            requester_id, "termination", "NEEDS_INFORMATION", problems, sources
        )

    # The LLM reads the reason against the type; its findings are warnings
    # for the approver, never blocks. Runs only once the hard checks pass.
    warnings = review_reason(
        termination_type,
        reason_value,
        article_80_ground=(article_80 or {}).get("ground_text"),
    )

    notes.insert(
        0,
        f"End of service for {employee.get('full_name')} ({target_id}) as "
        f"{termination_type} on {_format_day(termination_date)}. The record is "
        "updated only after an HR manager approves.",
    )
    notes.extend(f"Warning: {w['text']}" for w in warnings)

    return _staffing_result(
        requester_id,
        "termination",
        "READY_FOR_APPROVAL",
        notes,
        sources,
        proposed_action={
            "action_type": "termination",
            "payload": {
                "employee_id": target_id,
                "termination_type": termination_type,
                "termination_date": _format_day(termination_date),
                "reason": str(reason_value).strip(),
                "requested_by": requester_id,
                "notice_days_required": notice_days,
                "notice_waived": notice_waived,
                "notice_waiver_note": waiver_note or None,
                **({"article_80": article_80} if article_80 else {}),
                # Shown to the approver above Approve; they don't block.
                "warnings": warnings,
            },
        },
    )


def _staffing_request(user: dict, query: str) -> dict:
    """HR-agent result for a new_hire / termination request."""

    requester_id = str((user or {}).get("employee_id") or "")
    wants_hire = _is_new_hire(query)
    wants_termination = _is_termination(query)
    action_type = "new_hire" if wants_hire else "termination"
    sources = [f"employees:{requester_id}"]

    if (user or {}).get("role") not in _STAFF_ROLES:
        return _staffing_result(
            requester_id,
            action_type,
            "NOT_AUTHORIZED",
            ["Only HR staff can submit new-hire or end-of-service requests."],
            sources,
        )

    if wants_hire and wants_termination:
        return _staffing_result(
            requester_id,
            "multiple_actions",
            "NEEDS_INFORMATION",
            ["Please submit the new hire and the end of service as separate requests."],
            sources,
        )

    conn = get_connection()
    try:
        if wants_hire:
            return _new_hire_request(conn, requester_id, query)
        return _termination_request(conn, requester_id, query)
    finally:
        conn.close()


def _service_breakdown(start: date, end: date) -> tuple[int, int, int]:
    """Calendar years/months/days between two dates."""

    years = end.year - start.year
    months = end.month - start.month
    days = end.day - start.day
    if days < 0:
        months -= 1
        prev_month = end.month - 1 or 12
        prev_year = end.year if end.month > 1 else end.year - 1
        days += monthrange(prev_year, prev_month)[1]
    if months < 0:
        years -= 1
        months += 12
    return years, months, days


def _cite(law: tuple[str, str]) -> dict:
    return {"law_id": law[0], "article": law[1]}


def get_termination_profile(conn, proposal: dict) -> dict:
    """Read-only end-of-service estimate for a pending termination proposal.

    Used by the Decision Brief. Every figure carries the article it was
    computed from. Does not create or modify any records.
    """

    try:
        payload = json.loads(proposal.get("payload_json") or "{}")
    except (TypeError, ValueError):
        payload = {}

    employee_id = payload.get("employee_id")
    termination_type = payload.get("termination_type")
    employee = get_employee(conn, employee_id) if employee_id else None
    hire_date = _parse_day((employee or {}).get("hire_date"))
    end_date = _parse_day(payload.get("termination_date"))

    if not employee or not hire_date or not end_date:
        return {
            "employee_id": employee_id,
            "error": "Employee record, hire date or termination date is missing.",
            "disclaimer": TERMINATION_DISCLAIMER,
        }

    # --- Service length (hire_date -> termination_date) ---
    years, months, days = _service_breakdown(hire_date, end_date)
    total_days = (end_date - hire_date).days
    service_years = total_days / 365

    # --- Monthly wage (last recorded components) ---
    basic = float(employee.get("basic_salary_sar") or 0)
    housing = float(employee.get("housing_allowance_sar") or 0)
    transport = float(employee.get("transport_allowance_sar") or 0)
    monthly_wage = basic + housing + transport
    daily_wage = monthly_wage / 30

    # --- End-of-service award (Art. 84, adjusted by Art. 80/85/87) ---
    full_award = (
        monthly_wage * 0.5 * min(service_years, 5)
        + monthly_wage * max(service_years - 5, 0)
    )
    if termination_type == "article_80":
        ratio, ratio_law = 0.0, _LAW_ARTICLE_80
    elif termination_type == "resignation":
        if service_years < 2:
            ratio = 0.0
        elif service_years < 5:
            ratio = 1 / 3
        elif service_years < 10:
            ratio = 2 / 3
        else:
            ratio = 1.0
        ratio_law = _LAW_RESIGNATION_AWARD
    elif termination_type == "force_majeure":
        ratio, ratio_law = 1.0, _LAW_FULL_AWARD
    else:
        ratio, ratio_law = 1.0, _LAW_AWARD

    # --- Notice period (Art. 75) and shortfall (Art. 76) ---
    created_at = str(proposal.get("created_at") or "")
    try:
        # created_at is UTC; the last working day was set from the server's
        # local date, so count notice from the same local calendar day.
        notice_date = datetime.fromisoformat(created_at).astimezone().date()
    except ValueError:
        notice_date = date.today()
    days_given = max((end_date - notice_date).days, 0)

    warnings: list[dict] = []
    notice: dict = {"notice_given_on": _format_day(notice_date), "days_given": days_given}
    if termination_type == "article_80":
        notice.update(required_days=0, adjustment=0.0, waived=False, **_cite(_LAW_ARTICLE_80))
    elif termination_type in _NOTICE_DAYS:
        required, payer = _NOTICE_DAYS[termination_type]
        shortfall = max(required - days_given, 0)
        # Art. 76 applies "unless they agree otherwise": a notice waived by
        # agreement (with its note) carries no compensation.
        waived = bool(payload.get("notice_waived")) and shortfall > 0
        compensation = 0.0 if waived else round(daily_wage * shortfall, 2)
        notice.update(
            required_days=required,
            shortfall_days=shortfall,
            payer=payer,
            compensation=compensation,
            # Owed to the employee when the employer is short, deducted
            # when the employee is.
            adjustment=(compensation if payer == "employer" else -compensation) if compensation else 0.0,
            waived=waived,
            waiver_note=payload.get("notice_waiver_note") if waived else None,
            **_cite(_LAW_NOTICE),
        )
        if waived:
            warnings.append(
                {
                    "code": "notice_waived",
                    "text": (
                        f"Notice waived by agreement: {days_given} of {required} "
                        f"days given, no Article 76 compensation. Note: "
                        f"{payload.get('notice_waiver_note')}"
                    ),
                    **_cite(_LAW_NOTICE_COMPENSATION),
                }
            )
        elif shortfall:
            warnings.append(
                {
                    "code": "notice_shortfall",
                    "text": (
                        f"The termination date gives {days_given} days' notice "
                        f"but Article 75 requires {required}. Under Article 76 "
                        f"the {payer} owes the other party the wage for the "
                        f"missing {shortfall} days "
                        f"({round(daily_wage * shortfall, 2):,.2f} SAR), "
                        "unless they agree otherwise."
                    ),
                    **_cite(_LAW_NOTICE_COMPENSATION),
                }
            )
    else:
        notice.update(required_days=None, adjustment=0.0, waived=False, **_cite(_LAW_NOTICE))

    if termination_type == "article_80":
        warnings.append(
            {
                "code": "article_80_objection",
                "text": (
                    "Article 80 dismissal requires giving the employee the "
                    "opportunity to state the reasons for their objection "
                    "before the decision is final."
                ),
                **_cite(_LAW_ARTICLE_80),
            }
        )
    elif termination_type == "termination_by_employer":
        warnings.append(
            {
                "code": "illegitimate_termination",
                "text": (
                    "If this termination is found to lack a legitimate reason, "
                    "Article 77 compensation (at least two months' wage) may "
                    "also be due."
                ),
                **_cite(_LAW_ILLEGITIMATE_TERMINATION),
            }
        )

    # --- Unused annual leave (Art. 111) ---
    balance = get_leave_balance(conn, employee_id) or {}
    annual_remaining = float(balance.get("annual_remaining") or 0)

    # --- Total: award + unused leave + notice adjustment ---
    award_value = round(full_award * ratio, 2)
    leave_value = round(daily_wage * annual_remaining, 2)
    total = round(award_value + leave_value + notice.get("adjustment", 0.0), 2)

    # --- Settlement deadline (Art. 88) ---
    settlement_days = 14 if termination_type == "resignation" else 7

    # --- Read-only impact: nothing is reassigned automatically ---
    direct_reports = conn.execute(
        """
        SELECT COUNT(*) FROM employees
        WHERE manager_id = ? AND employment_status = 'Active'
        """,
        (employee_id,),
    ).fetchone()[0]
    departments_headed = [
        row[0]
        for row in conn.execute(
            "SELECT department_id FROM departments WHERE manager_employee_id = ?",
            (employee_id,),
        ).fetchall()
    ]
    pending_requests = conn.execute(
        """
        SELECT COUNT(*) FROM pending_approvals
        WHERE employee_id = ? AND status = 'pending' AND proposal_id != ?
        """,
        (employee_id, proposal.get("proposal_id")),
    ).fetchone()[0]

    return {
        "employee_id": employee_id,
        "full_name": employee.get("full_name"),
        "termination_type": termination_type,
        "hire_date": employee.get("hire_date"),
        "termination_date": payload.get("termination_date"),
        "service": {
            "years": years,
            "months": months,
            "days": days,
            "total_days": total_days,
            "service_years": round(service_years, 4),
            **_cite(_LAW_AWARD),
        },
        "monthly_wage": {
            "basic_salary_sar": basic,
            "housing_allowance_sar": housing,
            "transport_allowance_sar": transport,
            "value": round(monthly_wage, 2),
            "estimate": True,
            **_cite(_LAW_AWARD),
        },
        "end_of_service_award": {
            "full_award": round(full_award, 2),
            "full_award_law": _cite(_LAW_AWARD),
            "ratio": round(ratio, 4),
            "ratio_law": _cite(ratio_law),
            "value": award_value,
        },
        "notice": notice,
        "unused_leave": {
            "annual_remaining_days": annual_remaining,
            "daily_wage": round(daily_wage, 2),
            "value": leave_value,
            **_cite(_LAW_UNUSED_LEAVE),
        },
        "total": {"value": total},
        "settlement": {
            "within_days": settlement_days,
            "deadline": _format_day(end_date + timedelta(days=settlement_days)),
            **_cite(_LAW_SETTLEMENT),
        },
        "impact": {
            "active_direct_reports": direct_reports,
            "departments_headed": departments_headed,
            "pending_requests": pending_requests,
        },
        "warnings": warnings,
        # Art. 80 ground, details and confirmed procedures, as requested.
        "article_80": payload.get("article_80"),
        "contract_end_case": _cite(_LAW_END_CASES),
        "disclaimer": TERMINATION_DISCLAIMER,
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

        # Phase 4: hiring and termination act on someone else's record
        # (or a record that doesn't exist yet), so they skip the
        # self-service branches below.
        if _is_new_hire(query) or _is_termination(query):
            return _staffing_request(user, query)

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