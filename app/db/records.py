"""Records archive (Phase 4): READ-ONLY queries over every request.

proposed_actions + pending_approvals + employees, for all action types. Never
writes. Grievances and audit_log are not part of Records. IBANs are masked to
their last 4 digits everywhere they appear (list, detail, export), and CV
text is never returned. The UI renders the Excel / PDF files from export_records.
"""

from __future__ import annotations

import json
import re
import sqlite3

PAGE_SIZE = 20

# UI status filter -> proposed_actions.status
STATUS_FILTERS = {
    "pending": "pending_approval",
    "approved": "approved",
    "rejected": "rejected",
}

_IBAN_RE = re.compile(r"\bSA(?:[ -]?[0-9A-Z]){22}\b", re.IGNORECASE)
_DROP_KEYS = {"cv_text"}


def mask_iban(text: str) -> str:
    def _mask(match: re.Match) -> str:
        digits = re.sub(r"[ -]", "", match.group(0))
        return f"SA•• •••• •••• {digits[-4:]}"

    return _IBAN_RE.sub(_mask, text)


def _sanitize(value):
    """Mask IBANs in every string and drop CV text, recursively."""
    if isinstance(value, str):
        return mask_iban(value)
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items() if k not in _DROP_KEYS}
    return value


_BASE_SQL = """
    SELECT
        pa.proposal_id, pa.action_type, pa.status, pa.created_at, pa.payload_json,
        pa.employee_id, pa.related_request_id, pa.risk_level,
        ap.approval_id, ap.decided_by, ap.decided_at, ap.decision_note,
        e.full_name AS employee_name, e.department_id AS employee_department_id,
        d.department_name AS employee_department_name,
        d.department_name_ar AS employee_department_name_ar,
        e.employee_name_ar, e.job_title AS employee_job_title, e.job_title_ar AS employee_job_title_ar,
        e.nationality AS employee_nationality, e.nationality_ar AS employee_nationality_ar,
        e.hire_date AS employee_hire_date,
        e.employment_status, e.termination_date,
        dec.full_name AS decided_by_name
    FROM proposed_actions pa
    LEFT JOIN pending_approvals ap ON ap.proposal_id = pa.proposal_id
    LEFT JOIN employees e ON e.employee_id = pa.employee_id
    LEFT JOIN departments d ON d.department_id = e.department_id
    LEFT JOIN employees dec ON dec.employee_id = ap.decided_by
"""


def _summary(action_type: str, payload: dict) -> str:
    if action_type == "leave_request":
        return (
            f"{str(payload.get('leave_type') or '').title()} "
            f"{payload.get('start_date') or '?'} → {payload.get('end_date') or '?'}"
            + (f" ({payload.get('days'):g} days)" if isinstance(payload.get("days"), (int, float)) else "")
        ).strip()
    if action_type == "personal_info_update":
        return f"{payload.get('field_name')}: {payload.get('old_value') or '—'} → {payload.get('new_value') or '—'}"
    if action_type == "bank_update":
        return f"IBAN {payload.get('old_iban') or '—'} → {payload.get('new_iban') or '—'}"
    if action_type == "new_hire":
        hire = payload.get("new_employee") or {}
        return f"{hire.get('job_title') or ''}, starting {hire.get('hire_date') or '?'}".strip(", ")
    if action_type == "termination":
        return (
            f"{str(payload.get('termination_type') or '').replace('_', ' ')}, "
            f"last working day {payload.get('termination_date') or '?'}"
        )
    return ""


def _build(row: sqlite3.Row, departments: dict[str, tuple[str, str | None]]) -> dict:
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except (TypeError, ValueError):
        payload = {}
    action_type = row["action_type"] or ""

    subject_id = row["employee_id"]
    subject_name = row["employee_name"]
    department_id = row["employee_department_id"]
    department_name = row["employee_department_name"]
    department_name_ar = row["employee_department_name_ar"]
    subject_name_ar = row["employee_name_ar"]
    job_title, job_title_ar = row["employee_job_title"], row["employee_job_title_ar"]
    nationality, nationality_ar = row["employee_nationality"], row["employee_nationality_ar"]
    hire_date = row["employee_hire_date"]
    requested_by = payload.get("requested_by") or row["employee_id"]
    if action_type == "new_hire":
        # The proposal belongs to the HR requester; the subject is the hire.
        hire = payload.get("new_employee") or {}
        subject_id = row["related_request_id"]  # set once approved
        subject_name = hire.get("full_name")
        department_id = hire.get("department_id")
        dep_en, dep_ar = departments.get(department_id or "", (None, None))
        department_name = hire.get("department_name") or dep_en
        department_name_ar = dep_ar
        subject_name_ar, job_title_ar, nationality_ar = None, None, None
        job_title, nationality, hire_date = hire.get("job_title"), hire.get("nationality"), hire.get("hire_date")

    separation = None
    if action_type == "termination" and row["status"] == "approved":
        if payload.get("finalized_at"):
            separation = {"state": "finalized", "date": payload.get("termination_date"), "at": payload["finalized_at"]}
        else:
            separation = {"state": "notice", "until": payload.get("termination_date")}

    record = {
        "proposal_id": row["proposal_id"],
        "action_type": action_type,
        "status": row["status"],
        "submitted_at": row["created_at"],
        "risk_level": row["risk_level"],
        "subject_id": subject_id,
        "subject_name": subject_name,
        "department_id": department_id,
        "department_name": department_name,
        "department_name_ar": department_name_ar,
        "subject_name_ar": subject_name_ar,
        "job_title": job_title,
        "job_title_ar": job_title_ar,
        "nationality": nationality,
        "nationality_ar": nationality_ar,
        "hire_date": hire_date,
        "requested_by": requested_by,
        "decided_by": row["decided_by"],
        "decided_by_name": row["decided_by_name"],
        "decided_at": row["decided_at"],
        "decision_note": row["decision_note"],
        "separation": separation,
        "summary": _summary(action_type, payload),
        "_payload": payload,
    }
    return record


def _departments(conn: sqlite3.Connection) -> dict[str, tuple[str, str | None]]:
    return {
        r["department_id"]: (r["department_name"], r["department_name_ar"])
        for r in conn.execute("SELECT department_id, department_name, department_name_ar FROM departments")
    }


def _query(
    conn: sqlite3.Connection,
    *,
    types: list[str] | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    department_id: str | None = None,
    q: str | None = None,
    requested_by: str | None = None,
) -> list[dict]:
    where, params = [], []
    if types:
        where.append(f"pa.action_type IN ({', '.join('?' for _ in types)})")
        params.extend(types)
    if status:
        where.append("pa.status = ?")
        params.append(STATUS_FILTERS.get(status, status))
    if date_from:  # YYYY-MM-DD, against the submission timestamp
        where.append("substr(pa.created_at, 1, 10) >= ?")
        params.append(date_from)
    if date_to:
        where.append("substr(pa.created_at, 1, 10) <= ?")
        params.append(date_to)
    sql = _BASE_SQL + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY pa.created_at DESC, pa.proposal_id DESC"

    conn.row_factory = sqlite3.Row
    departments = _departments(conn)
    records = [_build(row, departments) for row in conn.execute(sql, params).fetchall()]

    # Derived fields (a new hire's department/name live in the payload).
    if department_id:
        records = [r for r in records if r["department_id"] == department_id]
    if requested_by:
        records = [r for r in records if r["requested_by"] == requested_by]
    if q:
        needle = q.strip().lower()
        records = [
            r
            for r in records
            if any(
                needle in str(r.get(field) or "").lower()
                for field in ("subject_id", "subject_name", "requested_by", "proposal_id")
            )
        ]
    return records


def _public(record: dict, *, with_payload: bool = False) -> dict:
    out = {k: v for k, v in record.items() if k != "_payload"}
    if with_payload:
        out["payload"] = record["_payload"]
    return _sanitize(out)


def list_records(conn: sqlite3.Connection, *, page: int = 1, page_size: int = PAGE_SIZE, **filters) -> dict:
    records = _query(conn, **filters)
    page = max(page, 1)
    start = (page - 1) * page_size
    return {
        "items": [_public(r) for r in records[start : start + page_size]],
        "total": len(records),
        "page": page,
        "page_size": page_size,
    }


def get_record(conn: sqlite3.Connection, proposal_id: str) -> dict | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(_BASE_SQL + " WHERE pa.proposal_id = ?", (proposal_id,)).fetchone()
    if row is None:
        return None
    return _public(_build(row, _departments(conn)), with_payload=True)


def list_facets(conn: sqlite3.Connection) -> dict:
    conn.row_factory = sqlite3.Row
    types = [r[0] for r in conn.execute("SELECT DISTINCT action_type FROM proposed_actions ORDER BY 1") if r[0]]
    departments = [
        {
            "department_id": r["department_id"],
            "department_name": r["department_name"],
            "department_name_ar": r["department_name_ar"],
        }
        for r in conn.execute(
            "SELECT department_id, department_name, department_name_ar FROM departments ORDER BY department_name"
        )
    ]
    return {"types": types, "departments": departments}


def export_records(conn: sqlite3.Connection, **filters) -> list[dict]:
    """Every record matching the filters (no paging), IBANs masked, with the
    payload so the UI can localize the summary. The UI builds the Excel and
    PDF files from this."""
    return [_public(r, with_payload=True) for r in _query(conn, **filters)]
