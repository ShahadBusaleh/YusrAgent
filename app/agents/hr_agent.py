from app.agents.base import BaseAgent
from app.agents.leave_intent import detects_leave_submission_intent, extract_leave_fields

from app.db.connection import get_connection
from app.db.employees import find_cover_candidates, get_employee
from app.db.leave import get_leave_balance, list_leave_requests


_STAFF_ROLES = {"hr_specialist", "hr_manager", "admin"}

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
    return {
        key: employee[key]
        for key in _PROFILE_FIELDS
        if key in employee
    }


class HRAgent(BaseAgent):

    def run(self, input: dict) -> dict:
        """
        HR / Execution Agent.

        Retrieves structured HR facts from the database.
        Does not make policy decisions or approvals.
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
        proposed_action = None

        try:
            facts: dict = {
                "employee_id": employee_id
            }

            sources: list[str] = []

            # Leave balance
            balance = get_leave_balance(
                conn,
                employee_id
            )

            if balance:
                facts["leave_balance"] = balance

                annual_remaining = balance.get(
                    "annual_remaining"
                )

                if annual_remaining is not None:
                    facts["annual_remaining"] = annual_remaining
                    facts["remaining_balance"] = annual_remaining

                sources.append(
                    f"leave_balances:{employee_id}"
                )

            # Employee profile
            employee = get_employee(
                conn,
                employee_id
            )

            if employee:
                facts["profile"] = _safe_profile(employee)

                sources.append(
                    f"employees:{employee_id}"
                )

            # Previous leave requests
            requests = list_leave_requests(
                conn,
                employee_id
            )

            if requests:
                facts["leave_requests"] = requests

                sources.append(
                    f"leave_requests:{employee_id}"
                )

            # Leave submission (chat-initiated): extract fields, find a
            # cover, and propose the action. Never fabricates a request
            # from incomplete input.
            if detects_leave_submission_intent(query):
                extracted = extract_leave_fields(query)

                missing = [
                    field
                    for field in ("leave_type", "start_date", "end_date")
                    if not extracted.get(field)
                ]

                if missing:
                    facts["request_assessment"] = {
                        "status": "NEEDS_INFORMATION",
                        "missing_information": missing,
                        "notes": [
                            "Provide the missing leave details "
                            "(type, start date, end date) to submit the request."
                        ],
                    }
                else:
                    leave_type = extracted["leave_type"]
                    start_date = extracted["start_date"]
                    end_date = extracted["end_date"]
                    days = extracted.get("days")

                    facts["requested_days"] = days

                    if balance:
                        type_remaining = balance.get(f"{leave_type}_remaining")
                        if type_remaining is not None:
                            facts["remaining_balance"] = type_remaining

                    candidates = find_cover_candidates(
                        conn, employee_id, start_date, end_date
                    )
                    top_cover = candidates[0] if candidates else None

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
                                top_cover.get("employee_id") if top_cover else None
                            ),
                            "suggested_cover_employee_name": (
                                top_cover.get("full_name") if top_cover else None
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